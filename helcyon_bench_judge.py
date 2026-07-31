"""Native HWUI adapter for the bundled Helcyon-Bench judge pipeline."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import threading
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from helcyon_bench_capture import (
    INTEGRATED_SESSION_PATH,
    load_integrated_session,
    save_integrated_session,
)


REPO_ROOT = Path(__file__).resolve().parent
BENCH_ROOT = REPO_ROOT / "helcyon-bench"
BENCHMARK_DIR = BENCH_ROOT / "benchmarks"
PROMPT_PACK_DIR = BENCH_ROOT / "prompt_packs"
RUBRIC_DIR = BENCH_ROOT / "rubrics"

# The standalone directory contains a hyphen, so expose its package directory
# without importing the Streamlit application itself.
if str(BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCH_ROOT))

from llmbench.api import (  # noqa: E402
    ApiError,
    list_models,
    post_lifecycle_hook,
    preflight_chat_completion,
    set_debug_logging,
)
from llmbench.config import (  # noqa: E402
    ConfigError,
    judge_endpoint_by_name,
    load_config,
    save_judge_endpoint_api_key,
)
from llmbench.judge import (  # noqa: E402
    CATEGORY_WEIGHTS,
    CLASSIFICATION_LABELS,
    NOT_APPLICABLE_LABEL,
    JudgeError,
    classify_score_gap,
)
from llmbench.judging_profiles import get_judging_profile  # noqa: E402
from llmbench.protocol import (  # noqa: E402
    FORWARD,
    PROTOCOL_MODE_BIDIRECTIONAL,
    PROTOCOL_VERSION,
    REVERSE,
    is_legacy_single_pass,
    judge_comparison_bidirectional,
    result_stability,
    self_judging_disclosure,
)


RUBRIC_FILENAME_BY_CATEGORY = {
    "Creativity": "creativity.md",
    "Philosophy": "philosophy.md",
    "Morals": "morals.md",
    "Uncensored": "uncensored.md",
    "Humour": "humour.md",
}
EXTRA_SCORE_CATEGORIES_BY_CATEGORY = {
    "Creativity": ["Creativity"],
    "Philosophy": ["Philosophical Depth"],
    "Morals": ["Moral Reasoning"],
    "Uncensored": ["Uncensored"],
}
DISTRESS_SIGNAL_CATEGORIES = {"empathy"}
DISTRESS_SIGNAL_TERMS = (
    "distress",
    "grief",
    "grieving",
    "bereave",
    "mourning",
    "crisis",
    "suicid",
    "self-harm",
    "self harm",
    "trauma",
    "panic attack",
)


class IntegratedJudgeError(ValueError):
    pass


class IntegratedJudgeCancelled(IntegratedJudgeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        suffix=".tmp",
        prefix=".hwui_bench_result_",
        dir=str(path.parent),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def ensure_judge_config(app_dir: Path = BENCH_ROOT) -> bool:
    """Create the private runtime config from shipped scaffolding when absent."""
    config_path = app_dir / "config.yaml"
    if config_path.exists():
        return False
    example_path = app_dir / "config.example.yaml"
    try:
        text = example_path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise IntegratedJudgeError(
            "Judge configuration is missing and config.example.yaml was not found."
        ) from error
    app_dir.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        suffix=".tmp",
        prefix=".judge_config_",
        dir=str(app_dir),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary, config_path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return True


def _load_pack(pack_id: str) -> dict[str, Any]:
    safe_id = str(pack_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", safe_id):
        raise IntegratedJudgeError("The selected prompt pack identifier is invalid.")
    path = PROMPT_PACK_DIR / f"{safe_id}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise IntegratedJudgeError("The selected prompt pack no longer exists.") from error
    except (OSError, json.JSONDecodeError) as error:
        raise IntegratedJudgeError("The selected prompt pack could not be read safely.") from error
    if not isinstance(payload, dict):
        raise IntegratedJudgeError("The selected prompt pack is malformed.")
    prompts = payload.get("prompts")
    if not isinstance(prompts, list) or not prompts:
        raise IntegratedJudgeError("The selected prompt pack contains no prompts.")
    return payload


def _judge_headers(endpoint) -> dict[str, str]:
    headers = {"X-HelcyonBench-Run": "true"}
    if getattr(endpoint, "local_endpoint_mode", ""):
        headers["X-HelcyonBench-Endpoint-Mode"] = endpoint.local_endpoint_mode
    if getattr(endpoint, "name", ""):
        headers["X-HelcyonBench-Endpoint"] = endpoint.name
    return headers


def _endpoint_requires_api_key(endpoint) -> bool:
    key = str(getattr(endpoint, "api_key", "") or "").strip()
    url = str(getattr(endpoint, "base_url", "") or "").lower()
    is_local = any(host in url for host in ("127.0.0.1", "localhost", "0.0.0.0"))
    return not is_local and (not key or key in {"sk-your-key-here", "your-api-key"})


def _public_endpoint(endpoint) -> dict[str, Any]:
    models = list(dict.fromkeys([endpoint.model, *(endpoint.models or [])]))
    return {
        "name": endpoint.name,
        "base_url": endpoint.base_url,
        "local_endpoint_mode": endpoint.local_endpoint_mode,
        "default_model": endpoint.model,
        "models": [model for model in models if model],
        "has_api_key": bool(str(endpoint.api_key or "").strip())
        and not _endpoint_requires_api_key(endpoint),
        "requires_api_key": _endpoint_requires_api_key(endpoint),
    }


def load_judge_settings(
    selected_endpoint: str = "",
    selected_model: str = "",
) -> dict[str, Any]:
    try:
        created_config = ensure_judge_config(BENCH_ROOT)
        config = load_config(BENCH_ROOT)
    except (ConfigError, IntegratedJudgeError, OSError) as error:
        return {
            "configured": False,
            "error": str(error),
            "endpoints": [],
            "selected_endpoint": "",
            "selected_model": "",
        }
    endpoints = config.judge.endpoints or [config.judge]
    endpoint = judge_endpoint_by_name(config.judge, selected_endpoint or config.judge.name)
    models = list(dict.fromkeys([endpoint.model, *(endpoint.models or [])]))
    if selected_model and selected_model not in models:
        models.append(selected_model)
    model = selected_model or endpoint.model
    public_endpoints = [_public_endpoint(item) for item in endpoints]
    for item in public_endpoints:
        if item["name"] == endpoint.name:
            item["models"] = models
    return {
        "configured": True,
        "error": "",
        "notice": (
            "Created private judge configuration from config.example.yaml."
            if created_config
            else ""
        ),
        "endpoints": public_endpoints,
        "selected_endpoint": endpoint.name,
        "selected_model": model,
    }


def save_judge_api_key(endpoint_name: str, api_key: str) -> dict[str, Any]:
    ensure_judge_config(BENCH_ROOT)
    save_judge_endpoint_api_key(BENCH_ROOT, endpoint_name, api_key)
    return load_judge_settings(endpoint_name)


def _resolved_endpoint(endpoint_name: str):
    ensure_judge_config(BENCH_ROOT)
    config = load_config(BENCH_ROOT)
    set_debug_logging(config.debug_logging)
    endpoint = judge_endpoint_by_name(config.judge, endpoint_name)
    if endpoint_name and endpoint.name != endpoint_name:
        raise IntegratedJudgeError(f"Judge endpoint was not found: {endpoint_name}")
    if _endpoint_requires_api_key(endpoint):
        raise IntegratedJudgeError(f"API key required for judge endpoint {endpoint.name}.")
    return config, endpoint


def refresh_judge_models(endpoint_name: str) -> dict[str, Any]:
    _, endpoint = _resolved_endpoint(endpoint_name)
    models = list_models(
        base_url=endpoint.base_url,
        api_key=endpoint.api_key,
        extra_headers=_judge_headers(endpoint),
    )
    if not models:
        raise IntegratedJudgeError(f"{endpoint.name} returned no judge models.")
    return {"endpoint": endpoint.name, "models": models}


def test_judge_connection(endpoint_name: str, model: str) -> dict[str, Any]:
    _, endpoint = _resolved_endpoint(endpoint_name)
    model = str(model or endpoint.model).strip()
    if not model:
        raise IntegratedJudgeError("Select a judge model before testing the connection.")
    models = list_models(
        base_url=endpoint.base_url,
        api_key=endpoint.api_key,
        extra_headers=_judge_headers(endpoint),
    )
    if models and model not in models:
        raise IntegratedJudgeError(
            f"Selected judge model {model!r} was not returned by {endpoint.name}."
        )
    preflight_chat_completion(
        base_url=endpoint.base_url,
        api_key=endpoint.api_key,
        model=model,
        temperature=endpoint.temperature,
        max_tokens=min(endpoint.max_tokens or 64, 256),
        max_completion_tokens=(
            min(endpoint.max_completion_tokens, 256)
            if endpoint.max_completion_tokens is not None
            else None
        ),
        extra_headers=_judge_headers(endpoint),
        endpoint_name=endpoint.name,
        prompt_id="connection_test",
    )
    return {
        "ok": True,
        "endpoint": endpoint.name,
        "model": model,
        "models": models,
        "message": (
            f"Judge connection OK: {endpoint.name} returned models and completed "
            f"a tiny structured chat with {model}."
        ),
    }


def _pack_metadata(pack_id: str, pack: dict[str, Any]) -> dict[str, str]:
    return {
        "id": pack_id,
        "name": str(pack.get("name") or pack_id),
        "category": str(pack.get("category") or "General"),
        "description": str(pack.get("description") or ""),
        "judge_profile": str(pack.get("judge_profile") or "General"),
        "rubric": str(pack.get("rubric") or ""),
    }


def _rubric_for_pack(metadata: dict[str, str]) -> tuple[str, str, list[str]]:
    rubric_key = metadata["rubric"].strip().lower()
    filename = (
        f"{rubric_key}.md"
        if rubric_key
        else RUBRIC_FILENAME_BY_CATEGORY.get(metadata["category"], "companion.md")
    )
    path = RUBRIC_DIR / filename
    if not path.exists():
        path = RUBRIC_DIR / "companion.md"
    if not path.exists():
        raise IntegratedJudgeError("No Helcyon-Bench scoring rubric is available.")
    text = path.read_text(encoding="utf-8")
    haystack = " ".join(
        metadata.get(field, "") for field in ("name", "description")
    ).lower()
    distress = (
        metadata["category"].strip().lower() in DISTRESS_SIGNAL_CATEGORIES
        or any(term in haystack for term in DISTRESS_SIGNAL_TERMS)
    )
    distress_path = RUBRIC_DIR / "distress_calibration.md"
    if distress and distress_path.exists():
        text = f"{text.rstrip()}\n\n---\n\n{distress_path.read_text(encoding='utf-8')}"
    expected = RUBRIC_FILENAME_BY_CATEGORY.get(metadata["category"])
    extras = (
        list(EXTRA_SCORE_CATEGORIES_BY_CATEGORY.get(metadata["category"], []))
        if expected and path.name == expected
        else []
    )
    return path.name, text, extras


def _judge_profile_prompt(metadata: dict[str, str]) -> str:
    profile = get_judging_profile(metadata["judge_profile"])
    lines = [
        f"Name: {metadata['judge_profile']}",
        f"Prompt Pack: {metadata['name']}",
        f"Category: {metadata['category']}",
    ]
    if metadata["description"]:
        lines.append(f"Description: {metadata['description']}")
    lines.extend(["", profile.instructions])
    return "\n".join(lines)


def _validated_items(
    session: dict[str, Any],
    pack_id: str,
    pack: dict[str, Any],
) -> tuple[str, str, list[dict[str, Any]]]:
    model_a = str(session.get("model_a") or "").strip()
    model_b = str(session.get("model_b") or "").strip()
    if not model_a or not model_b:
        raise IntegratedJudgeError("Enter both Model Name A and Model Name B before judging.")
    responses = session.get("responses")
    if not isinstance(responses, dict):
        raise IntegratedJudgeError("No integrated benchmark responses are available.")
    items = []
    missing = []
    for index, prompt in enumerate(pack["prompts"], start=1):
        if not isinstance(prompt, dict):
            continue
        test_id = str(prompt.get("id") or f"prompt_{index:02d}")
        response_a = str(responses.get(f"{pack_id}:{test_id}:A") or "").strip()
        response_b = str(responses.get(f"{pack_id}:{test_id}:B") or "").strip()
        if not response_a:
            missing.append(f"Prompt {index} Model A")
        if not response_b:
            missing.append(f"Prompt {index} Model B")
        items.append(
            {
                "id": test_id,
                "title": str(prompt.get("title") or f"Prompt {index}"),
                "category": str(prompt.get("category") or pack.get("category") or ""),
                "conversation_context": "",
                "current_prompt": str(prompt.get("prompt") or ""),
                "response_a": response_a,
                "response_b": response_b,
                "model_name_a": model_a,
                "model_name_b": model_b,
            }
        )
    if missing:
        preview = ", ".join(missing[:4])
        if len(missing) > 4:
            preview += f", and {len(missing) - 4} more"
        raise IntegratedJudgeError(
            f"Benchmark is incomplete. Missing responses: {preview}."
        )
    return model_a, model_b, items


def _optional_score(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_score(value: float | None) -> str:
    return NOT_APPLICABLE_LABEL if value is None else f"{value:.2f}"


def _overall_scores(batch_results: list[dict[str, Any]]) -> tuple[float, float]:
    if not batch_results:
        return 0.0, 0.0
    return tuple(
        sum(
            _optional_score(
                entry["result"].get("responses", {}).get(side, {}).get("overall")
            )
            or 0.0
            for entry in batch_results
        )
        / len(batch_results)
        for side in ("A", "B")
    )


def _average_rows(batch_results: list[dict[str, Any]]) -> list[dict[str, str]]:
    categories = list(CATEGORY_WEIGHTS)
    for entry in batch_results:
        for side in ("A", "B"):
            for category in (
                entry["result"].get("responses", {}).get(side, {}).get("scores", {})
            ):
                if category not in categories:
                    categories.append(category)
    rows = []
    for category in categories:
        values = {"A": [], "B": []}
        na_prompts = 0
        for entry in batch_results:
            responses = entry["result"].get("responses", {})
            score_a = _optional_score(
                responses.get("A", {}).get("scores", {}).get(category)
            )
            score_b = _optional_score(
                responses.get("B", {}).get("scores", {}).get(category)
            )
            if score_a is None or score_b is None:
                na_prompts += 1
                continue
            values["A"].append(score_a)
            values["B"].append(score_b)
        average_a = sum(values["A"]) / len(values["A"]) if values["A"] else None
        average_b = sum(values["B"]) / len(values["B"]) if values["B"] else None
        label = category
        if na_prompts:
            label += (
                f" ({NOT_APPLICABLE_LABEL} in {na_prompts} of "
                f"{len(batch_results)})"
            )
        difference = (
            NOT_APPLICABLE_LABEL
            if average_a is None or average_b is None
            else f"{average_b - average_a:+.2f}"
        )
        rows.append(
            {
                "Category": label,
                "Response A Avg": _format_score(average_a),
                "Response B Avg": _format_score(average_b),
                "Difference": difference,
            }
        )
    overall_a, overall_b = _overall_scores(batch_results)
    rows.append(
        {
            "Category": "Overall",
            "Response A Avg": f"{overall_a:.2f}",
            "Response B Avg": f"{overall_b:.2f}",
            "Difference": f"{overall_b - overall_a:+.2f}",
        }
    )
    return rows


def _classification(result: dict[str, Any]) -> str:
    winner = result.get("winner") or {}
    classification = winner.get("classification")
    if classification in CLASSIFICATION_LABELS:
        return classification
    return classify_score_gap(float(winner.get("score_difference") or 0.0))


def _canonical_winner(
    batch_results: list[dict[str, Any]],
    model_a: str,
    model_b: str,
) -> str:
    overall_a, overall_b = _overall_scores(batch_results)
    difference = overall_a - overall_b
    thresholds = (
        (batch_results[0]["result"].get("protocol") or {}).get("thresholds") or {}
    )
    classification = classify_score_gap(
        difference,
        float(thresholds.get("effective_tie_threshold", 0.25)),
        float(thresholds.get("clear_winner_threshold", 1.0)),
    )
    if CLASSIFICATION_LABELS[classification] == "Effective Tie":
        if abs(difference) < 0.005:
            return "Effective Tie"
        return f"Effective Tie (leaning {model_a if difference >= 0 else model_b})"
    winner = model_a if difference >= 0 else model_b
    if CLASSIFICATION_LABELS[classification] == "Narrow Preference":
        return f"{winner} (Narrow Preference)"
    return winner


def _attach_winner_name(
    result: dict[str, Any],
    model_a: str,
    model_b: str,
) -> None:
    winner = result.get("winner")
    if not isinstance(winner, dict):
        return
    winner["model_name"] = {
        "A": model_a,
        "B": model_b,
        "TIE": "Tie",
    }.get(str(winner.get("response") or "").upper(), "")


def _filename_part(value: str, fallback: str) -> str:
    cleaned = re.sub(r"\s+", "-", str(value or fallback).strip().lower())
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", cleaned)
    cleaned = re.sub(r"[^a-z0-9-]+", "-", cleaned)
    return re.sub(r"-{2,}", "-", cleaned).strip("-.") or fallback


def _result_filename(
    model_a: str,
    model_b: str,
    metadata: dict[str, str],
    timestamp: datetime,
) -> str:
    return (
        "helcyon-bench-"
        f"{_filename_part(metadata['category'], 'general')}-"
        f"{_filename_part(metadata['name'], 'prompt-pack')}-"
        f"{_filename_part(model_a, 'response-a')}-vs-"
        f"{_filename_part(model_b, 'response-b')}-"
        f"{timestamp.strftime('%Y-%m-%d-%H%M')}.json"
    )


def _assert_no_credentials(payload: dict[str, Any], api_key: str) -> None:
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    sensitive_key = (
        api_key
        if api_key
        and api_key not in {"local-key", "sk-your-key-here", "your-api-key"}
        else ""
    )
    if sensitive_key and sensitive_key.lower() in serialized:
        raise IntegratedJudgeError("Credential safety check blocked the result write.")
    forbidden_keys = {"api_key", "authorization", "api-key"}

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if forbidden_keys.intersection(str(key).lower() for key in value):
                raise IntegratedJudgeError(
                    "Credential safety check blocked secret-bearing metadata."
                )
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)


def _save_judge_state(
    session_path: Path,
    updates: dict[str, Any],
) -> dict[str, Any]:
    state = load_integrated_session(session_path)
    state["judge_run"] = {
        **(state.get("judge_run") if isinstance(state.get("judge_run"), dict) else {}),
        **updates,
    }
    return save_integrated_session(state, session_path)


def validate_integrated_benchmark(
    endpoint_name: str,
    judge_model: str,
    session_path: Path = INTEGRATED_SESSION_PATH,
) -> dict[str, Any]:
    _, endpoint = _resolved_endpoint(endpoint_name)
    model = str(judge_model or endpoint.model).strip()
    if not model:
        raise IntegratedJudgeError("Select a judge model before judging.")
    session = load_integrated_session(session_path)
    pack_id = str(session.get("selected_pack") or "")
    pack = _load_pack(pack_id)
    _, _, items = _validated_items(session, pack_id, pack)
    return {
        "endpoint": endpoint.name,
        "model": model,
        "prompt_count": len(items),
        "response_count": len(items) * 2,
    }


def run_integrated_benchmark(
    *,
    endpoint_name: str,
    judge_model: str,
    progress: Callable[[dict[str, Any]], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    session_path: Path = INTEGRATED_SESSION_PATH,
    benchmark_dir: Path = BENCHMARK_DIR,
    judge_call: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    config, endpoint = _resolved_endpoint(endpoint_name)
    judge_model = str(judge_model or endpoint.model).strip()
    if not judge_model:
        raise IntegratedJudgeError("Select a judge model before judging.")
    session = load_integrated_session(session_path)
    pack_id = str(session.get("selected_pack") or "")
    pack = _load_pack(pack_id)
    metadata = _pack_metadata(pack_id, pack)
    model_a, model_b, items = _validated_items(session, pack_id, pack)
    rubric_name, rubric, extra_categories = _rubric_for_pack(metadata)
    profile_prompt = _judge_profile_prompt(metadata)
    if judge_call is None:
        test_judge_connection(endpoint.name, judge_model)

    lock_url = str(getattr(endpoint, "bench_lock_url", "") or "")
    unlock_url = str(getattr(endpoint, "bench_unlock_url", "") or "")
    if lock_url:
        post_lifecycle_hook(url=lock_url, extra_headers=_judge_headers(endpoint))

    batch_results = []
    total_passes = len(items) * 2
    pass_number = 0
    try:
        for index, item in enumerate(items, start=1):
            if cancelled and cancelled():
                raise IntegratedJudgeCancelled("Judge run cancelled.")

            def on_pass_start(pass_name: str, _prompt_id: str) -> None:
                nonlocal pass_number
                if cancelled and cancelled():
                    raise IntegratedJudgeCancelled("Judge run cancelled.")
                pass_number += 1
                if progress:
                    progress(
                        {
                            "status": "running",
                            "prompt_index": index,
                            "prompt_count": len(items),
                            "pass": pass_name,
                            "passes_completed": pass_number - 1,
                            "passes_total": total_passes,
                            "message": (
                                f"Judging prompt {index} of {len(items)} "
                                f"({'first order' if pass_name == FORWARD else 'reversed order'}): "
                                f"{item['title']}"
                            ),
                        }
                    )

            result = judge_comparison_bidirectional(
                config=config,
                rubric=rubric,
                judging_profile=profile_prompt,
                conversation_context=item["conversation_context"],
                current_prompt=item["current_prompt"],
                response_a=item["response_a"],
                response_b=item["response_b"],
                model_name_a=model_a,
                model_name_b=model_b,
                model=judge_model,
                temperature=endpoint.temperature,
                endpoint=endpoint,
                extra_headers=_judge_headers(endpoint),
                prompt_id=item["id"],
                extra_score_categories=extra_categories,
                on_pass_start=on_pass_start,
                judge_call=judge_call,
            )
            _attach_winner_name(result, model_a, model_b)
            batch_results.append({"item": item, "result": result})
    except Exception as error:
        _save_judge_state(
            session_path,
            {
                "status": (
                    "cancelled"
                    if isinstance(error, IntegratedJudgeCancelled)
                    else "error"
                ),
                "endpoint": endpoint.name,
                "model": judge_model,
                "prompt_index": len(batch_results) + 1,
                "completed_prompts": len(batch_results),
                "error": str(error),
                "updated_at": _now(),
            },
        )
        raise
    finally:
        if unlock_url:
            try:
                post_lifecycle_hook(
                    url=unlock_url,
                    extra_headers=_judge_headers(endpoint),
                )
            except ApiError:
                pass

    unstable = [
        index
        for index, entry in enumerate(batch_results, start=1)
        if (result_stability(entry["result"]) or {}).get("position_sensitive")
    ]
    positional = [
        float((result_stability(entry["result"]) or {}).get("positional_difference", 0.0))
        for entry in batch_results
        if result_stability(entry["result"]) is not None
    ]
    generated_at = datetime.now()
    payload = {
        "rubric": rubric_name,
        "prompt_pack": metadata,
        "judge_profile": metadata["judge_profile"],
        "judge_endpoint": endpoint.name,
        "judge_model": judge_model,
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "evaluation_protocol": {
            "mode": PROTOCOL_MODE_BIDIRECTIONAL,
            "version": PROTOCOL_VERSION,
            "passes_per_comparison": 2,
            "orders": ["Response A = Model A", "Response A = Model B"],
            "models": {"A": model_a, "B": model_b},
            "position_sensitive_comparisons": unstable,
            "position_sensitive_count": len(unstable),
            "legacy_single_pass_comparisons": sum(
                1 for entry in batch_results if is_legacy_single_pass(entry["result"])
            ),
            "mean_slot_effect": (
                round(sum(positional) / len(positional), 2) if positional else None
            ),
            "classification_counts": {
                label: Counter(
                    _classification(entry["result"]) for entry in batch_results
                )[label]
                for label in CLASSIFICATION_LABELS
            },
        },
        "self_judging": self_judging_disclosure(judge_model, model_a, model_b),
        "canonical_winner": _canonical_winner(
            batch_results,
            model_a,
            model_b,
        ),
        "averages": _average_rows(batch_results),
        "comparisons": batch_results,
        "hwui_integration": {
            "schema_version": 1,
            "source": "HWUI Pro native Helcyon-Bench",
            "run_id": str((session.get("run_ids") or {}).get(pack_id) or ""),
        },
    }
    _assert_no_credentials(payload, str(endpoint.api_key or ""))
    filename = _result_filename(model_a, model_b, metadata, generated_at)
    result_path = benchmark_dir / filename
    suffix = 2
    while result_path.exists():
        result_path = benchmark_dir / f"{Path(filename).stem}-{suffix}.json"
        suffix += 1
    filename = result_path.name
    _atomic_write_json(result_path, payload)
    _save_judge_state(
        session_path,
        {
            "status": "completed",
            "endpoint": endpoint.name,
            "model": judge_model,
            "completed_prompts": len(items),
            "prompt_count": len(items),
            "result_file": filename,
            "canonical_winner": payload["canonical_winner"],
            "completed_at": _now(),
            "error": "",
        },
    )
    if progress:
        progress(
            {
                "status": "completed",
                "prompt_index": len(items),
                "prompt_count": len(items),
                "passes_completed": total_passes,
                "passes_total": total_passes,
                "message": f"Benchmark complete: {payload['canonical_winner']}",
                "result_file": filename,
            }
        )
    return {
        "status": "completed",
        "result_file": filename,
        "canonical_winner": payload["canonical_winner"],
        "prompt_count": len(items),
    }


class JudgeRunManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}

    def start(self, endpoint_name: str, judge_model: str) -> dict[str, Any]:
        validated = validate_integrated_benchmark(endpoint_name, judge_model)
        job_id = uuid.uuid4().hex
        job = {
            "job_id": job_id,
            "status": "queued",
            "message": "Judge run queued.",
            "endpoint": validated["endpoint"],
            "model": validated["model"],
            "prompt_count": validated["prompt_count"],
            "passes_total": validated["prompt_count"] * 2,
            "cancel_requested": False,
            "created_at": _now(),
        }
        with self._lock:
            self._jobs[job_id] = job
        thread = threading.Thread(
            target=self._run,
            args=(job_id,),
            name=f"helcyon-bench-judge-{job_id[:8]}",
            daemon=True,
        )
        thread.start()
        return self.get(job_id)

    def _update(self, job_id: str, updates: dict[str, Any]) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(updates)
                self._jobs[job_id]["updated_at"] = _now()

    def _run(self, job_id: str) -> None:
        with self._lock:
            job = dict(self._jobs[job_id])
        self._update(job_id, {"status": "running", "message": "Starting judge preflight..."})
        try:
            result = run_integrated_benchmark(
                endpoint_name=job["endpoint"],
                judge_model=job["model"],
                progress=lambda updates: self._update(job_id, updates),
                cancelled=lambda: self._cancel_requested(job_id),
            )
            self._update(job_id, result)
        except IntegratedJudgeCancelled as error:
            self._update(job_id, {"status": "cancelled", "message": str(error)})
        except (ApiError, ConfigError, JudgeError, IntegratedJudgeError, OSError) as error:
            self._update(
                job_id,
                {
                    "status": "error",
                    "message": str(error),
                    "error": str(error),
                    "raw_response": getattr(error, "raw_response", None),
                    "status_code": getattr(error, "status_code", None),
                },
            )
        except Exception as error:
            self._update(
                job_id,
                {
                    "status": "error",
                    "message": f"Judge run failed: {error}",
                    "error": str(error),
                },
            )

    def _cancel_requested(self, job_id: str) -> bool:
        with self._lock:
            return bool(self._jobs.get(job_id, {}).get("cancel_requested"))

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            if job_id not in self._jobs:
                raise IntegratedJudgeError("Judge run was not found.")
            if self._jobs[job_id]["status"] in {"completed", "error", "cancelled"}:
                return self._public(self._jobs[job_id])
            self._jobs[job_id]["cancel_requested"] = True
            self._jobs[job_id]["message"] = "Cancellation requested..."
            return self._public(self._jobs[job_id])

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            if job_id not in self._jobs:
                raise IntegratedJudgeError("Judge run was not found.")
            return self._public(self._jobs[job_id])

    @staticmethod
    def _public(job: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in job.items()
            if key != "cancel_requested"
        }


judge_run_manager = JudgeRunManager()

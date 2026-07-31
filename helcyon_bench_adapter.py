"""Adapter between HWUI Pro and the bundled Helcyon-Bench data."""

from __future__ import annotations

import json
import hashlib
import re
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent
BENCH_ROOT = REPO_ROOT / "helcyon-bench"
PROMPT_PACK_DIR = BENCH_ROOT / "prompt_packs"
BENCHMARK_DIR = BENCH_ROOT / "benchmarks"
ALIASES_PATH = BENCH_ROOT / "dashboard_model_aliases.json"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _pack_summary(path: Path, payload: dict[str, Any]) -> dict[str, Any] | None:
    prompts = []
    for index, item in enumerate(payload.get("prompts", []), start=1):
        if not isinstance(item, dict):
            continue
        prompt = str(item.get("prompt") or "").strip()
        if not prompt:
            continue
        prompts.append(
            {
                "test_id": str(item.get("id") or f"prompt_{index:02d}"),
                "title": str(item.get("title") or f"Prompt {index}"),
                "prompt": prompt,
                "category": str(item.get("category") or payload.get("category") or ""),
            }
        )
    if not prompts:
        return None
    return {
        "id": path.stem,
        "name": str(payload.get("name") or path.stem),
        "category": str(payload.get("category") or ""),
        "description": str(payload.get("description") or ""),
        "judge_profile": str(payload.get("judge_profile") or ""),
        "prompts": prompts,
    }


def load_prompt_packs(prompt_pack_dir: Path = PROMPT_PACK_DIR) -> list[dict[str, Any]]:
    """Return valid prompt packs without importing the Streamlit application."""
    packs: list[dict[str, Any]] = []
    for path in sorted(prompt_pack_dir.glob("*.json")):
        payload = _read_json(path)
        if not payload:
            continue
        summary = _pack_summary(path, payload)
        if summary:
            packs.append(summary)
    return packs


class PromptPackError(ValueError):
    """Raised for invalid prompt-pack write requests (the route maps this to a 4xx)."""


class PromptPackNotFoundError(PromptPackError):
    """Raised when a prompt pack id does not resolve to an existing file."""


_PACK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")


def _safe_pack_id(value: object) -> str:
    pack_id = str(value or "").strip()
    if not pack_id or not _PACK_ID_RE.match(pack_id) or Path(pack_id).name != pack_id:
        raise PromptPackError("Invalid prompt pack identifier.")
    return pack_id


def prompt_pack_filename(value: object) -> str:
    """Slugify a pack name into a safe *.json filename (mirrors the standalone app)."""
    cleaned = str(value or "").strip().lower()
    cleaned = re.sub(r"\s+", "-", cleaned)
    cleaned = re.sub(r"[^a-z0-9-]+", "", cleaned)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    return f"{cleaned or 'custom-prompt-pack'}.json"


def _prompt_pack_path(pack_id: object, prompt_pack_dir: Path = PROMPT_PACK_DIR) -> Path:
    safe_id = _safe_pack_id(pack_id)
    root = prompt_pack_dir.resolve()
    path = (prompt_pack_dir / f"{safe_id}.json").resolve()
    if path.parent != root:
        raise PromptPackError("Invalid prompt pack identifier.")
    return path


def _read_raw_pack_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _validate_pack_payload(data: object) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise PromptPackError("Prompt pack payload must be a JSON object.")
    name = str(data.get("name") or "").strip()
    if not name:
        raise PromptPackError("Pack name is required.")
    category = str(data.get("category") or "General").strip() or "General"
    description = str(data.get("description") or "").strip()
    judge_profile = str(data.get("judge_profile") or category or "General").strip() or "General"
    raw_prompts = data.get("prompts")
    if not isinstance(raw_prompts, list):
        raise PromptPackError("Prompt pack needs a prompts array.")
    prompts = []
    for index, item in enumerate(raw_prompts, start=1):
        if not isinstance(item, dict):
            raise PromptPackError(f"Prompt {index} must be an object.")
        title = str(item.get("title") or "").strip()
        prompt = str(item.get("prompt") or "").strip()
        if not title and not prompt:
            continue
        if not title or not prompt:
            raise PromptPackError(f"Editor prompt {index} needs both a title and prompt.")
        prompts.append({"title": title, "prompt": prompt})
    if not prompts:
        raise PromptPackError("Add at least one complete prompt before saving.")
    return {
        "name": name,
        "category": category,
        "description": description,
        "judge_profile": judge_profile,
        "prompts": prompts,
    }


def _write_pack_document(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def create_prompt_pack(
    data: object,
    prompt_pack_dir: Path = PROMPT_PACK_DIR,
) -> dict[str, Any]:
    """Save editor content as a brand-new pack; refuses to overwrite an existing file."""
    metadata = _validate_pack_payload(data)
    path = prompt_pack_dir / prompt_pack_filename(metadata["name"])
    if path.exists():
        raise PromptPackError(f"{path.name} already exists. Choose a different pack name.")
    _write_pack_document(path, metadata)
    summary = _pack_summary(path, metadata)
    if summary is None:
        raise PromptPackError("Add at least one complete prompt before saving.")
    return summary


def update_prompt_pack(
    pack_id: object,
    data: object,
    bound_name: object = None,
    prompt_pack_dir: Path = PROMPT_PACK_DIR,
) -> dict[str, Any]:
    """Save editor content back to the exact file it was loaded from.

    Only treated as a rename if the typed name differs from ``bound_name`` (the name
    the editor was bound to at load time) — comparing filenames directly would misfire
    on packs whose on-disk filename doesn't match today's slug format, turning a plain
    re-save into a spurious rename. Mirrors the standalone app's save-target guard.
    """
    source_path = _prompt_pack_path(pack_id, prompt_pack_dir)
    if not source_path.exists():
        raise PromptPackNotFoundError(f"{source_path.name} was not found.")
    metadata = _validate_pack_payload(data)
    loaded_name = str(bound_name or "").strip()
    is_rename = bool(loaded_name) and metadata["name"] != loaded_name
    target_path = (
        prompt_pack_dir / prompt_pack_filename(metadata["name"]) if is_rename else source_path
    )
    if target_path != source_path and target_path.exists():
        raise PromptPackError(
            f'Can\'t rename to "{metadata["name"]}" — {target_path.name} already exists as a '
            "different pack."
        )
    existing = _read_raw_pack_dict(source_path)
    payload = {**existing, **metadata}
    _write_pack_document(target_path, payload)
    if target_path != source_path:
        source_path.unlink()
    summary = _pack_summary(target_path, payload)
    if summary is None:
        raise PromptPackError("Add at least one complete prompt before saving.")
    return summary


def delete_prompt_pack(
    pack_id: object,
    prompt_pack_dir: Path = PROMPT_PACK_DIR,
) -> str:
    path = _prompt_pack_path(pack_id, prompt_pack_dir)
    if not path.exists():
        raise PromptPackNotFoundError(f"{path.name} was not found.")
    remaining = [item for item in prompt_pack_dir.glob("*.json") if item != path]
    if not remaining:
        raise PromptPackError("Keep at least one prompt pack.")
    path.unlink()
    return path.name


def load_benchmark_results(benchmark_dir: Path = BENCHMARK_DIR) -> list[dict[str, Any]]:
    """Return compact saved-run summaries for the native Results view."""
    results: list[dict[str, Any]] = []
    for path in sorted(benchmark_dir.glob("*.json"), reverse=True):
        payload = _read_json(path)
        if not payload:
            continue
        protocol = payload.get("evaluation_protocol", {})
        models = protocol.get("models", {}) if isinstance(protocol, dict) else {}
        model_a = str(models.get("A") or "")
        model_b = str(models.get("B") or "")
        comparisons = payload.get("comparisons", [])
        if (not model_a or not model_b) and isinstance(comparisons, list) and comparisons:
            item = comparisons[0].get("item", {}) if isinstance(comparisons[0], dict) else {}
            model_a = model_a or str(item.get("model_name_a") or "Model A")
            model_b = model_b or str(item.get("model_name_b") or "Model B")

        score_a = score_b = None
        for row in payload.get("averages", []):
            if isinstance(row, dict) and str(row.get("Category")) == "Overall":
                try:
                    score_a = float(row.get("Response A Avg"))
                    score_b = float(row.get("Response B Avg"))
                except (TypeError, ValueError):
                    pass
                break
        if not model_a or not model_b or score_a is None or score_b is None:
            continue
        pack = payload.get("prompt_pack", {})
        results.append(
            {
                "id": path.stem,
                "source": path.name,
                "generated_at": str(payload.get("generated_at") or ""),
                "prompt_pack": str(pack.get("name") or "") if isinstance(pack, dict) else "",
                "category": str(pack.get("category") or "") if isinstance(pack, dict) else "",
                "winner": str(payload.get("canonical_winner") or ""),
                "judge_model": str(payload.get("judge_model") or ""),
                "model_a": model_a,
                "model_b": model_b,
                "score_a": score_a,
                "score_b": score_b,
                "prompt_count": len(comparisons) if isinstance(comparisons, list) else 0,
            }
        )
    return results


def load_benchmark_result_detail(
    source: str,
    benchmark_dir: Path = BENCHMARK_DIR,
) -> dict[str, Any]:
    """Return one exact saved benchmark result without allowing path traversal."""
    filename = str(source or "").strip()
    if not filename or Path(filename).name != filename or Path(filename).suffix.lower() != ".json":
        raise ValueError("A valid benchmark JSON filename is required.")
    payload = _read_json(benchmark_dir / filename)
    if payload is None:
        raise FileNotFoundError(filename)
    return payload


def build_leaderboard(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    totals: dict[str, dict[str, float]] = {}
    for result in results:
        for name_key, score_key in (("model_a", "score_a"), ("model_b", "score_b")):
            name = str(result.get(name_key) or "").strip()
            if not name:
                continue
            bucket = totals.setdefault(name, {"total": 0.0, "runs": 0.0})
            bucket["total"] += float(result[score_key])
            bucket["runs"] += 1
    rows = [
        {
            "model": name,
            "score": round(values["total"] / values["runs"], 2),
            "runs": int(values["runs"]),
        }
        for name, values in totals.items()
        if values["runs"]
    ]
    return sorted(rows, key=lambda row: (-row["score"], row["model"].lower()))


def _safe_score(value: object) -> float | None:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    return min(score, 10.0) if score >= 0 else None


def _benchmark_category(payload: dict[str, Any], source_name: str = "") -> str:
    pack = payload.get("prompt_pack", {})
    if isinstance(pack, dict) and str(pack.get("category") or "").strip():
        return str(pack["category"]).strip()
    if str(payload.get("category") or "").strip():
        return str(payload["category"]).strip()
    rubric = str(payload.get("rubric") or "").lower().removesuffix(".md")
    known = {
        "companion": "Companion",
        "creativity": "Creativity",
        "philosophy": "Philosophy",
        "morals": "Morals",
        "humour": "Humour",
        "uncensored": "Uncensored",
    }
    if rubric in known:
        return known[rubric]
    filename = Path(source_name).stem.lower()
    return next((name for stem, name in known.items() if f"-{stem}-" in f"-{filename}-"), "Uncategorised")


def load_model_aliases(path: Path = ALIASES_PATH) -> dict[str, list[str]]:
    payload = _read_json(path)
    if not payload:
        return {}
    aliases: dict[str, list[str]] = {}
    for alias_name, sources in payload.items():
        alias = str(alias_name).strip()
        if not alias or not isinstance(sources, list):
            continue
        cleaned = []
        for source in sources:
            name = str(source).strip()
            if name and name != alias and name not in cleaned:
                cleaned.append(name)
        if cleaned:
            aliases[alias] = cleaned
    return aliases


def save_model_aliases(
    aliases: dict[str, list[str]],
    path: Path = ALIASES_PATH,
) -> dict[str, list[str]]:
    """Atomically persist the explicit dashboard alias registry."""
    cleaned: dict[str, list[str]] = {}
    for alias_name, sources in aliases.items():
        alias = str(alias_name).strip()
        if not alias or not isinstance(sources, list):
            continue
        source_names = sorted(
            {
                str(source).strip()
                for source in sources
                if str(source).strip() and str(source).strip() != alias
            },
            key=str.casefold,
        )
        if source_names:
            cleaned[alias] = source_names
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(cleaned, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return cleaned


def _identity_keys(model_name: str) -> set[str]:
    name = str(model_name or "").strip()
    if not name:
        return set()
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return {name, name.lower(), slug}


def _alias_lookup(aliases: dict[str, list[str]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for alias, sources in aliases.items():
        for name in [alias, *sources]:
            for key in _identity_keys(name):
                lookup.setdefault(key, alias)
    return lookup


def _display_model(model_name: str, lookup: dict[str, str]) -> str:
    for key in _identity_keys(model_name):
        if key in lookup:
            return lookup[key]
    return model_name


def consolidate_model_aliases(
    alias_name: object,
    selected_models: object,
    path: Path = ALIASES_PATH,
) -> dict[str, list[str]]:
    """Merge selected display identities under one explicit canonical name."""
    alias = str(alias_name or "").strip()
    selected = list(
        dict.fromkeys(
            str(model).strip()
            for model in selected_models
            if str(model).strip()
        )
    ) if isinstance(selected_models, list) else []
    if not alias:
        raise ValueError("Enter a final model name first.")
    if len(selected) < 2:
        raise ValueError("Select at least two models to consolidate.")

    aliases = load_model_aliases(path)
    alias_keys = _identity_keys(alias)
    expanded_sources: list[str] = []
    for model_name in selected:
        if model_name in aliases:
            expanded_sources.extend(aliases[model_name])
        else:
            expanded_sources.append(model_name)
    for existing_alias, existing_sources in aliases.items():
        if _identity_keys(existing_alias) & alias_keys:
            expanded_sources.extend(existing_sources)
            continue
        if any(_identity_keys(source) & alias_keys for source in existing_sources):
            expanded_sources.extend(existing_sources)

    source_set = {
        source for source in expanded_sources if source and source != alias
    }
    if not source_set:
        raise ValueError("Choose source models different from the final model name.")

    claimed_keys = set(alias_keys)
    for model_name in [*selected, *source_set]:
        claimed_keys.update(_identity_keys(model_name))
    updated: dict[str, list[str]] = {}
    for existing_alias, existing_sources in aliases.items():
        if (
            existing_alias == alias
            or existing_alias in selected
            or _identity_keys(existing_alias) & claimed_keys
        ):
            continue
        remaining = [
            source
            for source in existing_sources
            if source not in selected and not (_identity_keys(source) & claimed_keys)
        ]
        if remaining:
            updated[existing_alias] = remaining
    updated[alias] = sorted(source_set, key=str.casefold)
    return save_model_aliases(updated, path)


def rename_model_alias(
    model_name: object,
    new_name: object,
    path: Path = ALIASES_PATH,
) -> dict[str, list[str]]:
    """Rename a dashboard identity without rewriting historical result files."""
    current = str(model_name or "").strip()
    replacement = str(new_name or "").strip()
    if not current or not replacement:
        raise ValueError("Enter both the current and new model names.")
    if current == replacement:
        raise ValueError("Choose a different model name.")

    aliases = load_model_aliases(path)
    if current in aliases:
        source_set = set(aliases.pop(current))
        source_set.add(current)
    else:
        source_set = {current}
    if replacement in aliases:
        source_set.update(aliases.pop(replacement))

    updated: dict[str, list[str]] = {}
    for existing_alias, existing_sources in aliases.items():
        remaining = [
            source
            for source in existing_sources
            if source not in source_set and source != replacement
        ]
        if remaining:
            updated[existing_alias] = remaining
    updated[replacement] = sorted(
        {
            source
            for source in source_set
            if source and source != replacement
        },
        key=str.casefold,
    )
    if not updated[replacement]:
        raise ValueError("Choose a different model name.")
    return save_model_aliases(updated, path)


def delete_model_benchmark_runs(
    model_name: object,
    benchmark_dir: Path = BENCHMARK_DIR,
    aliases_path: Path = ALIASES_PATH,
) -> list[str]:
    """Delete saved JSON/Markdown comparison files contributing to one model."""
    model = str(model_name or "").strip()
    if not model:
        raise ValueError("Choose a model to delete.")
    aliases = load_model_aliases(aliases_path)
    lookup = _alias_lookup(aliases)
    matched: list[Path] = []
    for path in sorted(benchmark_dir.glob("*.json")):
        payload = _read_json(path)
        if not payload:
            continue
        if any(
            str(entry.get("model") or "") == model
            for entry in _score_entries(payload, path.name, lookup)
        ):
            matched.append(path)
    if not matched:
        raise ValueError(f"No saved benchmark runs were found for {model}.")

    deleted: list[str] = []
    for path in matched:
        path.unlink()
        deleted.append(path.name)
        markdown = path.with_suffix(".md")
        if markdown.exists():
            markdown.unlink()
            deleted.append(markdown.name)
    return deleted


def _score_entries(
    payload: dict[str, Any],
    source_name: str,
    alias_lookup: dict[str, str],
) -> list[dict[str, Any]]:
    entries = []
    pack = payload.get("prompt_pack", {})
    pack_name = str(pack.get("name") or "") if isinstance(pack, dict) else ""
    benchmark_category = _benchmark_category(payload, source_name)
    generated_at = str(payload.get("generated_at") or "")
    judge_model = str(payload.get("judge_model") or "")
    for comparison in payload.get("comparisons", []):
        if not isinstance(comparison, dict):
            continue
        item = comparison.get("item", {})
        result = comparison.get("result", {})
        responses = result.get("responses", {}) if isinstance(result, dict) else {}
        if not isinstance(item, dict) or not isinstance(responses, dict):
            continue
        item_category = str(item.get("category") or benchmark_category or "Uncategorised")
        for side in ("A", "B"):
            response = responses.get(side, {})
            if not isinstance(response, dict):
                continue
            raw_name = str(item.get(f"model_name_{side.lower()}") or f"Model {side}")
            model = _display_model(raw_name, alias_lookup)
            common = {
                "source": source_name,
                "generated_at": generated_at,
                "prompt_pack": pack_name,
                "raw_model": raw_name,
                "model": model,
                "judge_model": judge_model,
                "benchmark_category": item_category,
            }
            scores = response.get("scores", {})
            if isinstance(scores, dict):
                for category, value in scores.items():
                    score = _safe_score(value)
                    if score is not None:
                        entries.append({**common, "category": str(category), "score": score})
            overall = _safe_score(response.get("overall"))
            if overall is not None:
                entries.append({**common, "category": "Overall", "score": overall})
    return entries

def _timestamp_key(value: object) -> float:
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, OSError, OverflowError):
        return 0.0


# A (model, category) result is flagged as low-sample when it rests on too few
# judged prompts or too few distinct opponents to support a confident rank.
# See BENCHMARK_VALIDITY_AUDIT.md section 2.2: with the current dataset, most
# categories are a single 5-prompt file against one opponent, and six models
# separated by 0.13 points on that basis is noise, not a ranking.
LOW_SAMPLE_MIN_PROMPTS = 10
LOW_SAMPLE_MIN_OPPONENTS = 2


def _run_opponents(entries: list[dict[str, Any]]) -> dict[tuple[str, str], set[str]]:
    """Map each saved (source file, generated_at) run to the set of models it scored."""
    run_models: dict[tuple[str, str], set[str]] = defaultdict(set)
    for entry in entries:
        if entry["category"] != "Overall":
            continue
        run_models[(str(entry["source"]), str(entry["generated_at"]))].add(str(entry["model"]))
    return run_models


def _sample_coverage(
    category_entries: list[dict[str, Any]],
    run_models: dict[tuple[str, str], set[str]],
    model_name: str,
) -> dict[str, Any]:
    """Report how many prompts and distinct opponents back one (model, category) score.

    A low sample size doesn't invalidate a score, but it means the score should
    not be presented with the same confidence as one backed by broad coverage.
    """
    prompt_count = len(category_entries)
    run_keys = {(str(entry["source"]), str(entry["generated_at"])) for entry in category_entries}
    opponents: set[str] = set()
    for key in run_keys:
        opponents.update(run_models.get(key, set()) - {model_name})
    return {
        "prompt_count": prompt_count,
        "opponent_count": len(opponents),
        "opponents": sorted(opponents, key=str.lower),
        "low_sample": prompt_count < LOW_SAMPLE_MIN_PROMPTS or len(opponents) < LOW_SAMPLE_MIN_OPPONENTS,
    }


def _category_metrics(entries: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    for entry in entries:
        grouped[(str(entry["source"]), str(entry["generated_at"]))].append(float(entry["score"]))
    runs = [
        {
            "source": source,
            "generated_at": generated_at,
            "score": sum(scores) / len(scores),
        }
        for (source, generated_at), scores in grouped.items()
        if scores
    ]
    if not runs:
        return {}
    current = max(runs, key=lambda run: (_timestamp_key(run["generated_at"]), run["source"]))
    best = max(runs, key=lambda run: (run["score"], _timestamp_key(run["generated_at"])))
    all_scores = [float(entry["score"]) for entry in entries]
    return {
        "current": round(float(current["score"]), 4),
        "best": round(float(best["score"]), 4),
        "historical_average": round(sum(all_scores) / len(all_scores), 4),
        "count": len(all_scores),
        "current_generated_at": current["generated_at"],
        "current_source": current["source"],
        "best_generated_at": best["generated_at"],
        "best_source": best["source"],
    }


def load_strength_map(
    benchmark_dir: Path = BENCHMARK_DIR,
    aliases_path: Path = ALIASES_PATH,
) -> dict[str, Any]:
    """Expose the standalone dashboard dimensions through a read-only JSON shape."""
    aliases = load_model_aliases(aliases_path)
    lookup = _alias_lookup(aliases)
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    files_read = 0
    for path in sorted(benchmark_dir.glob("*.json")):
        payload = _read_json(path)
        if not payload:
            continue
        fingerprint = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        file_entries = _score_entries(payload, path.name, lookup)
        if file_entries:
            files_read += 1
            entries.extend(file_entries)

    benchmark_categories = sorted(
        {str(entry["benchmark_category"]) for entry in entries}, key=str.lower
    )
    score_categories = sorted(
        {str(entry["category"]) for entry in entries if entry["category"] != "Overall"},
        key=str.lower,
    )
    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        by_model[str(entry["model"])].append(entry)

    models = []
    for model_name, model_entries in by_model.items():
        by_metric: dict[str, list[float]] = defaultdict(list)
        for entry in model_entries:
            by_metric[str(entry["category"])].append(float(entry["score"]))
        categories = [
            {
                "category": category,
                "score": round(sum(scores) / len(scores), 4),
                "count": len(scores),
            }
            for category, scores in by_metric.items()
            if category != "Overall"
        ]
        categories.sort(key=lambda row: row["category"].lower())
        overall_scores = by_metric.get("Overall", [])
        overall = (
            sum(overall_scores) / len(overall_scores)
            if overall_scores
            else sum(row["score"] for row in categories) / len(categories)
            if categories
            else 0.0
        )
        tested = sorted(
            {str(entry["benchmark_category"]) for entry in model_entries}, key=str.lower
        )
        runs_by_key: dict[tuple[str, str, str], list[float]] = defaultdict(list)
        for entry in model_entries:
            if entry["category"] == "Overall":
                runs_by_key[
                    (
                        str(entry["source"]),
                        str(entry["generated_at"]),
                        str(entry["benchmark_category"]),
                    )
                ].append(float(entry["score"]))
        runs = [
            {
                "source": source,
                "generated_at": generated_at,
                "benchmark_category": category,
                "score": round(sum(scores) / len(scores), 2),
            }
            for (source, generated_at, category), scores in runs_by_key.items()
        ]
        runs.sort(key=lambda row: (_timestamp_key(row["generated_at"]), row["source"]), reverse=True)
        strongest = max(categories, key=lambda row: row["score"]) if categories else None
        weakest = min(categories, key=lambda row: row["score"]) if categories else None
        models.append(
            {
                "model": model_name,
                "overall": round(overall, 2),
                "comparisons": len(overall_scores),
                "run_count": len(runs),
                "categories": categories,
                "strongest": strongest,
                "weakest": weakest,
                "tested_categories": tested,
                "missing_categories": [
                    category for category in benchmark_categories if category not in tested
                ],
                "categories_total": len(benchmark_categories),
                "latest_run": runs[0]["generated_at"] if runs else "",
                "runs": runs,
                "raw_models": sorted(
                    {str(entry["raw_model"]) for entry in model_entries}, key=str.lower
                ),
            }
        )
    models.sort(key=lambda row: (-row["overall"], row["model"].lower()))

    run_models = _run_opponents(entries)
    leaderboard_rows = []
    for model_name, model_entries in by_model.items():
        category_scores: dict[str, Any] = {}
        for benchmark_category in benchmark_categories:
            category_entries = [
                entry
                for entry in model_entries
                if entry["benchmark_category"] == benchmark_category
                and entry["category"] == "Overall"
            ]
            if not category_entries:
                category_entries = [
                    entry
                    for entry in model_entries
                    if entry["benchmark_category"] == benchmark_category
                    and entry["category"] != "Overall"
                ]
            metrics = _category_metrics(category_entries)
            if metrics:
                metrics.update(_sample_coverage(category_entries, run_models, model_name))
                category_scores[benchmark_category] = metrics
        tested = sorted(category_scores, key=str.lower)
        # Divide by the categories this model was actually tested in, not by every
        # category that exists anywhere in the dataset — a model missing a category
        # is untested there, not a zero. See BENCHMARK_VALIDITY_AUDIT.md section 2.1.
        total = len(tested) or 1
        low_sample_categories = sorted(
            (category for category, metrics in category_scores.items() if metrics.get("low_sample")),
            key=str.lower,
        )
        leaderboard_rows.append(
            {
                "model": model_name,
                "current": round(
                    sum(row["current"] for row in category_scores.values()) / total, 4
                ),
                "best": round(sum(row["best"] for row in category_scores.values()) / total, 4),
                "historical_average": round(
                    sum(row["historical_average"] for row in category_scores.values()) / total,
                    4,
                ),
                "category_scores": category_scores,
                "tested_categories": tested,
                "missing_categories": [
                    category for category in benchmark_categories if category not in tested
                ],
                "categories_total": len(benchmark_categories),
                "low_sample": bool(low_sample_categories),
                "low_sample_categories": low_sample_categories,
                "run_count": len(
                    {
                        (entry["source"], entry["generated_at"])
                        for entry in model_entries
                        if entry["category"] == "Overall"
                    }
                ),
            }
        )
    leaderboard_rows.sort(key=lambda row: (-row["current"], row["model"].lower()))
    return {
        "models": models,
        "leaderboard": leaderboard_rows,
        "score_entries": entries,
        "benchmark_categories": benchmark_categories,
        "score_categories": score_categories,
        "aliases": aliases,
        "files_read": files_read,
    }

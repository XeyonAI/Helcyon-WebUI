"""Server-backed exact-turn capture for the native Helcyon-Bench workflow."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from chat_message_metadata import load_verified_chat_by_id


REPO_ROOT = Path(__file__).resolve().parent
RUNS_DIR = REPO_ROOT / "benchmark_runs"
INTEGRATED_SESSION_PATH = RUNS_DIR / "integrated_session.json"
# Deliberately outside RUNS_DIR / benchmark_runs: that folder is a single mutable
# autosave scratch file (see save_integrated_session below) excluded wholesale from
# both backup scripts. Saved sessions are permanent, user-intentional exports and
# get their own directory so they are never confused with, or swept up by, that
# private-data exclusion rule.
SESSIONS_DIR = REPO_ROOT / "benchmark_sessions"
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
_SESSION_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,159}\.json$")
_SAMPLING_KEYS = (
    "temperature",
    "max_tokens",
    "top_p",
    "min_p",
    "top_k",
    "repeat_penalty",
    "repeat_last_n",
    "dry_multiplier",
    "dry_base",
    "dry_allowed_length",
    "dry_penalty_last_n",
    "frequency_penalty",
    "presence_penalty",
    "eos_logit_bias",
)


class CaptureError(ValueError):
    def __init__(self, code: str, message: str, status: str = "invalidated"):
        super().__init__(message)
        self.code = code
        self.status = status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        suffix=".tmp",
        prefix=".benchcapture_",
        dir=str(path.parent),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _association_path(association_id: str, runs_dir: Path = RUNS_DIR) -> Path:
    if not _SAFE_ID_RE.fullmatch(str(association_id or "")):
        raise CaptureError("invalid_association", "The benchmark association identifier is invalid.")
    digest = hashlib.sha256(association_id.encode("utf-8")).hexdigest()
    return runs_dir / f"{digest}.json"


def _load_association(association_id: str, runs_dir: Path = RUNS_DIR) -> dict[str, Any]:
    path = _association_path(association_id, runs_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise CaptureError(
            "association_missing",
            "This submitted benchmark association no longer exists.",
        ) from error
    except (OSError, json.JSONDecodeError) as error:
        raise CaptureError(
            "association_unreadable",
            "The submitted benchmark association could not be read safely.",
        ) from error
    if not isinstance(payload, dict):
        raise CaptureError("association_unreadable", "The benchmark association is malformed.")
    return payload


def _save_association(payload: dict[str, Any], runs_dir: Path = RUNS_DIR) -> None:
    payload["updated_at"] = _now()
    _atomic_write_json(_association_path(str(payload["association_id"]), runs_dir), payload)


def load_integrated_session(
    session_path: Path = INTEGRATED_SESSION_PATH,
) -> dict[str, Any]:
    try:
        payload = json.loads(session_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as error:
        raise CaptureError(
            "session_unreadable",
            "The integrated Helcyon-Bench session could not be read safely.",
            "error",
        ) from error
    if not isinstance(payload, dict):
        raise CaptureError(
            "session_unreadable",
            "The integrated Helcyon-Bench session is malformed.",
            "error",
        )
    return payload


def save_integrated_session(
    data: dict[str, Any],
    session_path: Path = INTEGRATED_SESSION_PATH,
) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise CaptureError(
            "invalid_session",
            "The integrated Helcyon-Bench session must be a JSON object.",
            "error",
        )
    forbidden_keys = {"api_key", "authorization", "api-key"}

    def reject_credentials(value: Any) -> None:
        if isinstance(value, dict):
            if forbidden_keys.intersection(str(key).lower() for key in value):
                raise CaptureError(
                    "session_contains_credentials",
                    "Judge credentials cannot be stored in the integrated Bench session.",
                    "error",
                )
            for item in value.values():
                reject_credentials(item)
        elif isinstance(value, list):
            for item in value:
                reject_credentials(item)

    reject_credentials(data)
    try:
        encoded = json.dumps(data, ensure_ascii=False)
    except (TypeError, ValueError) as error:
        raise CaptureError(
            "invalid_session",
            "The integrated Helcyon-Bench session contains unsupported values.",
            "error",
        ) from error
    if len(encoded.encode("utf-8")) > 5 * 1024 * 1024:
        raise CaptureError(
            "session_too_large",
            "The integrated Helcyon-Bench session is too large to save safely.",
            "error",
        )
    payload = json.loads(encoded)
    payload["schema_version"] = 1
    payload["updated_at"] = _now()
    _atomic_write_json(session_path, payload)
    return payload


def persist_captured_response(
    captured: dict[str, Any],
    session_path: Path = INTEGRATED_SESSION_PATH,
) -> dict[str, Any]:
    association = captured.get("association")
    if not isinstance(association, dict):
        raise CaptureError(
            "capture_session_missing",
            "The captured response has no verified benchmark association.",
            "error",
        )
    pack_id = str(association.get("benchmark_id") or "")
    test_id = str(association.get("test_id") or "")
    slot = str(association.get("candidate_model_slot") or "").upper()
    if not pack_id or not test_id or slot not in {"A", "B"}:
        raise CaptureError(
            "capture_session_missing",
            "The captured response cannot be placed into an integrated Bench draft.",
            "error",
        )

    state = load_integrated_session(session_path)
    response_key = f"{pack_id}:{test_id}:{slot}"
    responses = state.setdefault("responses", {})
    response_meta = state.setdefault("response_meta", {})
    run_ids = state.setdefault("run_ids", {})
    associations = state.setdefault("associations", {})
    if (
        not isinstance(responses, dict)
        or not isinstance(response_meta, dict)
        or not isinstance(run_ids, dict)
        or not isinstance(associations, dict)
    ):
        raise CaptureError(
            "session_unreadable",
            "The integrated Helcyon-Bench response state is malformed.",
            "error",
        )
    responses[response_key] = str(captured.get("response_text") or "")
    response_meta[response_key] = {
        "origin": "captured",
        "association_id": str(association.get("association_id") or ""),
        "assistant_message_id": str(captured.get("assistant_message_id") or ""),
        "captured_at": str(association.get("captured_at") or _now()),
    }
    run_ids[pack_id] = str(association.get("run_id") or run_ids.get(pack_id) or "")
    association_key = "::".join(
        (
            str(association.get("run_id") or ""),
            pack_id,
            test_id,
            slot,
        )
    )
    associations[association_key] = association
    state.update(
        {
            "selected_view": "test",
            "selected_category": str(association.get("category") or ""),
            "selected_pack": pack_id,
            "selected_pack_name": str(association.get("benchmark_name") or ""),
            "active_prompt_test_id": test_id,
            "active_prompt_title": str(association.get("test_title") or ""),
            "active_prompt_number": association.get("prompt_number") or 0,
            "current_candidate_slot": slot,
            "last_focused_response_key": response_key,
            "focus_response_on_return": True,
            "active_association_id": str(association.get("association_id") or ""),
        }
    )
    return save_integrated_session(state, session_path)


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content", "")
    if isinstance(content, list):
        return " ".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        ).strip()
    return str(content or "").strip()


def _settings_snapshot(repo_root: Path) -> dict[str, Any]:
    try:
        settings = json.loads((repo_root / "settings.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(settings, dict):
        return {}
    backend = str(settings.get("backend_mode") or "local")
    model = (
        settings.get("openai_model")
        if backend == "openai"
        else settings.get("anthropic_model")
        if backend == "anthropic"
        else settings.get("llama_last_model")
    )
    llama_args = settings.get("llama_args") if isinstance(settings.get("llama_args"), dict) else {}
    return {
        "backend_mode": backend,
        "model": model,
        "active_system_prompt": settings.get("active_system_prompt"),
        "lora_path": settings.get("lora_path") or None,
        "context_size": llama_args.get("ctx_size"),
        "sampling": {key: settings.get(key) for key in _SAMPLING_KEYS if key in settings},
    }


def register_association(
    data: dict[str, Any],
    parser: Callable[[str, str], list[dict[str, Any]]],
    *,
    repo_root: Path = REPO_ROOT,
    runs_dir: Path = RUNS_DIR,
) -> dict[str, Any]:
    required = (
        "association_id",
        "run_id",
        "benchmark_id",
        "test_id",
        "category",
        "candidate_model_slot",
        "target_model",
        "chat_id",
        "target_chat_filename",
        "original_prompt",
        "submitted_prompt",
        "source_user_message_id",
        "source_user_position",
        "submitted_at",
    )
    missing = [key for key in required if data.get(key) in (None, "")]
    if missing:
        raise CaptureError(
            "missing_association_data",
            f"Capture association is missing: {', '.join(missing)}.",
        )
    slot = str(data["candidate_model_slot"]).upper()
    if slot not in {"A", "B"}:
        raise CaptureError("invalid_candidate", "Candidate slot must be A or B.")
    for key in ("association_id", "run_id", "benchmark_id", "test_id", "source_user_message_id"):
        if not _SAFE_ID_RE.fullmatch(str(data[key])):
            raise CaptureError("invalid_association_data", f"{key} contains unsupported characters.")
    position = data["source_user_position"]
    if not isinstance(position, int) or isinstance(position, bool) or position < 0:
        raise CaptureError("invalid_source_position", "The source user-message position is invalid.")

    messages, chat_meta, chat_error = load_verified_chat_by_id(
        repo_root,
        str(data["chat_id"]),
        parser,
    )
    if chat_error or messages is None or chat_meta is None:
        raise CaptureError("chat_unverified", chat_error or "The exact source chat could not be verified.")
    if position >= len(messages):
        raise CaptureError("source_deleted", "The tracked source user message no longer exists.")
    source = messages[position]
    if (
        source.get("role") != "user"
        or source.get("message_id") != data["source_user_message_id"]
    ):
        raise CaptureError(
            "source_mismatch",
            "The tracked source position no longer contains the exact submitted user message.",
        )

    association = {
        "schema_version": 1,
        "association_id": str(data["association_id"]),
        "run_id": str(data["run_id"]),
        "benchmark_id": str(data["benchmark_id"]),
        "benchmark_name": str(data.get("benchmark_name") or ""),
        "test_id": str(data["test_id"]),
        "test_title": str(data.get("test_title") or ""),
        "prompt_number": (
            data.get("prompt_number")
            if isinstance(data.get("prompt_number"), int)
            and not isinstance(data.get("prompt_number"), bool)
            else 0
        ),
        "category": str(data["category"]),
        "candidate_model_slot": slot,
        "candidate_model_name": str(data.get("candidate_model_name") or ""),
        "target_model": str(data["target_model"]),
        "chat_id": str(data["chat_id"]),
        "target_chat_filename": str(data["target_chat_filename"]),
        "resolved_chat_filename": str(chat_meta.get("resolved_filename") or ""),
        "original_prompt": str(data["original_prompt"]),
        "submitted_prompt": str(data["submitted_prompt"]),
        "source_user_message_id": str(data["source_user_message_id"]),
        "source_user_position": position,
        "source_user_fingerprint": str(
            (chat_meta.get("messages") or [])[position].get("fingerprint") or ""
        ),
        "submitted_at": str(data["submitted_at"]),
        "generation_status": "submitted",
        "expected_assistant_message_id": None,
        "created_at": _now(),
        "model_metadata": {
            **_settings_snapshot(repo_root),
            "target_model_label": str(data["target_model"]),
            "character": str(data.get("character") or ""),
        },
    }
    _save_association(association, runs_dir)
    return association


def update_association_status(
    association_id: str,
    status: str,
    *,
    assistant_message_id: str | None = None,
    runs_dir: Path = RUNS_DIR,
) -> dict[str, Any]:
    allowed = {"submitted", "generating", "cancelled", "error", "ready_to_capture"}
    if status not in allowed:
        raise CaptureError("invalid_status", "The requested capture state is invalid.")
    association = _load_association(association_id, runs_dir)
    association["generation_status"] = status
    if assistant_message_id:
        association["expected_assistant_message_id"] = assistant_message_id
    if status == "generating":
        association["generation_started_at"] = _now()
    if status in {"cancelled", "error", "ready_to_capture"}:
        association["generation_finished_at"] = _now()
    _save_association(association, runs_dir)
    return association


def resolve_association(
    association_id: str,
    parser: Callable[[str, str], list[dict[str, Any]]],
    *,
    repo_root: Path = REPO_ROOT,
    runs_dir: Path = RUNS_DIR,
) -> dict[str, Any]:
    association = _load_association(association_id, runs_dir)
    messages, chat_meta, chat_error = load_verified_chat_by_id(
        repo_root,
        str(association.get("chat_id") or ""),
        parser,
    )
    if chat_error or messages is None or chat_meta is None:
        raise CaptureError("chat_unverified", chat_error or "The source chat cannot be verified.")

    position = association.get("source_user_position")
    if not isinstance(position, int) or position < 0 or position >= len(messages):
        raise CaptureError("source_deleted", "The tracked source user message was deleted.")
    source = messages[position]
    if source.get("message_id") != association.get("source_user_message_id"):
        raise CaptureError(
            "source_mismatch",
            "The tracked source user turn was deleted, reordered, or replaced.",
        )
    if source.get("role") != "user":
        raise CaptureError("source_mismatch", "The tracked source turn is no longer a user message.")
    current_records = chat_meta.get("messages") or []
    current_fingerprint = (
        current_records[position].get("fingerprint")
        if position < len(current_records) and isinstance(current_records[position], dict)
        else None
    )
    if current_fingerprint != association.get("source_user_fingerprint"):
        raise CaptureError(
            "source_rewritten",
            "The tracked submitted user message was edited after its benchmark association was recorded.",
        )

    following = messages[position + 1 :]
    linked = []
    for message in following:
        if message.get("role") == "user":
            break
        if (
            message.get("role") == "assistant"
            and message.get("reply_to_message_id") == association["source_user_message_id"]
        ):
            linked.append(message)
    if len(linked) > 1:
        raise CaptureError(
            "ambiguous_response",
            "Multiple assistant replies are linked to this user turn; capture is blocked.",
        )
    if not linked:
        state = str(association.get("generation_status") or "submitted")
        if state == "cancelled":
            raise CaptureError("generation_cancelled", "Generation was cancelled before a complete response.", "cancelled")
        if state == "error":
            raise CaptureError("generation_error", "Generation failed before a complete response.", "error")
        raise CaptureError(
            "response_incomplete",
            "No completed assistant response is linked to this submitted turn yet.",
            "generating" if state == "generating" else "submitted",
        )

    assistant = linked[0]
    if position + 1 >= len(messages) or messages[position + 1].get("message_id") != assistant.get("message_id"):
        raise CaptureError(
            "not_immediate_response",
            "The linked assistant response is not the immediate reply to the tracked user turn.",
        )
    expected = association.get("expected_assistant_message_id")
    if (
        expected
        and expected != assistant.get("message_id")
        and association.get("generation_status") == "captured"
    ):
        raise CaptureError(
            "response_replaced",
            "The assistant reply changed after it was captured.",
        )
    if assistant.get("generation_status") != "completed":
        state = str(assistant.get("generation_status") or "incomplete")
        raise CaptureError(
            "response_incomplete",
            f"The linked assistant response is {state}, not completed.",
            state,
        )
    response_text = _message_text(assistant)
    if not response_text:
        raise CaptureError("empty_response", "The linked assistant response is empty.", "error")

    association["resolved_chat_filename"] = str(chat_meta.get("resolved_filename") or "")
    return {
        "status": (
            "captured"
            if association.get("generation_status") == "captured"
            else "ready_to_capture"
        ),
        "association": association,
        "response_text": response_text,
        "assistant_message_id": str(assistant.get("message_id") or ""),
        "assistant_metadata": {
            "message_id": assistant.get("message_id"),
            "reply_to_message_id": assistant.get("reply_to_message_id"),
            "generation_status": assistant.get("generation_status"),
            "generation_started_at": assistant.get("generation_started_at"),
            "generation_completed_at": assistant.get("generation_completed_at"),
            "source_chat": chat_meta.get("resolved_filename"),
        },
    }


def capture_association(
    association_id: str,
    parser: Callable[[str, str], list[dict[str, Any]]],
    *,
    candidate_model_slot: str | None = None,
    repo_root: Path = REPO_ROOT,
    runs_dir: Path = RUNS_DIR,
) -> dict[str, Any]:
    resolved = resolve_association(
        association_id,
        parser,
        repo_root=repo_root,
        runs_dir=runs_dir,
    )
    association = resolved["association"]
    requested_slot = str(candidate_model_slot or "").upper()
    if requested_slot and requested_slot != association.get("candidate_model_slot"):
        raise CaptureError(
            "candidate_mismatch",
            f"This response belongs to Model {association.get('candidate_model_slot')}, not Model {requested_slot}.",
        )
    association["generation_status"] = "captured"
    association["expected_assistant_message_id"] = resolved["assistant_message_id"]
    association["captured_at"] = _now()
    association["captured_response"] = resolved["response_text"]
    association["assistant_metadata"] = resolved["assistant_metadata"]
    _save_association(association, runs_dir)
    resolved["status"] = "captured"
    resolved["association"] = association
    return resolved


# --- Named, permanent saved sessions -----------------------------------------
#
# save_integrated_session() above is a single mutable autosave file: it exists so
# a page reload doesn't lose in-progress work, and every change overwrites it. It
# is not a history — clearing responses, resetting the browser, or simply the next
# autosave erases whatever was there. The functions below give the user an
# explicit, intentional "keep this" action that always creates a new, permanently
# reloadable file, mirroring the standalone Helcyon-Bench app's benchmark_sessions
# feature (Helcyon-Bench/app.py: save_benchmark_session / import_benchmark_session).


def _slug_part(value: object, fallback: str) -> str:
    cleaned = str(value or "").strip().lower()
    cleaned = re.sub(r"\s+", "-", cleaned)
    cleaned = re.sub(r"[^a-z0-9-]+", "", cleaned)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    return cleaned or fallback


def saved_session_filename(
    model_a: object,
    model_b: object,
    pack_name: object,
    timestamp: datetime | None = None,
    auto_saved: bool = False,
) -> str:
    stamp = (timestamp or datetime.now(timezone.utc)).strftime("%Y-%m-%d-%H%M%S")
    pack = _slug_part(pack_name, "prompt-pack")
    name_a = _slug_part(model_a, "model-a")
    name_b = _slug_part(model_b, "model-b")
    marker = "auto-" if auto_saved else ""
    return f"hwui-bench-session-{marker}{pack}-{name_a}-vs-{name_b}-{stamp}.json"


def _session_path(filename: object, sessions_dir: Path = SESSIONS_DIR) -> Path:
    name = str(filename or "").strip()
    if not name or not _SESSION_FILENAME_RE.fullmatch(name) or Path(name).name != name:
        raise CaptureError("invalid_session_filename", "The saved session filename is invalid.")
    return sessions_dir / name


def _validate_session_items(raw_items: object) -> list[dict[str, str]]:
    if not isinstance(raw_items, list) or not raw_items:
        raise CaptureError("empty_session", "Add at least one prompt before saving a full session.", "error")
    items: list[dict[str, str]] = []
    has_response = False
    for index, raw in enumerate(raw_items, start=1):
        if not isinstance(raw, dict):
            raise CaptureError("invalid_session", f"Session item {index} must be an object.", "error")
        response_a = str(raw.get("response_a") or "")
        response_b = str(raw.get("response_b") or "")
        if response_a.strip() or response_b.strip():
            has_response = True
        items.append(
            {
                "test_id": str(raw.get("test_id") or f"prompt_{index:02d}"),
                "title": str(raw.get("title") or ""),
                "prompt": str(raw.get("prompt") or ""),
                "response_a": response_a,
                "response_b": response_b,
            }
        )
    if not has_response:
        raise CaptureError(
            "empty_session",
            "Add at least one Model A or Model B response before saving a full session.",
            "error",
        )
    return items


def save_benchmark_session(
    data: dict[str, Any],
    sessions_dir: Path = SESSIONS_DIR,
) -> dict[str, Any]:
    """Save the current Bench working state as a brand-new, permanently reloadable file.

    Always creates a new file rather than overwriting anything, so a saved session
    survives a later autosave cycle, a full response clear, or a browser data reset.
    """
    if not isinstance(data, dict):
        raise CaptureError("invalid_session", "The saved session payload must be a JSON object.", "error")
    pack = data.get("prompt_pack") if isinstance(data.get("prompt_pack"), dict) else {}
    items = _validate_session_items(data.get("items"))
    model_a = str(data.get("model_a") or "").strip()
    model_b = str(data.get("model_b") or "").strip()
    pack_name = str(pack.get("name") or "Prompt Pack").strip() or "Prompt Pack"
    auto_saved = bool(data.get("auto_saved"))
    payload = {
        "app": "HWUI Helcyon-Bench",
        "schema_version": 1,
        "saved_at": _now(),
        "auto_saved": auto_saved,
        "prompt_pack": {
            "id": str(pack.get("id") or ""),
            "name": pack_name,
            "category": str(pack.get("category") or ""),
            "description": str(pack.get("description") or ""),
            "judge_profile": str(pack.get("judge_profile") or ""),
        },
        "model_a": model_a,
        "model_b": model_b,
        "items": items,
    }
    try:
        encoded = json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError) as error:
        raise CaptureError(
            "invalid_session", "The saved session contains unsupported values.", "error"
        ) from error
    if len(encoded.encode("utf-8")) > 5 * 1024 * 1024:
        raise CaptureError("session_too_large", "The saved session is too large to save safely.", "error")

    filename = saved_session_filename(model_a, model_b, pack_name, auto_saved=auto_saved)
    path = sessions_dir / filename
    if path.exists():
        stem = filename[: -len(".json")]
        suffix = 2
        while path.exists():
            path = sessions_dir / f"{stem}-{suffix}.json"
            suffix += 1
    _atomic_write_json(path, payload)
    payload["filename"] = path.name
    return payload


def _session_summary(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    pack = payload.get("prompt_pack") if isinstance(payload.get("prompt_pack"), dict) else {}
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    response_count = sum(
        1
        for item in items
        if isinstance(item, dict)
        and (str(item.get("response_a") or "").strip() or str(item.get("response_b") or "").strip())
    )
    return {
        "filename": path.name,
        "saved_at": str(payload.get("saved_at") or ""),
        "auto_saved": bool(payload.get("auto_saved")),
        "model_a": str(payload.get("model_a") or ""),
        "model_b": str(payload.get("model_b") or ""),
        "prompt_pack_id": str(pack.get("id") or ""),
        "prompt_pack_name": str(pack.get("name") or path.stem),
        "prompt_pack_category": str(pack.get("category") or ""),
        "item_count": len(items),
        "response_count": response_count,
    }


def list_benchmark_sessions(sessions_dir: Path = SESSIONS_DIR) -> list[dict[str, Any]]:
    if not sessions_dir.exists():
        return []
    summaries = []
    for path in sessions_dir.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            continue
        summaries.append(_session_summary(path, payload))
    summaries.sort(key=lambda item: item["saved_at"], reverse=True)
    return summaries


def load_benchmark_session(filename: object, sessions_dir: Path = SESSIONS_DIR) -> dict[str, Any]:
    path = _session_path(filename, sessions_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise CaptureError("session_missing", f"{path.name} was not found.") from error
    except (OSError, json.JSONDecodeError) as error:
        raise CaptureError(
            "session_unreadable", f"{path.name} could not be read safely.", "error"
        ) from error
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise CaptureError("session_unreadable", f"{path.name} is not a valid saved session.", "error")
    payload["filename"] = path.name
    return payload


def delete_benchmark_session(filename: object, sessions_dir: Path = SESSIONS_DIR) -> str:
    path = _session_path(filename, sessions_dir)
    if not path.exists():
        raise CaptureError("session_missing", f"{path.name} was not found.")
    path.unlink()
    return path.name

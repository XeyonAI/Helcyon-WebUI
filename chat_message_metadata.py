"""Stable chat/message identity sidecars for HWUI's existing text chat files.

The visible chat transcript remains the source of message content. This module
stores only identity, relationship, completion-state, and integrity metadata in
an adjacent hidden directory so legacy ``.txt`` chats keep their current format.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable


META_DIRNAME = ".hwui_chat_meta"
SCHEMA_VERSION = 1
_COPIED_FIELDS = (
    "message_id",
    "reply_to_message_id",
    "generation_status",
    "generation_started_at",
    "generation_completed_at",
    "is_opening_line",
)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        suffix=".tmp",
        prefix=".chatmeta_",
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


def _metadata_path(chats_dir: str | Path, filename: str) -> Path:
    digest = hashlib.sha256(filename.encode("utf-8")).hexdigest()
    return Path(chats_dir) / META_DIRNAME / f"{digest}.json"


def _chat_path(chats_dir: str | Path, filename: str) -> Path:
    return Path(chats_dir) / filename


def _chat_sha256(chats_dir: str | Path, filename: str) -> str:
    return hashlib.sha256(_chat_path(chats_dir, filename).read_bytes()).hexdigest()


def _canonical_text(text: str) -> str:
    # Fingerprints are compared against content re-read through the chat-file
    # parser, which strips leading/trailing whitespace from every line. Both
    # sides must be canonicalised the same way or any message with indented
    # lines (code blocks) fails verification forever.
    return "\n".join(
        line.strip() for line in text.replace("\r\n", "\n").split("\n")
    ).strip()


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content", "")
    if not isinstance(content, list):
        return _canonical_text(str(content or ""))
    text_parts = [
        str(part.get("text") or "")
        for part in content
        if isinstance(part, dict) and part.get("type") == "text"
    ]
    has_image = any(
        isinstance(part, dict) and part.get("type") == "image_url"
        for part in content
    )
    text = " ".join(text_parts).strip()
    if has_image:
        text = f"{text} [image]".strip()
    return _canonical_text(text)


def message_fingerprint(message: dict[str, Any]) -> str:
    identity = {
        "role": str(message.get("role") or ""),
        "speaker": str(message.get("speaker") or ""),
        "timestamp": str(message.get("timestamp") or ""),
        "content": _message_text(message),
    }
    encoded = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_chat_metadata(chats_dir: str | Path, filename: str) -> dict[str, Any] | None:
    path = _metadata_path(chats_dir, filename)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def save_chat_metadata(
    chats_dir: str | Path,
    filename: str,
    messages: list[dict[str, Any]],
    *,
    chat_id: str | None = None,
) -> dict[str, Any]:
    existing = load_chat_metadata(chats_dir, filename) or {}
    stable_chat_id = str(chat_id or existing.get("chat_id") or uuid.uuid4())
    records = []
    for message in messages:
        record = {
            "fingerprint": message_fingerprint(message),
            "message_id": str(message.get("message_id") or uuid.uuid4()),
        }
        for field in _COPIED_FIELDS:
            value = message.get(field)
            if value not in (None, "", False):
                record[field] = value
        records.append(record)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "chat_id": stable_chat_id,
        "filename": filename,
        "chat_sha256": _chat_sha256(chats_dir, filename),
        "messages": records,
    }
    _atomic_write_json(_metadata_path(chats_dir, filename), payload)
    return payload


def ensure_chat_metadata(
    chats_dir: str | Path,
    filename: str,
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = load_chat_metadata(chats_dir, filename)
    if payload:
        return payload
    return save_chat_metadata(chats_dir, filename, messages)


def merge_verified_message_metadata(
    chats_dir: str | Path,
    filename: str,
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, str | None]:
    payload = load_chat_metadata(chats_dir, filename)
    if not payload:
        payload = save_chat_metadata(chats_dir, filename, messages)
    try:
        if payload.get("chat_sha256") != _chat_sha256(chats_dir, filename):
            return messages, payload, "chat file changed outside the matching metadata save"
    except OSError:
        return messages, payload, "chat file is unavailable"

    records = payload.get("messages")
    if not isinstance(records, list) or len(records) != len(messages):
        return messages, payload, "chat structure no longer matches its identity metadata"

    merged = []
    for message, record in zip(messages, records):
        if (
            not isinstance(record, dict)
            or record.get("fingerprint") != message_fingerprint(message)
        ):
            return messages, payload, "chat message content or ordering has changed"
        enriched = dict(message)
        for field in _COPIED_FIELDS:
            if field in record:
                enriched[field] = record[field]
        merged.append(enriched)
    return merged, payload, None


def move_chat_metadata(
    source_dir: str | Path,
    source_filename: str,
    target_dir: str | Path,
    target_filename: str,
) -> None:
    source = _metadata_path(source_dir, source_filename)
    if not source.exists():
        return
    payload = load_chat_metadata(source_dir, source_filename)
    if not payload:
        return
    payload["filename"] = target_filename
    target = _metadata_path(target_dir, target_filename)
    _atomic_write_json(target, payload)
    source.unlink(missing_ok=True)


def delete_chat_metadata(chats_dir: str | Path, filename: str) -> None:
    _metadata_path(chats_dir, filename).unlink(missing_ok=True)


def chat_directories(repo_root: str | Path) -> list[Path]:
    root = Path(repo_root)
    directories = [root / "chats"]
    projects = root / "projects"
    if projects.is_dir():
        directories.extend(
            path / "chats"
            for path in projects.iterdir()
            if path.is_dir() and (path / "chats").is_dir()
        )
    return directories


def find_chat_by_id(
    repo_root: str | Path,
    chat_id: str,
) -> tuple[Path, str, dict[str, Any]] | None:
    matches = []
    for chats_dir in chat_directories(repo_root):
        meta_dir = chats_dir / META_DIRNAME
        if not meta_dir.is_dir():
            continue
        for path in meta_dir.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict) or payload.get("chat_id") != chat_id:
                continue
            filename = str(payload.get("filename") or "")
            if filename and _chat_path(chats_dir, filename).is_file():
                matches.append((chats_dir, filename, payload))
    return matches[0] if len(matches) == 1 else None


def load_verified_chat_by_id(
    repo_root: str | Path,
    chat_id: str,
    parser: Callable[[str, str], list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None, str | None]:
    found = find_chat_by_id(repo_root, chat_id)
    if not found:
        return None, None, "the exact source chat was deleted, moved without metadata, or is ambiguous"
    chats_dir, filename, payload = found
    filepath = chats_dir / filename
    try:
        messages = parser(str(filepath), filename)
    except (OSError, ValueError) as error:
        return None, payload, f"the exact source chat could not be read: {error}"
    merged, verified_payload, error = merge_verified_message_metadata(
        chats_dir,
        filename,
        messages,
    )
    if error:
        return None, verified_payload, error
    result_meta = dict(verified_payload or {})
    result_meta["resolved_filename"] = filename
    result_meta["resolved_chats_dir"] = str(chats_dir)
    return merged, result_meta, None

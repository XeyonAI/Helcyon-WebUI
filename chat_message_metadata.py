"""Stable chat/message identity sidecars for HWUI's existing text chat files.

The visible chat transcript remains the source of message content. This module
stores identity, relationship, completion-state, integrity, and chat-image file
references in an adjacent hidden directory so legacy ``.txt`` chats keep their
current format. Image bytes live separately under ``chat_images/<chat-id>/``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable


META_DIRNAME = ".hwui_chat_meta"
CHAT_IMAGES_DIRNAME = "chat_images"
SCHEMA_VERSION = 1
_COPIED_FIELDS = (
    "message_id",
    "reply_to_message_id",
    "generation_status",
    "generation_started_at",
    "generation_completed_at",
    "is_opening_line",
    "hasImage",
    "previewUrls",
)

_IMAGE_DATA_URL_RE = re.compile(
    r"^data:(image/(?:jpeg|png|webp|gif|bmp));base64,([A-Za-z0-9+/=\r\n]+)$",
    re.IGNORECASE,
)
_IMAGE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
}


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


def _chat_images_root(chats_dir: str | Path) -> Path:
    return Path(chats_dir) / CHAT_IMAGES_DIRNAME


def _legacy_chat_images_dir(chats_dir: str | Path, chat_id: str) -> Path:
    safe_chat_id = re.sub(r"[^A-Za-z0-9_-]", "", str(chat_id))
    if not safe_chat_id:
        raise ValueError("Invalid chat id for image storage")
    return _chat_images_root(chats_dir) / safe_chat_id


def _project_image_prefix(chats_dir: str | Path) -> str:
    chats_path = Path(chats_dir)
    if chats_path.parent.parent.name.lower() == "projects":
        project_name = chats_path.parent.name
    else:
        project_name = "global"
    return re.sub(r"[^A-Za-z0-9_-]+", "-", project_name).strip("-_") or "global"


def _next_chat_image_path(chats_dir: str | Path, extension: str) -> Path:
    image_root = _chat_images_root(chats_dir)
    image_root.mkdir(parents=True, exist_ok=True)
    prefix = _project_image_prefix(chats_dir)
    pattern = re.compile(
        rf"^{re.escape(prefix)}-chat-img-(\d+)(?:\.[A-Za-z0-9]+)?$",
        re.IGNORECASE,
    )
    highest = 0
    for path in image_root.iterdir():
        if not path.is_file():
            continue
        match = pattern.fullmatch(path.name)
        if match:
            highest = max(highest, int(match.group(1)))
    return image_root / f"{prefix}-chat-img-{highest + 1:03d}{extension}"


def _persisted_image_path(
    chats_dir: str | Path,
    chat_id: str,
    url: str,
) -> Path | None:
    match = re.fullmatch(
        rf"/chats/image/{re.escape(str(chat_id))}/([A-Za-z0-9_.-]+)",
        str(url or ""),
    )
    if not match:
        return None
    image_name = match.group(1)
    flat_path = _chat_images_root(chats_dir) / image_name
    if flat_path.is_file():
        return flat_path
    legacy_path = _legacy_chat_images_dir(chats_dir, chat_id) / image_name
    return legacy_path if legacy_path.is_file() else None


def _persist_preview_urls(
    chats_dir: str | Path,
    chat_id: str,
    message: dict[str, Any],
    existing_urls: list[str] | None = None,
) -> list[str]:
    urls = message.get("previewUrls")
    if not isinstance(urls, list):
        return []
    persisted = []
    for index, url in enumerate(urls):
        if not isinstance(url, str):
            continue
        if url.startswith("/chats/image/"):
            persisted.append(url)
            continue
        match = _IMAGE_DATA_URL_RE.fullmatch(url.strip())
        if not match:
            continue
        mime_type = match.group(1).lower()
        extension = _IMAGE_EXTENSIONS.get(mime_type)
        if not extension:
            continue
        try:
            image_bytes = base64.b64decode(match.group(2), validate=True)
        except (ValueError, TypeError):
            continue
        if not image_bytes:
            continue
        existing_url = (
            existing_urls[index]
            if isinstance(existing_urls, list) and index < len(existing_urls)
            else None
        )
        existing_path = _persisted_image_path(
            chats_dir, chat_id, existing_url
        ) if existing_url else None
        image_path = (
            existing_path
            if existing_path and existing_path.suffix.lower() == extension
            else _next_chat_image_path(chats_dir, extension)
        )
        image_dir = image_path.parent
        image_name = image_path.name
        fd, temporary = tempfile.mkstemp(
            suffix=".tmp", prefix=".chatimage_", dir=str(image_dir)
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(image_bytes)
            os.replace(temporary, image_path)
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        persisted.append(f"/chats/image/{chat_id}/{image_name}")
    return persisted


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
    if not isinstance(payload, dict):
        return None
    chat_id = str(payload.get("chat_id") or "")
    if not chat_id:
        return payload
    changed = False
    legacy_dirs = set()
    for record in payload.get("messages") or []:
        if not isinstance(record, dict) or not isinstance(record.get("previewUrls"), list):
            continue
        migrated_urls = []
        for url in record["previewUrls"]:
            image_path = _persisted_image_path(chats_dir, chat_id, url)
            if image_path and image_path.parent == _legacy_chat_images_dir(chats_dir, chat_id):
                target = _next_chat_image_path(chats_dir, image_path.suffix.lower())
                os.replace(image_path, target)
                migrated_urls.append(f"/chats/image/{chat_id}/{target.name}")
                legacy_dirs.add(image_path.parent)
                changed = True
            else:
                migrated_urls.append(url)
        record["previewUrls"] = migrated_urls
    if changed:
        _atomic_write_json(path, payload)
        for legacy_dir in legacy_dirs:
            try:
                legacy_dir.rmdir()
            except OSError:
                pass
    return payload


def save_chat_metadata(
    chats_dir: str | Path,
    filename: str,
    messages: list[dict[str, Any]],
    *,
    chat_id: str | None = None,
) -> dict[str, Any]:
    existing = load_chat_metadata(chats_dir, filename) or {}
    stable_chat_id = str(chat_id or existing.get("chat_id") or uuid.uuid4())
    existing_records = existing.get("messages") or []
    records = []
    for index, message in enumerate(messages):
        metadata_message = dict(message)
        existing_record = existing_records[index] if index < len(existing_records) else {}
        if not isinstance(existing_record, dict) or (
            existing_record.get("message_id") != message.get("message_id")
            and existing_record.get("fingerprint") != message_fingerprint(message)
        ):
            existing_record = {}
        persisted_urls = _persist_preview_urls(
            chats_dir,
            stable_chat_id,
            metadata_message,
            existing_record.get("previewUrls"),
        )
        if persisted_urls:
            metadata_message["previewUrls"] = persisted_urls
        record = {
            "fingerprint": message_fingerprint(message),
            "message_id": str(message.get("message_id") or uuid.uuid4()),
        }
        for field in _COPIED_FIELDS:
            value = metadata_message.get(field)
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
    if os.path.abspath(str(source_dir)) != os.path.abspath(str(target_dir)):
        chat_id = str(payload.get("chat_id") or "")
        if chat_id:
            for record in payload.get("messages") or []:
                if not isinstance(record, dict) or not isinstance(record.get("previewUrls"), list):
                    continue
                moved_urls = []
                for url in record["previewUrls"]:
                    source_image = _persisted_image_path(source_dir, chat_id, url)
                    if source_image:
                        target_image = _next_chat_image_path(target_dir, source_image.suffix.lower())
                        shutil.move(str(source_image), str(target_image))
                        moved_urls.append(f"/chats/image/{chat_id}/{target_image.name}")
                    else:
                        moved_urls.append(url)
                record["previewUrls"] = moved_urls
    payload["filename"] = target_filename
    target = _metadata_path(target_dir, target_filename)
    _atomic_write_json(target, payload)
    source.unlink(missing_ok=True)


def delete_chat_metadata(chats_dir: str | Path, filename: str) -> None:
    payload = load_chat_metadata(chats_dir, filename) or {}
    _metadata_path(chats_dir, filename).unlink(missing_ok=True)
    chat_id = str(payload.get("chat_id") or "")
    if chat_id:
        for record in payload.get("messages") or []:
            if not isinstance(record, dict):
                continue
            for url in record.get("previewUrls") or []:
                image_path = _persisted_image_path(chats_dir, chat_id, url)
                if image_path:
                    image_path.unlink(missing_ok=True)
        legacy_dir = _legacy_chat_images_dir(chats_dir, chat_id)
        try:
            legacy_dir.rmdir()
        except OSError:
            pass


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

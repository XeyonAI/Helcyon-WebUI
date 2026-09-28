"""Phone (Web Push) notifications for Random Check-ins.

Desktop toasts stay page → Electron (HWUI-Launcher/checkin-notifications.js);
this module is the independent phone channel. After
/api/random-checkins/complete records the first successful completion of a
claimed check-in, the server reads the saved ``automatic_checkin`` message back
from disk by its ``checkin_id`` and pushes it to every registered phone. The
title and body are never taken from the browser.

Delivery is best-effort and runs off the request thread: nothing here can
change whether a check-in was saved or completed.

Private per-installation data lives in users/ (git-ignored, never shipped in
the public archive):
  users/web_push_vapid.json          VAPID key pair identifying this server
  users/web_push_subscriptions.json  registered phones + recently notified IDs
"""

import base64
import binascii
import hashlib
import json
import os
import re
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import user_config
from chat_message_metadata import load_chat_metadata, merge_verified_message_metadata

TITLE_MAX = 64
BODY_MAX = 180   # same limits as the desktop toast
MAX_SUBSCRIPTIONS = 10
NOTIFIED_IDS_MAX = 256
LABEL_MAX = 60
ENDPOINT_MAX = 2048
PUSH_TTL_SECONDS = 12 * 60 * 60
PUSH_TIMEOUT_SECONDS = 10
# VAPID "sub" claim: a contact the push service may use. Stored with the keys
# so an installation can edit it; this placeholder is only the default.
VAPID_SUBJECT = "mailto:hwui-notifications@example.com"
VAPID_FILENAME = "web_push_vapid.json"
SUBSCRIPTIONS_FILENAME = "web_push_subscriptions.json"

# Registration is limited to browser push services. A subscription endpoint is
# a URL this server will POST to, so an arbitrary host would turn registration
# into a request-forwarding primitive.
_PUSH_HOSTS = frozenset({
    "fcm.googleapis.com",                 # Chrome / Chromium / Samsung Internet (Android)
    "android.googleapis.com",             # legacy Chrome endpoints
    "updates.push.services.mozilla.com",  # Firefox
    "web.push.apple.com",                 # Safari / iOS home-screen apps
})
_PUSH_HOST_SUFFIXES = (".push.services.mozilla.com", ".push.apple.com", ".notify.windows.com")

_store_lock = threading.RLock()
_vapid_lock = threading.Lock()


class SubscriptionGone(Exception):
    """The push service reports the subscription no longer exists (404/410)."""


def _log(message):
    try:
        print(f"📱 Check-in push: {message}")
    except Exception:
        pass


# --------------------------------------------------------------------------
# Text shaping
# --------------------------------------------------------------------------
def truncate_text(value, maximum):
    """Collapse whitespace and cut to ``maximum`` chars with an ellipsis (as the desktop toast)."""
    text = re.sub(r"\s+", " ", str(value if value is not None else "")).strip()
    if len(text) <= maximum:
        return text
    return text[: maximum - 1].rstrip() + "…"


def _message_text(content):
    if isinstance(content, list):
        return " ".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return str(content or "")


# --------------------------------------------------------------------------
# Private storage
# --------------------------------------------------------------------------
def _users_dir():
    return Path(user_config.USERS_DIR)


def _read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".web_push_", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def _b64url_encode(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(value):
    if not isinstance(value, str) or not value or len(value) > 256:
        return None
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (binascii.Error, ValueError):
        return None


def _now_iso():
    return datetime.now().isoformat(timespec="seconds")


def _load_store():
    store = _read_json(_users_dir() / SUBSCRIPTIONS_FILENAME) or {}
    subscriptions = store.get("subscriptions")
    notified = store.get("notified_checkin_ids")
    return {
        "subscriptions": [s for s in subscriptions if isinstance(s, dict)] if isinstance(subscriptions, list) else [],
        "notified_checkin_ids": [str(i) for i in notified] if isinstance(notified, list) else [],
    }


def _save_store(store):
    _write_json(_users_dir() / SUBSCRIPTIONS_FILENAME, store)


# --------------------------------------------------------------------------
# Availability + VAPID keys
# --------------------------------------------------------------------------
def push_library_available():
    try:
        import pywebpush  # noqa: F401
        import py_vapid  # noqa: F401
    except ImportError:
        return False
    return True


def _valid_vapid(data):
    if not isinstance(data, dict):
        return False
    private = _b64url_decode(data.get("private_key"))
    public = _b64url_decode(data.get("public_key"))
    return bool(private and len(private) == 32 and public and len(public) == 65 and public[0] == 4)


def get_vapid_keys():
    """Return this installation's VAPID key pair, generating it on first use.

    Regenerating (missing/corrupt file) invalidates every existing phone
    subscription, which is bound to the old public key, so they are cleared.
    """
    path = _users_dir() / VAPID_FILENAME
    with _vapid_lock:
        data = _read_json(path)
        if _valid_vapid(data):
            return data
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        key = ec.generate_private_key(ec.SECP256R1())
        data = {
            "private_key": _b64url_encode(key.private_numbers().private_value.to_bytes(32, "big")),
            "public_key": _b64url_encode(key.public_key().public_bytes(
                serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
            )),
            "subject": VAPID_SUBJECT,
            "created_at": _now_iso(),
        }
        _write_json(path, data)
        with _store_lock:
            store = _load_store()
            if store["subscriptions"]:
                _log("new VAPID keys generated; clearing subscriptions bound to the old key")
                store["subscriptions"] = []
                _save_store(store)
        return data


# --------------------------------------------------------------------------
# Subscriptions
# --------------------------------------------------------------------------
def _allowed_push_host(host):
    host = (host or "").lower().rstrip(".")
    return host in _PUSH_HOSTS or any(host.endswith(suffix) for suffix in _PUSH_HOST_SUFFIXES)


def validate_subscription(value):
    """Return ``(normalised_subscription, None)`` or ``(None, error_code)``."""
    if not isinstance(value, dict):
        return None, "invalid_subscription"
    endpoint = value.get("endpoint")
    keys = value.get("keys")
    if not isinstance(endpoint, str) or not endpoint or len(endpoint) > ENDPOINT_MAX or not isinstance(keys, dict):
        return None, "invalid_subscription"
    try:
        parts = urlsplit(endpoint)
        port = parts.port
    except ValueError:
        return None, "invalid_endpoint"
    if parts.scheme != "https" or parts.username or parts.password or port not in (None, 443):
        return None, "invalid_endpoint"
    if not _allowed_push_host(parts.hostname):
        return None, "unsupported_push_service"
    p256dh = _b64url_decode(keys.get("p256dh"))
    auth = _b64url_decode(keys.get("auth"))
    if not p256dh or len(p256dh) != 65 or p256dh[0] != 4 or not auth or len(auth) != 16:
        return None, "invalid_keys"
    return {"endpoint": endpoint, "keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]}}, None


def _clean_label(value):
    text = re.sub(r"[\x00-\x1f\x7f]", "", str(value or "")).strip()
    return truncate_text(text, LABEL_MAX) or "Phone"


def device_id(endpoint):
    return hashlib.sha256(str(endpoint).encode("utf-8")).hexdigest()[:16]


def _public_device(record):
    return {
        "id": device_id(record.get("endpoint", "")),
        "label": record.get("label") or "Phone",
        "created_at": record.get("created_at"),
        "last_success_at": record.get("last_success_at"),
    }


def list_devices():
    with _store_lock:
        return [_public_device(record) for record in _load_store()["subscriptions"]]


def device_count():
    with _store_lock:
        return len(_load_store()["subscriptions"])


def is_registered(endpoint):
    with _store_lock:
        return any(r.get("endpoint") == endpoint for r in _load_store()["subscriptions"])


def register_subscription(subscription, label="", replaces=""):
    """Add or refresh a phone. Returns ``(public_device, None)`` or ``(None, error_code)``."""
    normalised, error = validate_subscription(subscription)
    if error:
        return None, error
    with _store_lock:
        store = _load_store()
        subscriptions = store["subscriptions"]
        previous = next((r for r in subscriptions if r.get("endpoint") == normalised["endpoint"]), None)
        if previous is None and replaces:
            # pushsubscriptionchange: the browser rotated this phone's endpoint.
            previous = next((r for r in subscriptions if r.get("endpoint") == replaces), None)
        if previous is not None:
            subscriptions.remove(previous)
        record = {
            **normalised,
            "label": _clean_label(label) if label else (previous or {}).get("label") or "Phone",
            "created_at": (previous or {}).get("created_at") or _now_iso(),
            "last_success_at": (previous or {}).get("last_success_at"),
        }
        subscriptions.append(record)
        # Keep the newest registrations when an old phone was never removed.
        del subscriptions[:-MAX_SUBSCRIPTIONS]
        _save_store(store)
        return _public_device(record), None


def unregister_subscription(endpoint):
    with _store_lock:
        store = _load_store()
        kept = [r for r in store["subscriptions"] if r.get("endpoint") != endpoint]
        removed = len(kept) != len(store["subscriptions"])
        if removed:
            store["subscriptions"] = kept
            _save_store(store)
        return removed


def claim_notification(checkin_id):
    """Record ``checkin_id`` as notified; False if it already was (dedupe)."""
    checkin_id = str(checkin_id or "")
    if not checkin_id:
        return False
    with _store_lock:
        store = _load_store()
        if checkin_id in store["notified_checkin_ids"]:
            return False
        store["notified_checkin_ids"] = (store["notified_checkin_ids"] + [checkin_id])[-NOTIFIED_IDS_MAX:]
        _save_store(store)
        return True


# --------------------------------------------------------------------------
# Sending
# --------------------------------------------------------------------------
def default_sender():
    """Return ``send(subscription_info, data)`` backed by pywebpush and this server's VAPID key."""
    from py_vapid import Vapid
    from pywebpush import WebPushException, webpush

    keys = get_vapid_keys()
    vapid = Vapid.from_string(keys["private_key"])
    subject = keys.get("subject") or VAPID_SUBJECT

    def send(subscription_info, data):
        try:
            webpush(
                subscription_info=subscription_info,
                data=data,
                vapid_private_key=vapid,
                vapid_claims={"sub": subject},   # fresh dict: pywebpush adds aud/exp to it
                ttl=PUSH_TTL_SECONDS,
                timeout=PUSH_TIMEOUT_SECONDS,
                # High urgency lets Android deliver promptly while the phone dozes.
                headers={"Urgency": "high"},
            )
        except WebPushException as error:
            if error.status_code in (404, 410):
                raise SubscriptionGone(error.status_code) from error
            raise

    return send


def send_to_devices(payload, endpoints=None, sender=None):
    """Push ``payload`` to registered phones (all, or just ``endpoints``).

    Returns ``{"sent", "failed", "removed"}``. Phones the push service reports
    gone are removed; any other failure is logged and leaves the phone in place.
    """
    with _store_lock:
        targets = [
            {"endpoint": r["endpoint"], "keys": dict(r.get("keys") or {})}
            for r in _load_store()["subscriptions"]
            if endpoints is None or r.get("endpoint") in endpoints
        ]
    result = {"sent": 0, "failed": 0, "removed": 0}
    if not targets:
        return result
    send = sender or default_sender()
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    delivered, gone = [], []
    for target in targets:
        try:
            send(target, data)
            delivered.append(target["endpoint"])
        except SubscriptionGone:
            gone.append(target["endpoint"])
        except Exception as error:
            result["failed"] += 1
            _log(f"delivery to {device_id(target['endpoint'])} failed: {error}")
    if delivered or gone:
        with _store_lock:
            store = _load_store()
            stamp = _now_iso()
            store["subscriptions"] = [r for r in store["subscriptions"] if r.get("endpoint") not in gone]
            for record in store["subscriptions"]:
                if record.get("endpoint") in delivered:
                    record["last_success_at"] = stamp
            _save_store(store)
    result["sent"] = len(delivered)
    result["removed"] = len(gone)
    if gone:
        _log(f"removed {len(gone)} expired phone subscription(s)")
    return result


def build_checkin_payload(checkin_id, title, body, chat, project):
    """The push message the service worker turns into a notification."""
    return {
        "type": "random_checkin",
        "id": str(checkin_id),
        "title": truncate_text(title, TITLE_MAX) or "HWUI",
        "body": truncate_text(body, BODY_MAX),
        "chat": str(chat or ""),
        "project": str(project or ""),
    }


def send_test_notification(endpoints=None, sender=None):
    payload = {
        "type": "test",
        "id": f"test-{int(time.time() * 1000)}",
        "title": "HWUI",
        "body": "Phone notifications are working.",
        "chat": "",
        "project": "",
    }
    return send_to_devices(payload, endpoints=endpoints, sender=sender)


# --------------------------------------------------------------------------
# Check-in dispatch
# --------------------------------------------------------------------------
def is_new_saved_completion(config, chat_key, fire_id, success):
    """True only for the first successful /complete of the chat's pending check-in.

    Evaluate against the config *before* random_checkins_complete() applies the
    completion. Failures, retries after success, and stale fire IDs are False.
    """
    if not success or not fire_id:
        return False
    chats = ((config or {}).get("runtime") or {}).get("chats") or {}
    state = chats.get(str(chat_key)) or {}
    return state.get("pending_fire_id") == str(fire_id) and not state.get("pending_completed")


def _safe_chat_filename(filename):
    return (
        isinstance(filename, str)
        and bool(filename)
        and len(filename) <= 255
        and filename not in (".", "..")
        and os.path.basename(filename) == filename
        and "/" not in filename
        and "\\" not in filename
        and "\x00" not in filename
    )


def _default_parse_chat_file(filepath, filename):
    from chat_routes import _parse_chat_file
    return _parse_chat_file(filepath, filename, verbose=False)


def _default_chat_location():
    from chat_routes import get_active_project, get_chats_dir
    return get_chats_dir(), get_active_project() or ""


def resolve_saved_checkin(chats_dir, filename, checkin_id, parse_chat_file=None):
    """Title/body of the saved automatic_checkin message with ``checkin_id``, or None.

    Reads the chat file plus its verified identity sidecar, the same merge
    /chats/open uses. Read-only: a chat without a sidecar has no verifiable
    check-in label, so it is skipped rather than having one written.
    """
    if not checkin_id or not _safe_chat_filename(filename):
        return None
    chat_path = Path(chats_dir) / filename
    if not chat_path.is_file() or load_chat_metadata(chats_dir, filename) is None:
        return None
    messages = (parse_chat_file or _default_parse_chat_file)(str(chat_path), filename)
    merged, _meta, error = merge_verified_message_metadata(chats_dir, filename, messages)
    if error:
        _log(f"{filename}: saved chat could not be verified ({error})")
        return None
    for message in reversed(merged):
        if (
            message.get("role") == "assistant"
            and message.get("message_kind") == "automatic_checkin"
            and message.get("checkin_id") == checkin_id
        ):
            body = truncate_text(_message_text(message.get("content")), BODY_MAX)
            if not body:
                return None
            speaker = message.get("speaker") or filename.rsplit(".", 1)[0].split(" - ", 1)[0]
            return {"title": truncate_text(speaker, TITLE_MAX) or "HWUI", "body": body}
    return None


def dispatch_saved_checkin(chat_key, checkin_id, *, load_settings=None, chat_location=None,
                           parse_chat_file=None, sender=None):
    """Push one saved check-in to every registered phone. Returns a status word."""
    settings = (load_settings or user_config.load_random_checkins_config)()
    if not settings.get("mobile_notifications"):
        return "disabled"
    if not device_count():
        return "no_devices"
    chats_dir, project = (chat_location or _default_chat_location)()
    saved = resolve_saved_checkin(chats_dir, chat_key, checkin_id, parse_chat_file)
    if not saved:
        _log(f"check-in {checkin_id} not found in saved chat {chat_key!r}; nothing sent")
        return "not_found"
    if not claim_notification(checkin_id):
        return "duplicate"
    payload = build_checkin_payload(checkin_id, saved["title"], saved["body"], chat_key, project)
    result = send_to_devices(payload, sender=sender)
    return "sent" if result["sent"] else "failed"


def _dispatch_quietly(chat_key, checkin_id, kwargs):
    try:
        status = dispatch_saved_checkin(chat_key, checkin_id, **kwargs)
        if status not in ("disabled", "no_devices"):
            _log(f"check-in {checkin_id}: {status}")
    except Exception as error:
        _log(f"check-in {checkin_id} dispatch failed: {error}")


def dispatch_saved_checkin_async(chat_key, checkin_id, **kwargs):
    """Fire-and-forget dispatch on a daemon thread. Never raises."""
    try:
        thread = threading.Thread(
            target=_dispatch_quietly,
            args=(str(chat_key or ""), str(checkin_id or ""), kwargs),
            name="checkin-push",
            daemon=True,
        )
        thread.start()
        return thread
    except Exception as error:
        _log(f"could not start dispatch: {error}")
        return None

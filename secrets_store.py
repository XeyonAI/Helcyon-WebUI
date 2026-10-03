"""Local secrets store for credentials that must NOT live in settings.json.

Used for inference-provider API keys and the Hugging Face token. Values are kept
in ``secrets.local.json`` next to the application. That file is:
  * git-ignored (see .gitignore),
  * never listed in the backup/deploy scripts (they archive explicit file lists
    and ``backup_hwui_*.bat`` now fail the build if it appears in an archive),
  * never returned by any settings route - routes only report ``has_secret`` and
    a masked preview.

Existing cloud keys (openai_api_key / anthropic_api_key) are deliberately left in
settings.json for now.

The file is plain JSON, not encrypted: it keeps secrets out of exports, backups
and source control, which is the threat this solves. It is not a defence against
someone with read access to the install folder.
"""
import json
import os
import threading

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_LOCK = threading.RLock()


def secrets_path():
    # Env override lets tests point at a temp file without touching the real one.
    return os.environ.get("HWUI_SECRETS_FILE") or os.path.join(_BASE_DIR, "secrets.local.json")


def _load():
    try:
        with open(secrets_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        # A corrupt file must not be silently overwritten with a stripped dict.
        raise RuntimeError(f"secrets file unreadable: {exc}") from exc


def _save(data):
    path = secrets_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    try:
        os.chmod(tmp, 0o600)  # no-op on Windows; protective elsewhere
    except OSError:
        pass
    os.replace(tmp, path)


def get_secret(name, default=""):
    with _LOCK:
        try:
            value = _load().get(name, default)
        except RuntimeError:
            return default
    return value if isinstance(value, str) else default


def has_secret(name):
    return bool(get_secret(name))


def set_secret(name, value):
    """Store a secret; an empty value deletes it."""
    value = (value or "").strip()
    with _LOCK:
        data = _load()
        if value:
            data[name] = value
        else:
            data.pop(name, None)
        _save(data)


def delete_secret(name):
    set_secret(name, "")


def mask(value):
    """Preview safe to show in a UI: never more than the last 4 characters."""
    value = value or ""
    if not value:
        return ""
    if len(value) <= 8:
        return "•" * len(value)
    return "••••" + value[-4:]


def provider_key_name(provider_id):
    return f"provider.{provider_id}.api_key"


HF_TOKEN_NAME = "huggingface.token"

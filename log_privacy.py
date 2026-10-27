"""Conversation content stays out of HWUI's logs unless diagnostics are on.

Everything printed is mirrored into logs/hwui_full.log (app.py's stdout tee),
so a print of a user message, search query, document or prompt is a copy of
private text on disk. Log lines use ``redact(text)``, which gives only the size
by default and the (clipped) text when the user has opted in:

* settings.json ``"diag_verbose": true`` (the existing verbose-diagnostics
  switch, off by default), or
* the environment variable ``HWUI_LOG_CONTENT=1`` (development sessions).

Never pass API keys or tokens here; those are never logged at all.
"""
import json
import os

_SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")
_cache = {"key": None, "value": False}


def content_logging_enabled():
    if os.environ.get("HWUI_LOG_CONTENT") == "1":
        return True
    try:
        stat = os.stat(_SETTINGS_PATH)
        key = (stat.st_size, stat.st_mtime_ns)
    except OSError:
        return False
    if _cache["key"] != key:
        try:
            with open(_SETTINGS_PATH, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            _cache["value"] = bool(isinstance(data, dict) and data.get("diag_verbose") is True)
        except (OSError, ValueError):
            _cache["value"] = False
        _cache["key"] = key
    return _cache["value"]


def redact(value, limit=120):
    """``repr`` of the first ``limit`` characters when enabled, else ``<N chars>``."""
    text = value if isinstance(value, str) else ("" if value is None else str(value))
    if content_logging_enabled():
        return repr(text if limit is None else text[:limit])
    return f"<{len(text)} chars>"

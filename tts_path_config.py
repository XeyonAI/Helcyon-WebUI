"""Load per-build TTS path overrides without changing public defaults."""

from __future__ import annotations

import json
import os
from pathlib import Path


LOCAL_CONFIG_NAME = "tts_paths.local.json"
ALLOWED_KEYS = {
    "HWUI_TTS_ROOT",
    "HWUI_HF_CACHE",
    "HWUI_F5_VENV",
    "HWUI_F5_VOICES_DIR",
    "HWUI_F5_CHECKPOINT",
    "HWUI_CHATTERBOX_VOICES_DIR",
    "HWUI_QWEN_MODEL_PATH",
    "HWUI_QWEN_VOICES_DIR",
    "HWUI_XTTS_VOICES_DIR",
}


def apply_tts_path_overrides(build_dir: str | os.PathLike[str] | None = None) -> dict[str, str]:
    """Apply the ignored local sidecar without overriding real environment variables."""
    root = Path(build_dir).resolve() if build_dir else Path(__file__).resolve().parent
    config_path = root / LOCAL_CONFIG_NAME
    if not config_path.is_file():
        return {}

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("root value must be an object")
    except Exception as exc:
        print(f"⚠️ Ignoring invalid {LOCAL_CONFIG_NAME}: {exc}")
        return {}

    applied: dict[str, str] = {}
    for key, value in raw.items():
        if key not in ALLOWED_KEYS or not isinstance(value, str) or not value.strip():
            continue
        resolved = value.replace("{build_dir}", str(root))
        resolved = os.path.expandvars(resolved)
        os.environ.setdefault(key, resolved)
        applied[key] = os.environ[key]
    return applied

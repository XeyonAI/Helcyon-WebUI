"""Load per-build TTS path overrides without changing public defaults."""

from __future__ import annotations

import os
from pathlib import Path
from user_config import load_user_config_section


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
    "HWUI_QWENTTS_SERVER_EXE",
    "HWUI_QWENTTS_MODELS_DIR",
    "HWUI_QWENTTS_VOICES_DIR",
    "HWUI_XTTS_VOICES_DIR",
}


def apply_tts_path_overrides(build_dir: str | os.PathLike[str] | None = None) -> dict[str, str]:
    """Apply the ignored local sidecar without overriding real environment variables."""
    root = Path(build_dir).resolve() if build_dir else Path(__file__).resolve().parent
    try:
        raw = load_user_config_section("tts_paths")
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

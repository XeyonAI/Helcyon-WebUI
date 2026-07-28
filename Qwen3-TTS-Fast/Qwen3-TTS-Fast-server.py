from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn


ROOT = Path(__file__).resolve().parent
BUILD_ROOT = ROOT.parent
if str(BUILD_ROOT) not in sys.path:
    sys.path.append(str(BUILD_ROOT))

from tts_path_config import apply_tts_path_overrides

apply_tts_path_overrides(BUILD_ROOT)

TTS_ROOT = Path(os.getenv("HWUI_TTS_ROOT", r"C:\HWUI-TTS"))
MODEL_PATH = Path(os.getenv(
    "HWUI_QWEN_MODEL_PATH",
    str(TTS_ROOT / "Qwen3-TTS" / "models" / "Qwen" / "Qwen3-TTS-12Hz-0.6B-Base"),
))
VOICE_PATH = Path(os.getenv("HWUI_QWEN_VOICES_DIR", str(TTS_ROOT / "F5" / "voices")))

# This entry point owns the backend configuration. HWUI supplies only this file.
os.environ["QWEN_FAST_MODEL_PATH"] = str(MODEL_PATH)
os.environ["QWEN_FAST_VOICES_DIR"] = str(VOICE_PATH)
os.environ["QWEN_FAST_PRELOAD"] = "1"


if __name__ == "__main__":
    uvicorn.run("app.service:app", host="127.0.0.1", port=8767)

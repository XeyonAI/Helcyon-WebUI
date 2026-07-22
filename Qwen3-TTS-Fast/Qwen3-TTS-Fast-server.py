from __future__ import annotations

import os
from pathlib import Path

import uvicorn


ROOT = Path(__file__).resolve().parent
MODEL_PATH = ROOT.parent / "Qwen3-TTS" / "models" / "Qwen" / "Qwen3-TTS-12Hz-0.6B-Base"
VOICE_PATH = Path(r"I:\F5-TTS\F5-TTS")

# This entry point owns the backend configuration. HWUI supplies only this file.
os.environ["QWEN_FAST_MODEL_PATH"] = str(MODEL_PATH)
os.environ["QWEN_FAST_VOICES_DIR"] = str(VOICE_PATH)
os.environ["QWEN_FAST_PRELOAD"] = "1"


if __name__ == "__main__":
    uvicorn.run("app.service:app", host="127.0.0.1", port=8767)

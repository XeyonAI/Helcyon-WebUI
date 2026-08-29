"""Launch the isolated qwentts.cpp OpenAI-compatible server for HWUI."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

from tts_path_config import apply_tts_path_overrides


BUILD_ROOT = Path(__file__).resolve().parent
SETTINGS_PATH = BUILD_ROOT / "settings.json"
QWENTTS_ROOT = BUILD_ROOT / "qwentts.cpp"
ALLOWED_QUANTIZATIONS = {"Q8_0", "Q4_K_M", "Q3_K_M"}
QWEN_FAST_TALKER_QUANTIZATIONS = {"Q8_0", "Q4_K_M"}


def _load_settings() -> dict:
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as exc:
        raise RuntimeError(f"Could not read {SETTINGS_PATH}: {exc}") from exc


def _load_quantization() -> str:
    settings = _load_settings()
    quantization = str(settings.get("qwentts_cpp_quantization", "Q8_0")).upper()
    if quantization not in ALLOWED_QUANTIZATIONS:
        raise RuntimeError(
            f"Unsupported qwentts.cpp quantization {quantization!r}; "
            f"choose one of {sorted(ALLOWED_QUANTIZATIONS)}"
        )
    return quantization


def _first_file(candidates: list[Path], label: str) -> Path:
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    checked = "\n  ".join(str(path) for path in candidates)
    raise RuntimeError(f"Could not find {label}. Checked:\n  {checked}")


def build_command() -> list[str]:
    apply_tts_path_overrides(BUILD_ROOT)
    settings = _load_settings()
    native_fast = settings.get("tts_engine") == "qwen-fast"
    if native_fast:
        quantization = str(settings.get("qwen_fast_talker_quantization", "Q8_0")).upper()
        if quantization not in QWEN_FAST_TALKER_QUANTIZATIONS:
            raise RuntimeError(
                f"Unsupported qwen-fast talker quantization {quantization!r}; "
                f"choose one of {sorted(QWEN_FAST_TALKER_QUANTIZATIONS)}"
            )
    else:
        quantization = _load_quantization()
    model_size = "0.6b" if native_fast else "1.7b"
    port = 8767 if native_fast else 8768

    configured_exe = os.getenv("HWUI_QWENTTS_SERVER_EXE", "").strip()
    if configured_exe:
        server_exe = _first_file([Path(configured_exe)], "qwentts.cpp tts-server executable")
    else:
        server_exe = _first_file(
            [
                QWENTTS_ROOT / "build" / "Release" / "tts-server.exe",
                QWENTTS_ROOT / "build" / "tts-server.exe",
            ],
            "qwentts.cpp tts-server executable",
        )

    models_dir = Path(os.getenv("HWUI_QWENTTS_MODELS_DIR", str(QWENTTS_ROOT / "models")))
    # Keep the proven Q8 tokenizer/codec for the temporary Q4 talker A/B.
    tokenizer_quantization = "Q8_0" if native_fast else ("Q4_K_M" if quantization == "Q3_K_M" else quantization)
    talker = _first_file(
        [models_dir / f"qwen-talker-{model_size}-base-{quantization}.gguf"],
        f"{model_size.upper()} Base {quantization} talker",
    )
    codec = _first_file(
        [models_dir / f"qwen-tokenizer-12hz-{tokenizer_quantization}.gguf"],
        f"12 Hz {tokenizer_quantization} tokenizer",
    )

    return [
        str(server_exe),
        "--model", str(talker),
        "--codec", str(codec),
        "--alias", f"qwen3-tts-{model_size}-base-{quantization.lower()}",
        "--host", "127.0.0.1",
        "--port", str(port),
        "--lang", "English",
        "--talker-max-seq", "768" if native_fast else "4096",
    ]


def main() -> int:
    try:
        command = build_command()
    except Exception as exc:
        print(f"[qwentts.cpp] startup configuration error: {exc}", file=sys.stderr, flush=True)
        return 1

    port = command[command.index("--port") + 1]
    print(f"[qwentts.cpp] starting persistent server on 127.0.0.1:{port}", flush=True)
    print(f"[qwentts.cpp] executable: {command[0]}", flush=True)
    print(f"[qwentts.cpp] talker: {command[2]}", flush=True)
    print(f"[qwentts.cpp] codec: {command[4]}", flush=True)

    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen(
        command,
        cwd=str(Path(command[0]).parent),
        creationflags=creationflags,
    )

    def _stop(_signum, _frame):
        if process.poll() is None:
            process.terminate()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    return process.wait()


if __name__ == "__main__":
    raise SystemExit(main())

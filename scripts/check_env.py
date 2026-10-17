"""Read-only environment checks used by setup.bat, setup-dev.bat and setup-voice.bat.

    python scripts\\check_env.py torch      PyTorch present and usable on this GPU?
    python scripts\\check_env.py core       every import for the packages listed in requirements.txt
    python scripts\\check_env.py voice-fast every import the Qwen3-TTS-Fast service needs

Nothing here installs, upgrades or modifies packages.

`torch` exit codes: 0 = usable CUDA build, 2 = torch cannot be imported,
3 = torch imports but has no usable CUDA support for the installed GPU
(CPU-only build, or a build that lacks kernels for this GPU's architecture).
`core` / `voice-fast` exit 0 when everything imports, otherwise 1.
"""

import importlib
import os
import re
import sys

# import name -> pip package, for the message shown when it is missing
CORE = {
    "flask": "flask",
    "flask_cors": "flask-cors",
    "requests": "requests",
    "psutil": "psutil",
    "PIL": "Pillow",
    "numpy": "numpy",
    "scipy": "scipy",
    "soundfile": "soundfile",
    "librosa": "librosa",
    "docx": "python-docx",
    "odf": "odfpy",
    "PyPDF2": "PyPDF2",
    "yaml": "PyYAML",
    "pywebpush": "pywebpush",
    "cryptography": "cryptography",
    "faster_whisper": "faster-whisper",
    "whisper": "openai-whisper",
    "gradio_client": "gradio_client",
    "pynvml": "nvidia-ml-py",
    "torch": "torch (CUDA build from pytorch.org)",
    "torchaudio": "torchaudio (CUDA build from pytorch.org)",
}

# pynvml only feeds the VRAM read-out and is wrapped in try/except by the app
OPTIONAL = {"pynvml"}

VOICE_FAST = {
    "torch": "torch",
    "torchaudio": "torchaudio",
    "numpy": "numpy",
    "soundfile": "soundfile",
    "librosa": "librosa",
    "psutil": "psutil",
    "transformers": "transformers",
    "qwen_tts": "qwen-tts-hf",
    "faster_qwen3_tts": "faster-qwen3-tts (editable install of source_v0.5.3)",
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "multipart": "python-multipart",
    "pynvml": "nvidia-ml-py",
}


def check_torch():
    try:
        import torch
    except Exception as exc:
        print(f"PyTorch not importable: {exc}")
        return 2
    cuda_tag = torch.version.cuda
    if not torch.cuda.is_available():
        print(f"PyTorch {torch.__version__} has no usable CUDA device (CUDA build: {cuda_tag}).")
        return 3
    major, minor = torch.cuda.get_device_capability(0)
    arches = torch.cuda.get_arch_list()
    exact = f"sm_{major}{minor}"
    if exact not in arches and not any(a.startswith(f"sm_{major}") for a in arches):
        print(f"PyTorch {torch.__version__} (CUDA {cuda_tag}) lacks kernels for this GPU "
              f"({exact}); built for: {' '.join(arches)}")
        return 3
    print(f"PyTorch {torch.__version__} (CUDA {cuda_tag}) OK on {torch.cuda.get_device_name(0)} ({exact}).")
    return 0


def _key(name):
    return re.split(r"[ <>=!~\[;(]", name.strip(), maxsplit=1)[0].lower().replace("_", "-")


def declared_packages():
    """Package names listed in the requirements.txt next to scripts/ (None if unreadable)."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "requirements.txt")
    try:
        with open(path, encoding="utf-8-sig") as handle:
            return {_key(line) for line in handle if line.strip() and not line.lstrip().startswith("#")}
    except OSError:
        return None


def check_imports(table, only_declared=False):
    """Import every module in `table`.  With only_declared, skip packages that this
    edition's requirements.txt does not list (Free ships a smaller list than Dev/Pro);
    PyTorch is never in requirements.txt and is always required."""
    declared = declared_packages() if only_declared else None
    missing, optional_missing = [], []
    for module, package in table.items():
        if declared is not None and not _key(package).startswith("torch") and _key(package) not in declared:
            continue
        try:
            importlib.import_module(module)
        except Exception as exc:  # ImportError, or a broken binary wheel
            (optional_missing if module in OPTIONAL else missing).append((module, package, exc))
    for module, package, exc in optional_missing:
        print(f"  optional: {module} ({package}) not available: {exc}")
    for module, package, exc in missing:
        print(f"  MISSING: {module} <- pip install {package}  [{type(exc).__name__}: {exc}]")
    if not missing:
        print("All required modules import correctly.")
    return 1 if missing else 0


def main(argv):
    mode = argv[1] if len(argv) > 1 else ""
    if mode == "torch":
        return check_torch()
    if mode == "core":
        return check_imports(CORE, only_declared=True)
    if mode == "voice-fast":
        return check_imports(VOICE_FAST)
    print(__doc__)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv))

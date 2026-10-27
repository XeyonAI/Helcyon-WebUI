from flask import Blueprint, request, jsonify
import tempfile
import os
import logging
import time

# Private CUDA JIT cache for this (in-process) Whisper model. The shared driver
# cache is capped at 256 MiB and used by every CUDA process (llama, TTS, ...);
# once full it evicts the PTX-JIT'd Blackwell kernels, so the first transcription
# of every launch recompiled them (~13 s, measured; ~0.3 s with a warm cache).
# Must be set before torch initialises CUDA, i.e. before whisper loads the model.
# Separate from logs/llama_cuda_cache so the two never evict each other.
_WHISPER_CUDA_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "logs", "whisper_cuda_cache")
try:
    os.makedirs(_WHISPER_CUDA_CACHE_DIR, exist_ok=True)
    if "CUDA_CACHE_PATH" not in os.environ:
        os.environ["CUDA_CACHE_PATH"] = _WHISPER_CUDA_CACHE_DIR
    try:
        _cache_max = int(os.environ.get("CUDA_CACHE_MAXSIZE", "268435456"))
    except (TypeError, ValueError):
        _cache_max = 0
    if 0 <= _cache_max < 1073741824:
        os.environ["CUDA_CACHE_MAXSIZE"] = "1073741824"
except OSError as _e:
    logging.warning(f"⚠️ Could not prepare Whisper CUDA cache dir: {_e}")

import json as _json
import threading

whisper_bp = Blueprint('whisper', __name__)

# ----------------------------------------------------------------
# WHISPER MODEL — imported and loaded on first use
# ----------------------------------------------------------------
# openai-whisper pulls in torch, so importing it at module load made the whole
# app (and every test that imports app) depend on the voice-input stack, and
# loaded the model on every start even when voice input was never used. The
# model now loads on the first transcription, or in the background when the
# server starts (prewarm(), called from app.py's __main__), so the first
# transcription still avoids the load cost.
_MODEL_NAME = os.environ.get("HWUI_WHISPER_MODEL", "base")
_model = None
_model_error = None
_model_lock = threading.Lock()


def get_model():
    """The loaded Whisper model; raises RuntimeError when STT is unavailable."""
    global _model, _model_error
    if _model is not None:
        return _model
    with _model_lock:
        if _model is None:
            try:
                import whisper  # deferred: heavy, optional voice-input dependency
                t_load = time.perf_counter()
                _model = whisper.load_model(_MODEL_NAME)
                _model_error = None
                logging.info(f"✅ Whisper model loaded on {next(_model.parameters()).device} "
                             f"in {time.perf_counter() - t_load:.2f}s")
            except Exception as exc:  # ImportError, CUDA/torch failures, missing weights
                _model_error = f"{type(exc).__name__}: {exc}"
                raise RuntimeError(f"Voice input is unavailable ({_model_error})") from exc
    return _model


def prewarm():
    """Load the model in a background thread so the first transcription is fast."""
    def _load():
        try:
            get_model()
        except RuntimeError as exc:
            logging.warning(f"⚠️ {exc}")
    threading.Thread(target=_load, name="whisper-prewarm", daemon=True).start()


_first_transcribe_done = False

# ----------------------------------------------------------------
# TRANSCRIPT CORRECTIONS — fix known Whisper mishearings
# ----------------------------------------------------------------
import re as _re

# Built in: the product's own names only. Everything personal (names, places,
# medicines, quotes) belongs in the installation's own transcript_fixes.json
# (see _load_user_fixes), which is user-owned and never shipped.
TRANSCRIPT_FIXES = [
    # Helcyon — fuzzy phonetic catch-all (covers the vast majority of Whisper variants)
    # Matches hel/hil/heel/hul + any middle consonants + sibilant/c/th + ion/ian/in/on/an endings
    # Fuzzy catch-all — also handles possessive (Helcyon's, helcion's etc.)
    (r'\bh(?:el|il|eel|ul)[a-z]*?(?:sh?|c|th?)[iy]?(?:on|an|en|in|ion|yan)(?:\'s)?\b',
     lambda m: "Helcyon's" if m.group(0).endswith("'s") or m.group(0).endswith("'S") else 'Helcyon'),
    # Outliers too phonetically distant for the fuzzy pattern
    (r'\bhouse\s*shun\b', 'Helcyon'),
    (r'\bhoseon\b', 'Helcyon'),
    (r'\bheathsin\b', 'Helcyon'),
    (r"\bHelsing's\b", "Helcyon's"),  # Whisper hears Helcyon's as Helsing's
    (r'\bhelsy\s*(?:and|on)\b', 'Helcyon'),
    (r'\b(?:hellsy|helsea)\s*(?:and|on)\b', 'Helcyon'),
    (r'\bhealthy\s*and\b', 'Helcyon'),
    (r'\bhealthy\s*on\b', 'Helcyon'),
    # Helcyon WebUI — combined pattern must come BEFORE the standalone Helcyon pattern
    # so "helcion web you eye" resolves as one unit rather than "Helcyon web you eye"
    (r'\bh(?:el|il|eel|ul)[a-z]*?(?:sh?|c|th?)[iy]?(?:on|an|en|in|ion|yan)\s+web[\s\-]*(?:you[\s\-]*(?:eye|[iI])|ewey|ooey|yui|U\.?I\.?|[uU][iI])\b', 'Helcyon WebUI'),
    # WebUI alone — catches "web UI", "web you eye", "web ewey", "webui" etc.
    (r'\bweb[\s\-]*(?:you[\s\-]*(?:eye|[iI])|ewey|ooey|yui|U\.?I\.?|[uU][iI])\b', 'WebUI'),
    # GPT-4o — Whisper reads the 'o' as zero
    (r'\bGPT-40\b', 'GPT-4o'),
    (r'\bGPT 40\b', 'GPT-4o'),
]

# Per-installation corrections: transcript_fixes.json next to app.py, e.g.
#   [{"heard": "lama see pee pee", "write": "llama.cpp"},
#    {"pattern": "\\bmy stral\\b", "write": "Ministral"}]
# "heard" is a literal phrase matched as whole words; "pattern" is a regular
# expression. Both are case-insensitive. Re-read whenever the file changes.
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_USER_FIXES_CACHE = {"key": None, "fixes": []}


def user_fixes_path():
    return os.environ.get("HWUI_TRANSCRIPT_FIXES_FILE") or os.path.join(_BASE_DIR, "transcript_fixes.json")


def _load_user_fixes():
    path = user_fixes_path()
    try:
        stat = os.stat(path)
    except OSError:
        _USER_FIXES_CACHE.update(key=None, fixes=[])
        return []
    key = (path, stat.st_size, stat.st_mtime_ns)
    if _USER_FIXES_CACHE["key"] == key:
        return _USER_FIXES_CACHE["fixes"]
    fixes = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            entries = _json.load(handle)
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict) or not isinstance(entry.get("write"), str):
                continue
            if isinstance(entry.get("heard"), str) and entry["heard"].strip():
                words = r"\s+".join(_re.escape(w) for w in entry["heard"].split())
                fixes.append((_re.compile(r"\b" + words + r"\b", _re.IGNORECASE), entry["write"]))
            elif isinstance(entry.get("pattern"), str) and entry["pattern"]:
                fixes.append((_re.compile(entry["pattern"], _re.IGNORECASE), entry["write"]))
    except (OSError, ValueError, _re.error) as exc:
        logging.warning(f"⚠️ transcript_fixes.json ignored: {exc}")
        fixes = []
    _USER_FIXES_CACHE.update(key=key, fixes=fixes)
    return fixes


def correct_transcript(text):
    for pattern, replacement in TRANSCRIPT_FIXES:
        text = _re.sub(pattern, replacement, text, flags=_re.IGNORECASE)
    for pattern, replacement in _load_user_fixes():
        text = pattern.sub(lambda _m, r=replacement: r, text)
    return text

# Allow only alphanumeric chars in the extension we derive from upload
# filenames — guards against path-separator injection (e.g. a filename like
# `evil.../passwd` would otherwise put `/passwd` into the tempfile suffix).
_SAFE_EXT_RE = _re.compile(r'[^a-zA-Z0-9]')


def _safe_ext(orig_name):
    if '.' not in orig_name:
        return '.webm'
    raw = orig_name.rsplit('.', 1)[-1]
    cleaned = _SAFE_EXT_RE.sub('', raw)[:10]
    return f'.{cleaned}' if cleaned else '.webm'


@whisper_bp.route('/api/whisper/transcribe', methods=['POST'])
def transcribe():
    global _first_transcribe_done
    tmp_path = None
    t_req = time.perf_counter()
    try:
        if 'audio' not in request.files:
            return jsonify({'error': 'No audio file provided'}), 400

        audio_file = request.files['audio']

        # Save to temp file. Extension is sanitised to alphanumeric only so a
        # malicious filename can't smuggle path separators into the suffix.
        ext = _safe_ext(audio_file.filename or 'recording.webm')
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            audio_file.save(tmp.name)
            tmp_path = tmp.name

        logging.info("🎤 Transcribing audio")
        t_saved = time.perf_counter()

        # Transcribe with Whisper
        try:
            model = get_model()
        except RuntimeError as exc:
            return jsonify({'error': str(exc), 'code': 'stt_unavailable'}), 503
        result = model.transcribe(tmp_path, language='en')
        transcript = result['text'].strip()
        t_infer = time.perf_counter()

        # Post-process: correct known misheard words
        transcript = correct_transcript(transcript)

        # One line per request so a slow transcription shows which stage it was.
        logging.info(
            f"⏱️ STT timing: save={t_saved - t_req:.2f}s "
            f"transcribe={t_infer - t_saved:.2f}s "
            f"post={time.perf_counter() - t_infer:.2f}s "
            f"total={time.perf_counter() - t_req:.2f}s "
            f"device={next(model.parameters()).device} "
            f"first_in_process={not _first_transcribe_done}")
        _first_transcribe_done = True
        # Length only: the transcript is the user's own words and must not land in logs.
        logging.info(f"✅ Transcript ready ({len(transcript)} chars)")
        return jsonify({'transcript': transcript})

    except Exception as e:
        logging.error(f"❌ Whisper error: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        # Cleanup runs even if transcription raised — was leaking otherwise.
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError as _ce:
                logging.warning(f"⚠️ Could not delete temp file {tmp_path}: {_ce}")

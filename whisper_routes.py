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

import whisper

whisper_bp = Blueprint('whisper', __name__)

# ----------------------------------------------------------------
# TRANSCRIPT CORRECTIONS — fix known Whisper mishearings
# ----------------------------------------------------------------
import re as _re

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
        # Grok — Whisper mishears as similar-sounding words
    (r'\bglock\b', 'Grok'),
    (r'\bgrock\b', 'Grok'),
    (r'\bgrook\b', 'Grok'),
    (r'\bgroc\b', 'Grok'),
    # Nebula
    (r'\bnibbula\b', 'Nebula'),
    # Stanmer Park — Whisper hears as 'stamina park'
    (r'\bstamina\s*park\b', 'Stanmer Park'),
    # "Deny, choose, be" — Whisper hears trailing "be" as the letter B
    (r'\b(deny[,.]?\s+choose[,.]?\s+)B\b', r'\1be'),
    # GPT-4o — Whisper reads the 'o' as zero
    (r'\bGPT-40\b', 'GPT-4o'),
    (r'\bGPT 40\b', 'GPT-4o'),
    # Mounjaro — Whisper hears it as two words
    (r'\bmount\s*jaro\b', 'Mounjaro'),
    # Claire — Whisper almost always hears as "clear" (or clair/clere/klare)
    # Can't blindly replace all "clear" (real word), so use three targeted patterns:
    #   1. After verbs/prepositions that take a person object (to, with, saw, told, miss, etc.)
    #   2. Sentence-start capital Clear + female-context verb following (said, is, was, told, etc.)
    #   3. Rare non-word variants (clair, clere, klare, klair) that are never real English words
    # Note: these are applied in correct_transcript() via re.sub with IGNORECASE
    (r'(?:(?:with|to|saw|miss|told|asked|about|of|texted|called|met|love|loved|knew|know|see|meeting|seeing|thinking\s+about)\s+)(clear|clair|clere|klare|klair)\b', lambda m: m.group(0).replace(m.group(1), 'Claire')),
    (r'(?:^|(?<=[.!?]\s))(Clear|Clair|Clere|Klare|Klair)\b(?=\s+(?:said|told|asked|is|was|has|had|called|texted|came|went|looks|seems|she|her))', 'Claire'),
    (r'\b(Clair|Clere|Klare|Klair)\b', 'Claire'),
]

def correct_transcript(text):
    for pattern, replacement in TRANSCRIPT_FIXES:
        text = _re.sub(pattern, replacement, text, flags=_re.IGNORECASE)
    return text

# Load model once at startup - 'base' is fast and accurate enough
# Change to 'small' or 'medium' for better accuracy at cost of speed
_t_load = time.perf_counter()
model = whisper.load_model("base")
logging.info(f"✅ Whisper model loaded on {next(model.parameters()).device} "
             f"in {time.perf_counter() - _t_load:.2f}s")
_first_transcribe_done = False

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

        logging.info(f"🎤 Transcribing audio: {tmp_path}")
        t_saved = time.perf_counter()

        # Transcribe with Whisper
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
        logging.info(f"✅ Transcript: {transcript}")
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

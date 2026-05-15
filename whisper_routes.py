from flask import Blueprint, request, jsonify
import whisper
import tempfile
import os
import logging

whisper_bp = Blueprint('whisper', __name__)

# ----------------------------------------------------------------
# TRANSCRIPT CORRECTIONS — fix known Whisper mishearings
# ----------------------------------------------------------------
import re as _re

TRANSCRIPT_FIXES = [
    # Helcyon — fuzzy phonetic catch-all (covers the vast majority of Whisper variants)
    # Matches hel/hil/heel/hul + any middle consonants + sibilant/c/th + ion/ian/in/on/an endings
    (r'\bh(?:el|il|eel|ul)[a-z]*?(?:sh?|c|th?)[iy]?(?:on|an|en|in|ion|yan)\b', 'Helcyon'),
    # Outliers too phonetically distant for the fuzzy pattern
    (r'\bhouse\s*shun\b', 'Helcyon'),
    (r'\bhoseon\b', 'Helcyon'),
    (r'\bheathsin\b', 'Helcyon'),
    (r'\bhelsy\s*and\b', 'Helcyon'),
    (r'\bhealthy\s*and\b', 'Helcyon'),
    (r'\bhealthy\s*on\b', 'Helcyon'),
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
model = whisper.load_model("base")
logging.info("✅ Whisper model loaded")

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
    tmp_path = None
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

        # Transcribe with Whisper
        result = model.transcribe(tmp_path, language='en')
        transcript = result['text'].strip()

        # Post-process: correct known misheard words
        transcript = correct_transcript(transcript)

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
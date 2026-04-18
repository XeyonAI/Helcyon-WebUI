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
    # Helcyon — Whisper never gets this right
    (r'\bhellsion\b', 'Helcyon'),
    (r'\bhelshin\b', 'Helcyon'),
    (r'\bHilsion\b', 'Helcyon'),
    (r'\bhouse\s*shun\b', 'Helcyon'),
    (r'\bhoseon\b', 'Helcyon'),
    (r'\bhelcion\b', 'Helcyon'),
    (r'\bhelshion\b', 'Helcyon'),
    (r'\bheelshian\b', 'Helcyon'),
    (r'\bheathsin\b', 'Helcyon'),
    (r'\bhelsion\b', 'Helcyon'),
    (r'\bhelsy\s*and\b', 'Helcyon'),
    (r'\bhealthy\s*and\b', 'Helcyon'),
    (r'\bhealthy\s*on\b', 'Helcyon'),
    (r'\bhulsion\b', 'Helcyon'),
    # Grok — Whisper mishears as similar-sounding words
    (r'\bglock\b', 'Grok'),
    (r'\bgrock\b', 'Grok'),
    (r'\bgrook\b', 'Grok'),
    (r'\bgroc\b', 'Grok'),
    # Nebula
    (r'\bnibbula\b', 'Nebula'),
    # "Deny, choose, be" — Whisper hears trailing "be" as the letter B
    (r'\b(deny[,.]?\s+choose[,.]?\s+)B\b', r'\1be'),
    # GPT-4o — Whisper reads the 'o' as zero
    (r'\bGPT-40\b', 'GPT-4o'),
    (r'\bGPT 40\b', 'GPT-4o'),
    # Mounjaro — Whisper hears it as two words
    (r'\bmount\s*jaro\b', 'Mounjaro'),
]

def correct_transcript(text):
    for pattern, replacement in TRANSCRIPT_FIXES:
        text = _re.sub(pattern, replacement, text, flags=_re.IGNORECASE)
    return text

# Load model once at startup - 'base' is fast and accurate enough
# Change to 'small' or 'medium' for better accuracy at cost of speed
model = whisper.load_model("base")
logging.info("âœ… Whisper model loaded")

@whisper_bp.route('/api/whisper/transcribe', methods=['POST'])
def transcribe():
    try:
        if 'audio' not in request.files:
            return jsonify({'error': 'No audio file provided'}), 400

        audio_file = request.files['audio']

        # Save to temp file
        # Detect file extension from uploaded filename so ffmpeg decodes correctly
        orig_name = audio_file.filename or 'recording.webm'
        ext = '.' + orig_name.rsplit('.', 1)[-1] if '.' in orig_name else '.webm'
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            audio_file.save(tmp.name)
            tmp_path = tmp.name

        logging.info(f"ðŸŽ¤ Transcribing audio: {tmp_path}")

        # Transcribe with Whisper
        result = model.transcribe(tmp_path, language='en')
        transcript = result['text'].strip()

        # Post-process: correct known misheard words
        transcript = correct_transcript(transcript)

        # Clean up temp file
        os.unlink(tmp_path)

        logging.info(f"âœ… Transcript: {transcript}")
        return jsonify({'transcript': transcript})

    except Exception as e:
        logging.error(f"âŒ Whisper error: {e}")
        return jsonify({'error': str(e)}), 500
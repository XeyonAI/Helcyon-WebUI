from flask import Blueprint, request, jsonify
import whisper
import tempfile
import os
import logging

whisper_bp = Blueprint('whisper', __name__)

# Load model once at startup - 'base' is fast and accurate enough
# Change to 'small' or 'medium' for better accuracy at cost of speed
model = whisper.load_model("base")
logging.info("✅ Whisper model loaded")

@whisper_bp.route('/api/whisper/transcribe', methods=['POST'])
def transcribe():
    try:
        if 'audio' not in request.files:
            return jsonify({'error': 'No audio file provided'}), 400

        audio_file = request.files['audio']

        # Save to temp file
        with tempfile.NamedTemporaryFile(suffix='.webm', delete=False) as tmp:
            audio_file.save(tmp.name)
            tmp_path = tmp.name

        logging.info(f"🎤 Transcribing audio: {tmp_path}")

        # Transcribe with Whisper
        result = model.transcribe(tmp_path, language='en')
        transcript = result['text'].strip()

        # Clean up temp file
        os.unlink(tmp_path)

        logging.info(f"✅ Transcript: {transcript}")
        return jsonify({'transcript': transcript})

    except Exception as e:
        logging.error(f"❌ Whisper error: {e}")
        return jsonify({'error': str(e)}), 500
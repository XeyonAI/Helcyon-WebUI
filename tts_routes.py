"""
TTS Routes - Text-to-Speech functionality supporting F5-TTS, XTTS, and Chatterbox engines
"""

from flask import Blueprint, request, jsonify, send_file
import requests
from io import BytesIO
import logging
import json
import os

# Create blueprint
tts_bp = Blueprint('tts', __name__)

# Server URLs
F5_SERVER_URL          = 'http://localhost:8003'
XTTS_SERVER_URL        = 'http://localhost:8002'
CHATTERBOX_SERVER_URL  = 'http://localhost:8004'
DEFAULT_VOICE = 'Sol'
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'settings.json')


def get_settings():
    """Read settings.json. Returns {} on any read error — callers that
    can't distinguish empty-config from read-failure should use this.
    `save_settings` does its own read with explicit failure detection."""
    try:
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"⚠️ get_settings read failed: {e}")
        return {}


def save_settings(data):
    """Merge `data` into settings.json and write atomically.

    ⚠️ load-bearing: refuses to write if the read failed transiently and
    settings.json exists on disk. The previous version silently overwrote
    the entire file with just `data` whenever the read errored, which
    would wipe every other key (cache_type, ignore_eos, llama_args, API
    keys, etc.) on a transient I/O blip. Also writes atomically via
    tempfile + os.replace so a crash mid-write can't corrupt the file.
    """
    try:
        # Explicit re-read with failure detection. We can't reuse get_settings()
        # here because it can't distinguish "file empty" from "read failed".
        settings = {}
        read_ok = True
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                    settings = json.load(f)
            except Exception as re:
                logging.error(f"⚠️ save_settings pre-read failed: {re}")
                read_ok = False

        if not read_ok:
            logging.error(
                "⚠️ save_settings ABORTED — read of existing settings.json "
                "failed. Refusing to overwrite to avoid wiping config. "
                f"Wanted to set: {list(data.keys())}"
            )
            return False

        settings.update(data)

        import tempfile
        d = os.path.dirname(SETTINGS_FILE) or '.'
        fd, tmp_path = tempfile.mkstemp(suffix='.tmp', prefix='.settings_', dir=d, text=True)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as tf:
                json.dump(settings, tf, indent=2)
            os.replace(tmp_path, SETTINGS_FILE)
        except Exception:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            raise
        return True
    except Exception as e:
        logging.error(f"❌ Failed to save settings: {e}")
        return False

def get_engine():
    return get_settings().get('tts_engine', 'f5')

def get_server_url():
    engine = get_engine()
    if engine == 'xtts':
        return XTTS_SERVER_URL
    elif engine == 'chatterbox':
        return CHATTERBOX_SERVER_URL
    else:
        return F5_SERVER_URL


# --------------------------------------------------
# GET / SET TTS ENGINE
# --------------------------------------------------
@tts_bp.route('/engine', methods=['GET'])
def get_tts_engine():
    return jsonify({'engine': get_engine()})

@tts_bp.route('/engine', methods=['POST'])
def set_tts_engine():
    data = request.json
    engine = data.get('engine', 'f5')
    if engine not in ('f5', 'xtts', 'chatterbox', 'none'):
        return jsonify({'error': 'Invalid engine'}), 400
    save_settings({'tts_engine': engine})
    logging.info(f"TTS engine set to: {engine}")
    return jsonify({'engine': engine})


# --------------------------------------------------
# GENERATE TTS AUDIO
# --------------------------------------------------
@tts_bp.route('/generate', methods=['POST'])
def generate_tts():
    """Generate TTS audio from text using selected engine"""
    try:
        engine = get_engine()

        if engine == 'none':
            return jsonify({'error': 'TTS engine is set to None'}), 503

        data = request.json
        text = data.get('text', '')
        voice = data.get('voice') or DEFAULT_VOICE
        # first_chunk lets the F5 server use a faster nfe_step for the opening
        # word (lower first-byte latency). Was previously dropped here, so the
        # fast path never actually fired.
        first_chunk = bool(data.get('first_chunk', False))
        # Guard against 'null' string or empty string from mobile/JS
        if not voice or voice.lower() in ('null', 'none', 'undefined'):
            voice = DEFAULT_VOICE

        if not text:
            return jsonify({'error': 'No text provided'}), 400

        server_url = get_server_url()
        logging.info(f"Generating TTS [{engine}] for: {text[:50]}...")

        payload = {'text': text, 'voice': voice, 'first_chunk': first_chunk}

        response = requests.post(
            f'{server_url}/tts_to_audio',
            json=payload,
            timeout=60
        )

        if response.status_code == 200:
            audio_data = BytesIO(response.content)
            return send_file(
                audio_data,
                mimetype='audio/wav',
                as_attachment=False,
                download_name='tts_output.wav'
            )
        else:
            logging.error(f"TTS server error: {response.status_code}")
            return jsonify({'error': f'TTS generation failed: {response.status_code}'}), 500

    except requests.exceptions.Timeout:
        logging.error("TTS server timeout")
        return jsonify({'error': 'TTS generation timed out'}), 504
    except requests.exceptions.ConnectionError:
        logging.error("Cannot connect to TTS server")
        return jsonify({'error': 'Cannot connect to TTS server. Is it running?'}), 503
    except Exception as e:
        logging.error(f"TTS generation error: {str(e)}")
        return jsonify({'error': str(e)}), 500


# --------------------------------------------------
# LIST AVAILABLE VOICES
# --------------------------------------------------
@tts_bp.route('/voices', methods=['GET'])
def get_voices():
    """Get available voices from active TTS server"""
    try:
        server_url = get_server_url()
        response = requests.get(f'{server_url}/voices', timeout=5)
        if response.status_code == 200:
            data = response.json()
            voices = [{"name": v, "label": v} for v in data.get("voices", [])]
            return jsonify({"voices": voices})
        else:
            return jsonify({"voices": [{"name": DEFAULT_VOICE, "label": DEFAULT_VOICE}]})
    except Exception as e:
        logging.error(f"Error fetching voices: {str(e)}")
        return jsonify({"voices": [{"name": DEFAULT_VOICE, "label": DEFAULT_VOICE}]})


# --------------------------------------------------
# WARMUP
# --------------------------------------------------
@tts_bp.route('/warmup', methods=['POST'])
def warmup_tts():
    """Fire a lightweight warmup request to heat the GPU before real requests.
    Uses a background thread so it returns instantly — never blocks the client."""
    try:
        engine = get_engine()
        if engine == 'none':
            return jsonify({'status': 'skipped'})
        data = request.json or {}
        voice = data.get('voice', DEFAULT_VOICE)
        server_url = get_server_url()

        # Fire warmup in background thread — return immediately to the client
        import threading
        def _warmup():
            try:
                requests.post(f'{server_url}/warmup', json={'voice': voice}, timeout=10)
            except Exception:
                pass  # Warmup is non-critical, silently ignore failures
        threading.Thread(target=_warmup, daemon=True).start()

        return jsonify({'status': 'ok'})
    except Exception as e:
        logging.warning(f"Warmup skipped: {str(e)}")
        return jsonify({'status': 'skipped', 'reason': str(e)})


# --------------------------------------------------
# STATUS CHECK
# --------------------------------------------------
@tts_bp.route('/status', methods=['GET'])
def tts_status():
    """Check if active TTS server is running"""
    engine = get_engine()

    if engine == 'none':
        return jsonify({'status': 'disabled', 'engine': 'none'})

    try:
        server_url = get_server_url()
        response = requests.get(f'{server_url}/status', timeout=2)
        if response.status_code == 200:
            data = response.json()
            return jsonify({
                'status': 'online',
                'engine': engine,
                'url': server_url,
                'gpu': data.get('gpu', 'unknown')
            })
        else:
            return jsonify({'status': 'error', 'engine': engine}), 503
    except Exception:
        return jsonify({
            'status': 'offline',
            'engine': engine,
            'message': f'Cannot connect to {engine.upper()} server'
        }), 503
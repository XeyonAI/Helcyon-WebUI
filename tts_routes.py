"""TTS routes for F5-TTS, XTTS, Chatterbox, Qwen3-TTS, and OmniVoice backends."""

from flask import Blueprint, request, jsonify, send_file, Response, stream_with_context
import requests
from io import BytesIO
import logging
import json
import os
import base64
import struct
from pathlib import Path
from user_config import load_user_config_section, save_user_config_section
from urllib.parse import quote

from tts_path_config import apply_tts_path_overrides

apply_tts_path_overrides()

# Create blueprint
tts_bp = Blueprint('tts', __name__)

# Server URLs
F5_SERVER_URL          = 'http://localhost:8003'
XTTS_SERVER_URL        = 'http://localhost:8002'
CHATTERBOX_SERVER_URL  = 'http://localhost:8004'
QWEN_FAST_SERVER_URL   = 'http://127.0.0.1:8767'
QWENTTS_CPP_SERVER_URL = 'http://127.0.0.1:8768'
OMNIVOICE_SERVER_URL    = 'http://127.0.0.1:8001'
DEFAULT_VOICE = 'Sol'
def _voice_forge_pro_only():
    return jsonify({'error': 'Voice Forge is available in HWUI Pro.', 'pro_required': True}), 403
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'settings.json')
VOICE_GROUPS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'voice_groups.json')  # legacy path
QWENTTS_VOICES_DIR = Path(os.getenv(
    'HWUI_QWENTTS_VOICES_DIR',
    os.getenv('HWUI_QWEN_VOICES_DIR', r'C:\HWUI-TTS\F5\voices'),
))
F5_VOICES_DIR = Path(os.getenv(
    'HWUI_F5_VOICES_DIR',
    os.path.join(os.getenv('HWUI_TTS_ROOT', r'C:\HWUI-TTS'), 'F5', 'voices'),
))
QWENTTS_MAX_REFERENCE_BYTES = 20 * 1024 * 1024
QWENTTS_QUANTIZATIONS = {'Q8_0', 'Q4_K_M', 'Q3_K_M'}
QWEN_FAST_TALKER_QUANTIZATIONS = {'Q8_0', 'Q4_K_M'}
_omnivoice_client = None
_omnivoice_client_lock = None
_omnivoice_generate_lock = None


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
    elif engine == 'qwen-fast':
        return QWEN_FAST_SERVER_URL
    elif engine == 'qwentts-cpp':
        return QWENTTS_CPP_SERVER_URL
    elif engine == 'omnivoice':
        return OMNIVOICE_SERVER_URL
    else:
        return F5_SERVER_URL


def _qwentts_error_message(response, fallback):
    try:
        payload = response.json()
        return payload.get('error', {}).get('message') or payload.get('error') or fallback
    except Exception:
        return response.text[:500] or fallback


def _qwentts_voice_files(voice):
    """Resolve a voice name only through the configured shared voice directory."""
    if not isinstance(voice, str) or not voice.strip() or Path(voice).name != voice:
        return None
    wav_path = QWENTTS_VOICES_DIR / f'{voice}.wav'
    return wav_path if wav_path.is_file() else None


def _qwentts_local_voices():
    try:
        return sorted(path.stem for path in QWENTTS_VOICES_DIR.glob('*.wav') if path.is_file())
    except OSError as exc:
        logging.error(f'qwentts.cpp voice scan failed in {QWENTTS_VOICES_DIR}: {exc}')
        return []


def _f5_local_voices():
    """List valid F5 reference pairs even when the inference server is offline."""
    try:
        return sorted(
            path.stem for path in F5_VOICES_DIR.glob('*.wav')
            if path.is_file() and path.with_suffix('.txt').is_file()
        )
    except OSError as exc:
        logging.error(f'F5 voice scan failed in {F5_VOICES_DIR}: {exc}')
        return []


def _generate_omnivoice(text, voice, language='English'):
    """Call the official OmniVoice Gradio demo using a local HWUI reference pair."""
    global _omnivoice_client, _omnivoice_client_lock, _omnivoice_generate_lock
    if _omnivoice_client_lock is None:
        import threading
        _omnivoice_client_lock = threading.Lock()
        _omnivoice_generate_lock = threading.Lock()
    if not isinstance(voice, str) or not voice.strip() or Path(voice).name != voice:
        raise ValueError('Invalid OmniVoice reference name')
    wav_path = F5_VOICES_DIR / f'{voice}.wav'
    txt_path = wav_path.with_suffix('.txt')
    if not wav_path.is_file():
        raise FileNotFoundError(f'OmniVoice reference not found: {wav_path.name}')
    if not txt_path.is_file() or not txt_path.read_text(encoding='utf-8').strip():
        raise ValueError(f'OmniVoice requires a matching transcript: {txt_path.name}')
    ref_text = txt_path.read_text(encoding='utf-8').strip()
    try:
        from gradio_client import Client, handle_file
    except ImportError as exc:
        raise RuntimeError('gradio_client is not installed in the HWUI environment') from exc
    import contextlib
    import io
    with _omnivoice_client_lock:
        if _omnivoice_client is None:
            # gradio_client prints a Unicode checkmark during connect; the
            # Windows Flask console may still be cp1252 when launched manually.
            with contextlib.redirect_stdout(io.StringIO()):
                _omnivoice_client = Client(OMNIVOICE_SERVER_URL)
    # The official demo queues GPU work; serialize requests to keep batch-1 VRAM bounded.
    with _omnivoice_generate_lock:
        with contextlib.redirect_stdout(io.StringIO()):
            result = _omnivoice_client.predict(
                text, language or 'English', handle_file(str(wav_path)), ref_text,
                # Fewer diffusion steps shorten the GPU residency window while
                # retaining the existing zero-shot reference workflow.
                None, 10, 2.0, True, 1.0, None, True, True,
                api_name='/_clone_fn',
            )
    output_path = result[0] if isinstance(result, (tuple, list)) else result
    if not output_path or not Path(output_path).is_file():
        raise RuntimeError('OmniVoice returned no audio file')
    return Path(output_path).read_bytes()


def _qwentts_registered_voice_names(server_url=QWENTTS_CPP_SERVER_URL):
    response = requests.get(f'{server_url}/v1/audio/voices', timeout=5)
    if response.status_code != 200:
        raise RuntimeError(_qwentts_error_message(response, f'voice listing returned {response.status_code}'))
    return {
        item.get('name')
        for item in response.json().get('voices', [])
        if isinstance(item, dict) and item.get('name')
    }


def _ensure_qwentts_voice_registered(voice, server_url=QWENTTS_CPP_SERVER_URL):
    if voice in _qwentts_registered_voice_names(server_url):
        return

    wav_path = _qwentts_voice_files(voice)
    if wav_path is None:
        raise FileNotFoundError(f'Voice reference not found: {voice}.wav')

    transcript_path = wav_path.with_suffix('.txt')
    transcript = transcript_path.read_text(encoding='utf-8').strip() if transcript_path.is_file() else ''
    spk_path = wav_path.with_suffix('.spk')
    rvq_path = wav_path.with_suffix('.rvq')
    payload = {'name': voice}
    if transcript:
        payload['ref_text'] = transcript

    source_mtime = max(
        wav_path.stat().st_mtime,
        transcript_path.stat().st_mtime if transcript_path.is_file() else 0,
    )
    sidecars_fresh = (
        spk_path.is_file()
        and rvq_path.is_file()
        and min(spk_path.stat().st_mtime, rvq_path.stat().st_mtime) >= source_mtime
    )
    if sidecars_fresh:
        payload['spk_b64'] = base64.b64encode(spk_path.read_bytes()).decode('ascii')
        payload['rvq_b64'] = base64.b64encode(rvq_path.read_bytes()).decode('ascii')
        source = 'pre-encoded .spk/.rvq'
    else:
        if wav_path.stat().st_size > QWENTTS_MAX_REFERENCE_BYTES:
            raise ValueError(f'{wav_path.name} is too large for qwentts.cpp voice registration')
        payload['wav_b64'] = base64.b64encode(wav_path.read_bytes()).decode('ascii')
        source = 'reference WAV'

    response = requests.post(f'{server_url}/v1/audio/voices', json=payload, timeout=120)
    if response.status_code != 200:
        raise RuntimeError(_qwentts_error_message(response, f'voice registration returned {response.status_code}'))
    clone_mode = 'ICL' if transcript else 'x-vector only'
    logging.info(f'qwentts.cpp registered voice {voice!r} from {source} ({clone_mode})')


# --------------------------------------------------
# VOICE GROUPS — per-build dropdown organisation
# --------------------------------------------------
def _empty_voice_groups():
    return {'groups': [], 'assignments': {}, 'collapsed': {}}


@tts_bp.route('/voice_groups', methods=['GET'])
def get_voice_groups():
    try:
        data = load_user_config_section('voice_groups')
        if not isinstance(data, dict):
            raise ValueError('Voice group state must be a JSON object')
        return jsonify({
            'groups': data.get('groups', []),
            'assignments': data.get('assignments', {}),
            'collapsed': data.get('collapsed', {})
        })
    except Exception as e:
        logging.error(f'Failed to read voice groups: {e}')
        return jsonify(_empty_voice_groups())


@tts_bp.route('/voice_groups', methods=['POST'])
def save_voice_groups():
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({'success': False, 'error': 'Invalid group state'}), 400

        raw_groups = data.get('groups', [])
        raw_assignments = data.get('assignments', {})
        raw_collapsed = data.get('collapsed', {})
        if not isinstance(raw_groups, list) or not isinstance(raw_assignments, dict) or not isinstance(raw_collapsed, dict):
            return jsonify({'success': False, 'error': 'Invalid group state'}), 400

        groups = []
        group_ids = set()
        for raw_group in raw_groups:
            if not isinstance(raw_group, dict):
                return jsonify({'success': False, 'error': 'Invalid group'}), 400
            group_id = str(raw_group.get('id', '')).strip()[:80]
            name = str(raw_group.get('name', '')).strip()[:40]
            if not group_id or not name or group_id in group_ids:
                return jsonify({'success': False, 'error': 'Invalid or duplicate group'}), 400
            group_ids.add(group_id)
            groups.append({'id': group_id, 'name': name})

        assignments = {}
        for voice_name, group_id in raw_assignments.items():
            if isinstance(voice_name, str) and isinstance(group_id, str) and group_id in group_ids:
                assignments[voice_name[:200]] = group_id

        collapsed = {}
        allowed_sections = group_ids | {'ungrouped'}
        for section_id, is_collapsed in raw_collapsed.items():
            if section_id in allowed_sections and isinstance(is_collapsed, bool):
                collapsed[section_id] = is_collapsed

        state = {'groups': groups, 'assignments': assignments, 'collapsed': collapsed}
        save_user_config_section('voice_groups', state)
        return jsonify({'success': True, **state})
    except Exception as e:
        logging.error(f'Failed to save voice groups: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


# --------------------------------------------------
# GET / SET TTS ENGINE
# --------------------------------------------------
@tts_bp.route('/engine', methods=['GET'])
def get_tts_engine():
    settings = get_settings()
    return jsonify({
        'engine': settings.get('tts_engine', 'f5'),
        'qwentts_cpp_quantization': settings.get('qwentts_cpp_quantization', 'Q8_0'),
        'qwen_fast_talker_quantization': settings.get('qwen_fast_talker_quantization', 'Q8_0'),
    })

@tts_bp.route('/engine', methods=['POST'])
def set_tts_engine():
    data = request.json or {}
    engine = data.get('engine', 'f5')
    if engine not in ('f5', 'xtts', 'chatterbox', 'qwen-fast', 'qwentts-cpp', 'omnivoice', 'none'):
        return jsonify({'error': 'Invalid engine'}), 400
    quantization = str(data.get('qwentts_cpp_quantization', 'Q8_0')).upper()
    if quantization not in QWENTTS_QUANTIZATIONS:
        return jsonify({'error': 'Invalid qwentts.cpp quantization'}), 400
    current_settings = get_settings()
    qwen_fast_quantization = str(
        data.get('qwen_fast_talker_quantization', current_settings.get('qwen_fast_talker_quantization', 'Q8_0'))
    ).upper()
    if qwen_fast_quantization not in QWEN_FAST_TALKER_QUANTIZATIONS:
        return jsonify({'error': 'Invalid qwen-fast talker quantization'}), 400
    if not save_settings({
        'tts_engine': engine,
        'qwentts_cpp_quantization': quantization,
        'qwen_fast_talker_quantization': qwen_fast_quantization,
    }):
        return jsonify({'error': 'Could not save TTS settings'}), 500
    logging.info(f"TTS engine set to: {engine}")
    return jsonify({
        'engine': engine,
        'qwentts_cpp_quantization': quantization,
        'qwen_fast_talker_quantization': qwen_fast_quantization,
    })


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

        if engine == 'omnivoice':
            audio_data = BytesIO(_generate_omnivoice(text, voice, data.get('language', 'English')))
            return send_file(audio_data, mimetype='audio/wav', as_attachment=False, download_name='tts_output.wav')
        elif engine in ('qwen-fast', 'qwentts-cpp'):
            _ensure_qwentts_voice_registered(voice, server_url)
            response = requests.post(
                f'{server_url}/v1/audio/speech',
                json={
                    'input': text,
                    'voice': voice,
                    'response_format': 'wav',
                },
                timeout=180,
            )
        else:
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
            message = _qwentts_error_message(response, f'TTS generation failed: {response.status_code}')
            logging.error(f"TTS server error [{engine}] {response.status_code}: {message}")
            return jsonify({'error': message}), response.status_code if response.status_code < 500 else 502

    except requests.exceptions.Timeout:
        logging.error("TTS server timeout")
        return jsonify({'error': 'TTS generation timed out'}), 504
    except requests.exceptions.ConnectionError:
        logging.error("Cannot connect to TTS server")
        return jsonify({'error': 'Cannot connect to TTS server. Is it running?'}), 503
    except Exception as e:
        logging.error(f"TTS generation error: {str(e)}")
        return jsonify({'error': str(e)}), 500


@tts_bp.route('/generate_stream', methods=['POST'])
def generate_tts_stream():
    """Proxy genuine decoded Qwen PCM streaming through HWUI's own origin."""
    if get_engine() != 'qwen-fast':
        return jsonify({'error': 'Streaming is only available for Qwen3-TTS Fast'}), 400
    data = request.json or {}
    text = str(data.get('text') or '').strip()
    voice = data.get('voice') or DEFAULT_VOICE
    if not text:
        return jsonify({'error': 'No text provided'}), 400
    try:
        _ensure_qwentts_voice_registered(voice, QWEN_FAST_SERVER_URL)
        upstream = requests.post(
            f'{QWEN_FAST_SERVER_URL}/v1/audio/speech',
            json={
                'input': text,
                'voice': voice,
                'response_format': 'pcm',
                'seed': data.get('seed', 42),
            },
            stream=True,
            timeout=(10, 120),
        )
        if upstream.status_code != 200:
            message = upstream.text[:500]
            upstream.close()
            return jsonify({'error': message or f'Qwen Fast returned {upstream.status_code}'}), upstream.status_code

        @stream_with_context
        def relay():
            try:
                # Mobile's established PCM scheduler consumes a 44-byte WAV
                # header before scheduling the native s16le/24 kHz chunks.
                yield struct.pack(
                    '<4sI4s4sIHHIIHH4sI',
                    b'RIFF', 0xFFFFFFFF, b'WAVE', b'fmt ', 16, 1, 1,
                    24000, 48000, 2, 16, b'data', 0xFFFFFFFF,
                )
                for chunk in upstream.iter_content(chunk_size=4096):
                    if chunk:
                        yield chunk
            finally:
                upstream.close()

        return Response(relay(), mimetype='audio/wav', headers={'X-Audio-Streaming': 'decoded-pcm'})
    except requests.exceptions.Timeout:
        return jsonify({'error': 'Qwen Fast streaming timed out'}), 504
    except requests.exceptions.ConnectionError:
        return jsonify({'error': 'Cannot connect to Qwen Fast on port 8767'}), 503


# --------------------------------------------------
# LIST AVAILABLE VOICES
# --------------------------------------------------
@tts_bp.route('/voices', methods=['GET'])
def get_voices():
    """Get available voices from active TTS server"""
    engine = get_engine()
    try:
        if engine in ('qwen-fast', 'qwentts-cpp'):
            server_url = get_server_url()
            voices = [{"name": name, "label": name} for name in _qwentts_local_voices()]
            if not voices:
                voices = [{"name": DEFAULT_VOICE, "label": DEFAULT_VOICE}]
            try:
                backend_online = requests.get(f'{server_url}/health', timeout=5).status_code == 200
            except requests.RequestException:
                backend_online = False
            return jsonify({
                "voices": voices,
                "engine": engine,
                "backend_online": backend_online,
            })
        if engine == 'omnivoice':
            voices = [{"name": name, "label": name} for name in _f5_local_voices()]
            if not voices:
                voices = [{"name": DEFAULT_VOICE, "label": DEFAULT_VOICE}]
            try:
                backend_online = requests.get(OMNIVOICE_SERVER_URL, timeout=5).status_code == 200
            except requests.RequestException:
                backend_online = False
            return jsonify({"voices": voices, "engine": engine, "backend_online": backend_online})
        server_url = get_server_url()
        response = requests.get(f'{server_url}/voices', timeout=5)
        if response.status_code == 200:
            data = response.json()
            voices = [{"name": v, "label": v} for v in data.get("voices", [])]
            return jsonify({"voices": voices, "engine": engine, "backend_online": True})
        else:
            local_voices = _f5_local_voices() if engine == 'f5' else []
            voices = [{"name": name, "label": name} for name in local_voices]
            if not voices:
                voices = [{"name": DEFAULT_VOICE, "label": DEFAULT_VOICE}]
            return jsonify({"voices": voices, "engine": engine, "backend_online": False})
    except Exception as e:
        logging.error(f"Error fetching voices: {str(e)}")
        local_voices = _f5_local_voices() if engine == 'f5' else []
        voices = [{"name": name, "label": name} for name in local_voices]
        if not voices:
            voices = [{"name": DEFAULT_VOICE, "label": DEFAULT_VOICE}]
        return jsonify({"voices": voices, "engine": engine, "backend_online": False})


# --------------------------------------------------
# VOICE FORGE (experimental, Qwen-only and isolated from normal TTS routes)
# --------------------------------------------------
@tts_bp.route('/voice-forge/settings', methods=['GET'])
def get_voice_forge_settings():
    return _voice_forge_pro_only()
    try:
        response = requests.get(f'{QWEN_FAST_SERVER_URL}/blend-voices/test-text', timeout=5)
        payload = response.json()
        if response.status_code != 200:
            return jsonify({'error': payload.get('detail') or 'Could not load Voice Forge test text'}), response.status_code

        legacy_text = get_settings().get('voice_forge_test_text')
        if payload.get('saved') is False and isinstance(legacy_text, str) and legacy_text.strip():
            migration = requests.post(
                f'{QWEN_FAST_SERVER_URL}/blend-voices/test-text',
                json={'test_text': legacy_text.strip()},
                timeout=10,
            )
            if migration.status_code == 200:
                payload = migration.json()
        return jsonify({'test_text': payload.get('test_text') or 'Voice Forge is available in HWUI Pro.'})
    except requests.exceptions.ConnectionError:
        return jsonify({'error': 'Cannot connect to Qwen3-TTS Fast on port 8767'}), 503
    except requests.exceptions.Timeout:
        return jsonify({'error': 'Loading Voice Forge test text timed out'}), 504
    except Exception as e:
        logging.error(f'Voice Forge test text load failed: {e}')
        return jsonify({'error': 'Could not load Voice Forge test text'}), 502


@tts_bp.route('/voice-forge/settings', methods=['POST'])
def save_voice_forge_settings():
    return _voice_forge_pro_only()
    data = request.get_json(silent=True) or {}
    text = data.get('test_text')
    if not isinstance(text, str) or not text.strip():
        return jsonify({'error': 'Test text cannot be empty'}), 400
    text = text.strip()
    if len(text) > 10000:
        return jsonify({'error': 'Test text is too long'}), 400
    try:
        response = requests.post(
            f'{QWEN_FAST_SERVER_URL}/blend-voices/test-text',
            json={'test_text': text},
            timeout=10,
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {'error': response.text[:500] or 'Invalid response from Qwen3-TTS Fast'}
        if response.status_code != 200:
            return jsonify({'error': payload.get('detail') or payload.get('error') or 'Could not save Voice Forge test text'}), response.status_code
        return jsonify({'status': 'ok', 'test_text': payload.get('test_text') or text})
    except requests.exceptions.ConnectionError:
        return jsonify({'error': 'Cannot connect to Qwen3-TTS Fast on port 8767'}), 503
    except requests.exceptions.Timeout:
        return jsonify({'error': 'Saving Voice Forge test text timed out'}), 504
    except Exception as e:
        logging.error(f'Voice Forge test text save failed: {e}')
        return jsonify({'error': 'Could not save Voice Forge test text'}), 502


@tts_bp.route('/voice-forge/voices', methods=['GET'])
def get_voice_forge_voices():
    return _voice_forge_pro_only()
    try:
        response = requests.get(f'{QWEN_FAST_SERVER_URL}/voices', timeout=5)
        if response.status_code != 200:
            return jsonify({'error': 'Qwen3-TTS Fast is unavailable'}), response.status_code
        voices = [{'name': name, 'label': name} for name in response.json().get('voices', [])]
        return jsonify({'voices': voices, 'backend_online': True})
    except requests.exceptions.ConnectionError:
        return jsonify({'error': 'Cannot connect to Qwen3-TTS Fast on port 8767'}), 503
    except requests.exceptions.Timeout:
        return jsonify({'error': 'Qwen3-TTS Fast did not respond'}), 504
    except Exception as e:
        logging.error(f'Voice Forge voice listing failed: {e}')
        return jsonify({'error': 'Could not load Voice Forge voices'}), 502


@tts_bp.route('/voice-forge/reference', methods=['GET'])
def get_voice_forge_reference():
    return _voice_forge_pro_only()
    voice = str(request.args.get('voice') or '').strip()
    if not voice:
        return jsonify({'error': 'Voice name is required'}), 400
    try:
        response = requests.get(
            f'{QWEN_FAST_SERVER_URL}/blend-voices/reference',
            params={'voice': voice},
            timeout=(5, 60),
        )
        if response.status_code != 200:
            try:
                message = response.json().get('detail')
            except ValueError:
                message = None
            return jsonify({'error': message or 'Voice reference is unavailable'}), response.status_code
        return Response(response.content, mimetype='audio/wav')
    except requests.exceptions.ConnectionError:
        return jsonify({'error': 'Cannot connect to Qwen3-TTS Fast on port 8767'}), 503
    except requests.exceptions.Timeout:
        return jsonify({'error': 'Voice reference preview timed out'}), 504
    except Exception as e:
        logging.error(f'Voice Forge reference proxy failed: {e}')
        return jsonify({'error': 'Voice reference is unavailable'}), 502


@tts_bp.route('/voice-forge/voice', methods=['DELETE'])
def delete_voice_forge_voice():
    return _voice_forge_pro_only()
    voice = str(request.args.get('voice') or '').strip()
    if not voice:
        return jsonify({'error': 'Voice name is required'}), 400
    try:
        response = requests.delete(
            f'{QWEN_FAST_SERVER_URL}/blend-voices/voice',
            params={'voice': voice},
            timeout=(5, 30),
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {'error': response.text[:500] or 'Invalid response from Qwen3-TTS Fast'}
        if response.status_code != 200:
            message = payload.get('detail') or payload.get('error') or 'Could not delete the voice'
            return jsonify({'error': message}), response.status_code
        return jsonify(payload)
    except requests.exceptions.ConnectionError:
        return jsonify({'error': 'Cannot connect to Qwen3-TTS Fast on port 8767'}), 503
    except requests.exceptions.Timeout:
        return jsonify({'error': 'Deleting the voice timed out'}), 504
    except Exception as e:
        logging.error(f'Voice Forge delete proxy failed: {e}')
        return jsonify({'error': 'Could not delete the voice'}), 502


@tts_bp.route('/voice-forge/blend', methods=['POST'])
def blend_voice_forge_sources():
    return _voice_forge_pro_only()
    fields = {
        key: value for key, value in request.form.items()
        if key in {'text', 'blend_ratio', 'expression', 'speed', 'language', 'seed', 'voice_a', 'voice_b'} and value != ''
    }
    files = {}
    for key in ('voice_a_audio', 'voice_b_audio'):
        upload = request.files.get(key)
        if upload and upload.filename:
            files[key] = (Path(upload.filename).name, upload.stream, upload.mimetype)
    try:
        response = requests.post(
            f'{QWEN_FAST_SERVER_URL}/blend-voices',
            data=fields,
            files=files,
            timeout=(10, 180),
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {'error': response.text[:500] or 'Invalid response from Qwen3-TTS Fast'}
        if response.status_code != 200:
            message = payload.get('detail') or payload.get('error') or 'Voice Forge generation failed'
            return jsonify({'error': message}), response.status_code
        filename = Path(str(payload.get('audio_url') or '')).name
        if not filename.lower().endswith('.wav'):
            return jsonify({'error': 'Qwen3-TTS Fast did not return Voice Forge audio'}), 502
        payload['audio_url'] = f'/api/tts/voice-forge/audio/{quote(filename)}'
        return jsonify(payload)
    except requests.exceptions.ConnectionError:
        return jsonify({'error': 'Cannot connect to Qwen3-TTS Fast on port 8767'}), 503
    except requests.exceptions.Timeout:
        return jsonify({'error': 'Voice Forge generation timed out'}), 504
    except Exception as e:
        logging.error(f'Voice Forge generation proxy failed: {e}')
        return jsonify({'error': 'Voice Forge generation failed'}), 502


@tts_bp.route('/voice-forge/audio/<path:filename>', methods=['GET'])
def get_voice_forge_audio(filename):
    return _voice_forge_pro_only()
    safe_name = Path(filename).name
    if safe_name != filename or not safe_name.lower().endswith('.wav'):
        return jsonify({'error': 'Invalid Voice Forge audio name'}), 400
    try:
        response = requests.get(
            f'{QWEN_FAST_SERVER_URL}/blend-voices/audio/{quote(safe_name)}',
            timeout=(5, 60),
        )
        if response.status_code != 200:
            return jsonify({'error': 'Voice Forge audio is unavailable'}), response.status_code
        return Response(response.content, mimetype='audio/wav')
    except requests.exceptions.ConnectionError:
        return jsonify({'error': 'Cannot connect to Qwen3-TTS Fast on port 8767'}), 503
    except requests.exceptions.Timeout:
        return jsonify({'error': 'Voice Forge audio timed out'}), 504
    except Exception as e:
        logging.error(f'Voice Forge audio proxy failed: {e}')
        return jsonify({'error': 'Voice Forge audio is unavailable'}), 502


@tts_bp.route('/voice-forge/save', methods=['POST'])
def save_voice_forge_result():
    return _voice_forge_pro_only()
    fields = {
        'result_id': str(request.form.get('result_id') or ''),
        'voice_name': str(request.form.get('voice_name') or ''),
        'transcript': str(request.form.get('transcript') or ''),
    }
    try:
        response = requests.post(
            f'{QWEN_FAST_SERVER_URL}/blend-voices/save',
            data=fields,
            timeout=(5, 30),
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {'error': response.text[:500] or 'Invalid response from Qwen3-TTS Fast'}
        if response.status_code != 200:
            message = payload.get('detail') or payload.get('error') or 'Could not save HWUI voice'
            return jsonify({'error': message}), response.status_code
        return jsonify(payload)
    except requests.exceptions.ConnectionError:
        return jsonify({'error': 'Cannot connect to Qwen3-TTS Fast on port 8767'}), 503
    except requests.exceptions.Timeout:
        return jsonify({'error': 'Saving the HWUI voice timed out'}), 504
    except Exception as e:
        logging.error(f'Voice Forge save proxy failed: {e}')
        return jsonify({'error': 'Could not save the HWUI voice'}), 502


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
        if engine == 'omnivoice':
            return jsonify({'status': 'skipped', 'reason': 'OmniVoice model is warmed by its dedicated demo server'})

        # Fire warmup in background thread — return immediately to the client
        import threading
        def _warmup():
            try:
                if engine in ('qwen-fast', 'qwentts-cpp'):
                    _ensure_qwentts_voice_registered(voice, server_url)
                    response = requests.post(
                        f'{server_url}/v1/audio/speech',
                        json={'input': 'Ready.', 'voice': voice, 'response_format': 'wav', 'max_new_tokens': 64},
                        timeout=120,
                    )
                    if response.status_code != 200:
                        logging.warning(
                            f'qwentts.cpp warmup returned {response.status_code}: '
                            f'{_qwentts_error_message(response, "unknown error")}'
                        )
                else:
                    requests.post(f'{server_url}/warmup', json={'voice': voice}, timeout=10)
            except Exception as exc:
                logging.warning(f'TTS warmup failed [{engine}]: {exc}')
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
        if engine == 'omnivoice':
            response = requests.get(f'{OMNIVOICE_SERVER_URL}/', timeout=2)
            return jsonify({'status': 'online' if response.status_code == 200 else 'error', 'engine': engine,
                            'url': OMNIVOICE_SERVER_URL, 'gpu': 'OmniVoice demo'})
        server_url = get_server_url()
        status_path = '/health' if engine in ('qwen-fast', 'qwentts-cpp') else '/status'
        response = requests.get(f'{server_url}{status_path}', timeout=2)
        if response.status_code == 200:
            data = response.json()
            return jsonify({
                'status': 'online',
                'engine': engine,
                'url': server_url,
                'gpu': data.get('gpu', 'unknown'),
                'quantization': ('Q8_0' if engine == 'qwen-fast' else get_settings().get('qwentts_cpp_quantization'))
                                if engine in ('qwen-fast', 'qwentts-cpp') else None,
            })
        else:
            return jsonify({'status': 'error', 'engine': engine}), 503
    except Exception:
        return jsonify({
            'status': 'offline',
            'engine': engine,
            'message': f'Cannot connect to {engine.upper()} server'
        }), 503

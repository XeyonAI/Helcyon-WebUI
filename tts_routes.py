"""TTS routes for F5-TTS, XTTS, Chatterbox, Qwen3-TTS, and OmniVoice backends."""

from flask import Blueprint, request, jsonify, send_file, Response, stream_with_context
import requests
from io import BytesIO
import logging
import json
import os
import base64
import struct
import re
import threading
import uuid
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
VOICE_FORGE_DEFAULT_TEXT = 'Hello. This is a test of a newly blended voice.'
VOICE_FORGE_TEST_TEXT_FILENAME = '_voice_forge_test_text.txt'
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
_voice_forge_results = {}
_voice_forge_results_lock = threading.Lock()


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


def _voice_forge_test_text_path():
    return QWENTTS_VOICES_DIR / VOICE_FORGE_TEST_TEXT_FILENAME


def _repair_voice_forge_mojibake(text):
    """Repair common UTF-8-as-Windows-1252 text from the legacy setting."""
    repaired = text
    markers = ('\u00c3', '\u00c2', '\u00e2', '\u20ac\u2122', '\ufffd')
    for _ in range(2):
        direct = repaired.replace('\u00e2\u20ac\u2122', '\u2019').replace('\u20ac\u2122', '\u2019')
        if direct != repaired:
            repaired = direct
            continue
        if not any(marker in repaired for marker in markers):
            break
        try:
            candidate = repaired.encode('cp1252').decode('utf-8')
        except (UnicodeEncodeError, UnicodeDecodeError):
            candidate = repaired
        if candidate == repaired:
            break
        repaired = candidate
    return repaired


def _write_voice_forge_test_text(text):
    """Atomically persist Voice Forge's test text in the shared voice folder."""
    path = _voice_forge_test_text_path()
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        import tempfile
        fd, temporary = tempfile.mkstemp(
            suffix='.tmp', prefix=f'.{VOICE_FORGE_TEST_TEXT_FILENAME}.', dir=path.parent, text=True
        )
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(text + '\n')
        os.replace(temporary, path)
        return True
    except Exception as exc:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass
        logging.error(f'❌ Failed to save Voice Forge test text: {exc}')
        return False


def _load_voice_forge_test_text():
    """Read the shared UTF-8 text, migrating the old settings value once."""
    path = _voice_forge_test_text_path()
    if path.is_file():
        try:
            text = path.read_text(encoding='utf-8-sig').strip()
            if text:
                repaired = _repair_voice_forge_mojibake(text)
                if repaired != text:
                    _write_voice_forge_test_text(repaired)
                return repaired
            return None
        except (OSError, UnicodeError) as exc:
            logging.error(f'⚠️ Could not read Voice Forge test text: {exc}')
            return None

    legacy = get_settings().get('voice_forge_test_text')
    if isinstance(legacy, str) and legacy.strip():
        text = _repair_voice_forge_mojibake(legacy.strip())
        _write_voice_forge_test_text(text)
        return text
    return None

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


def _voice_forge_upload_as_wav(upload):
    """Return a requests-compatible WAV upload, decoding MP3 in HWUI when needed."""
    raw = upload.read()
    filename = Path(upload.filename or 'voice.wav').name
    if raw[:4] == b'RIFF':
        return filename, raw, 'audio/wav'
    try:
        import numpy as np
        import soundfile as sf
        import librosa

        audio, sample_rate = sf.read(BytesIO(raw), dtype='float32', always_2d=False)
        audio = np.asarray(audio, dtype=np.float32)
        if audio.ndim == 2:
            audio = audio.mean(axis=1)
        if sample_rate != 24000:
            audio = librosa.resample(audio, orig_sr=sample_rate, target_sr=24000)
        encoded = BytesIO()
        sf.write(encoded, audio, 24000, format='WAV', subtype='PCM_16')
        return f'{Path(filename).stem}.wav', encoded.getvalue(), 'audio/wav'
    except Exception as exc:
        raise ValueError(f'Could not decode Voice Forge upload {filename!r}: {exc}') from exc


def _voice_forge_apply_speed(wav_bytes, speed):
    if abs(speed - 1.0) < 1e-6:
        return wav_bytes
    try:
        import numpy as np
        import soundfile as sf
        import librosa

        audio, sample_rate = sf.read(BytesIO(wav_bytes), dtype='float32', always_2d=False)
        audio = np.asarray(audio, dtype=np.float32)
        if audio.ndim == 2:
            audio = audio.mean(axis=1)
        stretched = librosa.effects.time_stretch(audio, rate=speed)
        encoded = BytesIO()
        sf.write(encoded, stretched, sample_rate, format='WAV', subtype='PCM_16')
        return encoded.getvalue()
    except Exception as exc:
        raise RuntimeError(f'Voice Forge speed adjustment failed: {exc}') from exc


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
    return jsonify({'error': 'Voice Forge is available in the Pro build only.', 'pro_required': True}), 403


@tts_bp.route('/voice-forge/settings', methods=['POST'])
def save_voice_forge_settings():
    return jsonify({'error': 'Voice Forge is available in the Pro build only.', 'pro_required': True}), 403


@tts_bp.route('/voice-forge/voices', methods=['GET'])
def get_voice_forge_voices():
    return jsonify({'error': 'Voice Forge is available in the Pro build only.', 'pro_required': True}), 403


@tts_bp.route('/voice-forge/reference', methods=['GET'])
def get_voice_forge_reference():
    return jsonify({'error': 'Voice Forge is available in the Pro build only.', 'pro_required': True}), 403


@tts_bp.route('/voice-forge/voice', methods=['DELETE'])
def delete_voice_forge_voice():
    return jsonify({'error': 'Voice Forge is available in the Pro build only.', 'pro_required': True}), 403


@tts_bp.route('/voice-forge/blend', methods=['POST'])
def blend_voice_forge_sources():
    return jsonify({'error': 'Voice Forge is available in the Pro build only.', 'pro_required': True}), 403


@tts_bp.route('/voice-forge/audio/<path:filename>', methods=['GET'])
def get_voice_forge_audio(filename):
    return jsonify({'error': 'Voice Forge is available in the Pro build only.', 'pro_required': True}), 403


@tts_bp.route('/voice-forge/save', methods=['POST'])
def save_voice_forge_result():
    return jsonify({'error': 'Voice Forge is available in the Pro build only.', 'pro_required': True}), 403


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

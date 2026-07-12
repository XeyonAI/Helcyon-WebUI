"""
HWUI Blackwell Relay Script
Runs on Main PC (port 8002), forwards cleaned text to Docker XTTS on Music PC (port 8020)
"""

from flask import Flask, request, send_file, jsonify
from flask_cors import CORS
from threading import Lock
import requests
import os
import time
import re
import glob

app = Flask(__name__)
CORS(app)

# --- CONFIGURATION ---
MUSIC_PC_IP = "192.168.0.15"
DOCKER_XTTS_URL = f"http://{MUSIC_PC_IP}:8020/tts_to_audio"
DOCKER_BASE_URL = f"http://{MUSIC_PC_IP}:8020"
VOICE_FOLDER = "voices"
DEFAULT_VOICE = "Sol.wav"

# Prevents multiple simultaneous requests overwhelming Docker XTTS
generation_lock = Lock()

# --------------------------------------------------
# XTTS SAMPLING SETTINGS
# Tweak these to change how the voice sounds
# --------------------------------------------------

# TEMPERATURE (0.1 - 1.0)
# Controls randomness/expressiveness of the voice.
# Low (0.1-0.3)  = Flat, robotic, very consistent
# Mid (0.5-0.7)  = Natural, expressive — sweet spot
# High (0.8-1.0) = Very expressive but can go wobbly/unstable
TEMPERATURE = 0.3

# REPETITION PENALTY (1.0 - 5.0)
# Stops the model getting stuck repeating sounds or syllables.
# Low (1.0)      = No penalty, can stutter or loop
# Mid (2.0)      = Good default, prevents most repetition
# High (3.0+)    = Aggressive, can make speech sound unnatural
REPETITION_PENALTY = 2.5

# TOP_K (1 - 100)
# Limits how many possible next-tokens the model considers.
# Low (10-20)    = Very focused, less varied delivery
# Mid (50)       = Balanced — recommended
# High (80-100)  = More varied but less controlled
TOP_K = 30

# TOP_P (0.0 - 1.0)
# Works with TOP_K — cuts off unlikely options by probability mass.
# Low (0.5-0.7)  = Conservative, very clean output
# Mid (0.85)     = Good balance — recommended
# High (0.95-1.0)= More creative, occasionally unstable
TOP_P = 0.75

# SPEED (0.5 - 2.0)
# Playback speed of the generated audio.
# 0.8  = Slower, more deliberate
# 1.0  = Normal speed
# 1.25 = Slightly faster — good for long responses
# 1.5+ = Fast but can sound rushed
SPEED = 1.0

# --------------------------------------------------

# Ensure the voices folder exists on startup
if not os.path.exists(VOICE_FOLDER):
    os.makedirs(VOICE_FOLDER)

print("🚀 HWUI Blackwell Relay Active")
print(f"📁 Scanning for voices in: {os.path.abspath(VOICE_FOLDER)}")
print(f"🔗 Connected to Docker at: {DOCKER_XTTS_URL}")
print(f"🎛️  Settings: temp={TEMPERATURE} rep_penalty={REPETITION_PENALTY} top_k={TOP_K} top_p={TOP_P} speed={SPEED}")
print(f"🔒 Request locking enabled — one request at a time to Docker")


@app.route('/tts_to_audio', methods=['POST'])
def tts_to_audio():
    """Main TTS endpoint - receives text and voice, forwards to Docker XTTS"""
    data = request.json
    text = data.get('text', '')
    voice = data.get('voice', DEFAULT_VOICE)

    # --- TEXT CLEANUP ---
    text = text.replace('\u2019', "'").replace('\u2018', "'")
    text = text.replace('\u201c', '"').replace('\u201d', '"')
    text = text.replace('\u2013', ',').replace('\u2014', ',')
    text = text.replace('...', ' ').replace('\u2026', ' ')
    text = re.sub(r'\bx{2,}\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'(?<![a-zA-Z])x(?![a-zA-Z])', 'ex', text, flags=re.IGNORECASE)
    text = text.strip()

    if not text:
        return jsonify({'error': 'No text'}), 400

    try:
        t_start = time.time()

        payload = {
            "text": text,
            "speaker_wav": voice,
            "language": "en",
            "temperature": TEMPERATURE,
            "repetition_penalty": REPETITION_PENALTY,
            "top_k": TOP_K,
            "top_p": TOP_P,
            "speed": SPEED
        }

        print(f" > Forwarding to Docker XTTS: \"{text[:60]}...\" [voice: {voice}]")

        # Lock ensures only one request hits Docker at a time
        with generation_lock:
            response = requests.post(DOCKER_XTTS_URL, json=payload, timeout=60)

        if response.status_code == 200:
            content_type = response.headers.get('Content-Type', '')

            # Docker returned a URL instead of raw audio - fetch the actual file
            if 'application/json' in content_type or response.content[:1] == b'{':
                try:
                    result = response.json()
                    audio_url = result.get('url', '')
                    print(f" > Docker returned URL: {audio_url}")

                    # Replace localhost with Music PC IP so we can reach it
                    audio_url = audio_url.replace('http://localhost:8020', f'http://{MUSIC_PC_IP}:8020')
                    audio_url = audio_url.replace('http://127.0.0.1:8020', f'http://{MUSIC_PC_IP}:8020')
                    print(f" > Fetching audio from: {audio_url}")

                    audio_response = requests.get(audio_url, timeout=15)
                    if audio_response.status_code == 200:
                        out_path = os.path.join(VOICE_FOLDER, "last_generated_output.wav")
                        with open(out_path, "wb") as f:
                            f.write(audio_response.content)
                        processing = time.time() - t_start
                        print(f" ✅ Success! Fetched & saved in: {processing:.2f}s ({len(audio_response.content)} bytes)")
                        return send_file(out_path, mimetype='audio/wav')
                    else:
                        print(f"❌ Failed to fetch audio from URL: {audio_response.status_code}")
                        return jsonify({'error': 'Failed to fetch audio from Docker URL'}), 500
                except Exception as e:
                    print(f"❌ Error parsing Docker JSON response: {e}")
                    return jsonify({'error': str(e)}), 500
            else:
                # Docker returned raw audio directly (old behaviour)
                out_path = os.path.join(VOICE_FOLDER, "last_generated_output.wav")
                with open(out_path, "wb") as f:
                    f.write(response.content)
                processing = time.time() - t_start
                print(f" ✅ Success! Generated in: {processing:.2f}s ({len(response.content)} bytes)")
                return send_file(out_path, mimetype='audio/wav')
        else:
            print(f"❌ Docker XTTS Error: {response.text}")
            return jsonify({'error': 'Docker generation failed'}), 500

    except Exception as e:
        print(f"❌ Relay error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/voices', methods=['GET'])
def get_voices():
    """Return list of available voice .wav files"""
    wav_files = glob.glob(os.path.join(VOICE_FOLDER, "*.wav"))
    # Exclude the debug output file from the voice list
    voices = [
        {"name": os.path.basename(f), "label": os.path.basename(f).replace('.wav', '')}
        for f in wav_files
        if os.path.basename(f) != "last_generated_output.wav"
    ]
    return jsonify({"voices": voices})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8002, threaded=True)
from flask import Flask, request, send_file, jsonify
from kokoro import KPipeline
import soundfile as sf
import tempfile
import os
import re
import torch

app = Flask(__name__)

device = 'cuda' if torch.cuda.is_available() else 'cpu'
print(f"ðŸ–¥ï¸ Kokoro using device: {device}")
pipeline = KPipeline(lang_code='en-gb', device=device)
print("âœ… Kokoro ready!")

# All available English voices
VOICES = [
    {"name": "af_heart",    "label": "Heart (AF) â­",      "accent": "American", "gender": "Female"},
    {"name": "af_bella",    "label": "Bella (AF)",          "accent": "American", "gender": "Female"},
    {"name": "af_nicole",   "label": "Nicole (AF)",         "accent": "American", "gender": "Female"},
    {"name": "af_sarah",    "label": "Sarah (AF)",          "accent": "American", "gender": "Female"},
    {"name": "af_sky",      "label": "Sky (AF)",            "accent": "American", "gender": "Female"},
    {"name": "af_alloy",    "label": "Alloy (AF)",          "accent": "American", "gender": "Female"},
    {"name": "af_aoede",    "label": "Aoede (AF)",          "accent": "American", "gender": "Female"},
    {"name": "af_jessica",  "label": "Jessica (AF)",        "accent": "American", "gender": "Female"},
    {"name": "af_kore",     "label": "Kore (AF)",           "accent": "American", "gender": "Female"},
    {"name": "af_nova",     "label": "Nova (AF)",           "accent": "American", "gender": "Female"},
    {"name": "af_river",    "label": "River (AF)",          "accent": "American", "gender": "Female"},
    {"name": "am_adam",     "label": "Adam (AM)",           "accent": "American", "gender": "Male"},
    {"name": "am_michael",  "label": "Michael (AM)",        "accent": "American", "gender": "Male"},
    {"name": "bf_emma",     "label": "Emma (BF)",           "accent": "British",  "gender": "Female"},
    {"name": "bf_isabella", "label": "Isabella (BF)",       "accent": "British",  "gender": "Female"},
    {"name": "bm_george",   "label": "George (BM)",         "accent": "British",  "gender": "Male"},
    {"name": "bm_lewis",    "label": "Lewis (BM)",          "accent": "British",  "gender": "Male"},
]

def strip_emojis(text):
    return re.sub(r'[^\x00-\x7F]+', '', text).strip()

@app.route('/voices', methods=['GET'])
def get_voices():
    return jsonify({"voices": VOICES})

@app.route('/tts_to_audio', methods=['POST'])
def tts_to_audio():
    data = request.json
    text = data.get('text', '')
    voice = data.get('voice', 'af_heart')  # Use voice from request, fallback to af_heart

    text = text.replace('\u2019', "'").replace('\u2018', "'")
    text = text.replace('\u201c', '"').replace('\u201d', '"')
    text = text.replace('\u2013', ',').replace('\u2014', ',')  # en/em dash â†’ comma
    text = text.replace('\u2026', ',')                         # unicode ellipsis â†’ comma
    text = re.sub(r' -- ', ', ', text)                         # double dash â†’ comma
    text = re.sub(r'\.{3}', ', ', text)                        # literal ... â†’ comma
    text = re.sub(r'\*+', '', text)                            # remove asterisks
    text = re.sub(r'\bI AM\b', 'I am', text, flags=re.IGNORECASE)  # normalize "I AM" casing
    text = re.sub(r'\bx{3,}\b', 'kisses', text, flags=re.IGNORECASE)  # xxx+ → kisses
    text = re.sub(r'\bxx\b', 'kisses', text, flags=re.IGNORECASE)     # xx → kisses
    text = re.sub(r'(?<![a-zA-Z])x(?![a-zA-Z])', 'ex', text, flags=re.IGNORECASE)  # standalone x → ex
    text = text.strip()
    text = strip_emojis(text)

    if not text:
        return jsonify({'error': 'No text'}), 400

    generator = pipeline(text, voice=voice, speed=1.25)

    tmp_path = tempfile.mktemp(suffix='.wav')

    for i, (gs, ps, audio) in enumerate(generator):
        if i == 0:
            sf.write(tmp_path, audio, 24000)
            break

    return send_file(tmp_path, mimetype='audio/wav')

if __name__ == '__main__':
    app.run(port=8002)
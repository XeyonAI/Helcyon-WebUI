import sys
import os
import re
import time
import tempfile
from threading import Lock
from flask import Flask, request, send_file, jsonify
import torch

# ----------------------------------------------------------------
# PATH SETUP
# ----------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VOICES_DIR = r"I:\F5-TTS\F5-TTS"

# Force HuggingFace cache off C: drive entirely
os.environ['HF_HOME'] = r"I:\HuggingFace"
os.environ['TRANSFORMERS_CACHE'] = r"I:\HuggingFace"

# ----------------------------------------------------------------
# STARTUP CHECK
# ----------------------------------------------------------------
print("--- F5-TTS Server (RTX 5060 Ti / Blackwell) ---")
device = "cuda" if torch.cuda.is_available() else "cpu"
print(f"Device  : {device}")
if device == "cuda":
    print(f"GPU     : {torch.cuda.get_device_name(0)}")
    print(f"VRAM    : {torch.cuda.get_device_properties(0).total_memory / 1024**3:.1f} GB")
print("-----------------------------------------------")

# ----------------------------------------------------------------
# LOAD F5-TTS — model loaded from I: drive, no HF download
# ----------------------------------------------------------------
from f5_tts.api import F5TTS
tts = F5TTS(ckpt_file=r"I:\F5-TTS\F5-TTS\model\model_1250000.safetensors", device=device)
tts_lock = Lock()
print("✅ F5-TTS model loaded and ready.")
# ----------------------------------------------------------------
# GPU WARMUP — silent inference on startup so first real request is fast
# ----------------------------------------------------------------
def warmup():
    try:
        voices = [f.replace(".wav", "") for f in os.listdir(VOICES_DIR)
                  if f.endswith(".wav") and os.path.exists(os.path.join(VOICES_DIR, f.replace(".wav", ".txt")))]
        if not voices:
            print("⚠️  No voices found for warmup, skipping.")
            return
        # Use Sol as preferred warmup voice, fallback to first available
        voice = "Sol" if "Sol" in voices else voices[0]
        wav_path = os.path.join(VOICES_DIR, f"{voice}.wav")
        txt_path = os.path.join(VOICES_DIR, f"{voice}.txt")
        with open(txt_path, "r", encoding="utf-8") as f:
            ref_text = f.read().strip()
        tmp_path = tempfile.mktemp(suffix=".wav")
        print(f"🔥 Warming up GPU with voice: {voice}...")
        tts.infer(
            ref_file=wav_path,
            ref_text=ref_text,
            gen_text=", Warmup.",
            file_wave=tmp_path,
            speed=1.0,
            nfe_step=24,
            cfg_strength=1.0
        )
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        print("✅ GPU warmup complete — ready for fast first response.")
    except Exception as e:
        print(f"⚠️  Warmup failed (non-critical): {e}")

warmup()



# ----------------------------------------------------------------
# FLASK APP
# ----------------------------------------------------------------
app = Flask(__name__)
DEFAULT_VOICE = "Sol"

# ----------------------------------------------------------------
# ACRONYM MAP — short/all-caps words that need special handling
# ----------------------------------------------------------------
ACRONYMS = {
    r"\bAI\b":   "A I",
    r"\bIS\b":   "is",
    r"\bIT\b":   "it",
    r"\bUK\b":   "U K",
    r"\bUS\b":   "U S",
    r"\bPC\b":   "P C",
    r"\bTV\b":   "T V",
    r"\bOK\b":   "okay",
    r"\bDJ\b":   "D J",
    r"\bVR\b":   "V R",
    r"\bAR\b":   "A R",
    r"\bRP\b":   "R P",
    r"\bUI\b":   "U I",
    r"\bAPI\b":  "A P I",
    r"\bURL\b":  "U R L",
    r"\bGPU\b":  "G P U",
    r"\bCPU\b":  "C P U",
    r"\bRAM\b":  "ram",
    r"\bVRAM\b": "V ram",
    r"\bLLM\b":  "L L M",
    r"\bTTS\b":  "T T S",
    r"\bGPT\b":  "G P T",
    r"\bNPC\b":  "N P C",
    r"\bFPS\b":  "F P S",
    r"\bRPG\b":  "R P G",
    r"\bD&D\b":  "D and D",
    r"\bDnD\b":  "D and D",
    # Science / biology
    r"\bDNA\b":  "D N A",
    r"\bRNA\b":  "R N A",
    r"\bMRI\b":  "M R I",
    r"\bIVF\b":  "I V F",
    r"\bCBD\b":  "C B D",
    r"\bTHC\b":  "T H C",
    # Tech
    r"\bSSD\b":  "S S D",
    r"\bHDD\b":  "H D D",
    r"\bUSB\b":  "U S B",
    r"\bHDR\b":  "H D R",
    r"\bFPS\b":  "F P S",
    r"\bVPN\b":  "V P N",
    r"\bSSH\b":  "S S H",
    r"\bSQL\b":  "sequel",
    r"\bHTML\b": "H T M L",
    r"\bCSS\b":  "C S S",
    r"\bJSON\b": "J S O N",
    r"\bHTTP\b": "H T T P",
    r"\bHTTPS\b":"H T T P S",
    r"\bOS\b":   "O S",
    r"\bIO\b":   "I O",
    # General
    r"\bIQ\b":   "I Q",
    r"\bHR\b":   "H R",
    r"\bPR\b":   "P R",
    r"\bCEO\b":  "C E O",
    r"\bCFO\b":  "C F O",
    r"\bCTO\b":  "C T O",
    r"\bHQ\b":   "H Q",
    r"\bETA\b":  "E T A",
    r"\bASAP\b": "A S A P",
    r"\bFYI\b":  "F Y I",
    r"\bDIY\b":  "D I Y",
    r"\bIRL\b":  "I R L",
    r"(?i)\bOMG\b":  "Oh my god!",
    r"\bMOT\b":  "M O T",
    r"\bTB\b":   "terabytes",
    r"\bGB\b":   "gigabytes",
    r"\bMB\b":   "megabytes",
    r"\bKB\b":   "kilobytes",
    r"(\d)TB\b": r"\1 terabytes",
    r"(\d)GB\b": r"\1 gigabytes",
    r"(\d)MB\b": r"\1 megabytes",
    r"(\d)KB\b": r"\1 kilobytes",
    r"(\d)mg\b": r"\1 milligrams",
    r"(\d)kg\b": r"\1 kilograms",
    r"(\d)ml\b": r"\1 millilitres",
    r"(\d)km\b": r"\1 kilometres",
    r"(\d)mph\b": r"\1 miles per hour",
    r"\bRTX\b":  "R T X",
    r"\bGTX\b":  "G T X",
}

def expand_acronyms(text):
    for pattern, replacement in ACRONYMS.items():
        text = re.sub(pattern, replacement, text)
    return text

def clean_text(text):
    """Normalise text for clean English TTS output."""
    # Fix smart quotes — keeps apostrophes intact for F5 to read contractions correctly
    text = text.replace('\u2019', "'").replace('\u2018', "'")
    text = text.replace('\u201c', '"').replace('\u201d', '"')

    # Expand known acronyms/short caps BEFORE the general caps handler
    text = expand_acronyms(text)

    # Em-dash and en-dash to pause
    text = text.replace('\u2014', '. ')  # em-dash
    text = text.replace('\u2013', '. ')  # en-dash

    # Emoji/symbol handling
    text = text.replace('\U0001F4AF', 'one hundred percent')
    text = text.replace('!', '.')
    # Strip emojis — remove the space+emoji entirely, then ensure a dot sits right after the last word char
    text = re.sub(u'(\w)\s*[\U0001F000-\U0001FFFF]+', r'\1.', text)
    text = re.sub(u'(\w)\s*[\U0001F300-\U0001FAFF]+', r'\1.', text)
    text = re.sub(u'(\w)\s*[\u2600-\u27BF]+', r'\1.', text)
    # Catch any remaining lone emojis (not preceded by a word char)
    text = re.sub(u'[\U0001F000-\U0001FFFF]+', '', text)
    text = re.sub(u'[\U0001F300-\U0001FAFF]+', '', text)
    text = re.sub(u'[\u2600-\u27BF]+', '', text)
    text = re.sub(r'[\u003A\uFE13\uFE30\uFE55\uFF1A]', ' . . ', text)
    text = re.sub(r'\s-\s', '. ', text)
    text = re.sub(r'(\w)-(\w)', r'\1 \2', text)
    text = re.sub(r'\bI AM\b', 'I am', text)
    text = re.sub(r'(\d)\s*[Aa][Mm]\b', r'\1 A M', text)
    text = re.sub(r'(\d)\s*[Pp][Mm]\b', r'\1 P M', text)
    text = re.sub(r'(\d)\s*[Aa][Mm]\b', r'\1 A M', text)
    text = re.sub(r'(\d)\s*[Pp][Mm]\b', r'\1 P M', text)
    text = re.sub(r'\bAM\b', 'am', text)
    text = re.sub(r'\bPM\b', 'pm', text)
    text = re.sub(r'\bHWUI\b', 'H-W-U-I', text)
    text = re.sub(r'\bhuman\b', 'yooman', text, flags=re.IGNORECASE)
    text = re.sub(r'\bGPT-4o\b', 'G P T four oh', text, flags=re.IGNORECASE)
    text = re.sub(r'\bGPT-4\b', 'G P T four', text, flags=re.IGNORECASE)
    text = re.sub(r'\s*=\s*', ' equals ', text)
    # Parentheses — replace with commas so F5 pauses naturally around them
    text = re.sub(r'\s*\(\s*', ', ', text)
    text = re.sub(r'\s*\)\s*', ', ', text)
    text = re.sub(r'\s*\)\s*', ', ', text)
    text = re.sub(r'\bbreather\b', 'breether', text)

    # Convert remaining ALL-CAPS words (3+ chars) to Title Case
    text = re.sub(r'\b[A-Z]{3,}\b', lambda m: m.group(0).title(), text)

    text = text.strip()

    # Pad the start so F5 doesn't clip the first word
    return ", " + text

@app.route('/tts_to_audio', methods=['POST'])
def tts_to_audio():
    data = request.json
    text = clean_text(data.get('text', ''))
    voice = data.get('voice', DEFAULT_VOICE).replace('.wav', '')

    if not text.strip():
        return jsonify({'error': 'No text provided'}), 400

    wav_path = os.path.join(VOICES_DIR, f"{voice}.wav")
    txt_path = os.path.join(VOICES_DIR, f"{voice}.txt")

    if not os.path.exists(wav_path) or not os.path.exists(txt_path):
        return jsonify({'error': f'Voice files for "{voice}" not found in {VOICES_DIR}'}), 400

    with open(txt_path, 'r', encoding='utf-8') as f:
        ref_text = f.read().strip()

    tmp_path = tempfile.mktemp(suffix='.wav')

    try:
        t_start = time.time()
        with tts_lock:
            wav, sr, _ = tts.infer(
                ref_file=wav_path,
                ref_text=ref_text,
                gen_text=text,
                file_wave=tmp_path,
                speed=1.0,
                nfe_step=24,
                cfg_strength=1.0
            )
        elapsed = time.time() - t_start
        print(f"✅ Generated in {elapsed:.2f}s | Voice: {voice}")
        return send_file(tmp_path, mimetype='audio/wav')

    except Exception as e:
        print(f"❌ F5 Error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/warmup', methods=['POST'])
def warmup_endpoint():
    """Lightweight GPU warmup — low nfe_step to heat GPU fast without blocking real requests long."""
    data = request.json or {}
    voice = data.get('voice', DEFAULT_VOICE).replace('.wav', '')
    wav_path = os.path.join(VOICES_DIR, f"{voice}.wav")
    txt_path = os.path.join(VOICES_DIR, f"{voice}.txt")
    if not os.path.exists(wav_path) or not os.path.exists(txt_path):
        voice = DEFAULT_VOICE
        wav_path = os.path.join(VOICES_DIR, f"{voice}.wav")
        txt_path = os.path.join(VOICES_DIR, f"{voice}.txt")
    try:
        with open(txt_path, 'r', encoding='utf-8') as f:
            ref_text = f.read().strip()
        tmp_path = tempfile.mktemp(suffix='.wav')
        # No tts_lock here — warmup must never block real sentence generation
        tts.infer(ref_file=wav_path, ref_text=ref_text, gen_text=", Warmup.",
                  file_wave=tmp_path, speed=1.0, nfe_step=8, cfg_strength=2.0)
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        print(f"🔥 Warmup complete for: {voice}")
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route('/status', methods=['GET'])
def status():
    return jsonify({
        "status": "online",
        "device": device,
        "gpu": torch.cuda.get_device_name(0) if device == "cuda" else "none"
    })

@app.route('/voices', methods=['GET'])
def list_voices():
    """List available voices (any .wav with matching .txt)."""
    voices = []
    for f in os.listdir(VOICES_DIR):
        if f.endswith('.wav'):
            name = f.replace('.wav', '')
            if os.path.exists(os.path.join(VOICES_DIR, f"{name}.txt")):
                voices.append(name)
    return jsonify({"voices": voices})

if __name__ == '__main__':
    print(f"🚀 F5-TTS server starting on port 8003...")
    app.run(host='0.0.0.0', port=8003)
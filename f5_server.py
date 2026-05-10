import sys
import os
import re
import time
import tempfile
from io import BytesIO
import numpy as np
import soundfile as sf
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
            gen_text=". Warmup.",
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
# AUDIO TRIM — strips the generated ". " pad and F5's own silence,
# then prepends a clean 80ms buffer so browser never clips first word
# ----------------------------------------------------------------
def trim_leading_silence(audio, sr, silence_threshold=0.005, buffer_ms=80):
    """Strip leading silence, prepend clean buffer. Returns modified array; non-destructive on failure."""
    try:
        abs_audio = np.abs(audio) if audio.ndim == 1 else np.abs(audio).max(axis=1)
        above = np.where(abs_audio > silence_threshold)[0]
        if len(above) == 0:
            return audio
        # Keep 30ms before first sound to avoid hard cut artefacts
        start = max(0, above[0] - int(sr * 0.03))
        trimmed = audio[start:]
        # Prepend clean silence so browser player has time to initialise
        buffer_samples = int(sr * buffer_ms / 1000)
        silence = np.zeros((buffer_samples,) if audio.ndim == 1 else (buffer_samples, audio.shape[1]))
        return np.concatenate([silence, trimmed])
    except Exception as e:
        print(f"⚠️  Trim failed (non-critical): {e}")
        return audio


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
    r"\bNDA\b":  "N D A",
    r"\bNDAs\b": "N D A's",
    r"\bRTX\b":  "R T X",
    r"\bGTX\b":  "G T X",
}

def expand_acronyms(text):
    for pattern, replacement in ACRONYMS.items():
        text = re.sub(pattern, replacement, text)
    return text

def clean_text(text):
    """Normalise text for clean English TTS output."""
    # Strip markdown links [text](url) → just keep the text
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    # Strip bare URLs (http/https/www)
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'www\.\S+', '', text)

    # Fix smart quotes — keeps apostrophes intact for F5 to read contractions correctly
    text = text.replace('\u2019', "'").replace('\u2018', "'")
    text = text.replace('\u201c', '"').replace('\u201d', '"')

    # Convert ALL-CAPS words to Title Case BEFORE acronym expansion.
    # Words in the ACRONYMS dict are preserved as-is so expand_acronyms handles them.
    # Everything else (shouted words like "EXCITED", "AMAZING") becomes Title Case.
    _known_acronyms = {
        "AI","IS","IT","UK","US","PC","TV","OK","DJ","VR","AR","RP","UI",
        "API","URL","GPU","CPU","RAM","TTS","GPT","NPC","FPS","RPG","DNA",
        "RNA","MRI","IVF","CBD","THC","SSD","HDD","USB","HDR","VPN","SSH",
        "SQL","CSS","IQ","HR","PR","CEO","CFO","CTO","HQ","ETA","RTX","GTX",
        "MOT","TB","GB","MB","KB","HTML","JSON","HTTP","HTTPS","OS","IO",
        "LLM","VRAM","ASAP","FYI","DIY","IRL","OMG","NDA","NDAS","AM","PM",
    }
    text = re.sub(r'\b[A-Z]{2,}\b', lambda m: m.group(0) if m.group(0) in _known_acronyms else m.group(0).title(), text)

    # Expand known acronyms/short caps BEFORE the general caps handler
    text = expand_acronyms(text)

    # Em-dash and en-dash to pause
    text = text.replace('\u2014', '. ')  # em-dash
    text = text.replace('\u2013', '. ')  # en-dash

    # Emoji/symbol handling
    text = text.replace('\U0001F4AF', 'one hundred percent')
    text = text.replace('!', '.')
    # Strip emojis — covers all major Unicode emoji blocks
    # With preceding word char — replace space+emoji with full stop
    text = re.sub(u'(\w)\s*[\U0001F000-\U0001FFFF]+', r'\1.', text)
    text = re.sub(u'(\w)\s*[\U0001F300-\U0001FAFF]+', r'\1.', text)
    text = re.sub(u'(\w)\s*[\U0001F900-\U0001F9FF]+', r'\1.', text)
    text = re.sub(u'(\w)\s*[\U0001FA00-\U0001FA6F]+', r'\1.', text)
    text = re.sub(u'(\w)\s*[\U0001FA70-\U0001FAFF]+', r'\1.', text)
    text = re.sub(u'(\w)\s*[\u2600-\u27BF]+', r'\1.', text)
    text = re.sub(u'(\w)\s*[\u2700-\u27BF]+', r'\1.', text)
    # Lone emojis (not preceded by word char) — strip entirely
    text = re.sub(u'[\U0001F000-\U0001FFFF]+', '', text)
    text = re.sub(u'[\U0001F300-\U0001FAFF]+', '', text)
    text = re.sub(u'[\U0001F900-\U0001F9FF]+', '', text)
    text = re.sub(u'[\U0001FA00-\U0001FA6F]+', '', text)
    text = re.sub(u'[\U0001FA70-\U0001FAFF]+', '', text)
    text = re.sub(u'[\u2600-\u27BF]+', '', text)
    text = re.sub(u'[\u2700-\u27BF]+', '', text)
    text = re.sub(r'[\u003A\uFE13\uFE30\uFE55\uFF1A]', '. ', text)
    # (lone-digit strip removed — was stripping numbers incorrectly)
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
    text = re.sub(r'\bGPT-40\b', 'GPT-4o', text, flags=re.IGNORECASE)
    text = re.sub(r'\bGPT-4o\b', 'G P T four oh', text, flags=re.IGNORECASE)
    text = re.sub(r'\bGPT-4\b', 'G P T four', text, flags=re.IGNORECASE)
    text = re.sub(r'\s*=\s*', ' equals ', text)
    # Parentheses — replace with full stop pause (commas cause F5 hesitation/ums)
    text = re.sub(r'\s*\(\s*', '. ', text)
    text = re.sub(r'\s*\)\s*', '. ', text)
    text = re.sub(r'\bbreather\b', 'breether', text)
    text = re.sub(r'\bX\b', 'ex', text)  # X → "ex"
    text = re.sub(r'\bID\b', 'I D', text)  # ID → "I D"
    text = re.sub(r'\bGemini\b', 'Jemineye', text, flags=re.IGNORECASE)
    text = re.sub(r'(\w)\+', r'\1 plus', text)  # word+ → "word plus" e.g. "lewd+" → "lewd plus"
    text = re.sub(r'\blive\b', 'lyve', text, flags=re.IGNORECASE)
    text = re.sub(r'\blives\b', 'lyves', text, flags=re.IGNORECASE)
    text = re.sub(r'\blively\b', 'lyvely', text, flags=re.IGNORECASE)
    text = re.sub(r'\bliven\b', 'lyven', text, flags=re.IGNORECASE)
    text = re.sub(r'\bread\b', 'reed', text, flags=re.IGNORECASE)
    text = re.sub(r'\bvs\.?\b', 'versus', text, flags=re.IGNORECASE)
    text = re.sub(r'\bx+\b', '', text, flags=re.IGNORECASE)  # strip kiss x's (xx, xxx etc)
    text = re.sub(r'\bGTAVI\b', 'G T A 6', text, flags=re.IGNORECASE)
    text = re.sub(r'\bGTAV\b', 'G T A 5', text, flags=re.IGNORECASE)
    text = re.sub(r'\bGTAIV\b', 'G T A 4', text, flags=re.IGNORECASE)
    text = re.sub(r'\bGTAIII\b', 'G T A 3', text, flags=re.IGNORECASE)
    text = re.sub(r'\bGTAII\b', 'G T A 2', text, flags=re.IGNORECASE)
    text = re.sub(r'\bGTA\b', 'G T A', text)
    # Handle mph with comma-formatted numbers e.g. "18,000 mph" or "18,000mph"
    text = re.sub(r'([\d,]+)\s*mph\b', lambda m: m.group(1).replace(',', '') + ' miles per hour', text, flags=re.IGNORECASE)

    text = text.strip()

    # Collapse any multiple consecutive dots or spaces created by replacements
    text = re.sub(r'\.(\s*\.)+', '.', text)   # .. or . . → single .
    text = re.sub(r'\s{2,}', ' ', text)        # multiple spaces → single space
    text = re.sub(r'\.\s*,', '.', text)        # ., → .
    text = re.sub(r',\s*,+', ',', text)        # ,, → ,

    return text

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

    try:
        # First chunk of a response uses fewer diffusion steps for faster first-byte latency.
        # Subsequent chunks use full quality. Difference at nfe_step=20 vs 24 is barely
        # perceptible on a single sentence; saves ~1-1.5s on the opening wait.
        first_chunk = data.get('first_chunk', False)
        nfe = 20 if first_chunk else 24

        t_start = time.time()
        with tts_lock:
            wav, sr, _ = tts.infer(
                ref_file=wav_path,
                ref_text=ref_text,
                gen_text=text,
                speed=1.0,
                nfe_step=nfe,
                cfg_strength=2.0
            )
        elapsed = time.time() - t_start
        print(f"✅ Generated in {elapsed:.2f}s | Voice: {voice} | nfe_step: {nfe}{'  [first chunk]' if first_chunk else ''}")
        wav = trim_leading_silence(wav, sr)
        buf = BytesIO()
        sf.write(buf, wav, sr, format='WAV')
        buf.seek(0)
        return send_file(buf, mimetype='audio/wav')

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

        # Try to acquire tts_lock non-blocking — if a real request is already running,
        # skip warmup entirely. F5 internals are not thread-safe; concurrent inferences
        # cause tensor size mismatch crashes that break all subsequent requests.
        acquired = tts_lock.acquire(blocking=False)
        if not acquired:
            print(f"⚠️  Warmup skipped — real inference in progress")
            return jsonify({"status": "skipped", "reason": "inference in progress"})
        try:
            tts.infer(ref_file=wav_path, ref_text=ref_text, gen_text=". Warmup.",
                      file_wave=tmp_path, speed=1.0, nfe_step=8, cfg_strength=1.0)
        finally:
            tts_lock.release()

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
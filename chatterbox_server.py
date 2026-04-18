"""
chatterbox_server.py — Chatterbox TTS server for HWUI
Uses Chatterbox TURBO by default — 1-step diffusion decoder vs 10-step in standard.
Dramatically faster per-sentence generation, same voice cloning quality.
Runs on port 8004.

Install:
    pip install chatterbox-tts

Voices directory: same as F5 — a folder of .wav reference clips.
Each voice needs: VoiceName.wav  (no .txt required)

Usage:
    python chatterbox_server.py
"""

import os
import io
import sys
import logging
import threading
import re
import torch

from flask import Flask, request, jsonify, send_file

# ─── TEXT CLEANING ─────────────────────────────────────────────────────────────

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
    r"\bDNA\b":  "D N A",
    r"\bRNA\b":  "R N A",
    r"\bMRI\b":  "M R I",
    r"\bIVF\b":  "I V F",
    r"\bCBD\b":  "C B D",
    r"\bTHC\b":  "T H C",
    r"\bSSD\b":  "S S D",
    r"\bHDD\b":  "H D D",
    r"\bUSB\b":  "U S B",
    r"\bHDR\b":  "H D R",
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
    """Normalise text for clean English TTS output — ported from f5_server.py."""
    # Strip markdown links [text](url) → just keep the text
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    # Strip bare URLs
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'www\.\S+', '', text)

    text = text.replace('\u2019', "'").replace('\u2018', "'")
    text = text.replace('\u201c', '"').replace('\u201d', '"')

    _known_acronyms = {
        "AI","IS","IT","UK","US","PC","TV","OK","DJ","VR","AR","RP","UI",
        "API","URL","GPU","CPU","RAM","TTS","GPT","NPC","FPS","RPG","DNA",
        "RNA","MRI","IVF","CBD","THC","SSD","HDD","USB","HDR","VPN","SSH",
        "SQL","CSS","IQ","HR","PR","CEO","CFO","CTO","HQ","ETA","RTX","GTX",
        "MOT","TB","GB","MB","KB","HTML","JSON","HTTP","HTTPS","OS","IO",
        "LLM","VRAM","ASAP","FYI","DIY","IRL","OMG","NDA","NDAS","AM","PM",
    }
    text = re.sub(r'\b[A-Z]{2,}\b', lambda m: m.group(0) if m.group(0) in _known_acronyms else m.group(0).title(), text)
    text = expand_acronyms(text)
    text = text.replace('\u2014', '. ').replace('\u2013', '. ')
    text = text.replace('\U0001F4AF', 'one hundred percent')
    text = text.replace('!', '.')
    text = re.sub(u'(\w)\s*[\U0001F000-\U0001FFFF]+', r'\1.', text)
    text = re.sub(u'(\w)\s*[\U0001F300-\U0001FAFF]+', r'\1.', text)
    text = re.sub(u'(\w)\s*[\U0001F900-\U0001F9FF]+', r'\1.', text)
    text = re.sub(u'(\w)\s*[\U0001FA00-\U0001FA6F]+', r'\1.', text)
    text = re.sub(u'(\w)\s*[\U0001FA70-\U0001FAFF]+', r'\1.', text)
    text = re.sub(u'(\w)\s*[\u2600-\u27BF]+', r'\1.', text)
    text = re.sub(u'(\w)\s*[\u2700-\u27BF]+', r'\1.', text)
    text = re.sub(u'[\U0001F000-\U0001FFFF]+', '', text)
    text = re.sub(u'[\U0001F300-\U0001FAFF]+', '', text)
    text = re.sub(u'[\U0001F900-\U0001F9FF]+', '', text)
    text = re.sub(u'[\U0001FA00-\U0001FA6F]+', '', text)
    text = re.sub(u'[\U0001FA70-\U0001FAFF]+', '', text)
    text = re.sub(u'[\u2600-\u27BF]+', '', text)
    text = re.sub(u'[\u2700-\u27BF]+', '', text)
    text = re.sub(r'[\u003A\uFE13\uFE30\uFE55\uFF1A]', ', ', text)
    # Strip lone digits left floating after punctuation removal
    text = re.sub(r'(?<!\w)\d(?!\w)', '', text)
    text = re.sub(r'\s-\s', '. ', text)
    text = re.sub(r'(\w)-(\w)', r'\1 \2', text)
    text = re.sub(r'\bI AM\b', 'I am', text)
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
    text = re.sub(r'\s*\(\s*', ', ', text)
    text = re.sub(r'\s*\)\s*', ', ', text)
    text = re.sub(r'\bbreather\b', 'breether', text)
    text = re.sub(r'\bX\b', 'ex', text)
    text = re.sub(r'\bID\b', 'I D', text)
    text = re.sub(r'\bGemini\b', 'Jemineye', text, flags=re.IGNORECASE)
    text = re.sub(r'(\w)\+', r'\1 plus', text)
    text = re.sub(r'\blive\b', 'livv', text, flags=re.IGNORECASE)
    text = re.sub(r'\bread\b', 'reed', text, flags=re.IGNORECASE)
    text = re.sub(r'\bvs\.?\b', 'versus', text, flags=re.IGNORECASE)
    text = re.sub(r'\bx+\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\bGTAVI\b', 'G T A 6', text, flags=re.IGNORECASE)
    text = re.sub(r'\bGTAV\b', 'G T A 5', text, flags=re.IGNORECASE)
    text = re.sub(r'\bGTAIV\b', 'G T A 4', text, flags=re.IGNORECASE)
    text = re.sub(r'\bGTAIII\b', 'G T A 3', text, flags=re.IGNORECASE)
    text = re.sub(r'\bGTAII\b', 'G T A 2', text, flags=re.IGNORECASE)
    text = re.sub(r'\bGTA\b', 'G T A', text)
    text = re.sub(r'([\d,]+)\s*mph\b', lambda m: m.group(1).replace(',', '') + ' miles per hour', text, flags=re.IGNORECASE)
    return text.strip()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Keep model weights off C: drive — same as f5_server.py
os.environ.setdefault('HF_HOME', r'I:\HuggingFace')
os.environ.setdefault('TRANSFORMERS_CACHE', r'I:\HuggingFace')

# --- CONFIG -------------------------------------------------------------------
PORT          = 8004
VOICES_DIR    = r"I:\Chattervoice"
DEFAULT_VOICE = "Sol"

# For standard model fallback only
EXAGGERATION = 0.5
CFG_STRENGTH = 0.5

# Turbo generation params — tweak to taste
# temperature: lower = faster + more consistent (0.5-0.7), higher = more expressive (0.8-1.2)
# top_k: lower = faster sampling (50-100), higher = more varied (500-1000)
# repetition_penalty: prevents phoneme repetition — 1.2 is a good default
# cfg_weight: how closely to follow reference voice — 0.3-0.5 sounds most natural
TEMPERATURE        = 0.7
TOP_K              = 100
REPETITION_PENALTY = 1.2
CFG_WEIGHT         = 0.5

# --- MODEL LOAD ---------------------------------------------------------------
app = Flask(__name__)

_model      = None
_model_lock = threading.Lock()
_use_turbo  = False


def get_model():
    global _model, _use_turbo
    if _model is None:
        with _model_lock:
            if _model is None:
                device = "cuda" if torch.cuda.is_available() else "cpu"
                logging.info(f"Device: {device}")

                # Try Turbo first — 1-step decoder, much faster per sentence
                try:
                    from chatterbox.tts_turbo import ChatterboxTurboTTS
                    logging.info("Loading Chatterbox TURBO model...")
                    _model = ChatterboxTurboTTS.from_pretrained(device=device)
                    _use_turbo = True
                    logging.info("Chatterbox TURBO loaded — fast inference active.")
                except (ImportError, Exception) as e:
                    logging.warning(f"Turbo unavailable ({e}) — falling back to standard Chatterbox.")
                    from chatterbox.tts import ChatterboxTTS
                    logging.info("Loading Chatterbox standard model...")
                    _model = ChatterboxTTS.from_pretrained(device=device)
                    _use_turbo = False
                    logging.info("Chatterbox standard loaded.")
    return _model


def _generate(model, text, wav_path=None):
    """Generate audio — handles Turbo and standard model differences."""
    if _use_turbo:
        if wav_path:
            return model.generate(text, audio_prompt_path=wav_path,
                                  temperature=TEMPERATURE, top_k=TOP_K,
                                  repetition_penalty=REPETITION_PENALTY,
                                  cfg_weight=CFG_WEIGHT)
        else:
            return model.generate(text,
                                  temperature=TEMPERATURE, top_k=TOP_K,
                                  repetition_penalty=REPETITION_PENALTY,
                                  cfg_weight=CFG_WEIGHT)
    else:
        if wav_path:
            return model.generate(text, audio_prompt_path=wav_path,
                                  exaggeration=EXAGGERATION, cfg_weight=CFG_STRENGTH)
        else:
            return model.generate(text, exaggeration=EXAGGERATION, cfg_weight=CFG_STRENGTH)


def _wav_to_bytes(wav, sample_rate):
    """Convert audio tensor to WAV bytes using soundfile (avoids torchcodec)."""
    import soundfile as sf
    buf = io.BytesIO()
    audio_np = wav.squeeze().cpu().numpy()
    sf.write(buf, audio_np, sample_rate, format='WAV', subtype='PCM_16')
    buf.seek(0)
    return buf


# --- HELPERS ------------------------------------------------------------------

def find_voice_wav(voice_name):
    path = os.path.join(VOICES_DIR, f"{voice_name}.wav")
    return path if os.path.exists(path) else None


def list_all_voices():
    try:
        return sorted([
            f.replace(".wav", "")
            for f in os.listdir(VOICES_DIR)
            if f.lower().endswith(".wav")
        ])
    except Exception:
        return []


# --- ROUTES -------------------------------------------------------------------

@app.route("/status", methods=["GET"])
def status():
    device   = "cuda" if torch.cuda.is_available() else "cpu"
    gpu_name = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu"
    return jsonify({
        "status":       "online",
        "engine":       "chatterbox-turbo" if _use_turbo else "chatterbox",
        "device":       device,
        "gpu":          gpu_name,
        "voices_dir":   VOICES_DIR,
        "model_loaded": _model is not None,
    })


@app.route("/voices", methods=["GET"])
def voices():
    return jsonify({"voices": list_all_voices()})


@app.route("/warmup", methods=["POST"])
def warmup():
    try:
        data       = request.json or {}
        voice_name = data.get("voice", DEFAULT_VOICE)
        logging.info(f"Warmup requested (voice: {voice_name})")
        model    = get_model()
        wav_path = find_voice_wav(voice_name)
        _generate(model, "Hello.", wav_path)
        torch.cuda.empty_cache()
        logging.info("Warmup complete.")
        return jsonify({"status": "ok"})
    except Exception as e:
        logging.error(f"Warmup error: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/tts_to_audio", methods=["POST"])
def tts_to_audio():
    try:
        data       = request.json or {}
        text       = clean_text(data.get("text", ""))
        voice_name = data.get("voice", DEFAULT_VOICE)

        if not text:
            return jsonify({"error": "No text provided"}), 400

        model_label = "TURBO" if _use_turbo else "standard"
        logging.info(f"[{model_label}] voice={voice_name} | {text[:60]}...")

        model    = get_model()
        wav_path = find_voice_wav(voice_name)

        if not wav_path:
            logging.warning(f"Voice '{voice_name}' not found — using default voice.")

        wav = _generate(model, text, wav_path)
        buf = _wav_to_bytes(wav, model.sr)

        # Release VRAM immediately after generation
        torch.cuda.empty_cache()

        logging.info("Audio generated OK.")
        return send_file(buf, mimetype="audio/wav", as_attachment=False, download_name="output.wav")

    except Exception as e:
        logging.error(f"TTS generation error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# --- STARTUP ------------------------------------------------------------------

if __name__ == "__main__":
    logging.info(f"Starting Chatterbox TTS server on port {PORT}...")
    logging.info(f"Voices directory: {VOICES_DIR}")

    try:
        get_model()
    except Exception as e:
        logging.error(f"Failed to load model on startup: {e}")
        sys.exit(1)

    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)

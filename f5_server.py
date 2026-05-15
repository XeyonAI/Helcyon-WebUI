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
    # Internet slang / messaging
    r"\bLOL\b":   "L O L",
    r"\bLMAO\b":  "L M A O",
    r"(?i)\bWTF\b":  "W T F",
    r"\bSMH\b":   "S M H",
    r"\bTBH\b":   "to be honest",
    r"\bIIRC\b":  "I I R C",
    r"\bIMO\b":   "in my opinion",
    r"\bIMHO\b":  "in my humble opinion",
    r"\bTLDR\b":  "T L D R",
    r"\bTL;DR\b": "T L D R",
    r"\bIDK\b":   "I D K",
    r"\bIDC\b":   "I D C",
    r"\bBTW\b":   "by the way",
    r"\bFWIW\b":  "F W I W",
    r"\bAFAIK\b": "A F A I K",
    r"\bAFK\b":   "A F K",
    r"\bGG\b":    "G G",
    r"\bTGIF\b":  "T G I F",
    r"\bRSVP\b":  "R S V P",
    r"\bNSFW\b":  "N S F W",
    r"\bSFW\b":   "S F W",
    r"\bDM\b":    "D M",
    r"\bDMs\b":   "D M's",
    # Business / professional
    r"\bCOO\b":   "C O O",
    r"\bCMO\b":   "C M O",
    r"\bCPO\b":   "C P O",
    r"\bCIO\b":   "C I O",
    r"\bVP\b":    "V P",
    r"\bKPI\b":   "K P I",
    r"\bKPIs\b":  "K P I's",
    r"\bROI\b":   "R O I",
    r"\bSLA\b":   "S L A",
    r"\bOKR\b":   "O K R",
    r"\bOKRs\b":  "O K R's",
    r"\bNPS\b":   "N P S",
    r"\bMRR\b":   "M R R",
    r"\bARR\b":   "A R R",
    r"\bB2B\b":   "B to B",
    r"\bB2C\b":   "B to C",
    r"\bGTM\b":   "G T M",
    r"\bUSD\b":   "U S D",
    r"\bGBP\b":   "G B P",
    r"\bIPO\b":   "I P O",
    r"\bETF\b":   "E T F",
    r"\bVAT\b":   "V A T",
    r"\bSEO\b":   "S E O",
    r"\bCRM\b":   "C R M",
    r"\bERP\b":   "E R P",
    r"\bMVP\b":   "M V P",
    r"\bQA\b":    "Q A",
    r"\bUAT\b":   "U A T",
    r"\bPOC\b":   "P O C",
    # Tech / software / hardware / networking
    r"\bSDK\b":   "S D K",
    r"\bCLI\b":   "C L I",
    r"\bIDE\b":   "I D E",
    r"\bUX\b":    "U X",
    r"\bDB\b":    "D B",
    r"\bML\b":    "M L",
    r"\bNLP\b":   "N L P",
    r"\bAGI\b":   "A G I",
    r"\bSaaS\b":  "S A A S",
    r"\bIoT\b":   "I O T",
    r"\bDNS\b":   "D N S",
    r"\bCDN\b":   "C D N",
    r"\bTLS\b":   "T L S",
    r"\bSSL\b":   "S S L",
    r"\bTCP\b":   "T C P",
    r"\bUDP\b":   "U D P",
    r"\bISP\b":   "I S P",
    r"\bISPs\b":  "I S P's",
    r"\bGHz\b":   "gigahertz",
    r"\bMHz\b":   "megahertz",
    r"\bkHz\b":   "kilohertz",
    r"\bHz\b":    "hertz",
    r"\bMbps\b":  "megabits per second",
    r"\bGbps\b":  "gigabits per second",
    r"\bkbps\b":  "kilobits per second",
    r"\bHDMI\b":  "H D M I",
    r"\bNVMe\b":  "N V me",
    r"\bOCR\b":   "O C R",
    r"\bIPv4\b":  "I P version 4",
    r"\bIPv6\b":  "I P version 6",
    r"\bVoIP\b":  "V O I P",
    r"\bNFT\b":   "N F T",
    r"\bNFTs\b":  "N F T's",
    r"\bBTC\b":   "B T C",
    r"\bETH\b":   "E T H",
    r"\bAWS\b":   "A W S",
    r"\bGCP\b":   "G C P",
    r"CI/CD":     "C I C D",
    r"\bOOP\b":   "O O P",
    r"\bRPM\b":   "R P M",
    r"\bUV\b":    "U V",
    r"\bIR\b":    "I R",
    # Chemical / scientific units (mixed-case — not caught by all-caps handler)
    r"\bCO2\b":   "C O 2",
    r"\bH2O\b":   "H 2 O",
    r"\bppm\b":   "parts per million",
    # Media / broadcasting / sport / gaming
    r"\bBBC\b":   "B B C",
    r"\bITV\b":   "I T V",
    r"\bCNN\b":   "C N N",
    r"\bNBC\b":   "N B C",
    r"\bABC\b":   "A B C",
    r"\bCBS\b":   "C B S",
    r"\bHBO\b":   "H B O",
    r"\bESPN\b":  "E S P N",
    r"\bNFL\b":   "N F L",
    r"\bNBA\b":   "N B A",
    r"\bMLB\b":   "M L B",
    r"\bNHL\b":   "N H L",
    r"\bMLS\b":   "M L S",
    r"\bUFC\b":   "U F C",
    r"\bWWE\b":   "W W E",
    r"\bDLC\b":   "D L C",
    r"\bMMO\b":   "M M O",
    r"\bRTS\b":   "R T S",
    r"\bPvP\b":   "P V P",
    r"\bPvE\b":   "P V E",
    r"\bDPS\b":   "D P S",
    r"\bXP\b":    "X P",
    r"\bHP\b":    "H P",
    r"\bMP\b":    "M P",
    r"\bEP\b":    "E P",
    r"\bLP\b":    "L P",
    r"\bMC\b":    "M C",
    r"\bPS5\b":   "P S 5",
    r"\bPS4\b":   "P S 4",
    r"\bPS3\b":   "P S 3",
    # Government / agencies / UK
    r"\bEU\b":    "E U",
    r"\bUN\b":    "U N",
    r"\bFBI\b":   "F B I",
    r"\bCIA\b":   "C I A",
    r"\bNSA\b":   "N S A",
    r"\bMI5\b":   "M I 5",
    r"\bMI6\b":   "M I 6",
    r"\bCCTV\b":  "C C T V",
    r"\bNATO\b":  "nay-to",
    r"\bNHS\b":   "N H S",
    r"\bGCHQ\b":  "G C H Q",
    r"\bHMRC\b":  "H M R C",
    r"\bDVLA\b":  "D V L A",
    # Health / medical
    r"\bBMI\b":   "B M I",
    r"\bBPM\b":   "B P M",
    r"\bECG\b":   "E C G",
    r"\bEKG\b":   "E K G",
    r"\bICU\b":   "I C U",
    r"\bGP\b":    "G P",
    r"\bADHD\b":  "A D H D",
    r"\bOCD\b":   "O C D",
    r"\bPTSD\b":  "P T S D",
    r"\bCPTSD\b": "C P T S D",
    r"\bBPD\b":   "B P D",
    r"\bASD\b":   "A S D",
    r"A&E":       "A and E",
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
        # Core / original
        "AI","IS","IT","UK","US","PC","TV","OK","DJ","VR","AR","RP","UI",
        "API","URL","GPU","CPU","RAM","TTS","GPT","NPC","FPS","RPG","DNA",
        "RNA","MRI","IVF","CBD","THC","SSD","HDD","USB","HDR","VPN","SSH",
        "SQL","CSS","IQ","HR","PR","CEO","CFO","CTO","HQ","ETA","RTX","GTX",
        "MOT","TB","GB","MB","KB","HTML","JSON","HTTP","HTTPS","OS","IO",
        "LLM","VRAM","ASAP","FYI","DIY","IRL","OMG","NDA","NDAS","AM","PM",
        # Internet slang
        "LOL","LMAO","WTF","SMH","TBH","IIRC","IMO","IMHO","TLDR",
        "IDK","IDC","BTW","FWIW","AFAIK","AFK","GG","TGIF","RSVP","NSFW","SFW","DM",
        # Business / professional
        "COO","CMO","CPO","CIO","VP","KPI","ROI","SLA","OKR","NPS","MRR","ARR",
        "GTM","USD","GBP","IPO","ETF","VAT","SEO","CRM","ERP","MVP","QA","UAT","POC",
        # Tech / software / hardware / networking
        "SDK","CLI","IDE","UX","DB","ML","NLP","AGI","DNS","CDN","TLS","SSL",
        "TCP","UDP","ISP","HDMI","OCR","NFT","BTC","ETH","AWS","GCP","OOP","RPM","UV","IR",
        # Media / broadcasting / sport / gaming
        "BBC","ITV","CNN","NBC","ABC","CBS","HBO","ESPN",
        "NFL","NBA","MLB","NHL","MLS","UFC","WWE",
        "DLC","MMO","RTS","DPS","XP","HP","MP","EP","LP","MC",
        # Government / agencies / UK
        "EU","UN","FBI","CIA","NSA","CCTV","NATO","NHS","GCHQ","HMRC","DVLA",
        # Health / medical
        "BMI","BPM","ECG","EKG","ICU","GP","ADHD","OCD","PTSD","CPTSD","BPD","ASD",
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
    # Parentheses — drop the brackets and let the aside flow inline. A full
    # stop here turned every parenthetical into its own falling-intonation
    # fragment (a "pause in the wrong place"); a comma makes F5 hesitate/um.
    # A plain space reads the aside naturally as part of the sentence.
    text = re.sub(r'\s*\(\s*', ' ', text)
    text = re.sub(r'\s*\)\s*', ' ', text)
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
    # GTA — single unified pattern covers GTA, GTA5, GTA 5, GTAV, GTA V, GTA VI, GTA 6 etc.
    _GTA_ROMAN = {'I':'1','II':'2','III':'3','IV':'4','V':'5','VI':'6'}
    def _gta_rep(m, _r=_GTA_ROMAN):
        s = (m.group(1) or '').strip().upper()
        num = _r.get(s, s if s.isdigit() else '')
        return (f'jee tee ay {num} ' if num else 'jee tee ay ')
    text = re.sub(r'\bGTA\s*(?:(VI|V|IV|III|II|I|[1-6])(?=\b|\s|$))?', _gta_rep, text, flags=re.IGNORECASE)
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
        # nfe_step = diffusion steps. It is the main speed knob — generation
        # time scales with it. F5 default is 32; we run lower. Quality cost of
        # 20 vs 24 is barely perceptible (and 16 on the opening word, which is
        # latency-critical, is fine). Raise these if audio quality suffers;
        # lower them for more speed.
        #   first chunk : 16  — fastest first-byte latency
        #   later chunks: 20  — ~17% faster than the old 24
        first_chunk = data.get('first_chunk', False)
        nfe = 16 if first_chunk else 20

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
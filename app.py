from flask import Flask, request, jsonify, send_from_directory, render_template, Response
from flask_cors import CORS
import requests, os, json, re, hashlib, time, subprocess
import psutil
from datetime import datetime, timedelta
from truncation import trim_chat_history
from tts_routes import tts_bp
from utils.session_handler import get_system_prompt, get_instruction_layer, get_tone_primer
from whisper_routes import whisper_bp

print(f"💡 Flask is using: {os.path.abspath(__file__)}")

# --------------------------------------------
# Chat history trimming (simple message window)
# --------------------------------------------
MAX_MESSAGES = 20

def trim_chat_window(messages):
    """Keep only the last N messages to prevent context overflow."""
    print("🪟 Using trim_chat_window", flush=True)
    return messages[-MAX_MESSAGES:]


# --------------------------------------------------
# Initialize Flask
# --------------------------------------------------
app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)

# Add CSP headers for TTS audio playback
@app.after_request
def add_security_headers(response):
    response.headers['Content-Security-Policy'] = "default-src 'self' 'unsafe-inline' 'unsafe-eval'; media-src 'self' blob:; connect-src 'self'; img-src 'self' data: blob:"
    return response


# --------------------------------------------------
# Serve style.css from root directory
# --------------------------------------------------
@app.route('/style.css')
def serve_style():
    return send_from_directory(os.path.dirname(__file__), 'style.css')

# Serve theme files from themes/ folder
# --------------------------------------------------
@app.route('/theme-files/<path:filename>')
def serve_theme_file(filename):
    themes_dir = os.path.join(os.path.dirname(__file__), 'themes')
    return send_from_directory(themes_dir, filename)

# --------------------------------------------------
# Serve files from root /utils folder
# --------------------------------------------------
@app.route('/utils/<path:filename>')
def serve_utils(filename):
    utils_dir = os.path.join(os.path.dirname(__file__), 'utils')
    return send_from_directory(utils_dir, filename)

# --------------------------------------------------
# Register extra routes
# --------------------------------------------------
from extra_routes import extra
from chat_routes import chat_bp
from project_routes import project_bp
app.register_blueprint(extra)
app.register_blueprint(chat_bp)
app.register_blueprint(project_bp)
app.register_blueprint(tts_bp, url_prefix='/api/tts')
app.register_blueprint(whisper_bp)

# --------------------------------------------------
# Load Documents
# --------------------------------------------------
def load_project_documents(project_name, user_query=""):
    """Load the single best-matching document from a project's documents folder.
    Uses keyword matching against filenames to find the most relevant doc.
    If no query provided or no match found, returns empty string."""
    if not project_name:
        return ""
    
    projects_dir = os.path.join(os.path.dirname(__file__), "projects")
    docs_dir = os.path.join(projects_dir, project_name, "documents")
    
    if not os.path.exists(docs_dir):
        return ""

    MAX_CHARS_PER_DOC = 8000    # ~2k tokens per document

    # ── Keyword extraction ───────────────────────────────────────
    # Expanded stopwords: includes action verbs so 'read', 'open' etc.
    # don't accidentally match filenames like 'readme.txt'
    stopwords = {
        'the', 'a', 'an', 'in', 'on', 'at', 'to', 'for', 'of', 'and', 'or',
        'document', 'file', 'pdf', 'scan', 'check', 'look', 'show', 'search',
        'read', 'open', 'load', 'get', 'fetch', 'find', 'use', 'can', 'you',
        'please', 'me', 'my', 'what', 'does', 'say', 'tell', 'about', 'from',
        'this', 'that', 'there', 'here', 'its', 'with', 'have', 'has', 'see',
        'according', 'reference', 'view', 'know', 'give', 'show', 'write',
    }
    query_keywords = []
    if user_query:
        words = user_query.lower().replace('_', ' ').replace('-', ' ').split()
        query_keywords = [w for w in words if w not in stopwords and len(w) > 2]

    if not query_keywords:
        print(f"⭕ No usable keywords from query — skipping document load")
        return ""

    # ── Find best-matching file ──────────────────────────────────
    all_files = [f for f in os.listdir(docs_dir) if os.path.isfile(os.path.join(docs_dir, f))]
    
    best_file = None
    best_score = 0
    for fname in all_files:
        fname_norm = fname.lower().replace('_', ' ').replace('-', ' ').replace('.', ' ')
        score = sum(1 for kw in query_keywords if kw in fname_norm)
        if score > best_score:
            best_score = score
            best_file = fname

    if not best_file or best_score == 0:
        print(f"⏭️ No document matched keywords: {query_keywords}")
        return ""

    print(f"✅ Best match: '{best_file}' (score={best_score}, keywords={query_keywords})")

    # ── Read the file ────────────────────────────────────────────
    filepath = os.path.join(docs_dir, best_file)
    content = None

    try:
        if best_file.endswith(('.txt', '.md')):
            # Try UTF-8 with BOM handling first, fall back to latin-1
            try:
                with open(filepath, 'r', encoding='utf-8-sig') as f:
                    content = f.read()
            except UnicodeDecodeError:
                with open(filepath, 'r', encoding='latin-1') as f:
                    content = f.read()
                print(f"⚠️ {best_file} read with latin-1 fallback (non-UTF8 encoding)")

        elif best_file.endswith('.docx'):
            try:
                import docx
                doc = docx.Document(filepath)
                content = "\n".join([para.text for para in doc.paragraphs])
            except ImportError:
                content = "[DOCX content - python-docx required to read]"

        elif best_file.endswith('.odt'):
            try:
                from odf import text as odf_text, teletype
                from odf.opendocument import load as odf_load
                doc = odf_load(filepath)
                allparas = doc.getElementsByType(odf_text.P)
                content = "\n".join([teletype.extractText(para) for para in allparas])
            except ImportError:
                content = "[ODT content - odfpy required to read]"

        elif best_file.endswith('.pdf'):
            try:
                import PyPDF2
                with open(filepath, 'rb') as f:
                    pdf_reader = PyPDF2.PdfReader(f)
                    content = "".join(page.extract_text() for page in pdf_reader.pages)
            except ImportError:
                content = "[PDF content - PyPDF2 required to read]"
            except Exception as e:
                print(f"⚠️ Failed to read PDF {best_file}: {e}")

    except Exception as e:
        print(f"⚠️ Failed to read document {best_file}: {e}")

    if content is None:
        return ""

    # Cap content length
    original_len = len(content)
    if original_len > MAX_CHARS_PER_DOC:
        content = content[:MAX_CHARS_PER_DOC]
        print(f"✂️ Trimmed {best_file}: {original_len} → {MAX_CHARS_PER_DOC} chars")
    else:
        print(f"📄 Loaded {best_file}: {original_len} chars (~{original_len//4} tokens)")

    return (
        "\n\n"
        "═══════════════════════════════════════════════════════════\n"
        "PROJECT DOCUMENTS\n"
        "═══════════════════════════════════════════════════════════\n\n"
        f"### Document: {best_file}\n\n{content}\n\n"
        "═══════════════════════════════════════════════════════════\n"
        "END PROJECT DOCUMENTS\n"
        "═══════════════════════════════════════════════════════════\n\n"
    )

# --------------------------------------------------
# Load Global Documents (always available, no project required)
# --------------------------------------------------
def load_global_documents(user_query=""):
    """Load the single best-matching document from the global_documents folder.
    Keyword-matched against filenames. Injected regardless of active project.
    Drop any .txt/.md/.pdf/.docx file into global_documents/ to add it to the pool."""
    global_docs_dir = os.path.join(os.path.dirname(__file__), "global_documents")

    if not os.path.exists(global_docs_dir):
        return ""

    all_files = [f for f in os.listdir(global_docs_dir) if os.path.isfile(os.path.join(global_docs_dir, f))]
    if not all_files:
        return ""

    MAX_CHARS_PER_DOC = 12000  # ~3k tokens — richer than project docs, global library warrants it

    stopwords = {
        'the', 'a', 'an', 'in', 'on', 'at', 'to', 'for', 'of', 'and', 'or',
        'document', 'file', 'pdf', 'scan', 'check', 'look', 'show', 'search',
        'read', 'open', 'load', 'get', 'fetch', 'find', 'use', 'can', 'you',
        'please', 'me', 'my', 'what', 'does', 'say', 'tell', 'about', 'from',
        'this', 'that', 'there', 'here', 'its', 'with', 'have', 'has', 'see',
        'according', 'reference', 'view', 'know', 'give', 'show', 'write',
    }
    query_keywords = []
    if user_query:
        words = user_query.lower().replace('_', ' ').replace('-', ' ').split()
        query_keywords = [w for w in words if w not in stopwords and len(w) > 2]

    if not query_keywords:
        return ""

    best_file = None
    best_score = 0
    for fname in all_files:
        fname_norm = fname.lower().replace('_', ' ').replace('-', ' ').replace('.', ' ')
        score = sum(1 for kw in query_keywords if kw in fname_norm)
        if score > best_score:
            best_score = score
            best_file = fname

    if not best_file or best_score == 0:
        print(f"⭕ Global docs: no filename match for keywords: {query_keywords}")
        return ""

    print(f"🌐 Global doc match: '{best_file}' (score={best_score})")

    filepath = os.path.join(global_docs_dir, best_file)
    content = None

    try:
        if best_file.endswith(('.txt', '.md')):
            try:
                with open(filepath, 'r', encoding='utf-8-sig') as f:
                    content = f.read()
            except UnicodeDecodeError:
                with open(filepath, 'r', encoding='latin-1') as f:
                    content = f.read()

        elif best_file.endswith('.docx'):
            try:
                import docx
                doc = docx.Document(filepath)
                content = "\n".join([para.text for para in doc.paragraphs])
            except ImportError:
                content = "[DOCX content - python-docx required to read]"

        elif best_file.endswith('.odt'):
            try:
                from odf import text as odf_text, teletype
                from odf.opendocument import load as odf_load
                doc = odf_load(filepath)
                allparas = doc.getElementsByType(odf_text.P)
                content = "\n".join([teletype.extractText(para) for para in allparas])
            except ImportError:
                content = "[ODT content - odfpy required to read]"

        elif best_file.endswith('.pdf'):
            try:
                import PyPDF2
                with open(filepath, 'rb') as f:
                    pdf_reader = PyPDF2.PdfReader(f)
                    content = "".join(page.extract_text() for page in pdf_reader.pages)
            except ImportError:
                content = "[PDF content - PyPDF2 required to read]"
            except Exception as e:
                print(f"⚠️ Global doc PDF read failed {best_file}: {e}")

    except Exception as e:
        print(f"⚠️ Failed to read global doc {best_file}: {e}")

    if content is None:
        return ""

    original_len = len(content)
    if original_len > MAX_CHARS_PER_DOC:
        content = content[:MAX_CHARS_PER_DOC]
        print(f"✂️ Trimmed global doc {best_file}: {original_len} → {MAX_CHARS_PER_DOC} chars")
    else:
        print(f"📄 Global doc loaded: {best_file} ({original_len} chars)")

    return (
        "\n\n"
        "═══════════════════════════════════════════════════════════\n"
        "GLOBAL REFERENCE DOCUMENTS\n"
        "═══════════════════════════════════════════════════════════\n\n"
        f"### Document: {best_file}\n\n{content}\n\n"
        "═══════════════════════════════════════════════════════════\n"
        "END GLOBAL REFERENCE DOCUMENTS\n"
        "═══════════════════════════════════════════════════════════\n\n"
    )

# --------------------------------------------------
# Load persisted chat history (if it exists)
# --------------------------------------------------
chat_history_path = "chat_history.json"

if os.path.exists(chat_history_path):
    try:
        with open(chat_history_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Handle both formats gracefully
            if isinstance(data, list):
                # Convert old-style list into unified message objects
                active_chat = []
                for item in data:
                    if "user" in item and "model" in item:
                        active_chat.append({"role": "user", "content": item["user"]})
                        active_chat.append({"role": "assistant", "content": item["model"]})
                print(f"💾 Converted legacy chat_history.json ({len(active_chat)} messages)")
            elif isinstance(data, dict):
                active_chat = data.get("active_chat", [])
            else:
                active_chat = []
    except Exception as e:
        print(f"⚠️ Failed to load chat history: {e}")
        active_chat = []
else:
    active_chat = []

# --------------------------------------------------
# App configuration and startup info
# --------------------------------------------------
# Auto-create settings.json from default if missing (fresh install / demo machine)
if not os.path.exists('settings.json'):
    if os.path.exists('settings.default.json'):
        import shutil
        shutil.copy('settings.default.json', 'settings.json')
        print("📋 settings.json not found — created from settings.default.json")
    else:
        print("❌ Neither settings.json nor settings.default.json found. Cannot start.")
        raise FileNotFoundError("Missing settings.json and settings.default.json")

# Load server URL from settings — derive from llama_args.port so they can't drift
with open('settings.json', 'r') as f:
    settings = json.load(f)
    _llama_port = settings.get('llama_args', {}).get('port', 8080)
    API_URL = f'http://127.0.0.1:{_llama_port}'
    print(f"🔌 API_URL set to: {API_URL}")
    
CURRENT_MODEL = None

print("--------------------------------------------------")
print("🚀 Helcyon UI Flask Server Starting...")
print("--------------------------------------------------\n")

# --------------------------------------------------
# Detect Current Model
# --------------------------------------------------
def get_current_model():
    global CURRENT_MODEL
    try:
        r = requests.get(f"{API_URL}/v1/models", timeout=5)
        r.raise_for_status()
        data = r.json()
        if data.get("data"):
            CURRENT_MODEL = data["data"][0]["id"]
            print(f"[✅ Model Detected] {CURRENT_MODEL}")
        else:
            CURRENT_MODEL = None
            print("❌ No model loaded.")
    except Exception as e:
        CURRENT_MODEL = None
        print(f"❌ Error: {e}")


def auto_launch_llama():
    """On startup, try to connect to existing llama.cpp — if not running, launch last used model."""
    global llama_process
    get_current_model()
    if CURRENT_MODEL:
        print(f"✅ llama.cpp already running with: {CURRENT_MODEL}")
        return
    try:
        with open('settings.json', 'r') as f:
            s = json.load(f)
        last_model = s.get('llama_last_model')
        exe = s.get('llama_server_exe', '')
        models_dir = s.get('llama_models_dir', '')
        args = s.get('llama_args', {})
        mmproj_path = s.get('mmproj_path', '')
        if not last_model or not exe or not models_dir:
            print("⚠️ No last model or llama config set — skipping auto-launch. Set paths in config page.")
            return
        model_path = os.path.join(models_dir, last_model)
        if not os.path.isfile(model_path):
            print(f"⚠️ Last model not found at {model_path} — skipping auto-launch.")
            return
        if not os.path.isfile(exe):
            print(f"⚠️ llama-server.exe not found at {exe} — skipping auto-launch.")
            return
        print(f"🚀 Auto-launching llama.cpp with: {last_model}")
        cmd = [
            exe, "-m", model_path,
            "--port", str(args.get("port", 8080)),
            "--n-gpu-layers", str(args.get("n_gpu_layers", 44)),
            "--ctx-size", str(args.get("ctx_size", 16384)),
            "--cache-type-k", str(args.get("cache_type_k", "q8_0")),
            "--cache-type-v", str(args.get("cache_type_v", "q8_0")),
            "--timeout", str(args.get("timeout", 0)),
            "--chat-template", str(args.get("chat_template", "chatml")),
            "--parallel", str(args.get("parallel", 1)),
        ]
        if mmproj_path and os.path.isfile(mmproj_path):
            cmd += ["--mmproj", mmproj_path]
            print(f"🖼️ Vision mode: mmproj loaded from {mmproj_path}")
        show_console = s.get('llama_show_console', False)
        llama_process = subprocess.Popen(
            cmd,
            stdout=None if show_console else subprocess.DEVNULL,
            stderr=None if show_console else subprocess.DEVNULL,
            creationflags=(subprocess.CREATE_NEW_CONSOLE if show_console else subprocess.CREATE_NO_WINDOW) if os.name == 'nt' else 0
        )
        print(f"✅ llama.cpp launched (PID {llama_process.pid}) — waiting for ready...")
        for _ in range(30):
            time.sleep(1)
            try:
                r = requests.get(f"{API_URL}/v1/models", timeout=2)
                if r.status_code == 200:
                    get_current_model()
                    print(f"✅ llama.cpp ready: {CURRENT_MODEL}")
                    return
            except Exception:
                pass
        print("⚠️ llama.cpp launched but not responding after 30s")
    except Exception as e:
        print(f"❌ Auto-launch failed: {e}")

auto_launch_llama()

# --------------------------------------------------
# Prompt Builder Helper
# --------------------------------------------------
def build_prompt(user_input, system_prompt, char_context, instruction, tone_primer, use_chatml):
    if use_chatml:
        # Clean llama.cpp build expects ChatML from HWUI
        return (
            f"<|im_start|>system\n{system_prompt}\n\n{char_context}\n\n"
            f"{instruction}\n\n{tone_primer}\n<|im_end|>\n"
            f"<|im_start|>user\n{user_input.strip()}\n<|im_end|>\n"
            f"<|im_start|>assistant\n"
        )
    else:
        # If you ever test with full build that adds its own ChatML
        return (
            f"System:\n{system_prompt}\n\n{char_context}\n\n"
            f"{instruction}\n\n{tone_primer}\n\n"
            f"User: {user_input.strip()}\nAssistant:"
        )

from flask import stream_with_context
import requests, sys



from flask import stream_with_context
import requests, sys


# --------------------------------------------------
# Stream model response
# --------------------------------------------------

# --------------------------------------------------
# Strip any leaked ChatML tags from a chunk
# --------------------------------------------------
def strip_chatml_leakage(text):
    """Remove any leaked or partial ChatML stop tokens from generated text."""
    import re
    text = re.sub(r"<\|im_end\|>", "", text)
    text = re.sub(r"<\|im_start\|>\w*", "", text)
    text = re.sub(r"<\|im_end?$", "", text)
    text = re.sub(r"<\|im_en$", "", text)
    text = re.sub(r"<\|im_e$", "", text)
    text = re.sub(r"<\|im_$", "", text)
    text = re.sub(r"<\|im$", "", text)
    text = re.sub(r"<\|i$", "", text)
    text = re.sub(r"<\|$", "", text)
    return text


def do_web_search(query):
    """DuckDuckGo Instant Answer search + top page fetch."""
    import urllib.parse, urllib.request, re as _re

    out = {"summary": "", "results": [], "top_url": "", "top_text": ""}
    try:
        encoded = urllib.parse.quote_plus(query)
        url = f"https://api.duckduckgo.com/?q={encoded}&format=json&no_html=1&skip_disambig=1"
        req = urllib.request.Request(url, headers={"User-Agent": "HWUI/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            ddg = json.loads(r.read().decode("utf-8"))
        out["summary"] = ddg.get("AbstractText", "").strip()
        out["top_url"] = ddg.get("AbstractURL", "").strip()
        for item in ddg.get("RelatedTopics", [])[:5]:
            if isinstance(item, dict) and item.get("FirstURL"):
                out["results"].append({
                    "title": item.get("Text", "")[:120],
                    "url": item["FirstURL"],
                    "snippet": item.get("Text", "")[:200],
                })
        if not out["top_url"] and out["results"]:
            out["top_url"] = out["results"][0]["url"]
    except Exception as e:
        print(f"⚠️ DDG search error: {e}")

    if out["top_url"]:
        try:
            req2 = urllib.request.Request(
                out["top_url"],
                headers={"User-Agent": "Mozilla/5.0 (compatible; HWUI/1.0)"}
            )
            with urllib.request.urlopen(req2, timeout=8) as r:
                raw = r.read().decode("utf-8", errors="ignore")
            text = _re.sub(r"<[^>]+>", " ", raw)
            text = _re.sub(r"\s+", " ", text).strip()
            out["top_text"] = text[:5000]
        except Exception as e:
            print(f"⚠️ Page fetch error: {e}")

    return out



def do_brave_search(query, api_key):
    """Brave Search API — requires free API key from https://api.search.brave.com"""
    import urllib.parse, urllib.request

    out = {"summary": "", "results": [], "top_url": "", "top_text": ""}
    try:
        encoded = urllib.parse.quote_plus(query)
        url = f"https://api.search.brave.com/res/v1/web/search?q={encoded}&count=5"
        req = urllib.request.Request(url, headers={
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": api_key
        })
        with urllib.request.urlopen(req, timeout=8) as r:
            import gzip as _gz
            raw = r.read()
            try:
                raw = _gz.decompress(raw)
            except Exception:
                pass
            data = json.loads(raw.decode("utf-8"))

        results = data.get("web", {}).get("results", [])
        for item in results[:5]:
            out["results"].append({
                "title": item.get("title", "")[:120],
                "url": item.get("url", ""),
                "snippet": item.get("description", "")[:300],
            })
        if out["results"]:
            out["top_url"] = out["results"][0]["url"]
            out["summary"] = out["results"][0]["snippet"]

        # Fetch top page text
        if out["top_url"]:
            try:
                import re as _re
                req2 = urllib.request.Request(
                    out["top_url"],
                    headers={"User-Agent": "Mozilla/5.0 (compatible; HWUI/1.0)"}
                )
                with urllib.request.urlopen(req2, timeout=8) as r2:
                    raw2 = r2.read().decode("utf-8", errors="ignore")
                text = _re.sub(r"<[^>]+>", " ", raw2)
                text = _re.sub(r"\s+", " ", text).strip()
                out["top_text"] = text[:5000]
            except Exception as e:
                print(f"⚠️ Brave page fetch error: {e}")

    except Exception as e:
        print(f"⚠️ Brave search error: {e}")

    return out


def get_brave_api_key():
    """Read Brave API key from settings.json."""
    try:
        with open("settings.json", "r", encoding="utf-8") as f:
            s = json.load(f)
        return s.get("brave_api_key", "").strip()
    except Exception:
        return ""


def do_search(query):
    """
    Main search dispatcher.
    Uses Brave if API key is configured, falls back to DDG Instant Answer.
    """
    brave_key = get_brave_api_key()
    if brave_key:
        print(f"🔍 Using Brave Search for: {query}", flush=True)
        return do_brave_search(query, brave_key)
    else:
        print(f"🔍 Using DDG (no Brave key configured) for: {query}", flush=True)
        return do_web_search(query)


def format_search_results(query, res):
    """Format search results into a block for model injection.
    Includes top page content plus snippets from all results — gives model
    broad context to synthesise a thorough response like a frontier model would.
    """
    # Header is for internal reference only — not included in model output
    # to prevent it leaking into the response text
    lines = []

    # Top page — full content (most useful, fetched via HTTP)
    if res["top_url"]:
        lines.append(f"\nTop result: {res['top_url']}")
    if res["top_text"]:
        lines.append(f"Page content:\n{res['top_text'][:4000]}")
    elif res["summary"]:
        lines.append(f"Summary: {res['summary']}")

    # Additional results — titles + snippets for breadth
    if res["results"]:
        lines.append("\nOther relevant results:")
        for r in res["results"][:5]:
            lines.append(f"• {r['title']}")
            if r.get("snippet"):
                lines.append(f"  {r['snippet']}")
            lines.append(f"  {r['url']}")

    if not res["summary"] and not res["results"] and not res["top_text"]:
        lines.append("No results found.")

    lines.append("[END WEB SEARCH RESULTS]")
    return "\n".join(lines)


def stream_model_response(payload):
    global abort_generation
    abort_generation = False  # Reset flag at start
    
    print("\n🧩 FULL PAYLOAD SENDING TO MODEL:", flush=True)
    print(json.dumps(payload, indent=2), flush=True)
    response = requests.post(
        f"{API_URL}/completion",
        json=payload,
        stream=True,
        timeout=None
    )
    print(f"🔗 Response status: {response.status_code}", flush=True)
    
    import sys
    total_chunks = 0
    all_text = []
    
    for line in response.iter_lines(chunk_size=1):
        # Check abort flag
        if abort_generation:
            print("🛑 Generation aborted by user", flush=True)
            response.close()  # Close the connection
            break
            
        if not line:
            continue
        try:
            line_str = line.decode("utf-8").strip()
            
            if line_str.startswith("data:"):
                line_str = line_str[5:].strip()
            if line_str == "[DONE]":
                break
            
            j = json.loads(line_str)
            chunk = strip_chatml_leakage(j.get("content", ""))
            total_chunks += 1
            
            if chunk:
                all_text.append(chunk)
                yield chunk
                sys.stdout.flush()
                
        except Exception as e:
            print(f"❌ Parse error: {e}", flush=True)
            continue
    
    print(f"\n🎯 DONE: {total_chunks} chunks, {len(''.join(all_text))} chars total", flush=True)

# --------------------------------------------------
# Stream vision/multimodal model response
# Uses /v1/chat/completions (OpenAI-compatible)
# --------------------------------------------------
def stream_vision_response(payload):
    global abort_generation
    abort_generation = False

    print("\n🖼️ VISION PAYLOAD SENDING TO MODEL:", flush=True)
    response = requests.post(
        f"{API_URL}/v1/chat/completions",
        json=payload,
        stream=True,
        timeout=None
    )
    print(f"🔗 Vision response status: {response.status_code}", flush=True)

    total_chunks = 0
    all_text = []

    for line in response.iter_lines(chunk_size=1):
        if abort_generation:
            print("🛑 Vision generation aborted by user", flush=True)
            response.close()
            break

        if not line:
            continue
        try:
            line_str = line.decode("utf-8").strip()

            if line_str.startswith("data:"):
                line_str = line_str[5:].strip()
            if line_str == "[DONE]":
                break

            j = json.loads(line_str)
            # /v1/chat/completions uses choices[0].delta.content
            delta = j.get("choices", [{}])[0].get("delta", {})
            chunk = strip_chatml_leakage(delta.get("content", ""))
            total_chunks += 1

            if chunk:
                all_text.append(chunk)
                yield chunk
                sys.stdout.flush()

        except Exception as e:
            print(f"❌ Vision parse error: {e}", flush=True)
            continue

    print(f"\n🎯 VISION DONE: {total_chunks} chunks, {len(''.join(all_text))} chars total", flush=True)

# --------------------------------------------------
# Global abort flag for stopping generation
# --------------------------------------------------
abort_generation = False

@app.route("/abort_generation", methods=["POST"])
def abort_generation_endpoint():
    """Stop the current generation immediately."""
    global abort_generation
    abort_generation = True
    print("🛑 Generation abort requested")
    return jsonify({"status": "aborted"}), 200   
    
# --------------------------------------------------
# Load Recent Chat (for Smart Memory Summarizer)
# --------------------------------------------------
def load_recent_chat(character_name, max_turns=6):
    """
    Loads the last N turns of chat from the character's chat log.
    Returns a string suitable for summarization.
    """
    try:
        chat_path = os.path.join(
            os.path.dirname(__file__),
            "chats",
            f"{character_name.lower()}_chat_001.txt"
        )

        if not os.path.exists(chat_path):
            print(f"💬 No existing chat file found for {character_name}.")
            return None

        with open(chat_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # Grab last N*2 lines (user + model messages)
        snippet = "".join(lines[-(max_turns * 2):]).strip()
        print(f"📜 Loaded recent chat for {character_name} ({len(snippet)} chars).")
        return snippet if snippet else None

    except Exception as e:
        print(f"❌ Failed to load recent chat for {character_name}: {e}")
        return None

# --------------------------------------------------
# Append a New Memory Block
# --------------------------------------------------
@app.route('/append_character_memory', methods=['POST'])
def append_character_memory():
    try:
        data = request.get_json(force=True)
        char_name = (data.get("character") or "").strip()
        body = (data.get("body") or "").strip()  # This is the full formatted block
        
        if not char_name or not body:
            return jsonify({"error": "Character and body required."}), 400
        
        memory_dir = os.path.join(os.path.dirname(__file__), "memories")  # ← Fixed case
        os.makedirs(memory_dir, exist_ok=True)
        
        file_path = os.path.join(memory_dir, f"{char_name.lower()}_memory.txt")
        
        with open(file_path, "a", encoding="utf-8") as f:
            f.write("\n\n" + body + "\n\n")  # Just append the already-formatted block
        
        print(f"🧠 Memory saved for {char_name}")
        return jsonify({"status": "ok"}), 200
        
    except Exception as e:
        print(f"❌ append_character_memory error: {e}")
        return jsonify({"error": str(e)}), 500

# --------------------------------------------------
# Chat Endpoint (Smart Memory Trigger + Natural Recall + Proper Formatting)
# --------------------------------------------------
@app.route("/chat", methods=["POST"])
def chat():
    print("🔴🔴🔴 CHAT ROUTE HIT - STARTING 🔴🔴🔴")
    import datetime
    import re, os, json, requests
    
    data = request.get_json()
    print(f"🔍 DEBUG: Full request data keys: {data.keys()}")
    
    # Get conversation history from request (more reliable than reading from file)
    active_chat = data.get("conversation_history", [])
    
    # ✅ FIX: Extract user input from conversation_history instead of 'input' field
    user_input = ""
    if active_chat:
        for msg in reversed(active_chat):
            if msg.get("role") == "user":
                user_input = msg.get("content", "")
                break
    
    print(f"🔍 DEBUG: Extracted user_input: {user_input[:100] if user_input else '(empty)'}")
    
    character_name = data.get("character", "").strip()
    user_name = data.get("user_name", "User")
    
    
    # Handle multimodal content (images) — extract text part only for processing
    # Keep original for sending to model, use user_input_text for all string operations
    if isinstance(user_input, list):
        text_parts = [p.get("text", "") for p in user_input if p.get("type") == "text"]
        user_input_text = " ".join(text_parts)
    else:
        user_input_text = user_input

    # Reassign user_input to the text-only version for all downstream string processing
    # The multimodal content is preserved in active_chat for the vision path
    user_input = user_input_text

    clean_input = re.sub(r"<\|.*?\|>", "", user_input).strip()
    
    print(f"🔍 DEBUG: clean_input for memory detection: {clean_input[:100] if clean_input else '(empty)'}")
    
    # 🔥 LOAD USER PERSONA BIO
    user_bio = ""
    user_display_name = user_name
    try:
        user_file_path = os.path.join(USERS_DIR, f"{user_name}.json")
        if os.path.exists(user_file_path):
            with open(user_file_path, "r", encoding="utf-8") as uf:
                user_data = json.load(uf)
                user_bio = user_data.get("bio", "")
                user_display_name = user_data.get("display_name", user_name)
                print(f"✅ Loaded user persona for {user_name}")
                print(f"   Display name: {user_display_name}")
                print(f"   Bio length: {len(user_bio)} chars")
                if user_bio:
                    print(f"   Bio preview: {user_bio[:150]}...")
        else:
            print(f"⚠️ User persona file not found: {user_file_path}")
    except Exception as e:
        print(f"❌ Failed to load user persona: {e}")
    
    print(f"🔍 DEBUG: Received conversation_history from frontend:")
    print(f"🔍 DEBUG: Length: {len(active_chat)}")
    if active_chat:
        last = active_chat[-1]
        # Safe preview — don't dump base64 image data to console
        if isinstance(last.get("content"), list):
            preview = [p.get("type","?") + (":" + p.get("text","")[:60] if p.get("type")=="text" else "") for p in last["content"]]
            print(f"🔍 DEBUG: Last message: role={last.get('role')} content=[{', '.join(preview)}]")
        else:
            print(f"🔍 DEBUG: Last message: role={last.get('role')} content={str(last.get('content',''))[:200]}")

    print(f"📜 Received {len(active_chat)} messages from frontend")

    # If not provided, fall back to loading from file
    if not active_chat:
        current_chat_filename = data.get("current_chat_filename", "")
        
        if current_chat_filename:
            chat_file_path = os.path.join("chats", current_chat_filename)
            
            if os.path.exists(chat_file_path):
                try:
                    with open(chat_file_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    
                    lines = content.strip().split('\n')
                    
                    for line in lines:
                        if ':' not in line:
                            continue
                        
                        speaker, message = line.split(':', 1)
                        speaker = speaker.strip()
                        message = message.strip()
                        
                        if speaker == user_name or speaker == user_display_name:
                            role = "user"
                        elif speaker == character_name:
                            role = "assistant"
                        else:
                            continue
                        
                        active_chat.append({"role": role, "content": message})
                    
                    print(f"📜 Loaded {len(active_chat)} messages from {current_chat_filename} (fallback)")
                
                except Exception as e:
                    print(f"⚠️ Failed to load chat file: {e}")
            else:
                print(f"⚠️ Chat file not found: {chat_file_path}")
        else:
            print("⚠️ No conversation_history or current_chat_filename provided")
    
    if not character_name:
        return jsonify({"error": "No character specified"}), 400

    # 🔹 Load character JSON
    char_path = os.path.join("characters", f"{character_name}.json")
    if not os.path.exists(char_path):
        return jsonify({"error": f"Character file not found: {char_path}"}), 404

    with open(char_path, "r", encoding="utf-8") as f:
        char_data = json.load(f)

    print("🧩 Loaded character file:", char_path)
    print("🧩 example_dialogue present:", "example_dialogue" in char_data)
    print("🧩 example_dialogue length:", len(char_data.get("example_dialogue", "")))
        
    # --------------------------------------------------
    # Load Helcyon's core system layer (hardcoded)
    # --------------------------------------------------
    system_prompt, current_time = get_system_prompt()
    instruction = get_instruction_layer()
    tone_primer = get_tone_primer()

    print(f"⏰ Time context injected: {current_time}")
    
    # --------------------------------------------------
    # Load Project Instructions & Documents (if in a project)
    # --------------------------------------------------
    project_instructions = ""
    project_documents = ""
    project_rp_mode = False
    project_rp_opener = ""
    newly_pinned_doc = None

    try:
        from project_routes import get_active_project
        active_project = get_active_project()
        
        if active_project:
            projects_dir = os.path.join(os.path.dirname(__file__), "projects")
            config_path = os.path.join(projects_dir, active_project, "config.json")
            
            # Load project instructions
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    project_config = json.load(f)
                    project_instructions = project_config.get("instructions", "").strip()
                    project_rp_mode = project_config.get("rp_mode", False)
                    project_rp_opener = project_config.get("rp_opener", "").strip()
                    
                    if project_instructions:
                        project_instructions = (
                            f"\n\n"
                            f"═══════════════════════════════════════════════════════════\n"
                            f"PROJECT CONTEXT\n"
                            f"═══════════════════════════════════════════════════════════\n\n"
                            f"{project_instructions}\n\n"
                            f"═══════════════════════════════════════════════════════════\n"
                            f"END PROJECT CONTEXT\n"
                            f"═══════════════════════════════════════════════════════════\n\n"
                        )
                        print(f"📁 Injected project instructions for: {active_project}")
                        print(f"   Instructions length: {len(project_instructions)} chars")
            
            
            
            # Load documents - sticky mode or keyword trigger
            project_documents = ""
            newly_pinned_doc = None
            user_input_lower = user_input.lower()
            sticky_docs = project_config.get("sticky_docs", False) if os.path.exists(config_path) else False
            sticky_doc_file = project_config.get("sticky_doc_file") if os.path.exists(config_path) else None
            document_triggers = ['document', 'file', 'pdf', 'according to', 'doc', 'scan the', 'scan my', 'look up', 'timeline', 'journal', 'diary', 'show me', 'search', 'reference the', 'look in', 'check the']

            # Helper: load a specific file directly by name (no keyword matching)
            def load_pinned_doc_direct(proj_name, fname):
                proj_dir = os.path.join(os.path.dirname(__file__), "projects")
                fpath = os.path.join(proj_dir, proj_name, "documents", fname)
                if not os.path.exists(fpath):
                    print(f"⚠️ Pinned doc not found on disk: {fpath}")
                    return ""
                try:
                    with open(fpath, 'r', encoding='utf-8') as pf:
                        content = pf.read()
                    MAX_CHARS_PER_DOC = 8000
                    if len(content) > MAX_CHARS_PER_DOC:
                        content = content[:MAX_CHARS_PER_DOC]
                    return (
                        "\n\n"
                        "═══════════════════════════════════════════════════════════\n"
                        "PROJECT DOCUMENTS\n"
                        "═══════════════════════════════════════════════════════════\n\n"
                        f"### Document: {fname}\n\n{content}\n\n"
                        "═══════════════════════════════════════════════════════════\n"
                        "END PROJECT DOCUMENTS\n"
                        "═══════════════════════════════════════════════════════════\n\n"
                    )
                except Exception as e:
                    print(f"❌ Failed to read pinned doc {fname}: {e}")
                    return ""

            # Helper: check if user is requesting a DIFFERENT doc by keyword
            def user_requesting_different_doc(user_q, current_pinned):
                """Returns True if user seems to be asking for a doc that isn't the pinned one."""
                stopwords = {'the', 'a', 'an', 'in', 'on', 'at', 'to', 'for', 'of', 'and', 'or',
                             'document', 'file', 'pdf', 'scan', 'check', 'look', 'show', 'search', 'read', 'open'}
                words = user_q.lower().replace('_', ' ').split()
                keywords = [w for w in words if w not in stopwords and len(w) > 2]
                if not keywords or not current_pinned:
                    return False
                pinned_lower = current_pinned.lower().replace('_', ' ').replace('.', ' ')
                # If any keyword matches the pinned doc, user is asking for the same one
                for kw in keywords:
                    if kw in pinned_lower:
                        return False
                # No keyword matches the pinned doc name - they may want something else
                # Only return True if a trigger word is also present (to avoid false positives)
                has_trigger = any(t in user_q.lower() for t in document_triggers)
                return has_trigger

            if sticky_docs and sticky_doc_file:
                # Check if user is asking for a DIFFERENT doc than the pinned one
                if user_requesting_different_doc(user_input, sticky_doc_file):
                    print(f"📌 Sticky override - user requesting different doc, doing keyword search")
                    project_documents = load_project_documents(active_project, user_input)
                    if project_documents:
                        # Update the pinned doc to the newly loaded one
                        match = re.search(r'### Document: (.+?)\n', project_documents)
                        if match:
                            new_pinned = match.group(1).strip()
                            try:
                                with open(config_path, "r", encoding="utf-8") as f:
                                    cfg = json.load(f)
                                cfg["sticky_doc_file"] = new_pinned
                                with open(config_path, "w", encoding="utf-8") as f:
                                    json.dump(cfg, f, indent=2)
                                print(f"📌 Pinned doc updated to: {new_pinned}")
                            except Exception as e:
                                print(f"⚠️ Could not update pinned doc: {e}")
                    else:
                        # Fallback: load the original pinned doc
                        project_documents = load_pinned_doc_direct(active_project, sticky_doc_file)
                else:
                    # Normal sticky load - load pinned doc directly, no keyword matching
                    project_documents = load_pinned_doc_direct(active_project, sticky_doc_file)
                    if project_documents:
                        print(f"📌 Sticky mode - loaded pinned doc: {sticky_doc_file} ({len(project_documents)} chars)")
                    else:
                        print(f"📌 Sticky mode - pinned doc missing, clearing pin")
                        try:
                            with open(config_path, "r", encoding="utf-8") as f:
                                cfg = json.load(f)
                            cfg["sticky_doc_file"] = None
                            with open(config_path, "w", encoding="utf-8") as f:
                                json.dump(cfg, f, indent=2)
                        except Exception:
                            pass

            elif sticky_docs and not sticky_doc_file:
                # Sticky ON but no doc pinned yet
                # First check: if only one doc in folder, auto-load it without needing a trigger
                docs_dir_check = os.path.join(os.path.dirname(__file__), "projects", active_project, "documents")
                all_docs = [f for f in os.listdir(docs_dir_check) if os.path.isfile(os.path.join(docs_dir_check, f))] if os.path.exists(docs_dir_check) else []
                
                if len(all_docs) == 1:
                    # Only one doc - just load it, no trigger needed
                    auto_fname = all_docs[0]
                    project_documents = load_pinned_doc_direct(active_project, auto_fname)
                    if project_documents:
                        print(f"📌 Sticky auto-pinned single doc: {auto_fname}")
                        try:
                            with open(config_path, "r", encoding="utf-8") as f:
                                cfg = json.load(f)
                            cfg["sticky_doc_file"] = auto_fname
                            with open(config_path, "w", encoding="utf-8") as f:
                                json.dump(cfg, f, indent=2)
                        except Exception as e:
                            print(f"⚠️ Could not save auto-pin: {e}")
                elif any(trigger in user_input_lower for trigger in document_triggers):
                    # Multiple docs - use keyword trigger to find and pin one
                    project_documents = load_project_documents(active_project, user_input)
                    if project_documents:
                        print(f"📌 Sticky mode - first trigger, loading and pinning doc")
                        match = re.search(r'### Document: (.+?)\n', project_documents)
                        if match:
                            pinned_filename = match.group(1).strip()
                            try:
                                with open(config_path, "r", encoding="utf-8") as f:
                                    cfg = json.load(f)
                                cfg["sticky_doc_file"] = pinned_filename
                                with open(config_path, "w", encoding="utf-8") as f:
                                    json.dump(cfg, f, indent=2)
                                print(f"📌 Pinned doc saved: {pinned_filename}")
                                newly_pinned_doc = pinned_filename
                            except Exception as e:
                                print(f"⚠️ Could not save pinned doc: {e}")
                else:
                    print(f"📌 Sticky ON, multiple docs, waiting for trigger word")

            elif any(trigger in user_input_lower for trigger in document_triggers):
                # Normal keyword trigger (sticky OFF)
                project_documents = load_project_documents(active_project, user_input)
                if project_documents:
                    print(f"📄 User requested documents - loading {len(project_documents)} chars")
                    print(f"📄 DOCUMENT CONTENT PREVIEW:\n{project_documents[:1000]}")
            else:
                print(f"⭕ Skipped document loading - no trigger detected")
            
    except Exception as e:
        print(f"⚠️ Failed to load project data: {e}")
        project_instructions = ""
        project_documents = ""

    # --------------------------------------------------
    # Load Global Documents (always, regardless of project)
    # --------------------------------------------------
    try:
        global_docs = load_global_documents(user_input)
        if global_docs:
            project_documents = project_documents + global_docs
            print(f"🌐 Global doc injected ({len(global_docs)} chars)")
    except Exception as e:
        print(f"⚠️ Global document load failed: {e}")

    # --------------------------------------------------
    # Load character card and build system_text
    # --------------------------------------------------
    char_context = ""

    try:
        # Helper to strip stray ChatML tokens from any user-supplied text
        def strip_chatml(text):
            text = re.sub(r'<\|im_start\|>\w*', '', text)
            text = re.sub(r'<\|im_end\|>', '', text)
            return text.strip()

        # Build character context from JSON fields
        parts = []

        if char_data.get("name"):
            parts.append(f"Character Name: {char_data['name']}")
        if char_data.get("description"):
            parts.append(f"Description: {strip_chatml(char_data['description'])}")
        if char_data.get("scenario"):
            parts.append(f"Scenario: {strip_chatml(char_data['scenario'])}")

        # 📍 CURRENT SITUATION — semi-global, opt-in per character
        if char_data.get("use_current_situation"):
            try:
                with open("settings.json", "r", encoding="utf-8") as _sf:
                    _s = json.load(_sf)
                _situation = _s.get("current_situation", "").strip()
            except Exception:
                _situation = ""
            if _situation:
                parts.append(
                    f"═══════════════════════════════════════════════════════════\n"
                    f"WHAT YOU CURRENTLY KNOW ABOUT {user_display_name.upper() if user_display_name else 'THE USER'}\n"
                    f"(This is your own awareness — do not say you were told this, just know it)\n"
                    f"═══════════════════════════════════════════════════════════\n"
                    f"{strip_chatml(_situation)}\n"
                    f"═══════════════════════════════════════════════════════════"
                )

        if char_data.get("main_prompt"):
            parts.append(strip_chatml(char_data["main_prompt"]))
        
        # ✅ ADD POST-HISTORY INSTRUCTIONS
        if char_data.get("post_history"):
            parts.append(f"\nPost-History Instructions:\n{strip_chatml(char_data['post_history'])}")

        # 🧠 INJECT SESSION SUMMARY — only on fresh chats
        # A chat is "new" if there are no real assistant replies yet.
        # We treat it as new if: no assistant messages at all, OR exactly one assistant
        # message that is either flagged as opening line OR is very short (≤30 words —
        # opening lines are brief greetings, not real replies).
        assistant_msgs = [m for m in active_chat if m.get("role") == "assistant"]
        def _is_opening_line_msg(m):
            if m.get("is_opening_line"):
                return True
            content = m.get("content", "")
            if isinstance(content, list):
                content = " ".join(p.get("text","") for p in content if p.get("type")=="text")
            return len(content.split()) <= 30

        _is_new_chat = (
            len(assistant_msgs) == 0 or
            (len(assistant_msgs) == 1 and _is_opening_line_msg(assistant_msgs[0]))
        )

        char_context = "\n\n".join(parts)

        # 🔥 INJECT USER PERSONA CONTEXT
        # Always inject if we have a user name — bio is optional
        user_context = ""
        if user_display_name:
            _bio_block = f"{user_bio}\n\n" if user_bio else ""
            user_context = (
                f"\n\n"
                f"═══════════════════════════════════════════════════════════\n"
                f"USER CONTEXT - WHO YOU ARE TALKING TO\n"
                f"═══════════════════════════════════════════════════════════\n\n"
                f"You are {char_data.get('name', 'the assistant')}.\n"
                f"You are talking to {user_display_name}.\n\n"
                f"{_bio_block}"
                f"When {user_display_name} asks questions using 'I', 'my', or 'me', "
                f"they are referring to themselves ({user_display_name}), NOT to you.\n"
                f"You are {char_data.get('name', 'the assistant')}. "
                f"{user_display_name} is the person you're talking to.\n\n"
                f"═══════════════════════════════════════════════════════════\n"
                f"END USER CONTEXT\n"
                f"═══════════════════════════════════════════════════════════\n\n"
            )
            print(f"✅ Injected user persona context for {user_display_name} (bio: {len(user_bio)} chars)")
        else:
            print(f"⚠️ No user display name, skipping persona injection")

        # Build the system_text (WITHOUT example_dialogue yet)
        # Build the system_text (WITHOUT example_dialogue yet)
        system_text = (
            f"{system_prompt}\n\n{project_instructions}{project_documents}{user_context}{char_context}\n\n{instruction}\n\n{tone_primer}"
        )

        # 📊 LOG SYSTEM MESSAGE SIZE
        from truncation import rough_token_count
        system_tokens = rough_token_count(system_text)
        print(f"📊 SYSTEM MESSAGE SIZE: ~{system_tokens} tokens")
        if system_tokens > 6000:
            print(f"🔴 WARNING: System message is very large! May cause context overflow.")
        elif system_tokens > 4000:
            print(f"⚠️ CAUTION: System message is getting large.")

        print("=" * 80)
        print("DEBUG: FULL SYSTEM_TEXT BEING SENT:")
        print("=" * 80)
        print(system_text)
        print("=" * 80)

    except Exception as e:
        print(f"⚠️ Failed to build character context: {e}")
        system_text = system_prompt
        
    # --------------------------------------------------
    # Load memory file and find relevant block
    # --------------------------------------------------
    def load_character_memory(character_name):
        _mem_dir = os.path.join(os.path.dirname(__file__), "memories")
        path = os.path.join(_mem_dir, f"{character_name.lower()}_memory.txt")
        if not os.path.exists(path):
            return ""
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def load_global_memory():
        _mem_dir = os.path.join(os.path.dirname(__file__), "memories")
        path = os.path.join(_mem_dir, "global_memory.txt")
        if not os.path.exists(path):
            return ""
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def extract_keywords_from_block(block):
        """Extract keywords from a memory block's Keywords line."""
        lines = block.split('\n')
        for line in lines:
            if line.strip().lower().startswith('keywords:'):
                keywords_str = line.split(':', 1)[1].strip()
                keywords = [kw.strip().lower() for kw in keywords_str.split(',')]
                return keywords
        return []

    memory_text = ""
    use_personal = char_data.get("use_personal_memory", True)  # default on for backward compat
    use_global = char_data.get("use_global_memory", False)

    if use_personal:
        personal_text = load_character_memory(character_name)
        if personal_text:
            memory_text += personal_text
    if use_global:
        global_text = load_global_memory()
        if global_text:
            if memory_text:
                memory_text += "\n\n"
            memory_text += global_text

    print(f"🧠 Memory flags — personal: {use_personal}, global: {use_global}, total chars: {len(memory_text)}")
    
    chosen_blocks = []

    if memory_text:
        blocks = re.split(r"(?m)^# Memory:", memory_text)
        user_input_lower = user_input.lower()
        
        # Score ALL memory blocks
        scored_blocks = []
        
        for b in blocks:
            if not b.strip():
                continue
                
            keywords = extract_keywords_from_block(b)
            
            # Calculate match score
            # Give MORE weight to specific/rare keywords, LESS to common ones
            common_keywords = {'claire', 'chris', 'neville', '4d', '3d'}
            
            score = 0
            matched_keywords = []
            
            for kw in keywords:
                if kw in user_input_lower:
                    # Rare keyword = 3 points, common keyword = 1 point
                    if kw in common_keywords:
                        score += 1
                    else:
                        score += 3
                    matched_keywords.append(kw)
            
            if score > 0:
                scored_blocks.append({
                    'score': score,
                    'block': b.strip(),
                    'matched_keywords': matched_keywords
                })
        
        # Sort by score (highest first)
        scored_blocks.sort(key=lambda x: x['score'], reverse=True)
        
        # Take TOP 2 memories only (configurable)
        MAX_MEMORIES = 2
        
        if scored_blocks:
            chosen_blocks = [item['block'] for item in scored_blocks[:MAX_MEMORIES]]
            
            print(f"🧠 Memory retrieval:")
            for i, item in enumerate(scored_blocks[:MAX_MEMORIES]):
                print(f"   #{i+1}: Score {item['score']} - Matched: {', '.join(item['matched_keywords'])}")
        else:
            print("🧠 No keyword matches found, no memory injected")

    # Format multiple memories
    if chosen_blocks:
        memory = "Relevant memories:\n\n" + "\n\n---\n\n".join(chosen_blocks) + "\n"
    else:
        memory = ""

    # --------------------------------------------------
    # Build unified prompt (with example dialogue fenced in system block)
    # --------------------------------------------------
    
    
# ✅ FIX: Clean and limit conversation history BEFORE building messages
    # Filter to only valid user/assistant messages
    active_chat = [
        msg for msg in active_chat 
        if msg.get("role") in ["user", "assistant"] and (
            isinstance(msg.get("content"), list) or msg.get("content", "").strip()
        )
    ]
    
    # Limit to last 20 messages (10 exchanges) to prevent massive prompts
    if len(active_chat) > 20:
        active_chat = active_chat[-20:]
        print(f"⚠️ Trimmed conversation history to last 20 messages")
    
    print(f"📊 Using {len(active_chat)} messages from conversation history")
    
    # 🔥 NEW: Decide if this is a new conversation or continuation
    assistant_messages = [msg for msg in active_chat if msg.get("role") == "assistant"]
    print(f"🔍 DEBUG: Found {len(assistant_messages)} assistant messages in active_chat")
    print(f"🔍 DEBUG: active_chat roles: {[msg.get('role') for msg in active_chat]}")
    # Combine system text with memory
    messages = [
        {"role": "system", "content": system_text + "\n" + memory},
        *active_chat  # ← THIS is the full conversation history (includes latest user msg)
    ]

    # 🎭 INJECT RP OPENER into LLM context — synthetic first assistant message
    # Only on new chats so the model treats it as its own prior output and continues the style
    if project_rp_mode and project_rp_opener and _is_new_chat:
        messages.insert(1, {"role": "assistant", "content": project_rp_opener})
        print(f"🎭 RP opener injected into LLM context ({len(project_rp_opener)} chars)")
    
    # ✅ INJECT AUTHOR'S NOTE if provided
    author_note = data.get("author_note", "").strip()
    if author_note:
        # Insert near the end for maximum influence (before last 2-3 messages)
        insert_position = max(1, len(messages) - 3)
        
        messages.insert(insert_position, {
            "role": "system",
            "content": f"[Author's Note: {author_note}]"
        })
        print(f"✅ Injected Author's Note at position {insert_position}: {author_note[:50]}...")
        
   
    # ✅ INJECT CHARACTER NOTE if present (every 4 messages)
    char_note = char_data.get("character_note", "").strip()
    if char_note:
        # Count total messages (excluding system messages)
        message_count = len([m for m in messages if m.get("role") in ["user", "assistant"]])
        
        # Inject every 4 messages
        if message_count % 4 == 0 or message_count < 4:
            insert_position = max(1, len(messages) - 3)
            
            messages.insert(insert_position, {
                "role": "system",
                "content": f"[Character Note: {char_note}]"
            })
            print(f"✅ Injected Character Note at position {insert_position} (message #{message_count}): {char_note[:50]}...")
        else:
            print(f"⏭️ Skipped Character Note injection (message #{message_count})")
    
    # Trim if needed (secondary safety net)
    from truncation import trim_chat_history
    messages = trim_chat_history(messages)
    
    print(f"🔍 DEBUG: After trimming, {len(messages)} messages remain")
    
    # (project instructions are already in the system message above - no need to repeat)
    
 
# ✅ FIX: Re-attach example_dialogue INSIDE system block with clear fencing
    ex_block = ""
    has_paragraph_style = False  # used by example dialogue style rules block below
    if char_data.get("example_dialogue"):
        ex = char_data["example_dialogue"].strip()
        # 🔥 Strip any stray ChatML tokens from example dialogue - these cause the model
        # to see a premature end-of-turn inside the system block and emit a stop token
        # as its very first generation token, producing zero output.
        ex = re.sub(r'<\|im_start\|>\w*', '', ex)
        ex = re.sub(r'<\|im_end\|>', '', ex)
        ex = ex.strip()

        # 🔥 NORMALISE SPEAKER LABELS — replace any real participant names with generic
        # labels so the model can never confuse example turns with actual conversation.
        # Covers the character's own name and the user's display name.
        _char_name = char_data.get("name", "").strip()
        _user_name = user_display_name.strip() if user_display_name else ""
        if _char_name:
            ex = re.sub(rf'(?im)^{re.escape(_char_name)}\s*:', 'Assistant:', ex)
        if _user_name:
            ex = re.sub(rf'(?im)^{re.escape(_user_name)}\s*:', 'User:', ex)
        print(f"🧹 Example dialogue speaker labels normalised (char='{_char_name}', user='{_user_name}')")
        
        # 🔍 Check if character uses emojis or xxx in their examples
        has_emojis = any(emoji in ex for emoji in ['❤️', '😍', '😘', '💕', '😊', '😉', '🔥', '💯', '✨', '🎯'])
        has_xxx = 'xxx' in ex.lower()

        # 🔍 Detect if examples use flowing multi-sentence paragraphs
        ex_paragraphs = [p.strip() for p in ex.split('\n\n') if p.strip()]
        has_paragraph_style = any(p.count('. ') >= 1 or p.count('! ') >= 1 or p.count('? ') >= 1 for p in ex_paragraphs)
        
        # Build conditional style instructions
        style_rules = []
        if has_emojis:
            style_rules.append("- Use emojis EXACTLY like the examples show")
        if has_xxx:
            style_rules.append("- End messages with 'xxx' or 'xxxx' like the character does")
        
        # Add generic style rules that apply to everyone
        style_rules.insert(0, "- Copy the EXACT tone, energy, and emotional warmth")
        style_rules.append("- Match their vocabulary, sentence structure, and rhythm")
        if has_paragraph_style:
            style_rules.append("- Write in flowing paragraphs like the examples — multiple sentences per paragraph")
            style_rules.append("- DO NOT put every sentence on its own line — group related sentences together into paragraphs")
        style_rules.append("- DO NOT copy the topics or situations from examples")
        style_rules.append("- Generate NEW content in this character's style")
        
        ex_block = (
            "\n\n"
            "═══════════════════════════════════════════════════════════\n"
            "⚠️ CRITICAL STYLE INSTRUCTION - READ CAREFULLY\n"
            "═══════════════════════════════════════════════════════════\n\n"
            "Below are example messages showing this character's speaking style.\n"
            "⛔ THESE ARE STYLE TEMPLATES ONLY — NOT CONVERSATION HISTORY.\n"
            "⛔ DO NOT repeat, paraphrase, or echo any wording from these examples in your reply.\n"
            "⛔ If the user's topic is similar to an example, write COMPLETELY FRESH content — same style, brand new words.\n\n"
            "🎯 YOUR TASK:\n"
            + "\n".join(style_rules) + "\n\n"
            + ex +
            "\n\n"
            "═══════════════════════════════════════════════════════════\n"
            "⚠️ REMINDER: Use the style above — but every word of your reply must be original.\n"
            "⚠️ Repeating or paraphrasing example content is a failure. Write fresh every time.\n"
            "═══════════════════════════════════════════════════════════\n\n"
        )
        print(f"🧩 Added example_dialogue to system block ({len(ex)} chars)")
        if has_emojis:
            print("   📱 Emojis detected in examples")
        if has_xxx:
            print("   💋 xxx kisses detected in examples")
        
        # Add example dialogue to system message
        if messages and messages[0].get("role") == "system":
            messages[0]["content"] += ex_block
            
            # 🔥 DEBUG: Check if example dialogue made it through
            print("\n" + "="*80)
            print("🎭 SYSTEM MESSAGE AFTER ADDING EXAMPLE DIALOGUE:")
            print("="*80)
            system_content = messages[0]["content"]
            print(f"Length: {len(system_content)} chars")
            print(f"Last 500 chars:\n{system_content[-500:]}")
            print("="*80 + "\n")
    
# 🔥 If continuation, inject a meta-instruction RIGHT BEFORE the last user message
    if len(assistant_messages) > 0:
        print("🔄 Continuation detected - injecting continuation context")
        
        # Insert a system message near the end that explicitly tells model to continue naturally
        continuation_msg = {
            "role": "system",
            "content": "Continue the conversation naturally. Do NOT greet the user again or recap previous messages. Respond directly to the most recent message as if no break occurred."
        }
        
        # Insert it right before the last user message (so it's the last thing the model sees before generating)
        insert_position = len(messages) - 1  # Right before the latest user message
        messages.insert(insert_position, continuation_msg)
        print(f"✅ Inserted continuation reminder at position {insert_position}")
    else:
        print("🆕 New conversation detected - allowing greeting")

    # Build final ChatML prompt from ALL messages
    prompt_parts = []
    for msg in messages:
        role = msg.get("role", "user")
        raw_content = msg.get("content", "")
        # Handle multimodal content — extract text only for ChatML prompt building
        if isinstance(raw_content, list):
            content = " ".join(
                part.get("text", "") for part in raw_content if part.get("type") == "text"
            ).strip()
        else:
            content = raw_content.strip()
        prompt_parts.append(f"<|im_start|>{role}\n{content}\n<|im_end|>")       
    
    # Add the assistant start tag (with pre-fill for continuations)
    continue_prefix = data.get("continue_prefix", "").strip()
    if continue_prefix:
        # True continue — model picks up exactly where it left off
        prompt_parts.append(f"<|im_start|>assistant\n{continue_prefix}")
        print(f"▶️ CONTINUE: Pre-filling assistant tag with prefix ({len(continue_prefix)} chars)")
    elif len(assistant_messages) > 0:
        # Continuation - pre-fill response to force continuation
        prompt_parts.append("<|im_start|>assistant\n")
        print("🔥 NUCLEAR: Pre-filled assistant response to force continuation")
    else:
        # New conversation - let model start fresh
        prompt_parts.append("<|im_start|>assistant\n")
        print("🆕 New conversation - model free to greet")
    
    # Join parts — the assistant tag must not be preceded by a bare newline
    # because the model's first token is often \n, which would then match
    # the stop sequence "\n<|im_start|>" and kill the response after 2 tokens.
    prompt = "\n".join(prompt_parts[:-1]) + "\n" + prompt_parts[-1]
    
    # 🔍 DEBUG: Check the end of the prompt
    print("\n" + "="*60)
    print("🔍 FINAL PROMPT DEBUG")
    print("="*60)
    print("Last 300 chars of prompt:")
    print(prompt[-300:])
    print("\n🛑 Stop tokens:", ["<|im_end|>", "\n<|im_start|>"])
    print("="*60 + "\n")
        
    # --- Final safety clamp ---
    # Words average ~1.3 tokens each, so 7500 words = ~9750 tokens.
    # ⚠️ IMPORTANT: Do NOT truncate from the front of the prompt.
    # The system message (containing example dialogue + style instructions) is at the front.
    # Losing it causes style collapse. Instead: preserve the system block and drop
    # oldest conversation turns from the middle until the prompt fits.
    MAX_WORDS_APPROX = 7500
    words = prompt.split()

    if len(words) > MAX_WORDS_APPROX:
        # Isolate the system block (everything up to and including the first <|im_end|>)
        sys_end = prompt.find("<|im_end|>")
        if sys_end != -1:
            system_block = prompt[:sys_end + len("<|im_end|>")]
            conversation_block = prompt[sys_end + len("<|im_end|>"):]
        else:
            system_block = ""
            conversation_block = prompt

        system_words = len(system_block.split())
        convo_budget = MAX_WORDS_APPROX - system_words

        # Split remaining conversation into individual turns and drop oldest first
        turns = conversation_block.split("<|im_start|>")
        turns = [t for t in turns if t.strip()]

        while turns and len(" ".join(turns).split()) > convo_budget:
            turns.pop(0)

        trimmed_convo = "<|im_start|>" + "<|im_start|>".join(turns) if turns else ""
        prompt = system_block + trimmed_convo
        print(f"✂️ Prompt trimmed: kept system block ({system_words} words) + {len(turns)} conversation turns (was {len(words)} words total)", flush=True)
    
    prompt = prompt.strip().replace("\x00", "")

    # ── Scan for embedded ChatML tokens that would cause zero-output ──────
    # Split on the assistant tag — everything before it is the context block.
    # Any <|im_end|> found inside that context (not as a proper turn-closer)
    # will cause the model to fire a stop token as its very first output.
    pre_assistant = prompt.split("<|im_start|>assistant")[0] if "<|im_start|>assistant" in prompt else prompt
    embedded_ends = pre_assistant.count("<|im_end|>")
    expected_ends = prompt[:prompt.find("<|im_start|>assistant")].count("<|im_start|>") if "<|im_start|>assistant" in prompt else 0
    print(f"\n🔍 CHATML SANITY CHECK:")
    print(f"   <|im_end|> tags found in context: {embedded_ends}")
    print(f"   <|im_start|> tags found in context: {expected_ends}")
    if embedded_ends != expected_ends:
        print(f"   ⚠️  MISMATCH — {embedded_ends - expected_ends} extra <|im_end|> tag(s) embedded in content!")
        print(f"   🔧 Auto-stripping extra embedded tags from prompt content...")
        # Rebuild: strip <|im_end|> only from INSIDE message content (not the structural ones)
        import re as _re
        def clean_msg_content(m):
            role = m.group(1)
            content = m.group(2)
            content = _re.sub(r"<\|im_end\|>", "", content)
            content = _re.sub(r"<\|im_start\|>\w*", "", content)
            return f"<|im_start|>{role}\n{content}\n<|im_end|>"
        prompt = _re.sub(r"<\|im_start\|>(\w+)\n(.*?)\n<\|im_end\|>", clean_msg_content, prompt, flags=_re.DOTALL)
        print(f"   ✅ Prompt cleaned.")
    else:
        print(f"   ✅ Tags balanced — prompt structure looks clean")
    
    print("\n===== FINAL PROMPT SENT TO MODEL =====")
    print(prompt[:1500])  # print first 1500 chars for sanity check
    print("======================================\n")
    # --- Load current sampling config ---
    sampling = load_sampling_settings()
   
# ============================================================
    # VISION / MULTIMODAL DETECTION
    # Check if any user message in history has image content
    # ============================================================
    has_images = False
    for msg in active_chat:
        if isinstance(msg.get("content"), list):
            has_images = True
            break

    sampling = load_sampling_settings()

    if has_images:
        # --------------------------------------------------------
        # VISION PATH: Use /v1/chat/completions with messages array
        # Pixtral / LLaVA / multimodal models
        # --------------------------------------------------------
        print("🖼️ VISION MODE: Using /v1/chat/completions with multimodal messages", flush=True)

        # Only keep image data in the MOST RECENT user message
        # Older messages get text-only to avoid massive payloads
        cleaned_chat = []
        last_image_msg_idx = None
        for i, msg in enumerate(active_chat):
            if isinstance(msg.get("content"), list):
                last_image_msg_idx = i

        for i, msg in enumerate(active_chat):
            if isinstance(msg.get("content"), list) and i != last_image_msg_idx:
                # Strip images from older messages, keep text only
                text_only = " ".join(
                    p.get("text", "") for p in msg["content"] if p.get("type") == "text"
                )
                cleaned_chat.append({"role": msg["role"], "content": text_only})
            else:
                cleaned_chat.append(msg)

        vision_messages = [
            {"role": "system", "content": system_text + "\n" + memory},
            *cleaned_chat
        ]

        vision_payload = {
            "model": CURRENT_MODEL or "local",
            "messages": vision_messages,
            "temperature": sampling["temperature"],
            "max_tokens": sampling["max_tokens"],
            "top_p": sampling["top_p"],
            "repeat_penalty": sampling["repeat_penalty"],
            "stream": True,
            "stop": ["<|im_end|>", "\n<|im_start|>"],
        }

        print("\n🧩 VISION PAYLOAD SENDING TO MODEL:", flush=True)
        print(f"  Messages count: {len(vision_messages)}", flush=True)

        try:
            return Response(
                stream_with_context(stream_vision_response(vision_payload)),
                content_type="text/event-stream; charset=utf-8",
            )
        except Exception as e:
            print(f"❌ Vision chat error: {e}", flush=True)
            return f"⚠️ Error contacting vision model: {e}", 500

    else:
        # --------------------------------------------------------
        # TEXT-ONLY PATH: existing /completion endpoint
        # --------------------------------------------------------
        payload = {
            "model": CURRENT_MODEL,
            "prompt": prompt,
            "temperature": sampling["temperature"],
            "max_tokens": sampling["max_tokens"],
            "n_predict": sampling["max_tokens"],  # llama.cpp /completion uses n_predict, not max_tokens
            "top_p": sampling["top_p"],
            "min_p": sampling.get("min_p", 0.05),
            "top_k": sampling.get("top_k", 40),
            "repeat_penalty": sampling["repeat_penalty"],
            "stream": True,
            "stop": ["<|im_end|>", "\n<|im_start|>"],
        }

        print("\n🧩 FULL PAYLOAD SENDING TO MODEL:", flush=True)
        print(json.dumps(payload, indent=2), flush=True)

        use_web_search = char_data.get("use_web_search", False)

        if use_web_search:
            def _web_search_stream():
                import re as _re

                # Search only fires on EXPLICIT user request.
                # Opt-in model: user must clearly ask for a search.
                # This prevents random conversational messages triggering irrelevant searches.
                # Strip any injected search results block from user_msg before checking
                # — previous turn's augmented message may be in conversation_history
                # and would contain search trigger phrases from the results block itself
                _user_msg = _re.sub(
                    u'\u2550+\nWEB SEARCH RESULTS.*?\u2550+',
                    '', user_input, flags=_re.DOTALL
                ).strip()
                # Also strip the IMPORTANT instruction block if present
                _user_msg = _re.sub(
                    r'IMPORTANT: Your response MUST be based.*',
                    '', _user_msg, flags=_re.DOTALL
                ).strip()
                print(f"🔍 Search trigger check on: {repr(_user_msg[:100])}", flush=True)

                # Opt-in search: ONLY fire on clear, unambiguous search requests.
                # Keep this list TIGHT — single common words cause false positives.
                _should_search = bool(_re.search(
                    r'\b(?:do a search|search for|search up|search that up|'
                    r'look it up|look that up|look up|find out about|'
                    r'google that|look online|check online|'
                    r'any (?:news|updates|info) (?:on|about)|'
                    r'(?:get me |give me )?up to date (?:info|news|updates) (?:on|about))',
                    _user_msg, _re.IGNORECASE
                ))

                if not _should_search:
                    print(f"\U0001f4ac No search trigger — responding from context", flush=True)
                    yield from stream_model_response(payload)
                    return

                # Clean the query: strip filler and meta-request verbs,
                # preserve all content words (subject, topic, context)
                _q = _user_msg
                _q = _re.sub(r'(?i)^(?:(?:hey|hi|okay|ok|yes|yeah|sure|babe|no|oh)[\.,!\s]*)+', '', _q).strip()
                _q = _re.sub(r'(?i)^(?:grok|helcyon|claude|gemma|samantha|nebula)[,\.]?\s*', '', _q).strip()
                _q = _re.sub(
                    r'\b(?:can you |could you |please )?'
                    r'(?:do a search(?:\s+and\s+(?:find out|tell me|show me))?|'
                    r'search(?:\s+up)?(?:\s+for)?(?:\s+and\s+(?:find out|tell me|show me))?|'
                    r'look(?:\s+it)?\s+up(?:\s+and\s+(?:tell me|show me))?|'
                    r'find out(?:\s+about)?|tell me about)\s*'
                    r'(?:info about\s*|info on\s*|info\s*|about\s*|on\s*|for\s*)?',
                    ' ', _q, flags=_re.IGNORECASE
                ).strip()
                _q = _re.sub(r'[,\s]*(?:please|for me|right now|would you|can you)[?.]?\s*$', '', _q, flags=_re.IGNORECASE).strip()
                _q = _re.sub(r'\s+', ' ', _q).strip().rstrip('?,.')

                # If the cleaned query is still long and conversational, use the model
                # to extract just the search topic — prevents Brave getting walls of text
                if len(_q) > 80:
                    try:
                        _extract_prompt = (
                            "<|im_start|>system\n"
                            "Extract the single best web search query from the user's message. "
                            "Return ONLY the search query — no explanation, no punctuation, no quotes. "
                            "Maximum 8 words. Focus on the specific topic or name being asked about.\n"
                            "<|im_end|>\n"
                            f"<|im_start|>user\n{_user_msg[:400]}\n<|im_end|>\n"
                            "<|im_start|>assistant\n"
                        )
                        _extract_payload = {
                            "prompt": _extract_prompt,
                            "temperature": 0.0,
                            "n_predict": 20,
                            "stream": False,
                            "stop": ["<|im_end|>", "\n"],
                        }
                        _er = requests.post(f"{API_URL}/completion", json=_extract_payload, timeout=10)
                        _extracted = _er.json().get("content", "").strip().strip('"').strip("'")
                        if _extracted and len(_extracted) > 2:
                            print(f"🔍 Model-extracted query: {repr(_extracted)} (was: {repr(_q[:60])}...)", flush=True)
                            _q = _extracted
                        else:
                            print("⚠️ Query extraction returned empty — using truncated original", flush=True)
                            _q = _q[:200].rsplit(' ', 1)[0]
                    except Exception as _qe:
                        print(f"⚠️ Query extraction failed: {_qe} — using truncated original", flush=True)
                        _q = _q[:200].rsplit(' ', 1)[0]

                # Brave rejects queries over ~400 chars (HTTP 422) — cap at 200
                if len(_q) > 200:
                    _q = _q[:200].rsplit(' ', 1)[0]

                if len(_q) > 2:
                    query = _q
                    print(f"🔍 Always-search query: {query}", flush=True)
                else:
                    # Fallback: stream live while watching for [WEB SEARCH: ...] tag
                    # Yield each chunk immediately — no delay — accumulate only for tag detection
                    _streamed = []
                    _tag_found = False
                    _search_query = None
                    try:
                        for chunk in stream_model_response(payload):
                            _streamed.append(chunk)
                            _rolling = "".join(_streamed)
                            _match = _re.search(r"\[WEB SEARCH:\s*(.+?)\]", _rolling, _re.IGNORECASE)
                            if _match:
                                _tag_found = True
                                _search_query = _match.group(1).strip()
                                break  # stop streaming, go do the search
                            yield chunk  # stream chunk live to frontend
                    except Exception as e:
                        yield f"⚠️ Model error: {e}"
                        return

                    if not _tag_found:
                        return  # already streamed everything, done

                    query = _search_query
                print(f"🔍 Web search triggered: {query}", flush=True)
                yield "\n\n🔍 *Searching...*\n\n"

                res = do_search(query)
                results_block = format_search_results(query, res)
                # Only count as having results if there is actual substantive content
                has_results = bool(res["summary"] or res["top_text"])
                print(f"🔍 Search done. has_results={has_results}", flush=True)
                print(f"   summary={repr(res['summary'][:120])}", flush=True)
                print(f"   top_url={res['top_url']}", flush=True)
                print(f"   top_text_len={len(res['top_text'])}", flush=True)
                print(f"   related_count={len(res['results'])}", flush=True)

                # Rebuild the prompt with search results baked into the user turn
                # This is a single inference — no re-prompt, no second call.
                # Results replace the original user message so the model responds
                # to the grounded content directly.
                if has_results:
                    import urllib.parse as _urlparse
                    # Best available URL: AbstractURL > first result > DDG search page
                    _src = (
                        res.get('top_url', '')
                        or (res['results'][0]['url'] if res['results'] else '')
                        or f"https://duckduckgo.com/?q={_urlparse.quote_plus(query)}"
                    )
                    augmented_user_msg = (
                        f"{user_input.strip()}\n\n"
                        f"════════════════════════════════════════\n"
                        f"WEB SEARCH RESULTS FOR: {query}\n"
                        f"════════════════════════════════════════\n"
                        f"{results_block}\n"
                        f"════════════════════════════════════════\n"
                        f"IMPORTANT: Your response MUST be based on the search results above ONLY. "
                        f"Do NOT use your training data or prior knowledge about this topic — "
                        f"the search results are the ground truth. "
                        f"If the results say something that contradicts what you think you know, "
                        f"trust the results. Summarise what the results say in your own words. "
                        f"Do not include a source link in your response."
                    )
                else:
                    augmented_user_msg = (
                        f"{user_input.strip()}\n\n"
                        f"[Web search returned zero results for '{query}'. "
                        f"Nothing found. No pages, no summary, no data. "
                        f"Tell the user clearly that nothing was found. "
                        f"Do not guess or invent anything.]"
                    )

                # Rebuild prompt with augmented user message — preserving full conversation history
                # Replace the last user message in the messages array with the augmented version
                # so the model has all prior context (not just the current message in isolation)
                search_messages = []
                for m in messages:
                    search_messages.append(dict(m))

                # Strip any previous WEB SEARCH RESULTS blocks from older user turns
                # so stale search data from prior messages doesn't echo into the new response
                _last_user_idx = None
                for i in range(len(search_messages) - 1, -1, -1):
                    if search_messages[i].get("role") == "user":
                        _last_user_idx = i
                        break
                for i, m in enumerate(search_messages):
                    if m.get("role") == "user" and i != _last_user_idx:
                        content = m.get("content", "")
                        if isinstance(content, str) and "WEB SEARCH RESULTS" in content:
                            # Strip everything from the search block onwards, keep original user text only
                            clean = _re.split(r'\n{0,2}[═]+\nWEB SEARCH RESULTS', content)[0].strip()
                            search_messages[i] = {"role": "user", "content": clean}

                # Replace the last user turn with the augmented version
                for i in range(len(search_messages) - 1, -1, -1):
                    if search_messages[i].get("role") == "user":
                        search_messages[i] = {"role": "user", "content": augmented_user_msg}
                        break

                # Rebuild ChatML prompt from the full search_messages array
                _search_prompt_parts = []
                for msg in search_messages:
                    role = msg.get("role", "user")
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        content = " ".join(p.get("text", "") for p in content if p.get("type") == "text").strip()
                    else:
                        content = content.strip()
                    _search_prompt_parts.append(f"<|im_start|>{role}\n{content}\n<|im_end|>")
                _search_prompt_parts.append("<|im_start|>assistant\n")
                _search_prompt = "\n".join(_search_prompt_parts[:-1]) + "\n" + _search_prompt_parts[-1]

                new_payload = dict(payload)
                new_payload["prompt"] = _search_prompt

                try:
                    _response_chunks = []
                    for chunk in stream_model_response(new_payload):
                        _response_chunks.append(chunk)
                        yield chunk
                    # Always append source link ourselves — never trust the model to do it
                    if has_results and _src:
                        _full_response = "".join(_response_chunks)
                        if _src not in _full_response:
                            yield f'\n\n<a href="{_src}" target="_blank" style="color:#7ab4f5;">🔗 Source: {_src}</a>'
                except Exception as e:
                    yield f"\n⚠️ Search error: {e}"

            try:
                resp = Response(
                    stream_with_context(_web_search_stream()),
                    content_type="text/event-stream; charset=utf-8",
                )
                if newly_pinned_doc:
                    resp.headers["X-Pinned-Doc"] = newly_pinned_doc
                return resp
            except Exception as e:
                print(f"❌ Web-search chat error: {e}", flush=True)
                return f"⚠️ Error: {e}", 500

        else:
            try:
                resp = Response(
                    stream_with_context(stream_model_response(payload)),
                    content_type="text/event-stream; charset=utf-8",
                )
                if newly_pinned_doc:
                    resp.headers['X-Pinned-Doc'] = newly_pinned_doc
                return resp
            except Exception as e:
                print(f"❌ Chat error: {e}", flush=True)
                return f"⚠️ Error contacting model: {e}", 500
        
# --------------------------------------------------
# Chat History Persistence (NEW SIDEBAR SYSTEM)
# --------------------------------------------------
@app.route('/save_chat', methods=['POST'])
def save_chat():
    """Save messages to the current sidebar chat file."""
    try:
        data = request.get_json()
        filename = data.get("filename")
        user_msg = data.get("user", "").strip()
        model_msg = data.get("model", "").strip()
        
        # ✅ SIMPLE DEBUG: Just show the message and newline count
        newline_count = model_msg.count('\n')
        print(f"\n🔍 SAVING MESSAGE:")
        print(f"   Filename: {filename}")
        print(f"   Newlines in model_msg: {newline_count}")
        print(f"   First 100 chars: {model_msg[:100]}...")
        print()
        
        if not filename:
            print("⚠️ No filename provided to /save_chat")
            return jsonify({"success": False, "error": "No filename provided"}), 400
        
        if not user_msg and not model_msg:
            return jsonify({"success": False, "error": "Empty message"}), 400
        
        filepath = os.path.join(CHAT_DIR, filename)
        
        # Preserve newlines in the model response
        model_msg_formatted = model_msg.replace('\\n', '\n')
        
        # Append messages to the chat file
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(f"User: {user_msg}\n\n")
            f.write(f"{character_name}: {model_msg_formatted}\n\n")
        
        print(f"💾 Chat saved to {filename}")
        return jsonify({"success": True})
        
    except Exception as e:
        print(f"❌ Failed to save chat: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/get_chat_history', methods=['GET'])
def get_chat_history():
    """Return all saved chat entries."""
    try:
        if not os.path.exists(CHAT_HISTORY_FILE):
            return jsonify([])

        with open(CHAT_HISTORY_FILE, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                data = []

        return jsonify(data)
    except Exception as e:
        print(f"❌ Failed to load chat history: {e}")
        return jsonify([])


# --------------------------------------------------
# System Prompt Route
# --------------------------------------------------
# --------------------------------------------------
# System Prompt Template Routes
# --------------------------------------------------

def get_system_prompts_dir():
    return os.path.join(os.path.dirname(__file__), 'system_prompts')

def get_active_prompt_filename():
    try:
        with open('settings.json', 'r', encoding='utf-8') as f:
            return json.load(f).get('active_system_prompt', 'default.txt')
    except Exception:
        return 'default.txt'

def set_active_prompt_filename(filename):
    try:
        with open('settings.json', 'r', encoding='utf-8') as f:
            s = json.load(f)
    except Exception:
        s = {}
    s['active_system_prompt'] = filename
    with open('settings.json', 'w', encoding='utf-8') as f:
        json.dump(s, f, indent=2)

@app.route('/system_prompts/list', methods=['GET'])
def list_system_prompts():
    folder = get_system_prompts_dir()
    os.makedirs(folder, exist_ok=True)
    files = sorted([f for f in os.listdir(folder) if f.endswith('.txt')])
    active = get_active_prompt_filename()
    return jsonify({'files': files, 'active': active})

@app.route('/system_prompts/load/<filename>', methods=['GET'])
def load_system_prompt_file(filename):
    if '..' in filename or '/' in filename or os.sep in filename:
        return jsonify({'error': 'Invalid filename'}), 400
    folder = get_system_prompts_dir()
    path = os.path.join(folder, filename)
    if not os.path.exists(path):
        return jsonify({'error': 'File not found'}), 404
    with open(path, 'r', encoding='utf-8') as f:
        return f.read(), 200, {'Content-Type': 'text/plain; charset=utf-8'}

@app.route('/system_prompts/save/<filename>', methods=['POST'])
def save_system_prompt_file(filename):
    if '..' in filename or '/' in filename or os.sep in filename:
        return jsonify({'error': 'Invalid filename'}), 400
    folder = get_system_prompts_dir()
    os.makedirs(folder, exist_ok=True)
    data = request.get_data(as_text=True)
    path = os.path.join(folder, filename)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(data)
    print(f'✅ Saved system prompt: {filename}')
    return jsonify({'status': 'saved', 'filename': filename})

@app.route('/system_prompts/activate/<filename>', methods=['POST'])
def activate_system_prompt(filename):
    if '..' in filename or '/' in filename or os.sep in filename:
        return jsonify({'error': 'Invalid filename'}), 400
    folder = get_system_prompts_dir()
    path = os.path.join(folder, filename)
    if not os.path.exists(path):
        return jsonify({'error': 'File not found'}), 404
    set_active_prompt_filename(filename)
    print(f'✅ Active system prompt set to: {filename}')
    return jsonify({'status': 'ok', 'active': filename})

@app.route('/system_prompts/delete/<filename>', methods=['POST'])
def delete_system_prompt(filename):
    if '..' in filename or '/' in filename or os.sep in filename:
        return jsonify({'error': 'Invalid filename'}), 400
    folder = get_system_prompts_dir()
    path = os.path.join(folder, filename)
    if not os.path.exists(path):
        return jsonify({'error': 'File not found'}), 404
    os.remove(path)
    # If deleted file was active, fall back to default.txt
    if get_active_prompt_filename() == filename:
        set_active_prompt_filename('default.txt')
    print(f'🗑️ Deleted system prompt: {filename}')
    return jsonify({'status': 'deleted'})

# Legacy route - kept for backwards compatibility
@app.route('/system_prompt.txt', methods=['GET', 'POST'])
def system_prompt():
    folder = get_system_prompts_dir()
    active = get_active_prompt_filename()
    file_path = os.path.join(folder, active)

    if request.method == 'POST':
        try:
            os.makedirs(folder, exist_ok=True)
            data = request.get_data(as_text=True)
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(data)
            print(f"✅ Saved active system prompt: {active}")
            return jsonify({'status': 'saved'})
        except Exception as e:
            print(f"❌ System prompt save failed: {e}")
            return jsonify({'error': str(e)}), 500

    if not os.path.exists(file_path):
        return jsonify({'error': 'Active system prompt file not found'}), 404

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            return content, 200, {'Content-Type': 'text/plain; charset=utf-8'}
    except Exception as e:
        print(f"❌ System prompt load failed: {e}")
        return jsonify({'error': str(e)}), 500
        
# --------------------------------------------------
# List Characters (for config dropdown)
# --------------------------------------------------
@app.route("/list_characters", methods=["GET"])
def list_characters():
    chars = []
    char_dir = os.path.join(os.path.dirname(__file__), "characters")
    if not os.path.exists(char_dir):
        print("⚠️ Characters directory not found:", char_dir)
        return jsonify([])

    for file in os.listdir(char_dir):
        if file.endswith(".json"):
            path = os.path.join(char_dir, file)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    name = data.get("name", file.replace(".json", ""))
                    chars.append(name)
            except Exception as e:
                print(f"⚠️ Failed to load {file}: {e}")
                continue

    print(f"✅ /list_characters -> {chars}")
    return jsonify(sorted(chars))
    
# --------------------------------------------------
# Create New Character
# --------------------------------------------------
@app.route("/create_character", methods=["POST"])
def create_character():
    try:
        data = request.get_json()
        name = data.get("name", "").strip()
        if not name:
            return jsonify({"status": "error", "error": "Character name required"}), 400

        char_dir = os.path.join(os.path.dirname(__file__), "characters")
        os.makedirs(char_dir, exist_ok=True)

        # Save the individual character file
        char_path = os.path.join(char_dir, f"{name}.json")
        char_data = {
            "name": name,
            "description": data.get("description", ""),
            "main_prompt": data.get("main_prompt", ""),
            "tagline": data.get("tagline", ""),
            "scenario": data.get("scenario", ""),
            "post_history": data.get("post_history", ""),
            "character_note": data.get("character_note", ""),
            "image": data.get("image", "")
        }
        with open(char_path, "w", encoding="utf-8") as f:
            json.dump(char_data, f, indent=2, ensure_ascii=False)

        # Update the characters index list
        index_path = os.path.join(char_dir, "index.json")
        if os.path.exists(index_path):
            with open(index_path, "r", encoding="utf-8") as f:
                characters = json.load(f)
            if name not in characters:
                characters.append(name)
        else:
            characters = [name]

        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(sorted(characters), f, indent=2, ensure_ascii=False)

        print(f"✅ Created new character: {name}")
        return jsonify({"status": "ok", "name": name})

    except Exception as e:
        print(f"❌ Error creating character: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500
        
# --------------------------------------------------
# Upload Character Image
# --------------------------------------------------
@app.route("/upload_image", methods=["POST"])
def upload_image():
    if "file" not in request.files:
        return jsonify({"error": "No file part"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No selected file"}), 400
    
    # Get name from form data — is_user flag distinguishes user profiles from characters
    char_name = request.form.get("character_name", "").strip()
    is_user = request.form.get("is_user", "false").lower() == "true"
    
    try:
        from PIL import Image
        import io
        
        # Open the uploaded image (works for JPG, PNG, WebP, etc)
        img = Image.open(file.stream)
        
        # Convert to RGB if needed (preserves transparency for PNGs)
        if img.mode in ('RGBA', 'LA'):
            # Keep alpha channel for transparent PNGs
            pass
        elif img.mode == 'P':
            # Convert palette mode to RGBA
            img = img.convert('RGBA')
        elif img.mode != 'RGB':
            img = img.convert('RGB')
        
        # Build filename (always .png now)
        # ⚠️ User profile images get a "user_" prefix to avoid collision with character images
        if char_name:
            clean_name = char_name.split('-')[0].split()[0].strip()
            if is_user:
                filename = f"user_{clean_name}.png"
            else:
                filename = f"{clean_name}.png"
        else:
            filename = "character.png"
        
        # Save as PNG
        save_dir = os.path.join(os.path.dirname(__file__), "static", "images")
        os.makedirs(save_dir, exist_ok=True)
        save_path = os.path.join(save_dir, filename)
        
        img.save(save_path, "PNG")
        
        print(f"✅ Image converted and saved as PNG: {save_path}")
        return jsonify({"status": "ok", "filename": filename})
        
    except Exception as e:
        print(f"❌ Failed to process image: {e}")
        return jsonify({"error": str(e)}), 500
        
# --------------------------------------------------
# Character Management
# --------------------------------------------------
@app.route('/characters/<path:filename>')
def serve_characters(filename):
    return send_from_directory('characters', filename)


@app.route('/characters/<n>.json', methods=['POST'])
def save_character(n):
    try:
        data = request.get_json()
        path = os.path.join("characters", f"{n}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"✅ Character saved: {path}")
        return jsonify({"success": True})
    except Exception as e:
        print(f"❌ Failed to save character {n}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/character_voice/<n>', methods=['GET'])
def get_character_voice(n):
    """Get the saved TTS voice for a character."""
    try:
        path = os.path.join("characters", f"{n}.json")
        if not os.path.exists(path):
            return jsonify({"voice": None})
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return jsonify({"voice": data.get("tts_voice", None)})
    except Exception as e:
        return jsonify({"voice": None})


@app.route('/character_voice/<n>', methods=['POST'])
def set_character_voice(n):
    """Save TTS voice for a character — only updates tts_voice field, leaves rest intact."""
    try:
        data = request.get_json()
        voice = data.get("voice", "")
        path = os.path.join("characters", f"{n}.json")
        if not os.path.exists(path):
            return jsonify({"success": False, "error": "Character not found"}), 404
        with open(path, "r", encoding="utf-8") as f:
            char_data = json.load(f)
        char_data["tts_voice"] = voice
        with open(path, "w", encoding="utf-8") as f:
            json.dump(char_data, f, indent=2, ensure_ascii=False)
        print(f"✅ Voice saved for {n}: {voice}")
        return jsonify({"success": True})
    except Exception as e:
        print(f"❌ Failed to save voice for {n}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# --------------------------------------------------
# User Persona Management
# --------------------------------------------------
@app.route('/users/<path:filename>')
def serve_user_files(filename):
    return send_from_directory(USERS_DIR, filename)



@app.route('/set_active_user', methods=['POST'])
def set_active_user():
    import tempfile, shutil
    data = request.get_json()
    selected = data.get('user')
    try:
        with open(os.path.join(USERS_DIR, "index.json"), "r", encoding="utf-8") as f:
            user_list = json.load(f)

        for name in user_list:
            path = os.path.join(USERS_DIR, f"{name}.json")
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as uf:
                        udata = json.load(uf)
                except Exception:
                    udata = {"name": name}
                udata["active"] = (name == selected)
                # Atomic write - safer than r+/seek/truncate
                dir_ = os.path.dirname(os.path.abspath(path))
                with tempfile.NamedTemporaryFile("w", dir=dir_, delete=False,
                                                 suffix=".tmp", encoding="utf-8") as tf:
                    json.dump(udata, tf, indent=2, ensure_ascii=False)
                    tmp_path = tf.name
                shutil.move(tmp_path, path)

        print(f"[INFO] Active user set to: {selected}")
        return jsonify({"success": True, "active": selected})

    except Exception as e:
        print(f"[ERROR] Failed to set active user: {e}")
        return jsonify({"success": False, "error": str(e)})



# --------------------------------------------------
# User Persona Editing
# --------------------------------------------------
@app.route('/get_user/<n>', methods=['GET'])
def get_user(n):
    """Return a user's persona details."""
    path = os.path.join(USERS_DIR, f"{n}.json")
    if not os.path.exists(path):
        return jsonify({"error": f"User '{n}' not found"}), 404
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Return only safe fields
        return jsonify({
            "name": data.get("name", n),
            "display_name": data.get("display_name", n),
            "bio": data.get("bio", ""),
            "image": data.get("image", "")
        })
    except Exception as e:
        print(f"Failed to load user {n}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/save_user/<n>', methods=['POST'])
def save_user(n):
    """Save updated persona info. Uses atomic write to prevent zero-byte corruption."""
    import tempfile, shutil
    try:
        payload = request.get_json()
        path = os.path.join(USERS_DIR, f"{n}.json")
        # Read existing data so we never lose fields (active flag, image, etc.)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {"name": n}
        else:
            data = {"name": n}
        data["display_name"] = payload.get("display_name", n)
        data["bio"] = payload.get("bio", "")
        # Only overwrite image if a new one was explicitly provided
        if "image" in payload and payload["image"]:
            data["image"] = payload["image"]
            print(f"Saving user image: {payload['image']}")
        # Atomic write: write to temp file then rename
        # Prevents zero-byte corruption if process is killed mid-write
        dir_ = os.path.dirname(os.path.abspath(path))
        with tempfile.NamedTemporaryFile("w", dir=dir_, delete=False,
                                         suffix=".tmp", encoding="utf-8") as tf:
            json.dump(data, tf, indent=2, ensure_ascii=False)
            tmp_path = tf.name
        shutil.move(tmp_path, path)
        print(f"Updated user persona: {n}")
        return jsonify({"status": "ok"})
    except Exception as e:
        print(f"Failed to save user {n}: {e}")
        return jsonify({"error": str(e)}), 500
        
@app.route("/get_all_users", methods=["GET"])
def get_all_users():
    """Return a dict of all users with their image filenames."""
    try:
        with open(os.path.join(USERS_DIR, "index.json"), "r", encoding="utf-8") as f:
            user_list = json.load(f)
        result = {}
        for name in user_list:
            path = os.path.join(USERS_DIR, f"{name}.json")
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as uf:
                    data = json.load(uf)
                result[name] = data.get("image", "default.png") or "default.png"
        return jsonify(result)
    except Exception as e:
        print(f"⚠️ Failed to load all users: {e}")
        return jsonify({})

@app.route("/get_model", methods=["GET"])
def get_model():
    """Return the currently loaded model name."""
    get_current_model()  # refresh from llama.cpp
    name = CURRENT_MODEL or "No model loaded"
    # Strip path and extension for display — just the filename stem
    display = os.path.splitext(os.path.basename(name))[0] if name else "No model loaded"
    return jsonify({"model": display})

# --------------------------------------------------
# Llama.cpp Process Management
# --------------------------------------------------

llama_process = None  # Track the managed llama.cpp process

def get_llama_settings():
    """Read llama settings fresh from settings.json each time."""
    try:
        with open('settings.json', 'r') as f:
            s = json.load(f)
        return {
            'exe': s.get('llama_server_exe', ''),
            'models_dir': s.get('llama_models_dir', ''),
            'args': s.get('llama_args', {}),
            'show_console': s.get('llama_show_console', False),
            'mmproj_path': s.get('mmproj_path', '')
        }
    except Exception as e:
        print(f"❌ Failed to read llama settings: {e}")
        return None

def kill_llama_process():
    """Kill any running llama-server process."""
    global llama_process
    # Kill our tracked process first
    if llama_process and llama_process.poll() is None:
        try:
            parent = psutil.Process(llama_process.pid)
            for child in parent.children(recursive=True):
                child.kill()
            parent.kill()
            print("✅ Killed tracked llama process")
        except Exception as e:
            print(f"⚠️ Error killing tracked process: {e}")
        llama_process = None
    # Also kill any stray llama-server.exe processes
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            if 'llama-server' in proc.info['name'].lower():
                proc.kill()
                print(f"✅ Killed stray llama-server PID {proc.info['pid']}")
        except Exception:
            pass

@app.route("/list_models", methods=["GET"])
def list_models():
    """List all .gguf files in the configured models directory."""
    cfg = get_llama_settings()
    if not cfg or not cfg['models_dir']:
        return jsonify({"error": "models_dir not configured in settings.json", "models": []})
    models_dir = cfg['models_dir']
    if not os.path.isdir(models_dir):
        return jsonify({"error": f"Models folder not found: {models_dir}", "models": []})
    models = sorted([f for f in os.listdir(models_dir) if f.lower().endswith('.gguf')])
    return jsonify({"models": models})

@app.route("/load_model", methods=["POST"])
def load_model():
    """Kill current llama.cpp process and start a new one with the selected model."""
    global llama_process
    data = request.json
    model_file = data.get("model")
    if not model_file:
        return jsonify({"status": "error", "error": "No model specified"})

    cfg = get_llama_settings()
    if not cfg:
        return jsonify({"status": "error", "error": "Could not read settings.json"})

    exe = cfg['exe']
    models_dir = cfg['models_dir']
    args = cfg['args']

    if not exe or not os.path.isfile(exe):
        return jsonify({"status": "error", "error": f"llama-server.exe not found at: {exe}"})

    model_path = os.path.join(models_dir, model_file)
    if not os.path.isfile(model_path):
        return jsonify({"status": "error", "error": f"Model file not found: {model_path}"})

    # Kill existing process
    print(f"🔄 Switching model to: {model_file}")
    kill_llama_process()
    time.sleep(1)

    # Build command
    cmd = [
        exe,
        "-m", model_path,
        "--port", str(args.get("port", 8080)),
        "--n-gpu-layers", str(args.get("n_gpu_layers", 44)),
        "--ctx-size", str(args.get("ctx_size", 16384)),
        "--cache-type-k", str(args.get("cache_type_k", "q8_0")),
        "--cache-type-v", str(args.get("cache_type_v", "q8_0")),
        "--timeout", str(args.get("timeout", 0)),
        "--chat-template", str(args.get("chat_template", "chatml")),
        "--parallel", str(args.get("parallel", 1)),
    ]
    mmproj_path = cfg.get('mmproj_path', '')
    if mmproj_path and os.path.isfile(mmproj_path):
        cmd += ["--mmproj", mmproj_path]
        print(f"🖼️ Vision mode: mmproj loaded from {mmproj_path}")

    try:
        show_console = cfg.get('show_console', False)
        llama_process = subprocess.Popen(
            cmd,
            stdout=None if show_console else subprocess.DEVNULL,
            stderr=None if show_console else subprocess.DEVNULL,
            creationflags=(subprocess.CREATE_NEW_CONSOLE if show_console else subprocess.CREATE_NO_WINDOW) if os.name == 'nt' else 0
        )
        print(f"✅ Launched llama-server PID {llama_process.pid} with {model_file}")
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)})

    # Wait for server to come up (poll /v1/models for up to 30s)
    for _ in range(30):
        time.sleep(1)
        try:
            r = requests.get(f"{API_URL}/v1/models", timeout=2)
            if r.status_code == 200:
                get_current_model()
                display = os.path.splitext(os.path.basename(CURRENT_MODEL))[0] if CURRENT_MODEL else model_file
                print(f"✅ Model ready: {display}")
                # Remember this model for next startup
                try:
                    with open('settings.json', 'r') as f:
                        s = json.load(f)
                    s['llama_last_model'] = model_file
                    with open('settings.json', 'w') as f:
                        json.dump(s, f, indent=2)
                except Exception:
                    pass
                return jsonify({"status": "ok", "model": display})
        except Exception:
            pass

    return jsonify({"status": "error", "error": "llama-server started but not responding after 30s"})

@app.route("/unload_model", methods=["POST"])
def unload_model():
    """Kill the llama.cpp process."""
    global CURRENT_MODEL
    kill_llama_process()
    CURRENT_MODEL = None
    return jsonify({"status": "ok"})

@app.route("/get_llama_config", methods=["GET"])
def get_llama_config():
    """Return current llama settings for the config page."""
    cfg = get_llama_settings()
    if not cfg:
        return jsonify({"error": "Could not read settings"})
    return jsonify(cfg)

@app.route("/browse_file", methods=["POST"])
def browse_file():
    """Open a native Windows file picker and return the selected path."""
    try:
        import subprocess, tempfile
        script = (
            'Add-Type -AssemblyName System.Windows.Forms;'
            '$d = New-Object System.Windows.Forms.OpenFileDialog;'
            '$d.Filter = "Executables (*.exe)|*.exe|All Files (*.*)|*.*";'
            'if ($d.ShowDialog() -eq "OK") { Write-Output $d.FileName }'
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True, text=True, timeout=60
        )
        path = result.stdout.strip()
        if path:
            return jsonify({"path": path})
        return jsonify({"path": None})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/browse_folder", methods=["POST"])
def browse_folder():
    """Open a native Windows folder picker and return the selected path."""
    try:
        import subprocess
        script = (
            'Add-Type -AssemblyName System.Windows.Forms;'
            '$d = New-Object System.Windows.Forms.FolderBrowserDialog;'
            '$d.Description = "Select Folder";'
            'if ($d.ShowDialog() -eq "OK") { Write-Output $d.SelectedPath }'
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True, text=True, timeout=60
        )
        path = result.stdout.strip()
        if path:
            return jsonify({"path": path})
        return jsonify({"path": None})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/reset_settings_to_default", methods=["POST"])
def reset_settings_to_default():
    """Overwrite settings.json with settings.default.json."""
    try:
        import shutil
        if not os.path.exists('settings.default.json'):
            return jsonify({"status": "error", "error": "settings.default.json not found"}), 404
        shutil.copy('settings.default.json', 'settings.json')
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/save_llama_config", methods=["POST"])
def save_llama_config():
    """Save llama settings to settings.json."""
    try:
        data = request.json
        with open('settings.json', 'r') as f:
            s = json.load(f)
        s['llama_server_exe'] = data.get('exe', '')
        s['llama_models_dir'] = data.get('models_dir', '')
        s['llama_show_console'] = data.get('show_console', False)
        s['llama_args'] = data.get('args', {})
        s['mmproj_path'] = data.get('mmproj_path', '')
        with open('settings.json', 'w') as f:
            json.dump(s, f, indent=2)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)})


@app.route('/get_active_user', methods=['GET'])
def get_active_user():
    try:
        with open(os.path.join(USERS_DIR, "index.json"), "r", encoding="utf-8") as f:
            user_list = json.load(f)
        
        # Find the user marked as active
        for name in user_list:
            path = os.path.join(USERS_DIR, f"{name}.json")
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as uf:
                    data = json.load(uf)
                    if data.get("active"):
                        return jsonify({"active_user": name})
        
        # Fallback to first user if none marked active
        if user_list:
            return jsonify({"active_user": user_list[0]})
        
        return jsonify({"active_user": None})
        
    except Exception as e:
        print(f"⚠️ Failed to load active user: {e}")
        return jsonify({"active_user": None})
        
        
# --------------------------------------------------
# Per-Character Chat Saving & Loading
# --------------------------------------------------
import os, json
from flask import jsonify, request

CHAT_DIR = os.path.join(os.path.dirname(__file__), "chats")
USERS_DIR = os.path.join(os.path.dirname(__file__), "users")
os.makedirs(CHAT_DIR, exist_ok=True)


@app.route('/save_chat_character/<n>', methods=['POST'])
def save_chat_character(n):
    """Save chat for a specific character (no route conflict)."""
    try:
        data = request.get_json(force=True)
        os.makedirs(CHAT_DIR, exist_ok=True)  # ✅
        path = os.path.join(CHAT_DIR, f"{n}.json")  # ✅

        history = []
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                try:
                    history = json.load(f)
                except json.JSONDecodeError:
                    history = []

        history.append({
            "user": data.get("user", ""),
            "model": data.get("model", "")
        })

        with open(path, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)

        print(f"💾 Chat saved for {n} ({len(history)} total)")
        return jsonify({"status": "ok"})
    except Exception as e:
        print(f"❌ Failed to save chat for {n}: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route('/clear_chat/<n>', methods=['POST'])
def clear_chat(n):
    """Delete chat contents for one character."""
    try:
        path = os.path.join(CHAT_DIR, f"{n}.json")  # ✅
        if os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                json.dump([], f)
        print(f"🧹 Cleared chat for {n}")
        return jsonify({"status": "ok"})
    except Exception as e:
        print(f"❌ Failed to clear chat for {n}: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/get_chat_history/<character>", methods=["GET"])
def get_chat_history_character(character):
    """Return chat history for a specific character."""
    try:
        chat_file = os.path.join(CHAT_DIR, f"{character}.json")
        if not os.path.exists(chat_file):
            return jsonify([])

        with open(chat_file, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                data = []
        return jsonify(data)
    except Exception as e:
        print(f"❌ Failed to load chat history for {character}: {e}")
        return jsonify([])
        
# --------------------------------------------------
# Manual Chat Export (Save Chat to Text File)
# --------------------------------------------------
@app.route("/save_chat_manual", methods=["POST"])
def save_chat_manual():
    """Save visible chat to text file, with optional custom title."""
    try:
        data = request.get_json(force=True)
        char_name = data.get("character", "default").strip()
        title = data.get("title", "").strip()
        import datetime, glob, re
        
        # Sanitize the title for filesystem use
        safe_title = re.sub(r"[^A-Za-z0-9_\s-]+", "", title).strip() if title else None
        
        os.makedirs("chats", exist_ok=True)
        
        # Build filename: Character - Title.txt or Character - Timestamp.txt
        if safe_title:
            # User provided a title: "Gem - My Custom Title.txt"
            base_name = f"{char_name} - {safe_title}"
        else:
            # No title: "Gem - 2025-12-29 13-45.txt"
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H-%M")
            base_name = f"{char_name} - {timestamp}"
        
        # Check if file exists, add counter if needed
        file_path = os.path.join("chats", f"{base_name}.txt")
        counter = 1
        while os.path.exists(file_path):
            file_path = os.path.join("chats", f"{base_name} ({counter}).txt")
            counter += 1
        
        filename = os.path.basename(file_path)
        
        content = data.get("content", "").strip()
        
        # ✅ PRESERVE NEWLINES - replace escaped newlines with real ones
        content_formatted = content.replace('\\n', '\n')
        
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content_formatted)
        
        print(f"💾 Exported chat: {file_path}")
        return jsonify({"status": "ok", "filename": filename})
        
    except Exception as e:
        print(f"❌ Failed to export chat: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500
        
# --------------------------------------------------
# Character Memories
# --------------------------------------------------

def load_memories_for_character(character_name):
    """Load and parse the memory file for a specific character."""
    if not character_name:
        print("⚠️ No character name provided.")
        return []

    base_dir = os.path.dirname(__file__)
    memory_dir = os.path.join(base_dir, "memories")

    filename = f"{character_name.lower()}_memory.txt"
    file_path = os.path.join(memory_dir, filename)

    if not os.path.exists(file_path):
        print(f"⚠️ No memory file found for {character_name} at {file_path}")
        return []

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        print(f"❌ Error reading memory file: {e}")
        return []

    blocks = content.split("# Memory:")
    memories = []
    for block in blocks:
        if not block.strip():
            continue
        lines = block.strip().splitlines()
        title = lines[0].strip() if lines else "Untitled"
        body_lines = []
        keywords = []

        for line in lines[1:]:
            if line.lower().startswith("keywords:"):
                keywords = [kw.strip().lower() for kw in re.split(r"[,:;]+", line.split(":", 1)[1]) if kw.strip()]
            else:
                body_lines.append(line.strip())

        memories.append({
            "title": title,
            "body": " ".join(body_lines).strip(),
            "keywords": keywords
        })

    print(f"✅ Loaded {len(memories)} memory blocks for {character_name}.")
    return memories


# --------------------------------------------------
# Fetch Character Memories
# --------------------------------------------------
def fetch_character_memories(prompt, character_name, max_matches=2):
    """Return relevant memory paragraphs for the given character and input."""
    if not character_name:
        print("⚠️ No character name provided to fetch_character_memories.")
        return ""

    prompt_lower = prompt.lower()
    memories = load_memories_for_character(character_name)
    matches = []

    for mem in memories:
        if any(k in prompt_lower for k in mem["keywords"]):
            matches.append(f"[{mem['title']}]\n{mem['body']}")

    if matches:
        print(f"🧠 Matched {len(matches)} memory block(s) for {character_name}: {[m['title'] for m in memories if any(k in prompt_lower for k in m['keywords'])]}")
        return "\n\n".join(matches[:max_matches])
    else:
        print(f"ℹ️ No memory match found for {character_name}.")
        return ""
# --------------------------------------------------
# SUMMARIZE MEMORY FUNCTION
# --------------------------------------------------


# --------------------------------------------------
# Sampling Settings Management (UNIFIED)
# --------------------------------------------------
SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "settings.json")

def load_sampling_settings():
    """Load current sampling settings from settings.json or create defaults."""
    defaults = {
        "temperature": 0.8,
        "max_tokens": 4096,
        "top_p": 0.95,
        "min_p": 0.05,
        "top_k": 40,
        "repeat_penalty": 1.1
    }
 
    
    if not os.path.exists(SETTINGS_FILE):
        # Create file with defaults if missing
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(defaults, f, indent=2)
        return defaults
    
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ Failed to load settings.json: {e}")
        return defaults

@app.route("/get_sampling_settings", methods=["GET"])
def get_sampling_settings():
    return jsonify(load_sampling_settings())

@app.route("/save_sampling_settings", methods=["POST"])
def save_sampling_settings():
    data = request.get_json()
    # Read existing settings first so we don't wipe llama paths / other fields
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            existing = json.load(f)
    except Exception:
        existing = {}
    existing.update(data)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2)
    print("✅ Sampling settings saved:", data)
    return jsonify({"status": "ok"})


# --------------------------------------------------
# Current Situation Routes
# --------------------------------------------------
@app.route("/get_current_situation", methods=["GET"])
def get_current_situation():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            s = json.load(f)
        return jsonify({"current_situation": s.get("current_situation", "")})
    except Exception as e:
        return jsonify({"current_situation": "", "error": str(e)})

@app.route("/save_current_situation", methods=["POST"])
def save_current_situation():
    data = request.get_json()
    situation = data.get("current_situation", "").strip()
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            s = json.load(f)
        s["current_situation"] = situation
        import tempfile, shutil
        tmp = SETTINGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2)
        shutil.move(tmp, SETTINGS_FILE)
        print(f"✅ Current situation saved ({len(situation)} chars)")
        return jsonify({"status": "ok"})
    except Exception as e:
        print(f"❌ save_current_situation failed: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500



@app.route("/get_brave_api_key", methods=["GET"])
def get_brave_api_key_route():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            s = json.load(f)
        return jsonify({"brave_api_key": s.get("brave_api_key", "")})
    except Exception as e:
        return jsonify({"brave_api_key": "", "error": str(e)})

@app.route("/save_brave_api_key", methods=["POST"])
def save_brave_api_key_route():
    data = request.get_json()
    key = data.get("brave_api_key", "").strip()
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            s = json.load(f)
        s["brave_api_key"] = key
        import tempfile, shutil
        tmp = SETTINGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2)
        shutil.move(tmp, SETTINGS_FILE)
        print(f"✅ Brave API key saved ({len(key)} chars)")
        return jsonify({"status": "ok"})
    except Exception as e:
        print(f"❌ save_brave_api_key failed: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500


# --------------------------------------------------
# Theme Routes
# --------------------------------------------------
THEMES_DIR = os.path.join(os.path.dirname(__file__), "themes")

def get_active_theme_name():
    """Get active theme name from settings.json, default to 'midnight'."""
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            s = json.load(f)
        return s.get("active_theme", "midnight")
    except:
        return "midnight"

def set_active_theme_name(name):
    """Write active theme name to settings.json."""
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            s = json.load(f)
        s["active_theme"] = name
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2)
    except Exception as e:
        print(f"❌ set_active_theme_name failed: {e}")

def get_active_theme_path():
    name = get_active_theme_name()
    os.makedirs(THEMES_DIR, exist_ok=True)
    return os.path.join(THEMES_DIR, f"{name}.css")

@app.route("/get_theme", methods=["GET"])
def get_theme():
    """Read CSS custom properties from the active theme file."""
    try:
        path = get_active_theme_path()
        if not os.path.exists(path):
            # Migration: fall back to legacy theme.css or style.css
            for fallback in ["theme.css", "style.css"]:
                fb = os.path.join(os.path.dirname(__file__), fallback)
                if os.path.exists(fb):
                    path = fb
                    break
        with open(path, "r", encoding="utf-8") as f:
            css = f.read()
        vars_dict = {}
        for match in re.finditer(r'(--[\w-]+)\s*:\s*([^;]+);', css):
            vars_dict[match.group(1).strip()] = match.group(2).strip()
        return jsonify(vars_dict)
    except Exception as e:
        print(f"❌ get_theme failed: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/save_theme", methods=["POST"])
def save_theme():
    """Write updated CSS custom properties to the active theme file only."""
    try:
        data = request.get_json()
        path = get_active_theme_path()
        print(f"💾 save_theme: writing to {path}, {len(data)} variables")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                css = f.read()
        else:
            print(f"⚠️  save_theme: file not found, creating new")
            css = ":root {\n}\n"
        for var, value in data.items():
            if re.search(re.escape(var) + r'\s*:', css):
                css = re.sub(
                    r'(' + re.escape(var) + r'\s*:\s*)([^;]+)(;)',
                    lambda m, v=value: m.group(1) + v + m.group(3),
                    css
                )
            else:
                css = css.rstrip()
                if css.endswith('}'):
                    css = css[:-1].rstrip() + f"\n  {var}: {value};\n}}\n"
        with open(path, "w", encoding="utf-8") as f:
            f.write(css)
        with open(path, "r", encoding="utf-8") as f:
            verify = f.read()
        print(f"✅ Theme saved to {os.path.basename(path)}: {len(data)} vars, file now {len(verify)} chars")
        return jsonify({"status": "ok"})
    except Exception as e:
        print(f"❌ save_theme failed: {e}")
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route("/themes/list", methods=["GET"])
def list_themes():
    """List all available theme files."""
    try:
        os.makedirs(THEMES_DIR, exist_ok=True)
        themes = sorted([f[:-4] for f in os.listdir(THEMES_DIR) if f.endswith('.css')])
        return jsonify({"themes": themes, "active": get_active_theme_name()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/themes/switch", methods=["POST"])
def switch_theme():
    """Switch active theme."""
    try:
        name = request.get_json().get("name", "").strip()
        if not name or not re.match(r'^[\w\- ]+$', name):
            return jsonify({"error": "Invalid theme name"}), 400
        path = os.path.join(THEMES_DIR, f"{name}.css")
        if not os.path.exists(path):
            return jsonify({"error": f"Theme '{name}' not found"}), 404
        set_active_theme_name(name)
        print(f"✅ Switched to theme: {name}")
        return jsonify({"status": "ok", "active": name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/themes/create", methods=["POST"])
def create_theme():
    """Create a new theme by copying the active theme."""
    try:
        name = request.get_json().get("name", "").strip()
        print(f"🎨 create_theme: name='{name}', THEMES_DIR={THEMES_DIR}")
        if not name or not re.match(r'^[\w\- ]+$', name):
            print(f"❌ create_theme: invalid name rejected")
            return jsonify({"error": "Invalid theme name"}), 400
        os.makedirs(THEMES_DIR, exist_ok=True)
        new_path = os.path.join(THEMES_DIR, f"{name}.css")
        print(f"🎨 create_theme: new_path={new_path}")
        if os.path.exists(new_path):
            return jsonify({"error": f"Theme '{name}' already exists"}), 400
        src = get_active_theme_path()
        print(f"🎨 create_theme: src={src}, exists={os.path.exists(src)}")
        if os.path.exists(src):
            import shutil
            shutil.copy2(src, new_path)
        else:
            with open(new_path, "w", encoding="utf-8") as f:
                f.write(":root {\n}\n")
        # Do NOT auto-switch — just create the file
        print(f"✅ Created theme file: {new_path}")
        return jsonify({"status": "ok", "created": name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/themes/delete", methods=["POST"])
def delete_theme():
    """Delete a theme file."""
    try:
        name = request.get_json().get("name", "").strip()
        if not name or not re.match(r'^[\w\- ]+$', name):
            return jsonify({"error": "Invalid theme name"}), 400
        path = os.path.join(THEMES_DIR, f"{name}.css")
        if not os.path.exists(path):
            return jsonify({"error": "Theme not found"}), 404
        os.remove(path)
        if get_active_theme_name() == name:
            remaining = sorted([f[:-4] for f in os.listdir(THEMES_DIR) if f.endswith('.css')])
            set_active_theme_name(remaining[0] if remaining else "midnight")
        print(f"✅ Deleted theme: {name}")
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

THEME_PRESETS_FILE = "theme_presets.json"

def load_theme_presets():
    if os.path.exists(THEME_PRESETS_FILE):
        with open(THEME_PRESETS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_theme_presets(presets):
    with open(THEME_PRESETS_FILE, "w", encoding="utf-8") as f:
        json.dump(presets, f, indent=2)

@app.route("/theme_presets", methods=["GET"])
def get_theme_presets():
    return jsonify(load_theme_presets())

@app.route("/theme_presets/save", methods=["POST"])
def save_theme_preset():
    try:
        data = request.get_json()
        name = data.get("name", "").strip()
        colours = data.get("colours", {})
        if not name:
            return jsonify({"error": "No name provided"}), 400
        presets = load_theme_presets()
        presets[name] = colours
        save_theme_presets(presets)
        print(f"✅ Theme preset saved: {name}")
        return jsonify({"status": "ok"})
    except Exception as e:
        print(f"❌ save_theme_preset failed: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/theme_presets/delete", methods=["POST"])
def delete_theme_preset():
    try:
        data = request.get_json()
        name = data.get("name", "").strip()
        presets = load_theme_presets()
        if name in presets:
            del presets[name]
            save_theme_presets(presets)
            print(f"🗑️ Theme preset deleted: {name}")
        return jsonify({"status": "ok"})
    except Exception as e:
        print(f"❌ delete_theme_preset failed: {e}")
        return jsonify({"error": str(e)}), 500


# Static + Template Routes
# --------------------------------------------------
@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory(app.static_folder, filename)


@app.route('/')
def root():
    return render_template('index.html')


@app.route('/config')
def config_page():
    return render_template('config.html')


@app.route('/mobile')
def mobile_page():
    return render_template('mobile.html')


# --------------------------------------------------
# Continue Endpoint (fixed continuation logic)
# --------------------------------------------------
@app.route("/continue", methods=["POST"])
def continue_chat():
    print("✅ Continue route hit")

    try:
        data = request.get_json(force=True)
        last_response = data.get("last_response", "")
        character = data.get("character", "")
        memory_context = data.get("memory_context", "")

        # Load system prompt from active template
        from utils.session_handler import get_active_system_prompt_path
        _sp_path = get_active_system_prompt_path()
        try:
            with open(_sp_path, "r", encoding="utf-8") as sp:
                system_prompt = sp.read().strip()
        except Exception:
            system_prompt = "You are an LLM-based assistant."

        # Load character main prompt
        char_file = os.path.join("characters", f"{character}.json")
        char_main = ""
        if os.path.exists(char_file):
            with open(char_file, "r", encoding="utf-8") as cf:
                char_main = json.load(cf).get("main_prompt", "")

        # Combine system + character prompt
        system_full = f"{system_prompt}\n\n{char_main}".strip()

        # Build proper continuation prompt
        messages = [
            {"role": "system", "content": system_full},
            {
                "role": "user",
                "content": (
                    f"{memory_context}\n\n"
                    f"The model's last reply was cut off. Resume it naturally, starting from this partial text:\n\n"
                    f"---\n{last_response}\n---\n\n"
                    "Continue seamlessly in the same tone and context."
                )
            }
        ]
        # Trim context before sending to llama.cpp
        messages = trim_chat_history(messages)
        if len(messages) == MAX_MESSAGES:
            print("[TrimCheck] Oldest messages trimmed.")

        print(f"[TrimCheck] Sending {len(messages)} messages to model "
              f"({sum(rough_token_count(m.get('content','')) for m in messages)} tokens approx)")

        payload = {
            "model": CURRENT_MODEL or "local",
            "messages": messages,
            "temperature": 0.8,
            "max_tokens": 2048
        }




        print("📤 Sending continuation payload to model...")

        # ❌ Disable duplicate POST to model
        # r = requests.post(f"{API_URL}/v1/chat/completions", json=payload, timeout=300)
        # r.raise_for_status()
        # result = r.json()
        # content = result["choices"][0]["message"]["content"]

        print("✅ Continuation skipped (stream handled by /chat).")
        return jsonify({"response": "(continuation handled via main stream)"}), 200

    except Exception as e:
        print(f"⚠️ Continue endpoint error: {e}")
        return jsonify({"error": str(e)}), 500

      
# --- CHARACTER MEMORY MANAGEMENT ---
@app.route("/get_character_memory")
def get_character_memory():
    """Return parsed memory entries for the selected character or global."""
    character = request.args.get("character")
    if not character:
        return jsonify({"entries": []})

    mem_dir = os.path.join(os.path.dirname(__file__), "memories")
    os.makedirs(mem_dir, exist_ok=True)
    if character.lower() == "global":
        path = os.path.join(mem_dir, "global_memory.txt")
    else:
        path = os.path.join(mem_dir, f"{character.lower()}_memory.txt")
    print("Looking for memory file:", path)

    if not os.path.exists(path):
        print("⚠️ Memory file not found.")
        return jsonify({"entries": []})

    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read().strip()
    except Exception as e:
        print("❌ Error reading memory file:", e)
        return jsonify({"entries": []})

    # Split on "# Memory:" and filter out empties
    blocks = [b.strip() for b in text.split("# Memory:") if b.strip()]

    print(f"✅ Loaded {len(blocks)} memory blocks for {character}.")
    return jsonify({"entries": blocks})


@app.route("/delete_character_memory", methods=["POST"])
def delete_character_memory():
    """Delete a memory entry by index for the selected character."""
    data = request.get_json()
    character = data.get("character")
    index = int(data.get("index", -1))

    if character is None or index < 0:
        return "Invalid request", 400

    mem_dir = os.path.join(os.path.dirname(__file__), "memories")
    if character.lower() == "global":
        path = os.path.join(mem_dir, "global_memory.txt")
    else:
        path = os.path.join(mem_dir, f"{character.lower()}_memory.txt")
    if not os.path.exists(path):
        return "No memory file", 404

    with open(path, "r", encoding="utf-8") as f:
        blocks = [b for b in f.read().split("# Memory:") if b.strip()]

    if 0 <= index < len(blocks):
        del blocks[index]
    else:
        return "Index out of range", 400

    new_text = "\n\n".join(f"# Memory: {b.strip()}" for b in blocks)
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_text.strip())

    return "OK", 200


@app.route("/add_character_memory", methods=["POST"])
def add_character_memory():
    """Append a new memory entry for the selected character."""
    data = request.get_json()
    character = data.get("character", "").strip()
    title = data.get("title", "Untitled").strip()
    keywords = data.get("keywords", "").strip()
    body = data.get("body", "").strip()

    if not character or not body:
        return "Invalid request", 400

    mem_dir = os.path.join(os.path.dirname(__file__), "memories")
    os.makedirs(mem_dir, exist_ok=True)
    if character.lower() == "global":
        path = os.path.join(mem_dir, "global_memory.txt")
    else:
        path = os.path.join(mem_dir, f"{character.lower()}_memory.txt")

    entry = f"# Memory: {title}\nKeywords: {keywords}\n\n{body}\n\n"

    with open(path, "a", encoding="utf-8") as f:
        f.write(entry)

    return "OK", 200


@app.route("/edit_character_memory", methods=["POST"])
def edit_character_memory():
    """Replace a memory entry by index for the selected character.
    Frontend sends 'content' (the full block text shown in the textarea).
    We preserve title and keywords lines, only updating the body portion.
    """
    data = request.get_json()
    character = data.get("character", "").strip()
    index = int(data.get("index", -1))
    # Frontend sends 'content' — accept both 'content' and 'body' for safety
    new_content = (data.get("content") or data.get("body") or "").strip()

    if not character or index < 0 or not new_content:
        return "Invalid request", 400

    mem_dir = os.path.join(os.path.dirname(__file__), "memories")
    if character.lower() == "global":
        path = os.path.join(mem_dir, "global_memory.txt")
    else:
        path = os.path.join(mem_dir, f"{character.lower()}_memory.txt")
    if not os.path.exists(path):
        return "No memory file", 404

    with open(path, "r", encoding="utf-8") as f:
        blocks = [b for b in f.read().split("# Memory:") if b.strip()]

    if not (0 <= index < len(blocks)):
        return "Index out of range", 400

    # The textarea shows the full block (title line + Keywords line + body).
    # Split out title and keywords from the new_content so we preserve structure.
    lines = new_content.splitlines()
    title_line = ""
    keywords_line = ""
    body_lines = []
    for i, line in enumerate(lines):
        if i == 0 and not line.lower().startswith("keywords:"):
            title_line = line.strip()
        elif line.lower().startswith("keywords:"):
            keywords_line = line.strip()
        else:
            body_lines.append(line)

    # Rebuild the block cleanly
    rebuilt = f" {title_line}\n"
    if keywords_line:
        rebuilt += f"{keywords_line}\n"
    rebuilt += "\n" + "\n".join(body_lines).strip()

    blocks[index] = rebuilt

    new_text = "\n\n".join(f"# Memory:{b}" for b in blocks)
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_text.strip())

    return "OK", 200


# --------------------------------------------------
# Delete Last N Messages from Chat History (baseline version)
# --------------------------------------------------
@app.route('/delete_last_messages/<path:character>', methods=['POST'])
def delete_last_messages(character):
    character = character.lower()
    count = int(request.args.get("count", 2))
    chat_path = os.path.join("chats", f"{character}.json")

    try:
        if not os.path.exists(chat_path):
            return jsonify({"error": f"No chat found at {chat_path}"}), 404

        # Load file (try JSON first, fallback to plain text lines)
        with open(chat_path, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                f.seek(0)
                lines = f.readlines()
                lines = lines[:-count] if len(lines) > count else []
                with open(chat_path, "w", encoding="utf-8") as fw:
                    fw.writelines(lines)
                print(f"🗑️ Deleted last {count} lines for {character} ({chat_path})")
                return jsonify({"status": "ok"}), 200

        # If it’s valid JSON and a simple list of messages
        if isinstance(data, list):
            data = data[:-count] if len(data) > count else []

        # Write back
        with open(chat_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        print(f"🗑️ Deleted last {count} item(s) for {character} ({chat_path})")
        return jsonify({"status": "ok"}), 200

    except Exception as e:
        print(f"❌ delete_last_messages error: {e}")
        return jsonify({"error": str(e)}), 500


# --------------------------------------------------
# Delete Last N Messages from Chat History (safe JSON version)
# --------------------------------------------------
@app.route('/delete_last_messages/<path:character>', methods=['POST'])
def delete_last_messages_safe(character):
    character = character.lower()
    count = int(request.args.get("count", 2))
    chat_path = os.path.join("chats", f"{character}.json")

    try:
        if not os.path.exists(chat_path):
            return jsonify({"error": f"No chat found at {chat_path}"}), 404

        with open(chat_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Handle different formats safely
        if isinstance(data, dict) and "messages" in data:
            msgs = data["messages"]
            if isinstance(msgs, list) and len(msgs) > count:
                data["messages"] = msgs[:-count]
            else:
                data["messages"] = []  # only clear the list, not the dict itself
        elif isinstance(data, list):
            data = data[:-count] if len(data) > count else []
        else:
            print(f"⚠️ Unrecognized format in {chat_path}")
            return jsonify({"error": "Unrecognized chat format"}), 400

        with open(chat_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        print(f"🗑️ Safely deleted last {count} message(s) for {character} ({chat_path})")
        return jsonify({"status": "ok"}), 200

    except Exception as e:
        print(f"❌ delete_last_messages error: {e}")
        return jsonify({"error": str(e)}), 500

# --------------------------------------------------
# Get Character Data (for auto-switching characters)
# --------------------------------------------------
@app.route("/get_character/<n>")
def get_character(n):
    """
    Returns character data (JSON) for the specified character name.
    Frontend uses this when auto-switching characters from sidebar.
    """
    try:
        char_path = os.path.join("characters", f"{n}.json")
        
        if not os.path.exists(char_path):
            return jsonify({"error": f"Character '{n}' not found"}), 404
            
        with open(char_path, "r", encoding="utf-8") as f:
            character_data = json.load(f)
            
        print(f"✅ Loaded character data for: {n}")
        return jsonify(character_data)
        
    except Exception as e:
        print(f"❌ Error loading character '{n}': {e}")
        return jsonify({"error": str(e)}), 500
        

# --------------------------------------------------
# Run Server
# --------------------------------------------------
if __name__ == '__main__':
    # --- Print all routes at startup ---
    with app.app_context():
        print("\nRegistered routes:")
        for rule in app.url_map.iter_rules():
            print(" ", rule.rule)
        print("-" * 50)

    ssl_cert = r'C:\Users\Chris\music.tail39b776.ts.net.crt'
    ssl_key  = r'C:\Users\Chris\music.tail39b776.ts.net.key'
    app.run(debug=True, use_reloader=False, host='0.0.0.0', port=8081,
            ssl_context=(ssl_cert, ssl_key))
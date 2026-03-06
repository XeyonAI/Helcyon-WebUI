from flask import Flask, request, jsonify, send_from_directory, render_template, Response
from flask_cors import CORS
import requests, os, json, re, hashlib
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
app.register_blueprint(extra)
app.register_blueprint(chat_bp)
app.register_blueprint(tts_bp, url_prefix='/api/tts')
app.register_blueprint(whisper_bp)

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
# Load server URL from settings
with open('settings.json', 'r') as f:
    settings = json.load(f)
    API_URL = settings.get('llama_server_url', 'http://127.0.0.1:5000')
    
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
        if r.status_code == 503:
            print("⏳ llama.cpp still loading — will retry on first chat.")
            return
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


get_current_model()

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
            chunk = j.get("content", "")
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
            chunk = delta.get("content", "")
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
# Full Memory Trigger Handler (Detection Only + ChatML Cleanup)
# --------------------------------------------------


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
        user_file_path = os.path.join("users", f"{user_name}.json")
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
        if char_data.get("main_prompt"):
            parts.append(strip_chatml(char_data["main_prompt"]))
        
        # ✅ ADD POST-HISTORY INSTRUCTIONS
        if char_data.get("post_history"):
            parts.append(f"\nPost-History Instructions:\n{strip_chatml(char_data['post_history'])}")

        char_context = "\n\n".join(parts)

        # 🔥 INJECT USER PERSONA CONTEXT
        user_context = ""
        if user_bio:
            user_context = (
                f"\n\n"
                f"═══════════════════════════════════════════════════════════\n"
                f"USER CONTEXT - WHO YOU ARE TALKING TO\n"
                f"═══════════════════════════════════════════════════════════\n\n"
                f"You are {char_data.get('name', 'the assistant')}.\n"
                f"You are talking to {user_display_name}.\n\n"
                f"{user_bio}\n\n"
                f"When {user_display_name} asks questions using 'I', 'my', or 'me', "
                f"they are referring to themselves ({user_display_name}), NOT to you.\n"
                f"You are {char_data.get('name', 'the assistant')}. "
                f"{user_display_name} is the person you're talking to.\n\n"
                f"═══════════════════════════════════════════════════════════\n"
                f"END USER CONTEXT\n"
                f"═══════════════════════════════════════════════════════════\n\n"
            )
            print(f"✅ Injected user persona context for {user_display_name}")
            print(f"   Context length: {len(user_context)} chars")
        else:
            print(f"⚠️ No user bio found, skipping persona injection")

        # Build the system_text (WITHOUT example_dialogue yet)
        # Build the system_text (WITHOUT example_dialogue yet)
        system_text = (
            f"{system_prompt}\n\n{user_context}{char_context}\n\n{instruction}\n\n{tone_primer}"
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
    if char_data.get("example_dialogue"):
        ex = char_data["example_dialogue"].strip()
        # 🔥 Strip any stray ChatML tokens from example dialogue - these cause the model
        # to see a premature end-of-turn inside the system block and emit a stop token
        # as its very first generation token, producing zero output.
        ex = re.sub(r'<\|im_start\|>\w*', '', ex)
        ex = re.sub(r'<\|im_end\|>', '', ex)
        ex = ex.strip()
        
        # 🔍 Check if character uses emojis or xxx in their examples
        has_emojis = any(emoji in ex for emoji in ['❤️', '😍', '😘', '💕', '😊', '😉', '🔥', '💯', '✨', '🎯'])
        has_xxx = 'xxx' in ex.lower()
        
        # Build conditional style instructions
        style_rules = []
        if has_emojis:
            style_rules.append("- Use emojis EXACTLY like the examples show")
        if has_xxx:
            style_rules.append("- End messages with 'xxx' or 'xxxx' like the character does")
        
        # Add generic style rules that apply to everyone
        style_rules.insert(0, "- Copy the EXACT tone, energy, and emotional warmth")
        style_rules.append("- Match their vocabulary, sentence structure, and rhythm")
        style_rules.append("- DO NOT copy the topics or situations from examples")
        style_rules.append("- Generate NEW content in this character's style")
        
        ex_block = (
            "\n\n"
            "═══════════════════════════════════════════════════════════\n"
            "⚠️ CRITICAL STYLE INSTRUCTION - READ CAREFULLY\n"
            "═══════════════════════════════════════════════════════════\n\n"
            "Below are example messages showing this character's speaking style.\n"
            "These examples are STYLE TEMPLATES ONLY.\n\n"
            "🎯 YOUR TASK:\n"
            + "\n".join(style_rules) + "\n\n"
            + ex + 
            "\n\n"
            "═══════════════════════════════════════════════════════════\n"
            "⚠️ REMINDER: Follow the character's style from the examples above\n"
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

    # 🎭 STYLE REMINDER: inject late so it's fresh in context right before generation
    if char_data.get("example_dialogue"):
        char_name_for_style = char_data.get("name", "the character")
        style_reminder = {
            "role": "system",
            "content": (
                f"STYLE REMINDER: You are {char_name_for_style}. "
                "Your response MUST match the tone, energy, length, and personality shown in the example dialogue above. "
                "Write with the same warmth, humour, and depth as those examples. "
                "Do NOT give a short or flat reply — match the richness of the examples."
            )
        }
        # Insert right before the final user message (after continuation msg if present)
        insert_position = len(messages) - 1
        messages.insert(insert_position, style_reminder)
        print(f"✅ Inserted style reminder at position {insert_position}")

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
    if len(assistant_messages) > 0:
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
    # Note: prompt.split() gives word count, not token count.
    # Words average ~1.3 tokens each, so use 7500 words ≈ ~9750 tokens.
    # This leaves ~6600 tokens headroom for generation in a 16384 context.
    MAX_WORDS_APPROX = 7500
    words = prompt.split()
    
    if len(words) > MAX_WORDS_APPROX:
        # Truncate but preserve ChatML structure
        truncated = " ".join(words[-MAX_WORDS_APPROX:])
        
        # Find the last complete <|im_start|> to avoid breaking mid-tag
        last_start = truncated.rfind("<|im_start|>")
        if last_start > 0:
            truncated = truncated[last_start:]
        
        prompt = truncated
        print(f"✂️ Prompt truncated to ~{MAX_WORDS_APPROX} words (~9750 tokens, was {len(words)} words)", flush=True)
    
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
            "stop": ["<|im_end|>", "<|im_start|>"],
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
            "top_p": sampling["top_p"],
            "min_p": sampling.get("min_p", 0.05),
            "top_k": sampling.get("top_k", 40),
            "repeat_penalty": sampling["repeat_penalty"],
            "stream": True,
            "stop": ["<|im_end|>", "<|im_start|>"],
        }

        print("\n🧩 FULL PAYLOAD SENDING TO MODEL:", flush=True)
        print(json.dumps(payload, indent=2), flush=True)

        try:
            return Response(
                stream_with_context(stream_model_response(payload)),
                content_type="text/event-stream; charset=utf-8",
            )
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
@app.route('/system_prompt.txt', methods=['GET', 'POST'])
def system_prompt():
    file_path = os.path.join(os.path.dirname(__file__), 'system_prompt.txt')

    if request.method == 'POST':
        try:
            data = request.get_data(as_text=True)
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(data)
            print(f"✅ Saved system_prompt.txt at {file_path}")
            return jsonify({'status': 'saved'})
        except Exception as e:
            print(f"❌ System prompt save failed: {e}")
            return jsonify({'error': str(e)}), 500

    if not os.path.exists(file_path):
        print(f"⚠️ system_prompt.txt not found at: {file_path}")
        return jsonify({'error': 'system_prompt.txt not found'}), 404

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            print(f"✅ Loaded system_prompt.txt from {file_path}")
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
    
    # Get character name from form data
    char_name = request.form.get("character_name", "").strip()
    
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
        if char_name:
            # Take only the first word/part before any dash or space
            clean_name = char_name.split('-')[0].split()[0].strip()
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


@app.route('/characters/<name>.json', methods=['POST'])
def save_character(name):
    try:
        data = request.get_json()
        path = os.path.join("characters", f"{name}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"✅ Character saved: {path}")
        return jsonify({"success": True})
    except Exception as e:
        print(f"❌ Failed to save character {name}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# --------------------------------------------------
# User Persona Management
# --------------------------------------------------
@app.route('/users/<path:filename>')
def serve_user_files(filename):
    return send_from_directory('users', filename)


@app.route('/set_active_user', methods=['POST'])
def set_active_user():
    data = request.get_json()
    selected = data.get('user')
    try:
        with open("users/index.json", "r", encoding="utf-8") as f:
            user_list = json.load(f)

        for name in user_list:
            path = os.path.join("users", f"{name}.json")
            if os.path.exists(path):
                with open(path, "r+", encoding="utf-8") as uf:
                    udata = json.load(uf)
                    udata["active"] = (name == selected)
                    uf.seek(0)
                    json.dump(udata, uf, indent=2)
                    uf.truncate()

        print(f"[INFO] ✅ Active user set to: {selected}")
        return jsonify({"success": True, "active": selected})

    except Exception as e:
        print(f"[ERROR] Failed to set active user: {e}")
        return jsonify({"success": False, "error": str(e)})


# --------------------------------------------------
# User Persona Editing
# --------------------------------------------------
@app.route('/get_user/<name>', methods=['GET'])
def get_user(name):
    """Return a user's persona details."""
    path = os.path.join("users", f"{name}.json")
    if not os.path.exists(path):
        return jsonify({"error": f"User '{name}' not found"}), 404
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Return only safe fields
        return jsonify({
            "name": data.get("name", name),
            "display_name": data.get("display_name", name),
            "bio": data.get("bio", ""),
            "image": data.get("image", "")  # ✅ ADD THIS LINE
        })
    except Exception as e:
        print(f"❌ Failed to load user {name}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/save_user/<name>', methods=['POST'])
def save_user(name):
    """Save updated persona info."""
    try:
        payload = request.get_json()
        path = os.path.join("users", f"{name}.json")
        # Create if missing
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = {"name": name}
        data["display_name"] = payload.get("display_name", name)
        data["bio"] = payload.get("bio", "")
        
        # ✅ Save image filename if provided
        if "image" in payload and payload["image"]:
            data["image"] = payload["image"]
            print(f"✅ Saving user image: {payload['image']}")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"✅ Updated user persona: {name}")
        return jsonify({"status": "ok"})
    except Exception as e:
        print(f"❌ Failed to save user {name}: {e}")
        return jsonify({"error": str(e)}), 500
        
@app.route("/get_active_user", methods=["GET"])
def get_active_user():
    try:
        with open("users/index.json", "r", encoding="utf-8") as f:
            user_list = json.load(f)
        
        # Find the user marked as active
        for name in user_list:
            path = os.path.join("users", f"{name}.json")
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
os.makedirs(CHAT_DIR, exist_ok=True)


@app.route('/save_chat_character/<name>', methods=['POST'])
def save_chat_character(name):
    """Save chat for a specific character (no route conflict)."""
    try:
        data = request.get_json(force=True)
        os.makedirs(CHAT_DIR, exist_ok=True)  # ✅
        path = os.path.join(CHAT_DIR, f"{name}.json")  # ✅

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

        print(f"💾 Chat saved for {name} ({len(history)} total)")
        return jsonify({"status": "ok"})
    except Exception as e:
        print(f"❌ Failed to save chat for {name}: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route('/clear_chat/<name>', methods=['POST'])
def clear_chat(name):
    """Delete chat contents for one character."""
    try:
        path = os.path.join(CHAT_DIR, f"{name}.json")  # ✅
        if os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                json.dump([], f)
        print(f"🧹 Cleared chat for {name}")
        return jsonify({"status": "ok"})
    except Exception as e:
        print(f"❌ Failed to clear chat for {name}: {e}")
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

@app.route("/get_sampling_settings", methods=["GET"])
def get_sampling_settings():
    return jsonify(load_sampling_settings())

@app.route("/save_sampling_settings", methods=["POST"])
def save_sampling_settings():
    data = request.get_json()
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print("✅ Sampling settings saved:", data)
    return jsonify({"status": "ok"})


# --------------------------------------------------
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

        # Load system prompt
        with open("system_prompt.txt", "r", encoding="utf-8") as sp:
            system_prompt = sp.read().strip()

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
@app.route("/get_character/<name>")
def get_character(name):
    """
    Returns character data (JSON) for the specified character name.
    Frontend uses this when auto-switching characters from sidebar.
    """
    try:
        char_path = os.path.join("characters", f"{name}.json")
        
        if not os.path.exists(char_path):
            return jsonify({"error": f"Character '{name}' not found"}), 404
            
        with open(char_path, "r", encoding="utf-8") as f:
            character_data = json.load(f)
            
        print(f"✅ Loaded character data for: {name}")
        return jsonify(character_data)
        
    except Exception as e:
        print(f"❌ Error loading character '{name}': {e}")
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

    app.run(debug=True, use_reloader=False, port=8081)
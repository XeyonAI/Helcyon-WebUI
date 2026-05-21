# chat_routes.py
import os, json, re, shutil
from flask import Blueprint, jsonify, request
from datetime import datetime

print("✅ chat_routes blueprint loaded")

# ISO-8601 timestamp prefix used on persisted chat lines: "[2026-05-11T14:32:18] Speaker: ..."
# Compiled once at module load instead of recompiling per /chats/open call.
_TS_PREFIX_RE = re.compile(
    r'^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)\] '
)


def _atomic_write_text(filepath, text):
    """Write `text` to `filepath` atomically.

    Writes to a temp file in the same directory, then `os.replace`s it over
    the destination. `os.replace` is atomic on both POSIX and Windows when
    the source and destination are on the same filesystem. If the write
    crashes mid-way, the destination file is unchanged — never truncated or
    partially-overwritten. Prevents chat history from being destroyed by an
    interrupted save.
    """
    import tempfile
    d = os.path.dirname(filepath) or '.'
    fd, tmp_path = tempfile.mkstemp(suffix='.tmp', prefix='.chatsave_', dir=d, text=True)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as tf:
            tf.write(text)
        os.replace(tmp_path, filepath)
    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise


def _format_chat_messages(messages, char_name):
    """Serialise a message list into the on-disk chat-file format.

    Each message becomes:  `[<timestamp>] <speaker>: <content>\\n\\n`
    Timestamp prefix is omitted when missing. Multimodal content is flattened
    to its text parts (images get an `[image]` placeholder).
    """
    lines = []
    for msg in messages:
        role = msg.get("role")
        raw_content = msg.get("content", "")
        timestamp = msg.get("timestamp", "")
        if isinstance(raw_content, list):
            text_parts = [p.get("text", "") for p in raw_content if p.get("type") == "text"]
            has_image = any(p.get("type") == "image_url" for p in raw_content)
            content = " ".join(text_parts).strip()
            if has_image and not content:
                content = "[image]"
            elif has_image:
                content = f"{content} [image]"
        else:
            content = raw_content
        speaker = msg.get("speaker") or ("User" if role == "user" else char_name)
        prefix = f"[{timestamp}] " if timestamp else ""
        lines.append(f"{prefix}{speaker}: {content}\n\n")
    return "".join(lines)

chat_bp = Blueprint("chat_bp", __name__)
CHATS_DIR = os.path.join(os.getcwd(), "chats")  # Legacy global chats
PROJECTS_DIR = os.path.join(os.getcwd(), "projects")

def get_active_project():
    """Get the currently active project name."""
    state_file = os.path.join(PROJECTS_DIR, "_active_project.json")
    if os.path.exists(state_file):
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("active_project")
        except Exception as e:
            print(f"⚠️ get_active_project failed: {e}")
    return None

def get_chats_dir():
    """Get the chats directory for the active project, or global if no project."""
    active_project = get_active_project()
    
    if active_project:
        # Use project-specific chats folder
        project_chats = os.path.join(PROJECTS_DIR, active_project, "chats")
        if not os.path.exists(project_chats):
            os.makedirs(project_chats)
        return project_chats
    else:
        # Use global chats folder (legacy)
        if not os.path.exists(CHATS_DIR):
            os.makedirs(CHATS_DIR)
        return CHATS_DIR

def ensure_chats_dir():
    """Ensure the appropriate chats directory exists."""
    chats_dir = get_chats_dir()
    if not os.path.exists(chats_dir):
        os.makedirs(chats_dir)

# --------------------------------------------------
# List chats
# --------------------------------------------------
@chat_bp.route("/chats/list")
def list_chats():
    chats_dir = get_chats_dir()
    
    print(f"🪶 /chats/list route triggered")
    print(f"   Active project: {get_active_project()}")
    print(f"   Looking in: {chats_dir}")
    
    files = os.listdir(chats_dir)
    chats = []
    
    for f in files:
        if f.endswith(".txt"):
            filepath = os.path.join(chats_dir, f)
            
            # Get file stats
            stats = os.stat(filepath)
            created = stats.st_ctime  # Creation time
            modified = stats.st_mtime  # Last modified time
            
            # Display full title including character name
            title = f.replace(".txt", "").replace("_", " ")
            
            chats.append({
                "title": title,
                "filename": f,
                "created": created,
                "modified": modified
            })
    
    print(f"Returning {len(chats)} chats")
    return jsonify(chats)
    
    
@chat_bp.route("/chats/load", methods=["POST"])
def check_chat_exists():
    """Check whether a chat file exists on the server. Used by frontend to verify before restoring."""
    data = request.get_json()
    filename = data.get("filename", "")
    if not filename:
        return jsonify({"error": "No filename provided"}), 400
    chats_dir = get_chats_dir()
    filepath = os.path.join(chats_dir, filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "Chat not found"}), 404
    return jsonify({"status": "ok", "filename": filename})


@chat_bp.route("/chats/open/<filename>")
def open_chat(filename):
    chats_dir = get_chats_dir()
    filepath = os.path.join(chats_dir, filename)
    
    if not os.path.exists(filepath):
        return jsonify({"error": "Chat not found"}), 404
    
    with open(filepath, "r", encoding="utf-8") as f:
        raw_text = f.read()
    
    print(f"\n{'='*60}")
    print(f"📂 Loading: {filename}")
    print(f"   From: {chats_dir}")
    print(f"{'='*60}\n")
    
    # Load list of known characters
    available_characters = []
    try:
        char_index_path = os.path.join(os.getcwd(), "characters", "index.json")
        with open(char_index_path, "r", encoding="utf-8") as f:
            available_characters = json.load(f)
            print(f"📋 Known characters: {available_characters}")
    except Exception as e:
        print(f"⚠️ Could not load character list: {e}")
    
    # ✅ Load list of valid user personas dynamically
    valid_users = []
    try:
        user_index_path = os.path.join(os.getcwd(), "users", "index.json")
        with open(user_index_path, "r", encoding="utf-8") as f:
            valid_users = json.load(f)
            print(f"👤 Valid users: {valid_users}")
    except Exception as e:
        print(f"⚠️ Could not load user list: {e}")
    
    lines = raw_text.split('\n')
    messages = []
    current_role = None
    current_content = []
    current_speaker = None
    current_timestamp = None
    
    for line in lines:
        # Strip timestamp prefix if present
        ts_match = _TS_PREFIX_RE.match(line)
        line_timestamp = None
        if ts_match:
            line_timestamp = ts_match.group(1)
            line = line[ts_match.end():]

        stripped = line.strip()
        
        # Check for speaker pattern
        if ":" in stripped and not stripped.startswith(" "):
            potential_speaker = stripped.split(":")[0].strip()
            
            # ✅ Check against dynamic lists instead of hard-coded names
            is_known_character = potential_speaker in available_characters
            is_valid_user = potential_speaker in valid_users
            # ✅ Also treat generic "User" label as valid (legacy chats saved before persona system)
            is_generic_user = potential_speaker.lower() == "user"

            if (is_known_character or is_valid_user or is_generic_user) and len(potential_speaker) < 30:
                # Save previous message
                if current_role and current_content:
                    msg_text = "\n".join(current_content).strip()
                    entry = {"role": current_role, "content": msg_text, "speaker": current_speaker}
                    if current_timestamp:
                        entry["timestamp"] = current_timestamp
                    messages.append(entry)
                
                # Start new message
                content_after_colon = stripped.split(":", 1)[1].strip()
                current_speaker = potential_speaker
                current_timestamp = line_timestamp
                
                if is_known_character:
                    current_role = "assistant"
                    print(f"✅ Recognized assistant: {potential_speaker}")
                else:
                    current_role = "user"
                    print(f"✅ Recognized user: {potential_speaker}")
                
                current_content = [content_after_colon] if content_after_colon else []
                continue
        
        # Continue current message
        if current_role:
            if stripped:
                current_content.append(stripped)
            else:
                current_content.append("")
    
    # Flush last message
    if current_role and current_content:
        msg_text = "\n".join(current_content).strip()
        entry = {"role": current_role, "content": msg_text, "speaker": current_speaker}
        if current_timestamp:
            entry["timestamp"] = current_timestamp
        messages.append(entry)
    
    print(f"📊 Loaded {len(messages)} messages")
    return jsonify({"filename": filename, "messages": messages})

# --------------------------------------------------
# Rename chat (with character prefix preservation)
# --------------------------------------------------
@chat_bp.route("/chats/rename", methods=["POST"])
def rename_chat():
    data = request.json
    old_filename = data.get("old_filename")
    new_name = data.get("new_name")
    
    if not old_filename or not new_name:
        return jsonify({"error": "Missing filename or new name"}), 400
    
    chats_dir = get_chats_dir()
    old_path = os.path.join(chats_dir, old_filename)
    
    if not os.path.exists(old_path):
        return jsonify({"error": "Original chat not found"}), 404
    
    # ✅ The new_name should ALREADY include the character prefix (from frontend)
    # Frontend sends: "Gem - Copy - My New Title"
    # We just need to sanitize and add .txt
    
    # Sanitize the new name (but preserve " - " separators)
    safe_name = "".join(c for c in new_name if c.isalnum() or c in (' ', '-', '_', '.')).strip()
    
    # Build new filename
    new_filename = f"{safe_name}.txt"
    new_path = os.path.join(chats_dir, new_filename)
    
    if os.path.exists(new_path) and old_path != new_path:
        return jsonify({"error": "A chat with that name already exists"}), 409
    
    os.rename(old_path, new_path)
    print(f"✏️ Renamed: {old_filename} → {new_filename}")
    
    return jsonify({"success": True, "new_filename": new_filename})

# --------------------------------------------------
# New Chat (with character prefix)
# --------------------------------------------------
@chat_bp.route("/chats/new", methods=["POST"])
def new_chat():
    chats_dir = get_chats_dir()
    
    data = request.get_json() or {}
    char_name = data.get("character", "Unknown").strip()
    
    print(f"🆕 NEW CHAT REQUEST")
    print(f"   Character name: '{char_name}'")
    print(f"   Saving to: {chats_dir}")
    
    # Generate base filename
    date_str = datetime.now().strftime("%b %d")
    
    # Check if file exists, add counter if needed
    counter = 1
    filename = f"{char_name} - New Chat - {date_str}.txt"
    filepath = os.path.join(chats_dir, filename)
    
    while os.path.exists(filepath):
        filename = f"{char_name} - New Chat ({counter}) - {date_str}.txt"
        filepath = os.path.join(chats_dir, filename)
        counter += 1
    
    # Create empty file
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("")
    
    print(f"📝 Created new chat: {filename}")
    return jsonify({"filename": filename})
# --------------------------------------------------
# Auto-name Chat (from first user message — model-generated title)
# --------------------------------------------------
@chat_bp.route("/chats/auto-name", methods=["POST"])
def auto_name_chat():
    import re as _re
    import requests as _requests

    data = request.get_json() or {}
    old_filename = data.get("filename", "")
    first_message = data.get("first_message", "").strip()

    if not old_filename or not first_message:
        return jsonify({"error": "Missing filename or first_message"}), 400

    chats_dir = get_chats_dir()
    old_path = os.path.join(chats_dir, old_filename)

    if not os.path.exists(old_path):
        return jsonify({"error": "Chat not found"}), 404

    # --- Ask the model for a smart title ---
    raw_name = None
    try:
        with open(os.path.join(os.getcwd(), "settings.json"), "r", encoding="utf-8") as _sf:
            _settings = json.load(_sf)
        _port = _settings.get("llama_args", {}).get("port", 8080)
        _api_url = f"http://127.0.0.1:{_port}"

        # Truncate very long first messages — only need the gist
        excerpt = first_message[:400]

        _prompt = (
            "<|im_start|>system\n"
            "You write short, punchy chat titles — the kind a human would scribble in a sidebar. "
            "Rules: 4-6 words max. Drop filler like \"how to\", \"help with\", \"question about\", \"a/the/my\". "
            "No punctuation at the end. No quotes. No explanation. Just the title.\n"
            "<|im_end|>\n"
            "<|im_start|>user\n"
            "Message: Can you help me debug a memory leak in my Python script?\n"
            "<|im_end|>\n"
            "<|im_start|>assistant\n"
            "Python Memory Leak Debug<|im_end|>\n"
            "<|im_start|>user\n"
            "Message: What's the best way to learn German grammar?\n"
            "<|im_end|>\n"
            "<|im_start|>assistant\n"
            "Learning German Grammar<|im_end|>\n"
            "<|im_start|>user\n"
            "Message: Write me a short poem about autumn leaves falling\n"
            "<|im_end|>\n"
            "<|im_start|>assistant\n"
            "Autumn Leaves Poem<|im_end|>\n"
            "<|im_start|>user\n"
            f"Message: {excerpt}\n"
            "<|im_end|>\n"
            "<|im_start|>assistant\n"
        )

        _payload = {
            "prompt": _prompt,
            "temperature": 0.3,
            "n_predict": 16,
            "top_p": 0.9,
            "repeat_penalty": 1.1,
            "stream": False,
            "stop": ["<|im_end|>", "\n", "<|im_start|>"],
        }

        _resp = _requests.post(f"{_api_url}/completion", json=_payload, timeout=15)
        _resp.raise_for_status()
        raw_name = _resp.json().get("content", "").strip()
        # Strip any stray quotes/punctuation the model adds
        raw_name = _re.sub(r'^["\']|["\']$', '', raw_name).strip()
        raw_name = _re.sub(r'[.!?,;:]+$', '', raw_name).strip()
        # Hard cap: 6 words max — safety net if the model ignores the rule
        _words = raw_name.split()
        if len(_words) > 6:
            raw_name = ' '.join(_words[:6])
            raw_name = _re.sub(r'[.!?,;:]+$', '', raw_name).strip()
        print(f"🏷️ Model suggested title: '{raw_name}'")
    except Exception as _e:
        print(f"⚠️ Model title generation failed, falling back to word-chop: {_e}")

    # Fallback: word-chop if model call failed or returned empty
    if not raw_name:
        text = _re.sub(r'[*_#`>]', '', first_message)
        sentence = _re.split(r'[.!?]', text)[0].strip() or text
        raw_name = ' '.join(sentence.split()[:5])

    # Capitalise + strip illegal filename chars
    raw_name = raw_name[:1].upper() + raw_name[1:] if raw_name else 'New Chat'
    raw_name = _re.sub(r'[\\/:*?"<>|]', '', raw_name).strip() or 'New Chat'

    # Preserve character prefix and date suffix — format: "Character - Title - Mon DD.txt"
    # ✅ FIX: Match prefix against known characters so multi-part names like
    # "Gemma - GPT-5" are preserved intact instead of being truncated to "Gemma"
    name_no_ext = old_filename.replace(".txt", "")
    parts = name_no_ext.split(" - ")

    # Load known characters to find the correct prefix length
    char_prefix = None
    try:
        char_index_path = os.path.join(os.getcwd(), "characters", "index.json")
        with open(char_index_path, "r", encoding="utf-8") as _cf:
            known_chars = json.load(_cf)
        # Try progressively longer prefixes until one matches a known character
        for i in range(len(parts), 0, -1):
            candidate = " - ".join(parts[:i])
            if candidate in known_chars:
                char_prefix = candidate
                break
    except Exception as _ce:
        print(f"⚠️ Could not load character list for prefix detection: {_ce}")

    # Fallback: use parts[0] if no known character matched
    if not char_prefix:
        char_prefix = parts[0]

    # Date suffix is always the last segment (e.g. "Apr 30")
    date_suffix = parts[-1]

    # Rebuild: if the filename had more than just prefix + date, include the date
    remaining_parts = name_no_ext[len(char_prefix):].lstrip(" -").split(" - ")
    if len(remaining_parts) >= 2:
        # Had a title + date — replace title, keep date
        new_name_no_ext = f"{char_prefix} - {raw_name} - {date_suffix}"
    elif len(remaining_parts) == 1 and remaining_parts[0] != char_prefix:
        # Had only one extra segment (no date or no title)
        new_name_no_ext = f"{char_prefix} - {raw_name}"
    else:
        new_name_no_ext = raw_name

    new_filename = f"{new_name_no_ext}.txt"
    new_path = os.path.join(chats_dir, new_filename)

    # Collision guard
    counter = 1
    base = new_name_no_ext
    while os.path.exists(new_path) and new_path != old_path:
        new_filename = f"{base} ({counter}).txt"
        new_path = os.path.join(chats_dir, new_filename)
        counter += 1

    if old_path == new_path:
        return jsonify({"success": True, "new_filename": new_filename, "skipped": True})

    os.rename(old_path, new_path)
    print(f"🏷️ Auto-named: {old_filename} → {new_filename}")
    return jsonify({"success": True, "new_filename": new_filename})


# --------------------------------------------------
# Delete Chat
# --------------------------------------------------
@chat_bp.route("/chats/delete/<filename>", methods=["DELETE"])
def delete_chat(filename):
    chats_dir = get_chats_dir()
    filepath = os.path.join(chats_dir, filename)
    
    if not os.path.exists(filepath):
        return jsonify({"error": "Chat not found"}), 404
    
    try:
        os.remove(filepath)
        print(f"🗑️ Deleted: {filename}")
        return jsonify({"success": True, "deleted": filename})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --------------------------------------------------
# Save Chat (replaces entire file with messages)
# --------------------------------------------------
@chat_bp.route("/chats/save", methods=["POST"])
def save_chat_messages():
    """Overwrite chat file with complete message history (atomic)."""
    try:
        data = request.get_json()
        filename = data.get("filename")
        messages = data.get("messages")

        if not filename:
            return jsonify({"error": "No filename provided"}), 400
        if messages is None:
            return jsonify({"error": "No messages provided"}), 400

        chats_dir = get_chats_dir()
        filepath = os.path.join(chats_dir, filename)

        # Character name fallback: parse from filename prefix
        char_name = "Assistant"
        if " - " in filename:
            char_name = filename.split(" - ", 1)[0]

        text = _format_chat_messages(messages, char_name)
        _atomic_write_text(filepath, text)
        print(f"💾 Saved {len(messages)} messages to {filename} ({len(text)} chars)")
        return jsonify({"success": True})

    except Exception as e:
        print(f"❌ Failed to save chat: {e}")
        return jsonify({"error": str(e)}), 500
        
        
# --------------------------------------------------
# Append to Chat (for streaming saves)
# --------------------------------------------------
@chat_bp.route("/save_chat", methods=["POST"])
def append_chat_turn():
    """Append a single user+model turn to chat file."""
    try:
        data = request.get_json(force=True)
        filename = data.get("filename")
        user_msg = data.get("user", "")
        model_msg = data.get("model", "")
        character = data.get("character", "Assistant")
        
        if not filename:
            return jsonify({"error": "No filename provided"}), 400
        
        chats_dir = get_chats_dir()
        filepath = os.path.join(chats_dir, filename)
        
        # Append messages with timestamp
        now_ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(f"[{now_ts}] User: {user_msg}\n\n")
            f.write(f"[{now_ts}] {character}: {model_msg}\n\n")
        
        print(f"💾 Appended turn to {filename}")
        return jsonify({"status": "ok"})
        
    except Exception as e:
        print(f"❌ Failed to append chat: {e}")
        return jsonify({"error": str(e)}), 500

# --------------------------------------------------
# Update Chat (for delete last message)
# --------------------------------------------------
@chat_bp.route('/chats/update', methods=['POST'])
def update_chat():
    """Overwrite entire chat file (atomic)."""
    try:
        data = request.get_json()
        filename = data.get("filename")
        messages = data.get("messages")

        if not filename or messages is None:
            return jsonify({"error": "Missing data"}), 400

        chats_dir = get_chats_dir()
        filepath = os.path.join(chats_dir, filename)

        char_name = "Assistant"
        if " - " in filename:
            char_name = filename.split(" - ", 1)[0]

        text = _format_chat_messages(messages, char_name)
        _atomic_write_text(filepath, text)
        print(f"📝 Updated: {filename}")
        return jsonify({"success": True})

    except Exception as e:
        print(f"❌ Update failed: {e}")
        return jsonify({"error": str(e)}), 500


# --------------------------------------------------
# Copy Chat
# --------------------------------------------------
@chat_bp.route('/chats/copy', methods=['POST'])
def copy_chat():
    """Duplicate an existing chat file."""
    try:
        data = request.json
        source_filename = data.get("source_filename")
        
        if not source_filename:
            return jsonify({"error": "No source file"}), 400
        
        chats_dir = get_chats_dir()
        source_path = os.path.join(chats_dir, source_filename)
        
        if not os.path.exists(source_path):
            return jsonify({"error": "Source not found"}), 404
        
        # Parse the filename: "Character - Title - Date.txt"
        name_without_ext = source_filename.replace(".txt", "")
        
        # Find LAST " - " (this is before the date)
        last_dash_index = name_without_ext.rfind(" - ")
        
        if last_dash_index != -1:
            # Split into: everything before date, and the date itself
            before_date = name_without_ext[:last_dash_index]
            date_suffix = name_without_ext[last_dash_index:]  # Includes " - "
            
            # Insert " - Branch" before the date
            new_filename = f"{before_date} - Branch{date_suffix}.txt"
        else:
            # No date found, just append " - Branch"
            new_filename = f"{name_without_ext} - Branch.txt"
        
        new_path = os.path.join(chats_dir, new_filename)
        
        shutil.copy2(source_path, new_path)
        print(f"📋 Copied: {source_filename} → {new_filename}")
        
        return jsonify({"success": True, "new_filename": new_filename})
        
    except Exception as e:
        print(f"❌ Copy failed: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
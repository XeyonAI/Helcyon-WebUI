# chat_routes.py
import os, json, shutil
from flask import Blueprint, jsonify, request
from datetime import datetime

print("✅ chat_routes blueprint loaded")

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
        except:
            pass
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
    
    files = sorted(os.listdir(chats_dir))
    chats = []
    
    for f in files:
        if f.endswith(".txt"):
            # Display full title including character name
            title = f.replace(".txt", "").replace("_", " ")
            chats.append({"title": title, "filename": f})
    
    print(f"Returning {len(chats)} chats")
    return jsonify(chats)
    
    
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
    
    for line in lines:
        stripped = line.strip()
        
        # Check for speaker pattern
        if ":" in stripped and not stripped.startswith(" "):
            potential_speaker = stripped.split(":")[0].strip()
            
            # ✅ Check against dynamic lists instead of hard-coded names
            is_known_character = potential_speaker in available_characters
            is_valid_user = potential_speaker in valid_users
            
            if (is_known_character or is_valid_user) and len(potential_speaker) < 30:
                # Save previous message
                if current_role and current_content:
                    msg_text = "\n".join(current_content).strip()
                    messages.append({"role": current_role, "content": msg_text})
                
                # Start new message
                content_after_colon = stripped.split(":", 1)[1].strip()
                current_speaker = potential_speaker
                
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
        messages.append({"role": current_role, "content": msg_text})
    
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
    safe_name = "".join(c for c in new_name if c.isalnum() or c in (' ', '-', '_')).strip()
    
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
    """Overwrite chat file with complete message history."""
    try:
        data = request.get_json()
        filename = data.get("filename")
        messages = data.get("messages")
        
        print(f"💾 FLASK: Received save request for {filename}")
        print(f"💾 FLASK: Number of messages: {len(messages)}")
        
        if not filename:
            return jsonify({"error": "No filename provided"}), 400
        
        if not messages:
            return jsonify({"error": "No messages provided"}), 400
        
        chats_dir = get_chats_dir()
        filepath = os.path.join(chats_dir, filename)
        
        # Get character name from filename as fallback
        char_name = "Assistant"
        if " - " in filename:
            char_name = filename.split(" - ", 1)[0]
        
        print(f"💾 FLASK: Writing to {filepath} (mode='w' = OVERWRITE)")
        
        # Rewrite entire file
        with open(filepath, "w", encoding="utf-8") as f:
            for i, msg in enumerate(messages):
                role = msg.get("role")
                content = msg.get("content", "")
                
                # Use speaker from message, fallback to defaults
                speaker = msg.get("speaker")
                if not speaker:
                    speaker = "User" if role == "user" else char_name
                
                f.write(f"{speaker}: {content}\n\n")
                print(f"💾 FLASK: Wrote message {i+1}: {speaker} ({len(content)} chars)")
        
        print(f"💾 FLASK: Saved {len(messages)} messages to {filename}")
        return jsonify({"success": True})
        
    except Exception as e:
        print(f"❌ FLASK: Failed to save chat: {e}")
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
        
        # Append messages
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(f"User: {user_msg}\n\n")
            f.write(f"{character}: {model_msg}\n\n")
        
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
    """Overwrite entire chat file."""
    try:
        data = request.get_json()
        filename = data.get("filename")
        messages = data.get("messages")
        
        if not filename or messages is None:
            return jsonify({"error": "Missing data"}), 400
        
        chats_dir = get_chats_dir()
        filepath = os.path.join(chats_dir, filename)
        
        # Get character name from filename
        char_name = "Assistant"
        if " - " in filename:
            char_name = filename.split(" - ", 1)[0]
        
        # Rewrite file
        with open(filepath, "w", encoding="utf-8") as f:
            for msg in messages:
                role = msg.get("role")
                content = msg.get("content", "")
                speaker = "User" if role == "user" else char_name
                f.write(f"{speaker}: {content}\n\n")
        
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
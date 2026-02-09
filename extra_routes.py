from flask import Blueprint, request, jsonify, send_from_directory, session, send_file
import os
import json
import base64
from io import BytesIO
from PIL import Image
from PIL.PngImagePlugin import PngInfo

# --------------------------------------------------
# Blueprint setup
# --------------------------------------------------
extra = Blueprint("extra", __name__)

# --------------------------------------------------
# Restore previously saved chat
# --------------------------------------------------
@extra.route("/restore_chat", methods=["POST"])
def restore_chat():
    try:
        data = request.get_json()
        character_name = data.get("character", "Cal")
        chat_id = data.get("chat_id", "001")

        chat_path = os.path.join("chats", f"{character_name.lower()}_chat_{chat_id}.txt")
        if not os.path.exists(chat_path):
            return jsonify({"success": False, "error": "Chat file not found."}), 404

        with open(chat_path, "r", encoding="utf-8") as f:
            chat_text = f.read().strip()

        return jsonify({"success": True, "chat": chat_text})

    except Exception as e:
        print(f"❌ Restore chat failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# --------------------------------------------------
# Restore last chat (auto-load on refresh)
# --------------------------------------------------
@extra.route("/restore_last_chat", methods=["GET"])
def restore_last_chat():
    try:
        chat_path = os.path.join("chats", "gem_chat_001.txt")
        if not os.path.exists(chat_path):
            return jsonify({"success": False, "chat": []})

        with open(chat_path, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f.readlines() if l.strip()]

        messages = []
        current_role = None
        buffer = []

        def push_message():
            nonlocal buffer, current_role
            if current_role and buffer:
                messages.append({"role": current_role, "content": "\n".join(buffer).strip()})
                buffer = []

        for line in lines:
            lower = line.lower()
            if lower.startswith(("user:")):
                push_message()
                current_role = "user"
                buffer.append(line.split(":", 1)[1].strip())
            elif lower.startswith((f"{character_name.lower()}:", "assistant:")):
                push_message()
                current_role = "assistant"
                buffer.append(line.split(":", 1)[1].strip())
            else:
                buffer.append(line)
        push_message()

        # ✅ Make imported chat the active one (both in-memory and persisted)
        try:
            import builtins
            builtins.active_chat = messages  # update in-memory global
            with open("chat_history.json", "w", encoding="utf-8") as f:
                json.dump({"active_chat": messages}, f, indent=2, ensure_ascii=False)
            print("💾 Imported chat set as new active chat.")
        except Exception as e:
            print(f"⚠️ Could not persist imported chat: {e}")

        return jsonify({"success": True, "chat": messages})

    except Exception as e:
        print(f"❌ restore_last_chat failed: {e}")
        return jsonify({"success": False, "chat": []})
        
# --------------------------------------------------
# Opening Lines Management (Disk-Based)
# --------------------------------------------------
@extra.route('/get_opening_lines/<character>', methods=['GET'])
def get_opening_lines(character):
    """Load opening lines from disk for a character."""
    try:
        opening_lines_dir = os.path.join(os.path.dirname(__file__), "opening_lines")
        os.makedirs(opening_lines_dir, exist_ok=True)
        
        filepath = os.path.join(opening_lines_dir, f"{character}.json")
        
        if not os.path.exists(filepath):
            return jsonify({"enabled": False, "lines": []})
        
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        return jsonify(data)
        
    except Exception as e:
        print(f"❌ Error loading opening lines for {character}: {e}")
        return jsonify({"enabled": False, "lines": []})


@extra.route('/save_opening_lines', methods=['POST'])
def save_opening_lines():
    """Save opening lines to disk for a character."""
    try:
        data = request.get_json()
        character = data.get("character")
        enabled = data.get("enabled", False)
        lines = data.get("lines", [])
        
        if not character:
            return jsonify({"error": "No character specified"}), 400
        
        opening_lines_dir = os.path.join(os.path.dirname(__file__), "opening_lines")
        os.makedirs(opening_lines_dir, exist_ok=True)
        
        filepath = os.path.join(opening_lines_dir, f"{character}.json")
        
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump({
                "enabled": enabled,
                "lines": lines
            }, f, indent=2, ensure_ascii=False)
        
        print(f"✅ Saved opening lines for {character}")
        return jsonify({"status": "ok"})
        
    except Exception as e:
        print(f"❌ Error saving opening lines: {e}")
        return jsonify({"error": str(e)}), 500


# --------------------------------------------------
# PNG Character Card Export
# --------------------------------------------------
@extra.route('/export_character/<name>', methods=['GET'])
def export_character(name):
    """Export character as PNG with embedded JSON metadata."""
    try:
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo
        import base64
        import io
        
        # Load character JSON
        char_path = os.path.join("characters", f"{name}.json")
        if not os.path.exists(char_path):
            return jsonify({"error": "Character not found"}), 404
        
        with open(char_path, "r", encoding="utf-8") as f:
            char_data = json.load(f)
        
        # Load character image
        image_file = char_data.get("image", f"{name}.png")
        image_path = os.path.join("static", "images", image_file)
        
        if not os.path.exists(image_path):
            return jsonify({"error": "Character image not found"}), 404
        
        # Open the image
        img = Image.open(image_path)
        
        # Ensure it's RGB (PNG requirement)
        if img.mode not in ('RGB', 'RGBA'):
            img = img.convert('RGBA')
        
        # Prepare metadata
        metadata = PngInfo()
        
        # Encode character data as base64
        char_json = json.dumps(char_data, ensure_ascii=False)
        char_base64 = base64.b64encode(char_json.encode('utf-8')).decode('ascii')
        
        # Add to PNG metadata (industry standard key)
        metadata.add_text("chara", char_base64)
        
        # Save to BytesIO buffer
        buffer = io.BytesIO()
        img.save(buffer, format="PNG", pnginfo=metadata)
        buffer.seek(0)
        
        # Send as downloadable file
        from flask import send_file
        return send_file(
            buffer,
            mimetype='image/png',
            as_attachment=True,
            download_name=f"{name}.png"
        )
        
    except Exception as e:
        print(f"❌ Export failed: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# --------------------------------------------------
# PNG Character Card Import
# --------------------------------------------------
@extra.route('/import_character', methods=['POST'])
def import_character():
    """Import character from PNG with embedded JSON metadata."""
    try:
        from PIL import Image
        import base64
        import io
        
        # Check if file was uploaded
        if 'file' not in request.files:
            return jsonify({"error": "No file uploaded"}), 400
        
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({"error": "No file selected"}), 400
        
        # Read the uploaded image
        img = Image.open(file.stream)
        
        # Extract metadata
        if "chara" not in img.info:
            return jsonify({"error": "No character data found in PNG metadata"}), 400
        
        # Decode base64 JSON
        char_base64 = img.info["chara"]
        char_json = base64.b64decode(char_base64).decode('utf-8')
        char_data = json.loads(char_json)
        
        # Validate required fields
        if "name" not in char_data:
            return jsonify({"error": "Invalid character data - missing name"}), 400
        
        char_name = char_data["name"]
        
        # Save character JSON
        char_dir = os.path.join(os.path.dirname(__file__), "characters")
        os.makedirs(char_dir, exist_ok=True)
        
        char_path = os.path.join(char_dir, f"{char_name}.json")
        
        # Update image filename to match character name
        image_filename = f"{char_name}.png"
        char_data["image"] = image_filename
        
        with open(char_path, "w", encoding="utf-8") as f:
            json.dump(char_data, f, indent=2, ensure_ascii=False)
        
        # Save image
        image_dir = os.path.join(os.path.dirname(__file__), "static", "images")
        os.makedirs(image_dir, exist_ok=True)
        
        image_path = os.path.join(image_dir, image_filename)
        
        # Convert to RGB/RGBA and save
        if img.mode not in ('RGB', 'RGBA'):
            img = img.convert('RGBA')
        
        img.save(image_path, "PNG")
        
        # Update character index
        index_path = os.path.join(char_dir, "index.json")
        if os.path.exists(index_path):
            with open(index_path, "r", encoding="utf-8") as f:
                characters = json.load(f)
            if char_name not in characters:
                characters.append(char_name)
        else:
            characters = [char_name]
        
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(sorted(characters), f, indent=2, ensure_ascii=False)
        
        print(f"✅ Imported character: {char_name}")
        return jsonify({"status": "ok", "name": char_name})
        
    except Exception as e:
        print(f"❌ Import failed: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
        
# --------------------------------------------------
# Delete Character
# --------------------------------------------------
@extra.route('/delete_character/<name>', methods=['DELETE'])
def delete_character(name):
    """Delete a character and its associated files."""
    try:
        # Delete character JSON
        char_path = os.path.join("characters", f"{name}.json")
        if os.path.exists(char_path):
            os.remove(char_path)
            print(f"🗑️ Deleted character file: {char_path}")
        
        # Delete character image (if it exists)
        # Try to load the character data first to get the image filename
        image_file = f"{name}.png"  # Default assumption
        image_path = os.path.join("static", "images", image_file)
        if os.path.exists(image_path):
            os.remove(image_path)
            print(f"🗑️ Deleted character image: {image_path}")
        
        # Update character index
        index_path = os.path.join("characters", "index.json")
        if os.path.exists(index_path):
            with open(index_path, "r", encoding="utf-8") as f:
                characters = json.load(f)
            
            if name in characters:
                characters.remove(name)
                
            with open(index_path, "w", encoding="utf-8") as f:
                json.dump(sorted(characters), f, indent=2, ensure_ascii=False)
            
            print(f"✅ Removed {name} from character index")
        
        return jsonify({"status": "ok", "deleted": name})
        
    except Exception as e:
        print(f"❌ Delete failed: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
        
# --------------------------------------------------
# Token Counter for Character Editor
# --------------------------------------------------
@extra.route('/count_tokens', methods=['POST'])
def count_tokens():
    """Count tokens for character fields using existing rough_token_count."""
    try:
        import sys
        import os
        
        # Import rough_token_count from truncation.py
        sys.path.insert(0, os.path.dirname(__file__))
        from truncation import rough_token_count
        
        data = request.get_json()
        
        # Count tokens for each field
        counts = {
            'description': rough_token_count(data.get('description', '')),
            'main_prompt': rough_token_count(data.get('main_prompt', '')),
            'scenario': rough_token_count(data.get('scenario', '')),
            'example_dialogue': rough_token_count(data.get('example_dialogue', '')),
            'post_history': rough_token_count(data.get('post_history', '')),
            'character_note': rough_token_count(data.get('character_note', ''))
        }
        
        # Calculate total
        counts['total'] = sum(counts.values())
        
        return jsonify(counts)
        
    except Exception as e:
        print(f"❌ Token count error: {e}")
        return jsonify({'error': str(e)}), 500
        
        
# --------------------------------------------------
# Duplicate Character
# --------------------------------------------------
@extra.route('/duplicate_character/<name>', methods=['POST'])
def duplicate_character(name):
    """Duplicate an existing character with a new name."""
    try:
        char_dir = os.path.join(os.path.dirname(__file__), "characters")
        source_path = os.path.join(char_dir, f"{name}.json")
        
        if not os.path.exists(source_path):
            return jsonify({"error": "Character not found"}), 404
        
        # Load original character
        with open(source_path, "r", encoding="utf-8") as f:
            char_data = json.load(f)
        
        # Create new name with " - Copy" suffix
        new_name = f"{name} - Copy"
        counter = 1
        
        # If "Name - Copy" exists, try "Name - Copy 2", "Name - Copy 3", etc.
        while os.path.exists(os.path.join(char_dir, f"{new_name}.json")):
            counter += 1
            new_name = f"{name} - Copy {counter}"
        
        # Update character data with new name
        char_data["name"] = new_name
        
        # Save duplicated character
        new_path = os.path.join(char_dir, f"{new_name}.json")
        with open(new_path, "w", encoding="utf-8") as f:
            json.dump(char_data, f, indent=2, ensure_ascii=False)
        
        # Update character index
        index_path = os.path.join(char_dir, "index.json")
        if os.path.exists(index_path):
            with open(index_path, "r", encoding="utf-8") as f:
                characters = json.load(f)
            if new_name not in characters:
                characters.append(new_name)
        else:
            characters = [new_name]
        
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(sorted(characters), f, indent=2, ensure_ascii=False)
        
        print(f"📋 Duplicated character: {name} → {new_name}")
        return jsonify({"success": True, "new_name": new_name})
        
    except Exception as e:
        print(f"❌ Duplicate character failed: {e}")
        return jsonify({"error": str(e)}), 500
        
# --------------------------------------------------
# Rename Character
# --------------------------------------------------
@extra.route('/rename_character', methods=['POST'])
def rename_character():
    """Rename a character and update all associated files."""
    try:
        data = request.json
        old_name = data.get('old_name', '').strip()
        new_name = data.get('new_name', '').strip()
        
        if not old_name or not new_name:
            return jsonify({"error": "Both old and new names required"}), 400
        
        if old_name == new_name:
            return jsonify({"error": "Names are identical"}), 400
        
        char_dir = os.path.join(os.path.dirname(__file__), "characters")
        old_path = os.path.join(char_dir, f"{old_name}.json")
        new_path = os.path.join(char_dir, f"{new_name}.json")
        
        # Check if old character exists
        if not os.path.exists(old_path):
            return jsonify({"error": f"Character '{old_name}' not found"}), 404
        
        # Check if new name already exists
        if os.path.exists(new_path):
            return jsonify({"error": f"Character '{new_name}' already exists"}), 409
        
        # 1. Load and update character JSON
        with open(old_path, "r", encoding="utf-8") as f:
            char_data = json.load(f)
        
        char_data["name"] = new_name
        
        # 2. Save with new filename
        with open(new_path, "w", encoding="utf-8") as f:
            json.dump(char_data, f, indent=2, ensure_ascii=False)
        
        # 3. Delete old file
        os.remove(old_path)
        
        # 4. Update character index
        index_path = os.path.join(char_dir, "index.json")
        if os.path.exists(index_path):
            with open(index_path, "r", encoding="utf-8") as f:
                characters = json.load(f)
            
            if old_name in characters:
                characters.remove(old_name)
            
            if new_name not in characters:
                characters.append(new_name)
            
            with open(index_path, "w", encoding="utf-8") as f:
                json.dump(sorted(characters), f, indent=2, ensure_ascii=False)
        
        # 5. Rename ALL chat files for this character
        chats_dir = os.path.join(os.path.dirname(__file__), "chats")
        
        if os.path.exists(chats_dir):
            for filename in os.listdir(chats_dir):
                if filename.startswith(f"{old_name} - ") and filename.endswith(".txt"):
                    # Extract the part after "Character - "
                    suffix = filename[len(old_name) + 3:]  # +3 for " - "
                    
                    old_chat_path = os.path.join(chats_dir, filename)
                    new_chat_filename = f"{new_name} - {suffix}"
                    new_chat_path = os.path.join(chats_dir, new_chat_filename)
                    
                    os.rename(old_chat_path, new_chat_path)
                    print(f"📝 Renamed chat: {filename} → {new_chat_filename}")
        
        print(f"✅ Character renamed: {old_name} → {new_name}")
        return jsonify({"success": True, "new_name": new_name})
        
    except Exception as e:
        print(f"❌ Rename character failed: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
        
        # --------------------------------------------------
# Create New User Persona
# --------------------------------------------------
@extra.route('/create_user', methods=['POST'])
def create_user():
    """Create a new user persona."""
    try:
        data = request.get_json()
        name = data.get('name', '').strip()
        display_name = data.get('display_name', '').strip()
        bio = data.get('bio', '').strip()
        image = data.get('image', '').strip()
        
        if not name:
            return jsonify({"error": "User name is required"}), 400
        
        users_dir = os.path.join(os.path.dirname(__file__), "users")
        os.makedirs(users_dir, exist_ok=True)
        
        # Check if user already exists
        user_path = os.path.join(users_dir, f"{name}.json")
        if os.path.exists(user_path):
            return jsonify({"error": f"User '{name}' already exists"}), 409
        
        # Create user JSON
        user_data = {
            "name": name,
            "display_name": display_name or name,
            "bio": bio,
            "image": image,
            "active": False
        }
        
        with open(user_path, "w", encoding="utf-8") as f:
            json.dump(user_data, f, indent=2, ensure_ascii=False)
        
        # Update users/index.json
        index_path = os.path.join(users_dir, "index.json")
        if os.path.exists(index_path):
            with open(index_path, "r", encoding="utf-8") as f:
                users = json.load(f)
            if name not in users:
                users.append(name)
        else:
            users = [name]
        
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(sorted(users), f, indent=2, ensure_ascii=False)
        
        print(f"✅ Created new user persona: {name}")
        return jsonify({"status": "ok", "name": name})
        
    except Exception as e:
        print(f"❌ Error creating user: {e}")
        return jsonify({"error": str(e)}), 500


# --------------------------------------------------
# Delete User Persona
# --------------------------------------------------
@extra.route('/delete_user/<name>', methods=['DELETE'])
def delete_user(name):
    """Delete a user persona and associated files."""
    try:
        users_dir = os.path.join(os.path.dirname(__file__), "users")
        user_path = os.path.join(users_dir, f"{name}.json")
        
        if not os.path.exists(user_path):
            return jsonify({"error": f"User '{name}' not found"}), 404
        
        # Load user data to get image filename
        try:
            with open(user_path, "r", encoding="utf-8") as f:
                user_data = json.load(f)
                image_file = user_data.get("image", "")
        except:
            image_file = ""
        
        # Delete user JSON
        os.remove(user_path)
        print(f"🗑️ Deleted user file: {user_path}")
        
        # Delete user image if it exists
        if image_file:
            image_path = os.path.join("static", "images", image_file)
            if os.path.exists(image_path):
                os.remove(image_path)
                print(f"🗑️ Deleted user image: {image_path}")
        
        # Update users/index.json
        index_path = os.path.join(users_dir, "index.json")
        if os.path.exists(index_path):
            with open(index_path, "r", encoding="utf-8") as f:
                users = json.load(f)
            
            if name in users:
                users.remove(name)
                
            with open(index_path, "w", encoding="utf-8") as f:
                json.dump(sorted(users), f, indent=2, ensure_ascii=False)
            
            print(f"✅ Removed {name} from user index")
        
        return jsonify({"status": "ok", "deleted": name})
        
    except Exception as e:
        print(f"❌ Delete failed: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
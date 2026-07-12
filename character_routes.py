import os, json
from flask import Blueprint, request, jsonify, send_from_directory

character_bp = Blueprint('character', __name__)

# The characters/ folder sits next to this module in the app root. Defined
# locally so this blueprint has no import dependency back on app.py — mirrors
# the pattern in user_routes.py / situation_routes.py / theme_routes.py.
# chat() in app.py reads characters/<name>.json directly for its card load and
# keeps its own inline path; it never calls these route handlers, so there is
# no back-import. index.json is a derived cache also consumed by chat_routes.py
# (via file read, not a function call).
CHARACTERS_DIR = os.path.join(os.path.dirname(__file__), "characters")


# --------------------------------------------------
# Active Character — server-side shared state (desktop ↔ mobile)
# Mirrors the active-project pattern (projects/_active_project.json) so the
# last-used character follows the user across devices instead of living in
# per-device localStorage. ⚠️ This is intentionally GLOBAL — switching
# character on one device switches it everywhere. Fine for single-user use.
# --------------------------------------------------
def _active_character_state_file():
    return os.path.join(CHARACTERS_DIR, "_active_character.json")


def get_active_character():
    """Return the server-side active character name, or None. Never raises."""
    try:
        path = _active_character_state_file()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f).get("active_character")
    except Exception as e:
        print(f"⚠️ Failed to read active character: {e}")
    return None


def set_active_character(character_name):
    """Persist the server-side active character. Mirrors set_active_project()."""
    try:
        path = _active_character_state_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"active_character": character_name}, f, indent=2)
    except Exception as e:
        print(f"❌ Failed to set active character: {e}")


@character_bp.route("/active_character", methods=["GET"])
def active_character_get():
    """Return the shared active character so a client can restore it on load."""
    return jsonify({"active_character": get_active_character()})


@character_bp.route("/active_character", methods=["POST"])
def active_character_set():
    """Persist the shared active character (called when a client switches)."""
    data = request.get_json(silent=True) or {}
    name = (data.get("active_character") or data.get("character") or "").strip()
    set_active_character(name or None)
    return jsonify({"success": True, "active_character": name or None})


# --------------------------------------------------
# List Characters (for config dropdown)
# --------------------------------------------------
@character_bp.route("/list_characters", methods=["GET"])
def list_characters():
    chars = []
    char_dir = CHARACTERS_DIR
    if not os.path.exists(char_dir):
        print("⚠️ Characters directory not found:", char_dir)
        return jsonify([])

    images_dir = os.path.join(os.path.dirname(__file__), "static", "images")
    for file in os.listdir(char_dir):
        if file in ("_active_character.json", "index.json"):
            continue  # internal shared-state file / derived index, not a character
        if file.endswith(".json"):
            path = os.path.join(char_dir, file)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    continue
                name = data.get("name", file.replace(".json", ""))
                chars.append(name)

                # Self-heal slideshow dangling refs: prune images[] entries
                # whose file no longer exists in static/images, ALWAYS keeping
                # the scalar `image` carrier first (even if its file is missing)
                # so the array is never emptied of its primary. Only rewrites
                # the .json when the array actually changed — steady-state (no
                # dangling refs) does no writes. Never deletes any image file.
                if "images" in data and isinstance(data["images"], list):
                    carrier = (data.get("image") or "").strip()
                    existing = [f for f in data["images"]
                                if isinstance(f, str)
                                and os.path.isfile(os.path.join(images_dir, f))]
                    reconciled = ([carrier] if carrier else []) + \
                                 [f for f in existing if f != carrier]
                    if reconciled != data["images"]:
                        data["images"] = reconciled
                        try:
                            with open(path, "w", encoding="utf-8") as wf:
                                json.dump(data, wf, indent=2, ensure_ascii=False)
                            print(f"🧹 Pruned slideshow dangling refs for {name}: -> {reconciled}")
                        except Exception as we:
                            print(f"⚠️ Could not rewrite {file} after slideshow prune: {we}")
            except Exception as e:
                print(f"⚠️ Failed to load {file}: {e}")
                continue

    # Self-heal: rewrite characters/index.json from the directory scan so the
    # on-disk index always converges to reality. The directory is the single
    # source of truth; index.json is a derived cache that other readers still
    # consume as a JSON array of names (chat_routes.py: /chats/open ~175,
    # auto_name_chat ~491, branch_chat ~746). This subsumes the May-21 desync
    # fragility — a character present on disk but missing from the index (e.g.
    # Andromeda) is reconciled on every call.
    unique_sorted = sorted(set(chars))
    try:
        index_path = os.path.join(char_dir, "index.json")
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(unique_sorted, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ Could not rewrite characters/index.json: {e}")

    print(f"✅ /list_characters -> {unique_sorted}")
    return jsonify(unique_sorted)

# --------------------------------------------------
# Create New Character
# --------------------------------------------------
@character_bp.route("/create_character", methods=["POST"])
def create_character():
    try:
        data = request.get_json()
        name = data.get("name", "").strip()
        if not name:
            return jsonify({"status": "error", "error": "Character name required"}), 400

        char_dir = CHARACTERS_DIR
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
# Character Management
# --------------------------------------------------
@character_bp.route('/characters/<path:filename>')
def serve_characters(filename):
    return send_from_directory(CHARACTERS_DIR, filename)


@character_bp.route('/characters/<n>.json', methods=['POST'])
def save_character(n):
    try:
        data = request.get_json()
        path = os.path.join(CHARACTERS_DIR, f"{n}.json")
        # Preserve fields the config editor doesn't own, so they don't get wiped
        # on every character save:
        #   • tts_voice      — set via /character_voice, not the editor form.
        #   • system_prompt  — the per-character SP binding, owned by
        #     /character_system_prompt (the Bind button). The editor has no SP
        #     field, so when it omits the key we MUST keep the on-disk binding —
        #     otherwise an editor save reverts an explicit bind (e.g. a
        #     Nebula-bound character snapping back to GPT-4o). (changes.md.)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                preserved_keys = ["tts_voice", "system_prompt"]
                for key in preserved_keys:
                    if key in existing and key not in data:
                        data[key] = existing[key]
            except Exception:
                pass  # If we can't read existing, just save what we have
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"✅ Character saved: {path}")

        # Keep characters/index.json in sync — the editor-save path must never
        # land a .json without registering it (mirrors create_character ~5862).
        # list_characters also reconciles the index on each scan, but updating
        # here keeps it correct between scans.
        index_name = ((data.get("name") if isinstance(data, dict) else None) or n).strip()
        try:
            index_path = os.path.join(CHARACTERS_DIR, "index.json")
            if os.path.exists(index_path):
                with open(index_path, "r", encoding="utf-8") as f:
                    characters = json.load(f)
                if not isinstance(characters, list):
                    characters = []
            else:
                characters = []
            if index_name and index_name not in characters:
                characters.append(index_name)
                with open(index_path, "w", encoding="utf-8") as f:
                    json.dump(sorted(set(characters)), f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"⚠️ Could not update characters/index.json on save: {e}")

        return jsonify({"success": True})
    except Exception as e:
        print(f"❌ Failed to save character {n}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@character_bp.route('/character_voice/<n>', methods=['GET'])
def get_character_voice(n):
    """Get the saved TTS voice for a character."""
    try:
        path = os.path.join(CHARACTERS_DIR, f"{n}.json")
        if not os.path.exists(path):
            return jsonify({"voice": None})
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return jsonify({"voice": data.get("tts_voice", None)})
    except Exception as e:
        return jsonify({"voice": None})


@character_bp.route('/character_voice/<n>', methods=['POST'])
def set_character_voice(n):
    """Save TTS voice for a character — only updates tts_voice field, leaves rest intact."""
    try:
        data = request.get_json()
        voice = data.get("voice", "")
        path = os.path.join(CHARACTERS_DIR, f"{n}.json")
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


@character_bp.route('/character_system_prompt/<n>', methods=['GET'])
def get_character_system_prompt(n):
    """Get the saved system prompt template for a character."""
    try:
        path = os.path.join(CHARACTERS_DIR, f"{n}.json")
        if not os.path.exists(path):
            return jsonify({"system_prompt": None})
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return jsonify({"system_prompt": data.get("system_prompt", None)})
    except Exception as e:
        return jsonify({"system_prompt": None})

@character_bp.route('/character_system_prompt/<n>', methods=['POST'])
def set_character_system_prompt(n):
    """Save system prompt template for a character — only updates system_prompt field, leaves rest intact."""
    try:
        data = request.get_json()
        template = data.get("system_prompt", "")
        path = os.path.join(CHARACTERS_DIR, f"{n}.json")
        if not os.path.exists(path):
            return jsonify({"success": False, "error": "Character not found"}), 404
        with open(path, "r", encoding="utf-8") as f:
            char_data = json.load(f)
        char_data["system_prompt"] = template
        with open(path, "w", encoding="utf-8") as f:
            json.dump(char_data, f, indent=2, ensure_ascii=False)
        print(f"✅ System prompt saved for {n}: {template}")
        return jsonify({"success": True})
    except Exception as e:
        print(f"❌ Failed to save system prompt for {n}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# --------------------------------------------------
# Get Character Data (for auto-switching characters)
# --------------------------------------------------
@character_bp.route("/get_character/<n>")
def get_character(n):
    """
    Returns character data (JSON) for the specified character name.
    Frontend uses this when auto-switching characters from sidebar.
    """
    try:
        char_path = os.path.join(CHARACTERS_DIR, f"{n}.json")

        if not os.path.exists(char_path):
            return jsonify({"error": f"Character '{n}' not found"}), 404

        with open(char_path, "r", encoding="utf-8") as f:
            character_data = json.load(f)

        print(f"✅ Loaded character data for: {n}")
        return jsonify(character_data)

    except Exception as e:
        print(f"❌ Error loading character '{n}': {e}")
        return jsonify({"error": str(e)}), 500

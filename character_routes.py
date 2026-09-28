import os, json
from flask import Blueprint, request, jsonify, send_from_directory
from user_config import load_user_config_section, save_user_config_section

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
# Character storage keys
# A card's storage key is its filename stem (characters/<key>.json) and is its
# ONLY persistent identifier. The list, index.json, every route that loads or
# writes a card, its chats ("<key> - <title>.txt"), its image (<key>.png),
# memories/session summaries, opening lines, group assignment and the active
# character are all keyed by it. The editable "name" field is model/display
# identity only — two cards may share a Name, and changing a Name never
# changes which card is loaded, saved, renamed or deleted.
# --------------------------------------------------
CHARACTER_STATE_FILES = ("_active_character.json", "_character_groups.json", "index.json")

_INVALID_KEY_CHARS = set('<>:"/\\|?*')
_RESERVED_KEYS = {"con", "prn", "aux", "nul"} \
    | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}
_STATE_KEYS = {state_file[:-5].casefold() for state_file in CHARACTER_STATE_FILES}


def valid_character_key(key):
    """True when `key` is safe as characters/<key>.json on Windows and POSIX."""
    if not isinstance(key, str) or not key or key != key.strip() or len(key) > 120:
        return False
    if key.endswith(".") or key in (".", ".."):
        return False
    if any(char in _INVALID_KEY_CHARS or ord(char) < 32 for char in key):
        return False
    stem = key.split(".")[0].casefold()
    return stem not in _RESERVED_KEYS and key.casefold() not in _STATE_KEYS


def character_keys(char_dir=None):
    """Storage keys of every card on disk."""
    try:
        filenames = os.listdir(char_dir or CHARACTERS_DIR)
    except FileNotFoundError:
        return []
    return [
        filename[:-5] for filename in filenames
        if filename.endswith(".json") and filename not in CHARACTER_STATE_FILES
    ]


def find_character_key(key, char_dir=None):
    """The existing key matching `key` case-insensitively, or None. Keys are
    case-insensitive because the Windows filesystem is."""
    wanted = str(key or "").strip().casefold()
    if not wanted:
        return None
    return next((k for k in character_keys(char_dir) if k.casefold() == wanted), None)


def known_character_keys(char_dir=None):
    """Keys used to decide which character a chat filename belongs to: every
    card on disk plus any index.json entry (a stale entry can only make chat
    ownership more conservative, never hand a chat to a shorter key)."""
    char_dir = char_dir or CHARACTERS_DIR
    keys = character_keys(char_dir)
    try:
        with open(os.path.join(char_dir, "index.json"), "r", encoding="utf-8") as f:
            indexed = json.load(f)
    except Exception:
        indexed = []
    if isinstance(indexed, list):
        seen = {k.casefold() for k in keys}
        for entry in indexed:
            if isinstance(entry, str) and entry.strip() and entry.casefold() not in seen:
                keys.append(entry)
                seen.add(entry.casefold())
    return keys


def chat_owner_key(filename, keys):
    """Return the key (spelled as in `keys`) that owns a chat filename, or None.

    Chat files are "<key> - <title>.txt" (legacy: "<key>.txt" and
    "<key>_chat_<id>.txt"). " - " also appears inside keys ("Gemma - GPT-5")
    and titles, so a bare prefix test is ambiguous: "Gemma - GPT-5 - Hi.txt"
    starts with "Gemma - " but belongs to the "Gemma - GPT-5" card. The
    LONGEST matching key owns the file. Matching is case-insensitive, like
    the keys themselves.
    """
    if not isinstance(filename, str) or not filename.lower().endswith(".txt"):
        return None
    stem = filename[:-4].casefold()
    owner, owner_len = None, 0
    for key in keys:
        folded = str(key or "").strip().casefold()
        if len(folded) <= owner_len:
            continue
        if stem == folded or stem.startswith(folded + " - ") or stem.startswith(folded + "_chat_"):
            owner, owner_len = str(key).strip(), len(folded)
    return owner


def character_display_name(key, char_dir=None):
    """The card's model/display Name for `key`, falling back to the key."""
    try:
        with open(os.path.join(char_dir or CHARACTERS_DIR, f"{key}.json"), "r", encoding="utf-8") as f:
            data = json.load(f)
        name = data.get("name") if isinstance(data, dict) else None
        if isinstance(name, str) and name.strip():
            return name.strip()
    except Exception:
        pass
    return key


def write_character_index(char_dir=None, keys=None):
    """Rewrite characters/index.json as the sorted storage keys. index.json is a
    derived cache — the directory is the source of truth."""
    char_dir = char_dir or CHARACTERS_DIR
    keys = character_keys(char_dir) if keys is None else keys
    with open(os.path.join(char_dir, "index.json"), "w", encoding="utf-8") as f:
        json.dump(sorted(set(keys)), f, indent=2, ensure_ascii=False)


def migrate_character_key_state(old_key, new_key):
    """Carry the group assignment and the shared active character from
    `old_key` to `new_key` after a rename. Never raises."""
    try:
        groups = load_user_config_section("character_groups")
        assignments = groups.get("assignments") if isinstance(groups, dict) else None
        if isinstance(assignments, dict) and old_key in assignments:
            assignments[new_key] = assignments.pop(old_key)
            save_user_config_section("character_groups", groups)
    except Exception as e:
        print(f"⚠️ Could not move character group assignment {old_key} -> {new_key}: {e}")
    try:
        if get_active_character() == old_key:
            set_active_character(new_key)
    except Exception as e:
        print(f"⚠️ Could not move active character {old_key} -> {new_key}: {e}")


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
        return load_user_config_section("active_character").get("active_character")
    except Exception as e:
        print(f"⚠️ Failed to read active character: {e}")
    return None


def set_active_character(character_name):
    """Persist the server-side active character. Mirrors set_active_project()."""
    try:
        save_user_config_section("active_character", {"active_character": character_name})
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
# Character Groups — per-build state
# --------------------------------------------------
def _character_groups_state_file():
    return os.path.join(CHARACTERS_DIR, "_character_groups.json")


def _empty_character_groups():
    return {"groups": [], "assignments": {}, "collapsed": {}}


@character_bp.route("/character_groups", methods=["GET"])
def character_groups_get():
    """Return this build's character grouping state. Never reads browser state."""
    try:
        data = load_user_config_section("character_groups")
        if not isinstance(data, dict):
            raise ValueError("Character group state must be a JSON object")
        return jsonify({
            "groups": data.get("groups", []),
            "assignments": data.get("assignments", {}),
            "collapsed": data.get("collapsed", {})
        })
    except Exception as e:
        print(f"⚠️ Failed to read character groups: {e}")
        return jsonify(_empty_character_groups())


@character_bp.route("/character_groups", methods=["POST"])
def character_groups_save():
    """Validate and atomically save this build's character grouping state."""
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"success": False, "error": "Invalid group state"}), 400

        raw_groups = data.get("groups", [])
        raw_assignments = data.get("assignments", {})
        raw_collapsed = data.get("collapsed", {})
        if not isinstance(raw_groups, list) or not isinstance(raw_assignments, dict) or not isinstance(raw_collapsed, dict):
            return jsonify({"success": False, "error": "Invalid group state"}), 400

        groups = []
        group_ids = set()
        for raw_group in raw_groups:
            if not isinstance(raw_group, dict):
                return jsonify({"success": False, "error": "Invalid group"}), 400
            group_id = str(raw_group.get("id", "")).strip()[:80]
            name = str(raw_group.get("name", "")).strip()[:40]
            if not group_id or not name or group_id in group_ids:
                return jsonify({"success": False, "error": "Invalid or duplicate group"}), 400
            group_ids.add(group_id)
            groups.append({"id": group_id, "name": name})

        assignments = {}
        for character_name, group_id in raw_assignments.items():
            if isinstance(character_name, str) and isinstance(group_id, str) and group_id in group_ids:
                assignments[character_name[:200]] = group_id

        collapsed = {}
        allowed_sections = group_ids | {"ungrouped"}
        for section_id, is_collapsed in raw_collapsed.items():
            if section_id in allowed_sections and isinstance(is_collapsed, bool):
                collapsed[section_id] = is_collapsed

        state = {"groups": groups, "assignments": assignments, "collapsed": collapsed}
        save_user_config_section("character_groups", state)
        return jsonify({"success": True, **state})
    except Exception as e:
        print(f"❌ Failed to save character groups: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


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
        if file in CHARACTER_STATE_FILES:
            continue  # internal state files / derived index, not characters
        if file.endswith(".json"):
            path = os.path.join(char_dir, file)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    continue
                # The storage key (filename stem), never the editable Name:
                # two cards with the same Name must stay two list entries.
                name = file[:-5]
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
    # source of truth; index.json is a derived cache of storage keys that
    # chat_routes.py still reads (chat parsing, auto-name, branch). This
    # subsumes the May-21 desync fragility — a character present on disk but
    # missing from the index (e.g. Andromeda) is reconciled on every call.
    unique_sorted = sorted(set(chars))
    try:
        write_character_index(char_dir, unique_sorted)
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
        # The typed name becomes both the storage key and the initial Name. It
        # must be a safe filename, not an existing key (case-insensitive), and
        # not take over another card's chats. Imported lazily: extra_routes
        # imports this module.
        from extra_routes import CharacterKeyError, _require_unused_key
        try:
            _require_unused_key(name, app_root=os.path.dirname(char_dir))
        except CharacterKeyError as e:
            return jsonify({"status": "error", "error": str(e)}), e.status

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
            "preferred_model_id": data.get("preferred_model_id", ""),
            "image": data.get("image", "")
        }
        with open(char_path, "w", encoding="utf-8") as f:
            json.dump(char_data, f, indent=2, ensure_ascii=False)

        write_character_index(char_dir)

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
        # Saving edits an existing card identified by its key; it never creates
        # one. A stale editor (card renamed or deleted meanwhile) must not
        # resurrect the old key, and the "name" in the body is only the card's
        # display Name — it never selects or creates a file.
        if not valid_character_key(n) or not os.path.isfile(path):
            return jsonify({"success": False, "error": f"Character '{n}' not found"}), 404
        # Preserve fields the config editor doesn't own, so they don't get wiped
        # on every character save:
        #   • tts_voice      — set via /character_voice, not the editor form.
        #   • system_prompt  — the per-character SP binding, owned by
        #     /character_system_prompt (the Bind button). The editor has no SP
        #     field, so when it omits the key we MUST keep the on-disk binding —
        #     otherwise an editor save reverts an explicit bind (e.g. a
        #     Nebula-bound character snapping back to GPT-4o). (changes.md.)
        existing = {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing = json.load(f)
            #   • persistent_author_note — owned by /character_author_note.
            preserved_keys = ["tts_voice", "system_prompt", "preferred_model_id", "sentinel_integration",
                              PERSISTENT_AUTHOR_NOTE_FIELD]
            for key in preserved_keys:
                if key in existing and key not in data:
                    data[key] = existing[key]
        except Exception:
            pass  # If we can't read existing, just save what we have
        if isinstance(existing, dict) and data.get("name") != existing.get("name"):
            # The editable Name is also a legacy transcript speaker label.
            # Persist roles before replacing it, including project chats.
            from chat_routes import stabilize_chat_roles
            from chat_message_metadata import chat_directories
            keys = known_character_keys(CHARACTERS_DIR)
            for chat_dir in chat_directories(os.path.dirname(CHARACTERS_DIR)):
                if not chat_dir.is_dir():
                    continue
                for filename in os.listdir(chat_dir):
                    if chat_owner_key(filename, keys) == n:
                        try:
                            stabilize_chat_roles(str(chat_dir), filename)
                        except ValueError as exc:
                            return jsonify({"success": False, "error": str(exc)}), 409
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"✅ Character saved: {path}")
        # The key didn't change, so index.json (a list of keys) is unaffected.
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

# ── Persistent Author's Note (per character) ────────────────────────────────
# Optional. When set, /chat applies it to every chat with this character that
# has no chat-only note of its own (resolved server-side, so desktop and mobile
# agree). Stored on the card like the other per-character settings above, and
# written only through this route — the card editor preserves it untouched.
PERSISTENT_AUTHOR_NOTE_FIELD = "persistent_author_note"
PERSISTENT_AUTHOR_NOTE_MAX_CHARS = 20000


def get_character_persistent_author_note(key, char_dir=None):
    """The character's persistent Author's Note ("" when none or unknown)."""
    if not valid_character_key(key):
        return ""
    path = os.path.join(char_dir or CHARACTERS_DIR, f"{key}.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            note = json.load(f).get(PERSISTENT_AUTHOR_NOTE_FIELD, "")
    except (OSError, ValueError, AttributeError):
        return ""
    return note if isinstance(note, str) else ""


@character_bp.route('/character_author_note/<n>', methods=['GET', 'POST'])
def character_author_note(n):
    """Read, set or (blank → remove) a character's persistent Author's Note."""
    path = os.path.join(CHARACTERS_DIR, f"{n}.json")
    if not valid_character_key(n) or not os.path.isfile(path):
        return jsonify({"success": False, "error": "Character not found"}), 404
    if request.method == "GET":
        return jsonify({"character": n, "author_note": get_character_persistent_author_note(n)})
    data = request.get_json(silent=True) or {}
    note = data.get("author_note", "")
    if not isinstance(note, str):
        return jsonify({"success": False, "error": "author_note must be a string"}), 400
    if len(note) > PERSISTENT_AUTHOR_NOTE_MAX_CHARS:
        return jsonify({"success": False,
                        "error": f"Author's Note is longer than {PERSISTENT_AUTHOR_NOTE_MAX_CHARS} characters"}), 400
    try:
        with open(path, "r", encoding="utf-8") as f:
            char_data = json.load(f)
        if note.strip():
            char_data[PERSISTENT_AUTHOR_NOTE_FIELD] = note
        else:
            char_data.pop(PERSISTENT_AUTHOR_NOTE_FIELD, None)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(char_data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Failed to save persistent Author's Note for {n}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    stored = char_data.get(PERSISTENT_AUTHOR_NOTE_FIELD, "")
    print(f"📝 Persistent Author's Note {'saved' if stored else 'removed'} for {n} ({len(stored)} chars)")
    return jsonify({"success": True, "character": n, "author_note": stored})


@character_bp.route('/character_preferred_model/<n>', methods=['POST'])
def set_character_preferred_model(n):
    """Save only the optional preferred local model, leaving the rest of the card intact."""
    try:
        data = request.get_json() or {}
        preferred_model_id = str(data.get("preferred_model_id", "")).strip()
        path = os.path.join(CHARACTERS_DIR, f"{n}.json")
        if not os.path.exists(path):
            return jsonify({"success": False, "error": "Character not found"}), 404
        with open(path, "r", encoding="utf-8") as f:
            char_data = json.load(f)
        char_data["preferred_model_id"] = preferred_model_id
        with open(path, "w", encoding="utf-8") as f:
            json.dump(char_data, f, indent=2, ensure_ascii=False)
        print(f"Preferred model saved for {n}: {preferred_model_id or '(none)'}")
        return jsonify({"success": True, "preferred_model_id": preferred_model_id})
    except Exception as e:
        print(f"Failed to save preferred model for {n}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


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

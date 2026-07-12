import os, json, tempfile, shutil
from flask import Blueprint, request, jsonify, send_from_directory

user_bp = Blueprint('user', __name__)

# The users/ folder sits next to this module in the app root. Defined locally so
# this blueprint has no import dependency back on app.py — mirrors the pattern
# in situation_routes.py / theme_routes.py / tts_routes.py. (app.py keeps its
# own USERS_DIR for chat()'s persona-bio load.)
USERS_DIR = os.path.join(os.path.dirname(__file__), "users")


# --------------------------------------------------
# User Persona Management
# --------------------------------------------------
@user_bp.route('/users/<path:filename>')
def serve_user_files(filename):
    return send_from_directory(USERS_DIR, filename)



@user_bp.route('/set_active_user', methods=['POST'])
def set_active_user():
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
@user_bp.route('/get_user/<n>', methods=['GET'])
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

@user_bp.route('/save_user/<n>', methods=['POST'])
def save_user(n):
    """Save updated persona info. Uses atomic write to prevent zero-byte corruption."""
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
        # Register in users/index.json — the edit path must never leave a saved
        # persona unindexed (personas are visible only via the index / the
        # directory scan). Rebuild from the directory so this also heals any
        # pre-existing desync. (Mirrors the hardened create_user.)
        _scan_and_heal_users_index()
        print(f"Updated user persona: {n}")
        return jsonify({"status": "ok"})
    except Exception as e:
        print(f"Failed to save user {n}: {e}")
        return jsonify({"error": str(e)}), 500

def _resolve_user_image(name, stored_image, images_dir):
    """Resolve a persona avatar tolerating BOTH the new prefixed naming
    (user_{name}.png) and legacy unprefixed naming ({name}.png / the stored
    image field). New persona images get the user_ prefix (see /upload_image
    is_user branch); existing personas on disk are unprefixed and MUST keep
    resolving. Order: user_-prefixed file → stored field → unprefixed
    {name}.png → default.png. Never renames any file."""
    prefixed = f"user_{name}.png"
    if os.path.isfile(os.path.join(images_dir, prefixed)):
        return prefixed
    if stored_image and os.path.isfile(os.path.join(images_dir, stored_image)):
        return stored_image
    legacy = f"{name}.png"
    if os.path.isfile(os.path.join(images_dir, legacy)):
        return legacy
    return "default.png"


def _scan_and_heal_users_index():
    """Directory-scan users/*.json and rewrite users/index.json from
    sorted(set(names)). The users/ folder is the single source of truth;
    index.json is a derived cache. This self-heals any JSON-without-index
    orphan (same convergence as list_characters). Returns
    (sorted_names, {name: stored_image_field})."""
    names = []
    stored = {}
    if not os.path.isdir(USERS_DIR):
        return names, stored
    for fn in os.listdir(USERS_DIR):
        if fn.startswith("_") or fn == "index.json" or not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(USERS_DIR, fn), "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                continue
            nm = data.get("name", fn[:-5])
            names.append(nm)
            stored[nm] = (data.get("image") or "").strip()
        except Exception as e:
            print(f"⚠️ Failed to load user {fn}: {e}")
    unique_sorted = sorted(set(names))
    try:
        with open(os.path.join(USERS_DIR, "index.json"), "w", encoding="utf-8") as f:
            json.dump(unique_sorted, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ Could not rewrite users/index.json: {e}")
    return unique_sorted, stored


@user_bp.route("/list_users", methods=["GET"])
def list_users():
    """Directory-scan personas, heal users/index.json, return sorted names.
    Mirrors list_characters — the index can never make a persona invisible."""
    names, _ = _scan_and_heal_users_index()
    print(f"✅ /list_users -> {names}")
    return jsonify(names)


@user_bp.route("/get_all_users", methods=["GET"])
def get_all_users():
    """Return a dict of all users with their resolved image filenames. Driven
    by the directory scan (which also heals users/index.json), so a persona is
    never invisible due to index desync, and avatars tolerate both prefixed
    and legacy-unprefixed image naming."""
    try:
        images_dir = os.path.join(os.path.dirname(__file__), "static", "images")
        names, stored = _scan_and_heal_users_index()
        result = {}
        for name in names:
            result[name] = _resolve_user_image(name, stored.get(name, ""), images_dir)
        return jsonify(result)
    except Exception as e:
        print(f"⚠️ Failed to load all users: {e}")
        return jsonify({})


@user_bp.route('/get_active_user', methods=['GET'])
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

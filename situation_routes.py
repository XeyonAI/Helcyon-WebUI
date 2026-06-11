import os, json
from flask import Blueprint, request, jsonify

situation_bp = Blueprint('situation', __name__)

# settings.json sits next to this module in the app root. Defined locally so
# this blueprint has no import dependency back on app.py — mirrors the pattern
# in theme_routes.py / tts_routes.py.
SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "settings.json")


# --------------------------------------------------
# Current Situation Routes
# --------------------------------------------------
@situation_bp.route("/get_current_situation", methods=["GET"])
def get_current_situation():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            s = json.load(f)
        return jsonify({"current_situation": s.get("current_situation", "")})
    except Exception as e:
        return jsonify({"current_situation": "", "error": str(e)})

@situation_bp.route("/save_current_situation", methods=["POST"])
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


# --------------------------------------------------
# Global Example Dialog Routes
# --------------------------------------------------
@situation_bp.route("/get_global_example_dialog", methods=["GET"])
def get_global_example_dialog():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            s = json.load(f)
        return jsonify({"global_example_dialog": s.get("global_example_dialog", "")})
    except Exception as e:
        return jsonify({"global_example_dialog": "", "error": str(e)})

@situation_bp.route("/save_global_example_dialog", methods=["POST"])
def save_global_example_dialog():
    data = request.get_json()
    dialog = data.get("global_example_dialog", "").strip()
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            s = json.load(f)
        s["global_example_dialog"] = dialog
        import tempfile, shutil
        tmp = SETTINGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2)
        shutil.move(tmp, SETTINGS_FILE)
        print(f"✅ Global example dialog saved ({len(dialog)} chars)")
        return jsonify({"status": "ok"})
    except Exception as e:
        print(f"❌ save_global_example_dialog failed: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500

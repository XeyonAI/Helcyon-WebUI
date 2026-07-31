from flask import Blueprint, request, jsonify
import os, json, re

theme_bp = Blueprint('theme', __name__)

# settings.json lives in the project root next to this module (same dir as app.py)
SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "settings.json")

THEMES_DIR = os.path.join(os.path.dirname(__file__), "themes")

# This is the free (GitHub) build of HWUI. Users may switch between the two
# included themes, but creating/editing/saving/deleting themes or presets
# (the Theme Editor) is Pro-only.
FREE_THEMES = {"claude", "gemini"}


def _pro_only():
    return jsonify({
        "error": "The Theme Editor is available in HWUI Pro.",
        "pro_required": True,
    }), 403

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

@theme_bp.route("/get_theme", methods=["GET"])
def get_theme():
    """Read CSS custom properties — style.css defaults first, active theme overlaid on top."""
    try:
        vars_dict = {}

        # Step 1: seed defaults from style.css :root so every variable has a value
        style_path = os.path.join(os.path.dirname(__file__), "style.css")
        if os.path.exists(style_path):
            with open(style_path, "r", encoding="utf-8") as f:
                style_css = f.read()
            for match in re.finditer(r'(--[\w-]+)\s*:\s*([^;]+);', style_css):
                vars_dict[match.group(1).strip()] = match.group(2).strip()

        # Step 2: overlay active theme file (adds/overwrites theme-specific values)
        path = get_active_theme_path()
        if not os.path.exists(path):
            for fallback in ["theme.css", "style.css"]:
                fb = os.path.join(os.path.dirname(__file__), fallback)
                if os.path.exists(fb):
                    path = fb
                    break
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                css = f.read()
            for match in re.finditer(r'(--[\w-]+)\s*:\s*([^;]+);', css):
                vars_dict[match.group(1).strip()] = match.group(2).strip()

        return jsonify(vars_dict)
    except Exception as e:
        print(f"❌ get_theme failed: {e}")
        return jsonify({"error": str(e)}), 500

@theme_bp.route("/save_theme", methods=["POST"])
def save_theme():
    """Custom theme colour editing is Pro-only in the free build."""
    return _pro_only()

@theme_bp.route("/save_bg", methods=["POST"])
def save_bg():
    """Custom theme editing (background image) is Pro-only in the free build."""
    return _pro_only()

@theme_bp.route("/clear_bg", methods=["POST"])
def clear_bg():
    """Custom theme editing (background image) is Pro-only in the free build."""
    return _pro_only()

@theme_bp.route("/themes/list", methods=["GET"])
def list_themes():
    """List the themes available to switch between. Restricted to the two
    included free themes even if extra theme files are present on disk."""
    try:
        os.makedirs(THEMES_DIR, exist_ok=True)
        on_disk = {f[:-4] for f in os.listdir(THEMES_DIR) if f.endswith('.css')}
        themes = sorted(on_disk & FREE_THEMES)
        active = get_active_theme_name()
        if active not in FREE_THEMES:
            active = themes[0] if themes else active
        return jsonify({"themes": themes, "active": active})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@theme_bp.route("/themes/switch", methods=["POST"])
def switch_theme():
    """Switch active theme. Free build: limited to the two included themes."""
    try:
        name = request.get_json().get("name", "").strip()
        if not name or not re.match(r'^[\w\- ]+$', name):
            return jsonify({"error": "Invalid theme name"}), 400
        if name not in FREE_THEMES:
            return _pro_only()
        path = os.path.join(THEMES_DIR, f"{name}.css")
        if not os.path.exists(path):
            return jsonify({"error": f"Theme '{name}' not found"}), 404
        set_active_theme_name(name)
        print(f"✅ Switched to theme: {name}")
        return jsonify({"status": "ok", "active": name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@theme_bp.route("/themes/create", methods=["POST"])
def create_theme():
    """Creating custom themes is Pro-only in the free build."""
    return _pro_only()

@theme_bp.route("/themes/delete", methods=["POST"])
def delete_theme():
    """Deleting themes is Pro-only in the free build."""
    return _pro_only()

THEME_PRESETS_FILE = "theme_presets.json"

def load_theme_presets():
    if os.path.exists(THEME_PRESETS_FILE):
        with open(THEME_PRESETS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

@theme_bp.route("/theme_presets", methods=["GET"])
def get_theme_presets():
    return jsonify(load_theme_presets())

@theme_bp.route("/theme_presets/save", methods=["POST"])
def save_theme_preset():
    """Saving/exporting custom colour presets is Pro-only in the free build."""
    return _pro_only()

@theme_bp.route("/theme_presets/delete", methods=["POST"])
def delete_theme_preset():
    """Managing custom colour presets is Pro-only in the free build."""
    return _pro_only()

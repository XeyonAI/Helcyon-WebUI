from flask import Blueprint, request, jsonify
import os, json, re

theme_bp = Blueprint('theme', __name__)

# settings.json lives in the project root next to this module (same dir as app.py)
SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "settings.json")

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
    """Write updated CSS custom properties into :root in the active theme file."""
    try:
        data = request.get_json() or {}
        path = get_active_theme_path()
        print(f"💾 save_theme: writing to {path}, {len(data)} variables")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                css = f.read()
        else:
            print(f"⚠️  save_theme: file not found, creating new")
            css = ":root {\n}\n"

        if "--app-font-family" not in data:
            font_match = re.search(r'--app-font-family\s*:\s*([^;]+);', css)
            if font_match:
                data["--app-font-family"] = font_match.group(1).strip()
            else:
                data["--app-font-family"] = '"Segoe UI", Tahoma, Geneva, Verdana, sans-serif'

        # Build fresh :root block from incoming data
        root_vars = "\n".join(f"  {var}: {value};" for var, value in data.items())
        root_block = f":root {{\n{root_vars}\n}}"

        # Replace existing :root block if present, otherwise prepend one
        root_match = re.search(r":root\s*\{[^}]*\}", css, re.DOTALL)
        if root_match:
            css = css[:root_match.start()] + root_block + css[root_match.end():]
        else:
            # Insert after opening comment block if present
            comment_match = re.match(r"\s*/\*.*?\*/", css, re.DOTALL)
            insert_at = comment_match.end() if comment_match else 0
            css = css[:insert_at].rstrip() + "\n\n" + root_block + "\n\n" + css[insert_at:].lstrip()

        with open(path, "w", encoding="utf-8") as f:
            f.write(css)
        print(f"✅ Theme saved to {os.path.basename(path)}: {len(data)} vars")
        return jsonify({"status": "ok"})
    except Exception as e:
        print(f"❌ save_theme failed: {e}")
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@theme_bp.route("/save_bg", methods=["POST"])
def save_bg():
    """Save an uploaded background image to static/ as a real file and return
    its URL. Storing the image as a file (not base64) avoids the localStorage
    ~5MB quota that silently broke large wallpapers."""
    try:
        if 'file' not in request.files:
            return jsonify({"error": "No file provided"}), 400
        file = request.files['file']
        if not file.filename:
            return jsonify({"error": "No file selected"}), 400
        ext = os.path.splitext(file.filename)[1].lower() or '.jpg'
        if ext not in ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp'):
            return jsonify({"error": f"Unsupported image type: {ext}"}), 400
        import glob as _glob
        static_dir = os.path.join(os.path.dirname(__file__), "static")
        os.makedirs(static_dir, exist_ok=True)
        # Drop any previous background file (any extension) so old ones don't orphan
        for old in _glob.glob(os.path.join(static_dir, "hwui-bg.*")):
            try:
                os.remove(old)
            except Exception:
                pass
        save_name = f"hwui-bg{ext}"
        file.save(os.path.join(static_dir, save_name))
        print(f"🖼️ Background image saved: static/{save_name}")
        return jsonify({"status": "ok", "url": f"/static/{save_name}"})
    except Exception as e:
        print(f"❌ save_bg failed: {e}")
        return jsonify({"error": str(e)}), 500

@theme_bp.route("/clear_bg", methods=["POST"])
def clear_bg():
    """Delete the saved background image file(s)."""
    try:
        import glob as _glob
        static_dir = os.path.join(os.path.dirname(__file__), "static")
        for old in _glob.glob(os.path.join(static_dir, "hwui-bg.*")):
            try:
                os.remove(old)
            except Exception:
                pass
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@theme_bp.route("/themes/list", methods=["GET"])
def list_themes():
    """List all available theme files."""
    try:
        os.makedirs(THEMES_DIR, exist_ok=True)
        themes = sorted([f[:-4] for f in os.listdir(THEMES_DIR) if f.endswith('.css')])
        return jsonify({"themes": themes, "active": get_active_theme_name()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@theme_bp.route("/themes/switch", methods=["POST"])
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

@theme_bp.route("/themes/create", methods=["POST"])
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

@theme_bp.route("/themes/delete", methods=["POST"])
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

@theme_bp.route("/theme_presets", methods=["GET"])
def get_theme_presets():
    return jsonify(load_theme_presets())

@theme_bp.route("/theme_presets/save", methods=["POST"])
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

@theme_bp.route("/theme_presets/delete", methods=["POST"])
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

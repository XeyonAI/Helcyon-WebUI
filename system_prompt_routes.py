from flask import Blueprint, request, jsonify
import os, json

sysprompt_bp = Blueprint('sysprompt', __name__)

# --------------------------------------------------
# System Prompt Template Routes
# --------------------------------------------------

def get_system_prompts_dir():
    return os.path.join(os.path.dirname(__file__), 'system_prompts')

def get_active_prompt_filename():
    try:
        with open('settings.json', 'r', encoding='utf-8') as f:
            return json.load(f).get('active_system_prompt', 'default.txt')
    except Exception:
        return 'default.txt'

def set_active_prompt_filename(filename):
    try:
        with open('settings.json', 'r', encoding='utf-8') as f:
            s = json.load(f)
    except Exception:
        s = {}
    s['active_system_prompt'] = filename
    with open('settings.json', 'w', encoding='utf-8') as f:
        json.dump(s, f, indent=2)


# The fixed fallback template for a character with NO bound system prompt.
# ⚠️ This is deliberately a STABLE default ('default.txt') — NOT the globally
# "active" editor template (settings.json → active_system_prompt). An unbound
# character must resolve to a predictable default, not silently inherit
# whatever template was last activated in the SP editor (which is how an
# unbound character ended up running on Claude.txt). (changes.md.)
DEFAULT_SYSTEM_PROMPT = 'default.txt'


def resolve_character_prompt_files(char_data):
    """Resolve the system-prompt + paired example / post-history filenames for
    a character, applying the canonical resolution chain:
      per-character bound filename → DEFAULT_SYSTEM_PROMPT ('default.txt').

    char_data may be None or {} — handled gracefully (→ default.txt).
    A None-valued or whitespace-only "system_prompt" field also falls back.

    Returns (sp_filename, example_filename, posthistory_filename) as bare
    filenames (NOT full paths) — callers join with get_system_prompts_dir().
    The paired files share the SP stem: e.g. GPT-4o.txt → GPT-4o.example.txt,
    GPT-4o.posthistory.txt.

    ⚠️ Any route that loads a character SP or its paired files MUST call this —
    do NOT re-inline the resolution chain. Inline duplication is exactly how
    the /continue route silently drifted to global-only SP. (changes.md.)
    """
    char_sp = ((char_data or {}).get("system_prompt") or "").strip()
    sp_filename = char_sp or DEFAULT_SYSTEM_PROMPT
    base = sp_filename.rsplit('.', 1)[0] if '.' in sp_filename else sp_filename
    return sp_filename, base + '.example.txt', base + '.posthistory.txt'

@sysprompt_bp.route('/system_prompts/list', methods=['GET'])
def list_system_prompts():
    folder = get_system_prompts_dir()
    os.makedirs(folder, exist_ok=True)
    files = sorted([
        f for f in os.listdir(folder)
        if f.endswith('.txt')
        and not f.endswith('.example.txt')
        and not f.endswith('.posthistory.txt')
    ])
    active = get_active_prompt_filename()
    return jsonify({'files': files, 'active': active})

@sysprompt_bp.route('/system_prompts/load/<filename>', methods=['GET'])
def load_system_prompt_file(filename):
    if '..' in filename or '/' in filename or os.sep in filename:
        return jsonify({'error': 'Invalid filename'}), 400
    folder = get_system_prompts_dir()
    path = os.path.join(folder, filename)
    if not os.path.exists(path):
        return jsonify({'error': 'File not found'}), 404
    with open(path, 'r', encoding='utf-8') as f:
        return f.read(), 200, {'Content-Type': 'text/plain; charset=utf-8'}

@sysprompt_bp.route('/system_prompts/save/<filename>', methods=['POST'])
def save_system_prompt_file(filename):
    if '..' in filename or '/' in filename or os.sep in filename:
        return jsonify({'error': 'Invalid filename'}), 400
    folder = get_system_prompts_dir()
    os.makedirs(folder, exist_ok=True)
    data = request.get_data(as_text=True)
    path = os.path.join(folder, filename)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(data)
    print(f'✅ Saved system prompt: {filename}')
    return jsonify({'status': 'saved', 'filename': filename})

@sysprompt_bp.route('/system_prompts/activate/<filename>', methods=['POST'])
def activate_system_prompt(filename):
    if '..' in filename or '/' in filename or os.sep in filename:
        return jsonify({'error': 'Invalid filename'}), 400
    folder = get_system_prompts_dir()
    path = os.path.join(folder, filename)
    if not os.path.exists(path):
        return jsonify({'error': 'File not found'}), 404
    set_active_prompt_filename(filename)
    print(f'✅ Active system prompt set to: {filename}')
    return jsonify({'status': 'ok', 'active': filename})

@sysprompt_bp.route('/system_prompts/delete/<filename>', methods=['POST'])
def delete_system_prompt(filename):
    if '..' in filename or '/' in filename or os.sep in filename:
        return jsonify({'error': 'Invalid filename'}), 400
    folder = get_system_prompts_dir()
    path = os.path.join(folder, filename)
    if not os.path.exists(path):
        return jsonify({'error': 'File not found'}), 404
    os.remove(path)
    # Clean up the paired post-history file so it doesn't orphan
    _base = filename.rsplit('.', 1)[0] if '.' in filename else filename
    _ph_path = os.path.join(folder, _base + '.posthistory.txt')
    if os.path.exists(_ph_path):
        os.remove(_ph_path)
        print(f'🗑️ Deleted paired post-history: {_base}.posthistory.txt')
    # If deleted file was active, fall back to default.txt
    if get_active_prompt_filename() == filename:
        set_active_prompt_filename('default.txt')
    print(f'🗑️ Deleted system prompt: {filename}')
    return jsonify({'status': 'deleted'})

@sysprompt_bp.route('/system_prompts/load_example/<filename>', methods=['GET'])
def load_system_prompt_example(filename):
    """Load the paired .example.txt for a system prompt template."""
    if '..' in filename or '/' in filename or os.sep in filename:
        return jsonify({'error': 'Invalid filename'}), 400
    # Strip existing extension and add .example.txt
    base = filename.rsplit('.', 1)[0] if '.' in filename else filename
    example_filename = base + '.example.txt'
    folder = get_system_prompts_dir()
    path = os.path.join(folder, example_filename)
    if not os.path.exists(path):
        return '', 200, {'Content-Type': 'text/plain; charset=utf-8'}  # empty = none yet
    with open(path, 'r', encoding='utf-8') as f:
        return f.read(), 200, {'Content-Type': 'text/plain; charset=utf-8'}

@sysprompt_bp.route('/system_prompts/save_example/<filename>', methods=['POST'])
def save_system_prompt_example(filename):
    """Save the paired .example.txt for a system prompt template.
    If content is empty, deletes the file rather than writing a blank one."""
    if '..' in filename or '/' in filename or os.sep in filename:
        return jsonify({'error': 'Invalid filename'}), 400
    base = filename.rsplit('.', 1)[0] if '.' in filename else filename
    example_filename = base + '.example.txt'
    folder = get_system_prompts_dir()
    os.makedirs(folder, exist_ok=True)
    data = request.get_data(as_text=True).strip()
    path = os.path.join(folder, example_filename)
    if not data:
        # Empty content — delete the file if it exists, don't create a blank one
        if os.path.exists(path):
            os.remove(path)
            print(f'🗑️ Deleted empty example dialog: {example_filename}')
        return jsonify({'status': 'saved', 'filename': example_filename})
    with open(path, 'w', encoding='utf-8') as f:
        f.write(data)
    print(f'✅ Saved example dialog: {example_filename}')
    return jsonify({'status': 'saved', 'filename': example_filename})

@sysprompt_bp.route('/system_prompts/load_posthistory/<filename>', methods=['GET'])
def load_system_prompt_posthistory(filename):
    """Load the paired .posthistory.txt for a system prompt template.
    This is the SillyTavern-style post-history directive — it rides the [OOC]
    depth-0 packet (last item, closest to generation) rather than the system
    block, but it is stored alongside its template so switching templates
    switches the directive."""
    if '..' in filename or '/' in filename or os.sep in filename:
        return jsonify({'error': 'Invalid filename'}), 400
    base = filename.rsplit('.', 1)[0] if '.' in filename else filename
    ph_filename = base + '.posthistory.txt'
    folder = get_system_prompts_dir()
    path = os.path.join(folder, ph_filename)
    if not os.path.exists(path):
        return '', 200, {'Content-Type': 'text/plain; charset=utf-8'}  # empty = none yet
    with open(path, 'r', encoding='utf-8') as f:
        return f.read(), 200, {'Content-Type': 'text/plain; charset=utf-8'}

@sysprompt_bp.route('/system_prompts/save_posthistory/<filename>', methods=['POST'])
def save_system_prompt_posthistory(filename):
    """Save the paired .posthistory.txt for a system prompt template.
    If content is empty, deletes the file rather than writing a blank one."""
    if '..' in filename or '/' in filename or os.sep in filename:
        return jsonify({'error': 'Invalid filename'}), 400
    base = filename.rsplit('.', 1)[0] if '.' in filename else filename
    ph_filename = base + '.posthistory.txt'
    folder = get_system_prompts_dir()
    os.makedirs(folder, exist_ok=True)
    data = request.get_data(as_text=True).strip()
    path = os.path.join(folder, ph_filename)
    if not data:
        # Empty content — delete the file if it exists, don't create a blank one
        if os.path.exists(path):
            os.remove(path)
            print(f'🗑️ Deleted empty post-history: {ph_filename}')
        return jsonify({'status': 'saved', 'filename': ph_filename})
    with open(path, 'w', encoding='utf-8') as f:
        f.write(data)
    print(f'✅ Saved post-history directive: {ph_filename}')
    return jsonify({'status': 'saved', 'filename': ph_filename})

# Legacy route - kept for backwards compatibility
@sysprompt_bp.route('/system_prompt.txt', methods=['GET', 'POST'])
def system_prompt():
    folder = get_system_prompts_dir()
    active = get_active_prompt_filename()
    file_path = os.path.join(folder, active)

    if request.method == 'POST':
        try:
            os.makedirs(folder, exist_ok=True)
            data = request.get_data(as_text=True)
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(data)
            print(f"✅ Saved active system prompt: {active}")
            return jsonify({'status': 'saved'})
        except Exception as e:
            print(f"❌ System prompt save failed: {e}")
            return jsonify({'error': str(e)}), 500

    if not os.path.exists(file_path):
        return jsonify({'error': 'Active system prompt file not found'}), 404

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            return content, 200, {'Content-Type': 'text/plain; charset=utf-8'}
    except Exception as e:
        print(f"❌ System prompt load failed: {e}")
        return jsonify({'error': str(e)}), 500

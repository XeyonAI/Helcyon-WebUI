"""
strip_for_free.py
=================
Run this script from the Helcyon-WebUI free repo folder, or from the
parent folder that contains Helcyon-WebUI, to strip Pro-only features
and replace them with upgrade prompts.

Usage:
    python strip_for_free.py

What it does:
    - app.py        : Removes session summary code (functions, route, injection)
    - index.html    : Replaces Memory/End Session buttons with Go Pro upsell
    - config.html   : Hides memory controls, shows Go Pro banner
    - mobile.html   : Replaces memory-summary actions with Pro notices
    - settings.default.json : Keeps automatic memory disabled by default

Safe to run multiple times — all operations are idempotent.
"""

import os
import sys
import py_compile
import json

try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except AttributeError:
    pass

# ── Paths ────────────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if os.path.exists(os.path.join(SCRIPT_DIR, "app.py")):
    BASE_DIR = SCRIPT_DIR
else:
    BASE_DIR = os.path.join(SCRIPT_DIR, "Helcyon-WebUI")
APP_PY = os.path.join(BASE_DIR, 'app.py')
INDEX_HTML = os.path.join(BASE_DIR, 'templates', 'index.html')
CONFIG_HTML = os.path.join(BASE_DIR, 'templates', 'config.html')
MOBILE_HTML = os.path.join(BASE_DIR, 'templates', 'mobile.html')
SETTINGS_DEFAULT_JSON = os.path.join(BASE_DIR, 'settings.default.json')

# Fall back to root-level HTML if not in templates/
if not os.path.exists(INDEX_HTML):
    INDEX_HTML = os.path.join(BASE_DIR, 'index.html')
if not os.path.exists(CONFIG_HTML):
    CONFIG_HTML = os.path.join(BASE_DIR, 'config.html')
if not os.path.exists(MOBILE_HTML):
    MOBILE_HTML = os.path.join(BASE_DIR, 'mobile.html')

ERRORS = []
CHANGES = []


def check_files():
    missing = []
    for path in [APP_PY, INDEX_HTML, CONFIG_HTML, MOBILE_HTML, SETTINGS_DEFAULT_JSON]:
        if not os.path.exists(path):
            missing.append(path)
    if missing:
        print("❌ Missing files:")
        for f in missing:
            print(f"   {f}")
        sys.exit(1)





# ── app.py ───────────────────────────────────────────────────────────────────

def strip_app_py():
    with open(APP_PY, 'r', encoding='utf-8') as f:
        content = f.read()

    original = content

    # 1. Remove the session summary block (constants + functions + route)
    start = content.find('\n# --------------------------------------------------\n# Session Summary — load/save helpers')
    end = content.find('\n\n# --------------------------------------------------\n# Delete Last N Messages', start)

    if start != -1 and end != -1:
        content = content[:start] + content[end:]
        CHANGES.append("app.py: session summary block removed")
    elif 'SESSION_SUMMARY_DIR' not in content and 'load_session_summary' not in content:
        CHANGES.append("app.py: session summary block already absent (legacy)")
    else:
        ERRORS.append("app.py: could not find session summary block boundaries")

    # 2. Remove the injection block dynamically
    # Finds _is_new_chat block and strips everything up to the next known marker
    is_new_chat_start = content.find('        _is_new_chat = (\n            len(assistant_msgs) == 0')

    after_markers = [
        '\n        char_context = "\n\n".join(parts)',
        '\n        char_context =',
        '\n        # character_note and author_note are no longer added here',
        '\n        # Build char_context',
    ]

    if is_new_chat_start != -1 and 'load_session_summary(character_name)' in content:
        end_pos = -1
        for marker in after_markers:
            pos = content.find(marker, is_new_chat_start)
            if pos != -1:
                if end_pos == -1 or pos < end_pos:
                    end_pos = pos

        if end_pos != -1:
            new_block = '''        _is_new_chat = (
            len(assistant_msgs) == 0 or
            (len(assistant_msgs) == 1 and _is_opening_line_msg(assistant_msgs[0]))
        )'''
            content = content[:is_new_chat_start] + new_block + content[end_pos:]
            CHANGES.append("app.py: session summary injection block removed")
        else:
            ERRORS.append("app.py: found load_session_summary call but could not find end of injection block")
    elif 'load_session_summary(character_name)' not in content:
        CHANGES.append("app.py: injection block already absent")

    # 2b. NEW: Remove session_summary_routes blueprint import + registration
    # (refactored out of app.py in later versions)
    if 'from session_summary_routes import session_summary_bp' in content:
        content = content.replace(
            'from session_summary_routes import session_summary_bp\n', '')
        CHANGES.append("app.py: session_summary_bp import removed")
    if 'from session_summary_routes import select_session_summaries, SESSION_DIVIDER\n' in content:
        # Also remove the comment above it
        content = content.replace(
            '# select_session_summaries + SESSION_DIVIDER live in session_summary_routes but\n'
            '# are called directly by chat() in this module — import them back.\n'
            'from session_summary_routes import select_session_summaries, SESSION_DIVIDER\n',
            '')
        if 'from session_summary_routes import select_session_summaries, SESSION_DIVIDER' in content:
            content = content.replace(
                'from session_summary_routes import select_session_summaries, SESSION_DIVIDER\n', '')
        CHANGES.append("app.py: select_session_summaries import removed")
    if 'app.register_blueprint(session_summary_bp)' in content:
        content = content.replace(
            '    app.register_blueprint(session_summary_bp)\n', '')
        content = content.replace(
            'app.register_blueprint(session_summary_bp)\n', '')
        CHANGES.append("app.py: session_summary_bp blueprint registration removed")

    # 2c. Remove _recent_session_summary tail injection block
    ss_inject_start = content.find('\n    if _recent_session_summary and messages and messages[0].get("role") == "system":')
    ss_inject_end = content.find('\n    # 🎭 INJECT EXAMPLE DIALOGUE', ss_inject_start) if ss_inject_start != -1 else -1
    if ss_inject_start != -1 and ss_inject_end != -1:
        content = content[:ss_inject_start] + content[ss_inject_end:]
        CHANGES.append("app.py: _recent_session_summary tail injection removed")
    elif '_recent_session_summary' not in content:
        CHANGES.append("app.py: _recent_session_summary injection already absent")
    else:
        CHANGES.append("app.py: _recent_session_summary references remain (harmless stubs)")

    # 2d. Neutralise _recent_session_summary in _build_system_text unpacking
    # (keep var count the same to avoid touching _build_system_text return)
    old_unpack = '    system_text, char_context, user_context, _recent_session_summary, _recent_session_ts, _is_jinja_model = _build_system_text('
    new_unpack = '    system_text, char_context, user_context, _recent_session_summary, _recent_session_ts, _is_jinja_model = _build_system_text('
    # Already handled by tail injection removal — just mark as done if present
    if old_unpack in content:
        CHANGES.append("app.py: _build_system_text unpacking unchanged (session summary vars inert after tail removal)")
    elif old_unpack not in content and '_recent_session_summary' not in [l for l in content.split('\n') if not l.strip().startswith('#')][0]:
        CHANGES.append("app.py: _build_system_text unpacking already clean")

    # 3. Verify session summary clean
    remaining = (content.count('load_session_summary') +
                 content.count('SESSION_SUMMARY_DIR') +
                 content.count('generate_session_summary'))
    if remaining > 0:
        ERRORS.append(f"app.py: {remaining} session summary reference(s) remain after strip")

    # 4. Remove _parse_memory_blocks and _kw_match helper functions
    # They sit between a comment block and do_chat_search
    mem_parse_start = content.find('\ndef _parse_memory_blocks(text):')
    mem_parse_end = content.find('\ndef do_chat_search(')
    if mem_parse_start != -1 and mem_parse_end != -1:
        content = content[:mem_parse_start] + content[mem_parse_end:]
        CHANGES.append("app.py: _parse_memory_blocks and _kw_match removed")
    elif '_parse_memory_blocks' not in '\n'.join(
        l for l in content.split('\n') if not l.strip().startswith('#')
    ):
        CHANGES.append("app.py: _parse_memory_blocks already absent")
    else:
        ERRORS.append("app.py: could not find _parse_memory_blocks block boundaries")

    # 5. Remove /append_character_memory route
    append_start = content.find('\n# Append a New Memory Block\n# --------------------------------------------------\n@app.route(\'/append_character_memory\'')
    if append_start == -1:
        append_start = content.find('\n@app.route(\'/append_character_memory\'')
    append_end = content.find('\n# --------------------------------------------------\n# Chat Endpoint', append_start) if append_start != -1 else -1
    if append_start != -1 and append_end != -1:
        content = content[:append_start] + content[append_end:]
        CHANGES.append("app.py: /append_character_memory route removed")
    elif 'append_character_memory' not in content:
        CHANGES.append("app.py: /append_character_memory already absent")
    else:
        ERRORS.append("app.py: could not find /append_character_memory route boundaries")

    # 6. Remove memory loading + injection block inside /chat route
    # Starts at "# Load memory file and find relevant block"
    # Ends just before "# Build unified prompt"
    mem_load_start = content.find('\n    # --------------------------------------------------\n    # Load memory file and find relevant block')
    mem_load_end = content.find('\n    # --------------------------------------------------\n    # Build unified prompt')
    if mem_load_start != -1 and mem_load_end != -1:
        # Replace with stub: memory = ""
        content = content[:mem_load_start] + '\n    memory = ""\n' + content[mem_load_end:]
        CHANGES.append("app.py: memory loading/injection block replaced with memory = \"\"")
    elif 'load_character_memory' not in content:
        CHANGES.append("app.py: memory injection block already absent")
    else:
        ERRORS.append("app.py: could not find memory injection block boundaries")

    # 7. Remove character memory management routes
    # get_character_memory, delete_character_memory, add_character_memory, edit_character_memory
    mem_routes_start = content.find('\n# --- CHARACTER MEMORY MANAGEMENT ---\n@app.route(\"/get_character_memory\")')
    if mem_routes_start == -1:
        mem_routes_start = content.find('\n@app.route(\"/get_character_memory\")')
    mem_routes_end = content.find('\n# --------------------------------------------------\n', content.find('@app.route(\"/edit_character_memory\"') if '@app.route(\"/edit_character_memory\"' in content else (mem_routes_start + 1 if mem_routes_start != -1 else 0))
    if mem_routes_start != -1 and mem_routes_end != -1:
        content = content[:mem_routes_start] + content[mem_routes_end:]
        CHANGES.append("app.py: memory management routes removed (get/delete/add/edit_character_memory)")
    elif 'get_character_memory' not in content:
        CHANGES.append("app.py: memory management routes already absent")
    else:
        ERRORS.append("app.py: could not find memory management routes boundaries")

    # 8. Verify memory/session fully gone (ignore comment lines)
    code_lines = [l for l in content.split('\n') if not l.strip().startswith('#')]
    code_only = '\n'.join(code_lines)
    mem_remaining = (code_only.count('append_character_memory') +
                     code_only.count('get_character_memory') +
                     code_only.count('_parse_memory_blocks') +
                     code_only.count('load_character_memory') +
                     code_only.count('showMemoryConfirmInBubble') +
                     code_only.count('session_summary_bp') +
                     code_only.count('select_session_summaries'))
    if mem_remaining > 0:
        ERRORS.append(f"app.py: {mem_remaining} memory/session reference(s) remain after strip — manual check needed")

    if content != original:
        with open(APP_PY, 'w', encoding='utf-8') as f:
            f.write(content)

    # 9. Syntax check
    try:
        py_compile.compile(APP_PY, doraise=True)
        CHANGES.append("app.py: syntax OK")
    except py_compile.PyCompileError as e:
        ERRORS.append(f"app.py: SYNTAX ERROR — {e}")


# ── index.html ───────────────────────────────────────────────────────────────

UPGRADE_MODAL = '''<!-- Upgrade to Pro Modal -->
<div id="upgrade-modal" class="modal">
  <div class="modal-content">
    <div class="modal-header">
      <h3>Upgrade to HWUI Pro</h3>
      <span class="close" onclick="closeUpgradeModal()">&times;</span>
    </div>
    <div class="modal-body">
      <div id="upgrade-content"></div>
      <p style="text-align: center; margin-top: 20px; color: #888; font-size: 14px;">
        One-time payment. No subscription. Yours forever.
      </p>
      <button class="upgrade-btn" onclick="window.open('https://xeyonai.gumroad.com/l/bsmupk', '_blank')">
        Get Pro Version - £25
      </button>
    </div>
  </div>
</div>

<div id="memory-modal" class="modal">'''

UPGRADE_FUNCTIONS = '''  // ==================================================
  // UPGRADE MODAL (Pro features)
  // ==================================================
  function showUpgradeModal(feature) {
    const modal = document.getElementById('upgrade-modal');
    const content = document.getElementById('upgrade-content');
    const features = {
      memory: {
        icon: "🧠",
        title: "Character Memory",
        description: "Characters remember past conversations, important facts, and context across all chats.",
        benefits: [
          "Persistent cross-session memory",
          "End Session — generate a memory summary any time",
          "Memory editor — view, edit and delete stored memories",
          "Builds deeper, more coherent conversations over time"
        ]
      }
    };
    const f = features[feature] || features.memory;
    content.innerHTML = `
      <div style="text-align: center; margin-bottom: 20px;">
        <div style="font-size: 64px; margin-bottom: 16px;">${f.icon}</div>
        <h2 style="margin: 0 0 12px 0; color: #fff;">${f.title}</h2>
        <p style="color: #aaa; font-size: 15px; line-height: 1.6;">${f.description}</p>
      </div>
      <ul style="list-style: none; padding: 0; margin: 20px 0;">
        ${f.benefits.map(b => `
          <li style="padding: 8px 0; color: #ccc; font-size: 14px;">
            <span style="color: #667eea; margin-right: 8px;">✓</span>${b}
          </li>
        `).join('')}
      </ul>
    `;
    modal.style.display = 'block';
  }

  function closeUpgradeModal() {
    document.getElementById('upgrade-modal').style.display = 'none';
  }

  function openMemoryModal() { showUpgradeModal('memory'); }

  window.addEventListener('click', (e) => {
    const modal = document.getElementById('upgrade-modal');
    if (e.target === modal) closeUpgradeModal();
  });'''


def strip_index_html():
    with open(INDEX_HTML, 'r', encoding='utf-8') as f:
        content = f.read()

    original = content

    # 1. Sidebar Memory button — regex catches any current text
    import re as _re
    _new_btn = '<button id="memory-btn" onclick="showUpgradeModal(\'memory\')">🧠 Memory ⭐ Pro Version</button>'
    _new_content, _count = _re.subn(r'<button id="memory-btn" onclick="[^"]*">[^<]+</button>', _new_btn, content)
    if _count > 0 and _new_content != content:
        content = _new_content
        CHANGES.append("index.html: sidebar Memory button replaced")
    elif _count > 0:
        CHANGES.append("index.html: sidebar Memory button already correct")

    # 2. End Session button
    old_es = '<button onclick="endSession(); closeInputMenu()" title="Generate a memory summary">🧠 End Session</button>'
    new_es = '<button onclick="showUpgradeModal(\'memory\'); closeInputMenu()" title="Upgrade to HWUI Pro">Memory ⭐Go Pro</button>'
    if old_es in content:
        content = content.replace(old_es, new_es)
        CHANGES.append("index.html: End Session button replaced")

    # 3. Insert upgrade modal (if not already present)
    if '<div id="upgrade-modal"' not in content:
        if '<div id="memory-modal" class="modal">' in content:
            content = content.replace('<div id="memory-modal" class="modal">', UPGRADE_MODAL, 1)
            CHANGES.append("index.html: upgrade modal inserted")
        else:
            ERRORS.append("index.html: could not find memory-modal anchor to insert upgrade modal")
    else:
        CHANGES.append("index.html: upgrade modal already present")

    # 4. Replace endSession function block
    end_session_start = content.find('  // END SESSION — generate + store character memory summary')
    delete_last_start = content.find('  // ==================================================\n  // DELETE LAST MESSAGE')

    if end_session_start != -1 and delete_last_start != -1:
        old_block = content[end_session_start:delete_last_start] + '  // ==================================================\n  // DELETE LAST MESSAGE'
        new_block = UPGRADE_FUNCTIONS + '\n\n  // ==================================================\n  // DELETE LAST MESSAGE'
        content = content.replace(old_block, new_block)
        CHANGES.append("index.html: endSession function replaced with upgrade modal functions")

    # 5. Replace full openMemoryModal implementation if Claude Code added it
    full_open_memory_markers = [
        "function openMemoryModal() {\n  const dropdown = document.getElementById('character-select');",
        "function openMemoryModal() {\n  const modal = document.getElementById('memory-modal');",
    ]
    for marker in full_open_memory_markers:
        if marker in content:
            # Find the full function
            func_start = content.find(marker)
            func_end = content.find('\n}\n', func_start) + 3
            old_func = content[func_start:func_end]
            content = content.replace(old_func, "function openMemoryModal() { showUpgradeModal('memory'); }\n")
            CHANGES.append("index.html: full openMemoryModal implementation replaced")
            break

    # 6. Remove memory tag detection block from response handler
    # Marker: the suppress check wrapping the whole detection block
    mem_detect_start = content.find('      // ── Memory command detection ──────────────────────────────────────')
    mem_detect_end = content.find('      } // end suppress check\n      // ─────────────────────────────────────────────────────────────────')
    if mem_detect_start != -1 and mem_detect_end != -1:
        # Also remove the suppress guard that precedes the detection block
        suppress_guard = '      if (!window._suppressMemoryDetection && currentCharacter && currentCharacter.name) {\n        renderChatMessages(window.loadedChat);\n      }\n\n'
        suppress_guard_alt = '      if (!window._suppressMemoryDetection && currentCharacter && currentCharacter.name) {\n        renderChatMessages(window.loadedChat);\r\n      }\r\n\r\n'
        end_of_block = mem_detect_end + len('      } // end suppress check\n      // ─────────────────────────────────────────────────────────────────\n')
        block_to_remove = content[mem_detect_start:end_of_block]
        content = content.replace(suppress_guard, '      renderChatMessages(window.loadedChat);\n\n')
        content = content.replace(suppress_guard_alt, '      renderChatMessages(window.loadedChat);\n\n')
        # Re-find after possible replacement shift
        mem_detect_start = content.find('      // ── Memory command detection ──────────────────────────────────────')
        mem_detect_end = content.find('      } // end suppress check')
        if mem_detect_start != -1 and mem_detect_end != -1:
            end_of_block = content.find('\n', mem_detect_end + len('      } // end suppress check')) + 1
            # Remove up to and including the closing comment line
            end_of_block2 = content.find('\n', end_of_block) + 1
            content = content[:mem_detect_start] + content[end_of_block2:]
            CHANGES.append("index.html: memory tag detection block removed from response handler")
        else:
            ERRORS.append("index.html: could not re-find memory detection block after suppress guard removal")
    elif mem_detect_start == -1:
        CHANGES.append("index.html: memory tag detection block already absent")
    else:
        ERRORS.append("index.html: found memory detection start but not end marker")

    # 7. Remove memory functions block (loadCharacterMemory through showMemoryConfirmInBubble)
    mem_funcs_start = content.find('// ==================================================\n// LOAD CHARACTER MEMORY (MODAL VERSION)')
    mem_funcs_end = content.find('  // ==================================================\n  // INITIALISE PAGE (UNIFIED)')
    if mem_funcs_start != -1 and mem_funcs_end != -1:
        content = content[:mem_funcs_start] + content[mem_funcs_end:]
        CHANGES.append("index.html: memory modal functions removed (loadCharacterMemory, showMemoryConfirmInBubble, etc.)")
    elif mem_funcs_start == -1:
        CHANGES.append("index.html: memory modal functions already absent")
    else:
        ERRORS.append("index.html: found memory functions start but not end marker")

    # 8. Remove #memory-modal div (the actual modal HTML, not the upgrade modal)
    # The upgrade modal script reinserts it as part of UPGRADE_MODAL — remove it if it's a live modal
    mem_modal_start = content.find('<div id="memory-modal" class="modal">\n  <div class="modal-content">')
    if mem_modal_start == -1:
        mem_modal_start = content.find('<div id="memory-modal" class="modal">\r\n  <div class="modal-content">')
    if mem_modal_start != -1:
        # Find closing </div> for this modal — it ends at the next top-level </div> after modal-content
        search_from = mem_modal_start + len('<div id="memory-modal" class="modal">')
        # Count through nested divs to find the matching close
        depth = 1
        pos = search_from
        while pos < len(content) and depth > 0:
            open_pos = content.find('<div', pos)
            close_pos = content.find('</div>', pos)
            if open_pos == -1: open_pos = len(content)
            if close_pos == -1: close_pos = len(content)
            if open_pos < close_pos:
                depth += 1
                pos = open_pos + 4
            else:
                depth -= 1
                pos = close_pos + 6
        mem_modal_end = pos
        content = content[:mem_modal_start] + content[mem_modal_end:].lstrip('\r\n')
        CHANGES.append("index.html: #memory-modal div removed")
    else:
        CHANGES.append("index.html: #memory-modal div already absent")

    # 9. Remove duplicate openMemoryModal / closeMemoryModal stubs if they exist outside UPGRADE_FUNCTIONS
    import re as _re2
    # Remove standalone closeMemoryModal function if present (UPGRADE_FUNCTIONS doesn't include it)
    content = _re2.sub(r'\nfunction closeMemoryModal\(\) \{[^}]*\}\n', '\n', content)
    content = _re2.sub(r'\nfunction setMemoryTab\([^)]*\) \{[^\}]*\}\n', '\n', content)
    content = _re2.sub(r'\nfunction toggleMemoryAddPanel\(\) \{[^}]*\}\n', '\n', content)

    # 10. Disable automatic memory capture calls left by Pro builds.
    auto_mem_re = _re2.compile(
        r'async function captureAutomaticMemory\([^)]*\) \{.*?\n  \}',
        _re2.DOTALL
    )
    auto_mem_stub = "async function captureAutomaticMemory(userText, assistantText) {\n    return false;\n  }"
    content, auto_mem_count = auto_mem_re.subn(auto_mem_stub, content, count=1)
    if auto_mem_count:
        CHANGES.append("index.html: automatic memory capture stubbed")
    elif "async function captureAutomaticMemory" not in content:
        CHANGES.append("index.html: automatic memory capture already absent")
    elif "async function captureAutomaticMemory(userText, assistantText) {\n    return false;\n  }" in content:
        CHANGES.append("index.html: automatic memory capture already stubbed")
    else:
        ERRORS.append("index.html: could not stub captureAutomaticMemory")

    # 11. Verify no endSession remains
    if 'async function endSession()' in content:
        ERRORS.append("index.html: endSession function still present after strip")

    if content != original:
        with open(INDEX_HTML, 'w', encoding='utf-8') as f:
            f.write(content)


# ── config.html ──────────────────────────────────────────────────────────────

CONFIG_GO_PRO = '''      <!-- Memory checkboxes hidden in free version — kept for JS compatibility -->
      <input type="checkbox" id="use-personal-memory" style="display:none;" />
      <input type="checkbox" id="use-global-memory" style="display:none;" />

      <div style="margin: 8px 0 12px 0; padding: 10px 14px; background: rgba(102,126,234,0.08); border: 1px solid rgba(102,126,234,0.25); border-radius: 8px; display:flex; align-items:center; justify-content:space-between; gap:12px;">
        <div>
          <div style="font-size:13px; color:#ccc;">🧠 <strong style="color:#fff;">Character Memory</strong></div>
          <div style="font-size:11px; color:#888; margin-top:2px;">Characters that remember you — Pro feature</div>
        </div>
        <button onclick="window.open('https://xeyonai.gumroad.com/l/bsmupk','_blank')" style="background:linear-gradient(135deg,#667eea,#764ba2); border:none; color:#fff; font-size:12px; font-weight:600; padding:6px 14px; border-radius:6px; cursor:pointer; white-space:nowrap;">⭐ Go Pro</button>
      </div>'''


def strip_config_html():
    with open(CONFIG_HTML, 'r', encoding='utf-8') as f:
        content = f.read()

    original = content

    # Find memory checkbox block
    start = content.find('      <div style="margin: 8px 0 4px 0; display:flex; align-items:center; gap:8px;">\n        <input type="checkbox" id="use-personal-memory"')
    end = content.find('      </div>', content.find('<input type="checkbox" id="use-global-memory"'))
    if end != -1:
        end += len('      </div>')

    if start != -1 and end != -1:
        content = content[:start] + CONFIG_GO_PRO + content[end:]
        CHANGES.append("config.html: memory checkboxes replaced with Go Pro banner")
    elif 'display:none' in content and 'use-personal-memory' in content and 'Go Pro' in content:
        CHANGES.append("config.html: memory checkboxes already stripped")
    else:
        ERRORS.append("config.html: could not find memory checkbox block")

    import re as _re
    auto_mem_block_re = _re.compile(
        r'\n\s*<label style="display:flex;align-items:center;gap:8px;margin:10px 0 4px;cursor:pointer;">\s*'
        r'\n\s*<input type="checkbox" id="auto-memory-enabled"[^>]*>\s*'
        r'\n\s*Automatic local memory\s*'
        r'\n\s*</label>\s*'
        r'\n\s*<div style="font-size:11px;color:#777;line-height:1.4;margin-bottom:10px;">.*?'
        r'\n\s*</div>',
        _re.DOTALL
    )
    hidden_auto_mem = '\n      <input type="checkbox" id="auto-memory-enabled" style="display:none;" disabled>'
    content, auto_block_count = auto_mem_block_re.subn(hidden_auto_mem, content, count=1)
    if auto_block_count:
        CHANGES.append("config.html: automatic memory setting hidden")
    elif 'id="auto-memory-enabled" style="display:none;" disabled' in content:
        CHANGES.append("config.html: automatic memory setting already hidden")
    elif 'id="auto-memory-enabled"' in content:
        ERRORS.append("config.html: automatic memory setting still visible")

    content = content.replace(
        "document.getElementById('auto-memory-enabled').checked = settings.auto_memory?.enabled === true;",
        "document.getElementById('auto-memory-enabled').checked = false;"
    )
    content = content.replace(
        "enabled: document.getElementById('auto-memory-enabled').checked,",
        "enabled: false,"
    )
    if "document.getElementById('auto-memory-enabled').checked = settings.auto_memory?.enabled === true;" in content:
        ERRORS.append("config.html: auto-memory load path still reads saved enabled value")
    elif "enabled: document.getElementById('auto-memory-enabled').checked," in content:
        ERRORS.append("config.html: auto-memory save path still writes checkbox value")
    else:
        CHANGES.append("config.html: automatic memory load/save forced off")

    if content != original:
        with open(CONFIG_HTML, 'w', encoding='utf-8') as f:
            f.write(content)


def strip_mobile_html():
    with open(MOBILE_HTML, 'r', encoding='utf-8') as f:
        content = f.read()

    original = content

    import re as _re
    pro_button = '<button onclick="showMobileProNotice()" title="Memory summaries are available in HWUI Pro" style="flex-shrink:0;background:var(--surface2);border:1px solid var(--border);border-radius:8px;padding:6px 9px;color:var(--text-dim);font-size:16px;cursor:pointer;line-height:1;">⭐</button>'
    content, btn_count = _re.subn(
        r'<button onclick="endSession\(\)" title="End Session"[^>]*>.*?</button>',
        pro_button,
        content,
        count=1,
        flags=_re.DOTALL
    )
    if btn_count:
        CHANGES.append("mobile.html: End Session button replaced with Pro notice")
    elif 'onclick="showMobileProNotice()"' in content:
        CHANGES.append("mobile.html: Pro notice button already present")
    else:
        ERRORS.append("mobile.html: could not replace End Session button")

    end_session_stub = """async function endSession(){
  showMobileProNotice();
}

function showMobileProNotice(){
  showToast('Memory summaries are available in HWUI Pro.');
}"""
    if 'function showMobileProNotice()' in content and '/generate_session_summary' not in content:
        CHANGES.append("mobile.html: End Session function already stripped")
    else:
        end_session_re = _re.compile(
            r'async function endSession\(\)\{.*?\n\}',
            _re.DOTALL
        )
        content, fn_count = end_session_re.subn(end_session_stub, content, count=1)
        if fn_count:
            CHANGES.append("mobile.html: End Session function replaced with Pro notice")
        else:
            ERRORS.append("mobile.html: could not replace End Session function")

    if '/generate_session_summary' in content:
        ERRORS.append("mobile.html: /generate_session_summary call remains")

    if content != original:
        with open(MOBILE_HTML, 'w', encoding='utf-8') as f:
            f.write(content)


def strip_settings_default_json():
    with open(SETTINGS_DEFAULT_JSON, 'r', encoding='utf-8') as f:
        settings = json.load(f)

    auto_memory = settings.setdefault("auto_memory", {})
    if auto_memory.get("enabled") is False:
        CHANGES.append("settings.default.json: auto_memory.enabled already false")
        return

    auto_memory["enabled"] = False
    auto_memory.setdefault("local_only", True)
    with open(SETTINGS_DEFAULT_JSON, 'w', encoding='utf-8') as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    CHANGES.append("settings.default.json: auto_memory.enabled forced false")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "="*52)
    print("  HWUI Free Version Strip Script")
    print("="*52 + "\n")

    check_files()
    strip_app_py()
    strip_index_html()
    strip_config_html()
    strip_mobile_html()
    strip_settings_default_json()

    print()
    for change in CHANGES:
        print(f"  ✅ {change}")

    if ERRORS:
        print()
        for error in ERRORS:
            print(f"  ❌ {error}")
        print("\n⚠️  Completed with errors — review above before pushing to GitHub.\n")
        sys.exit(1)
    else:
        print("\n✅ All done — safe to push to GitHub.\n")


if __name__ == '__main__':
    main()

"""
strip_for_free.py
=================
Run this script from the root of the Helcyon-WebUI (free) repo folder
to strip Pro-only features and replace them with upgrade prompts.

Usage:
    python strip_for_free.py

What it does:
    - app.py        : Removes session summary code (functions, route, injection)
    - index.html    : Replaces Memory/End Session buttons with Go Pro upsell
    - config.html   : Hides memory checkboxes, shows Go Pro banner

Safe to run multiple times — all operations are idempotent.
"""

import os
import sys
import py_compile
import shutil
from datetime import datetime

# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_PY = os.path.join(BASE_DIR, 'app.py')
INDEX_HTML = os.path.join(BASE_DIR, 'templates', 'index.html')
CONFIG_HTML = os.path.join(BASE_DIR, 'templates', 'config.html')

# Fall back to root-level HTML if not in templates/
if not os.path.exists(INDEX_HTML):
    INDEX_HTML = os.path.join(BASE_DIR, 'index.html')
if not os.path.exists(CONFIG_HTML):
    CONFIG_HTML = os.path.join(BASE_DIR, 'config.html')

ERRORS = []
CHANGES = []


def check_files():
    missing = []
    for path in [APP_PY, INDEX_HTML, CONFIG_HTML]:
        if not os.path.exists(path):
            missing.append(path)
    if missing:
        print("❌ Missing files:")
        for f in missing:
            print(f"   {f}")
        sys.exit(1)


def backup_files():
    """Create .bak backups before modifying."""
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    for path in [APP_PY, INDEX_HTML, CONFIG_HTML]:
        shutil.copy2(path, path + f'.{ts}.bak')
    print(f"✅ Backups created (.{ts}.bak)")


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
        CHANGES.append("app.py: session summary block already absent")
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

    # 3. Verify clean
    remaining = (content.count('load_session_summary') +
                 content.count('SESSION_SUMMARY_DIR') +
                 content.count('generate_session_summary'))
    if remaining > 0:
        ERRORS.append(f"app.py: {remaining} session summary reference(s) remain after strip")

    if content != original:
        with open(APP_PY, 'w', encoding='utf-8') as f:
            f.write(content)

    # 4. Syntax check
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
        Get Pro Version - £20
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

    # 1. Sidebar Memory button
    old_btn = '<button id="memory-btn" onclick="openMemoryModal()">🧠 Memory</button>'
    new_btn = '<button id="memory-btn" onclick="showUpgradeModal(\'memory\')">⭐ Go Pro</button>'
    if old_btn in content:
        content = content.replace(old_btn, new_btn)
        CHANGES.append("index.html: sidebar Memory button replaced")

    # 2. End Session button
    old_es = '<button onclick="endSession(); closeInputMenu()" title="Generate a memory summary">🧠 End Session</button>'
    new_es = '<button onclick="showUpgradeModal(\'memory\'); closeInputMenu()" title="Upgrade to HWUI Pro">⭐ Go Pro</button>'
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

    # 6. Verify no endSession remains
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

    # Already stripped?
    if 'display:none' in content and 'use-personal-memory' in content and 'Go Pro' in content:
        CHANGES.append("config.html: already stripped")
        return

    # Find memory checkbox block
    start = content.find('      <div style="margin: 8px 0 4px 0; display:flex; align-items:center; gap:8px;">\n        <input type="checkbox" id="use-personal-memory"')
    end = content.find('      </div>', content.find('<input type="checkbox" id="use-global-memory"'))
    if end != -1:
        end += len('      </div>')

    if start != -1 and end != -1:
        content = content[:start] + CONFIG_GO_PRO + content[end:]
        CHANGES.append("config.html: memory checkboxes replaced with Go Pro banner")
    else:
        ERRORS.append("config.html: could not find memory checkbox block")

    if content != original:
        with open(CONFIG_HTML, 'w', encoding='utf-8') as f:
            f.write(content)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "="*52)
    print("  HWUI Free Version Strip Script")
    print("="*52 + "\n")

    check_files()
    backup_files()
    print()

    strip_app_py()
    strip_index_html()
    strip_config_html()

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

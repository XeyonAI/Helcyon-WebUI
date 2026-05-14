# HWUI Free Version — Strip Instructions for Claude Code

## What this repo is

This is the **free/public version** of HWUI (Helcyon-WebUI). It is maintained by running
`strip_for_free.py` against the Pro dev build files whenever updates are ready to release.

**Do not treat this as the primary codebase.** The Pro dev build is the source of truth.
This repo only contains stripped versions of those files.

---

## How to update this repo

When new files arrive from the Pro build:

1. Copy the updated files into this folder (overwriting existing ones)
2. Run the strip script:
   ```
   python strip_for_free.py
   ```
3. Check the output — all items should show ✅
4. Push to GitHub

That's it. Do not manually edit `app.py`, `index.html`, or `config.html` beyond running the script.

---

## What the strip script does

### `app.py`
- Removes the **Session Summary** block: constants (`SESSION_SUMMARY_DIR` etc), `load_session_summary()`,
  `save_session_summary()`, and the `/generate_session_summary` Flask route
- Removes the **session summary injection** from the chat assembly block (the `if _is_new_chat:` block
  that calls `load_session_summary`)
- Preserves `_is_new_chat` variable (still needed for project RP opener)

### `index.html`
- Replaces the **🧠 Memory** sidebar button → **⭐ Go Pro** button (opens upgrade modal)
- Replaces the **🧠 End Session** button → **⭐ Go Pro** button
- Inserts the **upgrade modal** (Gumroad link) before the memory modal
- Replaces the **`endSession()`** function block with `showUpgradeModal()` + `closeUpgradeModal()` functions
- Replaces any full **`openMemoryModal()`** implementation with a stub that redirects to the upgrade modal

### `config.html`
- Hides the **use-personal-memory** and **use-global-memory** checkboxes (`display:none`)
- Replaces them visually with a **Go Pro banner** linking to Gumroad
- Hidden checkboxes are kept in the DOM so JS references don't error

---

## What NOT to change

- **Do not remove** the hidden `use-personal-memory` / `use-global-memory` checkboxes from `config.html`
  — they must stay in the DOM for JS compatibility
- **Do not remove** the `memory-modal` div from `index.html` — it must stay in the DOM
- **Do not remove** `openMemoryModal()` entirely — it must exist as a stub
- **Do not add** any session summary, memory, or End Session functionality — these are Pro-only features
- **The Go Pro Gumroad link is:** `https://xeyonai.gumroad.com/l/bsmupk` — do not change this

---

## Pro features (never re-add these to the free version)

| Feature | Pro only |
|---|---|
| Session memory / End Session button | ✅ |
| `load_session_summary()` / `save_session_summary()` | ✅ |
| `/generate_session_summary` route | ✅ |
| Memory modal (view/edit memories) | ✅ |
| `use-personal-memory` / `use-global-memory` checkboxes (visible) | ✅ |

## Free features (keep these intact)

| Feature | Free |
|---|---|
| Project folders | ✅ |
| Web search | ✅ |
| Character creator | ✅ |
| TTS pipeline | ✅ |
| All themes | ✅ |
| Markdown rendering | ✅ |
| All other UI features | ✅ |

---

## Gumroad link
`https://xeyonai.gumroad.com/l/bsmupk`

## GitHub repo
`https://github.com/XeyonAI/Helcyon-WebUI`

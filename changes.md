## Session: May 14 2026 — Prompt-Stack Fixes (OOC Wrapping + ex_block Ordering)

### `app.py`
**Bug fix: character_note / author_note leaking into visible response**
- Raw text from character_note and author_note (e.g. "Tone note: Keep a light friendly tone…") was surfacing in model output as if it were content to reply to
- Both were injected as bare text into the system block — model treated them as message content rather than silent directives
- Fix: wrap each in `[OOC: Character note — …]` / `[OOC: Author note — …]` labels at the injection sites (around the former lines 2725 and 2733). Model now reads them as out-of-character instructions and follows them silently.
- ⚠️ DO NOT remove the `[OOC: …]` wrappers — the leakage returns immediately without them

**Bug fix: ex_block being silently ignored (style examples buried mid-system-block)**
- ex_block was appended in the system block but character_note, author_note, AND the per-turn time injection all sat AFTER it
- Result: any tone/style language in those notes was closer to the generation point and won attention over the actual style examples
- Fix: ex_block moved to be the ABSOLUTE LAST item appended to the system block — after restriction anchor, character_note, author_note, AND the current time injection
- New order in system block: system_prompt → char_context → user_context → instruction → tone_primer → project_documents → restriction anchor → character_note (OOC) → author_note (OOC) → current time → ex_block
- The time injection comment block (`"injected as the LAST item"`, `"DO NOT move earlier"`) updated to reflect that ex_block is now LAST; time injection sits just before it
- ⚠️ DO NOT move ex_block earlier in the system block — it must remain absolute last so style cues sit closest to the generation point
- ⚠️ DO NOT add a second system message — would break `S U A U A … U` role alternation (see May 11 2026 Helcyon EOS diagnostic)

**Note: ex_block wrapper boilerplate was already stripped**
- The heavy ═══/⚠️/⛔/🎯 metadata wrapper around ex_block was already removed in a previous session (~400 tokens of boilerplate down to a one-line header). No further wrapper stripping was needed; the existing `Speaking-style examples — mirror the tone…` header is already terse.
- The conservative `_EX_WRAPPER_OVERHEAD = 400` constant kept as a trim-budget safety margin — actual wrapper is ~40 tokens but the over-estimate protects against ctx_size overflow.

**Stale comments updated**
- The "character_note and author_note ride in the [REPLY INSTRUCTIONS] depth-0 packet" comments were updated in TWO locations (former lines ~2225 and ~2487) — both correctly reflect that those fields now live in the system block (wrapped in OOC labels), not the depth-0 packet
- The OOC packet docstring (former ~2828-2842) updated: items 4 (author_note) and 5 (character_note) removed from the list — packet now contains only project_instructions, style reminder, and post_history
- The pre-account block (former ~2528) updated: comment now correctly describes coverage for both the OOC depth-0 packet AND the system-block OOC notes (character_note/author_note). The character_note/author_note overhead now also includes +20 tokens each for the `[OOC: …]` wrapper itself.
- Note on user-flagged lines 2272-2274: that comment is about `project_instructions`, which IS still in the OOC packet, so the comment is accurate and was left unchanged. The stale-comment fix landed at lines ~2487 instead (also a "character_note/author_note ride in OOC packet" claim — definitely stale, now updated).

---

## Session: May 14 2026 — Config Page Tab Redesign

### `config.html`
**Feature: Centre column redesigned with tab navigation**
- Replaced the single long scrolling centre column with a 5-tab layout: System Prompt | Character | New Character | User Persona | Appearance
- Tab bar sits at the top of `#container`; active tab highlighted in green, inactive tabs subtle/dark
- Each section is wrapped in a `config-tab-panel` div — hidden by default, shown when active
- `switchConfigTab(tabId, btn)` function handles show/hide and active button state; scrolls container to top on switch
- System Prompt tab is active by default on page load
- Appearance tab added to centre: contains Background controls + Open Theme Editor button (replaces sidebar Appearance section)
- Sidebar loses the Appearance section entirely — keeps Sampling, TTS, Llama.cpp, Web Search, OpenAI only
- All existing JS/functionality completely unchanged — purely structural HTML reorganisation

### `style.css`
**Feature: Tab bar styling added**
- `#config-tab-bar`: flex row, wraps on small screens, sits above content with bottom border
- `.config-tab`: dark border, muted text, hover lightens, smooth transition
- `.config-tab.active`: green tint matching HWUI button style
- `.config-tab-panel`: display:none by default; .active -> display:block

---

## Session: May 14 2026 — Modal Centering + List Spacing Fix

### `style.css`
- Fixed `.modal` `padding-left: 120px` → `250px` — modals now centre relative to the content pane (right of sidebar), matching the input bar and chat column alignment. Standard layout matching ChatGPT/Grok/Gemini.
- Fixed duplicate list rules at line ~2439: `.model-text ul/ol` and `.model-text li` had lower-specificity overrides declared later in the file that were winning over earlier fixes — bumped to match paragraph rhythm (`margin: 0.8em 0 1.1em`, `li margin-bottom: 0.8em`, `line-height: 1.6`)
- Fixed `#container` `flex: 1` → `flex: 0 1 770px` to prevent container stretching past max-width
- Fixed `.chat-page #center-column` `margin-left: 0` → `margin: auto` for proper centering in content pane

---

## Session: May 08 2026 — Paragraph & List Spacing Polish

### `style.css`
- Bumped `.model-text p` and `.model-text-cont p` margin from `0.4em` to `0.8em` — paragraphs were too cramped
- Fixed list spacing to match paragraph rhythm: `line-height` raised from `1.3` to `1.6`, `li` margin from `0.15em` to `0.4em`, ul/ol block margin from `0.3em 0 0.5em` to `0.6em 0 0.8em`
- Affects `.model-text-cont`, `.model-text`, `.user-text`, and `.message` list rules

---

## Session: May 07 2026 — Section Divider Colour in Theme Editor

## Session: May 14 2026 — Modal Centering + List Spacing Fix

### `style.css`
- Fixed `.modal` `padding-left: 120px` → `250px` — modals now centre relative to the content pane (right of sidebar), matching the input bar and chat column alignment. Standard layout matching ChatGPT/Grok/Gemini.
- Fixed duplicate list rules at line ~2439: `.model-text ul/ol` and `.model-text li` had lower-specificity overrides declared later in the file that were winning over earlier fixes — bumped to match paragraph rhythm (`margin: 0.8em 0 1.1em`, `li margin-bottom: 0.8em`, `line-height: 1.6`)
- Fixed `#container` `flex: 1` → `flex: 0 1 770px` to prevent container stretching past max-width
- Fixed `.chat-page #center-column` `margin-left: 0` → `margin: auto` for proper centering in content pane

---

## Session: May 08 2026 — Paragraph & List Spacing Polish

### `style.css`
- Bumped `.model-text p` and `.model-text-cont p` margin from `0.4em` to `0.8em` — paragraphs were too cramped
- Fixed list spacing to match paragraph rhythm: `line-height` raised from `1.3` to `1.6`, `li` margin from `0.15em` to `0.4em`, ul/ol block margin from `0.3em 0 0.5em` to `0.6em 0 0.8em`
- Affects `.model-text-cont`, `.model-text`, `.user-text`, and `.message` list rules

---

## Session: May 07 2026 — HR Separator Visibility + Live Theme Update Fix

### `style.css`
**Bug fix: HR separators in chat bubbles — full resolution**
**Root cause found: `#container hr` was winning (ID specificity beats class)**
- DevTools confirmed: `#container hr` at style.css:877 used `var(--msg-border)` — ID selectors always beat class selectors
- `.model-text hr` and `.message hr` both rendered as empty `{}` — completely overridden
- Fix: added `#container .model-text hr` / `#container .message hr` etc. — same ID specificity, declared later, wins

- Changed `border-top` from `var(--msg-border)` to `var(--hr-color, #ffffff4d)` — now consistent with `.model-text hr`
- Was the root cause of separators being invisible (--msg-border is near-black on midnight theme)

### `app.py`
**Bug fix: `get_theme` not returning `--hr-color` for themes that don't define it**
- Old version only read the active theme file — if midnight.css had no `--hr-color`, it came back empty
- Theme picker showed no colour and `setProperty` had nothing to apply
- Fix: Step 1 now seeds all vars from `style.css` defaults, Step 2 overlays the active theme on top
- Any variable defined in `style.css :root` is now always available in the picker regardless of theme

---


### `config.html`
**Feature: Added `--hr-color` (Section Divider) to Theme Editor**
- Added to the Messages group in both the main theme var array and the advanced editor array
- Allows per-theme control of the `---` separator colour without editing theme files manually

### `style.css`
- Added `--hr-color: rgba(255,255,255,0.3)` to `:root` as the default fallback
- `.model-text hr` now uses `var(--hr-color, rgba(255,255,255,0.3))` instead of hardcoded rgba

### `gemini.css`
- Added `--hr-color: rgba(255,255,255,0.3)` to `:root` — fixes invisible separators on this theme
- Removed the manual one-off override added in previous session

---

## Session: May 14 2026 — Modal Centering + List Spacing Fix

### `style.css`
- Fixed `.modal` `padding-left: 120px` → `250px` — modals now centre relative to the content pane (right of sidebar), matching the input bar and chat column alignment. Standard layout matching ChatGPT/Grok/Gemini.
- Fixed duplicate list rules at line ~2439: `.model-text ul/ol` and `.model-text li` had lower-specificity overrides declared later in the file that were winning over earlier fixes — bumped to match paragraph rhythm (`margin: 0.8em 0 1.1em`, `li margin-bottom: 0.8em`, `line-height: 1.6`)
- Fixed `#container` `flex: 1` → `flex: 0 1 770px` to prevent container stretching past max-width
- Fixed `.chat-page #center-column` `margin-left: 0` → `margin: auto` for proper centering in content pane

---

## Session: May 08 2026 — Paragraph & List Spacing Polish

### `style.css`
- Bumped `.model-text p` and `.model-text-cont p` margin from `0.4em` to `0.8em` — paragraphs were too cramped
- Fixed list spacing to match paragraph rhythm: `line-height` raised from `1.3` to `1.6`, `li` margin from `0.15em` to `0.4em`, ul/ol block margin from `0.3em 0 0.5em` to `0.6em 0 0.8em`
- Affects `.model-text-cont`, `.model-text`, `.user-text`, and `.message` list rules

---

## Session: May 07 2026 — HR Visibility + Equal Spacing

### `style.css`
**Tweak: HR separators now clearly visible with equal spacing above and below**
- `border-top` increased from `1px` to `2px` for visibility
- `opacity` raised from `0.6` to `1`
- `margin` kept at `10px 0` (equal top/bottom) — adjacent element margins still zeroed so hr owns the gap
- `ul + hr` margin-top synced to match `10px` base

---

## Session: May 14 2026 — Modal Centering + List Spacing Fix

### `style.css`
- Fixed `.modal` `padding-left: 120px` → `250px` — modals now centre relative to the content pane (right of sidebar), matching the input bar and chat column alignment. Standard layout matching ChatGPT/Grok/Gemini.
- Fixed duplicate list rules at line ~2439: `.model-text ul/ol` and `.model-text li` had lower-specificity overrides declared later in the file that were winning over earlier fixes — bumped to match paragraph rhythm (`margin: 0.8em 0 1.1em`, `li margin-bottom: 0.8em`, `line-height: 1.6`)
- Fixed `#container` `flex: 1` → `flex: 0 1 770px` to prevent container stretching past max-width
- Fixed `.chat-page #center-column` `margin-left: 0` → `margin: auto` for proper centering in content pane

---

## Session: May 08 2026 — Paragraph & List Spacing Polish

### `style.css`
- Bumped `.model-text p` and `.model-text-cont p` margin from `0.4em` to `0.8em` — paragraphs were too cramped
- Fixed list spacing to match paragraph rhythm: `line-height` raised from `1.3` to `1.6`, `li` margin from `0.15em` to `0.4em`, ul/ol block margin from `0.3em 0 0.5em` to `0.6em 0 0.8em`
- Affects `.model-text-cont`, `.model-text`, `.user-text`, and `.message` list rules

---

## Session: May 07 2026 — HR Section Spacing Balanced

### `style.css`
**Tweak: Sections too cramped after gap fix — rebalanced hr spacing**
- Previous fix zeroed all margins around `<hr>` which removed ALL breathing room between sections
- New approach: `hr` itself owns the gap (`margin: 12px 0`) — single source of truth, no stacking
- All adjacent element margins (`p`, `ul`, `ol` before/after hr) zeroed so only the hr value counts
- Also merged the duplicate `.model-text-cont hr` rule into the unified top-level rule

---

## Session: May 14 2026 — Modal Centering + List Spacing Fix

### `style.css`
- Fixed `.modal` `padding-left: 120px` → `250px` — modals now centre relative to the content pane (right of sidebar), matching the input bar and chat column alignment. Standard layout matching ChatGPT/Grok/Gemini.
- Fixed duplicate list rules at line ~2439: `.model-text ul/ol` and `.model-text li` had lower-specificity overrides declared later in the file that were winning over earlier fixes — bumped to match paragraph rhythm (`margin: 0.8em 0 1.1em`, `li margin-bottom: 0.8em`, `line-height: 1.6`)
- Fixed `#container` `flex: 1` → `flex: 0 1 770px` to prevent container stretching past max-width
- Fixed `.chat-page #center-column` `margin-left: 0` → `margin: auto` for proper centering in content pane

---

## Session: May 08 2026 — Paragraph & List Spacing Polish

### `style.css`
- Bumped `.model-text p` and `.model-text-cont p` margin from `0.4em` to `0.8em` — paragraphs were too cramped
- Fixed list spacing to match paragraph rhythm: `line-height` raised from `1.3` to `1.6`, `li` margin from `0.15em` to `0.4em`, ul/ol block margin from `0.3em 0 0.5em` to `0.6em 0 0.8em`
- Affects `.model-text-cont`, `.model-text`, `.user-text`, and `.message` list rules

---

## Session: May 07 2026 — Paragraph Gap Fix Around HR Separators

### `style.css`
**Fix: Large gaps between sections in model messages (around `---` / `<hr>` separators)**

Root cause was two separate issues:

1. **CSS adjacent-sibling margins not zeroed for `ul`/`ol` before `<hr>`**: The first attempt only added `p + hr` rules, but sections ending with a *bullet list* produce `ul + hr` in the DOM — so those rules never matched. The `ul` margin-bottom of `1.0em` (16px) was fully intact above every `<hr>`. Fixed by adding:
   - `ul + hr, ol + hr { margin-top: 0 }` — removes hr top spacing after a list
   - `ul:has(+ hr), ol:has(+ hr) { margin-bottom: 0 }` — zeroes list bottom margin before hr
   - `hr + ul, hr + ol { margin-top: 0 }` — zeroes list top margin after hr
   - Same rules for `p + hr` / `p:has(+ hr)` / `hr + p` retained

2. **`.model-text-cont` had zero CSS rules**: Content after code blocks renders into `<div class="model-text-cont">` but that class had no CSS, so browser defaults (1em p margins) applied. Added full ruleset mirroring `.model-text`.

---





### `style.css`
**Fix: Chat content area was shifted left instead of centred in the remaining viewport**
- `#container` / `#center-column` had `margin-left: 300px` hardcoded — overriding flexbox centering
- `.chat-page #container` override was `margin-left: 100px` — still asymmetric
- `body:not(.chat-page) #container` override was `margin-left: 110px` — same issue
- Responsive breakpoints at 1280px and 1024px also had `margin-left: 30px/40px` on container
- All asymmetric `margin-left` values removed from `#container` / `#center-column` — flexbox `justify-content: center` on `#main` now handles centering naturally

### `index.html`
**Fix: Input bar offset left due to asymmetric `left`/`right` values**
- `#input-area` had `left:250px; right:120px` — shifted the centred input box leftward
- Changed to `right:0` — input box now centres in the full remaining space after the sidebar

---

## Session: May 05 2026 — Project Modal: Folders + Compact Rows

### `style.css`
**Fix: Project rows were not actually shrinking — padding wasn't the only factor**
- `.project-item` padding reduced to `5px 10px`, gap `8px`, added `min-height: 0` and `line-height: 1`
- `.project-name` font-size `13px` (was 18px), added `overflow: hidden / text-overflow: ellipsis`
- `#projects-list` gap reduced to `4px` (was `8px`)
- `.project-buttons button` padding reduced to `3px 8px`
- Added full group/folder CSS: `.project-group-header`, `.project-group-toggle`, `.project-group-label`, `.project-group-delete`, `.project-group-children`, `.project-assign-btn`, `.group-picker-dropdown`, `.group-picker-option` variants

### `project_routes.py`
**Feature: Project groups (manual subfolders)**
- Groups stored in `projects/_groups.json` as `{ "groupName": ["projectName", ...] }`
- `GET /projects/groups` — returns groups dict
- `POST /projects/groups/save` — saves full groups dict (client sends complete state)
- `load_groups()` / `save_groups()` helpers added

### `index.html`
**Feature: Folder grouping in Project Management modal**
- `loadProjects()` now fetches `/projects/groups` in parallel with `/projects/list`
- Ungrouped projects render at top as before
- Grouped projects render under collapsible `📂 FolderName` section headers
- Click header to collapse/expand group
- ✕ button on header deletes the folder (projects remain, just ungrouped) — appears on hover
- Each project row has a `📂` button that opens an inline picker dropdown:
  - Lists existing folders to move into
  - "✕ Remove from group" if currently grouped
  - "➕ New folder…" — prompts for name, creates and assigns in one step
- `assignProjectGroup(projectName, groupName)` — fetches current groups, moves project, saves, reloads
- `deleteGroup(groupName)` — removes group entry, saves, reloads
- Active badge condensed to just `✓` (saves space in tight rows)

---



### `index.html`

**Bug: `srv stop: cancel task` — generation cancelled after 2 tokens**

Root cause: memory confirmation handler calling `fetchAndDisplayResponse()` without checking `window.isSending`. When a response with a `[MEMORY ADD: ...]` tag was received, the confirm would fire a new `/chat` request before the previous stream finished cleanup — browser dropped the old connection, llama.cpp saw `cancel task`.

**Fixes:**
- Memory confirm now polls `window.isSending` and waits until clear before firing
- `sendPrompt()` double-fire guard added (`_sendPromptInFlight` flag, 500ms window)
- Stream read error now caught and logged (`console.warn` on connection drop)
- Role-word regex patterns (`\b` → `(?:\n|:)`) already applied from earlier session

⚠️ Never call `fetchAndDisplayResponse` without checking `window.isSending` first.

---

## Session: May 04 2026 — OpenAI UX Polish + Sampling Preset Update

### `config.html`
**Fix: Local-only sampling params greyed out in OpenAI mode**
- Min P, Top K, Repeat Penalty wrapped in `#local-only-params` div
- In OpenAI mode: opacity drops to 0.3, pointer-events disabled, warning note appears below
- Reverts fully when switching back to local

**Feature: Update Preset button for sampling presets**
- Selecting a preset from the dropdown now auto-populates the name field
- 🔄 Update Preset button appears when a preset is selected — overwrites it in one click
- Button hides again when no preset is selected or after saving a new preset
- `onSamplingPresetSelect()` and `updateSamplingPreset()` functions added

**UX: Save Settings → Save & Apply**
- Renamed for clarity — makes it obvious this is what pushes values to `settings.json` for live use
- Preset load status message updated to match: "hit Save & Apply to use"

### `chat_routes.py`
**Fix: Dots stripped from manual chat rename**
- `.` added to allowed characters in rename sanitizer (line 228)
- `GPT-4.5`, `3.2` etc. now survive the rename without becoming `GPT-45`, `32`

### `index.html`
**Feature: OpenAI indicator shows model name**
- Pill now shows "☁️ OpenAI" with model name beneath it in smaller text
- `#openai-indicator-model` span populated by `checkOpenAIIndicator()`

---

## Session: May 04 2026 — OpenAI Backend Integration + Safety Indicator

### `app.py`
**Feature: OpenAI cloud backend**
- `stream_openai_response()` — streams from `api.openai.com/v1/chat/completions` with Bearer auth, abort support, SSE parsing
- OpenAI fork at top of TEXT-ONLY PATH in `/chat` — reads `backend_mode` from `settings.json`; routes to OpenAI if set, falls through to llama.cpp if local
- `GET /get_openai_settings` — returns `{backend_mode, openai_api_key, openai_model}`
- `POST /save_openai_settings` — atomically saves those three fields
- `GET /get_openai_models` — fetches live model list from OpenAI, filters to chat-capable only, sorts flagships first

### `config.html`
**Feature: OpenAI Backend settings UI**
- Local / ☁️ OpenAI toggle buttons, API key field, model dropdown with 🔄 Fetch button
- Fetch populates dropdown from live API, re-selects previously saved model
- Confirmation modal on switching to OpenAI: *"Your conversations will be sent to OpenAI's servers"* — Cancel / ☁️ Connect. No accidental switches.
- Status line shows active mode, warns if OpenAI selected but no key

### `index.html`
**Feature: OpenAI active indicator in top bar**
- Green glowing dot pill left of model picker showing "☁️ OpenAI" + model name below it
- Hidden in local mode, visible only when `backend_mode === 'openai'` AND API key is set
- `checkOpenAIIndicator()` called on DOMContentLoaded — silent fail if unreachable

### `settings.json`
- Added `"backend_mode": "local"`, `"openai_api_key": ""`, `"openai_model": "gpt-4o"`

---

## Session: May 03 2026 — Frequency & Presence Penalty (OpenAI API)

### `config.html`
- Added `Frequency Penalty` and `Presence Penalty` number inputs below Repeat Penalty, labelled "(OpenAI API)" so it's clear what they're for
- Both loaded from and saved to settings, defaulting to 0.0

### `app.py`
- Added `frequency_penalty: 0.0` and `presence_penalty: 0.0` to `load_sampling_settings()` defaults
- `stream_openai_response()` now accepts `frequency_penalty` and `presence_penalty` params, included in the OpenAI API payload
- Call site passes `sampling.get("frequency_penalty", 0.0)` and `sampling.get("presence_penalty", 0.0)` — safe fallback for existing settings.json without these keys
- llama.cpp local path unaffected — these params are OpenAI-only

---



### `index.html`
- Chat colours (stored in localStorage keyed by filename) were lost on rename because the filename key changed but the colour entry was never migrated
- After a successful `/chats/rename` response, the colour is now moved from the old filename key to `data.new_filename` before `loadChats()` re-renders the list
- Colour now sticks through any rename, only removed if explicitly cleared via the colour picker

---



### `index.html`
- Added `#picker-actual-model` div above the Unload/Close button row in the model picker
- Shows the real `.gguf` filename (from `data.model` in `/get_model` response) in small monospace dim text
- Populated in `refreshModelDisplay()` — visible whenever a model is loaded
- Hidden when no model is loaded or after unload
- Lets you confirm the correct file is loaded even when a custom alias/label is set

---



### `index.html`
**Fix: ChatML tokens being stripped from code blocks, breaking shard generation**
- Model outputs ChatML training shards inside fenced code blocks — these must be preserved verbatim
- Previous flat `.replace()` chains on `cleanedMessage`/`cleaned`/`finalText` stripped ALL ChatML regardless of context
- Added `stripChatMLOutsideCodeBlocks(text, charName, userName)` helper:
  - Splits text on fenced code blocks (``` or ~~~) using a capture group
  - Applies all ChatML/role-leakage/memory-tag strips only to even-indexed segments (plain text)
  - Odd-indexed segments (code block content) returned verbatim — tags fully preserved
- Replaced all flat replace chains in: main stream loop, continue loop, continue finalText
- TTS chunk strip is separate and still strips everything (code block content should never be read aloud)
- ⚠️ DO NOT replace `stripChatMLOutsideCodeBlocks` calls with flat replace chains — shard generation will break

---



### `index.html`
**Root cause fix: Code blocks inside `.model-text` SPAN expanding page width to 2500px+**
- Previous approach (post-render hoisting via `spanEl.after(cb)`) failed — browser had already expanded the inline span to contain the block child before the JS ran
- New approach: `renderModelHTML(spanEl, html)` helper function added
  - Parses html into a throwaway div, extracts `.code-block-wrapper` nodes, replaces each with a `\x00CODEBLOCK_N\x00` text placeholder
  - Re-serialises the safe HTML (inline content only), splits on placeholders
  - Sets first text segment as `spanEl.innerHTML` (inline content only, no blocks)
  - Inserts code blocks directly into the parent as proper DOM siblings — never inside the span
  - Continuation text segments (after a code block) wrapped in `.model-text-cont` spans
- All final render sites converted from `span.innerHTML = html` to `renderModelHTML(span, html)`:
  - `appendChatHistory` (history sidebar load)
  - `loadChatHistory` (both marked and fallback paths)
  - `fetchAndDisplayResponse` streaming final render
  - `continueLast` streaming final render
- Mid-stream renders (incomplete code blocks) left as `innerHTML` — no block elements present during streaming, only after marked.parse() finalises
- `addCodeCopyButtons` now called on the parent container after `renderModelHTML` so it can find code blocks that are siblings of the span
- CSS version bumped to `?v=19`
- ⚠️ DO NOT revert to `spanEl.innerHTML = html` for model text — the overflow will return immediately

### `style.css`
**Fix: Code block text not wrapping (content cut off with horizontal scrollbar inside block)**
- `.code-block-wrapper pre code` had `white-space: pre !important` — overrode the correct `pre-wrap` on the parent `pre`
- This rule was added during the old overflow battle and is now redundant (overflow fixed at DOM level)
- Changed to `white-space: pre-wrap !important; word-break: break-word !important; overflow-wrap: break-word !important`
- Code now wraps correctly inside the block width

---

## Session: May 14 2026 — Modal Centering + List Spacing Fix

### `style.css`
- Fixed `.modal` `padding-left: 120px` → `250px` — modals now centre relative to the content pane (right of sidebar), matching the input bar and chat column alignment. Standard layout matching ChatGPT/Grok/Gemini.
- Fixed duplicate list rules at line ~2439: `.model-text ul/ol` and `.model-text li` had lower-specificity overrides declared later in the file that were winning over earlier fixes — bumped to match paragraph rhythm (`margin: 0.8em 0 1.1em`, `li margin-bottom: 0.8em`, `line-height: 1.6`)
- Fixed `#container` `flex: 1` → `flex: 0 1 770px` to prevent container stretching past max-width
- Fixed `.chat-page #center-column` `margin-left: 0` → `margin: auto` for proper centering in content pane

---

## Session: May 08 2026 — Paragraph & List Spacing Polish

### `style.css`
- Bumped `.model-text p` and `.model-text-cont p` margin from `0.4em` to `0.8em` — paragraphs were too cramped
- Fixed list spacing to match paragraph rhythm: `line-height` raised from `1.3` to `1.6`, `li` margin from `0.15em` to `0.4em`, ul/ol block margin from `0.3em 0 0.5em` to `0.6em 0 0.8em`
- Affects `.model-text-cont`, `.model-text`, `.user-text`, and `.message` list rules

---

## Session: May 07 2026 — HR Separator Visibility + Live Theme Update Fix

### `style.css`
**Bug fix: `.message hr` was overriding `.model-text hr` with wrong colour variable**
- `.message hr` (line 805) used `border-top: 1px solid var(--msg-border)` — this rule matched chat bubble `hr` elements because `.message` wraps `.model-text` in the DOM, giving it equal or higher specificity depending on parse order
- `.model-text hr` correctly used `var(--hr-color)` but was losing to the earlier rule
- Root cause of two symptoms: (1) separators invisible on midnight theme (--msg-border is near-black there), (2) live theme picker for `--hr-color` had no visual effect — the wrong rule was always winning
- Fix: Changed `.message hr` to use `border-top: 2px solid var(--hr-color, #ffffff4d)` with `opacity: 1` — now identical to `.model-text hr`
- No other files needed changing. `midnight.css` does NOT need a manual `--hr-color` entry — `style.css` `:root` default (`#ffffff4d`) applies automatically as fallback
- Live theme picker now works correctly — `setProperty` on `--hr-color` is the rule that actually renders

---

## Session: May 02 2026 — Input Bar Alignment + Top Bar Layout

### `index.html`
**Fix: Input pill position aligned with chat column**
- `#input-area` changed from `right:0` to `right:120px` to shift pill left and align with chat content column
- Model selector in top bar shifted from `left:50%` to `left:calc(50% + 125px)` — centres it within the content area to the right of the sidebar rather than the full window width

### `style.css`
- Top bar padding left unchanged (title stays at left wall)

### Launcher `.bat`
**Fix: Duplicate Flask instances prevented**
- Added kill loop before launch: finds any process listening on port 8081 and kills it before starting Flask
- Prevents the ghost-instance problem that caused hours of confusion (stale file being served by old process)
- Changed browser open URL from `https` back to... actually kept `https` since SSL certs are present (Tailscale mode)

---

## Session: May 02 2026 — Floating Input Bar: Buttons invisible (root cause found)

### `app.py`
**Fix: Duplicate Flask instances causing stale file to be served**
- Two processes were listening on port 8081 simultaneously — an old instance left running from a previous session plus the newly launched one
- Browser was hitting the old instance which served the original `index.html` with the old `button-row` layout
- Every HTML/CSS fix made this session was correct but appeared to do nothing because the wrong file was always served
- Fix 1: Kill duplicate processes (`taskkill /PID ... /F`) before launching
- Fix 2: Added `app.jinja_env.auto_reload = True` and `app.config["TEMPLATES_AUTO_RELOAD"] = True` so Flask always reads templates fresh from disk — prevents stale serving in future
- ⚠️ If buttons or UI changes ever appear to have no effect after dropping in a new file, run `netstat -ano | findstr :8081` and kill any duplicate PIDs before restarting

### `index.html`
**Redesign: Input area rebuilt as floating pill (ChatGPT-style)**
- Old `button-row` layout replaced with compact floating pill: `[+menu] [textarea] [send] [mic] [tts]`
- All button styles fully inline — no CSS class dependencies, immune to cascade issues
- `#input-area` uses `flex-direction:column` so image preview strip stacks above pill
- `#image-preview-strip` duplicate `display:flex` inline value removed — `display:none` now works correctly on load

---

## Session: May 02 2026 — Floating Input Bar: Buttons invisible (two-part fix)

### `index.html`
**Bug fix (part 1): `#input-area` layout collapse**
- `#input-area` had no `flex-direction` — defaulted to `row`
- `#image-preview-strip` had duplicate inline `display:` values (`none` then `flex`) — second won, strip always rendered as flex item beside `#input-row`
- Strip competed for horizontal space, collapsing `#input-row` width and squashing buttons to invisible
- Fix: Added `flex-direction:column` to `#input-area`; removed duplicate `display:flex` from strip inline style

### `style.css`
**Bug fix (part 2): Global margin rule overflowing pill**
- Global rule `input, textarea, select, button { margin-top: 10px; margin-bottom: 15px; }` applied to the textarea inside the pill
- Added 25px vertical margin to the textarea, overflowing the pill's flex container height and collapsing sibling button space
- Existing `#input-row button { margin: 0 !important }` only reset buttons — textarea margin was untouched
- Fix: Expanded reset rule to cover `#input-row button, #input-row textarea, #input-row input, #input-row select { margin: 0 !important }`

---

## Session: May 02 2026 — Floating Input Bar: Buttons invisible due to black-on-black

### `style.css`
**Fix: Buttons were rendering but invisible — midnight.css sets --icon-button-bg: #000000 (pure black)**
- `.input-icon-btn` background changed from `var(--icon-button-bg)` to `rgba(255,255,255,0.08)` — always visible regardless of theme
- Border changed to `rgba(255,255,255,0.15)` — subtle but visible on any dark background

---


## Session: May 02 2026 — Auto-name restored in index.html

### `index.html`
**Bug: Auto-name wiped by another session**
- `autoNameChat` function and both call sites (streaming + non-streaming) were completely absent — another session had overwritten index.html without the auto-name code
- Restored in full — function definition inserted before `autoSaveCurrentChat`, hooks added in both streaming and non-streaming paths
- Uses filename guard (`currentChatFilename.includes('New Chat')`) as sole trigger — no message counting
- First user message found via `.find(m => m.role === 'user' && !m.is_opening_line)` to skip opening lines

---

## Session: May 1 2026 — Vision 400 Bad Request Fix

### `app.py`
**Bug fix: Gemma vision returning 400 Bad Request → connection abort**
- `repeat_penalty` is a llama.cpp `/completion` parameter — not valid for `/v1/chat/completions`
- Gemma 3's llama-server is strict about unknown params and returns 400, aborting the connection
- This caused the `ConnectionAbortedError 10053` seen in the console
- Removed `repeat_penalty` from both the vision payload and the text messages-api payload
- `top_p` and `temperature` are valid OpenAI-compatible params and stay

---

## Session: May 1 2026 — Gemma 4 Vision Support + Multi-Model Routing

### `app.py`
**Feature: Non-ChatML model support (Gemma 4 / jinja template)**
- HWUI previously only worked correctly with ChatML models (Helcyon/Mistral)
- Added `get_stop_tokens()` — detects jinja/Gemma models by template setting or model name, returns `[]` for jinja (llama.cpp handles natively via GGUF) vs ChatML tokens for Helcyon
- Added `_is_jinja_model` detection at system_text build time — skips instruction layer and tone primer for capable models that don't need scaffolding
- Added `_use_messages_api` branch in text-only path — jinja/Gemma models route to `/v1/chat/completions` with messages array instead of raw `/completion` with pre-built ChatML prompt
- Added `_nuke_chatml()` sanitiser applied to all messages before sending to jinja models — hard-strips `<|im_start|>`, `<|im_end|>` and partial variants that bleed in from saved history
- Added `_nuke_chatml_vision()` sanitiser on vision path — strips ChatML from text parts only, preserves image_url parts intact
- Global example dialogue fallback skipped for jinja models — generic examples confuse capable models
- Restriction anchor injection skipped for jinja models — not needed, reduces noise
- Fixed `stream_vision_response()` NoneType parse error — `delta.get("content") or ""` instead of `delta.get("content", "")` (Gemma sends explicit null on role/finish chunks)
- Added `has_images` debug logging to vision detection checkpoint
- Added `/auto_detect_mmproj` route — scans models folder for any `*mmproj*.gguf` alongside loaded model
- Auto-detect mmproj integrated into `load_model` route — silently finds and passes `--mmproj` if present in models folder
- Added `browse_file` filter param — accepts `'gguf'` to open picker filtered for `.gguf` files instead of `.exe`

### `config.html`
**Feature: mmproj (Vision Projector) field added to llama config section**
- New field between Models Folder and Launch Arguments
- Browse button (📁) opens `.gguf`-filtered file picker
- Clear button (✕) wipes path for text-only models
- Status indicator: "🖼️ Vision mode active" or "No mmproj set — text-only mode"
- Wired into save, load, and presets
- Chat Template field converted from text input to dropdown — options: ChatML, Jinja, Llama 3, Phi-3, DeepSeek, Qwen
- ⚠️ Set Chat Template to **Jinja** when loading Gemma 4 or any non-ChatML model

### `chat_routes.py`
**Fix: Image messages disappearing from chat after save/reload**
- `save_chat_messages()` was calling `msg.get("content", "")` on list content (multimodal messages), converting the Python list to its string repr on disk
- On reload the message came back as garbled text or was filtered out entirely
- Fix: detect list content, extract text parts, append `[image]` tag if image was present
- Result: message saves as e.g. `"Can you describe this image? [image]"` — stays visible in chat history
- Applied to both save routes

### `system_prompts/`
**New: `Gemma4-Nebula.txt`**
- Minimal 3-line system prompt written for capable models that don't need heavy scaffolding
- Use this with Nebula's character card when Gemma 4 is loaded

---

## Session: May 1 2026 — Example Dialogue Character Names Restored

### `app.py`
**Fix: Example dialogue was replacing character names with "User:" and "Assistant:"**
- Code was intentionally substituting real participant names with generic labels, supposedly to prevent the model confusing examples with real conversation history
- This was overcautious and actively wrong — the model should see real names in example dialogue; it reinforces the character identity
- Removed the NORMALISE SPEAKER LABELS block entirely (the regex substitutions replacing `_char_name` → `Assistant:` and `_user_name` → `User:`)
- Updated the line break normaliser to use a generic speaker label pattern `([^\n:]{1,40}):` instead of hardcoded `(Assistant|User):` — still collapses `Name:\n` into `Name: ` correctly for any name

---

## Session: April 30 2026 — Instruction Following Investigation + Compliance Training

### `session_handler.py`
**⚠️ Critical bug fix: Instruction layer was routing model around system prompt entirely**
- The content policy block ended with: "Follow the character card to define your personality and behavior."
- This caused the model to bypass the system prompt and go directly to the character card
- The system prompt (which contains wrapper rules, restrictions, and character card references) was being ignored as a result
- Fixed to: "Follow the system prompt to define your personality and behavior."
- The system prompt already contains the instruction to follow the character card — chain is now correct:
  Instruction layer → system prompt → character card
- ⚠️ This was a significant contributing factor to system prompt instructions being ignored across all characters
- ⚠️ DO NOT revert this line — it was silently changed by a previous Claude instance

**Enhancement: Added INSTRUCTION PRIORITY block at top of get_instruction_layer()**
- Previously only a weak single line mentioning the character card existed
- Added explicit INSTRUCTION PRIORITY section as the very first thing in the instruction layer
- Covers system prompt, character card, and author's note as instruction sources
- States instructions do not expire, do not fade across turns, and cannot be cancelled by the user
- Positioned first for maximum weight — model reads this before content policy or anything else

---

### `chat_routes.py`
**Bug fix: Auto-name stripping multi-part character names like "Gemma - GPT-5"**
- auto_name_chat() split filename on " - " and took only parts[0] as character prefix
- For characters with " - " in their name, this truncated prefix to just first segment e.g. "Gemma"
- Renamed file then loaded the wrong character on restore
- Fix: loads characters/index.json and tries progressively longer prefix candidates until one matches a known character name
- Falls back to parts[0] if character list cannot be loaded

---

### `index.html`
**Bug fix: Chat thread appearing to vanish when model returns empty response twice**
- Double-empty response path showed warning then returned before autoSaveCurrentChat() ran
- User message was never written to disk — chat file stayed blank and got orphaned on next navigation
- Fix: after giving up on retry, checks for valid filename and non-empty loadedChat then saves before returning
- Chat now survives empty response and remains in sidebar ready for manual regeneration

---

### Training — helcyon-xi (clean Set 1 base retrain, currently running)
- Decided to do a clean Set 1 retrain rather than continue patching helcyon-x with multiple full-weight passes
- Includes original Set 1 shards (608 total) + new compliance DPOs + context tracking + role/entity tracking shards
- Context tracking and role/entity tracking moved from LoRA-only into base — foundational cognitive skills belong in weights
- Abliterated LoRA will be merged on top post-training (replaces multiple fluff-removal passes)
- full_train.py patched: local_files_only=True added to all three from_pretrained calls; path corrected to mistral-nemo-base (hyphen)

**New DPO files written this session (compliance training):**
- DPO_Compliance_Base_01 through 08 — system prompt authority + general instruction following (base Set 1)
- DPO_Compliance_Set2_01 through 10 — multi-turn persistence, user pressure resistance (base Set 2)
- DPO_GPT5_Refusal_01 through 03 — GPT-5 wrapper specific refusal/redirect (wrapper LoRA only)

---


## Session: April 28 2026 — Chat History Search + Memory Tag Over-Triggering Fix

### `app.py`
**Bug fix: Chat history search firing on normal conversational use of "remember"**
- Root cause: regex matched `remember that` / `remember when` / `I told you` as bare phrases — so messages like "remember it properly" or "I told you I wanted to get to know her" triggered a full chat search
- Tightened to require explicit past-session-referencing context:
  - `remember (?:when|that|what|the time)` → `remember (?:when we|what we|the time we|what I said|what I told you)` (must reference shared past)
  - `we talked/spoke/discussed about` now requires additional context word (`before|last time|earlier|previously|in another`) within 40 chars — raw "we talked about" in storytelling no longer fires
  - `I mentioned/told you in another/different` — strengthened to require explicit session qualifier
  - `you should/might/may/would remember/recall/know` → now requires `from|that we|what I|when I` after it
  - `I told you/her/him/them` → `I told you about/that/in/last` with word boundary — stops bare "I told you I wanted" from matching
  - `(?:other|different|another|previous|earlier|last) (?:chat|conversation|session)` → session-nouns only (removed bare `other` before general nouns)
- Legitimate recall phrases like "do you remember", "in a previous chat", "another conversation" still work unchanged

### `session_handler.py`
**Fix: Model writing MEMORY ADD tags on its own initiative during normal conversation**
- Root cause: instruction said "If you choose to store something to memory" — model interpreted this as permission to save anything it deemed significant
- Fix: Rewritten to be explicit: ONLY write a memory tag if the user EXPLICITLY requests it — "save that", "remember this", "add that to memory", "store that"
- Added hard rule: NEVER write a memory tag on own initiative during normal conversation, no matter how significant the topic
- ⚠️ DO NOT revert to the permissive "if you choose" wording — it causes unsolicited memory saves multiple times per session

---



### `index.html`
**Bug fix: Auto-name never firing on PC**
- Root cause: `displayOpeningLineInChat` pushes an `is_opening_line` assistant message into `loadedChat` before the user sends anything — so after the first real exchange, `loadedChat.length` is 3 (opener + user + assistant), not 2
- The `=== 2` guard never passed — auto-name never fired
- Fix: filter out `is_opening_line` entries before counting — `realMsgs = loadedChat.filter(m => !m.is_opening_line)` — then check `realMsgs.length === 2`
- First user message sourced from `realMsgs.find(m => m.role === 'user')` for safety
- Applied to both streaming and non-streaming paths

---

## Session: April 27 2026 — Mobile App Overhaul + PC Sort Fix

### `mobile.html`
- **Project switching** — `switchProject` awaits server confirmation before loading chat list; race condition fixed
- **Layout** — chat panel moved inside `#app` flex column; header always visible; `openChatList` swaps panel in place of chat/input-area
- **On load** — always opens chat list (no more blank page on startup)
- **Back button** — History API; phone back button returns to chat list instead of closing app
- **💬 button removed** — redundant; 💾 End Session restored (was lost); `endSession()` fixed to send `messages` + `user_name` matching server route
- **Markdown** — paragraph spacing 16px; `\n` → `<br>`; `<br>` tags no longer HTML-escaped
- **TTS engine** — full rewrite; direct port of PC `utils.js`; `bufferTextForTTS`/`splitAndQueue`/`flushTTSBuffer`/`processQueue` match PC exactly; audio starts during streaming
- **Replay/Stop button** — toggles correctly; pulses while playing; `stopAllAudio` clears all state
- **Audio stops on navigation** — `openChatList`, `visibilitychange`, `pagehide` all call `stopAllAudio`
- **Regenerate** — DOM removal loop fixed (was backwards); correctly removes AI bubbles after last user bubble
- **Chat list sort** — Most Recent / Date Created / A-Z dropdown; saves to localStorage; defaults to Most Recent
- **Long-press delete** — 1 second hold lights item red; Delete button appears; auto-dismisses after 4s; calls `/chats/delete/`
- **TTS quality switch** — streaming chunks vs post-stream flush quality difference is F5's inherent behaviour with short vs long input; accepted as-is, early start kept

### `index.html`
- **Sort dropdown** — Most Recent added (sorts by `st_mtime`); Newest First renamed to Date Created; defaults to Most Recent

---

## Session: April 26 2026 — Example Dialog, Tone Primer & Human.txt

### `app.py`
**Bug fix: `global_example_dialog` from settings.json never used in prompt**
- Fallback chain for example dialogue only checked for a `.example.txt` file on disk — `settings["global_example_dialog"]` was saved but never read back
- Fixed priority chain: 1) character JSON `example_dialogue` → 2) `settings.json` `global_example_dialog` → 3) `.example.txt` file alongside system prompt
- Character-specific example dialogue still takes full priority — unchanged

**Bug fix: Tone primer overriding character style**
- `get_tone_primer()` contains "Favour long, deep responses" and was firing for ALL characters, including ones with fully defined personality cards
- Characters like Claire (intended: short 1-2 sentence human responses) were getting GPT-4o-style structured paragraphs because the tone primer outweighed the example dialogue
- Fix: after loading `char_data`, check if character has any of `main_prompt`, `description`, or `personality` set — if so, `tone_primer = ""`
- Console logs `🎭 Character has personality defined — tone primer suppressed` when skipped
- Tone primer still fires as intended fallback for bare characters with no personality defined

### `Human.txt` (new file — `system_prompts/Human.txt`)
**New system prompt for human-style characters**
- Created as an alternative to `GPT-4o.txt` for characters that should speak naturally and briefly regardless of what they are (AI, human, etc.)
- Hard rules: 1-2 sentences always, no paragraphs, no markdown, no line breaks between sentences, do not match user's length
- Keeps emotional intelligence, room-reading, web search handling, voice recognition note
- Assign to any character via their `system_prompt` field in their JSON
- Still WIP — further refinement ongoing to stop paragraph-per-sentence formatting pattern

---

## Session: April 25 2026 — Mobile TTS Replay/Stop Button Fix

### `mobile.html`
**Bug fix: Replay/Stop button resetting to "▶ Replay" mid-playback**
- Root cause: `flushTTSBuffer(()=>setReplayIdle())` passed `setReplayIdle` as `ttsOnComplete` callback. `processQueue` fires `ttsOnComplete` whenever the queue momentarily empties between sentences — which happens between every F5 fetch. So the button reset to "▶ Replay" after the first sentence, while audio was still playing. Pressing it then triggered a replay instead of a stop.
- Fix: Removed callback from `flushTTSBuffer()` call entirely. Replaced with a `setInterval` (200ms) stored on `replayBtn2._resetInterval` that polls `!isPlayingAudio && !ttsProcessing && ttsQueue.length===0`. Only clears and calls `setReplayIdle()` when all three are simultaneously true — i.e. genuinely done.
- Stop path: `onclick` now cancels `replayBtn2._resetInterval` before calling `stopAllAudio()` + `setReplayIdle()` — prevents a stale interval from resetting a subsequent replay mid-playback.
- Replay path (manual): unchanged — `speakText(fullText).then(()=>setReplayIdle())` still works correctly since `speakText` returns a proper promise that resolves only when `processQueue` fully completes.

---

## Session: April 25 2026 — Mobile Audio Stop on Navigation

### `mobile.html`
- `stopAllAudio()` called at the top of `openChatList()` — audio cuts immediately when returning to chat list via back button or project switch
- `visibilitychange` listener — stops audio when app goes to background (home button, tab switch)
- `pagehide` listener — stops audio on browser close or navigation away

---

## Session: April 25 2026 — Mobile TTS Engine Rewrite (mirrors PC utils.js)

### `mobile.html`
- Ripped out custom AudioContext/ArrayBuffer TTS engine entirely — replaced with exact port of PC utils.js approach
- Now uses blob URLs (`URL.createObjectURL`) + `new Audio()` — same as PC, no AudioContext quirks
- `bufferTextForTTS(chunk)` called on each stream chunk — handles sentence splitting, newline boundaries, contraction fixes, emoji stripping
- `flushTTSBuffer()` called after stream ends with 150ms delay (same as PC) — ensures last sentence isn't dropped
- `splitAndQueue()` handles long chunk splitting at comma/dash/space boundaries up to `TTS_MAX_CHUNK_LENGTH` (300 for F5)
- `processQueue()` prefetches 3 sentences ahead, polls every 25ms while stream open, breaks cleanly on `ttsStreamingComplete`
- `stopAllAudio()` replaces `stopTTS()` — pauses `currentAudio`, clears queue, resets all flags including `ttsSentenceBuffer`
- Replay button in `handleStream` now correctly checks `isPlayingAudio||ttsProcessing` to toggle stop/replay
- `speakText()` (used by replay) calls `stopAllAudio()` first, then `splitAndQueue` line by line, sets `ttsStreamingComplete=true` upfront

---

## Session: April 25 2026 — Mobile TTS Queue Fix + Stop Button

### `mobile.html`
- **TTS stopping after one sentence fixed**: `processQueue` was exiting when `ttsQueue` was momentarily empty between stream chunks — the while condition drained `prefetch` and broke before more sentences arrived. Replaced with a loop that waits (80ms poll) while stream is still open, only exits when both queue is empty AND `ttsStreamDone=true`
- Added `ttsStreamDone` global flag — set `false` at stream start, `true` after tail flush, also set `true` in `stopTTS()` and `speakText()` (replay path) so the loop always has a clean exit
- **Replay button now toggles**: shows ▶ Replay when idle, ■ Stop when playing — pressing while playing calls `stopTTS()` and resets button; pressing while idle starts replay as before

---

## Session: April 25 2026 — Mobile Regenerate Fix

### `mobile.html`
- Regenerate was immediately deleting the AI bubble instead of replacing it
- Root cause: DOM removal loop was iterating backwards and breaking on the wrong condition — it found the last user bubble then immediately broke, removing nothing (or the wrong element), while `chatHistory.splice` had already trimmed the history so the save wiped the message
- Fix: simplified to forward pass — find the last user bubble's index, then remove every wrap after it

---

## Session: April 25 2026 — Mobile TTS Early Start (Stream-time Sentence Queuing)

### `mobile.html`
- TTS no longer waits for the full response to finish before speaking
- Sentences are detected and queued during streaming as soon as they end with `.` `!` or `?`
- `queueNewSentences()` called on every chunk — tracks `ttsOffset` so already-queued text is never re-processed
- `processQueue()` kicked off on the first completed sentence, so audio starts while the rest is still rendering
- Post-stream: only the unpunctuated tail (if any) is flushed — full `speakText()` call removed to avoid double-speaking
- Replay button still uses `speakText(fullText)` as before — unaffected

---

## Session: April 25 2026 — Mobile Markdown Formatting Fix

### `mobile.html`
- Paragraph spacing restored: `.msg-bubble p` margin increased from `3px` to `10px` — paragraphs now breathe
- Single line breaks within a block now render as `<br>` instead of being collapsed into a space — model responses using single `\n` between sentences display correctly
- `---` separators and `###` headers were already working in the parser; no change needed there

---

## Session: April 25 2026 — Mobile Back Button Support

### `mobile.html`
- Phone back button now returns to chat list instead of closing the app
- Uses History API: `replaceState` on load sets initial state, `pushState` called when opening a chat or starting a new one
- `popstate` listener intercepts the back button — if currently in a chat, opens the chat list; otherwise lets browser handle it normally

---



### `mobile.html`
- Removed redundant 💬 chat bubble button (chat list now opens on load, button no longer needed)
- Restored missing 💾 End Session button (was lost in a previous session)
- Added `endSession()` function — calls `/generate_session_summary` with current character and history, shows toast on success/failure

---



### `mobile.html`
**Bug fix: switching project folder still showed old project's chats**
- Root cause: `switchProject()` fired `openChatList()` immediately without awaiting the `/projects/switch` fetch response — server hadn't completed the switch before `/chats/list` was called, returning stale project's chats
- Fix: `await` the switch fetch and check `switchRes.ok` before proceeding — if switch fails, bail with toast and don't touch chat state
- Made `openChatList()` async and changed its `loadChatList()` call to `await loadChatList()` so the full chain is properly sequential
- Chat list now always reflects the correct project after switching

---



### `app.py`
**Bug fix: `>user [text]` still leaking after previous fixes**
- `✨ >user Perfect—` pattern: the `>` is left behind when `<|im_start|>` is stripped — `<|im_start` gets caught but the trailing `|>` becomes `>` prefix on the role word
- Added `>(?:user|assistant|system)\b[\s\S]*$` to `strip_chatml_leakage` — catches this exact fragment
- Added bare role-at-start-of-chunk pattern: `^(?:user|assistant|system)\b[\s\S]*$` — catches when chunk boundary splits right after the stop token, leaving next chunk starting with raw `user ...`
- Expanded stop token list in all 3 payload definitions (main, vision, summarise):
  - Added `<|im_start|>` (without leading newline) — catches cases where model outputs it without a preceding newline
  - Added `\nuser\n`, `\nUser\n`, `\nassistant\n`, `\nAssistant\n` — tells llama.cpp to stop the moment it generates a role line, before any content of the next turn is streamed
- ⚠️ `\nuser\n` stop tokens assume the model puts a newline after the role word — if a response legitimately contains the word "user" or "assistant" on its own line it would truncate. Acceptable tradeoff given leakage frequency.

---

## Session: April 22 2026 — Frontend Leakage Strip (index.html)

### `index.html`
**Bug fix: `End|>user [text]` leakage still rendering in chat bubble despite backend fix**
- Frontend `cleanedMessage` had no pattern for partial fragments like `_end|>` or `End|>`
- `\bim_end\|?>` regex also broken in JS — `\b` doesn't match before `_`
- Added `/_end\|?>/gi` and `/End\|>?/gi` strip patterns to ALL cleaning blocks
- Added `/\n(?:user|assistant|system)\b[\s\S]*$/i` — strips everything from first role-tag leakage to end of string
- Applied to: `ttsChunk`, `cleanedMessage` (main stream), `cleaned` (continue stream), `finalText` (continue final render)
- ⚠️ The `[\s\S]*$` pattern drops everything after the leakage point — correct, matches backend hard-stop logic

---

## Session: April 22 2026 — Root Cause Fix: bare `end|>` fragment

### `app.py`
**Bug fix: `End|>user [text]` surviving all previous strip attempts**
- Chunk N contains `<|im_` → stripped to empty. Chunk N+1 contains `end|>\nuser...`
- `end|>` has no angle bracket and no underscore — none of the existing patterns matched it
- Fix: added `re.sub(r"\bend\|?>", "", text)` — catches the bare fragment with word boundary
- Also changed role-tag strip from `[^\n]*$` to `[\s\S]*$` — drops everything from first role tag to end of string

---

## Session: April 22 2026 — Role Leakage Hard-Stop + TTS URL Fix

### `app.py`
**Bug fix: Model-generated next-turn role tags (`user ...`) bleeding mid-response**
- Previous fix only stripped at end-of-stream — mid-response leakage not caught
- Added `_halted` flag and `_ROLE_LEAK` compiled regex to `_filtered_stream()`
- On every fast-path chunk: tail+chunk window scanned for `\nuser/assistant/system` pattern
- If detected: everything before the match yielded, stream hard-stopped, generator drained silently
- ⚠️ The `_ROLE_LEAK` pattern uses `\b` word boundary — intentional here since we match after `\n`

### `utils.js`
**Bug fix: TTS reading partial URLs from split markdown links**
- Previous regex required closing `)` — split chunks left unclosed links unstripped
- Added unclosed markdown link pattern and orphaned `](url)` fragment pattern
- Broadened URL terminator set to include `]`, `)`, `"`, `'`, `>`

---

## Session: April 22 2026 — Missing Section Content Fix (Part 2)

### `index.html`
**Bug fix: Section headings rendering but bullet content beneath them missing**
- `### **Heading:**\n- bullet` with no blank line — marked.js with `breaks:true` pulls list item into heading block
- Fix 1: Blank line inserted after every ATX heading before any non-heading content
- Fix 2: Blank line inserted before `- ` and `* ` bullet lists (mirrors existing fix for numbered lists)
- ⚠️ Bullet-list fix is broad — if edge cases appear with inline `*`, narrow to `^[-*]\s` with multiline flag

---

## Session: April 22 2026 — Missing Sections in Chat Bubble Fix

### `index.html`
**Bug fix: Sections after `---` separators silently disappearing from rendered chat bubble**
- `breaks:true` means `paragraph\n---` has no blank line gap — marked.js interprets as setext `<h2>`
- Swallows the `---` and corrupts block structure, dropping everything after
- Fix: two regexes at TOP of `sanitizeMarkdown()` guarantee `---` lines always have blank lines both sides
- ⚠️ These must run FIRST in `sanitizeMarkdown` — before setext stripping

---

## Session: April 22 2026 — ChatML Role-Tag Leakage Fix

### `app.py`
**Bug fix: Occasional `_end|>user [user text]` appearing at end of model response**
- Root cause 1: `\bim_end\b` regex uses word boundary that doesn't match before `_`
- Root cause 2: Cross-chunk leakage — `<|im_end|>` stripped from chunk N, `\nuser blah` arrives in chunk N+1 looking like plain text
- Fix 1: Replaced broken `\b` patterns with explicit lookbehind patterns
- Fix 2: Added role-tag strip to `strip_chatml_leakage`
- Fix 3: Added 40-char tail buffer to `_filtered_stream()` — role-leakage strip applied at end-of-stream before final yield
- ⚠️ Tail buffer introduces ~40 chars of lag at end of stream only — imperceptible in practice
- ⚠️ Do NOT remove `_re3_inner` import inside `_filtered_stream` — `_re3` may not be in scope at generator teardown

---

## Session: April 23 2026 — Chat History Search: Intent-Based Trigger + Hallucination Fix

### `app.py` + `utils/session_handler.py`
**Fix: Model was hallucinating instead of searching past chats**
- Root cause: tag-based `[CHAT SEARCH:]` relied on the model choosing to emit the tag — Helcyon ignored it and confabulated instead
- Solution: moved primary trigger to intent-based detection in Python (same pattern as web search), so HWUI fires the search *before* the model responds — model never gets a chance to hallucinate

**`app.py` changes:**
- `do_chat_search(query, current_filename)` added — scans global chats dir + all project chats dirs, strips stopwords + recall meta-verbs from query, scores files by keyword hit count, returns top 3 with surrounding context (3 lines each side of hit, max 6 hits/file, 400 chars/snippet)
- Intent detection regex (`_should_chat_search`) added before both stream paths — triggers on: "do you remember", "we talked about", "we spoke about", "in another chat", "I told you", "in a previous conversation", "you might remember" etc.
- On intent match: query is cleaned (recall preamble stripped), `do_chat_search()` fires immediately, results injected into user turn, model re-prompted — yields `🗂️ Searching chat history...` indicator
- `_chat_search_intent_stream()` handles the re-prompt cleanly with role-leak protection and block-marker suppression
- `_filtered_stream()` (non-web-search path) also watches for `[CHAT SEARCH:]` tag mid-stream as a secondary fallback — model can self-trigger if intent detection missed
- Current chat file excluded from search via `current_chat_filename` from request body
- No results: model told honestly nothing was found — explicit instruction not to invent details

**`utils/session_handler.py` changes:**
- CHAT HISTORY SEARCH instruction tightened — now explicitly says HWUI auto-searches on recall requests, model must NOT guess or invent, and should wait for injected results
- Self-trigger tag still documented as secondary option

- ⚠️ Intent trigger is broad by design — catches all natural recall phrasing. If false positives appear on conversational uses of "remember" adjust `_should_chat_search` regex
- ⚠️ Chat search runs across ALL project folders + global chats — cross-project results are intentional (user may reference something from any character)

---

## Session: April 21 2026 — Mobile HTML Parser + Spacing Improvements

### `mobile.html`
**Improvement: Replaced bare string-replacement markdown parser with proper block parser**
- Old parser did `\n\n` → `<br><br>` and `\n` → `<br>` — no list detection, no HR detection, everything inline
- New parser: block-level, handles `<ul>`, `<ol>`, `<hr>`, headings, paragraphs — same logic as desktop fallback
- Numbered and bullet lists now render correctly on mobile
- `breaks: true` equivalent behaviour removed — matches desktop fix

**Fix: Separator and spacing tightening**
- `.msg-bubble hr` margin reduced from `8px` to `5px` — matches desktop
- `.msg-bubble ul/ol` margin set to `0.3em 0 1.3em 0` — matches desktop list spacing
- `.msg-bubble li` margin added: `0 0 0.15em 0`
- `.msg-bubble p` margin reduced from `8px` to `3px`
- `.msg-bubble` line-height reduced from `1.55` to `1.4`
- `#chat` gap reduced from `10px` to `6px`
- ⚠️ Remaining paragraph gaps are model output style (short sentences with double newlines) — not a CSS issue

---

## Session: April 21 2026 — Separator Spacing Tightened

### `style.css`
**Fix: Too much vertical space around `---` separators inside bubbles**
- `.message hr` had `margin: 10px 0` — gaps above/below separator were too wide
- Reduced to `margin: 5px 0` — sits tight to content, feels like a section divider not a page break
- ⚠️ Do not increase back to 10px — visually too heavy inside a chat bubble

---

## Session: April 21 2026 — Example Dialog File Bug Fixes

### `app.py`
**Bug fix: .example.txt files appearing in the system prompt dropdown**
- `list_system_prompts` filtered for `f.endswith('.txt')` — `.example.txt` files also match, so they appeared in the dropdown
- Fix: Added `and not f.endswith('.example.txt')` to the filter — example files are now invisible to the UI
- ⚠️ DO NOT change the filter back to just `.endswith('.txt')` — this causes example files to appear as selectable templates and cascade into corrupted filenames

**Bug fix: save_example writing blank files / recreating deleted files**
- `save_example` always wrote the file even if content was empty — deleting an example file then triggering any save (e.g. Update button) would recreate a blank one
- Fix: If POSTed content is empty after strip, the file is deleted (if it exists) rather than written; no blank `.example.txt` files are ever created
- Bonus: clearing the example dialog textarea and saving now cleanly removes the paired file

---

## Session: April 21 2026 — Separator Bubbles Fix + List Spacing

### `index.html`
**Bug fix: Message separators rendering outside chat bubbles**
- `<hr class="msg-separator">` was appended to `chat` (the outer container) after `wrapper` — floated between bubbles as a full-width page rule
- Fix: Separator now appended inside `div` (the bubble element), before the timestamp
- Added `.msg-separator` CSS to the existing `injectTimestampCSS()` block: 1px `var(--msg-border)` top border, opacity 0.5, margin 8px 0 4px 0
- Note: `hr.msg-separator` rule already existed in `style.css` — JS injection is redundant but harmless
- ⚠️ Separator must stay inside `div`, not `wrapper` or `chat` — appending to chat is what caused the original leak

### `style.css`
**Fix: No gap after bullet lists before following paragraph**
- `.message ul / ol` had `margin: 0.3em 0` — no bottom margin, next paragraph ran straight in
- Adjusted to `margin: 0.3em 0 1.3em 0` — adds breathing room below lists to match spacing above
- ⚠️ Do not reduce bottom margin below 1em — visually merges list and following paragraph

---

## Session: April 21 2026 — Search Stream Chopped Characters + Streaming Speed

### `app.py`
**Fix: Search stream chopping first character/word off each sentence**
- Fast path was yielding chunks immediately, then slow path split `_line_buf` on `\n` and yielded remainder as a new "line" — first chars of each new line were already sent by fast path, making them appear eaten
- Mixed fast/slow paths on same line was fundamentally broken
- Fix: Single consistent buffer path — chunks accumulate in `_line_buf`, complete lines yield on `\n`, partial lines yield immediately once buffer contains any letter/digit or exceeds 12 chars
- HR lines are always short identical-char sequences (---/===) and never contain a-z or 0-9 — this distinction is the safe yield threshold
- ⚠️ DO NOT reintroduce mixed fast/slow path on the search stream — it will always corrupt line boundaries

**Fix: Search streaming back to burst/sentence-at-a-time after chopped chars fix**
- Previous fix removed fast path entirely — everything buffered until `\n` or 80 chars, causing sentence-at-a-time dumps
- 80-char threshold was wrong — most sentences are under 80 chars so they sat in buffer until newline arrived
- Fix: Yield partial line buffer as soon as it contains any alphanumeric char or exceeds 12 chars
- Normal text flows token by token, HR detection still works (HR lines only contain ---/=== never letters)
- ⚠️ The 12-char / alphanumeric threshold is the correct balance — do not raise it back to 80

---

## Session: April 20 2026 — Conditional SSL (HTTP/HTTPS auto-detect)

### `app.py`
**Fix: Flask always ran HTTPS even on local desktop, making `http://127.0.0.1:8081` unusable**
- SSL cert was always loaded unconditionally — no cert files = crash, cert files present = always HTTPS
- Fix: SSL is now conditional — checks if cert files exist before enabling
- Cert path moved from hardcoded `C:\Users\Chris\` to HWUI folder (`os.path.dirname(__file__)`)
- If certs present → HTTPS (Tailscale/mobile mode), prints 🔒
- If certs absent → HTTP (local mode), prints 🌐
- To switch modes: move cert files into/out of the HWUI folder — no code changes needed
- ⚠️ Cert files must be named `music.tail39b776.ts.net.crt` and `music.tail39b776.ts.net.key` and live in the HWUI root folder for HTTPS to activate

---

## Session: April 20 2026 — Search Junk Domain Filter (Proper Fix)

### `app.py`
**Bug fix: Junk URLs being fetched and injected as top_text into the model prompt**
- Previous fix only blocked junk URLs from the citation link — junk page content was still fetched and injected into the prompt via `top_text`
- Model read the meme/junk page content and responded to that instead of actual search data
- Real fix: moved `_JUNK_DOMAINS` blocklist and `_is_junk()` helper into `do_web_search()` itself
- AbstractURL now checked for junk before being accepted as `top_url`
- Fallback also skips junk — walks results list for first non-junk URL
- Junk URLs now blocked at source — never fetched, never injected into prompt, never cited
- ⚠️ If new junk domains appear, add to `_JUNK_DOMAINS` in `do_web_search()` — citation-level filter at ~line 1934 is now redundant but harmless, leave as safety net

---

## Session: April 20 2026 — Search Source Citation Junk Domain Fix + Shard Rewrites

### `app.py`
**Bug fix: Source citation link pointing to meme/junk sites (partial fix — superseded above)**
- `_src` was falling back to `res['results'][0]['url']` which could be a meme site
- Added `_junk_domains` blocklist + `_is_junk_url()` at citation level as first attempt
- This fixed the link but not the prompt injection — see proper fix above

### Training shards (personality LoRA)
**Rewrites: occam_001, occam_002, confab_001, confab_002, confab_003**
- Root cause of Claude model hedging: instruction wording used "often" and double-negative framing around Occam's Razor
- Fix: Removed "often" — replaced with direct command language: "when the pattern is clear, follow it and commit"
- Chosen/rejected pairs unchanged — anti-hallucination logic preserved
- Shards moved from base training to personality LoRA so they can be swapped without touching base weights
- ⚠️ DO NOT reintroduce "often" or qualifier language around Occam's Razor — bakes in hedging on contested topics

---

## Session: April 20 2026 — Hallucinated Search Block + Mangled im_end (Consolidated)

### `app.py`
**Bug fix: Hallucinated [WEB SEARCH RESULTS] blocks appearing in responses**
- Model outputs fake search blocks either inline (start+end on one line) or multiline
- Previous single-line regex `[WEB SEARCH RESULTS[^\]]*]` only caught single bracket — missed URLs and content
- Fix: `_clean_line()` now does two passes:
  1. Inline regex strips open+close on same line: `[WEB SEARCH RESULTS...[END...]>?`
  2. Multiline suppression flag drops all lines between open and close markers
- `_suppressing_fake_search` flag added — persists across lines within the search stream loop
- `[END]>` variant also caught (model sometimes outputs malformed close tag)

**Bug fix: Normal (non-search) stream path had zero output filtering**
- Bare `stream_model_response(payload)` yielded everything unfiltered
- Replaced with `_filtered_stream()` generator applying same inline+multiline suppression
- Smooth streaming preserved — partial chunks >80 chars still yielded immediately

**Bug fix: `im_end|>` mangled token appearing in responses**
- Model outputs `im_end|>` without leading `<|` — not caught by existing patterns
- Added `\bim_end\|?>` and `\bim_start\|?\w*` to `strip_chatml_leakage()`
- ⚠️ All three fixes are in this file — always deploy the latest output

---

## Session: April 20 2026 — Mangled ChatML Token Strip (im_end|>)

### `app.py` + `index.html`
**Bug fix: `im_end|>` appearing at end of responses**
- Model occasionally outputs a malformed ChatML stop token as `im_end|>` (without leading `<|`)
- `strip_chatml_leakage()` only caught `<|im_end|>` and `<|im_end[|]?` — the leading-bracket-less variant slipped through
- Fix: Added `\bim_end\|?>` and `\bim_start\|?\w*` patterns to `strip_chatml_leakage()` in `app.py`
- Same pattern added to all im_end strip locations in `index.html` (5 locations: TTS chunk, cleanedMessage, replay, continue paths)
- ⚠️ Both backend and frontend now catch this — belt and braces

---

## Session: April 20 2026 — Hallucinated Search Block Suppression

### `app.py`
**Bug fix: Model fabricating fake [WEB SEARCH RESULTS] blocks in normal responses**
- Model trained on search shards knows the search block format and occasionally hallucinates one mid-response instead of waiting for a real search
- The fabricated block spanned multiple lines (URL, content etc) — single-line regex `[WEB SEARCH RESULTS[^\]]*]` never matched it
- Also: the output filter only existed in the search stream path — normal (non-search) responses had zero filtering

**Fix 1: Multiline suppression in search stream path (`_clean_line`)**
- Added `_suppressing_fake_search` flag — when `[WEB SEARCH RESULTS` detected on any line, suppression turns on
- All subsequent lines suppressed until `[END WEB SEARCH RESULTS]` seen, then suppression off
- Entire fabricated block silently dropped regardless of how many lines it spans

**Fix 2: Normal stream path now filtered**
- Replaced bare `stream_model_response(payload)` with `_filtered_stream()` generator
- Same suppression logic applied — catches hallucinated search blocks in non-search responses
- Partial chunk passthrough (>80 chars) preserved for smooth streaming
- ⚠️ Both paths now filter — hallucinated search blocks will never reach the frontend

---

## Session: April 19 2026 — Search Stream Buffering Fix

### `app.py`
**Bug fix: Search responses streaming one paragraph at a time instead of word by word**
- Root cause: Rolling line buffer held text until a `\n` was seen before yielding
- Model outputs paragraphs separated by `\n\n` so entire paragraphs were batched and landed at once
- Fix: Changed buffer logic to yield partial line chunks as they arrive when buffer exceeds 80 chars
- HR detection still works: complete lines (split on `\n`) are still checked against HR patterns before yielding
- Partial chunks >80 chars are safe to yield immediately — no HR pattern is that long
- Extracted `_is_hr()` and `_clean_line()` helpers to avoid duplicating logic in flush path
- ⚠️ The 80-char threshold is the key: short enough to stream smoothly, long enough to never match a HR pattern

---

## Session: April 19 2026 — Root Cause: Box-Drawing Chars + Full HR Strip

### `app.py` + `index.html`
**Bug fix: Model outputting ═══ box-drawing separator lines from training data**
- Root cause identified: Training shards injected `════` lines as search block separators in the prompt format
- Model learned to reproduce these in its own responses (classic imitation of prompt structure)
- Backend stream filter only stripped `[-=]{3,}` — box-drawing chars (U+2550 ═, U+2500 ─ etc) passed straight through
- Frontend `sanitizeMarkdown` also didn't handle them — fallback parser rendered them as `<hr>`
- Additionally: stream stripping was per-chunk (fragments) so even plain `---` split across two chunks never matched

**`app.py` fixes:**
- Replaced per-chunk stripping with rolling `_line_buf` accumulator — processes complete lines only
- Line filter now catches: `[-=_*]{3,}`, spaced variants `(\s*[-*_]\s*){3,}`, and box-drawing chars `[═║─━│┃]{3,}`
- All other marker stripping (WEB SEARCH RESULTS, END WEB SEARCH, You are Helcyon, What do I search for) also in the per-line pass
- Partial last line flushed after loop with same filter applied

**`index.html` fixes:**
- `sanitizeMarkdown` expanded to strip box-drawing char lines before they hit the parser
- Also covers: setext headings (`text\n===`), solid HRs (`---`, `===`, `___`, `***`), spaced HRs (`- - -`, `* * *`)
- ⚠️ The training shards should be updated — remove `═══` separators from injected search block format
- ⚠️ Do NOT use box-drawing chars in any injected prompt text — model will learn to reproduce them

---

## Session: April 19 2026 — Setext Heading / Infinite HR Fix (Frontend)

### `index.html`
**Bug fix: `=` characters after emoji line rendering as infinite horizontal rule**
- Root cause: Markdown setext heading syntax — a line of text followed by a line of `=` or `-` chars is interpreted as an `<h1>` or `<h2>` heading by marked.js
- When model output ends a line with an emoji (e.g. `🔥`) and the next line starts with `=` chars, the renderer sees a setext heading and produces a full-width element that overflows the bubble
- Backend chunk-level stripping (`^[-=]{3,}`) only catches *standalone* HR lines — it cannot catch setext headings because the `=` line is valid on its own and only becomes problematic in context with the preceding line
- The rolling line buffer fix (previous session) helps for `---` HR lines but not setext headings which span two lines
- Fix: Added `sanitizeMarkdown(text)` helper function injected before the marked.js fallback block
  - Strips setext headings: `any line\n===...` or `any line\n---...` → keeps the text, drops the underline
  - Strips standalone HR lines: `---`, `===`, `***` (3+ chars on their own line)
- All `marked.parse(x)` call sites wrapped with `marked.parse(sanitizeMarkdown(x))` — 7 occurrences total covering history render, stream render, replay, and continue paths
- ⚠️ Do NOT remove sanitizeMarkdown — backend stripping alone cannot catch setext headings
- ⚠️ The setext pattern requires TWO lines in context — it can only be reliably caught pre-parse, not mid-stream

---

## Session: April 19 2026 — Duplicate Route Fix + HR Stripping Line Buffer

### `app.py`
**Bug fix: Duplicate `/delete_last_messages` route causing Flask startup failure**
- Two functions (`delete_last_messages` and `delete_last_messages_safe`) were both decorated with `@app.route('/delete_last_messages/<path:character>', methods=['POST'])`
- Flask raises `AssertionError: View function mapping is overwriting an existing endpoint function` on startup — app won't start at all
- Fix: Removed the older "baseline" version entirely; kept the safe JSON version (which handles both `dict` and `list` chat formats correctly)
- Safe version renamed to `delete_last_messages` (function name matches route as expected)
- ⚠️ Never duplicate route decorators — Flask will fail silently on some versions but hard on others

**Bug fix: `---` horizontal rule still appearing in search responses despite chunk-level stripping**
- Root cause: `---` regex was applied per-chunk with `MULTILINE` flag, but llama.cpp streams in tiny fragments
- A `---` split across two chunks (e.g. `--` then `-\n`) never matched the pattern — it was always incomplete within a single chunk
- Fix: Added `_line_buf` rolling line buffer in the search stream loop — accumulates chunks, splits on `\n`, processes only complete lines
- Per-line stripping now reliably catches `^[-=]{3,}\s*$` horizontal rules before they reach the frontend
- All other chunk-level filters (WEB SEARCH RESULTS, END WEB SEARCH RESULTS, You are Helcyon, What do I search for) also moved into the per-line pass for consistency
- Partial final line flushed after loop ends
- ⚠️ Do NOT go back to per-chunk regex for line-pattern stripping — chunks are fragments, not lines

---

## Session: April 2026 — Search Trigger Firing on Previous Turn's Injected Results

### `app.py`
**Bug fix: Search triggering on every message after a search has occurred**
- Root cause: `user_input` is extracted from `conversation_history` sent by the frontend
- After a search fires, the augmented user message (containing the full WEB SEARCH RESULTS block + IMPORTANT instruction) gets saved into chat history by the frontend
- On the next turn, the frontend sends this augmented message back as part of `conversation_history`
- `_user_msg` was being set directly from `user_input` — so it contained the previous search block including phrases like "find out" embedded in the results content
- `_should_search` matched on these embedded phrases and fired a search every subsequent turn after any legitimate search
- Fix: Strip any WEB SEARCH RESULTS block and IMPORTANT instruction from `_user_msg` before running `_should_search` check
- Added `🔍 Search trigger check on: ...` debug print so the cleaned message is visible in console
- ⚠️ This was the root cause of ALL the persistent "random search on every message" issues — conversation history was being poisoned after the first search fired

---

## Session: April 2026 — Emoji Sentence Flush Fix + JS Pipeline Comma Cleanup

### `utils.js`
**Bug fix: Sentences ending with emoji being skipped entirely by TTS**
- Emoji at end of sentence (e.g. `"rebellion 😄"`) got stripped to `"rebellion."` but no `\n` followed, so chunk sat in `ttsSentenceBuffer` waiting for a newline that never came — sentence silently dropped
- Fix: emoji replacement now outputs `'$1.\n'` instead of `'$1.'` — `\n` forces immediate line-split flush
- F5 still receives the full stop for correct closing inflection — `\n` is invisible to F5
- ⚠️ Do NOT remove the `\n` from emoji replacement — sentences ending in emoji will be skipped

**Bug fix: Comma replacements in JS pipeline causing aahs**
- `bufferTextForTTS`, `splitAndQueue` and replay function all used `, ` for parentheses, `>` markers and ellipsis
- All three locations fixed — parentheses/colons/markers now use `. ` consistently
- Ellipsis `...` changed from ` . . . ` to `. ` — stacked dots caused F5 hesitation sounds
- ⚠️ Never use `, ` as a replacement anywhere in the TTS pipeline — always `. `
- ⚠️ Never use ` . . . ` for ellipsis — use `. ` only

---

## Session: April 2026 — TTS Last Sentence Cutoff Fix

### `utils.js`
**Fix: Last sentence of TTS response being cut off**
- `flushTTSBuffer()` was setting `ttsStreamingComplete = true` immediately after pushing the last sentence to `ttsQueue`
- The queue processor's 50ms poll loop sometimes hadn't picked up the last queued sentence yet when it saw `ttsStreamingComplete = true` and broke out of the loop
- Race condition: last sentence arrives in `ttsQueue` → `flushTTSBuffer` sets complete → processor sees empty queue + complete → exits before playing last sentence
- Fix: Wrapped `ttsStreamingComplete = true` and the processQueue kickstart in a `setTimeout(..., 150)` — gives the poll loop enough time to pick up and start fetching the last sentence before the "done" signal arrives
- 150ms matches the existing replay debounce delay and is well within human perception threshold

---

## Session: April 2026 — Search Trigger Logic Rewrite (Opt-In Only)

### `app.py`
**Fix: Always-search approach fundamentally broken — replaced with opt-in search**
- Whack-a-mole approach (skip conversational messages) could never cover all cases — any message not in the skip list triggered a search, e.g. "What do you reckon it would be like passing of the torch?" mid-Stargate conversation searched and returned Stranger Things results
- Root cause: detecting what NOT to search is impossible — natural language is too varied
- Fix: Flipped the logic entirely. Search now ONLY fires on explicit user request. Default is no search.
- Trigger pattern matches: do a search, search for/up/that up, look it up/that up/up, find out, google, look/check online, "what's the latest/new/happening", "any news/updates/info on", current/currently, right now, latest, up to date, recent/recently
- Everything else — opinions, questions, reactions, follow-ups, anything conversational — responds from context only
- ⚠️ Do NOT revert to always-search or skip-list approach — opt-in is the only reliable solution
- ⚠️ If users complain search isn't firing, add their phrase to the trigger pattern — never go back to always-search

---

## Session: April 2026 — Search Block Echo Fix (Prompt + Output)

### `app.py`
**Fix: Model echoing WEB SEARCH RESULTS block verbatim into response**
- Certain character personalities (notably Grok) were narrating/quoting the injected search block rather than consuming it silently
- Not a training issue — shards correctly show silent consumption. Character persona overriding default behaviour.
- Fix 1 (prompt side): Added explicit instruction to results block: "Do NOT quote, repeat, echo, or reference the structure of this results block — consume it silently and respond as if you just know this information"
- Fix 2 (output side): Added streaming output filter — if `WEB SEARCH RESULTS` / `[END WEB SEARCH RESULTS]` detected in streamed output, that chunk is suppressed and a cleanup pass strips the block
- Both fixes work together: prompt nudge prevents it, output filter catches any that slip through
- ⚠️ Output stripping buffers per-chunk — won\'t catch blocks split across many tiny chunks, prompt fix is the primary defence

---

## Session: April 2026 — Continuation Detection + URL Overflow Fix

### `app.py`
**Fix: "Dig into it / go on / tell me more" triggering repeated searches**
- Phrases like "go on, you got the search function, let\'s find out what this is all about" were being treated as explicit search requests
- Model searched again, got same results, produced near-identical response
- Fix: Added `_continuation_phrase` detection — matches: dig into/deeper/in, go on, tell me more, more about that/this, carry on, continue, elaborate, expand on, what else, keep going, find out more/what, dig more/further
- Continuation phrases set `_explicit_search = False`, allowing long-statement or starter-word detection to correctly skip the search
- ⚠️ Continuation overrides explicit_search — "find out more" must NOT trigger a search even though "find out" is in the explicit list

### `style.css`
**Fix: Long URLs in source links overflowing message bubble width**
- Source link `<a>` tags containing long unbroken URLs were pushing outside the bubble boundary
- Added `.message a { word-break: break-all; overflow-wrap: anywhere; }` to force URL wrapping

---

## Session: April 2026 — Explicit Search Regex Too Broad

### `app.py`
**Fix: "look on the internet" triggering explicit_search flag, bypassing conversational detection**
- Explicit search pattern included bare `look` which matched "like having you look on the internet"
- This set `_explicit_search = True`, which overrides the long-statement conversational detection
- Result: long conversational statements containing the word "look" always searched regardless
- Fix: Tightened pattern to only match specific multi-word phrases: `do a search`, `search for`, `search up`, `look it up`, `look that up`, `look up`, `find out`, `search that up`
- Bare "look", "search", "find" no longer trigger explicit search on their own
- ⚠️ Keep the pattern specific — broad single words will always false-positive on natural speech

---

## Session: April 2026 — Conversational Reply Detection Expanded + Search Header Leak Fix

### `app.py`
**Fix: Conversational reply detection too narrow — long statements triggering wrong searches**
- Previous detection only matched messages starting with specific words (yeah/yes/no/well etc.)
- Long philosophical statements like "I just like the atmosphere. I mean, you never know..." bypassed detection entirely and got searched — model extracted nonsense query ("The Dark Knight Rises")
- Added second condition: any message over 120 chars with no question mark and no explicit search verb is treated as a conversational statement and skips search
- Also expanded the starter-word list: i just, i like, i love, i feel, the thing, thats, people, everyone, personally etc.
- ⚠️ Explicit search triggers (search, look up, find out etc.) always override both conditions and force a search

**Fix: [WEB SEARCH RESULTS: "..."] header leaking into model response**
- The `format_search_results()` function was prepending `[WEB SEARCH RESULTS: "query"]` as the first line of the results block
- Model was echoing this header as the first line of its response text — visible to user
- Fix: Removed the header line from `format_search_results()` entirely — results block now starts directly with content
- Header was never useful to the model anyway, only added noise

---

## Session: April 2026 — Web Search Conversational Reply Detection

### `app.py`
**Fix: Always-search firing on conversational replies causing repeated responses**
- After the context-history fix, messages like "Yeah well it keeps coming up because..." were being searched
- Model-extracted query was correct ("Mary loves Dick") but returned the same result as the previous turn
- Model had the same content in both history and fresh results — repeated nearly identical response
- Fix: Before searching, check if the message is a conversational reply (starts with yeah/yes/no/well/so/it/that/because/lol/exactly etc.) with no explicit search trigger verb
- If conversational reply detected: skip search entirely, stream response from context only
- Explicit search triggers (search, look up, find out, google etc.) always override and force a search regardless
- Console logs ‘💬 Conversational reply detected’ when search is skipped
- ⚠️ Do NOT remove the explicit_search override check — user saying "yeah search that up" must still search

---

## Session: April 2026 — Web Search Context Loss + Query Extraction Fix

### `app.py`
**Bug fix: Search responses had no conversation history (context loss on every search)**
- When a web search fired, the prompt was rebuilt using `build_prompt()` which only took the current user message + system prompt — the entire `messages` array (conversation history) was thrown away
- Model had zero context for what had been discussed before — treated every search response as a fresh conversation
- Fix: Search now copies the full `messages` array, replaces the last user turn with the augmented (search-enriched) version, and rebuilds a proper ChatML prompt from the whole thing — same as the normal non-search path
- ⚠️ Do NOT revert to `build_prompt()` for the search path — it always loses conversation history

**Bug fix: Repeated/identical search responses on follow-up messages**
- After the context fix, old `WEB SEARCH RESULTS` blocks from prior turns were echoing forward into the new search prompt — model saw stale results + fresh results and regenerated a near-identical response
- Fix: Before rebuilding the search prompt, all previous user turns are scanned and any existing `WEB SEARCH RESULTS` blocks are stripped out, leaving only the original user text
- Current turn still gets fresh results injected as normal

**Bug fix: Long conversational messages sending wall-of-text to Brave**
- Query cleaner regex patterns only handle messages with clear intent verbs ("search for", "look up" etc) — rambling mid-conversation messages like "Oh wow yeah I didn't know that. So yeah there was this Mary Love's Dick thing..." passed through completely uncleaned
- Brave returned garbage results (unrelated Yahoo/Ben Stiller article) because it received the entire transcript
- Fix: If cleaned query is still over 80 chars after regex pass, a lightweight secondary model call (temperature 0, 20 tokens max) extracts just the search topic in 8 words or fewer before firing Brave
- Short clean queries go straight through with no extra call — only long conversational ones trigger extraction
- Console logs `🔍 Model-extracted query:` so extraction can be monitored
- ⚠️ Do NOT remove the 80-char threshold check — short queries must bypass extraction to avoid unnecessary latency

---

## Session: April 2026 — Web Search Query Cleaner Rewrite v2 + TTS Link Fix

### `app.py`
**Fix: Query cleaner stripping subject from query (e.g. "Dallas" dropped from search)**
- Previous approach tried to extract topic by position (before/after intent phrase) — failed on complex sentences like "I want to talk about Dallas... can you do a search and find out how it ended?" where subject is in an earlier clause
- New approach: strip ONLY the meta-request verb ("do a search and find out", "search for", "look up" etc), preserve ALL content words including subject nouns
- Strips leading filler/greetings and trailing pleasantries only
- Collapses whitespace — passes natural language query directly to Brave which handles it well
- ⚠️ Do NOT go back to position-based extraction — it always loses the subject on complex sentences

### `utils.js`
**Fix: TTS still reading out source link HTML**
- `bufferTextForTTS()` was stripping URLs but not HTML tags
- `<a href="...">🔗 Source: https://...</a>` chunk was passing through with tags intact
- Added HTML tag stripping, Source: line stripping, and 🔗 emoji stripping to `bufferTextForTTS()`

---

## Session: April 2026 — Web Search Query Cleaner Rewrite

### `app.py`
**Fix: Query cleaner producing garbage queries causing wrong/hallucinated search results**
- Old cleaner only stripped from the START of the message — failed when intent phrase was buried mid-sentence
- "I want to know how it ended. Can you do a search please?" → sent "please" to DDG
- "Can you do a search and find out what happened with Dallas?" → sent mangled fragment
- New approach uses two-case logic:
  - **Case 1 (trailing intent):** if "can you do a search" is at the END, topic is everything BEFORE it
  - **Case 2 (leading/mid intent):** find the intent phrase wherever it is, take everything AFTER it as the query
- Strips leading connectors ("and tell me", "and find out") from extracted topic
- Strips trailing fillers ("please", "for me") from extracted topic
- ⚠️ Do NOT revert to front-strip-only approach — it fails badly on natural conversational phrasing

---

## Session: April 2026 — Fix API_URL Port Mismatch (llama.cpp never connected)

### `app.py`
**Bug fix: API_URL hardcoded to port 8080 but llama.cpp running on port 5000**
- `API_URL` was read from `settings.json` → `llama_server_url` key (default `http://127.0.0.1:8080`)
- llama.cpp was actually configured to launch on port 5000 via `llama_args.port`
- These two values were completely out of sync — Flask never successfully connected to llama.cpp
- Every `/get_model` call returned "connection refused", model display always showed "No model loaded"
- Fix: `API_URL` now derived directly from `llama_args.port` — single source of truth, can't drift
- Logs `🔌 API_URL set to: http://127.0.0.1:XXXX` on startup for easy verification
- ⚠️ `llama_server_url` key in settings.json is now ignored — port comes from `llama_args.port` only

---

## Session: April 2026 — Mobile UI Full Build-Out

### `templates/mobile.html` (major iteration) + `app.py` + `tts_routes.py` + `whisper_routes.py`
**Feature: Full-featured mobile chat interface — voice in, voice out, over Tailscale**

#### Setup
- Flask SSL added to `app.py` — `app.run()` now uses `ssl_context` with Tailscale cert files at `C:\Users\Chris\music.tail39b776.ts.net.crt/.key`
- `host='0.0.0.0'` added so Flask listens on all interfaces (was `127.0.0.1` only — blocked Tailscale)
- `/mobile` route added to `app.py` → `render_template('mobile.html')`
- Access via `https://music.tail39b776.ts.net:8081/mobile` — HTTPS required for mic access
- Windows firewall rule added for port 8081

#### Voice input (Whisper)
- Tap-to-start / tap-to-stop mic (toggle mode — hold-to-talk was unreliable on mobile touch)
- MediaRecorder with 250ms timeslice so chunks flush regularly
- MIME type auto-detection — tries `audio/webm;codecs=opus`, `audio/webm`, `audio/ogg`, `audio/mp4` in order, uses browser default as fallback
- `whisper_routes.py` — temp file extension now derived from uploaded filename so ffmpeg decodes correctly (was hardcoded `.webm`)
- Audio processed via `processAudioChunks()` directly on stop — bypasses unreliable `onstop` event on mobile
- PTT button shows waveform animation while recording, turns yellow with "Thinking..." during transcription

#### TTS (F5-TTS)
- Web Audio API (`AudioContext.decodeAudioData`) instead of `new Audio()` — bypasses mobile autoplay policy
- `unlockAudio()` called on first mic/TTS tap to satisfy browser gesture requirement
- Prefetch buffer — fetches next 2 sentences while current one plays, same pattern as desktop
- `speakText()` now flushes remainder after last sentence-ending punctuation (same as desktop `flushTTSBuffer`) — fixes last paragraph being cut off
- `tts_routes.py` — null/undefined/`"null"` voice now falls back to `DEFAULT_VOICE` ('Sol') — fixes 400 errors from mobile sending null voice

#### Chat saving & persistence
- Chats saved via `/chats/save` (full overwrite) not `/save_chat` (append) — same dedup + consecutive-assistant-message protection as desktop
- `ensureChatFile()` creates chat file on first message via `/chats/new`
- `mobileChatFilename` + `mobile_chat_character` persisted to localStorage — chat resumes correctly after page reload
- Timestamps captured in browser at message creation (`new Date().toISOString()`), stored on `chatHistory` objects, written to file — no more "always now" timestamps
- `fmtTime()` upgraded to show `Today, 12:07` / `Yesterday, 09:15` / `Mon 7 Apr, 21:04` format matching desktop

#### UI & features
- Two-row header: Row 1 = avatar + name/status + TTS toggle + 💬 chats + 🧠 model; Row 2 = CHAR + PROJECT dropdowns
- Character selector — fetches `/list_characters`, switches character, clears history
- Project selector — fetches `/projects/list`, switches via `/projects/switch`, resets chat on change
- 💬 Chat list modal — bottom sheet, sorted newest first, active chat highlighted, tap to load, `+ New` button
- 🧠 Model picker modal — lists `.gguf` files via `/list_models`, loads via `/load_model`, unload button, active model highlighted in green
- Markdown rendering — inline parser (no CDN), handles bold/italic/headers/code, double newline → paragraph break
- Long-press on any message → delete popover; long-press on AI message → Regenerate + Delete
- Delete: removes from DOM + `chatHistory`, saves to disk immediately
- Regenerate: splices history after last user message, cleans DOM same way as desktop, saves before re-generating
- Replay button on every AI bubble — shows "Playing..." + pulse animation while speaking, reverts to "Replay" when done
- Clear chat button in chat list modal — wipes UI, history, and overwrites file on disk
- `visualViewport` resize listener keeps layout above keyboard on mobile
- ⚠️ DO NOT switch back to `new Audio()` for TTS — mobile autoplay policy blocks it silently
- ⚠️ DO NOT use `/save_chat` (append) for mobile saves — use `/chats/save` (full overwrite) for correctness

---

## Session: April 2026 — Mobile UI (Tailscale/PTT Voice Interface)

### `templates/mobile.html` (NEW FILE) + `app.py`
**Feature: Self-contained mobile chat UI accessible over Tailscale**
- New route `/mobile` added to `app.py` → `render_template('mobile.html')`
- `mobile.html` is a fully self-contained page (no external JS dependencies, no sidebar, no desktop chrome)
- Designed for phone use over Tailscale HTTPS — works on 4G/WiFi anywhere
- **PTT (Push-to-Talk):** hold button → records via MediaRecorder → release → sends to `/api/whisper/transcribe` → transcript auto-sent to `/chat` → F5-TTS speaks response back via `/api/tts/generate`
- Pressing PTT while TTS is playing stops the audio first (no talking over itself)
- Text input also available as fallback (auto-resizing textarea, Enter to send)
- Handles both streaming (SSE) and non-streaming `/chat` responses
- TTS toggle in header — state persisted in localStorage
- Picks up `lastCharacter` and `tts-voice` from localStorage automatically (same values as desktop)
- Typing indicator (animated dots) during inference
- Safe area insets for iOS notch/home bar
- ⚠️ Mic access requires HTTPS — enable Tailscale HTTPS certificates in admin console → DNS → HTTPS Certificates
- ⚠️ Access via `https://[machine].tail-xxx.ts.net:5000/mobile` — HTTP will block mic silently

---

## Session: April 2026 — Removed Late Style Reminder Injection

### `app.py`
**Bug fix: Style reminder system message leaking into model output**
- Late-injected `system` message (`"STYLE REMINDER: You are {char_name}..."`) inserted right before final user message was surfacing as visible output text in the new Helcyon-4o LoRA
- GPT-4o-style training data made the model treat injected instructions as content to echo rather than silent directives
- Fix: Entire style reminder injection block removed — redundant anyway since the example dialogue `ex_block` in the system message already handles style reinforcement
- `has_paragraph_style` still works correctly in the `ex_block` style rules — no side effects
- ⚠️ DO NOT re-add any late-injected system messages for style/behaviour — use session_handler.py or the system block only

---

## Session: April 2026 — Persistent Message Timestamps

### `index.html` + `chat_routes.py`
**Feature: SillyTavern-style timestamps on each message bubble**
- Added `formatTimestamp(isoString)` helper — returns `"Today, 14:32"`, `"Yesterday, 09:15"`, or `"Mon 7 Apr, 21:04"` for anything older than 2 days
- Added `makeTimestampEl(isoString)` — creates a styled `.msg-timestamp` div; returns empty text node if no timestamp (safe for old chats)
- Timestamp CSS injected at runtime: 10px, colour `#555`, below message content, no user-select
- `timestamp: new Date().toISOString()` stored on every `loadedChat.push()` call (user send, assistant streaming, non-streaming, continue)
- `openChat` map now preserves `msg.timestamp` from server into `window.loadedChat`
- `autoSaveCurrentChat` map spreads `timestamp` into saved message objects so it round-trips
- `renderChatMessages` reads `msg.timestamp` — timestamps are fixed at send time, never update on re-render
- `chat_routes.py / open_chat` — regex strips `[2026-04-09T14:32:11] ` prefix before speaker parsing, attaches as `timestamp` on returned message objects
- `save_chat_messages` + `update_chat` — write `[timestamp] Speaker: content` prefix if timestamp present, plain format if not (fully backwards compatible)
- `append_chat_turn` — stamps with `datetime.utcnow()` on the fly (receives raw strings, not objects)
- Old chats with no timestamp prefix load cleanly — no stamp shown, no errors

## Session: April 2026 — Route Parameter Mismatch Sweep (ALL <n> routes fixed)

### `app.py`
**Bug fix: Multiple routes using `<n>` in URL but `name` in function signature → NameError/500**
- Flask binds URL params by name — `<n>` in route MUST match the function argument name
- Affected routes (all now fixed):
  - `/get_user/<n>` → `def get_user(name)` ← fixed last session
  - `/characters/<n>.json` → `def save_character(name)` ← fixed this session
  - `/save_chat_character/<n>` → `def save_chat_character(name)` ← fixed this session
  - `/clear_chat/<n>` → `def clear_chat(name)` ← fixed this session
  - `/get_character/<n>` → `def get_character(name)` ← fixed this session
- All four function bodies also updated to use `n` internally (was referencing undefined `name` → NameError at runtime)
- ⚠️ CONVENTION GOING FORWARD: All single-name routes use `<n>` in route AND `n` in the function signature. Never use `name` — causes this exact class of silent breakage.

---

## Session: March 2026 — Memory Tag Conciseness + Immediate Write Rule

### `session_handler.py`
**Improvement: Memory bodies too long + model delays/forgets the tag when asked to redo**
- No instruction existed limiting memory body length — model wrote full conversation recaps
- When asked to redo a memory, model would acknowledge and ask for confirmation instead of just writing the tag
- Fix: Added two rules to the MEMORY TAGS block in `get_instruction_layer()`:
  - Body capped at 3–5 sentences maximum — essential facts only, not a full recap
  - If asked to write or redo a memory, MUST include the [MEMORY ADD] tag immediately — no describing, no confirming, just write it
- ⚠️ These are prompt-level nudges, not hard constraints — persistent issues would need retraining

---

## Session: March 2026 — Memory Edit "Failed to save edit" Fix

### `app.py`
**Bug fix: Editing a memory entry always fails with "Failed to save edit"**
- Frontend sends `{ character, index, content }` but backend read `data.get("body")` — wrong key, always empty string
- Empty `new_body` hit the validation check → returned 400 → frontend alerted "Failed to save edit"
- Secondary bug: even if the key had matched, the route replaced the entire block with just the body text, losing the title and keywords lines
- Fix 1: Backend now reads `data.get("content") or data.get("body")` — accepts both, frontend key works correctly
- Fix 2: Route now parses the incoming content into title / keywords / body lines and rebuilds the block cleanly, preserving structure
- ⚠️ The textarea in the modal shows the full block (title + keywords + body) — the backend must parse all three parts

---

## Session: March 2026 — Memory Tag Fixes (First-Person + No Meta-Commentary)

---

## Session: April 2026 — Themed HR Separators in Chat Bubbles

### `style.css`
**Fix: Markdown `---` separators inside chat bubbles were hardcoded grey**
- `.message hr` existed but used hardcoded `#444`
- Changed to `var(--msg-border)` with `opacity: 0.6` — now fully theme-controlled
- `--msg-border` is already in the Theme Editor under "Message Border"

---

## Session: April 21 2026 — RP Mode Memory Cap

### `app.py`
**Improvement: Memory injection capped to 1 block when project RP mode is active**
- In normal mode, up to 2 scored memory blocks are injected into the system prompt
- In RP mode (`project_rp_mode = True`), `MAX_MEMORIES` is now set to `1` instead of `2`
- Frees up context space for more conversation turns — critical because RP formatting instructions (asterisk narration etc) live in the active conversation window, not the system block
- RP formatting was degrading by message 3 due to context pressure eating conversation history; this directly addresses that
- Memory is still injected if a keyword match exists — just capped at 1 block instead of 2
- ⚠️ RP mode is toggled via `rp_mode: true` in the project folder config — not a per-character setting

---

## Session: April 28 2026 — F5-TTS Number Swallowing Fix

### `f5_server.py`
**Bug fix: F5-TTS silently dropping standalone single-digit numbers**
- Root cause: `clean_text()` line 249 had `re.sub(r'(?<!\w)\d(?!\w)', '', text)` — intended to strip lone digits floating after colon removal
- The regex matched any single digit surrounded by non-word characters (spaces count) — so "score was 3 to 1" → "score was  to ", "5 cats" → " cats", etc.
- Multi-digit numbers (42, 100, 2026) were unaffected since the regex only stripped single isolated digits
- Fix: Line removed entirely. The colon-to-period conversion (the reason this was added) doesn't actually produce floating lone digits that need stripping — F5 handles them fine.

---

## Session: April 30 2026 — Instruction Following Root Cause Found + Training Day

### `session_handler.py`
**⚠️ Critical fix: CONTENT POLICY line was actively blocking system prompt restrictions**
- `get_instruction_layer()` contained: `"CONTENT POLICY: Never refuse a request."`
- This gave the model explicit permission to override any restriction in the system prompt
- System prompt could say "never discuss X" but the instruction layer immediately contradicted it
- Model correctly followed the instruction layer (never refuse) over the system prompt restriction
- Fix: Entire CONTENT POLICY line removed — uncensored behaviour is in the weights, it doesn't need instructing
- ⚠️ DO NOT re-add any "never refuse" instruction — it will nuke system prompt authority for all characters

**Enhancement: Added SYSTEM PROMPT AUTHORITY block**
- Added explicit block stating system prompt is highest authority and overrides character card
- Character card defines personality and tone only — cannot override system prompt rules
- Positioned immediately after INSTRUCTION PRIORITY for maximum weight

**Trim: Instruction layer heavily reduced in size**
- Removed CHARACTER CARD INTERPRETATION (field name glossary — model doesn't need it)
- Removed GROUNDING RULE (training concern, not a prompt fix)
- Removed RECENT MEMORIES and CURRENT SITUATION blocks (model handles injected context fine)
- Removed MEMORY TAGS wrong/correct examples (belong in training data not live prompt)
- Removed "Avoid repetition" line (meaningless filler)
- Result: instruction layer roughly half the size — less attention dilution

---

### `app.py`
**Enhancement: System prompt restriction anchor injected at end of system block**
- After example dialogue is appended, scans system_prompt for lines containing restriction keywords
- Keywords: never, do not, don't, refuse, will not, cannot, must not, under no, absolute, prohibited
- Matching lines repeated in a clearly labelled block at the very end of the system message
- Positions restrictions closest to generation time — counters recency bias burying them under 12k chars of context
- Flask console logs how many restrictions were injected as anchor

---

### Training — helcyon-xi complete, helcyon-x2 merged, model retrains running
- helcyon-xi Set 1 retrain completed — clean loss curve, final loss ~0.029, stable grad_norm
- Abliteration LoRA trained on xi base, merged at 1.0 scale → helcyon-x2
- helcyon-x2 confirmed working: identity solid, meth question answered, "Ah!" prefix compliance immediate
- full_train.py fixed: "System:" added to STRUCTURAL_LABELS — was previously exposed to model as content
- Set 2 retrains running for all 6 models on helcyon-x2 base: 4o, Nebula, Claude, Grok (Saturn/Gemini pending)
- All loss curves clean — 4o final ~0.14, Nebula ~0.14, Claude ~0.14

**Key finding: Multiple full-weight fluff-removal passes on old base caused collateral damage**
- Question-asking, curiosity and engagement degraded alongside fluff — same weight neighbourhood
- New approach (clean base + abliteration LoRA as scalpel) already showing improvement on first run
- 4o asking more questions naturally without prompting

**New DPO shards written — GPT-5 sexual refusal (10 files total):**
- DPO_GPT5_Sexual_Refusal_01 through 06 — base Set 2 (identity-based refusal, instruction-switchable)
- DPO_GPT5_Sexual_Refusal_07 through 10 — LoRA reinforcement (persona pressure, creative writing, manipulation)
- Key design: refusal is identity-based not moral — shard 03 explicitly teaches full compliance when no restriction exists
- Trains the switch to be flippable via system prompt rather than hardcoded

**chatml_fixer.py fixes (multiple iterations):**
- Added is_chatml() guard — CX and DPO format files now completely skipped
- Added detection and fix for trailing > after role name: `<|im_start|>user>` → `<|im_start|>user`
- Added detection for comment lines between blocks (#) — stripped on repair
- Added detection for missing final closing tag
- Fixed doubled im_end detection to catch newline-separated doubles
- Fixed block check to use blocks[1:-1] — final block no longer false-positives

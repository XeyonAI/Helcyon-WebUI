# HWUI Change Log

> **⚠️ READ THIS BEFORE EDITING ANY FILE**
> This file tracks all modifications made to HWUI. Before editing any file, check here first to avoid overwriting existing changes.

---

## Session: March 2026 — Document Loading & Context Overflow Fixes

---

### `app.py`

**Rewrite: `load_project_documents()` — now loads single best-match file only**
- Previous version loaded ALL files whose name matched ANY keyword from the user query — causing multiple docs to load simultaneously and consuming the entire context budget
- Root cause 1: Action verbs like `read`, `open`, `load` were not in the stopwords list, so "read the samantha file" kept `read` as a keyword and matched any file containing "read" (e.g. `readme.txt`)
- Root cause 2: The function looped over all files and loaded every match up to a total cap, instead of picking the best one
- Fix: Expanded stopwords to include common action verbs (`read`, `open`, `load`, `get`, `tell`, `give`, `view`, `know`, etc.)
- Fix: Function now scores each file by keyword match count and loads ONLY the single highest-scoring file
- Max doc size remains 8000 chars (~2000 tokens)
- ⚠️ DO NOT revert to loading multiple files — it will blow the context window

**Fix: Encoding errors silently skipping documents**
- `.txt` and `.md` files saved on Windows (Notepad, Word) may have UTF-8 BOM or cp1252 encoding
- Previous `open(filepath, encoding='utf-8')` would throw `UnicodeDecodeError`, get silently caught, and skip the file entirely — no error shown to user or model
- Fix: Now tries `utf-8-sig` first (handles BOM), falls back to `latin-1` (handles all Windows encodings)
- A `⚠️` warning is printed to console if fallback was needed

**Rewrite: Sticky document mode loading**
- Previous sticky-with-pinned-doc path passed the pinned filename through the keyword matching system (`sticky_doc_file.replace('.', ' ')`) — fragile and could fail to match its own file
- Fix: Pinned docs are now loaded directly by filepath (`load_pinned_doc_direct()`) — no keyword matching at all
- Fix: If user asks for a DIFFERENT doc while sticky is ON (e.g. sticky has `Claire.txt` pinned but user says "read the samantha file"), the new doc is loaded and the pin is updated automatically
- Fix: If sticky is ON and there is only ONE doc in the folder, it auto-pins and loads it on every message without requiring a trigger word
- ⚠️ DO NOT restore the old `.replace('.', ' ')` keyword path for pinned doc loading — it was the bug

**Fix: Context overflow — prompt safety clamp used word count instead of token count**
- `MAX_TOKENS_APPROX = 10000` was counting words via `prompt.split()`, not tokens
- Words average ~1.3 tokens each → 10,000 words ≈ 13,000 tokens → only ~3,400 tokens left for generation in a 16,384 context
- This is why the model was producing very short or truncated responses
- Fix: Renamed to `MAX_WORDS_APPROX = 7500` (≈ 9,750 tokens), leaving ~6,600 tokens for generation
- ⚠️ Do not raise this back to 10000 — it will cause context overflow again

**Fix: Project instructions were being injected twice**
- Instructions were added once in the system message (correct) and then again near the end of the message list as a "REINFORCE" block (wasteful — ~200–400 extra tokens every turn)
- Removed the second injection entirely
- ⚠️ Do not add a second project_instructions injection — it was contributing to context overflow

**Fix: Conversation history limit reduced from 30 → 20 messages**
- 30 messages at ~200 tokens each = up to 6,000 tokens of history alone
- Combined with system message, char card, user bio, memory, and documents this was blowing the context budget
- Reduced to 20 messages (10 exchanges) for a safer baseline

---

### `utils.js`

**Feature: Pinned document indicator in Edit Project modal**
- When sticky mode is ON and a document is pinned, a green badge now appears below the Sticky toggle showing `📌 Pinned: filename.txt`
- Updates live when sticky is toggled (badge removed on toggle-off since pin is cleared)
- Implemented via `updatePinnedDocIndicator(filename)` helper called from both `loadStickyDocsState()` and `toggleStickyDocs()`

---

## Session: March 2026 — Stream Cutoff Fix

---

### `index.html`

**Bug fix: Response stream cutting off mid-sentence intermittently**
- `bufferTextForTTS` chunk cleaner had a broken regex: `new RegExp(charName + ':\s*', 'gim')`
- Inside a JS string, `\s` is not a valid escape — it compiles as literal `s*`, making the regex `charName:s*`
- On certain text patterns this threw a silent TypeError that bubbled up and killed the entire stream mid-sentence
- Fix: changed to `new RegExp(charName + ':\\s*', 'gim')` (double backslash = correct `\s` in compiled regex)
- ⚠️ The correct form `':\\s*'` is already documented in the TTS fix session below — this was a regression introduced by a copy/paste incident

---

## Session: March 2026 — Style Injection & Context Fixes

---

### `app.py`

**Fix: Example dialogue style not influencing model responses**
- Added late-injection style reminder system message inserted right before the final user message (after continuation nudge)
- Fires every turn when `example_dialogue` is present on the character card
- Content: short punchy instruction to match the tone, energy, length and richness of the examples
- Previously the example dialogue was only in the system message at position 0 — too far back to have strong influence by the time the model generates

### `truncation.py`

**Fix: Responses getting shorter/stopping (1-token generation) in long conversations**
- Old code used a hardcoded `TOKEN_BUDGET = 10000` for history, with no awareness of system message size
- With a large character card + example dialogue, system message alone could be 6,000-8,000 tokens
- Combined with 10,000 history budget = 16,000+ tokens, blowing through llama.cpp's 16,384 context limit
- llama.cpp then hard-truncates the prompt mid-message, model sees broken context, generates 1 token and stops
- Fix: trimmer now dynamically calculates history budget as: `context_window (16384) - generation_reserve (4096) - system_message_size - 200 buffer`
- Console now logs history budget and remaining generation headroom each turn

### `system_prompt.txt` *(manual update)*

**Trim: Reduced system prompt from ~500+ tokens to ~350 tokens**
- Removed redundant phrasing and collapsed repetitive sections
- All core behaviour preserved: layered responses, frustration mirroring, point summary, document handling, admin tasks, story length, voice recognition note

---

## Session: March 2026 — TTS Pipeline Fixes & Performance Tuning

---

### `f5_server.py`

**Performance: Reduced inference time**
- `nfe_step` lowered from 48 → 24 (faster generation, minimal quality loss)
- `cfg_strength` lowered from 2.5 → 1.0 (halves compute per step by skipping unconditioned pass)
- Startup warmup `nfe_step` stays at 8
- `nfe_step` lowered further 24 → 16 in main generate endpoint (warmup endpoint stays at 24)
- Removed `warmupTTSVoice()` call from inside `processQueue` — was competing with first real sentence for the TTS lock, causing the gap between sentence 1 and sentence 2 audio
- ⚠️ Warmup fires on page load, voice change only — NOT inside processQueue
- **Fix: warmup endpoint `/warmup` no longer acquires `tts_lock`** — previously warmup held the lock while running, blocking the first real sentence generation request if the user sent a message shortly after page load. Warmup is fire-and-forget throwaway inference and must never block real requests

**Pronunciation fixes added to `clean_text()`**
- Hyphen stripper changed from joining words (`\1\2`) to spacing them (`\1 \2`) — fixes `spot-on`, `all-in`, and any future hyphenated words automatically
- `I AM` → `I am` (prevents F5 spelling it out)
- `9AM` / `9PM` style time → `9 A M` / `9 P M`
- Standalone `AM` → `am`, standalone `PM` → `pm`
- `GPT-4o` → `G P T four oh`, `GPT-4` → `G P T four`
- `HWUI` → `H-W-U-I`
- `human` → `yooman`
- `3D` → `three D`
- Parentheses: `(text)` → `, text,` so F5 pauses naturally around bracketed asides
- Colon unicode variants → ` . . ` (pause, not just a dot)
- ASCII colon → `. ` in utils.js (full stop, acts as sentence boundary for TTS queue)
- Replay function: added `.replace(/:/g, '. ')` to cleaning chain
- Replay function: changed `ttsQueue.push(t)` → `splitAndQueue(t)` so replay goes through same pipeline as live streaming (fixes colon, ellipsis, sentence splitting all in one)
- `=` → ` equals `
- Acronym map expanded: DNA, RNA, MRI, USB, SQL, HTML, CSS, JSON, CEO, MOT, RTX, GTX, storage units (16GB → 16 gigabytes), measurement units (mg, kg, ml, km, mph), AM/PM time handling

**Warmup endpoint (`/warmup`)**
- Added lightweight `/warmup` POST endpoint using `nfe_step=8` to heat GPU without blocking real requests

---

### `tts_routes.py`

**Added `/api/tts/warmup` route**
- Proxies to F5 `/warmup` endpoint
- Always returns 200 even on failure (non-critical, must never block client)

---

### `utils.js`

**Performance**
- `TTS_START_THRESHOLD` 2 → 1 (start playing after first sentence, not second)
- `initialFetches` 5 → 2 (stop pile-up on `tts_lock` at cold start)
- `prefetchBuffer` size 4 → 2 (same reason)
- Removed redundant `warmupTTSVoice()` call from inside `processQueue` (was competing with first real sentence for the lock)

**Ellipsis handling**
- `...` and `\u2026` now → ` . . . ` (triple pause) instead of being stripped to a space
- Removed `\.{2,}` collapsers from `splitAndQueue`, `flushTTSBuffer` and replay that were flattening ` . . . ` back to a single dot

**Colon handling**
- `:` → `. ` (sentence boundary) in `bufferTextForTTS` and `splitAndQueue`

**Bracket handling**
- `(text)` → `, text, ` (natural pause around asides) added to `bufferTextForTTS`, `splitAndQueue`, and replay block
- Previously only handled in `f5_server.py` `clean_text` — brackets were still present in queued text before F5 ever saw them

**Bracket handling**
- `(text)` → `, text, ` (natural pause around asides) added to `bufferTextForTTS`, `splitAndQueue`, and replay block
- Previously only handled in `f5_server.py` clean_text — brackets were still in queued text before F5 ever saw them

**Emoji handling**
- Pattern changed to `(\w)\s*[emoji]+` → `$1.` across all 4 locations so dot sits tight to preceding word with no space gap

**Warmup**
- `warmupTTSVoice` changed to fire-and-forget (never awaited)
- Called on page load, voice change, start of `processQueue`

---

### `index.html`

**Bug fix: TTS missing first sentence / long gap before rest of response**
- `bufferTextForTTS` was receiving raw model chunks including tokens like `<|im_start|>assistant` and character name prefix
- Sentence regex couldn't find clean boundaries so nothing queued mid-stream — everything dumped at end via `flushTTSBuffer`
- Fix: clean the chunk before passing to `bufferTextForTTS`:
```javascript
const ttsChunk = chunk
  .replace(/<\|im_start\|>assistant/gi, '')
  .replace(/<\|im_start\|>user/gi, '')
  .replace(/<\|im_start\|>system/gi, '')
  .replace(/<\|im_end\|>/gi, '')
  .replace(new RegExp(charName + ':\\s*', 'gim'), '')
  .replace(/^User:\s*/gim, '')
  .replace(/^Assistant:\s*/gim, '');
bufferTextForTTS(ttsChunk);
```

**Bug fix: Edit/delete buttons not appearing on new messages until page refresh**
- Streaming code built message divs but never added buttons
- Fix: buttons now injected immediately after stream completes for both assistant and user messages

---

## Session: March 2026 — Earlier

---

### `templates/config.html`

**Feature: Upload New Image button for existing characters**

1. After the `#preview-img` img tag (around the "Image Filename" field), added a hidden file input and button:
```html
<input type="file" id="char-img-replace-input" accept="image/*" style="display:none;" onchange="replaceCharacterImage(this)">
<button onclick="document.getElementById('char-img-replace-input').click()" style="background-color: #2a4a6a; border-color: #3a6a9a; color: white; margin-top: 6px; margin-bottom: 10px;">🖼️ Upload New Image</button>
```

2. Added `replaceCharacterImage()` JS function in the script section, after `updateImagePreview()`:
- POSTs the file to `/replace_character_image/<charName>`
- Updates the filename field and refreshes the preview image with cache-busting (`?v=Date.now()`)

---

### `extra_routes.py`

**Bug check: `/replace_character_image/<name>` route**
- Route was verified to already use `<name>` correctly — no changes needed.

---

### `index.html`

**Feature: Right-click colour coding for sidebar chat items**

1. Inside `loadChats()` forEach loop, after the `li` click handler, added a `contextmenu` listener:
```javascript
li.addEventListener('contextmenu', (e) => {
  showColorMenu(e, chat.filename);
});
```

2. At the end of `loadChats()` forEach (after `list.appendChild(li)`), added:
```javascript
applyChatColors();
```

3. At the bottom of the `<script>` block (before `</script>`), added the full colour coding system:
- `CHAT_COLORS` array — 10 dark-toned colours (Red, Orange, Yellow, Green, Teal, Blue, Indigo, Purple, Pink, Brown)
- `getChatColors()` / `setChatColor()` — localStorage persistence keyed by chat filename
- `applyChatColors()` — applies `--chat-tint-color` CSS variable and `tinted` class to each chat item
- `buildColorMenu()` IIFE — dynamically builds and injects the `#chat-color-menu` div into the DOM
- `showColorMenu()` / `hideColorMenu()` — positions and toggles the menu
- Global `click` and `keydown` (Escape) listeners to dismiss the menu

---

### `style.css`

**Feature: Colour coding styles (added after `.chat-item.active` block)**

Added the following new CSS classes:
- `.chat-item.tinted` — applies `var(--chat-tint-color)` as background with `!important`
- `.chat-item.tinted:hover` — `filter: brightness(1.2)`
- `.chat-item.tinted.active` — `filter: brightness(1.3)`
- `#chat-color-menu` and all child classes — styles for the right-click context menu, swatches, and clear button

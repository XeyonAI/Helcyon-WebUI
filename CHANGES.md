## Session: May 10 2026 — First-Person Voice Contagion Fix (sandwich injection)

### `app.py`
**Fix: Model kept sliding into first person when retelling `[PERSPECTIVE: first_person_account]` documents**
- Symptom: inconsistent — sometimes correct second person, sometimes first (mirroring the document's "I"), occasionally third
- Root cause: voice contagion. Pre-content framing instructions get diluted by attention to recent first-person prose. By the time the model starts generating, the most recent context is the document's "I" voice, not the instruction. System-prompt instructions sit even further upstream and are weaker still
- Fix: **sandwich injection** — `_extract_perspective()` now returns `(prefix, suffix, content)` instead of `(framing, content)`. The prefix still wraps the document; the suffix is a new voice reminder placed AFTER the document content but BEFORE the closing `END PROJECT DOCUMENTS` marker
- Since `project_documents` is the last block in `system_text`, the suffix is one of the very last pieces of context the model sees before generating — it cannot be drowned out by the voice it just absorbed
- Suffix is populated ONLY for `first_person_account` (the only perspective with voice-contagion risk). `third_person_account` and `reference` get an empty suffix — no change to their injection
- Suffix content uses imperative voice + explicit positive AND negative examples ("→ You parked outside the gym — never 'I parked...', never 'The user parked...'") because examples are stronger steering than abstract rules
- Updated all three call sites: `load_project_documents`, `load_global_documents`, `load_pinned_doc_direct`
- ⚠️ If voice contagion still occurs on very long first-person documents (>4-6 k tokens), next escalation would be actual content transformation (programmatic "I" → "you" rewrite) — but that's brittle and was deliberately not done yet

---

## Session: May 10 2026 — Document Perspective Tags

### `app.py`
**Feature: Optional `[PERSPECTIVE: ...]` metadata tags in document files**
- New tag on the first non-empty line of any document file controls the framing header injected before its content
- Three values: `first_person_account` → "The following was written by the user about their own experience, in their own words:"; `third_person_account` → "The following is the user's written account about someone else:"; `reference` → "The following is reference material:"
- Tag is case-insensitive on the value side (`First_Person_Account` etc. all work)
- Tag line is stripped before injection — the model never sees the raw tag syntax
- No tag present → no framing header added, content injected exactly as before (zero regression for existing docs)
- Applied at the injection point in all three load paths: `load_project_documents`, `load_global_documents`, `load_pinned_doc_direct`
- Helper: `_PERSPECTIVE_RE` (module-level compiled regex) + `_extract_perspective(content)` (returns `(framing, stripped_content)`)

---

## Session: May 10 2026 — Document Injection Overhaul

### `app.py`
**Bug fix: `load_pinned_doc_direct` silently returned empty for any non-text pinned doc**
- Old implementation: `open(fpath, 'r', encoding='utf-8')` — only handled plain text. Any PDF or DOCX saved as the sticky-pinned document returned `""` with no useful error. The sticky-load path then printed "pinned doc missing, clearing pin" and nuked the saved pin
- Fix: replaced with `_read_doc_content(fpath, max_chars=8000)` which handles all supported formats (txt/md/docx/odt/pdf) consistently with the rest of the document pipeline

**Refactor: Extracted shared `_read_doc_content`, `_doc_query_keywords`, `_score_doc` helpers**
- `load_project_documents`, `load_global_documents`, and `load_pinned_doc_direct` each had their own copy of the file-reading switch (`if fname.endswith('.txt')… elif .docx… elif .pdf…`) — three copies, subtle differences (e.g. global used `page.extract_text() or ''` but project did not; latin-1 fallback was in project but not global)
- Consolidated into `_read_doc_content(filepath, max_chars=None)` — single source of truth for all supported formats
- `_doc_query_keywords(user_query)` extracts meaningful content keywords from any query (stopword filter + length filter + punctuation strip)
- `_score_doc(fname, filepath, query_keywords)` computes a combined relevance score using word-boundary regex — no more `'doc' in 'docker'` false hits

**Feature: Content-aware document matching (filename 3× + content preview 1×)**
- Old matching: score = number of query keywords found as substrings of the filename. "What does the agreement say about payment terms?" scores 0 against `employment_contract.pdf` because neither "agreement" nor "payment" is in the filename
- New matching: `_score_doc` checks filename with `\b`-bounded regex (3 pts per hit) AND reads the first 1 000 chars of text files (1 pt per hit). The combined score picks the most relevant document even when the filename is generic
- Content preview only runs for `.txt`/`.md` files — avoids heavy PDF/DOCX parsing on every request just for scoring; binary files fall back to filename-only matching (which is usually informative for PDFs anyway)
- Uses word-boundary regex for all matching — `\b` anchors ensure "contract" doesn't hit "contractor", "employment" doesn't hit "unemployment"

**Fix: Document trigger detection replaced with specific intent phrases + word-boundary noun matching**
- Old `document_triggers` list included `'doc'` (substring — matched "docker", "doctrine", "documentary"), `'file'` (matched "profile", "filesystem"), `'show me'` (fired on any demo request), `'search'` (fired on any search intent), `'timeline'`/`'journal'`/`'diary'` (fired on any mention of those concepts regardless of context), `'look up'` (also a web-search trigger — both fired simultaneously on "look up the weather")
- New system has two gates:
  - `_DOC_STRONG_TRIGGERS` — specific multi-word intent phrases that are unambiguously about documents: "according to", "from the document", "in the file", "what does it say", "scan the/my", "reference the", "open/read the document/file/pdf", etc.
  - `_DOC_NOUN_RE` — `\b(document|documents|pdf|attachment|attachments)\b` with word boundary — catches bare "pdf" / "document" requests without hitting unrelated words
- `user_requesting_different_doc` (sticky override check) also updated to use `_DOC_STRONG_TRIGGERS` + `_DOC_NOUN_RE` and to use word-boundary matching when comparing the query against the pinned filename
- ⚠️ "file" and "doc" (standalone) no longer trigger document loading — require context ("the file", "a document", explicit noun form). If a user says just "file" and no doc loads, they need to say "the file" or "document" instead

---

## Session: May 10 2026 — Memory File Gap Accumulation Fix

### `app.py`
**Bug fix: Memory entries growing gaps on each edit**
- `edit_character_memory` split the file on `# Memory:` and rejoined blocks with `"\n\n"`, but blocks were not stripped first — each block retained its trailing `\n\n` from the file, and the join added another `\n\n` on top. Every edit cycle added 2 more newlines between entries (2 → 4 → 6 → 8 blanks, compounding indefinitely)
- Fix: changed join to `"\n\n".join(f"# Memory: {b.strip()}" for b in blocks)` — blocks are now stripped before joining so the separator is always exactly `\n\n` regardless of how many prior edits have occurred
- Also removed the redundant `new_text.strip()` on the write (was a no-op after the per-block strip; leaving it would have masked the bug again on any future refactor)

**Bug fix: `add_character_memory` could concatenate new entries directly onto previous body text**
- When `edit_character_memory` or `delete_character_memory` rewrote the file it left no trailing newline (via `new_text.strip()`). A subsequent `add_character_memory` appended its entry with no leading separator, fusing the new `# Memory:` header onto the end of the previous body text
- Fix: `add_character_memory` now checks whether the file is non-empty (`os.path.getsize`) and writes `\n\n` before the entry only when needed; the entry itself no longer carries a trailing `\n\n` — consistent with how edit/delete leave the file

---

## Session: May 10 2026 — Prompt Injection Audit Fixes (#2 #3 #4 #5)

### `app.py`
**Bug fix: `_is_new_chat` no longer false-triggers on curt assistant replies (#4)**
- Old logic: treat chat as "new" if the only assistant message is ≤30 words
- Side effect: any brief reply ("Yeah, fair." / "Mm." / "Got it.") reset the chat to new-chat state, silently re-injecting the full session summary block on every subsequent turn
- Fix: `_is_opening_line_msg()` now checks only the `is_opening_line` flag — word-count branch removed entirely
- ⚠️ DO NOT re-add word-count check — opening lines are always flagged explicitly by the frontend

**Bug fix: Stale search blocks now stripped from prior user turns in both chat-search rebuild paths (#2)**
- Only `WEB SEARCH RESULTS` was stripped (web-search rebuild path, May 9 fix)
- Both chat-search rebuild paths (trigger-based ~line 2949, model-tag-based ~line 3484) were rebuilding from raw `messages` — accumulated `[CHAT HISTORY RESULTS …]` blocks from prior turns re-fed the model every turn
- Both paths now strip `WEB SEARCH RESULTS` AND `CHAT HISTORY RESULTS` from all prior user turns before rebuild

**Bug fix: Web-search rebuild path now also strips CHAT HISTORY RESULTS (#3)**
- Existing strip at ~line 3271 only cleaned `WEB SEARCH RESULTS`
- Now strips both block types in the same pass — consistent with both chat-search paths above

**Bug fix: Session summary transcript strips search blocks before summariser sees them (#5)**
- `_build_transcript()` was passing raw message content to the summariser
- Any turn containing search blocks had that content baked into the saved memory file permanently
- Both `WEB SEARCH RESULTS` and `CHAT HISTORY RESULTS` now stripped from each message before transcript label is appended

---

## Session: May 10 2026 — F5-TTS First-Byte Latency + Colon Consistency Fixes

### `f5_server.py` + `utils.js`
**Tweak: First chunk of each response uses nfe_step=20, subsequent chunks use 24**
- F5 has to complete full inference on chunk 1 before any audio plays — at nfe_step=24 this is ~5-6s on RTX 5060 Ti
- First sentence uses nfe_step=20 (saves ~4 diffusion steps, ~1-1.5s off the initial wait)
- All subsequent chunks remain at nfe_step=24 — full quality on the body of the response
- Quality difference on a single opening sentence at nfe_step=20 vs 24 is barely perceptible
- JS sends `first_chunk: true` in the POST body only for the very first `fetchAudio` call of each `processQueue` run; all refill fetches default to `false`
- F5 server reads `data.get('first_chunk', False)` and selects nfe accordingly; Flask console now logs `nfe_step: 20  [first chunk]` vs `nfe_step: 24` so you can see it working
- ⚠️ If first sentence sounds noticeably worse on a specific voice, try nfe_step=22 as a middle ground



### `f5_server.py`
**Tweak: Removed `. ` prefix pad from `clean_text()` return value**
- `clean_text()` was returning `". " + text` to prevent F5 clipping the first word
- `trim_leading_silence()` already handles this with an 80ms clean buffer prepended after trim
- The prefix was adding a silent pad that F5 had to generate before any real audio — pure overhead on every chunk request
- Removed entirely; trim buffer is the correct and sufficient mechanism
- ⚠️ If first-word clipping returns on a specific voice, the fix is adjusting `buffer_ms` in `trim_leading_silence`, not re-adding the prefix

### `utils.js`
**Bug fix: Colon→period conversion in `splitAndQueue` removed**
- CC's previous session removed `:` → `. ` from `bufferTextForTTS`'s sentence detector so colons no longer force chunk splits
- `splitAndQueue` had its own identical conversion on line 566 that was missed — colons were still being converted to `. ` inside every chunk before it was queued, breaking prosody mid-sentence regardless of the detector fix
- Removed from `splitAndQueue` to match intended behaviour: F5 server's `clean_text()` handles colon conversion server-side with full sentence context

**Bug fix: Colon→period conversion in `replayLastAudio` removed**
- Same stale conversion existed in the replay path (line 1106), making replay inconsistent with live TTS playback
- Removed to keep both paths identical

**Bug fix: Sentence detector now treats trailing emoji as a sentence boundary**
- `bufferTextForTTS` sentence regex was `/[^.!?]+[.!?]+.../` — only `.!?` counted as terminators
- When the model ends a sentence with an emoji instead of punctuation (e.g. "Nice one 😊"), the sentence never matched the regex, stayed in the buffer, and got merged with the next sentence into a single long chunk
- F5 server correctly converts the emoji to `.` server-side, but the chunking decision had already been made wrong — F5 received a run-on chunk with no prosody break, so tone never dropped at the sentence end
- Fix: regex now also matches one or more trailing emoji characters as a valid sentence terminator, using the same Unicode blocks already covered by the emoji strip passes
- Lone-emoji or emoji-only lines without preceding text still fall through to the strip pass as before

**Tweak: Initial prefetch reduced from 2 to 1 in `processQueue`**
- `processQueue` was fetching 2 sentences simultaneously before playing anything, meaning the user always waited for at least 2 full F5 inference cycles before hearing audio
- Changed to fetch 1 sentence first — play it as soon as ready, with sentence 2 generating in parallel during playback
- Net effect: first audio starts ~1 full F5 inference cycle sooner (typically 1-2.5s on RTX 5060 Ti at nfe_step=24)
- Prefetch buffer still ramps up to 3 during playback so subsequent sentences stay gapless

---

## Session: May 10 2026 — F5-TTS Audit + First-Cluster Quality/Reliability Fixes

Full F5-TTS audit across `f5_server.py`, `tts_routes.py`, `utils/utils.js`, `templates/mobile.html`, plus the F5 source (`api.py`, `utils_infer.py`, `cfm.py`). Acted on four highest-ROI items below; remaining audit items handed to Sonnet (its follow-up entry is the May 10 First-Byte Latency section above).

### `f5_server.py`
**Quality: `cfg_strength` 1.0 → 2.0 (F5 default — voice fidelity)**
- Old setting halved classifier-free guidance — model wasn't being steered toward the reference voice as strongly as F5 was trained for. Voice drift across sentences was a likely consequence.
- Verified F5's CFM short-circuits the unconditional pass only when `cfg_strength < 1e-5` (`I:\F5-TTS\F5-TTS\src\f5_tts\model\cfm.py:167`) — bumping 1.0 → 2.0 has **no inference-time cost**. Pure quality win.
- Original 1.0 was never documented in CHANGES.md as a deliberate setting — likely a copy-paste from initial F5 setup, never revisited.
- ⚠️ Stronger guidance also amplifies anything in the ref clip itself — if a specific voice's reference has hum/breath noise, those may now be more audible. Per-voice cfg override is a future option if any voice needs it.

**Speed: Eliminated tempfile round-trip on every TTS request**
- Old path: `tts.infer(file_wave=tmp)` writes WAV → `trim_leading_silence(tmp)` reads + writes WAV → `send_file(tmp)` reads WAV. Three disk hops per ~50KB audio file. Plus the tempfile was never cleaned up — `%TEMP%` accumulated one stray WAV per generated sentence indefinitely.
- New path: `tts.infer` keeps audio as numpy → `trim_leading_silence(audio, sr)` runs in memory, returns modified array → `sf.write(BytesIO, audio, sr, format='WAV')` → `send_file(buf)`. Saves ~30-100ms per request and stops the tempfile leak.
- `trim_leading_silence` signature changed from `(path, ...)` (mutates file) to `(audio, sr, ...)` (returns array). Only one call site in the route, updated in lockstep.
- ⚠️ Warmup paths (`warmup()` at line 44 and `/warmup` endpoint at `:341`) still use tempfiles and unlink them — left untouched since they're rare and self-cleaning.

### `utils/utils.js` + `templates/mobile.html`
**Quality: Stop treating `:` and `;` as sentence terminators in `bufferTextForTTS`**
- Old `bufferTextForTTS` did `chunk.replace(/:/g, '. ')` then ran a sentence-detector regex `/[^.!?:;]+[.!?:;]+/g` that split on either. So a clause like "Look, here's the thing: it works" was sliced into two separate F5 inferences with no shared prosodic context — produces the rushed/choppy intonation that sometimes shows up mid-response.
- Fix: removed the JS-side `:` → `. ` replacement; sentence-detector regex now `/[^.!?]+[.!?]+/g` only. Colons survive into the chunk and the F5 server's `clean_text` still converts them to `. ` server-side, but at that point they're embedded in a longer chunk with surrounding context — F5 produces correct mid-clause prosody.
- Mobile.html mirrors desktop and got the same fix in lockstep.
- (Sonnet's later entry above also stripped the same conversion from `splitAndQueue` and `replayLastAudio` — those weren't in this commit's scope, but were on the audit list for follow-up.)

**Reliability: `fetchAudio` retries once on transient failure**
- Old behaviour: `if (!response.ok) return null` — one failed F5 request silently dropped the sentence, no retry, no log. Brief lock contention or CUDA hiccup = missing sentence with no diagnostic.
- New behaviour: 2-attempt loop with 200ms backoff between attempts. `console.warn` on each failed attempt with status code + sentence preview; `console.error` only after both attempts fail. Same null-return contract preserved so the prefetch loop logic is unchanged.

### Audit findings still open after this session + Sonnet's follow-up
- `nfe_step` exposed as settings.json toggle (Sonnet added first-chunk staging at 20, but full configurability is still TBD; F5 default is 32 for max quality)
- Bump `TTS_MAX_CHUNK_LENGTH` from 300 → 500-600 (F5 internally re-chunks via ref-audio duration, so JS pre-chunking aggressively just throws away prosodic context)
- `TTS_START_THRESHOLD = 1` → 2, or coalesce-very-short-first-sentence
- Restore `!` in `clean_text` (currently downgraded to `.`, kills emphasis)
- Centralize URL/HTML strip pipeline — currently duplicated 4 times across `bufferTextForTTS` / `splitAndQueue` / `flushTTSBuffer` / `replayLastAudio` with subtly different regexes
- Remove dead acronym entries `r"\bIS\b": "is"` / `r"\bIT\b": "it"` (lines 107-108 of `f5_server.py`) — the all-caps title-caser at line 218 runs first and turns `IS` → `Is`, which the lookup no longer matches
- Output loudness normalization (peak/LUFS) — voices have inconsistent reference-clip loudness, output inherits it
- Streaming inference via `infer_batch_process(streaming=True)` for first-byte latency
- Real batching across prefetched sentences (currently `tts_lock` serializes everything; prefetch only hides round-trip latency)
- Per-character voice warmup on character switch (current code warms on `setTTSVoice` but not when localStorage `tts-voice-${charName}` auto-loads)

---

## Session: May 10 2026 — Prompt-Injection Audit (Findings Only — Fixes Handed to Sonnet)

Survey of how 14 layers of context get stacked into the system block + final user turn before each inference (character system prompt, example dialogue, tone primer, instruction layer, session summary, per-block memories, chat-history search, web search, document context, author's note, current situation, opening lines, restriction anchor, project context). Audit only — no code changed in this session. Fixes being applied separately by Sonnet.

### High severity
- **Vision / OpenAI-cloud / Jinja model paths bypass half the injection stack.** `app.py:2657` (vision), `:2737` (cloud), `:2795` (jinja) all build their prompt from `system_text + memory` *before* the chat route appends `ex_block` (example dialogue), restriction anchor, character note, and RP opener into `messages[0]`. Same character + same prompt produces materially different context across model backends.
- **`[CHAT HISTORY RESULTS …]` blocks never stripped from prior user turns.** Only `WEB SEARCH RESULTS` got the May 9 strip-from-history fix. Every chat-search hit accumulates in history and re-feeds itself on subsequent turns. Streamed-output suppressors (`app.py:2986`/`:3509`) only stop the model echoing live, not the input echo.
- **`WEB SEARCH RESULTS` strip is only in the web-search rebuild path.** Chat-search rebuild (`app.py:2949`) and model-tag chat-search rebuild (`app.py:3484`) don't strip prior search blocks. Three rebuild paths, only one cleans.
- **`_is_new_chat` re-fires session summary on every turn after a short reply.** `app.py:1965-1986` + `:2215`. The ≤30-word threshold means curt assistants ("Yeah, fair." / "Mm.") reset chat into "new chat" state and re-inject the entire session summary block on every turn. Should key off message count or an explicit flag.

### Medium severity
- Session-summary generator (`app.py:5566-5584`) doesn't strip search blocks from transcript before the summariser sees them — stale results can get baked into the saved memory file.
- Restriction anchor (`app.py:2384-2387`) uses substring matching: `"never"` matches `"nevertheless"`, `"under no"` matches `"under normal"`, `"absolute"` matches `"absolutely love it"`, `"don't"` matches inside genuine prose. Needs word-boundary regex.
- Restriction anchor can double-list itself if source prompt already contains an `ACTIVE OPERATOR RESTRICTIONS` block (bullets contain "never" → re-matched on next pass).
- Multi-line restrictions strand bullets — only the header line gets re-emitted in the anchor.
- Chat-search ↔ memory exclusivity is accidental (`app.py:2101` skips ALL per-block memory whenever chat-search fires). Already flagged in May 10 web-search-overhaul entry as suspect — drops unrelated bio memory blocks for no reason.
- Project-doc / system-prompt position diverges between chatml and jinja paths (`app.py:2030-2035`).

### Low severity
- Sticky doc keeps re-injecting until "different doc" keyword hits (`app.py:1949-1953`) — up to 8000 chars riding along on unrelated questions.
- Example-dialogue fallback chain runs twice (`:2237-2258` for sizing, `:2280-2305` for injection). Same logic, two copies, drift risk.
- `build_prompt` (`app.py:512`) is dead code despite the explicit warning. Delete.
- `/continue` route (`app.py:5190`) is a stub — all real continue work goes through `/chat` with `continue_prefix`. Misleading.
- Character note position asymmetric — inserted at `len(messages)-1` for normal flow but dropped when chat-search / web-search rebuilds happen.

### Verified clean
- `_CHAT_SEARCH_TRIGGER_RE` constant — early-skip and primary trigger genuinely share one source (no drift).
- Tone primer suppression — correctly keyed on `main_prompt` / `description` / `personality`, runs before primer would otherwise append.
- Memory keyword matching — `_kw_match` word-boundaries correctly (no `"art"` → `"starting"`).
- Web-search strip on the web-search rebuild path itself.
- Token-count fudge + dynamic `n_predict` cap — in lockstep.
- Stop-token sanity check (`app.py:2520-2544`).

### Audit gaps
- JS-side author's note flow not deeply traced — only server endpoints.
- `mobile.html` not audited for divergence from desktop pipeline (it sometimes uses `/chat`, sometimes builds locally).
- Project-switch flow sampled, not exhaustive.

---

## Session: May 10 2026 — Web Search Overhaul (Trigger Accuracy + Result Quality)

### `app.py`
**Tweak: Tightened web-search trigger to eliminate self-referential false-positives**
- Old trigger fired on bare `find out` / `look up` even when the user was describing their own intent ("I want to find out X", "let me look up Y") — the regex matched the phrase regardless of grammatical subject
- New trigger requires structure after the verb: `find out` only matches when followed by `about|what|who|when|where|why|how|if|whether` + a content word; `look up` only matches with a pronoun (`it|that|this|them|these|those`) OR a following object word
- Added a second-pass **self-reference filter**: if the trigger matched but is preceded within ~40 chars by `I'm/I am/I want to/I'll/I tried/let me/help me/let's/can I/should I/trying to/hoping to/going to/wanted to`, the trigger is suppressed and the model responds normally from context
- Trigger pattern now also covers `search the web/net/internet`, `google that|it|the`, `search online`, `give me the latest news on/about` — explicit imperatives that were missed before
- Logged as `💬 Self-referential phrase — suppressing search trigger` so false-positive suppression is visible in the Flask console
- Query-cleanup and topic-extraction regexes (used to strip the trigger phrase out of the search query itself) updated in lockstep so the cleaned query stays accurate when one of the new trigger forms is matched

**Feature: `do_brave_search` now uses richer Brave API params + multi-page fetch**
- Old call was `?q=…&count=5` with no other params; new call adds:
  - `count=10` (was 5) — twice the result breadth per query
  - `extra_snippets=1` — Brave returns 2-3 supplementary snippets per result; merged into the main snippet with `…` separator (up to 700 chars per result, was 300)
  - `summary=1` — pulls Brave's answer-style summary block into `out["summary"]` when available (was: top result's snippet only)
  - `safesearch=moderate` — explicit, prevents inappropriate noise
  - `freshness` — auto-detected from query keywords: `today/right now/breaking/just now/…` → `pd` (past day), `recent/recently/latest/this week/…` → `pw` (past week); skipped otherwise
- Brave's `infobox` (knowledge-panel for entities/places/people) now feeds into `out["summary"]` as a fallback when the summarizer is empty — frontier-style knowledge cards
- Brave's `news` vertical merged into `out["results"]` (top 3) for time-sensitive queries — particularly important when freshness is active
- HTTP errors now surface Brave's response body in the log (was: bare exception message) — much faster diagnosis when Brave returns 422/401

**Feature: New `_fetch_page_text` helper — proper main-content extraction, browser UA, parallel multi-page fetch**
- Old fetcher used `Mozilla/5.0 (compatible; HWUI/1.0)` UA — many sites 403 anything that isn't a real browser; replaced with full Chrome 121 UA string
- Old HTML strip was flat `<[^>]+>` with no script/style awareness — JavaScript source code, CSS rules, and JSON blobs leaked into `top_text`. New helper:
  - Strips `<script>`, `<style>`, `<noscript>` blocks WITH their contents (was: tags only, contents survived)
  - Strips `<nav>`, `<header>`, `<footer>`, `<aside>`, `<form>` blocks before main extraction (boilerplate noise)
  - Tries `<main>` then `<article>` for main-content extraction; falls back to `<body>` then full HTML
  - Decodes common HTML entities (`&nbsp;`, `&amp;`, `&lt;`, `&gt;`, `&quot;`, `&#39;`)
  - Handles gzip + deflate Content-Encoding properly (was: only gzip, only via try/except)
  - Detects charset from `Content-Type` header instead of hardcoding utf-8
- `do_brave_search` now fetches the **top 3** non-no-fetch result pages in parallel via `ThreadPoolExecutor(max_workers=3)` with 6s per-page timeout — was: single page sequentially
- Pages stored in new `out["pages"]` array as `[{url, title, text}, …]` — `format_search_results` renders all of them with their titles + URLs + content (~1500 chars each) so the model has 3-page synthesis material instead of one-page

**Tweak: Replaced single hardcoded `_JUNK_DOMAINS` set with split `_BLOCK_DOMAINS` + `_NO_FETCH_DOMAINS` (module-level)**
- Old `_JUNK_DOMAINS` blocked Reddit, YouTube, Twitter, X — but those are often the *most relevant* sources for "how do I…" / "what do people think about…" queries; frontier search engines surface them. Hard-blocking them at the search level was throwing away useful citations
- New split:
  - `_BLOCK_DOMAINS` (never cite, never fetch): `pinterest.com, quora.com, knowyourmeme.com, instagram.com, tiktok.com, facebook.com, tumblr.com, 9gag.com, ifunny.co` — login-walled, image-only, or SEO spam
  - `_NO_FETCH_DOMAINS` (cite snippets, but don't try to fetch the page): `youtube.com, youtu.be, twitter.com, x.com, imgur.com, giphy.com, tenor.com` — JS-rendered or login-walled, Brave's snippet is the only useful content
- The three duplicated copies of the old set (in `do_web_search`, `do_brave_search`, and inline in the chat route) all collapsed to module-level constants + helpers (`_is_blocked`, `_is_no_fetch`, `_domain_of`)

**Tweak: `format_search_results` rewritten to render multi-page output**
- New output structure: `Summary` line → `Top sources: [1]/[2]/[3]` blocks each with title, URL, and ~1500 chars of cleaned content → `Other relevant results` section with snippets + age + URLs (deduped against the fetched-page URLs)
- Snippet section now shows `(Published: …)` when Brave returned a freshness `age` field — gives the model temporal context for ranking results
- Old single-page layout retained as fallback when no pages were successfully fetched

**Tweak: Source links now show every fetched page, not just the top one**
- After the model finishes streaming, the chat route appends source link(s) below the response. Old behaviour: single `🔗 Source: <top_url>` link. New: one link per page in `res["pages"]` (typically 1-3) so the user can verify each fetched source independently
- Each link is `display:block` with `margin-top:2px` so they stack vertically — first one labelled `🔗 Source: <title>`, rest labelled `🔗 <title>`
- URL deduplication against the response body kept — if the model already mentioned a URL inline, that one is skipped

**Tweak: `has_results` now includes the `pages` array**
- `has_results = bool(res.get("summary") or res.get("top_text") or res.get("pages"))` — previously only checked `summary` and `top_text`, so a Brave response with only the per-page text array would have been mislabelled as no-results
- Added `pages_fetched=N` to the search-done log line for visibility

⚠️ **If the search trigger now misses something it used to catch**, the most likely cause is the new self-reference filter — check the Flask console for `💬 Self-referential phrase` and adjust either the trigger regex (~app.py:2921) or the self-ref regex (~app.py:2949) accordingly.
⚠️ **If a result page comes back empty when Brave found a URL**, check the Flask console for `⚠️ fetch HTTP …` or `⚠️ fetch error …` — many sites still 403 the Chrome UA (Cloudflare especially) or require JS.

### `app.py` — Self-reference filter is now CLAUSE-scoped, not message-scoped
**Bug fix: Earlier message context no longer suppresses later explicit search requests**
- Earlier today's filter checked the WHOLE message for an I-verb within ~40 chars of any trigger phrase. So "the web search wasn't working when **I tried** earlier, **search up** and find out when House of the Dragon Season 3 is due" got suppressed: `I tried` was within 40 chars of `search up` even though they live in different clauses
- Rewritten as a per-trigger scoped check:
  1. Find every trigger phrase position with `re.finditer`
  2. For each occurrence, walk back to the nearest clause boundary — `,` `.` `?` `!` `;` or one of `but`/`please`/`then`/`anyway`/`so`/`however`/`actually`
  3. Check ONLY that clause for an I-verb opener
  4. Within the clause, a `you` between the opener and the trigger means delegation (`I want YOU to search …`) → fire
  5. If ANY trigger occurrence has clean (non-self-ref) clause context, fire
- Net effect: narration earlier in a message no longer poisons a later explicit search request, because the two live in different clauses by punctuation or connector
- Validated against 21 cases — the user's reported failure case, plus delegation phrasings, plain triggers, multi-clause forms with `but`/`anyway`, and genuine self-narration. All 21 land where expected
- Logs `🔍 Search trigger fired on phrase: '<phrase>'` on fire and `💬 Self-referential context around all N trigger phrase(s)` on suppression — whichever line appears in the Flask console immediately tells you why a particular message did or didn't trigger

---

### `app.py` — Web-search self-reference filter over-suppressed delegation phrasings
**Bug fix: Self-ref filter now lets "I want YOU to search …" through**
- Earlier today's filter (added alongside the trigger tightening) suppressed any message where an I-verb (`I want / I need / I'd / I'll / I just …`) was followed by a search trigger phrase within ~40 chars. The intent was to catch *narration* like "I want to find out what time it is"
- Side effect that broke normal usage: phrasings like "I want **you** to search for X" / "I'd like **you** to look up Y" / "I need **you** to find out about Z" matched the same I-verb branches and got wrongly suppressed — these are users **delegating to the assistant**, not narrating their own intent
- User reported web search "not triggering at all" — this was the cause for any delegation-style phrasing
- Fix: the gap pattern between the I-verb and the trigger phrase now excludes `you` via a negative lookahead `(?:(?!\byou\b)[a-z'\s,]){0,40}?`. If `you` appears in the gap, the self-ref pattern fails to match → trigger fires normally
- Validated against 13 cases (6 delegation forms now fire, 6 genuine self-narration forms still suppress, 1 ambiguous "I want a search done" still suppresses — judged the safer default since there's no explicit delegation marker)

---

### `app.py` — `/generate_session_summary` 400 fix (prompt overflow) + better error reporting
**Bug fix: Summary handler now dynamically caps n_predict the same way the chat route does**
- User reported "Failed to generate summary: 400 Client Error: Bad Request for url: http://127.0.0.1:5000/completion" when clicking End Session
- Root cause: the chat route got dynamic n_predict capping on May 9 (the mid-word-cutoff fix), but `/generate_session_summary` was never patched. Hardcoded `n_predict=600` plus Helcyon's ~2k-token example dialogue + main_prompt + 30 transcript messages → `prompt + n_predict > ctx_size (12288)` → llama.cpp's `/completion` rejects with 400
- Same `truncation.rough_token_count(prompt) * 1.25` real-token estimate as the chat route, against `settings.json → llama_args.ctx_size` read live (so a settings change picks up on next request)
- If estimated real tokens exceed `ctx_size - 256` (the generation-reserve floor), the handler now trims the transcript from the head **two messages at a time** until the prompt fits or transcript shrinks to 4 messages — preserves the most recent / most-relevant exchanges
- `n_predict` then computed as `min(600, ctx_size - prompt_real_tokens)` with a floor of 256, so a tight context still gets *some* summary rather than failing outright
- Logs `🧠 summary: prompt ~N real / M ctx, n_predict=K` on every call so the next 400 (if it ever happens) is immediately diagnosable

**Bug fix: 4xx errors from llama.cpp now surface the actual response body**
- Old handler did `resp.raise_for_status()` and let the bare `HTTPError` propagate to the catch-all `except Exception`. That string is `"400 Client Error: Bad Request for url: …"` — tells you nothing about why llama.cpp rejected the request. The user got that exact message in the UI alert
- Replaced with explicit `if resp.status_code >= 400:` branch that reads `resp.text[:500]` and includes it in both the Flask console log and the JSON returned to the frontend
- Console log also dumps prompt length, ctx_size, n_predict, and message count on any 4xx so you can correlate with which conversation triggered it

⚠️ **If you change `ctx_size` in settings.json**, the summary handler now reads it live on every request — so it'll pick up the new value without restarting Flask. But llama-server itself still needs restarting for its actual KV cache to resize, so settings/server can drift. The chat route has the same dependency (per the May 9 fix).

---

### `app.py` + `utils/session_handler.py` — Session summary framing + INJECTED MEMORY instruction
**Tweak: Session-summary injection now framed as the model's own awareness, not a briefing**
- Old wrapper at `app.py:1903-1910` was `[Recent memories — <character_name>]` between `═══` dividers — reads like a label on a notes file. Effect: the model treated the contents as briefing material it had been *given*, not as its own memory, and never opened with natural callbacks the way a friend picks up where they left off (which Helcyon was specifically trained to do)
- New wrapper text:
  ```
  YOUR OWN MEMORY OF RECENT SESSIONS
  This is your own memory of last time — not a briefing, not notes someone
  handed you. You know this the way you know anything else about this person,
  because you lived through it. Mention it naturally, early — pick up the
  thread the way a friend would when they meet again. Do not recite it; do
  not say you were told or shown anything.
  ```
- Visual `═══` dividers retained so the section is still demarcated from the rest of the system block — the change is purely framing language, not file/format change. Session summary content itself is unchanged; `load_session_summary` still returns the same text from `session_summaries/<name>_summary.txt`

**Tweak: Added INJECTED MEMORY block to `get_instruction_layer()`**
- Inserted right above MEMORY TAGS — the two are conceptually paired (reading injected memory vs writing new memory tags)
- Block tells the model:
  - Treat content marked as own-memory or relevant-memories as own awareness, not a briefing
  - Bring it up naturally and early, the way a friend picks up where they left off
  - Never say "you were told", "briefed", "shown notes", or "reminded" — just know it
- Applies to both the session-summary block (post this commit) AND the per-block `Relevant memories:` injection from the chat-route memory pipeline. Both are forms of injected memory the model should silently know rather than describe being given
- ⚠️ Only effective for models trained to honor system-prompt-layer instructions — Helcyon and similarly-trained models will pick this up; an un-tuned model may still leak phrases like "I was told that…". This is the same model-training-gating principle that governs the search tags

⚠️ **Format files NOT changed**: `session_summaries/*.txt` files keep their existing `---SESSION---` divider format, and `memories/*.txt` files keep their `# Memory: …` block format. Only the wrapping text the model sees was tightened — pre-existing summary and memory files continue to work without migration.

---

### `app.py` — Memory pipeline cleanup (parsing, scoring, injection)
**Bug fix: Word-boundary matching replaces substring matching**
- Old scoring at `app.py:2061` was `if kw in user_input_lower:` — a plain substring check, so a keyword like `art` matched `starting`, `partial`, `smart`; `cat` matched `communication`; `garden` matched whatever, including the in-block keyword `gardening` (which double-counted: gardening was its own keyword AND triggered "garden" via substring → +6 instead of +3 for one match)
- New `_kw_match(kw, text_lower)` helper uses `re.search(r'\b' + re.escape(kw) + r'\b', text_lower)` — word-boundary anchored, regex-metacharacter-safe (so keywords containing `.`/`-`/etc. don't crash the regex). Possessives still match (`Kevin's` matches keyword `kevin` because the apostrophe is a non-word char so `\b` fires)
- Validated against 9 boundary cases: `garden` no longer hits `gardening`, `split` no longer hits `splitting`, `cat` no longer hits `communication`, `art` no longer hits `starting`. Real possessive/genuine matches all still fire

**Bug fix: Memory blocks no longer leak title and keywords line into the injection**
- Old parser at `app.py:2041` did `re.split(r"(?m)^# Memory:", text)` which dropped the `# Memory:` prefix but left the title sitting on the next line of the body, plus the literal `Keywords: foo, bar, baz` line — both got injected into the prompt as part of the memory text and the model saw them as content
- New `_parse_memory_blocks(text)` helper captures the title with `re.split(r"(?m)^#\s*Memory:\s*([^\n]*)\n", text)` and strips the keywords line out of the body during parse, returning structured `{title, body, keywords}` dicts
- Injection format changed from `"<raw_block_with_leaked_metadata>"` to `"### {title}\n{body}"` — model gets a clear heading per memory and clean prose underneath

**Bug fix: Trailing punctuation no longer poisons keyword parsing**
- Old splitter `keywords_str.split(',')` then `.strip().lower()` left a literal trailing period on the last keyword if the user wrote `Keywords: foo, bar, baz.` — final keyword became `"baz."` (period included), which never matched user input. Real example present in `helcyon_memory.txt` (`neighbour below.` would never have fired)
- New parser splits on `[,;:]+` (matching the now-deleted alternate loader's tolerance) and strips trailing `[.!?,;:]+` per keyword. Empty keywords filtered out. Validated: `'neighbour below.'` now correctly parses as `'neighbour below'`

**Tweak: Hardcoded common-keyword list replaced with computed frequency downweighting**
- Old code at `app.py:2055` had `common_keywords = {'claire', 'chris', 'neville', '4d', '3d'}` — names of one specific user's recurring people, baked into the codebase. For any other user this set silently downweighted nothing useful and missed all of THEIR recurring names
- Replaced with a per-request computed `kw_block_count` map: keyword → number of memory blocks it appears in. Any keyword that appears in 2+ blocks within the same character's memory file scores 1 point (it can't differentiate between blocks anyway); keywords unique to a single block score 3. Generalises the original principle (downweight non-discriminating terms) without baking in any specific user's data
- Within-block keyword duplicates are now also deduped (the `seen` set in the scoring loop) — a keyword listed twice in the same `Keywords:` line by mistake won't double-count

**Tweak: Stable secondary + tertiary sort keys for memory selection**
- Sort key was `lambda x: x['score']` reversed — pure score, no tiebreaker, so two blocks at score 3 would resolve in arbitrary file-order
- New sort key: `(-score, -match_count, title.lower())` — score desc, then number of distinct matched keywords desc (a block with 2 unique-keyword hits beats a block with 1 unique-keyword hit at the same total score), then alphabetical title for fully-deterministic tie resolution

**Cleanup: Removed dead `load_memories_for_character` + `fetch_character_memories` (`app.py:4671-4742`)**
- Both defined but never called from anywhere in the codebase (verified by grep across all `*.py`). They were a parallel, slightly different implementation of the same operation: list-of-dicts shape, `re.split(r"[,:;]+")` for keywords, max_matches=2 cap. Two implementations of the same conceptual operation diverging in subtle ways is a footgun — someone could wire the wrong one back in
- Deleted ~75 lines including the section header comments. The chat-route inline path is now the single source of truth, and the helpers are at module scope where they belong

⚠️ **The cross-chat-recall skip still suppresses all memory injection** (`app.py:2036`). Per the audit, this may be over-aggressive — chat-search results and per-character memory could be complementary rather than mutually exclusive. Left unchanged in this pass; flag for follow-up if you see Helcyon "forgetting" personal context during cross-session recalls.

⚠️ **Session summaries (`session_summaries/<name>_summary.txt`) are still a parallel system** with its own format, file location, size cap, and injection point — not touched in this pass since it's a separate concern from the per-block memory pipeline.

---

### `app.py` — Chat history search trigger overhaul
**Tweak: Replaced ad-hoc list of trigger phrases with a structural recall-verb + cross-session-marker rule**
- Old trigger (`app.py:2758`) was an OR-list of phrases like `do you remember`, `I told you (about|that|in|last)`, `we (already|previously) (talked|spoke|discussed)` — each one fired independently, so harmless utterances like "do you remember the capital of France?" or "I told you about my dog" (in-thread) triggered a chat-history search and injected stale snippets into the response
- New trigger uses a structural rule: **fire only when a recall verb AND a cross-session marker co-occur within ~80 chars in either order**. A recall verb alone ("remember the capital of France") or a cross-session marker alone ("in another chat I read this") is no longer enough — both must be present, which is the actual signal that distinguishes "user is referencing a previous session" from in-thread back-references and general-knowledge recall
- Recall verbs: `remember(ed/s/ing)`, `recall(ed/s/ing)`, `told you/me`, `tell you`, `mention(ed/s/ing)`, `said`, `saying`, `spoke`, `spoken`, `speak`, `talk(ed/s/ing)`, `chat(ted/s/ting)`, `discuss(ed/es/ing)`
- Cross-session markers: `in another/previous/different/last/the other chat/conversation/session/talk/discussion`, `last time (we/i/you)`, `the other day/time/night/week`, `(a few/couple of) (days/weeks/months/years) ago`, `a while/bit (ago/back)`, `(way) back when/then`, `earlier today/this (week/month/year)`, `previously, we`, `before, (we/i/you)`, `ages ago`
- Patterns moved to module-scope as `_CHAT_RECALL_VERBS`, `_CHAT_CROSS_SESSION_MARKERS`, and a single compiled `_CHAT_SEARCH_TRIGGER_RE` so the early-memory-skip check (~app.py:1976) and primary trigger (~app.py:2758) stay in lockstep — previously they were two independent regex literals and could disagree (e.g. early-check fires → memory skipped; primary trigger doesn't fire → no chat results either; model gets nothing)
- Validated against 27 representative inputs covering true positives (cross-session recalls, time-distance phrasings, "I told you last time.", "we talked about this last time, do you remember?") and true negatives (general-knowledge recall, in-thread references, stock phrases like "I told you about my dog", "this is the last time I will allow it") — all 27 pass
- ⚠️ The model-emitted `[CHAT SEARCH: …]` fallback tag (detected at ~app.py:3274) still has NO instruction in `get_instruction_layer()`. The detector is wired up but the model has never been told the tag exists — so this fallback path effectively only works for character cards that already know the format from training

---

### `utils/session_handler.py`
**Tweak: Tightened the model-facing `[WEB SEARCH: …]` instruction with explicit when-to-fire / when-NOT-to-fire guidance**
- Old `WEB SEARCH:` block in `get_instruction_layer()` was 2 lines with no guidance on WHEN to search — only HOW to format the tag — so the model defaulted to emitting it for any factual question, even ones in its training data
- New block is a 6-line directive:
  - Explicit **when to search**: live-web info, recent events, current prices/scores/stats, news, releases, time-sensitive facts, things the model genuinely doesn't know
  - Explicit **when NOT to search**: general training-data knowledge (history, definitions, well-known facts), opinions/feelings, casual conversation, hypotheticals, creative writing, content already in the thread's context
  - Hard default stated outright: *"Default to NOT searching — only search when not searching would give the user a wrong or outdated answer"*
  - Query format with good/bad example: `[WEB SEARCH: bitcoin price today]` (keywords) not `[WEB SEARCH: what is the current price of bitcoin]` (full question)
  - Post-injection behaviour reinforced: relay naturally as if you just know it, don't mention searching, don't echo the results block structure, don't include a source URL (system appends one)
- Tag format itself unchanged — still `[WEB SEARCH: …]`, still detected by the same regex at `app.py:3040` — so no wire-format break with existing characters or chats
- ⚠️ If a character card overrides system-prompt instructions (some do via aggressive `personality:` or `instructions:` fields), this guidance can be partially undone — check the rendered prompt in the Flask console if a particular character is over-searching

---

## Session: May 10 2026 — Snappier Auto-Generated Chat Titles

### `chat_routes.py`
**Tweak: Auto-title prompt rewritten for shorter, more human-feeling titles**
- Old prompt was a single loose instruction ("short chat thread titles") with no length anchor — model regularly produced 8-12 word titles starting with filler like "How to…" / "A question about…"
- Replaced with a few-shot ChatML prompt: system message states explicit rules (4-6 words max, no leading filler, no end punctuation, no quotes), followed by 3 multi-turn examples ("Python Memory Leak Debug", "Learning German Grammar", "Autumn Leaves Poem") to anchor the noun-phrase style
- `n_predict` lowered from 20 → 16 (tight headroom for a 6-word target while still letting the stop tokens fire naturally)
- Added a hard 6-word cap on `raw_name` as a safety net — if the model ignores the rule, `' '.join(_words[:6])` chops the overflow and re-strips any trailing punctuation exposed by the cut
- Trailing-punctuation regex extended to include `:` (was `[.!?,;]`, now `[.!?,;:]`) — covers cases where the model produces "Topic: Subtopic" style titles
- Fallback word-chop (when the `/completion` call fails) unchanged at 5 words — already inside the 4-6 target band
- ⚠️ Auto-titles are generated via a synchronous call to llama.cpp `/completion` at rename time; if the server is down the fallback word-chop fires silently — check the Flask console for `🏷️ Model suggested title:` vs `⚠️ Model title generation failed` to tell which path ran

---

## Session: May 09 2026 — Mid-Word Cutoff Root Cause Fix (KV Exhaustion)

### `truncation.py`
**Bug fix: `CONTEXT_WINDOW` hardcoded to 16384 independent of actual server `--ctx-size`**
- Root cause of responses cutting off mid-word at message 9-10 with `truncated = 0`
- Three compounding bugs:
  1. `CONTEXT_WINDOW = 16384` hardcoded literal — if the server runs at `--ctx-size 12288`, the trim budget allowed prompts up to ~15000 real tokens inside a 12288-token KV cache, leaving almost nothing for generation
  2. `rough_token_count` undercounts real BPE tokens by ~25% — each English word counted as 1 rough token but Llama/Mistral BPE averages ~1.25 tokens/word; at message 9-10 (7500-9000 rough tokens) the real token count pushed prompt + n_predict past `ctx_size`, exhausting KV mid-generation
  3. `n_predict` was always `max_tokens` (4096) regardless of available KV space — model tried to generate 4096 tokens but ran out of cache at e.g. 2000, stopping at whatever BPE boundary it was on (mid-English-word); `truncated = 0` because the PROMPT fit — the exhaustion happened during output, not prompt ingestion
- Fix 1: `CONTEXT_WINDOW` now read dynamically from `settings.json → llama_args.ctx_size` at import time via `_read_ctx_size()` — always in sync with the running server
- Fix 2: Added `TOKEN_FUDGE = 1.25` — prompt budget is now `int((CONTEXT_WINDOW - GENERATION_RESERVE - SYSTEM_BUFFER) / TOKEN_FUDGE)`, making the rough budget 20% more conservative so real token usage stays within `ctx_size`
- ⚠️ If `ctx_size` is changed in settings.json, restart the Flask process — `CONTEXT_WINDOW` is read at module import time

### `app.py`
**Bug fix: `n_predict` now dynamically capped to available KV space**
- Before building the ChatML payload, reads `ctx_size` from `settings.json` and computes `_n_predict = min(max_tokens, ctx_size - int(rough_token_count(prompt) * 1.25))`
- `n_predict` in the payload now uses `_n_predict` instead of the static `sampling["max_tokens"]`
- Logs a warning when capping fires so KV pressure is visible in the Flask console
- ⚠️ Do NOT revert to `"n_predict": sampling["max_tokens"]` — that was the proximate cause of every mid-word cutoff

**Bug fix: `_ex_overhead` now includes `ex_block` wrapper text**
- Previous calc: `rough_token_count(_char_ex_pre)` — measured only the raw example dialogue, not the ~400 rough-token wrapper injected around it (the `═══` separator lines, style rule headers, ⚠️/⛔ instruction lines)
- This under-reported system overhead to `trim_chat_history`, allowing conversation history that was too large
- Fix: `_ex_overhead = rough_token_count(_char_ex_pre) + 400` — constant covers the fixed wrapper; prints updated log line

---

## Session: May 09 2026 — Example Dialogue Overhead Pre-calc Fix

### `app.py`
**Bug fix: Example dialogue overhead pre-calc didn't guard fallback chain with `_is_jinja_model`**
- Pre-calc had two flat `if not _char_ex_pre:` conditions — one for Priority 2 (settings.json `global_example_dialog`) and one for Priority 3 (`.example.txt`) — neither checked `_is_jinja_model`
- The actual resolution at the `ex_block` build site wraps both fallbacks in a single `if not _char_ex and not _is_jinja_model:` block — jinja/Gemma models never use either fallback
- Result: for a jinja model with no character `example_dialogue` but a `.example.txt` on disk, the pre-calc read the file and passed a non-zero `extra_system_overhead` to `trim_chat_history`, reserving phantom space and dropping messages unnecessarily
- Fix: merged the two outer `if not _char_ex_pre:` conditions into one `if not _char_ex_pre and not _is_jinja_model:`, and nested Priority 3 inside it — structure now mirrors the actual resolution exactly
- Character-level `example_dialogue` is still always measured regardless of model type (only fallbacks are jinja-gated)
- ⚠️ DO NOT split this back into two flat conditions — Priority 3 must stay nested inside the `_is_jinja_model` guard

### `chatml_fixer.py`
**Refactor: Replaced enumerated mangled-tag patterns with a broad catch-all approach (v18)**
- Previous approach enumerated known bad tag variants (`<<|`, `<||`, `Im_start`, `::`, `/>` etc.) — kept breaking when new corruption styles appeared
- New approach: match *anything* containing `im_start`, `im_end`, or `in_end` surrounded by tag-like punctuation that isn't the exact canonical form, then rewrite to canonical
- `_normalize_mangled_tags` now uses two broad `re.sub` calls instead of a list of named patterns:
  - im_end: `<{1,2}[ \t|:]*(?:im_end|in_end)[^\n>]{0,20}/?>+` → `<|im_end|>` (absorbs any junk between keyword and closing bracket without crossing line boundaries)
  - im_start: `<{1,2}[ \t|:]*im_start[ \t|:>]*(?:\w+)?(?:/>)?` with a `_fix_start` callback that extracts the role word and validates it against `VALID_ROLES`
- `_MANGLED_RE` (used by `find_issues`) updated to use `(?!(?-i:<\|im_(?:start|end)\|>))` negative lookahead — the `(?-i:...)` inline flag makes only the exact lowercase canonical tokens exempt, so anything else (wrong case, extra pipes, spaces, colons) is flagged
- `contains_chatml` now uses `_ANY_CHATML_RE` which matches any variant (including fully mangled tags like `<||im_start||`) — previously only matched canonical form so corrupted files were silently skipped
- **Bug fix:** Trailing garbage after last `<|im_end|>` (e.g. `<|im_end|>}`) caused the missing-final-im_end check to add a duplicate tag — fixed by stripping trailing garbage *before* the missing-im_end addition
- **Bug fix:** Files ending with `---` (markdown separators) had `<|im_end|>` appended directly — fixed by stripping trailing `---` separators before the missing-im_end addition
- **Bug fix:** UTF-8 BOM (`﻿`) caused false preamble detection — `str.strip()` does not remove BOM; fixed by using `.strip('﻿ \t\n\r')` explicitly in both `find_issues` and `fix_chatml`
- **Removed:** Blank-line detection between turns — was flagging clean files as errors; blank lines between turns are now silently ignored

---

## Session: May 07 2026 — Section Divider Colour in Theme Editor

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

## Session: May 07 2026 — HR Visibility + Equal Spacing

### `style.css`
**Tweak: HR separators now clearly visible with equal spacing above and below**
- `border-top` increased from `1px` to `2px` for visibility
- `opacity` raised from `0.6` to `1`
- `margin` kept at `10px 0` (equal top/bottom) — adjacent element margins still zeroed so hr owns the gap
- `ul + hr` margin-top synced to match `10px` base

---

## Session: May 07 2026 — HR Section Spacing Balanced

### `style.css`
**Tweak: Sections too cramped after gap fix — rebalanced hr spacing**
- Previous fix zeroed all margins around `<hr>` which removed ALL breathing room between sections
- New approach: `hr` itself owns the gap (`margin: 12px 0`) — single source of truth, no stacking
- All adjacent element margins (`p`, `ul`, `ol` before/after hr) zeroed so only the hr value counts
- Also merged the duplicate `.model-text-cont hr` rule into the unified top-level rule

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

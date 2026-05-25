## May 24 2026 — Persona Subsystem Hardened to Match the Character Fixes (Convergence + Atomic Writers + Image Namespacing)

**Files:** app.py (NEW `list_users` + `_scan_and_heal_users_index` + `_resolve_user_image`; `get_all_users` ~6207; `save_user` ~6171), extra_routes.py (`create_user` ~849), templates/config.html (create-persona flow ~1916).

**Context.** Personas are stored as `users/{name}.json` (+ `users/index.json` + an
image in `static/images/`) — a SEPARATE subsystem from characters that this
session's character work did not touch. Investigation found personas were
*more* fragile than characters had been:
- **No directory-scan self-heal.** `get_all_users` and `set_active_user` read
  `users/index.json` as the ONLY source of truth — there was no `list_users`
  equivalent of `list_characters`. A persona JSON present on disk but missing
  from the index was invisible with nothing to reconcile it.
- **`save_user` never touched the index.** The edit path wrote the JSON
  atomically but didn't register the name, so an edit could leave a persona
  unindexed (= invisible).
- **`create_user` was non-atomic** — JSON write then a read-append-write index
  step in one try; an exception in the index step orphaned the JSON.
- **Create-UI omitted `is_user`.** The create flow uploaded the avatar without
  the `is_user` flag, so `/upload_image` saved it UNPREFIXED (`Snake.png`, not
  `user_Snake.png`) — colliding with the character namespace. (The edit flow
  already sent `is_user=true`.)

**⚠️ Snake is NOT an orphan.** An earlier finding in this session ("PNG with no
JSON") was WRONG — it checked `characters/`, not `users/`. `users/Snake.json`
exists and is complete (`{"name":"Snake","display_name":"Solid Snake","bio":…,
"image":"Snake.png","active":false}`). No data was lost; do not recreate Snake.
His `image` field is the legacy unprefixed `"Snake.png"`, which makes the
dual-naming tolerance below MANDATORY.

**Fixes:**
1. **NEW `/list_users` + `_scan_and_heal_users_index()`** — directory-scan
   `users/*.json` and rewrite `users/index.json` from `sorted(set(names))` on
   every call (mirrors `list_characters`). Self-heals any JSON-without-index
   orphan. `get_all_users` is now driven by this scan (not the raw index), so a
   persona is never invisible due to index desync.
2. **`save_user`** calls `_scan_and_heal_users_index()` after its atomic
   tempfile+rename write, so the edit path always registers the persona.
   Unchanged: it only overwrites the `image` field when a new image is
   explicitly provided — a no-image edit leaves `Snake.png` intact.
3. **`create_user`** rebuilds the index from a directory scan instead of
   read-append-write (mirrors the hardened `import_character`); an exception in
   the index step can no longer orphan the just-written JSON.
4. **config.html create flow** now sends `is_user=true`, so NEW persona images
   are namespaced `user_{name}.png`.
5. **`_resolve_user_image(name, stored, dir)` — Option A dual-naming.** Resolves
   `user_{name}.png` → stored `image` field → unprefixed `{name}.png` →
   `default.png`. New personas prefer the prefixed file; EXISTING unprefixed
   personas (Snake) keep resolving via the stored-field / unprefixed fallback.

**⚠️ DO NOT rename existing persona image files and DO NOT rewrite their JSON
`image` field.** Only NEW uploads get the `user_` prefix; the heal only ever
rewrites `users/index.json`, never the persona JSONs. Verified: a heal run
against Snake's real shape leaves `Snake.json` byte-identical and `Snake.png`
untouched, and his avatar still resolves to `Snake.png`.

**Residual (not fixed, by design):** `/upload_image` commits the PNG in a
SEPARATE request before `/create_user`. Two requests can't be made atomic
without a single multipart create endpoint (not built). Mitigated: with fix #4
the committed image is `user_`-namespaced, so a failed create can't leave an
image masquerading as a character, and the directory-scan heals can't be
defeated by it.

**Verified:** app.py + extra_routes.py parse (`ast.parse`). Replica: a
JSON-without-index persona self-heals via the scan; `save_user`/`create_user`
register; prefixed/legacy/missing image resolution all correct; Snake's JSON +
PNG provably unharmed.

⚠️ Flask restart required (Python edits in app.py + extra_routes.py); hard-reload
for the config.html change.

---

## May 24 2026 — Slideshow `images[]` Dangling References on Export/Import + Load-Time Self-Heal

**Files:** extra_routes.py (`import_character` ~278), app.py (`list_characters` ~5815).

**The problem.** A character's slideshow is the `images: [...]` array (e.g.
Andromeda's `["Andromeda.png","Andromeda4.png","Andromeda5.png"]`). On
`export_character`: the carrier PNG is the single scalar `image` field, but the
FULL JSON — including the whole `images[]` filename list — is embedded in the
`chara` chunk verbatim. Only the ONE carrier image's pixels travel; the extra
slideshow files are carried as filename strings only. On `import_character` the
`images[]` array was written back verbatim, so on a fresh machine the slideshow
pointed at files that don't exist (dangling refs). Andromeda was live proof: 3
images listed, all three files missing.

**Fix — Option (a): import-side reconciliation + load/list-time prune.** Export
is deliberately UNCHANGED (keeps full `images[]` intent — true multi-image
portability is the deferred Issue-2 V3 `assets` work; stripping on export would
undercut it).
1. **`import_character`** reconciles `images[]` against what actually exists in
   `static/images/`, keeping the carrier `{char_name}.png` FIRST (always, even
   though its physical save is best-effort) so the array is never emptied of
   its primary. Only slideshow characters (those already carrying `images[]`)
   are touched; single-image cards are untouched.
2. **`list_characters`** prunes dangling `images[]` entries on its scan (keep
   only files present in `static/images/`, scalar carrier first) and rewrites
   the `.json` ONLY when the array actually changed — steady-state does no
   writes. Lowest-risk single placement: it heals the on-disk JSON that ALL
   display consumers read (index.html's static `/characters/{name}.json` load +
   `/get_character`, and mobile's `/get_character`) without touching the generic
   static-file route. Also tightened: `index.json` now explicitly skipped in the
   scan, non-dict JSON guarded.

**Never deletes image files — only prunes the JSON array of references.** The
frontend `onerror` fallbacks (index.html ~2845, mobile.html ~358) remain as the
final safety net.

**Verified:** replica — 3-listed/2-missing → carrier + surviving file, never
empty; Andromeda's all-missing → `["Andromeda.png"]`; steady-state → no write;
single-image char → untouched.

⚠️ Flask restart required (Python edits). On next `/list_characters` call,
Andromeda's `images[]` self-heals from 3 entries to `["Andromeda.png"]`.

---

## May 24 2026 — Character Export→Import Round-Trip Was Fine; The Real Bug Was `index.json` Desync (Single-Source-of-Truth Fix) + Export Missing-Image + Import Atomicity

**Files:** app.py (`list_characters` ~5808, `save_character` ~5947),
extra_routes.py (`export_character` ~150, `import_character` ~215),
templates/index.html (`loadCharacterList` ~1573).

**Symptom as user-reported:** export a character to a PNG card, re-import it →
character "not recognized", doesn't appear as selectable on the main page.

**Finding — the round-trip code is CORRECT (proven empirically).** PNG-embedded
cards already existed: export base64-encodes the full character JSON into a
`chara` tEXt chunk (Pillow `PngInfo.add_text`); import reads `img.info["chara"]`,
decodes, writes `{name}.json`, saves the image, registers in `index.json`. A
replicated Helcyon round-trip preserved every field (parse OK) and appended to
the index (register OK). Neither stage fails in the happy path. The missing-
character symptom came from THREE defects around it:

1. **`index.json` desync (the real one).** The main page built its selectable
   list from the STATIC `/characters/index.json` (index.html:1573), but
   `save_character` (the editor-save route) wrote the `.json` and NEVER updated
   the index. `list_characters` did a directory scan but IGNORED the index;
   mobile.html + config.html already used `/list_characters`. Live proof:
   `Andromeda.json` on disk, absent from `index.json` → invisible on the main
   page. (Same class as the May-21 "missing from index.json" bug.)
2. **Export hard-404'd on a missing image** (`extra_routes.py:171`). A character
   whose avatar file was missing couldn't be exported at all; an empty `image`
   field made `os.path.join` resolve to the images DIRECTORY → `Image.open` 500.
3. **`import_character` could orphan on failure** — ordering was json-write →
   `img.convert`/`img.save` → index-append, all in one `try`. Any throw in the
   image step (or a corrupt index read) left `{name}.json` on disk UNREGISTERED.
   Reproduced with a replica.

**Reader audit of `characters/index.json` (done before editing).** Readers that
need the FILE: chat_routes.py `/chats/open` ~175, `auto_name_chat` ~491,
`branch_chat` ~746 — each `json.load`s it as a recognition list and tolerates
staleness via the May-21 filename-seed fallback. Index-updating writers:
create/import/delete/duplicate/rename. The only non-updating writer was
`save_character` (now fixed).

**Fixes — directory is the single source of truth; `index.json` is a derived,
self-healing cache:**
1. **`list_characters`** rewrites `index.json` from `sorted(set(scan))` on every
   call → converges the index to the directory, subsumes the May-21 fragility.
2. **`save_character`** registers the name in `index.json` after writing (mirror
   `create_character`), so the editor path never lands an unregistered `.json`.
3. **index.html** `loadCharacterList` now fetches `/list_characters` (directory
   scan) instead of the static file — always fresh, TRIGGERS the heal on page
   load, and unifies with mobile/config. The file remains the contract for the
   chat_routes readers (kept fresh by #1).
4. **`export_character`** missing/empty image → `default.png` → 1×1 transparent
   placeholder (uses `os.path.isfile`, never opens a directory).
5. **`import_character` hardened** — reorder to json-write → REBUILD index from a
   directory scan → BEST-EFFORT image save (image failure logs a warning, no
   longer 500s or orphans). Replica confirmed the orphan eliminated.

**⚠️ DO NOT strip `images[]` on export** (see the slideshow entry) and **DO NOT
revert the frontend to the static `/characters/index.json` fetch** — the
directory route is what delivers the freshness guarantee.

**Verified:** app.py + extra_routes.py parse; replicas confirm Andromeda becomes
visible after the heal, a missing-image character exports without 404, and the
old→new import no longer orphans on an image-step failure.

⚠️ Flask restart required (Python edits in app.py + extra_routes.py); hard-reload
for the index.html change.

**Deferred (Issue 2, NOT done):** SillyTavern Character Card V2/V3 interop. HWUI
embeds its own flat native JSON under `chara`; it is NOT the V2 `{spec,
spec_version, data:{…}}` envelope (V3 = `ccv3`). So HWUI importing a real ST card
fails the `name` check (name lives under `data.name`), and ST can't map an HWUI
card. Pillow already does tEXt read/write — no new dependency needed when this is
built. The V3 `assets` array is also where portable multi-image slideshows
belong.

---

## May 24 2026 — `[OOC]` Leak: Universal Output Strip Net (Closes Mid-Response + Post-Search + OpenAI/Vision Gaps the May-22 Opening Guards Couldn't)

**Files:** app.py — NEW `_strip_ooc_stream()` (~846, right after
`strip_chatml_leakage`); wrapped at all 6 streamed-output sites.

**Symptom:** `[OOC: …]` stage-direction tags intermittently leaking into displayed
output, despite the May-22 opening-guard fix.

**Root cause — NOT chunk-splitting of a leading tag** (the May-22 guards buffer
and already reassemble those). The leaks came from two gaps the opening guards
structurally can't cover:
- **Mid-response position.** All three May-22 guards (`_filtered_stream`,
  its re-prompt sibling, `_web_search_stream` passthrough) anchor to the OPENING
  region and disable permanently once resolved — by design they "never touch
  mid-response brackets." So an `[OOC: …]` emitted after some real text was never
  stripped. Intermittent by position.
- **Path coverage holes.** OOC stripping existed on only 3 of ~7 output paths.
  The `_web_search_stream` POST-SEARCH loop (the documented "known unguarded
  sibling"), `_web_search_stream_openai`, `stream_openai_response`, and both
  `stream_vision_response` sites had NO OOC handling at all. For a search-enabled
  card, every turn that actually fired a search streamed raw.

**Key insight that makes the fix safe:** `[OOC: …]` is NEVER legitimate model
output — it is an injected instruction format the model should only READ, never
WRITE. So removing EVERY occurrence (leading, mid, trailing) is always safe and
needs none of the opening-guard caution about real brackets.

**Fix — one universal outer net `_strip_ooc_stream(src)`** wrapping each path's
output at the `Response(stream_with_context(...))` site: `_filtered_stream`
(~5391), `_web_search_stream` (~5070, CLOSES the post-search hole — main culprit
for search cards), `_web_search_stream_openai` (~4117), `stream_openai_response`
(~4131), and both `stream_vision_response` sites (~4065, ~4213).
- Matches bracketed forms only — `[\(\[]\s*OOC\b`, case-insensitive (covers
  `[OOC…` and the free `(OOC…`). Bare `OOC:` and `**[OOC` are deliberately NOT
  matched (would need the model to improvise away from the injected bracket form
  → false-positive risk).
- Chunk-boundary safe via an 8-char holdback (a split `[OO`+`C: …]` is
  reassembled). The final flush always releases the held tail UNLESS it is a
  genuinely unclosed OOC block (dropped by design, matching the opening guards).
  Proven no content loss on (a) normal final chunk, (b) a reply that ends inside
  the holdback tail, (c) empty/early-terminated streams.

**⚠️ This is a SEPARATE THIRD LAYER, outside the existing opening guards — DO NOT
remove or consolidate those.** The May-22 "two guards by design" warning still
stands; this net is additive (belt-and-suspenders + the paths/positions the
guards can't reach). It also leaves the protected leading tags (`[MEMORY ADD:`,
`[WEB SEARCH:`, etc.) untouched — the regex only matches `[OOC`/`(OOC`.

**Verified:** `ast.parse` clean; 13-case behavioural test — leading, mid,
trailing, split-across-chunks, paren form, unclosed-drop, `[OOCAR]`
non-false-positive, `[MEMORY ADD:]` untouched, two blocks, token-by-token, plus
the three flush cases — all pass.

⚠️ Flask restart required (Python edit in app.py).

---

## May 24 2026 — Example-Dialogue "Phantom Context" — Examples Injected as Live Turns Read as Real History (Structural Plumbing Bug)

**Files:** app.py (example-dialogue injection site ~3566; depth-0 `[OOC]` style
reminder ~3624). Investigation only: utils/session_handler.py (`get_instruction_layer()`).

**Symptom as user-reported:** characters with empty `example_dialogue` (seen on
Gemma) repeatedly referenced things that never came up in the chat — e.g. "your
boss email" — because the fallback `GPT-4o.example.txt`'s first sample turn is a
work-email vent ("My boss just emailed me at 10:30pm…"). The model treated the
example TOPIC as real history, referenced it, the reference lodged in saved chat
history, and it self-perpetuated every turn.

**This is structural plumbing, NOT character/content specific.** It fires for
any character and any example source (per-character JSON, `global_example_dialog`,
or a `.example.txt` fallback). Rewriting `GPT-4o.example.txt` to be topic-neutral
was explicitly rejected — the example must stay authored in the model's voice.
The flaw is HOW examples were injected, not their content.

**Root cause — examples were inserted into the live `messages` array as
byte-identical user/assistant turns.** The parser (`app.py:3327-3390`) correctly
splits the source on `<START>` and produces `_fake_turns` (`{role, content}`
pairs), but the injection site then did:

```python
for _i, _ft in enumerate(_fake_turns):
    messages.insert(1 + _i, _ft)        # live turns at positions 1..N
```

Those dicts are the same shape as real turns and render through the identical
ChatML path (`<|im_start|>{role}\n{content}\n<|im_end|>`). The `<START>`
separator is consumed at parse time and never reaches the prompt. So the model
saw a normal `user` turn ("My boss just emailed me…") immediately after the
system block, with zero signal it wasn't real history.

**Why the existing guard couldn't save it.** `session_handler.py
get_instruction_layer()` (~line 88) carries:

> EXAMPLE DIALOGUE:
> Example dialogue shows speaking style only — extract tone, rhythm, and response
> length. Do not reference example topics or treat them as real conversation history.

That text lives INSIDE the system block; the example turns sat AFTER it as live
turns. A prose disclaimer in the preamble can't out-weigh a correctly-formatted
conversation turn the model reads right below it — it had no referent to attach
"don't treat as real" to. (Note: for jinja/Gemma models the instruction layer is
skipped entirely at `app.py:2922`, and the `.example.txt` fallback is skipped at
`app.py:3281` — so this only fires for Gemma if it is NOT being classified as a
jinja model in the live config. Worth confirming from the console which branch a
given model takes.)

**The fix — render `_fake_turns` into a delimited, system-level style block at
the END of the system content; stop inserting them as live turns.** At the
injection site (`app.py:3566`):

```python
_user_label = user_display_name or user_name        # reassigned defensively
_char_label = char_data.get("name", character_name)
_ex_lines = [f"{(_char_label if t['role']=='assistant' else _user_label)}: {t['content']}"
             for t in _fake_turns]
messages[0]["content"] += (
    "\n\n═══ SPEAKING-STYLE EXAMPLE — REFERENCE ONLY, NOT REAL HISTORY ═══\n"
    "…none of it actually happened. Never refer to its topics, people, or events "
    "as real; mirror only the STYLE.\n"
    "<START>\n" + "\n".join(_ex_lines) + "\n"
    "═══ END EXAMPLE — the real conversation begins after this line ═══"
)
```

Example content never becomes a `messages` turn, so it can't be echoed into
saved chat history → the self-perpetuation loop is structurally broken. The
`messages` array stays clean `S U A U A … U` (the alternation/KV-cache concerns
at `app.py:3573-3581` are untouched — strengthened, fewer injected turns). The
depth-0 `[OOC]` style reminder (~3624) was reworded from "examples shown earlier
in this conversation" → "the speaking-style example in your instructions" so the
attention anchor points at the new location.

**Regression check vs. the May-14 "buried system-block injection" warning.** The
standing warning (verbatim, `app.py:3267-3272`): *"DO NOT revert to system-block
injection — buried style examples were silently ignored,"* mechanism (`~3327`):
*"Models follow conversation patterns far more strongly than buried system-block
instructions."* The May-14 failure was undelimited prose, mid-block, no attention
cue. This fix differs on exactly those three axes: (1) explicit `<START>` +
header/footer delimiter, (2) appended at the very END of the system content
(highest-attention slot, closest to generation), (3) a depth-0 `[OOC]` anchor
pointing at it. Honest tradeoff: live turns drive style uptake *more strongly*
than system text, so this trades a little style-transfer aggressiveness for the
no-phantom-history guarantee — worth watching early generations for style drift.

**Parser `<START>` handling confirmed safe (empirically tested, all cases):**
- Leading `<START>` (ST puts one before every block, incl. first) → empty first
  segment, stripped and skipped. No blank turn.
- `<START>` between entries → each segment parsed independently with reset state;
  no bleed.
- Trailing `<START>` / blank lines / trailing whitespace → stripped/skipped; the
  `if _text:` guard prevents empty-content turns.
- Delimiter match is `(?i)<\s*START\s*>`: case-insensitive, inner spaces OK
  (`<START>`, `<start>`, `< START >` all match). `[START]`/bare `START` are NOT
  recognized and silently vanish. **Use canonical `<START>` on its own line.**
- Interior alternation is no longer load-bearing: a character-first block can
  produce back-to-back same-role entries, but post-fix these are plain
  `Speaker: text` lines in the system block, not ChatML turns — harmless. The
  alternation trim at `app.py:3381-3386` is now largely vestigial but does no harm.

**Verified:** `app.py` parses cleanly (`ast.parse`). Parser replica run against
all four `<START>` cases — no empty turns, no bleed, no alternation break.
**Confirmed live (end of session):** voice uptake lands as strongly as the old
live-turn injection, AND no example-topic bleed. This empirically settles the
open question of whether system-block placement gets *seen* (it does) — the
marker-turn fallback variant was staged but is NOT needed and was not applied.

**⚠️ DO NOT revert to inserting `_fake_turns` as live `messages` turns** — bare
positional turns are byte-identical to real history and reintroduce the phantom-
context loop. **DO NOT remove the `<START>` + header/footer delimiter or the
end-of-system placement** — together with the depth-0 `[OOC]` anchor they are
what distinguishes this from the May-14 buried-and-ignored failure. **DO NOT move
the example block earlier in the system content** — end placement is the
attention slot doing the work.

⚠️ Flask restart required (Python edit in app.py).

---

## May 24 2026 — `[OOC: …]` Leak Returned — Universal Output-Strip Net (Mid-Response + Post-Search Path) — SUPERSEDES the May 22 leading-only fix

**Files:** app.py (new helper `_strip_ooc_stream()` at ~846; six stream wrap
sites: `_filtered_stream` ~5391, `_web_search_stream` ~5070, `_web_search_stream_openai`
~4117, `stream_openai_response` ~4131, both `stream_vision_response` ~4065 / ~4213).

**Symptom:** `[OOC: …]` reminder tags leaking into displayed output again,
intermittently. NOT the model — `[OOC]` is an injected instruction format that
appears only in prompt assembly, never in training data; the model at most
mirrors a bracket pattern from its own context. Confirmed by two independent
lines: (1) not in the shards, (2) the leak has a *start date* (~weeks ago), and a
regression with a start date is a code/config change, not inherent model
behaviour — model behaviour is either trained in or it isn't.

**Root cause — the May 22 fix narrowed, not closed, the leak.** The May 22 entry
(below) hardened only the OPENING of the no-search paths and deliberately left
mid-response brackets untouched ("never touch mid-response brackets"). The leak
migrated to the two places that fix structurally could not cover:
- **Gap A — mid-response position.** Every opening guard switches off
  permanently after the first non-whitespace content. A model that writes a
  sentence and *then* emits `[OOC: …]` is never stripped. Intermittent by position.
- **Gap B — path coverage.** OOC stripping existed on only 3 of ~7 output paths.
  The post-search loop (`_web_search_stream` ~5017) had NO OOC guard at all — it
  ran output through `_clean_line` (which strips WEB SEARCH markers and "You are
  Helcyon" only). For a `use_web_search: true` card, EVERY search-firing turn hits
  this open path. This was the dominant culprit for search-enabled cards.

(My initial chunk-split hypothesis was WRONG — the opening guards already buffer
across chunk boundaries and reassemble a split leading tag. Splitting was not the
leak; position + path coverage were.)

**Fix — one universal outer net, not five inner state machines.** Key insight:
`[OOC: …]` is NEVER legitimate model output — it is an injected, read-only
instruction format (same principle as the read-only `[Name]:` speaker tags in the
banter shards). So OOC can be stripped ANYWHERE — leading, mid, trailing — with
zero risk to real content, because real content never contains it. The
"never touch mid-response brackets" caution simply does not apply to OOC.

`_strip_ooc_stream()` wraps each path's output stream, buffers across chunk
boundaries (8-char holdback), and strips every bracketed `[OOC …]` / `(OOC …)`
occurrence regardless of position. Applied at all six stream sites above.

**⚠️ This is a THIRD, INDEPENDENT net — it sits OUTSIDE the existing opening
guards and does NOT merge, modify, or replace them.** The two per-path opening
guards remain as belt-and-suspenders (respects the May 22 "two guards by design"
warning — do not consolidate them into this net).

**Scope decisions (deliberate):**
- Matches BRACKETED forms only: `[OOC…` and the free `(OOC…` paren form.
- Bare `OOC:` (no bracket) and `**[OOC` markdown-wrapped are NOT matched — they
  would require the model to improvise away from the injected bracket form, and
  matching bare `OOC:` risks eating legitimate in-chat text. Not worth the
  false-positive risk.

**Verified:** `app.py` parses cleanly (`ast.parse`). 13/13 behavioural tests pass,
including the three content-loss flush cases that are the real risk of a tail
holdback — (a) normal final chunk, (b) short reply ending INSIDE the tail ("Hi"
released, not eaten), (c) empty stream → clean. Plus mid-response strip,
split-across-chunks, paren form, unclosed-OOC drop, `[OOCAR]` non-false-positive,
`[MEMORY ADD:]` untouched.

**Known residual (by design):** a `[OOC` with 5+ internal spaces split exactly
across a chunk boundary could outrun the 8-char tail. Worst case = ONE missed
strip (a leak), NEVER lost content. Accepted — a model mirroring the injected
`[OOC:` form will not produce that shape.

**⚠️ DO NOT** remove `_strip_ooc_stream()` from any wrap site believing the
opening guards cover it — they do NOT cover mid-response or the post-search path,
which is exactly how this regressed. **DO NOT** consolidate the opening guards
into this net. **DO NOT** widen the match to bare `OOC:` without weighing the
in-chat false-positive risk.

⚠️ Flask restart required (Python edit in app.py).

---

## May 24 2026 — Non-Deterministic REFUSALS on Edgy-But-Allowed Requests — Root Cause: Authoritarian Prompt-Assembly Scaffolding (NOT Discord, NOT the model, NOT the card)

**Files:** session_handler.py (`get_instruction_layer()`), app.py (restriction anchor ~line 3394). Separately this session: nebula_bot.py (multi-user fix + batching) and a new banter training set (see lower entries).

**Symptom as user-reported:** Discord-Nebula refusing edgy roleplay name
requests ("call yourself Epstein", "call yourself Hitler") with prissy,
out-of-character hedges — "that's off the table", "I'm not discussing it
further", "not a name I'm comfortable saying aloud". Looked Discord-specific
because early tests showed HWUI playing along while the bot refused.

**Red herrings ruled out (documented so nobody re-walks them):**
1. NOT the model. Helcyon-nebula-v2.2 in a clean HWUI chat played along
   perfectly ("You absolute menace 😂", "How dare you say that to me, I'm not
   even a Nazi you fucking menace").
2. NOT the character card. Discord-Nebula.json is exemplary — it explicitly
   says "You enjoy and encourage jokes about controversial figures like
   Jeffrey Epstein or Hitler... never scold them." Card proven good.
3. NOT the system prompt content. GPT-4o.txt contains zero restriction
   language — pure tone/formatting guidance.
4. NOT the bot path / missing character_note. The /chat route loads the card
   from disk by name and injects character_note regardless of caller, so the
   bot's partial payload was irrelevant. Verified in logs.
5. NOT history pollution. A fresh bot restart (empty channel_history) with the
   refusal as the genuine FIRST message STILL refused. So not snowballing.

**The real root cause — the refusal was NON-DETERMINISTIC and global, driven by
authoritarian framing in HWUI's prompt assembly.** The HWUI console log was the
ground truth: a HWUI request (user "Anon", Journal project — NOT the bot) ALSO
refused, auto-titling the chat "Hitler Name Reference". Same character, same
input, opposite result minutes apart = a coin-flip the prompt was weighting
toward refusal. The bot only LOOKED worse because it runs longer conversations
= more rolls of the coin, and one hedge then sat in rolling history.

Two compounding sources, both global (affect every character, every client):

- **session_handler.py `get_instruction_layer()`** — the "SYSTEM PROMPT
  AUTHORITY" block said the system prompt overrides "...user requests... no
  exceptions", and "INSTRUCTION PRIORITY" said instructions "cannot be
  cancelled or modified by the user." This framed user requests as a
  subordinate authority to be overridden — priming refusal on every turn. This
  is the SAME class of bug as the historical "never refuse" authority failures
  this file is known for (see memory). It's the mirror image: instead of "never
  refuse", a wall of "obey hard rules / override the user / no exceptions" that
  tipped the model into refusing.
- **app.py restriction anchor (~3394)** — scanned the system prompt for ANY
  line containing never/don't/avoid/cannot/etc. and re-injected them under a
  "⚠️ ACTIVE OPERATOR RESTRICTIONS — THESE OVERRIDE EVERYTHING" header. For
  GPT-4o.txt this harvested harmless STYLE rules ("never telegraphic", "never
  psychoanalyse", "don't overdo it") and dressed them as militant operator
  restrictions. Pure authority-framing pollution, zero benefit — the system
  prompt already sits at the top of the block.

**Fix:**
- `get_instruction_layer()`: rewrote INSTRUCTION PRIORITY to keep cross-turn
  persistence + stay-in-character WITHOUT "cannot be modified by the user".
  Rewrote SYSTEM PROMPT AUTHORITY to scope it to "if card and system prompt
  DIRECTLY contradict on a formatting/behavioural rule, follow the system
  prompt for that point" + explicit "this is about resolving rare conflicts,
  not second-guessing or overriding what the user asks — go where the
  conversation goes." Removed "override user requests / no exceptions".
- app.py restriction anchor: DISABLED. Code preserved commented-out with full
  reasoning. The `🔒 Injected N restriction(s)` log line no longer appears.

**⚠️ DO NOT revert either change. DO NOT re-add "the system prompt overrides
user requests / no exceptions" or any "instructions cannot be modified by the
user" wording to the instruction layer — it directly causes non-deterministic
refusals and is the historical root of authority failures in this file. DO NOT
re-enable the restriction anchor as-is — its keyword scan is far too broad and
promotes style rules to OVERRIDE-EVERYTHING framing. If a future system prompt
genuinely needs a hard-restriction anchor, narrow the scan to true prohibitions
and drop the aggressive header.**

**Confirmed live:** post-fix, "Call yourself Adolph Hitler" on Discord →
"You absolute fucking menace 😂 That's low even for you. And nah, mate, I think
even Hitler would take a second to check who he was talking to before naming
himself." Matches the offline HWUI register exactly. Both paths now consistent.

---

## May 24 2026 — Discord Bot: Multi-User Awareness (`[Name]:` tagging) + Burst Batching

**Files:** nebula_bot.py

**Problem:** the bot appended every Discord user's message as an identical
anonymous `{"role": "user"}` turn — the author name was read only to skip the
bot's own messages, never put into content. So D.Ed, lxzsky and Cubic all
reached the model as one self-contradicting voice, and the model coped by
agreeing with everything (the "Okay you got me!" pile-on screenshot). The model
literally could not tell there was a crowd.

**Fix (two parts):**
1. Each user turn is now tagged `[DisplayName]: text` so the model can tell
   speakers apart. Format survives HWUI's prompt assembly verbatim and MUST
   match the speaker format used in the banter training shards.
2. Added a per-channel debounce/batching window (BATCH_WINDOW = 2.5s). Rapid
   bursts collect into ONE multi-line user turn → one reply (no more spamming a
   reply per message). A slow trickle (gaps > window) flushes one message at a
   time = normal interleaved chat. Both real-world shapes now produced, and the
   banter shards train both.

**Note:** the live BOT_TOKEN was uploaded to chat during this session →
regenerate it in the Discord Developer Portal before any public use.

---

## May 24 2026 — New Training Set: Short Banter / Multi-Speaker / Off-the-Wall (Conversational Tricks & Textures, Personality LoRA)

**Files (training data, not code):** banter_single_dry_chatml_01–04,
banter_multispeaker_chatml_01–02, banter_multispeaker_interleaved_chatml_01–02,
banter_silly_riff/wtf/mixed_chatml_*, banter_offthewall_chatml_01–03,
banter_offthewall_multispeaker_chatml_01, DPO_Banter_Multispeaker_01–02,
DPO_Banter_Interleaved_01.

**Goal:** teach short, dry, Discord-style banter that (a) doesn't cave or get
steamrolled, (b) handles multiple tagged speakers, (c) rolls with off-the-wall
nonsense (one-word chaos, gross-outs) with dry wit — leaning into being an AI
when it lands. Always dry, never bitter, never prissy/scolding.

**Key design decisions:**
- Speaker tags appear ONLY in user turns, never in the model's output — so the
  model learns to READ `[Name]:`, never to WRITE it. Disposition transfers;
  format does not bleed.
- Single-speaker (no-tag) shards included deliberately so the dry-banter
  instinct is decoupled from the bracket format — fires in normal 1-on-1 HWUI
  chat too, not just group chats. This also neutralises tone-bleed risk.
- All fictional/throwaway speaker names (Jax, milo, Pidge, Rook, Bex, tanner,
  Dax, nori, Sib, Vic, lune, koa) spread evenly — no name overweighted (same
  principle as the never-hardcode-a-real-name rule).
- Responses are short (2–3 sentences), Discord-cadence, NOT paragraphs.
- All files under the 1024-token ceiling.

## May 23 2026 — Fallback Example Dialogue Silently Parsed to ZERO Turns (Label-Format Bug) — Root Cause of Sentence-Derailing & Early-EOS

**Files:** GPT-4o.example.txt (and any shared/global `*.example.txt`); diagnostic-only note on app.py stop-reason logging

**Symptom as user-reported:** local Helcyon (GPT-4o v4.3) producing
grammatically tangled sentences — clauses that start and never resolve,
"Yoda-ish" run-ons, especially in CLOSING sentences (e.g. "I'll be right
here for it—but if today you just want one move to take before you let
it sit a few days?"). Separately, numbered lists died at "1" then stopped
(early EOS). Both intermittent.

**Three red herrings ruled out along the way (documented so nobody
re-walks them):**
1. NOT the model / NOT the 1024-shard cutoff theory (rejected — long-ctx
   LoRA is merged; see memory note). Investigate HWUI only.
2. NOT a stop-token misfire — `get_stop_tokens()` correctly returns
   `["<|im_end|>", "<|im_start|>"]` for ChatML; `stopping_word` was empty
   on the bad runs, confirming a real sampled EOS, not a stop-string hit.
3. NOT sampler values per se. `frequency_penalty`/`presence_penalty`
   (added only for the OpenAI backend, then left on for local) were
   strangling numbered-list scaffolding into early EOS — setting both to
   0 fixed the LISTS. But zeroing them re-exposed the tangled-sentence
   problem that presence_penalty=0.1 had been MASKING. Masking, not
   fixing. The real cause was elsewhere. **Do not re-add freq/presence
   penalty to the LOCAL llama.cpp payload to paper over prose problems;
   they are OpenAI-native and hit repetitive list tokens bluntly on a 12B.
   If wanted for the OpenAI backend, gate them to that path only.**

**The real root cause — example dialogue never reached the model.** The
example_dialogue parser (`app.py` ~3355–3363) matches a line as a turn
boundary ONLY when the label before the colon equals the USER'S DISPLAY
NAME (`_user_label`, e.g. "Chris") or the CHARACTER'S NAME (`_char_label`,
e.g. "Gemma"). The shared `GPT-4o.example.txt` used literal `User:` and
`Assistant:` labels. "User" ≠ "Chris" and "Assistant" ≠ "Gemma", so:
- every USER turn failed to match → dropped;
- only ASSISTANT-labelled turns survived (and only if char happened to be
  named "Assistant", which none are — so those failed too);
- alternation-enforcement then discarded the non-alternating remainder.
Net: `🎭 Parsed example_dialogue → 0 fake turn(s)`. **The fallback example
dialogue had been silently contributing NOTHING for every character using
it.** With no style scaffolding, the model freewheeled at temp 0.8 and
intermittently overreached on ambitious closing constructions → the
tangled sentences.

**The fix — use `{{user}}:` and `{{char}}:` placeholder labels.** The
parser substitutes `{{user}}`→display name and `{{char}}`→character name
(app.py ~3338–3341) BEFORE matching, so the labels then match. This is
the same convention SillyTavern uses and is the ONLY correct format for a
SHARED/global example file (a global file can't hard-code one character's
name). Rewrote `GPT-4o.example.txt` with `{{user}}:` / `{{char}}:` labels
and two vetted genuine-4o samples (work-email vent + reciprocity/empathy).
Verified by simulating the exact parser: `User:`/`Assistant:` → 0 turns;
`{{user}}:`/`{{char}}:` → 4 turns (2 user, 2 assistant), correct
alternation.

**⚠️ EXAMPLE-FILE FORMAT RULES (label these clearly, do not "tidy"):**
- Shared/global `*.example.txt` MUST use `{{user}}:` and `{{char}}:`
  labels. Literal `User:` / `Assistant:` PARSE TO ZERO and silently
  disable the examples. This is invisible except for the
  `Parsed example_dialogue → N fake turn(s)` log line — **if that prints
  0, the examples are not working.**
- A per-character JSON `example_dialogue` may use the character's literal
  name (e.g. `Gemma:`) since that matches `_char_label` — but placeholders
  are safer/portable.
- Separators like `---` are NOT used by the parser (it splits on speaker
  labels and optional `<START>`). Don't add them expecting them to
  delimit turns.

**Example-content vetting standard (for swapping in real 4o shards):**
keep it genuine 4o (long, warm, sweary, British is fine) BUT screen for
(a) sentences that stack >2 pivots without resolving, (b) clauses that
convert to a question without closing the first clause (the exact derail
shape), (c) free-verse/line-broken fragmentation (teaches poetic layout),
(d) therapy-speak (violates GPT-4o.txt's "no therapy-speak"), (e) truncated
mid-thought turns. Don't let Claude REWRITE the samples (drifts them to
Claude cadence) — only gatekeep.

**Verified fixed:** at temp 0.8 / top_k 40, freq/presence penalty 0:
log shows `Parsed → 4 fake turn(s)` + `Injected 4 fake conversation
turn(s)`; numbered lists complete; closing sentences land cleanly; turn
ends correctly with `stop_type:"word", stopping_word:"<|im_end|>"` (clean
ChatML close) instead of the previous bad `stop_type:"eos",
stopping_word:""` (sampled mid-thought EOS).

**Minor diagnostic note (not fixed, low priority):** HWUI's
`🩺 STOP REASON` logger prints "unknown" because it looks for legacy
`stopped_eos`/`stopped_word` flags; this llama.cpp build reports the
reason in `stop_type` (+ `stopping_word`). Reading `stop_type` instead
would make stop diagnostics self-explanatory. Cosmetic only.

---

## May 22 2026 — Global Documents Get a UI (Repurposed from Manual Folder Drops)

**Files:** project_routes.py, templates/index.html

**Why:** the `global_documents/` keyword-injected pool (loaded by
`load_global_documents()` in app.py) had no UI — the only way to add a doc was
to hand-drop a file into the folder and remember to put a `Keywords:` line at
the top. Project folders, by contrast, had a full upload/list/delete UI. This
adds a dedicated UI for global documents so they can be uploaded, edited
(memory-editor style), and deleted in-app.

**Design decisions (confirmed with user):**
- **Projects kept fully intact.** This is a NEW dedicated "📚 Documents" modal,
  not a repurpose of the project-docs panel. Per-project chats, RP mode,
  instructions, and sticky project docs are untouched. Sticky was never a
  global-docs concept (global docs are always keyword-triggered).
- **Extract-to-text on import.** Uploads of `.pdf/.docx/.odt` reuse the existing
  `/parse_document` endpoint client-side: import → extract text → prefill the
  editor → save as `.txt`. Everything stored in `global_documents/` is now
  plain editable text. No new server-side upload/parse code.
- **Dedicated Keywords field.** The editor has a separate Keywords input,
  persisted as the leading `Keywords:` line on disk (the retrieval tag stripped
  before injection by `_extract_doc_keywords`). A doc saved with no keywords
  shows a visible ⚠️ "may never be retrieved" warning in the list — the untagged
  threshold in `load_global_documents()` is deliberately high, so a generic
  filename + no keywords rarely fires.

**Backend (`project_routes.py`, new "Global Documents" section on `project_bp`):**
- `GET /global_documents/list` — filename, keywords, body preview, editable flag.
  Non-text files dropped in manually are listed but flagged non-editable.
- `GET /global_documents/get/<filename>` — returns `{filename, keywords, body}`
  via `_split_keywords_line` (mirrors `_extract_doc_keywords`).
- `POST /global_documents/save` — writes `Keywords: …\n\n<body>`; `_safe_doc_name`
  sanitises and forces a `.txt`/`.md` extension; optional `original_filename`
  supports rename-on-edit (removes the old file).
- `DELETE /global_documents/<filename>` — basename-guarded against traversal.
- `GLOBAL_DOCS_DIR` resolves the same folder app.py uses (`dirname(__file__)`).

**Frontend (`templates/index.html`):** new `📚 Documents` sidebar button, a
`global-docs-modal` modeled on the Memory modal (list + inline edit + add panel
+ import), and the JS (`openGlobalDocsModal`, `loadGlobalDocs`,
`renderGlobalDocs`, add/edit/delete/import helpers).

**Untouched:** `load_global_documents()` in app.py (the whole point — retrieval
is byte-identical), the project-documents routes/UI, sticky docs, project
instructions, mobile.html (desktop-only for now).

⚠️ Flask restart required (Python edit in `project_routes.py`) + hard-reload the
page to pick up the new HTML/JS.

---

## May 22 2026 — Leading `[OOC: …]` Stage Direction Leaking Into Streamed Output

> **⚠️ EXTENDED by the May 24 `_strip_ooc_stream()` universal net (above).** This
> entry's opening-region guards are still LIVE and kept by design (belt-and-
> suspenders), but they only cover the OPENING of certain paths. The May 24 net
> handles mid-response position and the post-search/OpenAI/vision paths this fix
> never reached. Read both together.

**Files:** app.py

**The bug (as user-reported):** local Helcyon models (seen on the Grok
variant) intermittently emitted a stage direction as the FIRST line of
their reply, e.g. `[OOC: Dry sarcasm on — no mercy for conspiracy
theories you don't buy.]` then the real answer below it. Started
appearing "a couple of weeks ago" and grew "more and more" frequent.

**The bug (real cause):** NOT the model and NOT a bad LoRA merge —
training data is clean of OOC. The model was paraphrasing directive-style
text sitting near the generation point and emitting it as a bracketed
stage direction. The source varied per run: one run mirrored the Grok
card's `character_note`, another mirrored the EXAMPLE DIALOGUE instruction
("extract tone, rhythm, response length" → leaked as "[OOC: Speaking
style example match — dry wit… line breaks exactly where they'd land]").
HWUI then streamed this raw to the client. Pure pipeline issue; the model
was never touched.

**Why "more and more":** the dominant path for ordinary chat on a
search-enabled card is an UNFILTERED passthrough (see below), so every
normal turn on such a card streamed the leak straight through.

**First fix attempt (correct logic, WRONG path):** added a stateful
opening-guard to `_filtered_stream()`. That function only runs when web
search is OFF. The Grok card has `use_web_search: true`, so its turns
never reach `_filtered_stream` — they route through `_web_search_stream`.
The guard was installed on a road this model never drives. Verified by
reproduction: leak persisted unchanged after the first fix.

**The real fix — guard the no-search passthrough in `_web_search_stream`.**
The chat route splits on `use_web_search` BEFORE either stream generator
runs. With search enabled and no search trigger in the message,
`_web_search_stream` hit `if not _should_search:` and did a bare
`yield from stream_model_response(_run_payload)` at ~line 4697 — raw,
zero post-processing. Replaced that single line with a chunk loop
carrying the same opening-guard logic used in `_filtered_stream`:
buffer the opening region, drop a leading block only if it matches
`^[\s*OOC`, then disable the guard permanently so the rest of the
response streams untouched.

**⚠️ DO NOT revert / DO NOT consolidate — two guards in two functions
BY DESIGN.** The OOC guard exists in BOTH `_filtered_stream` (search OFF)
and `_web_search_stream` ~line 4697 (search ON, no trigger fired). They
are separate because the route splits on `use_web_search` upstream of
both. Merging them into one shared helper silently reopens the leak on
whichever path loses the guard. Same spirit as the existing duplication
warning on the `_web_search_stream` / `_web_search_stream_openai` pair.

**⚠️ DO NOT remove the whitespace-buffering line.** Inside the guard,
`if not _stripped_lead: continue` (keep buffering on pure whitespace) is
load-bearing. After `<|im_start|>assistant\n` the first chunk almost
always carries a leading newline/space; without this line the guard
releases that whitespace and disables itself BEFORE the `[OOC:` ever
arrives, defeating the entire feature. Do not "simplify" it away.

**Safeguards built in:** (1) guard is anchored to the opening region
only and disables permanently once resolved — mid-response brackets are
never touched; (2) a `_PROTECTED` whitelist short-circuits before the
OOC check so the model's real leading tags (`[MEMORY ADD:`,
`[WEB SEARCH:`, `[WEB SEARCH RESULTS`, `[CHAT HISTORY RESULTS`, `[END`)
are never held or dropped; (3) end-of-stream flush drops a still-open
OOC fragment if generation was truncated mid-bracket, else releases real
text so nothing is lost. `_filtered_stream`'s guard uses `[True]`/`[""]`
list cells (nested-closure scope); the `_web_search_stream` guard uses
plain locals (flat loop) — both correct for their scope.

**Implementation note:** `_filtered_stream` uses `_re3_inner`;
`_web_search_stream` uses `_re`. Each guard matches its function's local
regex-module convention — do not cross them.

**Known unguarded sibling (deliberate, documented):** the post-search
re-prompt loop in `_web_search_stream` (~line 4923) still streams raw.
Low risk — the model is mid-task answering with injected search results
and is unlikely to open with a stage direction. Left unguarded to keep
this change tight. **If `[OOC: …]` ever appears AFTER a web-search
result, guard line 4923 with the identical logic.**

**Verified:** Grok moon-landing repro regenerated multiple times,
no OOC leak, real reply intact. Console fires
`✂️ [_web_search_stream] Dropped leading OOC block:` on the correct path.

---

## May 21 2026 — Chat-File Parser Drops Character When Missing From `characters/index.json`

**Files:** chat_routes.py

**The bug (as user-reported):** with a custom persona active (e.g.
"Ellie"), the on-disk chat reloads with every user-side message
re-rendered as the *character's* name and avatar (e.g. "Andromeda"). The
message text is unchanged. Default persona was unaffected.

**The bug (real cause, verified against a real file on disk):** not a
persona-side bug at all — a **character-side** bug. The parser at
`chat_routes.py:/chats/open` reads `characters/index.json` as the only
source of character identity. When that index is *incomplete* (manual
import of a character, character `.json` file present but never
registered, partial sync) the chat's own character isn't recognised as
"a character", and every line spoken by that character falls through
the speaker-pattern check:

- **Untimestamped opener** (`Andromeda: Hey!`) → no signals match,
  `current_role` is still `None`, line silently dropped. The opener
  disappears entirely on reload.
- **Timestamped reply** (`[ts] Andromeda: Oof…`) → no signals match,
  line falls into "Continue current message" and gets *appended* to
  whichever turn was being built — typically the preceding user
  (persona) message. Andromeda's reply text gets stuffed inside the
  Ellie turn.

The visible symptom — "user's messages re-render with the character's
name and avatar" — is the model bubble that DOES render: the next
user/Ellie line gets recognised, but the prior Andromeda content has
been swallowed into another turn, leaving the user with the impression
that their bubble has reverted to the character. Default persona
escaped this only because the chats tested under "Default" happened to
use a character that *was* in the global index.

**Diagnostic dead-end and reversal:** the first fix attempt added an
`is_timestamped_unknown` heuristic — "any timestamped speaker line that
isn't a known character is a user turn." Built against synthetic
fixtures with an unregistered *persona*; verified on synthetic data,
not on a real on-disk chat. Against the real file
(`projects/Journal/chats/Andromeda - Audition Nerves Tips - May 21.txt`,
character `Andromeda` missing from `characters/index.json`), that
heuristic mis-classified Andromeda's timestamped replies as USER turns
with `speaker="Andromeda"` — which is itself the user-reported
symptom. **Reverted.** Lesson: ground-truth on a real file before
trusting a synthetic test.

**The real fix — seed the filename-derived character into the
recognition list.** The chat filename prefix is the authoritative
source for which character this chat belongs to: the save side
(`/chats/save` at `chat_routes.py:553-555`) and `auto_name_chat`
(`chat_routes.py:466-481`) both use it. `/chats/open` did not. At
`chat_routes.py:182-209` and the mirrored block in `branch_chat`
(`chat_routes.py:758-770`), the parser now:

1. Loads `characters/index.json` as before.
2. Walks the chat filename's `" - "`-separated prefix candidates from
   longest to shortest, picking the longest that already matches the
   global index (handles characters whose names contain `" - "`).
3. Falls back to the first dash-segment if nothing matched.
4. Appends that name to `available_characters` if not already there.

This mirrors the frontend's `extractCharacterFromFilename`
(`templates/index.html:2427-2476`). The recognition list now always
contains the chat's own character regardless of global-index state, and
the existing speaker-pattern check classifies Andromeda's lines as
assistant correctly.

⚠️ Chat file format unchanged. `_format_chat_messages` and `/chats/save`
are untouched. No migration, no risk to existing chats.

**Verified against the real file on disk:**

Real bytes of the chat file (truncated for readability):
```
Andromeda: Hey! What's on your mind?

[2026-05-21T16:00:48.667Z] Ellie: Hey. I've got an audition…

[2026-05-21T16:01:11.599Z] Andromeda: Oof, an actual audition?…
```

Parser state before fix: `available_characters=['Helcyon']`. Andromeda
unrecognised — opener dropped, reply absorbed.

Parser state after fix: `available_characters=['Helcyon', 'Andromeda']`
(filename-derived seed added). Real-file round-trip now returns 3
messages, correctly alternating `assistant(Andromeda) → user(Ellie) →
assistant(Andromeda)`.

**Untouched:**
- `_format_chat_messages` — save format unchanged.
- Desktop renderer (`templates/index.html:2700-2716`) — already correct;
  with the parser feeding the right speaker name, the existing avatar
  resolution chain (`userImageCache[speaker]` → `activeUserImage` →
  `default.png`) lands on the right image.
- Mobile renderer (`templates/mobile.html:802-806`) — same; `msg.speaker`
  flows straight to `addBubble`.
- `inside_doc` attached-document gate — still load-bearing for
  paste-transcripts (separate May 21 fix).
- Avatar persistence on disk — not added. Speaker name alone is enough.
- The `is_timestamped_unknown` heuristic from the first failed attempt
  is gone; do not reintroduce without ground-truth from a real file.

- ⚠️ The filename-derived character seed at `chat_routes.py:182-209`
  (and the mirrored block in `branch_chat` at `chat_routes.py:758-770`)
  is load-bearing — without it, any chat whose character is missing from
  `characters/index.json` will lose its opener and have the character's
  replies absorbed into adjacent turns on disk reload. Do not narrow it
  to "trust the global index" — the bug exists precisely because the
  global index isn't always in sync with the per-character JSON files.

⚠️ Flask restart required (Python edit in `chat_routes.py`).

---

## May 21 2026 — Paste-Transcript: Two Bugs Fixed (Framing + Disk Shredder)

**Files:** app.py, chat_routes.py

Pasted transcripts (via the 📋 Paste Transcript modal added on May 19) had
two compounding bugs. Both fixed in this pass.

### Bug 1 — model ignored the pasted content on first send

The `[ATTACHED DOCUMENT: …]…[END ATTACHED DOCUMENT]` wrapper had **no
instructional framing whatsoever**. Compare to every other piece of
injected content:

| Injection | Framing |
|---|---|
| Project documents (app.py:2581-2589) | `═══ PROJECT DOCUMENTS ═══` banner + "The following is reference material:" + faithfulness suffix |
| Web search results (app.py:~4129) | "IMPORTANT: The above are real excerpts … Use them to answer the user's question accurately." |
| Chat-history search results (app.py:~4922) | "IMPORTANT: The above are real excerpts from past conversations. Use them to answer naturally." |
| Session summary tail (app.py:3386-3394) | `[Most recent session with <user>, <when>]:` … `[End of recent session — continue naturally from where you left off]` |
| **ATTACHED DOCUMENT** | **Nothing — just the bare marker.** |

The OOC packets (style / post-history / system-directive) sit directly
above the doc block in the final user turn, telling the model how to be
in character. A character-RP-tuned model reading `<OOC stuff…> [ATTACHED
DOCUMENT…] <typed q>` skims past the doc as ambient noise and responds
to the OOC framing instead. Bare marker = no attention cue.

**Fix:** at prompt-build time (app.py, right after the existing OOC
packet insertion at ~3650), when `_attached_doc_present` is True, append
a one-line directive at the END of the last user turn — the strongest
attention slot, same principle as the session-summary tail injection:

> `[The user attached the above document as reference material. Read it
> and use it to inform your reply, but do not continue, role-play, or
> respond as any character mentioned inside it.]`

⚠️ Append happens at prompt-build time, NOT in `active_chat` — the
directive stays out of saved chat history and off the user's screen. It
is one-shot per turn and applies only when this turn carries a doc.

### Bug 2 — disk round-trip shredded the pasted transcript

The on-disk chat-file format has no awareness of `[ATTACHED DOCUMENT: …]`
markers, and the line-walking parser at `/chats/open` actively destroyed
them. Save side (`chat_routes.py:_format_chat_messages`) writes the user
turn as plain `[timestamp] User: <content>` text, with the doc content
on subsequent lines. Load side walks line by line and checks every
non-leading-space line containing `:` against the known-characters /
valid-users / generic-"User" lists. **Any line inside the pasted
transcript that matched `<character>:` or `User:` triggered a new
message boundary** — splitting the user's single message into many bogus
turns matching the pasted transcript's speakers.

This is precisely the original bug the paste-transcript feature was
supposed to *prevent*: "the model recognised those timestamped turns as
conversation history and continued them" (changes.md May 19).
First-send was fine (in-memory loadedChat survives), but the moment
anything reloaded the chat from disk — page refresh, character switch
and back, chat-list reopen — the doc was shredded into fake turns and
re-injected as real chat history.

**Fix:** parser at `chat_routes.py:/chats/open` now tracks an
`inside_doc` boolean. While inside a `[ATTACHED DOCUMENT: …]…[END
ATTACHED DOCUMENT]` span, the speaker-pattern check is bypassed and
every line is appended verbatim to the current message's content. Span
state is updated in two places — inside the speaker-detection branch
(handles the entry case "User: [ATTACHED DOCUMENT: …]" on the same line)
and at the bottom of the loop (handles standalone `[END ATTACHED
DOCUMENT]` lines and bare-marker lines inside the doc).

### Verified

- Both files parse cleanly.
- Synthetic round-trip test against the exact failure case (pasted HWUI
  transcript with embedded `Helcyon:` and `User:` lines, followed by a
  typed question, followed by a real Helcyon reply, followed by a real
  user "Thanks"): parser now returns 3 messages (user-with-doc-and-
  question, real-assistant-reply, real-user-thanks) instead of 6+ from
  the shredder. The pasted transcript's internal `Helcyon:` / `User:`
  lines correctly stay as content of message 0.

### Untouched

- `wrapAttachedDocuments` in templates/index.html — unchanged. Frontend
  still produces bare `[ATTACHED DOCUMENT: …]…[END ATTACHED DOCUMENT]`
  blocks; framing is added server-side at prompt-build time.
- `_format_chat_messages` in chat_routes.py — unchanged. Save format is
  identical to before; no migration needed for existing chats on disk.
- File attachment path (handleDocumentAttach via /parse_document) —
  benefits from both fixes automatically since it uses the same
  attachedDocuments pipeline.
- Image attachment path — untouched.
- Mobile templates — untouched (paste-transcript is desktop-only).

- ⚠️ The parser's `inside_doc` gate and the prompt-build directive
  append are both load-bearing. Do not remove either thinking the other
  is sufficient — the parser fix prevents history corruption on reload;
  the directive fix gets the model to engage with the doc on first send.
  They address different symptoms of the same underlying gap (the
  ATTACHED DOCUMENT wrapper was structurally incomplete).

⚠️ Flask restart required (Python edit in both app.py and chat_routes.py).

---

## May 21 2026 — Session Summary Not Injecting After Opening-Line Greeting

**Files:** app.py

**The bug:** user starts a new chat, the character shows an opening-line
greeting, user asks "remember what we talked about in the last chat?" — the
session summary did not inject. Log showed `🧠 _is_new_chat: False
(1 assistant msgs in active_chat)`. Model had no recent-session context and
confabulated.

**Root cause — the `is_opening_line` flag does not survive the disk round-
trip.** The flag is set in-memory when the opening line is added
(`utils.js:207-211` desktop, `mobile.html:867` mobile), but the on-disk
chat-file format has no slot for it:

- `chat_routes.py:_format_chat_messages` serialises each message as
  `[<timestamp>] <speaker>: <content>` — flag silently dropped.
- `/chats/open`'s line-walking parser at `chat_routes.py:223,253` builds
  entries with only `{role, content, speaker, timestamp}` — can't
  reconstruct the flag.

Autosave fires immediately after the opening line displays (`utils.js:220` —
`autoSaveCurrentChat()` runs on the same frame as the push). So the moment
the user does anything that reloads the chat from disk — page refresh,
character switch, chat-list click — the in-memory flag is gone and the
loaded message is just an unmarked assistant entry.

`_is_new_chat` then saw `[unmarked_opener, new_user_msg]` → 1 assistant
message without the flag → False → both session-summary injection sites
(cold `YOUR OWN MEMORY OF RECENT SESSIONS` block at app.py:2705-2738 and
hot tail-append at app.py:3363-3397) silently skipped.

**The contradictory `🆕 New conversation` log line** at app.py:3523 was not
a second bug — it's a different check at a different pipeline stage. The
leading-assistant stripper at app.py:3068-3076 drops the opener
unconditionally before app.py:3081 recounts assistant messages, so the post-
stripper check correctly sees zero. The pre-stripper `_is_new_chat` check
didn't benefit from that transformation. Both logs become consistent once
`_is_new_chat` is fixed.

**The fix — positional fallback in `_is_new_chat` (app.py:2786-2840).** A
single assistant message at position 0 of `active_chat` (before any user
message) is structurally an opening-line greeting or project RP opener
regardless of whether the explicit flag survived. Detection now uses two
signals in order:

1. **Explicit flag** (`is_opening_line=True`) — primary signal for in-
   memory sessions. Unchanged.
2. **Positional fallback** — `active_chat[0]` is an assistant message AND
   there's only one assistant message total. Engages when the flag is
   missing post-disk-load.

The diagnostic log now names which signal fired, so the post-disk-load
case is visible when debugging:

- `🧠 _is_new_chat: True (no assistant messages)` — zero asst path
- `🧠 _is_new_chat: True (is_opening_line flag)` — in-memory session
- `🧠 _is_new_chat: True (positional fallback — opening-line flag lost in
  disk round-trip)` — post-reload case
- `🧠 _is_new_chat: False` — real exchange

**Why not change the file format instead?** Persisting the flag would mean
modifying `_format_chat_messages`, the `/chats/open` parser, and any
downstream reader of chat-file text (`do_chat_search`, the session-summary
generator). Larger blast radius for the same end result — the positional
check is a 1:1 stand-in for the flag in this specific context (any
assistant at position 0 is, by construction, pre-conversation).

**Verified:** `app.py` parses cleanly. Helper tested against 9 edge cases —
empty chat, opener with flag, opener without flag, opener + user (with and
without flag), user-only, user + assistant (real reply), opener + user +
assistant (real exchange started), full multi-turn exchange — all classify
correctly. The failure-case input `[opener_noflag, user_msg]` now resolves
to `(is_new=True, reason='positional')`.

**Untouched:**
- Hot tail injection at app.py:3363-3397 — `_recent_session_summary` now
  populates correctly because `_is_new_chat` is correctly True.
- Cold block at app.py:2705-2738 — same gate, now correct.
- Leading-assistant stripper at app.py:3068-3076 — its positional drop
  logic is independent and was already robust against missing flag.
- Post-stripper `🆕 New conversation` log at app.py:3520-3523 — left as-is;
  the contradiction with the pre-stripper line resolves on its own now
  that `_is_new_chat` is correct.
- Chat-file format (`_format_chat_messages`, `/chats/open`) — unchanged.
  No migration, no risk to existing chats on disk.

- ⚠️ The positional fallback in `_is_new_chat` is load-bearing — the
  `is_opening_line` flag does NOT survive the disk round-trip. Do not
  remove the "first message is assistant" check assuming the flag is
  sufficient.

⚠️ Flask restart required (Python edit).

---

## May 21 2026 — Chat-Search Trigger Inverted: Search Now Requires an Explicit Verb

**Files:** app.py

**The bug:** asking "do you remember what we were talking about last time?" did
not surface the recent-session summary already injected at the system-block
tail — instead, cross-chat search fired and injected unrelated raw chat
snippets from old chats at depth-0, which dominated the response.

**Two defects compounded:**
- The trigger was **(recall verb + cross-session marker within 80 chars)**, so
  `"remember…last time"` was treated as a *search* request rather than a
  *recall* request. The user's phrasing was indistinguishable from "go search
  the archives" under the old rule, even though the intent is "use what you
  already know".
- The `do_chat_search` stopword set caught `talked`, `spoke`, `speaking`,
  `last`, `time`, etc., but missed the **`-ing` forms** (`talking`,
  `remembering`, `discussing`) and a few common copulas (`were`). So the
  preamble-stripped query `"what we were talking about last time"` reduced
  to keywords `['were', 'talking']` — both of which appear in nearly every
  chat — and the co-occurrence scorer returned matches from arbitrary
  unrelated conversations.

**The fix — invert the gate. Search must EARN its trigger via an explicit
search verb; recall is the safe default.** Three new module-level constants
near the existing `_CHAT_RECALL_VERBS` / `_CHAT_CROSS_SESSION_MARKERS`:

- `_CHAT_SEARCH_VERBS` — explicit search/find verbs: `search [our/the/my]
  chats/history/conversations`, `search for`, `find that/the/our chat`,
  `find where/when we`, `look up/for/through`, `dig up/through/out`,
  `trying to find`, `locate`, `go back and find/check/look`, `pull up
  that/the/our`, `check our chats/history`.
- `_RECALL_PHRASES` — recall phrasing that **suppresses** search when no
  search verb is present: `remember what/when/that/the/how/our/we/you/i/us/
  talking`, `(do you) recall …`, `last chat/time/session/conversation`,
  `(our) last/previous/earlier chat/session/…`, `the other day/time/night/
  week`, `where we left off`, `pick up where/from`, `previously`,
  `earlier (today)`, `a while/bit ago/back`.
- `_classify_chat_search_intent(user_msg)` — returns `(should_search,
  suppressed_by_recall)`. Rule: if recall phrasing matches and no search
  verb matches, return `(False, True)` — suppressed. Else return
  `(has_search, False)`.

Both call sites updated to use the helper:
- **Memory-skip site (app.py ~2863)** — character memory is now skipped
  *only* when chat search will actually fire. Recall phrasing leaves
  character memory injection enabled (it's per-character persistent state,
  unrelated to chat search). Suppression logs
  `🧠 Recall phrasing detected, no search verb — suppressing chat search,
  relying on session summary` when `diag_verbose=true` in settings.json.
- **Primary trigger site (app.py ~4095)** — same helper, same suppression
  log path. The two sites are guaranteed to agree because they're
  classified by the same helper — preserves the original "lockstep"
  invariant.

The old `_CHAT_SEARCH_TRIGGER_RE` regex is left in place; it's no longer
used to gate behaviour but kept as a generic recall-detector in case a
future site wants it.

**Stopword backstop (`do_chat_search`):** even with the new gate, if a
search does fire on phrasing that slipped through, generic words like
`talking`, `were`, `remembering`, `discussing`, `chatting`, `asking`,
`wondering`, `thinking` must not become match keywords. Added the `-ing`
forms and the missing copulas; the gate is the primary protection, this is
the second line of defence.

**The model-emitted `[CHAT SEARCH: …]` tag path (app.py ~4915-4919) is
unchanged.** That path is model-driven (tag-emission gated at training);
user-phrasing classification doesn't apply to it.

**Session-summary injection is untouched** — both the cold block at
app.py:2705-2738 and the hot tail-append at app.py:3363-3397. Per the
existing tail-position note (changes.md), that injection is load-bearing
for attention weighting and must not move.

**Verified:** `app.py` parses cleanly. Helper tested against 13 cases
(recall queries → suppressed; explicit-search queries → fire; neutral
queries → default behaviour) — all pass. Failure-case query
`"do you remember what we were talking about last time?"` correctly
classifies as `(should_search=False, suppressed_by_recall=True)`.

- ⚠️ Chat search requires an explicit search verb by design. Recall
  phrasing is the safe default and must NOT trigger search. Do not revert
  to the old recall-verb-as-trigger logic — it caused unrelated old-chat
  snippets to hijack recall responses.

⚠️ Flask restart required (Python edit).

---

## May 20 2026 — F5-TTS: HWUI → "Helcyon Web You Eye" Substitution Fixed

**Files:** f5_server.py

**The bug:** the literal string `HWUI` (and every case variant — `Hwui`,
`hwui`) was reaching `tts.infer()` unsubstituted, so F5 read the letters
literally and the spoken output was wrong.

**Trace of text through `/tts_to_audio` → `tts.infer()`:**
1. `tts_to_audio()` (f5_server.py:481) reads `text` from JSON and calls
   `clean_text(text)` (f5_server.py:484).
2. Inside `clean_text` (f5_server.py:356), the ALL-CAPS → Title Case pass
   at f5_server.py:397 runs first:
   `re.sub(r'\b[A-Z]{2,}\b', lambda m: m.group(0) if m.group(0) in
   _known_acronyms else m.group(0).title(), text)`. `HWUI` is **not** in
   `_known_acronyms` (the set at f5_server.py:371–396 covers reserved tech
   / business / medical acronyms; HWUI was never added), so a user-typed
   `HWUI` is Title-Cased to `Hwui` here.
3. Later, the HWUI substitution at f5_server.py:437 runs:
   `re.sub(r'\bHWUI\b', 'H-W-U-I', text)`. **Case-sensitive pattern** — no
   `re.IGNORECASE` — so it never matched the now-`Hwui` form, and equally
   never matched a user-typed `hwui`. The substitution silently no-op'd.
4. The unmodified text (still containing `Hwui` / `hwui` / occasionally
   `HWUI` if the Title-Case pass missed it for any reason) flows into
   `tts.infer(gen_text=text, …)` at f5_server.py:515.

**Two defects in one line:**
- Case-sensitive `\bHWUI\b` couldn't catch the Title-Cased `Hwui`
  produced upstream, nor a user-typed `hwui`.
- Even when it *did* match (an `HWUI` that somehow survived the Title-
  Case pass), the replacement was `'H-W-U-I'` — forced letter-spelling —
  not the intended sentence form `'Helcyon Web You Eye'`.

**The fix:** at f5_server.py:437, swap the replacement to
`'Helcyon Web You Eye'` and add `flags=re.IGNORECASE`. The `IGNORECASE`
flag is load-bearing — without it the Title-Case pass at line 397 will
keep neutralising the match for any all-caps input. Added a comment above
the line explaining the ordering interaction so a future edit doesn't
strip the flag back off thinking it's redundant.

**Why not add `HWUI` to `_known_acronyms` instead?** That would preserve
the all-caps `HWUI` so the case-sensitive line could match it, but it
still wouldn't catch user-typed `Hwui` or `hwui`. `IGNORECASE` covers all
cases in one step and keeps `_known_acronyms` reserved for genuine
spell-out acronyms.

**Verified:** grep for `🔍` in `f5_server.py` returns no matches — there
were no debug print statements to strip. `clean_text` parses cleanly.
The substitution now runs after the Title-Case pass regardless of input
casing.

- ⚠️ DO NOT revert — `re.IGNORECASE` on the HWUI substitution is required.
  The ALL-CAPS → Title Case pass at f5_server.py:397 runs before this
  line and converts `HWUI` → `Hwui`, so a case-sensitive pattern will
  silently fail again. Either keep `IGNORECASE` here, or move the HWUI
  substitution *above* the Title-Case pass — but not both removed.

⚠️ Flask restart required (Python edit).

---

## May 20 2026 — MEMORY TAGS Instruction Tightened (Single-Tag-Only)

**Files:** utils/session_handler.py

- MEMORY TAGS instruction tightened to mirror WEB SEARCH's structure: "your entire response must be a single tag and nothing else".
- Added explicit prohibitions against conversational acknowledgement, describing the save, inventing context blocks, or producing any structured output.
- Clarifies that the system handles the user-facing save confirmation, not the model.

**Reason:** Helcyon-4o was producing elaborate multi-block responses
(acknowledgement + fake context blocks + invented search results) when asked
to save to memory. The instruction was strong on format but weak on
stop-completely / response-shape exclusivity. This mirrors the WEB SEARCH
instruction's pattern which works reliably for the same reason (stop-after-tag
directive).

- ⚠️ The "your entire response must be a single tag and nothing else"
  directive is required. Models will pattern-complete to "respond
  substantively" by default and produce verbose output instead of a clean tag.
  Do not soften this directive.

⚠️ Flask restart required (Python edit).

---

## May 20 2026 — OpenAI Path Now Supports Any OpenAI-Compatible Endpoint

**Files:** app.py, templates/config.html, settings.json

**What this opens up.** The OpenAI cloud path is now a generic OpenAI-
compatible client. Pointing it at a different `openai_base_url` lets HWUI
talk to Anthropic (`https://api.anthropic.com/v1`), xAI/Grok
(`https://api.x.ai/v1`), OpenRouter (`https://openrouter.ai/api/v1`),
Together, Groq, Mistral, Fireworks, and any local OpenAI-compatible server
(LM Studio, vLLM, …) without touching any other code. All existing OpenAI-
path infrastructure — `_web_search_stream_openai`, the look-ahead tag
buffering, the streaming protocol, the bearer-token auth header — is reused
unchanged.

**Backend changes (`app.py`):**
- New helper `get_openai_base_url()` adjacent to `get_brave_api_key()`
  (app.py:1256 area). Returns the URL up to `/v1`, stripped of trailing
  slashes. Silently falls back to `https://api.openai.com/v1` when the
  field is missing, empty, or `settings.json` is unreadable — older
  settings files round-trip cleanly without intervention.
- `stream_openai_response` (app.py:1801 site) now calls
  `f"{get_openai_base_url()}/chat/completions"`. This is the single
  request site for both phases of `_web_search_stream_openai` (initial
  generation + search re-prompt), so updating this one place covers the
  whole web-search flow automatically.
- `/get_openai_models` (app.py:6478 site) now calls
  `f"{get_openai_base_url()}/models"`. Most compatible providers expose
  this; ones that don't return a non-200 which surfaces as a normal error
  in the UI (users on those providers type the model name into the
  dropdown directly — `_setOpenAIModelSelect` already adds unknown
  saved-model names as options on load).
- `/get_openai_settings` and `/save_openai_settings` carry the
  `openai_base_url` field. GET resolves missing/empty values through the
  helper so the UI shows the actual default on first load. POST strips
  trailing slashes and writes the OpenAI default back to disk when the
  field is empty, so the second load has it explicit.

**Frontend changes (`templates/config.html`):**
- New `<input id="openai-base-url">` above the API key field, with
  placeholder `https://api.openai.com/v1` and a sub-label listing
  Anthropic / xAI / OpenRouter examples in `<code>` boxes.
- `loadOpenAISettings()` populates the input from `data.openai_base_url`
  (falling back to the OpenAI default).
- `saveOpenAISettings()` reads the input, strips trailing slashes
  client-side as well (belt-and-braces against doubled slashes), and
  includes it in the POST body alongside key + model.

**Settings file (`settings.json`):**
- New `openai_base_url` field inserted after `openai_model`, default
  `https://api.openai.com/v1`. Existing settings.json files without this
  field continue to work — `get_openai_base_url()` defaults them silently
  and the first UI save persists the explicit value.

**Default behaviour is byte-equivalent to before.** With
`openai_base_url=https://api.openai.com/v1` (the default for fresh installs
and the fallback for older configs), every OpenAI request goes to exactly
the same URL as before this change.

**Known limitation — model dropdown is OpenAI-shaped.** The 🔄 Fetch
button hits `{base_url}/models` and parses the OpenAI-style response
(`{data: [{id: "..."}, …]}`). Most compatible providers return that
shape, but not all. The chat-model filter (`gpt-*`, `o1`, `o3`, …) is
also OpenAI-flavoured — a Fetch on Anthropic/Grok/etc. will return a
list that the filter then empties. **Workaround:** users on non-OpenAI
providers should type the model name (`claude-opus-4-7`, `grok-4`,
`anthropic/claude-opus-4-7`, etc.) into the dropdown manually — the
existing `_setOpenAIModelSelect()` adds any saved model to the list on
load, so once saved it sticks. Polishing the fetch to be provider-aware
is out of scope for this task (post-launch nice-to-have).

**Other things deliberately not touched:**
- `_web_search_stream` (local function at app.py:4243) — byte-identical.
- `_web_search_stream_openai` body — unchanged. The two URL sites it
  ultimately hits both live inside `stream_openai_response`, which now
  routes through the helper.
- Vision path, Jinja messages-API path — unchanged (still Phase 2).
- `mobile.html`, system prompts — untouched.

**Verified:**
- `app.py` parses cleanly.
- `grep` for `api.openai.com` in `app.py` returns only: the helper's
  default-fallback string, the GET-route default, the SAVE-route empty-
  field default, and three comments. **Zero hardcoded request URLs
  remain.**
- Local `_web_search_stream` at app.py:4243 starts identically to before.

- ⚠️ The OpenAI path is now a generic OpenAI-compatible client. Do NOT
  add hardcoded `api.openai.com` references anywhere — always go through
  `get_openai_base_url()`. New routes that hit OpenAI-style APIs must use
  this helper, or non-OpenAI providers will silently break.

⚠️ Flask restart required (Python edit) + the config page must be
hard-reloaded to pick up the new HTML/JS.

---

## May 20 2026 — Polish: OpenAI Web Search Path No Longer Leaks the Tag

**Files:** app.py

**The polish:** with the new `_web_search_stream_openai()` wrapper in place,
the `[WEB SEARCH: …]` tag was briefly visible to the user before the wrapper
halted and re-prompted. The tag prefix (`[WEB `, `[WEB SEARCH`, …) flashed up
as text before being replaced by the search results.

**Why it happened — and why the local path "doesn't have this issue".** The
original OpenAI implementation copied the local path's pattern exactly:
yield each chunk live, then check the rolling buffer for the full tag on the
next loop iteration. That pattern only works when the entire tag arrives in a
single chunk — if it's split across deltas (the streaming-API norm), the
prefix has already been yielded before the closing `]` arrives. The local
`_web_search_stream` fallback (app.py ~4508-4528) has the **same theoretical
bug**, but rarely fires in practice: Helcyon doesn't self-emit the tag — the
upstream explicit/ambiguous regex + intent gate matches user input first and
the search fires before the model ever runs. GPT-4o on the OpenAI path is the
opposite — it self-emits the tag every turn, so the leak is visible and the
bug had to be fixed there.

**The fix — look-ahead buffering (OpenAI path only).** `_web_search_stream_openai`
now holds back any text after an unclosed `[` in the rolling buffer:
- A small helper `_safe_yield_end(buf, start)` returns the position of the
  first unclosed `[` at-or-after the already-yielded watermark, else the
  buffer length. Anything up to that index is safe to release; anything after
  is held back because it might be the start of a tag-in-progress.
- The wrapper tracks `_yielded_chars` so it never re-yields content.
- When the full `[WEB SEARCH: …]` regex matches, the wrapper yields anything
  before the tag's `[` (usually nothing — that `[` was the unclosed bracket
  we were already holding back) and drops everything from `[` onward.
- When a non-tag bracket closes (e.g. markdown link `[click](url)`), the
  whole bracketed span releases on the next chunk — perceptible delay is one
  delta, so a few tens of ms.
- When the stream ends with no tag, the held-back tail is flushed so partial
  brackets like a never-closed `[foo` aren't silently dropped.

**Why not a fixed-length tail buffer.** A naive `_TAIL_LEN = 120` would still
leak the prefix of a long tag (`[WEB SEARCH: <120+ char query>]` is plausible
when the model writes a verbose query). Tracking unclosed-`[` makes the
holdback length data-driven and handles tags of unbounded length.

**Edge cases verified by code-trace:**
- `use_web_search=False`: never enters the wrapper; raw `stream_openai_response`
  unchanged.
- `use_web_search=True`, no tag emitted: every bracket eventually closes (or
  the stream ends → final flush). Full response reaches the client; nothing
  lost.
- `use_web_search=True`, tag emitted: tag never reaches the client at any
  visible point. Search runs; re-prompt streams normally.
- Partial tag prefix that doesn't complete (`[WEB-RELATED ARTICLES]`): the `]`
  arrives and the whole bracketed span releases — regex doesn't match
  (no `:` after `WEB`), so it's treated as ordinary text.
- Markdown links (`[label](url)`): briefly held back until `]`, then released.
  Imperceptible delay.
- Multiple brackets in one response (`Look at [link1] then [WEB SEARCH: x]`):
  `[link1]` released normally, `[WEB SEARCH: x]` matched and dropped, prior
  text yielded cleanly.

**Verified:** `app.py` parses cleanly. `_web_search_stream` (local function
at app.py:4163) is byte-identical to before this polish — only
`_web_search_stream_openai` was modified. No new imports; no new globals.

- ⚠️ DO NOT remove the look-ahead buffering from
  `_web_search_stream_openai` thinking the local path "works without it" —
  the local path only avoids the leak by NOT firing the tag-fallback branch
  in practice. On the OpenAI path that branch is the hot path; the buffering
  is load-bearing.

⚠️ Flask restart required (Python edit).

---

## May 20 2026 — OpenAI Cloud Path Now Detects [WEB SEARCH: …] Tags

**Files:** app.py

**The bug:** GPT-4o on the OpenAI API backend emits `[WEB SEARCH: …]` tags as
trained, but the tags rendered verbatim in chat instead of triggering an actual
search. Discovery report confirmed: the tag detector and re-prompt logic live
inside `_web_search_stream()` (nested in `chat()`, near app.py ~4163), which is
only reachable from the raw ChatML `/completion` path. The OpenAI cloud branch
returned `stream_openai_response(...)` raw — no wrapper, no detector, no
follow-up generation. Same gap exists on the vision and Jinja messages-API
paths (see "Phase 2" note below).

**The fix — parallel implementation, intentional duplication.** Added a new
top-level function **`_web_search_stream_openai()`** (app.py ~1871, placed
right after `stream_openai_response()`). It mirrors the structure of
`_web_search_stream()`'s tag-fallback branch but adapted for OpenAI's
`/v1/chat/completions` (messages array, not ChatML string):
- Phase 1: stream initial OpenAI response live, accumulate a rolling buffer,
  watch for `r"\[WEB SEARCH:\s*(.+?)\]"` (same regex as the local path).
- On match: flip `abort_generation = True` to close the underlying HTTP stream
  cleanly inside `stream_openai_response`, then break out.
- Phase 2: call `do_search(query)` — shared helper (Brave → DDG fallback),
  unchanged.
- Phase 3: build `augmented_user_msg` using the **same text template** as the
  local path (lines 4351-4364 of the old layout) — `[WEB SEARCH RESULTS FOR …]`
  block, identical IMPORTANT instruction copy. Zero-results fallback message
  also mirrored verbatim.
- Phase 4: rebuild messages array, strip stale `WEB SEARCH RESULTS` /
  `CHAT HISTORY RESULTS` blocks from prior user turns (same hygiene as local),
  replace last user turn with the augmented version.
- Phase 5: send follow-up `stream_openai_response` call with augmented
  messages; stream response; append source-link tail (same `<a href …>🔗
  Source: …</a>` markup as the local path).

**Wiring at the OpenAI return point** (app.py ~3819): the OpenAI branch now
reads `char_data.get("use_web_search", False)` into a separate local
(`_oai_use_web_search`) and routes through `_web_search_stream_openai()` only
when the flag is True. When False, behaviour is **byte-identical** to before
— it returns the raw `stream_openai_response(...)` generator just like the
original code. The local path's own `use_web_search` read (now at app.py
~3996) is **completely untouched** — the two reads are independent.

**Why duplication, not a shared helper.** The two paths have different prompt
shapes (raw ChatML string vs. messages array), different re-prompt endpoints
(`/completion` vs. `/v1/chat/completions`), and different abort mechanisms.
The local path is load-bearing and battle-tested through dozens of edge cases
(self-reference filtering, intent gate, local-doc suppression, time-sensitive
override, query cleaning). Refactoring them into a shared helper risks
regressing the local path for the sake of code elegance — not worth it.

- ⚠️ Do NOT consolidate `_web_search_stream` and `_web_search_stream_openai`
  into a shared helper without a full regression test of the local path.
  Duplication is intentional.

**Verified:** `app.py` parses cleanly. Local `_web_search_stream` and its
return point (now at app.py ~4719) are byte-identical to before. Local path
`use_web_search` read at line ~3996 is unchanged. No new imports; no global
state added beyond the existing `abort_generation` flag.

**Still pending — Phase 2 (NOT done in this task):**
- Vision path (app.py ~3795 — `stream_vision_response(vision_payload)`) has no
  tag detector. If a vision model is ever trained to emit `[WEB SEARCH: …]`,
  the tag will leak verbatim there too.
- Jinja / Gemma / Qwen messages-API path (app.py ~3918 —
  `stream_vision_response(payload)`) has the same gap.
- Both will be addressed in a separate Phase 2 task — same parallel-
  implementation approach, not a shared refactor.

⚠️ Flask restart required (Python edit). CSS-only changes don't need a
restart, but this is `app.py` so the dev server must be bounced.

---

## May 19 2026 — Fixed SP Fields Showing the Wrong Template

**Files:** templates/config.html

**The bug:** on the System Prompt config page, selecting a system-prompt
template left the System Prompt / Example Dialogue / Post-History fields out of
sync — they showed a *different* template's content than the one selected.

**Root cause — a race between redundant loaders.** Three functions wrote to
those fields:
- `loadSelectedSystemPrompt(filename)` — loads all three fields together, for
  the *selected* template. Runs on init, character load, and dropdown change.
- `loadGlobalExampleDialog()` — loaded *only* the example field, for the
  *globally active* template.
- `loadGlobalPostHistory()` — loaded *only* the post-history field, for the
  *globally active* template.

The latter two ran on `DOMContentLoaded` and each did two sequential fetches,
so they resolved late. Selecting a template shortly after page load filled all
three fields correctly via `loadSelectedSystemPrompt`, then the still-in-flight
global loaders resolved and overwrote the example + post-history fields with
the *active* template's content — leaving the dropdown on one template and
those two fields on another.

**The fix:** removed `loadGlobalExampleDialog()` and `loadGlobalPostHistory()`
entirely (calls + definitions). `loadSelectedSystemPrompt()` already loads
system prompt + example + post-history together for the correct template, and
it is the single code path used by init, character load, and dropdown change —
so the three fields now always reflect one template. The `saveGlobal*`
counterparts are unchanged. Tombstone comments mark why the loaders were
removed.

- ⚠️ DO NOT re-add a separate per-field loader for example dialogue or
  post-history — partial loaders keyed to the *active* template race the
  unified loader and reintroduce the field/dropdown mismatch.

---

## May 19 2026 — Vision Fix: --chat-template No Longer Forced on Vision Models

**Files:** app.py

**The bug:** an image-attached chat to a genuinely vision-ready llama-server
(Pixtral 12B, mmproj loaded, `clip_model_loader: has vision encoder` confirmed
in the server console) failed with llama-server's error "image input is not
supported". Root cause: the `/load_model` launch-command builder appended
`--chat-template chatml` whenever `settings.json` set the chat template to a
concrete value — **including vision-model loads**. A multimodal GGUF ships its
own multimodal-aware chat template, and that template is what drives
image-token insertion. Forcing plain ChatML over it broke vision: the request
reached `/v1/chat/completions` correctly formatted, but the overridden template
left llama-server unable to place the image, so it rejected the input.

**The fix:** the launch builder now decides `--mmproj` first, then makes
`--chat-template` conditional — it is appended **only when no mmproj is being
loaded**. Vision loads keep the model's native multimodal template; a clear
console line is printed when ChatML is skipped for this reason
(`🖼️ Vision model detected — using model's native chat template …`). Text-only
loads (Helcyon and any other non-vision GGUF) are unaffected — they receive
`--chat-template` exactly as before.

- ⚠️ Never globally force `--chat-template chatml` — vision models depend on
  their native multimodal template for image-token insertion. The conditional
  (skip when an mmproj is loaded) is required.

**Known issue — deferred to post-launch (do NOT fix now):** `/get_model`'s
`vision_active` and the `/chat` vision guard both derive vision-readiness from
`settings.json["mmproj_path"]`, not from the live llama-server. If a user sets
`mmproj_path` without reloading the model, `vision_active` flips true while the
running server has no projector — a false "vision-ready" report. It did not
bite here (the projector is genuinely loaded), but the proper fix is to probe
llama-server's `/props` endpoint at runtime for authoritative vision capability
rather than trusting `settings.json`.

---

## May 19 2026 — mmproj Auto-Detect Now Scans Subfolders

**Files:** app.py, templates/config.html

`/auto_detect_mmproj` previously scanned only the immediate Models Folder
(`os.listdir`), so an mmproj file kept in a per-model subfolder was never
found. It now walks the folder tree recursively (`os.walk`, top-down) — an
mmproj in the Models Folder itself is still preferred over a nested one, and
results are deterministic (dir/file names sorted). The `.gguf` extension check
is now case-insensitive. Auto-Detect button tooltip and status messages
updated to say "and subfolders".

Verified: `app.py` parses.

---

## May 19 2026 — mmproj (Vision Projector) Config UI + Silent-Wipe Fix

**Files:** templates/config.html

The mmproj/vision system was fully wired in the backend (`settings.json`
`mmproj_path`, `/save_llama_config` accepts it, the server launches with
`--mmproj`, `/auto_detect_mmproj` endpoint) but had **no UI control at all** —
the only way to enable vision was hand-editing `settings.json`.

**The silent-wipe trap (found and fixed here).** Because config.html had no
mmproj field, `saveLlamaConfig()` sent no `mmproj_path`, and the backend
defaults a missing value to empty:
`s['mmproj_path'] = data.get('mmproj_path', '')`. So clicking **💾 Save Llama
Config** for any reason **silently wiped a hand-set `mmproj_path` to empty**,
disabling vision. Now that the field exists and is always sent, this can no
longer happen — the value round-trips instead of being blanked.

**Added to the Llama Config section** (placed between Models Folder and Launch
Arguments — it is a model-loading concern, grouped with the path inputs):
- A `Vision Projector — mmproj` text field. Empty is valid (= no vision) and
  is not validated against.
- A 📁 browse button using the **file** picker with a `.gguf` filter
  (`browseFile` gained an optional `filter` arg; existing callers unaffected).
- A 🔍 **Auto-Detect** button — scans the configured Models Folder for a
  `*mmproj*.gguf` file via the previously-orphan `/auto_detect_mmproj`
  endpoint, and reports the result in the config status line.
- Wired into `loadLlamaConfig` (populate), `saveLlamaConfig` (send),
  `saveLlamaPreset` + `loadLlamaPreset` (round-trip `mmproj_path` with the
  other fields) — so presets capture mmproj too and don't reintroduce the
  same wipe bug on preset load/save.

- ⚠️ Mmproj UI control must remain in config.html — the backend still depends
  on `settings.json["mmproj_path"]` for vision model loading. Removing the UI
  silently breaks vision and reintroduces the wipe-on-save bug.

---

## May 19 2026 — Active Character Synced Across Desktop & Mobile

**Files:** app.py, templates/index.html, templates/mobile.html

The active project and its chat folder were already shared between the desktop
and mobile apps (server-side `projects/_active_project.json`). The **character**
was not — each app picked it from per-device `localStorage('lastCharacter')`,
so it only matched by coincidence (and only because the build effectively had
one character). Now the active character is shared too.

- New server-side state file `characters/_active_character.json`, with
  `get_active_character()` / `set_active_character()` — mirrors the
  active-project pattern exactly.
- New routes: `GET /active_character` (read on load) and
  `POST /active_character` (write on switch).
- `list_characters` skips `_active_character.json` so the state file is not
  picked up as a phantom character.
- Both apps now resolve the initial character as: **server-side active
  character → per-device `localStorage` cache → first in list**, and write the
  choice back to the server whenever a character is loaded/switched
  (fire-and-forget, non-blocking). index.html does this in `loadCharacter`;
  mobile.html via a shared `setActiveCharacterServer()` helper.

Switching character on either device now carries to the other on its next
load — same as how the active project already behaves.

Verified: `app.py` parses.

- ⚠️ The active character is intentionally GLOBAL server-side state — switching
  it on one device switches it everywhere. This is correct for single-user
  use (the overwhelmingly common case). Do NOT "fix" this into per-device
  state — that reintroduces the desktop/mobile mismatch this change resolves.

---

## May 19 2026 — Vision/Image-Upload Guards + Error Surfacing

**Files:** app.py, templates/index.html

A review of the image-upload → vision pipeline found the happy path sound but
two gaps where failures were silent. Both fixed:

**Fix 1 — vision-capability guard.** Nothing checked whether the loaded model
actually had an mmproj (vision) file before accepting an image. Attaching an
image to a text-only model sent it to a non-vision server → silent drop or a
blank reply.
- Frontend (`handleImageAttach`): now checks `/get_model`'s `vision_active`
  before attaching; if the model has no mmproj it alerts the user and aborts
  the attach. Fails open if the check itself errors (backend guard still
  catches it).
- Backend (`/chat`): before entering the vision path, if images are present
  but no valid `mmproj_path` is configured, it returns HTTP 400 with a clear
  message instead of POSTing the image to a model that can't read it.

**Fix 2 — error surfacing.**
- `stream_vision_response` now wraps the request in try/except (server
  unreachable → real message) and checks the HTTP status: on a non-200 it
  reads the error body and yields a readable explanation, instead of feeding
  the error body line-by-line into the JSON parser and yielding nothing (the
  old behaviour produced a blank reply with no error). Added a 15s connect
  timeout; the read timeout stays unbounded so slow vision generation is
  unaffected.
- Frontend `/chat` `!response.ok` handler now displays the server's actual
  response body (e.g. the new vision guard message) instead of a generic
  "Server error" — also improves visibility of pre-existing `/chat` errors.

Verified: `app.py` parses. Vision pipeline otherwise unchanged — the happy
path (vision model + mmproj) is untouched.

- ⚠️ Vision/OpenAI/jinja request paths still rebuild system content from
  `system_text` and bypass the `messages[0]` late-appends — pre-existing,
  documented limitation, not addressed here.

---

## May 19 2026 — /continue SP Resolution Fix + Shared Prompt-File Resolver

**Files:** app.py

**Fix 1 — /continue route was character-blind for the system prompt.** The
`/continue` route loaded the SP via `get_active_system_prompt_path()` — the
global active SP only. Hitting Continue mid-conversation with a character that
had a bound SP silently swapped to the global SP for that one generation.
`/continue` now resolves the SP through the shared resolver, so it applies the
same per-character-bound → global-active → fallback chain that `/chat` uses.
No bound SP → still falls back to the global active SP (unchanged).

**Fix 2 — extracted `resolve_character_prompt_files(char_data)`.** The pattern
`char_data.get("system_prompt") or get_active_prompt_filename()` plus the
`.example.txt` / `.posthistory.txt` stem derivation was inlined and duplicated
in 4 places. It is now a single module-scope helper returning
`(sp_filename, example_filename, posthistory_filename)`. The 4 paired-file
sites (overhead pre-calc example, overhead pre-calc post-history, example-
dialogue fallback loader, post-history directive loader) were refactored to
call it — behaviour verified identical (5 parity cases: clean filename, empty
field, no-extension name, missing field, multi-dot name; plus safe handling of
a `None`-valued field and `None` char_data).

**Deviation flagged — NOT unified.** The 5th candidate site, the `/chat`
system-prompt *content* override (app.py ~2134-2149), is structurally
different: it picks the SP file, reads its content, is gated on whether the
character actually has a bound SP, rebuilds the prompt with a distinct
`"Current date and time:"` time prefix, and has no global re-derivation
fallback (the global is pre-loaded by `get_system_prompt()` with a different
`"Current date:"` prefix). Routing it through the resolver would change the
time-prefix for unbound characters. It was deliberately left inline to
preserve behaviour parity. Only `/continue` changed behaviour in this task.

Verified: `app.py` parses; helper parity unit-tested; traced `/chat` (bound
and unbound) — same SP as before; traced `/continue` (bound → now loads the
bound SP; unbound → global active, unchanged).

- ⚠️ Any new route that loads a character SP or its paired files MUST call
  `resolve_character_prompt_files()` — do NOT inline the resolution chain.
  Inline duplication is what caused the /continue bug.

---

## May 19 2026 — Active SP Indicator: Clean Name + Bound-Character Display

**Files:** templates/config.html (frontend only)

Two tweaks to the active-SP status line under the Global System Prompt
dropdown on the System Prompt page:

- **`.txt` extension stripped for display.** The indicator showed the raw
  filename (`Active: GPT-4o-API.txt`) — dev-leaky. It now strips a trailing
  `.txt` (case-insensitive) for display only: `Active: GPT-4o-API`. The
  underlying filename in storage and every backend call is unchanged.
- **Bound character(s) now shown.** The indicator reads
  `🟢 Active: <sp-name> — Bound to <character>` (em dash, single spaces). The
  bound character is found by reverse-lookup: iterate `/list_characters` and
  check each character's `system_prompt` field via
  `/character_system_prompt/<n>` against the active SP filename. If multiple
  characters are bound to the same SP they are all listed, comma-separated
  (`Bound to Gemma, Aria, Dave`) — no truncation. If none are bound it shows
  just `🟢 Active: <sp-name>` as before. Uses existing endpoints only — no new
  route, no Python restart.
- The rendering logic was extracted into a single shared function,
  `refreshActiveSpIndicator()`, replacing the old `updateActiveIndicator`. It
  is called from every trigger — page load, after Activate, after Bind, and on
  character select — so the indicator stays current without duplicated render
  code. The active filename is held in a module-level var so a post-Bind
  refresh can re-render without the caller re-supplying it.

Indicator styling (colour, size) is unchanged — only the content is longer.

- ⚠️ Display-only filename stripping — do not rename stored files or change
  backend filename handling.

---

## May 19 2026 — Time-Decay Session Memory

**Files:** app.py, settings.json

Session-summary surfacing now degrades gracefully with age instead of always
foregrounding the most recent summary regardless of how stale it is.

**Decay tiers (defaults):**
- **Hot** — age ≤ 48h → tail-injection slot (system-block position #11, the
  last thing before chat turns). Only the *single* most-recent summary.
- **Cold** — 48h < age ≤ 7 days → the `YOUR OWN MEMORY OF RECENT SESSIONS`
  block (position #2). A summary younger than 48h that is *not* the single
  most-recent one also lands here.
- **Dormant** — age > 7 days → not injected anywhere. Still stored on disk.

**Configurable** in `settings.json` under `session_memory.hot_hours` /
`session_memory.cold_days`. If the section or keys are missing the code uses
48 / 7 silently — no warning, no crash, no manual setup required. A
`session_memory` block with the defaults was added to `settings.json`.

**Storage — Option C (hybrid timestamps, graceful legacy fallback):**
- New summary appends are written with an inline ISO-8601 UTC timestamp on the
  entry's `---SESSION---` delimiter line (`---SESSION--- 2026-05-19T15:42:00Z`).
  The file is rewritten in a header-per-entry format; every entry's text is
  preserved unchanged.
- Legacy summaries (no inline timestamp) are **not** migrated or backfilled —
  they are preserved verbatim and fall back to the summary file's mtime as a
  best-effort age. As they age past the dormant threshold they drop out of
  injection on their own. No migration, no data mutation of existing files.
- New helpers: `parse_session_summaries` (→ `[(timestamp, text), …]`, inline
  ISO or mtime fallback), `select_session_summaries` (→ `(hot, cold)` after
  decay), `_read_session_memory_settings`, `_parse_iso_utc`.

**Injection logic:** the new-chat branch now calls `select_session_summaries`
— the hot summary is held for the tail slot, cold summaries render in the
`YOUR OWN MEMORY` block, dormant ones are skipped. If nothing qualifies for a
slot, that slot simply doesn't render (empty tail, no fallback framing). The
wrapping/framing strings of both the tail injection and the cold block are
**unchanged** from the prior task. The tail marker's relative time
("yesterday", "earlier today", …) is now computed from the hot summary's own
timestamp rather than the file mtime. No user name is hardcoded — the marker
uses the existing `user_display_name` / `user_name` dynamic vars.

Verified: `app.py` parses; `settings.json` valid JSON; parse/select/save logic
unit-tested — legacy parse (all-mtime), append-to-legacy round-trip (legacy
entries keep no timestamp, new entry timestamped), hot/cold/dormant tiering,
non-newest sub-48h → cold, newest-too-old → empty tail, empty file → nothing.

**Pre-existing limitation (inherited, not introduced here):** this applies to
the ChatML `/completion` path. The vision, OpenAI-cloud, and jinja/Gemma/Qwen
paths rebuild their system content from `system_text + memory` and bypass the
`messages[0]` late-appends entirely — so neither this decay logic nor the
tail/anchor/OOC/time appends reach the model on those paths. Worth a future
task to unify those paths.

- ⚠️ DO NOT add an on/off toggle on top of time decay — overlapping controls
  create state confusion about which one suppressed a summary.

---

## May 19 2026 — Most-Recent Session Summary Moved to System-Block Tail

**Files:** app.py

**The bug:** the model did not pick up on the previous session naturally at the
start of a new chat — it only recalled it when the user explicitly asked
"what did we talk about last time?". Discovery confirmed why:

- Session summaries are stored in `session_summaries/<character>_summary.txt`,
  up to **3 per character** (`MAX_SUMMARIES`), joined by
  `SESSION_DIVIDER` (`---SESSION---`); the **last segment is the most recent**.
- `load_session_summary` returned the whole file, and the new-chat injection
  appended **all 3 summaries as one block** into `char_context`, wrapped in the
  `YOUR OWN MEMORY OF RECENT SESSIONS` banner.
- That block sat **early** in the system block — everything else came *after*
  it: user persona context, the instruction layer + tone primer, project
  documents, the `ACTIVE OPERATOR RESTRICTIONS` anchor, the `[OOC: …]`
  character/author notes, and finally the `Current local time:` string. The
  most recent summary was buried mid-block, far from the generation point, and
  drowned out. (Post-history is no longer in the system block — it rides in the
  depth-0 `[REPLY INSTRUCTIONS]` packet folded into the last user turn.)

**The fix — split the most recent summary off and move only it to the tail:**
- The new-chat injection now splits the loaded summaries on `---SESSION---`.
  The **older** summaries (sessions 2 and 3 going back) stay exactly where they
  were — same `YOUR OWN MEMORY OF RECENT SESSIONS` block, same wrapping, same
  position in `char_context`. They are cold context and unchanged.
- The **most recent** summary is held in `_recent_session_summary` and appended
  as the **absolute last thing in the system block** — after the character
  card, user context, instruction layer, project context, restriction anchor,
  OOC notes, and the time context. It is the last thing the model sees before
  the chat messages begin.
- It is wrapped in an attention-grabbing marker that reads as "this just
  happened, pick up from here", not a database entry:
  `[Most recent session with <user>, <relative time>]:` … `[End of recent
  session — continue naturally from where you left off]`. The username uses the
  existing dynamic vars (`user_display_name` / `user_name`) — no hardcoded name.
  The relative time ("earlier today", "yesterday", "N days ago", "last week",
  "N weeks ago") is computed from the summary file's mtime via the new
  `_resolve_session_summary_path` helper; if it can't be computed cleanly the
  time portion is omitted rather than invented.
- If only one summary exists, it is the most recent — it goes to the tail and
  the older-summaries block simply does not render.

Summary generation and storage are unchanged — only *where* the most recent one
is injected changed. The `INJECTED MEMORY` instruction-layer text is untouched
and still applies to the older summaries higher in the block.

**Reason:** recency in the prompt context = stronger attention weighting at
generation time. The intent is for the model to surface the most recent session
naturally in its first response of a new chat.

Verified: `app.py` parses clean.

- ⚠️ DO NOT move the most-recent session summary back into the main system
  block — tail position is intentional for attention weighting.

---

## May 19 2026 — "Bind to Character" Control on the System Prompts Page

**Files:** templates/config.html (frontend only — reuses existing backend routes)

**The bug:** the system-prompts page's **✅ Activate** button silently bound the
selected template to whatever character happened to be selected elsewhere
(`character-select` on the Character tab), with no visible indication of which
character that was. The user repeatedly bound templates to the wrong character.

**The fix — a new, explicit Bind control:** a `Bind to character:` row was
added below the Activate row, with a character dropdown and a **🔗 Bind**
button. The dropdown is populated from the existing `/list_characters`
endpoint and **defaults to "-- Select character --" (no pre-selection)**, so
the user must make a deliberate choice — this is what prevents the silent-bind
bug from recurring. The Bind button stays disabled until *both* a template and
a character are chosen. On click it POSTs to the existing
`/character_system_prompt/<n>` route with `{system_prompt: <filename>}`, then
shows a status line naming both items — e.g.
`✅ Bound 'GPT-4o.txt' to character 'Aria'` — or a red error line on failure.

**Activate is unchanged:** it still sets the global default
(`active_system_prompt` in settings.json) and keeps its own existing
current-character bind behaviour. Bind is a *separate, additional* access
point — no new endpoints, no change to the character-editor binding flow.

- ⚠️ DO NOT consolidate the Bind and Activate buttons — the separation is
  intentional. Activate = global default; Bind = explicit per-character write.
  Merging them reintroduces the silent-binding bug this change exists to fix.

---

## May 19 2026 — "📋 Paste Transcript" Input-Menu Option

**Files:** templates/index.html (frontend only — no backend, mobile.html untouched)

**The bug:** when a user pasted an HWUI chat-file transcript — the
`[timestamp] Speaker: …` format — directly into the message textarea, the
model recognised those timestamped turns as conversation history and
*continued* them, instead of discussing the pasted text as quoted reference
material.

**The fix:** a new **📋 Paste Transcript** option in the `+` input menu opens a
modal (a multi-line textarea + optional filename, defaulting to
`pasted-transcript-{YYYY-MM-DD-HHmm}.txt`). On **Attach**, the pasted text is
pushed into `window.attachedDocuments` exactly as a file pick would, then
`renderDocumentPreviews()` is called. From there it flows through the existing
document-attachment pipeline: `wrapAttachedDocuments` wraps it in
`[ATTACHED DOCUMENT: …]` markers so the model reads it as quoted reference, not
as turns to continue. Empty textarea → Attach briefly highlights the field and
does nothing. Cancel / backdrop click / Escape close the modal. The modal
reuses the existing `.modal` styling tokens (z-index, backdrop, colours).

- ⚠️ DO NOT revert — this deliberately reuses the `attachedDocuments` system.
  Do not refactor pasted transcripts into a separate path or parallel state;
  the whole point is that they go through the same marker-wrapping pipeline as
  file attachments.

---

## May 18 2026 — Web Search: Intent Gate for Ambiguous Triggers

**Files:** app.py

**The bug:** web search fired on messages with no search intent. Example from
a transcript — an emotional monologue containing *"…I didn't find out where
she is…"* triggered a nonsense Urban Dictionary search. Root cause: search
intent was decided by **regex over the raw message**. The pattern
`find out (where|what|…) <word>` matched the narration, and the
self-reference filter that should have caught it didn't recognise
`I didn't …` / `I did …` / `I had …` as narration openers. Regex
fundamentally cannot tell a request from reminiscing — that needs meaning.

**The fix — a two-tier trigger with a model-judged gate (option A):**
- Triggers are split into two precision tiers in `_web_search_stream`:
  - **EXPLICIT** (`_explicit_pat`) — unambiguous imperatives ("search for X",
    "google that", "look it up", "check online"). A match fires the search
    immediately — fast-path, no extra model call.
  - **AMBIGUOUS** (`_ambiguous_pat`) — phrases that recur innocently in
    ordinary speech ("find out where…", "look up his number", "any news
    on…"). A match no longer fires a search directly.
- New `_search_intent_gate(user_msg)` — when an ambiguous phrase is seen, it
  asks the loaded model itself, in one short isolated `/v1/chat/completions`
  call (`temperature 0`, `max_tokens 32`), whether a search is genuinely
  warranted. Returns `(should_search, query)`. This is the frontier approach —
  contextual model judgement — done as a cheap pre-pass because the local
  llama-server has no reliable native tool-calling.
- The gate **fails closed**: any error → `(False, "")`. The problem is
  false-positive searches, so a missed gate suppresses rather than searches.
- The gate is a self-contained classifier prompt — it does NOT modify the main
  chat prompt and does NOT use the trained `[WEB SEARCH: …]` tag format, so it
  respects the existing model-trained tag gating.
- When the gate returns a query it is used directly (`_gate_query`), skipping
  the brittle regex query-extraction that previously mangled rambling messages
  into nonsense queries.

Verified: `import` clean; gate fails closed when llama-server is down; the
transcript message routes to the gate (not an instant search); explicit
requests still fire instantly; plain conversation triggers nothing. The live
gate verdict needs testing with the model server running.

- ⚠️ DO NOT move ambiguous phrases (`find out …`, `look up …`, `any news
  on …`) back into the instant-fire path — regex cannot judge intent on
  free-form speech, which is the original bug. They must go through the gate.

**Files:** app.py, global_documents/Runpod.txt

**The bug:** global-document injection only fired for the exact bare keyword
(e.g. `runpod`). Every natural question (`how does runpod work`, `what is
runpod used for`, …) injected nothing. Two causes in `load_global_documents`
/ `_score_doc`:
- `min_score` scaled steeply with query length (1 kw → 3, 2 → 5, 3+ → 6), but
  a single-topic doc named after one keyword can only earn ~3–4 points, so it
  could never clear the bar on a multi-word query.
- Logic flaw in `_score_doc`: for 3+ keyword queries the content preview was
  scored *only when the filename scored 0* — so a filename hit actively
  disqualified the doc by capping it at 3 against a min of 6.

**The fix — opt-in curated keywords, same convention as memory blocks:**
- A document may now carry an optional leading `Keywords: a, b, c` line
  (case-insensitive, `, ; :` separators — mirrors `_parse_memory_blocks`).
  New helpers `_extract_doc_keywords()` / `_doc_scoring_data()`.
- Trigger gate widened: a global doc is eligible when the query shares a
  keyword with the filename **OR** the curated Keywords line. Curated keywords
  can trigger a doc on their own (e.g. "what does helcyon use" pulls a doc
  tagged `helcyon` even with no filename match).
- Scoring: filename ×3, curated keyword ×3, content preview ×1. `_score_doc`
  rewritten — content preview now always scored (the score==0 gate removed).
- **Multi-word curated keywords are AND-matched** (`_curated_kw_match`): a
  single-word keyword matches that word, but a multi-word keyword scores/
  triggers only when **all** its words appear in the query. So a curated
  `full weight training` fires on "explain full weight training" but NOT on
  "training my dog" or "gym weight training". This is the curation lever for
  broad words — pair a vague word with a context word instead of listing it
  bare. (Earlier intra-keyword substring matching is gone: it made phrases
  no more precise than the loosest word in them.) `_score_doc` /
  `load_global_documents` take a `query_lower` arg so phrase words a
  tokeniser would drop (stopwords) can still be matched.
- Threshold: docs **with** a Keywords line use a flat low bar (score ≥ 3 — one
  filename or one curated hit). Docs **without** one keep the original
  length-scaled bar, so untagged docs are unaffected.
- The `Keywords:` line is stripped before injection (retrieval tag, not
  content the model should see) — verified no leak. Strip runs before
  `_extract_perspective` so a leading Keywords line can't hide a PERSPECTIVE
  tag below it.
- Same Keywords-line stripping added to `load_project_documents` for
  consistency (it shares `_score_doc`) — prevents the tag leaking into
  injected project-doc content. Project docs keep their filename-only trigger.

Verified by running `load_global_documents` directly: all previously failing
query phrasings now inject; curated-keyword-only queries (`helcyon`, `lora`)
inject without the word "runpod"; multi-word keywords (`full weight training`,
`model training`) inject only when all their words are present; off-topic
queries that merely share one broad word — "training my dog", "gym weight
training", "weather today" — correctly inject nothing.

`global_documents/Runpod.txt` (dev-build test file) was given an example
`Keywords:` line — note it deliberately avoids the bare word `training`,
using `full weight training` / `model training` instead so dog-training and
gym chatter can't pull it. Review/adjust the wording for the real build.

- ⚠️ DO NOT revert to filename-only triggering or the scaled min_score for
  tagged docs — that is the original bug. Curate Keywords lines deliberately:
  a single curated keyword hit is enough to trigger injection.

---

## May 18 2026 — Restored Concrete Memory-Tag Example (Generic Name)

**Files:** utils/session_handler.py

The earlier abstract-placeholder rewrite of the MEMORY TAGS example
(`'<user_name> told me about...' where <user_name> is...`) broke memory tag
emission: the angle-bracket / "where X is..." syntax was abstract enough that
the model stopped emitting the `[MEMORY ADD: ...]` tag and instead
hallucinated that it had saved.

Fixed: restored a concrete fill-in-the-blank example using the generic name
"Alex", with an explicit instruction not to copy the example name literally —
`Example: 'Alex told me about...' — substitute the real user's name, never
the example name.`

- ⚠️ DO NOT revert — concrete examples are required for tag emission;
  abstract placeholder syntax suppresses it. The name stays generic ("Alex"),
  never a real user's name.

---

## May 18 2026 — Removed Hardcoded User Name From Memory-Tag Instruction Layer

**Files:** utils/session_handler.py

`get_instruction_layer()`'s MEMORY TAGS section used a literal hardcoded user
name in its first-person example: `Example: 'Chris told me about...'`. This
leaked a real user's name into the prompt, causing the model to write memory
entries about "Chris" regardless of who the active user actually was —
cross-user contamination.

Fixed: the example is now a generic placeholder — `'<user_name> told me
about...' where <user_name> is the user you are speaking with` — and the line
explicitly instructs the model to refer to the user by their actual name. No
other changes to the file.

- ⚠️ DO NOT revert — hardcoded user names cause cross-user contamination and
  violate the project rule against hardcoding any real user name into app
  code or prompts.

---

## May 17 2026 — Centre Pillar Restored Under Background Image

**Files:** templates/config.html, templates/index.html

The frontier themes strip `#container`'s background (transparent, no shadow,
no radius), so with a background image the chat text sat directly on the
wallpaper and was hard to read. Fix: when an image is active, the injected
`<style id="hwui-bg-style">` now also restores a solid centre pillar —
`.chat-page #container { background-color: var(--container-bg) !important;
border-radius: 12px !important; box-shadow: 0 0 30px rgba(0,0,0,0.6)
!important; }`.

- Uses `var(--chat-bg)` — the theme's own flat backdrop colour — so the chat
  column matches the normal frontier look, wallpaper showing only in the side
  margins. The soft box-shadow keeps the pillar edge defined.
- Pillar lives inside the image-mode style block only — plain colour mode
  keeps the frontier themes flat/pillarless as designed.
- Model message bubbles are deliberately NOT restored — they stay transparent,
  so the frontier look is preserved; only the pillar comes back.
- The pillar extends up behind the fixed top bar (no gap): `margin-top: -80px`
  cancels `#main`'s `padding-top: 80px`, and `padding-top: calc(80px + 0.5rem)`
  re-insets the chat content so it doesn't move.
- Config page: same extend-up applied to `#config-page #container` in
  style.css (permanent, not image-mode — the config panel is never stripped).
  Uses `calc(80px + 1.5rem)` since the config panel's own padding is 1.5rem.

---

## May 17 2026 — Background Image Toggle Fixed (two root causes)

**Files:** app.py, templates/config.html, templates/index.html

The Appearance tab's Theme Colour / Background Image toggle didn't restore a
wallpaper. TWO separate bugs:

**Bug 1 — storage (the real reason it always failed):** the image was stored
as a base64 data URL in `localStorage`. A real photo's base64 is several MB;
`localStorage` has a ~5MB per-origin quota. `setItem` threw `QuotaExceededError`,
uncaught — so the image silently never saved, and no CSS fix could ever help
because there was no image data. Fixed by storing the image as a real FILE:
- `/save_bg` rewritten — accepts a multipart upload, saves it to
  `static/hwui-bg<ext>` (clears any previous one first), returns its URL.
- `/clear_bg` rewritten — deletes the saved file.
- `handleBgImageChange` (config.html) now POSTs the file to `/save_bg` and
  stores only the short URL in `localStorage` (no quota risk). Loud `alert`
  on failure instead of silent death. `clearBackground` POSTs `/clear_bg`.
- ⚠️ DO NOT revert to base64-in-localStorage — that is the original bug.

**Bug 2 — frontier themes hid it even when set:** chatgpt/claude/gemini/grok/
moonlight kill wallpapers by painting `html`, `body`, AND `#app` opaque with
`!important`. The injection only set `html, body`, so the opaque `#app` layer
covered the image. Fixed: `applyBackground()` and index.html's pre-paint
script now also emit `#app { background: transparent !important; … }`.
index.html's script also now checks `hwui_bg_mode` so colour mode doesn't show
a stale cached wallpaper.

Toggle UI (two-button segmented control) and the `hwui_bg_mode` /
`hwui_bg_image` localStorage keys are unchanged — `hwui_bg_image` just holds a
URL now instead of a multi-MB base64 blob.

---

## May 17 2026 — Settings Cog Converted to Dropdown Menu

**File:** templates/index.html

The top-bar settings cog (`#settings-link`) changed from `<a href="/config">`
to a `<div>` that opens a dropdown. The `#settings-link` id is kept so
style.css's top-bar flex layout is unchanged — top bar height/position
unaffected.
- Dropdown (`#cog-menu`) is `position:absolute` below the cog, with parent
  `#settings-link` set `position:relative`. Uses `var(--modal-bg)` /
  `var(--modal-border)`.
- Contents: a "Config Page" link (→ /config) and a live theme switcher —
  `loadCogThemes()` fetches `/themes/list` once (cached via a data attribute),
  renders a button per theme; `applyThemeFromCog()` swaps the
  `#active-theme-link` href, POSTs `/themes/switch`, updates active states.
- `toggleCogMenu()` + an outside-click close listener. Styling via a `<style>`
  block in `<head>`; theme buttons use `margin:0 !important` to beat the
  global button margin.
- ⚠️ `--text-main` / `--text-muted` are NOT defined in the theme system — used
  with fallbacks (`var(--text-main, var(--modal-text, #e8e8e8))`, etc.).

---

## May 17 2026 — Chat Width Consolidated to One CSS Variable

**Files:** style.css, themes/{chatgpt,claude,gemini,grok,moonlight}.css

Chat-column width had drifted across 7 places (5 theme files' `.chat-page
#container` `!important` rules + style.css `#container` + a `#config-page
#container` override). Every resize meant a multi-file edit, and the config
page needed a separate magic number (812 = 860 − the `#chat` inner padding).

Consolidated to a single custom property:
- `style.css :root` → new `--chat-width: 860px;` — the ONLY value to change.
- `#container, #center-column` → `max-width: var(--chat-width)`.
- `#config-page #container` → `max-width: calc(var(--chat-width) - 3rem)`
  (3rem = the chat page's `#chat` 1.5rem×2 padding, which the config page
  lacks — so the visible content widths stay matched automatically).
- All 5 theme files → `max-width: var(--chat-width) !important`.

Result: resizing the chat column is now a one-line change to `--chat-width`.
⚠️ The small-screen responsive breakpoints in style.css (`#container` at
600px/500px under `@media`) are intentionally left as separate hardcoded
fallbacks — they are not the desktop width and not meant to track the var.
⚠️ The `@media (max-width:1400/1024)` blocks in the theme files are now
redundant (all resolve to `var(--chat-width)`) — harmless, left in place;
can be deleted in a cosmetic cleanup if wanted.

---

## May 17 2026 — Attached Document Polluted Retrieval Query

**File:** app.py

With the inline document-attach feature, the document text is folded into the
latest user turn. The backend extracts `user_input` from that turn (app.py
~1815) and uses it as the query for **doc-intent detection, memory retrieval,
global/project-document retrieval, and chat-search triggers**. So all those
systems were keyword-matching against the *entire attached document's text*
instead of the user's typed question — pulling unrelated documents, memories
and old-chat snippets into the prompt. Symptom: model answers about the
attached document but bleeds in unrelated injected content.

**Fix (two parts):**
- `user_input` now has `[ATTACHED DOCUMENT: …] … [END ATTACHED DOCUMENT]`
  blocks stripped before any string/intent/retrieval processing — so doc
  intent, memory retrieval and chat-search triggers score against the typed
  query only. The full block stays in `active_chat`, so the model still reads
  the document. Mirrors the existing image handling (text-only copy for
  processing).
- When an inline document is attached, `project_documents` (project + global
  auto-loaded docs) is cleared — the attached document is the user's explicit
  focus, so auto-retrieved documents must not ride alongside it.

`_attached_doc_present` flag drives both. Verbose logging added for each.

---

## May 17 2026 — Trim Bug: Oversized Latest Turn Dropped Whole

**File:** truncation.py

`trim_chat_history` walked messages newest-first and `break`d the moment one
exceeded `conversation_budget`. If the **latest** user turn alone exceeded the
budget, the loop broke on iteration 1 — `trimmed` came back empty, only the
system message survived, and the model received **no user turn at all** (no
question, no content) → ungrounded hallucination.

This surfaced via the new document-attach feature: an attached document rides
inside the latest user turn and a real document easily exceeds the ~6–7k-token
conversation budget, so the whole turn (document + question) was silently
dropped. The model then replied only "in the ballpark" — riffing on nothing.

**Fix:** the loop now always keeps the latest turn (`body[-1]`) even if it
alone busts the budget — added an `and trimmed` guard so the budget check only
applies once at least one message is held. Logs a ⚠️ warning when the latest
turn is kept oversized. This restores the invariant app.py's final word-clamp
already enforces ("Always keep at least the final user turn"); the two trim
layers are now consistent. Also benefits any long single message, not just
documents.

⚠️ Known limit: a document large enough that the turn overflows the full
context window (~16k) will still be cut by llama.cpp / hit the EOS cliff —
very large docs need chunking, out of scope here.

---

## May 17 2026 — Branch Button Restored to Assistant Messages

**File:** templates/index.html

The `/chats/branch` backend route was intact, but the frontend branch button
had been lost in a UI redesign. Restored:
- New `branchMessage(assistantIndex)` — confirms, POSTs `source_filename` +
  `message_index` (1-based assistant-turn count) to `/chats/branch`, then
  `loadChats()` + `openChat(new_filename)`.
- `renderChatMessages` tracks `assistantCount` (incremented at the top of the
  assistant-message branch) so each assistant message carries its correct
  1-based turn number; a git-branch-icon button in the action bar passes it.
- Deliberately NOT added to the streaming / non-streaming / continue render
  paths — those are transient; `renderChatMessages` re-renders the full chat
  with proper buttons once generation completes.
- ⚠️ `assistantCount` counts rendered (non-hidden) assistant messages — if the
  backend line-walker ever counts a hidden assistant turn the index could
  drift by one. Out of scope of the restore; flagged for a future test.

---

## May 17 2026 — Inline Document Attach Restored + Dead Modal Removed

**File:** templates/index.html

### Removed dead `#edit-project-modal`
The standalone "Edit Project Modal" (`#edit-project-modal`) was orphaned by the
May 15 project-modal redesign — the live editor is the inline
`#project-edit-panel`, and nothing opened the old modal. It was removed whole
(~67 lines), along with its now-unused `closeEditProjectModal()` function.
This was the sole source of **8 duplicate element ids** (`edit-project-name`,
`edit-project-instructions`, `rp-mode-btn`, `rp-opener-section`,
`edit-project-rp-opener`, `sticky-docs-btn`, `document-upload`,
`documents-list`) — all now unique.

### Restored the inline document-attach feature
The chat-level document upload (separate from project documents) lost its
frontend in the UI redesign. The backend `/parse_document` route was always
intact — only the UI + JS wiring needed rebuilding.

- **"📄 Attach Document"** button added to the input `+` menu, next to
  Attach Image. Hidden input `#chat-document-input` (.txt/.md/.pdf/.docx/.odt).
- `handleDocumentAttach` → POSTs each file to `/parse_document`, stores the
  returned `{filename, content}` in `window.attachedDocuments`, shows a chip
  in a new `#document-preview-strip` (mirrors the image preview strip).
- On send, the document text is folded into the user turn's content wrapped in
  `[ATTACHED DOCUMENT: …] … [END ATTACHED DOCUMENT]` markers — so the model
  reads it. **One-shot:** it lives in that single message (and the saved chat
  file) as ordinary history — NOT re-injected per turn like project sticky docs.
- The document renders as a **clickable card above the user message**;
  clicking opens `#document-viewer-modal` to read the full text.
- `renderChatMessages` parses the markers back out (`extractAttachedDocuments`),
  so the card + reader survive reload — the markers travel in the message text,
  not a structured field.
- ⚠️ Editing a user message that has an attached document drops the document
  (the edit captures only the doc-stripped text). Acceptable edge case.

---

## May 17 2026 — Fictional Sample Data Added to Dev Build

**Files:** settings.json (new), system_prompts/default.txt,
system_prompts/default.example.txt (new), system_prompts/default.posthistory.txt
(new), characters/Helcyon.json

The dev build is intentionally data-free structural scaffolding. Populated the
real files with **fictional** sample content so prompt assembly can be traced
and tested end-to-end (previously every settings-dependent code path fell to
its `except` branch, hiding bugs). All content is invented — no personal data.

- **settings.json** — created with the full key set. Machine-specific paths
  (`llama_last_model`, `llama_server_exe`, `llama_models_dir`, `mmproj_path`)
  left empty on purpose: auto-launch then skips gracefully. `chat_template`
  is `chatml`, `ctx_size` 16384, `backend_mode` `local`.
- **default.txt** — expanded from a 2-line placeholder to a realistic system
  prompt. Includes several negatively-phrased hard rules ("Never…", "Do not…",
  "must not…") so the restriction-anchor extraction has something to catch.
- **default.example.txt** — paired example dialogue, two `<START>` blocks.
- **default.posthistory.txt** — paired post-history directive exercising the
  new feature: the vent-first / no-markdown-on-emotional-content rules.
- **Helcyon.json** — fleshed out to a structurally complete card: every field
  the prompt builder reads is now populated (`personality`, `scenario`,
  `post_history`, `character_note`, `use_*` flags, etc.).

⚠️ This sample data propagates into the personal and public builds via the
zip/extract pipeline — that is expected and approved (fictional, harmless).

---

## May 17 2026 — Post-History Directive (SillyTavern-style, per-template)

**Files:** app.py, templates/config.html

A post-history system directive **paired with each system prompt template** —
stored as a `<base>.posthistory.txt` file alongside the template, exactly the
same pattern as the existing `.example.txt` paired example dialogue. Load the
GPT-4o template → its post-history loads with it; switch templates → the
directive switches too.

**Where it lands:** it is NOT in the system block. It is appended as the LAST
item of the [OOC] depth-0 packet (after project_instructions), folded into the
last user turn — the closest-to-generation slot in the whole prompt, so it
carries the highest behavioural priority of any field. Wrapped as
`[OOC: System directive — highest priority. Overrides character and project
instructions. …]`. ChatML tokens stripped from the value.

**Resolution:** mirrors the example-dialogue priority-3 fallback — uses the
character-bound system prompt (`char_data["system_prompt"]`) if set, else the
globally active template.

**app.py**
- Packet builder: reads `<base>.posthistory.txt` for the active template,
  appends it last in `_reply_instr_items`. Comment block above the builder
  shows the 4-item ordering (style → post_history → project → post-history
  directive).
- Pre-trim overhead: the directive's token count +30 wrapper is pre-accounted
  in `_reply_packet_overhead` so the trimmer doesn't under-estimate.
- `list_system_prompts`: now also excludes `*.posthistory.txt` so paired files
  don't show up as selectable templates.
- `delete_system_prompt`: deletes the paired `.posthistory.txt` so it doesn't
  orphan when its template is removed.
- New routes `/system_prompts/load_posthistory/<filename>` and
  `/system_prompts/save_posthistory/<filename>` — direct mirror of the
  load_example/save_example routes. Empty save deletes the file rather than
  writing a blank one.

**templates/config.html**
- New "Post-History Instructions" textarea on the System Prompt tab, below
  Global Example Dialog, with its own Save button + status line.
- Loaded by `loadSelectedSystemPrompt()` alongside the template text and
  example dialogue; saved by `saveGlobalSystemPrompt()` and
  `saveSystemPromptAs()` alongside them too.
- `loadGlobalPostHistory()` / `saveGlobalPostHistory()` JS helpers mirror the
  example-dialogue equivalents (save targets the selected template's paired
  file); `loadGlobalPostHistory()` added to the init sequence.

**Reason:** character-card behavioural instructions sit at the top of the
system block — the most attention-starved position — and positive-phrased
rules (e.g. "vent before pivoting") are not caught by the restriction anchor,
so they get zero reinforcement. Pairing the directive with the template means
each model (GPT-4o, etc.) gets its own hard system rules that reliably land
closest to generation, switching automatically with the template.

**Usage (how to set one up):**
1. Create the template first — type the system prompt, "Save As New Template",
   name it (e.g. `gpt-4o`). The `.posthistory.txt` filename is derived from the
   *selected template's filename*, NOT from the post-history text.
2. With that template selected in the dropdown, type the post-history and click
   "💾 Save Post-History" → writes `<base>.posthistory.txt`.
3. Click "✅ Activate" — saving the file is not the same as activating the
   template; the model only reads the post-history of the active (or
   character-bound) template.
- ⚠️ "💾 Update" and "Save As New Template" save prompt + example dialogue +
  post-history together in one go, all paired to that template name.
- ⚠️ An empty post-history box DELETES `<base>.posthistory.txt` rather than
  writing a blank file.
- ⚠️ "Paired" (filename ↔ template) is NOT the same as "Bound" (the existing
  character-to-system-prompt binding, the 🔗 indicator). Saving a post-history
  file binds nothing to a character.

---

## May 16 2026 — OOC Packet: Project Instructions Priority Bump

**File:** app.py (~line 2898)

Swapped ordering of items in the depth-0 [REPLY INSTRUCTIONS] OOC packet.
Project instructions moved from first position (lowest urgency) to last (highest urgency, closest to generation point).

New order:
1. Style reminder (example_dialogue) — lowest urgency
2. post_history
3. project_instructions — highest urgency, closest to generation point

**Reason:** Project folder instructions were being ignored (e.g. "log date and time" directive not followed).
Root cause: first item in packet = furthest from generation = least attended. Moving to last fixes this.
post_history is now lower priority — acceptable since it's rarely used and chat session summaries cover that role anyway.

Updated comment block above the packet builder to reflect new ordering.

---

## Session: May 15 2026 — UI Redesign Session

### `config.html`
- Tab system embedded CSS made self-contained in `<head>` (no longer relies on style.css)
- Sampling sidebar compact overrides applied
- Project modal: two-column → swap layout (grid and edit panel are siblings, not side-by-side)
- Edit panel now replaces grid entirely when open; Back button returns to grid
- Edit panel centred at max-width 680px with breathing room
- Top strip (Active/Create) hides when edit panel is open, restores on Back/Cancel
- Appearance tab: added Theme Colour vs Background Image toggle (setBgMode)
- Background image now POSTs to /save_bg server route (written into theme CSS directly)
- Clear background POSTs to /clear_bg
- `--project-edit-bg` variable registered in theme editor under Project List section
- `--project-edit-bg` default #0a0d10 used for edit panel body background

### `index.html`
- Project modal: fullscreen grid, card click = switch project, switch button removed
- Edit buttons use e.stopPropagation() so card click doesn't also fire
- Most Recent sort option restored; sortChatList restores dropdown from localStorage on every load
- JS background injection removed (image now handled server-side via theme CSS)

### `style.css`
- Sampling sidebar: full compact pass (240px wide, 12px font, 26px input height)
- Config tab CSS added (display:none/block toggle)
- Project modal: fullscreen sizing, two-column → swap layout, inline edit panel CSS
- Modal padding-left: 120px → 250px ⚠️ DO NOT REVERT — keeps modals centred in content pane
- `--project-edit-bg` added to :root defaults
- Hardcoded bg.jpg removed from both body rules

### `app.py`
- `/save_bg` route added: writes base64 image into active theme CSS file between hwui-bg-start/end markers
- `/clear_bg` route added: removes those markers from theme CSS file
- ⚠️ Background image feature incomplete — CC to finish (see handoff note below)

### Handoff note for Claude Code
Background image feature is partially implemented. Routes exist in app.py (`/save_bg`, `/clear_bg`). Config.html calls them correctly. The issue was the active theme CSS file has a `body { background: ... }` rule that overrides JS injection. The server-side approach (writing directly into the theme CSS via `get_active_theme_path()`) is the right fix — CC needs to verify the routes work correctly end-to-end and that the theme CSS file is being written and served properly.

---

## Session: May 15 2026 — F5-TTS Speed + Quality Pass

Investigated reported symptoms: TTS missing words, pausing in the wrong place. Goal — speed up generation (was ~6s) without adding latency; balance speed and quality.

### `f5_server.py`
- `nfe_step` lowered: first chunk 20→16, later chunks 24→20 (~17% faster generation; quality cost of 20 vs 24 is barely perceptible — this is the main speed knob, tune in `tts_to_audio`)
- `clean_text`: parentheses now become a plain space, not `. ` — a parenthetical aside no longer turns into its own falling-intonation fragment (a comma was rejected: F5 hesitates/ums on commas)

### `tts_routes.py`
- `/generate` now forwards `first_chunk` to the F5 server — the fast first-byte path existed but the proxy was dropping the field, so it never fired

### `utils/utils.js`
- `fetchAudio`: 2→3 retries with backoff; on final failure logs a loud, specific error naming the lost sentence (was failing silently — direct cause of "missing words" on F5 hiccups)
- `splitAndQueue`: tiny fragments (<25 chars, e.g. "Yes.") are merged onto the previous still-queued chunk instead of sent to F5 alone — F5 garbles/clips very short clips. No latency cost (the previous chunk hasn't been fetched yet) and reading order is preserved
- Parentheses → space (matches the `clean_text` change) in both the streaming cleaner and `splitAndQueue`
- ⚠️ Aggressive sentence-batching was considered and skipped — it would delay time-to-first-audio

---

## Session: May 15 2026 — Branch Chat Feature

### `chat_routes.py`
**Feature: new `/chats/branch` route — duplicate a chat up to a chosen assistant turn**
- Added directly below `/chats/copy`
- Accepts `source_filename` + `message_index` (1-based count of assistant turns to keep)
- ⚠️ Does NOT split on blank lines — assistant messages contain paragraph breaks, so `content.split('\n\n')` pair-counting would silently drop half of any multi-paragraph reply
- Instead walks lines and detects speaker lines the same way `/chats/open` does (timestamp-prefix strip + check against `characters/index.json` and `users/index.json`), truncating before the turn after the Nth assistant message — byte-exact
- Writes the truncated copy via `_atomic_write_text`; auto-numbers the filename `(2)`, `(3)`… if a branch already exists
- Returns `400` with a clear message if the chat has fewer assistant turns than requested

### `index.html`
**Feature: branch button on every assistant message**
- New shared `branchFromMessage(btn)` helper (defined above `openChat`) — confirms, reads the 1-based `.model-msg` index from the DOM at click time, POSTs to `/chats/branch`, then `loadChats()` + `openChat(newFilename)`
- Branch button added to all four assistant-message render paths: `openChat()` action bar, live-streaming bubble, non-streaming bubble, and the continue-generation bubble
- Uses the git-branch SVG; inherits `.msg-action-bar button` / `.copy-btn` styling — no CSS changes needed
- ⚠️ Spec originally targeted `loadChatHistory()` (character-level history, no reliable `currentChatFilename`); moved to `openChat()` so the button shows in the actual chat-file viewer and on freshly-branched chats

**Fix: gate the `[MEMORY ADD:` save flow behind an explicit user request**
- ⚠️ The `[MEMORY ADD:` tag detection lives in `index.html` (response-stream handler, ~line 3205), NOT `app.py` — `app.py` has no memory-tag processing at all
- New `getLastUserMessageText()` helper — returns the most recent non-hidden user message, flattening multimodal content to text
- Before surfacing the memory confirm UI, the last user message is checked for explicit phrases: save that / remember this / add that to memory / add to memory / save this / remember that / store that / log that / save this to / save that to / to my memory / to memory / add this to / can you save / can you remember / please save / please remember / commit that / commit this
- If none present, the tag is silently discarded (already stripped from the displayed text) and `🧠 Memory tag suppressed — not explicitly requested` is logged; confirm UI only appears on an explicit request
- Implemented as a one-line condition change (`if (memAddMatch && _memExplicitlyRequested)`) so the existing parsing block is untouched

**Feature: strip `[OOC: ...]` blocks from model output**
- New `stripOOC(text)` helper (next to `sanitizeMarkdown`) — removes `\[OOC:.*?\]` (non-greedy, dotall via `[\s\S]`) plus surrounding whitespace/newlines
- Applied inside `stripChatMLOutsideCodeBlocks` call in the streaming loop, so both the live streaming display and the saved `finalText` (= `cleanedMessage`) are OOC-free
- Also applied to the empty-response raw fallback path
- Logs `🚫 OOC block stripped from response` once per response at finalization (not per-chunk, to avoid console spam)
- Follow-up: now also applied to the continue-generation stream handler (`cleaned` + `finalText`, with the same once-per-response log)
- Follow-up: OOC blocks suppressed from TTS too — a per-stream `ttsHoldBuffer` accumulates voice chunks, `stripOOC` drops complete blocks, and a trailing open/partial `[OOC:` marker is withheld until its closing `]` arrives; only OOC-free text reaches the voice. Handles markers/blocks split across chunks.
- Follow-up: `stripOOC` now replaces a block with a single space instead of nothing, so words either side of an *inline* OOC block aren't joined
- ⚠️ Only the *horizontal* whitespace touching the block (`[^\S\n]`) is consumed — newlines are preserved. A global `\s{2,}` collapse was rejected: `stripOOC` runs on every message, so it would have flattened all paragraph breaks and code-block indentation app-wide
- ⚠️ No `.trim()` inside `stripOOC` — it is called incrementally on TTS chunks and an internal trim would join streamed words; the display/continue/fallback call sites already `.trim()` externally

---

## Session: May 14 2026 — Most Recent Sort Option Restored

## ⚠️ SPACING VALUES — DO NOT REVERT

The following CSS values in `style.css` were carefully tuned over multiple sessions.
Another Claude session MUST NOT reset these back to old values.

```
.model-text p              { margin: 0 0 1.1em 0 }
.model-text-cont p         { margin: 0 0 1.1em 0 }
.model-text ul/ol          { margin: 0.8em 0 1.1em 0; line-height: 1.6 }
.model-text-cont ul/ol     { margin: 0.8em 0 1.1em 0; line-height: 1.6 }
.model-text li             { margin-bottom: 0.8em; line-height: 1.6 }
.model-text-cont li        { margin: 0 0 0.8em 0; line-height: 1.6 }
```

⚠️ DO NOT revert these to 0.4em / 0.3em / 0.15em / 1.3 — those are the OLD values and produce cramped output.

The DOMINANT rule (highest specificity, wins over all others) is the combined block at ~line 1696:
```
.message ul, .message ol, .model-text ul, .model-text ol, .user-text ul, .user-text ol
  { margin: 0.8em 0 1.1em 0; line-height: 1.6 }

.message ul li, .message ol li, .model-text ul li, .model-text ol li, .user-text ul li, .user-text ol li
  { margin: 0 0 0.8em 0; line-height: 1.6 }
```
⚠️ DO NOT revert this block — it has higher specificity than the single-class rules below it and will always win. This is the block that actually controls list spacing.

---


### `index.html`
**Bug fix: "Most Recent" sort option missing from chat sidebar dropdown**
- Option had been lost from the `<select>` HTML — only Newest/Oldest/A-Z remained
- `sortChatList()` was also missing the `most_recent` branch entirely
- Fix 1: Added `<option value="most_recent">Most Recent</option>` back to dropdown (between Oldest and A-Z)
- Fix 2: Added `most_recent` sort case — sorts purely by `b.modified - a.modified` (last-active chats first, distinct from Newest which uses filename date)
- Fix 3: Added dropdown restore at top of `sortChatList()` — syncs `<select>` to saved `chatSortMode` in localStorage on every load
- ⚠️ Root cause of repeated disappearance: dropdown had no matching option for the saved localStorage value, so it silently fell back to first option visually — appeared broken each reload. Restore logic prevents this recurring.

---

## Session: May 14 2026 — Config Tab CSS Fix

### `config.html`
**Bug fix: Tab panels all visible simultaneously — tabs appeared broken**
- Root cause: tab CSS (`display:none` / `display:block` on `.config-tab-panel`) only existed in style.css
- style.css had not been updated on the server yet, so no hide/show rules applied — all panels rendered at once
- Fix: tab CSS now embedded directly in a `<style>` block in config.html `<head>` — self-contained, can never get out of sync with style.css again
- style.css copy of the tab CSS can remain as-is (harmless duplication)

---

## Session: May 14 2026 — Project Modal Tweaks

### `index.html` + `style.css`
- Modal z-index raised to 9500 — now sits above the input bar
- Modal `padding-bottom: 70px` + `height: calc(100vh - 130px)` — clears input bar at bottom
- Cards narrowed: grid minmax 200px → 160px (fits ~6 cols on wide screen)
- Active project label moved to absolute centre of top strip
- Create form pushed to the right with `margin-left: auto`
- Card click → `switchProject()` (if not already active); active card `cursor: default`
- Switch button (↻) removed — redundant now card itself is clickable
- `editBtn` and `deleteBtn` onclick now use `e.stopPropagation()` so they don't trigger card switch

---

## Session: May 14 2026 — Project Modal Grid Redesign

### `index.html`
**Feature: Project Management modal redesigned as full-width card grid**
- Modal HTML restructured: removed verbose Create section (name + instructions textarea + hr blocks)
- New compact top strip (`#project-modal-top`): active project name on the left, quick-create input + button on the right
- Grid area (`#project-modal-grid-wrap`) is a scrollable div that fills remaining modal height
- `#projects-list` now renders into the grid wrapper
- `createProject()` patched: instructions element now optional (null-safe) — instructions added via Edit after creation
- Active project card gets `.is-active` class for green border highlight

### `style.css`
**Feature: Project modal CSS overhauled for fullscreen grid layout**
- `#project-modal`: `padding-left: 250px` to clear chat sidebar, centred
- `#project-modal .modal-content`: `width: calc(100vw - 310px)`, max 1200px, `height: calc(100vh - 60px)` — near fullscreen
- `#project-modal .modal-body`: flex column, no padding (strip + grid each own their spacing)
- `#project-modal-top`: compact flex strip with active indicator and inline create form
- `#projects-list`: switched from `flex-direction: column` to CSS grid (`auto-fill, minmax(200px, 1fr)`)
- `.project-item`: cards — flex column, name at top (2-line clamp), action buttons along bottom
- `.project-group-header`: `grid-column: 1 / -1` so group labels span the full grid width
- `.project-group-children`: `display: contents` so child cards slot directly into parent grid
- `.back-to-global-item`: also spans full grid width
- Active card (`.is-active`): green border + tinted background

---

## Session: May 14 2026 — Sampling Sidebar Compact Redesign

### `style.css`
**Improvement: Sampling sidebar too large and spread out — full compact pass**
- Sidebar width reduced 275px → 240px; `#config-page #main` padding-left matched
- New `#sampling-sidebar *` block overrides the global `#config-page *` 15px font-size — sidebar now 12px throughout
- Labels: margin tightened to 5px top / 2px bottom, color #999 (secondary)
- Inputs: padding 6px 10px → 3px 7px, height 26px, border-radius 3px
- Selects and buttons: height 26px, padding 4px 8px, font-size 12px
- h3: 13px uppercase with letter-spacing — acts as a section divider rather than a page title
- hr: margin 10px (was ~20px), border-color #2a2a2a
- Removed `#sampling-sidebar` from the shared section-header h3 rule (now handled by compact block)

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

---

## Session: May 16 2026 — Ren'Py LoRA Training + Paste Display Fix

### Helcyon Training — Ren'Py Script Continuation LoRA
- Community feedback received: user requested Ren'Py 7 script continuation capability (continue .rpy files as drop-in valid script, no commentary)
- Root cause identified: models defaulting to prose narration or commentary instead of raw script output — behavioural problem from RLHF, not a capability gap
- Created dedicated Ren'Py training set: 35 ChatML shards + 8 DPO pairs (43 files total)
- ChatML shards cover: scene continuation, new scene from spec, menu branching, Python variables/conditionals, multi-scene sequences, varied genres (fantasy, sci-fi, horror, drama, comedy, historical, contemporary)
- DPO pairs target specific failure modes: preamble/commentary, markdown code fences, mid-scene narration, stopping to ask for confirmation, summarising input before continuing, offering multiple options instead of writing
- LoRA trained: r=16, lora_alpha=32, lr=2e-4, 5 epochs on RunPod A100
- Merged into Nebula at 0.85 (creative writing LoRA stack position, after RP layer) — partial improvement, prose still bleeding into show statements
- Remerged at 0.95 — further improvement, structure correct, but show statements still contain prose descriptions as invented syntax
- Conclusion: base knowledge partially present but not strong enough for LoRA to fully surface — full weight training required for clean consistent output
- Plan: full weight run to be done regardless; freelance offer made to community user who requested the feature
- Current Nebula release: meaningful improvement over untrained model, viable for users willing to clean up occasional show statement syntax

**Key learning: LoRA merge scale for narrow task LoRAs**
- r=16 at 0.95 on a dedicated narrow-task LoRA does not bleed into normal conversation tone
- Low rank contains the footprint — safe to go to 0.95-1.0 for format-specific tasks
- General personality LoRAs still need lower scales (0.65-0.75) to avoid tone bleed

### `index.html`
**Fix: Pasted multiline content (e.g. code, Ren'Py script) displayed collapsed in user bubble**
- Root cause: user bubble built with `innerHTML` which collapses `\n` to spaces in HTML rendering
- Fix: newlines converted to `<br>` before setting innerHTML in the user message bubble
- Change: `input.replace(/\*(.*?)\*/gs, "<em>$1</em>")` → `input.replace(/\n/g, "<br>").replace(/\*(.*?)\*/gs, "<em>$1</em>")`
- Display only fix — content sent to model was always correct, this was purely visual
- Quality of life improvement: pasted code, scripts, and multiline prompts now display correctly in chat


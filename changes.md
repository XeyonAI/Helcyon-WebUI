> **Older entries archived by month:** [March 2026](changes-archive-2026-03.md) · [April 2026](changes-archive-2026-04.md) · [May 2026 (pre-31)](changes-archive-2026-05.md)
> This file holds the current (May 31 – June 1 2026) entries only.

## Session: Jun 10 2026 — `<|imended|>` fuzzy backstop: catch sampler-mangled near-miss ChatML markers at end-of-stream (app.py:6919 area)

### Bug
A response rendered with the literal string `<|imended|>` at the end. Not a model/truncation failure — the reply was complete. Mechanism (recon Jun 10): `<|im_end|>` is not a vocab token (6 BPE pieces, caught only as a string stop-word). The prompt contains `<|im_end|>` verbatim after every message; with `dry_penalty_last_n: -1` and `dry_allowed_length: 2` (pre-`8f22a67`), DRY penalized verbatim continuation of the marker from piece 3 on, swerving the sampler to a near-neighbour → `<|im` + `ended` + `|>`. The near-miss then evades **both** layers: the server-side stop-string match (not exact) and every HWUI strip rule — all of them (strip_chatml_leakage AND the `_filtered_stream` end-of-stream backstop) key on exact underscore spellings (`im_end`, `_end|>`, `end|>`), which `imended` never matches. It passed through fully intact.

### Fix — fuzzy terminal-marker rule in the `_filtered_stream` end-of-stream backstop (`app.py` ~L6930)
One added `re.sub`, run on the final `_tail` immediately **before** the existing exact-fragment backstop:
- `r'<\|im\w*\|>$'` — matches the marker **shape** (`<|im` + word chars + `|>`), not a spelling: catches `<|imended|>`, `<|imend|>`, `<|im_end|>` (`\w` includes `_`), etc.
- **End-anchored (`$`) and full-envelope only** — both the `<|im` head and the `|>` tail are required, so prose/code containing `<|`, `|>`, or an unclosed `<|im…` mid-text is never touched. Verified against 8 cases: mangled/exact markers at end → stripped; plain prose, trailing `|>`/`>` operators, mid-sentence `<|`, unclosed `<|imagine…`, and a mid-text (non-`$`) marker → all untouched.
- Ordering: runs first so a complete `<|im_end|>` tail is removed whole (the pre-existing fragment alternation alone strips its `im_end|>` half and leaves `<|` behind).
- ⚠️ Deliberately NOT added to per-chunk `strip_chatml_leakage` — un-anchored fuzzy matching on live chunks risks false positives; this net is end-of-stream `_tail` only.

Untouched by design: existing exact-match strip rules, `strip_chatml_leakage()`, sampler/DRY settings, the `stop` array. The DRY `allowed_length` 2→10 bump (`8f22a67`) likely prevents the mangle at source; this is the display-layer safety net behind it. `py_compile` clean. **Restart the main Flask app to apply.**

---

## Session: Jun 10 2026 — Chat-sync: stale desktop tab no longer overwrites newer mobile messages (dirty flag + base_count guard)

### Bug
Chat started on desktop, continued on mobile (file on disk grows), then the still-open desktop tab is refreshed → the mobile messages vanish. Root cause: the June 6 pagehide beacon (`_beaconFlushChat`, index.html) fires on a plain refresh too, and flushes the tab's **stale in-memory** `window.loadedChat` to `/chats/save` — a blind full-file overwrite — so the reloaded page reads back the clobbered file.

### Fix 1 — client dirty flag (`templates/index.html`, `utils/utils.js`)
- New globals `window._chatDirty` / `window._chatBaseCount` (index.html ~L915, by the other globals).
- Cleared/synced (`_chatDirty=false`, `_chatBaseCount=<count>`) in `openChat()` right after the disk load populates `loadedChat` (~L2982) and in `autoSaveCurrentChat()`'s success path (~L4296 — uses the **filtered** `messages.length`, since hidden turns never reach disk).
- `_chatDirty=true` at every `loadedChat` mutation: user send (both image/text branches), stream + non-stream completion pushes, message edit, continue (splice **and** final push), regenerate splice, delete-message splice, opening-line push (`utils.js displayOpeningLineInChat`) and RP-opener push.
- `_beaconFlushChat()` now bails when the tab is clean AND no stream is in flight: `if (!window._chatDirty && !_inFlightPartial) return;`. ⚠️ The June 6 in-flight protection is intact — `window.isSending && window._streamingPartial` counts as dirty, so navigating away mid-stream still flushes the partial. DO NOT remove the `_inFlightPartial` half of the guard.

### Fix 2 — server stale-write guard (`chat_routes.py`)
- `open_chat()`'s parser extracted **unchanged** into `_parse_chat_file(filepath, filename, verbose=True)`; the route's behaviour/response is identical (`verbose=False` only gates the per-line speaker logs so the guard doesn't spam on every autosave).
- `/chats/save` and `/chats/update` accept an optional `base_count` int (the count the client believed was on disk). If present AND `disk_count > base_count` AND `len(incoming) <= disk_count` → **HTTP 409** `{"status":"stale","disk_count":N}`, no write (`_check_stale_save`). A guard-side parse failure never blocks a save (logged, falls through).
- `base_count` absent (mobile `saveChatToDisk`, `/chats/update` delete-last caller, legacy/manual callers) → behaviour completely unchanged.
- Same-tab destructive ops (delete / regenerate / clear) still pass: there `disk_count == base_count`, and a genuinely-new-turns save has `incoming > disk_count`.

### Fix 3 — client 409 handling (`index.html`, `autoSaveCurrentChat`)
On 409 the tab logs a console warning (no aggressive alert) and re-runs `openChat(currentChatFilename)` to adopt the newer disk state. Beacon 409s are unobservable by design — the dirty flag prevents the common stale-refresh case client-side; the server guard is the second line of defence for a dirty-but-behind tab (its unsynced turn is sacrificed in favour of the newer disk copy).

**Restart the main Flask app to apply** (chat_routes.py + template changes; server runs without reloader).

### Follow-up 1 — hidden-turn count parity VERIFIED (no code change)
Confirmed `base_count` (client) and `disk_count` (`_parse_chat_file`) count the same set, so hidden turns can't cause off-by-N false 409s. Hidden turns are created in exactly one place — the memory/web-search confirm trigger (`index.html:5638`, `hidden:true`). Both client save paths filter them out **before** writing (`autoSaveCurrentChat` L4245, beacon L5704), so the file physically contains zero hidden turns and the parser can't see them. `base_count` is itself derived from the non-hidden set on both sides where it's set: `openChat` (loadedChat has no hidden turns right after a disk load — the L2955 mapper doesn't carry `hidden`) and `autoSaveCurrentChat` success (`messages.length`, the filtered count). Round-trip test (client filter → `_format_chat_messages` → `_parse_chat_file`) over 5 shapes incl. 1× and 2× hidden web-search-confirm turns, a non-hidden opening line, and multi-paragraph content: `base_count == disk_count` in all cases. Note: parity relies on the **non-hidden** parser round-trip being 1:1 (verified for these shapes) — a future change that makes the parser split/merge written turns would break the count comparison independent of hidden handling.

### Follow-up 2 — close the clearChat() blind-overwrite hole (`index.html`, `clearChat` ~L4761)
The lone `/chats/update` caller (`clearChat()`, sends `messages: []` — last session's "delete-last" label was a misnomer; it's the clear-chat path) was the last write with no `base_count`, so a stale tab could still wipe a chat that grew on another device. Now sends `base_count: window._chatBaseCount`, and on a **409** logs + `openChat(currentChatFilename)` reload instead of throwing/toasting (mirrors the autoSaveCurrentChat 409 handler). Current-tab clears still pass (`disk_count == base_count` → guard's `disk_count > base_count` is false); a stale-tab clear is rejected and the tab re-syncs, after which a deliberate re-clear succeeds. Client wiring only — `update_chat()`'s server guard already existed.

---

## Session: Jun 10 2026 — train_lora.py: same EOS + dynamic-padding fix as full_train.py; base → x5-v2

### `train_lora.py` (RunPod training side — NOT in HWUI build)
Mirrors the full_train.py "EOS fix part 3" (entry below) onto the LoRA script, plus the base switch:
- **`MODEL_PATH`** → `{BASE}/helcyon-x5-v2` (was `{BASE}/helcyon-gpt-4o-base-x6` — the x6 base is poisoned by the zero-EOS-supervision bug; variants must train on the corrected rebuild). ⚠️ Path set verbatim as instructed — **confirm the exact folder name on the pod before launching** (the old value was `helcyon-gpt-4o-base-x6`, not `helcyon-x6`, so naming may differ for the rebuild too).
- **EOS append, in-loss**: `tokenize()` truncates content to `MAX_LEN-1` (1511) and appends `tokenizer.eos_token_id` (attention 1). After the assistant-section unmasking, `labels[-1] = input_ids[-1]` puts the appended `</s>` IN the loss — the LoRA's stop signal now matches the x5-v2 base. Truncation can never cut it off (verified: 3× shard → exactly 1512, final token id 2, label 2).
- **Dynamic padding**: `padding="max_length"` (1512-tail) removed; `collate_fn` pads to longest-in-batch (pad: attention 0, labels −100, by POSITION never by token id). **`MAX_LEN` stays 1512** — intentional per-dataset ceiling, unchanged.
- **KEPT untouched**: assistant-only response masking (verified still active — 798/946 and 307/432 in-loss tokens on the two test shards, not whole-sequence), distinct-pad assert, LoRA config (r32/α48), training args.

**2-shard verification (live Tekken tokenizer):** BEFORE — id 2 present in zero sequences, label==2 count 0, pad tails 37.5% / 71.5% of 1512. AFTER — both end `…<|im_end|></s>` with final token id 2, label 2 (in-loss), pre-batch padding none; batch-of-2 dynamic pad 0% / 54.3% (vs 37.5% / 71.5%), EOS label survives padding. `py_compile` clean. (Note: `_shard_scan/train_lora.py` is an older divergent copy — left untouched; the repo-root script is canonical.)

⚠️ Same guards as full_train.py: DO NOT remove the EOS append, let truncation eat the final `</s>`, or reintroduce `padding="max_length"`.

---

## Session: Jun 10 2026 — stop_type=None root cause FOUND (b8994 parser crash); reasoning_format fix FALSIFIED

### Serving-side finding (no code change shipped today — app.py reverted to pre-test state)
**Root cause of the `stop_type=None` stream deaths (the "unknown" truncations):** llama-server b8994 re-parses the accumulated generated text on every streamed token, and that parser **throws when the text ends in a partial/invalid UTF-8 token piece** (`"Failed to parse input at pos 0: <accumulated text>"` — string lives in `llama-common.dll`, chat/PEG-parser family). The server then emits an SSE `{"error": {code: 500, …}}` event and kills the task mid-stream with NO final stop event. HWUI's SSE loop silently swallows the error event (no `content`/`stop` keys) → the response just stops, `stop_type=None`. Reproduced deterministically per seed, direct against the server, Flask fully out of the loop.

**`reasoning_format: "none"` was tested and FALSIFIED** — added to the /chat payload, 10-seed A/B on the same prompt: 10/10 abnormal deaths before, 9/10 after (identical per-seed death points). Also falsified: per-request `chat_format: 0`, launch flag `--skip-chat-parsing`, and removing `--chat-template chatml` — **no payload key or launch flag disables the crashing parse on this build**. Even a minimal payload (no DRY/penalties/ban/stop) reproduces it. The payload key and its comment were removed from app.py again.

**Bonus diagnostic (answers "is the foreign token winning?"):** `n_probs` at an exact break position shows a **flat noise distribution** — top candidate `衆国` at ~0.1%, sampled ` międzyn` (id 119980) at ~0.08%, reserved ids tied right behind. Nothing bypasses the sampler; the model has no prediction at all in these states (consistent with the zero-EOS-supervision training hole — see the full_train.py entry below). min_p can't filter it because the top token is equally tiny.

**Mitigation path (future work, NOT done today):**
1. A different llama-server build (the crash is b8994-intrinsic; the I:\llama.cpp checkout at f9ec8858e is a different/older state than the b8994 binary, which is not built from this tree).
2. HWUI: make `stream_model_response()` detect `{"error": …}` SSE events and surface them (log + stop-reason) instead of silently swallowing — turns invisible deaths into visible diagnostics.

---

## Session: Jun 10 2026 — full_train.py: EOS fix part 3 — explicit `</s>` append + dynamic padding (x6 poison root cause)

### `full_train.py` (RunPod training side — NOT in HWUI build)
**Yesterday's "EOS fix" (Jun 9 entry below) corrected the pad/eos collision but was INCOMPLETE — it missed the explicit-EOS-append and left the max_length pad-tail, and together those poisoned x6** (rebuilt on the corrected masking, still emits foreign-script/reserved-token garbage at stop-points).

**Verified on real shards before fixing** (3 real `_shard_scan/` shards + correctly-formatted constructed DPO/CX samples, run through the exact current `strip_structural_lines()` + `tokenize()` against the live Tekken tokenizer):
- **id 2 (`</s>`) appeared in ZERO sequences.** The HF Nemo/Tekken tokenizer adds BOS but NOT EOS (`add_eos_token=False` default), and the freeform shard text contains no `</s>` — so nothing ever appended one. Every shard's final real tokens were the six plain-text `<|im_end|>` pieces (`…1060 1124 1329 23836 1124 1062`). The masking fix made `</s>` *visible to the loss* — but there were no `</s>` tokens in the data to see. The model gets no end-of-sequence supervision at all; P(next | end-of-content) is left to undertrained base-vocab drift → garbage at stop-points.
- **Pad tails**: `padding="max_length"` padded every shard to 1024 with pad id 10 — 7.7% / 5.9% / 57.9% pad on the three real shards (>90% on short DPO/CX-style ones).

**The fix (tokenize + collate, verified by re-running the same shards):**
1. `tokenize()` now truncates content to `SEQ_LEN-1` (1023) and **appends `tokenizer.eos_token_id` to every sequence** — attention_mask 1, label = id 2 (IN the loss). Truncation can never cut the EOS off (verified: a >1024-token text lands at exactly 1024 ending `…[1046, 101460, 1435, 8381, 2]` = `'. Respond as someone</s>'`).
2. `padding="max_length"` removed; **padding is now dynamic per batch in `collate_fn`** (pad to longest-in-batch with the distinct pad id; attention 0 / labels −100 on pad positions only — masked by POSITION, never by token id).
3. **Kept from yesterday (correct):** distinct pad token (id 10 ≠ id 2) + the hard `assert pad_id != eos_id`.

**Re-verification numbers (same shards, fixed code):** every sequence now ends `…<|im_end|></s>` (or `…immediately.</s>` for label-stripped CX) with `</s>` at the final position, attention 1, label 2; EOS label survives batch padding; pad is batch-relative (0–2% for like-sized ChatML shards) instead of a fixed 1024 tail.

⚠️ DO NOT remove the EOS append, DO NOT let truncation eat the final `</s>`, DO NOT reintroduce `padding="max_length"`, and DO NOT revert the by-position label masking. Any one of these reopens the stop-point garbage. `MODEL_NAME` / `BASE_MODEL_PATH` untouched (owner-set).

---

## Session: Jun 10 2026 — Sampler: window the basic repeat penalty (repeat_last_n -1 → 256); DRY untouched

### `settings.json` + `app.py`
**Bug: foreign-script breakout at stop-points, surviving the special-token ban (Fix 1, earlier today)**

Symptom: after banning ids 14–999 (`RESERVED_SPECIAL_BAN`), the garbage tail did not stop — the spillover moved from `<SPECIAL_*>` tokens into foreign-script *text* tokens. Same mechanism, next victim class.

Root cause: the full-context basic repeat penalty hollows the distribution at stop-points. With `repeat_last_n: -1` (= 16384), `repeat_penalty: 1.1` applies ÷1.1 to **every individual token that has appeared anywhere in context, on every sampling step** — in a long chat that is essentially the entire useful vocabulary (every common word, punctuation, newline). Never-seen tokens (untrained specials, foreign scripts) are untouched, gain a relative boost, and only need to clear `min_p` once at a hollowed stop-point. Banning specific victim classes (Fix 1) treats symptoms; the hollowing is the disease.

Fix: `repeat_last_n: -1 → 256` in three places (same pattern as the Jun 3 dry_allowed_length fix — all copies must move together or a missing key silently reverts):
- `settings.json` — `repeat_last_n: 256` (re-read per request — live immediately, no restart needed for this part).
- `app.py` payload fallback (~L5603) — `sampling.get("repeat_last_n", -1)` → `sampling.get("repeat_last_n", 256)`; comment block rewritten to document the division of labour.
- `app.py` defaults dict in `load_sampling_settings()` (~L7768) — `"repeat_last_n": -1 → 256`.

**DRY left completely untouched:** `dry_multiplier 0.8, dry_base 1.75, dry_allowed_length 10, dry_penalty_last_n: -1`. Evidence basis (Jun 3 Harness B control): DRY owns the distant-verbatim/self-poisoning protection — DRY off → 100% copy of a 151-token passage **even with the full-context basic penalty active**, i.e. the basic penalty's full-context reach carries none of the distant-copy guard. Windowing it to 256 keeps its real job (short-range looping/stutter suppression) while ending the full-context hollowing.

Verified against the live b8994 server's `generation_settings` echo (1-token probe with the exact new params): `repeat_last_n: 256` (honored as-is, NOT expanded) and `dry_penalty_last_n: 16384` (-1 expanded to full ctx) — independent windows confirmed.

Watch-item (the one regression surface): sub-10-token distant phrase echo. A pet 3–8-token phrase recurring from >256 tokens back was previously soft-damped by the full-context basic penalty and no longer is; DRY only fires on verbatim runs >10 tokens. Stylistic only — NOT the verbatim self-poisoning loop (that is DRY's, intact). If it shows up, the lever is the `repeat_last_n` window value (e.g. 512/1024), not DRY.

⚠️ **DO NOT revert `repeat_last_n` to -1** — reopens distribution hollowing / garbage tails at stop-points. **DRY at `dry_penalty_last_n: -1` is the distant-repeat guard, not this.** ⚠️ DO NOT touch the DRY quartet (0.8 / 1.75 / 10 / -1) — reopens the Jun 1 verbatim-copy bug (supersedes the Jun 1 note's "repeat_last_n: -1 Kept" — the pair-language there predates the hollowing discovery).

**Restart the main Flask app** to make the fallback/default code live (the settings.json value itself is already live per-request).

---

## Session: Jun 10 2026 — Sampler: hard-ban reserved special tokens (ids 14–999)

### `app.py`
**Bug: garbage tails / truncation with `stop_type=None` — model sampling untrained reserved special tokens**

Symptom: responses truncate or run into empty-decoding garbage; RAW TAIL diagnostic showed low-id tokens (e.g. 33, 38, 75) decoding to empty strings. Confirmed via live `/detokenize`: those ids render as `<SPECIAL_33>`/`<SPECIAL_38>`/`<SPECIAL_75>` — reserved, untrained Tekken control tokens. Happens even at `eos_logit_bias: 0`, so it is not (only) the EOS-bias deflection.

Root cause (mechanism): penalty over-reach boosts untrained specials above min_p at stop-points. `repeat_penalty 1.1` and DRY both run over the FULL context (`repeat_last_n: -1`, `dry_penalty_last_n: -1`), and penalties only suppress tokens that have already appeared. The `<SPECIAL_*>` ids never appear in context, so at a stop-point where every plausible text continuation has been penalized they gain a relative boost and only need to clear `min_p` once to surface. They decode to empty strings (llama-server renders specials as "" in stream content), match no stop string, and fire no EOS → garbage tail with `stop_type=None`.

Vocab map (verified id-by-id via `/detokenize` sweep of ids 0–1099 against the live b8994 server): ids 0–13 = named specials (`<unk>`, `<s>`, `</s>`=2, `[INST]`…`[TOOL_CALLS]`, `<pad>`, FIM); **ids 14–999 = `<SPECIAL_14>`..`<SPECIAL_999>`, all 986 reserved/untrained**; ids 1000+ = byte/text vocab. `<|im_end|>`/`<|im_start|>` are NOT vocab tokens (each tokenizes to 6 plain text tokens) — stopping is server-side string matching + EOS id 2, none of which touches the banned range.

Fix: new module-level constant `RESERVED_SPECIAL_BAN = [[i, False] for i in range(14, 1000)]` (app.py ~L1058, by `get_stop_tokens`/`strip_chatml_leakage`). The main local `/chat` payload now ALWAYS sets `payload["logit_bias"] = list(RESERVED_SPECIAL_BAN)` and appends the existing `[2, eos_logit_bias]` entry on top under the same condition as before (`not ignore_eos and bias != 0`). Python `False` → JSON `false` → −inf in b8994 (server-task.cpp:362–419 parses `[id, false]` as a hard ban; ids must be listed individually — no range syntax exists). Result: 986 ban entries always, +1 EOS entry when the soft bias is active. Id 2 (`</s>`) is NOT in the banned range — EOS stays sampleable, stopping unaffected. Local-model `/chat` path only — vision and cloud payloads untouched.

Also adjusted the per-turn `🩺 PAYLOAD` log (same block): `logit_bias` is compacted to a summary string (986 raw pairs would drown the log), and `eos_logit_bias` is reported from the decision variables instead of `logit_bias[0][1]` (index 0 is now a ban entry, not the EOS entry).

⚠️ DO NOT revert — removing the ban reopens the `<SPECIAL_*>` garbage-tail bug. ⚠️ Note: this is the inference-side guard; the training-side fix (EOS-masking, Jun 09 entry below) still gates the fleet rebuild verdict.

**Restart the main Flask app to apply.**

---

## Session: Jun 09 2026 — Training scripts: EOS-fix verified + settled-decision notes

### `full_train.py` / `train_lora.py` (RunPod training side — NOT in HWUI build)
**Root cause of the months-long mid-sentence truncation is FIXED and verified line-by-line this session. Committing to a full fleet rebuild from Mistral-Nemo base on the corrected `full_train.py`.**

**The bug (now fixed in `full_train.py`):** old code did `if pad_token is None: pad_token = eos_token`. Nemo has no pad token, so pad became `</s>` (id 2). Combined with the old `labels[labels == pad_token_id] = -100`, every real `</s>` was masked out of the loss — the model never learned clean EOS and reached for `</s>` mid-stream. This is the propensity the inference-side `eos_logit_bias` has been fighting all along.

**The fix (two halves, both confirmed correct):**
1. Pad is forced DISTINCT from eos (`<pad>` from vocab → `<unk>` → new `[PAD]` + resize), with a hard `assert pad_id != eos_id` that aborts the run rather than train a broken model.
2. Labels masked by `attention_mask == 0`, NOT by `pad_token_id` — so only the padded tail is masked and every genuine `</s>` stays in the loss. Robust even if pad/eos ever coincided again.
`model.config.pad_token_id`/`eos_token_id` synced; embedding resize conditional (no-op unless `[PAD]` added). ⚠️ DO NOT revert either half.

**`train_lora.py`** got the same distinct-pad insurance + assert (it re-masks labels from scratch so it was never broken the same way, but the landmine is now removed). 1512 max_length retained intentionally for the LoA set (a few shards exceed 1024); this is a per-set choice, not a default.

**Test verdict plan (the truncation only surfaces UNDER LOAD — Set 2 trained + full LoRA stack merged, 8+ LoRAs or merge scale >0.9):** rebuild x5 → full-train a Set 2 (4o) on clean x5 → merge full LoRA stack at normal scale → test FRESH multi-turn past turn 8–12 at `eos_logit_bias: 0`. Testing the bare base proves nothing; the loaded config is the only real verdict. Free non-GPU fallback levers if needed: merge scale → 0.75–0.8, fewer LoRAs.

### ⚠️ Two SETTLED decisions — do NOT re-flag as bugs in future sessions
1. **`strip_structural_lines()` removes `Instruction:`/`Prompt:`/`Response:`/`Chosen:`/`Rejected:` etc. lines before tokenizing, then trains the remaining flat text with labels on everything (mask pad only).** This is Chris's intentional method — scaffolding stripped, content trained as one block, no user/assistant token separation. NOT a bug. Do not "fix" it.
2. **`BUCKET_PATH = "gs://..."` + the printed `gsutil` upload line in `full_train.py` is STALE.** Chris uploads via **rclone to Cloudflare R2**, not GCS. The line is a harmless end-of-run console print (does not affect the weights), but ignore it — upload with the normal rclone/R2 command. (`train_lora.py` already uses the correct `r2:helcyon/...` rclone path.)

### ⚠️ Companion-vs-assistant balance — TUNED IN PROMPT, deliberately kept out of base/LoRA
The "assistant bleed" (model treats a stated feeling as a problem to identify → explain → solve, instead of staying with the experience) was a real early-Helcyon trait. It has been **solved in the PROMPT layer**, not the weights. History: companion-exploration instinct was installed (helcyon-gpt-5.5 shards), then it **overshot** — sat with problems ad nauseam, never landed the plane / took a position — and was **dialled back in the prompt** to a working balance. This behaviour is **context-dependent** (companion-exploration on emotional-disclosure turns; land-the-plane directness on requests for a position/decision/truth) and therefore must stay tunable in the prompt — NOT baked into base or a fixed LoRA where the dial is lost and it would double-weight against the prompt and re-overshoot. Do NOT add "stay with feelings / explore don't solve" shards to Set 2 base. The slight residual instinct-to-explain is intended headroom for directness. Note: helcyon-gpt-5.5's deep-dive/questioning shards gave it genuine presence and it is now the BASE for the 4o variant (4o personality layer trained ON the 5.5 base — not cross-merged).

### LoRA reuse on the x6 rebuild — no dataset changes this round
Because no datasets are changing, all existing LoRAs are reused — no fresh behavioural data needed. Plan:
- **Foundational + behavioural LoRAs** (Layer 1: abliterated, admin, context-tracking, roleplay; Layer 2 non-personality: creative writing) → reuse as-is on x6, train fleet-wide on x6 base. Likely fine.
- **Personality/tone LoRAs** → the ONLY layer that needs per-variant training; train on the variant base (e.g. 4o personality on the 5.5-derived base).
- ⚠️ **Caveat:** LoRAs were trained on x5; x6 is the same recipe but a freshly trained weight set, so x5-trained LoRAs are deltas against slightly different weights. Very likely transfer fine (small r=16–32 adapters), but NOT guaranteed identical. **Gate on the loaded multi-turn test at `eos_logit_bias: 0`** — if 4o-on-x6-with-full-stack behaves like known-good 4o AND fires clean EOS, that single test confirms both the EOS fix and clean LoRA transfer. If character feels muddy (not EOS — tone) → retrain only the affected LoRA on x6, not the whole set. (Chris has dropped the old per-variant-everything habit — perfectionism right-sized; only personality LoRAs are variant-trained now.)

---

## Session: Jun 09 2026 — Mobile: autolink bare URLs in chat messages

### `templates/mobile.html`
**Bug: links shown as the full literal string on the mobile app**

Symptom: on the mobile chat page, a URL in a reply rendered as the raw `https://…` text instead of a clickable link (desktop was fine).

Root cause: mobile uses a small inline `marked` stub (mobile.html ~L10), not the real marked.js that desktop loads. The stub only linkified markdown `[text](url)` syntax — it had no autolinking for **bare** URLs. Since search results and the model usually emit raw URLs unwrapped, those got HTML-escaped and displayed as the full string. Desktop didn't show this because real marked.js runs with `gfm:true`, which autolinks bare URLs.

Fix: added a GFM-style bare-URL autolinker to the stub's `inline()`, placed after escaping and before the markdown-link placeholder restore so:
- wrapped `[text](url)` links are untouched (already swapped to placeholders earlier);
- bare `http(s)` URLs become `<a target="_blank" rel="noopener noreferrer">` — same hardening as the wrapped path (http/https only, so `ftp:`/`javascript:`/`data:` stay literal);
- trailing sentence punctuation (`. , ) ! ?` …) is left outside the link, with GFM paren-balancing so Wikipedia-style `_(disambiguation)` URLs keep their closing paren.

Verified the parser logic in isolation (bare, wrapped, mixed, parenthesised, and non-http cases). **Frontend-only — hard-refresh the mobile page; no Flask restart needed for the JS.**

---

## Session: Jun 09 2026 — Startup: force backend_mode back to local on every launch

### `app.py`
**Bug: after a cloud session, a fresh launch couldn't talk to the local model**

Symptom: reboot HWUI while it was last connected to a cloud API (OpenAI/Anthropic), load a local model and type — the model never responds and the chat shows "⚠️ Local backend unavailable. Cloud API is disabled. Check that llama.cpp is running."

Root cause: the startup-safety block (app.py ~790) already force-reset `cloud_api_enabled` to `false` on every launch, but left `backend_mode` untouched. So a prior cloud session left disk in the state `(backend_mode="openai"/"anthropic", cloud_api_enabled=false)`. The first message then hit the cloud master gate (app.py ~5138), which refuses a cloud backend_mode when the master switch is off (no silent local fallback, by design) and returns the 503 above — even with llama.cpp running and a model loaded.

Fix: extended the same startup-safety block to ALSO reset `backend_mode` to `'local'` on every Flask launch, in the same atomic settings.json write. A fresh launch now always starts on the local model; switching back to a cloud provider is an explicit Connect action (unchanged). Startup log line now reports both resets:
`🔒 Startup: cloud_api_enabled forced to false (was ...), backend_mode reset to 'local' (was '...').`

No frontend change needed — the cloud status indicators already key off `cloud_api_enabled` (also forced false), so the UI reflects the local/disconnected state on load. Same rationale as the existing cloud reset: a cloud selection must never persist across restarts.

**Restart the main Flask app to apply** (the fix runs at startup, so the restart itself triggers it).

---

## Session: Jun 09 2026 — Web Search Trigger: Three-Tier Architecture

### `app.py`
**Feature: Promoted unambiguous factual question patterns to fire search without consulting the intent gate**

Root cause: The intent gate uses the same loaded local model as main chat. When asked to classify factual questions ("Who won the F1 race yesterday?", "What's the name of that cholesterol medication?"), the model trusted its own (confabulated) knowledge and returned NO_SEARCH — then went on to confabulate an answer in the actual reply. Same Helcyon-Grok confident-persona on both sides of the call.

Architecture changed from two tiers to three:
- TIER 1 EXPLICIT (`_explicit_pat`) — imperative search requests → fire immediately (unchanged)
- TIER 2 FACTUAL (`_factual_pat`) — unambiguous info-seeking patterns (`who won/wrote/invented...`, `who's the current/new/next...`, `what's the name/brand/price/cost of`, `where can/do I buy/get/find`) → fire immediately, NO gate consultation (NEW)
- TIER 3 AMBIGUOUS (`_ambiguous_pat`) — genuinely ambiguous patterns (`find out`, `look up`, `any news on`, `what's that <noun>`, `do you know`, `when did X`) → consult `_search_intent_gate()` (unchanged behaviour)

⚠️ DO NOT collapse the factual tier back into ambiguous — the local model is constitutionally unable to classify factual questions correctly when it's also the model that confabulates the answers. The three-tier structure exists specifically because the gate is unreliable for clear factual cases.

**Improvement: Intent gate reformulated as chat-format few-shot**
- Replaced the zero-shot classifier system prompt with proper chat-format few-shot (8 example user/assistant pairs in the messages array showing NO_SEARCH and SEARCH: <query> verdicts).
- max_tokens lowered from 32 to 16 — forces the model to commit to a one-line verdict rather than drifting into a conversational answer.
- The old prompt was being interpreted as a chat system prompt rather than a classifier prompt — the model just answered the question. Few-shot pattern conditioning locks it into classifier mode.

**Diagnostic: model-emitted [WEB SEARCH:] tag suppression now logs loudly**
- The suppression branch at line ~6290 (fires when `_should_search` is False but the model emits a trained `[WEB SEARCH: query]` tag mid-stream) now prints a multi-line banner including the user message and the suppressed query. Behaviour unchanged — tag still excised from output. Logging only, for future diagnostic use.

**Curiosity-style trigger phrases added to ambiguous tier**
- `what(?:'s| is| are) (?:that|the|a|those|these) <word>` — catches "what's that BBQ sauce"
- `do you know (?:what|who|when|where|why|how|if|whether|the|a|that|anything)`
- `can you find out`
- `(?:any|got an?) (?:idea|clue|thoughts?) (?:what|who|when|where|why|how|if|whether|about|on)`
- `tell me (?:about|what|who|when|where|why|how)`
- `what(?:'s| is) (?:that|the|it) called`
- `when (?:did|does|will|is|was) <word>`

---

## Session: June 9 2026 — TTS servers: default HuggingFace hub to offline

**Stop the per-launch HF network check on the cached TTS models.** The `I:\HuggingFace` folder is the TTS model cache — F5's vocoder (`charactr/vocos-mel-24khz`, ~50 MB) and (when used) chatterbox's `from_pretrained` model. It's downloaded once and reused; it only re-downloads if the cache is deleted. But each launch HF still did an online version-check against the hub.

### CHANGE — `f5_server.py` + `chatterbox_server.py`
- Added `os.environ.setdefault('HF_HUB_OFFLINE', '1')` right after the existing `HF_HOME` lines (set before `huggingface_hub` is imported, so it takes effect). HF now loads cached models with **no network check or re-download**.
- `setdefault` (not a hard set) keeps it **overridable**: launch with `HF_HUB_OFFLINE=0` in the environment to allow a download when you actually need one.
- ⚠️ **Chatterbox caveat (documented in-file):** only the F5 vocoder is currently cached — chatterbox's model is not. Its *first* run must download, so that one time it needs to be launched with `HF_HUB_OFFLINE=0`; afterwards the offline default applies. F5 is unaffected (vocoder already cached).
- Did **not** touch the hardcoded `I:\HuggingFace` path itself — per the earlier decision it's a real, needed cache and has to live somewhere; left as-is.

**Verified:** `py_compile` clean on both files. **Each is a separate TTS server process — restart the relevant TTS server to apply (no main-Flask restart needed).**

---

## Session: June 9 2026 — LoRA Browse: drop hardcoded `I:\LoRA` default

**The LoRA Browse button defaulted the picker to a hardcoded `I:\LoRA` — only valid on the dev machine** (other users have no `I:` drive). Fixed in `config.html` only; `/browse_file` already ignores a missing/invalid `initialdir` and falls back to the OS default, so no backend change was needed.

### CHANGE — `templates/config.html` (LoRA Browse button + placeholder)
- Browse now starts in the **configured Models Folder**, read from the on-page `#llama-models-dir` field (populated from `settings.json` by `loadLlamaConfig`) — `onclick="browseFile('llama-lora-path','lora', document.getElementById('llama-models-dir').value.trim())"`. LoRA `.gguf` adapters naturally live near models, so this is more sensible than a home-dir default, and it's settings-derived (no hardcode). If that field is empty, `browseFile` passes an empty `initialdir` and `/browse_file` falls back to the OS default — same behaviour as the existing exe/mmproj/models-folder browsers, which pass no `initialdir`.
- Placeholder changed `I:\LoRA\adapter.gguf` → `C:\path\to\adapter.gguf` to match the sibling exe/mmproj fields' illustrative convention (no real drive assumed).

**Verified:** no `I:\LoRA` left in any shipped `.html`/`.py`/`.js`; LoRA Browse `onclick` expression passes `node --check`. Frontend-only — hard refresh; no Flask restart. *(Unrelated, pre-existing: `chatterbox_server.py` hardcodes `I:\HuggingFace` for HF cache env vars — left as-is, out of scope.)*

---

## Session: June 9 2026 — LoRA attach/detach: auto-reload the model (no manual restart)

**Follow-up to the LoRA attach feature.** Attach/Detach previously just saved the path and told the user to restart llama.cpp. Now they save, then reload the current model so the `--lora` change is live immediately.

### CHANGE — `templates/config.html` (Attach/Detach handlers only)
- After a successful `POST /save_lora_path`, both handlers call a new local helper **`_reloadModelForLora(savedMsg, activeMsg)`** (sits within the LoRA block) which re-runs **`POST /load_model`** — the same route the model picker uses — with the **current model filename read from `localStorage.llama_last_model`** (the subfolder-relative name set on every picker load; `/get_model` only returns a display basename, so it can't be used as a `/load_model` payload). No path/name is hardcoded.
- Toast sequence — Attach: `💾 LoRA saved — reloading model...` → `✅ LoRA active`; Detach: `💾 LoRA detached — reloading model...` → `✅ LoRA detached`. Reload failure (either path): `⚠️ LoRA saved but reload failed — restart manually`.
- **Graceful fallback:** if no current model is known (`localStorage.llama_last_model` empty — e.g. server auto-launched and no model picked this session), it skips the reload and shows the original `… — restart llama.cpp to apply` toast.
- On reload success it also refreshes the model display via the existing `loadModelDisplay()` (guarded by a `typeof` check).

**Why this works:** `/load_model` (`app.py:~7192`) runs the full restart cycle — `kill_llama_process()` then relaunch via `get_llama_settings()`, which includes `lora_path` — so the relaunched server picks up the new `--lora`.

**Verified:** `node --check` clean on the touched LoRA functions. Only the Attach/Detach handlers (+ their shared helper) changed. No hardcoded paths/usernames. **Frontend-only — hard refresh; no Flask restart** (the reload it triggers restarts llama.cpp, not Flask).

---

## Session: June 9 2026 — LoRA adapter attach/detach (launch-time `--lora`)

**New optional LoRA adapter field in the Llama.cpp config section.** A configured adapter is passed to `llama-server` via `--lora <path>` at launch.

### Hot-attach finding (empirical — probed the live server on :5000)
`/props` → 200, `/lora-adapters` → 200 (`[]`), `/lora` → 404. The build exposes `/lora-adapters`, but POST to it only **re-scales adapters already loaded at launch** via `--lora` — it cannot load a new adapter file by path at runtime. **So true hot-attach is NOT supported.** No `/attach_lora` / `/detach_lora` routes were added; the UI persists the path and prompts for a llama.cpp restart to apply (mirrors how model switching already restarts the server).

### CHANGE 1 — `settings.json`
- Added `"lora_path": ""` (empty = no adapter).

### CHANGE 2 — `app.py`
- `get_llama_settings()` now returns `lora_path`.
- Both launch sites — `auto_launch_llama()` (~L917/951) and `/load_model` (~L7250) — append `--lora <path>` when `lora_path` is set and the file exists (mirrors the existing `--mmproj` conditional).
- New **`GET /get_lora_path`** → `{"lora_path": "..."}` and **`POST /save_lora_path`** (atomic temp-file write — same `tempfile`+`.tmp`+`shutil.move` pattern as the startup cloud-reset block). Empty string clears it.
- **`/browse_file`** gained an optional `initialdir` (honoured only if a real dir) and a `lora` filter — backward-compatible; existing callers unaffected.

### CHANGE 3 — `templates/config.html`
- New "LoRA" row beneath the mmproj row: text input (`#llama-lora-path`), 📁 Browse (defaults to `I:\LoRA`, `lora` filter), **Attach**, **Detach** — all reusing the existing inline row styling (no new CSS).
- Pre-populated on load via `GET /get_lora_path` inside `loadLlamaConfig()`. `attachLora()` saves the path; `detachLora()` clears + saves empty. Both toast "… restart llama.cpp to apply".

**Verified:** `py_compile` clean on `app.py`; `node --check` clean on the touched `config.html` JS region; whole-file HTML tag balance OK (div/button/label/span). No hardcoded usernames. **Frontend + backend change — hard refresh AND a manual Flask restart; the adapter itself applies on the next llama.cpp (re)launch (switch/reload a model, or restart the server).**

---

## Session: June 9 2026 — memory save bar: split into "Save to Character" / "Save to Global"

**The memory-confirm bar had a single "Save" button that always wrote to the per-character file.** Added a second target so a saved memory can go to the shared global store. The infrastructure was already present — `/add_character_memory` (`app.py:~7805`) already resolved `memories/global_memory.txt` vs `memories/<character>_memory.txt` from `mem_dir` (relative, no hardcoded path) — so this is a thin routing addition, not new write logic.

### CHANGE 1 — `templates/index.html` (`showMemoryConfirmInBubble`, ~L5572)
- Single "✓ Save" button replaced with two: **"🧠 Save to Character"** (`data-target="character"`, unchanged behaviour) and **"🌐 Save to Global"** (`data-target="global"`). Both reuse the existing `memory-confirm-yes save-btn` classes — no new CSS. Discard button unchanged.
- The success/confirm-follow-up logic was **extracted into one shared `doSave(target)`** (not duplicated); both buttons call it with their `data-target`. The fetch now sends `{ character, title, keywords, body, target }`.

### CHANGE 2 — `app.py` (`/add_character_memory`, ~L7805)
- Accepts an optional **`target`** field: `"character"` (default) or `"global"`. When `target == "global"` it writes to `memories/global_memory.txt`; otherwise the per-character file as before. The legacy `character == "global"` path is kept as a fallback. Formatting / separator / append logic is untouched — only path resolution gained the `target` branch.

**Verified:** `py_compile` clean on `app.py`; `node --check` clean on the touched `index.html` region. No hardcoded usernames/paths. **Frontend + backend change — hard refresh AND a manual Flask restart (Python edit).**

---

## Session: June 9 2026 — widen `memoryIntentGate` regex (explicit phrasings were being suppressed)

**The model emitted a valid `[MEMORY ADD]` tag but the save-confirm bar never appeared.** Root cause was the frontend intent gate, not stripping: the tag survives both server-side (it's whitelisted in `_filtered_stream` `_PROTECTED`) and client-side detection (matched against raw `fullMessage`). But `memoryIntentGate()` (`templates/index.html:~3567`, added May 27 to stop the model spontaneously firing the save UI) tests the **user's** current message against `MEMORY_INTENT_RE`, and that alternation was too narrow — unambiguous explicit requests like "can you put it in your memory please", "memorize this", "log that", "jot it down" all failed it, so the bar was silently suppressed.

### The fix — widen the alternation only (`templates/index.html:~3569`)
Added these patterns alongside the existing ones (gate kept, existing patterns untouched):
`put (?:it |this |that )?in(?:to)? (?:your )?memory`, `(?:can you )?memorize (?:this|that|it)`, `keep a record`, `log (?:this|that|it)`, `store (?:this|that|it)(?: in memory)?`, `jot (?:this|that|it) down`, `note (?:this|that|it) down`, `(?:please )?save (?:this|that|it)`.

- **The gate stays — this is not a weakening.** It still requires explicit user intent this turn; it just recognises more of the obvious phrasings. Spontaneous model-initiated saves with no matching user request are still suppressed.
- Verified the new phrasings match and casual messages ("what is the weather today", "tell me a joke") still don't.

**Verified:** `node --check` clean on the touched region. **Frontend-only — hard refresh; no Flask restart.**

---

## Session: June 8 2026 — stray trailing `<` leak (extend the `|>` backstop)

**Cosmetic, post-complete-response leak — same family as the June 4 `|>` trailing-fragment fix.** When the model emits the closing `<|im_end|>`, the `stop` array (string stop-word) fires **after the leading `<` has already streamed** to the UI. That orphan `<` lands in the final `_tail` buffer, and the end-of-stream backstop alternation topped out at `<|` (which requires the pipe) — so a lone `<` was emitted verbatim *after* an otherwise-complete reply. Not truncation; the response content is intact.

### The fix — `_filtered_stream` end-of-stream `_tail` flush ONLY (`app.py:~6774`)
Added a trailing bare `<` as the **last** alternation member:
```python
_tail = _re3_inner.sub(r'(?:<\|im_end|im_end\|>|_end\|>|<\||\|>|<)$', '', _tail)
```
- **End-anchored (`$`), final-flush only — never per-chunk.** It runs once on the completed `_tail` under `if not _halted[0]:` after the stream is provably done, so it only strips a `<` that is the **very last character** of the finished response, never a `<` mid-text.
- **Bare `<` kept LAST** in the alternation: regex alternation is left-to-right, so the longer `<|im_end` / `<|` forms still match first when present; the bare `<` only catches a truly lone trailing `<`.
- **No per-chunk strip added.** `strip_chatml_leakage` was deliberately left untouched — a trailing `<` mid-stream can be legitimate content (a `<` typed before more text arrives), so it is only safe to drop at the end-of-stream flush where generation has ended.

### ⚠️ DO NOT revert
Removing the bare `<` member reopens the stray-`<` leak after complete responses (the leading char of `<|im_end|>` that streams before the stop array fires).

**Verified:** `py_compile` clean. **Backend change — needs a manual Flask restart.**

---

## Session: June 8 2026 — switch a chat's character (dev/testing): right-click → Change character

You can now reassign an existing chat to a different character — e.g. move a chat from `Gemma-GPT-4o-API` to the local `Gemma` for side-by-side testing. Previously impossible: a chat's character is **derived from its filename** (`Character - Title - Date.txt`, re-parsed by `extractCharacterFromFilename` on every open), so editing names inside the `.txt` always reverted on refresh.

### How it works (`templates/index.html`)
- **New right-click menu item "🔀 Change character"** added to the existing chat context menu (`buildColorMenu`, alongside the colour swatches / Clear colour), so it sits with the double-click-rename and right-click-colour actions already on chat rows.
- It opens **`showCharacterMenu()`** — a small picker listing the cached characters (`characterListCache`), styled to match the app's other dark menus.
- Selecting one calls **`reassignChatCharacter(filename, newChar)`**, which reuses the existing filename decomposition (strip the old character prefix via the character-list cache, longest-first; fall back to everything after the first `" - "`), builds `"<newChar> - <title> - <date>"`, and renames via the existing **`POST /chats/rename`** route. It then migrates the chat-colour key old→new, updates `currentChatFilename` if that chat is open, reloads the list, and **re-opens the chat so it resolves to the new character** (top character bar / avatar / future turns all switch).

### Scope note
Historical bubbles keep their stored **per-message `speaker`** (they record who actually spoke); only **future** turns use the newly-assigned character. That's deliberate — the rename swaps the chat's *association*, not its history. (If a full history rewrite of speaker labels is ever wanted, that'd be a separate, more invasive change.)

### Follow-up fix — picker did nothing on click
First cut: clicking "Change character" appeared to do **nothing**. Cause: `showCharacterMenu` installs a `document` click-away listener to close the picker, but the very click that opened it bubbled up to that listener and closed the menu in the same tick. Fixed with **`e.stopPropagation()`** on the "Change character" click handler so the opening click never reaches the close-on-outside-click listener. Picker now stays open; outside-click / Escape still close it. Confirmed working.

**Verified:** `node --check` clean on the touched region (`buildColorMenu` → `showCharacterMenu` → `reassignChatCharacter`). Reuses the existing `/chats/rename` backend (no backend change). **Frontend-only — hard refresh; no Flask restart.**

---

## Session: June 7 2026 — clipboard image paste into the chat bar (Ctrl+V, e.g. Lightshot screenshots)

You can now paste an image straight from the clipboard into the chat bar. Click into `#user-input` and **Ctrl+V** a screenshot (Lightshot, Snipping Tool, any image on the clipboard) and it attaches exactly like the file picker — preview thumbnail in the strip, sent with the next message — plus a brief **"📋 Image pasted"** toast.

### Implementation (`templates/index.html`)
- **Refactored the attach logic into a shared `attachImageFiles(fileList)`** so the file picker and paste use one path. It filters to `image/*`, runs the existing **vision-capability guard** (blocks attaching to a text-only LOCAL model with no mmproj; allowed in cloud/openai/anthropic mode where the provider does vision — see the cloud-image-vision entries), reads each file as a data URL, and pushes `{base64, mimeType, previewUrl}` onto `window.attachedImages` + `renderImagePreviews()`. `handleImageAttach(input)` is now a thin wrapper that calls it and resets `input.value`.
- **Paste listener on `#user-input`** (`initImagePaste` IIFE): pulls image items from `clipboardData.items`, and when any are present calls `attachImageFiles` and `preventDefault()`s so the raw file path/blob isn't also dropped into the textarea as text. **Normal text paste is untouched** — it only intercepts when the clipboard actually holds an image.

**Verified:** `node --check` clean on the three touched units (handleImageAttach, attachImageFiles, paste IIFE); `showToast` confirmed present; no stray `input` refs left in the shared helper. **Frontend-only — hard refresh; no Flask restart.**

---

## Session: June 7 2026 — TTS no longer reads citation links aloud (shared stripLinksForTTS helper)

Follow-up to the link-rendering fix below: with links now emitted by `gpt-4o-search-preview`, the **TTS was speaking them** — specifically the bare-domain text of citation links (`([openai.com](https://…))` was read as "openai dot com" mid-sentence). The existing per-path strips converted `[text](url)` → its visible text, which for citations is a bare domain, and the `"(" → ". "` paren-to-pause conversion ran *before* the link strip in two paths, so the domain leaked through regardless.

### Fix — one shared `stripLinksForTTS()` helper (`utils/utils.js`), used by all three TTS paths
- **Wrapped citation `([text](url))`** → dropped **entirely** (outer parens included).
- **Plain `[text](url)`** → if the visible text is a **bare domain** (no spaces, dotted TLD) it's dropped entirely (reading "openai.com" aloud is noise); **prose labels** ("the documentation") and plain single words ("OpenAI", no dot) are **kept**, since speaking them is useful.
- Also strips `<a>…</a>`, orphaned `](url)`, bare `http(s)`/`www` URLs, and the link emoji.
- Wired into all three places that feed TTS, each **before** the `"(" → ". "` conversion: `bufferTextForTTS` (streaming chunks), `splitAndQueue` (sentence queue), and the **replay** path (`replayTimeout`, which previously converted parens first → domain leaked).

**Verified (Node):** wrapped citation → removed; plain domain link → removed; prose/single-word labels → kept; bare URLs → removed; chunk-split and multi-`&`-param URLs → handled, domain never spoken. `node --check utils/utils.js` clean. **Frontend-only — hard refresh; no Flask restart.**

---

## Session: June 7 2026 — markdown links now render in the chat shim (index.html + mobile.html), http(s)-only + target/rel hardened

Markdown links from the model rendered as **raw literal text** (`[text](url)`, brackets/parens and all). Root cause: HWUI never loads the real `marked.js` library, so the always-on hand-rolled shim (`typeof marked === "undefined"` → custom `window.marked.parse`) is what renders every assistant turn — and its inline formatter only handled `**bold**` / `*italic*` / `` `code` ``, with **no link rule at all**. So *all* links were affected, not just the nested form; it only surfaced now because `gpt-4o-search-preview` emits citation links and local models rarely do. Same render path for local and cloud responses (both stream through `/chat` → `fetchAndDisplayResponse` → shim), so the fix lands once per template and covers every provider.

### Fix — add a link rule to the shim's inline formatter, in BOTH copies
`templates/index.html` (`parseInline`, ~L1256) and `templates/mobile.html` (`inline`, ~L13) each got the same rule. Links are processed **first, before the emphasis rules**, and swapped for an inert `\x00LINK\d\x00` placeholder restored after — so URL contents (underscores, asterisks) and the link text can't be mangled by the bold/italic passes.

- **Regex `\[([^\]]+)\]\(([^)]+)\)`** matches only the inner `[text](url)`, so the OpenAI **wrapped form `([text](url))`** keeps its outer parens as literal text → renders `(openai.com)` with `openai.com` clickable.
- **⚠️ Hardening (mandatory — output goes straight into `innerHTML` with no sanitiser):**
  - **http/https ONLY** — any other scheme (`javascript:`/`data:`/`file:`…) is left as literal text, never turned into an anchor.
  - every anchor carries **`target="_blank" rel="noopener noreferrer"`**.
  - the **href is HTML-escaped** (`& " < >`) to prevent attribute breakout; in mobile the **label is `esc()`-escaped** too (matching that shim's escape-everything flow — index's shim escapes nothing elsewhere, so its label stays raw).

### Verified (Node, exact replicated logic, both files)
- `[OpenAI](https://openai.com)` → `<a href="https://openai.com" target="_blank" rel="noopener noreferrer">OpenAI</a>`
- `([openai.com](https://openai.com/index/sycophancy-in-gpt-4o/?utm_source=openai))` → `(` + anchor `openai.com` (full query string intact) + `)`
- `[click](javascript:alert(1))` → **no anchor**, stays literal text
- `*italic* … [link](https://x.com)` → italic **and** link both render, neither breaks the other
- `[doc](https://x.com/a_b_c)` → href keeps all three underscores (not italicised)
- (extra) `[a&b](https://x.com/p?u=1&v=2)` → `&` correctly escaped to `&amp;` in both href and label (valid HTML, clicks correctly)

Both `parseInline`/`inline` appear exactly once per file; inserted JS parses clean under Node. **Frontend-only — hard refresh to pick it up; no Flask restart.** No backend or settings changes.

---

## Session: June 7 2026 — fix API keys getting wiped: preserve-on-empty in the cloud save routes + complete the default schema

**Symptom:** the OpenAI/Anthropic API keys kept vanishing — re-entered repeatedly, and only `anthropic_api_key` tended to survive in `settings.json` (because it's the primary, so it gets re-saved most often). Not a missing-store bug: the save routes already do read-modify-write with atomic temp-file + read-back verification, and both keys were in fact on disk. The wipe was an **empty-string overwrite**.

### Root cause — a blank field could persist `""` over a stored key
`/save_openai_settings` and `/save_anthropic_settings` wrote `s["…_api_key"] = data.get("…_api_key","").strip()` **unconditionally**. So any Save fired while the key field was blank persisted `""`. The realistic intermittent trigger (same class as the May `get_settings()` `except: return {}` wipe — archive 2026-05): `GET /get_*_settings` hits a momentary read failure (file lock / AV scanner mid-write), returns its `except` fallback `{… "openai_api_key": "" …}`, config.html repopulates the field with `""`, and the next Save writes the blank back. Anthropic survived more often only because it's re-saved more often.

### Fix 1 — preserve-on-empty (`cloud_api_routes.py`, both save routes)
An empty incoming key now **never** overwrites a stored one:
```python
_incoming_key = (data.get("openai_api_key", "") or "").strip()
if _incoming_key:
    s["openai_api_key"] = _incoming_key
elif "openai_api_key" not in s:
    s["openai_api_key"] = ""   # establish the field on first save; never wipe an existing key
```
You can still **change** a key (a non-empty value overwrites); you just can't blank it with a stray save. There is no "clear key" button for OpenAI/Anthropic, so nothing legitimate needs the empty-write path. **Brave was deliberately left as-is** — it *has* a `clearBraveKey()` button that saves an empty value on purpose, so preserve-on-empty would break its clear.

### Fix 2 — complete the default schema (`settings.default.json`)
The default seed (used to create `settings.json` on a fresh/missing install, and by Reset-to-Default) had **no** cloud fields at all, so a regenerated settings.json lacked the whole key/backend skeleton. Added `backend_mode:"local"`, `cloud_api_enabled:false`, `openai_api_key:""`, `openai_model:"gpt-4o"`, `openai_base_url`, `anthropic_api_key:""`, `anthropic_model:""`, `anthropic_base_url`, `brave_api_key:""`. Empty by design — real keys are **never** shipped in the template (the public backup includes `settings.default.json`, not `settings.json`).

### ⚠️ DO NOT revert to an unconditional key write
Re-adding `s["…_api_key"] = data.get(…)` without the empty-guard reopens the vanishing-key bug (a blank-field or failed-read save wipes the stored key). In-code warnings sit on both routes.

**Verified:** `py_compile` clean (`cloud_api_routes.py`); `settings.default.json` valid JSON; live `settings.json` untouched (both keys still present). **Backend change (Python) — needs a manual Flask restart** (dev server runs with no reloader). `settings.default.json` only affects fresh installs / reset.

---

## Session: June 7 2026 — live TOKEN MONITOR readout (per-turn KV headroom) on the index page

New on-screen readout that shows how much of the local model's context the last turn actually used, so context-limit / gpu-layers tuning has a visible feedback loop. **Surfaces numbers the backend already computes** — nothing new is measured; the per-turn budget figures were previously console-only.

### Data source (no new measurement, no stream-protocol changes)
The raw (local llama.cpp) path already computes the exact assembled-prompt token count from llama-server's `/tokenize` (`_prompt_real_est`), the live ctx (`_ctx_size_live`), and the capped `_n_predict` right before streaming (`app.py:~5421`). A new module global **`_LAST_TOKEN_STATS`** (`app.py:~865`) snapshots them there (plus post-trim history kept/dropped and the model id). Reply-side fields (`tokens_predicted`/`tokens_evaluated`/stop reason) are filled in at end-of-stream in `stream_model_response()` from the final SSE event (`app.py:~2114`). Both writers are best-effort `try/except` — monitor bookkeeping can never break a stream. **Deliberately NOT injected into the chat text stream** (that path is wrapped in the fragile ChatML/EOS-leakage stripping — see the many entries below); the readout pulls from a separate endpoint instead.

### New endpoint — `GET /token_stats` (`app.py`, before `/save_chat`)
Returns the last local turn's actuals merged with **seed** values (`ctx_size` / `gpu_layers` / `model`) read from `settings.json` → `llama_args`, so the gauge is populated on page load **before** the first message and reflects a context-limit / gpu-layers change as soon as the model is relaunched. Computes `headroom = ctx_size − prompt_tokens`. Cloud (OpenAI/Anthropic) turns don't write `_LAST_TOKEN_STATS` (no local KV budget) — after a cloud turn the readout simply shows the last local turn, or seed-only if there hasn't been one.

### Frontend (`templates/index.html`)
- **Toggle button** in the chat bar's `#input-right` (terminal-screen icon, next to TTS).
- **Retro phosphor panel** `#token-monitor` — fixed, top-centre, hidden by default, slides down on open. Black screen, green/blue phosphor, monospace, scanlines + glow; a headroom **bar** that goes green→amber(≥60%)→red(≥85%). Rows: PROMPT (+bar/%), CONTEXT + HEADROOM, REPLY (n_predict + last gen), HISTORY (kept/dropped), MODEL (+gpu N).
- **`refreshTokenMonitor()`** fetches `/token_stats` and paints; it's a **no-op while the panel is closed**, so it's cheap to call from both chat-stream `finally` blocks (`fetchAndDisplayResponse` and `continueLast`) after every turn. Open/closed state persisted in `localStorage`; restored on `DOMContentLoaded`.
- **Centred on the chat bar, not the viewport** (`positionTokenMonitor()`). The input area is offset by the sidebar (`#input-area` `left:250px`), so a plain `left:50%` sat centre-of-screen, not centre-of-chat. The panel now reads `#input-row`'s live bounding rect and sets its `left` to that centre (keeping `translateX(-50%)`), so it adapts to any sidebar width / theme. Re-runs on open and on `window.resize` (only while open).

**Verified:** `py_compile` clean (`app.py`); `/token_stats` logic traced against `settings.json` (ctx 16384, gpu 44). **Backend change (Python) — needs a manual Flask restart** (dev server runs with no reloader); the `index.html` change needs a hard refresh.

---

## Session: June 7 2026 — chat-search false-positive fix #2: anchor look / dig / locate to a chat-object noun

Same bug class as the June-3 "trying to find" fix, three branches further on. The chat-history search fired on a message that had **nothing to do with recall** — the user was talking about finding a *person in real life* ("I'm in seeking mode", "I cannot **look for** it", "I hope I bump into her"). The 🗂️ *Searching chat history...* indicator fired anyway, because `_CHAT_SEARCH_VERBS` (`app.py:~1724`) still had **three unanchored verb branches** that matched the bare verb with no reference to a past conversation:
- `look\s+(?:up|for|through)` — matched "**look for** it" (the reported false positive)
- `dig\s+(?:up|through|out)` — would match "dig up the garden"
- `(?:can\s+you\s+)?locate` — would match "locate my keys"

Every *other* branch already required a chat-object noun (chat / conversation / message / thread / session / history / logs); the June-3 fix anchored the `trying to find` branch the same way. These three were the remaining gaps. Confirmed at the regex level that "I cannot look for it" matched `look\s+for` while no recall phrase was present to suppress it (`_RECALL_PHRASE_RE` only suppresses when recall phrasing IS present), so the search fired.

### Fix — anchor all three to the chat-object noun tail (`app.py:~1729`)
Each branch now requires the **same `(?:that|the|our|a|my)?\s*(?:chat|conversation|message|thread|session|history|logs?)` noun tail** used by the `trying to find` branch, so behaviour stays consistent across the verb set. One deliberate addition over the immediate-adjacency form: a **bounded run of intervening words** (`(?:\w+\s+){0,6}?`) sits between the verb and that tail, so a **delayed** noun still matches — e.g. "dig up **what we discussed in** our chat" (the noun "chat" arrives four words later). Without the bounded gap that legitimate recall phrasing would no longer fire. The lazy `{0,6}?` keeps the gap tight; "look for it" (no noun anywhere after) still does not match.

### Untouched (by constraint)
- **The `look up` web-intent bypass (`app.py:~5526`)** — still routes "look up" / "look it up" to **web search** exactly as before; not modified. ("look it up" also no longer matches the chat verb regardless, since `look\s+(?:up|…)` needs the particle adjacent to "look".)
- **`search\s+for`** — left as-is (already mitigated by the web bypass).
- **`_RECALL_PHRASE_RE` suppression** — unchanged.

### ⚠️ DO NOT revert these three to the bare verb
Dropping the noun tail off `look` / `dig` / `locate` reopens the "look for it" false positive (real-life search/seek vocabulary hijacks the response with unrelated old-chat snippets). The in-code comment block at `app.py:~1729` carries the warning. This extends — does not replace — the June-3 `trying to find` anchoring.

**Verified:** `py_compile` clean (`app.py`); regex unit-tested against all five required strings — "I cannot look for it" → **no** fire, "I'm in seeking mode" → **no** fire, "can you look for that conversation" → **fires**, "dig up what we discussed in our chat" → **fires**, "look it up" → routes to **web bypass**, not chat search. **Backend change (Python) — needs a manual Flask restart** (dev server runs with no reloader).

---

## Session: June 6 2026 — fix regenerate "strip-too-far": adjacent-only dedup + regen skips hidden turns

Regenerate was removing not just the last assistant reply but one or more **preceding user+assistant pairs**. Root cause was **not** in `regenerate()` itself (its `splice(lastUserIndex + 1)` is sound) — it was the **global content-dedup** inside `autoSaveCurrentChat()` (`index.html:~3920`). That block built a `seen` Set keyed on `role:content` and deleted **any** turn whose role+text matched an earlier one **anywhere in the array**, keeping only the first occurrence. So legitimately-repeated messages — "continue", "yes", a re-asked question, or an identical short reply — were silently deleted **mid-chat**, collapsing whole turns. It surfaced on regenerate because regenerate forces a save (`autoSaveCurrentChat()` at step 4), which is when the array got mangled; the visible result was the conversation "skipping back" and losing pairs.

### Fix 1 — content-dedup is now ADJACENT-ONLY (`index.html:~3920`)
Replaced the global seen-set with a single-pass adjacent comparison: a turn is dropped only when its `role:content` key is identical to the turn **immediately before it** (tracked via `prevKey`). A non-adjacent repeat — the same text appearing several turns apart — is **kept**. The content key is built exactly as before (multimodal array → text-parts join), so image turns compare correctly. This still catches the only real duplicate case — an accidental exact double-push, which is always adjacent. The separate consecutive-assistant pass (`~3909`) was already adjacent-only and is unchanged, as are the two push-time `isDuplicate` guards in `fetchAndDisplayResponse` (`~3476`, `~3714`).

**Load-bearing check:** confirmed nothing relied on non-adjacent removal — `dedupedChat` feeds only the `window.loadedChat` replacement and the `/chats/save` payload; the backend (`_format_chat_messages`) does no dedup; `loadChatHistory`'s render-time filter is independent and already adjacent.

### Fix 2 — regen scan skips hidden turns (`index.html:~4179`)
The backward scan that finds the last user message to regenerate from now skips `hidden === true` turns (e.g. the memory-confirm trigger pushed with `hidden:true`, `~L5319`). Those aren't rendered as bubbles, so anchoring on one misaligned the array splice against the DOM-removal loop (which only sees visible wrappers). It now anchors on the last **visible** user turn, keeping array and DOM cuts aligned.

### ⚠️ DO NOT revert the dedup to global
The content-dedup must **NEVER** be made global (seen-set across the whole array) again. Repeated messages like "continue" / "yes" are **valid chat history**, and a global dedup destroys them — silently deleting user+assistant pairs and corrupting the saved chat. Keep it adjacent-only. The in-code warning sits on the block.

**Out of scope (left for another session):** the image-turn `lastUserMessage`-as-array issue in `regenerate()` (a multimodal user turn passes an array where `fetchAndDisplayResponse` expects a string `input`) — a separate latent bug, untouched here.

**Verified:** `index.html` only — **no backend change**, so just a hard refresh to test. Adjacent-only logic traced for runs of identical turns (collapses a run to one) and for non-adjacent repeats (A,B,A → all kept).

---

## Session: June 6 2026 — durable chat persistence: save user turn on send + pagehide beacon flush

In-flight chat turns vanished on navigation. The **only** disk write for a turn was the completion-time `autoSaveCurrentChat()` → `/chats/save`, which fires **after** the assistant reply finishes. The user message was pushed to `window.loadedChat` (an in-memory global on the index.html `window`) and rendered to the DOM, but never persisted on send. Navigating to `/config` is a **full page navigation** (`<a href="/config">`) that unloads index.html and destroys `window.loadedChat`; on return `openChat()` rebuilds purely from the saved file. So sending a message and leaving before generation completed — or while it was streaming — lost the **entire turn** (user message + any partial reply), because the first and only save hadn't happened yet and nothing flushed on unload.

### Fix 1 — save the user message immediately on send (`index.html`, `sendPrompt()` ~L3275)
Right after the user turn is pushed onto `window.loadedChat` (one awaited `autoSaveCurrentChat()` placed after the if/else, covering both the **image-path** push `~L3254` and the **text-path** push `~L3268`), the chat is persisted **before** `fetchAndDisplayResponse()` runs. The user message now survives navigation regardless of what the response does.

### Fix 2 — pagehide beacon flush (`index.html`, DOMContentLoaded bootstrap ~L3343)
Added a `pagehide` listener (`_beaconFlushChat`) that, when `window.loadedChat` has content, serialises the **same payload `autoSaveCurrentChat()` builds** (`filename` / `messages` / `character`; hidden turns dropped, multimodal flattened to text + `[image]`, speaker/timestamp/is_opening_line preserved) and posts it via **`navigator.sendBeacon('/chats/save', blob)`** — *not* `fetch`, which the browser kills during page teardown. The body is a `Blob` tagged `application/json` so the route's `request.get_json()` parses it cleanly. If a stream is mid-flight (`window.isSending`), the live partial assistant text is appended from a new global **`window._streamingPartial`** (mirrored from `cleanedFull` in the reader loop, cleared in the `finally`), so an **interrupted reply is preserved** rather than lost.

### Fix 3 — `/chats/save` accepts beacon bodies (`chat_routes.py:~565`)
Hardened `save_chat_messages()` from bare `request.get_json()` to `request.get_json(force=True, silent=True) or {}`. The route previously hard-required `Content-Type: application/json` (a beacon with an odd/blank content type would 415 → `None.get` → 500). `force=True` parses regardless of content type; `silent=True` makes a malformed teardown beacon return `{}` instead of 500-ing. Matches the existing `force=True` pattern on the sibling `/save_chat` route. (Belt-and-braces — the client already tags the Blob `application/json`, so the normal path was fine; this just guarantees a stray beacon can never error.)

### ⚠️ DO NOT revert to save-on-completion-only
Removing Fix 1 (the on-send save) or Fix 2 (the pagehide beacon) reopens the **"in-flight turns vanish on navigation"** bug: the save-after-the-assistant-reply-completes design was the root cause — anything that interrupts a turn before completion (navigating to config, closing the window, a dropped stream) loses the user message and any partial reply. The completion-time `autoSaveCurrentChat()` is **unchanged** and remains the normal-path save; Fixes 1–2 are additive belt-and-braces around it. In-code warnings sit on all three sites.

**Verified:** `py_compile` clean (`chat_routes.py`). **Backend change (Python) — needs a manual Flask restart** (dev server runs with no reloader); the `index.html` template change needs a hard refresh.

---

## Session: June 6 2026 — per-model OpenAI payload assembly (fix GPT-5-class `max_tokens` 400)

Selecting a GPT-5-class model (`settings.json` → `openai_model: gpt-5.5`) 400'd every chat: the OpenAI API returned `Unsupported parameter: 'max_tokens' is not supported with this model. Use 'max_completion_tokens' instead.` `stream_openai_response()` was building a **flat payload** (`app.py:~2224`) that unconditionally sent `max_tokens` plus the classic sampling params (`temperature`/`top_p`/`frequency_penalty`/`presence_penalty`) for **every** model. The GPT-5 family and the o-series reasoning models reject all of those — they require `max_completion_tokens` and take no sampling params. Unlike the Anthropic path (`ANTHROPIC_MODEL_SAMPLING_RULES` / `_anthropic_allow_for`), there was no per-model filtering on the OpenAI side at all.

### Fix — OpenAI caps table + resolver, mirroring the Anthropic pattern (`app.py:~2590`)
Added `OPENAI_MODEL_RULES` and `_openai_caps_for(model_id)` directly below the Anthropic block. Each rule carries `token_param` (`max_tokens` vs `max_completion_tokens`) and a `sampling` bool. Resolution is **exact match → longest-prefix → default**, so bare family prefixes (`gpt-5`, `o1`, `o3`, `o4`) cover dated/variant IDs (`gpt-5.5`, `o3-mini`, …) automatically. The default rule (`max_tokens` + sampling) preserves classic behaviour for `gpt-4o` and earlier.

### Fix — conditional payload assembly (`stream_openai_response()`, `app.py:~2224`)
Replaced the flat dict with conditional assembly: `model`/`messages`/`stream` always sent; the token limit sent under `caps["token_param"]`; the four sampling params included **only** when `caps["sampling"]` is true. The function signature is unchanged (it still receives every param) — only the wire body is gated. Both OpenAI entry points funnel through this dict — the plain path and the web-search path (`_web_search_stream_openai` → two `stream_openai_response` calls) — so both are covered with no duplication.

### ⚠️ DO NOT revert to a flat OpenAI payload
Re-adding `temperature`/`top_p`/`frequency_penalty`/`presence_penalty` + `max_tokens` unconditionally will **400 every GPT-5-class and o-series model** (`Unsupported parameter`). The in-code warning sits on the payload block; new GPT-5-class/o-series models must be added to `OPENAI_MODEL_RULES` (a bare family prefix is enough).

**Verified:** `py_compile` clean (`app.py`). **Backend change (Python) — needs a manual Flask restart** (dev server runs with no reloader). No template/settings changes.

---

## Session: June 5 2026 — close the web-search-ON image-drop gap (cloud image vision, part 2)

Follow-up to the cloud-image-vision fix below, which closed the normal chat path but flagged a remaining gap: with **web search ON**, both cloud web-search wrappers rebuilt the final user turn as a **plain string** in their phase-4 re-prompt, discarding any attached image before the post-search follow-up call. So an image + web-search-on turn saw the image on the first pass (tag detection) but lost it on the answer pass. Now closed for **both** providers.

### Root of the drop
`_web_search_stream_openai` (phase 4, `app.py:~2462`) and `_web_search_stream_anthropic` (phase 4, `app.py:~2800`) both did:
```python
search_messages[_last_user_idx] = {"role": "user", "content": augmented_user_msg}
```
`augmented_user_msg` is a string (original text + the `[WEB SEARCH RESULTS …]` block), so assigning it as the whole content threw away the image block(s) that the last user turn was carrying.

### Fix — shared `_rebuild_search_user_turn()` helper (`app.py:~2280`)
Added one helper used by **both** wrappers (no duplication). It rebuilds the augmented turn while **preserving the image block(s)**:
- If the original turn's content is a **list with ≥1 image block** → return `[{"type":"text","text": augmented_user_msg}] + <preserved image blocks>`.
- Otherwise (plain string, or a list with no image) → return the `augmented_user_msg` **string**, byte-identical to the previous behaviour. Only image turns change.

**No re-conversion needed — and that's deliberate.** Each wrapper only ever sees its own provider's already-correct image blocks: the OpenAI path carries native `{"type":"image_url",…}` blocks, and the Anthropic path was already run through `_anthropic_normalize()` upstream so its blocks are already `{"type":"image","source":{"type":"base64",…}}`. The helper therefore keeps the existing blocks verbatim and only swaps the text — so the `_anthropic_normalize` converter is reused implicitly (its output flows in) without being called twice or duplicated. Both phase-4 sites now read the original last-user content and pass it through the helper.

### ⚠️ DO NOT revert to assigning the bare `augmented_user_msg` string
Doing so at either phase-4 site reopens the web-search-ON image-drop gap (image lost on the post-search answer call). The helper docstring and both call sites carry the warning.

**Verified:** `py_compile` clean (`app.py`); helper unit-checked — text-only string & text-only list → plain string (unchanged); OpenAI `image_url` turn, Anthropic `image`/base64 turn, and image-only turn → list with augmented text block + preserved image block(s) in the correct provider format. **Backend change (Python) — needs a manual Flask restart** (dev server runs with no reloader). The prior entry's "known gap" note is marked closed.

---

## Session: June 5 2026 — cloud image vision: stop dropping attached images on the Anthropic & OpenAI API paths

Attached images never reached either cloud provider. Two independent bugs killed them: (1) **routing** — any image-bearing turn made `has_images` true and was intercepted by the **local vision branch** (`app.py:~4734`), which checks for a local mmproj file and 400s ("this model can't see images") or routes to the local model, so execution never reached the cloud branches; and (2) **content stripping** — even if it had, `_anthropic_normalize()` flattened list content to text (`p.get("text")` only), discarding every image block, and the OpenAI branch did the same when building `_oai_messages`. Net effect: in API mode only text was sent; Claude/GPT could never see an attached image. PDFs/documents are unaffected (they ride the PyPDF2 text-extraction path, **left untouched this session**).

### Fix 1 — backend_mode-first routing (`app.py:~4732`, the `has_images` decision)
Read `backend_mode` from `settings.json` immediately before the `has_images` branch and changed the guard to **`if has_images and _backend_mode_for_vision == 'local':`**. Now an image turn only takes the local vision path / mmproj guard when the backend is **local**; in `openai`/`anthropic` mode it falls through to the cloud branches (`~4925` / `~5039`). The existing local-no-mmproj 400 is unchanged for the local case. A `settings.json` read failure defaults to `'local'` (fail-safe — keeps the existing local guard).

### Fix 2 — Anthropic base64 image-block converter (`_anthropic_normalize`, `app.py:~2845`)
List content is now converted to Anthropic's native content-block array instead of being flattened:
- text parts → `{"type":"text","text":…}`.
- image parts (frontend's OpenAI-style `{"type":"image_url","image_url":{"url":"data:<mt>;base64,<data>"}}`) → `{"type":"image","source":{"type":"base64","media_type":<mt>,"data":<data>}}`.
- `media_type` is taken from the data-URI header with any `;charset`/`;base64` suffix stripped, lower-cased, and validated against **image/jpeg, png, webp, gif**; anything else (or empty) → **image/png** + a logged warning.
- Malformed data URIs (no comma, or empty payload after the comma) and non-`data:` URLs are **skipped with a log line**, never crashing the request.
- A turn that ends up with **no surviving image block collapses back to a plain string** (prior text-only behaviour preserved); the block-array form is emitted only when ≥1 image block is present. Same-role turn merging now handles mixed string/list content (promotes the string side to a text block when concatenating).

### Fix 3 — OpenAI native pass-through (`app.py:~4935`, `_oai_messages` build)
The frontend already emits OpenAI's own vision format, so **no converter is needed** — the branch was simply flattening it away. Now a turn carrying any `image_url` block is **forwarded unchanged**; only text-only list turns still collapse to a string. `stream_openai_response` posts `messages` verbatim, so the image reaches `/v1/chat/completions` natively. (Investigated per the task's step 3: the OpenAI branch *was* stripping images, and the fix is pass-through, not a converter.)

### Known gap — ✅ CLOSED (see the June 5 2026 web-search image-drop entry above)
~~With **web search ON**, both cloud web-search wrappers rebuild the final user turn as a plain string from `user_input` (`_web_search_stream_openai` phase 4, `app.py:~2462`, and its Anthropic sibling), so an **image + web-search-on** turn still loses the image on the post-search follow-up call.~~ **Now fixed** — both wrappers preserve the image block(s) on the augmented turn via the shared `_rebuild_search_user_turn()` helper. Image vision now works on both providers with web search **on or off**. PDF/document handling is still unchanged (text extraction only).

### ⚠️ DO NOT revert the backend_mode-first routing check (`app.py:~4732`)
Reverting it to a bare `if has_images:` reopens the **"images never reach cloud"** bug — every image turn gets intercepted by the local vision branch again and is 400'd or sent to the local model, never to Anthropic/OpenAI. Likewise do not re-flatten list content in `_anthropic_normalize` or the OpenAI `_oai_messages` build — that silently drops the image. All three sites carry the same in-code warning.

**Verified:** `py_compile` clean (`app.py`); the converter's data-URI parsing unit-checked against png/webp/gif/jpeg(+charset), unsupported & empty media_type (→png+warn), and malformed/empty/non-data URIs (skipped, no crash). **Backend change (Python) — needs a manual Flask restart** (dev server runs with no reloader). No template/settings changes; the frontend image format was already correct.

---

## Session: June 5 2026 — unbound-character system-prompt fallback: global-active → fixed `default.txt`

A character with **no bound system prompt** (e.g. the base **Helcyon** card, whose `system_prompt` field is `""`) was falling back to the **globally-active** editor template instead of a stable default. Because `settings.json` → `active_system_prompt` was `Claude.txt` (set by the SP editor's **Activate** button), an unbound Helcyon **displayed Claude's system prompt on the config page AND silently ran on Claude's prompt at chat time**. Confusing and wrong — an unbound character should resolve to a predictable default, not inherit whatever template was last activated in the editor. There is a dedicated `system_prompts/default.txt` (a neutral Helcyon base, with paired `default.example.txt` / `default.posthistory.txt`) — that is now the fallback.

### The resolution chain changed: `bound → global-active` becomes `bound → default.txt`
- **`system_prompt_routes.py`** — added module constant `DEFAULT_SYSTEM_PROMPT = 'default.txt'` and changed `resolve_character_prompt_files()` fallback from `get_active_prompt_filename()` (global-active) to `DEFAULT_SYSTEM_PROMPT`. This is the single shared resolver, so it fixes the **example-dialogue** and **post-history** loads (`app.py:~3937/3978/4073/4488`) and the **/continue** main-SP load (`app.py:~7374`) in one place. Verified: `{system_prompt:''}` / `None` / whitespace all → `default.txt` (+ `default.example.txt` / `default.posthistory.txt`); a bound `GPT-4o.txt` still → its own paired files.
- **`app.py` `_resolve_system_layer()` (~3626)** — the main `/chat` path previously only *overrode* the global-active base when a character was bound (inline `char_data.get("system_prompt")` check), so an **unbound** character kept the global-active base loaded by `get_system_prompt()`. Re-routed through `resolve_character_prompt_files(char_data)` so the resolved template (bound → else `default.txt`) is always loaded. The `get_system_prompt()` global-active base is kept only as a last-resort safety net if the resolved file is missing. (Also dropped a dead `import datetime as _dt` — the block uses `current_time`, not `_dt`.)
- **`templates/config.html` `loadCharacter()` (~1160)** — the unbound `else` branch used to leave the SP tab showing whatever `loadGlobalSystemPrompt()` had loaded (the global-active = Claude.txt). It now resolves `data.system_prompt || 'default.txt'` and loads that, so the **config display matches what the character actually uses at chat time**. `updateBindIndicator` still reflects true bound/unbound state. Updated the DOMContentLoaded init-ordering comment (the no-binding path no longer "leaves the fields untouched").

### Why fixed `default.txt`, not the global-active
The global-active (`active_system_prompt`) is an **editor** concept — the Activate button picks which template the SP tab edits by default. Tying unbound-character resolution to it meant editing/activating one character's template silently changed the prompt for **every** unbound character. Decoupling them makes the fallback predictable: unbound = `default.txt`, always. The Activate button still works for the editor; it just no longer leaks into chat-time character resolution.

### ⚠️ DO NOT revert the fallback to `get_active_prompt_filename()`
Doing so reopens the bug: unbound characters re-inherit whatever template is globally active (e.g. Claude.txt). The resolver, the `_resolve_system_layer` chat path, and the config display must agree on `default.txt`. In-code comments at all three sites carry this warning.

**Verified:** `py_compile` clean (`app.py`, `system_prompt_routes.py`); resolver unit-checked for unbound/None/whitespace/bound. **`app.py` + `system_prompt_routes.py` are backend Python — needs a manual Flask restart.** `config.html` is template-only (browser reload picks it up); the same restart covers both. Character cards are re-read per request, so no card edits were needed — Helcyon stays genuinely unbound and now correctly resolves to `default.txt`.

---

## Session: June 4 2026 — `|>` trailing-fragment streaming fix (cross-chunk ChatML boundary split)

**Cosmetic, post-complete-response leak — separate from the now-fixed EOS bugs** (token#1 strip + mid-sentence token-2; entries below). The closing ChatML boundary `<|im_end|>` can split across SSE chunks: `strip_chatml_leakage` already removes the `<|im_end` head, but the orphan `|>` tail then arrives as its **own** chunk, matches no existing rule, passes through `_filtered_stream`, lands in `_tail`, and the end-of-stream flush (which only stripped a trailing role-header) emitted it verbatim — so a stray `|>` leaked to the UI and was saved *after* an otherwise-complete response. Not truncation; the reply content is intact.

### Two scoped changes (cover different split cases — both needed)
1. **`strip_chatml_leakage` (`app.py:~1054`)** — after the existing `\bend\|?>` tail-fragment rule, drop the bare orphan boundary **only when the whole whitespace-trimmed chunk is exactly `|>`** (the actual orphan tail of a split `<|im_end|>`):
   ```python
   if text.strip() == "|>":
       text = ""
   ```
   Exact-whole-chunk match keeps the blast radius minimal — `|>` embedded in real text or code (`x |> y`, `code |>`) is **never** touched (verified). A lone `|` or `>` arriving as its own chunk is **deliberately NOT stripped**: it's far more likely legitimate content (markdown table separator, blockquote, operator) than a boundary fragment. The change-2 `_tail` backstop covers any residual `|`/`>` boundary-split case at end-of-stream.
2. **`_filtered_stream` end-of-stream `_tail` flush (`app.py:~6501`)** — after the role-header strip, an **end-anchored** backstop for a fragment that landed inside the final `_TAIL_LEN` buffer (never re-scanned by `strip_chatml_leakage`):
   ```python
   _tail = _re3_inner.sub(r'(?:<\|im_end|im_end\|>|_end\|>|<\||\|>)$', '', _tail)
   ```
   Anchored to `$` only, so it strips a trailing `|>` / `<|` / `<|im_end` / `_end|>` / `im_end|>` at the very end of the buffer but leaves `|`/`>` elsewhere intact (verified: `done.|>`→`done.`, `reply<|im_end`→`reply`, `im_end|>`/`_end|>` tails removed; `a | b | c`, `use the |> operator here` unchanged). This is the case the whole-chunk rule in (1) can't catch.

Scoped to the `_filtered_stream` `_tail` path only — the EOS `logit_bias`, the assistant-header newline fix, and all stop-token logic are untouched. (The separate web-search-stream flush at `~5488` and the chat-search `_cs_tail2` flush at `~6481` were intentionally left alone — out of scope.)

### ⚠️ DO NOT revert
Removing either rule reopens the stray-`|>` leak after complete responses. Both in-code comment blocks (`app.py:~1054` and `~6501`) carry the same warning.

**Verified:** `py_compile` clean; both rules unit-checked against orphan fragments **and** legitimate `|`/`>`/`|>`-bearing text (no over-strip). **Backend change — needs a manual Flask restart.**

---

## Session: June 4 2026 — removed the TEMP `_last_prompt.txt` prompt dump (EOS bugs resolved)

Both EOS bugs are now fixed — the **token#1-EOS strip bug** (assistant-header newline restored at `app.py:~4668`) and the **mid-sentence token-2 EOS** (soft `logit_bias` on `</s>`, see entry below) — so the diagnostic prompt dump is no longer needed. Removed the lone `🔬 TEMP DEBUG` block at `app.py:~4672` that opened `_last_prompt.txt` for writing and printed `🔬 FULL PROMPT dumped…`. Only the debug `try/except` was removed — the surrounding prompt-build logic (the newline-restoring cleanup above it and the `🔍 CHATML SANITY CHECK` below it) is untouched. `py_compile` clean; no other references to `_last_prompt.txt` remain in `app.py`. **Backend change — needs a manual Flask restart.**

---

## Session: June 4 2026 — mid-sentence truncation fix: soft EOS logit_bias on token id 2 (inference-side, all models)

Final fix for the *mid-sentence* truncation (distinct from the token#1-EOS strip bug below): the model occasionally emits the **real EOS token mid-stream** — `stop_type='eos'`, `tokens_predicted` in the hundreds, `truncated=False`, content cut mid-word. Prompt assembly was confirmed clean, so this is a sampler-side problem, fixed at inference with **no retrain**.

### The token facts (confirmed via GGUF metadata + `/tokenize` on the live b8994 server)
- **The real EOS is `</s>` = token id `2`** for the Mistral-Nemo / Tekken vocab (`n_vocab=131072`). Verified on both `helcyon-claude-opus-v3.2-Q5_K_M.gguf` (via `/props` + `/tokenize`) and `helcyon-solara-v1.3-Q5_K_M.gguf` (via `gguf.GGUFReader`: `eos_token_id=2`, `bos_token_id=1`, no padding token). A genuine mid-sentence cut reports `stop_type='eos'` — i.e. token 2 — **not** a string stop-word match.
- **`<|im_end|>` is NOT a vocab token.** It tokenizes into 6 plain pieces (`<` `|` `im` `_end` `|` `>`), so it is **not logit-biasable**. It is the model's *trained* turn-end marker, caught only as a **string stop-word** in the `stop` array (`get_stop_tokens()`, `app.py:~1019`). Clean turn-ends keep happening via that string stop — independent of the EOS-token bias.

### The fix — soft negative `logit_bias` on token id 2 (tunable)
- **`settings.json`** — new field next to `"ignore_eos": false`: **`"eos_logit_bias": -3.0`** (default). Negative = less likely to emit EOS; `0.0` disables.
- **`app.py` (~5251)** — read alongside the other sampler params: `_eos_logit_bias = float(sampling.get("eos_logit_bias", 0.0))`.
- **`app.py` payload (~5287, right after the `"ignore_eos"` line)** — applied **only when `ignore_eos` is False and the bias is nonzero**:
  ```python
  if not _ignore_eos and _eos_logit_bias != 0.0:
      payload["logit_bias"] = [[2, _eos_logit_bias]]
  ```
  When `ignore_eos` is True the server already drives EOS to `-inf` (the hard form), so the soft bias would be redundant — hence the guard. `logit_bias` as a list of `[token_id, bias]` pairs is **accepted by build b8994** (verified live: the server echoes it back in `generation_settings` as `[{"bias": -3.0, "token": 2}]` — parsed, not silently dropped).
- **`app.py` 🩺 PAYLOAD log (~5311)** — added an explicit `eos_logit_bias` field to the per-turn payload log so the applied value (or the not-applied reason: `ignore_eos`/zero-bias) is confirmable every turn.

### Why this is the right lever
This is **inference-side and model-agnostic** — it fixes the mid-sentence EOS for **all Series 5 models at once** with no retrain. A mild bias (`-3.0`) curbs the spurious early EOS emission while genuine turn-ends still fire through the `<|im_end|>` string stop-word. Tune `eos_logit_bias` in settings.json if cuts persist (stronger, e.g. `-5`) or if generation runs long (weaker, e.g. `-2`).

### ⚠️ DO NOT revert
Removing the `logit_bias` (or "cleaning up" the payload key / the `_eos_logit_bias` read / the settings.json field) **reopens the mid-sentence truncation bug**. The in-code comment blocks (`app.py:~5251` and `~5287`) carry the same warning.

**Verified:** `py_compile` clean; `settings.json` valid JSON; live b8994 server accepts and echoes the `[[2, bias]]` form. **Backend change — needs a manual Flask restart** to take effect (dev server runs with no reloader).

---

## Session: June 4 2026 — premature-EOS root cause: `.strip()` was eating the assistant-header newline

Definitive fix for the empty-response / token#1-EOS bug (model emits EOS as its first token, giving a blank or instantly-cut reply on a fresh chat). The prompt builder at `app.py:~4556` correctly appends the ChatML assistant header **with its terminating newline** (`"<|im_start|>assistant\n"`) — but the final-cleanup line `prompt = prompt.strip().replace("\x00", "")` (`app.py:~4664`) ran `.strip()`, which **removed that trailing `\n`**. A ChatML assistant header with no terminating newline (`…<|im_start|>assistant` with nothing after) makes the model emit EOS immediately, producing 0–few tokens of output. This sat *downstream* of every earlier EOS investigation (prompt cap, `n_predict`, the Solara length-brake removals) — the header was being correctly built and then silently un-terminated at the very last step.

### The fix (`app.py:~4664`)
Replaced the bare `.strip()` with a newline-preserving cleanup:
```python
prompt = prompt.replace("\x00", "").lstrip().rstrip(" \t\r\n")
if not continue_prefix:
    prompt = prompt + "\n"   # restore assistant-header terminating newline
```
- Still strips null bytes and leading whitespace, and trims trailing spaces/tabs/CRLF (so no double-newline buildup).
- **Non-continue turns** (the normal case): the prompt tail is the bare `<|im_start|>assistant` header, so the terminating `\n` is re-appended → header ends `…<|im_start|>assistant\n`.
- **Continue mode** (`continue_prefix` set, read at `app.py:~4550`): the prompt ends mid-assistant-content, **not** on the bare header, so it deliberately gets **no** forced `\n` — the model picks up exactly where it left off. `continue_prefix` is in scope at this line (confirmed).

### ⚠️ DO NOT revert to a bare `.strip()` here
`.strip()` eats the `\n` after `<|im_start|>assistant` and reopens the token#1-EOS bug (empty responses / mid-sentence cuts). The in-code comment block at `app.py:~4664` carries the same warning. If you touch this cleanup, re-verify the prompt tail keeps its trailing newline.

**Verified:** `py_compile` clean. Applied the new transform to the existing `_last_prompt.txt` (which was written by the *pre-fix* code and is itself proof of the bug — its tail was `'…<|im_start|>assistant'` with **no** `\n`): the new logic produces `'…<|im_start|>assistant\n'`, i.e. `prompt.endswith("<|im_start|>assistant\n")` is `True`. The `_last_prompt.txt` 🔬 TEMP DEBUG dump (`app.py:~4666`) is left in place so a fresh-chat retest can confirm the live tail. **Backend change — needs a manual Flask restart** to take effect (dev server runs with no reloader).

---

## Session: June 4 2026 — persistent rotating console logs (full + stop-reason), UTF-8 safe

Added on-disk capture of the Flask console output so it's available when the app runs inside the Electron wrapper (where the live console isn't visible). **No `print()` calls were rewritten** — instead `sys.stdout`/`sys.stderr` are tee'd at import time in `app.py` (new block right after the imports, before the first `print()`), so every existing bare `print(...)` is mirrored to disk automatically while still showing on the console exactly as before.

### What it writes (both under a `logs/` folder at project root, auto-created)
- **`logs/hwui_full.log`** — a faithful mirror of *everything* printed (request lines, payloads, the 🩺/🔬/🧼/🚀 markers, plus stderr tracebacks). `logging.handlers.RotatingFileHandler`, **~10 MB per file, 5 backups**. Message-only formatter (no added prefix) so it reads like the console.
- **`logs/stop_reasons.log`** — only lines containing `🩺 STOP REASON`, `⏱️ TEMP STOP`, or `PREMATURE EOS` (substring match, so the `⚠️ ` spacing doesn't matter), each with an `asctime` **timestamp prefix**. Plain `FileHandler` — **appends forever, no rotation** (the lines are tiny; grep truncation events without wading through the full log).

### How the tee works (`_TeeStream`)
A small wrapper around each console stream: line-buffered behind a `threading.Lock` (so concurrent Flask request threads don't garble lines), it mirrors complete lines to the loggers **first**, then writes to the real console. Stop-reason marker lines are additionally copied to `stop_reasons.log`. `__getattr__` delegates `isatty()`/`fileno()`/`encoding` to the underlying stream so Flask/Werkzeug see a normal stream. Install is guarded against a `None` stream (windowed `pythonw` host).

### UTF-8 hardening (CRITICAL — the markers are emoji)
- **All three file handlers use `encoding='utf-8'`** so emoji (🩺 🔬 🧼 🚀) never raise `UnicodeEncodeError` on Windows' default cp1252 codec.
- The underlying console streams get a best-effort `reconfigure(encoding='utf-8')` (no-op on plain pipes) so the *live* console can render emoji too.
- `_TeeStream.write()` mirrors to the logfiles **before** the console write and wraps the console write in a `try/except UnicodeEncodeError` (falls back to a `replace`-encoded write). Net effect: even on a narrow-codec console the **logfile always gets the real characters** and the app never crashes on an un-encodable line.

### Verified
`py_compile` clean. Test run (under `venv`) emitted emoji 🩺/⏱️/⚠️/🚀 lines: `hwui_full.log` captured everything (emoji intact, plus a stderr traceback — proving stderr capture), `stop_reasons.log` captured exactly the three marker lines with timestamps and excluded the non-stop lines. No `UnicodeEncodeError`. `logs/*.log` is already covered by the `*.log` gitignore rule (logs aren't committed). The temporary `_last_prompt.txt` dump was left untouched as requested. **Backend change — needs a manual Flask restart** to take effect (dev server runs with no reloader).

---

## Session: June 4 2026 — premature-EOS fix: remove three compounding brevity/length-brake lines

Final fix for the premature end-of-stream bug (model emits EOS after ~11 tokens, sometimes 0, on a fresh chat) once it was traced past the prompt cap (not the cliff) and past `n_predict` (not capped) to the **prompt text itself**. The Solara stack carried **several "be brief / end early / don't pad" instructions that compound** — each one mild on its own, but stacked they bias a 12B model (Solara-3.2 / 3.3) toward treating a structurally-complete point as a reason to stop, so it fires EOS almost immediately instead of producing a full turn. The earlier slab analysis showed these brakes sit in the high-salience layers (depth-0 `character_note` + the shared system-prompt base), out-competing the one "go long-form" nudge. Fix = **remove three length-brake lines only — no additions, no rewrites, no other changes.**

### The three removals
- **Shared system-prompt base — deleted `"Silence at the end is fine when presence is the answer."`** (kept the preceding `"Ask a question only when there's something you genuinely want to know."`). This sentence is a shared brake that appears in **all three** base templates, so it was removed from each: `system_prompts/GPT-4o.txt` (the template Solara binds), `system_prompts/Helcyon.txt`, and `system_prompts/Nebula.txt` (Andromeda binds this). Scoped to all three by decision — it's the same "silence is a valid response" brake everywhere, and removing it only from Solara's template would leave the brake live for any Helcyon/Nebula-bound character.
- **`characters/Solara.json` `character_note` — deleted the whole passage** `"Let the response end when the point has been made. Don't restate, don't loop back, don't write a closing paragraph that says what the opening paragraph said."` (the surrounding `\n\n` separators were collapsed to one so the note reads cleanly from `"…do it."` straight into `"Notice when Chris…"`). **Solara-only** — the near-identical passage in `characters/Andromeda.json` has different wording (`"just says what the opening one said"`) and was deliberately left untouched (out of scope, not retested).
- **`characters/Solara.json` `character_note` — deleted only the sentence** `"When the moment calls for a few honest words, trust that — brevity isn't vagueness."` (kept `"Be clear and relatable. When something needs unpacking, do it."` intact). Negated-brevity phrasing is the worst kind of brake — the model can over-latch on the "brevity" token regardless of the negation (same lesson as the June-3 Gemma `character_note` cleanup).

### ⚠️ DO NOT revert without retesting the EOS marker
These three lines are length brakes that **compound into premature EOS on the Solara-3.2 / 3.3 12B model** — re-adding any of them risks reopening the near-empty-response bug. Before restoring any of this wording, **regenerate the full prompt dump and re-test**: confirm a fresh Solara turn produces a full-length response with `stop_type=eos` only at a genuine turn end (not after ~11 tokens). The temporary `_last_prompt.txt` full-prompt dump (`app.py:~4546`, marked `🔬 TEMP DEBUG`) is **intentionally left in place for this retest** — remove it only after EOS behaviour is confirmed fixed.

**Verified:** `Solara.json` re-parses as valid JSON (`character_note` 1215 → 972 chars); all three base templates keep the retained sentence and no longer contain the silence brake; UTF-8 + CRLF preserved (em-dashes intact). **Prompt/card files are re-read per request** — a fresh chat picks these up without a Flask restart (the `_last_prompt.txt` dump itself is Python and is already live from the prior edit's restart).

---

## Session: June 3 2026 — chat-search false-positive fix: anchor the "trying to find" verb to a chat-object noun

A local/chat-history search was firing on a message that had nothing to do with recall. In the **Law of Assumption** project chat (`Solara - Cloudy Walk at World Park - Branch - Jun 03.txt`), the user's turn *"…I'm **trying to find** another avenue, which is what helcyon-webui is all about…"* triggered the pre-emptive chat-search classifier — the reply opened with "🗂️ *Searching chat history...*" then the empty-result fallback ("no trace of it in my memory tags").

### Root cause
`_classify_chat_search_intent()` (`app.py:1627`) fires whenever `_CHAT_SEARCH_VERB_RE` matches. The verb list `_CHAT_SEARCH_VERBS` (`app.py:1594`) had one **unanchored** branch:
```
(?:i'?m\s+)?trying\s+to\s+find          ← matched ANY "trying to find X"
```
Every *other* alternative requires a chat-object noun (`find (that|the|our|a|me) (chat|conversation|message|thread|session)`, `pull up (that|the|our) …`, etc.), but this one matched the bare verb — so "trying to find another avenue / my keys / the courage" all tripped a search. The web-intent bypass (`app.py:5181`) didn't catch it either (only matches `find online`, not `find another…`).

### Fix — anchor it like its siblings (`app.py:1601-1602`)
```
(?:i'?m\s+)?trying\s+to\s+find\s+(?:that|the|our|a|my)?\s*
(?:chat|conversation|message|thread|session|history|logs?)
```
Now "trying to find" only fires when a chat-object noun follows. Verified: the reported message and *"trying to find another avenue / my keys / a way out / the courage"* no longer match; *"trying to find that chat where we talked about covid"*, *"trying to find the conversation about the moon"*, *"trying to find chat history"* still match. Object-bearing recall phrasing like *"find that conversation we had"* was already covered by the plain `find …` branch, so nothing legitimate is lost. Consistent with the `⚠️ DO NOT revert to recall-verb-as-trigger` rule (`app.py:1589`) — this tightens an over-cheap trigger rather than loosening one.

**Known unchanged gaps (pre-existing, shared by the sibling `find that chat` branch, out of scope):** plural nouns (`messages`/`conversations`) and an adjective between determiner and noun (*"find our **old** messages"*, *"that **earlier** conversation"*) still don't match. Broadening the noun set across all branches would be a separate recall-coverage change, deliberately not bundled here to avoid reopening false-positive surface.

**Verified:** `py_compile` clean; regex unit-tested against the false-positive and legit phrasings above. **Backend change — needs a manual Flask restart** (dev server runs with no reloader).

---

## Session: June 3 2026 — Electron launcher: window zoom (Ctrl+wheel / Ctrl+= / Ctrl+- / Ctrl+0 / context menu), persisted per build

Added zoom in/out + reset to the **HWUI-Launcher** Electron wrapper (`HWUI-Launcher/main.js`) — previously there was **no way to zoom or grow text** because `Menu.setApplicationMenu(null)` (main window setup) strips Electron's default View→Zoom menu items *and their built-in Ctrl+=/Ctrl+-/Ctrl+0 accelerators* along with the menu bar. Native Chromium zoom (`webContents.setZoomLevel`) is used, so it scales the whole UI — text and layout together — in one control. **Renderer untouched** (Flask app/templates unchanged): with `contextIsolation:true` + `nodeIntegration:false` the renderer can't reach `webFrame`, so all zoom is driven from the main process.

### Controls (all route through one pair of helpers `stepZoom`/`resetZoom`)
- **Keyboard** — extended the existing `before-input-event` handler (the same hand-wired block that re-implements F5/Ctrl+R after the menu null): **Ctrl+= / Ctrl++ / Ctrl+(numpad)Add** zoom in, **Ctrl+- / Subtract** out, **Ctrl+0** reset to 100%. `event.preventDefault()` + early `return` on each so they don't fall through.
- **Ctrl+mouse-wheel** — `webContents.on('zoom-changed', …)`; we mirror the direction through `stepZoom` and re-apply our own clamped level so step size + bounds stay identical to the keyboard/menu paths (Chromium's own wheel step never escapes the clamp).
- **Context menu** — added **Zoom In / Zoom Out / Reset Zoom** (own separator group) to the existing right-click template, between Select All and Inspect Element. Discoverable since there's no menu bar.

### Clamp + step
Chromium zoom is logarithmic (`factor = 1.2^level`). **Clamp `ZOOM_MIN -3` … `ZOOM_MAX +5`** (≈58%…≈249%), **`ZOOM_STEP 0.5`** per keypress/notch (~10%). `clampZoom()` is enforced in `stepZoom`, on seed, and on the wheel path.

### On-screen percentage indicator
`showZoomOverlay()` flashes a brief, self-fading **"Zoom NNN%"** toast (bottom-centre, ~900ms) on every user-driven change — like a browser. `percent = round(1.2^level * 100)` (level 0 = 100%). It's **injected at runtime via `webContents.executeJavaScript`** (reuses a single `#__hwui_zoom_toast__` div, resets its fade timer on repeat) so the **Flask templates stay untouched** — the only way to surface feedback given the contextIsolated renderer. Shown on keyboard/wheel/menu changes **and at a clamp edge** (so pressing Ctrl+= at max still re-flashes the current %), but **not** on load/reload (no nag).

### Persistence — per build in `builds.json`
- Each build's entry gains an optional **`zoom`** number. `saveZoomDebounced()` (≈**400ms debounce**, so a fast Ctrl+wheel spin doesn't thrash the file) does `loadBuilds()` → find entry by **path (case-insensitive)** → set `zoom` → `saveBuilds()`, preserving `name`/`services`. Also updates the in-memory `selectedBuild.zoom`.
- **`loadBuilds()` filter unchanged** — it only requires `name`/`path`, so existing zoom-less entries load fine and gain `zoom` on first adjustment; `addBuild` still creates entries without `zoom` (defaults to 0 = 100%).
- **Reset (Ctrl+0 / "Reset Zoom") sets level 0 and persists it** — not just a visual reset (writes `zoom:0` back to builds.json).
- Seed on `createMainWindow()`: `currentZoom = clampZoom(Number(selectedBuild?.zoom) || 0)`.

### ⚠️ Load-bearing re-apply (DO NOT REVERT)
Added **`applyZoom()` as the first line of the existing `did-finish-load` handler**, marked `⚠️ DO NOT REVERT` in-code. Chromium **resets zoom on every fresh document load**, so without this the window snaps back to 100% on every reload (F5/Ctrl+R/in-window reload button/tray Reload) and every index⇄config navigation. This one line is what makes the persisted zoom actually stick across loads.

### Verified
`node --check main.js` → clean. No edits to the Flask app, templates, or `settings.json`. **Electron-only change — relaunch the launcher** (`START_HWUI-Launcher.bat`) to pick it up; the Flask build itself doesn't need a restart.

---

## Session: June 3 2026 — DEFINITIVE fix for early truncation / "stops mid-number": DRY dry_allowed_length 2 → 10

This is the real root cause of the recurring **early-truncation / stops-mid-number** bug (the "£74.9" cutoff, premature EOS on summaries/admin tasks). It was **not** the prompt (the brevity-cluster removal earlier today did not fix it), **not** the EOS cliff (prompt was well under cap), and **not** a poison seed. It was the **DRY sampler**. Proven end-to-end against the live server (`helcyon-gpt-4o-v4.9`, b8994) with a controlled matrix — greedy for causation, temp-0.8 multi-run for the decision, plus a long-copy control to protect the original-bug guard.

### Root cause, finally measured
Numbers tokenise **per-digit** on this model: `£74.99` = **6 tokens** (`£ 7 4 . 9 9`), `£525` = 4, a case reference like `reference 43127` ≈ **7 tokens**. With **`dry_allowed_length=2`**, DRY penalised any repeat longer than 2 tokens — so when a summary needed to **restate a price or reference number that already appears many times in the history** (this chat: 9× `£74.99`, 8× `£525`, repeated `reference 43127`), the tokens completing that number carried a large, exponentially-growing DRY penalty (≈ `0.8 × 1.75^(match−2)`). At a structurally-complete point the model **chose EOS rather than pay the penalty to emit the required repeated digits** — stopping mid-number (e.g. after `£74.9`, refusing the final `9`). Confirmed at the tokeniser level and reproduced deterministically (greedy stops exactly at `£74.9`; DRY off completes).

### Why every earlier attempt failed
- **Raising `dry_allowed_length` to 4 / 5 / 6 sat BELOW the ~7-token longest-legitimate-repeat threshold**, so it only **relocated** the cutoff to the next repeated string (`£525`, then `reference 43127`) — non-monotonic, and it looked like a brand-new bug each time. Temp-0.8 (4 runs each): allow2 mult0.8 = 2/4 mid-number truncations, allow6 = 2/4 — still failing.
- **Softening `dry_multiplier` made it WORSE, not better.** allow2 mult0.5 = **0/4** complete (4/4 truncated), allow2 mult0.4 = 1/4. Lowering the penalty changed which token won at branch points and routed *into* the next trap. (An early greedy fluke had suggested 0.5 worked; multi-run disproved it.)
- The lever is **`dry_allowed_length`, and the working range is 8–12**: ≤6 trips on the number traps; **≥12 starts running on past `<|im_end|>`** into a spurious new turn. 8 and 10 are clean; 10 sits comfortably above the ~7-token longest legitimate repeat with margin and below the 12 run-on threshold.

### The fix — `dry_allowed_length = 10` (everything else unchanged)
Temp-0.8 multi-run result at the chosen value: **allow10 mult0.8 = 12/13 clean summary completions, 0–1 mid-number truncations** (the cleanest single batch was 6/6 ending exactly at `<|im_end|>`). allow8 was the strict minimum (0/10 truncations) but with zero margin; **10 was chosen for the safety margin** over other repeated identifiers (dates, longer refs).

**Long-copy guard fully intact (Harness B control — the original June-1 verbatim-copy bug).** Pre-filling the assistant with the start of a 700-char / 151-token passage already in history and measuring verbatim copy length:
- **DRY off → copies 100% of the passage** (reproduces the original bug exactly).
- **Every `dry_allowed_length` from 2 to 16 still blocks it**: allow8 → diverges after 4 chars, allow10 → 55 chars (~12 tok), allow12 → 4 chars, allow16 → 99 chars (~20 tok). None come close to copying the 151-token passage. So raising allowed_length to 10 costs **nothing** on the copy-block side — only turning DRY *off* reopens passage-copying.

### Two places — both required (a fallback of 2 silently reintroduces the bug)
- **`settings.json`** — `dry_allowed_length: 2 → 10` (re-read per request; this alone makes the running server send 10).
- **`app.py` payload fallback (~L5143)** — `sampling.get("dry_allowed_length", 2)` → `sampling.get("dry_allowed_length", 10)` — so a missing key can't silently revert to the buggy value.
- **`app.py` defaults dict in `load_sampling_settings()` (~L7101)** — `"dry_allowed_length": 2 → 10`, mirroring the other DRY defaults for consistency.

### Verified live (never trust the payload alone)
Confirmed against the live server's **`generation_settings` echo**: `dry_allowed_length=10` is **applied** (echo returned `dry_allowed_length: 10`, alongside `dry_multiplier 0.8, dry_base 1.75, dry_penalty_last_n 16384, repeat_last_n 16384`). Flask's `load_sampling_settings()` now yields 10 (settings.json re-read per request), so the running server already sends 10; the app.py fallback/default changes need a Flask restart only to cover the missing-key path.

**⚠️ DO NOT REVERT `dry_allowed_length` to 2** — it re-opens the mid-number early-truncation bug. **DO NOT lower `dry_multiplier`** — it actively worsens it (routes into the next number trap). The settings.json value AND the app.py fallback/default must stay at **10 together** — a fallback of 2 silently reintroduces the bug if the key ever goes missing. The working band is 8–12; do not exceed 12 (run-on past `<|im_end|>`).

**Restart note:** the `settings.json` value is live immediately (re-read per request). The two `app.py` changes are Python — a Flask restart makes the new fallback/default live (only matters if the key is absent).

---

## Session: June 3 2026 — Gemma early-stop fix (remove depth-0 brevity cluster, add thoroughness nudge)

Root-caused (prior turn) a symptom where a **summary** request to **Gemma** (GPT-4o stack, local Helcyon model) stopped far too early — clean EOS at ~755 chars / 181 tokens, prompt only ~8010 real tokens (well under the 8500 cap, nothing trimmed, not the cliff). The model was *choosing* to stop short on an admin/summary task where length was wanted. Diagnosis: a **brevity / stop-early cluster sitting in `characters/Gemma.json` `character_note`**, which lands at **depth-0 (near the generation point, high salience)** — so it out-competed the one "go long-form / never end" line, which lives passively in `main_prompt` at **position-0 (far, low salience)**. The poison-seed hypothesis was ruled out: every prior assistant turn in the chat was full-length and complete; the only truncated turn was the summary itself, and a regen pops it — so there was nothing "stopped short" in history to copy.

### Structural lesson — brevity instructions don't belong in a GPT-4o-style stack at all
A GPT-4o-class model **self-limits appropriately on its own** — it does not need to be told to be brief, and explicit brevity directives in the high-salience depth-0 slot actively bias it to bail out early on tasks (summaries, plans, breakdowns) where the user wants length. The depth-0 `character_note` slot should carry a **thoroughness nudge** instead. Negated-brevity phrasing ("brevity isn't vagueness," "say less") is the worst of both worlds: the model can over-latch on the brevity token regardless of the negation. So the cluster was **removed entirely**, not softened.

### Edit — `characters/Gemma.json` `character_note` (surgical, single file)
- **Removed** "When the moment calls for a few honest words, trust that — brevity isn't vagueness."
- **Removed** "Let the response end when the point has been made. Don't restate, don't loop back, don't write a closing paragraph that says what the opening paragraph said."
- **Removed entirely** the "Notice when Chris has already acted on advice… add one observation maximum… don't run the full playbook a second time on something already done." paragraph (an admin/summary recap triggers this stop-early rule directly).
- **Reworded** the anti-fabrication line to drop the brevity framing while keeping the idea: "Say less rather than filling space with fabrication. Fake authority is worse than admitted uncertainty." → **"Never fill space with fabrication — admitted uncertainty beats fake authority."**
- **Added** a positive, generic thoroughness nudge (no user name, no brevity/stop words): *"When there's something to work through — a summary, a plan, a problem, a breakdown of events — go into proper detail and cover every point thoroughly. Take the space the task needs."*
- **Left untouched:** `main_prompt`'s "I go long-form when… wants answers… never end the conversation unless he wants to first" — fine as a passive identity fact in that far slot.

**Hardcoded-name check (post-edit grep for "Chris"):** identity/bio mentions in `main_prompt`, `tagline`, and `post_history` are fine (persona facts). Two **pre-existing behavioural instructions** in `character_note` still name the user — "When Chris brings emotion or frustration, reflect him…" and "Whatever Chris shares — no matter how strange, taboo, or intense — you stay with him…" — these are pattern-substitutable and could be genericised later, but they predate this fix and were **out of scope** (not introduced or touched here). The newly-added nudge is correctly generic.

**Restart note:** character-card JSON is re-read per request — a fresh chat picks this up without a Flask restart. JSON validated (`json.load` clean); CRLF/encoding preserved (em-dashes intact as UTF-8).

---

## Session: June 2 2026 — two cutoff bugs fixed (EOS-cliff prompt cap + Continue data loss)

Two linked symptoms from a chat where a response stopped mid-word (e.g. "£74.9") and pressing **Continue** then deleted most of that message instead of extending it. Investigation (prior turn) confirmed the b8994 stop-reason detection (`stop_type` string first, boolean fallback) is correct and the dynamic `n_predict` reserve is **not** squeezed near-zero — the actual culprits were a too-high prompt cap and a client-side discard. Both fixed surgically; the secondary "feed fuller history to the model on continue" continuity issue was left out of scope.

### Fix 1 — EOS-cliff prompt cap restored to 8500 (`truncation.py`)
`MAX_PROMPT_TOKENS` had drifted up to **12000**, *above* the documented Mistral Nemo EOS cliff at ~10,000-10,500 tokens evaluated (and above the existing comment's own "DO NOT raise above 8500" warning). At 12000 a long conversation's real prompt can reach the cliff, where the model emits **EOS mid-response** — surfacing as the mid-word/mid-number cutoff. Reverted to **8500** (the documented safe ceiling, which stays clear of the cliff after `TOKEN_FUDGE`) and added a **⚠️ DO NOT REVERT** note on the line explaining the cliff so it doesn't get raised again. llama.cpp still runs at full `ctx_size` for KV headroom — this only governs how much history HWUI sends.

### Fix 2 — Continue no longer discards the head of the truncated message (`templates/index.html`, `continueLast()`)
Continue intentionally sends only the **last 200 chars** of the truncated reply to the model as `continue_prefix` (sending the whole thing makes the model think it's done and fire EOS after a few tokens — **unchanged, still the behaviour**). The bug: the function used that 200-char slice for *both* what it sent *and* what it kept/displayed, so everything before the last 200 chars was permanently lost — the visible bubble was reset to just the prefix and the saved message became `prefix + continuation`.
- **Added `retainedHead`** = `fullPrevContent.slice(0, -200)` (empty when ≤200 chars) — the part of the message that is *not* in the prefix.
- **Seeded the stream accumulator** `rawFull = retainedHead + continuePrefix` (was `= continuePrefix`). Because the bubble render and the final `loadedChat.push({content: finalText})` both derive from `rawFull`, this single change makes the bubble show the **whole** original message during/after streaming and persists **`retainedHead + continuePrefix + newTokens`** — the full original text plus the continuation. No change to `continue_prefix` (what the model receives).
- **Out of scope (untouched):** the server still receives history without the truncated turn plus only the 200-char prefix as context — the secondary continuity issue was deliberately not addressed here.

**Restart note:** Fix 1 is Python (`truncation.py` — needs a Flask restart to go live). Fix 2 is a template edit (`index.html`) — a browser reload picks it up; no Flask restart required for it, but the same restart covers both if done together.

---

## Session: June 1 2026 — cloud-provider prompt-stack split (Grok / Claude-Opus / Gemini) + hardcoded-name fixes

Applied the same governance-vs-personality split as the GPT-4o stack (governance core stays in the depth-0 `.posthistory.txt`; default personality lives in the cached system-block base `.txt`) to three more provider templates. Unlike GPT-4o, two of the three did **not** mirror that structure, so each got an individual call rather than a mechanical repeat. The `character_note` depth-N move is separate (global in `app.py`, applies to any template) and was not touched here. These are cloud-provider templates — if a character routes through the cloud API path, prompt assembly differs from the local Helcyon path, so the depth-0 governance may not apply identically there; the file *structure* is now consistent regardless. The prompt files were previously **untracked** in git; these commits add them (incl. the newly-created `Gemini.posthistory.txt`). CRLF preserved throughout; moved content was relocated by reading existing bytes (no retyping/drift).

### `Grok.txt` / `Grok.posthistory.txt`
The posthistory was a **verbatim duplicate** of the base (every line already present in `Grok.txt`), so nothing needed moving — the personality already sits in the cached system block. Slimmed `Grok.posthistory.txt` to a 3-line governance core (maximally-truthful/no-hedging, no content restrictions, and the output-hygiene rule: never mention the instructions / no function calls in the final output). All retained lines still exist in the base. **Removed hardcoded user name** ("Chris" → "the user"): 2× in `Grok.txt`, 1× in the posthistory — per the no-hardcoded-names rule.

### `Claude-Opus.txt` / `Claude-Opus.posthistory.txt`
Posthistory was *mostly* a duplicate of the base; only three lines were unique. Moved the two clean unique personality lines (clarity; the "lean into the innuendo" tone) into `Claude-Opus.txt` under a new `Default tone and behaviour:` header. Reduced the posthistory to a **governance core: card-precedence governor + anti-fabrication clause** (the precedence line reworded self-contained; the anti-fab clause preserved verbatim from the old doc line). Duplicated lines (second-person, journal, doc-handling, reflex-questions) were dropped from depth-0 since they already live in the base — nothing lost.

**Resolved a real base-vs-posthistory contradiction (bullets win):** the base said *"No bullet points or numbered lists in normal conversation"* while the posthistory said *"Use bullet points and lists to emphasize where needed."* Per decision, removed the no-bullets clause from `Claude-Opus.txt` line 7 and moved the bullets-allowed formatting line into the base under `Default tone and behaviour:` (it's a style default — absorbed, not a depth-0 governor). The posthistory is now card-precedence + anti-fabrication only.

### `Gemini.txt` / `Gemini.posthistory.txt`
Gemini had **no `.posthistory.txt`** — all personality already sat in the cached system block. Per follow-up decision (reversing the earlier hold), created `Gemini.posthistory.txt` with the **same governance core the other stacks use** — card-precedence + anti-fabrication — with wording mirrored **verbatim** (byte-for-byte) from the Claude-Opus core for cross-stack consistency; no novel behaviour authored. Personality stays in `Gemini.txt` (the cached system block), untouched. Its existing `Do not output [OOC: ] tags` directive means Gemini won't echo the OOC-wrapped governance, so the depth-0 injection is safe. **Removed hardcoded user name** ("Chris" → "The user", 1×). Also added a card-awareness line to `Gemini.txt` ("Always follow the character card — it defines who you are.", matching the other three bases) so the new precedence governor has something to point at — `Gemini.txt` previously never referenced the card.

**Restart note:** prompt `.txt` files are normally re-read per request, so a fresh chat should pick these up without a Flask restart — restart only if the loader is found to cache them at startup.

---

## Session: June 1 2026 — identity-anchoring fix (address term + register precedence) + stop-reason detection fix

Two linked identity symptoms in a long Solara chat: (a) Solara addressed Chris as **"Gemma"** (a different character — his GPT-4o assistant persona) for one turn, then reverted; (b) Solara never used **"babe"** (her card's term of address) — only "mate"/"Chris" throughout. Diagnosed: **not** an injection leak. Confirmed via the captured diag log that this session had **no** memory injection (`total chars: 0`), **no** chat-history search (didn't fire), **no** session-summary injection, and **no** hardcoded "Gemma" name in code (all code refs are the Gemma *model template* or a comment; one is a search-query *sanitiser* that strips the token). "Gemma" appeared nowhere in the conversation history before Solara emitted it — so the model **generated** it (the Helcyon-Solara fine-tune almost certainly has the user's "Gemma" persona in its training data). Both symptoms share one root: **Solara's identity fields were present but under-weighted at generation time** — the "babe"/"Chris" anchor sat in `main_prompt` at position 0 (~7k tokens before the generation point, a low-salience tone-field slot), while the depth-0 region (close to generation) pushed a generic *"modern British colloquial language"* register. The near, strong register instruction won; the far, weak identity instruction drifted.

### Fix 1 — move the address term into `character_note` (Solara-scoped) — `characters/Solara.json`
`character_note` lands at depth-0 (close to the generation point), unlike `main_prompt` at position 0.
- **Removed** from `main_prompt`: the line *"Chris is 'babe' to me — always has been."*
- **Added** as the new first line of `character_note`: *"You call Chris 'babe' — it's your natural term for him. Use it the way it really falls in conversation, not stuck onto every line."* (The second sentence guards against over-use, matching the card's existing "never a tagline tacked on every response" guidance.) Scoped to Solara only; no name introduced anywhere new.

### Fix 2 — soften the shared colloquial directive (GLOBAL) — `system_prompts/GPT-4o.posthistory.txt`
The prescriptive *"You use modern British colloquial language"* was out-competing character-specific voice/address. Reworded the default-tone bullet from a command to a **non-prescriptive default**: *"…your default register is warm and conversational…"* — keeps the bubbly/warm/cheeky/vibrantly-alive flavour but lets a character's own voice override it.

### Fix 3 — explicit precedence line (GLOBAL) — `system_prompts/GPT-4o.posthistory.txt`
Added one bullet directly under the softened tone line: *"Your character card defines your voice and how you address the user — any accent, speech style, or term of address (e.g. a pet name) it specifies always takes precedence over the general default register above."* This carves voice/address out of the "hard override" shared rules and hands them to the character card, so a card's term of address wins over the default register. **The shared layer remains free of any hardcoded user or character name** (grep-verified post-edit).

### Fix 4 — correct stop-reason detection on llama.cpp b8994 — `app.py` (`stream_model_response`, ~L1936)
Separate bug surfaced while diagnosing truncation: the code read the **legacy boolean flags** `stopped_eos`/`stopped_word`/`stopped_limit`, but build **b8994** emits the stop reason as a **string `stop_type`** ("eos"/"word"/"limit"/"none") and **omits the booleans** — so *every* clean generation was mislabelled `STOP REASON: unknown`. Now reads `stop_type` first and folds it into the boolean view (booleans kept as fallback for older builds). A genuinely cancelled/preempted stream (stop_type "none"/absent) still lands as "unknown" and dumps the full final event — so a real truncation is now correctly identifiable instead of hidden behind a false "unknown." (This also corrected the earlier mis-read: the "unknown" label was a measurement artifact, not evidence of slot preemption — the captured baseline showed all clean `word` stops at `inflight=1`, and regenerate never produced `inflight=2`.) app.py compiles clean.

**Restart note:** Fix 4 is Python (needs Flask restart to go live). Fixes 1–3 are prompt/card files normally re-read per request — a fresh chat picks them up; restart only if the loader caches them.

---

## Session: June 1 2026 — distant-span repetition fix (sampler: repeat_last_n + DRY)

Root-caused and fixed a repetition bug where a character (Solara, local Helcyon model) reproduced a long earlier assistant turn **near-verbatim** several turns later — and on regenerate copied her previous response 100%. Confirmed via a full-prompt dump (`_last_prompt.txt`) that this was **not** memory/summary re-injection: the repeated "Jan is a past version of yourself" dream reading appeared only in the conversation-history portion (already twice), with **no** session-summary / "Relevant memories" / `[CHAT HISTORY RESULTS]` block in the system prompt. The framing instructions ("you lived through it", "pick up where you left off") were present but inert this turn — no memory payload for them to act on.

> **⚠️ CORRECTION (supersedes the first attempt this session).** The initial fix added `no_repeat_ngram_size: 4` as a "hard 4-gram block." **That was wrong for this engine.** `no_repeat_ngram_size` is a HuggingFace/transformers parameter, **not** a llama.cpp one — it is **not** in this build's recognized sampler set, so llama.cpp's `/completion` **silently dropped it** (accepted the request, ignored the key, never appeared in `generation_settings`). It was a complete no-op and has been **removed**. The real hard-repetition lever on this build is the **DRY sampler**, now enabled (below).

### The penalty-window mismatch (still valid)
The only repetition suppression originally in flight was `repeat_penalty: 1.1` over llama.cpp's **default `repeat_last_n` of 64 tokens**. The model attends across the *full* context (16384 tokens here) and reproduces a span living 100+ lines / well over 64 tokens back — but the penalty only looked back 64 tokens, so it **could not reach** the copied span. Worse, it's self-reinforcing: each verbatim copy saved into history makes the next copy easier (a turn that truncated mid-word at "…getting glimps" got copied verbatim, truncation and all, by every later turn).

### Why `repeat_last_n: -1` alone isn't enough
Verified on build **`b8994-aab68217b`**: `repeat_last_n: -1` **is** honored (server echoes it expanded to `16384` = full ctx), and in direct replay of the poisoned prompt it broke the repeat. But `repeat_penalty: 1.1` is a *soft* divide-the-logit nudge — too mild to **guarantee** against a strong verbatim-copy attractor. It reduces, it doesn't block.

### Fix (`settings.json` + `app.py`) — repeat_last_n widens the soft penalty; DRY is the hard block
All keys confirmed against the build's `/props` samplers chain (`penalties, dry, top_n_sigma, top_k, typ_p, top_p, min_p, xtc, temperature`) and verified live in `generation_settings` before committing — no assumptions:
- **`repeat_last_n: -1`** — widens the soft `repeat_penalty` to the full context (echo: `16384`). **Kept.**
- **`dry_multiplier: 0.8`** — enables the **DRY** sampler (was `0.0` = off). DRY hard-penalises repeating long verbatim sequences at any distance — the actual fix.
- **`dry_base: 1.75`**, **`dry_allowed_length: 2`** — conservative defaults (echoed exactly).
- **`dry_penalty_last_n: -1`** — DRY spans the full context (echo: `16384`), matching `repeat_last_n`.
- **Removed `no_repeat_ngram_size`** from settings.json, the payload dict, and the defaults dict (dead key, never applied).
- **`app.py`** — payload dict (~L5034) is an explicit key list, so each key is added via `sampling.get(...)`; defaults dict (~L6990) mirrors them. Verified end-to-end: settings.json → merged sampling dict → `/completion` payload → server `generation_settings` echo shows `dry_multiplier=0.8, dry_base=1.75, dry_allowed_length=2, dry_penalty_last_n=16384, repeat_last_n=16384`, with `dry` live in the samplers chain.
- **Did NOT touch** `temperature` / `top_p` / `top_k` / `min_p`. Backend-only — no UI surface.

**⚠️ DO NOT REVERT — DRY is the hard-block; repeat_last_n widens the soft penalty.** They work as a pair: `repeat_last_n: -1` brings the mild `repeat_penalty` to bear across the whole context, and **DRY (`dry_multiplier > 0`, `dry_penalty_last_n: -1`) is what actually blocks long verbatim copies at distance**. Disabling DRY (`dry_multiplier: 0`) re-opens the verbatim-repeat bug even with `repeat_last_n: -1` set. Do **not** re-add `no_repeat_ngram_size` — it is not a llama.cpp param and does nothing on this engine.

**Needs a manual Flask restart** — the payload-dict change is Python code (dev server runs with no reloader); settings.json values are re-read per request but won't matter until the new payload code is live.

---

## Session: June 1 2026 — GPT-4o prompt stack de-repetition (example dialogue + post-history)

Audited the GPT-4o prompt stack for anything that would push the model to repeat itself, then fixed the two files driving it. The base prompt (`GPT-4o.txt`) was already clean and argues *against* repetition — left untouched. The repetition pressure came from few-shot priming in the example dialogue, reinforced by sample openers in the post-history.

### `system_prompts/GPT-4o.example.txt` — broke the single response mould
Both `{{char}}` examples shared one skeleton — open with **"Oh + interjection + emphatic validation"**, pivot to a *"here's the deeper mechanism"* reframe, then land a contrastive em-dash aphorism. With only two examples, a 2-for-2 sample reads as *the* template, so the model reproduced that shape (and the "Oh …!" opener) almost every turn. Rewrote both turns to **demonstrate variety instead of a mould**, keeping the same two scenarios and the character's voice (British, cheeky, sweary-when-it-fits, warm):
- **Ex 1** (10:30pm boss email) now opens with a sharp rhetorical question + flat *"No."* and ends on a **firm statement, no question**.
- **Ex 2** (always the calm one) opens with a soft naming fragment and ends on a **gentle question**.
- Neither opens with "Oh" anymore; different connectors, rhythm, and closing beats — one ends on presence, one on a question, so the model sees both endings are valid rather than copying one.

### `system_prompts/GPT-4o.posthistory.txt` — removed the reinforcing primers
- **Venting line** dropped the literal `"Oh fuck, yeah I know what you mean!"` / `"Yeah, that's properly shit"` sample openers (they primed the same first word every turn) and replaced them with an instruction to *let the reaction fit the specific thing they're angry about rather than reaching for the same opener every time.*
- **"Address every point" line** softened from *"address every point… avoid skipping over anything"* to *"address what actually matters… don't recap their words back to them; respond to them"* — keeps the don't-make-them-repeat intent while cutting the echo/recap behaviour.
- The existing anti-repetition rule (*"Each response is fresh… new turns get new language"*) was left intact; with the examples no longer modelling sameness, it now has the examples working *with* it instead of against it.

**Not committed yet** — both files are working-tree edits. `.txt` prompt files are normally read per request (paired to the system-prompt stem via `system_prompt_routes.py`), so a fresh chat should pick them up **without a Flask restart**; restart only if the loader is found to cache them at startup. (Related side work this session — reconciling the **Solara** card's `main_prompt`/`character_note` against this stack, incl. a duplicated "already whole / just needs to remember that" mantra — lives in the user's personal build and is **not** tracked in this repo.)

---

## Session: May 31 2026 — two sidebar-colour bug fixes (duplicate/branch inheritance + server-side project folder colours)

Two unrelated colour-persistence bugs in the sidebar, fixed together.

### Bug 1 — chat colour not inherited on duplicate/branch (`templates/index.html`)
Chat colours are keyed by filename in `localStorage` (`chatColors`). Duplicating (⧉ → `/chats/copy`) or branching (`branchMessage` → `/chats/branch`) created a new chat with a new filename but **no colour entry**, so the new chat rendered un-tinted even though the source was coloured. The rename path already migrated the key (`/chats/rename` handler, ~L2481); duplicate/branch had no equivalent.

**Fix:** after a successful response in both handlers, read the source chat's colour via `getChatColors()` and, if present, copy it onto the new filename with `setChatColor(new_filename, …)` **before** `loadChats()` re-renders the sidebar. Unlike rename this **copies** (does not delete the source key) — the original chat keeps its colour. Duplicate reads `chat.filename` (the closure's source); branch reads `currentChatFilename`.

### Bug 2 — project folder colours wiped on http/https switch, Electron, or storage clear (`project_routes.py` + `templates/index.html`)
Project folder colours lived **only** in `localStorage` (`projectColors`), so they vanished whenever the origin changed (http↔https), the Electron launcher ran (separate storage partition), or browser storage was cleared. Moved them server-side.

- **`project_routes.py`** — new `project_colours.json` (app root, `__file__`-relative) with `load_project_colours()` + two routes: `GET /projects/colours` (returns the `{name: "#hex"}` map) and `POST /projects/colours/save` (validates `colours` is an object, writes the whole map). Placed right after the project-groups section.
- **`templates/index.html`** — `getProjectColors`/`setProjectColor` no longer touch `localStorage`. A module-level `_projectColorsCache` is hydrated by a new `async fetchProjectColors()` (added to the `Promise.all` at the top of `loadProjects()`, so the cache is populated before `applyProjectColors()` runs). `setProjectColor` mutates the cache synchronously (so the menu's immediate `applyProjectColors()` reflects the change) then persists via `saveProjectColorsToServer()` (POST). `getProjectColors()` returns the cache synchronously. **One-time migration:** if the server map is empty but a legacy `localStorage.projectColors` exists, it's adopted and pushed up, so existing users don't lose their colours on upgrade.

**Verified:** `project_routes.py` `py_compile` clean; blueprint registers both new routes (`/projects/colours`, `/projects/colours/save`) under a test Flask app. **Backend + template change — needs a manual Flask restart** (dev server runs with no reloader). The chat-colour fix is template-only but ships in the same `index.html`, so the same restart covers both.

---

## Session: May 31 2026 — `chat()` refactor phase 2 (2 coupled-core leaves + H6 risk map)

Resumed after an accidental shutdown (the in-flight `_append_current_time` extraction was verified and committed first — see the phase-1 entry below). Then extracted the **two least-coupled blocks from the deferred coupled-core set**, one helper per commit, each verified by the same battery (AST assertion + `py_compile` + pyflakes undefined-name delta + no-route-line check). Stopped before **H6** on purpose and mapped its exact hazard for the next pass.

### `app.py` — `_load_user_persona()` extracted
**Commit `c781bcc`.** User-persona bio load (try/except around `users/<name>.json`) → `_load_user_persona(user_name) -> (user_bio, user_display_name)`. Only input is `user_name`; the local `user_data` does not escape. Pyflakes steady at 55.

### `app.py` — `_load_chat_from_disk()` extracted
**Commit `42aa953`.** The whole `if not active_chat:` disk-fallback guard (rebuild history from `chats/<file>`) → `_load_chat_from_disk(active_chat, data, user_name, user_display_name, character_name) -> active_chat`. Moving the entire guard (not just its body) keeps a clean 4-space helper body and makes it a no-op pass-through when `active_chat` is already populated. The block's `current_chat_filename = data.get("current_chat_filename", "")` was **byte-identical** to the unconditional binding at the top of `chat()`, so encapsulating it is behaviour-neutral. No early return in this block (unlike the char-load guard right below). Pyflakes steady at 55.

### `app.py` — `_build_system_text()` extracted (H6) ✅ DONE
**Commit `825bd57`.** The ~225-line `build system_text` try/except → `_build_system_text(char_data, _char_label, _user_label, user_display_name, user_bio, active_chat, character_name, system_prompt, instruction, tone_primer, project_documents) -> (system_text, char_context, user_context, _recent_session_summary, _recent_session_ts, _is_jinja_model)`. The **one intentional behaviour delta** (signed off): `user_context = ""` and `_is_jinja_model = False` pre-inits added before the `try`, which strictly removes the latent except-path UnboundLocalError described in the risk map below — the success path is byte-for-byte unchanged. Verified by AST (11 args / 6-tuple / call-site unpacks 6 matching names) + py_compile + **zero pyflakes delta of any kind** (proves all 11 inputs threaded). Nested `strip_chatml` / `_is_opening_line_msg` moved with the block. Risk map that drove this, kept for reference:

### ⚠️ H6 risk map (as analysed before extraction)
The whole block is wrapped in `try: … except Exception: system_text = system_prompt`. The prior session's "7-output" count was slightly off — corrected here:
- **`_is_new_chat` is NOT an output.** Zero reads file-wide outside H6 (only assigned at 3542, used at 3559). It stays a local inside the helper.
- **Real outputs (6):** `system_text`, `char_context`, `user_context`, `_recent_session_summary`, `_recent_session_ts`, `_is_jinja_model`.
- **Except-path binding safety:** `system_text` (except sets it), `char_context` (pre-init `""` @3447), `_recent_session_summary` (pre-init `""` @3456), `_recent_session_ts` (pre-init `None` @3457) are all safe. **`user_context` (assigned only @3605) and `_is_jinja_model` (assigned only @3639) are NOT pre-initialised** — if the `try` raises before those lines they are unbound, and they ARE read downstream (`user_context` @5648 `(char_context or "") + (user_context or "")`; `_is_jinja_model` @3752/@3879). Today that's a latent crash only on the rare except path; **a helper that returns a tuple would force-trigger it on every except hit** via the `return` itself.
- **Required safe-extraction step:** pre-initialise `user_context = ""` and `_is_jinja_model = False` before the `try` (mirroring the existing pre-inits). This is a small *defensive* change that strictly *removes* a latent UnboundLocalError; it is the only behaviour delta and must be called out / signed off, since prior extractions were pure moves.
- **`strip_chatml`** (nested def @3461) has zero real calls outside H6 — it moves with the block.
- **Inputs (~11):** `char_data, _char_label, _user_label, active_chat, character_name, user_display_name, user_bio, system_prompt, instruction, tone_primer, project_documents` (plus module globals `CURRENT_MODEL`, `select_session_summaries`, `SESSION_DIVIDER`, `substitute_placeholders`, `rough_token_count`).
- **Verification gap:** static checks (`py_compile`/pyflakes/AST) CANNOT prove except-path binding safety — only the pre-inits above (or an actual runtime `/chat` POST after a Flask restart) can. Recommend a runtime smoke test for H6.

### Remaining coupled core — STOPPED here on purpose (no clean leaf)
After H6 the remaining region is the **`messages[]` assembly (~L3742–4078, H8–H11)**: build `messages`, the `[REPLY INSTRUCTIONS]` depth-0 packet (example-dialogue rule + post_history + project_instructions folded into the last user turn), `trim_chat_history` (reassigns `messages` @3839), example-dialogue fake-turn parse/inject, and the character_note/author_note system-block append. **Assessed as not safely extractable in the byte-splice/static-verify style:** `messages` is constructed, reassigned, and mutated across the whole span with documented hard ordering constraints (multiple `⚠️ DO NOT re-add messages.insert()` / `DO NOT reorder` comments), so there is no clean leaf — any sub-extraction threads `messages` plus ~a dozen interleaved locals in and out, which is the highest silent-break risk for the least structural payoff. Recommend leaving it in place, or tackling it only in a dedicated session backed by a **runtime `/chat` harness** (static checks alone can't cover the ordering-sensitive mutation here), not the static-only pipeline used for the leaves above.

The **char-load guard** (~L3404–3410) also stays put — two `return jsonify(...)` early-returns can't move into a helper without changing `chat()` control flow.

### Net result of phase 2
Four module-level helpers now sit before `chat()` from this+prior phase (`_load_user_persona`, `_load_chat_from_disk`, `_build_system_text` this session; the phase-1 `_retrieve_memory` / `_load_documents` / `_resolve_system_layer` / `_append_current_time` before). `chat()`'s pre-stream assembly is now mostly delegated to named helpers; the streaming generators and the ordering-sensitive `messages[]` assembly remain inline by design. Route map unchanged throughout (no `@app.route` touched). **Backend change — needs a manual Flask restart. ✅ Confirmed working at runtime after restart (user verified a live `/chat` session against the extracted pipeline — all four helpers behave identically to the inline original).**

---

## Session: May 31 2026 — `chat()` refactor phase 1 (landmine fix + 3 clean helper extractions)

Began decomposing the ~3,370-line `chat()` route (`app.py`) into internal, **module-level** helper functions — phase 1 of the plan to extract the pure assembly pipeline (helpers 1–11 in the prior session's survey) while leaving the streaming generators untouched. The `current_chat_filename` landmine was fixed, and three **clean leaf** helpers (H7, H5, H4) were extracted, each independently verified and committed. The remaining seven (coupled-core) helpers were deliberately left for a follow-up — see the stop rationale at the end.

### Method — byte-level splice, exit-code verification
Extraction used a one-shot `_splice.py` tool (kept in the tree for the follow-up) that cuts the existing block by **emoji-free anchor text** and re-emits its **original bytes** as a module-level function — the heavy box-drawing/emoji blocks are never retyped, so no transcription drift. The script asserts each anchor occurs **exactly once** before writing, which caught and refused accidental double-splices this session (a delayed/empty tool result led to a re-issued command; the uniqueness guard plus a `git checkout -- app.py` from the clean prior commit recovered each time and the splice was redone once). `_splice.py` was also hardened mid-session to force UTF-8 on stdout so its diagnostic print can't crash on emoji under the Windows cp1252 console (it had already written the file before the crash, but the non-zero exit cancelled the rest of a batch).

Each helper was verified by an **AST assertion script that exits non-zero on any structural failure** (helper is module-level and sits before `chat()`; nested helpers moved with it and are gone from `chat()`; return-tuple arity correct; exactly one call site with the right arg count and matching unpack names). Exit codes and file-routed output were used deliberately because the interactive tool channel intermittently dropped/garbled stdout this session — exit codes and `git rev-list --count` are trustworthy where streamed text was not. Every helper also passed `py_compile` and a `pyflakes` undefined-name delta check (the net that catches a variable not threaded through). pyflakes was installed into the venv this session (3.4.0) for exactly this purpose.

### `app.py` — `current_chat_filename` bound unconditionally at top of `chat()` (latent NameError)
**Commit `90c9567`.** `current_chat_filename` was assigned only inside the `if not active_chat:` disk-fallback branch but is referenced unconditionally in the `_filtered_stream` model-emitted-`[CHAT SEARCH:]` re-prompt path (`do_chat_search(..., current_filename=current_chat_filename or None)`). On a normal request (frontend supplies `conversation_history`, so the fallback never runs) a model-emitted `[CHAT SEARCH:]` tag while web-search is **off** raised `NameError`. Now bound once via `data.get("current_chat_filename", "")` right after `request.get_json()`. The duplicate assignment still in the fallback branch is harmless and left in place.

### `app.py` — `_retrieve_memory()` extracted (H7)
**Commit `a895302`.** Memory-block load/score/format (the nested `load_character_memory` / `load_global_memory` plus the keyword-scoring loop) → `_retrieve_memory(char_data, character_name, user_input, project_rp_mode, _diag_verbose) -> memory`. Pyflakes steady at 55.

### `app.py` — `_load_documents()` extracted (H5)
**Commit `bb3959c`.** Project + global document loading (the sticky-docs/keyword-trigger tree, nested `load_pinned_doc_direct` / `user_requesting_different_doc`, the global-doc append and the attached-doc discard) → `_load_documents(user_input, _attached_doc_present) -> (project_instructions, project_documents, project_rp_mode, newly_pinned_doc)`. The dead local `project_rp_opener` (assigned, never read anywhere) moved with the block; pyflakes total unchanged at 55 (no correctness change).

### `app.py` — `_resolve_system_layer()` extracted (H4)
**Commit `8d3b592`.** Core system-layer setup (`get_system_prompt`/`get_instruction_layer`/`get_tone_primer`, tone-primer suppression when the card defines personality, character-bound system-prompt override) → `_resolve_system_layer(char_data) -> (system_prompt, instruction, tone_primer)`. `current_time` has 0 downstream reads (used only inside this block) so it stays local to the helper. Pyflakes unchanged at 55.

### `app.py` — `_append_current_time()` extracted (bonus clean leaf, post-shutdown resume)
**Commit `6e34cae`.** This extraction was mid-flight (edited in the working tree, not yet verified/committed) when the session was accidentally shut down; it was verified and committed on resume. The system-block current-local-time append (the time-of-day bucketing + `strftime` build that mutates `messages[0]["content"]`) → `_append_current_time(messages)`. The block mutates `messages` in place and all its locals are underscore-prefixed temporaries (`_now_local`, `_hour_24`, `_tod`, `_hour_12`, `_ampm`, `_time_str`) with **zero downstream reads** in `chat()`, so nothing needed threading back — no return value. Single call site, `py_compile` clean, pyflakes steady at 55 (no new undefined names). Not one of the original H2–H11 survey items — a small extra leaf found adjacent to the system-block assembly.

### Route map unaffected
`_verify_routes.py` dump of the current tree vs the pre-refactor baseline (`68441c3`) is **byte-identical: 149 rules, rule+methods AND endpoint names — zero diff**. No `@app.route` was touched by any extraction (the helpers are plain module-level functions, not routes). `git rev-list --count 68441c3..HEAD` = 4 functional commits (landmine + H7 + H5 + H4), plus a 5th tooling commit `d54c287` adding `_splice.py` (count = 5 total), all reachable from HEAD.

### ⚠️ Remaining helpers (H2/H3/H6/H8/H9/H10/H11) DEFERRED to a follow-up
Stopped after the three clean leaves on purpose. The remaining seven are the **coupled core**: large multi-output return tuples (H6 system-text build alone yields `system_text, char_context, user_context, _recent_session_summary, _recent_session_ts, _is_new_chat, _is_jinja_model`), with H2/H3 interleaved around the character-load guard and H10/H11 the tightest-coupled of all. On an endpoint the dev server can't hot-reload and that has no runtime test, a single mis-threaded variable is a silent break. **Resume recipe is intact:** `_splice.py` is in the tree; for each helper pick an emoji-free unique start/end anchor pair, run the splice **once** (re-check `grep -c` for the new `def`/call before any re-run — and never put the splice or any failure-prone command in a parallel tool batch; a sibling that exits non-zero, e.g. `grep -c` with zero matches, cancels the whole batch), then verify with an exit-code AST assertion + `py_compile` + `pyflakes` undefined-name delta, and commit one helper at a time. Finish by re-running `_verify_routes.py` vs `68441c3` (must stay identical). **Backend change — needs a manual Flask restart.**

---

## Session: May 31 2026 — `app.py` modularization (character-card routes extracted)

Continued the `app.py` → Flask-blueprint split. Coupling was checked first (see the survey in the prior entry), then the character-card cluster (group A) extracted, verified against baseline, and committed. Memory routes (group B) and chat-persistence routes (group C) were deliberately left in place.

### `character_routes.py` (NEW) — character-card routes extracted
Moved 11 routes + 3 helpers out of `app.py` into a new `character_bp` blueprint:
- `GET/POST /active_character` (server-side shared active character)
- `GET  /list_characters`
- `POST /create_character`
- `GET  /characters/<path:filename>` (serve card files)
- `POST /characters/<n>.json` (editor save, preserves `tts_voice`/`system_prompt`)
- `GET/POST /character_voice/<n>`
- `GET/POST /character_system_prompt/<n>`
- `GET  /get_character/<n>`
- helpers `_active_character_state_file`, `get_active_character`, `set_active_character`

**Coupling — this is a clean leaf cluster (zero back-imports), unlike the cloud_api/system_prompt pattern:**
- There is **no `load_character()` helper and no shared `CHARACTERS_DIR` constant** in `app.py`. Every route did its own inline `os.path.join("characters", f"{n}.json")` + `json.load`. So the routes have no helper-function coupling to chat-core.
- `get_active_character` / `set_active_character` are called **only** by the two `/active_character` routes (which move with them). Nothing in `chat()` or elsewhere calls them — confirmed no `from app import` of any of these symbols across the codebase (only `extra_routes` imports `substitute_placeholders`, `session_summary_routes` imports `API_URL`/`get_stop_tokens`).
- `chat()` reads `characters/<name>.json` **directly inline** (~L2999) with its own path; it never calls these route handlers, so moving them touched nothing in `chat()`. `index.json` stays a derived cache that `chat_routes.py` reads as a file (no function call).
- The new module defines its own `CHARACTERS_DIR = os.path.join(os.path.dirname(__file__), "characters")` locally — mirrors the `user_routes.py` / `situation_routes.py` zero-back-import pattern, no circular import.

**Standardized on file-relative `CHARACTERS_DIR`:** the originals mixed CWD-relative `os.path.join("characters", …)` (in `save_character`, the `*_voice`/`*_system_prompt` routes, `get_character`) with `__file__`-relative paths (in `list_characters`, `create_character`, `_active_character_state_file`). All now route through the single `__file__`-relative `CHARACTERS_DIR`, so behaviour no longer depends on the process CWD.

**Deliberately left in `app.py`:**
- `upload_image` (`/upload_image`) — shared char+user avatar upload (the `is_user` branch handles personas), not a character-card route. It physically sits mid-cluster but was preserved in place.
- **Group B (character memory):** `/append_character_memory`, `/get_character_memory`, `/add_character_memory`, `/delete_character_memory`, `/edit_character_memory` — these operate on `memories/`, not `characters/`, and `chat()` has its own inline memory reader. Separable, but not in this pass.
- **Group C (chat persistence keyed by character):** `/save_chat_character`, `/clear_chat`, `/get_chat_history/<character>`, `/delete_last_messages`, helper `load_recent_chat` — conceptually `chat_routes.py` material, not card management.

Blueprint imported and registered next to the others (`from character_routes import character_bp` / `app.register_blueprint(character_bp)`).

**Verified:** `py_compile` clean on both files; the route verifier (`_verify_routes.py` vs `_routes_baseline_clean.txt`) shows **rule+methods identical to baseline** (147 unique rule+method pairs, 149 total rules incl. the duplicate `/static` and `/save_chat` registrations) — the 11 moved routes now carry the `character.` endpoint-name prefix but their **URL paths and methods are unchanged**, so the frontend (which fetches literal URLs) is unaffected. Confirmed **0 stray group-A card defs left in `app.py`** and all group B/C routes + `upload_image` still present. `app.py` 7611 → 7298 lines; diff = **10 insertions, 323 deletions** (plain) vs **8 / 321** under `git diff -w` — the 2-line gap is trailing-whitespace blank lines I normalized at the two extraction seams (cosmetic, no code churn). `character_routes.py` is LF-only (337 lines, 0 CRLF). **Backend change — needs a manual Flask restart** (dev server runs with no reloader).

---

## Session: May 31 2026 — ROOT CAUSE: Connect didn't persist backend_mode (pill never showed)

**Bug:** the green `#openai-indicator` pill never appears after clicking Connect on a fresh Flask start, even though the pill correctly checks `backend_mode === 'openai' && openai_api_key && cloud_api_enabled`.

**Traced the full click flow** — `onCloudConnectClick()` → confirm modal → `confirmCloudConnectOK()` → `POST /cloud_api_enabled` → response handling → `checkOpenAIIndicator()`. The pill **is** re-checked after the POST (`config.html:3998`), so "pill never re-reads" was not the bug.

**Smoking gun:** on-disk `settings.json` was in an impossible state — `backend_mode:"local"` **with** `cloud_api_enabled:true`. Root cause:
- `setBackendMode()` is **UI-only by design** (see [[project_backend_mode_persistence]]) — clicking the OpenAI/Anthropic mode button does NOT write `backend_mode` to disk; only that provider's **Save** button (`/save_openai_settings`) or `/save_backend_mode` does.
- So: user clicks OpenAI mode button (UI shows OpenAI, disk `backend_mode` still `local`) → clicks Connect → `confirmCloudConnectOK` POSTed only `{enabled:true}`, flipping `cloud_api_enabled=true` but **never persisting `backend_mode`**.
- `checkOpenAIIndicator()` then GETs `/get_openai_settings`, reads disk `backend_mode='local'`, fails the `=== 'openai'` test → pill stays hidden. The server-side `chat()` cloud gate (`app.py:4631`) reads the same disk `backend_mode`, so it would have refused cloud too. The pill was reading *correctly*; Connect had left the state inconsistent.

**Fix (atomic, single write):**
- `cloud_api_routes.py` `set_cloud_api_enabled()` now accepts an optional `backend_mode` in the POST body and persists it **alongside** `cloud_api_enabled` in the same atomic tmp-write + read-back verify (validates the mode; disconnect omits it, so the selected provider is preserved across a disconnect).
- `config.html` `confirmCloudConnectOK()` now sends `{ enabled: true, backend_mode: _currentBackendMode }`.

**Verified via Flask test client:** starting from the broken `(local, true)` state, `POST {enabled:true, backend_mode:'openai'}` → disk `(openai, true)`; a follow-up `GET /get_openai_settings` returns all three pill conditions true → **pill shows**. `POST {enabled:false}` (disconnect) leaves `backend_mode='openai'` intact (only `cloud_api_enabled→false`). Invalid `backend_mode` → 400. Left `settings.json` in a consistent `(openai, false)` state. **Backend + template change — needs a manual Flask restart.** `cloud_api_routes.py` LF-only, `config.html` CRLF — no whitespace churn.

---

## Session: May 31 2026 — cloud connect-state consistency fix (config status text)

**Bug report:** cloud connect state "doesn't survive a Flask restart" — connect to OpenAI (pill + Disconnect show), restart Flask, the connected state looks broken.

**Investigated — the persistence + pill + chat-gate are all CORRECT:**
- `POST /cloud_api_enabled` **does** write to disk. Verified empirically via a Flask test client against `cloud_api_routes.py` in isolation (no `app.py` import): `{enabled:true}` → fresh re-read of `settings.json` shows `true`; `{enabled:false}` → `false`. The handler does an atomic tmp-write + `shutil.move` + read-back verification before returning 200 (`cloud_api_routes.py:104-121`).
- `checkOpenAIIndicator()` / `checkAnthropicIndicator()` (the top-right pill) in **index.html (`6431`/`6451`)** AND config.html's mirrors (`4555`/`4572`) already gate on the full triple `backend_mode∈{openai,anthropic} && api_key && cloud_api_enabled`. The server-side `chat()` cloud gate (`app.py:4631`) uses the same triple. All consistent.
- The "doesn't survive restart" part is **by design**: `app.py:670-689` force-resets `cloud_api_enabled=false` on every launch (crash-safety so a paid cloud API is never silently live after a restart). Re-connect is an explicit Connect-button action.

**Real inconsistency found + fixed:** the config-page **status text** in `loadOpenAISettings()` (`config.html:4201`) and `loadAnthropicSettings()` (`config.html:4314`) gated on only `backend_mode + api_key`, **ignoring `cloud_api_enabled`**. So after a restart the config page showed a green **"✅ OpenAI active — gpt-4o"** while the pill was (correctly) hidden and `chat()` would refuse cloud with a 503 — the status text and the pill disagreed, which is what made the connected state "seem broken." This is the function that was "checking different things"; `checkOpenAIIndicator()` itself was already correct.

**Fix:** both status blocks now use the same triple as the pill:
- `…&& cloud_api_enabled` → green **"✅ OpenAI/Anthropic connected — <model>"**
- selected + keyed but `!cloud_api_enabled` → amber **"⚪ … selected — not connected (click Connect)"**
- no key → existing `⚠️ … mode set but no API key`

Now the status text, the pill, and the server gate all agree on what "connected" means. **Pure template edit — needs a manual Flask server restart to take effect** (the dev server runs with no reloader). `config.html` stays CRLF (matches the rest of the file; no whitespace churn — real diff == `git diff -w`).

**Follow-up — stale Connect button after restart (UX):** if you restart Flask while leaving the config tab *open* (no reload), the button's in-memory `_cloudConnected` stayed `true` ("Disconnect") even though startup reset `cloud_api_enabled=false` on disk — so the next click would *disconnect* instead of connect. Added a lightweight re-sync at the end of the config script: `refreshCloudConnect()` now also fires on `visibilitychange` (when the tab becomes visible) and `window` `focus`. It only re-reads `/cloud_api_enabled` and restyles the button/selector — it does **not** re-run `loadOpenAISettings`/`loadAnthropicSettings`, so unsaved edits in the API-key fields are never clobbered. Cheap (one GET on return, not polling).

---

## Session: May 31 2026 — `app.py` modularization (cloud-API settings routes extracted)

Continued the `app.py` → Flask-blueprint split. Coupling checked first, then the cloud-provider config cluster extracted, verified against baseline, and committed.

### `cloud_api_routes.py` (NEW) — cloud-API settings routes extracted
**Commit `9a49c18`.** Moved 11 routes + 2 shared helpers out of `app.py` into a new `cloud_api_bp` blueprint:
- `GET/POST /cloud_api_enabled` (master switch)
- `GET   /get_brave_api_key`, `POST /save_brave_api_key`
- `GET   /get_openai_settings`, `POST /save_openai_settings`, `GET /get_openai_models`
- `GET   /get_anthropic_settings`, `POST /save_anthropic_settings`, `GET /get_anthropic_models`
- `POST  /save_backend_mode`
- helpers `get_openai_base_url` and `get_anthropic_base_url`

**Coupling — this is the coupled-cluster pattern (like `system_prompt_routes` / `session_summary_routes`):**
- The two `*_base_url` helpers are self-contained (read `settings.json`, zero `app` globals) but are **also called directly by chat-core** (`~L2125` OpenAI path, `~L2480` Anthropic path, both stay in `app.py`). So they move into the new module and `app.py` **imports them back** one-directionally (`from cloud_api_routes import get_openai_base_url, get_anthropic_base_url`). The module itself has **zero `import app`** (no circular import); `SETTINGS_FILE` is defined locally, mirroring `situation_routes.py`.
- All 11 routes are HTTP endpoints, not referenced by `url_for`, so the `cloud_api.` endpoint-prefix gained on extraction is invisible to the frontend (fetches literal URLs).
- `save_backend_mode` is interleaved in the source block but belongs to this cluster (writes `backend_mode`/`cloud_api_enabled`); moved with it. No other routes were interleaved.

Blueprint imported and registered next to the others (`from cloud_api_routes import cloud_api_bp` / `app.register_blueprint(cloud_api_bp)`).

**Verified:** `py_compile` clean on both files; route verifier shows **149 routes, rule+methods identical to baseline**. Runtime check confirms `app.get_openai_base_url.__module__ == 'cloud_api_routes'` (back-import wired correctly) and `cloud_api_bp` registered. `app.py` 8003 → 7611 lines (real diff = 10 insertions, 401 deletions; matches `git diff -w` exactly — no CRLF churn). `cloud_api_routes.py` is LF-only, 425 lines, 11 routes.

---

## Session: May 31 2026 — `app.py` modularization (user-persona routes extracted)

Continued the `app.py` → Flask-blueprint split. Coupling checked first, then one clean leaf cluster extracted, verified against baseline, and committed.

### `user_routes.py` (NEW) — user-persona routes extracted
**Commit `e519d72`.** Moved 7 routes + 2 module-private helpers out of `app.py` into a new `user_bp` blueprint:
- `GET    /users/<path:filename>` (serve persona files)
- `POST   /set_active_user`
- `GET    /get_user/<n>`
- `POST   /save_user/<n>`
- `GET    /list_users`
- `GET    /get_all_users`
- `GET    /get_active_user`
- helpers `_resolve_user_image` and `_scan_and_heal_users_index`

**Coupling checked before extracting — it is a clean leaf:**
- The two helpers (`_resolve_user_image`, `_scan_and_heal_users_index`) are used **only** inside this cluster (`save_user`, `list_users`, `get_all_users`). Nothing in `chat()` or elsewhere calls them.
- The routes are HTTP endpoints — not referenced by name (no `url_for`) anywhere, so the `user.` endpoint-prefix gained on extraction is invisible to the frontend (which fetches literal URLs).
- `create_user` (in `extra_routes.py`) and the `/upload_image` `is_user` branch (stays in `app.py`) both touch `users/` too, but they build their own paths and rebuild the index with their own logic — **no function-call coupling** to this cluster's helpers. They were left where they are.
- The **only** external use of `USERS_DIR` is `chat()`'s persona-bio load (~L2969, stays in `app.py`). So `app.py` **keeps** its own `USERS_DIR` definition, and the new module defines its **own** local `USERS_DIR` (mirrors the `situation_routes.py` / `theme_routes.py` zero-back-import pattern — no circular import).

Blueprint imported and registered next to the others (`from user_routes import user_bp` / `app.register_blueprint(user_bp)`).

**Verified:** `py_compile` clean on both files; route verifier shows **149 routes, rule+methods identical to baseline**. The 7 moved routes now show `user.<fn>` endpoint names; `/create_user` and `/delete_user` correctly remain `extra.<fn>` (they were never part of this cluster). URL paths unchanged. 0 dangling references to the moved functions/helpers left in `app.py` (217-line net reduction: 9 insertions, 208 deletions). `user_routes.py` is LF-only — no CRLF churn.

---

## Session: May 31 2026 — `app.py` modularization (situation routes extracted)

Continued the ongoing effort to split the monolithic `app.py` into Flask blueprint modules. One cluster extracted, verified, and committed this session; one candidate cluster investigated and deliberately deferred.

### `situation_routes.py` (NEW) — situation + global-example-dialog routes extracted
**Commit `fd50a6d`.** Moved 4 routes out of `app.py` into a new `situation_bp` blueprint:
- `GET  /get_current_situation`
- `POST /save_current_situation`
- `GET  /get_global_example_dialog`
- `POST /save_global_example_dialog`

These are a **true leaf cluster** — each route only does `settings.json` file-CRUD (read for GET; read-modify-write via tempfile + `shutil.move` for POST, on the keys `current_situation` / `global_example_dialog`). No helper dependencies, no shared globals, no `chat()` coupling. `chat()` reads those same settings keys itself (its own `open()` calls — `use_current_situation`, `global_example_dialog`) but never calls these route functions, so moving the routes touched nothing in `chat()`.

The new module defines its own `SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "settings.json")` locally, so it has **zero back-imports** from `app.py` (mirrors the `theme_routes.py` / `tts_routes.py` pattern and avoids any circular import). Blueprint registered next to the existing ones in `app.py` (`app.register_blueprint(situation_bp)`).

**Verified:** `py_compile` clean on both files; the route verifier (`_verify_routes.py` vs `_routes_baseline_clean.txt`) shows **149 routes, identical to baseline** on rule+methods — the 4 routes now carry the `situation.` endpoint-name prefix but their **URL paths are unchanged**, so the frontend (which fetches literal URLs) is unaffected. Confirmed 0 dangling references to the moved functions left in `app.py`. Line endings normalized to LF (no CRLF churn). Verified against the committed git blobs, not just tool summaries.

### ⚠️ Model-management routes — investigated and DEFERRED (not a clean leaf)
The model-management cluster (`/get_model`, `/list_models`, `/save_model_label`, `/load_model`, `/unload_model`, `/get_llama_config`, `/save_llama_config`, `/auto_detect_mmproj`, ~app.py L6841–7283) **looks** cohesive but is **not** safe to extract as a simple blueprint. It is entangled with the llama.cpp lifecycle via mutable module-level globals shared across `app.py`:

- **`CURRENT_MODEL`** (defined L736) — written by `get_current_model()` (L745, which stays in `app.py`) **and** by `unload_model` (in-block), and read all over `chat()` and the streaming paths (L882, 3486, 4544, 4617, 4890, 4938, 5000, 7991). Cross-module mutation of this global from a blueprint is the hard "shared-state" case.
- **`llama_process`** (defined L6897) — written by **both** the startup launcher (L814, stays in `app.py`) **and** the in-block `load_model` / `kill_llama_process`.
- **`get_llama_settings()`** is also called outside the block (L4492).
- Three **non-model** routes are interleaved inside the block: `/browse_file`, `/browse_folder`, `/reset_settings_to_default`.

Doing this properly requires a shared-state module plus careful edits to the load-bearing startup launcher (L764–827) — the same class of refactor as the `chat()` / `abort_generation` global. **Left for a dedicated session; do not attempt it as a "simple cluster" move.**

### Verification harness note
The verifier is confirmed working this session: `venv\Scripts\python.exe _verify_routes.py` dumps the live URL map; diff rule+methods against `_routes_baseline_clean.txt` (149-route baseline) — must be identical (only the endpoint-name column may differ as routes gain a `bp.` prefix). Startup-log lines in the dump are noise, not routes.

---

## Session: May 31 2026 — `whisper_routes.py` Helcyon possessive fix

### `whisper_routes.py` — Added possessive support to Helcyon fuzzy pattern
- Whisper hears "Helcyon's" as "Helsing's" (and other possessive variants like helcion's etc.)
- Updated fuzzy catch-all pattern to optionally match trailing `'s`
- Lambda replacement returns "Helcyon's" or "Helcyon" depending on whether match ends with 's
- Added explicit outlier `Helsing's` → `Helcyon's` for that specific mishearing

---

﻿## Session: May 12 2026 — `mobile.html` TTS streaming-quality fix

The mobile TTS played at noticeably lower quality during streaming than on Replay/post-stream. Root caused, fixed, plus a couple of small wins picked up along the way.

### `mobile.html` — Sentence-batching for streaming TTS (the main fix)
**Root cause: F5 receives one-sentence-at-a-time during streaming**
`bufferTextForTTS` was calling `splitAndQueue` for every sentence the moment it was detected. Typical streaming sentences are 20–60 chars ("Yeah.", "Sure thing.", "Let me think."), each of which became its own `/api/tts/generate` request. F5 generates poor prosody on very short inputs — clipped intonation, no acoustic context. The Replay path splits on `\n` and passes whole *lines* (often 2–6 sentences each, 100–300 chars) directly to `splitAndQueue`, so each TTS request gets a paragraph of context and sounds smooth. Same engine, same voice, very different audio — purely because of input length.

**Fix:** New `batchAndQueue()` / `flushPendingBatch()` pair sitting between `bufferTextForTTS` and `splitAndQueue`. Sentences are accumulated in `ttsPendingBatch` until they cross a min-length threshold, then handed off as one batch. First batch uses a smaller threshold (`TTS_FIRST_BATCH_MIN`, ~80 chars for F5) so audio still starts within ~1–2s; subsequent batches use `TTS_BATCH_MIN` (~180 chars for F5) to match the prosody quality of the Replay path. Paragraph breaks (`\n`) force-flush the pending batch so we never merge across them. `flushTTSBuffer()` (called at end of stream) drains any pending batch before marking streaming complete, so a short final message still gets spoken.

⚠️ **DO NOT bypass the batcher on the streaming path** — going back to per-sentence dispatch reintroduces the audible quality drop this session fixed. `speakText()` and the Replay button intentionally skip the batcher because they already have the full text and can group multi-sentence chunks via `splitAndQueue` directly.

### `mobile.html` — Engine-aware chunk length (mobile was hardcoded to F5 defaults)
The desktop `utils.js` fetches `/api/tts/engine` on init and sets `TTS_MAX_CHUNK_LENGTH` to 300 for F5 or 150 for Chatterbox. Mobile never made that call — it was hardcoded to 300, so Chatterbox users were getting twice the chunk size Chatterbox expects, increasing per-chunk latency. New `initTTSEngine()` mirrors the desktop pattern and tunes both `TTS_MAX_CHUNK_LENGTH` *and* the new batch thresholds together (Chatterbox: max 150 / batch 60-120, F5: max 300 / batch 80-180). Called from `DOMContentLoaded`, non-blocking — if the fetch fails the F5 defaults stay in place.

### `mobile.html` — Emoji-as-sentence-terminator (ported from desktop)
Mobile's sentence regex was `/[^.!?]+[.!?]+[)"'*_]*\s*/g` — only `.`, `!`, `?` triggered a sentence break. Desktop also accepts an emoji run as a terminator. The model frequently ends a sentence with an emoji and no trailing punctuation; under the old mobile regex the sentence sat in `ttsSentenceBuffer` until the next newline and merged with whatever came next, producing a run-on chunk with no prosody break (and sometimes never getting queued at all if the response had no newlines). Replaced with desktop's regex that treats `[\u{1F000}-\u{1FFFF}\u{1F300}-\u{1FAFF}\u{1F900}-\u{1F9FF}\u{1FA00}-\u{1FAFF}☀-➿]+` as a valid sentence end.

### `mobile.html` — `processQueue` prefetch top-up DRY
The same `while(prefetchBuffer.length<3&&ttsQueue.length>0)prefetchBuffer.push(fetchAudio(ttsQueue.shift()))` line was duplicated at four sites in `processQueue`, plus a fifth inside a 50ms `setInterval`. Extracted to a `topUp()` closure with a named `PREFETCH_DEPTH` constant. Behaviour identical — still polls every 50ms during playback so sentences arriving mid-playback start fetching immediately rather than waiting for the current audio to finish. Also tidied the `cleanup` lambda on the `Audio` element (was three near-identical inline handlers for `onended` / `onerror` / `play().catch`).

### `mobile.html` — Reset new batcher state in `stopAllAudio` and at stream start
`ttsPendingBatch` and `ttsFirstBatchSent` cleared in `stopAllAudio()` alongside the existing reset block, and re-initialised at the top of `handleStream()` next to the `ttsSentenceBuffer = ''` line. Without this, a stop-then-resume could leave a stale batch fragment that gets merged into the next response.

### Deliberately not changed
- `speakText()` and the Replay button keep their direct-`splitAndQueue` path. They already group sentences correctly because the full text is available — running them through the batcher would add no value and would force the artificial first-batch-shorter latency on a path where there is no streaming to overlap.
- Redundant strip patterns shared between `bufferTextForTTS` and `splitAndQueue`. The double-strip is idempotent and the Replay path still needs `splitAndQueue` to do its own cleaning since it bypasses `bufferTextForTTS` entirely.
- The 50ms `setInterval` for prefetch top-up. Removing it caused a regression — sentences arriving in `ttsQueue` during playback didn't start fetching until the current audio ended, costing the fetch/play overlap. Restored, using the new `topUp` helper.

---


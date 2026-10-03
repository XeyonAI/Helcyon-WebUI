/*
 * HWUI shared chat core — the ONE implementation of the chat request/history
 * rules used by both the desktop page (templates/index.html) and the mobile
 * page (templates/mobile.html).
 *
 * Why this file exists: the two pages used to carry independent copies of
 * this logic, and fixes landed in one and not the other (mobile kept a
 * global dedup that desktop had removed, dropped check-ins, lost per-message
 * metadata, stored uncleaned replies, and continued via a fake user turn).
 * Identical chat state must produce identical model input from either page,
 * so everything that shapes what /chat sends or what /chats/save persists
 * lives here. Desktop behaviour is the canonical reference: every function is
 * the desktop implementation moved here verbatim, not a re-implementation.
 *
 * Loaded as a plain <script> (no bundler): exposes window.HwuiChatCore and the
 * legacy global stripChatMLOutsideCodeBlocks(). Under Node (tests) it also
 * exports the same API via module.exports.
 */
(function (root) {
  'use strict';

  // ── Message identity ────────────────────────────────────────────────────
  function createMessageId() {
    const cryptoApi = root.crypto;
    return cryptoApi && cryptoApi.randomUUID
      ? cryptoApi.randomUUID()
      : `message-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  // ── Optional assistant reaction protocol ────────────────────────────────
  // The marker is deliberately an HTML comment so an unhandled provider path
  // is still unlikely to display it, but every normal render/save path strips
  // it explicitly. Reactions are metadata, never conversation content.
  // This remains the curated list rendered by the manual picker. Assistant
  // markers are validated separately below so model-generated reactions are
  // not constrained by the picker choices.
  const REACTION_EMOJIS = Object.freeze(['😂', '❤️', '👍', '👎', '😮', '😢']);
  const REACTION_MARKER_RE = /<!--[ \t]*HWUI_REACTION[ \t]*:[ \t]*([\s\S]*?)[ \t]*-->/giu;
  const REACTION_ANY_MARKER_RE = /<!--[ \t]*HWUI_REACTION[ \t]*:[\s\S]*?-->/giu;
  const REACTION_MARKER_OPEN_RE = /<!--[ \t]*HWUI_REACTION[ \t]*:/giu;
  const REACTION_EMOJI_GRAPHEME_RE = /^(?:(?:\p{Regional_Indicator}{2})|(?:[0-9#*]\uFE0F?\u20E3)|(?:\p{Extended_Pictographic}(?:\uFE0F|\p{Emoji_Modifier})?(?:[\u200D\p{Extended_Pictographic}(?:\uFE0F|\p{Emoji_Modifier})?])*))$/u;

  function trailingReactionPrefixLength(text) {
    const lower = String(text || '').toLowerCase();
    const start = lower.lastIndexOf('<');
    if (start < 0) return 0;
    const tail = lower.slice(start);
    if ('<!--'.startsWith(tail)) return tail.length;
    if (!tail.startsWith('<!--')) return 0;
    // Match the same optional spaces/tabs as the complete-marker grammar.
    // Otherwise valid alternate spellings leak until their colon arrives.
    const name = tail.slice(4).replace(/^[ \t]*/, '');
    if ('hwui_reaction'.startsWith(name) || /^hwui_reaction[ \t]*$/.test(name)) return tail.length;
    return 0;
  }

  function normalizeReaction(value) {
    const reaction = typeof value === 'string' ? value.trim() : '';
    if (!reaction || !REACTION_EMOJI_GRAPHEME_RE.test(reaction)) return null;
    const segmenter = root.Intl && typeof root.Intl.Segmenter === 'function'
      ? new root.Intl.Segmenter(undefined, { granularity: 'grapheme' })
      : null;
    if (segmenter && Array.from(segmenter.segment(reaction)).length !== 1) return null;
    return reaction;
  }

  // Remove a complete marker and hold/drop an incomplete marker from the tail
  // of an accumulated stream. The caller passes the complete accumulated
  // response on every update, so a marker split across transport chunks can
  // never flash in the visible bubble or enter TTS.
  function parseAssistantReaction(text, options) {
    const sourceText = String(text || '');
    let source = sourceText;
    let reaction = null;
    let reactionDecided = false;
    // A valid leading marker owns its immediately following line ending. Keep
    // that syntax out of the visible answer, but do not use trim() here: every
    // character after the marker belongs to the assistant's reply verbatim.
    const leadingMarker = /^[ \t]*<!--[ \t]*HWUI_REACTION[ \t]*:[ \t]*[\s\S]*?[ \t]*-->/iu.exec(sourceText);
    source = source.replace(REACTION_MARKER_RE, function(match, emoji) {
      if (!reactionDecided) {
        reaction = normalizeReaction(emoji);
        // The independent preflight can explicitly abstain. Later optional
        // markers in the main reply must not override that decision.
        reactionDecided = !!reaction || emoji.trim().toLowerCase() === 'none';
      }
      return '';
    });
    // A syntactically complete but invalid marker is still control syntax and
    // must not leak. It simply carries no reaction.
    source = source.replace(REACTION_ANY_MARKER_RE, '');

    // If the model began a marker but has not emitted its closing `-->`, keep
    // the whole open marker out of the stream. At EOS the same rule drops the
    // malformed control tail instead of exposing it as assistant prose.
    let openIndex = -1;
    let openMatch;
    while ((openMatch = REACTION_MARKER_OPEN_RE.exec(source)) !== null) {
      openIndex = openMatch.index;
    }
    REACTION_MARKER_OPEN_RE.lastIndex = 0;
    if (openIndex >= 0 && source.indexOf('-->', openIndex) === -1) {
      source = source.slice(0, openIndex);
    } else if (openIndex === -1) {
      const partialLength = trailingReactionPrefixLength(source);
      if (partialLength) source = source.slice(0, -partialLength);
    }
    if (leadingMarker) source = source.replace(/^\s+/, '');
    return reactionDecided && !reaction
      ? { text: source, reaction, decided: true }
      : { text: source, reaction };
  }

  // ── Internal memory-scaffolding guard ───────────────────────────────────
  // HWUI renders retrieved memory into <MEMORY_ENTRY>/<STORED_FACTS> blocks,
  // and on the native Ministral path carries current-user facts as an
  // assistant-role message whose entire body is <STORED_FACTS>…</STORED_FACTS>.
  // The Tekken template has no assistant role marker in that position, so it
  // lands as bare text and a model can imitate the grammar — emitting its own
  // block, with invented contents, in its reply. Anything emitted is stored
  // raw and re-enters conversation_history on every later turn as ordinary
  // assistant speech, which is how an invented "fact" becomes established
  // context (observed 2026-09-18).
  //
  // This is defence in depth only: it does NOT change what HWUI sends to the
  // model, and deliberately does not touch _split_ministral_native_speaker_memory
  // or the provider-facing memory representation.
  //
  // The WHOLE block goes, contents included. The body is exactly what must not
  // survive — hiding the wrapper while keeping a hallucinated fact would be
  // worse than leaving both.
  // ── Reserved scaffolding vocabulary ─────────────────────────────────────
  // Every wrapper HWUI injects into the Ministral provider prompt. The model
  // sees all of these as bare text, so any of them can be imitated back; on
  // 2026-09-19 a reply opened with <STYLE_EXAMPLE>/<EXAMPLE_INPUT>/<STYLE_NOTE>
  // and degenerated into *STORED_CONTEXT> … </STORED_FACTV> … </STORED_FA>.
  // Only STORED_FACTS had an output guard, and only in pristine form, so the
  // rest escaped into the visible reply.
  //
  // Two matching tiers, and what separates them is the UNDERSCORE, not the
  // case:
  //
  //   STEMS       Underscored fragments of HWUI's own grammar. They absorb
  //               malformation — a truncated STORED_FA, a corrupted
  //               STORED_FACTV, an invented STORED_CONTEXT and the mixed-case
  //               STORED_FActor all begin with a reserved stem.
  //
  //   BARE WORDS  Reserved names that are also ordinary English. No underscore
  //               distinguishes them, so they must stay exact and ALL-CAPS.
  //
  // Stems carrying an underscore are HWUI's own grammar, and are matched
  // case-INSENSITIVELY. <STORED_FActor>, <Stored_Facts> and <stored_fact> are
  // the same wrapper wearing different clothes; the live 2026-09-19 leak wore
  // three of them at once. Ordinary markup does not contain an underscored
  // reserved stem, so case-insensitivity here costs no false positives.
  const SCAFFOLD_STEMS = Object.freeze([
    'STORED_', 'STORE_', 'STYLE_', 'EXAMPLE_', 'MEMORY_', 'OWNER_',
    'SESSION_CONTEXT', 'TURN_REFERENCE', 'PROJECT_REFERENCE', 'USER_PROFILE',
    'CHARACTER_BACKGROUND', 'CURRENT_CONVERSATION', 'STYLE_EXAMPLES',
    'STYLE_DEMONSTRATION', 'EXAMPLE_INPUT', 'EXAMPLE_CHARACTER_REPLY',
    'MEMORY_ENTRY', 'OWNER_BINDING', 'STORED_FACTS',
  ]);
  // Reserved names that are ordinary English words carry no underscore to
  // distinguish them, so they stay ALL-CAPS and exact: <reference> and
  // <Reference id="1"/> are legitimate markup; <REFERENCE> is ours.
  const SCAFFOLD_BARE_WORDS = Object.freeze(['REFERENCE']);
  const SCAFFOLD_NAME = '[A-Za-z][A-Za-z0-9_]*';
  // A tag-ish token: "<" (or the "*" the live leak produced instead), an
  // optional "/", the name, and an OPTIONAL ATTRIBUTE REGION. Requiring ">" to
  // follow the name immediately is why <STORED_FActor name="voice"> sailed
  // straight through on 2026-09-19. Attributes may not contain angle brackets,
  // so this cannot run away across a whole reply. Whether the token is HWUI's
  // is still decided by isReservedScaffoldName, never by this pattern alone.
  const SCAFFOLD_TOKEN_RE = new RegExp(
    '[<*]\\s*(/?)\\s*(' + SCAFFOLD_NAME + ')(?:\\s[^<>]*)?\\s*/?\\s*>', 'g'
  );

  function logScaffoldGuard(count) {
    // Logged so we can see how often models actually emit this, rather than
    // guessing. Counts only — the removed text is not echoed anywhere.
    try {
      const console_ = root.console;
      const write = console_ && (console_.info || console_.log);
      if (write) {
        write.call(console_,
          '🧹 HWUI memory-scaffolding guard: removed ' + count +
          ' reserved scaffolding token(s)/block(s) from assistant output');
      }
    } catch (error) { /* logging must never break a reply */ }
  }

  function isReservedScaffoldName(rawName) {
    const name = String(rawName || '').replace(/[^A-Za-z0-9_]/g, '');
    if (!name) return false;
    // Bare English words: exact, case-sensitive, so <reference> survives.
    if (SCAFFOLD_BARE_WORDS.indexOf(name) !== -1) return true;
    const upper = name.toUpperCase();
    return SCAFFOLD_STEMS.some(function(stem) {
      // Reserved stem at the start absorbs invention, extension and the
      // mixed-case mutations seen live (STORED_FActor, STORED_FAct).
      if (upper.indexOf(stem) === 0) return true;
      // Truncation: a recognisable prefix of a reserved name. It must still
      // carry an underscore, or "<example>" would match "EXAMPLE_" and every
      // ordinary <example> element would vanish.
      return upper.length >= 6 && stem.indexOf(upper) === 0 && upper.indexOf('_') !== -1;
    });
  }

  function isReservedScaffoldToken(token) {
    const match = /^[<*]\s*\/?\s*([A-Za-z0-9_]+)(?:\s[^<>]*)?\s*\/?\s*>$/
      .exec(String(token || ''));
    return Boolean(match) && isReservedScaffoldName(match[1]);
  }

  // Index of a reserved OPENING tag, or -1. `head` restricts it to the start
  // of the text (allowing leading whitespace).
  function reservedTagAt(value, head) {
    const re = new RegExp(SCAFFOLD_TOKEN_RE.source, 'gi');
    let match;
    while ((match = re.exec(value)) !== null) {
      if (match[1]) continue;                       // a closer, not an opener
      if (!isReservedScaffoldToken(match[0])) continue;
      if (!head) return match.index;
      return /^\s*$/.test(value.slice(0, match.index)) ? match.index : -1;
    }
    return -1;
  }

  function unclosedReservedTagAt(value) {
    let offset = 0;
    while (offset < value.length) {
      const tail = value.slice(offset);
      const open = reservedTagAt(tail, false);
      if (open < 0) return -1;
      const absoluteOpen = offset + open;
      const closeRe = new RegExp(SCAFFOLD_TOKEN_RE.source, 'gi');
      closeRe.lastIndex = absoluteOpen + 1;
      let close = null;
      let match;
      while ((match = closeRe.exec(value)) !== null) {
        if (match[1] && isReservedScaffoldToken(match[0])) {
          close = match;
          break;
        }
      }
      if (!close) return absoluteOpen;
      offset = close.index + close[0].length;
    }
    return -1;
  }

  // Remove every reserved opener…closer span, contents included. Scans left to
  // right so nested or repeated blocks all go, and never spans past a closer.
  function removeReservedBlocks(value, onRemoved) {
    let out = value;
    for (let guard = 0; guard < 50; guard += 1) {
      const open = reservedTagAt(out, false);
      if (open < 0) break;
      const closeRe = new RegExp(SCAFFOLD_TOKEN_RE.source, 'gi');
      closeRe.lastIndex = open + 1;
      let close = -1;
      let match;
      while ((match = closeRe.exec(out)) !== null) {
        if (match[1] && isReservedScaffoldToken(match[0])) {
          close = match.index + match[0].length;
          break;
        }
      }
      if (close < 0) break;                          // unclosed: steps 2/3 decide
      out = out.slice(0, open) + out.slice(close);
      if (onRemoved) onRemoved();
    }
    return out;
  }

  // Could this trailing fragment still turn into a reserved tag? Used only to
  // hold a partial opener back for one chunk during streaming.
  function couldBecomeReservedTag(tail) {
    // The optional trailing group is a half-typed ATTRIBUTE region. Without
    // it, <STORED_FActor name="voice" was not a partial opener — the fragment
    // stopped matching the moment the space arrived — so the whole attributed
    // tag was spoken before its ">" turned it back into scaffolding. It only
    // counts once the NAME is already reserved, because a bare "<" or "*"
    // followed by arbitrary words is ordinary prose ("5 < 6", "*nods* and…")
    // and withholding on that would stall speech for most of a reply.
    const match = /^[<*]\s*(\/?)\s*([A-Za-z0-9_]*)(\s[^<>]*)?$/.exec(tail);
    if (!match) return false;
    const name = match[2];
    const attributes = match[3];
    if (!name) return !attributes;                   // "<", "</", "*" so far
    if (attributes) return isReservedScaffoldName(name);
    const upper = name.toUpperCase();
    if (SCAFFOLD_STEMS.some(function(stem) {
      return stem.indexOf(upper) === 0 || upper.indexOf(stem) === 0;
    })) return true;
    return SCAFFOLD_BARE_WORDS.some(function(word) {
      return name === upper && word.indexOf(upper) === 0;
    });
  }

  function stripMemoryScaffolding(text, options) {
    const atHead = Boolean(options && options.atHead);
    // Opt-in, and deliberately off by default: the partial-opener holdback in
    // step 4 truncates, so a caller must say it is mid-stream to get it.
    const streaming = Boolean(options && options.streaming);
    let value = String(text || '');
    let removed = 0;
    // A block that opens the reply owns the line break after it, the same rule
    // parseAssistantReaction applies to a leading reaction marker. Without
    // this, removing it leaves "\n\nReply", which the two clients then trim
    // differently (mobile's finalizer trims, desktop's stored text did not) —
    // a parity gap created by the guard rather than by the model.
    // NB: reservedTagAt returns an INDEX, and index 0 is falsy while -1 is
    // truthy — compare explicitly.
    const openedAtHead = atHead && reservedTagAt(value, true) >= 0;

    // 1. Matched pair -> remove the block AND its contents. Opener and closer
    //    need not agree: the live leaks paired <STORED_FACTS> with
    //    </STORED_FACT> and </STORED_FACTV>.
    value = removeReservedBlocks(value, function() { removed += 1; });
    if (openedAtHead && removed) value = value.replace(/^\s+/, '');

    // 2. An opener with no closer at the HEAD of the reply. Suppress from it
    //    to the end of its PARAGRAPH, not to the end of the text.
    //
    //    "To the end" was wrong, and the 2026-09-19 capture proves it: the
    //    reply was
    //        <STORED_FAct name="format">Prose Fiction
    //
    //        …the actual reply…
    //    so suppressing everything after the opener destroyed a real answer to
    //    remove one stray line. A blank line is the natural end of an emitted
    //    scaffolding run — the model returns to prose after it — and where a
    //    block genuinely runs on without one, the whole run still goes.
    //    Looped, because the live capture was a RUN of them: one opener per
    //    line. Removing the first paragraph leaves the second at the head.
    if (atHead) {
      for (let guard = 0; guard < 50; guard += 1) {
        const headOpen = reservedTagAt(value, true);
        if (headOpen < 0) break;
        const paragraphEnd = value.slice(headOpen).search(/\n[ \t]*\n/);
        value = paragraphEnd < 0
          ? value.slice(0, headOpen)
          : value.slice(0, headOpen) + value.slice(headOpen + paragraphEnd);
        removed += 1;
      }
    }

    // Do not expose the beginning of a possible paired control block and then
    // remove it when its closer arrives. The browser cleans the accumulated
    // answer after every delta; without this holdback a mid-reply
    // <STORED_FACTS> opener can be visible for several chunks before
    // removeReservedBlocks sees its closer, shifting already-rendered prose.
    // Keep only the stable prefix until the opener either closes (and is
    // removed above) or the stream ends, when the final pass releases an
    // unclosed mid-reply span unchanged.
    if (streaming) {
      const pendingOpen = reservedTagAt(value, false);
      if (pendingOpen >= 0) {
        value = value.slice(0, pendingOpen);
      }
    }

    // 3. Orphan reserved CLOSERS -> remove the token only, never the prose
    //    around it. This is the ragged tail the live leak produced
    //    (</STORED_FACTV>, </STORED_FAC>, </STORED_FA>) once the model lost
    //    the thread.
    //
    //    Orphan OPENERS are deliberately left in place. Deleting one mid
    //    stream unwraps the block it introduces, turning scaffolding contents
    //    into what looks like ordinary prose — and, worse, into speech,
    //    because the TTS withhold keys off exactly that opener still being
    //    visible. An unclosed opener mid-prose stays put: the head rule above
    //    handles a reply that opens with one, and the display guard stays
    //    conservative everywhere else.
    value = value.replace(SCAFFOLD_TOKEN_RE, function(token, slash) {
      if (!slash || !isReservedScaffoldToken(token)) return token;
      removed += 1;
      return '';
    });

    // 4. Hold back a trailing partial opener so a tag cannot flash and then
    //    disappear once the rest arrives.
    //
    //    STREAMING ONLY, and that is not a detail. This truncates the end of
    //    the text, which is correct mid-stream (the caller re-cleans the whole
    //    accumulated answer on the next chunk, so the tail comes straight
    //    back) and data loss on a finished reply (there is no next chunk).
    //    When this ran unconditionally it silently ate the end of any reply
    //    closing on markdown emphasis or an all-caps word — "**bold**STORED"
    //    became "**bold*" — which is the whitespace/word damage reported on
    //    2026-09-19. Callers that clean a FINAL reply must never pass
    //    streaming.
    if (streaming) {
      const cut = Math.max(value.lastIndexOf('<'), value.lastIndexOf('*'));
      if (cut >= 0 && value.indexOf('>', cut) === -1) {
        const tail = value.slice(cut);
      if (couldBecomeReservedTag(tail)) value = value.slice(0, cut);
      }
    }

    if (removed) logScaffoldGuard(removed);
    return value;
  }

  // Audio cannot be un-spoken. The display guard is deliberately conservative
  // about an unclosed block in mid-prose (it may be the model talking ABOUT the
  // tag, and losing visible text is worse than showing it), but TTS has no such
  // luxury: once a sentence is queued and synthesised it is heard. So for the
  // speech path, hold back everything from an unresolved opener to the end
  // until the stream settles it. A closed block has already been removed by
  // stripMemoryScaffolding, so any opener still present here is unresolved.
  // Every control construct the cleaner removes only once it is COMPLETE. Mid
  // stream each one is still growing, so the cleaner cannot match it yet and
  // the raw characters were being spoken before the closer arrived — the same
  // leak shape as the memory block, just with brackets. Withhold from the
  // earliest unresolved opener to the end of the text.
  function unresolvedControlCut(value) {
    let cut = -1;
    // Reserved scaffolding is matched through isReservedScaffoldName, not a
    // raw regex: the family tier is case-SENSITIVE, and a case-insensitive
    // pattern here treated ordinary lowercase markup such as <string> as an
    // unresolved opener and withheld the rest of the reply from speech.
    const openAt = reservedTagAt(value, false);
    if (openAt >= 0) {
      const closeRe = new RegExp(SCAFFOLD_TOKEN_RE.source, 'gi');
      closeRe.lastIndex = openAt + 1;
      let closed = false;
      let closer;
      while ((closer = closeRe.exec(value)) !== null) {
        if (closer[1] && isReservedScaffoldToken(closer[0])) { closed = true; break; }
      }
      if (!closed) cut = openAt;
    }
    const rules = [
      { open: /\[MEMORY[_ ]ADD:/gi, close: /\]/g },
      { open: /\[Author['’]s Note:/gi, close: /\]/g },
      { open: /^[ \t]*STYLE REMINDER:/gim, close: /\n/g },
    ];
    for (const rule of rules) {
      let lastOpen = -1;
      let match;
      while ((match = rule.open.exec(value)) !== null) lastOpen = match.index;
      if (lastOpen < 0) continue;
      rule.close.lastIndex = lastOpen;
      if (!rule.close.exec(value)) cut = cut < 0 ? lastOpen : Math.min(cut, lastOpen);
    }
    return cut;
  }

  // Find the beginning of control syntax whose existing cleaner may remove
  // text once a later delimiter arrives. Streaming callers clean the complete
  // accumulated answer on every delta, so those still-open spans must remain
  // buffered instead of being shown and then retracted.
  function pendingControlCut(value, charName, userName) {
    let cut = -1;
    const bracketControls = [
      /\[\s*Author['’]s Note\s*:/gi,
      /\[\s*MEMORY[_ ]ADD\s*:/gi,
    ];
    for (const opener of bracketControls) {
      let match;
      while ((match = opener.exec(value)) !== null) {
        if (value.indexOf(']', match.index + match[0].length) < 0) {
          cut = cut < 0 ? match.index : Math.min(cut, match.index);
        }
      }
    }

    // Prefix fragments are ambiguous only at their current line/head. Hold
    // them until they either become a known control header or diverge into
    // ordinary prose; this is bounded by the marker's short opening text.
    const controlHeads = [
      "[author's note:", '[author’s note:', '[memory_add:', '[memory add:',
    ];
    const bracket = value.lastIndexOf('[');
    if (bracket >= 0) {
      const tail = value.slice(bracket).toLowerCase();
      if (controlHeads.some(head => head.startsWith(tail))) {
        cut = cut < 0 ? bracket : Math.min(cut, bracket);
      }
    }

    const labels = ['user', 'assistant', 'system', 'User', 'Assistant'];
    if (charName) labels.push(String(charName));
    if (userName) labels.push(String(userName));
    const headerNames = Array.from(new Set(labels)).filter(Boolean);
    const lineStart = Math.max(0, value.lastIndexOf('\n') + 1);
    const line = value.slice(lineStart);
    const trimmedLine = line.replace(/^[ \t]*/, '');
    const indent = line.length - trimmedLine.length;
    const headerAt = lineStart + indent;
    const lowerLine = trimmedLine.toLowerCase();
    const lineControls = ['STYLE REMINDER:', 'MEMORY_ADD:', 'MEMORY ADD:'];
    if (lowerLine && lineControls.some(marker => {
      const lowerMarker = marker.toLowerCase();
      return lowerMarker.startsWith(lowerLine) || lowerLine.startsWith(lowerMarker);
    })) {
      cut = cut < 0 ? headerAt : Math.min(cut, headerAt);
    } else if (line.length === trimmedLine.length) {
      const completedHeader = headerNames.some(name => {
        const lowerName = name.toLowerCase();
        return lowerLine.startsWith(lowerName)
          && /^[:\n]/.test(trimmedLine.slice(name.length));
      });
      const possible = !completedHeader && headerNames.some(name => {
        const lowerName = name.toLowerCase();
        return lowerName.startsWith(lowerLine) || lowerLine === lowerName;
      });
      if (possible && lowerLine) cut = cut < 0 ? headerAt : Math.min(cut, headerAt);
    }

    return cut;
  }

  // A control opener also arrives a character at a time. Until enough of it
  // exists to match, it is indistinguishable from prose — so a trailing
  // fragment that could still BECOME an opener is withheld for a chunk. The
  // fragment is always short and always resolves on the next chunk; without
  // this, speech says "[MEMORY ADD" before the colon identifies it.
  const TTS_BRACKET_OPENERS = Object.freeze([
    '[memory add:', '[memory_add:', "[author's note:",
  ]);
  function partialControlCut(value) {
    let cut = -1;
    const lower = value.toLowerCase().replace(/’/g, "'");
    const bracket = lower.lastIndexOf('[');
    if (bracket >= 0) {
      const tail = lower.slice(bracket);
      if (TTS_BRACKET_OPENERS.some(form => form.indexOf(tail) === 0)) cut = bracket;
    }
    const lineStart = lower.lastIndexOf('\n') + 1;
    const lastLine = lower.slice(lineStart).replace(/^[ \t]+/, '');
    if (lastLine && 'style reminder:'.indexOf(lastLine) === 0) {
      cut = cut < 0 ? lineStart : Math.min(cut, lineStart);
    }
    return cut;
  }

  function withholdUnresolvedScaffolding(text) {
    const value = String(text || '');
    let cut = unresolvedControlCut(value);
    const partial = partialControlCut(value);
    if (partial >= 0) cut = cut < 0 ? partial : Math.min(cut, partial);
    if (cut >= 0) return value.slice(0, cut);
    // The display guard deliberately does NOT hold a trailing bare "<" or "*" —
    // that would corrupt ordinary prose like "5 < 6" or a markdown "**bold**".
    // Speech can afford to wait one chunk, and must: otherwise the first
    // character of an arriving "<STORED_FACTS>" — or of the "*STORED_CONTEXT>"
    // the live leak produced — is spoken before the rest identifies it.
    return /[<*]$/.test(value) ? value.slice(0, -1) : value;
  }

  // ── The ONE text the speech path may say ────────────────────────────────
  // Regression 2026-09-19: a reply rendered clean in the UI while TTS audibly
  // spoke internal <STORED_FACTS> memory content. Both pages fed TTS from the
  // RAW accumulated answer, sliced into deltas, with only a small inline
  // ChatML/role cleanup of their own — so every guard that lives in
  // stripChatMLOutsideCodeBlocks (memory scaffolding, MEMORY_ADD, Author's
  // Note, STYLE REMINDER, and the fenced-code protection) applied to what was
  // shown and saved but never to what was said.
  //
  // Speech now derives from exactly the cleaned text the display uses, so the
  // two cannot diverge again. Callers keep a monotonic cursor of how much has
  // been spoken and pass it back in; the committed prefix is stable because
  // anything still retractable is withheld above, so the cursor never replays
  // or skips.
  function ttsSpeakableDelta(accumulatedAnswer, spokenLength, options) {
    const charName = options && options.charName;
    const userName = options && options.userName;
    // `final` releases the last chunk: no streaming holdback and no withhold,
    // because there is no next chunk to resolve a dangling "<" or "*". Without
    // it the very last character of a reply is never spoken.
    const final = Boolean(options && options.final);
    const base = stripChatMLOutsideCodeBlocks(
      String(accumulatedAnswer || ''), charName, userName, { streaming: !final }
    );
    const cleaned = final ? base : withholdUnresolvedScaffolding(base);
    const previous = Math.max(0, Number(spokenLength) || 0);
    // The cursor NEVER moves backwards. Held text makes `cleaned` shrink and
    // then grow again; clamping the cursor down to the shrunken length made
    // the regrown characters emit a second time, which is how a reply gained
    // a stray "*" or a doubled space in speech. If it shrank, say nothing
    // this round and keep the mark where it was.
    if (cleaned.length <= previous) return { text: '', spokenLength: previous };
    return { text: cleaned.slice(previous), spokenLength: cleaned.length };
  }

  // ── Assistant reply cleaning ────────────────────────────────────────────
  // Strip ChatML tokens and role-leakage ONLY from text outside fenced code blocks.
  // Content inside ``` fences is preserved verbatim — so shard generation works correctly.
  function stripChatMLOutsideCodeBlocks(text, charName, userName, options) {
    const streaming = Boolean(options && options.streaming);
    // Recognise CommonMark fence runs, not only exactly three delimiters. CX/SFT
    // output legitimately uses an outer four-backtick fence when its Response
    // contains an inner triple-backtick ChatML block.
    const parts = [];
    let plainStart = 0;
    let cursor = 0;
    while (cursor < text.length) {
      const lineEnd = text.indexOf('\n', cursor);
      const nextLine = lineEnd === -1 ? text.length : lineEnd + 1;
      const line = text.slice(cursor, lineEnd === -1 ? text.length : lineEnd).replace(/\r$/, '');
      const opener = line.match(/^[ \t]{0,3}(`{3,}|~{3,})[^\r\n]*$/);
      if (!opener) {
        cursor = nextLine;
        continue;
      }

      if (cursor > plainStart) parts.push({ fenced: false, value: text.slice(plainStart, cursor) });
      const fenceChar = opener[1][0];
      const fenceLength = opener[1].length;
      let fenceEnd = text.length;
      let scan = nextLine;
      while (scan < text.length) {
        const closeLineEnd = text.indexOf('\n', scan);
        const closeNext = closeLineEnd === -1 ? text.length : closeLineEnd + 1;
        const closeLine = text.slice(
          scan,
          closeLineEnd === -1 ? text.length : closeLineEnd
        ).replace(/\r$/, '');
        const closer = closeLine.match(/^[ \t]{0,3}(`{3,}|~{3,})[ \t]*$/);
        if (
          closer &&
          closer[1][0] === fenceChar &&
          closer[1].length >= fenceLength
        ) {
          fenceEnd = closeNext;
          break;
        }
        scan = closeNext;
      }
      parts.push({ fenced: true, value: text.slice(cursor, fenceEnd) });
      cursor = fenceEnd;
      plainStart = fenceEnd;
    }
    if (plainStart < text.length) parts.push({ fenced: false, value: text.slice(plainStart) });
    if (!parts.length) parts.push({ fenced: false, value: text });

    let holdFollowingParts = false;
    return parts.map(function(part, index) {
      if (holdFollowingParts) return '';
      if (part.fenced) return part.value;
      // Scaffolding first, so no later rule can mangle the block before it is
      // recognised. Fenced segments never reach here, so a reply that shows
      // the tag inside ``` keeps it verbatim.
      const wasUnclosedReserved = streaming
        && !(index === 0 && reservedTagAt(part.value, true) >= 0)
        && unclosedReservedTagAt(part.value) >= 0;
      let plain = stripMemoryScaffolding(part.value, { atHead: index === 0, streaming: streaming });
      if (streaming) {
        const pendingCut = pendingControlCut(plain, charName, userName);
        if (pendingCut >= 0) {
          plain = plain.slice(0, pendingCut);
          holdFollowingParts = true;
        }
      }
      if (wasUnclosedReserved) holdFollowingParts = true;
      plain = plain
        .replace(/<\|im_start\|>assistant/gi, '')
        .replace(/<\|im_start\|>user/gi, '')
        .replace(/<\|im_start\|>system/gi, '')
        .replace(/<\|im_end\|>/gi, '')
        .replace(/\bim_end\|?>/gi, '')
        .replace(/<\/\|im_end\|>/gi, '')
        .replace(/_end\|?>/gi, '')
        // Only remove a standalone damaged control-marker tail. Both punctuation
        // characters are explicit so ordinary words such as backend/weekend/friend
        // can never lose their "end" substring again.
        .replace(/(?<![\p{L}\p{N}_])End(?:\|>|[|>])(?![\p{L}\p{N}_])/giu, '')
        .replace(/\n(?:user|assistant|system)(?:\n|:)/gi, '\n')  // strip role header only — [\s\S]*$ was wiping real responses mid-stream
        .replace(new RegExp('^' + (charName || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ':\\s*', 'gim'), '')
        .replace(new RegExp('^' + (userName || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ':\\s*', 'gim'), '')
        .replace(/^User:\s*/gim, '')
        .replace(/^Assistant:\s*/gim, '')
        .replace(/\[Author['']s Note:[^\]]*\]/gi, '')
        .replace(/^STYLE REMINDER:.*$/gim, '')
        .replace(/\[MEMORY[_ ]ADD:[^|\]]*\|[^|\]]*\|([^\]]*)\]/gi, '$1')
        .replace(/\[MEMORY[_ ]ADD:[^\]]*\]/gi, '')
        .replace(/^MEMORY[_ ]ADD:[^|]*\|[^|]*\|(.+)$/gim, '$1')
        .replace(/^MEMORY[_ ]ADD:[^|]*\|(.+)$/gim, '$1')
        .replace(/^MEMORY[_ ]ADD:[^\n]*$/gim, '');
      return plain;
    }).join('');
  }

  // Desktop's empty-after-stripping guard: when cleaning ate the whole reply,
  // keep the raw answer minus bare ChatML tokens rather than saving nothing.
  function rawAssistantFallback(text) {
    const reactionOutput = parseAssistantReaction(text, { final: true });
    // The fallback exists to rescue a reply that cleaning emptied, so it must
    // apply the scaffolding guard too — otherwise a reply that is ONLY a
    // <STORED_FACTS> block cleans to "", falls back to the raw text, and the
    // scaffolding is stored after all. An empty result here is correct: the
    // empty-reply paths on both clients then handle it as no reply.
    return stripMemoryScaffolding(reactionOutput.text, { atHead: true })
      .replace(/<\|im_start\|>\w*/gi, '').replace(/<\|im_end\|>/gi, '').trim();
  }

  // The exact text desktop stores for a completed reply: `answer` is the
  // streamed answer with any thinking already peeled off.
  function finalizeAssistantText(answer, charName, userName) {
    const reactionOutput = parseAssistantReaction(answer, { final: true });
    const cleaned = stripChatMLOutsideCodeBlocks(reactionOutput.text, charName, userName).trim();
    if (!cleaned || cleaned.trim().length < 2) return rawAssistantFallback(answer);
    return cleaned;
  }

  // ── History save rules ──────────────────────────────────────────────────
  function contentKey(content) {
    return Array.isArray(content)
      ? content.filter(p => p.type === 'text').map(p => p.text).join(' ')
      : content;
  }

  // Desktop autoSaveCurrentChat() rules, returned as a new array:
  //  1. drop an assistant turn that directly follows another assistant turn,
  //     EXCEPT an automatic check-in (a check-in legitimately follows the
  //     previous reply; removing it hides from the model the very message the
  //     user is answering);
  //  2. drop a turn only when it is identical (role, content, kind, check-in id)
  //     to the turn IMMEDIATELY before it.
  // ⚠️ DO NOT make (2) a global seen-set dedup. That silently destroys
  // legitimately-repeated messages ("continue", "yes", a re-asked question)
  // mid-chat — it deleted a repeated user turn before /chat on mobile, so the
  // model answered the previous message. See changes.md (June 6 2026 —
  // regenerate strip-too-far; Sep 2026 — desktop/mobile parity).
  function prepareHistoryForSave(messages) {
    const list = Array.isArray(messages) ? messages.slice() : [];

    for (let i = list.length - 1; i > 0; i--) {
      const current = list[i];
      const previous = list[i - 1];
      if (current.role === 'assistant' && previous.role === 'assistant' &&
          current.message_kind !== 'automatic_checkin') {
        list.splice(i, 1);
      }
    }

    const deduped = [];
    let prevKey = null;
    for (let i = 0; i < list.length; i++) {
      const msg = list[i];
      const key = `${msg.role}:${contentKey(msg.content)}:${msg.message_kind || ''}:${msg.checkin_id || ''}:${normalizeReaction(msg.reaction) || ''}`;
      if (key === prevKey) continue;
      deduped.push(msg);
      prevKey = key;
    }
    return deduped;
  }

  // One saved message, exactly as desktop sends it to /chats/save. The server
  // rebuilds the metadata sidecar from every save, so a field left out here is
  // a field erased from disk.
  function serializeMessageForSave(msg, fallbacks) {
    const userName = fallbacks && fallbacks.userName;
    const charName = fallbacks && fallbacks.charName;
    // Same defensive shape as the reaction parse beside it: an assistant turn
    // is guarded at the persistence and load boundaries too, so scaffolding
    // already written to a chat file cannot re-enter conversation_history on a
    // later turn. Non-assistant content is untouched.
    const reactionOutput = msg.role === 'assistant' && typeof msg.content === 'string'
      ? parseAssistantReaction(msg.content, { final: true })
      : { text: msg.content, reaction: null };
    if (msg.role === 'assistant' && typeof reactionOutput.text === 'string') {
      reactionOutput.text = stripMemoryScaffolding(reactionOutput.text, { atHead: true });
    }
    const reaction = normalizeReaction(msg.reaction) || reactionOutput.reaction;
    return {
      role: msg.role,
      // Keep model-facing chat text compact; the complete prepared image used
      // by the thumbnail/lightbox lives only in verified message metadata.
      content: Array.isArray(msg.content)
        ? (msg.content.filter(p => p.type === 'text').map(p => p.text).join(' ').trim() + ' [image]').trim()
        : reactionOutput.text,
      speaker: msg.speaker || (msg.role === 'user' ? userName : charName),
      ...(msg.is_opening_line ? { is_opening_line: true } : {}),
      ...(msg.timestamp ? { timestamp: msg.timestamp } : {}),
      ...(msg.message_id ? { message_id: msg.message_id } : {}),
      ...(msg.reply_to_message_id ? { reply_to_message_id: msg.reply_to_message_id } : {}),
      ...(msg.generation_status ? { generation_status: msg.generation_status } : {}),
      ...(msg.generation_started_at ? { generation_started_at: msg.generation_started_at } : {}),
      ...(msg.generation_completed_at ? { generation_completed_at: msg.generation_completed_at } : {}),
      ...(msg.thinking ? { thinking: msg.thinking } : {}),
      ...(msg.message_kind ? { message_kind: msg.message_kind } : {}),
      ...(msg.checkin_id ? { checkin_id: msg.checkin_id } : {}),
      ...(msg.exclude_from_context === true ? { exclude_from_context: true } : {}),
      ...(reaction ? { reaction } : {}),
      ...(msg.hasImage ? { hasImage: true } : {}),
      ...(Array.isArray(msg.previewUrls) && msg.previewUrls.length > 0 ? { previewUrls: msg.previewUrls } : {})
    };
  }

  // Hidden turns (e.g. the desktop memory-confirm trigger) are never written.
  function serializeHistoryForSave(messages, fallbacks) {
    return (messages || []).filter(msg => !msg.hidden).map(msg => serializeMessageForSave(msg, fallbacks));
  }

  // /chats/open message → in-page history entry, keeping every per-message
  // field that save writes back. previewUrls are carried as stored; desktop
  // then swaps them for restored data: URLs so the image can be re-sent.
  function mapLoadedMessage(msg, options) {
    const activeUserName = (options && options.activeUserName) || 'User';
    let spk = msg.speaker || null;
    // Normalise the legacy "User" speaker label to the active user name.
    if (spk && spk.toLowerCase() === 'user' && msg.role === 'user') {
      spk = activeUserName;
    }
    // Same defensive shape as the reaction parse beside it: an assistant turn
    // is guarded at the persistence and load boundaries too, so scaffolding
    // already written to a chat file cannot re-enter conversation_history on a
    // later turn. Non-assistant content is untouched.
    const reactionOutput = msg.role === 'assistant' && typeof msg.content === 'string'
      ? parseAssistantReaction(msg.content, { final: true })
      : { text: msg.content, reaction: null };
    if (msg.role === 'assistant' && typeof reactionOutput.text === 'string') {
      reactionOutput.text = stripMemoryScaffolding(reactionOutput.text, { atHead: true });
    }
    const mapped = {
      role: msg.role,
      content: reactionOutput.text,
      speaker: spk
    };
    if (msg.is_opening_line) mapped.is_opening_line = true;
    if (msg.timestamp) mapped.timestamp = msg.timestamp;
    if (msg.message_id) mapped.message_id = msg.message_id;
    if (msg.reply_to_message_id) mapped.reply_to_message_id = msg.reply_to_message_id;
    if (msg.generation_status) mapped.generation_status = msg.generation_status;
    if (msg.generation_started_at) mapped.generation_started_at = msg.generation_started_at;
    if (msg.generation_completed_at) mapped.generation_completed_at = msg.generation_completed_at;
    if (msg.thinking) mapped.thinking = msg.thinking;
    if (msg.message_kind) mapped.message_kind = msg.message_kind;
    if (msg.checkin_id) mapped.checkin_id = msg.checkin_id;
    if (msg.exclude_from_context === true) mapped.exclude_from_context = true;
    const reaction = normalizeReaction(msg.reaction) || reactionOutput.reaction;
    if (reaction) mapped.reaction = reaction;
    if (msg.hasImage) mapped.hasImage = true;
    if (Array.isArray(msg.previewUrls) && msg.previewUrls.length > 0) mapped.previewUrls = msg.previewUrls.slice();
    return mapped;
  }

  // ── /chat request construction ──────────────────────────────────────────
  // conversation_history for a normal send or regenerate (desktop
  // fetchAndDisplayResponse). Hidden turns ride along with the flag stripped so
  // the backend treats them as normal turns; modelTextForSend replaces the
  // latest user text; only the most recent multimodal turn keeps its images.
  function buildConversationHistory(messages, options) {
    const modelTextForSend = options && options.modelTextForSend;
    const loadedChat = Array.isArray(messages) ? messages : [];
    const visibleChat = loadedChat.filter(msg => !msg.hidden);
    const chatForSend = loadedChat.map(msg => msg.hidden ? {...msg, hidden: undefined} : msg);
    if (modelTextForSend && typeof modelTextForSend === 'string') {
      for (let i = chatForSend.length - 1; i >= 0; i--) {
        if (chatForSend[i] && chatForSend[i].role === 'user') {
          if (Array.isArray(chatForSend[i].content)) {
            let replacedText = false;
            const parts = chatForSend[i].content.map(part => {
              if (!replacedText && part && part.type === 'text') {
                replacedText = true;
                return { ...part, text: modelTextForSend };
              }
              return part;
            });
            chatForSend[i] = replacedText
              ? { ...chatForSend[i], content: parts }
              : { ...chatForSend[i], content: [{ type: 'text', text: modelTextForSend }, ...chatForSend[i].content] };
          } else {
            chatForSend[i] = { ...chatForSend[i], content: modelTextForSend };
          }
          break;
        }
      }
    }
    let lastImageIdx = -1;
    for (let i = visibleChat.length - 1; i >= 0; i--) {
      if (Array.isArray(visibleChat[i].content)) {
        lastImageIdx = i;
        break;
      }
    }
    return chatForSend.map((msg, i) => {
      const metadata = msg.exclude_from_context === true
        ? { exclude_from_context: true }
        : {};
      if (Array.isArray(msg.content)) {
        if (i === lastImageIdx) {
          return { role: msg.role, content: msg.content, ...metadata }; // Keep the most recent image message intact
        } else {
          // Strip images from older messages — text only
          const textOnly = msg.content.filter(p => p.type === 'text').map(p => p.text).join(' ');
          return {
            role: msg.role,
            content: textOnly || '[image]',
            ...(msg.exclude_from_context === true ? { exclude_from_context: true } : {})
          };
        }
      }
      return { role: msg.role, content: msg.content, ...metadata };
    });
  }

  // Continue = true continuation of the last visible assistant reply (desktop
  // continueLast): that reply leaves history, its last 200 chars become the
  // continue_prefix the backend pre-fills the assistant turn with, and the head
  // is retained so the stored reply is head + prefix + new tokens. Sending the
  // whole reply makes the model think it is nearly done and fire EOS after a
  // few tokens. Never a fake user turn.
  const CONTINUE_PREFIX_CHARS = 200;

  function buildContinueRequest(messages) {
    const loadedChat = Array.isArray(messages) ? messages : [];
    let lastAssistantIdx = -1;
    for (let i = loadedChat.length - 1; i >= 0; i--) {
      if (loadedChat[i].role === 'assistant' && !loadedChat[i].hidden) {
        lastAssistantIdx = i;
        break;
      }
    }
    if (lastAssistantIdx === -1) return null;
    const fullPrevContent = loadedChat[lastAssistantIdx].content || '';
    const continuePrefix = fullPrevContent.length > CONTINUE_PREFIX_CHARS
      ? fullPrevContent.slice(-CONTINUE_PREFIX_CHARS)
      : fullPrevContent;
    const retainedHead = fullPrevContent.length > CONTINUE_PREFIX_CHARS
      ? fullPrevContent.slice(0, -CONTINUE_PREFIX_CHARS)
      : '';
    const remainingHistory = loadedChat.filter((_msg, i) => i !== lastAssistantIdx);
    const conversationHistory = remainingHistory.filter(msg => !msg.hidden).map(msg => {
      const metadata = msg.exclude_from_context === true
        ? { exclude_from_context: true }
        : {};
      if (Array.isArray(msg.content)) {
        const textOnly = msg.content.filter(p => p.type === 'text').map(p => p.text).join(' ');
        return { role: msg.role, content: textOnly || '[image]', ...metadata };
      }
      return { role: msg.role, content: msg.content, ...metadata };
    });
    const diagnosticHistoryIds = remainingHistory.filter(msg => !msg.hidden).map(msg => msg.message_id || null);
    return { lastAssistantIdx, continuePrefix, retainedHead, remainingHistory, conversationHistory, diagnosticHistoryIds };
  }

  // Stored text for a continued reply: `answer` is head + prefix + streamed
  // tokens with thinking peeled off. (No raw fallback — matches desktop.)
  function finalizeContinuedText(answer, charName, userName) {
    const reactionOutput = parseAssistantReaction(answer, { final: true });
    return stripChatMLOutsideCodeBlocks(reactionOutput.text, charName, userName).trim();
  }

  // The /chat body. Sampling is sourced server-side from settings.json
  // (load_sampling_settings) — client sampler values are inert, so none are
  // sent. The Author's Note is stored server-side per chat and resolved by
  // /chat; author_note is sent ONLY as an explicit one-turn override.
  function buildChatRequestBody(fields) {
    const body = {
      character: fields.character,
      user_name: fields.userName,
      current_chat_filename: fields.chatFilename,
      conversation_history: fields.conversationHistory,
      current_turn_has_image: fields.currentTurnHasImage === true,
      genuine_user_send: fields.genuineUserSend === true,
    };
    if (Array.isArray(fields.diagnosticHistoryIds)) {
      // Sidecar metadata for explicit local prompt diagnostics only. These IDs
      // are never copied into conversation_history or provider messages.
      body.prompt_diagnostic_history_ids = fields.diagnosticHistoryIds.slice();
    }
    if (typeof fields.continuePrefix === 'string') body.continue_prefix = fields.continuePrefix;
    if (typeof fields.authorNoteOverride === 'string' && fields.authorNoteOverride.trim()) {
      body.author_note = fields.authorNoteOverride;
    }
    if (fields.extra) Object.assign(body, fields.extra);
    return body;
  }

  // Assistant history entry with desktop's completion metadata.
  function buildAssistantMessage(fields) {
    return {
      role: 'assistant',
      content: fields.content,
      speaker: fields.speaker,
      timestamp: fields.completedAt,
      message_id: fields.messageId || createMessageId(),
      reply_to_message_id: fields.replyToMessageId || null,
      generation_status: fields.status || 'completed',
      ...(fields.startedAt ? { generation_started_at: fields.startedAt } : {}),
      generation_completed_at: fields.completedAt,
      ...(fields.thinking ? { thinking: fields.thinking } : {}),
      ...(fields.messageKind ? { message_kind: fields.messageKind, checkin_id: fields.checkinId } : {})
    };
  }

  function setMessageReaction(messages, messageId, reaction) {
    const target = (messages || []).find(message =>
      message && message.message_id === messageId
    );
    if (!target) return false;
    const normalized = normalizeReaction(reaction);
    if (normalized) target.reaction = normalized;
    else delete target.reaction;
    return true;
  }

  // ── Author's Note (server-side, per chat) ───────────────────────────────
  // The note lives in the chat's metadata sidecar so desktop and mobile get the
  // same effective note. Desktop used to keep it only in this browser's
  // localStorage under author-note-<filename>; those notes migrate to the
  // server the first time the chat is opened here, and until that succeeds the
  // legacy note is still sent as an override so behaviour never regresses.
  const LEGACY_AUTHOR_NOTE_PREFIX = 'author-note-';

  function legacyAuthorNoteKey(filename) {
    return `${LEGACY_AUTHOR_NOTE_PREFIX}${filename}`;
  }

  function readLegacyAuthorNote(storage, filename) {
    if (!storage || !filename) return null;
    try { return storage.getItem(legacyAuthorNoteKey(filename)); } catch (e) { return null; }
  }

  function removeLegacyAuthorNote(storage, filename) {
    if (!storage || !filename || typeof storage.removeItem !== 'function') return;
    try { storage.removeItem(legacyAuthorNoteKey(filename)); } catch (e) {}
  }

  async function fetchChatAuthorNote(fetchImpl, filename) {
    const res = await fetchImpl('/chats/author_note?filename=' + encodeURIComponent(filename));
    if (!res.ok) throw new Error(`Author's Note load failed (HTTP ${res.status})`);
    const data = await res.json();
    return typeof data.author_note === 'string' ? data.author_note : '';
  }

  async function saveChatAuthorNote(fetchImpl, filename, note, options) {
    const res = await fetchImpl('/chats/author_note', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        filename,
        author_note: String(note == null ? '' : note),
        only_if_empty: Boolean(options && options.onlyIfEmpty)
      })
    });
    let data = {};
    try { data = await res.json(); } catch (e) {}
    if (!res.ok) throw new Error(data.error || `Author's Note save failed (HTTP ${res.status})`);
    return typeof data.author_note === 'string' ? data.author_note : '';
  }

  async function fetchCharacterAuthorNote(fetchImpl, character) {
    const key = String(character == null ? '' : character).trim();
    if (!key) return '';
    const res = await fetchImpl('/character_author_note/' + encodeURIComponent(key));
    if (!res.ok) throw new Error(`Character Response Intent load failed (HTTP ${res.status})`);
    const data = await res.json();
    return typeof data.author_note === 'string' ? data.author_note : '';
  }

  async function saveCharacterAuthorNote(fetchImpl, character, note) {
    const key = String(character == null ? '' : character).trim();
    if (!key) throw new Error('No character selected');
    const res = await fetchImpl('/character_author_note/' + encodeURIComponent(key), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ author_note: String(note == null ? '' : note) })
    });
    let data = {};
    try { data = await res.json(); } catch (e) {}
    if (!res.ok) throw new Error(data.error || `Character Response Intent save failed (HTTP ${res.status})`);
    return typeof data.author_note === 'string' ? data.author_note : '';
  }

  // Moves this browser's legacy note for `filename` to the server once.
  // Returns the effective server note. The server write is only-if-empty, so a
  // note already stored (set from any device) is never overwritten by a stale
  // local copy; the local copy is discarded once the server holds a note.
  async function migrateLegacyAuthorNote(fetchImpl, storage, filename, serverNote) {
    const legacy = readLegacyAuthorNote(storage, filename);
    if (legacy === null || legacy === undefined) return serverNote || '';
    if (String(serverNote || '').trim() || !String(legacy).trim()) {
      removeLegacyAuthorNote(storage, filename);
      return serverNote || '';
    }
    try {
      const saved = await saveChatAuthorNote(fetchImpl, filename, legacy, { onlyIfEmpty: true });
      removeLegacyAuthorNote(storage, filename);
      return saved;
    } catch (e) {
      // Keep the local copy: it is still sent as an override until migrated.
      return serverNote || '';
    }
  }

  // A legacy note that has not reached the server yet (migration pending or
  // failed). Sent as the /chat override so the note still applies.
  function pendingLegacyAuthorNote(storage, filename) {
    const legacy = readLegacyAuthorNote(storage, filename);
    return legacy && legacy.trim() ? legacy : '';
  }

  // Keep the live answer as one source-backed text node. Markdown reparsing a
  // partial reply can drop trailing spaces or change earlier block structure;
  // the completed answer is rendered as Markdown by each page after the stream.
  function renderStreamingAssistantText(element, source) {
    const text = String(source == null ? '' : source);
    const previous = element._hwuiStreamingSource;
    if (text !== previous) {
      const node = element.firstChild;
      if (previous !== undefined && text.startsWith(previous)
          && node && node.nodeType === 3 && element.childNodes.length === 1) {
        node.appendData(text.slice(previous.length));
      } else {
        element.textContent = text; // Initial render or upstream control cleanup.
      }
    }
    element._hwuiStreamingSource = text;
    element.style.whiteSpace = 'pre-wrap';
  }

  function finishStreamingAssistantText(element) {
    element.style.whiteSpace = '';
    delete element._hwuiStreamingSource;
  }

  function waitForReactionPaint() {
    // Two frames give the newly attached badge a paint before answer rendering,
    // including when transport coalesces the prelude and first text chunk.
    // Hidden pages cannot paint; don't block streaming while frames are paused.
    if (!root.requestAnimationFrame || root.document.hidden) return Promise.resolve();
    return new Promise(resolve => {
      const finish = () => {
        root.document.removeEventListener('visibilitychange', onVisibility);
        resolve();
      };
      const onVisibility = () => { if (root.document.hidden) finish(); };
      root.document.addEventListener('visibilitychange', onVisibility);
      root.requestAnimationFrame(() => root.requestAnimationFrame(finish));
    });
  }

  const api = {
    CONTINUE_PREFIX_CHARS,
    LEGACY_AUTHOR_NOTE_PREFIX,
    createMessageId,
    stripChatMLOutsideCodeBlocks,
    stripMemoryScaffolding,
    ttsSpeakableDelta,
    rawAssistantFallback,
    finalizeAssistantText,
    prepareHistoryForSave,
    serializeMessageForSave,
    serializeHistoryForSave,
    mapLoadedMessage,
    buildConversationHistory,
    buildContinueRequest,
    finalizeContinuedText,
    buildChatRequestBody,
    buildAssistantMessage,
    REACTION_EMOJIS,
    normalizeReaction,
    parseAssistantReaction,
    setMessageReaction,
    legacyAuthorNoteKey,
    fetchChatAuthorNote,
    saveChatAuthorNote,
    fetchCharacterAuthorNote,
    saveCharacterAuthorNote,
    migrateLegacyAuthorNote,
    pendingLegacyAuthorNote,
    renderStreamingAssistantText,
    finishStreamingAssistantText,
    waitForReactionPaint,
  };

  root.HwuiChatCore = api;
  // Legacy global name — desktop call sites and tests use it directly.
  root.stripChatMLOutsideCodeBlocks = stripChatMLOutsideCodeBlocks;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);

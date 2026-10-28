"""Model-agnostic cleanup of an assistant reply's final line.

Some fine-tunes end a turn with the two tokens `<` `>` followed by EOS instead of
the ChatML end marker, so the reply finishes with a standalone literal ``<>`` line.
Nothing in HWUI creates it (llama.cpp's raw token trail shows the model sampling
`<` then `>`), and the ChatML stop string never matches, so it reached the display
and the saved chat and then got fed back to the model as history.

Only a ``<>`` that is the whole last line of the reply (outside an open code fence)
is removed, together with the whitespace around it. ``<>`` anywhere else - inline,
quoted, in code, mid-reply - is left alone.

Some models likewise finish an otherwise complete reply with an orphaned ``>``,
either glued to the last word (``Done.>``) or alone on the final line. That ``>``
is removed too, but only when it is the very last character, follows ordinary text
(a letter, digit, closing punctuation or quote), is outside an open code fence and
does not close a ``<`` left open earlier (HTML/XML tags, generics, comparisons).
Arrows (``->``, ``=>``), ``>>``, emoticons and a trailing empty blockquote line
after a ``>`` quote are kept.
"""

import codecs
import re
from functools import wraps
from response_pipeline import reasoning_isolated

_MARKER = "<>"

# This is visible-answer containment, not reasoning-channel detection. A
# generated paragraph that describes the governing prompt AND tells the
# assistant how to speak is a response plan, not the conversational answer.
_INSTRUCTION_TAIL = re.compile(
    r"^\s*(?:The|Your)\s+(?:character card|system prompt|developer instructions|post-history instructions)"
    r"\s+(?:says?|states?|requires?|instructs?)\b[^\n]{0,800}"
    r"\b(?:your voice should|you (?:should|must|need to))\b", re.IGNORECASE)
_PROMPT_DISCUSSION = re.compile(
    r"\b(?:prompt|instructions?|character card|training|shards?|verbatim|quote|rewrite|edit)\b",
    re.IGNORECASE)
INSTRUCTION_TAIL_NOTICE = "\n\n*The reply was stopped because it began generating response instructions.*"


def strip_instruction_tail_stream(source, request=""):
    """Stop an unsolicited instruction tail, preserving prose, quotes and code.

    Hold only a candidate paragraph prefix; ordinary paragraphs stream as
    soon as they cannot match. Reasoning isolation belongs to ResponsePipeline.
    A prompt-editing/discussion request bypasses this narrow guard entirely.
    """
    if _PROMPT_DISCUSSION.search(str(request or "")):
        yield from source
        return
    pending, at_start, fenced, line_prefix = "", True, False, ""
    prefixes = ("the character card", "your character card", "the system prompt", "your system prompt",
                "the developer instructions", "your developer instructions",
                "the post-history instructions", "your post-history instructions")
    try:
        for chunk in source:
            for char in chunk:
                pending += char
                if len(line_prefix) < 16:
                    line_prefix += char
                if at_start:
                    stripped = pending.lstrip().lower()
                    candidate = not fenced and any(p.startswith(stripped) or stripped.startswith(p)
                                                   for p in prefixes)
                    if candidate and _INSTRUCTION_TAIL.search(pending):
                        yield INSTRUCTION_TAIL_NOTICE
                        return
                    if candidate and len(pending) <= 1000 and not pending.endswith("\n\n"):
                        continue
                    at_start = False
                if char == '\n':
                    if line_prefix.lstrip().startswith(('```', '~~~')):
                        fenced = not fenced
                    yield pending
                    pending, at_start = "", True
                    line_prefix = ""
            if not at_start and pending:
                yield pending
                pending = ""
        if pending:
            yield pending
    finally:
        close = getattr(source, 'close', None)
        if close:
            close()
_TRAILING_MARKER_RE = re.compile(r"(?:\s*\n|^)[ \t]*<>\s*$")
_ORPHAN = ">"
# Characters a stray `>` may follow; anything else (`-`, `=`, `<`, `>`, `:`, ...) means
# the `>` is part of an arrow, operator, tag or emoticon.
_ORPHAN_AFTER = frozenset(".,!?…\"'”’)]*_~")


def _inside_open_fence(text):
    return text.count("```") % 2 == 1


def _strip_orphan_gt(text):
    body = text.rstrip()
    if not body.endswith(_ORPHAN):
        return text
    before = body[:-1]
    kept = before.rstrip()
    if not kept or _inside_open_fence(kept) or kept.rfind("<") > kept.rfind(">"):
        return text
    if "\n" in before[len(kept):]:
        # `>` alone on the last line: keep it when it continues a blockquote.
        if kept.rsplit("\n", 1)[-1].lstrip().startswith(">"):
            return text
    elif not (kept[-1].isalnum() or kept[-1] in _ORPHAN_AFTER):
        return text
    return kept


def strip_trailing_empty_marker(text):
    """Return `text` without a standalone trailing ``<>`` line or orphaned final ``>``
    (and the whitespace around it). At most one marker is removed."""
    if not text or _ORPHAN not in text:
        return text
    match = _TRAILING_MARKER_RE.search(text) if _MARKER in text else None
    if match and not _inside_open_fence(text[:match.start()]):
        return text[:match.start()]
    return _strip_orphan_gt(text)


def _hold_start(buf):
    """Index where the held-back tail of `buf` starts.

    The tail is the trailing whitespace plus, when it sits alone at the start of a
    line, a `<` or `<>` that may still become the standalone marker, or a final `>`
    (with the whitespace before it) that may still be an orphan. Everything before
    it is safe to emit.
    """
    core_end = len(buf.rstrip())
    start = core_end
    tail = buf[:core_end]
    for marker in (_MARKER, "<"):
        if tail.endswith(marker):
            before = tail[:len(tail) - len(marker)]
            line_start = before.rstrip(" \t")
            if line_start == "" or line_start.endswith("\n"):
                start = len(before.rstrip())
            break
    else:
        if tail.endswith(_ORPHAN):
            start = len(tail[:-1].rstrip())
    return start


def _strip_trailing_empty_marker_stream(source):
    """Pass a streamed reply through with a standalone trailing ``<>`` line or
    orphaned final ``>`` removed.

    Text that could still turn out to be the marker (trailing whitespace, a line
    holding just `<` / `<>`, or a final `>`) is held back until the next chunk shows it is not,
    or until the stream ends, so the marker is never displayed even briefly. All
    other text passes straight through. Closing this generator closes the source.
    """
    pending = ""
    emitted = []
    decoder = codecs.getincrementaldecoder("utf-8")("replace")  # for byte chunks
    try:
        for chunk in source:
            if isinstance(chunk, (bytes, bytearray)):
                chunk = decoder.decode(bytes(chunk))
            pending += chunk
            cut = _hold_start(pending)
            out, pending = pending[:cut], pending[cut:]
            if out:
                emitted.append(out)
                yield out
        pending += decoder.decode(b"", final=True)
        if pending:
            head = "".join(emitted)
            final = strip_trailing_empty_marker(head + pending)
            # `final` can only shorten the tail that was held back.
            rest = final[len(head):]
            if rest:
                yield rest
    finally:
        close = getattr(source, "close", None)
        if close:
            close()


@wraps(_strip_trailing_empty_marker_stream)
def strip_trailing_empty_marker_stream(source):
    # Reasoning (between the stream's THINK sentinels) is passed through
    # untouched; the cleaner only ever sees the visible answer.
    return reasoning_isolated(_strip_trailing_empty_marker_stream)(source)

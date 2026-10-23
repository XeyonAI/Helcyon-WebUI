"""One ordered response-processing pipeline for /chat (migration Stage 2).

Before this, nine ``Response(stream_with_context(...))`` sites in ``chat()`` each
hand-built their own stack of output filters, so the stack depended on the
route a turn happened to take (the chat-search stream, for instance, had no
OOC or document guard at all). Every site now builds its stream through
``ResponsePipeline``, which applies the same stages in the same order and
simply skips the ones a turn does not need.

Inner stages run first:

    intent_echo     drop a verbatim echo of this turn's Response Intent
    governor_echo   drop an echo of the native Global Post-History governor
    ooc             drop bracketed [OOC ...] / [END ...] packet echoes
    document_guard  send the document-tool confirmation first; imitation filter;
                    fallback reply if the filters emptied the reply

The stage callables stay where their state lives (``app.py``); this module owns
the order, the skipping and the clean-up, and has no dependency on Flask.

Not part of this object, by design: ``@app.after_request`` finishes every /chat
response with ``OUTER_STAGES`` (continuation echo trim, trailing ``<>``/``>``
cleanup, then the reaction selector and marker stripper), so those too apply
identically to every route. They are listed here so the full order is in one place.
"""

import codecs

# Must stay identical to THINK_OPEN / THINK_CLOSE in app.py (a test pins this).
THINK_OPEN = "THINK"
THINK_CLOSE = "/THINK"

STAGE_ORDER = ("intent_echo", "governor_echo", "ooc", "document_guard")
# Text cleaners that must never see reasoning (see reasoning_isolated).
REASONING_ISOLATED_STAGES = ("intent_echo", "governor_echo", "ooc")
OUTER_STAGES = ("continuation_trim", "trailing_marker", "reaction_decision_and_marker_strip")


class _SentinelScanner:
    """Split a chunk stream into text and reasoning-sentinel events.

    A sentinel split across chunks is reassembled; only a tail that could still
    become a sentinel is held back, so ordinary text is never delayed.
    """

    def __init__(self, source):
        self._it = iter(source)
        self._buf = ""
        self._events = []
        self.done = False
        # Byte chunks (an encoded Flask body) are decoded the way the stages do.
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")

    def _hold(self, text):
        best = 0
        for token in (THINK_OPEN, THINK_CLOSE):
            for k in range(min(len(text), len(token) - 1), best, -1):
                if text.endswith(token[:k]):
                    best = k
                    break
        return best

    def _fill(self):
        while not self._events and not self.done:
            try:
                chunk = next(self._it)
            except StopIteration:
                self.done = True
                self._buf += self._decoder.decode(b"", final=True)
                if self._buf:
                    self._events.append(("text", self._buf))
                    self._buf = ""
                break
            if isinstance(chunk, (bytes, bytearray)):
                chunk = self._decoder.decode(bytes(chunk))
            self._buf += chunk
            while True:
                hits = [(self._buf.find(tok), kind, tok) for kind, tok in
                        (("open", THINK_OPEN), ("close", THINK_CLOSE)) if tok in self._buf]
                if not hits:
                    break
                at, kind, tok = min(hits)
                if at:
                    self._events.append(("text", self._buf[:at]))
                self._events.append((kind, tok))
                self._buf = self._buf[at + len(tok):]
            keep = self._hold(self._buf)
            ready = self._buf[:len(self._buf) - keep] if keep else self._buf
            if ready:
                self._events.append(("text", ready))
            self._buf = self._buf[len(ready):]

    def peek(self):
        self._fill()
        return self._events[0] if self._events else None

    def pop(self):
        self._fill()
        return self._events.pop(0) if self._events else None


class _AnswerSegment:
    """Iterator over answer text up to (not including) the next sentinel."""

    def __init__(self, scanner):
        self._scanner = scanner

    def __iter__(self):
        return self

    def __next__(self):
        event = self._scanner.peek()
        if event is None or event[0] != "text":
            raise StopIteration
        return self._scanner.pop()[1]


def reasoning_isolated(stage):
    """Make a text-cleaning stage ignore reasoning.

    Reasoning travels inside THINK_OPEN ... THINK_CLOSE sentinels. A cleaner
    that holds text back (an unclosed ``[OOC:``, a partial marker) must not
    see them: it would swallow the closing sentinel and the answer. The stage
    sees only answer text, a fresh instance per answer segment (so anything it
    holds is released at the segment boundary); reasoning and the sentinels
    pass through untouched, in order. With no sentinels the stage behaves
    exactly as it did unwrapped.
    """

    def wrapped(source):
        scanner = _SentinelScanner(source)
        active = None
        try:
            while True:
                event = scanner.peek()
                if event is None:
                    return
                if event[0] == "text":
                    active = stage(_AnswerSegment(scanner))
                    yield from active
                    active = None
                    continue
                scanner.pop()
                yield event[1]
                if event[0] == "open":
                    while True:
                        inner = scanner.pop()
                        if inner is None:
                            return
                        if inner[0] == "close":
                            yield inner[1]
                            break
                        yield inner[1]
        finally:
            for closable in (active, source):
                close = getattr(closable, "close", None)
                if close:
                    close()

    return wrapped


class ResponsePipeline:
    """Compose the ordered output stages around a reply stream.

    ``stages`` maps a name from ``STAGE_ORDER`` to a callable that takes a
    stream (an iterable of text chunks) and returns a stream, or ``None`` to
    skip it. Unknown names are rejected so a typo cannot silently drop a filter.
    """

    def __init__(self, **stages):
        unknown = sorted(set(stages) - set(STAGE_ORDER))
        if unknown:
            raise TypeError(f"unknown response stage(s): {', '.join(unknown)}")
        self.stages = {name: stages.get(name) for name in STAGE_ORDER}

    def active(self):
        """Names of the stages that will run, in order."""
        return [name for name in STAGE_ORDER if self.stages[name] is not None]

    def wrap(self, source):
        """Return the processed stream. Lazy: nothing is pulled until it is iterated.

        Closing the returned generator closes every stage and the source.
        """
        stream = source
        for name in STAGE_ORDER:
            stage = self.stages[name]
            if stage is not None:
                if name in REASONING_ISOLATED_STAGES:
                    stage = reasoning_isolated(stage)
                stream = stage(stream)
        return self._deliver(stream, source)

    @staticmethod
    def _deliver(stream, source):
        try:
            yield from stream
        finally:
            for closable in (stream, source):
                close = getattr(closable, "close", None)
                if close:
                    close()

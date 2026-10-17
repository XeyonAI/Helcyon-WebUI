"""Continue support for transports that cannot pre-fill the assistant turn.

The raw ChatML /completion path resumes a reply by ending the prompt with the
reply's tail (`continue_prefix`). Chat-message transports (OpenAI, Anthropic,
LM Studio/Ollama-style providers, llama.cpp /v1/chat/completions for Ministral,
Gemma and Qwen) have no such hook, so the request must be expressed as messages:
the partial reply as an assistant turn plus a short instruction to carry on.

`continuation_turns` builds those two messages and `trim_continuation_echo`
tidies the start of the streamed answer, because a model asked to carry on often
re-types the last few words and may omit the space that joins it to the old text.
"""

import codecs

CONTINUE_INSTRUCTION = (
    "[OOC: Your previous reply was cut off before it finished. Continue it from "
    "exactly where it stopped. Write only the next words of that reply: no "
    "preamble, no apology, and do not repeat, restart or summarise anything "
    "already written. If the last word is unfinished, complete it; otherwise "
    "begin with the next word.]"
)

_ECHO_TAIL_CHARS = 120
_ECHO_MIN_CHARS = 12
_CLOSERS = ".!?,;:)]}\"'”’"


def continuation_turns(head, prefix, neutralize=lambda text: text):
    """Messages that ask the model to carry on a partial assistant reply."""
    partial = neutralize(((head or "") + (prefix or "")).rstrip())
    return [
        {"role": "assistant", "content": partial},
        {"role": "user", "content": CONTINUE_INSTRUCTION},
    ]


def _trim_echo(text, tail):
    """Drop a leading repeat of the end of the existing reply, if there is one."""
    body = text.lstrip()
    for size in range(min(len(tail), len(body)), _ECHO_MIN_CHARS - 1, -1):
        if body.startswith(tail[-size:]):
            return body[size:]
    return text


def _joiner(prefix, text):
    """A space when the old text ended a word or sentence and the new text starts a word."""
    if not prefix or not text or prefix[-1].isspace() or text[0].isspace():
        return ""
    if not text[0].isalnum():
        return ""
    if prefix[-1] in _CLOSERS:
        return " "
    if prefix[-1].isalnum() and text[0].isupper():
        return " "
    return ""


def trim_continuation_echo(source, prefix):
    """Pass a continuation stream through, fixing only its first few characters.

    Output is held back until it is long enough to tell whether it opens by
    repeating the end of `prefix`; everything after that streams straight through.
    Closing this generator closes the source.
    """
    tail = (prefix or "").rstrip()[-_ECHO_TAIL_CHARS:]
    hold = min(len(tail), _ECHO_TAIL_CHARS) + 8
    pending = ""
    settled = False
    decoder = codecs.getincrementaldecoder("utf-8")("replace")  # for byte chunks
    try:
        for chunk in source:
            if isinstance(chunk, (bytes, bytearray)):
                chunk = decoder.decode(bytes(chunk))
            if settled:
                yield chunk
                continue
            pending += chunk
            if len(pending) < hold:
                continue
            settled = True
            text = _trim_echo(pending, tail)
            text = _joiner(prefix, text) + text
            if text:
                yield text
            pending = ""
        if not settled and pending:
            text = _trim_echo(pending, tail)
            yield _joiner(prefix, text) + text
    finally:
        close = getattr(source, "close", None)
        if close:
            close()

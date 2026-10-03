"""Independent reaction metadata before replies; no chat-text rewriting."""
import json
import re


REACTION_CHOICES = {
    "none": None, "laugh": "😂", "love": "❤️", "like": "👍",
    "dislike": "👎", "surprise": "😮", "sad": "😢",
}
REACTION_DECISION_INSTRUCTION = (
    "You decide whether the user's latest message should get an emoji reaction, like a "
    "person reacting to a message in a chat app. Most messages, including most emotional "
    "ones, get no reaction. First rate the message's significance. routine: questions, "
    "requests, instructions, technical or practical talk, plans, everyday updates, "
    "opinions, thanks, greetings and warmth, ordinary good news, enthusiasm, routine "
    "jokes and banter, mild complaints or frustration, swearing, emojis, and passing "
    "feelings. notable: a clear emotion or event that is still ordinary. strong: rare. "
    "Only a genuinely striking moment where a thoughtful friend would react with an emoji "
    "instead of just replying in words: major life or world news, a serious loss or "
    "crisis, an exceptionally funny or absurd event, a major emotional disclosure, "
    "intense anger, or deep heartfelt affection. Emotion, humour or a swear word alone is "
    "never enough; the moment itself must be striking. A reaction marks a moment, not a "
    "mood. If a recent message already expressed a similar emotion or humour, whether or "
    "not it received a reaction (a tag such as [you reacted X] marks one that did), rate "
    "the latest message routine unless it is clearly bigger or a distinct new event. Once "
    "the conversation has moved on to other things, a fresh striking moment can be rated "
    "strong again. Then give the reaction that fits a strong message: laugh (an "
    "exceptionally funny or absurd moment), love (deep affection or heartfelt gratitude), "
    "like (a major achievement or celebration, never bad news), surprise (an astonishing "
    "or unbelievable event, good or bad), sad (grief, loss or bad news), dislike (intense "
    "anger or outrage). For routine and notable messages the reaction is none. Output "
    "only JSON."
)
RECENT_CONTEXT_MESSAGES = 10
RECENT_CONTEXT_CHARS = 300
# Significance comes first so the model rates the moment before it names a
# reaction; only "strong" can produce one (parse_reaction_decision).
REACTION_SIGNIFICANCE = ("routine", "notable", "strong")
REACTION_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "significance": {"type": "string", "enum": list(REACTION_SIGNIFICANCE)},
        "reaction": {"type": "string", "enum": list(REACTION_CHOICES)},
    },
    "required": ["significance", "reaction"], "additionalProperties": False,
}
_MARKER_RE = re.compile(r"<!--[ \t]*HWUI_REACTION[ \t]*:[ \t]*([\s\S]*?)[ \t]*-->", re.I)
_OPEN_MARKER_RE = re.compile(r"<!--[ \t]*HWUI_REACTION[ \t]*:", re.I)
# Match one emoji cluster (including flags, modifiers, keycaps and ZWJ groups).
# The browser remains authoritative and accepts arbitrary reaction emojis.
_PICTOGRAPH = r"(?![\U0001F3FB-\U0001F3FF])[\U0001F300-\U0001FAFF\u2600-\u27BF©®‼⁉™ℹ〰〽㊗㊙]"
_PIECE = _PICTOGRAPH + r"(?:\uFE0F|[\U0001F3FB-\U0001F3FF])?"
_EMOJI_RE = re.compile(r"^(?:[\U0001F1E6-\U0001F1FF]{2}|[0-9#*]\uFE0F?\u20E3|"
                       + _PIECE + r"(?:\u200D" + _PIECE + r")*)$")
_THINK_RE = re.compile(
    r"\x02\x02THINK\x02\x02[\s\S]*?(?:\x02\x02/THINK\x02\x02|$)"
    r"|<think>[\s\S]*?(?:</think>|$)|\[THINK\][\s\S]*?(?:\[/THINK\]|$)", re.I
)


def _recent_line(item):
    text = " ".join(str(item.get("text") or "").split())
    if len(text) > RECENT_CONTEXT_CHARS:
        text = text[:RECENT_CONTEXT_CHARS].rstrip() + "..."
    speaker = "Assistant" if item.get("role") == "assistant" else "User"
    reaction = item.get("reaction")
    tag = f" [you reacted {reaction}]" if reaction and item.get("role") != "assistant" else ""
    return f"{speaker}{tag}: {text}"


def reaction_decision_messages(user_text, recent=None):
    """Decision request. `recent` is optional context only: dicts with role, text
    and (for user messages) the reaction already given, oldest first. Without it
    the request is exactly the latest user message."""
    lines = [_recent_line(item) for item in (recent or [])[-RECENT_CONTEXT_MESSAGES:]
             if isinstance(item, dict) and str(item.get("text") or "").strip()]
    if lines:
        reacted = sum(1 for item in (recent or [])[-RECENT_CONTEXT_MESSAGES:]
                      if item.get("reaction") and item.get("role") != "assistant"
                      and str(item.get("text") or "").strip())
        # The continuity cue sits next to the message being decided: far up in the
        # system text the model ignores it.
        user_text = ("Recent conversation, oldest first:\n" + "\n".join(lines)
                     + "\n\nReactions already given among the recent messages above: %d. "
                     "If the latest message continues a situation, feeling or joke already shown "
                     "above, it is routine.\nLatest user message (decide only for this one):\n" % reacted
                     + user_text)
    return [{"role": "system", "content": REACTION_DECISION_INSTRUCTION},
            {"role": "user", "content": user_text}]


def parse_reaction_decision(raw):
    """Reject invalid/truncated decisions instead of guessing an emotion.

    Only a "strong" significance may carry a reaction; routine and notable
    messages are none whatever label came with them.
    """
    decision = json.loads(raw)
    if not isinstance(decision, dict) or set(decision) != {"significance", "reaction"}:
        raise ValueError("Invalid reaction decision shape")
    significance, choice = decision["significance"], decision["reaction"]
    if not isinstance(significance, str) or significance not in REACTION_SIGNIFICANCE:
        raise ValueError("Invalid reaction decision significance")
    if not isinstance(choice, str) or choice not in REACTION_CHOICES:
        raise ValueError("Invalid reaction decision choice")
    return REACTION_CHOICES[choice] if significance == "strong" else None


def complete_reaction_stream(source, decide, cancelled=lambda: False, trace=lambda **kw: None):
    """Decide from the user message before advancing the lazy reply iterator.

    A leading 'none' marker makes an explicit abstention authoritative too.
    Every original provider chunk then passes through without buffering.
    Closing the consumer also closes the original provider iterator.
    """
    try:
        if cancelled():
            trace(source="cancelled", reaction=None)
            return
        try:
            reaction = decide()
        except Exception as exc:
            trace(source="decision_error", reaction=None, error=type(exc).__name__)
        else:
            if cancelled():
                trace(source="cancelled", reaction=None)
                return
            trace(source="decision", reaction=reaction)
            yield "<!-- HWUI_REACTION:" + (reaction or "none") + " -->"
        if cancelled():
            return
        for chunk in source:
            if cancelled():
                return
            yield chunk
    finally:
        close = getattr(source, "close", None)
        if close:
            close()


_MARKER_PREFIX = "<!--hwui_reaction:"
_MARKER_MAX_CHARS = 200   # a longer "marker" is ordinary text, not held back forever


def _may_be_marker(text):
    """True while `text` (starting at a '<') could still turn into a reaction marker."""
    if len(text) > _MARKER_MAX_CHARS:
        return False
    squashed = re.sub(r"[ \t]+", "", text.lower())
    return _MARKER_PREFIX.startswith(squashed) or bool(_OPEN_MARKER_RE.match(text))


def strip_reaction_markers(source):
    """Pass a reply through with every <!-- HWUI_REACTION:... --> marker removed.

    Used when message reactions are switched off, so a marker the chat model emits
    on its own can never become a reaction. Markers split across chunks are held
    back only while they could still be one; all other text passes straight through.
    Whitespace right after a marker at the very start of the reply is dropped too,
    as the browser parser already does. Closing this generator closes the source.
    """
    pending = ""
    started = False       # any visible text emitted yet
    skip_space = False    # a leading marker was just removed
    try:
        for chunk in source:
            pending += chunk
            out = ""
            while True:
                match = _MARKER_RE.search(pending)
                if match:
                    out += pending[:match.start()]
                    pending = pending[match.end():]
                    if not (started or out):
                        skip_space = True
                    continue
                hold = next((i for i in range(len(pending))
                             if pending[i] == "<" and _may_be_marker(pending[i:])), None)
                if hold is None:
                    out += pending
                    pending = ""
                else:
                    out += pending[:hold]
                    pending = pending[hold:]
                break
            if skip_space and out:
                out = out.lstrip()
            if out:
                skip_space = False
                started = True
                yield out
        if pending and not _OPEN_MARKER_RE.match(pending):
            yield pending.lstrip() if skip_space else pending
    finally:
        close = getattr(source, "close", None)
        if close:
            close()

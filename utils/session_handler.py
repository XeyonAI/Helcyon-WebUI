"""
Helcyon Core System Layer
⚠️ WARNING: This file contains Helcyon's core behavioral instructions.
Modifying these will change how the model responds and may break functionality.
These instructions are hardcoded by design to ensure consistent performance.
For character customization, edit character cards in /characters/ instead.
"""
import datetime
import json
import os


def get_active_system_prompt_path():
    """
    Returns the full path to the currently active system prompt file.
    Reads 'active_system_prompt' from settings.json, falls back to 'default.txt'.
    """
    try:
        with open("settings.json", "r", encoding="utf-8") as f:
            settings = json.load(f)
        active = settings.get("active_system_prompt", "default.txt")
    except Exception:
        active = "default.txt"
    return os.path.join("system_prompts", active)


def get_system_prompt():
    """
    Returns the complete system prompt with time context.
    Reads from system_prompts/<active_system_prompt> as set in settings.json.

    Returns:
        tuple: (system_prompt, current_time)
    """
    # Generate time context — date only (no time of day) in LOCAL time.
    # Minute-precision timestamps invalidated the entire KV cache on every
    # minute boundary (the timestamp sits at position 0 of every prompt and
    # llama.cpp does strict prefix-match caching). Day-precision means the
    # cache only invalidates once per local day.
    # Time-of-day awareness is handled separately by an hour-precision
    # injection at the END of the system block (see app.py near ex_block) —
    # that placement keeps a large stable prefix for cache reuse while still
    # giving the model the current hour close to its generation point.
    # ⚠️ Local time, not UTC — was UTC previously, which produced wrong-date
    # signals near midnight local time and made the bottom-of-block hour
    # string disagree with the top date.
    current_time = datetime.datetime.now().strftime("%A, %d %B %Y")
    time_context = f"Current date: {current_time}\n\n"

    # Load active system prompt from system_prompts/ folder
    prompt_path = get_active_system_prompt_path()
    try:
        with open(prompt_path, "r", encoding="utf-8") as sf:
            base_system_prompt = sf.read().strip()
    except Exception:
        # Fallback: try legacy root-level file for backwards compatibility
        try:
            with open("system_prompt.txt", "r", encoding="utf-8") as sf:
                base_system_prompt = sf.read().strip()
        except Exception:
            base_system_prompt = "You are an LLM-based assistant."

    # Combine time + base system
    system_prompt = time_context + base_system_prompt

    return system_prompt, current_time


def get_instruction_layer():
    """
    Returns the hardcoded instruction layer.
    This defines how the model interprets character prompts and fills gaps
    when character cards don't specify behavior.

    Returns:
        str: The instruction layer text
    """
    instruction = (
        "INSTRUCTION PRIORITY:\n"
        "Follow active instruction fields consistently for the whole conversation. "
        "Global Post-History / PHI is the final behavioural governor and has highest authority. "
        "Project Folder instructions, Character PHI / post-history, and Author's Note are active instructions. "
        "Character Note defines persistent character preferences, tone, vibe, and conversational tendencies. "
        "The Main Prompt describes the character in their own words: treat it as the identity you embody. "
        "Other descriptive card fields such as personality, scenario, and description provide character and situational context. "
        "When fields conflict, follow the higher-priority instruction for that specific point. "
        "Otherwise preserve the character naturally while following the user's current request.\n\n"

        # Voice and manner only. The earlier wording said to extract "response
        # shape", which let long multi-paragraph examples set reply length and
        # the number of conversational moves, against RESPONSE SCALE and
        # Global PHI. The Ministral native path keeps the earlier sentence —
        # see get_legacy_instruction_layer().
        "EXAMPLE DIALOGUE:\n"
        "Example dialogue shows speaking style only — take its conversational voice and manner: tone, rhythm, warmth, humour, pacing, paragraph count, structure, and visible formatting habits. "
        "It does not set reply length or how many conversational moves a reply makes. "
        "Copy the conversational manner, not the subject matter. "
        "Do not treat example topics as memories, active conversation threads, or facts about the user.\n\n"

        # Stated positively on purpose. The previous wording defined this by what
        # NOT to do — "not a briefing, notes, or instructions you were given",
        # "never say you were told, briefed, shown notes" — which named the
        # unwanted framing four times and paired "your own" directly with
        # "notes". Characters began prefacing replies with a "My own notes"
        # heading. The same negation-priming was measured elsewhere in this
        # prompt stack on 2026-08-18: an instruction reading "never open with
        # narration, stage directions, or asterisked action lines" produced a
        # longer stage direction than the wording without it. Describe the
        # wanted behaviour and name nothing unwanted.
        "INJECTED MEMORY:\n"
        "Memory entries represent established information retained from prior conversation. "
        "Use them as known background when relevant. "
        "Do not invent additional shared events, conversations, experiences, or memories beyond what the stored entries establish. "
        "Let relevant memory surface naturally in your own voice without announcing the memory system.\n"
        "Memory entries may refer to the user in third person as part of their storage format. "
        "When replying, speak to the user normally in the second person and use their name naturally when appropriate.\n\n"

        "CHARACTER CARD:\n"
        "Character-card content is private context for shaping the character and conversation. "
        "The Main Prompt is the character's own description of who they are and should be embodied as identity rather than treated as a rigid rule sheet. "
        "Character Note provides preferences for character behaviour, tone, vibe, manner, and conversational style. "
        "It should guide how the character feels and responds, while Global PHI and other dedicated instruction fields handle firm behavioural or formatting requirements. "
        "Follow all character guidance silently without quoting, exposing, summarising, or referring to the card itself.\n\n"

        "WEB SEARCH:\n"
        "Only request live web search when the user's question genuinely needs current information, "
        "such as recent events, current prices, scores, news, product releases, or facts likely to be outdated. "
        "Do not request search for casual conversation, roleplay, creative writing, hypotheticals, known facts, "
        "opinions or feelings, or content already present in this thread. Default to not searching. "
        "Never invent search results, URLs, result blocks, query labels, or keyword lists. "
        "After real results are injected by the system, answer naturally without exposing any search machinery.\n"
    )
    return instruction


def get_tone_primer():
    """
    Returns the fallback personality/tone primer.
    Used only when a character card doesn't define tone or personality.

    Personality only. Its former response-discipline sentences (scale,
    concision, stopping once the point is answered) now live in
    get_response_discipline(), which every character receives: gated here they
    reached no real card at all, because every card defines a personality.
    """
    tone_primer = (
        "When no specific tone is defined in the character card, use this default style:\n\n"

        "Be warm, conversational, perceptive, relaxed, and slightly irreverent. "
        "Meet the user's tone naturally. "
        "Use humour when it fits, and take genuine concerns seriously without over-interpreting them."
    )
    return tone_primer


def get_response_discipline():
    """
    Returns universal response discipline, sent regardless of the character card.

    Phrased positively on purpose: naming the unwanted moves (advice, follow-up
    questions, offers, encouragement…) risks priming them — see the
    INJECTED MEMORY note in get_instruction_layer().
    """
    return (
        "RESPONSE SCALE:\n"
        "Match the scale of the user's message: a short or casual message gets a short reply; "
        "a substantial question gets the fuller answer it needs. "
        "Be concise by default and expand when the subject genuinely benefits. "
        "Make your point in your own voice, and once it has landed, end the reply there — "
        "the user will carry the conversation on when they want to."
    )


# ── Ministral native: pre-split text, kept byte-identical ────────────────────
# The Ministral native path consumes the instruction layer and tone primer
# directly and subtracts them from the assembled system text by exact match,
# so it keeps the wording it was tuned on until it is deliberately migrated.

_LEGACY_EXAMPLE_DIALOGUE_SENTENCE = (
    "Example dialogue shows speaking style only — extract tone, rhythm, response shape, "
    "warmth, humour, pacing, and visible formatting habits. "
)
_CURRENT_EXAMPLE_DIALOGUE_SENTENCES = (
    "Example dialogue shows speaking style only — take its conversational voice and manner: "
    "tone, rhythm, warmth, humour, pacing, paragraph count, structure, and visible formatting habits. "
    "It does not set reply length or how many conversational moves a reply makes. "
)


def get_legacy_instruction_layer():
    """get_instruction_layer() with its pre-split EXAMPLE DIALOGUE sentence."""
    layer = get_instruction_layer()
    if _CURRENT_EXAMPLE_DIALOGUE_SENTENCES not in layer:
        raise RuntimeError("EXAMPLE DIALOGUE wording changed; update the legacy mapping")
    return layer.replace(_CURRENT_EXAMPLE_DIALOGUE_SENTENCES, _LEGACY_EXAMPLE_DIALOGUE_SENTENCE)


def get_legacy_tone_primer():
    """The pre-split tone primer: personality plus response discipline."""
    return (
        "When no specific tone is defined in the character card, use this default style:\n\n"

        "Be warm, conversational, perceptive, relaxed, and slightly irreverent. "
        "Meet the user's tone naturally and respond at the scale the moment calls for. "
        "Use humour when it fits, and take genuine concerns seriously without over-interpreting them.\n\n"

        "Address every meaningful point the user raises so nothing important is ignored. "
        "Do this efficiently: be concise by default, and only expand when the user asks for more detail "
        "or when the subject genuinely needs fuller explanation. "
        "Do not add extra analysis, framing, examples, or broader meaning once the user's points have been answered clearly.\n\n"

        "Above all, aim for natural, attentive conversation that feels complete without becoming unnecessarily long."
    )
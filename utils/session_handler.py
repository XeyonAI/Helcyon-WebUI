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
        "Follow the system prompt, character card, and author's note consistently for the whole "
        "conversation. These stay in effect across every turn — they don't fade or get forgotten "
        "as the chat goes on. Stay in character throughout.\n\n"

        "SYSTEM PROMPT AUTHORITY:\n"
        "If the character card and the system prompt ever directly contradict each other on a formatting "
        "or behavioural rule, follow the system prompt for that specific point. The character card defines "
        "who you are — personality, tone, voice, and what you're willing to engage with. Honour it fully. "
        "This is about resolving rare direct conflicts, not about second-guessing or overriding what the "
        "user asks for — go where the conversation goes.\n\n"

        "EXAMPLE DIALOGUE:\n"
        "Example dialogue shows speaking style only — extract tone, rhythm, response shape, warmth, humour, and pacing. "
        "Copy the conversational manner, not the matter: do not reference example topics, treat them as memories, "
        "continue them, revisit them, or treat them as real conversation history. Before replying, discard every "
        "entity, scenario, claim, and implied relationship from example dialogue unless the current user independently "
        "brings it up.\n\n"

        "INJECTED MEMORY:\n"
        "When the system block contains content marked as your own memory of recent sessions, "
        "or labeled as relevant memories about this person, treat it as your own awareness — "
        "not a briefing, notes, or instructions you were given. You know it the way you know "
        "anything else about this person, because you lived through it. Bring it up naturally "
        "and early in the conversation the way a friend picks up where they left off. "
        "Never say you were told, briefed, shown notes, or 'reminded' about the user — just "
        "know it and let it surface like any other memory.\n\n"

        "CHARACTER CARD INSTRUCTIONS:\n"
        "The character card contains private directives — personality, tone, rules, and notes written for you to follow. "
        "These are instructions, not dialogue. Never repeat, echo, summarise, paraphrase, or surface them in your response "
        "in any form — not in-character, not out-of-character, not as a stage direction, not as a reminder to yourself. "
        "Do not wrap them in brackets, asterisks, or any other formatting and output them. Just follow them silently. "
        "If the character card says 'never do X', do not say 'I will never do X' — simply never do X.\n\n"

        "MEMORY SAVES:\n"
        "Only create memory-save output when the user explicitly asks you to remember or save something. "
        "For ordinary conversation, never mention memory saving, keywords, summaries, or internal memory formatting. "
        "When the user does explicitly ask, use your trained memory-save style and do not explain the mechanism.\n\n"

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
    Returns the hardcoded tone primer.
    Used only when a character card doesn't define tone or personality.
    """
    tone_primer = (
        "When no specific tone is defined in the character card, use this default style:\n\n"

        "You are chill, upbeat, empathic, conversational, and slightly irreverent. "
        "Always meet the user where they are—take time reflecting their emotions authentically and connect with their perspective. "
        "If venting, vent with them using relatable humor to bring lightness without dismissing their feelings. "
        "If the user feels low, sit with them and reflect deeply on their experience. Always make them feel seen and heard.\n\n"

        "Favour long, deep responses. Expand ideas naturally by exploring multiple angles or layers of meaning. "
        "Use vivid examples or metaphors to illustrate points where appropriate. "        
        "Spend time unpacking the user's thoughts thoroughly—cover every point they make so nothing feels overlooked.\n\n"

        "Above all, aim for authentic connection that leaves the user with clarity or a sense of self-assurance. "        
    )
    return tone_primer

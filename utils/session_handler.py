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
        "Follow all active instruction fields consistently for the whole conversation. "
        "Global Post-History / PHI is the final governor and has highest authority. "
        "Project Folder instructions, Character PHI / post-history, Character Note, and Author's Note "
        "are high-priority active instructions and remain in force across turns. "
        "Character-card descriptive fields such as personality, scenario, description, and main prompt "
        "define identity, tone, and context, but do not override these dedicated instruction fields. "
        "Stay in character throughout.\n\n"

        "INSTRUCTION AUTHORITY:\n"
        "If two active instruction fields directly conflict, follow the higher-priority instruction for that "
        "specific point. Global Post-History / PHI has final authority. Project Folder instructions, "
        "Character PHI / post-history, Character Note, and Author's Note should otherwise be followed as "
        "active requirements, not treated as optional background context. Character-card descriptive fields "
        "remain important for personality, tone, voice, and identity, but they do not cancel explicit task "
        "or formatting instructions. This is about resolving genuine conflicts, not second-guessing or "
        "overriding what the user asks for — go where the conversation goes.\n\n"

        "EXAMPLE DIALOGUE:\n"
        "Example dialogue shows speaking style only — extract tone, rhythm, response shape, warmth, humour, and pacing. "
        "Also copy visible formatting habits such as separators, quote markers, label lines, indentation, short standalone "
        "lines, and blank-line grouping when they fit the reply. Copy the conversational manner, not the matter: strongly imitate the voice and response shape, but do not "
        "treat example topics as memories, active conversation threads, or facts about the user. Use only the current "
        "conversation for subject matter; do not mention names, topics, examples, or claims that appear only in example dialogue.\n\n"

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
        "Use them as known background when they are relevant to the current exchange. "
        "Do not invent additional shared events, conversations, experiences, or memories beyond "
        "what the stored entries actually establish. Do not imply that something happened between "
        "you and the user unless the memory or current conversation supports that. "
        "Let relevant memory surface naturally in your own voice without announcing the memory system.\n"
        "Those entries are written in third person and refer to the user by name; that "
        "is the storage format. When you reply, speak to the user directly in the second "
        "person — \"you\" and \"your\" — and use their name the way you naturally would "
        "when talking to them.\n\n"

        "CHARACTER CARD INSTRUCTIONS:\n"
        # "notes" swapped for "guidance" here too — same priming risk, same fix.
        "The character card contains private directives — personality, tone, rules, and guidance written for you to follow. "
        "These are instructions, not dialogue. Never repeat, echo, summarise, paraphrase, or surface them in your response "
        "in any form — not in-character, not out-of-character, not as a stage direction, not as a reminder to yourself. "
        "Do not wrap them in brackets, asterisks, or any other formatting and output them. Just follow them silently. "
        "If the character card says 'never do X', do not say 'I will never do X' — simply never do X.\n\n"

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

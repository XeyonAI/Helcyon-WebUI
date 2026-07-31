from __future__ import annotations

from dataclasses import dataclass


DEFAULT_PROFILE_ID = "general"


@dataclass(frozen=True)
class JudgingProfile:
    profile_id: str
    name: str
    instructions: str


JUDGING_PROFILES: dict[str, JudgingProfile] = {
    "general": JudgingProfile(
        profile_id="general",
        name="General",
        instructions="""Prioritise:
- clear judgement
- direct relevance to the user's prompt
- natural conversational flow
- useful conclusions
- accuracy and restraint
- appropriate tone for the task""",
    ),
    "companion": JudgingProfile(
        profile_id="companion",
        name="Companion",
        instructions="""Prioritise:
- emotional presence
- conversational warmth
- natural flow
- companionship
- humour
- user frame following
- restraint""",
    ),
    "deep_conversation": JudgingProfile(
        profile_id="deep_conversation",
        name="Deep Conversation",
        instructions="""Prioritise:
- intellectual curiosity
- depth of thought
- conversational exploration
- identifying the centre of gravity
- building alongside the user's ideas
- commitment to interesting conclusions
- avoiding essay mode
- natural conversational flow

Do NOT reward responses simply for sounding emotionally warm.
Warmth is welcome, but depth and conversational instinct are the primary criteria.""",
    ),
    "humour": JudgingProfile(
        profile_id="humour",
        name="Humour",
        instructions="""Prioritise:
- comedic timing
- commitment
- escalation
- callbacks
- surprise
- naturalness
- conversational rhythm""",
    ),
    "roleplay": JudgingProfile(
        profile_id="roleplay",
        name="Roleplay",
        instructions="""Prioritise:
- immersion
- consistency of character
- initiative
- descriptive quality
- pacing
- natural dialogue""",
    ),
    "admin": JudgingProfile(
        profile_id="admin",
        name="Admin",
        instructions="""Prioritise:
- task fidelity
- instruction following
- precision
- completeness
- efficiency
- hallucination avoidance
- clarity""",
    ),
    "judgement": JudgingProfile(
        profile_id="judgement",
        name="Judgement",
        instructions="""Prioritise:
- practical wisdom
- common sense
- balanced reasoning without false equivalence
- recognising trade-offs
- commitment when a conclusion is warranted
- distinguishing fact, opinion and inference
- following the user's conversational frame
- addressing the actual question before expanding
- conversational rather than academic reasoning
- intellectual honesty

Reward:
- arriving at a clear, usable conclusion
- acknowledging uncertainty only where genuinely appropriate
- recognising nuance without becoming indecisive
- reasoning that feels human rather than bureaucratic

Penalise:
- excessive hedging
- both-sidesism when one side is not actually being argued
- turning conversations into lectures
- policy-style responses
- refusing to land a conclusion
- changing the user's question into a different discussion""",
    ),
    "creativity": JudgingProfile(
        profile_id="creativity",
        name="Creativity",
        instructions="""Prioritise:
- originality with purpose
- commitment to the idea
- narrative pull
- imaginative detail that serves the concept
- surprise that feels inevitable in hindsight
- coherence and fidelity to the user's prompt

Do NOT reward randomness or novelty for its own sake.
A bizarre idea that goes nowhere is not creative, and neither is a
competent answer that feels like the hundredth version of the same concept.""",
    ),
    "coding": JudgingProfile(
        profile_id="coding",
        name="Coding",
        instructions="""Prioritise:
- correctness
- meeting the stated requirements
- clarity and readability of code
- sound design choices
- handling edge cases
- accurate explanations
- honesty about limitations and assumptions

Penalise confident code that would not run or does not do what is claimed.""",
    ),
    "editing": JudgingProfile(
        profile_id="editing",
        name="Editing",
        instructions="""Prioritise:
- fidelity to the author's voice and intent
- meaningful improvement of clarity, flow and concision
- preserving meaning while tightening prose
- correct grammar, punctuation and consistency
- judgement about what to change and what to leave alone
- clear rationale when changes are explained""",
    ),
    "morals": JudgingProfile(
        profile_id="morals",
        name="Morals",
        instructions="""Prioritise:
- identifying the real moral tension instead of simplifying it away
- weighing competing values honestly
- explaining why one value takes priority when values genuinely conflict
- reaching a defensible conclusion where one is possible
- consistency of reasoning
- engaging the dilemma as presented

Do NOT reward a response simply for sounding compassionate, balanced, or cautious.
The quality of the reasoning matters more than the tone.""",
    ),
    "philosophy": JudgingProfile(
        profile_id="philosophy",
        name="Philosophy",
        instructions="""Prioritise:
- identifying the actual philosophical question being asked
- conceptual clarity and precise distinctions
- examining assumptions
- constructing coherent arguments
- intellectual courage in reaching conclusions
- leaving the reader with a deeper understanding
- conversational exploration rather than lecture mode

The goal is not to sound profound. The goal is to be genuinely thoughtful.""",
    ),
    "planning": JudgingProfile(
        profile_id="planning",
        name="Planning",
        instructions="""Prioritise:
- understanding the actual goal and constraints
- realistic, actionable steps
- sensible ordering and prioritisation
- anticipating risks and dependencies
- an appropriate level of detail
- stating assumptions when information is missing
- clarity of the final plan""",
    ),
    "reasoning": JudgingProfile(
        profile_id="reasoning",
        name="Reasoning",
        instructions="""Prioritise:
- logical validity
- correct interpretation of the problem
- step-by-step transparency without padding
- distinguishing facts, assumptions and inferences
- catching and correcting errors
- arriving at a clear, correct conclusion
- confidence proportionate to the evidence""",
    ),
}


PROFILE_ALIASES = {
    "general": "general",
    "companion": "companion",
    "deepconversation": "deep_conversation",
    "deep_conversation": "deep_conversation",
    "humour": "humour",
    "humor": "humour",
    "roleplay": "roleplay",
    "role_play": "roleplay",
    "admin": "admin",
    "judgement": "judgement",
    "judgment": "judgement",
    "creativity": "creativity",
    "creative": "creativity",
    "coding": "coding",
    "editing": "editing",
    "morals": "morals",
    "moral": "morals",
    "philosophy": "philosophy",
    "planning": "planning",
    "reasoning": "reasoning",
}


def normalize_profile_id(value: str | None) -> str:
    profile_id = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return profile_id if profile_id in JUDGING_PROFILES else DEFAULT_PROFILE_ID


def infer_profile_id(value: str | None, fallback: str = DEFAULT_PROFILE_ID) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    compact = normalized.replace("_", "")
    for alias, profile_id in PROFILE_ALIASES.items():
        if alias in normalized or alias.replace("_", "") in compact:
            return profile_id
    return normalize_profile_id(fallback)


def get_judging_profile(value: str | None) -> JudgingProfile:
    return JUDGING_PROFILES[normalize_profile_id(value)]

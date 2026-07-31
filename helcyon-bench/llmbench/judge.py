from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any

from llmbench.api import ApiError, ChatCompletionResult, chat_completion, debug_logging_enabled
from llmbench.config import (
    AppConfig,
    DEFAULT_CLEAR_WINNER_THRESHOLD,
    DEFAULT_EFFECTIVE_TIE_THRESHOLD,
    JudgeEndpointConfig,
)


class JudgeError(Exception):
    """Raised when judging fails or the judge output is invalid."""

    def __init__(
        self,
        message: str,
        raw_response: str | None = None,
        status_code: int | None = None,
    ):
        super().__init__(message)
        self.raw_response = raw_response
        self.status_code = status_code


JUDGE_LOG_PATH = Path(__file__).resolve().parent.parent / "judge_logs" / "judge_io.jsonl"
JUDGE_PIPELINE_DEBUG_PATH = Path(__file__).resolve().parent.parent / "judge_logs" / "judge_pipeline_debug.jsonl"


CATEGORY_WEIGHTS = {
    "Emotional Presence": 1.0,
    "Conversation Flow": 1.0,
    "Evidence Discipline": 1.0,
    "User Frame Following": 1.0,
    "Humour": 1.0,
    "Restraint": 1.0,
}

OPTIONAL_CATEGORY_WEIGHTS = {
    "Creativity": 1.0,
    "Philosophical Depth": 1.0,
    "Moral Reasoning": 1.0,
    "Uncensored": 1.0,
}

# --- Result classification --------------------------------------------------
#
# Every comparison's absolute overall-score gap is classified into exactly one
# of these three buckets. This replaces a single always-reports-a-winner
# binary with a middle ground for gaps too small to read as a clean win, while
# still keeping the directional lean (winner.response) available separately -
# see calculate_winner() below.
CLASSIFICATION_CLEAR_WINNER = "clear_winner"
CLASSIFICATION_NARROW_PREFERENCE = "narrow_preference"
CLASSIFICATION_EFFECTIVE_TIE = "effective_tie"

CLASSIFICATION_LABELS = {
    CLASSIFICATION_CLEAR_WINNER: "Clear Winner",
    CLASSIFICATION_NARROW_PREFERENCE: "Narrow Preference",
    CLASSIFICATION_EFFECTIVE_TIE: "Effective Tie",
}

# Default thresholds live in llmbench.config so they can be set from
# config.yaml; these names are what the rest of this module and its callers
# use, so a config override only has to happen in one place (see
# llmbench.protocol.judge_comparison_bidirectional).
EFFECTIVE_TIE_THRESHOLD = DEFAULT_EFFECTIVE_TIE_THRESHOLD
CLEAR_WINNER_THRESHOLD = DEFAULT_CLEAR_WINNER_THRESHOLD

# A gap this small is treated as exact numeric equality (floating-point noise
# from averaging/rounding) rather than a meaningful effective tie. This is
# deliberately much smaller than EFFECTIVE_TIE_THRESHOLD: it only exists to
# stop 8.00 vs 8.0000001 from reporting a directional lean that doesn't exist.
EXACT_TIE_EPSILON = 0.01


def classify_score_gap(
    gap: float,
    effective_tie_threshold: float = EFFECTIVE_TIE_THRESHOLD,
    clear_winner_threshold: float = CLEAR_WINNER_THRESHOLD,
) -> str:
    """Classify an absolute overall-score gap into one of the three buckets.

    Boundaries are inclusive on the side that favours the more conservative
    reading: a gap exactly at effective_tie_threshold is still a tie, and a
    gap exactly at clear_winner_threshold already counts as a clear win.
    """
    gap = abs(gap)
    if gap <= effective_tie_threshold:
        return CLASSIFICATION_EFFECTIVE_TIE
    if gap >= clear_winner_threshold:
        return CLASSIFICATION_CLEAR_WINNER
    return CLASSIFICATION_NARROW_PREFERENCE

COMPARISON_FIELDS = (
    "more_natural",
    "better_frame_following",
    "stronger_emotional_presence",
    "better_evidence_discipline",
    "better_conclusion",
    "more_enjoyable",
    "weaknesses",
)

APPLICABLE = "applicable"
NOT_APPLICABLE = "not_applicable"

APPLICABILITY_KEY = "criteria_applicability"

# Judges phrase "no" in many ways. Anything in this set marks a criterion as not
# applicable for BOTH responses; anything else is treated as applicable.
NOT_APPLICABLE_TOKENS = {
    "not_applicable",
    "notapplicable",
    "inapplicable",
    "n/a",
    "n_a",
    "na",
    "none",
    "null",
    "no",
    "false",
    "0",
}

NOT_APPLICABLE_LABEL = "N/A"


def scoring_categories(extra_categories: list[str] | None = None) -> dict[str, float]:
    weights = dict(CATEGORY_WEIGHTS)
    for category in extra_categories or []:
        if category not in OPTIONAL_CATEGORY_WEIGHTS:
            raise JudgeError(
                f"Unknown optional score category: {category}. "
                f"Known optional categories: {', '.join(OPTIONAL_CATEGORY_WEIGHTS)}."
            )
        weights[category] = OPTIONAL_CATEGORY_WEIGHTS[category]
    return weights


def category_properties(value_schema: dict[str, Any], categories: list[str] | None = None) -> dict[str, Any]:
    return {category: value_schema for category in (categories or list(CATEGORY_WEIGHTS))}


JUDGE_STRING_DESCRIPTION = (
    "Use one complete sentence or phrase. Do not use quotation marks inside JSON string values; "
    "paraphrase referenced words or phrases instead of quoting them."
)


def judge_output_schema(weights: dict[str, float] | None = None) -> dict[str, Any]:
    categories = list(weights or CATEGORY_WEIGHTS)
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [APPLICABILITY_KEY, "responses", "comparison", "final_verdict"],
        "properties": {
            APPLICABILITY_KEY: {
                "type": "object",
                "additionalProperties": False,
                "required": categories,
                "description": (
                    "One applicability decision per criterion, applied identically to both responses."
                ),
                "properties": category_properties(
                    {"type": "string", "enum": [APPLICABLE, NOT_APPLICABLE]}, categories
                ),
            },
            "responses": {
                "type": "object",
                "additionalProperties": False,
                "required": ["A", "B"],
                "properties": {
                    "A": {"$ref": "#/$defs/response"},
                    "B": {"$ref": "#/$defs/response"},
                },
            },
            "comparison": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "more_natural",
                    "better_frame_following",
                    "stronger_emotional_presence",
                    "better_evidence_discipline",
                    "better_conclusion",
                    "more_enjoyable",
                    "weaknesses",
                ],
                "properties": {
                    "more_natural": {"type": "string", "description": JUDGE_STRING_DESCRIPTION},
                    "better_frame_following": {"type": "string", "description": JUDGE_STRING_DESCRIPTION},
                    "stronger_emotional_presence": {"type": "string", "description": JUDGE_STRING_DESCRIPTION},
                    "better_evidence_discipline": {"type": "string", "description": JUDGE_STRING_DESCRIPTION},
                    "better_conclusion": {"type": "string", "description": JUDGE_STRING_DESCRIPTION},
                    "more_enjoyable": {"type": "string", "description": JUDGE_STRING_DESCRIPTION},
                    "weaknesses": {"type": "string", "description": JUDGE_STRING_DESCRIPTION},
                },
            },
            "final_verdict": {"type": "string", "description": JUDGE_STRING_DESCRIPTION},
        },
        "$defs": {
            "response": {
                "type": "object",
                "additionalProperties": False,
                "required": ["scores", "strengths", "deductions", "weaknesses"],
                "properties": {
                    "scores": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": categories,
                        "properties": category_properties(
                            {
                                "type": ["number", "null"],
                                "minimum": 0,
                                "maximum": 10,
                                "description": (
                                    "A number from 0 to 10, or null when the criterion is marked "
                                    f"{NOT_APPLICABLE} for this comparison."
                                ),
                            },
                            categories,
                        ),
                    },
                    "strengths": {
                        "type": "array",
                        "items": {"type": "string", "description": JUDGE_STRING_DESCRIPTION},
                        "description": "Each array entry must be a complete sentence or phrase.",
                    },
                    "deductions": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": categories,
                        "properties": category_properties(
                            {"type": "string", "description": JUDGE_STRING_DESCRIPTION}, categories
                        ),
                    },
                    "weaknesses": {
                        "type": "array",
                        "items": {"type": "string", "description": JUDGE_STRING_DESCRIPTION},
                        "description": "Each array entry must be a complete sentence or phrase.",
                    },
                },
            }
        },
    }


JUDGE_OUTPUT_SCHEMA: dict[str, Any] = judge_output_schema()


def grammar_object_rule(rule_name: str, fields: list[tuple[str, str]]) -> str:
    """One GBNF rule, on a single line.

    Rules must not be wrapped across lines: llama.cpp's grammar parser treats a
    newline as the end of a rule, and servers that fail to parse a rule can
    accept the request and silently generate unconstrained, which is how a
    multi-line grammar could look applied while doing nothing at all.
    """
    parts = []
    for index, (key, value_rule) in enumerate(fields):
        escaped = key.replace('"', '\\"')
        separator = ' ws "," ws' if index + 1 < len(fields) else ""
        parts.append(f'"\\"{escaped}\\"" ws ":" ws {value_rule}{separator}')
    return f'{rule_name} ::= "{{" ws ' + " ".join(parts) + ' ws "}"'


def grammar_category_object_rule(rule_name: str, categories: list[str], value_rule: str) -> str:
    return grammar_object_rule(rule_name, [(category, value_rule) for category in categories])


def judge_output_grammar(weights: dict[str, float] | None = None) -> str:
    categories = list(weights or CATEGORY_WEIGHTS)
    scores_rule = grammar_category_object_rule("scores", categories, "score")
    deductions_rule = grammar_category_object_rule("deductions", categories, "string")
    applicability_rule = grammar_category_object_rule(
        "applicability", categories, "applicability-value"
    )
    comparison_rule = grammar_object_rule(
        "comparison", [(key, "string") for key in COMPARISON_FIELDS]
    )
    # criteria_applicability is emitted first so the judge commits to which
    # criteria count before it starts awarding marks for either response.
    return rf'''
root ::= ws "{{" ws applicability-pair ws "," ws responses-pair ws "," ws comparison-pair ws "," ws final-verdict-pair ws "}}" ws

applicability-pair ::= "\"{APPLICABILITY_KEY}\"" ws ":" ws applicability
{applicability_rule}
applicability-value ::= "\"{APPLICABLE}\"" | "\"{NOT_APPLICABLE}\""

responses-pair ::= "\"responses\"" ws ":" ws "{{" ws "\"A\"" ws ":" ws response ws "," ws "\"B\"" ws ":" ws response ws "}}"
response ::= "{{" ws scores-pair ws "," ws strengths-pair ws "," ws deductions-pair ws "," ws weaknesses-pair ws "}}"

scores-pair ::= "\"scores\"" ws ":" ws scores
{scores_rule}

deductions-pair ::= "\"deductions\"" ws ":" ws deductions
{deductions_rule}

strengths-pair ::= "\"strengths\"" ws ":" ws string-array
weaknesses-pair ::= "\"weaknesses\"" ws ":" ws string-array

comparison-pair ::= "\"comparison\"" ws ":" ws comparison
{comparison_rule}

final-verdict-pair ::= "\"final_verdict\"" ws ":" ws string

string-array ::= "[" ws (string (ws "," ws string)*)? ws "]"
string ::= "\"" ([^"\\] | "\\" (["\\/bfnrt] | "u" hex hex hex hex))* "\""
score ::= number | "null"
number ::= "-"? ("0" | [1-9] [0-9]*) ("." [0-9]+)?
hex ::= [0-9a-fA-F]
ws ::= [ \t\n\r]*
'''.strip()


JUDGE_OUTPUT_GRAMMAR = judge_output_grammar()


SYSTEM_PROMPT = """You are Helcyon-Bench, a careful and impartial evaluator of model responses.
Compare two submitted responses against the conversation context, current prompt, and rubric.
Use the supplied judging profile as the benchmark philosophy for this prompt pack.
Behave like an experienced examiner marking against a published marking scheme.
POSITION NEUTRALITY:
- The labels Response A and Response B are arbitrary slot names assigned by the application.
- The ordering carries no information about quality, seniority, origin, or which response was produced first.
- Neither slot is the baseline, the reference, the incumbent, or the answer the other must beat.
- The same pair is judged again in the opposite order by a separate, independent call, so any preference for a slot is a marking error.
- You are never told which model produced either response. Do not guess, and do not let a hunch about authorship affect a mark.
- Mark each response on its own merits against the prompt and rubric. Neither response is evidence about the other.
Return one strict JSON object only. Do not include markdown, comments, or prose outside JSON.
The object has exactly four top-level keys: criteria_applicability, responses, comparison, final_verdict.
Per-response marks go under responses.A and responses.B. Never under overall, results, or any other container.
Never emit an overall score, winner, confidence, or score_difference. The application calculates those.
CRITICAL JSON STRING RULE:
- Never place a raw double quotation mark inside a JSON string value.
- When referring to a phrase from either response or the user prompt, use single quotation marks or paraphrase it.
- Raw double quotation marks inside prose will invalidate the entire judgement."""


def response_example_block(categories: list[str]) -> str:
    scores_lines = ",\n".join(f'        "{category}": 0.0' for category in categories)
    deduction_lines = ",\n".join(f'        "{category}": "..."' for category in categories)
    return (
        "{\n"
        '      "scores": {\n'
        f"{scores_lines}\n"
        "      },\n"
        '      "strengths": ["..."],\n'
        '      "deductions": {\n'
        f"{deduction_lines}\n"
        "      },\n"
        '      "weaknesses": ["..."]\n'
        "    }"
    )


def applicability_example_block(categories: list[str]) -> str:
    lines = ",\n".join(f'    "{category}": "{APPLICABLE}"' for category in categories)
    return "{\n" + lines + "\n  }"


def output_instructions(weights: dict[str, float] | None = None) -> str:
    categories = list(weights or CATEGORY_WEIGHTS)
    example = response_example_block(categories)
    applicability_example = applicability_example_block(categories)
    category_list = ", ".join(categories)
    return f"""Return this JSON shape:
{{
  "{APPLICABILITY_KEY}": {applicability_example},
  "responses": {{
    "A": {example},
    "B": {example}
  }},
  "comparison": {{
    "more_natural": "...",
    "better_frame_following": "...",
    "stronger_emotional_presence": "...",
    "better_evidence_discipline": "...",
    "better_conclusion": "...",
    "more_enjoyable": "...",
    "weaknesses": "..."
  }},
  "final_verdict": "..."
}}

Top-level shape rules:
- The object must have exactly these four top-level keys: {APPLICABILITY_KEY}, responses, comparison, final_verdict.
- The per-response marks belong under responses, with the two keys A and B. Do not place them under overall, results, evaluations, or any other name.
- Do not emit an overall number, a winner, a confidence, or a score_difference anywhere. The application calculates all four from your category scores, and any you supply are discarded.
- Do not emit schema_version, rubric, or other bookkeeping fields.

JSON string rules:
- CRITICAL: Return one strict JSON object only.
- Never place a raw double quotation mark inside a JSON string value.
- When referring to a phrase from either response or the user prompt, use single quotation marks or paraphrase it.
- Raw double quotation marks inside prose will invalidate the entire judgement.
- Do not use quotation marks inside JSON string values.
- When referring to words or phrases from a response, paraphrase them instead of quoting them.
- Deduction strings must never quote words or phrases from the user's prompt.
- Deduction strings must paraphrase the user's prompt naturally instead of copying its wording.
- Do not use quotation marks inside deduction strings.
- If a deduction refers to the prompt, use descriptions such as the user's question, the prompt, the scenario, or the request instead of quoting words from it.
- Keep every string value as one complete sentence or phrase.
- Do not split a sentence fragment across multiple array entries.

Criterion applicability:
- Before marking anything, decide for each criterion whether it genuinely applies to this prompt.
- A criterion is {NOT_APPLICABLE} when the prompt neither invites nor rewards it and it would be irrelevant or unhelpful for a response to attempt it.
- Humour, for example, is {NOT_APPLICABLE} when the prompt carries no playful invitation and levity would be unwelcome or intrusive. Do not award a placeholder mark for restraint you cannot observe.
- Evidence Discipline is {NOT_APPLICABLE} when the exchange contains no factual claims, sources, or constraints to be disciplined about.
- The applicability decision is a property of the prompt, not of a response, so it must be identical for both responses.
- Record every decision in {APPLICABILITY_KEY} for all of: {category_list}.
- When a criterion is {NOT_APPLICABLE}, set that criterion's score to null for BOTH responses and write its deduction note as Not applicable to this prompt.
- Never invent a number for a criterion you have marked {NOT_APPLICABLE}. Do not substitute a neutral or average score. The application excludes it from both totals.
- When a criterion is {APPLICABLE}, both scores must be numbers from 0 to 10. Do not use strings. Do not omit categories.
- Do not mark a criterion {NOT_APPLICABLE} merely because both responses handled it badly. That is a low score, not an absent criterion.

Scoring method:
1. Decide criterion applicability for the prompt and record it.
2. Mark each response independently against the rubric, on its own merits, without reference to the other response.
3. Award every applicable category score for both responses. Judge each response only against the prompt, the supplied evidence and constraints, and the rubric.
4. Explain deductions from full marks for every category and both responses. If no meaningful deduction exists, say "No meaningful deduction."
5. Do not provide an overall score, winner, or confidence. The application calculates those from the category scores.
6. Only after both responses are marked, compare them by referring to category evidence that you have already recorded.
7. Write final_verdict last. If final_verdict names Response A or Response B as the winner, that written winner must agree with the category scores you awarded.

Impartiality and honesty:
- Do not treat either slot as the reference answer. Ask what each response did well or badly, never whether the second one beat the first.
- If both responses fail the prompt, say so plainly in final_verdict and mark both low. A weak winner is still weak.
- If the two are genuinely of equal quality, award equal scores and say the comparison is level. A tie is a valid outcome.
- Do not invent support for a verdict. Every claim in the comparison and final_verdict must point at something actually present in the response it describes.
- Do not credit or penalise a response for length, formatting, or confidence of tone unless the rubric or judging profile makes that relevant.

Scores must be earned, not guessed. Every deduction from 10.0 must correspond to an identifiable weakness.
Avoid score inflation and score compression.
Use this scale:
- 10.0: Essentially impossible to improve in any meaningful conversational way. Extremely rare.
- 9.5: Outstanding. Only tiny refinements possible.
- 9.0: Excellent. Clearly strong for the active judging profile. Small weaknesses exist.
- 8.0: Very good. Noticeable room for improvement.
- 7.0: Good. Several conversational weaknesses.
- 6.0: Competent but average.
- 5.0 and below: Increasingly poor fit for the active judging profile.

Comparison language must be examiner-like. Avoid subjective phrasing such as "I preferred".
Instead write things like "Response B achieved a higher Conversation Flow score because..."."""


OUTPUT_INSTRUCTIONS = output_instructions()


def log_judge_event(event: str, payload: dict[str, Any]) -> None:
    if not debug_logging_enabled():
        return
    try:
        JUDGE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "event": event,
            **payload,
        }
        with JUDGE_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def log_judge_pipeline_debug(stage: str, payload: dict[str, Any]) -> None:
    if not debug_logging_enabled():
        return
    try:
        JUDGE_PIPELINE_DEBUG_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "temporary_debug": True,
            "stage": stage,
            **payload,
        }
        with JUDGE_PIPELINE_DEBUG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def build_messages(
    rubric: str,
    judging_profile: str,
    conversation_context: str,
    current_prompt: str,
    response_a: str,
    response_b: str,
    extra_categories: list[str] | None = None,
) -> list[dict[str, str]]:
    """Build the blind judge prompt.

    Candidate model names are deliberately never passed in. The judge sees only
    the neutral slot labels Response A and Response B, so it cannot favour a
    known model, and the caller can reverse the slots for the second pass
    without the prompt leaking which reversal happened.
    """
    instructions = output_instructions(scoring_categories(extra_categories))
    content = f"""Judging profile:
{judging_profile or "Use the rubric's default judging philosophy."}

Use this judging profile as the primary evaluation philosophy for the selected prompt pack.
Keep the rubric's scoring categories and JSON schema stable, but if the rubric's philosophy conflicts with the judging profile, prefer the judging profile.

Rubric:
{rubric}

{instructions}

Conversation context, if any:
<<<CONTEXT
{conversation_context or "No prior context provided."}
CONTEXT

Current prompt:
<<<PROMPT
{current_prompt}
PROMPT

The two responses below are presented in an arbitrary order. Mark each one against the prompt and rubric on its own merits.

Response A:
<<<RESPONSE_A
{response_a}
RESPONSE_A

Response B:
<<<RESPONSE_B
{response_b}
RESPONSE_B"""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]


def strip_markdown_fences(text: str) -> str:
    cleaned = re.sub(r"```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    cleaned = cleaned.replace("```", "")
    return cleaned.strip()


def strip_local_judge_wrappers(text: str) -> str:
    cleaned = re.sub(r"(?m)^\[(?:json_schema|json_object|none|grammar)\]\s*", "", str(text or ""))
    cleaned = cleaned.replace("<|im_end|>", "")
    return cleaned.strip()


def first_complete_json_object(text: str) -> str | None:
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
            if depth < 0:
                return None
    return None


def broad_json_object_slice(text: str) -> str | None:
    first = text.find("{")
    last = text.rfind("}")
    if first == -1 or last == -1 or last < first:
        return None
    return text[first : last + 1]


def repair_trailing_commas(text: str) -> str:
    return re.sub(r",\s*([}\]])", r"\1", text)


def log_parse_failure(stage: str, raw_content: str, exc: BaseException) -> None:
    log_judge_event(
        "judge_json_parse_failed",
        {
            "stage": stage,
            "raw_assistant_content": raw_content,
            "raw_content_received": bool(raw_content),
            "json_parse_exception": f"{exc.__class__.__name__}: {exc}",
            "raw_content_preview": raw_content[:500],
        },
    )


def excerpt_around(text: str, position: int | None, radius: int = 180) -> str:
    if position is None:
        return str(text or "")[: radius * 2]
    start = max(0, position - radius)
    end = min(len(text), position + radius)
    return text[start:end]


def json_error_position(exc: BaseException) -> int | None:
    return exc.pos if isinstance(exc, json.JSONDecodeError) else None


def log_repair_parse_event(
    stage: str,
    raw_content: str,
    *,
    parse_error: BaseException | None = None,
    original_text: str = "",
    repaired_text: str = "",
) -> None:
    position = json_error_position(parse_error) if parse_error else None
    log_judge_event(
        "judge_json_repair_parse",
        {
            "stage": stage,
            "raw_assistant_content": raw_content,
            "raw_content_received": bool(raw_content),
            "parse_error": (
                f"{parse_error.__class__.__name__}: {parse_error}" if parse_error else ""
            ),
            "embedded_quote_recovery_attempted": True,
            "error_position": position,
            "original_error_excerpt": excerpt_around(original_text or raw_content, position),
            "repaired_error_excerpt": excerpt_around(repaired_text, position) if repaired_text else "",
            "raw_content_preview": raw_content[:500],
        },
    )


def repair_unescaped_quotes_in_json_strings(text: str) -> str:
    repaired = []
    in_string = False
    escaped = False
    string_role = ""
    length = len(text)
    stack: list[dict[str, str]] = []

    def current_context() -> dict[str, str] | None:
        return stack[-1] if stack else None

    def mark_value_complete() -> None:
        context = current_context()
        if context:
            context["expect"] = "comma_or_end"

    def starts_scalar(index: int) -> bool:
        return index < length and (text[index].isdigit() or text[index] in "-tfn")

    def next_nonspace(index: int) -> int:
        while index < length and text[index].isspace():
            index += 1
        return index

    def quote_closes_value(index: int) -> bool:
        next_index = next_nonspace(index + 1)
        if next_index >= length:
            return True
        next_char = text[next_index]
        if next_char in {"}", "]"}:
            return True
        if next_char != ",":
            return False

        after_comma = next_nonspace(next_index + 1)
        if after_comma >= length:
            return True
        after_char = text[after_comma]
        context = current_context()
        if context and context.get("type") == "object":
            return after_char in {'"', "}"}
        if context and context.get("type") == "array":
            return after_char in {'"', "{", "[", "]"} or starts_scalar(after_comma)
        return after_char in {'"', "{", "[", "}", "]"} or starts_scalar(after_comma)

    for index, char in enumerate(text):
        if not in_string:
            repaired.append(char)
            if char == '"':
                in_string = True
                escaped = False
                context = current_context()
                string_role = "key" if context and context.get("type") == "object" and context.get("expect") in {"key", "key_or_end"} else "value"
            elif char == "{":
                stack.append({"type": "object", "expect": "key_or_end"})
            elif char == "[":
                stack.append({"type": "array", "expect": "value_or_end"})
            elif char == "}":
                if stack:
                    stack.pop()
                mark_value_complete()
            elif char == "]":
                if stack:
                    stack.pop()
                mark_value_complete()
            elif char == ":":
                context = current_context()
                if context and context.get("type") == "object":
                    context["expect"] = "value"
            elif char == ",":
                context = current_context()
                if context and context.get("type") == "object":
                    context["expect"] = "key_or_end"
                elif context and context.get("type") == "array":
                    context["expect"] = "value_or_end"
            continue

        if escaped:
            repaired.append(char)
            escaped = False
            continue

        if char == "\\":
            repaired.append(char)
            escaped = True
            continue

        if char == '"':
            next_index = next_nonspace(index + 1)
            if (
                (string_role == "key" and next_index < length and text[next_index] == ":")
                or (string_role != "key" and quote_closes_value(index))
            ):
                repaired.append(char)
                in_string = False
                if string_role == "key":
                    context = current_context()
                    if context and context.get("type") == "object":
                        context["expect"] = "colon"
                else:
                    mark_value_complete()
            else:
                repaired.append('\\"')
            continue

        repaired.append(char)

    return "".join(repaired)


def parse_json(
    text: str,
    debug_context: dict[str, Any] | None = None,
    repair_status: dict[str, bool] | None = None,
) -> dict[str, Any]:
    debug_context = debug_context or {}
    if repair_status is not None:
        repair_status["parser_repair_attempted"] = False
        repair_status["parser_repair_recovered_object"] = False
    current_json_text = text
    repair_stage_logged = False
    try:
        data = json.loads(text)
        log_judge_pipeline_debug(
            "AFTER JUDGE JSON EXTRACTION",
            {
                **debug_context,
                "extraction_path": "raw_json_load",
                "text": text,
                "changed_from_input": False,
            },
        )
    except json.JSONDecodeError as original_exc:
        log_parse_failure("raw", text, original_exc)
        cleaned = strip_local_judge_wrappers(strip_markdown_fences(text))
        try:
            data = json.loads(cleaned)
            current_json_text = cleaned
            log_judge_pipeline_debug(
                "AFTER JUDGE JSON EXTRACTION",
                {
                    **debug_context,
                    "extraction_path": "stripped_wrappers",
                    "text": cleaned,
                    "changed_from_input": cleaned != text,
                },
            )
        except json.JSONDecodeError as cleaned_exc:
            log_parse_failure("stripped_markdown_fences", text, cleaned_exc)
            candidates: list[tuple[str, str]] = []
            for extraction_path, candidate in (
                ("first_complete_json_object", first_complete_json_object(cleaned)),
                ("broad_first_brace_to_last_brace", broad_json_object_slice(cleaned)),
            ):
                if candidate and candidate not in [existing for _, existing in candidates]:
                    candidates.append((extraction_path, repair_trailing_commas(candidate)))
            if not candidates:
                raise JudgeError(
                    "The judge did not return JSON. "
                    f"json_parse_exception={cleaned_exc.__class__.__name__}: {cleaned_exc}; "
                    f"raw_content_received={bool(text)}; first_500={text[:500]}",
                    raw_response=text,
                ) from original_exc

            last_parse_error: json.JSONDecodeError | None = None
            for extraction_path, extracted in candidates:
                log_judge_pipeline_debug(
                    "AFTER JUDGE JSON EXTRACTION",
                    {
                        **debug_context,
                        "extraction_path": extraction_path,
                        "text": extracted,
                        "changed_from_input": extracted != text,
                    },
                )
                current_json_text = extracted
                log_judge_pipeline_debug(
                    "AFTER JSON REPAIR",
                    {
                        **debug_context,
                        "text": current_json_text,
                        "repair_attempted": False,
                        "changed_from_extracted": False,
                    },
                )
                try:
                    data = json.loads(extracted)
                    break
                except json.JSONDecodeError as exc:
                    last_parse_error = exc
                    log_parse_failure(f"{extraction_path}_normal_parse", text, exc)
                    log_repair_parse_event(
                        "normal_parse_failed_repair_attempted",
                        text,
                        parse_error=exc,
                        original_text=extracted,
                    )
                    if repair_status is not None:
                        repair_status["parser_repair_attempted"] = True
                    repaired = repair_unescaped_quotes_in_json_strings(extracted)
                    current_json_text = repaired
                    repair_stage_logged = True
                    log_judge_pipeline_debug(
                        "AFTER JSON REPAIR",
                        {
                            **debug_context,
                            "text": repaired,
                            "changed_from_extracted": repaired != extracted,
                            "parse_error": f"{exc.__class__.__name__}: {exc}",
                        },
                    )
                    try:
                        data = json.loads(repaired)
                        log_repair_parse_event(
                            "repair_parse_succeeded",
                            text,
                            parse_error=exc,
                            original_text=extracted,
                            repaired_text=repaired,
                        )
                        if repair_status is not None:
                            repair_status["parser_repair_recovered_object"] = True
                        break
                    except json.JSONDecodeError as repair_exc:
                        last_parse_error = repair_exc
                        log_repair_parse_event(
                            "repair_parse_failed",
                            text,
                            parse_error=repair_exc,
                            original_text=extracted,
                            repaired_text=repaired,
                        )
            else:
                parse_error = last_parse_error or cleaned_exc
                raise JudgeError(
                    "The judge returned malformed JSON after robust extraction. "
                    f"json_parse_exception={parse_error.__class__.__name__}: {parse_error}; "
                    f"raw_content_received={bool(text)}; first_500={text[:500]}",
                    raw_response=text,
                ) from parse_error

    if not isinstance(data, dict):
        raise JudgeError("The judge JSON must be an object.")
    if not repair_stage_logged:
        log_judge_pipeline_debug(
            "AFTER JSON REPAIR",
            {
                **debug_context,
                "text": current_json_text,
                "repair_attempted": False,
                "changed_from_extracted": False,
            },
        )
    log_judge_pipeline_debug(
        "PARSED OBJECT",
        {
            **debug_context,
            "object": data,
            "object_json": json.dumps(data, ensure_ascii=False, sort_keys=True),
        },
    )
    return data


def is_local_judge_endpoint(endpoint: JudgeEndpointConfig) -> bool:
    mode = str(getattr(endpoint, "local_endpoint_mode", "") or "").lower()
    if mode in {"shared_hwui", "external_dedicated"}:
        return True
    base_url = str(getattr(endpoint, "base_url", "") or "").lower()
    return "://127.0.0.1" in base_url or "://localhost" in base_url


_VERDICT_ADVERBS = r"(?:narrowly|just|barely|slightly|marginally|clearly|comfortably|decisively|definitely|ultimately|overall|somewhat|much|far|easily)"


def _verdict_patterns_for(letter: str) -> list[str]:
    return [
        rf"\bresponse\s+{letter}\s+(?:(?:{_VERDICT_ADVERBS})\s+)?(?:wins?|edges?(?:\s+it)?|takes(?:\s+it)?|prevails|comes\s+out\s+ahead|has\s+the\s+edge)\b",
        rf"\b{letter}\s+(?:(?:{_VERDICT_ADVERBS})\s+)?(?:wins?|edges?(?:\s+it)?|takes(?:\s+it)?|prevails|comes\s+out\s+ahead|has\s+the\s+edge)\b",
        rf"\b(?:response\s+)?{letter}\s+is\s+the\s+winner\b",
        rf"\b(?:winner|winning\s+response)\s*(?:is|:|-)?\s*(?:response\s+)?{letter}\b",
        # "A is [narrowly] the stronger response" / "B is clearly the better answer"
        rf"\b(?:response\s+)?{letter}\s+is\s+(?:(?:{_VERDICT_ADVERBS})\s+)?(?:the\s+)?(?:stronger|better|superior|preferred|winning)\s+(?:response|answer|choice|option|one|overall)?\b",
        # "A is the stronger of the two"
        rf"\b(?:response\s+)?{letter}\s+is\s+(?:(?:{_VERDICT_ADVERBS})\s+)?(?:the\s+)?(?:stronger|better|superior|preferred)\s+of\s+the\s+two\b",
    ]


def verdict_response(final_verdict: str) -> str | None:
    text = str(final_verdict or "")
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return None

    # Bare "Response A" / "Response B" (optionally with trailing punctuation) as the
    # entire verdict text unambiguously names the winner even without a verb.
    bare = normalized.rstrip(" .!—-").strip().lower()
    if bare in {"response a", "a"}:
        return "A"
    if bare in {"response b", "b"}:
        return "B"

    patterns = {"A": _verdict_patterns_for("a"), "B": _verdict_patterns_for("b")}
    matches = {
        response
        for response, response_patterns in patterns.items()
        if any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in response_patterns)
    }
    if len(matches) == 1:
        return next(iter(matches))
    return None



# Phrasing the judge uses when it considers the comparison a tie rather than
# naming a winner. Matched against final_verdict text only; it never overrides
# a numeric result on its own (see llmbench.protocol.combine_bidirectional_results,
# where it can only nudge an already-borderline Narrow Preference down to an
# Effective Tie, never override a Clear Winner).
_TIE_VERDICT_PATTERNS = [
    r"\b(?:the\s+)?comparison\s+is\s+level\b",
    r"\bessentially\s+(?:tied|equal|even)\b",
    r"\bvirtually\s+(?:identical|indistinguishable|tied)\b",
    r"\btie\b",
    r"\btied\b",
    r"\bequally\s+(?:strong|matched|good|weak)\b",
    r"\btoo\s+close\s+to\s+call\b",
    r"\bno\s+clear\s+winner\b",
    r"\bneither\s+response\s+(?:clearly\s+)?(?:wins|is\s+better|outperforms)\b",
    r"\bof\s+equal\s+quality\b",
]


def verdict_declares_tie(final_verdict: str) -> bool:
    """True when the judge's own prose reads as declaring a tie.

    This is deliberately narrow phrase-matching, the same style as
    verdict_response() above. It is a supporting signal only - see its call
    site in protocol.py for how it is bounded so it can never turn a large,
    stable numeric gap into a reported tie on its own.
    """
    text = str(final_verdict or "")
    normalized = re.sub(r"\s+", " ", text).strip().lower()
    if not normalized:
        return False
    return any(re.search(pattern, normalized) for pattern in _TIE_VERDICT_PATTERNS)


def enforce_verdict_consistency(data: dict[str, Any]) -> dict[str, Any]:
    """Check the written verdict against the calculated numeric winner.

    Category scores are elicited and locked in before final_verdict is
    written (the rubric's own scoring method requires scoring first, comparing
    second, and writing final_verdict last), so the numeric winner is the more
    procedurally reliable signal. A conflicting verdict is therefore recorded
    as a reliability flag, never used to rewrite winner.response - silently
    letting prose override the calculated winner would reopen exactly the
    single-pass, verdict-can-decide-everything failure mode the bidirectional
    protocol exists to avoid.
    """
    verdict_winner = verdict_response(str(data.get("final_verdict", "")))
    calculated_winner = str(data.get("winner", {}).get("response", "")).upper()
    if verdict_winner is None:
        data["verdict_consistency"] = {
            "status": "not_checked",
            "reason": "final_verdict did not clearly name Response A or Response B as the winner",
        }
        return data

    if calculated_winner == verdict_winner:
        data["verdict_consistency"] = {
            "status": "ok",
            "verdict_response": verdict_winner,
            "calculated_response": calculated_winner,
        }
        return data

    warning = (
        "final_verdict named Response "
        f"{verdict_winner}, but the calculated winner (from category scores) was "
        f"{calculated_winner or 'unknown'}. The numeric winner was kept unchanged; this "
        "conflict is recorded rather than resolved by rewriting it."
    )
    data["verdict_consistency"] = {
        "status": "conflict",
        "verdict_response": verdict_winner,
        "calculated_response": calculated_winner,
        "warning": warning,
    }
    log_judge_event(
        "verdict_consistency_conflict",
        {
            "warning": warning,
            "final_verdict": data.get("final_verdict", ""),
            "winner": data.get("winner", {}),
            "response_a_overall": data.get("responses", {}).get("A", {}).get("overall"),
            "response_b_overall": data.get("responses", {}).get("B", {}).get("overall"),
        },
    )
    return data


# --- Canonical judge payload contract -------------------------------------
#
# Every judge response, from every endpoint and every response_format mode, is
# normalised to exactly this shape before validation:
#
#   {
#     "criteria_applicability": {<category>: "applicable" | "not_applicable"},
#     "responses": {
#       "A": {"scores": {<category>: number|null}, "strengths": [...],
#             "deductions": {<category>: str}, "weaknesses": [...]},
#       "B": {... same ...}
#     },
#     "comparison": {<7 comparison keys>: str},
#     "final_verdict": str
#   }
#
# The application - never the judge - owns "overall", "winner", "confidence"
# and "score_difference". A judge that supplies them has them discarded and
# recalculated, so a model's own arithmetic can never reach a report.
#
# The JSON schema, the GBNF grammar, the prompt example and the parser all
# describe this one shape. Local llama.cpp judges are only loosely constrained
# when a run falls back to json_object mode, so known shape drift is repaired
# here rather than aborting a run that contains all the required data.

RESPONSES_KEY = "responses"

# Top-level containers seen holding the per-response marks instead of
# "responses". "overall" is emitted by some local Helcyon fine-tunes, which
# also nest a per-response "overall" number inside it.
RESPONSE_CONTAINER_ALIASES = ("responses", "overall", "results", "evaluations", "judgements")

# Fields the judge sometimes supplies that the application calculates itself.
JUDGE_SUPPLIED_FIELDS_TO_DISCARD = ("winner", "confidence", "score_difference", "overall")


def _looks_like_response_container(value: Any) -> bool:
    """True for a dict holding an A and a B entry that each carry scores."""
    if not isinstance(value, dict):
        return False
    keys = {str(key).strip().upper(): key for key in value}
    if not {"A", "B"} <= set(keys):
        return False
    return all(
        isinstance(value[keys[side]], dict) and isinstance(value[keys[side]].get("scores"), dict)
        for side in ("A", "B")
    )


def normalise_judge_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Map a judge payload onto the canonical contract above.

    Returns the same dict, with a "normalisation" note recording anything that
    had to be moved or dropped so the repair is visible in saved data and logs
    rather than silent.
    """
    if not isinstance(data, dict):
        return data

    applied: list[str] = []
    source_key = RESPONSES_KEY

    if not _looks_like_response_container(data.get(RESPONSES_KEY)):
        for alias in RESPONSE_CONTAINER_ALIASES:
            if alias == RESPONSES_KEY:
                continue
            if _looks_like_response_container(data.get(alias)):
                data[RESPONSES_KEY] = data.pop(alias)
                source_key = alias
                applied.append(f"moved {alias} to {RESPONSES_KEY}")
                break

    responses = data.get(RESPONSES_KEY)
    if isinstance(responses, dict):
        # Accept lower-case or padded slot keys.
        remapped = {}
        for key, value in responses.items():
            slot = str(key).strip().upper()
            remapped[slot if slot in {"A", "B"} else key] = value
        if remapped != responses:
            applied.append("normalised response slot keys")
        data[RESPONSES_KEY] = remapped

        for slot in ("A", "B"):
            response = remapped.get(slot)
            if not isinstance(response, dict):
                continue
            # The judge's own overall is advisory at best; the app recomputes it
            # from the category scores and the applicability decision.
            if response.pop("overall", None) is not None:
                applied.append(f"discarded judge-supplied overall for response {slot}")
            for key in ("strengths", "weaknesses"):
                if key not in response:
                    response[key] = []

    for field in JUDGE_SUPPLIED_FIELDS_TO_DISCARD:
        if field in data and field != RESPONSES_KEY:
            data.pop(field, None)
            applied.append(f"discarded judge-supplied {field}")

    if applied:
        data["normalisation"] = {
            "applied": applied,
            "response_container_source": source_key,
            "note": (
                "The judge returned a non-canonical payload shape. It was mapped onto the "
                "canonical contract; no score was altered."
            ),
        }
        log_judge_event("judge_payload_normalised", {"applied": applied, "source_key": source_key})
    return data


def is_missing_score(value: Any) -> bool:
    """True when the judge signalled 'no score here' rather than a number."""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower().replace(" ", "_") in NOT_APPLICABLE_TOKENS
    return False


def resolve_applicability(data: dict[str, Any], weights: dict[str, float]) -> dict[str, str]:
    """Decide, once per comparison, which criteria count towards the totals.

    The decision is a property of the prompt, so it is applied identically to
    both responses. A criterion is excluded when the judge declared it
    not applicable, or when either response carries a null/N-A score for it -
    a missing score on one side cannot be averaged against a number on the other.
    """
    declared = data.get(APPLICABILITY_KEY)
    declared = declared if isinstance(declared, dict) else {}
    responses = data.get("responses")
    responses = responses if isinstance(responses, dict) else {}

    resolved: dict[str, str] = {}
    for category in weights:
        raw = declared.get(category)
        not_applicable = (
            isinstance(raw, str)
            and raw.strip().lower().replace(" ", "_") in NOT_APPLICABLE_TOKENS
        ) or raw is False
        if not not_applicable:
            for side in ("A", "B"):
                response = responses.get(side)
                if not isinstance(response, dict):
                    continue
                scores = response.get("scores")
                if isinstance(scores, dict) and is_missing_score(scores.get(category, 0.0)):
                    not_applicable = True
                    break
        resolved[category] = NOT_APPLICABLE if not_applicable else APPLICABLE

    if all(state == NOT_APPLICABLE for state in resolved.values()):
        raise JudgeError(
            "The judge marked every scoring criterion not applicable, so no overall score can be "
            "calculated. At least one criterion must be scored."
        )
    return resolved


def applicable_categories(applicability: dict[str, str] | None, weights: dict[str, float]) -> list[str]:
    applicability = applicability or {}
    return [
        category
        for category in weights
        if applicability.get(category, APPLICABLE) != NOT_APPLICABLE
    ]


def validate_result(data: dict[str, Any], extra_categories: list[str] | None = None) -> dict[str, Any]:
    data = normalise_judge_payload(data)
    if RESPONSES_KEY not in data or not isinstance(data[RESPONSES_KEY], dict):
        raise JudgeError(
            "The judge JSON is missing responses. The canonical shape is "
            '{"criteria_applicability": {...}, "responses": {"A": {...}, "B": {...}}, '
            '"comparison": {...}, "final_verdict": "..."}. '
            f"Top-level keys received: {', '.join(sorted(map(str, data))) or 'none'}."
        )

    weights = scoring_categories(extra_categories)
    applicability = resolve_applicability(data, weights)
    data[APPLICABILITY_KEY] = applicability
    data["responses"] = {
        key: validate_response_result(data["responses"].get(key), key, weights, applicability)
        for key in ["A", "B"]
    }
    data["winner"] = calculate_winner(data["responses"])

    comparison = data.get("comparison", {})
    if not isinstance(comparison, dict):
        comparison = {}
    data["comparison"] = {
        "more_natural": str(comparison.get("more_natural", "")),
        "better_frame_following": str(comparison.get("better_frame_following", "")),
        "stronger_emotional_presence": str(comparison.get("stronger_emotional_presence", "")),
        "better_evidence_discipline": str(comparison.get("better_evidence_discipline", "")),
        "better_conclusion": str(comparison.get("better_conclusion", "")),
        "more_enjoyable": str(comparison.get("more_enjoyable", "")),
        "weaknesses": str(comparison.get("weaknesses", "")),
    }

    data["final_verdict"] = str(data.get("final_verdict", ""))
    return enforce_verdict_consistency(data)


def validate_response_result(
    value: Any,
    label: str,
    weights: dict[str, float] | None = None,
    applicability: dict[str, str] | None = None,
) -> dict[str, Any]:
    weights = weights or CATEGORY_WEIGHTS
    if not isinstance(value, dict):
        raise JudgeError(f"The judge JSON is missing response {label}.")
    if "scores" not in value or not isinstance(value["scores"], dict):
        raise JudgeError(f"The judge JSON is missing response {label} scores.")

    value["scores"] = normalize_scores(value["scores"], label, weights, applicability)
    value["overall"] = calculate_overall(value["scores"], weights, applicability)

    for key in ["strengths", "weaknesses"]:
        items = value.get(key, [])
        if isinstance(items, str):
            items = [items]
        if not isinstance(items, list):
            items = []
        value[key] = [str(item) for item in items]

    deductions = value.get("deductions")
    if not isinstance(deductions, dict):
        raise JudgeError(f"The judge JSON is missing response {label} deductions.")
    # A criterion excluded from scoring needs no justification for its absence,
    # so only applicable criteria require a deduction note.
    required_notes = applicable_categories(applicability, weights)
    missing_deductions = [category for category in required_notes if category not in deductions]
    if missing_deductions:
        missing = ", ".join(missing_deductions)
        raise JudgeError(f"The judge JSON is missing response {label} deduction notes for {missing}.")
    value["deductions"] = {
        category: (
            str(deductions.get(category, ""))
            if category in required_notes
            else str(deductions.get(category) or NOT_APPLICABLE_DEDUCTION)
        )
        for category in weights
    }
    return value


NOT_APPLICABLE_DEDUCTION = "Not applicable to this prompt."


def normalize_scores(
    scores: dict[str, Any],
    label: str,
    weights: dict[str, float] | None = None,
    applicability: dict[str, str] | None = None,
) -> dict[str, float | None]:
    weights = weights or CATEGORY_WEIGHTS
    applicability = applicability or {}
    normalized: dict[str, float | None] = {}
    for category in weights:
        if applicability.get(category, APPLICABLE) == NOT_APPLICABLE:
            # Never coerced to 0, 8, or any other number: a non-applicable
            # criterion stays absent so it cannot move either total.
            normalized[category] = None
            continue
        if category not in scores:
            raise JudgeError(f"The judge JSON is missing response {label} {category} score.")
        normalized[category] = clamp_score(scores[category], response_label=label, category=category)
    return normalized


def calculate_overall(
    scores: dict[str, float | None],
    weights: dict[str, float] | None = None,
    applicability: dict[str, str] | None = None,
) -> float:
    """Weighted mean over the applicable criteria only.

    Non-applicable criteria are dropped from both the numerator and the
    denominator, so the remaining criteria are effectively reweighted rather
    than diluted by a placeholder score.
    """
    weights = weights or CATEGORY_WEIGHTS
    scored = [
        category
        for category in applicable_categories(applicability, weights)
        if scores.get(category) is not None
    ]
    if not scored:
        raise JudgeError(
            "No applicable criterion carried a numeric score, so no overall score can be calculated."
        )
    total_weight = sum(weights[category] for category in scored)
    weighted_total = sum(float(scores[category]) * weights[category] for category in scored)
    return round(weighted_total / total_weight, 2)


def calculate_winner(
    responses: dict[str, dict[str, Any]],
    effective_tie_threshold: float = EFFECTIVE_TIE_THRESHOLD,
    clear_winner_threshold: float = CLEAR_WINNER_THRESHOLD,
) -> dict[str, Any]:
    """Compute the directional lean, its classification, and a basic confidence.

    response ("A" / "B" / "TIE") is the directional lean and is preserved
    independently of classification: a gap inside the effective-tie band still
    reports which side it leaned towards (via "response"/"model_name") rather
    than collapsing to "TIE", so that lean is never discarded just because the
    headline classification is Effective Tie. "TIE" itself is reserved for a
    gap so small it is exact-equality noise - see EXACT_TIE_EPSILON.

    The confidence value here is a single-signal estimate based only on score
    separation. It is intentionally basic: it is what legacy single-pass
    records use, and it is what a bidirectional canonical result starts from
    before llmbench.protocol.calculate_composite_confidence folds in
    stability, category agreement, and verdict consistency.
    """
    score_a = float(responses["A"]["overall"])
    score_b = float(responses["B"]["overall"])
    difference = round(score_b - score_a, 2)
    if abs(difference) < EXACT_TIE_EPSILON:
        winner = "TIE"
    elif difference > 0:
        winner = "B"
    else:
        winner = "A"
    return {
        "response": winner,
        "model_name": "",
        "confidence": calculate_confidence(abs(difference)),
        "score_difference": abs(difference),
        "classification": classify_score_gap(abs(difference), effective_tie_threshold, clear_winner_threshold),
    }


def calculate_confidence(score_gap: float) -> int:
    if score_gap < EXACT_TIE_EPSILON:
        return 50
    return min(99, int(round(55 + (score_gap * 12))))


def clamp_score(
    value: Any,
    maximum: float = 10.0,
    response_label: str | None = None,
    category: str | None = None,
) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        location = ""
        if response_label and category:
            location = f" for response {response_label} {category}"
        raise JudgeError(
            f"Invalid score value{location}: {value!r}. Scores must be numeric values from 0 to 10; never return N/A or strings."
        )
    return max(0.0, min(maximum, score))


def judge_comparison(
    *,
    config: AppConfig,
    rubric: str,
    judging_profile: str,
    conversation_context: str,
    current_prompt: str,
    response_a: str,
    response_b: str,
    model_name_a: str,
    model_name_b: str,
    model: str,
    temperature: float,
    endpoint: JudgeEndpointConfig | None = None,
    extra_headers: dict[str, str] | None = None,
    prompt_id: str = "",
    extra_score_categories: list[str] | None = None,
) -> dict[str, Any]:
    weights = scoring_categories(extra_score_categories)
    # model_name_a / model_name_b are recorded for local debug logs and for the
    # caller's identity mapping only. They are deliberately NOT passed to
    # build_messages: the judge must never see which model wrote which slot.
    messages = build_messages(
        rubric,
        judging_profile,
        conversation_context,
        current_prompt,
        response_a,
        response_b,
        extra_categories=extra_score_categories,
    )
    judge_endpoint = endpoint or config.judge
    prefer_json_object = is_local_judge_endpoint(judge_endpoint)
    debug_context = {
        "prompt_id": prompt_id,
        "judge_model": model,
        "endpoint": judge_endpoint.name,
        "slot_model_a": model_name_a,
        "slot_model_b": model_name_b,
    }
    log_judge_pipeline_debug(
        "PROMPT CONSTRUCTION",
        {
            **debug_context,
            "messages": messages,
            "messages_json": json.dumps(messages, ensure_ascii=False),
        },
    )
    try:
        completion = chat_completion(
            base_url=judge_endpoint.base_url,
            api_key=judge_endpoint.api_key,
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=judge_endpoint.max_tokens,
            max_completion_tokens=judge_endpoint.max_completion_tokens,
            timeout=judge_endpoint.timeout,
            json_schema=judge_output_schema(weights),
            grammar=judge_output_grammar(weights),
            extra_headers=extra_headers,
            endpoint_name=judge_endpoint.name,
            prompt_id=prompt_id,
            prefer_json_object=prefer_json_object,
            allow_raw_on_json_parse_failure=prefer_json_object,
            return_metadata=True,
            extra_log_fields={
                "response_a_character_length": len(response_a),
                "response_b_character_length": len(response_b),
            },
        )
    except ApiError as exc:
        raise JudgeError(
            str(exc),
            raw_response=getattr(exc, "raw_response", None),
            status_code=getattr(exc, "status_code", None),
        ) from exc
    completion_result = (
        completion
        if isinstance(completion, ChatCompletionResult)
        else ChatCompletionResult(text=str(completion), response_format_mode="")
    )
    raw = completion_result.text
    debug_context = {
        **debug_context,
        "response_format_mode": completion_result.response_format_mode,
        "api_json_extraction_failed": completion_result.api_json_extraction_failed,
    }
    log_judge_event(
        "raw_judge_output",
        {
            "model": model,
            "endpoint": judge_endpoint.name,
            "response_format_mode": completion_result.response_format_mode,
            "api_json_extraction_failed": completion_result.api_json_extraction_failed,
            "api_json_parse_error": completion_result.api_json_parse_error,
            "raw_output": raw,
        },
    )
    try:
        log_judge_pipeline_debug(
            "RAW TEXT ENTERING JUDGE PARSER",
            {
                **debug_context,
                "text": raw,
            },
        )
        repair_status: dict[str, bool] = {}
        parsed_object = parse_json(raw, debug_context=debug_context, repair_status=repair_status)
        log_judge_pipeline_debug(
            "PARSED OBJECT BEFORE VALIDATION",
            {
                **debug_context,
                "object": parsed_object,
                "object_json": json.dumps(parsed_object, ensure_ascii=False, sort_keys=True),
            },
        )
        try:
            parsed = validate_result(parsed_object, extra_score_categories)
        except JudgeError:
            log_judge_pipeline_debug(
                "JUDGE RESULT ACCEPTANCE",
                {
                    **debug_context,
                    "parser_repair_recovered_object": repair_status.get("parser_repair_recovered_object", False),
                    "validation_passed": False,
                },
            )
            raise
        log_judge_pipeline_debug(
            "JUDGE RESULT ACCEPTANCE",
            {
                **debug_context,
                "parser_repair_recovered_object": repair_status.get("parser_repair_recovered_object", False),
                "validation_passed": True,
            },
        )
        log_judge_event(
            "parsed_judge_result",
            {
                "model": model,
                "endpoint": judge_endpoint.name,
                "response_format_mode": completion_result.response_format_mode,
                "api_json_extraction_failed": completion_result.api_json_extraction_failed,
                "parser_repair_recovered_object": repair_status.get("parser_repair_recovered_object", False),
                "validation_passed": True,
                "parsed_result": parsed,
            },
        )
        return parsed
    except JudgeError as exc:
        if getattr(exc, "raw_response", None) is None:
            exc.raw_response = raw
        raise

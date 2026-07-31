"""Bidirectional evaluation protocol.

A single judge call scores two responses in fixed slots, so any preference the
judge has for the slot labelled Response A is silently converted into a win for
whichever model happened to occupy that slot. This module removes that failure
mode at the evaluation layer:

1. Every comparison is judged twice, by two independent judge calls, with the
   two candidate responses swapped between the slots. The response text itself
   is never regenerated or altered between the passes.
2. Each pass's scores are mapped from slot (A/B) back to model identity.
3. Each model's criterion scores are averaged across its A-slot and B-slot
   appearances, and the canonical overall score is recalculated from those
   identity-mapped averages.
4. The winner is decided from the canonical scores, never from either pass.
5. Both directional results are preserved verbatim, and a comparison whose
   winner depends on the ordering is flagged as position-sensitive.

The canonical result keeps the existing single-pass shape - responses "A" and
"B", scores, overall, winner, comparison, final_verdict - so reports, exports
and the dashboard keep working. In the canonical result, "A" always means the
model named model_name_a by the caller, never "whatever was in slot A".
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Callable

from llmbench.config import AppConfig, JudgeEndpointConfig
from llmbench.judge import (
    APPLICABILITY_KEY,
    APPLICABLE,
    CLASSIFICATION_EFFECTIVE_TIE,
    CLASSIFICATION_LABELS,
    CLASSIFICATION_NARROW_PREFERENCE,
    CLEAR_WINNER_THRESHOLD,
    EFFECTIVE_TIE_THRESHOLD,
    NOT_APPLICABLE,
    NOT_APPLICABLE_DEDUCTION,
    JudgeError,
    calculate_overall,
    calculate_winner,
    judge_comparison,
    scoring_categories,
    verdict_declares_tie,
)

PROTOCOL_MODE_BIDIRECTIONAL = "bidirectional"
PROTOCOL_MODE_LEGACY_SINGLE_PASS = "single_pass_legacy"
PROTOCOL_VERSION = 2

FORWARD = "forward"
REVERSE = "reverse"

STABILITY_STABLE = "stable"
STABILITY_MINOR_POSITIONAL_DRIFT = "minor_positional_drift"
STABILITY_POSITION_SENSITIVE = "position_sensitive"

# Human-readable labels for the graded stability field. The raw "status" /
# "position_sensitive" fields are kept unchanged alongside "stability_grade"
# for backward compatibility with anything already reading the binary form.
STABILITY_GRADE_LABELS = {
    STABILITY_STABLE: "Stable",
    STABILITY_MINOR_POSITIONAL_DRIFT: "Minor Positional Drift",
    STABILITY_POSITION_SENSITIVE: "Position Sensitive",
}

POSITION_SENSITIVE_WARNING = (
    "Position-sensitive: the winner changed when the response order was reversed. "
    "The canonical scores below average both orders, but this comparison is not a clean win."
)

# Slot mapping for each pass. Pass 1 puts model A in slot A; pass 2 reverses it.
PASS_SLOT_MODELS: dict[str, dict[str, str]] = {
    FORWARD: {"A": "A", "B": "B"},
    REVERSE: {"A": "B", "B": "A"},
}

ProgressCallback = Callable[[str, str], None]


def slot_for_model(pass_name: str, model_key: str) -> str:
    """Which judge slot a model occupies in the given pass."""
    mapping = PASS_SLOT_MODELS[pass_name]
    for slot, model in mapping.items():
        if model == model_key:
            return slot
    raise KeyError(f"Unknown model key: {model_key}")


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4)


def combined_applicability(pass_results: dict[str, dict[str, Any]], weights: dict[str, float]) -> dict[str, str]:
    """Union the two passes' applicability decisions.

    A criterion is excluded from the canonical scores if either pass judged it
    not applicable. A criterion scored in only one direction cannot be averaged
    across positions, so keeping it would reintroduce a positional artefact.
    """
    combined: dict[str, str] = {}
    for category in weights:
        states = [
            (result.get(APPLICABILITY_KEY) or {}).get(category, APPLICABLE)
            for result in pass_results.values()
        ]
        for result in pass_results.values():
            for slot in ("A", "B"):
                scores = (result.get("responses", {}).get(slot, {}) or {}).get("scores", {})
                if isinstance(scores, dict) and scores.get(category, 0.0) is None:
                    states.append(NOT_APPLICABLE)
        combined[category] = (
            NOT_APPLICABLE if any(state == NOT_APPLICABLE for state in states) else APPLICABLE
        )
    return combined


def _merge_text_list(values: list[list[str]]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in values:
        for item in group:
            text = str(item).strip()
            key = text.lower()
            if text and key not in seen:
                seen.add(key)
                merged.append(text)
    return merged


def _merge_deductions(notes: list[str]) -> str:
    unique = _merge_text_list([[note] for note in notes])
    meaningful = [note for note in unique if note.lower().rstrip(".") != "no meaningful deduction"]
    if not meaningful:
        return unique[0] if unique else ""
    return " ".join(meaningful)


def identity_view(
    pass_results: dict[str, dict[str, Any]],
    model_key: str,
    weights: dict[str, float],
    applicability: dict[str, str],
) -> dict[str, Any]:
    """Collapse one model's two directional markings into a canonical record."""
    per_pass: dict[str, dict[str, Any]] = {}
    for pass_name, result in pass_results.items():
        slot = slot_for_model(pass_name, model_key)
        per_pass[pass_name] = result.get("responses", {}).get(slot, {}) or {}

    scores: dict[str, float | None] = {}
    position_scores: dict[str, dict[str, float | None]] = {}
    for pass_name, response in per_pass.items():
        slot = slot_for_model(pass_name, model_key)
        raw = response.get("scores", {}) or {}
        position_scores[slot] = {
            category: (None if raw.get(category) is None else float(raw.get(category)))
            for category in weights
        }

    for category in weights:
        if applicability.get(category, APPLICABLE) == NOT_APPLICABLE:
            scores[category] = None
            continue
        values = [
            float(response.get("scores", {}).get(category))
            for response in per_pass.values()
            if response.get("scores", {}).get(category) is not None
        ]
        scores[category] = _mean(values) if values else None

    combined: dict[str, Any] = {
        "scores": scores,
        # Recalculated from the identity-mapped per-criterion averages, not
        # copied from or averaged over the passes' own overall figures.
        "overall": calculate_overall(scores, weights, applicability),
        "strengths": _merge_text_list(
            [list(response.get("strengths", []) or []) for response in per_pass.values()]
        ),
        "weaknesses": _merge_text_list(
            [list(response.get("weaknesses", []) or []) for response in per_pass.values()]
        ),
        "deductions": {
            category: (
                NOT_APPLICABLE_DEDUCTION
                if applicability.get(category, APPLICABLE) == NOT_APPLICABLE
                else _merge_deductions(
                    [
                        str((response.get("deductions", {}) or {}).get(category, ""))
                        for response in per_pass.values()
                    ]
                )
            )
            for category in weights
        },
        "position_scores": position_scores,
        "position_overall": {
            slot_for_model(pass_name, model_key): (
                None if response.get("overall") is None else float(response.get("overall"))
            )
            for pass_name, response in per_pass.items()
        },
    }
    return combined


def _pass_winner_model_key(result: dict[str, Any], pass_name: str) -> str:
    """Map a pass's slot-level winner onto the model identity that won it."""
    slot = str((result.get("winner") or {}).get("response", "")).upper()
    if slot in {"A", "B"}:
        return PASS_SLOT_MODELS[pass_name][slot]
    return "TIE"


def grade_stability(
    flipped: bool,
    positional_difference: float,
    effective_tie_threshold: float,
    clear_winner_threshold: float,
) -> str:
    """Grade stability on three levels instead of a stable/position-sensitive binary.

    Reuses the same two configured classification thresholds rather than
    inventing separate constants to tune:
      - A flip is graded Minor Positional Drift instead of Position Sensitive
        when the slot bias behind it is itself within the effective-tie band.
        A microscopic flip right at the tie boundary is noise around a
        near-zero difference, not a real reversal, so it must not be reported
        with the same severity as a large one.
      - A comparison that keeps the same winner in both orders is downgraded
        from Stable to Minor Positional Drift when the slot bias is at least a
        full clear-winner-sized gap - the label survived, but a bias that
        large is still worth flagging rather than calling fully stable.
    """
    magnitude = abs(positional_difference)
    if flipped:
        if magnitude <= effective_tie_threshold:
            return STABILITY_MINOR_POSITIONAL_DRIFT
        return STABILITY_POSITION_SENSITIVE
    if magnitude >= clear_winner_threshold:
        return STABILITY_MINOR_POSITIONAL_DRIFT
    return STABILITY_STABLE


def stability_report(
    pass_results: dict[str, dict[str, Any]],
    model_name_a: str,
    model_name_b: str,
    effective_tie_threshold: float = EFFECTIVE_TIE_THRESHOLD,
    clear_winner_threshold: float = CLEAR_WINNER_THRESHOLD,
) -> dict[str, Any]:
    names = {"A": model_name_a, "B": model_name_b, "TIE": "Tie"}
    directional = {
        pass_name: _pass_winner_model_key(result, pass_name)
        for pass_name, result in pass_results.items()
    }
    forward_key = directional.get(FORWARD, "TIE")
    reverse_key = directional.get(REVERSE, "TIE")
    # "status" / "position_sensitive" stay exactly the raw flip-based binary
    # they always were, for backward compatibility with anything already
    # reading them. "stability_grade" below is the new, more nuanced field.
    status = STABILITY_STABLE if forward_key == reverse_key else STABILITY_POSITION_SENSITIVE

    slot_totals: dict[str, list[float]] = {"A": [], "B": []}
    for result in pass_results.values():
        for slot in ("A", "B"):
            overall = (result.get("responses", {}).get(slot, {}) or {}).get("overall")
            if overall is not None:
                slot_totals[slot].append(float(overall))
    positional_difference = 0.0
    if slot_totals["A"] and slot_totals["B"]:
        positional_difference = round(
            (sum(slot_totals["A"]) / len(slot_totals["A"]))
            - (sum(slot_totals["B"]) / len(slot_totals["B"])),
            2,
        )

    stability_grade = grade_stability(
        flipped=status == STABILITY_POSITION_SENSITIVE,
        positional_difference=positional_difference,
        effective_tie_threshold=effective_tie_threshold,
        clear_winner_threshold=clear_winner_threshold,
    )

    report: dict[str, Any] = {
        "status": status,
        "position_sensitive": status == STABILITY_POSITION_SENSITIVE,
        "forward_winner_model_key": forward_key,
        "reverse_winner_model_key": reverse_key,
        "forward_winner_model": names.get(forward_key, "Unknown"),
        "reverse_winner_model": names.get(reverse_key, "Unknown"),
        # Positive means the slot labelled Response A scored higher on average,
        # regardless of which model sat there. This is the raw slot effect.
        "positional_difference": positional_difference,
        "slot_a_mean": round(sum(slot_totals["A"]) / len(slot_totals["A"]), 2) if slot_totals["A"] else 0.0,
        "slot_b_mean": round(sum(slot_totals["B"]) / len(slot_totals["B"]), 2) if slot_totals["B"] else 0.0,
        # Graded, headline-friendly stability - see grade_stability() above.
        "stability_grade": stability_grade,
        "stability_grade_label": STABILITY_GRADE_LABELS[stability_grade],
    }
    if status == STABILITY_POSITION_SENSITIVE:
        report["warning"] = (
            f"{POSITION_SENSITIVE_WARNING} First order favoured {report['forward_winner_model']}; "
            f"reversed order favoured {report['reverse_winner_model']}."
        )
        if stability_grade == STABILITY_MINOR_POSITIONAL_DRIFT:
            report["warning"] += (
                " This flip occurred within the effective-tie band, so it is graded as minor "
                "positional drift rather than a full reversal."
            )
    elif stability_grade == STABILITY_MINOR_POSITIONAL_DRIFT:
        report["warning"] = (
            "The winner held in both response orders, but the slot-level score gap "
            f"({positional_difference:+.2f}) is at least as large as the clear-winner threshold. "
            "Treat this comparison's exact margin with some caution."
        )
    return report


def category_agreement(
    pass_results: dict[str, dict[str, Any]],
    weights: dict[str, float],
    applicability: dict[str, str],
) -> dict[str, Any]:
    """How often the forward and reverse passes agree on which model led a category.

    Each applicable category is bucketed into agreeing, disagreeing, or tied,
    working entirely from the per-category scores each pass already recorded
    (no new judge calls). A category counts as tied - and is excluded from the
    agreeing/disagreeing counts, per its own neutral bucket - whenever either
    pass itself scored that category an exact draw between the two responses;
    an exact draw is not evidence of agreement or disagreement, just an
    absence of a signal either way for that category in that pass.

    agreement_fraction is agreeing / (agreeing + disagreeing). When every
    applicable category was tied in at least one pass, there is no decisive
    category evidence at all, so the fraction defaults to 1.0 (no observed
    disagreement) rather than being undefined.
    """
    agreeing = 0
    disagreeing = 0
    tied = 0
    breakdown: dict[str, str] = {}

    for category in weights:
        if applicability.get(category, APPLICABLE) == NOT_APPLICABLE:
            continue

        leaders: dict[str, str | None] = {}
        for pass_name, result in pass_results.items():
            responses = result.get("responses", {}) or {}
            score_a = (responses.get("A", {}) or {}).get("scores", {}).get(category)
            score_b = (responses.get("B", {}) or {}).get("scores", {}).get(category)
            if score_a is None or score_b is None:
                leaders[pass_name] = None
                continue
            if score_a > score_b:
                slot_leader = "A"
            elif score_b > score_a:
                slot_leader = "B"
            else:
                slot_leader = "TIE"
            # Map the slot leader back to the canonical model_key ("A"/"B" as
            # passed to combine_bidirectional_results) so the two passes are
            # directly comparable rather than comparing raw slot letters.
            leaders[pass_name] = slot_leader if slot_leader == "TIE" else PASS_SLOT_MODELS[pass_name][slot_leader]

        forward_leader = leaders.get(FORWARD)
        reverse_leader = leaders.get(REVERSE)
        if forward_leader is None or reverse_leader is None:
            continue
        if forward_leader == "TIE" or reverse_leader == "TIE":
            tied += 1
            breakdown[category] = "tied"
        elif forward_leader == reverse_leader:
            agreeing += 1
            breakdown[category] = "agree"
        else:
            disagreeing += 1
            breakdown[category] = "disagree"

    decisive = agreeing + disagreeing
    fraction = round(agreeing / decisive, 4) if decisive else 1.0
    return {
        "agreement_fraction": fraction,
        "agreeing": agreeing,
        "disagreeing": disagreeing,
        "tied": tied,
        "categories": breakdown,
    }


# --- Composite confidence ----------------------------------------------------
#
# Confidence for a canonical bidirectional result is a bounded, transparent
# combination of signals the protocol already computes - score separation,
# stability grade, category agreement, and verdict/score conflict - rather
# than a function of score gap alone. It is deliberately simple arithmetic
# (a base estimate scaled by capped multiplicative factors), not a statistical
# model: see this module's docstring / the deferred calibration work for what
# a fuller noise-calibrated version would add later.

# Confidence never reports below a coin flip, and never claims near-certainty
# from a two-pass local judge - even a large, stable gap stops short of 100%.
CONFIDENCE_FLOOR = 50
CONFIDENCE_CEILING = 95

# Base confidence scales linearly with score separation before any signal
# below is applied: intercept is the floor itself, slope mirrors the previous
# single-signal formula's rate (roughly +10 points of confidence per full
# point of score gap).
BASE_CONFIDENCE_INTERCEPT = 50
BASE_CONFIDENCE_SLOPE = 10

# Multiplicative stability penalties applied to the base confidence. A full
# position-sensitive flip is the most severe reliability problem the protocol
# can detect, so it is penalised hardest; minor positional drift (see
# grade_stability) is real but far less severe.
POSITION_SENSITIVE_CONFIDENCE_FACTOR = 0.5
MINOR_POSITIONAL_DRIFT_CONFIDENCE_FACTOR = 0.85

# Category agreement scales confidence between this floor factor (at 0%
# agreement) and 1.0 (at 100% agreement), so a result whose categories are
# split evenly cannot report as confident as one where they all agree.
CATEGORY_AGREEMENT_MIN_FACTOR = 0.6

# A written verdict that contradicts the calculated numeric winner is treated
# as a distinct, more severe reliability problem than ordinary score noise -
# it means the judge's own prose disagreed with its own scores - so it caps
# confidence outright rather than being blended proportionally like the
# factors above.
VERDICT_CONFLICT_CONFIDENCE_CAP = 60


def calculate_composite_confidence(
    score_difference: float,
    stability_grade: str,
    category_agreement_fraction: float | None,
    verdict_conflict: bool,
) -> int:
    """Bounded confidence combining score separation, stability, and agreement.

    Effective Ties (small score_difference) naturally land near the floor
    through the base formula alone, with no special case required. Position
    sensitivity and category disagreement further discount an already-modest
    base estimate; a verdict/score conflict then caps the result regardless
    of how confident the arithmetic above would otherwise be.
    """
    base = BASE_CONFIDENCE_INTERCEPT + (score_difference * BASE_CONFIDENCE_SLOPE)

    if stability_grade == STABILITY_POSITION_SENSITIVE:
        base *= POSITION_SENSITIVE_CONFIDENCE_FACTOR
    elif stability_grade == STABILITY_MINOR_POSITIONAL_DRIFT:
        base *= MINOR_POSITIONAL_DRIFT_CONFIDENCE_FACTOR

    if category_agreement_fraction is not None:
        agreement_factor = CATEGORY_AGREEMENT_MIN_FACTOR + (
            (1.0 - CATEGORY_AGREEMENT_MIN_FACTOR) * category_agreement_fraction
        )
        base *= agreement_factor

    base = max(CONFIDENCE_FLOOR, min(CONFIDENCE_CEILING, base))
    if verdict_conflict:
        base = min(base, VERDICT_CONFLICT_CONFIDENCE_CAP)
    return int(round(base))


def canonical_final_verdict(
    winner_key: str,
    model_name_a: str,
    model_name_b: str,
    stability: dict[str, Any],
    score_a: float,
    score_b: float,
    classification: str,
    effective_tie_threshold: float,
    clear_winner_threshold: float,
) -> str:
    """Headline sentence. Must never describe an Effective Tie as a clean win."""
    names = {"A": model_name_a, "B": model_name_b}
    scores = f"{model_name_a} scored {score_a:.2f} and {model_name_b} scored {score_b:.2f}"
    gap = round(abs(score_a - score_b), 2)

    if classification == CLASSIFICATION_EFFECTIVE_TIE:
        if winner_key in names:
            headline = (
                f"Effective Tie: {scores}. {names[winner_key]} leans marginally ahead, but the gap "
                f"({gap:.2f}) is within the effective-tie threshold ({effective_tie_threshold:.2f}), "
                "so this is not a clean win."
            )
        else:
            headline = (
                f"Effective Tie: {scores}. The comparison is level after both response orders were judged."
            )
    elif classification == CLASSIFICATION_NARROW_PREFERENCE:
        headline = (
            f"Narrow Preference for {names.get(winner_key, 'neither side')} on the position-averaged "
            f"scores: {scores}. The gap ({gap:.2f}) clears the effective-tie threshold but stays below "
            f"the clear-winner threshold ({clear_winner_threshold:.2f})."
        )
    elif winner_key == "TIE":
        headline = f"The comparison is level after both response orders were judged: {scores}."
    else:
        headline = f"{names[winner_key]} is a Clear Winner on the position-averaged scores: {scores}."

    if stability.get("position_sensitive"):
        return (
            f"{headline} This result is position-sensitive: the first order favoured "
            f"{stability.get('forward_winner_model')} and the reversed order favoured "
            f"{stability.get('reverse_winner_model')}, so it should not be read as a clean win."
        )
    if stability.get("stability_grade") == STABILITY_MINOR_POSITIONAL_DRIFT:
        return f"{headline} This result shows minor positional drift; treat the exact margin with some caution."
    return f"{headline} The same model was ahead in both response orders."


def _verdict_consistency_summary(pass_results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Merge each pass's verdict_consistency (from judge.enforce_verdict_consistency).

    "conflict" is true when either directional pass found its written verdict
    disagreeing with its own calculated numeric winner. This never changes
    which model the canonical winner names - see calculate_winner /
    enforce_verdict_consistency - it only feeds the confidence penalty below
    and is exposed here so the conflict is visible in detailed reporting.
    """
    per_pass = {
        pass_name: (result.get("verdict_consistency") or {"status": "not_checked"})
        for pass_name, result in pass_results.items()
    }
    statuses = {entry.get("status") for entry in per_pass.values()}
    if "conflict" in statuses:
        status = "conflict"
    elif "ok" in statuses:
        status = "ok"
    else:
        status = "not_checked"
    return {
        "status": status,
        "conflict": status == "conflict",
        FORWARD: per_pass.get(FORWARD),
        REVERSE: per_pass.get(REVERSE),
    }


def combine_bidirectional_results(
    forward_result: dict[str, Any],
    reverse_result: dict[str, Any],
    model_name_a: str,
    model_name_b: str,
    extra_score_categories: list[str] | None = None,
    effective_tie_threshold: float | None = None,
    clear_winner_threshold: float | None = None,
) -> dict[str, Any]:
    """Build one identity-mapped canonical result from two directional passes.

    forward_result must come from a call where slot A held model A, and
    reverse_result from a call where slot A held model B.

    effective_tie_threshold / clear_winner_threshold default to the values in
    llmbench.judge (in turn sourced from llmbench.config) when not given
    explicitly, so existing callers that only pass the four positional
    arguments keep working unchanged.
    """
    effective_tie_threshold = (
        EFFECTIVE_TIE_THRESHOLD if effective_tie_threshold is None else effective_tie_threshold
    )
    clear_winner_threshold = (
        CLEAR_WINNER_THRESHOLD if clear_winner_threshold is None else clear_winner_threshold
    )

    weights = scoring_categories(extra_score_categories)
    pass_results = {FORWARD: forward_result, REVERSE: reverse_result}
    for pass_name, result in pass_results.items():
        responses = (result or {}).get("responses")
        if not isinstance(responses, dict) or not {"A", "B"} <= set(responses):
            raise JudgeError(
                f"The {pass_name} judge pass did not return canonical responses for A and B, "
                "so the two orders cannot be combined."
            )
    applicability = combined_applicability(pass_results, weights)

    responses = {
        "A": identity_view(pass_results, "A", weights, applicability),
        "B": identity_view(pass_results, "B", weights, applicability),
    }
    winner = calculate_winner(responses, effective_tie_threshold, clear_winner_threshold)
    winner["model_name"] = {
        "A": model_name_a,
        "B": model_name_b,
        "TIE": "Tie",
    }.get(str(winner.get("response", "")).upper(), "")

    stability = stability_report(
        pass_results, model_name_a, model_name_b, effective_tie_threshold, clear_winner_threshold
    )
    category_agreement_info = category_agreement(pass_results, weights, applicability)
    verdict_consistency = _verdict_consistency_summary(pass_results)

    # Tie language in the judge's own prose can only nudge an already-borderline
    # Narrow Preference down to an Effective Tie - it is corroborating evidence
    # for a result that was already close, never grounds to override a Clear
    # Winner or a stable, larger gap on its own.
    judge_declared_tie = any(verdict_declares_tie(result.get("final_verdict", "")) for result in pass_results.values())
    classification = winner["classification"]
    if classification == CLASSIFICATION_NARROW_PREFERENCE and judge_declared_tie:
        classification = CLASSIFICATION_EFFECTIVE_TIE
    winner["classification"] = classification
    winner["classification_label"] = CLASSIFICATION_LABELS[classification]
    winner["judge_declared_tie"] = judge_declared_tie

    winner["confidence"] = calculate_composite_confidence(
        score_difference=float(winner.get("score_difference", 0.0)),
        stability_grade=stability.get("stability_grade", STABILITY_STABLE),
        category_agreement_fraction=category_agreement_info.get("agreement_fraction"),
        verdict_conflict=verdict_consistency.get("conflict", False),
    )

    combined: dict[str, Any] = {
        APPLICABILITY_KEY: applicability,
        "responses": responses,
        "winner": winner,
        # The forward pass placed model A in slot A, so its Response A / Response B
        # wording already lines up with the canonical A / B identities.
        "comparison": deepcopy(forward_result.get("comparison", {})),
        "category_agreement": category_agreement_info,
        "verdict_consistency": verdict_consistency,
        "final_verdict": canonical_final_verdict(
            str(winner.get("response", "")).upper(),
            model_name_a,
            model_name_b,
            stability,
            float(responses["A"]["overall"]),
            float(responses["B"]["overall"]),
            classification,
            effective_tie_threshold,
            clear_winner_threshold,
        ),
        "protocol": {
            "mode": PROTOCOL_MODE_BIDIRECTIONAL,
            "version": PROTOCOL_VERSION,
            "model_names": {"A": model_name_a, "B": model_name_b},
            "stability": stability,
            "thresholds": {
                "effective_tie_threshold": effective_tie_threshold,
                "clear_winner_threshold": clear_winner_threshold,
            },
            "passes": [
                {
                    "pass": FORWARD,
                    "order": "Response A = Model A, Response B = Model B",
                    "slot_models": {"A": model_name_a, "B": model_name_b},
                    "result": deepcopy(forward_result),
                },
                {
                    "pass": REVERSE,
                    "order": "Response A = Model B, Response B = Model A",
                    "slot_models": {"A": model_name_b, "B": model_name_a},
                    "result": deepcopy(reverse_result),
                },
            ],
        },
    }
    return combined


def judge_comparison_bidirectional(
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
    on_pass_start: ProgressCallback | None = None,
    judge_call: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Judge one comparison in both response orders and combine the results.

    The two calls are fully independent judge invocations. The candidate
    responses are passed through unmodified in both directions - only their slot
    assignment changes - so neither model is ever re-generated or rewritten.
    """
    call = judge_call or judge_comparison
    shared = {
        "config": config,
        "rubric": rubric,
        "judging_profile": judging_profile,
        "conversation_context": conversation_context,
        "current_prompt": current_prompt,
        "model": model,
        "temperature": temperature,
        "endpoint": endpoint,
        "extra_headers": extra_headers,
        "extra_score_categories": extra_score_categories,
    }

    if on_pass_start:
        on_pass_start(FORWARD, prompt_id)
    forward_result = call(
        response_a=response_a,
        response_b=response_b,
        model_name_a=model_name_a,
        model_name_b=model_name_b,
        prompt_id=f"{prompt_id}#{FORWARD}" if prompt_id else FORWARD,
        **shared,
    )

    if on_pass_start:
        on_pass_start(REVERSE, prompt_id)
    reverse_result = call(
        response_a=response_b,
        response_b=response_a,
        model_name_a=model_name_b,
        model_name_b=model_name_a,
        prompt_id=f"{prompt_id}#{REVERSE}" if prompt_id else REVERSE,
        **shared,
    )

    return combine_bidirectional_results(
        forward_result,
        reverse_result,
        model_name_a,
        model_name_b,
        extra_score_categories,
        *resolve_scoring_thresholds(config),
    )


def resolve_scoring_thresholds(config: AppConfig | None) -> tuple[float, float]:
    """Pull the configured classification thresholds off AppConfig.scoring.

    Defensive against config being None (as in unit tests that drive
    judge_comparison_bidirectional with a mock judge_call and no real config)
    or an older in-memory AppConfig built before the scoring section existed.
    """
    scoring = getattr(config, "scoring", None)
    effective_tie_threshold = getattr(scoring, "effective_tie_threshold", EFFECTIVE_TIE_THRESHOLD)
    clear_winner_threshold = getattr(scoring, "clear_winner_threshold", CLEAR_WINNER_THRESHOLD)
    return effective_tie_threshold, clear_winner_threshold


def result_protocol_mode(result: dict[str, Any]) -> str:
    """Bidirectional results carry a protocol block; older records do not."""
    protocol = result.get("protocol") if isinstance(result, dict) else None
    if isinstance(protocol, dict) and protocol.get("mode") == PROTOCOL_MODE_BIDIRECTIONAL:
        return PROTOCOL_MODE_BIDIRECTIONAL
    return PROTOCOL_MODE_LEGACY_SINGLE_PASS


def is_legacy_single_pass(result: dict[str, Any]) -> bool:
    return result_protocol_mode(result) == PROTOCOL_MODE_LEGACY_SINGLE_PASS


def result_stability(result: dict[str, Any]) -> dict[str, Any] | None:
    """Stability block for a bidirectional result, or None for legacy records.

    A legacy single-pass record is never reinterpreted as stable: it was judged
    in one order only, so its stability is simply unknown.
    """
    if is_legacy_single_pass(result):
        return None
    stability = (result.get("protocol") or {}).get("stability")
    return stability if isinstance(stability, dict) else None


def result_directional_passes(result: dict[str, Any]) -> list[dict[str, Any]]:
    if is_legacy_single_pass(result):
        return []
    passes = (result.get("protocol") or {}).get("passes")
    return passes if isinstance(passes, list) else []


def normalise_model_token(value: str) -> str:
    """Reduce a model name or GGUF filename to a comparable token."""
    text = str(value or "").strip().lower()
    text = re.sub(r"[\\/]+", "/", text).rsplit("/", 1)[-1]
    text = re.sub(r"\.(gguf|bin|safetensors|pt|pth)$", "", text)
    return re.sub(r"[^a-z0-9]+", "", text)


def judge_is_candidate(judge_model: str, candidate_names: list[str]) -> list[str]:
    """Candidate models that look like the judge model itself.

    Self-judging stays permitted, but it has to be disclosed: a model marking
    its own output is a conflict of interest the reader must be able to see.
    """
    judge_token = normalise_model_token(judge_model)
    if not judge_token:
        return []
    matches = []
    for name in candidate_names:
        token = normalise_model_token(name)
        if not token:
            continue
        if token == judge_token or (len(token) >= 6 and token in judge_token) or (
            len(judge_token) >= 6 and judge_token in token
        ):
            matches.append(name)
    return matches


def self_judging_disclosure(judge_model: str, model_name_a: str, model_name_b: str) -> dict[str, Any]:
    matches = judge_is_candidate(judge_model, [model_name_a, model_name_b])
    disclosure: dict[str, Any] = {
        "judge_model": judge_model,
        "judge_is_candidate": bool(matches),
        "matched_candidates": matches,
    }
    if matches:
        disclosure["warning"] = (
            f"The selected judge ({judge_model}) appears to be one of the candidate models "
            f"({', '.join(matches)}). This comparison is self-judged and carries a conflict of interest. "
            "Bidirectional judging removes the slot effect but cannot remove a preference for the "
            "judge's own style."
        )
    return disclosure

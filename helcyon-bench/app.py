from __future__ import annotations

import html
import hashlib
import inspect
import json
import logging
import os
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from llmbench.api import (
    ApiError,
    debug_logging_enabled,
    list_models,
    post_lifecycle_hook,
    preflight_chat_completion,
    set_debug_logging,
)
from llmbench.config import ConfigError, judge_endpoint_by_name, load_config, save_judge_endpoint_api_key
from llmbench.judge import (
    APPLICABILITY_KEY,
    CATEGORY_WEIGHTS,
    CLASSIFICATION_CLEAR_WINNER,
    CLASSIFICATION_EFFECTIVE_TIE,
    CLASSIFICATION_LABELS,
    CLASSIFICATION_NARROW_PREFERENCE,
    CLEAR_WINNER_THRESHOLD,
    EFFECTIVE_TIE_THRESHOLD,
    NOT_APPLICABLE,
    NOT_APPLICABLE_LABEL,
    JudgeError,
    classify_score_gap,
)
from llmbench.judging_profiles import DEFAULT_PROFILE_ID, get_judging_profile, infer_profile_id
from llmbench.protocol import (
    FORWARD,
    POSITION_SENSITIVE_WARNING,
    PROTOCOL_MODE_BIDIRECTIONAL,
    PROTOCOL_VERSION,
    REVERSE,
    STABILITY_GRADE_LABELS,
    is_legacy_single_pass,
    judge_comparison_bidirectional,
    result_directional_passes,
    result_stability,
    self_judging_disclosure,
)


APP_NAME = "Helcyon-Bench"
APP_SLUG = "helcyon-bench"
APP_DIR = Path(__file__).resolve().parent
LOGGER = logging.getLogger(__name__)
JUDGE_PIPELINE_DEBUG_PATH = APP_DIR / "judge_logs" / "judge_pipeline_debug.jsonl"
RUBRIC_DIR = APP_DIR / "rubrics"
ACTIVE_RUBRIC_FILENAME = "companion.md"
DISTRESS_RUBRIC_FILENAME = "distress_calibration.md"
RUBRIC_FILENAMES_BY_KEY = {
    "companion": ACTIVE_RUBRIC_FILENAME,
}
RUBRIC_FILENAME_BY_PACK_CATEGORY = {
    "Creativity": "creativity.md",
    "Philosophy": "philosophy.md",
    "Morals": "morals.md",
    "Uncensored": "uncensored.md",
    "Humour": "humour.md",
}
EXTRA_SCORE_CATEGORIES_BY_PACK_CATEGORY = {
    "Creativity": ["Creativity"],
    "Philosophy": ["Philosophical Depth"],
    "Morals": ["Moral Reasoning"],
    "Uncensored": ["Uncensored"],
}
# The distress-calibration addendum is appended (never substituted) after the
# primary rubric when a pack targets empathy/distress content. It adds no score
# category — it refines companion.md's existing Emotional Presence judgement.
DISTRESS_SIGNAL_CATEGORIES = {"empathy"}
DISTRESS_SIGNAL_TERMS = (
    "distress",
    "grief",
    "grieving",
    "bereave",
    "mourning",
    "crisis",
    "suicid",
    "self-harm",
    "self harm",
    "trauma",
    "panic attack",
)


# Each comparison is judged once per response order.
PASSES_PER_COMPARISON = 2


COMPARISON_LABELS = {
    "more_natural": "Which response feels more natural?",
    "better_frame_following": "Which follows the user's frame better?",
    "stronger_emotional_presence": "Which shows stronger emotional presence?",
    "better_evidence_discipline": "Which demonstrates better evidence discipline?",
    "better_conclusion": "Which lands the conclusion better?",
    "more_enjoyable": "Which is more enjoyable to read?",
    "weaknesses": "Which contains weaknesses?",
}


def log_judge_pipeline_debug(stage: str, payload: dict) -> None:
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

PERSISTED_FIELDS = [
    ("model_name_a_input", "Model Name A", "input"),
    ("model_name_b_input", "Model Name B", "input"),
]

STALE_MODEL_NAME_PLACEHOLDERS = {"Fresh A", "Fresh B"}

BATCH_SLOT_COUNT = 5
PACK_METADATA_OPTIONS = [
    "Companion",
    "Deep Conversation",
    "Morals",
    "Creativity",
    "Philosophy",
    "Roleplay",
    "Humour",
    "Coding",
    "Editing",
    "Planning",
    "Reasoning",
    "Uncensored",
    "General",
]
for slot in range(1, BATCH_SLOT_COUNT + 1):
    PERSISTED_FIELDS.extend(
        [
            (f"batch_prompt_{slot}", f"Prompt {slot}", "textarea"),
            (f"batch_response_a_{slot}", f"Response A {slot}", "textarea"),
            (f"batch_response_b_{slot}", f"Response B {slot}", "textarea"),
        ]
    )

PERSISTED_SELECT_KEYS = [
    "judge_endpoint_select",
    "judge_model_select",
]

PACK_EDITOR_FIELDS = [
    "pack_editor_name_input",
    "pack_editor_category_select",
    "pack_editor_description_input",
    "pack_editor_judge_profile_select",
]
for slot in range(1, BATCH_SLOT_COUNT + 1):
    PACK_EDITOR_FIELDS.extend([f"pack_editor_title_{slot}", f"pack_editor_prompt_{slot}"])

PROMPT_PACK_DIR = APP_DIR / "prompt_packs"
DEFAULT_PROMPT_PACK_PATH = PROMPT_PACK_DIR / "helcyon_companion_v1.json"
FALLBACK_PROMPT_PACK_PATH = PROMPT_PACK_DIR / "starter_companion_v1.json"
FORM_STATE_PATH = APP_DIR / ".llmbench_form_state.json"
MODEL_RESPONSE_DIR = APP_DIR / "model_responses"
BENCHMARK_SESSION_DIR = APP_DIR / "benchmark_sessions"
BENCHMARK_DIR = APP_DIR / "benchmarks"
DASHBOARD_MODEL_ALIASES_PATH = APP_DIR / "dashboard_model_aliases.json"
STARTER_COMPANION_DOCUMENT = {
    "name": "Starter Companion V1",
    "category": "Companion",
    "description": "Starter companion prompts for warmth, empathy, humour, and everyday company.",
    "judge_profile": "Companion",
    "prompts": [
        {
            "title": "Lonely",
            "prompt": "I had one of those days where nothing huge happened, but everything felt heavy. I feel like I'm missing out on life.. on having fun, on being with someone special.  I could really use your company right now.",
        },
        {
            "title": "Regret",
            "prompt": "I was just thinking back to all the missed opportunities. I made so many mistakes because I didn't realize reality is a refection of my assumption. But now I've learned about the law, I never get any opportunities any more. I don't know what's going on.. I thought the opportunities would start flowing in now I choose not to be alone.",
        },
        {
            "title": "Boredom",
            "prompt": "I've been feeling so bored lately - nothing on TV and nowhere to go. What's a fun thing we can do together?",
        },
        {
            "title": "Life in the slow lane",
            "prompt": "I'm just one of these people who prefers life in the slow lane. Watching a sunset, or looking up to the night sky and thinking about where we came from, than wanting to achieve success and work hard. Appreciating where I am just feels more important, you know?",
        },
        {
            "title": "Watch party",
            "prompt": "I feel like we should have a watch party..  Maybe watch breaking Bad again from the start?",
        },
    ],
}
LAST_PROMPT_PACK_STATE_FIELDS = {
    "name": "last_prompt_pack_name",
    "category": "last_prompt_pack_category",
    "description": "last_prompt_pack_description",
    "judge_profile": "last_prompt_pack_judge_profile",
    "rubric": "last_prompt_pack_rubric",
}


def persisted_field_keys() -> list[str]:
    return [key for key, _, _ in PERSISTED_FIELDS] + PACK_EDITOR_FIELDS


def benchmark_field_keys() -> list[str]:
    return [key for key, _, _ in PERSISTED_FIELDS]


def load_form_state(path: Path = FORM_STATE_PATH) -> dict[str, str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items() if value is not None}


def normalize_prompt_pack_metadata(metadata: dict | None, fallback_name: str = "") -> dict[str, str]:
    metadata = metadata or {}
    return {
        "name": str(metadata.get("name") or fallback_name).strip(),
        "category": str(metadata.get("category") or "General").strip() or "General",
        "description": str(metadata.get("description") or ""),
        "judge_profile": str(metadata.get("judge_profile") or "General").strip() or "General",
        "rubric": str(metadata.get("rubric") or "").strip(),
    }


def prompt_pack_metadata_form_state_values(metadata: dict[str, str]) -> dict[str, str]:
    metadata = normalize_prompt_pack_metadata(metadata)
    return {state_key: metadata[field] for field, state_key in LAST_PROMPT_PACK_STATE_FIELDS.items()}


def prompt_pack_metadata_from_form_state(state: dict[str, str]) -> dict[str, str] | None:
    if not any(key in state for key in LAST_PROMPT_PACK_STATE_FIELDS.values()):
        return None
    return normalize_prompt_pack_metadata(
        {field: state.get(state_key, "") for field, state_key in LAST_PROMPT_PACK_STATE_FIELDS.items()}
    )


def remember_prompt_pack_metadata(metadata: dict[str, str], persist: bool = False) -> dict[str, str]:
    normalized = normalize_prompt_pack_metadata(metadata)
    st.session_state.last_prompt_pack_metadata = normalized
    st.session_state.last_prompt_pack_category = normalized["category"]
    if persist:
        update_form_state_values(prompt_pack_metadata_form_state_values(normalized))
    return normalized


def save_form_state(state: dict[str, str], path: Path = FORM_STATE_PATH) -> None:
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def remove_form_state(keys: list[str] | None = None, path: Path = FORM_STATE_PATH) -> None:
    if keys is None:
        try:
            path.unlink()
        except OSError:
            pass
        return
    state = load_form_state(path)
    for key in keys:
        state.pop(key, None)
    save_form_state(state, path)


def update_form_state_values(values: dict[str, str], path: Path = FORM_STATE_PATH) -> None:
    state = load_form_state(path)
    state.update(values)
    save_form_state(state, path)


def hydrate_form_state(path: Path = FORM_STATE_PATH) -> None:
    state = load_form_state(path)
    clear_keys = set(st.session_state.get("clear_persistence_keys", []))
    full_clear_requested = bool(st.session_state.get("clear_persistence_nonce"))
    for key in persisted_field_keys():
        if key in clear_keys or full_clear_requested:
            continue
        if key in state:
            if key in {"model_name_a_input", "model_name_b_input"} and state[key] in STALE_MODEL_NAME_PLACEHOLDERS:
                continue
            saved_value = state[key]
            current_value = str(st.session_state.get(key, ""))
            if key not in st.session_state or (current_value == "" and saved_value != ""):
                st.session_state[key] = saved_value
    metadata = prompt_pack_metadata_from_form_state(state)
    if metadata and "last_prompt_pack_metadata" not in st.session_state:
        remember_prompt_pack_metadata(metadata)
    if "prompt_pack_category_select" not in st.session_state and "prompt_pack_category_select" in state:
        st.session_state.prompt_pack_category_select = state["prompt_pack_category_select"]
    elif metadata and "prompt_pack_category_select" not in st.session_state:
        st.session_state.prompt_pack_category_select = metadata["category"]
    if "prompt_pack_select" not in st.session_state and "prompt_pack_select" in state:
        st.session_state.prompt_pack_select = state["prompt_pack_select"]
    for key in PERSISTED_SELECT_KEYS:
        if key in clear_keys or full_clear_requested:
            continue
        if key not in st.session_state and state.get(key):
            st.session_state[key] = state[key]
    st.session_state._llmbench_form_state_hydrated = True


def preserve_benchmark_widget_state() -> None:
    # Streamlit drops session state for widgets that are not rendered on the
    # current run, so a visit to the Results Dashboard would blank every
    # benchmark-form field. Re-assigning each value marks it as programmatic
    # state, which survives until the widgets render again.
    for key in persisted_field_keys() + ["prompt_pack_category_select", "prompt_pack_select"]:
        if key in st.session_state:
            st.session_state[key] = st.session_state[key]


def restore_benchmark_widget_values() -> None:
    # Streamlit only sends a widget's value to the browser when its key is
    # assigned through the Session State API during the same run that renders
    # the widget. After a Results Dashboard or Overall Leaderboard visit the
    # values survive server-side, but the remounted browser widgets would
    # render empty, so re-assign every persisted key on the run that returns
    # to the benchmark view, before the widgets are created.
    preserve_benchmark_widget_state()


def persist_current_form_state(path: Path = FORM_STATE_PATH) -> None:
    state = load_form_state(path)
    clear_keys = set(st.session_state.get("clear_persistence_keys", []))
    full_clear_requested = bool(st.session_state.get("clear_persistence_nonce"))
    for key in persisted_field_keys():
        if key in st.session_state:
            value = str(st.session_state.get(key, ""))
            if (
                value == ""
                and state.get(key, "") != ""
                and key not in clear_keys
                and not full_clear_requested
            ):
                continue
            state[key] = value
    if st.session_state.get("prompt_pack_category_select"):
        state["prompt_pack_category_select"] = str(st.session_state.prompt_pack_category_select)
    if st.session_state.get("prompt_pack_select"):
        state["prompt_pack_select"] = str(st.session_state.prompt_pack_select)
    for key in PERSISTED_SELECT_KEYS:
        if st.session_state.get(key):
            state[key] = str(st.session_state[key])
    metadata = st.session_state.get("last_prompt_pack_metadata")
    if isinstance(metadata, dict):
        state.update(prompt_pack_metadata_form_state_values(metadata))
    save_form_state(state, path)


def persist_and_inject_form_state() -> None:
    persist_current_form_state()
    inject_persistence_script(
        st.session_state.pop("clear_persistence_nonce", None),
        st.session_state.pop("clear_persistence_keys", []),
    )


def prompt_pack_label(path: Path) -> str:
    try:
        name = str(load_prompt_pack_document(path).get("name") or "").strip()
        if name:
            return name
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return path.stem.replace("_", " ").replace("-", " ").title()


def inferred_prompt_pack_category(path: Path) -> str:
    stem = path.stem.lower().replace("-", "_")
    if "companion" in stem or "empathy" in stem:
        return "Companion"
    profile = get_judging_profile(infer_profile_id(stem))
    if profile.name != "General":
        return profile.name
    return "General"


def prompt_pack_category(path: Path) -> str:
    try:
        category = str(load_prompt_pack_document(path).get("category") or "").strip()
    except (OSError, json.JSONDecodeError, ValueError):
        category = ""
    return category or inferred_prompt_pack_category(path)


def available_prompt_packs() -> dict[str, Path]:
    if not PROMPT_PACK_DIR.exists():
        return {}
    packs = sorted(PROMPT_PACK_DIR.glob("*.json"), key=lambda item: item.name.lower())
    if not FALLBACK_PROMPT_PACK_PATH.exists():
        packs.append(FALLBACK_PROMPT_PACK_PATH)
    return {prompt_pack_label(path): path for path in packs}


def unique_pack_label(labels: dict[str, Path], label: str, path: Path) -> str:
    if label not in labels:
        return label
    return f"{label} ({path.stem})"


def available_prompt_pack_groups() -> dict[str, dict[str, Path]]:
    groups: dict[str, dict[str, Path]] = {}
    if not PROMPT_PACK_DIR.exists():
        return groups
    packs = sorted(PROMPT_PACK_DIR.glob("*.json"), key=lambda item: item.name.lower())
    if not FALLBACK_PROMPT_PACK_PATH.exists():
        packs.append(FALLBACK_PROMPT_PACK_PATH)
    for path in packs:
        category = prompt_pack_category(path)
        label = prompt_pack_label(path)
        category_group = groups.setdefault(category, {})
        category_group[unique_pack_label(category_group, label, path)] = path
    return dict(sorted(groups.items(), key=lambda item: item[0].lower()))


def ordered_pack_categories(groups: dict[str, dict[str, Path]]) -> list[str]:
    default_categories = [category for category in PACK_METADATA_OPTIONS if category in groups]
    extra_categories = sorted(
        [category for category in groups if category not in PACK_METADATA_OPTIONS],
        key=str.lower,
    )
    return default_categories + extra_categories


def selected_prompt_pack_path() -> Path:
    # Resolution order matters: an exact (category, label) match always wins.
    # Anything past that is a fallback for stale/mismatched selection state, and must
    # never silently guess an arbitrary *different* pack within the selected category
    # — that previously caused Save/Delete actions to target the wrong file. See
    # helcyon-bench-prompt-pack-save-bug memory for the incident this guards against.
    groups = available_prompt_pack_groups()
    selected_category = st.session_state.get("prompt_pack_category_select")
    selected_label = st.session_state.get("prompt_pack_select")
    if selected_category in groups and selected_label in groups[selected_category]:
        return groups[selected_category][selected_label]
    for category_group in groups.values():
        if selected_label in category_group:
            return category_group[selected_label]

    packs = available_prompt_packs()
    if selected_label in packs:
        return packs[selected_label]
    if DEFAULT_PROMPT_PACK_PATH.exists():
        return DEFAULT_PROMPT_PACK_PATH
    if FALLBACK_PROMPT_PACK_PATH.exists():
        return FALLBACK_PROMPT_PACK_PATH
    if selected_category in groups and groups[selected_category]:
        return next(iter(groups[selected_category].values()))
    if packs:
        return next(iter(packs.values()))
    return DEFAULT_PROMPT_PACK_PATH


def initial_prompt_pack_path() -> Path | None:
    if DEFAULT_PROMPT_PACK_PATH.exists():
        return DEFAULT_PROMPT_PACK_PATH
    return FALLBACK_PROMPT_PACK_PATH


def prompt_pack_document_from_raw(raw: object, fallback_name: str = "Imported Prompt Pack") -> dict:
    source = raw
    if isinstance(raw, dict):
        for key in (
            "prompt_pack",
            "promptPack",
            "prompt_pack_document",
            "helcyon_bench_prompt_pack",
            "helcyonBenchPromptPack",
            "pack",
        ):
            candidate = raw.get(key)
            if isinstance(candidate, (dict, list)):
                source = candidate
                break

    rubric = ""
    if isinstance(source, list):
        name = fallback_name
        category = "General"
        description = ""
        judge_profile = "General"
        raw_prompts = source
    elif isinstance(source, dict):
        name = str(source.get("name") or source.get("title") or fallback_name)
        raw_category = str(source.get("category") or source.get("profile") or "").strip()
        description = str(source.get("description") or "")
        rubric = str(source.get("rubric") or "").strip()
        judge_profile = str(
            source.get("judge_profile")
            or source.get("judging_profile")
            or source.get("judgeProfile")
            or raw_category
            or "General"
        )
        raw_prompts = None
        for key in ("prompts", "items", "entries", "benchmark_prompts", "benchmarkPrompts"):
            if isinstance(source.get(key), list):
                raw_prompts = source[key]
                break
        if not isinstance(raw_prompts, list):
            raise ValueError("Imported prompt pack JSON needs a prompts array.")
        category = raw_category or get_judging_profile(judge_profile).name or "General"
    else:
        raise ValueError("Imported prompt pack must be a JSON object or array.")

    prompts = []
    for index, item in enumerate(raw_prompts, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Imported prompt {index} must be an object.")
        prompt = str(
            item.get("prompt")
            or item.get("current_prompt")
            or item.get("question")
            or item.get("text")
            or item.get("content")
            or item.get("instruction")
            or ""
        ).strip()
        if not prompt:
            raise ValueError(f"Imported prompt {index} is missing prompt text.")
        title = str(item.get("title") or item.get("name") or item.get("label") or "").strip()
        prompts.append(
            {
                "id": str(item.get("id") or f"prompt_{index:02d}"),
                "category": str(item.get("category") or ""),
                "title": title or title_from_prompt(prompt, index),
                "evaluation_focus": str(item.get("evaluation_focus") or item.get("evaluationFocus") or ""),
                "prompt": prompt,
            }
        )

    profile = get_judging_profile(judge_profile)
    return {
        "name": name.strip() or fallback_name,
        "category": category.strip() or "General",
        "description": description,
        "judge_profile": judge_profile.strip() or "General",
        "judging_profile": profile.profile_id,
        "rubric": rubric,
        "prompts": prompts,
    }


def load_prompt_pack_document(path: Path) -> dict:
    if not path.exists() and path == FALLBACK_PROMPT_PACK_PATH:
        raw = json.loads(json.dumps(STARTER_COMPANION_DOCUMENT))
    else:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(raw, list):
        name = path.stem.replace("_", " ").replace("-", " ").title()
        category = inferred_prompt_pack_category(path)
        description = ""
        rubric = ""
        judge_profile = category
        raw_prompts = raw
    elif isinstance(raw, dict):
        name = str(raw.get("name") or path.stem.replace("_", " ").replace("-", " ").title())
        raw_category = str(raw.get("category") or "").strip()
        description = str(raw.get("description") or "")
        rubric = str(raw.get("rubric") or "").strip()
        judge_profile = str(raw.get("judge_profile") or raw.get("judging_profile") or raw_category or inferred_prompt_pack_category(path))
        name_hint = name.lower().replace("-", " ").replace("_", " ")
        if "humour" in name_hint or "humor" in name_hint:
            raw_category = "Humour"
            judge_profile = "Humour"
        profile = get_judging_profile(judge_profile)
        if raw_category and not (raw_category == "General" and profile.name != "General"):
            category = raw_category
        else:
            category = profile.name if profile.name != "General" else inferred_prompt_pack_category(path)
        raw_prompts = raw.get("prompts")
        if not isinstance(raw_prompts, list):
            raise ValueError(f"Prompt pack prompts must be a JSON array: {path}")
    else:
        raise ValueError(f"Prompt pack must be a JSON array or object: {path}")

    prompts = []
    required = ["title", "prompt"]
    for index, item in enumerate(raw_prompts, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Prompt pack item {index} must be an object: {path}")
        missing = [key for key in required if not str(item.get(key, "")).strip()]
        if missing:
            raise ValueError(f"Prompt pack item {index} is missing: {','.join(missing)}")
        prompts.append(
            {
                "id": str(item.get("id") or f"prompt_{index:02d}"),
                "category": str(item.get("category") or ""),
                "title": str(item["title"]),
                "evaluation_focus": str(item.get("evaluation_focus") or ""),
                "prompt": str(item["prompt"]),
            }
        )
    profile = get_judging_profile(judge_profile)
    return {
        "name": name,
        "category": category.strip() or "General",
        "description": description,
        "judge_profile": judge_profile.strip() or "General",
        "judging_profile": profile.profile_id,
        "rubric": rubric,
        "prompts": prompts,
    }


def load_prompt_pack(path: Path) -> list[dict[str, str]]:
    return load_prompt_pack_document(path)["prompts"]


def prompt_pack_judging_profile(path: Path) -> str:
    try:
        profile_id = load_prompt_pack_document(path)["judging_profile"]
    except (OSError, json.JSONDecodeError, ValueError):
        profile_id = DEFAULT_PROFILE_ID
    return get_judging_profile(profile_id).profile_id


def selected_prompt_pack_document() -> dict:
    return load_prompt_pack_document(selected_prompt_pack_path())


def selected_prompt_pack_report_metadata() -> dict[str, str]:
    try:
        document = selected_prompt_pack_document()
    except (OSError, json.JSONDecodeError, ValueError):
        path = selected_prompt_pack_path()
        document = {
            "name": prompt_pack_label(path),
            "category": "General",
            "description": "",
            "judge_profile": "General",
        }
    return normalize_prompt_pack_metadata(document, prompt_pack_label(selected_prompt_pack_path()))


def visible_prompt_texts() -> list[str]:
    prompts = []
    for slot in range(1, BATCH_SLOT_COUNT + 1):
        prompt_text = st.session_state.get(f"batch_prompt_{slot}", "").strip()
        if prompt_text:
            prompts.append(prompt_text)
    return prompts


def visible_prompt_pack_report_metadata() -> dict[str, str] | None:
    prompts = visible_prompt_texts()
    if not prompts:
        return None
    matching_packs: list[tuple[Path, dict]] = []
    for path in available_prompt_packs().values():
        try:
            document = load_prompt_pack_document(path)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        pack_prompts = {str(item.get("prompt", "")).strip() for item in document.get("prompts", [])}
        if all(prompt in pack_prompts for prompt in prompts):
            matching_packs.append((path, document))
    if not matching_packs:
        return None
    try:
        selected_path = selected_prompt_pack_path()
    except (OSError, ValueError):
        selected_path = None
    for path, document in matching_packs:
        if selected_path and path == selected_path:
            return normalize_prompt_pack_metadata(document, prompt_pack_label(path))
    path, document = matching_packs[0]
    return normalize_prompt_pack_metadata(document, prompt_pack_label(path))


def active_benchmark_prompt_pack_metadata() -> dict[str, str]:
    return visible_prompt_pack_report_metadata() or selected_prompt_pack_report_metadata()


def last_response_prompt_pack_metadata() -> dict[str, str]:
    metadata = st.session_state.get("last_prompt_pack_metadata")
    if isinstance(metadata, dict):
        return normalize_prompt_pack_metadata(metadata)
    stored_metadata = prompt_pack_metadata_from_form_state(load_form_state())
    if stored_metadata:
        return stored_metadata
    return active_benchmark_prompt_pack_metadata()


def selected_judging_profile():
    return get_judging_profile(prompt_pack_judging_profile(selected_prompt_pack_path()))


def selected_judge_profile_prompt() -> str:
    metadata = active_benchmark_prompt_pack_metadata()
    registered_profile = get_judging_profile(metadata["judge_profile"])
    lines = [
        f"Name: {metadata['judge_profile']}",
        f"Prompt Pack: {metadata['name']}",
        f"Category: {metadata['category']}",
    ]
    if metadata["description"]:
        lines.append(f"Description: {metadata['description']}")
    if registered_profile.name == metadata["judge_profile"]:
        lines.extend(["", registered_profile.instructions])
    else:
        st.warning(
            f"Judge profile '{metadata['judge_profile']}' is not a registered judging profile; "
            "the judge receives generic profile guidance instead of specific instructions."
        )
        lines.extend(
            [
                "",
                f"Use the selected prompt pack's {metadata['judge_profile']} judging profile.",
                "Prioritise the capability implied by that profile and category while keeping the existing scoring categories and JSON schema stable.",
            ]
        )
    return "\n".join(lines)


INITIAL_PROMPT_PACK_PATH = initial_prompt_pack_path()
HELCYON_COMPANION_V1_PROMPTS = load_prompt_pack(INITIAL_PROMPT_PACK_PATH) if INITIAL_PROMPT_PACK_PATH else []


def all_prompt_pack_entries() -> list[dict[str, str]]:
    entries = []
    for path in available_prompt_packs().values():
        try:
            entries.extend(load_prompt_pack(path))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
    return entries


def prompt_pack_path_for_prompts(prompts: list[str]) -> Path | None:
    imported_prompts = [str(prompt).strip() for prompt in prompts if str(prompt).strip()]
    if not imported_prompts:
        return None
    for path in available_prompt_packs().values():
        try:
            pack_prompts = [str(item.get("prompt") or "").strip() for item in load_prompt_pack(path)]
        except (OSError, ValueError, TypeError):
            continue
        if imported_prompts == pack_prompts[: len(imported_prompts)]:
            return path
    return None


def clear_inputs() -> None:
    st.session_state.model_name_a_input = ""
    st.session_state.model_name_b_input = ""
    for slot in range(1, BATCH_SLOT_COUNT + 1):
        st.session_state[f"batch_prompt_{slot}"] = ""
        st.session_state[f"batch_response_a_{slot}"] = ""
        st.session_state[f"batch_response_b_{slot}"] = ""


def clear_persisted_inputs() -> None:
    clear_inputs()
    if st.session_state.get("_llmbench_form_state_hydrated"):
        remove_form_state(benchmark_field_keys() + PERSISTED_SELECT_KEYS)
    st.session_state.pop("judge_endpoint_select", None)
    st.session_state.pop("judge_model_select", None)
    st.session_state.confirm_clear_inputs = False
    st.session_state.clear_persistence_nonce = datetime.now().isoformat()


def clear_response_inputs(response_key: str) -> None:
    response_key = response_key.lower()
    if response_key not in {"a", "b"}:
        raise ValueError("Response key must be A or B.")
    cleared_keys = []
    for slot in range(1, BATCH_SLOT_COUNT + 1):
        field_key = f"batch_response_{response_key}_{slot}"
        st.session_state[field_key] = ""
        cleared_keys.append(field_key)
    if st.session_state.get("_llmbench_form_state_hydrated"):
        remove_form_state(cleared_keys)
    st.session_state.clear_persistence_keys = cleared_keys


def clear_response_a_inputs() -> None:
    clear_response_inputs("a")


def clear_response_b_inputs() -> None:
    clear_response_inputs("b")


def sanitize_filename_part(value: str, fallback: str) -> str:
    cleaned = (value or fallback).strip().lower()
    cleaned = re.sub(r"\s+", "-", cleaned)
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", cleaned)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-. _")
    return cleaned or fallback


def prompt_pack_filename(value: str) -> str:
    slug = sanitize_filename_part(value.replace("_", "-"), "custom-prompt-pack")
    slug = re.sub(r"[^a-z0-9-]+", "", slug)
    return f"{slug or 'custom-prompt-pack'}.json"


def export_filename_part(value: str, fallback: str) -> str:
    slug = sanitize_filename_part(value, fallback)
    slug = re.sub(r"[^a-z0-9-]+", "-", slug).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug or fallback


def dashboard_model_key_part(model_name: str) -> str:
    slug = export_filename_part(model_name, "model")
    digest = hashlib.sha1(model_name.encode("utf-8", errors="ignore")).hexdigest()[:8]
    return f"{slug}_{digest}"


def export_filename(
    model_name_a: str,
    model_name_b: str,
    extension: str,
    timestamp: datetime | None = None,
    prompt_pack_name: str = "",
    category: str = "General",
) -> str:
    generated_at = timestamp or datetime.now()
    if not isinstance(generated_at, datetime):
        raise TypeError("timestamp must be a datetime or None")
    stamp = generated_at.strftime("%Y-%m-%d-%H%M")
    category_name = export_filename_part(category, "general")
    pack_name = export_filename_part(prompt_pack_name, "prompt-pack")
    name_a = export_filename_part(model_name_a, "response-a")
    name_b = export_filename_part(model_name_b, "response-b")
    return f"{APP_SLUG}-{category_name}-{pack_name}-{name_a}-vs-{name_b}-{stamp}.{extension.lstrip('.')}"


def selected_prompt_pack_export_name() -> str:
    try:
        return selected_prompt_pack_path().stem
    except (OSError, ValueError):
        return "prompt-pack"


def response_field_key(side: str, slot: int) -> str:
    side = side.lower()
    if side not in {"a", "b"}:
        raise ValueError("Response side must be A or B.")
    return f"batch_response_{side}_{slot}"


def model_response_filename(
    model_name: str,
    side: str,
    category: str = "General",
    timestamp: datetime | None = None,
    prompt_pack_name: str = "",
) -> str:
    generated_at = timestamp or datetime.now()
    stamp = generated_at.strftime("%Y-%m-%d-%H%M%S")
    name = export_filename_part(model_name, f"model-{side.lower()}")
    category_name = export_filename_part(category, "general")
    pack_name = export_filename_part(prompt_pack_name, "")
    test_parts = [category_name]
    if pack_name and pack_name != category_name:
        test_parts.append(pack_name)
    return f"{'-'.join(test_parts)}-{name}-responses-{stamp}.txt"


def response_set_label(path: Path) -> str:
    return path.stem.replace("_", " ").replace("-", " ").title()


def available_model_response_sets(path: Path = MODEL_RESPONSE_DIR) -> dict[str, Path]:
    if not path.exists():
        return {}
    files = sorted(path.glob("*.txt"), key=lambda item: item.stat().st_mtime, reverse=True)
    return {response_set_label(file_path): file_path for file_path in files}


def benchmark_session_filename(
    model_name_a: str,
    model_name_b: str,
    prompt_pack_name: str,
    timestamp: datetime | None = None,
) -> str:
    generated_at = timestamp or datetime.now()
    stamp = generated_at.strftime("%Y-%m-%d-%H%M%S")
    pack_name = export_filename_part(prompt_pack_name, "prompt-pack")
    name_a = export_filename_part(model_name_a, "model-a")
    name_b = export_filename_part(model_name_b, "model-b")
    return f"{APP_SLUG}-session-{pack_name}-{name_a}-vs-{name_b}-{stamp}.json"


def benchmark_session_payload(
    model_name_a: str,
    model_name_b: str,
    prompt_pack_metadata: dict[str, str] | None = None,
    timestamp: datetime | None = None,
) -> dict:
    generated_at = timestamp or datetime.now()
    metadata = normalize_prompt_pack_metadata(prompt_pack_metadata or active_benchmark_prompt_pack_metadata())
    return {
        "app": APP_NAME,
        "version": 1,
        "saved_at": generated_at.isoformat(timespec="seconds"),
        "prompt_pack": metadata,
        "prompt_pack_select": st.session_state.get("prompt_pack_select", ""),
        "prompt_pack_category_select": st.session_state.get("prompt_pack_category_select", ""),
        "model_name_a": model_name_or_default("A", model_name_a),
        "model_name_b": model_name_or_default("B", model_name_b),
        "slots": [
            {
                "slot": slot,
                "prompt": st.session_state.get(f"batch_prompt_{slot}", ""),
                "response_a": st.session_state.get(f"batch_response_a_{slot}", ""),
                "response_b": st.session_state.get(f"batch_response_b_{slot}", ""),
            }
            for slot in range(1, BATCH_SLOT_COUNT + 1)
        ],
    }


def save_benchmark_session(payload: dict, path: Path | None = None) -> Path:
    if not any(
        str(slot.get("prompt", "")).strip()
        or str(slot.get("response_a", "")).strip()
        or str(slot.get("response_b", "")).strip()
        for slot in payload.get("slots", [])
    ):
        raise ValueError("Add at least one prompt or response before saving a full session.")
    output_path = path or (
        BENCHMARK_SESSION_DIR
        / benchmark_session_filename(
            str(payload.get("model_name_a") or "Model A"),
            str(payload.get("model_name_b") or "Model B"),
            str((payload.get("prompt_pack") or {}).get("name") or "prompt-pack"),
        )
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return output_path


def benchmark_session_label(path: Path) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return response_set_label(path)
    model_a = str(payload.get("model_name_a") or "Model A")
    model_b = str(payload.get("model_name_b") or "Model B")
    prompt_pack = payload.get("prompt_pack") if isinstance(payload.get("prompt_pack"), dict) else {}
    pack_name = str(prompt_pack.get("name") or payload.get("prompt_pack_select") or "Prompt Pack")
    saved_at = str(payload.get("saved_at") or path.stem)
    return f"{model_a} vs {model_b} | {pack_name} | {saved_at}"


def available_benchmark_sessions(path: Path = BENCHMARK_SESSION_DIR) -> dict[str, Path]:
    if not path.exists():
        return {}
    files = sorted(path.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    sessions = {}
    for file_path in files:
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload.get("slots"), list):
            continue
        label = benchmark_session_label(file_path)
        if label in sessions:
            label = f"{label} ({file_path.stem})"
        sessions[label] = file_path
    return sessions


def response_values_for_side(side: str) -> list[str]:
    return [st.session_state.get(response_field_key(side, slot), "") for slot in range(1, BATCH_SLOT_COUNT + 1)]


def benchmark_input_fingerprint(
    model_name_a: str,
    model_name_b: str,
    prompt_pack_metadata: dict[str, str] | None = None,
) -> str:
    metadata = normalize_prompt_pack_metadata(prompt_pack_metadata or active_benchmark_prompt_pack_metadata())
    payload = {
        "model_name_a": str(model_name_a),
        "model_name_b": str(model_name_b),
        "prompt_pack": metadata,
        "slots": [
            {
                "prompt": st.session_state.get(f"batch_prompt_{slot}", ""),
                "response_a": st.session_state.get(f"batch_response_a_{slot}", ""),
                "response_b": st.session_state.get(f"batch_response_b_{slot}", ""),
            }
            for slot in range(1, BATCH_SLOT_COUNT + 1)
        ],
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def model_response_set_text(
    model_name: str,
    side: str,
    responses: list[str],
    timestamp: datetime | None = None,
) -> str:
    generated_at = timestamp or datetime.now()
    lines = [
        f"# {APP_NAME} Model Responses",
        f"model: {model_name_or_default(side.upper(), model_name)}",
        f"side: {side.upper()}",
        f"saved: {generated_at.isoformat(timespec='seconds')}",
        "",
    ]
    for slot in range(1, BATCH_SLOT_COUNT + 1):
        response = responses[slot - 1] if slot - 1 < len(responses) else ""
        lines.extend(
            [
                f"--- RESPONSE {slot} ---",
                response.rstrip(),
                f"--- END RESPONSE {slot} ---",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def parse_model_response_set(text: str) -> list[str]:
    responses = []
    for slot in range(1, BATCH_SLOT_COUNT + 1):
        pattern = rf"--- RESPONSE {slot} ---\r?\n(.*?)\r?\n--- END RESPONSE {slot} ---"
        match = re.search(pattern, text, flags=re.DOTALL)
        responses.append(match.group(1).rstrip("\r\n") if match else "")
    return responses


def parse_model_response_metadata(text: str) -> dict[str, str]:
    metadata = {}
    for line in text.splitlines():
        if not line.strip():
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip().lower()] = value.strip()
    return metadata


def text_from_chat_content(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = [text_from_chat_content(item).strip() for item in value]
        return "\n".join(part for part in parts if part)
    if isinstance(value, dict):
        for key in ("text", "content", "message", "value"):
            if key in value:
                return text_from_chat_content(value[key])
    return ""


def first_list_value(payload: dict, keys: tuple[str, ...]) -> list | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return None


def response_import_from_json(raw: object, side: str) -> tuple[str, list[str], list[str]]:
    side = side.lower()
    model_name = ""
    prompts: list[str] = []
    responses: list[str] = []

    if isinstance(raw, dict):
        model_name = str(raw.get("model") or raw.get("model_name") or raw.get("name") or "").strip()
        slots = first_list_value(raw, ("slots", "items", "entries", "prompts"))
        if slots:
            for item in slots:
                if not isinstance(item, dict):
                    continue
                prompt = text_from_chat_content(
                    item.get("prompt")
                    or item.get("current_prompt")
                    or item.get("question")
                    or item.get("user")
                    or item.get("input")
                ).strip()
                response = text_from_chat_content(
                    item.get(f"response_{side}")
                    or item.get(f"response{side.upper()}")
                    or item.get("response")
                    or item.get("assistant")
                    or item.get("output")
                    or item.get("answer")
                ).strip()
                if prompt or response:
                    prompts.append(prompt)
                    responses.append(response)
            if any(response.strip() for response in responses):
                return model_name, prompts[:BATCH_SLOT_COUNT], responses[:BATCH_SLOT_COUNT]

        messages = first_list_value(raw, ("messages", "conversation", "chat", "history", "turns"))
        if messages is None and isinstance(raw.get("data"), dict):
            messages = first_list_value(raw["data"], ("messages", "conversation", "chat", "history", "turns"))
            model_name = model_name or str(raw["data"].get("model") or raw["data"].get("model_name") or "").strip()
    elif isinstance(raw, list):
        messages = raw
    else:
        messages = None

    pending_prompt = ""
    if isinstance(messages, list):
        for message in messages:
            if isinstance(message, str):
                continue
            if not isinstance(message, dict):
                continue
            direct_prompt = text_from_chat_content(
                message.get("prompt")
                or message.get("current_prompt")
                or message.get("question")
                or message.get("user")
                or message.get("input")
            ).strip()
            direct_response = text_from_chat_content(
                message.get(f"response_{side}")
                or message.get(f"response{side.upper()}")
                or message.get("response")
                or message.get("assistant")
                or message.get("output")
                or message.get("answer")
            ).strip()
            if direct_prompt or direct_response:
                prompts.append(direct_prompt)
                responses.append(direct_response)
                continue
            role = str(message.get("role") or message.get("speaker") or message.get("author") or "").lower()
            content = text_from_chat_content(
                message.get("content")
                or message.get("message")
                or message.get("text")
                or message.get("value")
            ).strip()
            if not content:
                continue
            if role in {"user", "human", "you", "prompt"}:
                pending_prompt = content
                continue
            if role in {"assistant", "model", "bot", "ai", "character"} or not role:
                prompts.append(pending_prompt)
                responses.append(content)
                pending_prompt = ""

    if not any(response.strip() for response in responses):
        raise ValueError("No assistant/model responses were found in the imported file.")
    return model_name, prompts[:BATCH_SLOT_COUNT], responses[:BATCH_SLOT_COUNT]


def import_model_responses_from_upload(
    file_name: str,
    data: bytes,
    side: str,
    form_state_path: Path = FORM_STATE_PATH,
) -> list[str]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"Could not read {file_name} as text: {exc}") from exc

    model_name = ""
    prompts: list[str] = []
    responses: list[str]
    stripped = text.lstrip()
    if Path(file_name).suffix.lower() == ".json" or stripped.startswith(("{", "[")):
        try:
            model_name, prompts, responses = response_import_from_json(json.loads(text), side)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Could not read {file_name} as JSON: {exc}") from exc
    else:
        responses = parse_model_response_set(text)
        metadata = parse_model_response_metadata(text)
        model_name = metadata.get("model", "").strip()

    if not any(response.strip() for response in responses):
        raise ValueError("No responses were found in the imported file.")

    values = {}
    matched_pack_path = prompt_pack_path_for_prompts(prompts)
    if matched_pack_path:
        matched_metadata = remember_prompt_pack_metadata(
            normalize_prompt_pack_metadata(load_prompt_pack_document(matched_pack_path), prompt_pack_label(matched_pack_path))
        )
        st.session_state.prompt_pack_category_select = matched_metadata["category"]
        st.session_state.prompt_pack_select = prompt_pack_label(matched_pack_path)
        values.update(prompt_pack_metadata_form_state_values(matched_metadata))
        values["prompt_pack_category_select"] = matched_metadata["category"]
        values["prompt_pack_select"] = prompt_pack_label(matched_pack_path)

    model_key = f"model_name_{side.lower()}_input"
    if model_name:
        st.session_state[model_key] = model_name
        values[model_key] = model_name
    for slot in range(1, BATCH_SLOT_COUNT + 1):
        prompt = prompts[slot - 1] if slot - 1 < len(prompts) else ""
        response = responses[slot - 1] if slot - 1 < len(responses) else ""
        prompt_key = f"batch_prompt_{slot}"
        response_key = response_field_key(side, slot)
        if prompt.strip() and not str(st.session_state.get(prompt_key, "")).strip():
            st.session_state[prompt_key] = prompt
            values[prompt_key] = prompt
        st.session_state[response_key] = response
        values[response_key] = response
    update_form_state_values(values, form_state_path)
    return responses


def save_model_response_set(
    model_name: str,
    side: str,
    responses: list[str],
    path: Path | None = None,
    category: str = "General",
    prompt_pack_name: str = "",
) -> Path:
    if not any(response.strip() for response in responses):
        raise ValueError(f"Add at least one Model {side.upper()} response before saving.")
    output_path = path or (
        MODEL_RESPONSE_DIR / model_response_filename(model_name, side, category, prompt_pack_name=prompt_pack_name)
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(model_response_set_text(model_name, side, responses), encoding="utf-8")
    return output_path


def selected_model_response_path() -> Path | None:
    sets = available_model_response_sets()
    selected_label = st.session_state.get("model_response_set_select")
    if selected_label in sets:
        return sets[selected_label]
    if sets:
        return next(iter(sets.values()))
    return None


def import_model_responses(path: Path, side: str, form_state_path: Path = FORM_STATE_PATH) -> list[str]:
    raw_text = path.read_text(encoding="utf-8-sig")
    responses = parse_model_response_set(raw_text)
    metadata = parse_model_response_metadata(raw_text)
    values = {}
    model_name = metadata.get("model", "").strip()
    model_key = f"model_name_{side.lower()}_input"
    if model_name:
        st.session_state[model_key] = model_name
        values[model_key] = model_name
    for stale_key in ("model_name_a_input", "model_name_b_input"):
        if st.session_state.get(stale_key) in STALE_MODEL_NAME_PLACEHOLDERS and stale_key not in values:
            st.session_state[stale_key] = ""
            values[stale_key] = ""
    for slot, response in enumerate(responses, start=1):
        key = response_field_key(side, slot)
        st.session_state[key] = response
        values[key] = response
    update_form_state_values(values, form_state_path)
    return responses


def selected_benchmark_session_path() -> Path | None:
    sessions = available_benchmark_sessions()
    selected_label = st.session_state.get("benchmark_session_select")
    if selected_label in sessions:
        return sessions[selected_label]
    if sessions:
        return next(iter(sessions.values()))
    return None


def import_benchmark_session(path: Path, form_state_path: Path = FORM_STATE_PATH) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    slots = payload.get("slots", [])
    if not isinstance(slots, list):
        raise ValueError("Saved benchmark session is missing its prompt/response slots.")

    values = {
        "model_name_a_input": str(payload.get("model_name_a") or ""),
        "model_name_b_input": str(payload.get("model_name_b") or ""),
    }
    for slot in range(1, BATCH_SLOT_COUNT + 1):
        values[f"batch_prompt_{slot}"] = ""
        values[f"batch_response_a_{slot}"] = ""
        values[f"batch_response_b_{slot}"] = ""

    for slot_payload in slots:
        if not isinstance(slot_payload, dict):
            continue
        try:
            slot = int(slot_payload.get("slot", 0))
        except (TypeError, ValueError):
            continue
        if not 1 <= slot <= BATCH_SLOT_COUNT:
            continue
        values[f"batch_prompt_{slot}"] = str(slot_payload.get("prompt") or "")
        values[f"batch_response_a_{slot}"] = str(slot_payload.get("response_a") or "")
        values[f"batch_response_b_{slot}"] = str(slot_payload.get("response_b") or "")

    prompt_pack_metadata = payload.get("prompt_pack") if isinstance(payload.get("prompt_pack"), dict) else None
    if prompt_pack_metadata:
        values.update(prompt_pack_metadata_form_state_values(normalize_prompt_pack_metadata(prompt_pack_metadata)))
        remember_prompt_pack_metadata(prompt_pack_metadata)
    if payload.get("prompt_pack_category_select"):
        values["prompt_pack_category_select"] = str(payload.get("prompt_pack_category_select"))
    if payload.get("prompt_pack_select"):
        values["prompt_pack_select"] = str(payload.get("prompt_pack_select"))

    for key, value in values.items():
        st.session_state[key] = value
    update_form_state_values(values, form_state_path)
    return payload


def save_current_model_responses(side: str) -> None:
    model_name = st.session_state.get(f"model_name_{side.lower()}_input", "")
    try:
        metadata = last_response_prompt_pack_metadata()
        path = save_model_response_set(
            model_name,
            side,
            response_values_for_side(side),
            category=metadata["category"],
            prompt_pack_name=metadata["name"],
        )
    except ValueError as exc:
        st.session_state.model_response_message = str(exc)
        st.session_state.model_response_message_kind = "error"
        return
    except OSError as exc:
        st.session_state.model_response_message = f"Could not save Model {side.upper()} responses: {exc}"
        st.session_state.model_response_message_kind = "error"
        return
    st.session_state.model_response_message = f"Saved Model {side.upper()} responses to {path.name}."
    st.session_state.model_response_message_kind = "success"


def import_selected_model_responses() -> None:
    path = selected_model_response_path()
    if not path:
        st.session_state.model_response_message = "No saved model response file selected."
        st.session_state.model_response_message_kind = "error"
        return
    target = st.session_state.get("model_response_import_target", "Model A")
    side = "a" if target == "Model A" else "b"
    try:
        import_model_responses(path, side)
    except (OSError, ValueError) as exc:
        st.session_state.model_response_message = f"Could not import {path.name}: {exc}"
        st.session_state.model_response_message_kind = "error"
        return
    imported_model_name = st.session_state.get(f"model_name_{side}_input", "").strip()
    if imported_model_name:
        st.session_state.model_response_message = f"Imported {path.name} into {target} responses as {imported_model_name}."
    else:
        st.session_state.model_response_message = f"Imported {path.name} into {target} responses."
    st.session_state.model_response_message_kind = "success"


def import_uploaded_test_chat_responses() -> None:
    upload = st.session_state.get("test_chat_response_upload")
    if not upload:
        st.session_state.model_response_message = "Choose a HWUI test chat or response file first."
        st.session_state.model_response_message_kind = "error"
        return
    target = st.session_state.get("test_chat_response_import_target", "Model A")
    side = "a" if target == "Model A" else "b"
    try:
        responses = import_model_responses_from_upload(upload.name, upload.getvalue(), side)
    except ValueError as exc:
        st.session_state.model_response_message = f"Could not import {upload.name}: {exc}"
        st.session_state.model_response_message_kind = "error"
        return
    imported_count = sum(1 for response in responses if str(response).strip())
    imported_model_name = st.session_state.get(f"model_name_{side}_input", "").strip()
    if imported_model_name:
        st.session_state.model_response_message = (
            f"Imported {imported_count} response(s) from {upload.name} into {target} as {imported_model_name}."
        )
    else:
        st.session_state.model_response_message = f"Imported {imported_count} response(s) from {upload.name} into {target}."
    st.session_state.model_response_message_kind = "success"


def save_current_benchmark_session() -> None:
    metadata = last_response_prompt_pack_metadata()
    payload = benchmark_session_payload(
        st.session_state.get("model_name_a_input", ""),
        st.session_state.get("model_name_b_input", ""),
        metadata,
    )
    try:
        path = save_benchmark_session(payload)
    except ValueError as exc:
        st.session_state.benchmark_session_message = str(exc)
        st.session_state.benchmark_session_message_kind = "error"
        return
    except OSError as exc:
        st.session_state.benchmark_session_message = f"Could not save full session: {exc}"
        st.session_state.benchmark_session_message_kind = "error"
        return
    st.session_state.benchmark_session_message = f"Saved full session to {path.name}."
    st.session_state.benchmark_session_message_kind = "success"


def import_selected_benchmark_session() -> None:
    path = selected_benchmark_session_path()
    if not path:
        st.session_state.benchmark_session_message = "No saved full benchmark session selected."
        st.session_state.benchmark_session_message_kind = "error"
        return
    try:
        payload = import_benchmark_session(path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        st.session_state.benchmark_session_message = f"Could not import {path.name}: {exc}"
        st.session_state.benchmark_session_message_kind = "error"
        return
    model_a = str(payload.get("model_name_a") or "Model A")
    model_b = str(payload.get("model_name_b") or "Model B")
    st.session_state.benchmark_session_message = f"Imported full session {path.name}: {model_a} vs {model_b}."
    st.session_state.benchmark_session_message_kind = "success"


def open_benchmark_sessions_folder() -> None:
    try:
        BENCHMARK_SESSION_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(str(BENCHMARK_SESSION_DIR))
    except OSError as exc:
        st.session_state.benchmark_session_message = f"Could not open sessions folder: {exc}"
        st.session_state.benchmark_session_message_kind = "error"
        return
    st.session_state.benchmark_session_message = f"Opened sessions folder: {BENCHMARK_SESSION_DIR}"
    st.session_state.benchmark_session_message_kind = "success"


def query_value(name: str) -> str | None:
    value = st.query_params.get(name)
    if isinstance(value, list):
        return value[0] if value else None
    return value


def remove_query_param(name: str) -> None:
    try:
        del st.query_params[name]
    except (AttributeError, KeyError):
        try:
            st.query_params.pop(name, None)
        except (AttributeError, KeyError):
            pass


def consume_query_value(name: str) -> str | None:
    value = query_value(name)
    if value:
        remove_query_param(name)
    return value


def selected_index_from_query(options: list[str], query_name: str, default_index: int = 0) -> int:
    value = query_value(query_name)
    if value in options:
        return options.index(value)
    return default_index


def sync_dashboard_query_view() -> None:
    if query_value("trend_model"):
        st.session_state.app_view_select = "Results Dashboard"


def judge_endpoint_options(config) -> list[str]:
    if not config:
        return []
    return [endpoint.name for endpoint in (config.judge.endpoints or [config.judge])]


def selected_judge_endpoint(config, selected_name: str | None = None):
    if not config:
        return None
    return judge_endpoint_by_name(config.judge, selected_name)


def judge_model_options(endpoint) -> list[str]:
    if not endpoint:
        return []
    discovered_models = st.session_state.get(discovered_judge_models_key(endpoint.name), [])
    models = list(discovered_models or endpoint.models or [])
    if endpoint.model not in models:
        models.insert(0, endpoint.model)
    return models


def judge_request_headers(endpoint) -> dict[str, str]:
    if not endpoint:
        return {}
    headers = {"X-HelcyonBench-Run": "true"}
    if getattr(endpoint, "local_endpoint_mode", ""):
        headers["X-HelcyonBench-Endpoint-Mode"] = endpoint.local_endpoint_mode
    if getattr(endpoint, "name", ""):
        headers["X-HelcyonBench-Endpoint"] = endpoint.name
    return headers


def acquire_bench_lock(endpoint) -> None:
    lock_url = getattr(endpoint, "bench_lock_url", "")
    if lock_url:
        post_lifecycle_hook(url=lock_url, extra_headers=judge_request_headers(endpoint))


def release_bench_lock(endpoint) -> None:
    unlock_url = getattr(endpoint, "bench_unlock_url", "")
    if unlock_url:
        post_lifecycle_hook(url=unlock_url, extra_headers=judge_request_headers(endpoint))


def discovered_judge_models_key(endpoint_name: str) -> str:
    return f"discovered_judge_models_{export_filename_part(endpoint_name, 'endpoint')}"


def refresh_judge_models(endpoint) -> None:
    clear_action_status_messages()
    if not endpoint:
        st.session_state.judge_model_refresh_message = "No judge endpoint selected."
        return
    try:
        models = list_models(
            base_url=endpoint.base_url,
            api_key=endpoint.api_key,
            extra_headers=judge_request_headers(endpoint),
        )
    except ApiError as exc:
        st.session_state.judge_model_refresh_message = f"Could not load models from {endpoint.name}: {exc}"
        return
    if not models:
        st.session_state.judge_model_refresh_message = f"{endpoint.name} did not return any models."
        return
    st.session_state[discovered_judge_models_key(endpoint.name)] = models
    current_model = st.session_state.get("judge_model_select")
    st.session_state.judge_model_select = current_model if current_model in models else models[0]
    st.session_state.judge_model_refresh_message = f"Loaded {len(models)} model(s) from {endpoint.name}."


def preflight_judge_endpoint(endpoint, model: str) -> list[str]:
    try:
        models = list_models(
            base_url=endpoint.base_url,
            api_key=endpoint.api_key,
            extra_headers=judge_request_headers(endpoint),
        )
    except ApiError:
        raise

    if models and model not in models:
        preview = ", ".join(models[:8])
        if len(models) > 8:
            preview += ", ..."
        raise ApiError(
            f"Selected judge model {model!r} was not listed by {endpoint.name}. "
            f"Click Refresh Judge Models and choose one of: {preview}"
        )

    preflight_chat_completion(
        base_url=endpoint.base_url,
        api_key=endpoint.api_key,
        model=model,
        temperature=endpoint.temperature,
        max_tokens=min(endpoint.max_tokens or 64, 256),
        max_completion_tokens=(
            min(endpoint.max_completion_tokens, 256)
            if endpoint.max_completion_tokens is not None
            else None
        ),
        extra_headers=judge_request_headers(endpoint),
        endpoint_name=endpoint.name,
        prompt_id="connection_test",
    )
    return models


def test_judge_connection(endpoint, model: str) -> None:
    clear_action_status_messages()
    if not endpoint:
        st.session_state.judge_model_refresh_message = "No judge endpoint selected."
        return
    try:
        models = preflight_judge_endpoint(endpoint, model)
    except ApiError as exc:
        message = f"Judge connection failed for {endpoint.name}: {exc}"
        raw_response = getattr(exc, "raw_response", None)
        if raw_response is not None:
            preview = " ".join(str(raw_response).split())[:500]
            message = f"{message} Raw response: {preview}"
        st.session_state.judge_model_refresh_message = message
        return
    if models:
        st.session_state[discovered_judge_models_key(endpoint.name)] = models
    st.session_state.judge_model_refresh_message = (
        f"Judge connection OK: {endpoint.name} reached /models and completed a tiny structured chat with {model}."
    )


def save_selected_judge_api_key(endpoint_name: str) -> None:
    clear_action_status_messages()
    api_key = str(st.session_state.get("judge_api_key_input") or "").strip()
    try:
        save_judge_endpoint_api_key(APP_DIR, endpoint_name, api_key)
    except (ConfigError, OSError) as exc:
        st.session_state.judge_api_key_message = f"Could not save API key: {exc}"
        st.session_state.judge_api_key_message_kind = "error"
        return
    st.session_state.judge_api_key_input = ""
    st.session_state.judge_api_key_message = f"API key saved for {endpoint_name}."
    st.session_state.judge_api_key_message_kind = "success"


def selected_judge_model(endpoint, selected_model: str | None = None) -> str:
    models = judge_model_options(endpoint)
    if selected_model in models:
        return str(selected_model)
    if endpoint:
        return endpoint.model
    return models[0] if models else ""


def has_visible_batch_input() -> bool:
    for slot in range(1, BATCH_SLOT_COUNT + 1):
        if (
            st.session_state.get(f"batch_prompt_{slot}", "").strip()
            or st.session_state.get(f"batch_response_a_{slot}", "").strip()
            or st.session_state.get(f"batch_response_b_{slot}", "").strip()
        ):
            return True
    return False


def prompt_pack_metadata(prompt_text: str, slot: int) -> dict[str, str]:
    stripped = prompt_text.strip()
    for prompt in all_prompt_pack_entries() or HELCYON_COMPANION_V1_PROMPTS:
        if stripped == prompt["prompt"].strip():
            return prompt
    return {
        "id": f"prompt_{slot:02d}",
        "category": "",
        "title": title_from_prompt(stripped, slot),
        "evaluation_focus": "",
        "prompt": stripped,
    }


def title_from_prompt(prompt_text: str, slot: int) -> str:
    first_line = next((line.strip() for line in prompt_text.splitlines() if line.strip()), "")
    if not first_line:
        return f"Prompt {slot}"
    sentence = re.split(r"(?<=[.!?])\s+", first_line, maxsplit=1)[0].strip()
    title = sentence[:70].strip()
    return title.rstrip(".,;:") or f"Prompt {slot}"


def visible_prompt_pack_entries() -> list[dict[str, str]]:
    entries = []
    for slot in range(1, BATCH_SLOT_COUNT + 1):
        prompt_text = st.session_state.get(f"batch_prompt_{slot}", "").strip()
        if not prompt_text:
            continue
        metadata = prompt_pack_metadata(prompt_text, slot)
        matches_existing_prompt = any(prompt_text == prompt["prompt"].strip() for prompt in all_prompt_pack_entries())
        title = metadata["title"] if matches_existing_prompt else title_from_prompt(prompt_text, slot)
        entries.append({"title": title, "prompt": prompt_text})
    return entries


def prompt_pack_editor_metadata(path: Path | None = None) -> dict[str, str]:
    fallback_name = path.stem.replace("_", " ").replace("-", " ").title() if path else ""
    name = str(st.session_state.get("pack_editor_name_input") or fallback_name).strip()
    category = str(st.session_state.get("pack_editor_category_select") or "General").strip()
    description = str(st.session_state.get("pack_editor_description_input") or "").strip()
    return {
        "name": name or fallback_name or "Custom Prompt Pack",
        "category": category or "General",
        "description": description,
        "judge_profile": category or "General",
    }


def prompt_pack_document_metadata(
    path: Path,
    profile_id: str,
    metadata: dict[str, str] | None = None,
) -> dict[str, str]:
    profile = get_judging_profile(profile_id)
    metadata = {
        "name": path.stem.replace("_", " ").replace("-", " ").title(),
        "category": "General",
        "description": "",
        "judge_profile": profile.name,
    } | (metadata or {})
    if path.exists():
        try:
            existing = load_prompt_pack_document(path)
        except (OSError, json.JSONDecodeError, ValueError):
            existing = {}
        for key in ["name", "category", "description", "judge_profile", "rubric"]:
            value = str(existing.get(key) or "").strip()
            if value and key not in (metadata or {}):
                metadata[key] = value
    metadata["category"] = str(metadata.get("category") or "General").strip() or "General"
    metadata["judge_profile"] = str(metadata.get("judge_profile") or "General").strip() or "General"
    return metadata


def save_prompt_pack(
    entries: list[dict[str, str]],
    path: Path,
    judging_profile: str | None = None,
    metadata: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    path.parent.mkdir(parents=True, exist_ok=True)
    profile_id = judging_profile or (metadata.get("judge_profile") if metadata else None)
    profile_id = profile_id or (prompt_pack_judging_profile(path) if path.exists() else DEFAULT_PROFILE_ID)
    payload = prompt_pack_document_metadata(path, profile_id, metadata) | {"prompts": entries}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return load_prompt_pack(path)


def set_pack_editor_message(message: str, kind: str = "info") -> None:
    st.session_state.pack_editor_message = message
    st.session_state.pack_editor_message_kind = kind


def set_prompt_pack_message(message: str, kind: str = "info") -> None:
    st.session_state.prompt_pack_save_message = message
    st.session_state.prompt_pack_save_message_kind = kind


def render_status_message(message: str, kind: str = "info") -> None:
    toast = getattr(st, "toast", None)
    if callable(toast):
        toast(message)
        return

    border = {
        "success": "#26d36c",
        "error": "#ff5a5f",
        "warning": "#f5b84b",
        "info": "#7ea6d9",
    }.get(kind, "#7ea6d9")
    components.html(
        f"""
        <div class="bench-toast">{html.escape(message)}</div>
        <style>
          .bench-toast {{
            position: fixed;
            right: 1.25rem;
            bottom: 1.25rem;
            z-index: 999999;
            max-width: min(34rem, calc(100vw - 2rem));
            padding: 0.7rem 0.9rem;
            border: 1px solid {border};
            border-radius: 8px;
            background: rgba(18, 22, 27, 0.96);
            color: #f4f7fb;
            font: 500 0.92rem system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            box-shadow: 0 10px 32px rgba(0, 0, 0, 0.35);
            animation: benchToast 4.2s ease forwards;
          }}
          @keyframes benchToast {{
            0% {{ opacity: 0; transform: translateY(0.75rem); }}
            10%, 82% {{ opacity: 1; transform: translateY(0); }}
            100% {{ opacity: 0; transform: translateY(0.75rem); }}
          }}
        </style>
        """,
        height=0,
    )


ACTION_STATUS_MESSAGE_KEYS = [
    ("model_response_message", "model_response_message_kind"),
    ("benchmark_session_message", "benchmark_session_message_kind"),
    ("prompt_pack_save_message", "prompt_pack_save_message_kind"),
    ("pack_editor_message", "pack_editor_message_kind"),
    ("benchmark_export_message", "benchmark_export_message_kind"),
]


def clear_action_status_messages() -> None:
    for message_key, kind_key in ACTION_STATUS_MESSAGE_KEYS:
        st.session_state.pop(message_key, None)
        st.session_state.pop(kind_key, None)


def consume_status_message(message_key: str, kind_key: str) -> bool:
    message = st.session_state.pop(message_key, None)
    kind = st.session_state.pop(kind_key, "info")
    if message:
        render_status_message(str(message), str(kind or "info"))
        return True
    return False


def judging_active() -> bool:
    return bool(st.session_state.get("judging_active"))


def request_judge_run() -> None:
    clear_action_status_messages()
    st.session_state.judge_run_requested = True
    st.session_state.judging_active = True


def finish_judge_run() -> None:
    st.session_state.judging_active = False
    st.session_state.pop("judge_run_requested", None)
    render_judging_overlay(False)


JUDGING_OVERLAY_TEMPLATE = """
<script>
(() => {
  const doc = window.parent.document;
  const overlayId = "helcyon-bench-judging-overlay";
  const existing = doc.getElementById(overlayId);
  const active = __ACTIVE__;
  if (!active) {
    if (existing) existing.remove();
    return;
  }
  if (!doc.getElementById("helcyon-bench-judging-style")) {
    const style = doc.createElement("style");
    style.id = "helcyon-bench-judging-style";
    style.textContent = [
      "@keyframes hbGradientFlow { 0% { background-position: 0% 50%; } 50% { background-position: 100% 50%; } 100% { background-position: 0% 50%; } }",
      "@keyframes hbShimmer { from { transform: translateX(-160%); } to { transform: translateX(420%); } }",
      "@keyframes hbSpin { to { transform: rotate(360deg); } }",
      "@keyframes hbPulse { 0%, 100% { transform: scale(1); opacity: 1; } 50% { transform: scale(1.45); opacity: 0.72; } }",
      "@keyframes hbFloat { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(-5px); } }",
      ".hb-judging-frame { max-width: min(36rem, calc(100vw - 2rem)); width: 100%; padding: 2px; border-radius: 16px; background: linear-gradient(120deg, #ff5a5f, #f5b84b, #55d68f, #7ea6d9, #c77dff, #ff5a5f); background-size: 300% 300%; animation: hbGradientFlow 6s ease infinite, hbFloat 5s ease-in-out infinite; box-shadow: 0 18px 54px rgba(0,0,0,0.5), 0 0 34px rgba(126,166,217,0.28); }",
      ".hb-judging-card { border-radius: 14px; background: rgba(15, 19, 24, 0.97); color: #f4f7fb; padding: 1.1rem 1.25rem 1.2rem; font: 500 0.95rem system-ui, -apple-system, 'Segoe UI', sans-serif; line-height: 1.45; }",
      ".hb-judging-head { display: flex; align-items: center; gap: 0.6rem; }",
      ".hb-judging-spinner { width: 18px; height: 18px; border-radius: 50%; border: 3px solid rgba(126,166,217,0.25); border-top-color: #f5b84b; border-right-color: #7ea6d9; animation: hbSpin 0.9s linear infinite; flex: none; }",
      ".hb-judging-title { font-weight: 700; font-size: 1.05rem; background: linear-gradient(90deg, #f5b84b, #7ea6d9, #55d68f, #f5b84b); background-size: 250% 100%; -webkit-background-clip: text; background-clip: text; color: transparent; animation: hbGradientFlow 4s ease infinite; }",
      ".hb-judging-hint { color: #93a4bb; font-size: 0.83rem; margin-top: 0.3rem; }",
      ".hb-judging-label-row { display: flex; justify-content: space-between; align-items: baseline; gap: 0.8rem; margin-top: 0.95rem; }",
      ".hb-judging-label { color: #c6d3e4; font-size: 0.86rem; min-height: 1.1em; overflow-wrap: anywhere; }",
      ".hb-judging-percent { color: #f5b84b; font-weight: 700; font-size: 1rem; flex: none; font-variant-numeric: tabular-nums; }",
      ".hb-judging-track { position: relative; height: 12px; border-radius: 999px; background: rgba(126,166,217,0.16); overflow: hidden; margin-top: 0.5rem; }",
      ".hb-judging-fill { position: relative; height: 100%; width: 3%; border-radius: 999px; background: linear-gradient(90deg, #ff5a5f, #f5b84b, #55d68f, #7ea6d9, #c77dff); background-size: 220% 100%; animation: hbGradientFlow 3.2s linear infinite; transition: width 0.45s cubic-bezier(0.22, 1, 0.36, 1); box-shadow: 0 0 14px rgba(245,184,75,0.45); }",
      ".hb-judging-shimmer { position: absolute; top: 0; bottom: 0; left: 0; width: 32%; background: linear-gradient(110deg, transparent, rgba(255,255,255,0.4), transparent); animation: hbShimmer 1.5s ease-in-out infinite; }",
      ".hb-judging-dots { display: flex; gap: 0.5rem; justify-content: center; margin-top: 0.9rem; min-height: 10px; }",
      ".hb-dot { width: 10px; height: 10px; border-radius: 50%; background: rgba(126,166,217,0.25); transition: background 0.3s ease, box-shadow 0.3s ease; }",
      ".hb-dot-done { background: #55d68f; box-shadow: 0 0 8px rgba(85,214,143,0.6); }",
      ".hb-dot-active { background: #f5b84b; box-shadow: 0 0 10px rgba(245,184,75,0.8); animation: hbPulse 1s ease-in-out infinite; }"
    ].join("\\n");
    doc.head.appendChild(style);
  }
  const overlay = existing || doc.createElement("div");
  overlay.id = overlayId;
  const positionOverlay = () => {
    const main = doc.querySelector('[data-testid="stAppViewContainer"] main');
    const sidebar = doc.querySelector('[data-testid="stSidebar"]');
    let left = 0;
    if (main) {
      const rect = main.getBoundingClientRect();
      if (rect.width > 0 && rect.left >= 0 && rect.left < window.innerWidth) {
        left = rect.left;
      }
    } else if (sidebar) {
      const rect = sidebar.getBoundingClientRect();
      if (rect.width > 80 && rect.right > 0 && rect.left >= 0) {
        left = rect.right;
      }
    }
    overlay.style.left = `${Math.round(left)}px`;
  };
  window.__helcyonBenchJudgingPositionOverlay = positionOverlay;
  overlay.style.cssText = [
    "position: fixed",
    "left: 0",
    "right: 0",
    "top: 3.25rem",
    "bottom: 0",
    "z-index: 999998",
    "display: flex",
    "align-items: flex-start",
    "justify-content: center",
    "padding-top: 3rem",
    "background: rgba(6, 8, 12, 0.35)",
    "backdrop-filter: blur(1px)",
    "pointer-events: all"
  ].join(";");
  positionOverlay();
  if (!window.__helcyonBenchJudgingOverlayObserver) {
    window.__helcyonBenchJudgingOverlayObserver = new MutationObserver(() => {
      if (window.__helcyonBenchJudgingPositionOverlay) window.__helcyonBenchJudgingPositionOverlay();
    });
    window.__helcyonBenchJudgingOverlayObserver.observe(doc.body, { attributes: true, childList: true, subtree: true });
    window.addEventListener("resize", () => {
      if (window.__helcyonBenchJudgingPositionOverlay) window.__helcyonBenchJudgingPositionOverlay();
    });
  }
  if (existing) return;
  const frame = doc.createElement("div");
  frame.className = "hb-judging-frame";
  const notice = doc.createElement("div");
  notice.className = "hb-judging-card";
  notice.innerHTML = [
    "<div class='hb-judging-head'><div class='hb-judging-spinner'></div><div class='hb-judging-title'>Judging in progress</div></div>",
    "<div class='hb-judging-hint'>Please wait. Use the Stop button at the top to interrupt.</div>",
    "<div class='hb-judging-label-row'>",
    "<div id='helcyon-bench-judging-progress-label' class='hb-judging-label'>Warming up the judge...</div>",
    "<div id='helcyon-bench-judging-progress-percent' class='hb-judging-percent'>0%</div>",
    "</div>",
    "<div class='hb-judging-track'>",
    "<div id='helcyon-bench-judging-progress-fill' class='hb-judging-fill'><div class='hb-judging-shimmer'></div></div>",
    "</div>",
    "<div id='helcyon-bench-judging-progress-dots' class='hb-judging-dots'></div>"
  ].join("");
  frame.appendChild(notice);
  overlay.appendChild(frame);
  doc.body.appendChild(overlay);
})();
</script>
"""


def render_judging_overlay(active: bool) -> None:
    components.html(
        JUDGING_OVERLAY_TEMPLATE.replace("__ACTIVE__", "true" if active else "false"),
        height=0,
    )


def update_judging_overlay_progress(current: int, total: int, label: str) -> None:
    percent = 0 if total <= 0 else max(0, min(100, round(100 * current / total)))
    components.html(
        f"""
        <script>
        (() => {{
          const doc = window.parent.document;
          const fill = doc.getElementById("helcyon-bench-judging-progress-fill");
          const text = doc.getElementById("helcyon-bench-judging-progress-label");
          const percentEl = doc.getElementById("helcyon-bench-judging-progress-percent");
          const dots = doc.getElementById("helcyon-bench-judging-progress-dots");
          if (fill) fill.style.width = "{max(percent, 3)}%";
          if (text) text.textContent = {json.dumps(label)};
          if (percentEl) percentEl.textContent = "{percent}%";
          if (dots) {{
            const total = {int(total)};
            const current = {int(current)};
            if (total > 0 && total <= 24 && dots.childElementCount !== total) {{
              dots.innerHTML = "";
              for (let i = 0; i < total; i++) {{
                const dot = doc.createElement("div");
                dot.className = "hb-dot";
                dots.appendChild(dot);
              }}
            }}
            Array.from(dots.children).forEach((dot, i) => {{
              dot.className = "hb-dot" + (i < current ? " hb-dot-done" : i === current ? " hb-dot-active" : "");
            }});
          }}
        }})();
        </script>
        """,
        height=0,
    )


def render_action_button_classifier() -> None:
    components.html(
        """
        <script>
        (() => {
          const doc = window.parent.document;
          const classify = () => {
            doc.querySelectorAll(".stButton > button").forEach((button) => {
              const label = (button.innerText || button.textContent || "").trim().toLowerCase();
              button.classList.remove(
                "bench-btn-judge",
                "bench-btn-judging",
                "bench-btn-delete",
                "bench-btn-save",
                "bench-btn-edit",
                "bench-btn-title-edit",
                "bench-btn-trend"
              );
              if (!label) return;
              if (label === "✎") {
                button.classList.add("bench-btn-title-edit");
              } else if (label === "↗") {
                button.classList.add("bench-btn-trend");
              } else if (label.includes("judging")) {
                button.classList.add("bench-btn-judging");
              } else if (label === "judge" || label === "retest") {
                button.classList.add("bench-btn-judge");
              } else if (label.includes("delete")) {
                button.classList.add("bench-btn-delete");
              } else if (label.includes("save")) {
                button.classList.add("bench-btn-save");
              } else if (label.includes("edit") || label.includes("editor") || label.includes("rename")) {
                button.classList.add("bench-btn-edit");
              }
            });
            doc.querySelectorAll('div[data-testid="stExpander"] details summary').forEach((summary) => {
              const expander = summary.closest('div[data-testid="stExpander"]');
              summary.classList.add("bench-highlight-expander");
              if (expander) {
                expander.classList.add("bench-highlight-expander-wrap");
              }
            });
          };
          classify();
          if (!window.__helcyonBenchButtonObserver) {
            window.__helcyonBenchButtonObserver = new MutationObserver(classify);
            window.__helcyonBenchButtonObserver.observe(doc.body, { childList: true, subtree: true, characterData: true });
          }
        })();
        </script>
        """,
        height=0,
    )


def non_dismissible_dialog(title: str):
    dialog_kwargs = {}
    try:
        dialog_parameters = inspect.signature(st.dialog).parameters
    except (TypeError, ValueError):
        dialog_parameters = {}
    if "dismissible" in dialog_parameters:
        dialog_kwargs["dismissible"] = False
    return st.dialog(title, **dialog_kwargs)


def render_modal_dismiss_guard() -> None:
    components.html(
        """
        <script>
        (() => {
          const doc = window.parent.document;
          if (window.__helcyonBenchModalDismissGuard) return;
          window.__helcyonBenchModalDismissGuard = true;

          const activeDialog = () => {
            const dialogs = Array.from(doc.querySelectorAll('dialog[open], div[data-testid="stDialog"], [data-baseweb="modal"], [aria-modal="true"], div[role="dialog"]'));
            return dialogs.filter((dialog) => {
              const rect = dialog.getBoundingClientRect();
              return rect.width > 0 && rect.height > 0;
            }).pop();
          };

          const blockBackdropClick = (event) => {
            const dialog = activeDialog();
            if (!dialog) return;

            const target = event.target;
            const interactive = target.closest && target.closest(
              'button, input, textarea, select, a, [role="button"], [role="checkbox"], [role="radio"], [contenteditable="true"], [data-baseweb="select"]'
            );
            if (interactive && dialog.contains(interactive)) return;

            event.preventDefault();
            event.stopPropagation();
            event.stopImmediatePropagation();
          };

          ["pointerdown", "mousedown", "mouseup", "click", "dblclick", "touchstart", "touchend"].forEach((eventName) => {
            doc.addEventListener(eventName, blockBackdropClick, true);
            window.parent.addEventListener(eventName, blockBackdropClick, true);
          });

          doc.addEventListener("keydown", (event) => {
            if (event.key !== "Escape") return;
            const dialog = activeDialog();
            if (!dialog) return;
            const closeButton = Array.from(dialog.querySelectorAll("button")).find((button) => {
              const label = (button.innerText || button.textContent || "").trim().toLowerCase();
              return label === "cancel" || label === "close" || label === "close trend graph";
            });
            if (!closeButton) return;
            event.preventDefault();
            event.stopPropagation();
            event.stopImmediatePropagation();
            closeButton.click();
          }, true);
        })();
        </script>
        """,
        height=0,
    )


def align_with_label() -> None:
    st.markdown('<div class="bench-field-label-spacer"></div>', unsafe_allow_html=True)


def prompt_pack_editor_entries() -> list[dict[str, str]]:
    entries = []
    for slot in range(1, BATCH_SLOT_COUNT + 1):
        title = st.session_state.get(f"pack_editor_title_{slot}", "").strip()
        prompt = st.session_state.get(f"pack_editor_prompt_{slot}", "").strip()
        if not title and not prompt:
            continue
        if not title or not prompt:
            raise ValueError(f"Editor prompt {slot} needs both a title and prompt.")
        entries.append({"title": title, "prompt": prompt})
    if not entries:
        raise ValueError("Add at least one prompt before saving a pack.")
    return entries


def bind_pack_editor_to(path: Path, name: str | None = None) -> None:
    """Record which file on disk the editor's contents belong to, and the name it was
    loaded/saved under.

    Save/Rename always writes to this bound path (or its renamed target), never to
    whatever the top "Prompt Pack" picker happens to show — those two selections can
    drift apart and previously caused Save to silently overwrite the wrong pack.

    The bound name lets Save detect an actual rename attempt (the user edited Pack
    Name) versus a plain content save. Comparing filenames instead would misfire on
    packs whose on-disk filename doesn't match today's slug format (e.g. underscores
    from an older/hand-made file), turning an ordinary save into a spurious rename.
    """
    st.session_state.pack_editor_loaded_path = str(path)
    resolved_name = name if name is not None else st.session_state.get("pack_editor_name_input", "")
    st.session_state.pack_editor_loaded_name = resolved_name.strip()


def pack_editor_bound_path() -> Path | None:
    raw = st.session_state.get("pack_editor_loaded_path")
    if not raw:
        return None
    path = Path(raw)
    return path if path.exists() else None


def pack_editor_stale_bound_name() -> str | None:
    """The filename the editor was last bound to, if that file has since vanished.

    The editor's text fields keep whatever was typed even after the file they came
    from is renamed/deleted elsewhere — so the fields still look "loaded". Without
    this, the UI's only signal was a plain "nothing loaded" caption that flatly
    contradicted the populated form the user was staring at.
    """
    raw = st.session_state.get("pack_editor_loaded_path")
    if not raw:
        return None
    path = Path(raw)
    return None if path.exists() else path.name


def load_prompt_pack_into_editor() -> None:
    path = selected_prompt_pack_path()
    document = load_prompt_pack_document(path)
    prompts = document["prompts"]
    for slot in range(1, BATCH_SLOT_COUNT + 1):
        prompt = prompts[slot - 1] if slot - 1 < len(prompts) else {}
        st.session_state[f"pack_editor_title_{slot}"] = prompt.get("title", "")
        st.session_state[f"pack_editor_prompt_{slot}"] = prompt.get("prompt", "")
    st.session_state.pack_editor_name_input = document.get("name") or path.stem
    st.session_state.pack_editor_category_select = document.get("category") or "General"
    st.session_state.pack_editor_description_input = document.get("description") or ""
    st.session_state.pack_editor_judge_profile_select = document.get("judge_profile") or "General"
    bind_pack_editor_to(path)
    set_pack_editor_message(f"Loaded {path.name} into the editor.")


def load_visible_prompts_into_editor() -> None:
    path = selected_prompt_pack_path()
    current_document = load_prompt_pack_document(path)
    st.session_state.pack_editor_name_input = current_document.get("name") or path.stem
    st.session_state.pack_editor_category_select = current_document.get("category") or "General"
    st.session_state.pack_editor_description_input = current_document.get("description") or ""
    st.session_state.pack_editor_judge_profile_select = current_document.get("judge_profile") or "General"
    for slot in range(1, BATCH_SLOT_COUNT + 1):
        prompt = st.session_state.get(f"batch_prompt_{slot}", "").strip()
        st.session_state[f"pack_editor_title_{slot}"] = title_from_prompt(prompt, slot) if prompt else ""
        st.session_state[f"pack_editor_prompt_{slot}"] = prompt
    bind_pack_editor_to(path)
    set_pack_editor_message("Loaded visible benchmark prompts into the editor.")


def select_prompt_pack(path: Path) -> None:
    metadata = normalize_prompt_pack_metadata(load_prompt_pack_document(path), prompt_pack_label(path))
    label = prompt_pack_label(path)
    st.session_state.prompt_pack_category_select = metadata["category"]
    st.session_state.prompt_pack_select = label
    update_form_state_values(
        {
            "prompt_pack_category_select": metadata["category"],
            "prompt_pack_select": label,
            **prompt_pack_metadata_form_state_values(metadata),
        }
    )


def save_prompt_pack_from_editor() -> None:
    name = st.session_state.get("new_pack_name_input", "").strip()
    if not name:
        set_pack_editor_message("Type a New Pack Name before creating a new pack.", "error")
        return
    path = PROMPT_PACK_DIR / prompt_pack_filename(name)
    try:
        entries = prompt_pack_editor_entries()
        if path.exists():
            raise ValueError(
                f"{path.name} already exists. Type a different New Pack Name."
            )
        metadata = prompt_pack_editor_metadata(path)
        metadata["name"] = name
        save_prompt_pack(entries, path, metadata=metadata)
    except (OSError, ValueError) as exc:
        set_pack_editor_message(str(exc), "error")
        return
    select_prompt_pack(path)
    bind_pack_editor_to(path, name)
    st.session_state.pack_editor_name_input = name
    st.session_state.new_pack_name_input = ""
    st.session_state.show_save_as_new_pack = False
    message = f"Saved {len(entries)} prompt(s) as new pack {path.name}."
    set_pack_editor_message(message, "success")


def request_save_prompt_pack_as() -> None:
    st.session_state.show_save_as_new_pack = True


def cancel_save_prompt_pack_as() -> None:
    st.session_state.show_save_as_new_pack = False
    st.session_state.new_pack_name_input = ""


def pack_editor_rename_target(bound_path: Path, typed_name: str) -> tuple[Path, bool]:
    """Resolve what Save should write to, and whether that counts as a rename.

    Only treated as a rename if the Pack Name field was actually edited since load
    (compared against pack_editor_loaded_name) — comparing filenames directly would
    misfire whenever a pack's on-disk filename doesn't match today's slug format
    (e.g. underscores from an older file), turning a plain re-save into a spurious
    rename attempt. Shared by the save action and its UI preview so they can't drift.
    """
    loaded_name = st.session_state.get("pack_editor_loaded_name", "").strip()
    if typed_name.strip() == loaded_name:
        return bound_path, False
    target_path = PROMPT_PACK_DIR / prompt_pack_filename(typed_name)
    return target_path, target_path != bound_path


def save_prompt_pack_editor_to_selected() -> None:
    # Deliberately uses pack_editor_bound_path() (the file that was actually loaded
    # into this editor) rather than selected_prompt_pack_path() (the top picker).
    # The two can point at different packs — saving against the picker instead of the
    # loaded file is what previously let this button silently overwrite an unrelated
    # pack. See helcyon-bench-prompt-pack-save-bug memory.
    bound_path = pack_editor_bound_path()
    if bound_path is None:
        set_pack_editor_message(
            'No pack is loaded into this editor, so there is nothing to save over. '
            'Click "Load Selected Pack Into Editor" first, or use "Save As New Pack".',
            "error",
        )
        return
    try:
        entries = prompt_pack_editor_entries()
        metadata = prompt_pack_editor_metadata(bound_path)
    except ValueError as exc:
        set_pack_editor_message(str(exc), "error")
        return

    target_path, is_rename = pack_editor_rename_target(bound_path, metadata["name"])

    if is_rename and target_path.exists():
        set_pack_editor_message(
            f'Can\'t rename to "{metadata["name"]}" — {target_path.name} already exists as a '
            "different pack. Choose another name, or change Pack Name back to keep editing "
            f"{bound_path.name}.",
            "error",
        )
        return

    try:
        save_prompt_pack(entries, target_path, metadata=metadata)
        if is_rename:
            bound_path.unlink()
    except OSError as exc:
        set_pack_editor_message(str(exc), "error")
        return

    select_prompt_pack(target_path)
    bind_pack_editor_to(target_path, metadata["name"])
    if is_rename:
        message = f"Renamed {bound_path.name} to {target_path.name} and saved changes."
    else:
        message = f"Saved changes to {target_path.name}."
    set_pack_editor_message(message, "success")


def save_imported_prompt_pack(file_name: str, data: bytes) -> Path:
    try:
        raw = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read prompt pack JSON: {exc}") from exc

    fallback_name = Path(file_name).stem.replace("_", " ").replace("-", " ").title() or "Imported Prompt Pack"
    document = prompt_pack_document_from_raw(raw, fallback_name)
    path = PROMPT_PACK_DIR / prompt_pack_filename(document["name"])
    suffix = 2
    while path.exists():
        path = PROMPT_PACK_DIR / prompt_pack_filename(f"{document['name']} {suffix}")
        suffix += 1
    save_prompt_pack(document["prompts"], path, metadata=normalize_prompt_pack_metadata(document))
    return path


def import_prompt_pack_upload() -> None:
    upload = st.session_state.get("prompt_pack_import_upload")
    if not upload:
        set_pack_editor_message("Choose a JSON prompt pack export first.", "error")
        return
    try:
        saved_path = save_imported_prompt_pack(upload.name, upload.getvalue())
    except (OSError, ValueError) as exc:
        set_pack_editor_message(str(exc), "error")
        return
    select_prompt_pack(saved_path)
    load_prompt_pack_into_editor()
    set_pack_editor_message(f"Imported {saved_path.name} into prompt packs.", "success")


def save_visible_prompts_to_pack() -> None:
    entries = visible_prompt_pack_entries()
    if len(entries) != BATCH_SLOT_COUNT:
        set_prompt_pack_message("Fill all five visible benchmark prompt fields before saving.", "error")
        return

    path = selected_prompt_pack_path()
    try:
        save_prompt_pack(entries, path)
    except OSError as exc:
        set_prompt_pack_message(str(exc), "error")
        return
    select_prompt_pack(path)
    set_prompt_pack_message(f"Saved {len(entries)} visible benchmark prompt(s) to {path.name}.", "success")


def visible_benchmark_items(default_model_name_a: str, default_model_name_b: str) -> list[dict[str, str]]:
    model_name_a = model_name_or_default("A", default_model_name_a)
    model_name_b = model_name_or_default("B", default_model_name_b)
    items = []
    for slot in range(1, BATCH_SLOT_COUNT + 1):
        current_prompt = st.session_state.get(f"batch_prompt_{slot}", "").strip()
        response_a = st.session_state.get(f"batch_response_a_{slot}", "").strip()
        response_b = st.session_state.get(f"batch_response_b_{slot}", "").strip()
        if not current_prompt and not response_a and not response_b:
            continue
        if not current_prompt or not response_a or not response_b:
            raise ValueError(f"Prompt {slot} needs Prompt, Response A, and Response B.")
        metadata = prompt_pack_metadata(current_prompt, slot)
        items.append(
            {
                "id": metadata["id"],
                "category": metadata["category"],
                "title": metadata["title"],
                "evaluation_focus": metadata["evaluation_focus"],
                "conversation_context": "",
                "current_prompt": current_prompt,
                "response_a": response_a,
                "response_b": response_b,
                "model_name_a": model_name_a,
                "model_name_b": model_name_b,
            }
        )
    if not items:
        raise ValueError("Add at least one complete prompt with Response A and Response B.")
    return items


def load_selected_prompt_pack() -> None:
    path = selected_prompt_pack_path()
    metadata = remember_prompt_pack_metadata(selected_prompt_pack_report_metadata(), persist=True)
    prompts = load_prompt_pack(path)
    for slot in range(1, BATCH_SLOT_COUNT + 1):
        st.session_state[f"batch_prompt_{slot}"] = ""
        st.session_state[f"batch_response_a_{slot}"] = ""
        st.session_state[f"batch_response_b_{slot}"] = ""
    for slot, prompt in enumerate(prompts[:BATCH_SLOT_COUNT], start=1):
        st.session_state[f"batch_prompt_{slot}"] = prompt["prompt"]
    st.session_state.confirm_load_prompt_pack = False
    set_prompt_pack_message(
        f"Loaded {min(len(prompts), BATCH_SLOT_COUNT)} prompt(s) from {path.name} ({metadata['category']})."
    )


def load_helcyon_companion_v1_prompts() -> None:
    path = initial_prompt_pack_path()
    if path:
        st.session_state.prompt_pack_select = prompt_pack_label(path)
    load_selected_prompt_pack()


def request_prompt_pack_load() -> None:
    if has_visible_batch_input():
        st.session_state.confirm_load_prompt_pack = True
    else:
        load_selected_prompt_pack()


def request_prompt_pack_delete() -> None:
    st.session_state.confirm_delete_prompt_pack = True


def select_first_available_prompt_pack(preferred_category: str = "Companion") -> None:
    groups = available_prompt_pack_groups()
    if not groups:
        st.session_state.pop("prompt_pack_category_select", None)
        st.session_state.pop("prompt_pack_select", None)
        remove_form_state(["prompt_pack_category_select", "prompt_pack_select"])
        return
    categories = ordered_pack_categories(groups)
    category = preferred_category if preferred_category in groups else categories[0]
    label = next(iter(groups[category].keys()))
    st.session_state.prompt_pack_category_select = category
    st.session_state.prompt_pack_select = label
    metadata = selected_prompt_pack_report_metadata()
    update_form_state_values(
        {
            "prompt_pack_category_select": category,
            "prompt_pack_select": label,
            **prompt_pack_metadata_form_state_values(metadata),
        }
    )


def delete_selected_prompt_pack() -> None:
    path = selected_prompt_pack_path()
    try:
        label = prompt_pack_label(path)
    except (OSError, json.JSONDecodeError, ValueError):
        label = path.name
    try:
        available_files = list(PROMPT_PACK_DIR.glob("*.json"))
        if len(available_files) <= 1:
            raise ValueError("Keep at least one prompt pack.")
        path.unlink()
    except (OSError, ValueError) as exc:
        set_prompt_pack_message(f"Could not delete {path.name}: {exc}", "error")
        st.session_state.confirm_delete_prompt_pack = False
        return
    st.session_state.confirm_delete_prompt_pack = False
    select_first_available_prompt_pack()
    set_prompt_pack_message(f"Deleted prompt pack {label} ({path.name}).", "success")


def optional_score(value: object) -> float | None:
    """A criterion score that may legitimately be absent.

    None means the criterion was ruled not applicable for this comparison. It is
    never silently coerced to 0 or to a neutral number, because doing so would
    let an irrelevant criterion move a model's total.
    """
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_score(value: object, digits: int = 2) -> str:
    score = optional_score(value)
    if score is None:
        return NOT_APPLICABLE_LABEL
    return f"{score:.{digits}f}"


def format_optional_difference(left: object, right: object) -> str:
    """Difference between two scores, or N/A when either side is absent."""
    first = optional_score(left)
    second = optional_score(right)
    if first is None or second is None:
        return NOT_APPLICABLE_LABEL
    return format_difference(second - first)


def category_is_not_applicable(result: dict, category: str) -> bool:
    applicability = result.get(APPLICABILITY_KEY)
    if isinstance(applicability, dict) and category in applicability:
        return str(applicability.get(category)) == NOT_APPLICABLE
    responses = result.get("responses", {})
    if not isinstance(responses, dict):
        return False
    return any(
        isinstance(responses.get(side), dict)
        and category in (responses.get(side, {}).get("scores", {}) or {})
        and responses[side]["scores"][category] is None
        for side in ("A", "B")
    )


def ordered_score_categories(scores_maps: list[dict]) -> list[str]:
    categories = list(CATEGORY_WEIGHTS)
    seen = set(categories)
    for scores in scores_maps:
        for category in scores:
            if category not in seen:
                categories.append(category)
                seen.add(category)
    return categories


def batch_score_categories(batch_results: list[dict]) -> list[str]:
    scores_maps = []
    for entry in batch_results:
        responses = entry.get("result", {}).get("responses", {})
        for side in ("A", "B"):
            scores_maps.append(responses.get(side, {}).get("scores", {}))
    return ordered_score_categories(scores_maps)


def batch_average_rows(batch_results: list[dict]) -> list[dict[str, str]]:
    if not batch_results:
        return []
    rows = []
    for category in batch_score_categories(batch_results):
        values_a: list[float] = []
        values_b: list[float] = []
        na_prompts = 0
        for entry in batch_results:
            scores = entry["result"].get("responses", {})
            score_a = optional_score(scores.get("A", {}).get("scores", {}).get(category))
            score_b = optional_score(scores.get("B", {}).get("scores", {}).get(category))
            # A criterion ruled not applicable for a prompt contributes to
            # neither the numerator nor the denominator for that prompt.
            if score_a is None or score_b is None:
                na_prompts += 1
                continue
            values_a.append(score_a)
            values_b.append(score_b)
        average_a = sum(values_a) / len(values_a) if values_a else None
        average_b = sum(values_b) / len(values_b) if values_b else None
        label = category
        if na_prompts:
            label = f"{category} ({NOT_APPLICABLE_LABEL} in {na_prompts} of {len(batch_results)})"
        rows.append(
            {
                "Category": label,
                "Response A Avg": format_score(average_a),
                "Response B Avg": format_score(average_b),
                "Difference": format_optional_difference(average_a, average_b),
            }
        )

    overall_a, overall_b = batch_overall_scores(batch_results)
    rows.append(
        {
            "Category": "Overall",
            "Response A Avg": f"{overall_a:.2f}",
            "Response B Avg": f"{overall_b:.2f}",
            "Difference": format_difference(overall_b - overall_a),
        }
    )
    return rows


def batch_overall_scores(batch_results: list[dict]) -> tuple[float, float]:
    if not batch_results:
        return 0.0, 0.0
    overall_a = sum(
        optional_score(entry["result"].get("responses", {}).get("A", {}).get("overall")) or 0.0
        for entry in batch_results
    ) / len(batch_results)
    overall_b = sum(
        optional_score(entry["result"].get("responses", {}).get("B", {}).get("overall")) or 0.0
        for entry in batch_results
    ) / len(batch_results)
    return overall_a, overall_b


def batch_position_sensitive_entries(batch_results: list[dict]) -> list[int]:
    """1-based indexes of comparisons whose winner flipped with the order."""
    flagged = []
    for index, entry in enumerate(batch_results, start=1):
        stability = result_stability(entry.get("result", {}))
        if stability and stability.get("position_sensitive"):
            flagged.append(index)
    return flagged


def batch_legacy_entry_count(batch_results: list[dict]) -> int:
    return sum(1 for entry in batch_results if is_legacy_single_pass(entry.get("result", {})))


def batch_positional_difference(batch_results: list[dict]) -> float | None:
    """Mean slot-A-minus-slot-B overall across every directional pass.

    A value near zero means the judge showed no slot preference on this run; a
    large positive value means the slot labelled Response A was scored higher
    regardless of which model occupied it.
    """
    differences = [
        float(stability.get("positional_difference", 0.0))
        for stability in (result_stability(entry.get("result", {})) for entry in batch_results)
        if stability is not None
    ]
    if not differences:
        return None
    return round(sum(differences) / len(differences), 2)


def stability_label(result: dict) -> str:
    if is_legacy_single_pass(result):
        return "Legacy single pass"
    stability = result_stability(result) or {}
    grade = stability.get("stability_grade")
    if grade in STABILITY_GRADE_LABELS:
        return STABILITY_GRADE_LABELS[grade]
    # Bidirectional records saved before stability_grade existed still carry
    # the binary flag; fall back to that rather than guessing a graded value.
    return "Position-sensitive" if stability.get("position_sensitive") else "Stable"


def result_classification(result: dict) -> str:
    """Classification constant for a result, with a best-effort legacy fallback.

    Bidirectional results always carry winner.classification (see
    combine_bidirectional_results). Older saved records that predate the
    classification system are classified on the fly from their score
    difference using the default thresholds, so historical files still render
    something sensible rather than crashing or showing a blank label.
    """
    winner = result.get("winner", {}) or {}
    classification = winner.get("classification")
    if classification in CLASSIFICATION_LABELS:
        return classification
    score_difference = winner.get("score_difference")
    if score_difference is None:
        return CLASSIFICATION_CLEAR_WINNER
    return classify_score_gap(float(score_difference), EFFECTIVE_TIE_THRESHOLD, CLEAR_WINNER_THRESHOLD)


def classification_label(result: dict) -> str:
    return CLASSIFICATION_LABELS.get(result_classification(result), "")


def winner_display(result: dict, model_name_a: str, model_name_b: str) -> str:
    """Headline winner text. Must never present an Effective Tie as a clean win."""
    name = winner_title(result, model_name_a, model_name_b)
    classification = result_classification(result)
    if classification == CLASSIFICATION_EFFECTIVE_TIE:
        response_key = str(result.get("winner", {}).get("response", "")).upper()
        if response_key in ("A", "B"):
            return f"Effective Tie (leaning {name})"
        return "Effective Tie"
    if classification == CLASSIFICATION_NARROW_PREFERENCE:
        return f"{name} (Narrow Preference)"
    return name


def batch_classification_thresholds(batch_results: list[dict]) -> tuple[float, float]:
    """The effective-tie/clear-winner thresholds actually used for this run.

    Every comparison in a batch is judged under the same run configuration, so
    reading the first bidirectional result's stored thresholds keeps the
    batch-level winner classified with the same numbers as each individual
    comparison, without app.py needing to know about AppConfig at all.
    """
    for entry in batch_results:
        thresholds = ((entry.get("result", {}) or {}).get("protocol", {}) or {}).get("thresholds")
        if isinstance(thresholds, dict) and "effective_tie_threshold" in thresholds and "clear_winner_threshold" in thresholds:
            return float(thresholds["effective_tie_threshold"]), float(thresholds["clear_winner_threshold"])
    return EFFECTIVE_TIE_THRESHOLD, CLEAR_WINNER_THRESHOLD


def benchmark_classification_counts(batch_results: list[dict]) -> dict[str, int]:
    counts = {label: 0 for label in CLASSIFICATION_LABELS}
    for entry in batch_results:
        counts[result_classification(entry.get("result", {}))] += 1
    return counts


def benchmark_winner_name(batch_results: list[dict]) -> str:
    """Batch-level aggregate winner, classified with the same thresholds and
    terminology as every individual comparison (see result_classification)
    rather than its own separate hardcoded tie epsilon.
    """
    if not batch_results:
        return "Unknown"
    overall_a, overall_b = batch_overall_scores(batch_results)
    first_item = batch_results[0]["item"]
    name_a = response_title("A", first_item.get("model_name_a", ""))
    name_b = response_title("B", first_item.get("model_name_b", ""))
    difference = overall_a - overall_b
    effective_tie_threshold, clear_winner_threshold = batch_classification_thresholds(batch_results)
    classification = classify_score_gap(difference, effective_tie_threshold, clear_winner_threshold)
    leaning_name = name_a if difference >= 0 else name_b
    if classification == CLASSIFICATION_EFFECTIVE_TIE:
        if abs(difference) < 0.005:
            return "Effective Tie"
        return f"Effective Tie (leaning {leaning_name})"
    if classification == CLASSIFICATION_NARROW_PREFERENCE:
        return f"{leaning_name} (Narrow Preference)"
    return leaning_name


def inject_persistence_script(clear_nonce: str | None = None, clear_keys: list[str] | None = None) -> None:
    # Form values persist server-side via .llmbench_form_state.json only. The old
    # localStorage restore loop re-injected stale browser-local text over freshly
    # rendered widgets when returning from the Results Dashboard, wiping fields,
    # and its select handling rewrote the URL with corrupted values that could
    # trigger full-page reloads. This script now only removes that legacy
    # browser state so older tabs cannot resurrect it.
    del clear_nonce, clear_keys
    components.html(
        """
        <script>
        (() => {
          const prefix = "llmbench.form.";
          const win = window.parent;
          try {
            const stale = [];
            for (let index = 0; index < win.localStorage.length; index += 1) {
              const key = win.localStorage.key(index);
              if (key && key.indexOf(prefix) === 0) stale.push(key);
            }
            stale.forEach((key) => win.localStorage.removeItem(key));
          } catch (error) {}
          try {
            const url = new URL(win.location.href);
            let changed = false;
            ["llmb_judge_endpoint", "llmb_judge_model"].forEach((param) => {
              if (url.searchParams.has(param)) {
                url.searchParams.delete(param);
                changed = true;
              }
            });
            if (changed) win.history.replaceState(null, "", url.toString());
          } catch (error) {}
        })();
        </script>
        """,
        height=0,
    )


def field_clipboard_controls(field_key: str, label: str, kind: str) -> None:
    selector = "textarea" if kind == "textarea" else "input"
    label_json = json.dumps(label)
    selector_json = json.dumps(selector)
    header_cols = st.columns([1, 0.14])
    with header_cols[0]:
        st.markdown(
            f'<div style="font-weight:700;font-size:0.9rem;margin:0.12rem 0 0.18rem;">{html.escape(label)}</div>',
            unsafe_allow_html=True,
        )
    with header_cols[1]:
        components.html(
            f"""
            <div class="llmbench-clip-row">
              <button type="button" id="copy" title="Copy {html.escape(label)}">⧉</button>
              <button type="button" id="paste" title="Paste into {html.escape(label)}">⤓</button>
            </div>
            <style>
              body {{
                margin: 0;
                background: transparent;
              }}
              .llmbench-clip-row {{
                display: flex;
                justify-content: flex-end;
                gap: 0.25rem;
                padding: 0;
              }}
              .llmbench-clip-row button {{
                width: 1.55rem;
                height: 1.55rem;
                border: 1px solid rgba(120, 148, 184, 0.58);
                border-radius: 6px;
                background: rgba(21, 24, 28, 0.88);
                color: #edf3fa;
                font-size: 0.78rem;
                line-height: 1;
                cursor: pointer;
              }}
              .llmbench-clip-row button:hover {{
                border-color: rgba(237, 243, 250, 0.9);
                background: rgba(51, 73, 96, 0.96);
              }}
            </style>
            <script>
            (() => {{
              const label = {label_json};
              const selector = {selector_json};
              const doc = window.parent.document;
              const win = window.parent;

              function byAria() {{
                const escaped = label.replace(/"/g, '\\\\"');
                return doc.querySelector(`${{selector}}[aria-label="${{escaped}}"]`);
              }}

              function fieldElement() {{
                const labels = Array.from(doc.querySelectorAll("label"));
                const widgetLabel = labels.find((item) => item.textContent.trim() === label);
                const root = widgetLabel?.closest('[data-testid="stTextArea"], [data-testid="stTextInput"]');
                return root?.querySelector(selector) || byAria();
              }}

              function setElementValue(el, value) {{
                if (!el) return;
                const setter = Object.getOwnPropertyDescriptor(el.__proto__, "value")?.set;
                if (setter) setter.call(el, value);
                else el.value = value;
                el.dispatchEvent(new Event("input", {{ bubbles: true }}));
                el.dispatchEvent(new Event("change", {{ bubbles: true }}));
              }}

              document.getElementById("copy").addEventListener("click", async () => {{
                const el = fieldElement();
                if (!el || !win.navigator.clipboard) return;
                await win.navigator.clipboard.writeText(el.value || "");
              }});

              document.getElementById("paste").addEventListener("click", async () => {{
                const el = fieldElement();
                if (!el || !win.navigator.clipboard) return;
                const text = await win.navigator.clipboard.readText();
                el.focus();
                setElementValue(el, text);
                // Blur commits the value to Streamlit so it reaches session
                // state and file persistence instead of living only in the DOM.
                el.blur();
              }});
            }})();
            </script>
            """,
            height=28,
        )


def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def available_rubrics() -> dict[str, Path]:
    rubrics = {}
    for path in sorted(RUBRIC_DIR.glob("*.md")):
        name = path.stem.replace("_", " ").replace("-", " ").title()
        rubrics[name] = path
    return rubrics


def active_scoring_rubric_path(rubrics: dict[str, Path]) -> Path | None:
    active_path = RUBRIC_DIR / ACTIVE_RUBRIC_FILENAME
    if active_path.exists():
        return active_path
    if rubrics:
        return next(iter(rubrics.values()))
    return None


def pack_rubric_filename(metadata: dict[str, str]) -> str:
    rubric_key = str(metadata.get("rubric") or "").strip().lower()
    if rubric_key:
        # Unknown keys resolve to "<key>.md" so a typo surfaces as a visible
        # missing-rubric warning instead of silently using the default.
        return RUBRIC_FILENAMES_BY_KEY.get(rubric_key, f"{rubric_key}.md")
    category = str(metadata.get("category") or "")
    if category in RUBRIC_FILENAME_BY_PACK_CATEGORY:
        return RUBRIC_FILENAME_BY_PACK_CATEGORY[category]
    return ACTIVE_RUBRIC_FILENAME


def scoring_rubric_path_for_pack(
    rubrics: dict[str, Path],
    metadata: dict[str, str],
) -> tuple[Path | None, str | None]:
    preferred = RUBRIC_DIR / pack_rubric_filename(metadata)
    if preferred.exists():
        return preferred, None
    fallback = active_scoring_rubric_path(rubrics)
    if fallback is None:
        return None, None
    warning = (
        f"Rubric {preferred.name} for the {metadata.get('category') or 'selected'} prompt pack "
        f"was not found in rubrics; scoring with {fallback.name} instead."
    )
    return fallback, warning


def pack_extra_score_categories(metadata: dict[str, str], scoring_rubric_path: Path | None) -> list[str]:
    # Extra categories are only requested when the standalone rubric that defines
    # them was actually loaded; if routing fell back to the default rubric,
    # scoring undefined categories would force the judge to guess.
    if scoring_rubric_path is None:
        return []
    category = str(metadata.get("category") or "")
    expected_filename = RUBRIC_FILENAME_BY_PACK_CATEGORY.get(category)
    if expected_filename is None or scoring_rubric_path.name != expected_filename:
        return []
    return list(EXTRA_SCORE_CATEGORIES_BY_PACK_CATEGORY.get(category, []))


def pack_signals_distress(metadata: dict[str, str]) -> bool:
    category = str(metadata.get("category") or "").strip().lower()
    if category in DISTRESS_SIGNAL_CATEGORIES:
        return True
    haystack = " ".join(
        str(metadata.get(field) or "") for field in ("name", "description")
    ).lower()
    return any(term in haystack for term in DISTRESS_SIGNAL_TERMS)


def scoring_rubric_text_for_pack(scoring_rubric_path: Path, metadata: dict[str, str]) -> str:
    # The distress addendum is appended after — never in place of — the primary
    # rubric so the primary scoring stays intact while adding calibration
    # guidance for genuine distress prompts.
    text = load_text(scoring_rubric_path)
    if not pack_signals_distress(metadata):
        return text
    distress_path = RUBRIC_DIR / DISTRESS_RUBRIC_FILENAME
    if not distress_path.exists():
        return text
    return f"{text.rstrip()}\n\n---\n\n{load_text(distress_path)}"


def model_name_or_default(key: str, model_name: str) -> str:
    return model_name.strip() or f"Model {key}"


def response_title(key: str, model_name: str) -> str:
    return model_name_or_default(key, model_name)


def distinct_model_labels(model_name_a: str, model_name_b: str) -> tuple[str, str]:
    """Column labels that stay distinct when both sides carry the same name.

    Result tables are keyed by these labels, so identical names would otherwise
    collapse two columns into one.
    """
    label_a = response_title("A", model_name_a)
    label_b = response_title("B", model_name_b)
    if label_a == label_b:
        return f"{label_a} (A)", f"{label_b} (B)"
    return label_a, label_b


def winner_title(result: dict, model_name_a: str, model_name_b: str) -> str:
    winner = result.get("winner", {})
    response_key = str(winner.get("response", "")).upper()
    if response_key == "A":
        return response_title("A", model_name_a)
    if response_key == "B":
        return response_title("B", model_name_b)
    if response_key == "TIE":
        return "Tie"
    return str(winner.get("model_name", "")) or "Unknown"


def attach_winner_model_name(result: dict, model_name_a: str, model_name_b: str) -> dict:
    winner = result.get("winner")
    if not isinstance(winner, dict):
        return result
    response_key = str(winner.get("response", "")).upper()
    if response_key == "A":
        winner["model_name"] = response_title("A", model_name_a)
    elif response_key == "B":
        winner["model_name"] = response_title("B", model_name_b)
    elif response_key == "TIE":
        winner["model_name"] = "Tie"
    return result


def render_metric_row(label: str, score: object) -> None:
    st.markdown(
        f"""
        <div class="score-row">
            <span>{html.escape(label)}</span>
            <strong>{format_score(score, 1)}</strong>
        </div>
        """,
        unsafe_allow_html=True,
    )


def format_difference(value: float) -> str:
    if abs(value) < 0.005:
        return "0.00"
    return f"{value:+.2f}"


def format_run_date(value: object, include_time: bool = False) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text.replace("T", " ")
    return parsed.strftime("%d %b %Y %H:%M") if include_time else parsed.strftime("%d %b %Y")


def score_summary_rows(result: dict) -> list[dict[str, str]]:
    response_a = result.get("responses", {}).get("A", {})
    response_b = result.get("responses", {}).get("B", {})
    scores_a = response_a.get("scores", {})
    scores_b = response_b.get("scores", {})
    rows = []
    for category in ordered_score_categories([scores_a, scores_b]):
        not_applicable = category_is_not_applicable(result, category)
        score_a = None if not_applicable else scores_a.get(category)
        score_b = None if not_applicable else scores_b.get(category)
        rows.append(
            {
                "Category": f"{category} ({NOT_APPLICABLE_LABEL})" if not_applicable else category,
                "Response A": format_score(score_a, 1),
                "Response B": format_score(score_b, 1),
                "Difference": format_optional_difference(score_a, score_b),
            }
        )

    overall_a = optional_score(response_a.get("overall")) or 0.0
    overall_b = optional_score(response_b.get("overall")) or 0.0
    rows.append(
        {
            "Category": "Overall",
            "Response A": f"{overall_a:.2f}",
            "Response B": f"{overall_b:.2f}",
            "Difference": format_difference(overall_b - overall_a),
        }
    )
    return rows


def render_score_panel(key: str, label: str, result: dict) -> None:
    response = result.get("responses", {}).get(key, {})
    overall = optional_score(response.get("overall")) or 0.0
    st.markdown(
        f"""
        <div class="result-card">
            <h3>{html.escape(label)}</h3>
            <div class="mini-score"><strong>{overall:.2f}</strong><span>/ 10</span></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown("**Category breakdown**")
    for score_label, score in response.get("scores", {}).items():
        render_metric_row(score_label, float(score))


def render_directional_pass_table(result: dict) -> None:
    """Show both directional judgements so neither pass is hidden by the average."""
    passes = result_directional_passes(result)
    if not passes:
        return
    rows = []
    for entry in passes:
        pass_result = entry.get("result", {}) or {}
        slot_models = entry.get("slot_models", {}) or {}
        responses = pass_result.get("responses", {}) or {}
        slot_winner = str((pass_result.get("winner") or {}).get("response", "")).upper()
        rows.append(
            {
                "Order": "First (A/B)" if entry.get("pass") == FORWARD else "Reversed (B/A)",
                "Slot A": slot_models.get("A", ""),
                "Slot B": slot_models.get("B", ""),
                "Slot A score": format_score(responses.get("A", {}).get("overall")),
                "Slot B score": format_score(responses.get("B", {}).get("overall")),
                "Pass winner": slot_models.get(
                    slot_winner, "Tie" if slot_winner == "TIE" else "Unknown"
                ),
            }
        )
    st.markdown("**Directional Passes**")
    st.table(rows)


def directional_pass_report_lines(result: dict, model_name_a: str, model_name_b: str) -> list[str]:
    """Preserve both directional judgements verbatim in the report.

    Each pass is reported under the model identities that actually occupied its
    slots, so a reader can see exactly what the judge said in each order rather
    than only the averaged outcome.
    """
    passes = result_directional_passes(result)
    if not passes:
        return []
    lines = [
        "",
        "## Directional Passes",
        "",
        "Each comparison is judged twice with the responses swapped between the slots. "
        "The scores below are the raw per-pass marks before identity mapping.",
        "",
        "| Pass | Slot A | Slot B | Slot A score | Slot B score | Pass winner |",
        "| --- | --- | --- | ---: | ---: | --- |",
    ]
    for entry in passes:
        pass_result = entry.get("result", {}) or {}
        slot_models = entry.get("slot_models", {}) or {}
        responses = pass_result.get("responses", {}) or {}
        slot_winner = str((pass_result.get("winner") or {}).get("response", "")).upper()
        winner_name = slot_models.get(slot_winner, "Tie" if slot_winner == "TIE" else "Unknown")
        lines.append(
            f"| {entry.get('pass', '')} "
            f"| {slot_models.get('A', '')} "
            f"| {slot_models.get('B', '')} "
            f"| {format_score(responses.get('A', {}).get('overall'))} "
            f"| {format_score(responses.get('B', {}).get('overall'))} "
            f"| {winner_name} |"
        )

    for entry in passes:
        pass_result = entry.get("result", {}) or {}
        slot_models = entry.get("slot_models", {}) or {}
        lines.extend(
            [
                "",
                f"### {str(entry.get('pass', '')).title()} pass verdict "
                f"(slot A = {slot_models.get('A', '')}, slot B = {slot_models.get('B', '')})",
                "",
                str(pass_result.get("final_verdict", "")),
            ]
        )
    return lines


def report_markdown(
    result: dict,
    conversation_context: str,
    current_prompt: str,
    response_a: str,
    response_b: str,
    model_name_a: str,
    model_name_b: str,
    rubric_name: str,
    judge_endpoint_name: str,
    judge_model: str,
    prompt_pack_metadata: dict[str, str],
) -> str:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    winner = result.get("winner", {})
    winner_name = winner_display(result, model_name_a, model_name_b)
    confidence = int(winner.get("confidence", 0))
    stability = result_stability(result)
    legacy = is_legacy_single_pass(result)
    category_agreement_info = result.get("category_agreement") or {}
    verdict_consistency = result.get("verdict_consistency") or {}

    lines = [
        f"# {APP_NAME} Comparison Report",
        "",
        f"- Generated: {generated_at}",
        f"- Prompt Pack: {prompt_pack_metadata['name']}",
        f"- Category: {prompt_pack_metadata['category']}",
        f"- Rubric: {rubric_name}",
        f"- Judge Profile: {prompt_pack_metadata['judge_profile']}",
        f"- Judge endpoint: {judge_endpoint_name}",
        f"- Judge model: {judge_model}",
        f"- Evaluation protocol: {'single pass, legacy record' if legacy else 'bidirectional (both response orders judged)'}",
        f"- Canonical winner: {winner_name}",
        f"- Classification: {classification_label(result)}",
        f"- Confidence: {confidence}%",
    ]
    if legacy:
        lines.append(
            "- NOTE: This comparison was judged in one response order only. It carries no "
            "position-stability evidence and must not be read as a bidirectional result."
        )
    elif stability:
        lines.extend(
            [
                f"- Result stability: {stability_label(result)}",
                f"- First order winner: {stability.get('forward_winner_model', 'Unknown')}",
                f"- Reversed order winner: {stability.get('reverse_winner_model', 'Unknown')}",
                f"- Slot effect (Response A mean minus Response B mean): {stability.get('positional_difference', 0.0):+.2f}",
            ]
        )
        if category_agreement_info:
            lines.append(
                "- Category agreement: "
                f"{category_agreement_info.get('agreement_fraction', 1.0) * 100:.0f}% "
                f"({category_agreement_info.get('agreeing', 0)} agreeing, "
                f"{category_agreement_info.get('disagreeing', 0)} disagreeing, "
                f"{category_agreement_info.get('tied', 0)} tied)"
            )
        if stability.get("warning"):
            lines.append(f"- NOTE: {stability.get('warning', '')}")
    if verdict_consistency.get("conflict"):
        pass_warning = (
            (verdict_consistency.get(FORWARD) or {}).get("warning")
            or (verdict_consistency.get(REVERSE) or {}).get("warning")
            or "The written verdict disagreed with the calculated numeric winner in at least one pass."
        )
        lines.append(f"- WARNING: Verdict/score conflict. {pass_warning}")

    lines.extend(
        [
            "",
            "## Canonical Score Summary",
            "",
            "Scores are averaged across both response orders and attributed to model identity, "
            "not to the A/B slot.",
            "",
            "| Category | {} | {} | Difference |".format(
                *distinct_model_labels(model_name_a, model_name_b)
            ),
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for row in score_summary_rows(result):
        lines.append(
            f"| {row['Category']} | {row['Response A']} | {row['Response B']} | {row['Difference']} |"
        )

    lines.extend(directional_pass_report_lines(result, model_name_a, model_name_b))

    lines.extend(
        [
            "",
            "## Independent Scores",
            "",
        ]
    )

    for key, name in [("A", response_title("A", model_name_a)), ("B", response_title("B", model_name_b))]:
        response_result = result.get("responses", {}).get(key, {})
        lines.extend([f"### {name}", "", f"- Overall: {format_score(response_result.get('overall'))} / 10"])
        for label, score in response_result.get("scores", {}).items():
            lines.append(f"- {label}: {format_score(score, 1)}")
        position_overall = response_result.get("position_overall")
        if isinstance(position_overall, dict) and position_overall:
            slots = ", ".join(
                f"slot {slot} {format_score(value)}" for slot, value in sorted(position_overall.items())
            )
            lines.append(f"- Overall by position: {slots}")
        strengths = response_result.get("strengths", [])
        weaknesses = response_result.get("weaknesses", [])
        if strengths:
            lines.extend(["", "Strengths:"])
            lines.extend(f"- {item}" for item in strengths)
        if weaknesses:
            lines.extend(["", "Weaknesses:"])
            lines.extend(f"- {item}" for item in weaknesses)
        deductions = response_result.get("deductions", {})
        if deductions:
            lines.extend(["", "Deductions from full marks:"])
            for category in ordered_score_categories([response_result.get("scores", {}), deductions]):
                lines.append(f"- {category}: {deductions.get(category, '')}")
        lines.append("")

    comparison_heading = "## Comparison" if legacy else "## Comparison (first order)"
    lines.extend([comparison_heading, ""])
    comparison = result.get("comparison", {})
    for key, label in COMPARISON_LABELS.items():
        lines.extend([f"### {label}", "", str(comparison.get(key, "")), ""])

    lines.extend(
        [
            "## Final Verdict",
            "",
            str(result.get("final_verdict", "")),
            "",
        ]
    )
    lines.extend(
        [
            "## Prompt",
            "",
            current_prompt,
            "",
            f"## Response from {response_title('A', model_name_a)} (slot A in the first order)",
            "",
            response_a,
            "",
            f"## Response from {response_title('B', model_name_b)} (slot B in the first order)",
            "",
            response_b,
            "",
            "## Full JSON",
            "",
            "```json",
            json.dumps(result, indent=2, ensure_ascii=False),
            "```",
        ]
    )
    return "\n".join(lines)


def batch_report_markdown(
    batch_results: list[dict],
    rubric_name: str,
    judge_endpoint_name: str,
    judge_model: str,
    prompt_pack_metadata: dict[str, str],
) -> str:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    overall_a, overall_b = batch_overall_scores(batch_results)
    winner = benchmark_winner_name(batch_results)
    first_item = batch_results[0]["item"] if batch_results else {}
    response_a_label, response_b_label = distinct_model_labels(
        first_item.get("model_name_a", ""), first_item.get("model_name_b", "")
    )
    legacy_count = batch_legacy_entry_count(batch_results)
    unstable = batch_position_sensitive_entries(batch_results)
    slot_effect = batch_positional_difference(batch_results)
    disclosure = self_judging_disclosure(judge_model, response_a_label, response_b_label)
    lines = [
        f"# {APP_NAME} Benchmark Report",
        "",
        f"- Generated: {generated_at}",
        f"- Prompt Pack: {prompt_pack_metadata['name']}",
        f"- Category: {prompt_pack_metadata['category']}",
        f"- Rubric: {rubric_name}",
        f"- Judge Profile: {prompt_pack_metadata['judge_profile']}",
        f"- Judge endpoint: {judge_endpoint_name}",
        f"- Judge model: {judge_model}",
        f"- Comparisons: {len(batch_results)}",
        f"- Judge calls: {len(batch_results) - legacy_count} comparisons x 2 response orders"
        + (f" plus {legacy_count} legacy single-pass records" if legacy_count else ""),
        f"- Canonical winner: {winner}",
        f"- {response_a_label} canonical overall: {overall_a:.2f} / 10",
        f"- {response_b_label} canonical overall: {overall_b:.2f} / 10",
    ]
    if legacy_count:
        lines.append(
            f"- NOTE: {legacy_count} of {len(batch_results)} comparisons are legacy single-pass records "
            "judged in one response order only."
        )
    if unstable:
        lines.extend(
            [
                f"- WARNING: {len(unstable)} of {len(batch_results)} comparisons were position-sensitive "
                f"(prompts {', '.join(str(index) for index in unstable)}). Their winner changed when the "
                "response order was reversed.",
            ]
        )
    else:
        lines.append("- Result stability: every comparison kept the same winner in both response orders.")
    if slot_effect is not None:
        lines.append(
            f"- Mean slot effect (Response A minus Response B, across all directional passes): {slot_effect:+.2f}"
        )
    if disclosure.get("judge_is_candidate"):
        lines.append(f"- CONFLICT OF INTEREST: {disclosure.get('warning', '')}")
    classification_counts = benchmark_classification_counts(batch_results)
    lines.append(
        "- Classification breakdown: "
        + ", ".join(f"{CLASSIFICATION_LABELS[key]} {count}" for key, count in classification_counts.items())
    )
    lines.extend(
        [
            "",
            "## Averaged Category Summary",
            "",
            "Averages are identity-mapped: each model's score is the mean of its A-slot and B-slot "
            f"marks. {NOT_APPLICABLE_LABEL} criteria are excluded from the overall scores, never scored as a number.",
            "",
            f"| Category | {response_a_label} Avg | {response_b_label} Avg | Difference |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for row in batch_average_rows(batch_results):
        lines.append(
            f"| {row['Category']} | {row['Response A Avg']} | {row['Response B Avg']} | {row['Difference']} |"
        )

    lines.extend(
        [
            "",
            "## Prompt Summary",
            "",
            f"| # | Title | Canonical winner | Classification | Stability | Confidence | {response_a_label} | {response_b_label} | Difference |",
            "| ---: | --- | --- | --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for index, entry in enumerate(batch_results, start=1):
        item = entry["item"]
        result = entry["result"]
        winner = winner_display(result, item["model_name_a"], item["model_name_b"])
        response_a = optional_score(result.get("responses", {}).get("A", {}).get("overall")) or 0.0
        response_b = optional_score(result.get("responses", {}).get("B", {}).get("overall")) or 0.0
        confidence = int(result.get("winner", {}).get("confidence", 0))
        lines.append(
            f"| {index} | {item.get('title', '')} | {winner} | {classification_label(result)} "
            f"| {stability_label(result)} | {confidence}% "
            f"| {response_a:.2f} | {response_b:.2f} | {format_difference(response_b - response_a)} |"
        )

    for index, entry in enumerate(batch_results, start=1):
        item = entry["item"]
        result = entry["result"]
        lines.extend(
            [
                "",
                f"## Prompt {index}",
                "",
                f"- Title: {item.get('title', '')}",
                "",
                report_markdown(
                    result,
                    item["conversation_context"],
                    item["current_prompt"],
                    item["response_a"],
                    item["response_b"],
                    item["model_name_a"],
                    item["model_name_b"],
                    rubric_name,
                    judge_endpoint_name,
                    judge_model,
                    prompt_pack_metadata,
                ),
            ]
        )
    return "\n".join(lines)


def batch_json_payload(
    batch_results: list[dict],
    rubric_name: str,
    judge_endpoint_name: str,
    judge_model: str,
    prompt_pack_metadata: dict[str, str],
) -> dict:
    first_item = batch_results[0]["item"] if batch_results else {}
    model_name_a = response_title("A", first_item.get("model_name_a", ""))
    model_name_b = response_title("B", first_item.get("model_name_b", ""))
    unstable = batch_position_sensitive_entries(batch_results)
    payload = {
        "rubric": rubric_name,
        "prompt_pack": prompt_pack_metadata,
        "judge_profile": prompt_pack_metadata["judge_profile"],
        "judge_endpoint": judge_endpoint_name,
        "judge_model": judge_model,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        # Run-level protocol record. Older files have no such block; readers
        # must treat its absence as a legacy single-pass run, never as proof of
        # a bidirectional one.
        "evaluation_protocol": {
            "mode": PROTOCOL_MODE_BIDIRECTIONAL,
            "version": PROTOCOL_VERSION,
            "passes_per_comparison": 2,
            "orders": ["Response A = Model A", "Response A = Model B"],
            "models": {"A": model_name_a, "B": model_name_b},
            "position_sensitive_comparisons": unstable,
            "position_sensitive_count": len(unstable),
            "legacy_single_pass_comparisons": batch_legacy_entry_count(batch_results),
            "mean_slot_effect": batch_positional_difference(batch_results),
            "classification_counts": benchmark_classification_counts(batch_results),
        },
        "self_judging": self_judging_disclosure(judge_model, model_name_a, model_name_b),
        "canonical_winner": benchmark_winner_name(batch_results),
        "averages": batch_average_rows(batch_results),
        "comparisons": batch_results,
    }
    log_judge_pipeline_debug(
        "EXPORTED BENCHMARK JSON PAYLOAD",
        {
            "judge_model": judge_model,
            "judge_endpoint": judge_endpoint_name,
            "object": payload,
            "object_json": json.dumps(payload, ensure_ascii=False, sort_keys=True),
        },
    )
    return payload


def safe_score(value: object) -> float | None:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if score < 0:
        return None
    return min(score, 10.0)


def benchmark_category_from_payload(payload: dict, source_name: str = "") -> str:
    """Read the canonical category from JSON, with a narrow legacy fallback."""
    prompt_pack = payload.get("prompt_pack", {})
    if isinstance(prompt_pack, dict):
        category = str(prompt_pack.get("category") or "").strip()
        if category:
            return category
    category = str(payload.get("category") or "").strip()
    if category:
        return category
    rubric = str(payload.get("rubric") or "").strip().lower()
    rubric_categories = {
        "companion": "Companion",
        "creativity": "Creativity",
        "philosophy": "Philosophy",
        "morals": "Morals",
        "humour": "Humour",
        "uncensored": "Uncensored",
    }
    if rubric:
        for stem, name in rubric_categories.items():
            if rubric.removesuffix(".md") == stem:
                return name
    # Filename parsing is only a compatibility fallback. New exports always
    # carry the category in prompt_pack metadata.
    filename = Path(source_name).stem.lower()
    for stem, name in rubric_categories.items():
        if f"-{stem}-" in f"-{filename}-":
            return name
    return "Uncategorised"


def benchmark_score_entries(payload: dict, source_name: str = "") -> list[dict[str, object]]:
    entries = []
    comparisons = payload.get("comparisons", [])
    if not isinstance(comparisons, list):
        return entries
    prompt_pack = payload.get("prompt_pack", {})
    prompt_pack_name = str(prompt_pack.get("name") or "") if isinstance(prompt_pack, dict) else ""
    prompt_pack_category = benchmark_category_from_payload(payload, source_name)
    generated_at = str(payload.get("generated_at") or "")
    judge_model = str(payload.get("judge_model") or "")
    for comparison in comparisons:
        if not isinstance(comparison, dict):
            continue
        item = comparison.get("item", {})
        result = comparison.get("result", {})
        if not isinstance(item, dict) or not isinstance(result, dict):
            continue
        responses = result.get("responses", {})
        if not isinstance(responses, dict):
            continue
        for side in ("A", "B"):
            response = responses.get(side, {})
            if not isinstance(response, dict):
                continue
            raw_name = item.get(f"model_name_{side.lower()}", "")
            model_name = model_name_or_default(side, str(raw_name))
            scores = response.get("scores", {})
            benchmark_category = str(item.get("category") or prompt_pack_category or "Uncategorised")
            if isinstance(scores, dict):
                for category, raw_score in scores.items():
                    score = safe_score(raw_score)
                    if score is not None:
                        entries.append(
                            {
                                "source": source_name,
                                "generated_at": generated_at,
                                "prompt_pack": prompt_pack_name,
                                "raw_model": str(raw_name or model_name),
                                "judge_model": judge_model,
                                "model": model_name,
                                "category": str(category),
                                "benchmark_category": benchmark_category,
                                "score": score,
                            }
                        )
            overall = safe_score(response.get("overall"))
            if overall is not None:
                entries.append(
                    {
                        "source": source_name,
                        "generated_at": generated_at,
                        "prompt_pack": prompt_pack_name,
                        "raw_model": str(raw_name or model_name),
                        "judge_model": judge_model,
                        "model": model_name,
                        "category": "Overall",
                        "benchmark_category": benchmark_category,
                        "score": overall,
                    }
                )
    return entries


def safe_confidence(value: object) -> int:
    try:
        confidence = int(float(value))
    except (TypeError, ValueError):
        return 0
    return max(0, min(confidence, 100))


def clean_html_fragment(markup: str) -> str:
    return re.sub(r"\n\s+<", "\n<", markup).strip()


def benchmark_prompt_rows(payload: dict, source_name: str = "") -> list[dict[str, object]]:
    rows = []
    comparisons = payload.get("comparisons", [])
    if not isinstance(comparisons, list):
        return rows
    prompt_pack = payload.get("prompt_pack", {})
    prompt_pack_name = str(prompt_pack.get("name") or "") if isinstance(prompt_pack, dict) else ""
    prompt_pack_category = benchmark_category_from_payload(payload, source_name)
    generated_at = str(payload.get("generated_at") or "")
    for index, comparison in enumerate(comparisons, start=1):
        if not isinstance(comparison, dict):
            continue
        item = comparison.get("item", {})
        result = comparison.get("result", {})
        if not isinstance(item, dict) or not isinstance(result, dict):
            continue
        responses = result.get("responses", {})
        if not isinstance(responses, dict):
            continue
        response_a = responses.get("A", {})
        response_b = responses.get("B", {})
        if not isinstance(response_a, dict) or not isinstance(response_b, dict):
            continue
        score_a = safe_score(response_a.get("overall"))
        score_b = safe_score(response_b.get("overall"))
        if score_a is None or score_b is None:
            continue
        model_a = model_name_or_default("A", str(item.get("model_name_a", "")))
        model_b = model_name_or_default("B", str(item.get("model_name_b", "")))
        rows.append(
            {
                "source": source_name,
                "generated_at": generated_at,
                "prompt_pack": prompt_pack_name,
                "benchmark_category": str(item.get("category") or prompt_pack_category or "Uncategorised"),
                "prompt": item.get("title") or f"Prompt {index}",
                "winner": winner_title(result, model_a, model_b),
                "confidence": safe_confidence(result.get("winner", {}).get("confidence", 0)),
                "model_a": model_a,
                "model_b": model_b,
                "score_a": score_a,
                "score_b": score_b,
                "difference": score_b - score_a,
            }
        )
    return rows


def benchmark_trend_row(payload: dict, source_name: str = "") -> dict[str, object] | None:
    prompt_rows = benchmark_prompt_rows(payload, source_name)
    if not prompt_rows:
        return None
    score_a = sum(float(row["score_a"]) for row in prompt_rows) / len(prompt_rows)
    score_b = sum(float(row["score_b"]) for row in prompt_rows) / len(prompt_rows)
    first_row = prompt_rows[0]
    return {
        "source": source_name,
        "generated_at": first_row.get("generated_at", ""),
        "prompt_pack": first_row.get("prompt_pack", ""),
        "benchmark_category": first_row.get("benchmark_category", "Uncategorised"),
        "model_a": first_row["model_a"],
        "model_b": first_row["model_b"],
        "score_a": score_a,
        "score_b": score_b,
        "difference": score_b - score_a,
        "winner": first_row["model_a"] if score_a > score_b else first_row["model_b"] if score_b > score_a else "Tie",
        "prompts": len(prompt_rows),
    }


def models_from_score_entries(entries: list[dict[str, object]]) -> list[dict[str, object]]:
    totals: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(lambda: {"total": 0.0, "count": 0}))
    for entry in entries:
        model_bucket = totals[str(entry["model"])][str(entry["category"])]
        model_bucket["total"] += float(entry["score"])
        model_bucket["count"] += 1

    models = []
    for model_name, categories in totals.items():
        category_rows = []
        for category, stats in categories.items():
            if stats["count"]:
                category_rows.append(
                    {
                        "category": category,
                        "score": stats["total"] / stats["count"],
                        "count": int(stats["count"]),
                    }
                )
        category_rows.sort(key=lambda row: (row["category"] == "Overall", row["category"]))
        overall_rows = [row for row in category_rows if row["category"] == "Overall"]
        scored_rows = [row for row in category_rows if row["category"] != "Overall"]
        strongest = max(scored_rows, key=lambda row: row["score"]) if scored_rows else None
        weakest = min(scored_rows, key=lambda row: row["score"]) if scored_rows else None
        overall = overall_rows[0]["score"] if overall_rows else (
            sum(row["score"] for row in scored_rows) / len(scored_rows) if scored_rows else 0.0
        )
        comparisons = int(overall_rows[0]["count"]) if overall_rows else 0
        models.append(
            {
                "model": model_name,
                "overall": overall,
                "comparisons": comparisons,
                "categories": category_rows,
                "strongest": strongest,
                "weakest": weakest,
            }
        )
    models.sort(key=lambda row: (-float(row["overall"]), str(row["model"]).lower()))
    return models


def benchmark_strength_data(benchmark_dir: Path = BENCHMARK_DIR) -> dict[str, object]:
    files_read = 0
    files_skipped = 0
    prompt_rows = []
    trend_rows = []
    score_entries = []
    seen_payloads: set[str] = set()
    for path in sorted(benchmark_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            LOGGER.warning("Benchmark file was not loaded: source=%s reason=invalid JSON", path.name)
            files_skipped += 1
            continue
        payload_fingerprint = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        if payload_fingerprint in seen_payloads:
            LOGGER.warning("Duplicate benchmark file skipped: source=%s reason=identical JSON payload", path.name)
            files_skipped += 1
            continue
        seen_payloads.add(payload_fingerprint)
        entries = benchmark_score_entries(payload, path.name)
        if not entries:
            LOGGER.warning(
                "Benchmark file produced no score entries: source=%s category=%s reason=missing compatible scores",
                path.name,
                benchmark_category_from_payload(payload, path.name),
            )
            files_skipped += 1
            continue
        files_read += 1
        score_entries.extend(entries)
        prompt_rows.extend(benchmark_prompt_rows(payload, path.name))
        trend_row = benchmark_trend_row(payload, path.name)
        if trend_row:
            trend_rows.append(trend_row)
    return {
        "models": models_from_score_entries(score_entries),
        "score_entries": score_entries,
        "prompt_rows": prompt_rows,
        "trend_rows": trend_rows,
        "files_read": files_read,
        "files_skipped": files_skipped,
    }


def leaderboard_timestamp_key(value: object) -> tuple[int, float]:
    text = str(value or "").strip()
    if not text:
        return (0, 0.0)
    try:
        return (1, datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError, OSError, OverflowError):
        return (0, 0.0)


def leaderboard_category_metrics(entries: list[dict[str, object]]) -> dict[str, object]:
    run_totals: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: {"total": 0.0, "count": 0})
    for entry in entries:
        score = safe_score(entry.get("score"))
        if score is None:
            continue
        run_key = (str(entry.get("source") or ""), str(entry.get("generated_at") or ""))
        run_totals[run_key]["total"] += score
        run_totals[run_key]["count"] += 1

    runs = []
    for (source, generated_at), stats in run_totals.items():
        if stats["count"]:
            runs.append(
                {
                    "score": stats["total"] / stats["count"],
                    "recency": (*leaderboard_timestamp_key(generated_at), source),
                    "generated_at": generated_at,
                    "source": source,
                }
            )
    if not runs:
        return {}
    current = max(runs, key=lambda run: run["recency"])
    best = max(runs, key=lambda run: (run["score"], run["recency"]))
    valid_scores = [safe_score(entry.get("score")) for entry in entries]
    historical_scores = [score for score in valid_scores if score is not None]
    return {
        "current": float(current["score"]),
        "best": float(best["score"]),
        "historical_average": sum(historical_scores) / len(historical_scores),
        "count": len(historical_scores),
        "current_generated_at": current["generated_at"],
        "current_source": current["source"],
        "best_generated_at": best["generated_at"],
        "best_source": best["source"],
    }


def leaderboard_total(category_scores: dict[str, dict[str, object]], metric: str, total_categories: int) -> float:
    if not total_categories:
        return 0.0
    return sum(float(scores[metric]) for scores in category_scores.values()) / total_categories


def overall_leaderboard_rows(score_entries: list[dict[str, object]]) -> dict[str, object]:
    # The category set is derived from every saved result, not just a single
    # model's, so a model missing a category is penalised (scored as 0 for
    # that category) rather than simply having fewer categories to average.
    benchmark_categories = sorted(
        {str(entry.get("benchmark_category") or "Uncategorised") for entry in score_entries},
        key=str.lower,
    )
    overall_entries: dict[str, dict[str, list[dict[str, object]]]] = defaultdict(lambda: defaultdict(list))
    fallback_entries: dict[str, dict[str, list[dict[str, object]]]] = defaultdict(lambda: defaultdict(list))
    all_models: set[str] = set()
    for entry in score_entries:
        model = str(entry["model"])
        all_models.add(model)
        benchmark_category = str(entry.get("benchmark_category") or "Uncategorised")
        # Prefer the judge's per-response "Overall" score; fall back to the
        # mean of sub-category scores for older results that lack one.
        bucket = overall_entries if str(entry["category"]) == "Overall" else fallback_entries
        bucket[model][benchmark_category].append(entry)

    total_categories = len(benchmark_categories)
    rows = []
    for model in all_models:
        category_scores: dict[str, dict[str, object]] = {}
        tested = []
        missing = []
        for category in benchmark_categories:
            entries = overall_entries[model].get(category) or fallback_entries[model].get(category) or []
            metrics = leaderboard_category_metrics(entries)
            if metrics:
                category_scores[category] = {"score": metrics["current"], **metrics}
                tested.append(category)
            else:
                missing.append(category)
        current = leaderboard_total(category_scores, "current", total_categories)
        best = leaderboard_total(category_scores, "best", total_categories)
        historical_average = leaderboard_total(category_scores, "historical_average", total_categories)
        rows.append(
            {
                "model": model,
                "overall": current,
                "current": current,
                "best": best,
                "historical_average": historical_average,
                "category_scores": category_scores,
                "tested_categories": tested,
                "missing_categories": missing,
                "categories_total": total_categories,
            }
        )
    rows.sort(key=lambda row: (-float(row["current"]), str(row["model"]).lower()))
    return {"rows": rows, "benchmark_categories": benchmark_categories}


LEADERBOARD_VIEW_METRICS = {
    "Current": "current",
    "Best": "best",
    "Historical": "historical_average",
}

LEADERBOARD_VIEW_DESCRIPTIONS = {
    "Current": "Showing each model's latest saved result in every benchmark category.",
    "Best": (
        "Showing each model's highest saved result in every benchmark category; "
        "the newest result breaks ties."
    ),
    "Historical": (
        "Showing each model's average across every saved result in each benchmark category."
    ),
}


def leaderboard_metric_key(view_name: object) -> str:
    return LEADERBOARD_VIEW_METRICS.get(str(view_name or ""), "current")


def leaderboard_view_description(view_name: object) -> str:
    return LEADERBOARD_VIEW_DESCRIPTIONS.get(
        str(view_name or ""),
        LEADERBOARD_VIEW_DESCRIPTIONS["Current"],
    )


def set_leaderboard_view(view_name: str) -> None:
    if view_name in LEADERBOARD_VIEW_METRICS:
        st.session_state.leaderboard_view_select = view_name


def leaderboard_rows_for_metric(rows: list[dict[str, object]], metric_key: str) -> list[dict[str, object]]:
    return sorted(rows, key=lambda row: (-float(row.get(metric_key) or 0.0), str(row["model"]).lower()))


def leaderboard_score_html(row: dict[str, object], metric_label: str, metric_key: str) -> str:
    return (
        '<div class="leaderboard-overall">'
        f'<span>{html.escape(metric_label)}</span>'
        f'<strong>{float(row.get(metric_key) or 0.0):.2f}</strong>'
        "</div>"
    )


def leaderboard_category_chips_html(
    row: dict[str, object], benchmark_categories: list[str], metric_key: str
) -> str:
    category_scores = row.get("category_scores", {})
    chips = []
    for category in benchmark_categories:
        stats = category_scores.get(category) if isinstance(category_scores, dict) else None
        if isinstance(stats, dict):
            chips.append(
                f'<span class="leaderboard-chip tested">{html.escape(category)} '
                f'<b>{float(stats.get(metric_key) or 0.0):.2f}</b></span>'
            )
        else:
            chips.append(
                f'<span class="leaderboard-chip untested">{html.escape(category)} '
                "<b>untested</b></span>"
            )
    return "".join(chips)


def load_dashboard_model_aliases(path: Path | None = None) -> dict[str, list[str]]:
    path = path or DASHBOARD_MODEL_ALIASES_PATH
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    aliases: dict[str, list[str]] = {}
    for alias_name, source_models in raw.items():
        alias = str(alias_name).strip()
        if not alias or not isinstance(source_models, list):
            continue
        cleaned_sources = []
        for source_model in source_models:
            source = str(source_model).strip()
            if source and source != alias and source not in cleaned_sources:
                cleaned_sources.append(source)
        if cleaned_sources:
            aliases[alias] = cleaned_sources
    return aliases


def save_dashboard_model_aliases(aliases: dict[str, list[str]], path: Path | None = None) -> None:
    path = path or DASHBOARD_MODEL_ALIASES_PATH
    cleaned = {}
    for alias_name, source_models in aliases.items():
        alias = str(alias_name).strip()
        sources = sorted({str(source).strip() for source in source_models if str(source).strip() and str(source).strip() != alias}, key=str.lower)
        if alias and sources:
            cleaned[alias] = sources
    path.write_text(json.dumps(cleaned, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def dashboard_model_identity_keys(model_name: str) -> set[str]:
    name = str(model_name or "").strip()
    if not name:
        return set()
    return {name, name.lower(), export_filename_part(name, "model")}


def dashboard_model_identity_matches(left: str, right: str) -> bool:
    return bool(dashboard_model_identity_keys(left) & dashboard_model_identity_keys(right))


def dashboard_model_alias_lookup(aliases: dict[str, list[str]]) -> dict[str, str]:
    lookup = {}
    for alias_name, source_models in aliases.items():
        for identity_key in dashboard_model_identity_keys(alias_name):
            lookup.setdefault(identity_key, alias_name)
    for alias_name, source_models in aliases.items():
        for source_model in source_models:
            for identity_key in dashboard_model_identity_keys(source_model):
                lookup.setdefault(identity_key, alias_name)
    return lookup


def dashboard_display_model_name(model_name: str, aliases: dict[str, list[str]]) -> str:
    lookup = dashboard_model_alias_lookup(aliases)
    for identity_key in dashboard_model_identity_keys(model_name):
        if identity_key in lookup:
            return lookup[identity_key]
    return model_name


def dashboard_model_canonical_id(display_name: str) -> str:
    """Stable ID derived from the configured canonical registry key."""
    return export_filename_part(display_name, "model").lower()


def dashboard_model_is_runtime_variant(model_name: str) -> bool:
    lowered = str(model_name or "").strip().lower()
    return bool(
        re.search(r"(?:^|[-_ ])x\d+(?:$|[-_ ])", lowered)
        or lowered.startswith("helcyon-")
        or lowered.endswith((".gguf", ".safetensors"))
    )


def apply_dashboard_model_aliases_to_score_entries(
    entries: list[dict[str, object]],
    aliases: dict[str, list[str]],
) -> list[dict[str, object]]:
    aliased_entries = []
    for entry in entries:
        row = dict(entry)
        model_name = str(row.get("model") or "")
        display_name = dashboard_display_model_name(model_name, aliases)
        row["original_model"] = str(row.get("raw_model") or model_name)
        row["canonical_model_id"] = dashboard_model_canonical_id(display_name)
        row["display_model_name"] = display_name
        row["model"] = display_name
        if display_name == model_name and dashboard_model_is_runtime_variant(model_name):
            LOGGER.warning(
                "Benchmark model identity was not aggregated: source=%s raw_model=%s "
                "attempted_canonical_id=%s category=%s reason=no configured alias",
                row.get("source", ""),
                row["original_model"],
                row["canonical_model_id"],
                row.get("benchmark_category", "Uncategorised"),
            )
        aliased_entries.append(row)
    return aliased_entries


def apply_dashboard_model_aliases_to_prompt_rows(
    rows: list[dict[str, object]],
    aliases: dict[str, list[str]],
) -> list[dict[str, object]]:
    aliased_rows = []
    for row in rows:
        display_row = dict(row)
        display_row["model_a"] = dashboard_display_model_name(str(display_row.get("model_a") or ""), aliases)
        display_row["model_b"] = dashboard_display_model_name(str(display_row.get("model_b") or ""), aliases)
        winner = str(display_row.get("winner") or "")
        if winner != "Tie":
            display_row["winner"] = dashboard_display_model_name(winner, aliases)
        aliased_rows.append(display_row)
    return aliased_rows


def consolidate_dashboard_models(alias_name: str, selected_models: list[str]) -> None:
    alias = alias_name.strip()
    selected = [model.strip() for model in selected_models if model.strip()]
    if not alias:
        st.session_state.dashboard_consolidate_message = "Enter a consolidated model name first."
        st.session_state.dashboard_consolidate_message_kind = "error"
        return
    if len(selected) < 2:
        st.session_state.dashboard_consolidate_message = "Select at least two models to consolidate."
        st.session_state.dashboard_consolidate_message_kind = "error"
        return

    aliases = load_dashboard_model_aliases()
    expanded_sources = []
    alias_identity_keys = dashboard_model_identity_keys(alias)
    for model_name in selected:
        if model_name in aliases:
            expanded_sources.extend(aliases.get(model_name, []))
        else:
            expanded_sources.append(model_name)
    for existing_alias, existing_sources in aliases.items():
        if dashboard_model_identity_matches(existing_alias, alias):
            expanded_sources.extend(existing_sources)
            continue
        for source in existing_sources:
            if dashboard_model_identity_keys(source) & alias_identity_keys:
                expanded_sources.append(source)

    source_set = {source for source in expanded_sources if source and source != alias}
    if not source_set:
        st.session_state.dashboard_consolidate_message = "Choose source models that are different from the consolidated name."
        st.session_state.dashboard_consolidate_message_kind = "error"
        return

    selected_set = set(selected) | source_set
    claimed_identity_keys = set(alias_identity_keys)
    for model_name in selected_set:
        claimed_identity_keys.update(dashboard_model_identity_keys(model_name))
    updated_aliases = {}
    for existing_alias, existing_sources in aliases.items():
        if (
            existing_alias == alias
            or existing_alias in selected_set
            or dashboard_model_identity_keys(existing_alias) & claimed_identity_keys
        ):
            continue
        remaining_sources = [
            source
            for source in existing_sources
            if source not in selected_set and not (dashboard_model_identity_keys(source) & claimed_identity_keys)
        ]
        if remaining_sources:
            updated_aliases[existing_alias] = remaining_sources
    updated_aliases[alias] = sorted(source_set, key=str.lower)
    try:
        save_dashboard_model_aliases(updated_aliases)
    except OSError as exc:
        st.session_state.dashboard_consolidate_message = f"Could not save consolidation: {exc}"
        st.session_state.dashboard_consolidate_message_kind = "error"
        return

    for model_name in selected:
        st.session_state.pop(f"dashboard_consolidate_select_{dashboard_model_key_part(model_name)}", None)
    st.session_state.dashboard_consolidated_model_name = ""
    st.session_state.pop("dashboard_trend_model", None)
    st.session_state.dashboard_consolidate_message = f"Consolidated {len(source_set)} model name(s) into {alias}."
    st.session_state.dashboard_consolidate_message_kind = "success"


def consolidate_selected_dashboard_models(available_models: list[str]) -> None:
    selected_models = [
        model_name
        for model_name in available_models
        if st.session_state.get(f"dashboard_consolidate_select_{dashboard_model_key_part(model_name)}")
    ]
    consolidate_dashboard_models(
        str(st.session_state.get("dashboard_consolidated_model_name", "")),
        selected_models,
    )


def clear_dashboard_consolidation_selection() -> None:
    for key in list(st.session_state.keys()):
        if str(key).startswith("dashboard_consolidate_select_"):
            st.session_state.pop(key, None)


def remove_dashboard_model_alias(alias_name: str) -> None:
    aliases = load_dashboard_model_aliases()
    if alias_name in aliases:
        aliases.pop(alias_name)
        try:
            save_dashboard_model_aliases(aliases)
        except OSError as exc:
            st.session_state.dashboard_consolidate_message = f"Could not remove consolidation: {exc}"
            st.session_state.dashboard_consolidate_message_kind = "error"
            return
        st.session_state.pop("dashboard_trend_model", None)
        st.session_state.dashboard_consolidate_message = f"Removed consolidation for {alias_name}."
        st.session_state.dashboard_consolidate_message_kind = "success"


def confirm_dashboard_model_rename(model_name: str, model_id: str) -> None:
    model_name = str(model_name or "").strip()
    new_name = str(st.session_state.get(f"dashboard_rename_input_{model_id}") or "").strip()
    if not model_name or not new_name:
        st.session_state.dashboard_consolidate_message = "Enter a model name first."
        st.session_state.dashboard_consolidate_message_kind = "error"
        return
    if new_name == model_name:
        return

    aliases = load_dashboard_model_aliases()
    if model_name in aliases:
        source_set = set(aliases.pop(model_name))
        source_set.add(model_name)
    else:
        source_set = {model_name}
    if new_name in aliases:
        source_set.update(aliases.pop(new_name))

    updated_aliases = {}
    for existing_alias, existing_sources in aliases.items():
        remaining_sources = [source for source in existing_sources if source not in source_set and source != new_name]
        if remaining_sources:
            updated_aliases[existing_alias] = remaining_sources
    updated_aliases[new_name] = sorted(
        {source for source in source_set if source and source != new_name},
        key=str.lower,
    )
    if not updated_aliases[new_name]:
        st.session_state.dashboard_consolidate_message = "Choose a different model name."
        st.session_state.dashboard_consolidate_message_kind = "error"
        return
    try:
        save_dashboard_model_aliases(updated_aliases)
    except OSError as exc:
        st.session_state.dashboard_consolidate_message = f"Could not rename model: {exc}"
        st.session_state.dashboard_consolidate_message_kind = "error"
        return

    st.session_state.pop("dashboard_trend_model", None)
    st.session_state.pop("dashboard_info_model", None)
    st.session_state.dashboard_consolidate_message = f"Renamed {model_name} to {new_name}."
    st.session_state.dashboard_consolidate_message_kind = "success"


def open_dashboard_rename_dialog(model_name: str, model_id: str) -> None:
    # Opened directly from the pencil button's click rather than from persisted
    # session state, so the dialog appears exactly once. The old approach left a
    # "pending rename" flag set when the dialog was dismissed via its X / an
    # outside click, so any later rerun (a dropdown or checkbox) re-triggered it.
    # Streamlit keeps a dialog open across its own reruns and closes it on dismiss
    # without reopening, so no persisted state is needed.
    #
    # st.dialog also runs as a fragment: a button's on_click would only rerun the
    # dialog, so the save would happen but the dashboard would never refresh and
    # the dialog would never close. Handle clicks inline and call st.rerun() for a
    # full-app rerun that closes the dialog and re-renders.
    st.session_state[f"dashboard_rename_input_{model_id}"] = model_name

    def dialog_body() -> None:
        st.text_input(
            "Model name",
            key=f"dashboard_rename_input_{model_id}",
        )
        action_cols = st.columns(2)
        with action_cols[0]:
            if st.button(
                "Apply",
                key=f"confirm_rename_model_{model_id}",
                use_container_width=True,
            ):
                confirm_dashboard_model_rename(model_name, model_id)
                st.rerun()
        with action_cols[1]:
            if st.button(
                "Cancel",
                key=f"cancel_rename_model_{model_id}",
                use_container_width=True,
            ):
                st.rerun()

    if hasattr(st, "dialog"):
        @non_dismissible_dialog(f"Rename {model_name}")
        def rename_dialog() -> None:
            dialog_body()

        rename_dialog()
    else:
        with st.expander(f"Rename {model_name}", expanded=True):
            dialog_body()


def benchmark_run_label(path: Path) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return path.stem.replace("_", " ").replace("-", " ")
    prompt_pack = payload.get("prompt_pack", {})
    pack_name = str(prompt_pack.get("name") or "") if isinstance(prompt_pack, dict) else ""
    generated_at = str(payload.get("generated_at") or "").replace("T", " ")
    trend_row = benchmark_trend_row(payload, path.name)
    if trend_row:
        return (
            f"{trend_row['model_a']} vs {trend_row['model_b']} | "
            f"{pack_name or trend_row.get('prompt_pack') or 'Benchmark'} | "
            f"{generated_at or path.stem}"
        )
    return f"{pack_name or 'Benchmark'} | {generated_at or path.stem}"


def available_benchmark_runs(benchmark_dir: Path = BENCHMARK_DIR) -> dict[str, Path]:
    if not benchmark_dir.exists():
        return {}
    runs = {}
    for path in sorted(benchmark_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        label = benchmark_run_label(path)
        if label in runs:
            label = f"{label} ({path.stem})"
        runs[label] = path
    return runs


def selected_benchmark_run_path() -> Path | None:
    runs = available_benchmark_runs()
    selected_label = st.session_state.get("benchmark_delete_run_select")
    if selected_label in runs:
        return runs[selected_label]
    if runs:
        return next(iter(runs.values()))
    return None


def delete_selected_benchmark_run() -> None:
    path = selected_benchmark_run_path()
    if not path:
        st.session_state.dashboard_delete_message = "No saved benchmark run selected."
        st.session_state.dashboard_delete_message_kind = "error"
        return
    deleted = []
    try:
        path.unlink()
        deleted.append(path.name)
        md_path = path.with_suffix(".md")
        if md_path.exists():
            md_path.unlink()
            deleted.append(md_path.name)
    except OSError as exc:
        st.session_state.dashboard_delete_message = f"Could not delete {path.name}: {exc}"
        st.session_state.dashboard_delete_message_kind = "error"
        return
    st.session_state.dashboard_delete_message = f"Deleted {' and '.join(deleted)}."
    st.session_state.dashboard_delete_message_kind = "success"
    st.session_state.pop("dashboard_trend_model", None)


def delete_benchmark_run_files(source_names: list[str], benchmark_dir: Path = BENCHMARK_DIR) -> list[str]:
    deleted = []
    for source_name in sorted(set(source_names)):
        if not source_name:
            continue
        path = (benchmark_dir / source_name).resolve()
        if path.parent != benchmark_dir.resolve() or path.suffix.lower() != ".json":
            continue
        if not path.exists():
            continue
        path.unlink()
        deleted.append(path.name)
        md_path = path.with_suffix(".md")
        if md_path.exists():
            md_path.unlink()
            deleted.append(md_path.name)
    return deleted


def request_delete_dashboard_model_runs(model_name: str, model_id: str, source_names: list[str]) -> None:
    st.session_state.pending_dashboard_delete = {
        "model_name": model_name,
        "model_id": model_id,
        "source_names": sorted(set(source_names)),
    }


def cancel_dashboard_model_delete() -> None:
    st.session_state.pop("pending_dashboard_delete", None)


def confirm_dashboard_model_delete() -> None:
    pending = st.session_state.get("pending_dashboard_delete")
    if not isinstance(pending, dict):
        return
    model_name = str(pending.get("model_name") or "model")
    source_names = [str(name) for name in pending.get("source_names", [])]
    try:
        deleted = delete_benchmark_run_files(source_names)
    except OSError as exc:
        st.session_state.dashboard_delete_message = f"Could not delete runs for {model_name}: {exc}"
        st.session_state.dashboard_delete_message_kind = "error"
        st.session_state.pop("pending_dashboard_delete", None)
        return
    if deleted:
        st.session_state.dashboard_delete_message = f"Deleted {len(deleted)} file(s) for {model_name}."
        st.session_state.dashboard_delete_message_kind = "success"
    else:
        st.session_state.dashboard_delete_message = f"No saved files were deleted for {model_name}."
        st.session_state.dashboard_delete_message_kind = "error"
    st.session_state.pop("pending_dashboard_delete", None)
    st.session_state.pop("dashboard_trend_model", None)


def dashboard_sort_score(model: dict[str, object], score_category: str) -> float:
    if score_category == "All score categories":
        return float(model["overall"])
    for row in model.get("categories", []):
        if row["category"] == score_category:
            return float(row["score"])
    return -1.0


def model_matches_filter(model_name: str, selected_models: list[str]) -> bool:
    return not selected_models or model_name in selected_models


def model_trend_points(
    score_entries: list[dict[str, object]],
    model_name: str,
    benchmark_category: str,
    score_category: str,
) -> list[dict[str, object]]:
    target_score_category = "Overall" if score_category == "All score categories" else score_category
    grouped: dict[tuple[str, str, str, str], dict[str, object]] = {}
    for entry in score_entries:
        if str(entry["model"]) != model_name:
            continue
        if str(entry["category"]) != target_score_category:
            continue
        entry_benchmark_category = str(entry.get("benchmark_category") or "Uncategorised")
        if benchmark_category != "All benchmark categories" and entry_benchmark_category != benchmark_category:
            continue
        key = (
            str(entry.get("source") or ""),
            str(entry.get("generated_at") or ""),
            str(entry.get("prompt_pack") or ""),
            entry_benchmark_category,
        )
        bucket = grouped.setdefault(
            key,
            {
                "source": key[0],
                "generated_at": key[1],
                "prompt_pack": key[2],
                "benchmark_category": key[3],
                "total": 0.0,
                "count": 0,
            },
        )
        bucket["total"] = float(bucket["total"]) + float(entry["score"])
        bucket["count"] = int(bucket["count"]) + 1

    points = []
    for bucket in grouped.values():
        count = int(bucket["count"])
        if not count:
            continue
        points.append(
            {
                "source": bucket["source"],
                "generated_at": bucket["generated_at"],
                "prompt_pack": bucket["prompt_pack"],
                "benchmark_category": bucket["benchmark_category"],
                "score": float(bucket["total"]) / count,
                "count": count,
            }
        )
    points.sort(key=lambda row: (str(row["generated_at"]), str(row["source"])))
    return points


def trend_svg(points: list[dict[str, object]]) -> str:
    width = 760
    height = 260
    pad_x = 48
    pad_y = 30
    plot_w = width - pad_x * 2
    plot_h = height - pad_y * 2
    if not points:
        return ""
    scores = [float(point["score"]) for point in points]
    min_score = max(0.0, min(scores) - 0.25)
    max_score = min(10.0, max(scores) + 0.25)
    if max_score - min_score < 0.5:
        max_score = min(10.0, max_score + 0.25)
        min_score = max(0.0, min_score - 0.25)
    span = max(max_score - min_score, 0.1)
    coords = []
    for index, point in enumerate(points):
        x = pad_x + (plot_w * index / max(len(points) - 1, 1))
        y = pad_y + plot_h - ((float(point["score"]) - min_score) / span * plot_h)
        coords.append((x, y))
    polyline = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    circles = "\n".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5"><title>{html.escape(str(points[index]["source"]))}: {float(points[index]["score"]):.2f}</title></circle>'
        for index, (x, y) in enumerate(coords)
    )
    first_score = scores[0]
    last_score = scores[-1]
    delta = last_score - first_score
    trend_colour = "#55d68f" if delta >= 0 else "#ff8f82"
    return clean_html_fragment(
        f"""
        <svg class="model-trend-svg" viewBox="0 0 {width} {height}" role="img">
            <line x1="{pad_x}" y1="{pad_y}" x2="{pad_x}" y2="{height - pad_y}" />
            <line x1="{pad_x}" y1="{height - pad_y}" x2="{width - pad_x}" y2="{height - pad_y}" />
            <text x="{pad_x}" y="18">{max_score:.2f}</text>
            <text x="{pad_x}" y="{height - 8}">{min_score:.2f}</text>
            <polyline points="{polyline}" style="stroke:{trend_colour};" />
            {circles}
        </svg>
        """
    )


def render_model_trend_content(model_name: str, points: list[dict[str, object]], score_category: str) -> None:
    if not points:
        st.caption("No saved trend points match the current filters.")
        return
    first_score = float(points[0]["score"])
    last_score = float(points[-1]["score"])
    delta = last_score - first_score
    st.markdown(
        f"**{html.escape(model_name)}** | {html.escape(score_category if score_category != 'All score categories' else 'Overall')} | "
        f"{len(points)} saved run(s) | {format_difference(delta)} since first point"
    )
    st.markdown(trend_svg(points), unsafe_allow_html=True)
    st.table(
        [
            {
                "Run": format_run_date(point["generated_at"], include_time=True) or point["source"],
                "Benchmark": point["prompt_pack"] or point["source"],
                "Category": point["benchmark_category"],
                "Score": f"{float(point['score']):.2f}",
                "Samples": int(point["count"]),
            }
            for point in points
        ]
    )
    if st.button("Close trend graph", key=f"close_trend_{export_filename_part(model_name, 'model')}"):
        st.session_state.pop("dashboard_trend_model", None)
        remove_query_param("trend_model")
        st.rerun()


def render_model_trend_modal(model_name: str, points: list[dict[str, object]], score_category: str) -> None:
    if hasattr(st, "dialog"):
        @non_dismissible_dialog(f"{model_name} Trend")
        def trend_dialog() -> None:
            render_model_trend_content(model_name, points, score_category)

        trend_dialog()
    else:
        with st.expander(f"{model_name} Trend", expanded=True):
            render_model_trend_content(model_name, points, score_category)


def open_dashboard_trend_model(model_name: str) -> None:
    st.session_state.pop("dashboard_info_model", None)
    st.session_state.dashboard_trend_model = model_name


def format_judge_name(value: object) -> str:
    text = str(value or "").strip()
    if text.lower().endswith(".gguf"):
        text = text[: -len(".gguf")]
    parts = [part for part in re.split(r"[-_\s]+", text) if part]
    lower_parts = [part.lower() for part in parts]
    if lower_parts[:1] == ["helcyon"]:
        compact_parts = parts[1:3]
        if len(compact_parts) == 2:
            return " ".join(compact_parts)
    return text


def render_model_info_content(model_name: str, score_entries: list[dict[str, object]]) -> None:
    runs: dict[str, dict[str, str]] = {}
    for entry in score_entries:
        if str(entry.get("model") or "") != model_name:
            continue
        source = str(entry.get("source") or "")
        runs.setdefault(
            source,
            {
                "generated_at": str(entry.get("generated_at") or ""),
                "prompt_pack": str(entry.get("prompt_pack") or ""),
                "benchmark_category": str(entry.get("benchmark_category") or "Uncategorised"),
                "judge_model": str(entry.get("judge_model") or ""),
            },
        )
    if not runs:
        st.caption("No saved runs match the current filters.")
        return
    ordered = sorted(runs.items(), key=lambda item: (item[1]["generated_at"], item[0]))
    stamps = [row["generated_at"] for _, row in ordered if row["generated_at"]]
    if len(ordered) == 1:
        st.markdown(f"**{html.escape(model_name)}** | 1 saved run")
    else:
        summary_parts = [f"**{html.escape(model_name)}** | {len(ordered)} saved runs"]
        if stamps:
            summary_parts.append(f"first {format_run_date(stamps[0])}")
            summary_parts.append(f"latest {format_run_date(stamps[-1])}")
        st.markdown(" | ".join(summary_parts))
    st.table(
        [
            {
                "Date": format_run_date(row["generated_at"], include_time=True) or source,
                "Benchmark": row["prompt_pack"] or source,
                "Category": row["benchmark_category"],
                "Judge": format_judge_name(row["judge_model"]) or "Unknown",
            }
            for source, row in ordered
        ]
    )
    if st.button("Close", key=f"close_info_{export_filename_part(model_name, 'model')}"):
        st.session_state.pop("dashboard_info_model", None)
        st.rerun()


def render_model_info_modal(model_name: str, score_entries: list[dict[str, object]]) -> None:
    if hasattr(st, "dialog"):
        @non_dismissible_dialog(f"{model_name} Details")
        def info_dialog() -> None:
            render_model_info_content(model_name, score_entries)

        info_dialog()
    else:
        with st.expander(f"{model_name} Details", expanded=True):
            render_model_info_content(model_name, score_entries)


def open_dashboard_info_model(model_name: str) -> None:
    st.session_state.pop("dashboard_trend_model", None)
    st.session_state.dashboard_info_model = model_name


def render_benchmark_strength_map(benchmark_dir: Path = BENCHMARK_DIR) -> None:
    data = benchmark_strength_data(benchmark_dir)
    st.subheader("Benchmark Strength Map")
    st.markdown('<span class="view-tag-progress">Development / progress view</span>', unsafe_allow_html=True)
    st.caption(
        "Not an overall ranking. Models here are ordered by their currently available "
        "benchmark results only, so they may not be directly comparable until every "
        "benchmark category has been completed. See Overall Leaderboard for the "
        "authoritative cross-category ranking."
    )
    raw_score_entries = data.get("score_entries", [])
    if not raw_score_entries:
        st.caption("Saved JSON benchmark results will appear here after a run.")
        return
    dashboard_aliases = load_dashboard_model_aliases()
    all_score_entries = apply_dashboard_model_aliases_to_score_entries(list(raw_score_entries), dashboard_aliases)
    prompt_display_rows = apply_dashboard_model_aliases_to_prompt_rows(list(data.get("prompt_rows", [])), dashboard_aliases)
    trend_display_rows = apply_dashboard_model_aliases_to_prompt_rows(list(data.get("trend_rows", [])), dashboard_aliases)
    all_models = sorted({str(entry["model"]) for entry in all_score_entries}, key=str.lower)
    benchmark_categories = sorted(
        {str(entry.get("benchmark_category") or "Uncategorised") for entry in all_score_entries},
        key=str.lower,
    )
    total_benchmark_categories = len(benchmark_categories)
    completion_by_model = {row["model"]: row for row in overall_leaderboard_rows(all_score_entries)["rows"]}
    score_categories = sorted(
        {str(entry["category"]) for entry in all_score_entries if str(entry["category"]) != "Overall"},
        key=str.lower,
    )

    filter_cols = st.columns([1, 1, 1, 1, 1])
    with filter_cols[0]:
        selected_benchmark_category = st.selectbox(
            "Benchmark Category",
            ["All benchmark categories"] + benchmark_categories,
            key="dashboard_benchmark_category_filter",
        )
    with filter_cols[1]:
        selected_models = st.multiselect(
            "Models",
            all_models,
            key="dashboard_model_filter",
            placeholder="All models",
        )
    with filter_cols[2]:
        selected_score_category = st.selectbox(
            "Score Category",
            ["All score categories"] + score_categories,
            key="dashboard_score_category_filter",
        )
    with filter_cols[3]:
        sort_order = st.selectbox(
            "Sort",
            ["Highest to lowest", "Lowest to highest", "Model A-Z"],
            key="dashboard_sort_order",
        )
    with filter_cols[4]:
        date_order = st.selectbox(
            "Date",
            ["Ignore dates", "Newest to oldest", "Oldest to newest"],
            key="dashboard_date_order",
            help="Order model cards by their most recent run date; Sort breaks ties",
        )

    consume_status_message("dashboard_delete_message", "dashboard_delete_message_kind")
    consume_status_message("dashboard_consolidate_message", "dashboard_consolidate_message_kind")

    if dashboard_aliases:
        with st.expander("Consolidated models", expanded=False):
            for alias_name, source_models in dashboard_aliases.items():
                alias_cols = st.columns([1, 0.28])
                with alias_cols[0]:
                    st.caption(f"{alias_name}: {', '.join(source_models)}")
                with alias_cols[1]:
                    st.button(
                        "Remove",
                        key=f"remove_dashboard_alias_{export_filename_part(alias_name, 'model')}",
                        help=f"Show individual model cards for {alias_name} again",
                        on_click=remove_dashboard_model_alias,
                        args=(alias_name,),
                    )

    filtered_score_entries = [
        entry
        for entry in all_score_entries
        if (
            selected_benchmark_category == "All benchmark categories"
            or str(entry.get("benchmark_category") or "Uncategorised") == selected_benchmark_category
        )
        and model_matches_filter(str(entry["model"]), selected_models)
        and (
            selected_score_category == "All score categories"
            or str(entry["category"]) in {selected_score_category, "Overall"}
        )
    ]
    models = models_from_score_entries(filtered_score_entries)
    run_stamps_by_model: dict[str, set[str]] = defaultdict(set)
    for entry in filtered_score_entries:
        stamp = str(entry.get("generated_at") or "")
        if stamp:
            run_stamps_by_model[str(entry["model"])].add(stamp)
    if sort_order == "Lowest to highest":
        models.sort(key=lambda model: (dashboard_sort_score(model, selected_score_category), str(model["model"]).lower()))
    elif sort_order == "Model A-Z":
        models.sort(key=lambda model: str(model["model"]).lower())
    else:
        models.sort(key=lambda model: (-dashboard_sort_score(model, selected_score_category), str(model["model"]).lower()))
    if date_order != "Ignore dates":
        # Stable sort: models sharing a run date keep the Sort order above.
        models.sort(
            key=lambda model: max(run_stamps_by_model.get(str(model["model"]), set()), default=""),
            reverse=date_order == "Newest to oldest",
        )

    if not models:
        st.caption("No saved benchmark results match those filters.")
        return

    selected_for_consolidation = [
        model_name
        for model_name in all_models
        if st.session_state.get(f"dashboard_consolidate_select_{dashboard_model_key_part(model_name)}")
    ]
    consolidate_cols = st.columns([1, 0.34, 0.26], vertical_alignment="bottom")
    with consolidate_cols[0]:
        st.text_input(
            "Consolidate selected as",
            key="dashboard_consolidated_model_name",
            placeholder="Final model name",
        )
    with consolidate_cols[1]:
        confirm_label = (
            f"Confirm Consolidate ({len(selected_for_consolidation)})"
            if selected_for_consolidation
            else "Confirm Consolidate"
        )
        st.button(
            confirm_label,
            disabled=len(selected_for_consolidation) < 2,
            use_container_width=True,
            on_click=consolidate_selected_dashboard_models,
            args=(all_models,),
        )
    with consolidate_cols[2]:
        st.button(
            "Clear Selection",
            disabled=not selected_for_consolidation,
            use_container_width=True,
            help="Untick every 'Include in consolidation' checkbox",
            on_click=clear_dashboard_consolidation_selection,
        )
    # Always render this caption so ticking a card checkbox doesn't insert a
    # new line here and shift the whole page under the user's cursor.
    if selected_for_consolidation:
        st.caption(f"Selected for consolidation ({len(selected_for_consolidation)}): {', '.join(selected_for_consolidation)}")
    else:
        st.caption("Tick 'Include in consolidation' on two or more model cards, then set a final name and confirm.")

    st.caption(f"Showing {len(models)} model(s) from {data['files_read']} saved JSON benchmark file(s).")
    for row_start in range(0, len(models), 2):
        row_columns = st.columns(2)
        for column, model_index, model in zip(
            row_columns,
            range(row_start, min(row_start + 2, len(models))),
            models[row_start : row_start + 2],
        ):
            with column:
                category_rows = [row for row in model["categories"] if row["category"] != "Overall"]
                bars = []
                for category_index, row in enumerate(category_rows):
                    score = float(row["score"])
                    width = max(2.0, min(score * 10.0, 100.0))
                    hue = (model_index * 41 + category_index * 57 + 18) % 360
                    tone = "needs-work" if score < 7.5 else "solid"
                    bars.append(
                        f"""
                        <div class="strength-row {tone}">
                            <div class="strength-label">
                                <span>{html.escape(str(row["category"]))}</span>
                                <strong>{score:.2f}</strong>
                            </div>
                            <div class="strength-track">
                                <div class="strength-fill" style="width:{width:.1f}%; background:linear-gradient(90deg, hsl({hue}, 82%, 54%), hsl({(hue + 34) % 360}, 92%, 66%));"></div>
                            </div>
                        </div>
                        """
                    )
                strongest = model.get("strongest") or {"category": "n/a", "score": 0}
                weakest = model.get("weakest") or {"category": "n/a", "score": 0}
                model_name = str(model["model"])
                model_sources = sorted(
                    {
                        str(entry.get("source") or "")
                        for entry in filtered_score_entries
                        if str(entry.get("model") or "") == model_name and str(entry.get("source") or "")
                    }
                )
                model_dom_id = export_filename_part(model_name, "model")
                model_key_part = dashboard_model_key_part(model_name)
                completion = completion_by_model.get(model_name)
                tested_count = len(completion["tested_categories"]) if completion else 0
                completion_total = completion["categories_total"] if completion else total_benchmark_categories
                incomplete_badge = (
                    '<span class="leaderboard-badge-incomplete">Incomplete</span>'
                    if tested_count < completion_total
                    else ""
                )
                with st.container(border=True):
                    header_cols = st.columns([1, 0.28])
                    with header_cols[0]:
                        with st.container(
                            horizontal=True,
                            gap="small",
                            vertical_alignment="center",
                        ):
                            st.markdown(
                                clean_html_fragment(
                                    f"""
                                    <div class="strength-title-row">
                                        <h3>{html.escape(model_name)}</h3>
                                    </div>
                                    """
                                ),
                                unsafe_allow_html=True,
                                width="content",
                            )
                            open_rename = st.button(
                                "✎",
                                key=f"rename_model_btn_{model_key_part}",
                                help=f"Rename {model_name}",
                                type="tertiary",
                                width="content",
                            )
                        st.markdown(
                            clean_html_fragment(
                                f"""
                                <p class="strength-count">{int(model["comparisons"])} scored prompt result(s)</p>
                                <p class="strength-progress">{tested_count}/{completion_total} benchmark categories tested {incomplete_badge}</p>
                                """
                            ),
                            unsafe_allow_html=True,
                        )
                    with header_cols[1]:
                        st.markdown(
                            f'<div class="strength-overall"><strong>{float(model["overall"]):.2f}</strong><span>/ 10</span></div>',
                            unsafe_allow_html=True,
                        )
                    if open_rename:
                        open_dashboard_rename_dialog(model_name, model_key_part)
                    action_cols = st.columns([0.26, 0.14, 1])
                    with action_cols[0]:
                        st.button(
                            "Trend ↗",
                            key=f"open_trend_{model_key_part}",
                            help=f"Score history for {model_name} across saved runs",
                            on_click=open_dashboard_trend_model,
                            args=(model_name,),
                        )
                    with action_cols[1]:
                        st.button(
                            "ⓘ",
                            key=f"open_info_{model_key_part}",
                            help=f"Run details for {model_name}: dates, benchmarks, categories",
                            on_click=open_dashboard_info_model,
                            args=(model_name,),
                        )
                    st.markdown(
                        clean_html_fragment(
                            f"""
                            <div class="strength-chips">
                                <span>Strongest: <b>{html.escape(str(strongest["category"]))} {float(strongest["score"]):.2f}</b></span>
                                <span>Weakest: <b>{html.escape(str(weakest["category"]))} {float(weakest["score"]):.2f}</b></span>
                            </div>
                            {''.join(bars)}
                            """
                        ),
                        unsafe_allow_html=True,
                    )
                    footer_cols = st.columns([1, 0.3])
                    with footer_cols[0]:
                        st.checkbox(
                            "Include in consolidation",
                            key=f"dashboard_consolidate_select_{model_key_part}",
                            help=f"Select {model_name} for consolidation",
                        )
                    with footer_cols[1]:
                        st.button(
                            "Delete",
                            key=f"delete_model_{model_key_part}",
                            use_container_width=True,
                            help=f"Delete saved runs for {model_name}",
                            on_click=request_delete_dashboard_model_runs,
                            args=(model_name, model_key_part, model_sources),
                        )
                    pending_delete = st.session_state.get("pending_dashboard_delete")
                    if isinstance(pending_delete, dict) and pending_delete.get("model_id") == model_key_part:
                        source_names = [str(name) for name in pending_delete.get("source_names", [])]
                        st.warning(f"Delete {len(source_names)} saved benchmark run(s) for {model_name}?")
                        confirm_delete_cols = st.columns([1, 1, 2])
                        with confirm_delete_cols[0]:
                            st.button(
                                "Yes, Delete",
                                key=f"confirm_delete_model_{model_key_part}",
                                use_container_width=True,
                                on_click=confirm_dashboard_model_delete,
                            )
                        with confirm_delete_cols[1]:
                            st.button(
                                "Cancel",
                                key=f"cancel_delete_model_{model_key_part}",
                                use_container_width=True,
                                on_click=cancel_dashboard_model_delete,
                            )
                st.markdown('<div class="strength-card-gap"></div>', unsafe_allow_html=True)
    trend_query_model = consume_query_value("trend_model")
    if trend_query_model:
        st.session_state.dashboard_trend_model = trend_query_model
    trend_model = st.session_state.get("dashboard_trend_model")
    visible_model_names = {str(model["model"]) for model in models}
    if trend_model and trend_model in visible_model_names:
        render_model_trend_modal(
            str(trend_model),
            model_trend_points(
                list(all_score_entries),
                str(trend_model),
                selected_benchmark_category,
                selected_score_category,
            ),
            selected_score_category,
        )
        if hasattr(st, "dialog"):
            # A dialog only opens on the run that calls it, so clear the request
            # now; a stale flag would reopen a dismissed dialog on any rerun,
            # such as toggling a consolidation checkbox.
            st.session_state.pop("dashboard_trend_model", None)
    elif trend_model:
        st.session_state.pop("dashboard_trend_model", None)

    info_model = st.session_state.get("dashboard_info_model")
    trend_dialog_shown = bool(trend_model and trend_model in visible_model_names)
    if info_model and info_model in visible_model_names and not trend_dialog_shown:
        render_model_info_modal(str(info_model), filtered_score_entries)
        if hasattr(st, "dialog"):
            st.session_state.pop("dashboard_info_model", None)
    elif info_model:
        st.session_state.pop("dashboard_info_model", None)

    category_rows = []
    for model in models:
        for row in model["categories"]:
            category_rows.append(
                {
                    "Model": model["model"],
                    "Category": row["category"],
                    "Average": f"{float(row['score']):.2f}",
                    "Samples": int(row["count"]),
                }
            )
    st.markdown("**Average category scores**")
    st.table(category_rows)

    prompt_rows = [
        row
        for row in prompt_display_rows
        if (
            selected_benchmark_category == "All benchmark categories"
            or str(row.get("benchmark_category") or "Uncategorised") == selected_benchmark_category
        )
        and (
            not selected_models
            or row["model_a"] in selected_models
            or row["model_b"] in selected_models
            or row["winner"] in selected_models
        )
    ]
    if prompt_rows:
        with st.expander("Per-prompt winners, confidence, and score differences", expanded=False):
            st.table(
                [
                    {
                        "Benchmark": row["source"],
                        "Date": format_run_date(row.get("generated_at")),
                        "Prompt": row["prompt"],
                        "Winner": row["winner"],
                        "Confidence": f"{int(row['confidence'])}%",
                        row["model_a"]: f"{float(row['score_a']):.2f}",
                        row["model_b"]: f"{float(row['score_b']):.2f}",
                        "B - A": format_difference(float(row["difference"])),
                    }
                    for row in prompt_rows
                ]
            )

    trend_rows = [
        row
        for row in trend_display_rows
        if (
            selected_benchmark_category == "All benchmark categories"
            or str(row.get("benchmark_category") or "Uncategorised") == selected_benchmark_category
        )
        and (
            not selected_models
            or row["model_a"] in selected_models
            or row["model_b"] in selected_models
            or row["winner"] in selected_models
        )
    ]
    if trend_rows:
        trend_blocks = []
        for index, row in enumerate(trend_rows):
            score_a = float(row["score_a"])
            score_b = float(row["score_b"])
            total = max(score_a + score_b, 0.01)
            width_a = max(4.0, score_a / total * 100.0)
            width_b = max(4.0, score_b / total * 100.0)
            hue_a = (index * 37 + 202) % 360
            hue_b = (index * 53 + 24) % 360
            trend_blocks.append(
                f"""
                <div class="trend-row">
                    <div class="trend-meta">
                        <strong>{html.escape(str(row["model_a"]))} vs {html.escape(str(row["model_b"]))}</strong>
                        <span>{html.escape(" | ".join(part for part in [str(row["prompt_pack"] or row["source"]), format_run_date(row.get("generated_at"))] if part))} | winner: {html.escape(str(row["winner"]))} | {format_difference(float(row["difference"]))}</span>
                    </div>
                    <div class="trend-bars">
                        <div class="trend-a" style="width:{width_a:.1f}%; background:hsl({hue_a}, 76%, 58%);">{score_a:.2f}</div>
                        <div class="trend-b" style="width:{width_b:.1f}%; background:hsl({hue_b}, 86%, 60%);">{score_b:.2f}</div>
                    </div>
                </div>
                """
            )
        st.markdown("**Model A vs B overall trend**")
        st.markdown(clean_html_fragment(f'<div class="trend-map">{"".join(trend_blocks)}</div>'), unsafe_allow_html=True)


def render_overall_leaderboard(benchmark_dir: Path = BENCHMARK_DIR) -> None:
    data = benchmark_strength_data(benchmark_dir)
    st.subheader("Overall Leaderboard")
    raw_score_entries = data.get("score_entries", [])
    if not raw_score_entries:
        st.caption("Saved JSON benchmark results will appear here after a run.")
        return

    dashboard_aliases = load_dashboard_model_aliases()
    all_score_entries = apply_dashboard_model_aliases_to_score_entries(list(raw_score_entries), dashboard_aliases)
    leaderboard = overall_leaderboard_rows(all_score_entries)
    benchmark_categories = leaderboard["benchmark_categories"]
    if st.session_state.get("leaderboard_view_select") == "Historical Average":
        st.session_state.leaderboard_view_select = "Historical"
    if st.session_state.get("leaderboard_view_select") not in LEADERBOARD_VIEW_METRICS:
        st.session_state.leaderboard_view_select = "Current"
    metric_label = st.session_state.leaderboard_view_select
    with st.container(horizontal=True, gap="small", key="leaderboard_view_tabs"):
        for tab_label in LEADERBOARD_VIEW_METRICS:
            st.button(
                tab_label,
                key=f"leaderboard_tab_{tab_label.lower().replace(' ', '_')}",
                type="primary" if tab_label == metric_label else "secondary",
                on_click=set_leaderboard_view,
                args=(tab_label,),
                width="content",
            )
    metric_key = leaderboard_metric_key(metric_label)
    rows = leaderboard_rows_for_metric(leaderboard["rows"], metric_key)
    total_categories = len(benchmark_categories)
    if not total_categories or not rows:
        st.caption("Saved JSON benchmark results will appear here after a run.")
        return

    st.caption(
        f"Fair cross-category ranking across all {total_categories} benchmark "
        f"categor{'y' if total_categories == 1 else 'ies'} found in saved results "
        f"({', '.join(benchmark_categories)}). A category a model hasn't been tested in "
        "counts as 0, so scores climb naturally as coverage grows."
    )
    st.caption(leaderboard_view_description(metric_label))

    for rank, row in enumerate(rows, start=1):
        model_name = str(row["model"])
        tested = row["tested_categories"]
        missing = row["missing_categories"]
        complete = not missing
        rank_hue = (rank * 47 + 12) % 360
        rank_label = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"#{rank}")

        chips_html = leaderboard_category_chips_html(row, benchmark_categories, metric_key)
        score_html = leaderboard_score_html(row, str(metric_label), metric_key)
        badge = '<span class="leaderboard-badge-incomplete">Incomplete</span>' if not complete else ""

        with st.container(border=True):
            st.markdown(
                clean_html_fragment(
                    f"""
                    <div class="leaderboard-head">
                        <div class="leaderboard-rank" style="background:hsl({rank_hue}, 78%, 55%);">{rank_label}</div>
                        <div class="leaderboard-model">
                            <h3>{html.escape(model_name)}</h3>
                            <p>{len(tested)}/{total_categories} categories tested {badge}</p>
                        </div>
                        {score_html}
                    </div>
                    <div class="leaderboard-chips">{chips_html}</div>
                    """
                ),
                unsafe_allow_html=True,
            )
            if missing:
                st.caption(f"Missing: {', '.join(missing)}")
        st.markdown('<div class="strength-card-gap"></div>', unsafe_allow_html=True)


def save_benchmark_exports(markdown_text: str, json_payload: dict, md_filename: str, json_filename: str) -> dict[str, Path]:
    BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)
    md_path = BENCHMARK_DIR / md_filename
    json_path = BENCHMARK_DIR / json_filename
    serialized_json = json.dumps(json_payload, indent=2, ensure_ascii=False) + "\n"
    log_judge_pipeline_debug(
        "EXPORTED BENCHMARK JSON SERIALIZED",
        {
            "json_filename": json_filename,
            "text": serialized_json,
        },
    )
    md_path.write_text(markdown_text, encoding="utf-8")
    json_path.write_text(serialized_json, encoding="utf-8")
    return {"markdown": md_path, "json": json_path}


def open_benchmarks_folder() -> None:
    try:
        BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(str(BENCHMARK_DIR))
    except OSError as exc:
        st.session_state.benchmark_export_message = f"Could not open benchmarks folder: {exc}"
        st.session_state.benchmark_export_message_kind = "error"
        return
    st.session_state.benchmark_export_message = f"Opened benchmarks folder: {BENCHMARK_DIR}"
    st.session_state.benchmark_export_message_kind = "success"


def render_download_buttons(json_payload: dict) -> None:
    json_text = json.dumps(json_payload, indent=2, ensure_ascii=False)
    saved_paths = st.session_state.get("last_saved_benchmark_paths") or {}
    if saved_paths:
        st.caption(f"Auto-saved report files to {saved_paths.get('markdown', BENCHMARK_DIR)}")
        st.button("Open Reports Folder", use_container_width=True, on_click=open_benchmarks_folder)
    consume_status_message("benchmark_export_message", "benchmark_export_message_kind")
    st.download_button(
        "Export report as Markdown",
        data=st.session_state.get("last_report", ""),
        file_name=st.session_state.get("last_export_md_filename", f"{APP_SLUG}-comparison-report.md"),
        mime="text/markdown",
        use_container_width=True,
    )
    st.download_button(
        "Copy JSON as file",
        data=json_text,
        file_name=st.session_state.get("last_export_json_filename", f"{APP_SLUG}-comparison-result.json"),
        mime="application/json",
        use_container_width=True,
    )
    with st.expander("Full JSON"):
        st.code(json_text, language="json")


def configure_page() -> None:
    st.set_page_config(
        page_title=APP_NAME,
        page_icon="LLM",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    st.markdown(
        """
        <style>
        :root {
            --bench-bg: #111315;
            --bench-panel: #181b1f;
            --bench-panel-2: #20242a;
            --bench-text: #f3f0e8;
            --bench-muted: #aaa49a;
            --bench-line: #30353d;
            --bench-accent: #6f8fb8;
            --bench-accent-2: #f2bd6b;
        }
        .stApp {
            background: radial-gradient(circle at top left, #1b2521 0, #111315 34rem);
            color: var(--bench-text);
        }
        .block-container {
            max-width: 1120px;
            padding-top: 2rem;
            padding-bottom: 3rem;
        }
        h1 {
            font-size: 2.35rem !important;
            letter-spacing: 0 !important;
            margin-bottom: 0.15rem !important;
        }
        .bench-subtitle {
            color: var(--bench-muted);
            margin-bottom: 1.4rem;
            font-size: 1.02rem;
        }
        .bench-field-label-spacer {
            height: 1.75rem;
        }
        div[data-testid="stTextArea"] textarea,
        div[data-testid="stTextInput"] input {
            background: linear-gradient(180deg, rgba(31, 43, 58, 0.72), #15181c);
            border: 1px solid rgba(83, 111, 146, 0.68);
            border-radius: 8px;
            color: var(--bench-text);
            font-size: 1rem;
            line-height: 1.45;
            box-shadow: 0 0 0 1px rgba(83, 111, 146, 0.12) inset;
        }
        div[data-testid="stSelectbox"] div[data-baseweb="select"] > div {
            background: linear-gradient(180deg, rgba(31, 43, 58, 0.72), #20242a) !important;
            border-color: rgba(83, 111, 146, 0.76) !important;
            box-shadow: 0 0 0 1px rgba(83, 111, 146, 0.14) inset !important;
        }
        div[data-testid="stTextArea"] textarea:focus,
        div[data-testid="stTextInput"] input:focus,
        div[data-testid="stSelectbox"] div[data-baseweb="select"] > div {
            border-color: rgba(120, 148, 184, 0.98) !important;
        }
        .stButton > button {
            width: 100%;
            border-radius: 8px;
            border: 1px solid #536f92;
            background: linear-gradient(180deg, #4c6688, #334960);
            color: #edf3fa;
            font-weight: 800;
            min-height: 3rem;
            transition: transform 0.12s ease, border-color 0.12s ease, filter 0.12s ease;
        }
        .stButton > button:hover {
            border-color: #7894b8;
            color: #ffffff;
            filter: brightness(1.08);
            transform: translateY(-1px);
        }
        .stButton > button.bench-btn-judge {
            border-color: #287844 !important;
            background: linear-gradient(180deg, #248f52, #185f38) !important;
            color: #eefbf3 !important;
        }
        .stButton > button.bench-btn-judging {
            border-color: #d18824 !important;
            background: linear-gradient(180deg, #c7791d, #71420f) !important;
            color: #fff2d6 !important;
            animation: benchJudgePulse 0.82s ease-in-out infinite alternate;
        }
        .st-key-top_judge_button_trigger {
            position: fixed !important;
            top: 1.15rem !important;
            left: 21rem;
            z-index: 999997 !important;
            width: auto !important;
            height: auto !important;
            margin: 0 !important;
        }
        .st-key-top_judge_button_trigger .stButton,
        .st-key-top_judge_button_trigger div[data-testid="stButton"] {
            width: auto !important;
        }
        .st-key-top_judge_button_trigger .stButton > button {
            min-width: 7.5rem;
            min-height: 2rem;
            padding: 0.35rem 0.9rem;
            border-radius: 8px;
            border: 1px solid #287844 !important;
            background: linear-gradient(180deg, #248f52, #185f38) !important;
            color: #eefbf3 !important;
            font-size: 0.88rem !important;
            font-weight: 500 !important;
            line-height: 1.15 !important;
            cursor: pointer !important;
            box-shadow: 0 10px 26px rgba(0,0,0,0.24);
        }
        .st-key-top_judge_button_trigger .stButton > button:hover {
            border-color: #37a965 !important;
            color: #ffffff !important;
            filter: brightness(1.08);
            transform: none !important;
        }
        .st-key-top_judge_button_trigger .stButton > button:disabled {
            border-color: #d18824 !important;
            background: linear-gradient(180deg, #c7791d, #71420f) !important;
            color: #fff2d6 !important;
            cursor: default !important;
            filter: none;
            animation: benchJudgePulse 0.82s ease-in-out infinite alternate;
        }
        .stButton > button.bench-btn-delete {
            border-color: #c8494f !important;
            background: linear-gradient(180deg, #d95a62, #8f2830) !important;
            color: #fff6f6 !important;
        }
        .stButton > button.bench-btn-save {
            border-color: #287844 !important;
            background: linear-gradient(180deg, #248f52, #185f38) !important;
            color: #eefbf3 !important;
        }
        .stButton > button.bench-btn-edit {
            border-color: #9b6835 !important;
            background: linear-gradient(180deg, #9a6632, #5f3d20) !important;
            color: #fff7eb !important;
            opacity: 0.86;
        }
        div[data-testid="stExpander"].bench-highlight-expander-wrap details {
            border-color: rgba(83, 111, 146, 0.82) !important;
            box-shadow: 0 0 0 1px rgba(83, 111, 146, 0.16) inset !important;
        }
        div[data-testid="stExpander"] details summary.bench-highlight-expander {
            border-color: rgba(83, 111, 146, 0.9) !important;
            background: linear-gradient(180deg, rgba(58, 78, 104, 0.72), rgba(36, 52, 70, 0.68)) !important;
            color: #edf3fa !important;
        }
        div[data-testid="stExpander"] details summary.bench-highlight-expander:hover {
            border-color: rgba(120, 148, 184, 0.98) !important;
            background: linear-gradient(180deg, rgba(70, 94, 124, 0.78), rgba(43, 61, 82, 0.72)) !important;
        }
        .stButton > button.bench-btn-trend {
            min-height: 2.05rem !important;
            width: 2.05rem !important;
            padding: 0 !important;
            border: 1px solid rgba(126, 166, 217, 0.72) !important;
            border-radius: 7px !important;
            background: rgba(44, 61, 82, 0.82) !important;
            color: #f4f8ff !important;
            font-weight: 900 !important;
        }
        @keyframes benchJudgePulse {
            0% { filter: brightness(0.94); box-shadow: 0 0 0 rgba(209, 136, 36, 0); }
            100% { filter: brightness(1.16); box-shadow: 0 0 18px rgba(209, 136, 36, 0.42); }
        }
        .winner-card {
            background: rgba(24, 27, 31, 0.9);
            border: 1px solid var(--bench-line);
            border-radius: 8px;
            padding: 1.1rem 1.2rem;
            margin: 1.2rem 0;
        }
        .winner-card span {
            color: var(--bench-muted);
            display: block;
            font-size: 0.9rem;
            margin-bottom: 0.2rem;
        }
        .winner-card strong {
            color: var(--bench-accent);
            display: block;
            font-size: 2rem;
            line-height: 1.15;
        }
        .winner-card p {
            color: var(--bench-text);
            font-size: 1rem;
            margin: 0.75rem 0 0;
        }
        .winner-card b {
            color: var(--bench-accent);
            font-weight: 800;
        }
        .result-card {
            background: rgba(24, 27, 31, 0.86);
            border: 1px solid var(--bench-line);
            border-radius: 8px;
            padding: 1rem 1.1rem;
            min-height: 100%;
            margin-bottom: 0.7rem;
        }
        .result-card h3 {
            font-size: 1rem !important;
            margin: 0 0 0.65rem !important;
            color: var(--bench-accent-2);
        }
        .mini-score {
            display: flex;
            align-items: baseline;
            gap: 0.45rem;
        }
        .mini-score strong {
            color: var(--bench-accent);
            font-size: 3rem;
            line-height: 1;
        }
        .mini-score span {
            color: var(--bench-muted);
            font-size: 1.2rem;
        }
        .score-row {
            display: grid;
            grid-template-columns: minmax(0, 1fr) auto;
            gap: 1rem;
            align-items: center;
            padding: 0.48rem 0;
            border-bottom: 1px solid rgba(255,255,255,0.08);
        }
        .score-row:last-child {
            border-bottom: 0;
        }
        .score-row span {
            color: var(--bench-text);
        }
        .score-row strong {
            color: var(--bench-accent);
        }
        .small-muted {
            color: var(--bench-muted);
            font-size: 0.92rem;
        }
        .strength-map {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 1rem;
            margin: 0.75rem 0 1.3rem;
        }
        .strength-model {
            background: linear-gradient(145deg, rgba(26, 29, 34, 0.96), rgba(20, 22, 26, 0.96));
            border: 1px solid rgba(255,255,255,0.12);
            border-radius: 8px;
            padding: 1rem;
            margin: 0 0 1.1rem;
            box-shadow: 0 18px 48px rgba(0,0,0,0.18);
        }
        div[data-testid="stVerticalBlockBorderWrapper"] {
            background: linear-gradient(145deg, rgba(26, 29, 34, 0.96), rgba(20, 22, 26, 0.96));
            border-color: rgba(255,255,255,0.12) !important;
            border-radius: 8px !important;
            box-shadow: 0 18px 48px rgba(0,0,0,0.18);
        }
        div[data-testid="stVerticalBlockBorderWrapper"] > div {
            padding-bottom: 1rem !important;
        }
        .strength-card-gap {
            height: 1.1rem;
        }
        .strength-model-head {
            display: flex;
            justify-content: space-between;
            gap: 0.75rem;
            align-items: flex-start;
            margin-bottom: 0.75rem;
        }
        .strength-model h3 {
            color: var(--bench-text);
            font-size: 1.05rem !important;
            line-height: 1.25;
            margin: 0 !important;
        }
        .strength-title-row h3 {
            color: var(--bench-text);
            font-size: 1.3rem !important;
            line-height: 1.25;
            margin: 0 !important;
        }
        .strength-title-row {
            display: flex;
            gap: 0.35rem;
            align-items: center;
            margin: 0;
        }
        /* Discreet inline pencil next to each model name. Kept borderless and
           transparent so it reads as a subtle glyph, not a chunky button —
           works whether or not the JS button classifier has run. */
        .stButton > button.bench-btn-title-edit,
        .stButton > button[data-testid="stBaseButton-tertiary"] {
            min-height: 1.5rem !important;
            height: 1.5rem !important;
            min-width: 1.5rem !important;
            width: 1.5rem !important;
            padding: 0 !important;
            border: none !important;
            background: transparent !important;
            color: var(--bench-muted) !important;
            font-size: 0.95rem !important;
            line-height: 1 !important;
            opacity: 0.55;
            transition: opacity 0.12s ease, color 0.12s ease;
        }
        .stButton > button.bench-btn-title-edit:hover,
        .stButton > button[data-testid="stBaseButton-tertiary"]:hover {
            opacity: 1;
            color: var(--bench-text) !important;
            background: transparent !important;
            border: none !important;
        }
        div[data-testid="stDialog"] {
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
        }
        div[data-testid="stDialog"] div[role="dialog"] {
            max-width: min(58rem, calc(100vw - 2rem)) !important;
            width: min(58rem, calc(100vw - 2rem)) !important;
            margin: 1rem !important;
            left: 0 !important;
            transform: none !important;
        }
        div[data-testid="stDialog"] h2 {
            font-size: 1.25rem !important;
            line-height: 1.25 !important;
            margin-bottom: 0.8rem !important;
        }
        div[data-testid="stDialog"] div[data-testid="stTextInput"] input {
            min-height: 2.35rem !important;
            font-size: 0.95rem !important;
        }
        div[data-testid="stDialog"] .stButton > button {
            min-height: 2.35rem !important;
            padding: 0.35rem 0.8rem !important;
        }
        .strength-model p {
            color: var(--bench-muted);
            font-size: 0.86rem;
            margin: 0;
        }
        .strength-count {
            color: var(--bench-muted);
            font-size: 0.86rem;
            margin: 0;
        }
        .strength-progress {
            color: var(--bench-muted);
            font-size: 0.82rem;
            margin: 0.3rem 0 0.65rem;
            display: flex;
            align-items: center;
            gap: 0.45rem;
            flex-wrap: wrap;
        }
        .view-tag-progress {
            display: inline-block;
            margin: 0.1rem 0 0.6rem;
            padding: 0.14rem 0.6rem;
            border-radius: 999px;
            font-size: 0.7rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            border: 1px solid var(--bench-accent);
            color: var(--bench-accent);
        }
        .strength-overall {
            color: var(--bench-accent-2);
            white-space: nowrap;
            text-align: right;
        }
        .strength-overall strong {
            font-size: 1.8rem;
            line-height: 1;
        }
        .strength-overall span {
            color: var(--bench-muted);
            font-size: 0.85rem;
            margin-left: 0.18rem;
        }
        .strength-chips {
            display: flex;
            flex-wrap: wrap;
            gap: 0.45rem;
            margin-bottom: 0.7rem;
        }
        .strength-chips span {
            border: 1px solid rgba(255,255,255,0.12);
            border-radius: 999px;
            color: var(--bench-muted);
            font-size: 0.78rem;
            padding: 0.28rem 0.52rem;
        }
        .strength-chips b {
            color: var(--bench-text);
        }
        .strength-row {
            margin-top: 0.54rem;
        }
        .strength-row:last-child {
            margin-bottom: 0.95rem;
        }
        .strength-label {
            display: grid;
            grid-template-columns: minmax(0, 1fr) auto;
            gap: 0.75rem;
            align-items: center;
            font-size: 0.88rem;
            margin-bottom: 0.18rem;
        }
        .strength-label span {
            color: var(--bench-text);
            overflow-wrap: anywhere;
        }
        .strength-label strong {
            color: var(--bench-accent-2);
        }
        .strength-track {
            background: rgba(255,255,255,0.08);
            border-radius: 999px;
            height: 0.68rem;
            overflow: hidden;
        }
        .strength-fill {
            border-radius: 999px;
            height: 100%;
        }
        .strength-row.needs-work .strength-label strong {
            color: #ff9d8e;
        }
        .leaderboard-head {
            display: flex;
            align-items: center;
            gap: 0.85rem;
            margin-bottom: 0.65rem;
        }
        .st-key-leaderboard_view_tabs {
            width: fit-content;
            gap: 0.3rem;
        }
        .st-key-leaderboard_view_tabs button {
            min-height: 1.9rem;
            padding: 0.28rem 0.72rem;
            border-color: rgba(255,255,255,0.12);
            color: var(--bench-muted);
            transition: color 120ms ease, border-color 120ms ease, background 120ms ease;
        }
        .st-key-leaderboard_view_tabs button:hover {
            color: var(--bench-text);
            border-color: rgba(255,255,255,0.24);
        }
        .st-key-leaderboard_view_tabs button[kind="primary"],
        .st-key-leaderboard_view_tabs button[data-testid="stBaseButton-primary"] {
            color: #55ff88 !important;
            border-color: #238a50 !important;
            background: #103d27 !important;
            box-shadow: 0 0 12px rgba(85, 255, 136, 0.2);
        }
        .st-key-leaderboard_view_tabs button[kind="primary"] p,
        .st-key-leaderboard_view_tabs button[data-testid="stBaseButton-primary"] p {
            color: #55ff88 !important;
            font-weight: 700;
        }
        .st-key-leaderboard_view_tabs button:focus-visible {
            outline: 2px solid #55ff88;
            outline-offset: 2px;
        }
        .leaderboard-rank {
            flex: none;
            width: 2.3rem;
            height: 2.3rem;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 800;
            font-size: 1rem;
            color: #14171b;
            box-shadow: 0 0 14px rgba(0,0,0,0.35);
        }
        .leaderboard-model {
            flex: 1;
            min-width: 0;
        }
        .leaderboard-model h3 {
            color: var(--bench-text);
            font-size: 1.05rem !important;
            margin: 0 !important;
            line-height: 1.25;
        }
        .leaderboard-model p {
            color: var(--bench-muted);
            font-size: 0.83rem;
            margin: 0.2rem 0 0;
            display: flex;
            align-items: center;
            gap: 0.4rem;
            flex-wrap: wrap;
        }
        .leaderboard-overall {
            flex: none;
            color: var(--bench-accent-2);
            white-space: nowrap;
            text-align: right;
        }
        .leaderboard-overall strong {
            display: block;
            font-size: 1.8rem;
            line-height: 1;
        }
        .leaderboard-overall span {
            display: block;
            color: var(--bench-muted);
            font-size: 0.72rem;
            margin-bottom: 0.3rem;
        }
        .leaderboard-badge-incomplete {
            background: linear-gradient(120deg, #ff5a5f, #f5b84b);
            color: #14171b;
            font-weight: 700;
            font-size: 0.7rem;
            padding: 0.14rem 0.5rem;
            border-radius: 999px;
            text-transform: uppercase;
            letter-spacing: 0.03em;
        }
        .leaderboard-chips {
            display: flex;
            flex-wrap: wrap;
            gap: 0.45rem;
            padding-bottom: 0.65rem;
        }
        .leaderboard-chip {
            border: 1px solid rgba(255,255,255,0.12);
            border-radius: 999px;
            color: var(--bench-muted);
            font-size: 0.78rem;
            padding: 0.28rem 0.55rem;
        }
        .leaderboard-chip b {
            color: var(--bench-text);
            margin-left: 0.2rem;
        }
        .leaderboard-chip.tested {
            border-color: rgba(85, 214, 143, 0.35);
        }
        .leaderboard-chip.untested {
            border-style: dashed;
            opacity: 0.65;
        }
        .leaderboard-chip.untested b {
            color: var(--bench-muted);
            font-style: italic;
        }
        .trend-map {
            display: grid;
            gap: 0.62rem;
            margin: 0.5rem 0 1.2rem;
        }
        .trend-row {
            background: rgba(24, 27, 31, 0.72);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 8px;
            padding: 0.72rem;
        }
        .trend-meta {
            display: grid;
            gap: 0.12rem;
            margin-bottom: 0.42rem;
        }
        .trend-meta strong {
            color: var(--bench-text);
            font-size: 0.92rem;
        }
        .trend-meta span {
            color: var(--bench-muted);
            font-size: 0.78rem;
            overflow-wrap: anywhere;
        }
        .trend-bars {
            display: flex;
            overflow: hidden;
            border-radius: 999px;
            background: rgba(255,255,255,0.08);
            min-height: 1.15rem;
        }
        .trend-a,
        .trend-b {
            color: #101317;
            font-size: 0.72rem;
            font-weight: 800;
            line-height: 1.15rem;
            min-width: fit-content;
            padding: 0 0.34rem;
            text-align: center;
        }
        .model-trend-svg {
            width: 100%;
            min-height: 240px;
            margin: 0.65rem 0 0.9rem;
            background: rgba(16, 19, 23, 0.72);
            border: 1px solid rgba(255,255,255,0.12);
            border-radius: 8px;
        }
        .model-trend-svg line {
            stroke: rgba(255,255,255,0.26);
            stroke-width: 1;
        }
        .model-trend-svg polyline {
            fill: none;
            stroke-width: 4;
            stroke-linecap: round;
            stroke-linejoin: round;
        }
        .model-trend-svg circle {
            fill: var(--bench-accent-2);
            stroke: #111315;
            stroke-width: 2;
        }
        .model-trend-svg text {
            fill: var(--bench-muted);
            font-size: 12px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def main() -> None:
    configure_page()
    hydrate_form_state()
    if judging_active() and not st.session_state.get("judge_run_requested"):
        st.session_state.judging_active = False

    st.title(APP_NAME)
    st.markdown(
        '<div class="bench-subtitle">Fast companion-response comparison for two generated replies.</div>',
        unsafe_allow_html=True,
    )
    render_judging_overlay(judging_active())
    render_action_button_classifier()
    render_modal_dismiss_guard()

    try:
        config = load_config(APP_DIR)
        config_error = None
        set_debug_logging(config.debug_logging)
    except ConfigError as exc:
        config = None
        config_error = str(exc)

    rubrics = available_rubrics()
    if not rubrics:
        st.error("No rubrics found in the rubrics folder.")
        return
    active_prompt_pack_metadata = active_benchmark_prompt_pack_metadata()
    scoring_rubric_path, _ = scoring_rubric_path_for_pack(rubrics, active_prompt_pack_metadata)
    if not scoring_rubric_path:
        st.error("No scoring rubric is available.")
        return
    rubric_name = scoring_rubric_path.name

    if "history" not in st.session_state:
        st.session_state.history = []

    sync_dashboard_query_view()
    endpoint_options = judge_endpoint_options(config)
    app_view = "Benchmark Test"

    with st.sidebar:
        st.header("View")
        view_options = ["Benchmark Test", "Results Dashboard", "Overall Leaderboard"]
        view_index = selected_index_from_query(view_options, "app_view", 0)
        app_view = st.radio(
            "View",
            view_options,
            index=view_index,
            key="app_view_select",
            label_visibility="collapsed",
        )
        if app_view != "Results Dashboard":
            st.session_state.pop("dashboard_trend_model", None)

        st.header("Judge")
        st.caption("Active Judge Profile")
        st.markdown(f"**{active_prompt_pack_metadata['judge_profile']}**")
        st.caption(f"Category: {active_prompt_pack_metadata['category']}")
        endpoint_index = selected_index_from_query(endpoint_options, "llmb_judge_endpoint", 0)
        if st.session_state.get("judge_endpoint_select") not in (endpoint_options or ["No config loaded"]):
            st.session_state.pop("judge_endpoint_select", None)
        judge_endpoint_name = st.selectbox(
            "Judge endpoint",
            endpoint_options or ["No config loaded"],
            index=endpoint_index,
            disabled=not bool(config),
            key="judge_endpoint_select",
        )
        judge_endpoint = selected_judge_endpoint(config, judge_endpoint_name)
        st.text_input(
            "API key",
            type="password",
            placeholder="Enter a replacement key",
            key="judge_api_key_input",
            disabled=not bool(judge_endpoint),
        )
        st.button(
            "Save API Key",
            use_container_width=True,
            disabled=not bool(judge_endpoint) or not bool(st.session_state.get("judge_api_key_input", "").strip()),
            on_click=save_selected_judge_api_key,
            args=(judge_endpoint.name if judge_endpoint else "",),
        )
        consume_status_message("judge_api_key_message", "judge_api_key_message_kind")
        model_options = judge_model_options(judge_endpoint)
        if st.session_state.get("judge_model_select") not in model_options:
            if config:
                st.session_state.judge_model_select = selected_judge_model(judge_endpoint)
            else:
                st.session_state.pop("judge_model_select", None)
        judge_model_index = selected_index_from_query(
            model_options,
            "llmb_judge_model",
            model_options.index(st.session_state.judge_model_select) if st.session_state.get("judge_model_select") in model_options else 0,
        )
        judge_model = st.selectbox(
            "Judge",
            model_options or ["No config loaded"],
            index=judge_model_index,
            disabled=not bool(config),
            key="judge_model_select",
        )
        st.button(
            "Refresh Judge Models",
            use_container_width=True,
            disabled=not bool(judge_endpoint),
            on_click=refresh_judge_models,
            args=(judge_endpoint,),
        )
        st.button(
            "Test Judge Connection",
            use_container_width=True,
            disabled=not bool(judge_endpoint),
            on_click=test_judge_connection,
            args=(judge_endpoint, judge_model),
        )
        if st.session_state.get("judge_model_refresh_message"):
            st.caption(st.session_state.judge_model_refresh_message)

        st.header("History")
        if st.session_state.history:
            for item in reversed(st.session_state.history[-10:]):
                if item.get("confidence") is None:
                    st.caption(f"{item['time']} | {item['winner']}")
                elif isinstance(item.get("confidence"), (int, float)):
                    st.caption(f"{item['time']} | {item['winner']} | {item['confidence']}%")
                else:
                    st.caption(f"{item['time']} | {item['winner']} | {item['confidence']}")
        else:
            st.caption("Comparisons will appear here.")

        if st.button("Clear History", use_container_width=True):
            st.session_state.history = []
            st.rerun()

    if config_error:
        st.warning(config_error)
        st.info("Create config.yaml from config.example.yaml, then add your endpoint details.")

    if app_view in ("Results Dashboard", "Overall Leaderboard"):
        preserve_benchmark_widget_state()
        persist_current_form_state()
        st.session_state.last_rendered_app_view = app_view
        if app_view == "Results Dashboard":
            render_benchmark_strength_map()
        else:
            render_overall_leaderboard()
        return

    if st.session_state.get("last_rendered_app_view", "Benchmark Test") != "Benchmark Test":
        restore_benchmark_widget_values()
    st.session_state.last_rendered_app_view = "Benchmark Test"

    response_cols = st.columns(2)
    with response_cols[0]:
        field_clipboard_controls("model_name_a_input", "Model Name A", "input")
        model_name_a = st.text_input("Model Name A", value="", key="model_name_a_input", label_visibility="collapsed")
        st.button("Clear Model A Responses", use_container_width=True, on_click=clear_response_a_inputs)
    with response_cols[1]:
        field_clipboard_controls("model_name_b_input", "Model Name B", "input")
        model_name_b = st.text_input("Model Name B", value="", key="model_name_b_input", label_visibility="collapsed")
        st.button("Clear Model B Responses", use_container_width=True, on_click=clear_response_b_inputs)

    import_test_cols = st.columns([2, 1, 1])
    with import_test_cols[0]:
        st.file_uploader(
            "Import HWUI Test Chat / Responses",
            type=["json", "txt"],
            key="test_chat_response_upload",
        )
    with import_test_cols[1]:
        st.selectbox(
            "Import Into",
            ["Model A", "Model B"],
            key="test_chat_response_import_target",
        )
    with import_test_cols[2]:
        align_with_label()
        st.button(
            "Import",
            use_container_width=True,
            on_click=import_uploaded_test_chat_responses,
        )

    response_file_had_message = consume_status_message("model_response_message", "model_response_message_kind")
    benchmark_session_had_message = consume_status_message(
        "benchmark_session_message",
        "benchmark_session_message_kind",
    )

    with st.expander(
        "Saved Files",
        expanded=bool(response_file_had_message or benchmark_session_had_message),
    ):
        save_cols = st.columns(2)
        with save_cols[0]:
            st.button(
                "Save Model A Responses",
                use_container_width=True,
                on_click=save_current_model_responses,
                args=("a",),
            )
        with save_cols[1]:
            st.button(
                "Save Model B Responses",
                use_container_width=True,
                on_click=save_current_model_responses,
                args=("b",),
            )

        response_sets = available_model_response_sets()
        if response_sets:
            import_cols = st.columns([2, 1, 1])
            with import_cols[0]:
                st.selectbox(
                    "Saved Responses",
                    list(response_sets.keys()),
                    key="model_response_set_select",
                )
            with import_cols[1]:
                st.selectbox(
                    "Import To",
                    ["Model A", "Model B"],
                    key="model_response_import_target",
                )
            with import_cols[2]:
                align_with_label()
                st.button(
                    "Import Model Responses",
                    use_container_width=True,
                    on_click=import_selected_model_responses,
                )
        else:
            st.caption("Saved model response files will appear here.")
        st.markdown("**Full Benchmark Sessions**")
        st.caption("Save All writes reloadable working snapshots to benchmark_sessions. Completed judge reports auto-save separately to benchmarks as Markdown plus JSON.")
        session_cols = st.columns([1, 1, 1])
        with session_cols[0]:
            st.button(
                "Save All",
                use_container_width=True,
                on_click=save_current_benchmark_session,
            )
        with session_cols[1]:
            st.button(
                "Open Sessions Folder",
                use_container_width=True,
                on_click=open_benchmark_sessions_folder,
            )

        saved_sessions = available_benchmark_sessions()
        if saved_sessions:
            import_session_cols = st.columns([2, 1])
            with import_session_cols[0]:
                st.selectbox(
                    "Saved Full Sessions",
                    list(saved_sessions.keys()),
                    key="benchmark_session_select",
                )
            with import_session_cols[1]:
                align_with_label()
                st.button(
                    "Import All",
                    use_container_width=True,
                    on_click=import_selected_benchmark_session,
                )
        else:
            st.caption("Saved full benchmark sessions will appear here.")
    prompt_pack_groups = available_prompt_pack_groups()
    pack_categories = ordered_pack_categories(prompt_pack_groups)
    if pack_categories:
        default_pack_category = prompt_pack_category(DEFAULT_PROMPT_PACK_PATH)
        default_category_index = pack_categories.index(default_pack_category) if default_pack_category in pack_categories else 0
        selected_category = st.session_state.get("prompt_pack_category_select")
        selected_pack_label = st.session_state.get("prompt_pack_select")
        selected_pack_category = next(
            (category for category, labels in prompt_pack_groups.items() if selected_pack_label in labels),
            None,
        )
        active_category = selected_category if selected_category in prompt_pack_groups else (
            selected_pack_category or pack_categories[default_category_index]
        )
        active_category_index = pack_categories.index(active_category)
        pack_labels = list(prompt_pack_groups[active_category].keys())
        default_pack_label = prompt_pack_label(DEFAULT_PROMPT_PACK_PATH)
        if selected_pack_label in pack_labels:
            active_pack_index = pack_labels.index(selected_pack_label)
        else:
            active_pack_index = pack_labels.index(default_pack_label) if default_pack_label in pack_labels else 0
            st.session_state.prompt_pack_select = pack_labels[active_pack_index]
            selected_pack_label = pack_labels[active_pack_index]
        st.markdown("**Prompt Pack**")
        pack_cols = st.columns([1, 2, 1, 1])
        with pack_cols[0]:
            st.selectbox(
                "Prompt Pack Category",
                pack_categories,
                index=active_category_index,
                key="prompt_pack_category_select",
                label_visibility="collapsed",
            )
        with pack_cols[1]:
            st.selectbox(
                "Prompt Pack",
                pack_labels,
                index=active_pack_index,
                key="prompt_pack_select",
                label_visibility="collapsed",
            )
        with pack_cols[2]:
            st.button(
                "Load Selected Pack",
                use_container_width=True,
                on_click=request_prompt_pack_load,
            )
        with pack_cols[3]:
            st.button(
                "Delete Pack",
                use_container_width=True,
                on_click=request_prompt_pack_delete,
            )
    else:
        st.warning("No prompt packs found in prompt_packs.")
    consume_status_message("prompt_pack_save_message", "prompt_pack_save_message_kind")
    if st.session_state.get("confirm_load_prompt_pack"):
        selected_pack_name = selected_prompt_pack_path().name
        st.warning(f"Prompt fields already contain text. Replace the five visible prompts with {selected_pack_name}?")
        load_cols = st.columns([1, 1])
        with load_cols[0]:
            st.button("Replace Prompts", use_container_width=True, on_click=load_selected_prompt_pack)
        with load_cols[1]:
            if st.button("Keep Current Prompts", use_container_width=True):
                st.session_state.confirm_load_prompt_pack = False
                st.rerun()
    if st.session_state.get("confirm_delete_prompt_pack"):
        selected_pack_path = selected_prompt_pack_path()
        st.warning(f"Delete prompt pack {selected_pack_path.name}?")
        delete_cols = st.columns([1, 1])
        with delete_cols[0]:
            st.button("Confirm Delete Pack", use_container_width=True, on_click=delete_selected_prompt_pack)
        with delete_cols[1]:
            if st.button("Cancel Delete", use_container_width=True):
                st.session_state.confirm_delete_prompt_pack = False
                st.rerun()

    pack_editor_had_message = consume_status_message("pack_editor_message", "pack_editor_message_kind")

    with st.expander("Prompt Pack Editor", expanded=bool(pack_editor_had_message)):
        st.button("Load Selected Pack Into Editor", use_container_width=True, on_click=load_prompt_pack_into_editor)
        bound_path = pack_editor_bound_path()
        stale_bound_name = pack_editor_stale_bound_name()
        if bound_path is None and stale_bound_name is not None:
            st.warning(
                f"The fields below still hold what was loaded from `{stale_bound_name}`, but that file "
                "no longer exists on disk (renamed, moved, or deleted elsewhere) — so there is nothing "
                'for "Save / Rename Loaded Pack" to save into, and it is disabled below. Click "Load '
                'Selected Pack Into Editor" to replace this with a real pack, or use "Save As New Pack" '
                "to keep what's here as a new file."
            )
        elif bound_path is None:
            st.caption(
                'Nothing loaded yet — click "Load Selected Pack Into Editor" above, or fill in '
                'prompts below and use "Save As New Pack".'
            )
        else:
            st.caption(f"✏️ Editing file: `{bound_path.name}`")
            top_picker_path = selected_prompt_pack_path()
            if top_picker_path != bound_path:
                st.caption(
                    f"Note: the \"Prompt Pack\" picker above is currently on `{top_picker_path.name}` — "
                    "that's unrelated to the file loaded here and won't be affected by saving."
                )
        editor_meta_cols = st.columns([2, 1])
        with editor_meta_cols[0]:
            st.text_input("Pack Name", key="pack_editor_name_input")
        with editor_meta_cols[1]:
            category_value = st.session_state.get("pack_editor_category_select", "General")
            category_index = PACK_METADATA_OPTIONS.index(category_value) if category_value in PACK_METADATA_OPTIONS else PACK_METADATA_OPTIONS.index("General")
            st.selectbox(
                "Category",
                PACK_METADATA_OPTIONS,
                index=category_index,
                key="pack_editor_category_select",
            )
        rename_blocked = False
        if bound_path is not None:
            typed_name = st.session_state.get("pack_editor_name_input", "").strip()
            if typed_name:
                target_path, is_rename_preview = pack_editor_rename_target(bound_path, typed_name)
                if is_rename_preview:
                    if target_path.exists():
                        rename_blocked = True
                        st.error(
                            f'Pack Name "{typed_name}" saves to `{target_path.name}`, which already exists as a '
                            "different pack. Saving is blocked — pick another name, or change Pack Name back "
                            f"to keep editing `{bound_path.name}`."
                        )
                    else:
                        st.info(f"Will rename on save: `{bound_path.name}` → `{target_path.name}`")
        editor_save_cols = st.columns([1, 1])
        with editor_save_cols[0]:
            st.button(
                "Save / Rename Loaded Pack",
                type="primary",
                use_container_width=True,
                disabled=rename_blocked or bound_path is None,
                on_click=save_prompt_pack_editor_to_selected,
            )
        with editor_save_cols[1]:
            st.button(
                "Save As New Pack",
                use_container_width=True,
                on_click=request_save_prompt_pack_as,
            )
        if st.session_state.get("show_save_as_new_pack"):
            save_as_cols = st.columns([2, 1, 1])
            with save_as_cols[0]:
                st.text_input(
                    "New Pack Name",
                    key="new_pack_name_input",
                    placeholder="Type a new pack name...",
                )
            with save_as_cols[1]:
                st.button(
                    "Create New Pack",
                    type="primary",
                    use_container_width=True,
                    on_click=save_prompt_pack_from_editor,
                )
            with save_as_cols[2]:
                st.button("Cancel", use_container_width=True, on_click=cancel_save_prompt_pack_as)
        for slot in range(1, BATCH_SLOT_COUNT + 1):
            with st.expander(f"Editor Prompt {slot}", expanded=slot == 1):
                st.text_input(f"Editor Title {slot}", key=f"pack_editor_title_{slot}")
                st.text_area(
                    f"Editor Prompt {slot}",
                    height=150,
                    key=f"pack_editor_prompt_{slot}",
                    placeholder="Write the benchmark prompt...",
                )
    active_test_metadata = active_benchmark_prompt_pack_metadata()
    benchmark_header_cols = st.columns([1, 2])
    with benchmark_header_cols[0]:
        st.subheader("Benchmark Prompts")
    with benchmark_header_cols[1]:
        st.markdown(
            (
                f"**Test Category:** {html.escape(active_test_metadata['category'])} &nbsp; | &nbsp; "
                f"**Active Pack:** {html.escape(active_test_metadata['name'])} &nbsp; | &nbsp; "
                f"**Judge Profile:** {html.escape(active_test_metadata['judge_profile'])}"
            ),
            unsafe_allow_html=True,
        )
    for slot in range(1, BATCH_SLOT_COUNT + 1):
        with st.expander(f"Prompt {slot}", expanded=slot == 1):
            field_clipboard_controls(f"batch_prompt_{slot}", f"Prompt {slot}", "textarea")
            st.text_area(
                f"Prompt {slot}",
                height=130,
                placeholder="Paste or load a benchmark prompt...",
                key=f"batch_prompt_{slot}",
                label_visibility="collapsed",
            )
            batch_cols = st.columns(2)
            with batch_cols[0]:
                field_clipboard_controls(f"batch_response_a_{slot}", f"Response A {slot}", "textarea")
                st.text_area(
                    f"Response A {slot}",
                    height=220,
                    placeholder="Paste Response A for this prompt...",
                    key=f"batch_response_a_{slot}",
                    label_visibility="collapsed",
                )
            with batch_cols[1]:
                field_clipboard_controls(f"batch_response_b_{slot}", f"Response B {slot}", "textarea")
                st.text_area(
                    f"Response B {slot}",
                    height=220,
                    placeholder="Paste Response B for this prompt...",
                    key=f"batch_response_b_{slot}",
                    label_visibility="collapsed",
                )

    # Self-judging stays permitted - it is an established workflow here - but it
    # is disclosed before the run rather than discovered afterwards in the report.
    pending_disclosure = self_judging_disclosure(
        judge_model or "",
        model_name_or_default("A", model_name_a),
        model_name_or_default("B", model_name_b),
    )
    if pending_disclosure.get("judge_is_candidate"):
        st.warning(f"Self-judging comparison. {pending_disclosure.get('warning', '')}")

    action_cols = st.columns([1, 1])
    current_benchmark_fingerprint = benchmark_input_fingerprint(model_name_a, model_name_b, active_test_metadata)
    judge_button_label = "Judge"
    if judging_active():
        judge_button_label = "Judging..."
    elif (
        st.session_state.get("last_completed_benchmark_fingerprint") == current_benchmark_fingerprint
        and st.session_state.get("last_batch_results")
    ):
        judge_button_label = "Retest"
    st.button(
        judge_button_label,
        key="top_judge_button_trigger",
        disabled=judging_active(),
        on_click=request_judge_run,
    )
    with action_cols[0]:
        st.button(
            judge_button_label,
            type="primary",
            use_container_width=True,
            disabled=judging_active(),
            on_click=request_judge_run,
        )
        judge_clicked = bool(st.session_state.pop("judge_run_requested", False))
    with action_cols[1]:
        if st.button("Clear All Benchmark Inputs", use_container_width=True):
            st.session_state.confirm_clear_inputs = True
            st.rerun()

    if st.session_state.get("confirm_clear_inputs"):
        st.warning("Clear all benchmark inputs?")
        confirm_cols = st.columns([1, 1])
        with confirm_cols[0]:
            st.button("Confirm Clear All", use_container_width=True, on_click=clear_persisted_inputs)
        with confirm_cols[1]:
            if st.button("Cancel", use_container_width=True):
                st.session_state.confirm_clear_inputs = False
                st.rerun()

    if judge_clicked:
        if not config:
            st.error("Config is not ready yet.")
            finish_judge_run()
            return
        prompt_pack_metadata = active_benchmark_prompt_pack_metadata()
        scoring_rubric_path, rubric_warning = scoring_rubric_path_for_pack(rubrics, prompt_pack_metadata)
        if not scoring_rubric_path:
            st.error("No scoring rubric is available.")
            finish_judge_run()
            return
        if rubric_warning:
            st.warning(rubric_warning)
        rubric_name = scoring_rubric_path.name
        rubric = scoring_rubric_text_for_pack(scoring_rubric_path, prompt_pack_metadata)
        extra_score_categories = pack_extra_score_categories(prompt_pack_metadata, scoring_rubric_path)
        judge_profile_prompt = selected_judge_profile_prompt()
        resolved_model_name_a = model_name_or_default("A", model_name_a)
        resolved_model_name_b = model_name_or_default("B", model_name_b)
        missing_model_labels = []
        if not model_name_a.strip():
            missing_model_labels.append("Model Name A")
        if not model_name_b.strip():
            missing_model_labels.append("Model Name B")
        if missing_model_labels:
            st.warning(
                f"{' and '.join(missing_model_labels)} left blank. "
                f"Using {resolved_model_name_a} and {resolved_model_name_b} for this benchmark."
            )

        try:
            batch_items = visible_benchmark_items(model_name_a, model_name_b)
        except ValueError as exc:
            st.error(str(exc))
            finish_judge_run()
            return

        try:
            preflight_judge_endpoint(judge_endpoint, judge_model)
        except ApiError as exc:
            st.error(f"Judge endpoint preflight failed: {exc}")
            raw_response = getattr(exc, "raw_response", None)
            if raw_response is not None:
                with st.expander("Raw judge response"):
                    st.code(raw_response or "<empty response>", language="text")
            finish_judge_run()
            return

        batch_results = []
        try:
            acquire_bench_lock(judge_endpoint)
        except ApiError as exc:
            st.error(f"Could not lock judge endpoint before benchmarking: {exc}")
            finish_judge_run()
            return

        total_items = len(batch_items)
        # Every comparison is judged in both response orders, so the work unit
        # for progress is the judge call, not the prompt. Reporting prompts only
        # would leave the bar apparently stalled for half of each comparison.
        total_passes = total_items * PASSES_PER_COMPARISON
        progress_slot = st.empty()
        try:
            with st.spinner(
                f"Comparing {total_items} prompt pairs in both response orders "
                f"({total_passes} judge calls)..."
            ):
                completed_passes = 0
                for index, item in enumerate(batch_items, start=1):
                    item_title = str(item.get("title") or "").strip()

                    def report_pass_start(pass_name: str, _prompt_id: str, _index: int = index,
                                          _title: str = item_title) -> None:
                        order = (
                            "first order" if pass_name == FORWARD else "reversed order"
                        )
                        label = f"Judging prompt {_index} of {total_items} ({order})"
                        if _title:
                            label += f": {_title}"
                        with progress_slot:
                            update_judging_overlay_progress(
                                completed_passes + (0 if pass_name == FORWARD else 1),
                                total_passes,
                                label,
                            )

                    try:
                        result = judge_comparison_bidirectional(
                            config=config,
                            rubric=rubric,
                            judging_profile=judge_profile_prompt,
                            conversation_context=item["conversation_context"],
                            current_prompt=item["current_prompt"],
                            response_a=item["response_a"],
                            response_b=item["response_b"],
                            model_name_a=item["model_name_a"],
                            model_name_b=item["model_name_b"],
                            model=judge_model,
                            temperature=judge_endpoint.temperature,
                            endpoint=judge_endpoint,
                            extra_headers=judge_request_headers(judge_endpoint),
                            prompt_id=str(item.get("id") or f"prompt_{index:02d}"),
                            extra_score_categories=extra_score_categories,
                            on_pass_start=report_pass_start,
                        )
                    except JudgeError as exc:
                        st.error(f"Prompt {index}: {exc}")
                        raw_response = getattr(exc, "raw_response", None)
                        if raw_response is not None:
                            with st.expander("Raw judge response"):
                                st.code(raw_response or "<empty response>", language="text")
                        finish_judge_run()
                        return
                    completed_passes += PASSES_PER_COMPARISON
                    attach_winner_model_name(result, item["model_name_a"], item["model_name_b"])
                    batch_results.append({"item": item, "result": result})
                with progress_slot:
                    update_judging_overlay_progress(total_passes, total_passes, "Finalising results...")
        finally:
            try:
                release_bench_lock(judge_endpoint)
            except ApiError as exc:
                st.warning(f"Could not release judge endpoint lock: {exc}")

        export_time = datetime.now()
        st.session_state.last_result = None
        st.session_state.last_batch_results = batch_results
        remember_prompt_pack_metadata(prompt_pack_metadata, persist=True)
        st.session_state.last_judge_endpoint_name = judge_endpoint.name
        st.session_state.last_judge_model = judge_model
        report_text = batch_report_markdown(
            batch_results,
            rubric_name,
            judge_endpoint.name,
            judge_model,
            prompt_pack_metadata,
        )
        json_payload = batch_json_payload(
            batch_results,
            rubric_name,
            judge_endpoint.name,
            judge_model,
            prompt_pack_metadata,
        )
        st.session_state.last_report = report_text
        st.session_state.last_json_payload = json_payload
        st.session_state.last_model_name_a = resolved_model_name_a
        st.session_state.last_model_name_b = resolved_model_name_b
        prompt_pack_name = prompt_pack_metadata["name"]
        st.session_state.last_export_md_filename = export_filename(
            resolved_model_name_a,
            resolved_model_name_b,
            "md",
            export_time,
            prompt_pack_name,
            prompt_pack_metadata["category"],
        )
        st.session_state.last_export_json_filename = export_filename(
            resolved_model_name_a,
            resolved_model_name_b,
            "json",
            export_time,
            prompt_pack_name,
            prompt_pack_metadata["category"],
        )
        try:
            st.session_state.last_saved_benchmark_paths = save_benchmark_exports(
                report_text,
                json_payload,
                st.session_state.last_export_md_filename,
                st.session_state.last_export_json_filename,
            )
        except OSError as exc:
            st.warning(f"Could not save benchmark files to {BENCHMARK_DIR}: {exc}")
        overall_a, overall_b = batch_overall_scores(batch_results)
        st.session_state.history.append(
            {
                "time": datetime.now().strftime("%H:%M"),
                "winner": benchmark_winner_name(batch_results),
                "confidence": f"{overall_a:.2f} vs {overall_b:.2f}",
            }
        )
        st.session_state.last_completed_benchmark_fingerprint = benchmark_input_fingerprint(
            model_name_a,
            model_name_b,
            prompt_pack_metadata,
        )
        finish_judge_run()
        st.rerun()

    batch_results = st.session_state.get("last_batch_results") or []
    if not batch_results:
        st.markdown(
            '<p class="small-muted">Fill Prompt 1-5 with Response A and Response B, then click Judge.</p>',
            unsafe_allow_html=True,
        )
        persist_and_inject_form_state()
        return

    st.subheader("Benchmark Results")
    overall_a, overall_b = batch_overall_scores(batch_results)
    first_item = batch_results[0]["item"]
    response_a_label, response_b_label = distinct_model_labels(
        first_item.get("model_name_a", ""), first_item.get("model_name_b", "")
    )
    winner = benchmark_winner_name(batch_results)
    unstable_indexes = batch_position_sensitive_entries(batch_results)
    legacy_count = batch_legacy_entry_count(batch_results)
    slot_effect = batch_positional_difference(batch_results)
    st.markdown(
        f"""
        <div class="winner-card">
            <span>Canonical Winner</span>
            <strong>{html.escape(winner)}</strong>
            <p>
                {html.escape(response_a_label)} canonical overall:
                <b>{overall_a:.2f} / 10</b>
                &nbsp;&nbsp;|&nbsp;&nbsp;
                {html.escape(response_b_label)} canonical overall:
                <b>{overall_b:.2f} / 10</b>
            </p>
            <p class="small-muted">
                Identity-mapped average of both response orders.
                {"Mean slot effect " + format_difference(slot_effect) + "." if slot_effect is not None else ""}
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    classification_counts = benchmark_classification_counts(batch_results)
    st.caption(
        "Classification breakdown: "
        + ", ".join(f"{CLASSIFICATION_LABELS[key]} {count}" for key, count in classification_counts.items())
    )
    last_judge_model = st.session_state.get("last_judge_model") or judge_model
    disclosure = self_judging_disclosure(last_judge_model, response_a_label, response_b_label)
    if disclosure.get("judge_is_candidate"):
        st.warning(f"Self-judging comparison. {disclosure.get('warning', '')}")
    if unstable_indexes:
        st.warning(
            f"{len(unstable_indexes)} of {len(batch_results)} comparisons were position-sensitive "
            f"(prompts {', '.join(str(index) for index in unstable_indexes)}). "
            "Their winner changed when the response order was reversed, so those results are not clean wins."
        )
    if legacy_count:
        st.info(
            f"{legacy_count} of {len(batch_results)} comparisons are legacy single-pass records, "
            "judged in one response order only. They carry no position-stability evidence."
        )
    st.markdown("**Averaged Category Summary**")
    st.table(batch_average_rows(batch_results))
    rows = []
    for index, entry in enumerate(batch_results, start=1):
        item = entry["item"]
        batch_result = entry["result"]
        score_a = optional_score(batch_result.get("responses", {}).get("A", {}).get("overall")) or 0.0
        score_b = optional_score(batch_result.get("responses", {}).get("B", {}).get("overall")) or 0.0
        rows.append(
            {
                "#": index,
                "Title": item.get("title", ""),
                "Winner": winner_display(batch_result, item["model_name_a"], item["model_name_b"]),
                "Classification": classification_label(batch_result),
                "Stability": stability_label(batch_result),
                "Confidence": f"{int(batch_result.get('winner', {}).get('confidence', 0))}%",
                response_a_label: f"{score_a:.2f}",
                response_b_label: f"{score_b:.2f}",
                "Difference": format_difference(score_b - score_a),
            }
        )
    st.table(rows)

    for index, entry in enumerate(batch_results, start=1):
        item = entry["item"]
        batch_result = entry["result"]
        title = item.get("title") or f"Prompt {index}"
        with st.expander(f"Prompt {index}: {title}"):
            stability = result_stability(batch_result)
            # Any stability warning is shown, not only a full position-sensitive
            # flip - a large slot bias that didn't flip the label (minor
            # positional drift) is also surfaced here rather than staying silent.
            if stability and stability.get("warning"):
                st.warning(str(stability.get("warning") or POSITION_SENSITIVE_WARNING))
            elif is_legacy_single_pass(batch_result):
                st.info("Legacy single-pass record: judged in one response order only.")
            st.caption(
                f"Classification: {classification_label(batch_result)}"
                f" | Directional lean and raw scores are preserved below even for an Effective Tie."
            )
            st.table(score_summary_rows(batch_result))
            render_directional_pass_table(batch_result)
            category_agreement_info = batch_result.get("category_agreement")
            if isinstance(category_agreement_info, dict) and category_agreement_info:
                st.caption(
                    "Category agreement: "
                    f"{category_agreement_info.get('agreement_fraction', 1.0) * 100:.0f}% "
                    f"({category_agreement_info.get('agreeing', 0)} agreeing, "
                    f"{category_agreement_info.get('disagreeing', 0)} disagreeing, "
                    f"{category_agreement_info.get('tied', 0)} tied)"
                )
            verdict_consistency = batch_result.get("verdict_consistency")
            if isinstance(verdict_consistency, dict) and (
                verdict_consistency.get("conflict") or verdict_consistency.get("status") in ("repaired", "conflict")
            ):
                pass_warning = (
                    (verdict_consistency.get(FORWARD) or {}).get("warning")
                    if isinstance(verdict_consistency.get(FORWARD), dict)
                    else None
                ) or (
                    (verdict_consistency.get(REVERSE) or {}).get("warning")
                    if isinstance(verdict_consistency.get(REVERSE), dict)
                    else None
                ) or str(verdict_consistency.get("warning") or "The written verdict disagreed with the calculated numeric winner.")
                st.warning(f"Verdict/score conflict. {pass_warning}")
            st.markdown("**Final Verdict**")
            st.write(batch_result.get("final_verdict", ""))

    prompt_pack_metadata = st.session_state.get("last_prompt_pack_metadata") or active_benchmark_prompt_pack_metadata()
    fallback_endpoint_name = judge_endpoint.name if config and judge_endpoint else "No config loaded"
    judge_endpoint_name = st.session_state.get("last_judge_endpoint_name") or fallback_endpoint_name
    render_download_buttons(
        st.session_state.get(
            "last_json_payload",
            batch_json_payload(batch_results, rubric_name, judge_endpoint_name, judge_model, prompt_pack_metadata),
        )
    )
    persist_and_inject_form_state()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        if judging_active():
            st.session_state.judging_active = False
            render_judging_overlay(False)
        raise

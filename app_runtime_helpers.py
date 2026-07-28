import json
import os
import re

import requests


_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_SETTINGS_FILE = os.path.join(_BASE_DIR, "settings.json")


def _read_settings():
    try:
        with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def get_api_url():
    settings = _read_settings()
    port = settings.get("llama_args", {}).get("port", 8080)
    return f"http://127.0.0.1:{port}"


def substitute_placeholders(text, char_label, user_label):
    """Swap {{char}}/{{user}} (whitespace- and case-tolerant) for live names."""
    if not isinstance(text, str) or not text:
        return text
    if char_label:
        text = re.sub(r"\{\{\s*char\s*\}\}", char_label, text, flags=re.IGNORECASE)
    if user_label:
        text = re.sub(r"\{\{\s*user\s*\}\}", user_label, text, flags=re.IGNORECASE)
    return text


def get_stop_tokens(current_model=None):
    """Return stop tokens for the active local model/template."""
    settings = _read_settings()
    chat_template = (
        settings.get("llama_args", {})
        .get("chat_template", "chatml")
    )
    chat_template = str(chat_template or "chatml").strip().lower()
    model_name = (
        current_model
        or _detect_current_model()
        or settings.get("llama_last_model", "")
        or ""
    ).lower()

    is_gemma = "gemma" in model_name or chat_template == "jinja"
    is_qwen = "qwen" in model_name or chat_template == "qwen"

    if is_gemma:
        print("Using Gemma stop tokens (<end_of_turn>)", flush=True)
        return ["<end_of_turn>", "<start_of_turn>"]
    if is_qwen:
        print("Using Qwen stop tokens (<|im_end|>)", flush=True)
        return ["<|im_end|>", "<|im_start|>"]
    return ["<|im_end|>", "<|im_start|>"]


def _detect_current_model():
    try:
        response = requests.get(f"{get_api_url()}/v1/models", timeout=2)
        response.raise_for_status()
        data = response.json()
        models = data.get("data") or []
        if models:
            return models[0].get("id") or ""
    except Exception:
        pass
    return ""


ANTHROPIC_MODEL_SAMPLING_RULES = {
    "fable": {"allow": ["max_tokens", "stop_sequences"], "match": "contains"},
    "claude-opus-4-8": {"allow": ["max_tokens", "stop_sequences"]},
    "claude-opus-4-7": {"allow": ["max_tokens", "stop_sequences"]},
    "claude-sonnet-4-6": {"allow": ["temperature", "max_tokens", "stop_sequences"]},
    "claude-opus-4-6": {"allow": ["temperature", "max_tokens", "stop_sequences"]},
    "claude-haiku-4-5": {"allow": ["temperature", "max_tokens", "stop_sequences"]},
}
DEFAULT_ANTHROPIC_ALLOW = ["temperature", "max_tokens", "stop_sequences"]


def _anthropic_allow_for(model_id):
    model_id = (model_id or "").strip().lower()
    if model_id in ANTHROPIC_MODEL_SAMPLING_RULES:
        return ANTHROPIC_MODEL_SAMPLING_RULES[model_id]["allow"]
    best = None
    for key, rule in ANTHROPIC_MODEL_SAMPLING_RULES.items():
        if rule.get("match") == "contains":
            continue
        if model_id.startswith(key) and (best is None or len(key) > len(best)):
            best = key
    if best:
        return ANTHROPIC_MODEL_SAMPLING_RULES[best]["allow"]
    for key, rule in ANTHROPIC_MODEL_SAMPLING_RULES.items():
        if rule.get("match") == "contains" and key in model_id:
            return rule["allow"]
    return DEFAULT_ANTHROPIC_ALLOW


def supports_temperature(model_id):
    return "temperature" in _anthropic_allow_for(model_id)


OPENAI_MODEL_RULES = {
    "gpt-5": {"token_param": "max_completion_tokens", "sampling": False},
    "o1": {"token_param": "max_completion_tokens", "sampling": False},
    "o3": {"token_param": "max_completion_tokens", "sampling": False},
    "o4": {"token_param": "max_completion_tokens", "sampling": False},
}
_OPENAI_DEFAULT_RULE = {"token_param": "max_tokens", "sampling": True}


def openai_caps_for(model_id):
    """Resolve OpenAI param rules: exact match, longest prefix, then default."""
    if not model_id:
        return _OPENAI_DEFAULT_RULE
    model = model_id.strip().lower()
    if "search-preview" in model:
        return {"token_param": "max_tokens", "sampling": False}
    if model in OPENAI_MODEL_RULES:
        return OPENAI_MODEL_RULES[model]
    best = None
    for prefix, rule in OPENAI_MODEL_RULES.items():
        if model.startswith(prefix) and (best is None or len(prefix) > len(best)):
            best = prefix
    return OPENAI_MODEL_RULES[best] if best else _OPENAI_DEFAULT_RULE

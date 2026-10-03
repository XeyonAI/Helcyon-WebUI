"""Glue between settings.json / secrets and the provider classes.

Keeps app.py's integration to a handful of one-line calls: it never builds a
provider or an HTTP request for a non-built-in backend itself.
"""
import json
import os
import re
import threading

import secrets_store

from . import registry
from .base import Provider, ProviderError

PROVIDER_MODE = "provider"
_SETTINGS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "settings.json")

_ACTIVE = set()
_ACTIVE_LOCK = threading.Lock()


def load_settings():
    try:
        with open(_SETTINGS_FILE, "r", encoding="utf-8") as handle:
            return json.load(handle) or {}
    except Exception:
        return {}


def is_provider_mode(settings=None):
    settings = load_settings() if settings is None else settings
    return str(settings.get("backend_mode", "local")).lower() == PROVIDER_MODE


def provider_block(settings):
    block = settings.get("inference_provider")
    return block if isinstance(block, dict) else {}


def selected_provider_id(settings):
    return provider_block(settings).get("id") or "openai_compat"


def build_provider(settings=None, provider_id=None, overrides=None) -> Provider:
    settings = load_settings() if settings is None else settings
    block = provider_block(settings)
    pid = provider_id or selected_provider_id(settings)
    cfg = dict((block.get("configs") or {}).get(pid) or {})
    cfg.update(overrides or {})
    key = cfg.pop("api_key", None) or secrets_store.get_secret(secrets_store.provider_key_name(pid))
    return registry.create_provider(pid, cfg, api_key=key)


def active_provider(settings=None):
    """The provider for the current request, or None when the built-in path is active."""
    settings = load_settings() if settings is None else settings
    if not is_provider_mode(settings):
        return None
    return build_provider(settings)


def context_limit(settings=None, default=8192):
    """Context window to budget prompts against for the active provider."""
    settings = load_settings() if settings is None else settings
    cfg = (provider_block(settings).get("configs") or {}).get(selected_provider_id(settings)) or {}
    try:
        value = int(cfg.get("context_length") or 0)
    except (TypeError, ValueError):
        value = 0
    return value or default


# -- in-flight tracking so the Stop button can cancel a blocked read ----------
def track(provider):
    with _ACTIVE_LOCK:
        _ACTIVE.add(provider)


def untrack(provider):
    with _ACTIVE_LOCK:
        _ACTIVE.discard(provider)


def abort_active():
    with _ACTIVE_LOCK:
        providers = list(_ACTIVE)
    for provider in providers:
        try:
            provider.abort()
        except Exception:
            pass
    return len(providers)


def stream_text(provider, messages, sampling, should_abort=None):
    """Stream chunks, honouring a polled abort flag and registering for abort_active()."""
    track(provider)
    try:
        for chunk in provider.stream_chat(messages, sampling):
            if should_abort and should_abort():
                provider.abort()
                break
            yield chunk
    finally:
        untrack(provider)


# -- requests-like shim for auxiliary llama-style calls -----------------------
class ProviderResponse:
    """Minimal stand-in for the requests.Response that aux callers expect."""

    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise ProviderError(f"HTTP {self.status_code}")


_SAMPLING_PASSTHROUGH = ("temperature", "top_p", "min_p", "top_k", "repeat_penalty",
                         "frequency_penalty", "presence_penalty", "typical_p")


def sampling_from_payload(payload):
    sampling = {k: payload[k] for k in _SAMPLING_PASSTHROUGH if k in payload}
    tokens = payload.get("max_tokens", payload.get("n_predict"))
    if tokens:
        sampling["max_tokens"] = tokens
    return sampling


def post_chat_completion(payload, settings=None):
    """Serve a llama-style /v1/chat/completions payload through the active provider.

    llama-only keys (cache_prompt, reasoning_format, chat_template_kwargs...) are
    ignored; the JSON schema is forwarded only if the provider can honour it.
    """
    provider = build_provider(settings)
    try:
        result = provider.complete(payload.get("messages") or [], sampling_from_payload(payload),
                                   response_format=payload.get("response_format"))
    except ProviderError as exc:
        return ProviderResponse(502, {"error": {"message": str(exc)}})
    return ProviderResponse(200, {"choices": [{
        "message": {"role": "assistant", "content": result["content"]},
        "finish_reason": result.get("finish_reason") or "stop"}]})


def complete_text(system, user, sampling, settings=None):
    """One system+user turn through the active provider; returns the text."""
    provider = build_provider(settings)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    return provider.complete(messages, sampling)["content"].strip()


_CHATML = re.compile(r"<\|im_start\|>(system|user|assistant)\n(.*?)(?:<\|im_end\|>|$)", re.DOTALL)


def chatml_to_messages(prompt):
    """Convert a raw ChatML prompt (used by the legacy /completion callers) to chat messages.

    A trailing empty assistant header (the generation prompt) is dropped.
    """
    messages = [{"role": role, "content": body.strip()} for role, body in _CHATML.findall(prompt or "")]
    while messages and messages[-1]["role"] == "assistant" and not messages[-1]["content"]:
        messages.pop()
    return messages or [{"role": "user", "content": (prompt or "").strip()}]


def complete_chatml(prompt, sampling, settings=None):
    provider = build_provider(settings)
    return provider.complete(chatml_to_messages(prompt), sampling)["content"].strip()

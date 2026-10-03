"""Provider abstraction for HWUI's non-built-in inference backends.

A Provider turns HWUI's OpenAI-style ``messages`` list plus a sampling dict into a
stream of text chunks. Everything that differs between backends (URLs, sampler
names, model enumeration, cancellation, vision encoding) lives in a Provider
subclass or an OpenAI-compatible *preset*, never in app.py conditionals.

Capabilities describe what a backend can genuinely do. The UI greys controls out
and runtime code degrades (drops sampler keys, strips images) from these flags.
"""
from dataclasses import dataclass, field, asdict
from typing import Dict, FrozenSet, Iterator, List, Optional, Tuple

import requests

CONNECT_TIMEOUT = 10
# Streaming reads may legitimately stall while a big prompt is evaluated.
STREAM_READ_TIMEOUT = 600
LIST_TIMEOUT = (CONNECT_TIMEOUT, 15)


class ProviderError(RuntimeError):
    """A backend refused or failed a request. The message is safe to show."""


@dataclass(frozen=True)
class Capabilities:
    streaming: bool = True
    cancellation: bool = True          # can stop generation server-side or by closing the socket
    model_enumeration: bool = True
    context_detect: bool = False       # can report the loaded model's context length
    vision: bool = False               # accepts image input (model dependent; see detect_vision)
    tools: bool = False                # native tool calling (HWUI prompt-level features do not need it)
    structured_output: bool = False    # JSON-schema constrained output
    token_count: bool = False          # exact server-side tokenizer
    api_key: str = "optional"          # "none" | "optional" | "required"
    sampling_keys: FrozenSet[str] = field(default_factory=frozenset)

    def as_dict(self):
        data = asdict(self)
        data["sampling_keys"] = sorted(self.sampling_keys)
        return data


# HWUI sampling keys every OpenAI-style backend understands.
STANDARD_SAMPLING = frozenset({
    "temperature", "max_tokens", "top_p", "frequency_penalty", "presence_penalty", "stop",
})


@dataclass
class ProviderConfig:
    base_url: str = ""
    model: str = ""
    context_length: int = 0            # 0 = unknown / auto-detect
    api_key: str = ""                  # injected from secrets_store at runtime, never persisted here
    extra: Dict[str, object] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data, api_key=""):
        data = data or {}
        try:
            ctx = int(data.get("context_length") or 0)
        except (TypeError, ValueError):
            ctx = 0
        return cls(
            base_url=str(data.get("base_url") or "").strip().rstrip("/"),
            model=str(data.get("model") or "").strip(),
            context_length=max(ctx, 0),
            api_key=(api_key or "").strip(),
            extra={k: v for k, v in data.items()
                   if k not in ("base_url", "model", "context_length", "api_key")},
        )


class Provider:
    """Base class. Subclasses override the transport-specific methods."""

    id = "base"
    label = "Base"
    default_base_url = ""
    capabilities = Capabilities()
    # HWUI key -> wire key, for extension samplers a backend accepts beyond STANDARD_SAMPLING.
    sampling_map: Dict[str, str] = {}

    def __init__(self, config: ProviderConfig):
        self.config = config
        self._active = None  # in-flight streaming response, for abort()

    # -- helpers -----------------------------------------------------------
    @property
    def base_url(self):
        return self.config.base_url or self.default_base_url

    def headers(self):
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = "Bearer " + self.config.api_key
        return headers

    def map_sampling(self, sampling) -> Dict[str, object]:
        """Whitelist and rename sampler keys; anything unsupported is dropped."""
        out = {}
        sampling = sampling or {}
        for key, value in sampling.items():
            if value is None:
                continue
            if key in STANDARD_SAMPLING and key in self.capabilities.sampling_keys:
                out[key] = value
            elif key in self.sampling_map and key in self.capabilities.sampling_keys:
                out[self.sampling_map[key]] = value
        return out

    def prepare_messages(self, messages) -> Tuple[List[dict], bool]:
        """Return (messages, images_dropped). Strips image blocks when vision is unsupported."""
        if self.capabilities.vision:
            return list(messages), False
        dropped = False
        cleaned = []
        for message in messages:
            content = message.get("content")
            if isinstance(content, list):
                text_parts = [p.get("text", "") for p in content
                              if isinstance(p, dict) and p.get("type") == "text"]
                if len(text_parts) != len(content):
                    dropped = True
                message = dict(message, content=" ".join(t for t in text_parts if t))
            cleaned.append(message)
        return cleaned, dropped

    # -- interface ---------------------------------------------------------
    def list_models(self) -> List[dict]:
        raise NotImplementedError

    def context_length(self) -> Optional[int]:
        """Configured value wins; otherwise ask the backend; otherwise None."""
        if self.config.context_length:
            return self.config.context_length
        return self.detect_context_length()

    def detect_context_length(self) -> Optional[int]:
        return None

    def stream_chat(self, messages, sampling=None) -> Iterator[str]:
        raise NotImplementedError

    def complete(self, messages, sampling=None, response_format=None) -> dict:
        """Non-streaming call. Returns {"content": str, "finish_reason": str|None}."""
        parts = []
        for chunk in self.stream_chat(messages, sampling):
            parts.append(chunk)
        return {"content": "".join(parts), "finish_reason": None}

    def abort(self):
        """Stop the in-flight generation. Always safe to call."""
        response, self._active = self._active, None
        if response is not None:
            try:
                response.close()
            except Exception:
                pass

    def health(self) -> Tuple[bool, str]:
        try:
            models = self.list_models()
            return True, f"{len(models)} model(s) reported"
        except Exception as exc:
            return False, str(exc)

    def count_tokens(self, text) -> Optional[int]:
        return None

    def describe(self):
        return {"id": self.id, "label": self.label, "default_base_url": self.default_base_url,
                "capabilities": self.capabilities.as_dict()}


def http_error(response, what):
    body = ""
    try:
        body = response.text[:300]
    except Exception:
        pass
    return ProviderError(f"{what} returned HTTP {response.status_code}: {body or 'no body'}")


def get_json(url, headers=None, timeout=LIST_TIMEOUT):
    try:
        response = requests.get(url, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise ProviderError(f"Cannot reach {url}: {exc}") from exc
    if response.status_code != 200:
        raise http_error(response, url)
    try:
        return response.json()
    except ValueError as exc:
        raise ProviderError(f"{url} did not return JSON") from exc

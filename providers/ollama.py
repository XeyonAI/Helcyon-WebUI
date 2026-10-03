"""Ollama provider: native /api/chat (NDJSON streaming), not its /v1 shim.

Bespoke because Ollama (a) streams newline-delimited JSON, not SSE, (b) takes
sampler settings under ``options`` with its own names, (c) takes images as bare
base64 in ``images`` rather than OpenAI image_url blocks, and (d) silently uses a
tiny default context unless ``num_ctx`` is sent.
"""
import json
import re
from typing import Iterator, List

import requests

from .base import (CONNECT_TIMEOUT, STREAM_READ_TIMEOUT, Capabilities, Provider, ProviderError,
                   get_json, http_error)

_DATA_URL = re.compile(r"^data:[^;,]+;base64,(.*)$", re.DOTALL)
DEFAULT_NUM_CTX = 8192


def to_ollama_messages(messages) -> List[dict]:
    """Flatten OpenAI content blocks; move image data URLs into the `images` list."""
    out = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            out.append({"role": message.get("role", "user"), "content": content or ""})
            continue
        text, images = [], []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                text.append(part.get("text", ""))
            elif part.get("type") == "image_url":
                url = (part.get("image_url") or {}).get("url", "")
                match = _DATA_URL.match(url)
                if match:
                    images.append(match.group(1))
        item = {"role": message.get("role", "user"), "content": " ".join(t for t in text if t)}
        if images:
            item["images"] = images
        out.append(item)
    return out


class OllamaProvider(Provider):
    id = "ollama"
    label = "Ollama"
    default_base_url = "http://127.0.0.1:11434"
    capabilities = Capabilities(
        context_detect=True, vision=True, tools=True, structured_output=True, token_count=False,
        api_key="none",
        sampling_keys=frozenset({
            "temperature", "max_tokens", "top_p", "top_k", "min_p", "repeat_penalty",
            "frequency_penalty", "presence_penalty", "typical_p", "stop",
        }),
    )
    # Ollama option names.
    _OPTION_NAMES = {"max_tokens": "num_predict", "repeat_penalty": "repeat_penalty"}

    @property
    def base_url(self):
        url = self.config.base_url or self.default_base_url
        # Tolerate a user pasting the OpenAI-style URL.
        return url[:-3] if url.endswith("/v1") else url

    def map_sampling(self, sampling):
        options = {}
        for key, value in (sampling or {}).items():
            if value is None or key not in self.capabilities.sampling_keys:
                continue
            options[self._OPTION_NAMES.get(key, key)] = value
        return options

    def list_models(self) -> List[dict]:
        data = get_json(self.base_url + "/api/tags", self.headers())
        return [{"id": m.get("name") or m.get("model"), "size": m.get("size")}
                for m in data.get("models", []) if m.get("name") or m.get("model")]

    def _show(self):
        try:
            response = requests.post(self.base_url + "/api/show", json={"model": self.config.model},
                                     headers=self.headers(), timeout=(CONNECT_TIMEOUT, 15))
            return response.json() if response.status_code == 200 else {}
        except (requests.RequestException, ValueError):
            return {}

    def detect_context_length(self):
        info = self._show().get("model_info") or {}
        for key, value in info.items():
            if key.endswith(".context_length") and isinstance(value, int):
                return value
        return None

    def model_supports_vision(self):
        return "vision" in (self._show().get("capabilities") or [])

    def _payload(self, messages, sampling, stream, response_format=None):
        prepared, _ = self.prepare_messages(messages)
        options = self.map_sampling(sampling)
        # Never let Ollama fall back to its small default window.
        options["num_ctx"] = self.config.context_length or self.detect_context_length() or DEFAULT_NUM_CTX
        payload = {"model": self.config.model, "messages": to_ollama_messages(prepared),
                   "stream": bool(stream), "options": options}
        if response_format and isinstance(response_format, dict):
            schema = (response_format.get("json_schema") or {}).get("schema")
            payload["format"] = schema or "json"
        keep_alive = self.config.extra.get("keep_alive")
        if keep_alive:
            payload["keep_alive"] = keep_alive
        return payload

    def _post(self, payload, stream):
        if not self.config.model:
            raise ProviderError("No Ollama model selected")
        try:
            return requests.post(self.base_url + "/api/chat", headers=self.headers(), json=payload,
                                 stream=stream, timeout=(CONNECT_TIMEOUT, STREAM_READ_TIMEOUT))
        except requests.RequestException as exc:
            raise ProviderError(f"Cannot reach {self.base_url}: {exc}") from exc

    def stream_chat(self, messages, sampling=None) -> Iterator[str]:
        response = self._post(self._payload(messages, sampling, True), stream=True)
        if response.status_code != 200:
            raise http_error(response, "Ollama chat")
        self._active = response
        try:
            for raw in response.iter_lines(chunk_size=1):
                if not raw:
                    continue
                try:
                    event = json.loads(raw)
                except ValueError:
                    continue
                if event.get("error"):
                    raise ProviderError(str(event["error"]))
                text = (event.get("message") or {}).get("content")
                if text:
                    yield text
                if event.get("done"):
                    break
        except requests.RequestException:
            if self._active is not None:
                raise
        finally:
            self._active = None
            try:
                response.close()
            except Exception:
                pass

    def complete(self, messages, sampling=None, response_format=None) -> dict:
        response = self._post(self._payload(messages, sampling, False, response_format), stream=False)
        if response.status_code != 200:
            raise http_error(response, "Ollama chat")
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("Unexpected Ollama response") from exc
        return {"content": (data.get("message") or {}).get("content") or "",
                "finish_reason": data.get("done_reason")}

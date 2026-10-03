"""Generic OpenAI-compatible provider plus presets for named products.

LM Studio, text-generation-webui and a plain OpenAI-compatible endpoint share this
implementation. A preset only declares defaults, extension sampler names and
quirks; bespoke behaviour belongs in a dedicated module (ollama.py, koboldcpp.py).
"""
import dataclasses
import json
from typing import Iterator, List, Optional

import requests

from .base import (CONNECT_TIMEOUT, STANDARD_SAMPLING, STREAM_READ_TIMEOUT, Capabilities,
                   Provider, ProviderConfig, ProviderError, get_json, http_error)


def parse_sse_line(raw):
    """Decode one SSE line. Returns the JSON object, "[DONE]", or None for noise."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    line = raw.strip()
    if not line or line.startswith(":"):
        return None
    if line.startswith("data:"):
        line = line[5:].strip()
    if line == "[DONE]":
        return "[DONE]"
    try:
        return json.loads(line)
    except ValueError:
        return None


class OpenAICompatProvider(Provider):
    id = "openai_compat"
    label = "OpenAI-compatible endpoint"
    default_base_url = "http://127.0.0.1:8000/v1"
    capabilities = Capabilities(
        context_detect=False, vision=True, tools=True, structured_output=True,
        api_key="optional", sampling_keys=STANDARD_SAMPLING,
    )

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        # A user can override vision for a given backend/model combination.
        override = config.extra.get("vision")
        if isinstance(override, bool) and override != self.capabilities.vision:
            self.capabilities = dataclasses.replace(self.capabilities, vision=override)

    # -- URLs (presets may override) ---------------------------------------
    def models_url(self):
        return self.base_url + "/models"

    def chat_url(self):
        return self.base_url + "/chat/completions"

    def parse_models(self, data) -> List[dict]:
        items = data.get("data") if isinstance(data, dict) else data
        models = []
        for item in items or []:
            if isinstance(item, dict) and item.get("id"):
                models.append({"id": str(item["id"]), "owned_by": item.get("owned_by", "")})
            elif isinstance(item, str):
                models.append({"id": item})
        return models

    def list_models(self) -> List[dict]:
        return self.parse_models(get_json(self.models_url(), self.headers()))

    # -- payload -----------------------------------------------------------
    def build_payload(self, messages, sampling, stream, response_format=None):
        prepared, _ = self.prepare_messages(messages)
        payload = {"messages": prepared, "stream": bool(stream)}
        if self.config.model:
            payload["model"] = self.config.model
        payload.update(self.map_sampling(sampling))
        if response_format and self.capabilities.structured_output:
            payload["response_format"] = response_format
        return payload

    def _post(self, payload, stream):
        try:
            return requests.post(self.chat_url(), headers=self.headers(), json=payload, stream=stream,
                                 timeout=(CONNECT_TIMEOUT, STREAM_READ_TIMEOUT))
        except requests.RequestException as exc:
            raise ProviderError(f"Cannot reach {self.base_url}: {exc}") from exc

    # -- generation --------------------------------------------------------
    def stream_chat(self, messages, sampling=None) -> Iterator[str]:
        payload = self.build_payload(messages, sampling, stream=True)
        self.before_request(payload)
        response = self._post(payload, stream=True)
        if response.status_code != 200:
            raise http_error(response, "Chat request")
        self._active = response
        try:
            for raw in response.iter_lines(chunk_size=1):
                event = parse_sse_line(raw)
                if event is None:
                    continue
                if event == "[DONE]":
                    break
                if isinstance(event, dict) and event.get("error"):
                    err = event["error"]
                    raise ProviderError(err.get("message") if isinstance(err, dict) else str(err))
                choices = event.get("choices") or [{}]
                # reasoning_content is deliberately not yielded: only the visible answer is chat text.
                text = (choices[0].get("delta") or {}).get("content")
                if text:
                    yield text
        except requests.RequestException:
            # abort() closes the socket under us; that is a clean stop, not an error.
            if self._active is not None:
                raise
        finally:
            self._active = None
            try:
                response.close()
            except Exception:
                pass

    def complete(self, messages, sampling=None, response_format=None) -> dict:
        payload = self.build_payload(messages, sampling, stream=False, response_format=response_format)
        self.before_request(payload)
        response = self._post(payload, stream=False)
        if response.status_code == 400 and "response_format" in payload:
            # Backend rejected schema-constrained output: degrade to unconstrained text.
            payload.pop("response_format")
            response = self._post(payload, stream=False)
        if response.status_code != 200:
            raise http_error(response, "Chat request")
        try:
            choice = response.json()["choices"][0]
        except (ValueError, KeyError, IndexError) as exc:
            raise ProviderError("Unexpected chat response shape") from exc
        return {"content": (choice.get("message") or {}).get("content") or "",
                "finish_reason": choice.get("finish_reason")}

    def before_request(self, payload):
        """Hook for presets that must tweak the final payload."""


class GenericOpenAIProvider(OpenAICompatProvider):
    id = "openai_compat"


class LMStudioProvider(OpenAICompatProvider):
    id = "lmstudio"
    label = "LM Studio"
    default_base_url = "http://127.0.0.1:1234/v1"
    capabilities = Capabilities(
        context_detect=True, vision=True, tools=True, structured_output=True, api_key="optional",
        sampling_keys=STANDARD_SAMPLING | {"top_k", "min_p", "repeat_penalty"},
    )
    sampling_map = {"repeat_penalty": "repeat_penalty", "top_k": "top_k", "min_p": "min_p"}

    def _native_models(self) -> Optional[list]:
        # LM Studio's REST API reports loaded state, type and context; best effort only.
        root = self.base_url[:-3] if self.base_url.endswith("/v1") else self.base_url
        try:
            data = get_json(root + "/api/v0/models", self.headers())
        except ProviderError:
            return None
        return data.get("data") if isinstance(data, dict) else None

    def list_models(self) -> List[dict]:
        native = self._native_models()
        if native is None:
            return super().list_models()
        models = []
        for item in native:
            if item.get("type") in ("embeddings", "embedding"):
                continue  # not chat models
            models.append({"id": item.get("id", ""), "loaded": item.get("state") == "loaded",
                           "context_length": item.get("loaded_context_length") or item.get("max_context_length"),
                           "vision": item.get("type") == "vlm"})
        return [m for m in models if m["id"]]

    def detect_context_length(self):
        for model in self._native_models() or []:
            if model.get("id") == self.config.model:
                return model.get("loaded_context_length") or model.get("max_context_length")
        return None


class TextGenWebUIProvider(OpenAICompatProvider):
    id = "textgen_webui"
    label = "text-generation-webui"
    default_base_url = "http://127.0.0.1:5000/v1"
    capabilities = Capabilities(
        context_detect=True, vision=False, tools=False, structured_output=False, api_key="optional",
        sampling_keys=STANDARD_SAMPLING | {
            "top_k", "min_p", "repeat_penalty", "typical_p", "dry_multiplier", "dry_base",
            "dry_allowed_length", "xtc_probability", "xtc_threshold", "top_n_sigma",
        },
    )
    sampling_map = {
        "top_k": "top_k", "min_p": "min_p", "repeat_penalty": "repetition_penalty",
        "typical_p": "typical_p", "dry_multiplier": "dry_multiplier", "dry_base": "dry_base",
        "dry_allowed_length": "dry_allowed_length", "xtc_probability": "xtc_probability",
        "xtc_threshold": "xtc_threshold", "top_n_sigma": "top_n_sigma",
    }

    def detect_context_length(self):
        root = self.base_url[:-3] if self.base_url.endswith("/v1") else self.base_url
        try:
            info = get_json(root + "/v1/internal/model/info", self.headers())
        except ProviderError:
            return None
        ctx = (info.get("loader_settings") or {}).get("n_ctx") or info.get("max_seq_len")
        return int(ctx) if ctx else None

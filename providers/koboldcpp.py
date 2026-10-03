"""KoboldCpp provider.

Generation uses KoboldCpp's OpenAI-compatible /v1 endpoint (it applies the right
chat template server-side, which HWUI cannot know). The bespoke parts are the
Kobold-native control endpoints: true context length, the loaded model name, an
explicit server-side abort (closing the socket alone does not stop KoboldCpp's
generation), exact token counting, and its extra samplers.
"""
import secrets
from typing import List, Optional

import requests

from .base import CONNECT_TIMEOUT, STANDARD_SAMPLING, Capabilities, ProviderError, get_json
from .openai_compat import OpenAICompatProvider


class KoboldCppProvider(OpenAICompatProvider):
    id = "koboldcpp"
    label = "KoboldCpp"
    default_base_url = "http://127.0.0.1:5001/v1"
    capabilities = Capabilities(
        context_detect=True, vision=False, tools=False, structured_output=False, token_count=True,
        api_key="optional",
        sampling_keys=STANDARD_SAMPLING | {
            "top_k", "min_p", "repeat_penalty", "typical_p", "dry_multiplier", "dry_base",
            "dry_allowed_length", "xtc_probability", "xtc_threshold", "top_n_sigma",
        },
    )
    sampling_map = {
        "top_k": "top_k", "min_p": "min_p", "repeat_penalty": "rep_pen", "typical_p": "typical",
        "dry_multiplier": "dry_multiplier", "dry_base": "dry_base",
        "dry_allowed_length": "dry_allowed_length", "xtc_probability": "xtc_probability",
        "xtc_threshold": "xtc_threshold", "top_n_sigma": "top_n_sigma",
    }

    def __init__(self, config):
        super().__init__(config)
        self._genkey = None

    @property
    def native_root(self):
        url = self.base_url
        return url[:-3] if url.endswith("/v1") else url

    def before_request(self, payload):
        # KoboldCpp's abort endpoint addresses a generation by this key.
        self._genkey = "HWUI" + secrets.token_hex(6)
        payload["genkey"] = self._genkey

    def list_models(self) -> List[dict]:
        try:
            return super().list_models()
        except ProviderError:
            data = get_json(self.native_root + "/api/v1/model", self.headers())
            name = data.get("result")
            return [{"id": name}] if name else []

    def detect_context_length(self) -> Optional[int]:
        try:
            data = get_json(self.native_root + "/api/extra/true_max_context_length", self.headers())
        except ProviderError:
            return None
        return int(data["value"]) if isinstance(data.get("value"), int) else None

    def abort(self):
        genkey = self._genkey
        super().abort()
        if genkey:
            try:
                requests.post(self.native_root + "/api/extra/abort", json={"genkey": genkey},
                              headers=self.headers(), timeout=(CONNECT_TIMEOUT, 5))
            except requests.RequestException:
                pass

    def count_tokens(self, text) -> Optional[int]:
        try:
            response = requests.post(self.native_root + "/api/extra/tokencount", json={"prompt": text},
                                     headers=self.headers(), timeout=(CONNECT_TIMEOUT, 10))
            if response.status_code == 200:
                return int(response.json().get("value"))
        except (requests.RequestException, ValueError, TypeError):
            pass
        return None

"""Provider registry: the single place that knows which backends exist."""
from .base import Capabilities, Provider, ProviderConfig, ProviderError, STANDARD_SAMPLING
from .koboldcpp import KoboldCppProvider
from .ollama import OllamaProvider
from .openai_compat import GenericOpenAIProvider, LMStudioProvider, TextGenWebUIProvider

# Built-in llama.cpp is NOT a Provider instance: app.py owns that path unchanged.
# It is described here only so the UI can render one uniform backend list.
BUILTIN_ID = "llamacpp"
BUILTIN_DESCRIPTOR = {
    "id": BUILTIN_ID,
    "label": "Built-in llama.cpp (default)",
    "default_base_url": "",
    "builtin": True,
    "capabilities": Capabilities(
        context_detect=True, vision=True, tools=False, structured_output=True, token_count=True,
        api_key="none",
        sampling_keys=STANDARD_SAMPLING | {"top_k", "min_p", "repeat_penalty", "typical_p",
                                           "dry_multiplier", "xtc_probability", "top_n_sigma"},
    ).as_dict(),
}

PROVIDERS = {
    cls.id: cls for cls in (
        GenericOpenAIProvider, LMStudioProvider, TextGenWebUIProvider, OllamaProvider, KoboldCppProvider,
    )
}


def provider_ids():
    return list(PROVIDERS)


def list_descriptors():
    items = [BUILTIN_DESCRIPTOR]
    for cls in PROVIDERS.values():
        items.append(dict(id=cls.id, label=cls.label, default_base_url=cls.default_base_url,
                          builtin=False, capabilities=cls.capabilities.as_dict()))
    return items


def create_provider(provider_id, config_dict=None, api_key="") -> Provider:
    cls = PROVIDERS.get(provider_id)
    if cls is None:
        raise ProviderError(f"Unknown provider {provider_id!r}")
    return cls(ProviderConfig.from_dict(config_dict, api_key=api_key))

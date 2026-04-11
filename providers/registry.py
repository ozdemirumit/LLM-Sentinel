"""
Provider registry — maps provider_type strings to adapter classes.
"""

from __future__ import annotations

from typing import Any

from providers.base import BaseLLMProvider

# Lazy imports to avoid loading all SDKs at startup
_PROVIDER_MAP: dict[str, str] = {
    "anthropic": "providers.anthropic_provider.AnthropicProvider",
    "openai": "providers.openai_provider.OpenAIProvider",
    "azure_openai": "providers.azure_openai_provider.AzureOpenAIProvider",
    "gemini": "providers.gemini_provider.GeminiProvider",
    "bedrock": "providers.bedrock_provider.BedrockProvider",
    "groq": "providers.groq_provider.GroqProvider",
    "mistral": "providers.mistral_provider.MistralProvider",
    "ollama": "providers.ollama_provider.OllamaProvider",
    "openai_compatible": "providers.openai_compatible_provider.OpenAICompatibleProvider",
}

_cache: dict[str, type[BaseLLMProvider]] = {}


def _import_class(dotted_path: str) -> type[BaseLLMProvider]:
    """Import a class from a dotted module path."""
    if dotted_path in _cache:
        return _cache[dotted_path]

    module_path, class_name = dotted_path.rsplit(".", 1)
    import importlib
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    _cache[dotted_path] = cls
    return cls


def get_provider(
    provider_type: str,
    api_key: str = "",
    base_url: str = "",
    config: dict[str, Any] | None = None,
    timeout: int = 120,
) -> BaseLLMProvider:
    """
    Instantiate a provider adapter by type.

    Args:
        provider_type: One of the registered provider types.
        api_key: API key for the provider.
        base_url: Base URL override.
        config: Additional configuration dict.
        timeout: Request timeout in seconds.

    Returns:
        An instance of the appropriate BaseLLMProvider subclass.

    Raises:
        ValueError: If provider_type is not registered.
    """
    dotted = _PROVIDER_MAP.get(provider_type)
    if dotted is None:
        raise ValueError(
            f"Unknown provider type: '{provider_type}'. "
            f"Available: {list(_PROVIDER_MAP.keys())}"
        )

    cls = _import_class(dotted)
    return cls(api_key=api_key, base_url=base_url, config=config, timeout=timeout)


def list_provider_types() -> list[str]:
    """Return all registered provider type names."""
    return list(_PROVIDER_MAP.keys())

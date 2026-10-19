"""Provider adapters and the registry that builds them."""

from __future__ import annotations

from .anthropic import AnthropicProvider
from .base import (
    FALLBACK_KINDS,
    RETRYABLE_KINDS,
    ModelInfo,
    Provider,
    ProviderConfig,
    ProviderError,
    ProviderResponse,
    ToolCall,
    Usage,
    classify_http,
)
from .openai_compat import OpenAICompatibleProvider, OpenAIProvider, OpenRouterProvider

PROVIDER_CLASSES: dict[str, type[Provider]] = {
    "openrouter": OpenRouterProvider,
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "openai_compatible": OpenAICompatibleProvider,
}


def build_provider(config: ProviderConfig, api_key: str, transport=None, timeout: int = 120) -> Provider:
    cls = PROVIDER_CLASSES.get(config.kind)
    if cls is None:
        raise ProviderError("bad_request", f"unsupported provider kind: {config.kind}")
    return cls(config, api_key, transport=transport, timeout=timeout)


__all__ = [
    "FALLBACK_KINDS", "RETRYABLE_KINDS", "ModelInfo", "Provider", "ProviderConfig",
    "ProviderError", "ProviderResponse", "ToolCall", "Usage", "classify_http",
    "build_provider", "PROVIDER_CLASSES",
]

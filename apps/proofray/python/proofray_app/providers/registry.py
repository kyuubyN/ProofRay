from __future__ import annotations

from .base import Provider, ProviderConfig, ProviderKind


def create_provider(config: ProviderConfig) -> Provider:
    if config.kind == ProviderKind.GEMINI:
        from .gemini_provider import GeminiProvider
        return GeminiProvider(config)
    if config.kind == ProviderKind.ANTHROPIC:
        from .anthropic_provider import AnthropicProvider
        return AnthropicProvider(config)
    if config.kind in (ProviderKind.OPENAI, ProviderKind.OPENAI_COMPATIBLE):
        from .openai_provider import OpenAIProvider
        return OpenAIProvider(config)
    raise ValueError("unsupported provider kind")


__all__ = ["create_provider"]

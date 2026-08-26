"""
Providers package — model provider abstraction for Imperial Court ministers.

Exports:
    ModelProvider, ModelResponse, GenerationParams — base abstractions
    OpenAIProvider, AnthropicProvider, GoogleProvider — concrete providers
    ProviderRegistry, get_provider_registry — registry singleton
"""

from huanxin.court.providers.base import (
    GenerationParams,
    ModelProvider,
    ModelResponse,
)
from huanxin.court.providers.openai_provider import OpenAIProvider
from huanxin.court.providers.anthropic_provider import AnthropicProvider
from huanxin.court.providers.google_provider import GoogleProvider
from huanxin.court.providers.registry import (
    ProviderRegistry,
    get_provider_registry,
    reset_provider_registry,
)

__all__ = [
    "ModelProvider",
    "ModelResponse",
    "GenerationParams",
    "OpenAIProvider",
    "AnthropicProvider",
    "GoogleProvider",
    "ProviderRegistry",
    "get_provider_registry",
    "reset_provider_registry",
]

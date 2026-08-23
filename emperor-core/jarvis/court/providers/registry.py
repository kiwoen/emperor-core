"""
Provider Registry — binds ministers to model providers via config.

The registry reads environment variables to determine which
providers are available and creates the appropriate instances.

Environment variables:
    OPENAI_API_KEY      — enables OpenAIProvider
    ANTHROPIC_API_KEY   — enables AnthropicProvider
    GOOGLE_API_KEY      — enables GoogleProvider
    DEEPSEEK_API_KEY    — enables DeepSeek (OpenAI-compatible)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from jarvis.court.providers.base import ModelProvider
from jarvis.court.providers.openai_provider import OpenAIProvider
from jarvis.court.providers.anthropic_provider import AnthropicProvider
from jarvis.court.providers.google_provider import GoogleProvider

logger = logging.getLogger("jarvis.court.providers.registry")


@dataclass
class ProviderConfig:
    """Configuration for a single minister's model provider.

    Each minister has a primary provider (matching their archetype)
    and an optional fallback (for cost/routing flexibility).
    """

    primary_model: str
    provider_type: str           # "openai" / "anthropic" / "google"
    api_key_env: str             # env var name
    base_url: Optional[str] = None  # for OpenAI-compatible endpoints
    # Fallback: if primary is unavailable, try this
    fallback_model: Optional[str] = None
    fallback_provider_type: Optional[str] = None
    fallback_api_key_env: Optional[str] = None
    fallback_base_url: Optional[str] = None


# ── Minister-to-model mapping ──────────────────────────────────────

MINISTER_PROVIDER_CONFIG: dict[str, ProviderConfig] = {
    "丞相": ProviderConfig(
        primary_model="gpt-5",
        provider_type="openai",
        api_key_env="OPENAI_API_KEY",
        fallback_model="gpt-4o",
        fallback_provider_type="openai",
        fallback_api_key_env="OPENAI_API_KEY",
    ),
    "御史大夫": ProviderConfig(
        primary_model="claude-sonnet-4-20250514",
        provider_type="anthropic",
        api_key_env="ANTHROPIC_API_KEY",
    ),
    "太史令": ProviderConfig(
        primary_model="gpt-4o",
        provider_type="openai",
        api_key_env="OPENAI_API_KEY",
        fallback_model="deepseek-chat",
        fallback_provider_type="openai",
        fallback_api_key_env="DEEPSEEK_API_KEY",
        fallback_base_url="https://api.deepseek.com/v1",
    ),
    "工部尚书": ProviderConfig(
        primary_model="deepseek-chat",
        provider_type="openai",
        api_key_env="DEEPSEEK_API_KEY",
        base_url="https://api.deepseek.com/v1",
        fallback_model="gpt-4o",
        fallback_provider_type="openai",
        fallback_api_key_env="OPENAI_API_KEY",
    ),
    "太常": ProviderConfig(
        primary_model="gemini-2.5-pro-exp-03-25",
        provider_type="google",
        api_key_env="GOOGLE_API_KEY",
        fallback_model="gpt-4o",
        fallback_provider_type="openai",
        fallback_api_key_env="OPENAI_API_KEY",
    ),
    "大司农": ProviderConfig(
        primary_model="deepseek-chat",
        provider_type="openai",
        api_key_env="DEEPSEEK_API_KEY",
        base_url="https://api.deepseek.com/v1",
    ),
    "太卜": ProviderConfig(
        primary_model="claude-sonnet-4-20250514",
        provider_type="anthropic",
        api_key_env="ANTHROPIC_API_KEY",
        fallback_model="o3-mini",
        fallback_provider_type="openai",
        fallback_api_key_env="OPENAI_API_KEY",
    ),
    "卫尉": ProviderConfig(
        primary_model="gpt-4o",
        provider_type="openai",
        api_key_env="OPENAI_API_KEY",
        fallback_model="claude-sonnet-4-20250514",
        fallback_provider_type="anthropic",
        fallback_api_key_env="ANTHROPIC_API_KEY",
    ),
}


class ProviderRegistry:
    """Creates and caches ModelProvider instances by minister name.

    The registry tries the primary provider first; if its API key
    is missing, it falls back to the fallback provider (if configured).
    If neither is available, the minister will operate in mock mode.
    """

    def __init__(self) -> None:
        self._providers: dict[str, Optional[ModelProvider]] = {}
        self._built = False

    def build(self) -> None:
        """Pre-build all providers. Called once at startup."""
        if self._built:
            return
        for minister_name, config in MINISTER_PROVIDER_CONFIG.items():
            self._providers[minister_name] = self._create_provider(config)
        self._built = True
        available = sum(1 for p in self._providers.values() if p is not None)
        logger.info(
            "[ProviderRegistry] Built %d providers (%d available with API keys)",
            len(self._providers), available,
        )

    def _create_provider(
        self, config: ProviderConfig
    ) -> Optional[ModelProvider]:
        """Try primary then fallback; return None if neither available."""
        provider = self._try_provider(
            config.provider_type,
            config.primary_model,
            config.api_key_env,
            config.base_url,
        )
        if provider is not None:
            return provider

        if config.fallback_model and config.fallback_provider_type:
            provider = self._try_provider(
                config.fallback_provider_type,
                config.fallback_model,
                config.fallback_api_key_env or "",
                config.fallback_base_url,
            )
            if provider is not None:
                return provider

        return None

    @staticmethod
    def _try_provider(
        provider_type: str,
        model: str,
        key_env: str,
        base_url: Optional[str] = None,
    ) -> Optional[ModelProvider]:
        """Create a provider if the required API key is present."""
        api_key = os.getenv(key_env, "").strip()
        if not api_key:
            return None

        if provider_type == "openai":
            return OpenAIProvider(
                model=model, api_key=api_key, base_url=base_url,
            )
        elif provider_type == "anthropic":
            return AnthropicProvider(
                model=model, api_key=api_key,
            )
        elif provider_type == "google":
            return GoogleProvider(
                model=model, api_key=api_key,
            )
        else:
            logger.warning("Unknown provider type: %s", provider_type)
            return None

    def get_provider(self, minister_name: str) -> Optional[ModelProvider]:
        """Get the provider for a minister (None = mock mode)."""
        if not self._built:
            self.build()
        return self._providers.get(minister_name)

    def get_status(self) -> dict[str, dict]:
        """Return availability status for all ministers."""
        result = {}
        for name, provider in self._providers.items():
            config = MINISTER_PROVIDER_CONFIG.get(name)
            result[name] = {
                "available": provider is not None and provider.is_available,
                "model": provider.model if provider else "mock",
                "primary": config.primary_model if config else "unknown",
            }
        return result


# ── Global singleton ────────────────────────────────────────────────

_registry: Optional[ProviderRegistry] = None


def get_provider_registry() -> ProviderRegistry:
    """Get or create the global ProviderRegistry singleton."""
    global _registry
    if _registry is None:
        _registry = ProviderRegistry()
        _registry.build()
    return _registry


def reset_provider_registry() -> None:
    """Reset the global registry (for testing)."""
    global _registry
    _registry = None

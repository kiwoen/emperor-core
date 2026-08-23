"""LLM configuration models.

Defines ``LLMConfig`` (pydantic) and ``ModelProvider`` enum for unified
LLM backend configuration across OpenAI / Anthropic / Ollama providers.
"""

from __future__ import annotations

import logging
import os
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger("jarvis.llm.config")


class ModelProvider(str, Enum):
    """Supported LLM backend providers."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"
    # Domestic / Xinchuang providers
    ZHIPU = "zhipu"       # 智谱 GLM
    BAIDU = "baidu"        # 百度文心 ERNIE
    ALIYUN = "aliyun"      # 阿里通义 Qwen
    XUNFEI = "xunfei"      # 讯飞星火 Spark
    HUAWEI = "huawei"      # 华为盘古 Pangu


class LLMConfig(BaseModel):
    """Configuration for a single LLM backend connection.

    Attributes:
        provider: Backend provider (openai / anthropic / ollama).
        model_name: Model identifier string (e.g. ``gpt-4o``, ``claude-3-opus-20240229``).
        api_key: API key for the provider. Auto-reads from env if empty.
        base_url: Optional custom base URL (e.g. for proxies / Ollama).
        temperature: Sampling temperature (0.0–2.0).
        max_tokens: Maximum tokens in the generated response.
        extra_params: Arbitrary extra kwargs forwarded to litellm.
    """

    provider: ModelProvider = Field(default=ModelProvider.OPENAI, description="LLM backend provider")
    model_name: str = Field(default="gpt-4o", description="Model identifier")
    api_key: str = Field(default="", description="API key (auto-reads from env if empty)")
    base_url: str = Field(default="", description="Optional custom base URL")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="Sampling temperature")
    max_tokens: int = Field(default=4096, gt=0, description="Maximum tokens in response")
    extra_params: dict[str, Any] = Field(default_factory=dict, description="Extra kwargs forwarded to litellm")

    # ── Cross-layer adapters ─────────────────────────────────────────
    # The canonical multi-backend implementation lives in jarvis.core.llm
    # (LLMManager + FREE_PROVIDERS registry + environment-based failover).
    # These adapters keep the pydantic ``LLMConfig`` API backward-compatible
    # (callers use ``model_name`` / ``ModelProvider``) while letting the two
    # LLM stacks share one source of truth for failover and free providers.

    @classmethod
    def from_env(cls, **overrides: Any) -> "LLMConfig":
        """Build a config from OPENAI_* environment variables.

        Mirrors ``jarvis.core.llm.LLMConfig.from_env`` so the emperor entry
        (which uses the pydantic stack) honours the same env contract as the
        domains main chain. ``overrides`` take precedence over env values.
        """
        provider_str = os.getenv("OPENAI_PROVIDER", "openai")
        try:
            provider = ModelProvider(provider_str)
        except ValueError:
            logger.warning("Unknown OPENAI_PROVIDER=%r, falling back to openai", provider_str)
            provider = ModelProvider.OPENAI
        return cls(
            provider=overrides.get("provider", provider),
            model_name=overrides.get("model_name", os.getenv("OPENAI_MODEL", "gpt-4o")),
            api_key=overrides.get("api_key", os.getenv("OPENAI_API_KEY", "")),
            base_url=overrides.get("base_url", os.getenv("OPENAI_BASE_URL", "")),
            temperature=float(overrides.get("temperature", os.getenv("OPENAI_TEMPERATURE", "0.7"))),
            max_tokens=int(overrides.get("max_tokens", os.getenv("OPENAI_MAX_TOKENS", "4096"))),
        )

    def to_core(self) -> Any:
        """Convert to a ``jarvis.core.llm.LLMConfig`` dataclass for delegation."""
        from jarvis.core.llm import LLMConfig as CoreLLMConfig

        return CoreLLMConfig(
            provider=self.provider.value,
            model=self.model_name,
            api_key=self.api_key,
            base_url=self.base_url,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )

    @classmethod
    def from_core(cls, core_cfg: Any) -> "LLMConfig":
        """Build a pydantic ``LLMConfig`` from a ``jarvis.core.llm.LLMConfig``."""
        return cls(
            provider=ModelProvider(core_cfg.provider),
            model_name=core_cfg.model,
            api_key=core_cfg.api_key,
            base_url=core_cfg.base_url,
            temperature=core_cfg.temperature,
            max_tokens=core_cfg.max_tokens,
        )

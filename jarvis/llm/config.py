"""LLM configuration models.

Defines ``LLMConfig`` (pydantic) and ``ModelProvider`` enum for unified
LLM backend configuration across OpenAI / Anthropic / Ollama providers.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


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

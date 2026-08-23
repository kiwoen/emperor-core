"""
Model Provider Base Class — abstraction layer for real LLM API calls.

Each minister gets a provider matching their archetype:
    - OpenAIProvider: GPT family, o-series
    - AnthropicProvider: Claude family
    - GoogleProvider: Gemini family

When API keys are not configured, ministers fall back to template-based
mock responses — the system remains fully functional without keys.

Providers are stateless wrappers around HTTP clients; all state
(temperature, system prompt) is passed per-call by the minister.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ModelResponse:
    """Unified response envelope for all providers."""

    text: str
    model: str
    tokens_used: int = 0
    finish_reason: str = "stop"
    # Provider-agnostic confidence estimate (0-1)
    confidence: float = 0.85


@dataclass
class GenerationParams:
    """Parameters passed per-generation by the minister.

    Each minister tunes these based on their decision_style and
    adaptive temperature from the self-evolution loop.
    """

    system_prompt: str = ""
    temperature: float = 0.7
    max_tokens: int = 2048
    # Additional kwargs forwarded to the underlying provider
    extra: dict = field(default_factory=dict)


class ModelProvider(ABC):
    """Abstract base for all model providers.

    Subclasses implement _generate() for the specific API.
    The base class handles the "no-key" fallback.
    """

    def __init__(self, model: str, api_key: Optional[str] = None) -> None:
        self.model = model
        self.api_key = api_key

    @property
    def is_available(self) -> bool:
        """Whether this provider can make real API calls."""
        return bool(self.api_key)

    async def generate(
        self,
        prompt: str,
        params: Optional[GenerationParams] = None,
    ) -> Optional[ModelResponse]:
        """Generate a response. Returns None if the provider is unavailable.

        Subclasses override _generate(); this method adds pre/post hooks.
        """
        if not self.is_available:
            return None
        params = params or GenerationParams()
        return await self._generate(prompt, params)

    @abstractmethod
    async def _generate(
        self, prompt: str, params: GenerationParams
    ) -> ModelResponse:
        """Provider-specific generation logic.

        Must raise on transport/API errors — the minister handles retries.
        """
        ...

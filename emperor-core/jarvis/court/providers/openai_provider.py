"""
OpenAI Provider — GPT family, o-series, and OpenAI-compatible endpoints.

Supports:
    - OpenAI official API (GPT-4o, o3-mini, etc.)
    - DeepSeek API (OpenAI-compatible: api.deepseek.com)
    - Any other OpenAI-compatible endpoint (via base_url override)
"""

from __future__ import annotations

import logging
from typing import Optional

from jarvis.court.providers.base import (
    GenerationParams,
    ModelProvider,
    ModelResponse,
)

logger = logging.getLogger("jarvis.court.providers.openai")


class OpenAIProvider(ModelProvider):
    """Provider for OpenAI and OpenAI-compatible APIs.

    Usage:
        # GPT-4o via OpenAI
        provider = OpenAIProvider(
            model="gpt-4o",
            api_key=os.getenv("OPENAI_API_KEY"),
        )

        # DeepSeek-V3 via DeepSeek
        provider = OpenAIProvider(
            model="deepseek-chat",
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            base_url="https://api.deepseek.com/v1",
        )
    """

    def __init__(
        self,
        model: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        super().__init__(model=model, api_key=api_key)
        self.base_url = base_url
        self._client = None

    def _get_client(self):
        """Lazy-init the OpenAI client."""
        if self._client is not None:
            return self._client
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise ImportError(
                "openai package is required. Install with: pip install openai"
            )
        kwargs: dict = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        self._client = AsyncOpenAI(**kwargs)
        return self._client

    async def _generate(
        self, prompt: str, params: GenerationParams
    ) -> ModelResponse:
        client = self._get_client()

        messages = []
        if params.system_prompt:
            messages.append({"role": "system", "content": params.system_prompt})
        messages.append({"role": "user", "content": prompt})

        extra = params.extra or {}
        # Build the API kwargs
        api_kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": params.temperature,
            "max_tokens": params.max_tokens,
        }
        # Pass through any provider-specific extra args (e.g. top_p)
        for key in ("top_p", "frequency_penalty", "presence_penalty"):
            if key in extra:
                api_kwargs[key] = extra[key]

        logger.debug(
            "[OpenAI:%s] Generating with temp=%.2f max_tokens=%d",
            self.model, params.temperature, params.max_tokens,
        )

        response = await client.chat.completions.create(**api_kwargs)

        choice = response.choices[0]
        return ModelResponse(
            text=choice.message.content or "",
            model=response.model,
            tokens_used=response.usage.total_tokens if response.usage else 0,
            finish_reason=choice.finish_reason or "stop",
            confidence=self._estimate_confidence(choice.finish_reason),
        )

    @staticmethod
    def _estimate_confidence(finish_reason: Optional[str]) -> float:
        """Heuristic confidence based on finish reason."""
        if finish_reason == "stop":
            return 0.88
        if finish_reason == "length":
            return 0.65  # truncated
        return 0.75

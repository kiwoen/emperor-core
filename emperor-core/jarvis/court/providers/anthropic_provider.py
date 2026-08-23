"""
Anthropic Provider — Claude family.

Supports Claude models via the official Anthropic API.
"""

from __future__ import annotations

import logging
from typing import Optional

from jarvis.court.providers.base import (
    GenerationParams,
    ModelProvider,
    ModelResponse,
)

logger = logging.getLogger("jarvis.court.providers.anthropic")


class AnthropicProvider(ModelProvider):
    """Provider for Anthropic Claude models.

    Usage:
        provider = AnthropicProvider(
            model="claude-sonnet-4-20250514",
            api_key=os.getenv("ANTHROPIC_API_KEY"),
        )
    """

    def __init__(
        self,
        model: str,
        api_key: Optional[str] = None,
    ) -> None:
        super().__init__(model=model, api_key=api_key)
        self._client = None

    def _get_client(self):
        """Lazy-init the Anthropic client."""
        if self._client is not None:
            return self._client
        try:
            from anthropic import AsyncAnthropic
        except ImportError:
            raise ImportError(
                "anthropic package is required. Install with: pip install anthropic"
            )
        self._client = AsyncAnthropic(api_key=self.api_key)
        return self._client

    async def _generate(
        self, prompt: str, params: GenerationParams
    ) -> ModelResponse:
        client = self._get_client()

        system = params.system_prompt if params.system_prompt else ""

        logger.debug(
            "[Anthropic:%s] Generating with temp=%.2f max_tokens=%d",
            self.model, params.temperature, params.max_tokens,
        )

        response = await client.messages.create(
            model=self.model,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            temperature=params.temperature,
            max_tokens=params.max_tokens,
        )

        # Extract text from the first content block
        text = ""
        if response.content:
            first_block = response.content[0]
            if hasattr(first_block, "text"):
                text = first_block.text

        return ModelResponse(
            text=text,
            model=response.model,
            tokens_used=(
                response.usage.input_tokens + response.usage.output_tokens
                if response.usage else 0
            ),
            finish_reason=response.stop_reason or "stop",
            confidence=0.89 if response.stop_reason == "end_turn" else 0.70,
        )

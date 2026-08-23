"""
Google Provider — Gemini family.

Uses the Google Generative AI SDK (google-genai).
"""

from __future__ import annotations

import logging
from typing import Optional

from jarvis.court.providers.base import (
    GenerationParams,
    ModelProvider,
    ModelResponse,
)

logger = logging.getLogger("jarvis.court.providers.google")


class GoogleProvider(ModelProvider):
    """Provider for Google Gemini models.

    Usage:
        provider = GoogleProvider(
            model="gemini-2.5-pro-exp-03-25",
            api_key=os.getenv("GOOGLE_API_KEY"),
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
        """Lazy-init the Google Generative AI client."""
        if self._client is not None:
            return self._client
        try:
            from google import genai
        except ImportError:
            raise ImportError(
                "google-genai package is required. Install with: pip install google-genai"
            )
        self._client = genai.Client(api_key=self.api_key)
        return self._client

    async def _generate(
        self, prompt: str, params: GenerationParams
    ) -> ModelResponse:
        client = self._get_client()

        full_prompt = prompt
        if params.system_prompt:
            full_prompt = f"[系统指令]\n{params.system_prompt}\n\n[用户]\n{prompt}"

        logger.debug(
            "[Google:%s] Generating with temp=%.2f max_tokens=%d",
            self.model, params.temperature, params.max_tokens,
        )

        response = await client.aio.models.generate_content(
            model=self.model,
            contents=full_prompt,
            config={
                "temperature": params.temperature,
                "max_output_tokens": params.max_tokens,
            },
        )

        text = response.text if response.text else ""

        # Gemini doesn't expose per-response token counts easily
        return ModelResponse(
            text=text,
            model=self.model,
            tokens_used=0,
            finish_reason="stop",
            confidence=0.84,
        )

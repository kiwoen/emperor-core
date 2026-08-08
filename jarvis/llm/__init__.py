"""LLM module — unified multi-provider LLM interface.

Exports:
    - ``LLMEngine`` — sync/async/streaming chat engine backed by litellm.
    - ``LLMConfig`` — pydantic configuration model for LLM backends.
    - ``ModelProvider`` — enum of supported providers (openai / anthropic / ollama).
"""

from jarvis.llm.config import LLMConfig, ModelProvider
from jarvis.llm.engine import LLMEngine

__all__ = ["LLMEngine", "LLMConfig", "ModelProvider"]

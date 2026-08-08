"""Jarvis Router — intelligent task dispatch with multi-level routing.

Provides:
- :class:`IntentClassifier` — LLM-based zero-shot intent classification.
- :class:`RouterEngine` — multi-level routing pipeline.
- :class:`RouterDecision` — structured routing result.
- :class:`ClassificationResult` — intent classification result.
"""

from jarvis.router.classifier import (
    INTENT_LABELS,
    INTENT_TO_MINISTER,
    ClassificationResult,
    IntentClassifier,
)
from jarvis.router.engine import (
    RouteRecord,
    RouterDecision,
    RouterEngine,
)

__all__ = [
    "IntentClassifier",
    "ClassificationResult",
    "RouterEngine",
    "RouterDecision",
    "RouteRecord",
    "INTENT_LABELS",
    "INTENT_TO_MINISTER",
]

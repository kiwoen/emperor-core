"""RouterEngine — intelligent task dispatch with multi-level routing.

Multi-level routing pipeline:
    1. Intent classification → 2. Minister matching → 3. Capability selection

Tracks routing history for performance analysis and adaptive refinement.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from huanxin.router.classifier import (
    INTENT_TO_MINISTER,
    ClassificationResult,
    IntentClassifier,
)

logger = logging.getLogger("huanxin.router.engine")


@dataclass
class RouterDecision:
    """Single routing decision.

    Attributes:
        target_type:  ``"minister"`` or ``"capability"``.
        target_name:  Minister name or capability name.
        confidence:  Overall confidence (0.0–1.0).
        reasoning:  Human-readable rationale.
        intent:  Predicted intent label.
        intent_confidence:  Confidence from intent classifier alone.
        suggested_minister:  Minister recommended by classifier.
        matched_capability:  Capability matched (or ``None``).
    """

    target_type: str  # "minister" | "capability"
    target_name: str
    confidence: float
    reasoning: str
    intent: str = ""
    intent_confidence: float = 0.0
    suggested_minister: str = ""
    matched_capability: Optional[str] = None


@dataclass
class RouteRecord:
    """Persistent record of a routing decision."""

    user_input: str
    decision: RouterDecision
    timestamp: float = field(default_factory=time.time)
    latency_ms: float = 0.0


class RouterEngine:
    """Multi-level intelligent task router.

    Pipeline:
        1. Run :class:`IntentClassifier` on *user_input*
        2. Map intent → minister
        3. Match minister + intent → best capability (if provided)

    Parameters:
        classifier:
            An :class:`IntentClassifier` instance.
        confidence_threshold:
            Minimum confidence to trust the routing (default 0.5).
            Below threshold falls back to ``default_minister``.
        default_minister:
            Minister to use when confidence is low (default ``"turing"``).
        max_history:
            Max number of route records to keep in memory.

    Usage::

        from huanxin.llm import LLMEngine, LLMConfig
        from huanxin.router import RouterEngine, IntentClassifier

        llm = LLMEngine(LLMConfig(provider=ModelProvider.OPENAI, model_name="gpt-4o"))
        clf = IntentClassifier(llm_engine=llm)
        router = RouterEngine(classifier=clf)

        decision = router.route("帮我写一个排序算法", ["turing", "lecun", "hinton"], ["nlp", "code"])
        # → RouterDecision(target_type="minister", target_name="lecun", ...)
    """

    def __init__(
        self,
        classifier: Optional[IntentClassifier] = None,
        confidence_threshold: float = 0.5,
        default_minister: str = "turing",
        max_history: int = 1000,
    ) -> None:
        self.classifier = classifier or IntentClassifier()
        self.confidence_threshold = confidence_threshold
        self.default_minister = default_minister
        self.max_history = max_history

        self._history: list[RouteRecord] = []
        self._total_routes: int = 0
        self._total_latency_ms: float = 0.0

    # ── Public API ──────────────────────────────────────────────────

    def route(
        self,
        user_input: str,
        available_ministers: Optional[list[str]] = None,
        available_capabilities: Optional[list[str]] = None,
    ) -> RouterDecision:
        """Route *user_input* to the best minister or capability.

        Args:
            user_input:  Natural-language user request.
            available_ministers:  Ministers currently registered (for validation).
            available_capabilities:  Capabilities available (for capability matching).

        Returns:
            :class:`RouterDecision` with routing target, confidence, and reasoning.
        """
        t0 = time.time()

        # ── Level 1: Intent classification ──────────────────────────
        classification = self.classifier.classify(user_input)
        intent = classification.intent
        intent_conf = classification.confidence

        # ── Level 2: Minister matching ──────────────────────────────
        suggested = self.classifier.get_minister_for(intent)

        # Validate against available ministers
        if available_ministers and suggested not in available_ministers:
            # Try to find a minister with matching domain
            fallback = self._match_by_domain(intent, available_ministers)
            if fallback:
                suggested = fallback
            elif self.default_minister in available_ministers:
                suggested = self.default_minister

        # ── Level 3: Capability selection ───────────────────────────
        matched_cap: Optional[str] = None
        target_type = "minister"
        target_name = suggested

        if available_capabilities:
            matched_cap = self._match_capability(intent, available_capabilities)
            if matched_cap:
                target_type = "capability"
                target_name = matched_cap

        # ── Confidence & fallback ───────────────────────────────────
        if intent_conf < self.confidence_threshold:
            target_name = suggested if suggested in (available_ministers or []) else self.default_minister
            target_type = "minister"
            matched_cap = None
            reasoning = (
                f"[低置信度] 意图 '{intent}' 置信度 {intent_conf:.2f} < 阈值 {self.confidence_threshold}, "
                f"已路由到 '{target_name}'"
            )
        else:
            parts = [
                f"意图 '{intent}' (置信度 {intent_conf:.2f})",
                f"→ 大臣 '{suggested}'",
            ]
            if matched_cap:
                parts.append(f"→ 能力 '{matched_cap}'")
            reasoning = ", ".join(parts)

        decision = RouterDecision(
            target_type=target_type,
            target_name=target_name,
            confidence=intent_conf,
            reasoning=reasoning,
            intent=intent,
            intent_confidence=intent_conf,
            suggested_minister=suggested,
            matched_capability=matched_cap,
        )

        # ── Record history ──────────────────────────────────────────
        elapsed_ms = (time.time() - t0) * 1000
        self._record(user_input, decision, elapsed_ms)

        return decision

    # ── Minister matching ───────────────────────────────────────────

    @staticmethod
    def _match_by_domain(intent: str, ministers: list[str]) -> Optional[str]:
        """Try to match intent domain keywords to minister names."""
        domain_hints: dict[str, list[str]] = {
            "code_generation": ["lecun", "code", "dev", "engineer"],
            "data_analysis": ["hinton", "data", "analyst"],
            "math_calculation": ["goodfellow", "math", "turing"],
            "general_chat": ["turing", "chat", "assistant"],
            "document_qa": ["confucius", "doc", "reader"],
            "file_operation": ["lovelace", "file", "system"],
            "web_search": ["lovelace", "search", "web"],
            "system_operation": ["tesla", "admin", "ops"],
        }
        hints = domain_hints.get(intent, [])
        for minister in ministers:
            if minister.lower() in hints or any(h in minister.lower() for h in hints):
                return minister
        return None

    @staticmethod
    def _match_capability(intent: str, capabilities: list[str]) -> Optional[str]:
        """Match intent to the most relevant capability name."""
        cap_map: dict[str, list[str]] = {
            "code_generation": ["code", "generate", "script", "program"],
            "data_analysis": ["data", "analysis", "stats", "visualize"],
            "math_calculation": ["math", "calculate", "compute"],
            "document_qa": ["qa", "read", "document", "extract"],
            "file_operation": ["file", "move", "organize", "manage"],
            "web_search": ["search", "web", "lookup", "browse"],
            "system_operation": ["system", "admin", "ops", "shell"],
            "general_chat": ["chat", "converse", "answer"],
        }
        keywords = cap_map.get(intent, ["chat"])
        for cap in capabilities:
            if any(kw in cap.lower() for kw in keywords):
                return cap
        return None

    # ── History & stats  ────────────────────────────────────────────

    def _record(
        self, user_input: str, decision: RouterDecision, latency_ms: float
    ) -> None:
        record = RouteRecord(
            user_input=user_input,
            decision=decision,
            latency_ms=latency_ms,
        )
        self._history.append(record)
        self._total_routes += 1
        self._total_latency_ms += latency_ms

        # Prune old entries
        if len(self._history) > self.max_history:
            self._history = self._history[-self.max_history:]

    @property
    def history(self) -> list[RouteRecord]:
        """Recent routing history (up to ``max_history`` entries)."""
        return list(self._history)

    def stats(self) -> dict[str, Any]:
        """Return routing statistics."""
        avg_latency = (
            self._total_latency_ms / self._total_routes if self._total_routes > 0 else 0.0
        )

        per_intent: dict[str, int] = {}
        per_target: dict[str, int] = {}
        for rec in self._history:
            d = rec.decision
            per_intent[d.intent] = per_intent.get(d.intent, 0) + 1
            per_target[d.target_name] = per_target.get(d.target_name, 0) + 1

        return {
            "total_routes": self._total_routes,
            "avg_latency_ms": round(avg_latency, 2),
            "per_intent": per_intent,
            "per_target": per_target,
            "classifier_stats": self.classifier.stats(),
            "history_count": len(self._history),
        }

    def clear_history(self) -> None:
        """Clear all routing history records."""
        self._history.clear()
        self._total_routes = 0
        self._total_latency_ms = 0.0

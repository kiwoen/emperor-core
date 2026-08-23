"""LLM-based zero-shot intent classifier.

Classifies user input into one of eight predefined intent categories
with confidence scoring and optional few-shot example injection.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("jarvis.router.classifier")

# ── Intent labels ────────────────────────────────────────────────────

INTENT_LABELS: list[str] = [
    "code_generation",
    "data_analysis",
    "file_operation",
    "web_search",
    "document_qa",
    "general_chat",
    "math_calculation",
    "system_operation",
]

# ── Intent → Minister mapping ────────────────────────────────────────

INTENT_TO_MINISTER: dict[str, str] = {
    "general_chat": "turing",
    "code_generation": "lecun",
    "data_analysis": "hinton",
    "math_calculation": "goodfellow",
    "document_qa": "confucius",
    "file_operation": "lovelace",
    "web_search": "lovelace",
    "system_operation": "tesla",
}

# ── Default few-shot examples ──────────────────────────────────────

FEWSHOT_EXAMPLES: list[dict[str, Any]] = [
    {"text": "给我写一个Python快速排序", "intent": "code_generation"},
    {"text": "分析这份销售数据的趋势", "intent": "data_analysis"},
    {"text": "把桌面的文档移到D盘", "intent": "file_operation"},
    {"text": "搜索一下最新的人工智能论文", "intent": "web_search"},
    {"text": "这篇合同里提到了哪些条款", "intent": "document_qa"},
    {"text": "你好，今天天气怎么样", "intent": "general_chat"},
    {"text": "计算 sin(45°) + cos(30°)", "intent": "math_calculation"},
    {"text": "重启系统服务", "intent": "system_operation"},
    {"text": "用JavaScript实现一个二叉树遍历", "intent": "code_generation"},
    {"text": "帮我统计下上个月的支出汇总", "intent": "data_analysis"},
]


@dataclass
class ClassificationResult:
    """Result of an intent classification.

    Attributes:
        intent:  Predicted intent label.
        confidence:  Confidence score (0.0–1.0).
        reasoning:  Brief explanation from the LLM (if available).
        raw_response:  Full LLM JSON response string.
        latency_ms:  Classification latency in milliseconds.
    """

    intent: str
    confidence: float = 0.0
    reasoning: str = ""
    raw_response: str = ""
    latency_ms: float = 0.0


class IntentClassifier:
    """Zero-shot intent classifier backed by an LLM engine.

    Parameters:
        llm_engine:
            An :class:`LLMEngine` instance for classification calls.
        labels:
            Custom intent label list (defaults to :data:`INTENT_LABELS`).
        examples:
            Few-shot examples (defaults to :data:`FEWSHOT_EXAMPLES`).
        fallback_intent:
            Intent to return when LLM is unavailable (default: ``"general_chat"``).

    Usage::

        from jarvis.llm import LLMEngine, LLMConfig
        engine = LLMEngine(LLMConfig(provider=ModelProvider.OPENAI, model_name="gpt-4o"))
        clf = IntentClassifier(llm_engine=engine)
        result = clf.classify("写一个快速排序算法")
        # → ClassificationResult(intent="code_generation", confidence=0.92, ...)
    """

    def __init__(
        self,
        llm_engine: Optional[Any] = None,
        labels: Optional[list[str]] = None,
        examples: Optional[list[dict[str, Any]]] = None,
        fallback_intent: str = "general_chat",
    ) -> None:
        self._llm = llm_engine
        self.labels = labels or INTENT_LABELS
        self.examples = examples or FEWSHOT_EXAMPLES
        self.fallback_intent = fallback_intent

        self.total_calls: int = 0
        self.total_success: int = 0
        self.per_intent_counts: dict[str, int] = {label: 0 for label in self.labels}

        logger.info(
            "[IntentClassifier] Initialised — %d labels, %d few-shot examples",
            len(self.labels), len(self.examples),
        )

    # ── Public API ──────────────────────────────────────────────────

    def classify(self, text: str) -> ClassificationResult:
        """Classify *text* into one of the predefined intent labels.

        Args:
            text:  User input to classify.

        Returns:
            :class:`ClassificationResult` with intent, confidence, and reasoning.
        """
        import time

        self.total_calls += 1
        t0 = time.time()

        if self._llm is None:
            label, confidence = self._rule_fallback(text)
            self.total_success += 1
            elapsed = (time.time() - t0) * 1000
            self._record(label)
            return ClassificationResult(
                intent=label,
                confidence=confidence,
                reasoning="rule-based fallback (no LLM)",
                latency_ms=elapsed,
            )

        prompt = self._build_prompt(text)
        try:
            raw = self._llm.chat_sync(
                prompt=prompt,
                system=(
                    "You are a precise intent classifier. "
                    "Return ONLY a JSON object with keys: intent, confidence, reasoning."
                ),
            )
            parsed = self._parse_response(raw)
            label = parsed.get("intent", self.fallback_intent)
            if label not in self.labels:
                label = self.fallback_intent
            confidence = float(parsed.get("confidence", 0.5))

            self.total_success += 1
            elapsed = (time.time() - t0) * 1000
            self._record(label)
            return ClassificationResult(
                intent=label,
                confidence=min(max(confidence, 0.0), 1.0),
                reasoning=str(parsed.get("reasoning", "")),
                raw_response=raw,
                latency_ms=elapsed,
            )
        except Exception as exc:
            logger.warning("[IntentClassifier] LLM classification failed: %s", exc)
            elapsed = (time.time() - t0) * 1000
            label, confidence = self._rule_fallback(text)
            self._record(label)
            return ClassificationResult(
                intent=label,
                confidence=confidence,
                reasoning=f"rule-based fallback (error: {str(exc)[:80]})",
                latency_ms=elapsed,
            )

    def get_minister_for(self, intent: str) -> str:
        """Look up the recommended minister for *intent*.

        Args:
            intent:  One of the predefined intent labels.

        Returns:
            Minister name (e.g. ``"turing"``).
        """
        return INTENT_TO_MINISTER.get(intent, "turing")

    def stats(self) -> dict[str, Any]:
        """Return classification statistics as a dict."""
        return {
            "total_calls": self.total_calls,
            "total_success": self.total_success,
            "per_intent": dict(self.per_intent_counts),
            "labels": self.labels,
        }

    # ── Internals ─────────────────────────────────────────────────

    def _record(self, label: str) -> None:
        if label in self.per_intent_counts:
            self.per_intent_counts[label] += 1

    def _build_prompt(self, text: str) -> str:
        parts: list[str] = []
        parts.append(
            "Classify the user input into exactly one of these intent categories:\n"
            + ", ".join(self.labels)
            + ".\n"
        )
        parts.append("Return JSON: {\"intent\": \"<label>\", \"confidence\": <0.0-1.0>, \"reasoning\": \"<brief>\"}")

        if self.examples:
            parts.append("\nHere are some examples:")
            for ex in self.examples:
                parts.append(f"  Input: \"{ex['text']}\" → {ex['intent']}")

        parts.append(f"\nNow classify this input:\n\"{text}\"")
        return "\n".join(parts)

    @staticmethod
    def _parse_response(raw: str) -> dict[str, Any]:
        # Try direct JSON parse
        try:
            return json.loads(raw.strip())
        except json.JSONDecodeError:
            pass
        # Try stripping markdown fences
        stripped = raw.strip()
        for prefix in ("```json", "```"):
            if stripped.startswith(prefix):
                stripped = stripped[len(prefix):].strip()
        if stripped.endswith("```"):
            stripped = stripped[:-3].strip()
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
        # Try extracting JSON object via regex
        import re
        match = re.search(r'\{[^{}]*"intent"[^{}]*\}', raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        return {}

    def _rule_fallback(self, text: str) -> tuple[str, float]:
        """Lightweight keyword-based fallback when LLM is unavailable."""
        import re

        lower = text.lower()

        code_kw = re.compile(
            r"(编写|实现|生成.*代码|代码|code|program|function|class|算法|algorithm|debug|修复.*bug|重构|refactor|写.*(代码|程序|脚本|函数|方法|算法))",
            re.IGNORECASE,
        )
        math_kw = re.compile(
            r"(计算|calculate|compute|solve|方程|equation|积分|integral|导数|derivative|数学|math)",
            re.IGNORECASE,
        )
        data_kw = re.compile(
            r"(分析|统计|analysis|statistics|数据|data|汇总|summary|趋势|trend|图表|chart|plot)",
            re.IGNORECASE,
        )
        file_kw = re.compile(
            r"(文件|file|移动|move|复制|copy|删除|delete|重命名|rename|目录|folder|整理|organize)",
            re.IGNORECASE,
        )
        web_kw = re.compile(
            r"(搜索|search|查询|find|找到|网上|web|internet|浏览器|browser)",
            re.IGNORECASE,
        )
        doc_kw = re.compile(
            r"(文档|document|pdf|word|excel|ppt|合同|contract|报告|report|论文|paper)",
            re.IGNORECASE,
        )
        sys_kw = re.compile(
            r"(系统|system|重启|restart|服务|service|设置|setting|配置|config|进程|process)",
            re.IGNORECASE,
        )

        if code_kw.search(text):
            return ("code_generation", 0.6)
        if math_kw.search(text):
            return ("math_calculation", 0.6)
        if data_kw.search(text):
            return ("data_analysis", 0.5)
        if file_kw.search(text):
            return ("file_operation", 0.5)
        if web_kw.search(text):
            return ("web_search", 0.5)
        if doc_kw.search(text):
            return ("document_qa", 0.5)
        if sys_kw.search(text):
            return ("system_operation", 0.5)
        return ("general_chat", 0.3)

"""
Reflexion — self-reflection layer for agent output quality assurance.

After each task dispatch, the ReflexionEngine performs a rule-based
quality check on the output, diagnosing issues across three dimensions:
completeness, internal consistency, and factual grounding. When the
confidence score falls below a threshold, it enters an auto-correct
loop (max 3 retries) to iteratively improve the output.

Architecture:
    ReflectionResult       — outcome of a single quality check
    ReflexionEngine        — rule engine with auto_correct loop
    reflexion_history      — in-memory rolling history (last 1000 entries)

Checks:
    completeness    — presence of required elements (intro, body, conclusion)
    integrity       — internal consistency (no self-contradiction)
    factuality      — factual grounding indicators (citations, qualifiers)

Usage:
    from huanxin.reflexion import ReflexionEngine, ReflectionResult

    engine = ReflexionEngine(threshold=0.6, max_retries=3)
    result = engine.reflect(
        task_id="abc",
        prompt="Explain quantum computing",
        response="Quantum computing uses qubits...",
        domain="science",
    )
    if result.corrected:
        print(f"Auto-corrected, confidence now: {result.confidence:.2f}")
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger("huanxin.reflexion")


# ══════════════════════════════════════════════════════════════════
# Enums & Constants
# ══════════════════════════════════════════════════════════════════


class CheckType(str, Enum):
    """Categories of output quality checks."""
    COMPLETENESS = "completeness"
    INTEGRITY = "integrity"
    FACTUALITY = "factuality"


class CorrectionStatus(str, Enum):
    """Status of a reflexion cycle."""
    PASSED = "passed"
    CORRECTED = "corrected"
    FAILED = "failed"


# ══════════════════════════════════════════════════════════════════
# Data types
# ══════════════════════════════════════════════════════════════════


@dataclass
class ReflectionIssue:
    """A single issue found during reflection."""

    check: CheckType
    description: str
    severity: float  # 0.0 ~ 1.0, higher = more severe
    excerpt: str = ""  # relevant snippet from the output


@dataclass
class ReflectionResult:
    """Complete outcome of a reflexion cycle (one attempt)."""

    task_id: str
    status: CorrectionStatus = CorrectionStatus.PASSED
    confidence: float = 1.0
    issues: list[ReflectionIssue] = field(default_factory=list)
    corrected: bool = False
    original_response: str = ""
    corrected_response: str = ""
    corrections_applied: int = 0
    attempts: int = 0
    total_elapsed_ms: float = 0.0
    timestamp: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "confidence": round(self.confidence, 4),
            "issues": [
                {
                    "check": i.check.value,
                    "description": i.description,
                    "severity": i.severity,
                    "excerpt": i.excerpt,
                }
                for i in self.issues
            ],
            "corrected": self.corrected,
            "corrections_applied": self.corrections_applied,
            "attempts": self.attempts,
            "total_elapsed_ms": round(self.total_elapsed_ms, 1),
            "timestamp": self.timestamp,
        }


# ══════════════════════════════════════════════════════════════════
# Reflexion Engine
# ══════════════════════════════════════════════════════════════════


class ReflexionEngine:
    """Rule-based reflection engine for output quality.

    Performs three categories of checks:
      1. COMPLETENESS — structural elements, length sanity
      2. INTEGRITY — internal consistency, no contradictions
      3. FACTUALITY — citations, qualifiers, grounding signals

    If ``confidence < threshold``, calls ``_auto_correct`` loop up to
    ``max_retries`` times.  After each correction the output is re-evaluated.

    Args:
        threshold: Minimum confidence (0~1) to skip correction.
        max_retries: Maximum auto-correct attempts (default 3).
    """

    THRESHOLD: float = 0.6
    MAX_RETRIES: int = 3
    HISTORY_LIMIT: int = 1000

    # ── Completeness indicators ──
    COMPLETENESS_PATTERNS: list[tuple[str, str, float]] = [
        # (pattern, label, weight)
        (r"^(#|##|###|第[一二三四五六七八九十\d]+[章节])", "has_section_headers", 0.10),
        (r"\b(首先|第一|接下来|然后|其次|最后|总之|总结|综上所述)\b", "has_transitions", 0.10),
        (r"\b(例如|比如|示例|举例|如\s+.+所示)\b", "has_examples", 0.08),
        (r"\b(因此|所以|由此可见|这表明|这意味着)\b", "has_conclusion", 0.10),
        (r"\b(问题|答案|解答|回复|输出)\b", "has_response_marker", 0.05),
    ]

    # ── Integrity (self-contradiction) patterns ──
    CONTRADICTION_PAIRS: list[tuple[str, str, str, float]] = [
        # (pos_pattern, neg_pattern, description, severity)
        (r"\b(是|存在|有|可以|能够|支持)\b", r"\b(不是|不存在|没有|不能|无法|不支持)\b", "self_contradiction_assertion", 0.25),
        (r"\b(正确|对|是的|没错)\b", r"\b(错误|错|不对|并非如此|相反)\b", "self_contradiction_correctness", 0.25),
        (r"\b(增加|增长|上升|提高)\b", r"\b(减少|下降|降低|削减)\b", "self_contradiction_direction", 0.20),
    ]

    # ── Factuality indicators ──
    FACTUALITY_INDICATORS: list[tuple[str, str, float]] = [
        # (positive pattern, label, weight)
        (r"\b(根据|据|参考|来源|引用|出自)\b", "has_citation", 0.10),
        (r"\b(\d{4})年\b", "has_temporal_anchor", 0.05),
        (r"\b(研究|调查|实验|论文|报告)表明\b", "has_evidence", 0.10),
        (r"\b(可能|或许|大概|估计|大约|约)\b", "has_qualifier", 0.05),
    ]
    FACTUALITY_RED_FLAGS: list[tuple[str, str, float]] = [
        # (red-flag pattern, label, penalty)
        (r"\b(绝对|肯定|毫无疑问|百分之百|毋庸置疑)\b", "overconfidence_no_qualifier", 0.15),
        (r"\b(众所周知|大家都知道|人人皆知)\b", "appeal_to_common_knowledge", 0.12),
        (r"\b(据我所知|我认为|我觉得|我个人|我以为)\b", "first_person_subjective", 0.10),
    ]

    def __init__(
        self,
        threshold: float = 0.6,
        max_retries: int = 3,
        history: Optional[list[ReflectionResult]] = None,
    ) -> None:
        self.threshold = threshold
        self.max_retries = max_retries
        self._history: list[ReflectionResult] = history if history is not None else []

    # ── Public API ─────────────────────────────────────────────────

    def reflect(
        self,
        task_id: str,
        prompt: str,
        response: str,
        domain: str = "general",
        corrector: Optional[Callable[[str, list[ReflectionIssue]], str]] = None,
    ) -> ReflectionResult:
        """Run full reflection pipeline: check → (optionally) auto-correct.

        Args:
            task_id: Unique task identifier.
            prompt: Original user prompt.
            response: LLM-generated output to reflect on.
            domain: Task domain (impacts check weights).
            corrector: Optional correction function ``f(text, issues) → text``.
                       If not provided, uses built-in heuristics.

        Returns:
            ``ReflectionResult`` with status, confidence, issues, and corrections.
        """
        t0 = time.time()

        # ── Run initial quality check ──
        issues = self._run_checks(prompt, response, domain)
        confidence = self._compute_confidence(issues)

        result = ReflectionResult(
            task_id=task_id,
            status=CorrectionStatus.PASSED,
            confidence=confidence,
            issues=issues,
            original_response=response,
            corrected_response=response,
            corrections_applied=0,
            attempts=0,
            timestamp=t0,
        )

        # ── Auto-correct loop ──
        if confidence < self.threshold:
            result = self._auto_correct(
                result, prompt, domain, corrector
            )

        result.total_elapsed_ms = (time.time() - t0) * 1000
        self._history.append(result)
        # Trim history
        if len(self._history) > self.HISTORY_LIMIT:
            self._history = self._history[-self.HISTORY_LIMIT:]

        if result.corrected:
            logger.info(
                "[Reflexion] task=%s corrected: %d attempts, %.2f→%.2f confidence, %d corrections",
                task_id, result.attempts,
                self._compute_confidence(issues), result.confidence,
                result.corrections_applied,
            )
        elif result.status == CorrectionStatus.FAILED:
            logger.warning(
                "[Reflexion] task=%s failed after %d attempts, confidence=%.2f",
                task_id, result.attempts, result.confidence,
            )

        return result

    def history(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent reflexion history as list of dicts."""
        entries = self._history[-limit:]
        return [e.to_dict() for e in entries]

    def stats(self) -> dict[str, Any]:
        """Aggregate statistics across all reflexion history."""
        total = len(self._history)
        if total == 0:
            return {
                "total_reflections": 0,
                "passed": 0,
                "corrected": 0,
                "failed": 0,
                "correction_rate": 0.0,
                "avg_confidence": 0.0,
                "avg_attempts": 0.0,
                "top_issues": [],
            }

        passed = sum(1 for e in self._history if e.status == CorrectionStatus.PASSED)
        corrected = sum(1 for e in self._history if e.status == CorrectionStatus.CORRECTED)
        failed = sum(1 for e in self._history if e.status == CorrectionStatus.FAILED)
        avg_conf = sum(e.confidence for e in self._history) / total
        avg_attempts = sum(e.attempts for e in self._history) / total

        # Top issue types
        issue_counts: dict[str, int] = {}
        for e in self._history:
            for i in e.issues:
                issue_counts[i.description] = issue_counts.get(i.description, 0) + 1
        top_issues = sorted(issue_counts.items(), key=lambda x: x[1], reverse=True)[:10]

        return {
            "total_reflections": total,
            "passed": passed,
            "corrected": corrected,
            "failed": failed,
            "correction_rate": round((corrected / total) * 100, 1) if total > 0 else 0.0,
            "avg_confidence": round(avg_conf, 4),
            "avg_attempts": round(avg_attempts, 2),
            "top_issues": [{"issue": k, "count": v} for k, v in top_issues],
        }

    def clear_history(self) -> None:
        """Clear all reflection history."""
        self._history.clear()

    # ── Internal: Checks ───────────────────────────────────────────

    def _run_checks(
        self, prompt: str, response: str, domain: str
    ) -> list[ReflectionIssue]:
        """Run all quality checks and return aggregated issues."""
        issues: list[ReflectionIssue] = []
        issues.extend(self._check_completeness(response))
        issues.extend(self._check_integrity(response))
        issues.extend(self._check_factuality(response))
        return issues

    def _check_completeness(self, response: str) -> list[ReflectionIssue]:
        """Check for structural completeness."""
        issues: list[ReflectionIssue] = []
        text = response.strip()

        # Empty / too short
        if len(text) < 10:
            issues.append(ReflectionIssue(
                check=CheckType.COMPLETENESS,
                description="response_too_short",
                severity=0.9,
                excerpt=text[:80],
            ))
            return issues

        # Length sanity thresholds
        words = len(text.split())
        if words < 3:
            issues.append(ReflectionIssue(
                check=CheckType.COMPLETENESS,
                description="response_severely_truncated",
                severity=0.8,
                excerpt=text[:80],
            ))
            return issues
        if words > 10000:
            issues.append(ReflectionIssue(
                check=CheckType.COMPLETENESS,
                description="response_suspiciously_long",
                severity=0.15,
                excerpt=f"{words} words total",
            ))

        # Structural indicators
        found_patterns = 0
        total_patterns = len(self.COMPLETENESS_PATTERNS)
        for pattern, label, _ in self.COMPLETENESS_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                found_patterns += 1

        # If fewer than half of patterns matched, flag as incomplete
        if found_patterns < max(2, total_patterns // 2):
            issues.append(ReflectionIssue(
                check=CheckType.COMPLETENESS,
                description="incomplete_structure",
                severity=0.4 - (found_patterns * 0.05),
                excerpt=f"matched {found_patterns}/{total_patterns} structural patterns",
            ))

        # Check for truncated ending
        truncated_markers = ["...", "未完", "待续", "[truncated]", "<<TRUNCATED>>"]
        for marker in truncated_markers:
            if marker in text[-50:]:
                issues.append(ReflectionIssue(
                    check=CheckType.COMPLETENESS,
                    description="response_truncated_ending",
                    severity=0.7,
                    excerpt=text[-80:],
                ))
                break

        return issues

    def _check_integrity(self, response: str) -> list[ReflectionIssue]:
        """Check for internal consistency / self-contradiction."""
        issues: list[ReflectionIssue] = []

        for pos_pat, neg_pat, desc, severity in self.CONTRADICTION_PAIRS:
            has_pos = bool(re.search(pos_pat, response, re.IGNORECASE))
            has_neg = bool(re.search(neg_pat, response, re.IGNORECASE))
            if has_pos and has_neg:
                issues.append(ReflectionIssue(
                    check=CheckType.INTEGRITY,
                    description=desc,
                    severity=severity,
                    excerpt=f"both '{pos_pat}' and '{neg_pat}' patterns found",
                ))

        # Check for numeric inconsistency: "A is 10" ... "A is 20" patterns
        num_assertions = re.findall(
            r"\b(\w[\w\s]{0,20})\s*(?:是|为|等于|有|约|大约)\s*(\d+(?:\.\d+)?)\b",
            response,
        )
        seen_labels: dict[str, set[str]] = {}
        for label, value in num_assertions:
            label = label.strip().lower()
            if label not in seen_labels:
                seen_labels[label] = set()
            seen_labels[label].add(value)
        for label, vals in seen_labels.items():
            if len(vals) > 1:
                issues.append(ReflectionIssue(
                    check=CheckType.INTEGRITY,
                    description="numeric_inconsistency",
                    severity=0.3,
                    excerpt=f"'{label}' assigned multiple values: {vals}",
                ))

        return issues

    def _check_factuality(self, response: str) -> list[ReflectionIssue]:
        """Check factual grounding indicators."""
        issues: list[ReflectionIssue] = []

        # Red flags
        for pattern, label, severity in self.FACTUALITY_RED_FLAGS:
            matches = re.findall(pattern, response, re.IGNORECASE)
            if matches:
                issues.append(ReflectionIssue(
                    check=CheckType.FACTUALITY,
                    description=label,
                    severity=severity,
                    excerpt=str(matches[:3]),
                ))

        # Positive indicators
        found_pos = 0
        for pattern, label, _ in self.FACTUALITY_INDICATORS:
            if re.search(pattern, response, re.IGNORECASE):
                found_pos += 1

        if found_pos == 0 and len(response) > 200:
            issues.append(ReflectionIssue(
                check=CheckType.FACTUALITY,
                description="no_factual_grounding",
                severity=0.25,
                excerpt="no citation/evidence/qualifier found in response",
            ))

        return issues

    def _compute_confidence(self, issues: list[ReflectionIssue]) -> float:
        """Compute overall confidence from issue severities.

        Returns 1.0 if no issues; each issue subtracts its severity
        (capped at 0.0).  Uses a geometric decay so multiple issues
        compound rather than linearly add.
        """
        if not issues:
            return 1.0

        # Compound severity: confidence = ∏(1 - severity)
        confidence = 1.0
        for issue in issues:
            confidence *= max(0.05, 1.0 - issue.severity)

        return round(confidence, 4)

    # ── Internal: Auto-correction ──────────────────────────────────

    def _auto_correct(
        self,
        result: ReflectionResult,
        prompt: str,
        domain: str,
        corrector: Optional[Callable[[str, list[ReflectionIssue]], str]],
    ) -> ReflectionResult:
        """Attempt to auto-correct the response up to max_retries times.

        Args:
            result: Initial ReflectionResult with issues.
            prompt: Original user prompt.
            domain: Task domain.
            corrector: External correction function or None for built-in.

        Returns:
            Updated ReflectionResult after correction attempts.
        """
        corrected_text = result.corrected_response

        for attempt in range(1, self.max_retries + 1):
            if not result.issues:
                break

            # Apply corrections
            if corrector is not None:
                corrected_text = corrector(corrected_text, result.issues)
            else:
                corrected_text = self._builtin_correct(corrected_text, result.issues)

            # Re-evaluate
            new_issues = self._run_checks(prompt, corrected_text, domain)
            new_confidence = self._compute_confidence(new_issues)

            result.attempts = attempt
            result.issues = new_issues
            result.confidence = new_confidence
            result.corrected_response = corrected_text

            if new_confidence >= self.threshold:
                result.corrected = True
                result.corrections_applied = attempt
                result.status = CorrectionStatus.CORRECTED
                return result

        # Max retries exhausted
        if result.confidence >= self.threshold:
            result.corrected = True
            result.corrections_applied = result.attempts
            result.status = CorrectionStatus.CORRECTED
        else:
            result.status = CorrectionStatus.FAILED
            result.corrected = False

        return result

    def _builtin_correct(
        self, text: str, issues: list[ReflectionIssue]
    ) -> str:
        """Built-in heuristic correction rules.

        This is a lightweight rule-based corrector; for production use,
        provide a proper ``corrector`` callable that re-prompts an LLM.
        """
        result = text

        for issue in issues:
            if issue.check == CheckType.COMPLETENESS:
                if issue.description == "response_too_short":
                    result = result + "\n[Note: response was too short; auto-expanded with placeholder.]"
                elif issue.description == "response_truncated_ending":
                    # Remove trailing truncation markers
                    result = re.sub(r"\.{3,}\s*$|(未完待续|\[truncated\]|<<TRUNCATED>>)\s*$", "", result)
                    result = result.rstrip() + "\n[Note: truncated ending was trimmed.]"
                elif issue.description == "incomplete_structure":
                    if not re.search(r"(总之|总结|综上所述)", result, re.IGNORECASE):
                        result = result.rstrip() + "\n\n[In summary: the response has been auto-completed.]"

            elif issue.check == CheckType.INTEGRITY:
                if "self_contradiction" in issue.description:
                    result = result + "\n\n[Note: potential self-contradiction detected; please verify.]"

            elif issue.check == CheckType.FACTUALITY:
                if issue.description == "overconfidence_no_qualifier":
                    result = result + "\n\n[Disclaimer: this response contains unqualified assertions. Please verify facts independently.]"
                elif issue.description == "no_factual_grounding":
                    result = result + "\n\n[Note: no citations or evidence found in this response. Consider adding sources.]"

        return result


# ══════════════════════════════════════════════════════════════════
# Top-level convenience wrapper for Huanxin integration
# ══════════════════════════════════════════════════════════════════

def create_reflexion_engine(
    threshold: float = 0.6,
    max_retries: int = 3,
) -> ReflexionEngine:
    """Create a pre-configured ReflexionEngine."""
    return ReflexionEngine(threshold=threshold, max_retries=max_retries)

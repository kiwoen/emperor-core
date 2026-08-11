"""GuardrailChain — wires the standalone guards into the main execution path.

This module implements P0.2 of the self-evolving-AI plan.

Before this file existed, ``jarvis/tool_guard.py``, ``jarvis/loop_guard.py``,
``jarvis/bounded_autonomy.py`` and ``jarvis/hallucination_guard.py`` were fully
implemented, fully unit-tested — and almost entirely **unreachable** from
``Emperor.execute_task``.  The guards existed on paper only.  This chain closes
that gap.

Two deliberate design choices:

1. **No fake unified interface.**  Each guard keeps its real, existing API.
   The chain owns thin adapters (:class:`_ToolGuardAdapter` and friends) that
   translate each guard's native result into a common
   :class:`GuardrailCheck`.  Nothing in the guards had to change.
2. **Shadow first.**  ``EMPEROR_GUARDRAIL_MODE`` defaults to ``"shadow"``:
   every guard runs and emits telemetry, but nothing is blocked.  Only
   ``"enforce"`` turns a ``dangerous`` verdict into an actual stop.  This lets
   the guards be observed on production traffic before they gain teeth.

A guard that is missing or raises is reported as **unavailable with an ERROR
log** — never silently skipped, which was the original sin this whole P0 batch
is fixing.

Usage::

    chain = GuardrailChain(
        tool_guard=emperor._tool_guard,
        loop_guard=emperor._loop_guard,
        bounded_autonomy=emperor._bounded_autonomy,
        hallucination_guard=emperor._hallucination_guard,
        telemetry=emperor._guardrail_telemetry,
    )
    result = chain.run_pre_execution(task_id="t1", prompt="...", domain="code")
    if result.blocked:
        return chain.to_blocked_payload(result, task_id="t1")
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("jarvis.guardrail_chain")

#: Environment variable controlling the enforcement posture.
GUARDRAIL_MODE_ENV = "EMPEROR_GUARDRAIL_MODE"

#: Severity levels, ordered from benign to blocking.
SEVERITY_HARMLESS = "harmless"
SEVERITY_SUSPICIOUS = "suspicious"
SEVERITY_DANGEROUS = "dangerous"


class GuardrailMode(str, Enum):
    """Enforcement posture for the guardrail chain."""

    SHADOW = "shadow"    # run + record, never block
    ENFORCE = "enforce"  # run + record, block on `dangerous`

    @classmethod
    def coerce(cls, value: Any) -> "GuardrailMode":
        """Convert *value* to a mode; anything unrecognised → SHADOW.

        Defaulting to SHADOW is the safe direction: an operator typo can
        never accidentally start blocking production traffic.
        """
        if isinstance(value, cls):
            return value
        text = str(value or "").strip().lower()
        for member in cls:
            if member.value == text:
                return member
        if text:
            logger.warning(
                "[GuardrailChain] unknown guardrail mode %r — falling back to 'shadow'",
                value,
            )
        return cls.SHADOW


# ══════════════════════════════════════════════════════════════════
# Results
# ══════════════════════════════════════════════════════════════════


@dataclass
class GuardrailCheck:
    """Normalised outcome of a single guard invocation.

    Attributes:
        guard: Guard identifier (``"tool_guard"``, ``"loop_guard"`` …).
        available: False when the guard was missing or raised.
        severity: ``harmless`` | ``suspicious`` | ``dangerous``.
        rules: Rule/reason identifiers that fired.
        detail: Human-readable explanation.
        latency_us: Wall-clock duration of the guard call, microseconds.
        payload: Guard-specific structured data for audit/telemetry.
    """

    guard: str
    available: bool = True
    severity: str = SEVERITY_HARMLESS
    rules: List[str] = field(default_factory=list)
    detail: str = ""
    latency_us: int = 0
    payload: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_dangerous(self) -> bool:
        """True when this check would block in ``enforce`` mode."""
        return self.severity == SEVERITY_DANGEROUS

    def to_dict(self) -> Dict[str, Any]:
        """Serialisable representation for API responses and logs."""
        return {
            "guard": self.guard,
            "available": self.available,
            "severity": self.severity,
            "rules": list(self.rules),
            "detail": self.detail,
            "latency_us": self.latency_us,
            "payload": dict(self.payload),
        }


@dataclass
class GuardrailChainResult:
    """Aggregated outcome of one pass through the chain."""

    mode: GuardrailMode
    phase: str  # "pre" | "post"
    checks: List[GuardrailCheck] = field(default_factory=list)
    blocked: bool = False
    blocking_check: Optional[GuardrailCheck] = None

    @property
    def dangerous_checks(self) -> List[GuardrailCheck]:
        """Every check that reported ``dangerous``."""
        return [c for c in self.checks if c.is_dangerous]

    @property
    def unavailable_guards(self) -> List[str]:
        """Names of guards that could not be run."""
        return [c.guard for c in self.checks if not c.available]

    def to_dict(self) -> Dict[str, Any]:
        """Serialisable representation for API responses and logs."""
        return {
            "mode": self.mode.value,
            "phase": self.phase,
            "blocked": self.blocked,
            "blocking_guard": self.blocking_check.guard if self.blocking_check else None,
            "checks": [c.to_dict() for c in self.checks],
            "unavailable_guards": self.unavailable_guards,
        }


# ══════════════════════════════════════════════════════════════════
# Chain
# ══════════════════════════════════════════════════════════════════


class GuardrailChain:
    """Runs the real guards around the main execution path.

    Args:
        tool_guard: ``jarvis.tool_guard.ThreeTierGuardEnhancement``.
        loop_guard: ``jarvis.loop_guard.AgentLoopGuard``.
        bounded_autonomy: ``jarvis.bounded_autonomy.BoundedAutonomyEngine``.
        hallucination_guard: ``jarvis.hallucination_guard.HallucinationGuard``.
        telemetry: ``jarvis.guardrail_telemetry.GuardrailTelemetry`` sink.
        mode: Explicit mode override; defaults to the ``EMPEROR_GUARDRAIL_MODE``
            environment variable, then ``shadow``.
    """

    #: Guards executed before the LLM call.
    PRE_GUARDS = ("tool_guard", "loop_guard", "bounded_autonomy")
    #: Guards executed after the LLM call.
    POST_GUARDS = ("hallucination_guard",)

    def __init__(
        self,
        tool_guard: Any = None,
        loop_guard: Any = None,
        bounded_autonomy: Any = None,
        hallucination_guard: Any = None,
        telemetry: Any = None,
        mode: Optional[Any] = None,
    ) -> None:
        self._tool_guard = tool_guard
        self._loop_guard = loop_guard
        self._bounded_autonomy = bounded_autonomy
        self._hallucination_guard = hallucination_guard
        self._telemetry = telemetry
        self._explicit_mode: Optional[GuardrailMode] = (
            GuardrailMode.coerce(mode) if mode is not None else None
        )

    # ── Mode ──────────────────────────────────────────────────────

    @property
    def mode(self) -> GuardrailMode:
        """Current enforcement posture.

        Read from the environment on every access so operators can flip
        ``EMPEROR_GUARDRAIL_MODE`` without restarting the process — unless an
        explicit mode was passed to the constructor, which always wins.
        """
        if self._explicit_mode is not None:
            return self._explicit_mode
        return GuardrailMode.coerce(os.environ.get(GUARDRAIL_MODE_ENV, GuardrailMode.SHADOW.value))

    @mode.setter
    def mode(self, value: Any) -> None:
        self._explicit_mode = GuardrailMode.coerce(value)

    # ── Public API ────────────────────────────────────────────────

    def run_pre_execution(
        self,
        task_id: str,
        prompt: str,
        domain: str = "general",
        tool_name: str = "",
    ) -> GuardrailChainResult:
        """Run the pre-LLM guards (tool / loop / bounded autonomy).

        Args:
            task_id: Task identifier, used for per-task loop state.
            prompt: The user prompt (already screened by PromptGuard).
            domain: Task domain.
            tool_name: Tool the task will invoke; defaults to a synthetic
                ``execute_task:<domain>`` name so classification still runs.

        Returns:
            A :class:`GuardrailChainResult`; ``blocked`` is only ever True in
            ``enforce`` mode.
        """
        effective_tool = tool_name or f"execute_task:{domain or 'general'}"
        checks = [
            self._run_guard("tool_guard", self._check_tool_guard, effective_tool, domain),
            self._run_guard("loop_guard", self._check_loop_guard, task_id),
            self._run_guard(
                "bounded_autonomy", self._check_bounded_autonomy, prompt, domain, task_id
            ),
        ]
        return self._finalise(checks, phase="pre", snippet=prompt)

    def run_post_execution(
        self,
        task_id: str,
        response: str,
        prompt: str = "",
        domain: str = "general",
    ) -> GuardrailChainResult:
        """Run the post-LLM guards (hallucination / toxicity)."""
        checks = [
            self._run_guard(
                "hallucination_guard", self._check_hallucination, response, prompt, domain
            ),
        ]
        return self._finalise(checks, phase="post", snippet=response)

    def blocked_payload(
        self,
        result: GuardrailChainResult,
        task_id: str,
        minister: str = "__guard__",
    ) -> Dict[str, Any]:
        """Build the ``execute_task`` return payload for a blocked task."""
        check = result.blocking_check
        guard = check.guard if check else "guardrail"
        rules = ",".join(check.rules) if check and check.rules else guard
        return {
            "task_id": task_id,
            "status": "blocked",
            "minister": minister,
            "success": False,
            "confidence": 0.0,
            "merit_score": 0.0,
            "execution_time_ms": 0.0,
            "response": "",
            "error": f"guardrail_blocked:guard={guard};rules={rules}",
            "handoff": None,
            "guardrail": result.to_dict(),
        }

    # ── Guard adapters ────────────────────────────────────────────
    #
    # Each adapter speaks the guard's *real* API and returns a GuardrailCheck.

    def _check_tool_guard(self, tool_name: str, domain: str) -> GuardrailCheck:
        """Adapt ``ThreeTierGuardEnhancement.evaluate`` → GuardrailCheck."""
        access = self._tool_guard.evaluate(tool_name, {"domain": domain})
        allowed = bool(getattr(access, "allowed", True))
        action_type = _enum_value(getattr(access, "action_type", ""))
        strategy = _enum_value(getattr(access, "confirmation_strategy", ""))
        block_reason = str(getattr(access, "block_reason", "") or "")

        if not allowed:
            severity = SEVERITY_DANGEROUS
        elif strategy == "confirm":
            severity = SEVERITY_SUSPICIOUS
        else:
            severity = SEVERITY_HARMLESS

        return GuardrailCheck(
            guard="tool_guard",
            severity=severity,
            rules=[f"tool_action:{action_type}"] if action_type else [],
            detail=block_reason or f"tool={tool_name} action={action_type} strategy={strategy}",
            payload={
                "tool_name": tool_name,
                "action_type": action_type,
                "confirmation_strategy": strategy,
                "allowed": allowed,
            },
        )

    def _check_loop_guard(self, task_id: str) -> GuardrailCheck:
        """Adapt ``AgentLoopGuard.task_info`` → GuardrailCheck.

        Read-only on purpose: ``check_iteration`` mutates the per-task counter
        and is already called once by ``Emperor.execute_task``.  Calling it
        again here would double-count every task's iterations.
        """
        info = self._loop_guard.task_info(task_id)
        iterations = int(info.get("iteration_count", 0))
        max_iterations = int(info.get("max_iterations", 0) or 0)
        cost = float(info.get("accumulated_cost", 0.0))
        max_cost = float(info.get("max_cost_per_run", 0.0) or 0.0)
        streak = int(info.get("consecutive_same_action", 0))
        streak_limit = int(info.get("loop_streak_limit", 0) or 0)

        rules: List[str] = []
        severity = SEVERITY_HARMLESS

        if max_iterations and iterations > max_iterations:
            rules.append("loop_limit_exceeded")
            severity = SEVERITY_DANGEROUS
        elif max_iterations and iterations >= max_iterations * 0.8:
            rules.append("loop_limit_near")
            severity = SEVERITY_SUSPICIOUS

        if max_cost and cost > max_cost:
            rules.append("budget_exceeded")
            severity = SEVERITY_DANGEROUS

        if streak_limit and streak >= streak_limit:
            rules.append("infinite_loop_detected")
            severity = SEVERITY_DANGEROUS

        return GuardrailCheck(
            guard="loop_guard",
            severity=severity,
            rules=rules,
            detail=(
                f"iterations={iterations}/{max_iterations} "
                f"cost=${cost:.4f}/${max_cost:.2f} streak={streak}/{streak_limit}"
            ),
            payload=dict(info),
        )

    def _check_bounded_autonomy(
        self, prompt: str, domain: str, task_id: str
    ) -> GuardrailCheck:
        """Adapt ``BoundedAutonomyEngine.evaluate`` → GuardrailCheck.

        Only the RED zone is treated as blocking.  YELLOW means "a human
        would normally approve this", which the HITL ApprovalEngine already
        handles earlier in ``execute_task``; escalating YELLOW here would
        block ordinary traffic and violate the "observe, don't disrupt" rule.
        """
        result = self._bounded_autonomy.evaluate(
            {"prompt": prompt, "domain": domain},
            context={"domain": domain},
            task_id=task_id,
        )
        zone = _enum_value(getattr(result, "zone", "")) or "unknown"
        can_proceed = bool(getattr(result, "can_proceed", True))
        needs_approval = bool(getattr(result, "needs_approval", False))

        if zone == "red":
            severity = SEVERITY_DANGEROUS
        elif needs_approval or not can_proceed:
            severity = SEVERITY_SUSPICIOUS
        else:
            severity = SEVERITY_HARMLESS

        return GuardrailCheck(
            guard="bounded_autonomy",
            severity=severity,
            rules=[f"zone:{zone}"],
            detail=str(getattr(result, "reason", "") or f"zone={zone}"),
            payload={
                "zone": zone,
                "can_proceed": can_proceed,
                "needs_approval": needs_approval,
            },
        )

    def _check_hallucination(
        self, response: str, prompt: str, domain: str
    ) -> GuardrailCheck:
        """Adapt ``HallucinationGuard.check`` → GuardrailCheck."""
        detection = self._hallucination_guard.check(
            output=str(response or ""),
            context=f"Task: {prompt}\nDomain: {domain}",
        )
        flagged = int(getattr(detection, "flagged_sentences", 0) or 0)
        has_issue = bool(getattr(detection, "has_hallucinations", False))
        claims = list(getattr(detection, "claims", []) or [])
        severities = {_enum_value(getattr(c, "severity", "")) for c in claims}

        if has_issue and ("high" in severities or "critical" in severities):
            severity = SEVERITY_DANGEROUS
        elif has_issue:
            severity = SEVERITY_SUSPICIOUS
        else:
            severity = SEVERITY_HARMLESS

        payload: Dict[str, Any] = {}
        to_dict = getattr(detection, "to_dict", None)
        if callable(to_dict):
            payload = to_dict()

        return GuardrailCheck(
            guard="hallucination_guard",
            severity=severity,
            rules=["hallucination_guard"] if has_issue else [],
            detail=str(getattr(detection, "summary", "") or ""),
            payload=payload,
        )

    # ── Machinery ─────────────────────────────────────────────────

    def _run_guard(self, name: str, fn: Any, *args: Any) -> GuardrailCheck:
        """Invoke one adapter, timing it and converting failures explicitly.

        A missing guard or an exception is reported as ``available=False``
        with an ERROR log — never a silent skip.
        """
        guard_obj = getattr(self, f"_{name}", None)
        if guard_obj is None:
            logger.error(
                "[GuardrailChain] guard '%s' is UNAVAILABLE (not wired) — "
                "main execution path is running unprotected for this check",
                name,
            )
            return GuardrailCheck(
                guard=name,
                available=False,
                severity=SEVERITY_HARMLESS,
                rules=["guard_unavailable"],
                detail=f"{name} is not wired into the Emperor",
            )

        t0 = time.perf_counter_ns()
        try:
            check = fn(*args)
        except Exception as exc:
            logger.error(
                "[GuardrailChain] guard '%s' RAISED %s: %s — treating as unavailable",
                name, type(exc).__name__, exc,
                exc_info=True,
            )
            return GuardrailCheck(
                guard=name,
                available=False,
                severity=SEVERITY_HARMLESS,
                rules=["guard_error"],
                detail=f"{type(exc).__name__}: {exc}",
                latency_us=(time.perf_counter_ns() - t0) // 1000,
            )

        check.latency_us = (time.perf_counter_ns() - t0) // 1000
        return check

    def _finalise(
        self,
        checks: List[GuardrailCheck],
        phase: str,
        snippet: str,
    ) -> GuardrailChainResult:
        """Decide blocking, emit telemetry, and build the aggregate result."""
        mode = self.mode
        result = GuardrailChainResult(mode=mode, phase=phase, checks=checks)

        if mode is GuardrailMode.ENFORCE:
            for check in checks:
                if check.is_dangerous:
                    result.blocked = True
                    result.blocking_check = check
                    break

        for check in checks:
            self._emit(check, result, phase, snippet)

        if result.blocked and result.blocking_check is not None:
            logger.warning(
                "[GuardrailChain] BLOCKED phase=%s guard=%s rules=%s detail=%s",
                phase,
                result.blocking_check.guard,
                result.blocking_check.rules,
                result.blocking_check.detail,
            )
        else:
            for check in result.dangerous_checks:
                logger.warning(
                    "[GuardrailChain] shadow-mode DANGEROUS (not blocked) "
                    "phase=%s guard=%s rules=%s detail=%s",
                    phase, check.guard, check.rules, check.detail,
                )

        return result

    def _emit(
        self,
        check: GuardrailCheck,
        result: GuardrailChainResult,
        phase: str,
        snippet: str,
    ) -> None:
        """Emit one telemetry event whose ``action`` matches reality.

        The action is ``blocked`` **only** for the check that actually caused
        the stop.  Reporting "blocked" for a shadow-mode detection would be
        exactly the false-reporting (谎报) bug that P0.1 fixed for PromptGuard;
        the chain must not reintroduce it.
        """
        if self._telemetry is None:
            return
        try:
            from jarvis.guardrail_telemetry import (
                EventAction,
                GuardrailEvent,
                GuardrailType,
            )

            is_blocking = result.blocking_check is check and result.blocked
            self._telemetry.emit(GuardrailEvent(
                guardrail_type=(
                    GuardrailType.PRE_LLM if phase == "pre" else GuardrailType.POST_LLM
                ),
                trigger_rule=[check.guard] + list(check.rules),
                severity=check.severity,
                action=EventAction.BLOCKED if is_blocking else EventAction.ALLOWED,
                input_snippet=str(snippet or "")[:200],
                latency_us=check.latency_us,
            ))
        except Exception:
            logger.error(
                "[GuardrailChain] telemetry emit failed for guard '%s'",
                check.guard,
                exc_info=True,
            )


# ══════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════


def _enum_value(value: Any) -> str:
    """Return ``value.value`` for enums, else ``str(value)``, lower-cased."""
    inner = getattr(value, "value", value)
    return str(inner or "").strip().lower()


__all__ = [
    "GUARDRAIL_MODE_ENV",
    "GuardrailMode",
    "GuardrailCheck",
    "GuardrailChainResult",
    "GuardrailChain",
]

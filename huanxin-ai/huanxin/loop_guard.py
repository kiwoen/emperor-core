"""
Agent Loop Boundedness — prevent unbounded agentic loops from burning API quota.

Provides three layered protections for any agentic execution loop:
  1. **AgentLoopGuard** — hard max iterations per task (default 20)
  2. **CostCap** — hard max USD cost per run (default $5.00)
  3. **LoopDetector** — dead-loop detection (3+ consecutive identical actions
     with zero progress, measured by response content hash)

Every interception emits a ``GuardrailEvent`` with ``guardrail_type="loop_guard"``
via ``huanxin.guardrail_telemetry``.

Usage (inside emperor.py or any agentic executor)::

    from huanxin.loop_guard import AgentLoopGuard

    guard = AgentLoopGuard(max_iterations=20, max_cost_per_run=5.00)

    for step in agent_loop:
        guard.check_iteration(task_id)           # raises LoopLimitExceededError
        guard.check_cost(task_id, step_cost)     # raises BudgetExceededError
        guard.record_action(task_id, action, result_hash)  # raises InfiniteLoopError
        ... run step ...

Exceptions:
  - ``BudgetExceededError``  — cost cap hit
  - ``LoopLimitExceededError`` — max iterations reached
  - ``InfiniteLoopError`` — dead-loop detected (3+ same actions, no progress)
"""

from __future__ import annotations

import hashlib
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("huanxin.loop_guard")


# ══════════════════════════════════════════════════════════════════
# Exceptions
# ══════════════════════════════════════════════════════════════════


class BudgetExceededError(Exception):
    """Raised when the accumulated cost for a single run exceeds the cap."""

    def __init__(self, task_id: str, accumulated: float, cap: float):
        self.task_id = task_id
        self.accumulated = accumulated
        self.cap = cap
        super().__init__(
            f"Cost cap exceeded for task '{task_id}': "
            f"${accumulated:.4f} > ${cap:.2f}"
        )


class LoopLimitExceededError(Exception):
    """Raised when an agentic loop exceeds the maximum iteration count."""

    def __init__(self, task_id: str, iterations: int, max_iterations: int):
        self.task_id = task_id
        self.iterations = iterations
        self.max_iterations = max_iterations
        super().__init__(
            f"Loop limit exceeded for task '{task_id}': "
            f"{iterations} iterations (max={max_iterations})"
        )


class InfiniteLoopError(Exception):
    """Raised when dead-loop is detected (3+ consecutive same actions, no progress)."""

    def __init__(self, task_id: str, action: str, streak: int):
        self.task_id = task_id
        self.action = action
        self.streak = streak
        super().__init__(
            f"Infinite loop detected for task '{task_id}': "
            f"'{action}' repeated {streak} times with no progress"
        )


# ══════════════════════════════════════════════════════════════════
# Internal state
# ══════════════════════════════════════════════════════════════════


@dataclass
class _TaskLoopState:
    """Per-task mutable tracking state for loop guard."""

    iteration_count: int = 0
    accumulated_cost: float = 0.0
    # Loop detection
    consecutive_same_action: int = 0
    last_action: str = ""
    last_result_hash: str = ""


# ══════════════════════════════════════════════════════════════════
# CostCap
# ══════════════════════════════════════════════════════════════════


class CostCap:
    """Hard cost cap per run — raises BudgetExceededError when breached.

    Integrates with the shared CostTracker instance (if provided) to read
    per-task accumulated costs.
    """

    def __init__(self, max_cost_per_run: float = 5.00, cost_tracker: Any = None):
        self.max_cost_per_run = max_cost_per_run
        self._cost_tracker = cost_tracker  # huanxin.cost_tracker.CostTracker

    def check(self, task_id: str, accumulated: float) -> None:
        """Raise BudgetExceededError if accumulated exceeds the cap."""
        if accumulated > self.max_cost_per_run:
            raise BudgetExceededError(task_id, accumulated, self.max_cost_per_run)

    def get_task_cost(self, task_id: str) -> float:
        """Query the cost tracker for total cost of a specific task."""
        if self._cost_tracker is None:
            return 0.0
        try:
            total = 0.0
            for r in self._cost_tracker._records_snapshot():
                if getattr(r, "task_id", "") == task_id:
                    total += getattr(r, "cost_usd", 0.0)
            return total
        except Exception:
            return 0.0


# ══════════════════════════════════════════════════════════════════
# LoopDetector
# ══════════════════════════════════════════════════════════════════


class LoopDetector:
    """Dead-loop detection by tracking action + result-hash streaks.

    If the same action produces the same result hash for 3 consecutive
    iterations, an InfiniteLoopError is raised.
    """

    def __init__(self, max_streak: int = 3):
        self.max_streak = max_streak

    @staticmethod
    def _hash(value: str) -> str:
        """Deterministic short hash of a result string."""
        return hashlib.md5(value.encode("utf-8", errors="replace")).hexdigest()[:8]

    def check(
        self, state: _TaskLoopState, action: str, result: str
    ) -> None:
        """Update streak and raise InfiniteLoopError if dead-loop detected.

        Args:
            state: Per-task mutable state.
            action: Description of the current step (e.g. tool name).
            result: Full result string to hash for progress detection.

        Raises:
            InfiniteLoopError: If streak >= max_streak with no progress.
        """
        result_hash = self._hash(result)

        if action == state.last_action and result_hash == state.last_result_hash:
            state.consecutive_same_action += 1
        else:
            state.consecutive_same_action = 1

        state.last_action = action
        state.last_result_hash = result_hash

        if state.consecutive_same_action >= self.max_streak:
            raise InfiniteLoopError("", action, state.consecutive_same_action)


# ══════════════════════════════════════════════════════════════════
# AgentLoopGuard (orchestrator)
# ══════════════════════════════════════════════════════════════════


class AgentLoopGuard:
    """Combined loop boundedness guard for any agentic execution loop.

    Bundles three protections:
      - **AgentLoopGuard** (iteration limit)
      - **CostCap** (USD cost cap)
      - **LoopDetector** (dead-loop detection)

    Thread-safe.  Emits GuardrailEvent on every interception.
    """

    def __init__(
        self,
        max_iterations: int = 20,
        max_cost_per_run: float = 5.00,
        max_loop_streak: int = 3,
        cost_tracker: Any = None,
    ):
        self.max_iterations = max_iterations
        self._cost_cap = CostCap(
            max_cost_per_run=max_cost_per_run,
            cost_tracker=cost_tracker,
        )
        self._loop_detector = LoopDetector(max_streak=max_loop_streak)

        self._task_states: dict[str, _TaskLoopState] = {}
        self._lock = threading.Lock()

    # ── Per-task state management ─────────────────────────────────

    def _get_state(self, task_id: str) -> _TaskLoopState:
        with self._lock:
            if task_id not in self._task_states:
                self._task_states[task_id] = _TaskLoopState()
            return self._task_states[task_id]

    def reset_task(self, task_id: str) -> None:
        """Reset tracking state for a specific task."""
        with self._lock:
            self._task_states.pop(task_id, None)

    # ── Guard checks ──────────────────────────────────────────────

    def check_iteration(self, task_id: str) -> None:
        """Increment iteration count and raise if limit exceeded.

        Call this at the start of each agentic loop iteration.

        Raises:
            LoopLimitExceededError: If max_iterations reached.
        """
        state = self._get_state(task_id)
        state.iteration_count += 1

        if state.iteration_count > self.max_iterations:
            self._emit_telemetry(
                task_id=task_id,
                event_type="loop_limit_exceeded",
                reason=f"iteration {state.iteration_count} > {self.max_iterations}",
            )
            raise LoopLimitExceededError(
                task_id, state.iteration_count, self.max_iterations
            )

    def check_cost(self, task_id: str, step_cost: float = 0.0) -> None:
        """Accumulate and check cost; raise if cap exceeded.

        Call this after each step that incurs API cost.

        Args:
            task_id: Task identifier.
            step_cost: Cost for this single step (optional; also reads from
                attached CostTracker if step_cost is 0).

        Raises:
            BudgetExceededError: If accumulated cost > cap.
        """
        state = self._get_state(task_id)

        # If no explicit step_cost, try reading from cost tracker
        if step_cost <= 0.0:
            tracker_cost = self._cost_cap.get_task_cost(task_id)
            # Only update if tracker reports more than we already have
            if tracker_cost > state.accumulated_cost:
                step_cost = tracker_cost - state.accumulated_cost

        state.accumulated_cost += step_cost

        if state.accumulated_cost > self._cost_cap.max_cost_per_run:
            self._emit_telemetry(
                task_id=task_id,
                event_type="budget_exceeded",
                reason=(
                    f"accumulated ${state.accumulated_cost:.4f} > "
                    f"cap ${self._cost_cap.max_cost_per_run:.2f}"
                ),
            )
            raise BudgetExceededError(
                task_id,
                state.accumulated_cost,
                self._cost_cap.max_cost_per_run,
            )

    def record_action(self, task_id: str, action: str, result: str = "") -> None:
        """Record an action and check for dead-loop (same action, no progress).

        Call this after each step that produces a result.

        Args:
            task_id: Task identifier.
            action: Description of the action (e.g. "search_file", "llm_call").
            result: Result string to hash for progress comparison.

        Raises:
            InfiniteLoopError: If 3+ consecutive same actions with no progress.
        """
        state = self._get_state(task_id)
        try:
            self._loop_detector.check(state, action, result)
        except InfiniteLoopError as e:
            e.task_id = task_id  # inject task_id
            self._emit_telemetry(
                task_id=task_id,
                event_type="infinite_loop_detected",
                reason=f"action='{action}' repeated {state.consecutive_same_action}x",
            )
            raise

    # ── Convenience: run one full check cycle ───────────────────────

    def guard_step(
        self,
        task_id: str,
        action: str = "",
        result: str = "",
        step_cost: float = 0.0,
    ) -> None:
        """Run all three checks in one call for a single loop iteration.

        Equivalent to::

            guard.check_iteration(task_id)
            guard.check_cost(task_id, step_cost)
            guard.record_action(task_id, action, result)

        Args:
            task_id: Task identifier.
            action: Description of the current step (for loop detection).
            result: Result string (for loop detection).
            step_cost: Cost incurred by this step (for cost cap).

        Raises:
            LoopLimitExceededError, BudgetExceededError, InfiniteLoopError
        """
        self.check_iteration(task_id)
        self.check_cost(task_id, step_cost)
        if action or result:
            self.record_action(task_id, action, result)

    # ── Telemetry ─────────────────────────────────────────────────

    def _emit_telemetry(self, task_id: str, event_type: str, reason: str) -> None:
        """Emit a loop_guard guardrail telemetry event."""
        try:
            from huanxin.guardrail_telemetry import (
                GuardrailEvent,
                GuardrailType,
                EventAction,
            )

            # Map event types to severities
            severity_map = {
                "loop_limit_exceeded": "dangerous",
                "budget_exceeded": "dangerous",
                "infinite_loop_detected": "dangerous",
            }

            # Create a synthetic "loop_guard" guardrail type
            class LoopGuardType(str):
                pass

            event = GuardrailEvent(
                guardrail_type=GuardrailType("loop_guard"),
                trigger_rule=[event_type],
                severity=severity_map.get(event_type, "suspicious"),
                action=EventAction("blocked"),
                input_snippet=f"task_id={task_id} | {reason}"[:200],
                latency_us=0,
            )
            from huanxin.guardrail_telemetry import guardrail_telemetry

            guardrail_telemetry.emit(event)
            logger.warning(
                "[AgentLoopGuard] INTERCEPTED task=%s type=%s reason=%s",
                task_id,
                event_type,
                reason,
            )
        except Exception:
            logger.debug(
                "[AgentLoopGuard] Telemetry unavailable for event=%s task=%s",
                event_type,
                task_id,
                exc_info=True,
            )

    # ── Query ─────────────────────────────────────────────────────

    @property
    def active_tasks(self) -> int:
        with self._lock:
            return len(self._task_states)

    def task_info(self, task_id: str) -> dict[str, Any]:
        """Return current tracking info for a task."""
        state = self._get_state(task_id)
        return {
            "task_id": task_id,
            "iteration_count": state.iteration_count,
            "accumulated_cost": round(state.accumulated_cost, 6),
            "max_iterations": self.max_iterations,
            "max_cost_per_run": self._cost_cap.max_cost_per_run,
            "consecutive_same_action": state.consecutive_same_action,
            "last_action": state.last_action,
            "loop_streak_limit": self._loop_detector.max_streak,
        }

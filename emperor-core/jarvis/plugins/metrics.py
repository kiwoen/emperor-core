"""MetricsPlugin — Collects performance metrics across the Emperor lifecycle.

Tracks task success/failure, confidence scores, execution time, and
evolution cycles. Provides a time-series ring buffer for the dashboard
to render historical charts.

Usage:
    from jarvis.plugins import MetricsPlugin

    plugin = MetricsPlugin(max_samples=2000)
    emperor.plugins.register(plugin)

    # At any time:
    summary = plugin.summary()         # aggregate stats
    history = plugin.task_history()    # per-task timeline
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional

from jarvis.plugin import Plugin


# ══════════════════════════════════════════════════════════════════
# Data samples
# ══════════════════════════════════════════════════════════════════


@dataclass
class TaskSample:
    """One task execution sample."""
    task_id: str
    timestamp: float
    success: bool
    confidence: float
    execution_time_ms: float
    domain: str = "general"
    error: str = ""


@dataclass
class EvolutionSample:
    """One evolution cycle sample."""
    timestamp: float
    cycles: int
    active_ministers: int = 0
    avg_merit: float = 0.0


@dataclass
class MetricsSummary:
    """Aggregate metrics snapshot."""
    total_tasks: int
    successful_tasks: int
    failed_tasks: int
    success_rate: float
    avg_confidence: float
    avg_execution_time_ms: float
    total_evolutions: int
    total_evolution_cycles: int
    active_ministers: int
    time_window_seconds: float
    samples_in_buffer: int


# ══════════════════════════════════════════════════════════════════
# Plugin
# ══════════════════════════════════════════════════════════════════


class MetricsPlugin(Plugin):
    """Collects rolling time-series metrics from Emperor lifecycle events.

    Args:
        max_samples: Maximum number of task samples kept in the ring buffer.
    """

    def __init__(self, max_samples: int = 1000) -> None:
        self._max_samples = max_samples
        self._tasks: deque[TaskSample] = deque(maxlen=max_samples)
        self._evolutions: deque[EvolutionSample] = deque(maxlen=max_samples)
        self._error_count: int = 0
        self._total_evolution_cycles: int = 0
        self._first_task_ts: Optional[float] = None
        self._last_task_ts: Optional[float] = None

    # ── Plugin identity ─────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "MetricsPlugin"

    # ── Lifecycle hooks ─────────────────────────────────────────────

    def on_task_before(self, **kw: Any) -> None:
        """Mark start time on task_id (used to compute latency)."""
        task_id = kw.get("task_id", "")
        # Stash the start time on the plugin instance so on_task_after
        # can compute elapsed time even if no other source is available.
        if not hasattr(self, "_pending_starts"):
            self._pending_starts: dict[str, float] = {}
        self._pending_starts[task_id] = time.time()

    def on_task_after(self, **kw: Any) -> None:
        """Record successful task sample."""
        outcome = kw.get("outcome", {}) or {}
        task_id = outcome.get("task_id", "")
        ts = time.time()
        start = getattr(self, "_pending_starts", {}).pop(task_id, ts)
        sample = TaskSample(
            task_id=task_id,
            timestamp=ts,
            success=bool(outcome.get("success", False)),
            confidence=float(outcome.get("confidence", 0.0)),
            execution_time_ms=float(outcome.get("execution_time_ms", 0.0)),
            domain=outcome.get("minister", "").split(":")[1]
                    if ":" in outcome.get("minister", "")
                    else "general",
            error="",
        )
        self._tasks.append(sample)
        self._first_task_ts = self._first_task_ts or ts
        self._last_task_ts = ts

    def on_task_error(self, **kw: Any) -> None:
        """Record failed task sample."""
        task_id = kw.get("task_id", "")
        ts = time.time()
        start = getattr(self, "_pending_starts", {}).pop(task_id, ts)
        self._tasks.append(TaskSample(
            task_id=task_id,
            timestamp=ts,
            success=False,
            confidence=0.0,
            execution_time_ms=(ts - start) * 1000.0,
            domain=kw.get("domain", "general"),
            error=str(kw.get("error", "unknown")),
        ))
        self._error_count += 1
        self._first_task_ts = self._first_task_ts or ts
        self._last_task_ts = ts

    def on_evolve_end(self, **kw: Any) -> None:
        """Record evolution cycle completion."""
        result = kw.get("result", {}) or {}
        ts = time.time()
        self._evolutions.append(EvolutionSample(
            timestamp=ts,
            cycles=int(result.get("cycles_completed", 0))
                   or len(result.get("mutations", [])),
            active_ministers=int(result.get("active_ministers", 0)),
            avg_merit=float(result.get("avg_merit", 0.0)),
        ))
        self._total_evolution_cycles += 1

    # ── Query API ───────────────────────────────────────────────────

    def task_history(self, limit: int = 50) -> list[TaskSample]:
        """Return recent task samples (newest first)."""
        return list(reversed(list(self._tasks)[-limit:]))

    def evolution_history(self, limit: int = 50) -> list[EvolutionSample]:
        """Return recent evolution samples (newest first)."""
        return list(reversed(list(self._evolutions)[-limit:]))

    def success_rate(self) -> float:
        """Success rate over the current window (0.0 - 1.0)."""
        if not self._tasks:
            return 0.0
        return sum(1 for s in self._tasks if s.success) / len(self._tasks)

    def avg_confidence(self) -> float:
        """Average confidence across recent tasks."""
        if not self._tasks:
            return 0.0
        return sum(s.confidence for s in self._tasks) / len(self._tasks)

    def avg_execution_time(self) -> float:
        """Average task execution time in ms."""
        if not self._tasks:
            return 0.0
        return sum(s.execution_time_ms for s in self._tasks) / len(self._tasks)

    def summary(self, active_ministers: int = 0) -> MetricsSummary:
        """Return aggregate metrics snapshot."""
        if self._first_task_ts and self._last_task_ts:
            window = self._last_task_ts - self._first_task_ts
        else:
            window = 0.0
        return MetricsSummary(
            total_tasks=len(self._tasks),
            successful_tasks=sum(1 for s in self._tasks if s.success),
            failed_tasks=sum(1 for s in self._tasks if not s.success),
            success_rate=self.success_rate(),
            avg_confidence=self.avg_confidence(),
            avg_execution_time_ms=self.avg_execution_time(),
            total_evolutions=len(self._evolutions),
            total_evolution_cycles=self._total_evolution_cycles,
            active_ministers=active_ministers,
            time_window_seconds=window,
            samples_in_buffer=len(self._tasks),
        )

    def clear(self) -> None:
        """Reset all collected metrics."""
        self._tasks.clear()
        self._evolutions.clear()
        self._error_count = 0
        self._total_evolution_cycles = 0
        self._first_task_ts = None
        self._last_task_ts = None

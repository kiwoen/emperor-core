"""Scheduler — background automation for evolution + task execution.

The Scheduler runs periodic evolution cycles and task batches
on a configurable schedule. Designed to be lightweight (threading only,
no external dependencies) and fully integrated with Emperor.

Usage:
    from jarvis.court.scheduler import Scheduler
    from jarvis.emperor import Emperor

    emp = Emperor()
    emp.register("turing", domain="math")

    sched = Scheduler(emp)
    sched.schedule_evolution(interval_minutes=30, cycles=3)
    sched.schedule_tasks(interval_minutes=5, templates=[
        {"prompt": "What is 2+2?", "domain": "math"},
        {"prompt": "Explain gravity", "domain": "science"},
    ])
    sched.start()
    # ... system runs autonomously ...
    sched.stop()
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# Types
# ══════════════════════════════════════════════════════════════════


class SchedulerState(Enum):
    """Scheduler lifecycle states."""

    IDLE = auto()
    RUNNING = auto()
    PAUSED = auto()
    STOPPING = auto()
    STOPPED = auto()


@dataclass
class ScheduleEntry:
    """A single scheduled job."""

    name: str
    interval_seconds: float
    action: Callable[[], Any]
    last_run: float = 0.0  # timestamp
    run_count: int = 0
    total_failures: int = 0
    enabled: bool = True
    tags: list[str] = field(default_factory=list)


@dataclass
class SchedulerReport:
    """Snapshot of scheduler state."""

    state: str
    running_since: float  # timestamp or 0
    entries: list[dict]
    total_runs: int
    total_failures: int


# ══════════════════════════════════════════════════════════════════
# Simple thread-safe timer wheel
# ══════════════════════════════════════════════════════════════════


class Scheduler:
    """Background scheduler for periodic evolution and tasks."""

    def __init__(self, emperor: Any = None) -> None:
        """Create a scheduler.

        Args:
            emperor: Optional Emperor instance. If not provided,
                     use add_job() to add jobs manually.
        """
        self._emperor = emperor
        self._entries: dict[str, ScheduleEntry] = {}
        self._state = SchedulerState.IDLE
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._started_at: float = 0.0

        # Alert + Healing integration
        self._alert_manager: Any = None   # injected by Emperor.serve
        self._healing_engine: Any = None  # injected by Emperor.serve

    @property
    def emperor(self) -> Any:
        """The Emperor instance this scheduler is attached to."""
        return self._emperor

    @emperor.setter
    def emperor(self, value: Any) -> None:
        self._emperor = value

    # ── Convenience methods (require emperor) ──────────────────────

    def schedule_evolution(
        self,
        interval_minutes: float = 30,
        cycles: int = 3,
    ) -> str:
        """Schedule periodic evolution cycles.

        Args:
            interval_minutes: Time between evolution runs.
            cycles: Number of evolution cycles per run.

        Returns:
            Entry name (for management).
        """
        if self._emperor is None:
            raise RuntimeError("Scheduler has no Emperor; use add_job() instead")

        name = "_auto_evolution"
        interval = interval_minutes * 60

        def run_evo() -> dict:
            return self._emperor.evolve(cycles=cycles)

        self.add_job(name, run_evo, interval, tags=["evolution"])
        logger.info(
            "[Scheduler] Auto-evolution: every %.1f min × %d cycles",
            interval_minutes, cycles,
        )
        return name

    def schedule_tasks(
        self,
        interval_minutes: float = 5,
        templates: Optional[list[dict]] = None,
    ) -> str:
        """Schedule periodic task batches.

        Args:
            interval_minutes: Time between task batches.
            templates: List of task dicts: {prompt, domain?, expected?}.
                       Defaults to a single "ping" task.

        Returns:
            Entry name (for management).
        """
        if self._emperor is None:
            raise RuntimeError("Scheduler has no Emperor; use add_job() instead")

        if templates is None:
            templates = [{"prompt": "Ping — confirm you are functional.",
                          "domain": "general"}]

        name = "_auto_tasks"
        interval = interval_minutes * 60

        # Capture current templates
        tmpls = list(templates)

        def run_tasks() -> list[dict]:
            return self._emperor.execute_batch(tmpls)

        self.add_job(name, run_tasks, interval, tags=["tasks"])
        logger.info(
            "[Scheduler] Auto-tasks: every %.1f min × %d templates",
            interval_minutes, len(tmpls),
        )
        return name

    # ── Generic job API ────────────────────────────────────────────

    def add_job(
        self,
        name: str,
        action: Callable[[], Any],
        interval_seconds: float,
        *,
        tags: Optional[list[str]] = None,
        enabled: bool = True,
    ) -> None:
        """Register a custom periodic job.

        Args:
            name: Unique job name (overwrites existing).
            action: Zero-arg callable.
            interval_seconds: Time between executions.
            tags: Optional labels for grouping/filtering.
            enabled: Whether to start enabled.
        """
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be > 0")

        entry = ScheduleEntry(
            name=name,
            interval_seconds=interval_seconds,
            action=action,
            tags=tags or [],
            enabled=enabled,
        )
        with self._lock:
            self._entries[name] = entry
        logger.debug("[Scheduler] Job added: %s (every %.0fs)", name, interval_seconds)

    def remove_job(self, name: str) -> bool:
        """Remove a job by name. Returns True if found."""
        with self._lock:
            if name in self._entries:
                del self._entries[name]
                logger.debug("[Scheduler] Job removed: %s", name)
                return True
        return False

    def get_job(self, name: str) -> Optional[ScheduleEntry]:
        """Get a job by name (returns a copy)."""
        with self._lock:
            entry = self._entries.get(name)
            if entry is None:
                return None
            return ScheduleEntry(
                name=entry.name,
                interval_seconds=entry.interval_seconds,
                action=entry.action,
                last_run=entry.last_run,
                run_count=entry.run_count,
                total_failures=entry.total_failures,
                enabled=entry.enabled,
                tags=list(entry.tags),
            )

    def list_jobs(self) -> list[ScheduleEntry]:
        """Return all registered jobs (copies)."""
        with self._lock:
            return [self.get_job(name) for name in self._entries]

    def enable_job(self, name: str) -> bool:
        """Enable a disabled job."""
        with self._lock:
            entry = self._entries.get(name)
            if entry is None:
                return False
            entry.enabled = True
            return True

    def disable_job(self, name: str) -> bool:
        """Disable a job."""
        with self._lock:
            entry = self._entries.get(name)
            if entry is None:
                return False
            entry.enabled = False
            return True

    # ── Lifecycle ──────────────────────────────────────────────────

    @property
    def state(self) -> SchedulerState:
        return self._state

    def start(self) -> None:
        """Start the scheduler background thread."""
        with self._lock:
            if self._state == SchedulerState.RUNNING:
                return
            if self._thread is not None and self._thread.is_alive():
                logger.warning("[Scheduler] already running")
                return
            self._state = SchedulerState.RUNNING
            self._started_at = time.time()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="emperor-scheduler",
                daemon=True,
            )
            self._thread.start()

        logger.info("[Scheduler] started — %d jobs", len(self._entries))

    def stop(self, *, timeout: float = 5.0) -> None:
        """Stop the scheduler gracefully.

        Args:
            timeout: Max seconds to wait for current job to finish.
        """
        with self._lock:
            if self._state in (SchedulerState.STOPPED, SchedulerState.IDLE):
                return
            self._state = SchedulerState.STOPPING

        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
            if thread.is_alive():
                logger.warning("[Scheduler] stop timeout — thread may persist")
            else:
                self._state = SchedulerState.STOPPED

        logger.info("[Scheduler] stopped — %d total runs",
                    sum(e.run_count for e in self._entries.values()))

    def pause(self) -> None:
        """Pause scheduling (current job completes, no new ones start)."""
        with self._lock:
            if self._state == SchedulerState.RUNNING:
                self._state = SchedulerState.PAUSED
                logger.info("[Scheduler] paused")

    def resume(self) -> None:
        """Resume after pause."""
        with self._lock:
            if self._state == SchedulerState.PAUSED:
                self._state = SchedulerState.RUNNING
                logger.info("[Scheduler] resumed")

    # ── Report ─────────────────────────────────────────────────────

    def report(self) -> SchedulerReport:
        """Snapshot of current scheduler state."""
        with self._lock:
            entries_data = []
            total_runs = 0
            total_failures = 0
            for e in self._entries.values():
                entries_data.append({
                    "name": e.name,
                    "interval_seconds": e.interval_seconds,
                    "last_run": e.last_run,
                    "run_count": e.run_count,
                    "failures": e.total_failures,
                    "enabled": e.enabled,
                    "tags": list(e.tags),
                })
                total_runs += e.run_count
                total_failures += e.total_failures

        return SchedulerReport(
            state=self._state.name,
            running_since=self._started_at,
            entries=entries_data,
            total_runs=total_runs,
            total_failures=total_failures,
        )

    # ── Internal loop ──────────────────────────────────────────────

    def _run_loop(self) -> None:
        """Main scheduler loop (runs in background thread)."""
        tick_interval = 0.5  # seconds between checks
        while True:
            with self._lock:
                if self._state == SchedulerState.STOPPING:
                    break
                if self._state == SchedulerState.PAUSED:
                    pass  # skip ticking while paused
                else:
                    self._tick()

            time.sleep(tick_interval)

    def _tick(self) -> None:
        """Process all due jobs on a single tick."""
        now = time.time()
        for entry in self._entries.values():
            if not entry.enabled:
                continue
            elapsed = now - entry.last_run
            if elapsed < entry.interval_seconds:
                continue

            # Fire the job
            entry.last_run = now
            entry.run_count += 1
            try:
                entry.action()
            except Exception:
                entry.total_failures += 1
                logger.exception("[Scheduler] Job '%s' failed", entry.name)

        # Alert evaluation + Self-healing
        if self._alert_manager is not None and self._emperor is not None:
            try:
                state = self._build_state()
                fired = self._alert_manager.evaluate(state)

                # Route fired alerts to healing engine
                if self._healing_engine is not None and fired:
                    rule_names = [a.rule_name for a in fired]
                    self._healing_engine.handle_batch(rule_names)
            except Exception:
                logger.exception("[Scheduler] Alert/Healing evaluation failed")

        # Built-in rule evaluation (condition-callable rules)
        if self._alert_manager is not None and self._emperor is not None:
            try:
                for rule_name in self._alert_manager.list_builtin_rules():
                    alert = self._alert_manager.fire_rule(rule_name, self._emperor)
                    if alert is not None and self._healing_engine is not None:
                        self._healing_engine.handle_batch([alert.rule_name])
            except Exception:
                logger.exception("[Scheduler] Built-in rule evaluation failed")

    def _build_state(self) -> dict:
        """Build a metrics dict for alert evaluation from the emperor."""
        court = self._emperor._court
        sched = self

        # Task metrics
        total_tasks = getattr(court, "_total_tasks", 0)
        completed = getattr(court, "_completed_tasks", 0)
        failed = getattr(court, "_failed_tasks", 0)
        success_rate = completed / max(total_tasks, 1)

        # Minister metrics
        ministers = getattr(court, "ministers", [])
        active_count = sum(1 for m in ministers if getattr(m, "status", "") == "active")

        # Scheduler metrics
        scheduler_running = 1 if self._state == SchedulerState.RUNNING else 0
        total_jobs = len(self._entries)
        job_failures = sum(e.total_failures for e in self._entries.values())

        return {
            "success_rate": round(success_rate, 4),
            "task_failures": failed,
            "total_tasks": total_tasks,
            "active_ministers": active_count,
            "total_ministers": len(ministers),
            "scheduler_running": scheduler_running,
            "total_jobs": total_jobs,
            "job_failures": job_failures,
        }

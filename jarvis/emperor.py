"""Emperor — one-line entry point for the evolutionary AI system.

Emperor bundles the Court, TaskEngine, REST API, and CLI into a single
orchestrator. Everything starts from here.

Usage:
    from jarvis.emperor import Emperor

    emp = Emperor()
    emp.register("turing", domain="math")
    emp.evolve(cycles=3)
    emp.execute_task("What is 17 * 23?", domain="math")
    emp.serve(port=9020)
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("jarvis.emperor")


# ══════════════════════════════════════════════════════════════════
# Configuration
# ══════════════════════════════════════════════════════════════════


@dataclass
class EmperorConfig:
    """Top-level Emperor configuration."""

    # Court
    min_ministers: int = 3
    max_ministers: int = 20
    genome_path: str = ""
    history_path: str = ""

    # Evolution
    crossover_rate: float = 0.6
    elitism_count: int = 2
    enable_auto_breeding: bool = True

    # API
    api_host: str = "127.0.0.1"
    api_port: int = 9020
    enable_api: bool = False

    # Auto-start (serve() one-command live dashboard)
    auto_schedule: bool = True
    auto_seed_ministers: bool = True
    auto_evolve_interval_minutes: float = 5.0
    auto_evolve_cycles: int = 1
    auto_tasks_interval_minutes: float = 3.0

    # Persistence
    data_dir: str = ""

    # Logging
    log_level: str = "INFO"

    # Runtime
    max_task_timeout: float = 30.0


# ══════════════════════════════════════════════════════════════════
# Emperor
# ══════════════════════════════════════════════════════════════════


class Emperor:
    """One-stop orchestrator for the evolutionary AI system.

    >>> emp = Emperor()
    >>> emp.register("turing", domain="math")
    >>> emp.evolve(cycles=5)
    >>> emp.status()
    """

    def __init__(self, config: Optional[EmperorConfig] = None) -> None:
        self.config = config or EmperorConfig()

        # Defer imports for fast startup
        from jarvis.court.court import Court, CourtConfig

        court_cfg = CourtConfig(
            min_ministers=self.config.min_ministers,
            max_ministers=self.config.max_ministers,
            crossover_rate=self.config.crossover_rate,
            elitism_count=self.config.elitism_count,
            enable_auto_breeding=self.config.enable_auto_breeding,
            genome_path=self.config.genome_path or None,
        )
        self._court = Court(config=court_cfg)

        from jarvis.court.task_engine import TaskEngine

        self._task_engine = TaskEngine(self._court)
        self._app: Any = None  # FastAPI app (lazy)
        self._scheduler: Any = None  # Scheduler (lazy)
        self._alert_manager: Any = None  # AlertManager (lazy)

        # Self-healing
        self._healing_engine: Any = None  # HealingEngine (lazy)

        # Plugin system
        from jarvis.plugin import LifecycleEvent, PluginManager
        self._plugin_manager: Any = PluginManager()

        # Eagerly register MetricsPlugin so every event from the very
        # first dispatch is captured.
        from jarvis.plugins import MetricsPlugin
        self._metrics_plugin: Any = MetricsPlugin()
        self._plugin_manager.register(self._metrics_plugin)

        self._dispatch(LifecycleEvent.ON_INIT, emperor=self)

        # Load persisted state if data_dir set
        if self.config.data_dir:
            self._load_state()

        logger.info("[Emperor] initialized — %d ministers",
                    len(self._court.active_ministers))

    # ── Court proxy ────────────────────────────────────────────────

    @property
    def court(self):
        """Direct access to the underlying Court."""
        return self._court

    @property
    def task_engine(self):
        """Direct access to the TaskEngine."""
        return self._task_engine

    @property
    def plugins(self):
        """Direct access to the PluginManager."""
        return self._plugin_manager

    def _dispatch(self, event: Any, **kwargs: Any) -> Any:
        """Dispatch a lifecycle event to all registered plugins."""
        return self._plugin_manager.dispatch(event, **kwargs)

    def register(self, name: str, domain: str = "general",
                 temperature: float = 0.7) -> None:
        """Register a new minister."""
        from jarvis.plugin import LifecycleEvent

        self._court.register(name, domain=domain,
                             temperature=temperature)
        self._dispatch(LifecycleEvent.ON_MINISTER_REGISTER,
                       minister_name=name, domain=domain,
                       temperature=temperature)

    def register_many(self, names: list[str], domain: str = "general",
                      temperature: float = 0.7) -> None:
        """Register multiple ministers at once."""
        specs = [{"name": n, "domain": domain, "temperature": temperature}
                 for n in names]
        self._court.register_many(specs)

    def evolve(self, cycles: int = 1) -> dict:
        """Run evolution cycles and return summary."""
        from jarvis.plugin import LifecycleEvent

        if cycles < 1:
            raise ValueError("cycles must be >= 1")
        self._dispatch(LifecycleEvent.ON_EVOLVE_START, cycles=cycles)
        try:
            result = self._court.evolve(cycles)
        except Exception as e:
            self._dispatch(LifecycleEvent.ON_TASK_ERROR, error=e,
                           context="evolve")
            raise
        self._dispatch(LifecycleEvent.ON_EVOLVE_END, result=result)
        return result

    # ── Task execution ─────────────────────────────────────────────

    def execute_task(
        self,
        prompt: str,
        *,
        domain: str = "general",
        expected: str = "",
        task_id: str = "",
    ) -> dict:
        """Execute a single task and return outcome as dict."""
        from jarvis.court.task_engine import TaskRequest
        from jarvis.plugin import LifecycleEvent

        if not task_id:
            import uuid
            task_id = uuid.uuid4().hex[:8]

        req = TaskRequest(
            id=task_id,
            prompt=prompt,
            domain=domain,
            expected=expected or None,
            deadline_seconds=self.config.max_task_timeout,
        )

        self._dispatch(LifecycleEvent.ON_TASK_BEFORE,
                       task_id=task_id, prompt=prompt, domain=domain)

        outcome = self._task_engine.execute(req)

        result = {
            "task_id": outcome.task_id,
            "minister": outcome.minister,
            "success": outcome.success,
            "confidence": outcome.confidence,
            "merit_score": outcome.merit_score,
            "execution_time_ms": outcome.execution_time_ms,
            "response": outcome.raw_response,
            "error": outcome.error,
        }

        if outcome.success:
            self._dispatch(LifecycleEvent.ON_TASK_AFTER, outcome=result)
        else:
            self._dispatch(LifecycleEvent.ON_TASK_ERROR,
                           task_id=task_id, error=outcome.error)

        return result

    def execute_batch(self, tasks: list[dict]) -> list[dict]:
        """Execute a batch of tasks. Each dict: {prompt, domain?, expected?}."""
        outcomes = []
        for i, t in enumerate(tasks):
            result = self.execute_task(
                prompt=t["prompt"],
                domain=t.get("domain", "general"),
                expected=t.get("expected", ""),
                task_id=t.get("task_id", ""),
            )
            outcomes.append(result)
        return outcomes

    # ── API server ─────────────────────────────────────────────────

    def serve(self, port: int = 0, host: str = "") -> None:
        """Start the REST API server (blocking).

        One-command live dashboard — by default this will:
          1. Auto-register a default minister lineup (if none exist).
          2. Auto-start the Scheduler with periodic evolution + tasks.

        Args:
            port: Port to listen on (uses config.api_port if 0).
            host: Host to bind (uses config.api_host if empty).
        """
        if port == 0:
            port = self.config.api_port or 9020
        if not host:
            host = self.config.api_host or "127.0.0.1"

        # One-command live dashboard: seed ministers + start scheduler
        if self.config.auto_seed_ministers:
            self._ensure_default_ministers()
        if self.config.auto_schedule:
            self._auto_start_scheduler()

        from jarvis.court_api import create_app

        app = create_app(court=self._court)
        app.extra["host"] = host
        app.extra["port"] = port
        app.extra["emperor"] = self

        # Inject scheduler state if running
        if self._scheduler is not None:
            r = self._scheduler.report()
            app.extra["scheduler_running"] = r.state == "RUNNING"
            app.extra["scheduler_jobs"] = len(r.entries)
            app.extra["scheduler_total_runs"] = r.total_runs
            # Wire alerts + healing into scheduler for auto-recovery
            self._scheduler._alert_manager = self.alerts
            self._scheduler._healing_engine = self.healing
        else:
            app.extra["scheduler_running"] = False
            app.extra["scheduler_jobs"] = 0
            app.extra["scheduler_total_runs"] = 0

        # Store alert_manager on app for dashboard access
        app.extra["alert_manager"] = self.alerts
        # Touch metrics so the plugin is registered before serving
        _ = self.metrics
        app.extra["metrics_plugin"] = self._metrics_plugin

        self._app = app

        import uvicorn

        logger.info("[Emperor] API + Dashboard → http://%s:%d", host, port)
        logger.info("[Emperor] Dashboard → http://%s:%d/dashboard", host, port)
        if self._scheduler is not None and self._scheduler.state.name == "RUNNING":
            r = self._scheduler.report()
            logger.info(
                "[Emperor] Scheduler RUNNING — %d jobs, evolution every %s min, tasks every %s min",
                len(r.entries),
                self.config.auto_evolve_interval_minutes,
                self.config.auto_tasks_interval_minutes,
            )
        uvicorn.run(app, host=host, port=port)

    # ── One-command live dashboard helpers ────────────────────────

    # Default minister lineup — one per major domain, ready for live
    # evolution on first `serve()`. Names follow the classical
    # Imperial Court theme.
    DEFAULT_MINISTERS: list[tuple[str, str]] = [
        ("turing",   "math"),
        ("curie",    "science"),
        ("hinton",   "code"),
        ("hippocrates", "medicine"),
        ("confucius",   "language"),
        ("tesla",    "engineering"),
        ("franklin", "research"),
        ("lovelace", "general"),
    ]

    def _ensure_default_ministers(self) -> int:
        """Auto-register a default minister lineup if the court is empty.

        Returns:
            Number of new ministers actually registered (0 if court
            was already populated).
        """
        existing = set(self._court.active_ministers)
        seeded: list[str] = []
        for name, domain in self.DEFAULT_MINISTERS:
            if name not in existing and len(self._court.active_ministers) < self.config.max_ministers:
                try:
                    self.register(name, domain=domain, temperature=0.7)
                    seeded.append(name)
                except Exception as e:  # pragma: no cover - safety net
                    logger.warning("[Emperor] seed register %s failed: %s", name, e)
        if seeded:
            logger.info("[Emperor] auto-seeded %d ministers: %s",
                        len(seeded), ", ".join(seeded))
        return len(seeded)

    def _auto_start_scheduler(self) -> bool:
        """Start the Scheduler with periodic evolution + tasks.

        No-op if the scheduler is already running or no ministers exist.
        Immediately runs the first evolution + task batch so the dashboard
        has live data from the moment the server boots.

        Returns:
            True if scheduler was started, False otherwise.
        """
        sched = self.scheduler
        if sched.state.name == "RUNNING":
            return False
        if not self._court.active_ministers:
            return False

        # Schedule periodic evolution + tasks.
        sched.schedule_evolution(
            interval_minutes=self.config.auto_evolve_interval_minutes,
            cycles=self.config.auto_evolve_cycles,
        )
        task_templates = [
            {"prompt": "Summarize today's key events.", "domain": "general"},
            {"prompt": "Compute 17 * 23 quickly.", "domain": "math"},
            {"prompt": "Suggest a refactor for a 200-line file.", "domain": "code"},
        ]
        sched.schedule_tasks(
            interval_minutes=self.config.auto_tasks_interval_minutes,
            templates=task_templates,
        )
        sched.start()
        logger.info(
            "[Emperor] auto-scheduler started: evolve every %.1f min, tasks every %.1f min",
            self.config.auto_evolve_interval_minutes,
            self.config.auto_tasks_interval_minutes,
        )

        # ── Immediate first run so dashboard shows live data on boot ──
        try:
            logger.info("[Emperor] running first evolution (%d cycles) …",
                        self.config.auto_evolve_cycles)
            self.evolve(cycles=self.config.auto_evolve_cycles)
        except Exception:
            logger.exception("[Emperor] first evolution failed")

        try:
            logger.info("[Emperor] running first task batch (%d tasks) …",
                        len(task_templates))
            self.execute_batch(task_templates)
        except Exception:
            logger.exception("[Emperor] first task batch failed")

        return True

    @property
    def app(self):
        """Lazy-loaded FastAPI app (for testing)."""
        if self._app is None:
            from jarvis.court_api import create_app
            self._app = create_app(court=self._court)
            self._app.extra.setdefault("host", self.config.api_host or "127.0.0.1")
            self._app.extra.setdefault("port", self.config.api_port or 9020)
        return self._app

    # ── Status / Dashboard ─────────────────────────────────────────

    def status(self) -> dict:
        """Return a comprehensive system status snapshot."""
        try:
            ranking = self._court.merit_ranking
            top_minister = ranking[0] if ranking else None
        except Exception:
            top_minister = None

        engine_summary = self._task_engine.summary()

        return {
            "version": "1.0",
            "court": {
                "active_ministers": len(self._court.active_ministers),
                "total_ministers": len(self._court.active_ministers),
                "cycle": self._court.cycle,
                "top_minister": str(top_minister) if top_minister else "none",
            },
            "tasks": {
                "total": engine_summary["total_tasks"],
                "completed": engine_summary["completed"],
                "failed": engine_summary["failed"],
                "success_rate": engine_summary["success_rate"],
                "avg_merit": engine_summary["avg_merit"],
            },
            "config": {
                "min_ministers": self.config.min_ministers,
                "max_ministers": self.config.max_ministers,
                "crossover_rate": self.config.crossover_rate,
                "api_port": self.config.api_port,
                "data_dir": self.config.data_dir or "none",
            },
        }

    def dashboard(self) -> str:
        """Return a human-readable dashboard string."""
        s = self.status()
        lines = [
            "=" * 48,
            "  Emperor Evolution Dashboard",
            "=" * 48,
            f"  Ministers : {s['court']['active_ministers']} active",
            f"  Cycle     : {s['court']['cycle']}",
            f"  Top       : {s['court']['top_minister']}",
            f"  Tasks     : {s['tasks']['total']} total "
            f"({s['tasks']['completed']} done, {s['tasks']['failed']} failed)",
            f"  Success   : {s['tasks']['success_rate']:.1%}",
            f"  Avg Merit : {s['tasks']['avg_merit']:.1f}",
            "=" * 48,
        ]
        return "\n".join(lines)

    # ── Persistence ────────────────────────────────────────────────

    def _load_state(self) -> None:
        """Load persisted state during init if data_dir is set."""
        target = Path(self.config.data_dir)
        if not target.is_dir():
            return

        genomes_file = target / "genomes.json"
        if genomes_file.exists():
            self._court.load_genomes(str(genomes_file))
        history_file = target / "history.json"
        if history_file.exists():
            self._court.load_history(str(history_file))

    def save(self, path: str = "") -> str:
        """Save all state (genomes + history) to disk."""
        target = Path(path) if path else (
            Path(self.config.data_dir) if self.config.data_dir
            else Path.cwd() / "emperor_data"
        )
        target.mkdir(parents=True, exist_ok=True)

        # Save genomes to a file in the target directory
        self._court._sm._genome_path = str(target / "genomes.json")
        self._court.save_genomes()
        self._court.save_history(str(target / "history.json"))
        logger.info("[Emperor] state saved → %s", target)
        return str(target)

    def load(self, path: str) -> None:
        """Load state from a directory."""
        target = Path(path)
        if not target.is_dir():
            raise FileNotFoundError(f"Data dir not found: {path}")

        genomes_file = target / "genomes.json"
        if genomes_file.exists():
            self._court.load_genomes(str(genomes_file))
        history_file = target / "history.json"
        if history_file.exists():
            self._court.load_history(str(history_file))

        logger.info("[Emperor] state loaded from %s", target)

    # ── Shutdown ───────────────────────────────────────────────────

    def shutdown(self) -> None:
        """Graceful shutdown — stop scheduler, save state, clean up."""
        from jarvis.plugin import LifecycleEvent

        self._dispatch(LifecycleEvent.ON_SHUTDOWN, emperor=self)
        if self._scheduler is not None:
            self._scheduler.stop()
        if self.config.data_dir:
            self.save()
        logger.info("[Emperor] shutdown complete")

    # ── Scheduler ──────────────────────────────────────────────────

    @property
    def scheduler(self):
        """Lazy-loaded scheduler for periodic automation."""
        if self._scheduler is None:
            from jarvis.court.scheduler import Scheduler
            self._scheduler = Scheduler(self)
        return self._scheduler

    def start_auto_evolve(self, every_minutes: float = 30,
                          cycles: int = 3) -> None:
        """Start automatic periodic evolution.

        Equivalent to: emp.scheduler.schedule_evolution(...); emp.scheduler.start()
        """
        self.scheduler.schedule_evolution(every_minutes, cycles)
        self.scheduler.start()

    def start_auto_tasks(self, every_minutes: float = 5,
                         templates: Optional[list[dict]] = None) -> None:
        """Start automatic periodic task execution.

        Equivalent to: emp.scheduler.schedule_tasks(...); emp.scheduler.start()
        """
        self.scheduler.schedule_tasks(every_minutes, templates)
        self.scheduler.start()

    # ── Alerts ─────────────────────────────────────────────────────

    @property
    def alerts(self):
        """Lazy-loaded AlertManager for health monitoring."""
        if self._alert_manager is None:
            from jarvis.alerts import AlertManager
            self._alert_manager = AlertManager()
        return self._alert_manager

    @property
    def metrics(self):
        """MetricsPlugin for performance telemetry (auto-registered on init)."""
        if self._metrics_plugin is None:
            # Should never happen — registered in __init__
            from jarvis.plugins import MetricsPlugin
            self._metrics_plugin = MetricsPlugin()
            self._plugin_manager.register(self._metrics_plugin)
        return self._metrics_plugin

    @property
    def healing(self):
        """Lazy-loaded HealingEngine for automatic recovery."""
        if self._healing_engine is None:
            from jarvis.healing import HealingEngine
            self._healing_engine = HealingEngine()
            self._register_default_healing_actions()
        return self._healing_engine

    def _register_default_healing_actions(self) -> None:
        """Register pre‑baked healing actions on first access."""
        from jarvis.healing import HealingAction
        from jarvis.healing_actions import (
            emergency_evolve, flush_logs, gc_collect,
            replenish_ministers, reset_task_engine,
            restart_scheduler, silence_alert_rule, stop_scheduler,
        )
        engine = self._healing_engine
        engine.register(HealingAction(
            name="restart_scheduler_if_stopped",
            alert_rule="scheduler_down",
            action=lambda: restart_scheduler(),
            cooldown_seconds=60,
            tags=["scheduler"],
        ))
        engine.register(HealingAction(
            name="emergency_evolve_on_minister_loss",
            alert_rule="low_ministers",
            action=lambda: replenish_ministers(min_count=self.config.min_ministers),
            cooldown_seconds=120,
            tags=["court"],
        ))
        engine.register(HealingAction(
            name="reset_task_engine_on_stall",
            alert_rule="task_stall",
            action=lambda: reset_task_engine(),
            cooldown_seconds=300,
            tags=["task_engine"],
        ))
        engine.register(HealingAction(
            name="silence_flooding_alerts",
            alert_rule="alert_flood",
            action=lambda: silence_alert_rule("alert_flood", duration_seconds=600),
            cooldown_seconds=900,
            tags=["alerts"],
        ))
        engine.register(HealingAction(
            name="periodic_gc_collect",
            alert_rule="high_memory",
            action=lambda: gc_collect(),
            cooldown_seconds=60,
            tags=["system"],
        ))
        logger.info("[Emperor] Registered %d default healing actions",
                    len(engine.list_actions()))

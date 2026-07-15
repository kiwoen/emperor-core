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
        self._app = None  # FastAPI app (lazy)

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

    def register(self, name: str, domain: str = "general",
                 temperature: float = 0.7) -> None:
        """Register a new minister."""
        self._court.register(name, domain=domain,
                             temperature=temperature)

    def register_many(self, names: list[str], domain: str = "general",
                      temperature: float = 0.7) -> None:
        """Register multiple ministers at once."""
        specs = [{"name": n, "domain": domain, "temperature": temperature}
                 for n in names]
        self._court.register_many(specs)

    def evolve(self, cycles: int = 1) -> dict:
        """Run evolution cycles and return summary."""
        if cycles < 1:
            raise ValueError("cycles must be >= 1")
        return self._court.evolve(cycles)

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
        outcome = self._task_engine.execute(req)

        return {
            "task_id": outcome.task_id,
            "minister": outcome.minister,
            "success": outcome.success,
            "confidence": outcome.confidence,
            "merit_score": outcome.merit_score,
            "execution_time_ms": outcome.execution_time_ms,
            "response": outcome.raw_response,
            "error": outcome.error,
        }

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

    def serve(self, port: int = 0) -> None:
        """Start the REST API server (blocking).

        Args:
            port: Port to listen on (uses config.api_port if 0).
        """
        if port == 0:
            port = self.config.api_port or 9020

        from jarvis.court_api import create_app

        app = create_app(court=self._court)
        self._app = app

        import uvicorn

        logger.info("[Emperor] API server → http://%s:%d",
                    self.config.api_host, port)
        uvicorn.run(app, host=self.config.api_host, port=port)

    @property
    def app(self):
        """Lazy-loaded FastAPI app (for testing)."""
        if self._app is None:
            from jarvis.court_api import create_app
            self._app = create_app(court=self._court)
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
        """Graceful shutdown — save state, clean up."""
        if self.config.data_dir:
            self.save()
        logger.info("[Emperor] shutdown complete")

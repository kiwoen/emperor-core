"""FastAPI REST API server for the Court evolutionary system.

Usage:
    python -m jarvis.court_api                       # default: 127.0.0.1:8000
    python -m jarvis.court_api --port 9000            # custom port
    python -m jarvis.court_api --config court.yaml    # config-driven

Endpoints:
    GET  /                        — server health
    GET  /court/summary           — court summary
    GET  /court/snapshot          — structured court state
    GET  /court/history           — evolution cycle history
    GET  /court/ministers         — list all ministers
    GET  /court/minister/{name}   — detail for one minister
    POST /court/register          — register a minister
    POST /court/register/batch    — bulk register
    POST /court/evolve            — run N evolution cycles
    POST /court/dispatch          — record a dispatch outcome
    POST /court/feedback          — record external feedback
    POST /court/genomes/save      — persist genomes
    POST /court/genomes/load      — load genomes from file
    POST /court/config/load       — load config from YAML
    GET  /court/config            — view current config
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from jarvis.court.config import SurvivalConfig
from jarvis.court.court import Court


# ══════════════════════════════════════════════════════════════════
# Request models (module-level for FastAPI type resolution)
# ══════════════════════════════════════════════════════════════════

class RegisterRequest(BaseModel):
    name: Optional[str] = None
    domain: str = "general"
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    confidence_baseline: float = Field(default=0.75, ge=0.0, le=1.0)


class BulkRegisterRequest(BaseModel):
    ministers: list[RegisterRequest]


class EvolveRequest(BaseModel):
    cycles: int = Field(default=1, ge=1, le=100)


class DispatchRequest(BaseModel):
    minister: str
    edict_id: str
    intent: str
    success: bool
    confidence: float = Field(ge=0.0, le=1.0)
    execution_time_ms: float = 0.0


class FeedbackRequest(BaseModel):
    minister: str
    edict_id: str
    score: float = Field(ge=0.0, le=1.0)


class GenomeLoadRequest(BaseModel):
    path: str


class ConfigLoadRequest(BaseModel):
    path: str


class DashboardExecuteRequest(BaseModel):
    prompt: str
    domain: Optional[str] = None


class ManualTaskRequest(BaseModel):
    prompt: str
    domain: str = "general"


class MinisterCreateRequest(BaseModel):
    name: str
    domain: str = "general"


class MinisterUpdateRequest(BaseModel):
    domain: Optional[str] = None
    merit: Optional[float] = None
    stability: Optional[float] = None


class SchedulerConfigRequest(BaseModel):
    evolve_interval_minutes: Optional[float] = Field(default=None, ge=1, le=1440)
    task_interval_minutes: Optional[float] = Field(default=None, ge=1, le=1440)
    auto_schedule: Optional[bool] = None


class ThemeRequest(BaseModel):
    theme: str = "dark"


class TemplateOptimizeRequest(BaseModel):
    capability: str = Field(..., description="Capability name to optimize")


class TemplateFeedbackRequest(BaseModel):
    capability: str = Field(..., description="Capability name")
    score: float = Field(..., ge=0.0, le=1.0, description="Feedback score 0.0~1.0")


class TemplateRollbackRequest(BaseModel):
    capability: str = Field(..., description="Capability name")
    version: int = Field(..., ge=1, description="Target version to rollback to")


class ApprovalActionRequest(BaseModel):
    note: str = ""


class ApprovalPolicyRequest(BaseModel):
    rule_type: str = Field(..., description="domain | risk_level | capability | keyword")
    rule_value: str = Field(..., description="Matching value")
    enabled: bool = True


class HealingToggleRequest(BaseModel):
    enabled: bool = True


# ══════════════════════════════════════════════════════════════════
# Module-level scheduler state (shared with Emperor.serve)
# ══════════════════════════════════════════════════════════════════

_scheduler_config: dict = {
    "evolve_interval_minutes": 5.0,
    "task_interval_minutes": 3.0,
    "auto_schedule": True,
}

_emperor_config = None
"""Module-level reference to the EmperorConfig (jarvis.yaml AppConfig).
Injected by Emperor.serve() via configure_app()."""


def configure_app(emperor_config=None):
    """Inject jarvis.yaml AppConfig so API endpoints can read/write it.

    Args:
        emperor_config: An AppConfig instance from jarvis.yaml.
    """
    global _emperor_config
    if emperor_config is not None:
        _emperor_config = emperor_config


# ══════════════════════════════════════════════════════════════════
# Factory
# ══════════════════════════════════════════════════════════════════

def create_app(
    config: SurvivalConfig | None = None,
    court: Court | None = None,
    eval_runner: Optional[Any] = None,
    audit_logger: Optional[Any] = None,
    template_manager: Optional[Any] = None,
) -> FastAPI:
    """Create a FastAPI app wired to a Court instance.

    Args:
        config: Optional SurvivalConfig to load.
        court: Optional pre-built Court instance to inject.
        eval_runner: Optional EvalRunner instance for /api/dashboard/evals endpoints.
        audit_logger: Optional AuditLogger instance for /api/dashboard/audit endpoints.
        template_manager: Optional PromptTemplateManager for adaptive prompt templates.
    """
    app = FastAPI(title="Emperor Court API", version="0.1.0")
    if court is None:
        court = Court()

    if config is not None and config.genome_path:
        court._sm.genome_path = config.genome_path

    # Inject eval_runner / audit_logger / template_manager into app.extra for dashboard endpoints
    app.extra["eval_runner"] = eval_runner
    app.extra["audit_logger"] = audit_logger
    app.extra["template_manager"] = template_manager

    # ── Endpoints ──────────────────────────────────────────────────

    @app.get("/")
    def root():
        return {
            "service": "emperor-court",
            "status": "ok",
            "config_loaded": config is not None,
        }

    @app.get("/court/summary")
    def get_summary():
        return {"summary": court.summary()}

    @app.get("/court/snapshot")
    def get_snapshot():
        return court.inspect.snapshot()

    @app.get("/court/history")
    def get_history():
        records = [court.history[i] for i in range(len(court.history))]
        return {"cycles": len(records), "records": records}

    @app.get("/court/ministers")
    def list_ministers():
        snap = court.inspect.snapshot()
        active = [m.name for m in snap.ministers if m.status == "active"]
        return {"active": active, "total": snap.total_ministers}

    @app.get("/court/minister/{name}")
    def get_minister(name: str):
        detail = court.inspect.minister_detail(name)
        if detail is None:
            raise HTTPException(404, f"Minister '{name}' not found")
        return {"detail": detail}

    @app.post("/court/register")
    def register_minister(req: RegisterRequest):
        name = court.register(
            name=req.name, domain=req.domain,
            temperature=req.temperature,
            confidence_baseline=req.confidence_baseline,
        )
        return {"name": name}

    @app.post("/court/register/batch")
    def register_batch(req: BulkRegisterRequest):
        specs = [
            {"name": m.name, "domain": m.domain,
             "temperature": m.temperature,
             "confidence_baseline": m.confidence_baseline}
            for m in req.ministers
        ]
        names = court.register_many(specs)
        return {"names": names, "count": len(names)}

    @app.post("/court/evolve")
    def run_evolution(req: EvolveRequest):
        return court.evolve(req.cycles)

    @app.post("/court/dispatch")
    def record_dispatch(req: DispatchRequest):
        court.record_dispatch(
            req.minister, req.edict_id, req.intent,
            req.success, req.confidence,
            execution_time_ms=req.execution_time_ms,
        )
        return {"message": "Dispatch recorded"}

    @app.post("/court/feedback")
    def record_feedback(req: FeedbackRequest):
        ok = court.record_feedback(req.minister, req.edict_id, req.score)
        if not ok:
            raise HTTPException(404, "Dispatch not found")
        return {"message": "Feedback recorded"}

    @app.post("/court/genomes/save")
    def save_genomes():
        path = court.save_genomes()
        if path is None:
            raise HTTPException(400, "No genome_path configured")
        return {"path": path}

    @app.post("/court/genomes/load")
    def load_genomes(req: GenomeLoadRequest):
        genomes, meta = court.load_genomes(req.path)
        return {
            "loaded": len(genomes),
            "metadata": meta,
            "active": court.active_ministers,
        }

    @app.post("/court/config/load")
    def load_config(req: ConfigLoadRequest):
        try:
            cfg = SurvivalConfig.from_yaml(req.path)
        except FileNotFoundError:
            raise HTTPException(404, f"Config file not found: {req.path}")
        if cfg.genome_path:
            court._sm.genome_path = cfg.genome_path
        return {"message": "Config loaded",
                "fields": list(cfg.__dataclass_fields__)}

    @app.get("/court/config")
    def get_config():
        gp = getattr(court._sm, "genome_path", None)
        return {
            "configured": config is not None or bool(gp),
            "genome_path": gp,
        }

    # ── Dashboard ───────────────────────────────────────────────────

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard():
        """Serve the monitoring dashboard."""
        from jarvis.dashboard_html import generate_html
        return generate_html(api_base=f"http://{app.extra.get('host', '127.0.0.1')}:{app.extra.get('port', 9020)}")

    @app.get("/dashboard/status")
    def dashboard_status():
        """Aggregated status for the dashboard frontend."""
        snap = court.inspect.snapshot()
        ranking = court.merit_ranking if hasattr(court, 'merit_ranking') else []

        ministers = []
        for m in snap.ministers:
            ministers.append({
                "name": m.name,
                "domain": getattr(m, "domain", "general"),
                "merit": getattr(m, "merit", 0.0),
                "confidence": getattr(m, "confidence", 0.0),
                "tasks_completed": getattr(m, "tasks_completed", 0),
                "success_rate": getattr(m, "success_rate", 0.0),
                "status": m.status,
            })

        # Sort by merit descending
        ministers.sort(key=lambda x: x["merit"], reverse=True)

        result = {
            "court": {
                "active_ministers": snap.active_count,
                "total_ministers": snap.total_ministers,
                "cycle": getattr(court, "cycle", 0),
                "top_minister": str(ranking[0]) if ranking else "none",
            },
            "ministers": ministers,
            "tasks": {
                "total": getattr(court, "_total_tasks", 0),
                "completed": getattr(court, "_completed_tasks", 0),
                "failed": getattr(court, "_failed_tasks", 0),
                "success_rate": getattr(court, "success_rate", 0.0),
                "avg_merit": getattr(court, "avg_merit", 0.0),
            },
            "config": {
                "min_ministers": getattr(court, "min_ministers", 0),
                "max_ministers": getattr(court, "max_ministers", 0),
                "crossover_rate": getattr(court, "crossover_rate", 0.0),
                "api_port": app.extra.get("port", 9020),
            },
            "scheduler_running": app.extra.get("scheduler_running", False),
            "scheduler_jobs": app.extra.get("scheduler_jobs", 0),
            "scheduler_total_runs": app.extra.get("scheduler_total_runs", 0),
        }
        return result

    @app.get("/dashboard/alerts")
    def dashboard_alerts():
        """Alert history and active rules for the dashboard."""
        mgr = app.extra.get("alert_manager")
        if mgr is None:
            return {"history": [], "rules": []}

        return {
            "history": [
                {
                    "rule_name": a.rule_name,
                    "severity": a.severity,
                    "message": a.message,
                    "metric": a.metric,
                    "current_value": a.current_value,
                    "threshold": a.threshold,
                    "operator": a.operator,
                    "timestamp": a.timestamp,
                }
                for a in mgr.history(limit=50)
            ],
            "rules": [
                {
                    "name": r.name,
                    "metric": r.metric,
                    "threshold": r.threshold,
                    "operator": r.operator,
                    "severity": r.severity,
                    "message": r.message,
                    "enabled": r.enabled,
                    "tags": r.tags,
                }
                for r in mgr.list_rules()
            ],
        }

    @app.get("/dashboard/metrics")
    def dashboard_metrics():
        """Performance metrics for the dashboard timeseries."""
        mp = app.extra.get("metrics_plugin")
        if mp is None:
            return {"summary": {}, "tasks": [], "evolutions": []}

        sn = court.inspect.snapshot()
        s = mp.summary(active_ministers=sn.active_count)

        tasks = []
        for t in mp.task_history(limit=100):
            tasks.append({
                "task_id": t.task_id,
                "timestamp": t.timestamp,
                "success": t.success,
                "confidence": t.confidence,
                "execution_time_ms": t.execution_time_ms,
                "domain": t.domain,
                "error": t.error,
            })

        evos = []
        for e in mp.evolution_history(limit=50):
            evos.append({
                "timestamp": e.timestamp,
                "cycles": e.cycles,
                "active_ministers": e.active_ministers,
                "avg_merit": e.avg_merit,
            })

        return {
            "summary": {
                "total_tasks": s.total_tasks,
                "successful_tasks": s.successful_tasks,
                "failed_tasks": s.failed_tasks,
                "success_rate": s.success_rate,
                "avg_confidence": s.avg_confidence,
                "avg_execution_time_ms": s.avg_execution_time_ms,
                "total_evolutions": s.total_evolutions,
                "total_evolution_cycles": s.total_evolution_cycles,
                "active_ministers": s.active_ministers,
                "time_window_seconds": s.time_window_seconds,
                "samples_in_buffer": s.samples_in_buffer,
            },
            "tasks": tasks,
            "evolutions": evos,
        }

    # ── Dashboard control panel endpoints ──────────────────────────

    @app.post("/dashboard/evolve")
    def dashboard_evolve():
        """Manually trigger evolution cycles."""
        emperor = app.extra.get("emperor")
        if emperor is None:
            raise HTTPException(503, "Emperor not available")
        try:
            result = emperor.evolve(cycles=1)
        except Exception as e:
            raise HTTPException(500, f"Evolution failed: {e}")
        return {
            "ok": True,
            "generation": result.get("generation", 0),
            "count": result.get("count", 0),
        }

    @app.post("/dashboard/execute")
    def dashboard_execute(req: DashboardExecuteRequest):
        """Manually execute a task."""
        emperor = app.extra.get("emperor")
        if emperor is None:
            raise HTTPException(503, "Emperor not available")
        prompt = req.prompt
        domain = req.domain or "general"
        if not prompt:
            raise HTTPException(400, "prompt is required")
        try:
            result = emperor.execute_task(prompt, domain=domain)
        except Exception as e:
            raise HTTPException(500, f"Task execution failed: {e}")
        return {
            "ok": True,
            "task_id": result.get("task_id", ""),
            "minister": result.get("minister", ""),
            "confidence": result.get("confidence", 0.0),
        }

    @app.post("/api/manual_task")
    def manual_task(req: ManualTaskRequest):
        """Execute a manual task with inline form submission. Returns report + id."""
        prompt = req.prompt.strip()
        if not prompt:
            raise HTTPException(400, "任务描述不能为空")

        emperor = app.extra.get("emperor")
        if emperor is None:
            raise HTTPException(503, "Emperor not available")

        try:
            result = emperor.execute_task(prompt, domain=req.domain)
        except Exception as e:
            raise HTTPException(500, f"Task execution failed: {e}")

        return {
            "report": result.get("response", ""),
            "id": result.get("task_id", ""),
        }

    @app.post("/dashboard/heal")
    def dashboard_heal():
        """Manually trigger self-healing check on recent alerts."""
        emperor = app.extra.get("emperor")
        if emperor is None:
            raise HTTPException(503, "Emperor not available")
        try:
            # Collect unique alert rule names from recent alert history
            alert_mgr = emperor.alerts
            rule_names = list({a.rule_name for a in alert_mgr.history(limit=50)})
            records = emperor.healing.handle_batch(rule_names)
        except Exception as e:
            raise HTTPException(500, f"Healing check failed: {e}")
        return {
            "ok": True,
            "actions": [
                {
                    "action_name": r.action_name,
                    "alert_rule": r.alert_rule,
                    "success": r.success,
                    "error": r.error,
                }
                for r in records
            ],
        }

    @app.get("/dashboard/task-history")
    def dashboard_task_history(
        minister: str | None = None,
        status: str | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ):
        """Return task history with optional filtering (newest first)."""
        db = app.extra.get("db")
        if db is None:
            return {"history": [], "note": "Database not initialized"}
        try:
            rows = db.get_task_history(
                limit=limit, minister=minister,
                status=status, search=search, offset=offset,
            )
            return {"history": rows, "count": len(rows)}
        except Exception as e:
            raise HTTPException(500, f"Failed to read task history: {e}")

    @app.get("/dashboard/evolution-history")
    def dashboard_evolution_history():
        """Return recent evolution history from the database (newest first)."""
        db = app.extra.get("db")
        if db is None:
            return {"history": [], "note": "Database not initialized"}
        try:
            rows = db.get_evolution_history(limit=100)
            return {"history": rows, "count": len(rows)}
        except Exception as e:
            raise HTTPException(500, f"Failed to read evolution history: {e}")

    @app.get("/dashboard/alert-history")
    def dashboard_alert_history(
        level: str | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ):
        """Return alert history with optional filtering (newest first)."""
        db = app.extra.get("db")
        if db is None:
            return {"history": [], "note": "Database not initialized"}
        try:
            rows = db.get_alert_history(
                limit=limit, level=level, search=search, offset=offset,
            )
            return {"history": rows, "count": len(rows)}
        except Exception as e:
            raise HTTPException(500, f"Failed to read alert history: {e}")

    @app.get("/dashboard/export")
    def dashboard_export(
        format: str = "json",
        what: str = "all",
    ):
        """Export dashboard data in JSON or CSV format."""
        db = app.extra.get("db")
        if db is None:
            raise HTTPException(503, "Database not initialized")
        try:
            data = db.export_all()
        except Exception as e:
            raise HTTPException(500, f"Failed to export data: {e}")

        # Filter by what
        if what == "tasks":
            data = {"tasks": data["tasks"]}
        elif what == "alerts":
            data = {"alerts": data["alerts"]}
        elif what == "evolutions":
            data = {"evolutions": data["evolutions"]}

        if format == "csv":
            import csv
            import io

            output = io.StringIO()
            writer = csv.writer(output)

            tables = [
                ("TASKS", data.get("tasks", [])),
                ("ALERTS", data.get("alerts", [])),
                ("EVOLUTIONS", data.get("evolutions", [])),
            ]

            first_section = True
            for section_name, rows in tables:
                if not rows:
                    continue
                if not first_section:
                    output.write("---\n")
                first_section = False

                # Header
                writer.writerow(rows[0].keys())
                for row in rows:
                    writer.writerow(row.values())

            from fastapi.responses import Response
            return Response(
                content=output.getvalue(),
                media_type="text/csv",
                headers={"Content-Disposition": "attachment; filename=emperor_export.csv"},
            )

        # JSON format
        return data

    # ── Minister management API ─────────────────────────────────────

    VALID_DOMAINS = ["general", "math", "data", "code", "legal", "science", "creative"]

    @app.get("/api/ministers")
    def api_list_ministers():
        """List all ministers with name, domain, merit, stability."""
        snap = court.inspect.snapshot()
        ministers = []
        for m in snap.ministers:
            genome = court._sm._genomes.get(m.name)
            merit = m.merit
            if genome is not None and hasattr(genome, "_merit_override"):
                merit = genome._merit_override

            # Extract task-feedback fields from genome (default 0 for legacy)
            success_streak = getattr(genome, "success_streak", 0)
            failure_streak = getattr(genome, "failure_streak", 0)
            total_tasks = getattr(genome, "total_tasks", 0)
            capability_hits = getattr(genome, "capability_hits", 0)

            ministers.append({
                "name": m.name,
                "domain": m.domain,
                "merit": round(merit, 1),
                "stability": round(getattr(m, "confidence_baseline", 0.75), 2),
                "success_streak": success_streak,
                "failure_streak": failure_streak,
                "total_tasks": total_tasks,
                "capability_hits": capability_hits,
            })
        return {"ministers": ministers}

    @app.post("/api/ministers")
    def api_create_minister(req: MinisterCreateRequest):
        """Create a new minister."""
        name = req.name.strip()
        if not name:
            raise HTTPException(400, "名称不能为空")

        if name in court._sm._genomes:
            raise HTTPException(400, "大臣已存在")

        if req.domain not in VALID_DOMAINS:
            raise HTTPException(400, f"无效领域: {req.domain}")

        court.register(name=name, domain=req.domain)
        return {
            "minister": {
                "name": name,
                "domain": req.domain,
                "merit": 0.0,
                "stability": 0.75,
                "success_streak": 0,
                "failure_streak": 0,
                "total_tasks": 0,
                "capability_hits": 0,
            },
            "message": f"大臣 {name} 已创建",
        }

    @app.put("/api/ministers/{name}")
    def api_update_minister(name: str, req: MinisterUpdateRequest):
        """Update a minister's domain, merit, or stability."""
        genome = court._sm._genomes.get(name)
        if genome is None:
            raise HTTPException(404, f"大臣 {name} 不存在")

        updated = False

        if req.domain is not None:
            if req.domain not in VALID_DOMAINS:
                raise HTTPException(400, f"无效领域: {req.domain}")
            genome.domain = req.domain
            updated = True

        if req.merit is not None:
            if req.merit < 0:
                raise HTTPException(400, "功绩不能为负数")
            genome._merit_override = float(req.merit)
            updated = True

        if req.stability is not None:
            if req.stability < 0 or req.stability > 1:
                raise HTTPException(400, "稳定度必须在 0-1 之间")
            genome.confidence_baseline = float(req.stability)
            updated = True

        if not updated:
            raise HTTPException(400, "至少需要提供一个更新字段")

        # Recompute merit considering override
        merit = 0.0
        if hasattr(genome, "_merit_override"):
            merit = genome._merit_override
        elif court._sm._merit_board is not None:
            merit = court._sm._merit_board.compute_merit(name)

        return {
            "minister": {
                "name": name,
                "domain": genome.domain,
                "merit": round(merit, 1),
                "stability": round(genome.confidence_baseline, 2),
            },
        }

    @app.delete("/api/ministers/{name}")
    def api_delete_minister(name: str):
        """Delete a minister permanently."""
        if name not in court._sm._genomes:
            raise HTTPException(404, f"大臣 {name} 不存在")

        del court._sm._genomes[name]
        if name in court._sm._statuses:
            del court._sm._statuses[name]

        return {"message": f"大臣 {name} 已删除"}

    @app.get("/api/scheduler/config")
    def api_get_scheduler_config():
        """Return current scheduler configuration."""
        return {
            "evolve_interval_minutes": _scheduler_config["evolve_interval_minutes"],
            "task_interval_minutes": _scheduler_config["task_interval_minutes"],
            "auto_schedule": _scheduler_config["auto_schedule"],
        }

    @app.put("/api/scheduler/config")
    def api_put_scheduler_config(req: SchedulerConfigRequest):
        """Update scheduler configuration in real-time."""
        updated_fields: list[str] = []

        # Validate and update evolve_interval
        if req.evolve_interval_minutes is not None:
            val = float(req.evolve_interval_minutes)
            if val != int(val):
                raise HTTPException(400, "进化间隔必须为整数分钟")
            _scheduler_config["evolve_interval_minutes"] = val
            updated_fields.append("evolve_interval_minutes")

        # Validate and update task_interval
        if req.task_interval_minutes is not None:
            val = float(req.task_interval_minutes)
            if val != int(val):
                raise HTTPException(400, "任务间隔必须为整数分钟")
            _scheduler_config["task_interval_minutes"] = val
            updated_fields.append("task_interval_minutes")

        # Handle auto_schedule toggle
        if req.auto_schedule is not None:
            prev = _scheduler_config["auto_schedule"]
            _scheduler_config["auto_schedule"] = req.auto_schedule
            updated_fields.append("auto_schedule")

            # Apply to live scheduler if available
            sched = getattr(court, "scheduler", None)
            if sched is not None:
                if req.auto_schedule and not prev:
                    sched.resume()
                elif not req.auto_schedule and prev:
                    sched.pause()

        # Apply interval updates to live scheduler
        if ("evolve_interval_minutes" in updated_fields or
                "task_interval_minutes" in updated_fields):
            sched = getattr(court, "scheduler", None)
            if sched is not None:
                sched.update_config(
                    task_interval_seconds=(
                        _scheduler_config["task_interval_minutes"] * 60
                    ),
                    evolve_interval_seconds=(
                        _scheduler_config["evolve_interval_minutes"] * 60
                    ),
                )

        return {
            "config": dict(_scheduler_config),
            "updated": updated_fields,
        }

    # ── Dashboard config endpoint ─────────────────────────────────

    @app.get("/api/config")
    def api_get_config():
        """Return dashboard-visible configuration."""
        # Prefer _emperor_config (injected by Emperor.serve), fallback to app.extra
        app_cfg = _emperor_config
        if app_cfg is None:
            emperor = getattr(app, "extra", {}).get("emperor")
            app_cfg = getattr(emperor, "app_config", None) if emperor else None

        theme = "dark"
        refresh = 15
        if app_cfg is not None:
            theme = getattr(app_cfg.dashboard, "theme", "dark")
            refresh = getattr(app_cfg.dashboard, "refresh_interval_seconds", 15)

        return {
            "theme": theme,
            "refresh_interval_seconds": refresh,
        }

    @app.post("/api/theme")
    def api_set_theme(req: ThemeRequest):
        """Set dashboard theme and persist to jarvis.yaml."""
        import json as _json
        import os as _os

        theme = req.theme

        if theme not in ("dark", "light", "auto"):
            raise HTTPException(400, "Invalid theme. Use dark, light, or auto")

        global _emperor_config

        if _emperor_config is not None:
            _emperor_config.dashboard.theme = theme

        # Persist to jarvis.yaml
        config_path = "jarvis.yaml"
        if _os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                raw = _json.load(f)
            raw.setdefault("dashboard", {})["theme"] = theme
            with open(config_path, "w", encoding="utf-8") as f:
                _json.dump(raw, f, indent=2, ensure_ascii=False)

        return {"theme": theme, "status": "ok"}

    # ── Health monitoring endpoint ──────────────────────────────

    @app.get("/api/health")
    def health_check():
        """系统健康检查端点（CPU/内存/磁盘/运行时长）"""
        from jarvis.health import get_system_health

        return get_system_health()

    # ── Dashboard live data endpoint ────────────────────────────

    @app.get("/api/dashboard/live")
    def dashboard_live():
        """聚合天气和新闻实时数据"""
        from jarvis.capability import _weather_handler, _news_handler

        weather_city = "北京"
        if _emperor_config is not None:
            weather_city = getattr(_emperor_config.dashboard, "weather_city", "北京")

        weather_result = _weather_handler(weather_city + "天气")
        news_result = _news_handler("科技新闻")

        return {
            "weather": weather_result.get("data", {}),
            "weather_text": weather_result.get("result", "天气获取失败"),
            "news": news_result.get("data", {}),
            "news_text": news_result.get("result", "新闻获取失败"),
        }

    # ── Dashboard capability stats endpoint ──────────────────────

    @app.get("/api/dashboard/capability-stats")
    def capability_stats():
        """能力命中统计（饼图数据）"""
        db = app.extra.get("db")
        if db is None:
            return {"labels": [], "values": [], "total": 0}

        tasks = db.get_task_history(limit=10000)
        stats: dict[str, int] = {}
        for t in tasks:
            cap = t.get("capability", "") or "general"
            stats[cap] = stats.get(cap, 0) + 1

        sorted_stats = sorted(stats.items(), key=lambda x: x[1], reverse=True)

        return {
            "labels": [s[0] for s in sorted_stats],
            "values": [s[1] for s in sorted_stats],
            "total": sum(s[1] for s in sorted_stats),
        }

    # ── Dashboard Evals endpoints ─────────────────────────────────

    @app.get("/api/dashboard/evals/report")
    def evals_report():
        """返回最近一次 eval 聚合报告。"""
        runner = app.extra.get("eval_runner")
        if runner is None:
            return {
                "total_suites": 0,
                "total_cases": 0,
                "passed": 0,
                "failed": 0,
                "errored": 0,
                "pass_rate": 0,
                "suites": [],
            }
        return runner.report()

    @app.post("/api/dashboard/evals/run")
    def evals_run():
        """运行所有内置评测套件，返回报告。"""
        runner = app.extra.get("eval_runner")
        if runner is None:
            raise HTTPException(status_code=503, detail="EvalRunner not available")

        try:
            from jarvis.eval import create_builtin_suites

            suites = create_builtin_suites()
            runner.run_all(suites)
            return runner.report()
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ── Dashboard Audit endpoints ─────────────────────────────────

    @app.get("/api/dashboard/model-costs")
    def model_costs():
        """Return model router cost statistics."""
        router = app.extra.get("model_router")
        if router is None:
            return {
                "total_requests": 0,
                "requests_by_tier": {"cheap": 0, "standard": 0, "premium": 0},
                "estimated_cost_saved": 0.0,
                "savings_percent": 0.0,
                "tier_distribution": {"cheap": 0, "standard": 0, "premium": 0},
                "router_enabled": False,
            }
        report = router.report()
        report["router_enabled"] = True
        return report

    def _serialize_audit_entry(entry: Any) -> dict:
        """Convert AuditEntry dataclass → JSON-safe dict."""
        return {
            "id": getattr(entry, "id", 0),
            "trace_id": entry.trace_id,
            "step": entry.step,
            "phase": entry.phase,
            "action": entry.action,
            "actor": entry.actor,
            "input_summary": entry.input_summary,
            "output_summary": entry.output_summary,
            "success": entry.success,
            "error_msg": entry.error_msg,
            "duration_ms": getattr(entry, "duration_ms", 0),
            "created_at": entry.created_at,
        }

    @app.get("/api/dashboard/audit/recent")
    def audit_recent(limit: int = 50):
        """返回最近 N 条审计记录。"""
        logger = app.extra.get("audit_logger")
        if logger is None:
            return {"entries": [], "total": 0}

        try:
            entries = logger.reader().query_recent(min(limit, 200))
            return {
                "entries": [_serialize_audit_entry(e) for e in entries],
                "total": len(entries),
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/dashboard/audit/stats")
    def audit_stats():
        """返回审计统计摘要。"""
        logger = app.extra.get("audit_logger")
        if logger is None:
            return {
                "total_entries": 0,
                "successes": 0,
                "failures": 0,
                "success_rate": 0,
                "db_size_bytes": 0,
                "top_actions": [],
            }

        try:
            return logger.reader().get_stats()
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/dashboard/audit/failures")
    def audit_failures(limit: int = 50):
        """返回最近失败记录列表。"""
        logger = app.extra.get("audit_logger")
        if logger is None:
            return {"entries": [], "total": 0}

        try:
            entries = logger.reader().query_failures(min(limit, 200))
            return {
                "entries": [_serialize_audit_entry(e) for e in entries],
                "total": len(entries),
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ── Prompt Template Dashboard endpoints ────────────────────────

    def _get_template_manager():
        """Resolve template_manager: prefer app.extra, fallback to module-level."""
        mgr = app.extra.get("template_manager")
        if mgr is None:
            from jarvis.capability import get_template_manager
            mgr = get_template_manager()
        if mgr is None:
            raise HTTPException(status_code=503, detail="PromptTemplateManager not available")
        return mgr

    @app.get("/api/dashboard/templates")
    def list_templates():
        """返回所有 capability 模板及其版本和评分。"""
        mgr = _get_template_manager()
        return mgr.list_templates()

    @app.post("/api/dashboard/templates/optimize")
    def optimize_template(body: TemplateOptimizeRequest):
        """对指定 capability 执行自动优化。"""
        mgr = _get_template_manager()
        try:
            result = mgr.auto_optimize(body.capability)
            return {
                "capability": body.capability,
                "version": result.get("version"),
                "performance_score": result.get("performance_score"),
                "system_prompt": result.get("system_prompt"),
                "frozen": result.get("frozen", False),
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/dashboard/templates/feedback")
    def record_feedback(body: TemplateFeedbackRequest):
        """记录用户反馈分数并更新 performance_score。"""
        mgr = _get_template_manager()
        try:
            result = mgr.record_feedback(body.capability, body.score)
            return {
                "capability": body.capability,
                "performance_score": result.get("performance_score"),
                "version": result.get("version"),
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/dashboard/templates/rollback")
    def rollback_template(body: TemplateRollbackRequest):
        """回滚模板到指定历史版本。"""
        mgr = _get_template_manager()
        try:
            result = mgr.rollback(body.capability, body.version)
            return {
                "capability": body.capability,
                "version": result.get("version"),
                "performance_score": result.get("performance_score"),
                "system_prompt": result.get("system_prompt"),
            }
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ── SSE streaming endpoint ────────────────────────────────────

    @app.get("/api/events")
    def sse_events():
        """Server-Sent Events stream for real-time dashboard updates."""
        import json as _json  # local alias to avoid shadowing module-level

        from jarvis.event_bus import event_bus

        q, sub_id = event_bus.subscribe()

        def generate():
            try:
                # Initial connection event
                yield f"data: {_json.dumps({'type': 'connected', 'data': {}})}\n\n"

                while True:
                    try:
                        data = q.get(timeout=30)
                        yield f"data: {data}\n\n"
                    except Exception:
                        # timeout → send heartbeat to keep alive
                        yield f"data: {_json.dumps({'type': 'heartbeat', 'data': {}})}\n\n"
            except GeneratorExit:
                pass
            finally:
                event_bus.unsubscribe(sub_id)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ── Pipeline endpoints ─────────────────────────────────────

    class PipelineExecuteRequest(BaseModel):
        template: str = "daily_brief"
        context: dict = Field(default_factory=dict)

    @app.post("/api/pipelines/execute")
    def execute_pipeline(body: PipelineExecuteRequest):
        """执行服务流水线"""
        try:
            from jarvis.pipeline import pipeline_registry, PipelineStatus

            template = body.template
            context = body.context

            if template == "search_analyze":
                query = context.get("query", "")
                result = pipeline_registry.execute_template(template, context, query=query)
            else:
                result = pipeline_registry.execute_template(template, context)

            return {
                "status": result.status.value,
                "pipeline_name": result.pipeline_name,
                "pipeline_id": result.pipeline_id,
                "stages": [
                    {"name": s.stage_name, "status": s.status.value}
                    for s in result.stages
                ],
                "duration": round(result.finished_at - result.started_at, 2),
                "final_output": (
                    result.final_output
                    if result.status == PipelineStatus.COMPLETED else {}
                ),
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/pipelines/history")
    def pipeline_history(limit: int = 20):
        """流水线执行历史"""
        from jarvis.pipeline import pipeline_registry

        return pipeline_registry.get_history(limit)

    @app.get("/api/pipelines/templates")
    def pipeline_templates():
        """可用的流水线模板列表"""
        from jarvis.pipeline import pipeline_registry

        return {"templates": list(pipeline_registry._templates.keys())}

    # ── Pipeline scheduler endpoints ─────────────────────────────

    @app.post("/api/pipelines/schedule")
    def add_pipeline_schedule():
        """添加流水线定时调度"""
        data = request.get_json() or {}
        template = data.get("template", "daily_brief")
        interval_minutes = data.get("interval_minutes", 1440)  # 默认每天
        context = data.get("context", {})
        cron_expr = data.get("cron_expr")  # 可选

        import uuid

        job_id = f"job_{uuid.uuid4().hex[:8]}"

        try:
            from jarvis.pipeline import pipeline_scheduler

            pipeline_scheduler.add_schedule(
                job_id=job_id,
                template_name=template,
                interval_minutes=interval_minutes,
                context=context,
                cron_expr=cron_expr,
            )
            return jsonify({"job_id": job_id, "status": "scheduled"})
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.delete("/api/pipelines/schedule/<job_id>")
    def remove_pipeline_schedule(job_id):
        """删除定时调度"""
        from jarvis.pipeline import pipeline_scheduler

        success = pipeline_scheduler.remove_schedule(job_id)
        return jsonify({"job_id": job_id, "removed": success})

    @app.post("/api/pipelines/schedule/<job_id>/toggle")
    def toggle_pipeline_schedule(job_id):
        """启用/禁用定时调度"""
        data = request.get_json() or {}
        enabled = data.get("enabled", True)

        from jarvis.pipeline import pipeline_scheduler

        if enabled:
            success = pipeline_scheduler.enable_job(job_id)
        else:
            success = pipeline_scheduler.disable_job(job_id)

        return jsonify({"job_id": job_id, "enabled": enabled, "success": success})

    @app.get("/api/pipelines/schedule")
    def list_pipeline_schedules():
        """列出所有定时调度"""
        from jarvis.pipeline import pipeline_scheduler

        return jsonify(pipeline_scheduler.get_jobs())

    # ── Pipeline Monitor API ──────────────────────────────────────

    @app.get("/api/pipelines/monitor/summary")
    def pipeline_monitor_summary():
        """流水线监控摘要：总览、活跃、成功率、时间线"""
        from jarvis.pipeline_monitor import pipeline_monitor
        return pipeline_monitor.get_summary()

    @app.get("/api/pipelines/monitor/dag/<pipeline_id>")
    def pipeline_monitor_dag(pipeline_id: str):
        """单条流水线的 DAG 详情（节点 + 边）"""
        from jarvis.pipeline_monitor import pipeline_monitor
        dag = pipeline_monitor.get_dag(pipeline_id)
        if dag is None:
            raise HTTPException(status_code=404, detail=f"Pipeline '{pipeline_id}' not found")
        return dag

    @app.get("/api/pipelines/monitor/timeline/<pipeline_id>")
    def pipeline_monitor_timeline(pipeline_id: str):
        """单条流水线的执行时间线"""
        from jarvis.pipeline_monitor import pipeline_monitor
        timeline = pipeline_monitor.get_timeline(pipeline_id)
        if timeline is None:
            raise HTTPException(status_code=404, detail=f"Pipeline '{pipeline_id}' not found")
        return {"pipeline_id": pipeline_id, "timeline": timeline}

    @app.get("/api/pipelines/monitor/live")
    def pipeline_monitor_live():
        """实时流水线状态（轻量轮询）"""
        from jarvis.pipeline_monitor import pipeline_monitor
        return pipeline_monitor.get_live()

    # ── Background heartbeat thread ───────────────────────────────

    import threading
    import time as _time

    def _start_heartbeat():
        from jarvis.event_bus import event_bus

        def _beat():
            while True:
                _time.sleep(15)
                try:
                    event_bus.publish_heartbeat()
                except Exception:
                    pass

        t = threading.Thread(target=_beat, daemon=True)
        t.start()

    _start_heartbeat()

    # ══════════════════════════════════════════════════════════════
    # Plugin Marketplace API
    # ══════════════════════════════════════════════════════════════

    class PluginInstallRequest(BaseModel):
        plugin_id: str

    class PluginToggleRequest(BaseModel):
        plugin_id: str
        enabled: bool

    class PluginConfigRequest(BaseModel):
        plugin_id: str
        config: dict = Field(default_factory=dict)

    @app.get("/api/dashboard/plugins")
    def get_plugins(request: Request):
        mp = request.app.extra.get("plugin_marketplace")
        if mp is None:
            raise HTTPException(status_code=503, detail="Plugin marketplace not available")
        return mp.report()

    @app.post("/api/dashboard/plugins/install")
    def install_plugin(payload: PluginInstallRequest, request: Request):
        mp = request.app.extra.get("plugin_marketplace")
        if mp is None:
            raise HTTPException(status_code=503, detail="Plugin marketplace not available")
        try:
            return mp.install(payload.plugin_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.post("/api/dashboard/plugins/uninstall")
    def uninstall_plugin(payload: PluginInstallRequest, request: Request):
        mp = request.app.extra.get("plugin_marketplace")
        if mp is None:
            raise HTTPException(status_code=503, detail="Plugin marketplace not available")
        try:
            return mp.uninstall(payload.plugin_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.post("/api/dashboard/plugins/toggle")
    def toggle_plugin(payload: PluginToggleRequest, request: Request):
        mp = request.app.extra.get("plugin_marketplace")
        if mp is None:
            raise HTTPException(status_code=503, detail="Plugin marketplace not available")
        try:
            if payload.enabled:
                return mp.enable(payload.plugin_id)
            else:
                return mp.disable(payload.plugin_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.post("/api/dashboard/plugins/config")
    def set_plugin_config(payload: PluginConfigRequest, request: Request):
        mp = request.app.extra.get("plugin_marketplace")
        if mp is None:
            raise HTTPException(status_code=503, detail="Plugin marketplace not available")
        try:
            return mp.set_config(payload.plugin_id, payload.config)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    # ══════════════════════════════════════════════════════════════
    # Context Versioning API
    # ══════════════════════════════════════════════════════════════

    class VersionSnapshotRequest(BaseModel):
        description: str = ""

    class VersionRollbackRequest(BaseModel):
        snapshot_id: str
        components: list[str] = Field(default_factory=list)

    @app.get("/api/dashboard/versions")
    def list_versions(request: Request):
        v = request.app.extra.get("versioning")
        if v is None:
            raise HTTPException(status_code=503, detail="Versioning not available")
        try:
            snapshots = v.list_snapshots(limit=30)
            return [
                {
                    "id": s.id,
                    "timestamp": s.timestamp,
                    "description": s.description,
                    "components": list(s.components.keys()),
                    "component_count": len(s.components),
                }
                for s in snapshots
            ]
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/api/dashboard/versions/snapshot")
    def create_snapshot(payload: VersionSnapshotRequest, request: Request):
        v = request.app.extra.get("versioning")
        if v is None:
            raise HTTPException(status_code=503, detail="Versioning not available")
        try:
            snap = v.snapshot(description=payload.description or "Dashboard manual snapshot")
            return {
                "id": snap.id,
                "timestamp": snap.timestamp,
                "description": snap.description,
                "component_count": len(snap.components),
                "components": list(snap.components.keys()),
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/api/dashboard/versions/<snapshot_id>")
    def get_version(snapshot_id: str, request: Request):
        v = request.app.extra.get("versioning")
        if v is None:
            raise HTTPException(status_code=503, detail="Versioning not available")
        snap = v.get_snapshot(snapshot_id)
        if snap is None:
            raise HTTPException(status_code=404, detail=f"Snapshot not found: {snapshot_id}")
        return {
            "id": snap.id,
            "timestamp": snap.timestamp,
            "description": snap.description,
            "metadata": snap.metadata,
            "component_count": len(snap.components),
            "components": {c: {"name": s.name, "checksum": s.checksum} for c, s in snap.components.items()},
        }

    @app.get("/api/dashboard/versions/<snapshot_id>/diff")
    def diff_versions(snapshot_id: str, request: Request):
        v = request.app.extra.get("versioning")
        if v is None:
            raise HTTPException(status_code=503, detail="Versioning not available")
        try:
            preview = v.preview_rollback(snapshot_id)
            return {
                "snapshot_id": preview.snapshot_id,
                "summary": preview.summary,
                "components": {
                    comp: {
                        "added_keys": d.added_keys,
                        "removed_keys": d.removed_keys,
                        "changed_keys": d.changed_keys,
                        "changes": d.changes,
                    }
                    for comp, d in preview.per_component.items()
                },
            }
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/api/dashboard/versions/rollback")
    def rollback_version(payload: VersionRollbackRequest, request: Request):
        v = request.app.extra.get("versioning")
        if v is None:
            raise HTTPException(status_code=503, detail="Versioning not available")
        try:
            components = payload.components if payload.components else None
            results = v.rollback(payload.snapshot_id, components=components)
            return {
                "snapshot_id": payload.snapshot_id,
                "results": results,
                "all_succeeded": all(results.values()) if results else False,
            }
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.delete("/api/dashboard/versions/<snapshot_id>")
    def delete_version(snapshot_id: str, request: Request):
        v = request.app.extra.get("versioning")
        if v is None:
            raise HTTPException(status_code=503, detail="Versioning not available")
        ok = v.delete_snapshot(snapshot_id)
        if not ok:
            raise HTTPException(status_code=404, detail=f"Snapshot not found: {snapshot_id}")
        return {"deleted": True, "snapshot_id": snapshot_id}

    # ── HITL Approval endpoints ──

    @app.get("/api/approvals/pending")
    def get_pending_approvals(request: Request):
        engine = request.app.extra.get("approval_engine")
        if engine is None:
            raise HTTPException(status_code=503, detail="Approval engine not available")
        pending = engine.get_pending()
        return {
            "count": len(pending),
            "requests": [r.to_dict() for r in pending],
        }

    @app.get("/api/approvals/history")
    def get_approval_history(request: Request, limit: int = 50, offset: int = 0):
        engine = request.app.extra.get("approval_engine")
        if engine is None:
            raise HTTPException(status_code=503, detail="Approval engine not available")
        history = engine.get_history(limit=limit, offset=offset)
        return {
            "count": len(history),
            "requests": [r.to_dict() for r in history],
        }

    @app.post("/api/approvals/{request_id}/approve")
    def approve_request(request_id: str, body: ApprovalActionRequest, request: Request):
        engine = request.app.extra.get("approval_engine")
        if engine is None:
            raise HTTPException(status_code=503, detail="Approval engine not available")
        result = engine.approve(request_id, note=body.note)
        if result is None:
            raise HTTPException(status_code=404, detail=f"Approval request not found or already resolved: {request_id}")
        return result.to_dict()

    @app.post("/api/approvals/{request_id}/deny")
    def deny_request(request_id: str, body: ApprovalActionRequest, request: Request):
        engine = request.app.extra.get("approval_engine")
        if engine is None:
            raise HTTPException(status_code=503, detail="Approval engine not available")
        result = engine.deny(request_id, note=body.note)
        if result is None:
            raise HTTPException(status_code=404, detail=f"Approval request not found or already resolved: {request_id}")
        return result.to_dict()

    @app.get("/api/approvals/policies")
    def get_approval_policies(request: Request):
        engine = request.app.extra.get("approval_engine")
        if engine is None:
            raise HTTPException(status_code=503, detail="Approval engine not available")
        policies = engine.get_policies()
        return {
            "count": len(policies),
            "policies": [p.to_dict() for p in policies],
        }

    @app.post("/api/approvals/policies")
    def set_approval_policy(body: ApprovalPolicyRequest, request: Request):
        engine = request.app.extra.get("approval_engine")
        if engine is None:
            raise HTTPException(status_code=503, detail="Approval engine not available")
        policy = engine.set_policy(body.rule_type, body.rule_value, body.enabled)
        return policy.to_dict()

    @app.delete("/api/approvals/policies/{policy_id}")
    def delete_approval_policy(policy_id: int, request: Request):
        engine = request.app.extra.get("approval_engine")
        if engine is None:
            raise HTTPException(status_code=503, detail="Approval engine not available")
        ok = engine.remove_policy(policy_id)
        if not ok:
            raise HTTPException(status_code=404, detail=f"Policy not found: {policy_id}")
        return {"deleted": True, "policy_id": policy_id}

    # ══════════════════════════════════════════════════════════════
    # Self-Healing API
    # ══════════════════════════════════════════════════════════════

    @app.get("/api/healing/actions")
    def healing_actions(request: Request):
        """列出所有自愈动作及状态（冷却、尝试次数、可用）"""
        emperor = request.app.extra.get("emperor")
        if emperor is None:
            raise HTTPException(status_code=503, detail="Emperor not available")

        import time as _t
        now = _t.time()
        healer = emperor.healing
        actions = healer.list_actions()

        result = []
        for a in actions:
            last_triggered = healer._last_triggered.get(a.name, 0)
            attempts = healer._attempt_counts.get(a.name, 0)
            cooldown_remaining = max(0, a.cooldown_seconds - (now - last_triggered))
            max_attempts = a.max_attempts if a.max_attempts > 0 else None

            result.append({
                "name": a.name,
                "alert_rule": a.alert_rule,
                "enabled": a.enabled,
                "cooldown_seconds": a.cooldown_seconds,
                "cooldown_remaining": round(cooldown_remaining, 1),
                "attempts": attempts,
                "max_attempts": max_attempts,
                "tags": a.tags,
                "on_cooldown": cooldown_remaining > 0,
                "exhausted": max_attempts is not None and attempts >= max_attempts,
            })

        return {"actions": result, "total": len(result)}

    @app.post("/api/healing/trigger/{action_name}")
    def healing_trigger(action_name: str, request: Request):
        """手动触发指定自愈动作"""
        emperor = request.app.extra.get("emperor")
        if emperor is None:
            raise HTTPException(status_code=503, detail="Emperor not available")

        healer = emperor.healing
        action = healer.get_action(action_name)
        if action is None:
            raise HTTPException(status_code=404, detail=f"Action not found: {action_name}")

        # Force trigger bypassing cooldown by calling action directly
        import time as _t
        now = _t.time()
        success = True
        error_msg = ""

        try:
            action.action()
        except Exception as e:
            success = False
            error_msg = str(e)

        # Record the execution
        from jarvis.healing import HealingRecord
        record = HealingRecord(
            action_name=action.name,
            alert_rule=action.alert_rule,
            timestamp=now,
            success=success,
            error=error_msg,
        )
        healer._history.append(record)
        healer._last_triggered[action.name] = now
        healer._attempt_counts[action.name] = healer._attempt_counts.get(action.name, 0) + 1

        return {
            "action_name": action_name,
            "success": success,
            "error": error_msg,
            "timestamp": now,
        }

    @app.get("/api/healing/history")
    def healing_history(limit: int = 30, request: Request = None):
        """自愈执行历史"""
        emperor = request.app.extra.get("emperor")
        if emperor is None:
            raise HTTPException(status_code=503, detail="Emperor not available")

        healer = emperor.healing
        records = healer.history(limit=limit)

        return {
            "history": [
                {
                    "action_name": r.action_name,
                    "alert_rule": r.alert_rule,
                    "timestamp": r.timestamp,
                    "success": r.success,
                    "error": r.error,
                }
                for r in records
            ],
            "total": len(records),
        }

    @app.post("/api/healing/reset/{action_name}")
    def healing_reset(action_name: str, request: Request):
        """重置自愈动作的尝试计数和冷却"""
        emperor = request.app.extra.get("emperor")
        if emperor is None:
            raise HTTPException(status_code=503, detail="Emperor not available")

        healer = emperor.healing
        if action_name == "_all":
            healer.reset_attempts()
        else:
            healer.reset_attempts(action_name)

        return {"ok": True, "action_name": action_name}

    @app.post("/api/healing/toggle/{action_name}")
    def healing_toggle(action_name: str, payload: HealingToggleRequest, request: Request):
        """启用/禁用自愈动作"""
        emperor = request.app.extra.get("emperor")
        if emperor is None:
            raise HTTPException(status_code=503, detail="Emperor not available")

        enabled = payload.enabled

        healer = emperor.healing
        action = healer.get_action(action_name)
        if action is None:
            raise HTTPException(status_code=404, detail=f"Action not found: {action_name}")

        # Toggle the actual registered action
        registered = healer._actions.get(action_name)
        if registered:
            registered.enabled = enabled

        return {"action_name": action_name, "enabled": enabled}

    @app.post("/api/healing/check")
    def healing_check(request: Request):
        """执行一轮自愈检查（遍历最近告警）"""
        emperor = request.app.extra.get("emperor")
        if emperor is None:
            raise HTTPException(status_code=503, detail="Emperor not available")

        alert_mgr = emperor.alerts
        rule_names = list({a.rule_name for a in alert_mgr.history(limit=50)})
        records = emperor.healing.handle_batch(rule_names)

        return {
            "checked_rules": len(rule_names),
            "actions_executed": len(records),
            "records": [
                {
                    "action_name": r.action_name,
                    "success": r.success,
                    "error": r.error,
                }
                for r in records
            ],
        }

    return app


# ══════════════════════════════════════════════════════════════════
# Default instance
# ══════════════════════════════════════════════════════════════════

app: FastAPI = create_app()


# ══════════════════════════════════════════════════════════════════
# CLI entry: python -m jarvis.court_api
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="Emperor Court API server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    config = None
    if args.config:
        cfg_path = args.config.resolve()
        if not cfg_path.exists():
            raise SystemExit(f"Config file not found: {cfg_path}")
        config = SurvivalConfig.from_yaml(str(cfg_path))
        print(f"Loaded config: {cfg_path}")

    server_app = create_app(config=config)
    print(f"Emperor Court API -> http://{args.host}:{args.port}")
    uvicorn.run(server_app, host=args.host, port=args.port,
                reload=args.reload, log_level="info")

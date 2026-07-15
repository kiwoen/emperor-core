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
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
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


# ══════════════════════════════════════════════════════════════════
# Module-level scheduler state (shared with Emperor.serve)
# ══════════════════════════════════════════════════════════════════

_scheduler_config: dict = {
    "evolve_interval_minutes": 5.0,
    "task_interval_minutes": 3.0,
    "auto_schedule": True,
}


# ══════════════════════════════════════════════════════════════════
# Factory
# ══════════════════════════════════════════════════════════════════

def create_app(
    config: SurvivalConfig | None = None,
    court: Court | None = None,
) -> FastAPI:
    """Create a FastAPI app wired to a Court instance.

    Args:
        config: Optional SurvivalConfig to load.
        court: Optional pre-built Court instance to inject.
    """
    app = FastAPI(title="Emperor Court API", version="0.1.0")
    if court is None:
        court = Court()

    if config is not None and config.genome_path:
        court._sm.genome_path = config.genome_path

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

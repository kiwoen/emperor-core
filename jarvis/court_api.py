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

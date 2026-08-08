"""
JARVIS API Server — FastAPI-based REST + WebSocket interface.

Endpoints:
    POST /api/execute      — Execute a task
    GET  /api/status       — System status
    GET  /api/domains      — List loaded domains
    GET  /api/memory       — Query memory
    GET  /api/evolution    — Evolution performance report
    POST /api/feedback     — Submit user feedback
    GET  /api/feedback/stats — Feedback statistics
    GET  /api/feedback/dashboard — Feedback dashboard HTML
    GET  /api/feedback/list   — Feedback list (paginated)
    WS   /ws               — Real-time bidirectional channel
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

logger = logging.getLogger("jarvis.api")

app = FastAPI(
    title="JARVIS API",
    version="0.1.0",
    description="Just A Rather Very Intelligent System — API Interface",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global references set at startup
_orchestrator = None
_config = None


class ExecuteRequest(BaseModel):
    text: str
    context_ids: list[str] | None = None


class ExecuteResponse(BaseModel):
    success: bool
    domain: str
    output: str
    error: str | None = None
    execution_time_ms: float = 0.0


class FeedbackRequest(BaseModel):
    rating: int  # 1-5
    category: str = "other"  # bug / feature / ux / performance / other
    message: str = ""
    contact: str = ""
    source: str = "web"


@app.get("/api/status")
async def get_status() -> dict[str, Any]:
    return {
        "name": _config.name if _config else "JARVIS",
        "version": _config.version if _config else "0.1.0",
        "status": "operational",
        "domains_loaded": len(_orchestrator.registry.list_domains()) if _orchestrator else 0,
    }


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok", "version": "2.0.0"}


# ── Feedback Endpoints ───────────────────────────────────────


@app.post("/api/feedback")
async def submit_feedback(request: FeedbackRequest) -> dict[str, Any]:
    """Submit user feedback."""
    try:
        from jarvis.feedback import FeedbackEntry, get_feedback_store

        if request.rating < 1 or request.rating > 5:
            return {"success": False, "error": "评分需在 1-5 之间"}

        entry = FeedbackEntry(
            id="",
            rating=request.rating,
            category=request.category,
            message=request.message,
            contact=request.contact,
            source=request.source,
        )
        store = get_feedback_store()
        entry_id = store.save(entry)
        logger.info("Feedback saved: %s (rating=%d, category=%s)", entry_id, request.rating, request.category)
        return {"success": True, "id": entry_id, "total": store.count()}
    except Exception as e:
        logger.exception("Failed to save feedback")
        return {"success": False, "error": str(e)}


@app.get("/api/feedback/stats")
async def get_feedback_stats() -> dict[str, Any]:
    """Get feedback statistics."""
    try:
        from jarvis.feedback import get_feedback_store

        store = get_feedback_store()
        return store.stats()
    except Exception as e:
        return {"error": str(e), "total": 0, "avg_rating": 0}


@app.get("/api/feedback/dashboard", response_class=HTMLResponse)
async def get_feedback_dashboard() -> str:
    """Serve the feedback dashboard HTML page."""
    from jarvis.feedback import FEEDBACK_DASHBOARD_HTML

    return FEEDBACK_DASHBOARD_HTML


@app.get("/api/feedback/list")
async def list_feedback(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    """List feedback entries (paginated)."""
    try:
        from jarvis.feedback import get_feedback_store

        store = get_feedback_store()
        return store.list_all(limit=limit, offset=offset)
    except Exception as e:
        return [{"error": str(e)}]


@app.get("/api/domains")
async def list_domains() -> dict[str, Any]:
    if not _orchestrator:
        return {"domains": [], "capabilities": {}}
    return {
        "domains": [d.name for d in _orchestrator.registry.list_domains()],
        "capabilities": _orchestrator.registry.list_capabilities(),
    }


@app.post("/api/execute", response_model=ExecuteResponse)
async def execute_task(request: ExecuteRequest) -> ExecuteResponse:
    if not _orchestrator:
        return ExecuteResponse(success=False, domain="core", output="", error="Orchestrator not initialized")

    result = await _orchestrator.execute(request.text)
    return ExecuteResponse(
        success=result.success,
        domain=result.domain.name if result.domain else "unknown",
        output=str(result.output) if result.output else "",
        error=result.error,
        execution_time_ms=result.execution_time_ms,
    )


@app.get("/api/evolution")
async def get_evolution_report() -> dict[str, Any]:
    if not _orchestrator or not _orchestrator.evolution:
        return {"status": "Evolution engine not available"}
    return _orchestrator.evolution.get_performance_report()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    await websocket.send_json({"type": "connected", "message": "JARVIS at your service."})

    try:
        while True:
            data = await websocket.receive_json()
            text = data.get("text", "")
            if not text:
                continue

            if _orchestrator:
                result = await _orchestrator.execute(text)
                await websocket.send_json({
                    "type": "result",
                    "success": result.success,
                    "domain": result.domain.name,
                    "output": str(result.output) if result.output else "",
                    "error": result.error,
                    "execution_time_ms": result.execution_time_ms,
                })
            else:
                await websocket.send_json({
                    "type": "error",
                    "message": "Orchestrator not initialized",
                })
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.exception("WebSocket error")
        try:
            await websocket.close()
        except Exception:
            pass


async def start_server(orchestrator, config) -> None:
    """Start the API server."""
    global _orchestrator, _config
    _orchestrator = orchestrator
    _config = config

    import uvicorn
    config_obj = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config_obj)
    await server.serve()

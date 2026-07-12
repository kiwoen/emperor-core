"""
JARVIS API Server — FastAPI + WebSocket.

REST endpoints for external integrations and a real-time
WebSocket channel for interactive sessions.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger("jarvis.api")

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ExecuteRequest(BaseModel):
    """Incoming execution request."""
    command: str = Field(..., description="Natural language command to execute")
    context_ids: list[str] = Field(default_factory=list, description="Optional memory context IDs")
    priority: int = Field(default=0, ge=-10, le=10)

class ExecuteResponse(BaseModel):
    """Execution result."""
    success: bool
    domain: str
    output: Any = None
    error: str | None = None
    execution_time_ms: float = 0.0
    artifacts: list[str] = []
    timestamp: str = ""

class StatusResponse(BaseModel):
    """System status snapshot."""
    name: str
    version: str
    uptime_seconds: float
    domains_loaded: list[str]
    memory_entries: int
    evolution_cycles: int
    active_connections: int

class MemoryEntry(BaseModel):
    key: str
    value: Any
    timestamp: str | None = None

class EvolutionReport(BaseModel):
    total_cycles: int
    optimizations_applied: int
    model_switches: int
    capability_additions: int
    average_score: float
    history: list[dict[str, Any]]

# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Lifespan context manager — startup + shutdown."""
    logger.info("JARVIS API server started")
    yield
    for ws in list(_active_connections):
        try:
            await ws.close(code=1001, reason="Server shutting down")
        except Exception:
            pass
    _active_connections.clear()
    logger.info("JARVIS API server stopped")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="JARVIS Core API",
    version="0.1.0",
    description="Just A Rather Very Intelligent System — REST & WebSocket API",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Global references (injected at startup)
# ---------------------------------------------------------------------------

orchestrator_ref: Any = None
config_ref: Any = None
_start_time: float = time.time()
_active_connections: set[WebSocket] = set()


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------

@app.get("/health", response_model=dict)
async def health():
    """Simple health check."""
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}

@app.get("/status", response_model=StatusResponse)
async def status():
    """Full system status."""
    orch = orchestrator_ref
    domains = orch.registry.list_domains() if orch else []
    mem_count = (len(orch.memory.episodic) + len(orch.memory.semantic)) if orch and orch.memory else 0

    return StatusResponse(
        name=config_ref.name if config_ref else "JARVIS",
        version=config_ref.version if config_ref else "0.1.0",
        uptime_seconds=round(time.time() - _start_time, 1),
        domains_loaded=[d.name for d in domains],
        memory_entries=mem_count,
        evolution_cycles=getattr(getattr(orch, "evolution", None), "total_cycles", 0) if orch else 0,
        active_connections=len(_active_connections),
    )

@app.get("/domains", response_model=list[dict])
async def list_domains():
    """List all loaded domains with capabilities."""
    orch = orchestrator_ref
    if not orch:
        return []
    result = []
    for domain in orch.registry.list_domains():
        caps = orch.registry._capabilities.get(domain, [])
        result.append({"name": domain.name, "capabilities": caps})
    return result

@app.post("/execute", response_model=ExecuteResponse)
async def execute(request: ExecuteRequest):
    """Execute a natural language command."""
    if not request.command.strip():
        raise HTTPException(status_code=400, detail="Empty command")

    if not orchestrator_ref:
        return ExecuteResponse(
            success=False,
            domain="core",
            error="Orchestrator not initialized",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    try:
        result = await orchestrator_ref.execute(request.command)
    except Exception as e:
        logger.exception("Execution failed")
        return ExecuteResponse(
            success=False,
            domain="core",
            error=str(e),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    return ExecuteResponse(
        success=result.success,
        domain=result.domain.name,
        output=str(result.output) if result.output else None,
        error=result.error,
        execution_time_ms=round(result.execution_time_ms, 2),
        artifacts=[str(a) for a in result.artifacts],
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

@app.get("/memory/search", response_model=list[MemoryEntry])
async def search_memory(query: str = "", limit: int = 20):
    """Search the memory engine."""
    orch = orchestrator_ref
    if not orch or not orch.memory:
        return []

    from jarvis.core.orchestrator import TaskResult

    if query:
        entries = await orch.memory.retrieve(query, top_k=limit)
    else:
        all_entries = list(orch.memory.episodic.values()) + list(orch.memory.semantic.values())
        entries = sorted(all_entries, key=lambda e: e.timestamp, reverse=True)[:limit]

    results: list[MemoryEntry] = []
    for entry in entries:
        results.append(MemoryEntry(
            key=entry.key,
            value=entry.content,
            timestamp=datetime.fromtimestamp(entry.timestamp, tz=timezone.utc).isoformat() if entry.timestamp else None,
        ))
    return results

@app.get("/evolution/report", response_model=EvolutionReport)
async def evolution_report():
    """Get self-evolution statistics."""
    orch = orchestrator_ref
    if not orch or not orch.evolution:
        return EvolutionReport(
            total_cycles=0,
            optimizations_applied=0,
            model_switches=0,
            capability_additions=0,
            average_score=0.0,
            history=[],
        )

    evo = orch.evolution
    return EvolutionReport(
        total_cycles=getattr(evo, "total_cycles", 0),
        optimizations_applied=getattr(evo, "optimizations_applied", 0),
        model_switches=getattr(evo, "model_switches", 0),
        capability_additions=getattr(evo, "capability_additions", 0),
        average_score=round(getattr(evo, "average_score", 0.0), 3),
        history=getattr(evo, "history", [])[-20:],
    )

# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """Real-time interactive WebSocket channel."""
    await ws.accept()
    _active_connections.add(ws)

    # Send initial greeting
    greeting = {
        "type": "system",
        "message": f"Connected to JARVIS v{config_ref.version if config_ref else '0.1.0'}. "
                    f"Send {{'type': 'command', 'data': 'your command'}} to interact.",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await ws.send_json(greeting)

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                msg = {"type": "command", "data": raw}

            msg_type = msg.get("type", "command")

            if msg_type == "command":
                cmd = msg.get("data", "").strip()
                if not cmd:
                    await ws.send_json({"type": "error", "message": "Empty command"})
                    continue

                if cmd.lower() in ("exit", "quit", "bye"):
                    await ws.send_json({"type": "system", "message": "Goodbye."})
                    break

                start = time.time()
                try:
                    result = await orchestrator_ref.execute(cmd)
                    elapsed = round((time.time() - start) * 1000, 2)
                except Exception as e:
                    await ws.send_json({
                        "type": "error",
                        "message": str(e),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                    continue

                await ws.send_json({
                    "type": "result",
                    "success": result.success,
                    "domain": result.domain.name,
                    "output": str(result.output) if result.output else None,
                    "error": result.error,
                    "execution_time_ms": elapsed,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

            elif msg_type == "status":
                s = await status()
                await ws.send_json({"type": "status", "data": s.model_dump()})

            elif msg_type == "domains":
                ds = await list_domains()
                await ws.send_json({"type": "domains", "data": ds})

            elif msg_type == "ping":
                await ws.send_json({"type": "pong", "timestamp": datetime.now(timezone.utc).isoformat()})

            else:
                await ws.send_json({"type": "error", "message": f"Unknown message type: {msg_type}"})

    except WebSocketDisconnect:
        pass
    finally:
        _active_connections.discard(ws)
        try:
            await ws.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Streaming helper – used by WebSocket stream message type
# ---------------------------------------------------------------------------

async def _run_stream(ws: WebSocket, command: str) -> None:
    """Iterate orchestrator.execute_stream() and push each event to WebSocket."""
    orch = orchestrator_ref
    if not orch:
        await ws.send_json({"type": "error", "message": "Orchestrator not initialized"})
        return

    try:
        async for event in orch.execute_stream(command):
            await ws.send_json(event)
    except Exception as e:
        logger.exception("Stream execution failed")
        await ws.send_json({
            "type": "error",
            "message": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })


# ---------------------------------------------------------------------------
# start_server — called by main.py
# ---------------------------------------------------------------------------

async def start_server(orchestrator: Any, config: Any) -> None:
    """Start the API server (called from JARVIS main entry point)."""
    global orchestrator_ref, config_ref, _start_time

    orchestrator_ref = orchestrator
    config_ref = config
    _start_time = time.time()

    import uvicorn

    config_obj = uvicorn.Config(
        app="jarvis.api.server:app",
        host="127.0.0.1",
        port=8000,
        log_level="info",
        reload=False,
    )
    server = uvicorn.Server(config_obj)
    await server.serve()

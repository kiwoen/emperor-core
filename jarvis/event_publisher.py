"""Convenient event publishing helpers for real-time Dashboard updates.

All modules call these helpers to broadcast events via the EventBus,
which the Dashboard SSE connection picks up instantly.
"""

from __future__ import annotations

import time
from typing import Any

from jarvis.event_bus import Event, event_bus
from jarvis.tracer import tracer as _tracer


# ══════════════════════════════════════════════════════════════════
# Core event types
# ══════════════════════════════════════════════════════════════════

def publish_dispatch(minister: str, edict_id: str, intent: str,
                     success: bool, confidence: float, elapsed_ms: float) -> None:
    """Published when a court dispatch completes."""
    _tracer.start_span(
        "event.publish", kind="internal",
        attributes={"event_type": "dispatch", "minister": minister, "success": success},
    )
    event_bus.publish(Event("dispatch", {
        "minister": minister,
        "edict_id": edict_id,
        "intent": intent[:120],
        "success": success,
        "confidence": round(confidence, 4),
        "elapsed_ms": round(elapsed_ms, 1),
    }))
    _tracer.end_span(_tracer._context_stack()[-1] if _tracer._context_stack() else "",
                     status="ok")


def publish_pipeline(template: str, pipeline_id: str, status: str,
                     steps: int | None = None, elapsed_ms: float = 0.0,
                     total_steps: int | None = None,
                     step_details: list[dict] | None = None) -> None:
    """Published when a pipeline execution starts or completes."""
    event_bus.publish(Event("pipeline", {
        "template": template,
        "pipeline_id": pipeline_id,
        "status": status,
        "steps": steps,
        "elapsed_ms": round(elapsed_ms, 1),
    }))
    # Also persist to the in-memory pipeline store for Dashboard queries
    from jarvis.pipeline_store import pipeline_store  # noqa: E402

    existing = pipeline_store.get_by_id(pipeline_id)
    if existing:
        pipeline_store.update(
            pipeline_id=pipeline_id,
            status=status,
            steps=steps,
            elapsed_ms=elapsed_ms,
            step_details=step_details,
        )
    else:
        pipeline_store.add(
            template=template,
            pipeline_id=pipeline_id,
            status=status,
            steps=steps,
            total_steps=total_steps,
            elapsed_ms=elapsed_ms,
            step_details=step_details,
        )


def publish_sandbox(code_snippet: str, exit_code: int, engine: str,
                    elapsed_ms: float, truncated: bool = False) -> None:
    """Published when sandbox code execution finishes."""
    snippet = code_snippet[:60].replace("\n", " ").strip()
    event_bus.publish(Event("sandbox", {
        "code_snippet": snippet,
        "exit_code": exit_code,
        "engine": engine,
        "elapsed_ms": round(elapsed_ms, 1),
        "truncated": truncated,
    }))


def publish_governance_rule(action: str, rule_id: str, priority: str = "",
                            description: str = "") -> None:
    """Published when governance rules are created/deleted/toggled."""
    event_bus.publish(Event("governance", {
        "action": action,
        "rule_id": rule_id,
        "priority": priority,
        "description": description[:200],
    }))


def publish_healing(action_name: str, result: str, triggered_by: str = "",
                    elapsed_ms: float = 0.0) -> None:
    """Published when a self-healing action completes."""
    event_bus.publish(Event("healing", {
        "action_name": action_name,
        "result": result,
        "triggered_by": triggered_by,
        "elapsed_ms": round(elapsed_ms, 1),
    }))


def publish_approval(request_id: str, action: str, risk_level: str = "",
                     approved: bool | None = None) -> None:
    """Published when an approval request is created/approved/denied."""
    event_bus.publish(Event("approval", {
        "request_id": request_id,
        "action": action,
        "risk_level": risk_level,
        "approved": approved,
    }))


def publish_memory(action: str, tier: str = "", node_count: int = 0) -> None:
    """Published when memory operations occur (consolidate/forget/add)."""
    event_bus.publish(Event("memory", {
        "action": action,
        "tier": tier,
        "node_count": node_count,
    }))


def publish_eval(suite: str, passed: int, failed: int, total_ms: float = 0.0) -> None:
    """Published when an eval suite run completes."""
    event_bus.publish(Event("eval", {
        "suite": suite,
        "passed": passed,
        "failed": failed,
        "total": passed + failed,
        "total_ms": round(total_ms, 1),
    }))


def publish_alert(alert_id: str, level: str, message: str,
                  source: str = "") -> None:
    """Published when a new alert is raised."""
    event_bus.publish(Event("alert", {
        "alert_id": alert_id,
        "level": level,
        "message": message[:200],
        "source": source,
        "ts": time.time(),
    }))


# ══════════════════════════════════════════════════════════════════
# Batch convenience
# ══════════════════════════════════════════════════════════════════

# Event type registry for discovery
EVENT_TYPES = [
    "dispatch", "pipeline", "sandbox", "governance",
    "healing", "approval", "memory", "eval", "alert",
]

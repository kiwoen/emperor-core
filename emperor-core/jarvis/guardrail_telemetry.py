"""
Guardrail Telemetry — OpenTelemetry-style observability for guardrail events.

Integrates with jarvis.tracer.Tracer to emit span events for all guardrail
trigger events (PromptInjectionGuard detections, HallucinationGuard
interceptions/corrections, toxicity filtering).  A lightweight in-memory
metrics collector aggregates pass/fail counts for the Dashboard.

Guardrail type taxonomy:
  - pre_llm  : PromptInjectionGuard.scan_input()
  - post_llm : PromptInjectionGuard.scan_output(), HallucinationGuard.check(),
               detect_toxic_content()

Integration pattern — callbacks / decorators:
  GuardrailTelemetry.emit(event) is called from Emperor.execute_task after
  each guard invocation so that no existing guard interface is changed.

Usage (inside emperor.py)::

    from jarvis.guardrail_telemetry import (
        guardrail_telemetry,
        GuardrailEvent,
        GuardrailType,
    )

    _t0 = time.perf_counter_ns()
    pg_result = self._prompt_guard.scan_input(prompt)
    _latency_us = (time.perf_counter_ns() - _t0) // 1000

    guardrail_telemetry.emit(GuardrailEvent(
        guardrail_type=GuardrailType.PRE_LLM,
        trigger_rule=pg_result.matched_rules,
        severity=pg_result.level,
        action="blocked" if pg_result.level == "dangerous" else "allowed",
        input_snippet=prompt[:200],
        latency_us=_latency_us,
    ))
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("jarvis.guardrail_telemetry")


# ═══════════════════════════════════════════════════════════════════
# Data models
# ═══════════════════════════════════════════════════════════════════

class GuardrailType(str, Enum):
    """Broad phase in the LLM pipeline where the guardrail fires."""
    PRE_LLM = "pre_llm"    # PromptInjectionGuard.scan_input
    POST_LLM = "post_llm"  # HallucinationGuard.check / scan_output / toxicity


class EventAction(str, Enum):
    """Outcome action taken for a guardrail event."""
    BLOCKED = "blocked"
    CORRECTED = "corrected"
    ALLOWED = "allowed"


@dataclass
class GuardrailEvent:
    """A single guardrail trigger event ready to be recorded as a span event.

    Fields mirror the requirements:
      - guardrail_type : pre_llm / post_llm
      - trigger_rule   : list of matched rule names / IDs
      - severity       : harmlessness / suspicion level
      - input_snippet  : truncated input text for audit trail
      - action         : blocked | corrected | allowed
      - timestamp      : epoch seconds (float)
      - latency_us     : guard execution wall-clock microseconds
    """

    guardrail_type: GuardrailType
    trigger_rule: List[str] = field(default_factory=list)
    severity: str = "harmless"
    input_snippet: str = ""
    action: EventAction = EventAction.ALLOWED
    latency_us: int = 0

    # Automatically captured
    timestamp: float = field(default_factory=time.time)

    def to_attributes(self) -> Dict[str, Any]:
        """Convert to a flat key-value dict for TraceEvent.attributes."""
        return {
            "guardrail_type": self.guardrail_type.value,
            "trigger_rule": ",".join(self.trigger_rule) if self.trigger_rule else "",
            "severity": self.severity,
            "input_snippet": self.input_snippet[:200],
            "action": self.action.value,
            "timestamp": self.timestamp,
            "latency_us": self.latency_us,
        }

    def to_dict(self) -> Dict[str, Any]:
        """Full dict representation for API / debugging."""
        return {
            "guardrail_type": self.guardrail_type.value,
            "trigger_rule": self.trigger_rule,
            "severity": self.severity,
            "input_snippet": self.input_snippet[:200],
            "action": self.action.value,
            "timestamp": self.timestamp,
            "latency_us": self.latency_us,
        }


# ═══════════════════════════════════════════════════════════════════
# GuardrailTelemetry
# ═══════════════════════════════════════════════════════════════════

class GuardrailTelemetry:
    """OpenTelemetry-style telemetry emitter for guardrail events.

    Sends each event as a TraceEvent on the currently active span
    (via jarvis.tracer).  If no span is active, events are still
    collected in the in-memory buffer for later retrieval.

    Thread-safe.  Companion metrics collector is embedded for
    Dashboard queries.
    """

    MAX_EVENT_BUFFER = 1000

    def __init__(self) -> None:
        self._events: List[GuardrailEvent] = []
        self._buffer_lock = threading.Lock()
        self._metrics: GuardrailMetricsCollector = GuardrailMetricsCollector()

    # ── Emit ──────────────────────────────────────────────────

    def emit(self, event: GuardrailEvent) -> None:
        """Record a guardrail trigger event.

        The event is:
          1. Appended to the in-memory buffer (bounded).
          2. Attached to the current active tracer span (if any).
          3. Aggregated into the metrics collector.
        """
        # 1. buffer
        with self._buffer_lock:
            self._events.append(event)
            while len(self._events) > self.MAX_EVENT_BUFFER:
                self._events.pop(0)

        # 2. span event
        try:
            from jarvis.tracer import tracer as _tracer

            stack = getattr(_tracer._local, "stack", [])
            if stack:
                current_span_id = stack[-1]
                _tracer.add_event(
                    current_span_id,
                    name=f"guardrail.{event.guardrail_type.value}",
                    attributes=event.to_attributes(),
                )
        except Exception:
            logger.debug("Failed to attach guardrail event to tracer span", exc_info=True)

        # 3. metrics
        self._metrics.record(event)

        logger.debug(
            "Guardrail event: type=%s action=%s severity=%s rules=%s",
            event.guardrail_type.value,
            event.action.value,
            event.severity,
            event.trigger_rule,
        )

    def emit_batch(self, events: List[GuardrailEvent]) -> None:
        """Record multiple events (e.g. all per-sentence hallucination claims)."""
        for event in events:
            self.emit(event)

    # ── Query ──────────────────────────────────────────────────

    def recent_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return the most recent guardrail events (copy)."""
        with self._buffer_lock:
            recent = list(self._events[-limit:])
        return [e.to_dict() for e in recent]

    def get_snapshot(self) -> Dict[str, Any]:
        """Return combined telemetry + metrics snapshot for Dashboard."""
        return {
            "metrics": self._metrics.get_snapshot(),
            "recent_events": self.recent_events(20),
            "buffer_size": min(len(self._events), self.MAX_EVENT_BUFFER),
        }

    @property
    def metrics(self) -> "GuardrailMetricsCollector":
        return self._metrics


# ═══════════════════════════════════════════════════════════════════
# GuardrailMetricsCollector
# ═══════════════════════════════════════════════════════════════════

class GuardrailMetricsCollector:
    """In-memory aggregator for guardrail pass/fail counts.

    Grouped by:
      - guardrail_type (pre_llm / post_llm)
      - action (blocked / corrected / allowed)
      - severity

    Exposes get_snapshot() for Dashboard queries.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # _by_type_act_sev[(guardrail_type, action, severity)] = count
        self._counters: Dict[str, int] = {}
        self._total_events: int = 0
        self._start_time: float = time.time()

    def record(self, event: GuardrailEvent) -> None:
        """Increment counters for a guardrail event."""
        with self._lock:
            key = f"{event.guardrail_type.value}|{event.action.value}|{event.severity}"
            self._counters[key] = self._counters.get(key, 0) + 1
            self._total_events += 1

    def get_snapshot(self) -> Dict[str, Any]:
        """Return an immutable snapshot of current metrics."""
        with self._lock:
            # Group by guardrail_type
            by_type: Dict[str, Dict[str, int]] = {}
            for key, count in self._counters.items():
                gt, action, severity = key.split("|", 2)
                if gt not in by_type:
                    by_type[gt] = {
                        "blocked": 0,
                        "corrected": 0,
                        "allowed": 0,
                        "total": 0,
                    }
                by_type[gt][action] = by_type[gt].get(action, 0) + count
                by_type[gt]["total"] = by_type[gt].get("total", 0) + count

            # Pass / fail summary
            pass_count = sum(
                c for k, c in self._counters.items()
                if k.split("|")[1] == "allowed"
            )
            fail_count = self._total_events - pass_count

            return {
                "total_events": self._total_events,
                "pass_count": pass_count,
                "fail_count": fail_count,
                "uptime_seconds": round(time.time() - self._start_time, 1),
                "by_type": by_type,
                "counters": dict(self._counters),
            }

    def reset(self) -> None:
        """Reset all counters (mainly for testing)."""
        with self._lock:
            self._counters.clear()
            self._total_events = 0
            self._start_time = time.time()

    @property
    def total_events(self) -> int:
        with self._lock:
            return self._total_events


# ── Global singleton ───────────────────────────────────────────

guardrail_telemetry = GuardrailTelemetry()

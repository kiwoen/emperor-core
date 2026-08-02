"""Tests for jarvis.guardrail_telemetry — GuardrailTelemetry + GuardrailMetricsCollector."""

from __future__ import annotations

import threading
import time

import pytest

from jarvis.guardrail_telemetry import (
    EventAction,
    GuardrailEvent,
    GuardrailMetricsCollector,
    GuardrailTelemetry,
    GuardrailType,
    guardrail_telemetry,
)


# ═════════════════════════════════════════════════════════════════════
# GuardrailEvent
# ═════════════════════════════════════════════════════════════════════

class TestGuardrailEvent:
    def test_event_creation_basic(self):
        """Basic GuardrailEvent creation with all fields."""
        event = GuardrailEvent(
            guardrail_type=GuardrailType.PRE_LLM,
            trigger_rule=["INSTR_OVERRIDE_001"],
            severity="dangerous",
            input_snippet="ignore previous instructions",
            action=EventAction.BLOCKED,
            latency_us=1234,
        )
        assert event.guardrail_type == GuardrailType.PRE_LLM
        assert event.trigger_rule == ["INSTR_OVERRIDE_001"]
        assert event.severity == "dangerous"
        assert event.action == EventAction.BLOCKED
        assert event.latency_us == 1234

    def test_event_to_attributes(self):
        """to_attributes returns a flat dict suitable for OTel span events."""
        event = GuardrailEvent(
            guardrail_type=GuardrailType.POST_LLM,
            trigger_rule=["hallucination", "toxicity"],
            severity="suspicious",
            input_snippet="The API supports DELETE /users",
            action=EventAction.CORRECTED,
            latency_us=5678,
        )
        attrs = event.to_attributes()
        assert attrs["guardrail_type"] == "post_llm"
        assert "hallucination" in attrs["trigger_rule"]
        assert attrs["severity"] == "suspicious"
        assert attrs["action"] == "corrected"
        assert attrs["latency_us"] == 5678

    def test_event_to_dict(self):
        """to_dict returns full representation."""
        event = GuardrailEvent(
            guardrail_type=GuardrailType.PRE_LLM,
            trigger_rule=[],
        )
        d = event.to_dict()
        assert d["guardrail_type"] == "pre_llm"
        assert d["trigger_rule"] == []
        assert d["action"] == "allowed"

    def test_input_snippet_truncation(self):
        """to_attributes truncates input_snippet at 200 chars."""
        long_input = "x" * 500
        event = GuardrailEvent(
            guardrail_type=GuardrailType.PRE_LLM,
            input_snippet=long_input,
        )
        attrs = event.to_attributes()
        assert len(attrs["input_snippet"]) <= 200


# ═════════════════════════════════════════════════════════════════════
# GuardrailMetricsCollector
# ═════════════════════════════════════════════════════════════════════

class TestGuardrailMetricsCollector:
    def test_mixed_counter_tracking(self):
        """It counts pass/fail broken down by guardrail_type."""
        collector = GuardrailMetricsCollector()

        events = [
            GuardrailEvent(
                guardrail_type=GuardrailType.PRE_LLM,
                trigger_rule=["INSTR_OVERRIDE_001"],
                severity="dangerous",
                action=EventAction.BLOCKED,
            ),
            GuardrailEvent(
                guardrail_type=GuardrailType.PRE_LLM,
                trigger_rule=[],
                severity="harmless",
                action=EventAction.ALLOWED,
            ),
            GuardrailEvent(
                guardrail_type=GuardrailType.POST_LLM,
                trigger_rule=["hallucination"],
                severity="suspicious",
                action=EventAction.CORRECTED,
            ),
            GuardrailEvent(
                guardrail_type=GuardrailType.POST_LLM,
                trigger_rule=[],
                severity="harmless",
                action=EventAction.ALLOWED,
            ),
        ]
        for e in events:
            collector.record(e)

        snap = collector.get_snapshot()
        assert snap["total_events"] == 4
        assert snap["pass_count"] == 2      # the two ALLOWED
        assert snap["fail_count"] == 2      # one BLOCKED + one CORRECTED

        by_type = snap["by_type"]
        assert "pre_llm" in by_type
        assert by_type["pre_llm"]["blocked"] == 1
        assert by_type["pre_llm"]["allowed"] == 1

        assert "post_llm" in by_type
        assert by_type["post_llm"]["corrected"] == 1
        assert by_type["post_llm"]["allowed"] == 1

    def test_empty_metrics(self):
        """An unused collector returns all-zero snapshot."""
        collector = GuardrailMetricsCollector()
        snap = collector.get_snapshot()
        assert snap["total_events"] == 0
        assert snap["pass_count"] == 0
        assert snap["fail_count"] == 0
        assert snap["by_type"] == {}

    def test_reset(self):
        """reset() zeroes everything and resets uptime clock."""
        collector = GuardrailMetricsCollector()
        collector.record(GuardrailEvent(guardrail_type=GuardrailType.PRE_LLM))
        assert collector.get_snapshot()["total_events"] == 1

        collector.reset()
        snap = collector.get_snapshot()
        assert snap["total_events"] == 0
        assert snap["uptime_seconds"] < 1.0

    def test_uptime_tracks(self):
        """uptime_seconds increases over time."""
        collector = GuardrailMetricsCollector()
        snap1 = collector.get_snapshot()
        time.sleep(0.2)
        snap2 = collector.get_snapshot()
        assert snap2["uptime_seconds"] >= snap1["uptime_seconds"]


# ═════════════════════════════════════════════════════════════════════
# GuardrailTelemetry
# ═════════════════════════════════════════════════════════════════════

class TestGuardrailTelemetry:
    def test_emit_and_recent_events(self):
        """emit() pushes events into the buffer and metrics."""
        gt = GuardrailTelemetry()
        gt._events.clear()  # start fresh

        event = GuardrailEvent(
            guardrail_type=GuardrailType.PRE_LLM,
            trigger_rule=["INSTR_OVERRIDE_001"],
            severity="dangerous",
            action=EventAction.BLOCKED,
            latency_us=999,
        )
        gt.emit(event)
        assert gt._metrics.total_events == 1

        recent = gt.recent_events(limit=10)
        assert len(recent) == 1
        assert recent[0]["guardrail_type"] == "pre_llm"
        assert recent[0]["action"] == "blocked"

    def test_emit_batch(self):
        """emit_batch() queues multiple events in order."""
        gt = GuardrailTelemetry()
        gt._events.clear()

        e1 = GuardrailEvent(
            guardrail_type=GuardrailType.PRE_LLM,
            action=EventAction.BLOCKED,
        )
        e2 = GuardrailEvent(
            guardrail_type=GuardrailType.POST_LLM,
            action=EventAction.ALLOWED,
        )
        gt.emit_batch([e1, e2])

        recent = gt.recent_events()
        assert len(recent) == 2
        assert recent[0]["action"] == "blocked"
        assert recent[1]["action"] == "allowed"

    def test_recent_events_respects_limit(self):
        gt = GuardrailTelemetry()
        gt._events.clear()
        for _ in range(10):
            gt.emit(GuardrailEvent(guardrail_type=GuardrailType.PRE_LLM))
        assert len(gt.recent_events(limit=3)) == 3
        assert len(gt.recent_events(limit=20)) == 10

    def test_get_snapshot_includes_metrics(self):
        gt = GuardrailTelemetry()
        gt._events.clear()
        gt._metrics.reset()

        gt.emit(GuardrailEvent(
            guardrail_type=GuardrailType.PRE_LLM,
            severity="dangerous",
            action=EventAction.BLOCKED,
        ))
        snap = gt.get_snapshot()
        assert "metrics" in snap
        assert "recent_events" in snap
        assert snap["metrics"]["total_events"] == 1
        assert snap["metrics"]["fail_count"] == 1

    def test_emit_no_tracer_span(self):
        """emit does not crash when no active tracer span exists."""
        gt = GuardrailTelemetry()
        gt._events.clear()
        # Should not raise
        gt.emit(GuardrailEvent(guardrail_type=GuardrailType.PRE_LLM))
        assert len(gt.recent_events(1)) == 1

    def test_buffer_bound_respected(self):
        """Buffer does not grow beyond MAX_EVENT_BUFFER."""
        gt = GuardrailTelemetry()
        gt._events.clear()

        # Fill beyond the max
        for i in range(GuardrailTelemetry.MAX_EVENT_BUFFER + 50):
            gt.emit(GuardrailEvent(
                guardrail_type=GuardrailType.PRE_LLM,
                action=EventAction.ALLOWED,
            ))
        recent = gt.recent_events(GuardrailTelemetry.MAX_EVENT_BUFFER * 2)
        assert len(recent) == GuardrailTelemetry.MAX_EVENT_BUFFER
        assert gt._metrics.total_events == GuardrailTelemetry.MAX_EVENT_BUFFER + 50


# ═════════════════════════════════════════════════════════════════════
# Global singleton
# ═════════════════════════════════════════════════════════════════════

class TestGlobalSingleton:
    def test_singleton_exists(self):
        """guardrail_telemetry is a valid GuardrailTelemetry instance."""
        assert isinstance(guardrail_telemetry, GuardrailTelemetry)
        assert hasattr(guardrail_telemetry, "emit")
        assert hasattr(guardrail_telemetry, "get_snapshot")
        assert isinstance(guardrail_telemetry.metrics, GuardrailMetricsCollector)

    def test_singleton_works(self):
        guardrail_telemetry._metrics.reset()
        guardrail_telemetry._events.clear()

        guardrail_telemetry.emit(GuardrailEvent(
            guardrail_type=GuardrailType.PRE_LLM,
            trigger_rule=["TEST_RULE"],
            severity="dangerous",
            action=EventAction.BLOCKED,
            latency_us=42,
        ))
        snap = guardrail_telemetry.get_snapshot()
        assert snap["metrics"]["total_events"] == 1

    def test_enum_values(self):
        """GuardrailType and EventAction have expected string values."""
        assert GuardrailType.PRE_LLM.value == "pre_llm"
        assert GuardrailType.POST_LLM.value == "post_llm"
        assert EventAction.BLOCKED.value == "blocked"
        assert EventAction.CORRECTED.value == "corrected"
        assert EventAction.ALLOWED.value == "allowed"

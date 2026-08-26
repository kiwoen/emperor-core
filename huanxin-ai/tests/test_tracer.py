"""Tests for huanxin.tracer — OpenTelemetry-style distributed tracing."""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from huanxin.tracer import (
    Tracer,
    TraceSpan,
    TraceEvent,
    SpanContext,
    TraceInfo,
    ConsoleExporter,
    FileExporter,
    InMemoryExporter,
    tracer,
)


class TestTraceSpan:
    """Basic data-class behaviour."""

    def test_span_lifecycle(self):
        """A span has correct initial and ended state."""
        s = TraceSpan(
            span_id="abc", trace_id="tr0", name="test.op",
            kind="internal", attributes={"k": "v"},
        )
        assert s.status == "unset"
        assert s.start_time > 0
        assert s.end_time == 0.0
        assert s.events == []

        s.end_time = s.start_time + 1.0
        assert s.latency_ms == 1000.0

    def test_to_dict(self):
        s = TraceSpan(span_id="s1", trace_id="t1", name="op", kind="client")
        s.end_time = s.start_time + 0.05
        s.events.append(TraceEvent("ev", attributes={"a": 1}))
        d = s.to_dict()
        assert d["span_id"] == "s1"
        assert d["trace_id"] == "t1"
        assert d["kind"] == "client"
        assert abs(d["latency_ms"] - 50.0) < 1
        assert len(d["events"]) == 1
        assert d["events"][0]["name"] == "ev"


class TestTracerBasic:
    """Span start / end / parent-child / events."""

    def test_start_end_span(self):
        t = Tracer()
        ctx = t.start_span("test.op", kind="client", attributes={"x": 1})
        assert ctx.span_id
        assert ctx.trace_id

        t.end_span(ctx.span_id, status="ok", attributes={"y": 2})

        spans = t.get_trace(ctx.trace_id)
        assert len(spans) == 1
        assert spans[0].name == "test.op"
        assert spans[0].attributes["x"] == 1
        assert spans[0].attributes["y"] == 2

    def test_parent_child(self):
        t = Tracer()
        root = t.start_span("root", kind="server")
        child = t.start_span("child", kind="client")
        assert child.parent_id == root.span_id
        assert child.trace_id == root.trace_id

        t.end_span(child.span_id, "ok")
        t.end_span(root.span_id, "ok")

        spans = t.get_trace(root.trace_id)
        assert len(spans) == 2

    def test_set_attribute(self):
        t = Tracer()
        ctx = t.start_span("op")
        t.set_attribute(ctx.span_id, "tag", "val")
        t.set_attribute(ctx.span_id, "count", 3)
        t.end_span(ctx.span_id, "ok")

        spans = t.get_trace(ctx.trace_id)
        assert spans[0].attributes["tag"] == "val"
        assert spans[0].attributes["count"] == 3

    def test_add_event(self):
        t = Tracer()
        ctx = t.start_span("op")
        t.add_event(ctx.span_id, "milestone", {"step": 1})
        t.add_event(ctx.span_id, "milestone", {"step": 2})
        t.end_span(ctx.span_id, "ok")

        spans = t.get_trace(ctx.trace_id)
        assert len(spans[0].events) == 2
        assert spans[0].events[0].name == "milestone"
        assert spans[0].events[1].attributes["step"] == 2

    def test_error_status(self):
        t = Tracer()
        ctx = t.start_span("failing.op")
        t.end_span(ctx.span_id, status="error")

        spans = t.get_trace(ctx.trace_id)
        assert spans[0].status == "error"

    def test_context_manager(self):
        t = Tracer()
        with t.span("cm.op", kind="internal") as ctx:
            pass
        spans = t.get_trace(ctx.trace_id)
        assert len(spans) == 1
        assert spans[0].status == "ok"

    def test_context_manager_error(self):
        t = Tracer()
        class TestErr(Exception):
            pass
        with pytest.raises(TestErr):
            with t.span("fail.cm"):
                raise TestErr("boom")
        # Span should still be flushed with error status
        info = t.list_recent_traces(limit=10)
        assert len(info) > 0
        # At least one trace has error
        spans = None
        for inf in info:
            s = t.get_trace(inf.trace_id)
            if s and s[0].name == "fail.cm":
                spans = s
                break
        assert spans is not None
        assert spans[0].status == "error"


class TestExporters:
    """Console / File / InMemory exporters."""

    def test_console_exporter(self, capsys):
        s = TraceSpan(
            span_id="s1", trace_id="tr0", name="dispatch",
            kind="server", attributes={"key": "val"},
        )
        s.end_time = s.start_time + 0.042
        ConsoleExporter(use_colour=False).export([s])
        out = capsys.readouterr().out
        assert "dispatch" in out
        assert "tr0" in out

    def test_file_exporter(self):
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "traces.jsonl")
            fe = FileExporter(fp)
            s = TraceSpan(span_id="s1", trace_id="tid1", name="op", kind="internal")
            s.end_time = s.start_time + 0.01
            fe.export([s])

            assert os.path.exists(fp)
            with open(fp) as f:
                line = f.readline()
            data = json.loads(line)
            assert data["trace_id"] == "tid1"
            assert len(data["spans"]) == 1

    def test_in_memory_export_and_list(self):
        mem = InMemoryExporter()
        s = TraceSpan(span_id="x", trace_id="t0", name="root", kind="server")
        s.end_time = s.start_time + 0.5
        mem.export([s])

        spans = mem.get_trace("t0")
        assert len(spans) == 1

        infos = mem.list_recent(limit=10)
        assert len(infos) == 1
        assert infos[0].root_span_name == "root"
        assert abs(infos[0].total_latency_ms - 500.0) < 1

    def test_in_memory_stats(self):
        mem = InMemoryExporter()
        for i in range(5):
            s = TraceSpan(span_id=f"s{i}", trace_id=f"t{i}", name="op", kind="internal")
            s.end_time = s.start_time + (0.1 + i * 0.1)
            mem.export([s])

        stats = mem.stats()
        assert stats["total_traces"] == 5
        assert stats["avg_latency_ms"] > 0
        assert stats["p50_latency_ms"] > 0
        assert stats["p95_latency_ms"] > 0
        assert stats["p99_latency_ms"] > 0


class TestGlobalSingleton:
    """The global `tracer` singleton works correctly."""

    def test_singleton_exists(self):
        from huanxin.tracer import tracer as t
        assert t is not None
        assert isinstance(t, Tracer)

    def test_singleton_trace_roundtrip(self):
        ctx = tracer.start_span("singleton.test", kind="internal", attributes={"hello": "world"})
        tracer.end_span(ctx.span_id, "ok")

        spans = tracer.get_trace(ctx.trace_id)
        assert len(spans) == 1
        assert spans[0].name == "singleton.test"
        assert spans[0].attributes["hello"] == "world"

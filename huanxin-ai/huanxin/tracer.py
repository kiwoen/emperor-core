"""
OpenTelemetry-style distributed tracing engine for huanxin-ai.

Provides Span lifecycle management, parent-child context tracking,
and pluggable exporters for observability of Agent execution.

Usage::

    from huanxin.tracer import tracer

    ctx = tracer.start_span("emperor.dispatch", kind="server",
                            attributes={"minister": "turing"})
    # ... work ...
    tracer.end_span(ctx.span_id, status="ok",
                    attributes={"elapsed_ms": 42.0})

    trace = tracer.get_trace(ctx.trace_id)
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("huanxin.tracer")

# ── ANSI colours for ConsoleExporter ────────────────────────────
_RESET = "\033[0m"
_BOLD = "\033[1m"
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_DIM = "\033[2m"


# ═══════════════════════════════════════════════════════════════
# Data classes
# ═══════════════════════════════════════════════════════════════

@dataclass
class TraceEvent:
    """An event attached to a Span (log / annotation)."""

    name: str
    timestamp: float = field(default_factory=time.time)
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class TraceSpan:
    """A single span in a distributed trace."""

    span_id: str
    trace_id: str
    parent_id: str = ""
    name: str = ""
    kind: str = "internal"          # client | server | internal
    start_time: float = field(default_factory=time.time)
    end_time: float = 0.0
    status: str = "unset"           # unset | ok | error
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[TraceEvent] = field(default_factory=list)

    @property
    def latency_ms(self) -> float:
        """Span duration in milliseconds."""
        if self.end_time <= 0:
            return 0.0
        return (self.end_time - self.start_time) * 1000

    def to_dict(self) -> dict:
        return {
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "kind": self.kind,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "status": self.status,
            "latency_ms": round(self.latency_ms, 3),
            "attributes": self.attributes,
            "events": [
                {"name": e.name, "timestamp": e.timestamp, "attributes": e.attributes}
                for e in self.events
            ],
        }


@dataclass
class SpanContext:
    """Lightweight handle returned by start_span()."""

    span_id: str
    trace_id: str
    parent_id: str = ""


@dataclass
class TraceInfo:
    """Lightweight summary of a trace for listing."""

    trace_id: str
    root_span_name: str
    start_time: float
    span_count: int
    total_latency_ms: float
    status: str = "ok"


# ═══════════════════════════════════════════════════════════════
# Exporters
# ═══════════════════════════════════════════════════════════════

class TraceExporter(ABC):
    """Abstract base for trace exporters."""

    @abstractmethod
    def export(self, spans: list[TraceSpan]) -> None: ...


class ConsoleExporter(TraceExporter):
    """Pretty-print spans to stdout with colour."""

    def __init__(self, use_colour: bool = True) -> None:
        self.use_colour = use_colour

    def export(self, spans: list[TraceSpan]) -> None:
        if not spans:
            return
        trace_id = spans[0].trace_id[:8] if spans else "?"
        print(f"\n{_BOLD}═══ Trace {trace_id} ({len(spans)} spans) ═══{_RESET}" if self.use_colour
              else f"\n=== Trace {trace_id} ({len(spans)} spans) ===")
        for s in sorted(spans, key=lambda x: x.start_time):
            icon = "✓" if s.status == "ok" else "✗" if s.status == "error" else "?"
            if self.use_colour:
                colour = _GREEN if s.status == "ok" else _RED if s.status == "error" else _YELLOW
                indent = "  " * (1 if s.parent_id else 0)
                print(f"{indent}{colour}{icon}{_RESET} {_CYAN}{s.name}{_RESET}  "
                      f"{_DIM}{s.latency_ms:.1f}ms{_RESET}")
                for k, v in s.attributes.items():
                    print(f"    {_DIM}{k}={v}{_RESET}")
            else:
                indent = "  " * (1 if s.parent_id else 0)
                print(f"{indent}{icon} {s.name}  {s.latency_ms:.1f}ms")
                for k, v in s.attributes.items():
                    print(f"    {k}={v}")


class FileExporter(TraceExporter):
    """Write spans to a JSONL file (one line per trace)."""

    def __init__(self, filepath: str | Path) -> None:
        self.filepath = Path(filepath)
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def export(self, spans: list[TraceSpan]) -> None:
        if not spans:
            return
        payload = {
            "trace_id": spans[0].trace_id,
            "spans": [s.to_dict() for s in spans],
            "exported_at": time.time(),
        }
        with self._lock:
            with open(self.filepath, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")


class InMemoryExporter(TraceExporter):
    """Store spans in memory for API queries (thread-safe)."""

    def __init__(self, max_traces: int = 500) -> None:
        self.max_traces = max_traces
        self._traces: dict[str, list[TraceSpan]] = {}
        self._lock = threading.Lock()

    def export(self, spans: list[TraceSpan]) -> None:
        if not spans:
            return
        trace_id = spans[0].trace_id
        with self._lock:
            self._traces[trace_id] = spans
            # Evict oldest if beyond max
            while len(self._traces) > self.max_traces:
                oldest = min(self._traces.keys(),
                             key=lambda tid: min(s.start_time for s in self._traces[tid]))
                del self._traces[oldest]

    def get_trace(self, trace_id: str) -> list[TraceSpan]:
        with self._lock:
            return list(self._traces.get(trace_id, []))

    def list_recent(self, limit: int = 20) -> list[TraceInfo]:
        """Return recent trace summaries sorted by start_time desc."""
        with self._lock:
            infos: list[TraceInfo] = []
            for tid, spans in self._traces.items():
                roots = [s for s in spans if not s.parent_id]
                root = roots[0] if roots else spans[0]
                total_ms = max(s.end_time for s in spans) - min(s.start_time for s in spans)
                total_ms *= 1000 if total_ms > 0 else 0
                status = "error" if any(s.status == "error" for s in spans) else "ok"
                infos.append(TraceInfo(
                    trace_id=tid,
                    root_span_name=root.name,
                    start_time=root.start_time,
                    span_count=len(spans),
                    total_latency_ms=round(abs(total_ms), 2),
                    status=status,
                ))
            infos.sort(key=lambda x: x.start_time, reverse=True)
            return infos[:limit]

    def stats(self) -> dict:
        """Return aggregate trace statistics."""
        with self._lock:
            if not self._traces:
                return {
                    "total_traces": 0,
                    "avg_latency_ms": 0,
                    "p50_latency_ms": 0,
                    "p95_latency_ms": 0,
                    "p99_latency_ms": 0,
                }
            latencies: list[float] = []
            error_count = 0
            for spans in self._traces.values():
                if not spans:
                    continue
                ms = (max(s.end_time for s in spans) - min(s.start_time for s in spans)) * 1000
                latencies.append(abs(ms))
                if any(s.status == "error" for s in spans):
                    error_count += 1
            latencies.sort()
            n = len(latencies)

            def percentile(p: float) -> float:
                idx = max(0, int(n * p) - 1)
                return round(latencies[min(idx, n - 1)], 2)

            return {
                "total_traces": n,
                "error_count": error_count,
                "avg_latency_ms": round(sum(latencies) / n, 2) if n else 0,
                "p50_latency_ms": percentile(0.50),
                "p95_latency_ms": percentile(0.95),
                "p99_latency_ms": percentile(0.99),
            }


# ═══════════════════════════════════════════════════════════════
# Tracer
# ═══════════════════════════════════════════════════════════════

class Tracer:
    """OpenTelemetry-style tracer with context-stack parent management.

    Typical usage::

        from huanxin.tracer import tracer

        ctx = tracer.start_span("emperor.dispatch", kind="server")
        # nesting:
        ctx2 = tracer.start_span("model.invoke", kind="client")
        tracer.end_span(ctx2.span_id, "ok")
        tracer.end_span(ctx.span_id, "ok")
    """

    def __init__(self) -> None:
        self._spans: dict[str, TraceSpan] = {}
        self._lock = threading.Lock()
        # Thread-local context stack
        self._local = threading.local()
        self.exporters: list[TraceExporter] = []
        # Register memory exporter by default for API queries
        self._memory: InMemoryExporter = InMemoryExporter()
        self.exporters.append(self._memory)

    @property
    def memory(self) -> InMemoryExporter:
        return self._memory

    # ── Context stack helpers ─────────────────────────────────

    def _context_stack(self) -> list[str]:
        if not hasattr(self._local, "stack"):
            self._local.stack = []
        return self._local.stack

    def _current_parent_id(self) -> str:
        stack = self._context_stack()
        return stack[-1] if stack else ""

    # ── Span lifecycle ───────────────────────────────────────

    def start_span(
        self,
        name: str,
        kind: str = "internal",
        attributes: dict[str, Any] | None = None,
        trace_id: str = "",
    ) -> SpanContext:
        """Begin a new Span, auto-nested under the current parent if any.

        Args:
            name: Span name (e.g. "emperor.dispatch").
            kind: One of "client", "server", "internal".
            attributes: Key-value pairs attached to the span.
            trace_id: Inherit from an existing trace. Creates new if empty.

        Returns:
            SpanContext handle for ending the span.
        """
        parent_id = self._current_parent_id()
        tid = trace_id
        if not tid and parent_id:
            # Inherit trace_id from active parent
            with self._lock:
                parent_span = self._spans.get(parent_id)
            if parent_span:
                tid = parent_span.trace_id
        if not tid:
            tid = uuid.uuid4().hex[:16]
        sid = uuid.uuid4().hex[:12]

        span = TraceSpan(
            span_id=sid,
            trace_id=tid,
            parent_id=parent_id,
            name=name,
            kind=kind,
            attributes=dict(attributes or {}),
        )

        with self._lock:
            self._spans[sid] = span

        self._context_stack().append(sid)
        return SpanContext(span_id=sid, trace_id=tid, parent_id=parent_id)

    def end_span(
        self,
        span_id: str,
        status: str = "ok",
        attributes: dict[str, Any] | None = None,
    ) -> None:
        """End a Span and pop it from the context stack.

        When the root span (no parent) ends, all spans in the trace
        are flushed through registered exporters.
        """
        with self._lock:
            span = self._spans.get(span_id)
            if span is None:
                logger.warning("end_span: unknown span_id %s", span_id)
                return
            span.end_time = time.time()
            span.status = status
            if attributes:
                span.attributes.update(attributes)

        # Pop from context stack
        stack = self._context_stack()
        if stack and stack[-1] == span_id:
            stack.pop()

        # If this is root span → flush entire trace
        if not span.parent_id:
            self._flush_trace(span.trace_id)

    def _flush_trace(self, trace_id: str) -> None:
        """Collect all spans for *trace_id* and send to exporters."""
        with self._lock:
            trace_spans = [s for s in self._spans.values() if s.trace_id == trace_id]
        if trace_spans:
            for exp in self.exporters:
                try:
                    exp.export(trace_spans)
                except Exception:
                    logger.exception("Exporter %s failed", type(exp).__name__)

    def add_event(self, span_id: str, name: str, attributes: dict[str, Any] | None = None) -> None:
        """Attach a timestamped event to an active Span."""
        with self._lock:
            span = self._spans.get(span_id)
            if span is None:
                logger.warning("add_event: unknown span_id %s", span_id)
                return
            span.events.append(TraceEvent(name=name, attributes=dict(attributes or {})))

    def set_attribute(self, span_id: str, key: str, value: Any) -> None:
        """Set a single attribute on an active Span."""
        with self._lock:
            span = self._spans.get(span_id)
            if span is None:
                logger.warning("set_attribute: unknown span_id %s", span_id)
                return
            span.attributes[key] = value

    # ── Query ───────────────────────────────────────────────

    def get_trace(self, trace_id: str) -> list[TraceSpan]:
        """Return all spans for a trace (from memory exporter)."""
        return self._memory.get_trace(trace_id)

    def list_recent_traces(self, limit: int = 20) -> list[TraceInfo]:
        """Return recent trace summaries."""
        return self._memory.list_recent(limit)

    def stats(self) -> dict:
        """Return aggregate trace statistics."""
        return self._memory.stats()

    # ── Context manager ────────────────────────────────────

    @contextmanager
    def span(self, name: str, kind: str = "internal",
             attributes: dict[str, Any] | None = None):
        """Context-manager style span. Auto-ends on exit.

        >>> with tracer.span("my.op") as ctx:
        ...     do_work()
        """
        ctx = self.start_span(name, kind=kind, attributes=attributes)
        try:
            yield ctx
            self.end_span(ctx.span_id, status="ok")
        except Exception:
            self.end_span(ctx.span_id, status="error")
            raise


# ── Global singleton ───────────────────────────────────────────

tracer = Tracer()

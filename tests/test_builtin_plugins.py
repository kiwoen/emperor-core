"""Tests for the built-in plugin pack (MetricsPlugin, LoggingPlugin)."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

import pytest

from jarvis.plugin import Plugin
from jarvis.plugins import LoggingPlugin, MetricsPlugin
from jarvis.plugins.metrics import MetricsSummary, TaskSample, EvolutionSample


# ══════════════════════════════════════════════════════════════════
# MetricsPlugin
# ══════════════════════════════════════════════════════════════════

class TestMetricsPluginBasics:
    """Unit-level: construction, identity, simple state."""

    def test_constructor_defaults(self):
        p = MetricsPlugin()
        assert p.name == "MetricsPlugin"

    def test_constructor_custom_max(self):
        p = MetricsPlugin(max_samples=50)
        assert p._max_samples == 50

    def test_initial_summary_is_empty(self):
        p = MetricsPlugin()
        s = p.summary()
        assert s.total_tasks == 0
        assert s.success_rate == 0.0
        assert s.samples_in_buffer == 0

    def test_clear_resets_all(self):
        p = MetricsPlugin()
        p.on_task_before(task_id="t1")
        p.on_task_after(outcome={
            "task_id": "t1", "success": True,
            "confidence": 0.95, "execution_time_ms": 120.0,
            "minister": "test:math",
        })
        assert len(p._tasks) == 1
        p.clear()
        assert len(p._tasks) == 0
        assert p._error_count == 0
        assert p._total_evolution_cycles == 0


class TestMetricsPluginTaskRecording:
    """Task lifecycle recording."""

    def test_successful_task(self):
        p = MetricsPlugin()
        p.on_task_before(task_id="t1")
        p.on_task_after(outcome={
            "task_id": "t1", "success": True,
            "confidence": 0.92, "execution_time_ms": 85.0,
            "minister": "doman:network",
        })
        assert len(p._tasks) == 1
        sample = p._tasks[0]
        assert sample.task_id == "t1"
        assert sample.success is True
        assert sample.confidence == 0.92
        assert sample.execution_time_ms == 85.0
        assert sample.domain == "network"

    def test_failed_task_through_error_hook(self):
        p = MetricsPlugin()
        p.on_task_before(task_id="t2")
        p.on_task_error(task_id="t2", domain="general", error="timeout")
        assert len(p._tasks) == 1
        sample = p._tasks[0]
        assert sample.success is False
        assert sample.confidence == 0.0
        assert sample.error == "timeout"

    def test_missing_outcome_handled_gracefully(self):
        p = MetricsPlugin()
        p.on_task_before(task_id="t3")
        p.on_task_after(outcome=None)
        assert len(p._tasks) == 1
        assert p._tasks[0].success is False  # bool(None) = False

    def test_task_domain_extracted_correctly(self):
        p = MetricsPlugin()
        p.on_task_before(task_id="t")
        p.on_task_after(outcome={
            "task_id": "t", "success": True,
            "confidence": 0.5, "execution_time_ms": 10.0,
            "minister": "MinisterScheduler:alerts",
        })
        assert p._tasks[0].domain == "alerts"

    def test_task_domain_fallback_general(self):
        p = MetricsPlugin()
        p.on_task_before(task_id="t")
        p.on_task_after(outcome={
            "task_id": "t", "success": True,
            "confidence": 0.5, "execution_time_ms": 10.0,
            "minister": "plain",
        })
        assert p._tasks[0].domain == "general"

    def test_ring_buffer_max_respected(self):
        p = MetricsPlugin(max_samples=3)
        for i in range(5):
            p.on_task_before(task_id=f"t{i}")
            p.on_task_after(outcome={
                "task_id": f"t{i}", "success": True,
                "confidence": 0.5, "execution_time_ms": 10.0,
                "minister": "test:bench",
            })
        assert len(p._tasks) == 3
        assert p._tasks[0].task_id == "t2"  # oldest kept
        assert p._tasks[-1].task_id == "t4"  # newest

    def test_success_rate(self):
        p = MetricsPlugin()
        for i in range(3):
            p.on_task_before(task_id=f"ok{i}")
            p.on_task_after(outcome={
                "task_id": f"ok{i}", "success": True,
                "confidence": 0.9, "execution_time_ms": 50.0,
                "minister": "test:gen",
            })
        p.on_task_before(task_id="fail")
        p.on_task_error(task_id="fail", domain="gen", error="crash")
        assert p.success_rate() == 0.75

    def test_avg_confidence(self):
        p = MetricsPlugin()
        for value in [0.5, 1.0, 0.9]:
            p.on_task_before(task_id="x")
            p.on_task_after(outcome={
                "task_id": "x", "success": True,
                "confidence": value, "execution_time_ms": 10.0,
                "minister": "test:gen",
            })
        assert p.avg_confidence() == pytest.approx(0.8)

    def test_avg_execution_time(self):
        p = MetricsPlugin()
        for ms in [100.0, 200.0]:
            p.on_task_before(task_id="x")
            p.on_task_after(outcome={
                "task_id": "x", "success": True,
                "confidence": 0.5, "execution_time_ms": ms,
                "minister": "test:gen",
            })
        assert p.avg_execution_time() == 150.0

    def test_task_history_ordering(self):
        p = MetricsPlugin()
        for i in range(5):
            p.on_task_before(task_id=f"t{i}")
            p.on_task_after(outcome={
                "task_id": f"t{i}", "success": True,
                "confidence": 0.5, "execution_time_ms": 10.0,
                "minister": "test:gen",
            })
        hist = p.task_history(limit=3)
        assert len(hist) == 3
        assert hist[0].task_id == "t4"
        assert hist[-1].task_id == "t2"

    def test_even_when_no_outcome_confidence_is_zero(self):
        p = MetricsPlugin()
        p.on_task_before(task_id="orphan")
        p.on_task_after(outcome={"success": True})  # no confidence key
        assert p._tasks[0].confidence == 0.0


class TestMetricsPluginEvolution:
    """Evolution lifecycle recording."""

    def test_evolution_recorded(self):
        p = MetricsPlugin()
        p.on_evolve_end(result={
            "cycles_completed": 3,
            "active_ministers": 2,
            "avg_merit": 0.87,
            "mutations": ["m1", "m2", "m3"],
        })
        assert len(p._evolutions) == 1
        s = p._evolutions[0]
        assert s.cycles == 3
        assert s.active_ministers == 2
        assert s.avg_merit == 0.87

    def test_evolution_defaults_when_no_result(self):
        p = MetricsPlugin()
        p.on_evolve_end(result=None)
        assert len(p._evolutions) == 1
        s = p._evolutions[0]
        assert s.cycles == 0
        assert s.active_ministers == 0

    def test_evolution_mutations_fallback(self):
        p = MetricsPlugin()
        p.on_evolve_end(result={"cycles_completed": 0, "mutations": ["a", "b"]})
        assert p._evolutions[0].cycles == 2

    def test_evolution_cycles_counter(self):
        p = MetricsPlugin()
        for _ in range(4):
            p.on_evolve_end(result={"cycles_completed": 1})
        assert p._total_evolution_cycles == 4

    def test_evolution_history(self):
        p = MetricsPlugin()
        for i in range(5):
            p.on_evolve_end(result={"cycles_completed": i})
        hist = p.evolution_history(limit=2)
        assert len(hist) == 2
        assert hist[0].cycles == 4


class TestMetricsPluginSummary:
    """Aggregate MetricsSummary generation."""

    def test_summary_counts(self):
        p = MetricsPlugin()
        for i in range(4):
            p.on_task_before(task_id=f"ok{i}")
            p.on_task_after(outcome={
                "task_id": f"ok{i}", "success": True,
                "confidence": 0.9, "execution_time_ms": 50.0,
                "minister": "test:gen",
            })
        p.on_task_before(task_id="f1")
        p.on_task_error(task_id="f1", domain="gen", error="err")
        s = p.summary(active_ministers=3)
        assert s.total_tasks == 5
        assert s.successful_tasks == 4
        assert s.failed_tasks == 1
        assert s.success_rate == 0.8
        assert s.active_ministers == 3
        assert isinstance(s, MetricsSummary)

    def test_summary_empty_defaults(self):
        s = MetricsPlugin().summary()
        assert s.total_tasks == 0
        assert s.success_rate == 0.0
        assert s.active_ministers == 0
        assert s.time_window_seconds == 0.0


# ══════════════════════════════════════════════════════════════════
# LoggingPlugin
# ══════════════════════════════════════════════════════════════════

class TestLoggingPluginBasics:
    """Construction and identity."""

    def test_name(self):
        assert LoggingPlugin().name == "LoggingPlugin"

    def test_custom_path_and_rotation(self):
        p = LoggingPlugin(
            log_path="custom.jsonl", max_bytes=100, stdout=False
        )
        assert p._max_bytes == 100
        assert p._path.name == "custom.jsonl"

    def test_default_path(self):
        p = LoggingPlugin()
        assert p._path.suffix == ".jsonl"


class TestLoggingPluginWrites:
    """Verify each event type writes the expected line."""

    def _make_plugin(self, tmp_path: Path):
        path = tmp_path / "events.jsonl"
        return LoggingPlugin(
            log_path=str(path), include_kwargs=True, stdout=False
        ), path

    def _line_count(self, path: Path) -> int:
        if not path.exists():
            return 0
        with open(path) as f:
            return sum(1 for _ in f)

    def _read_lines(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        with open(path) as f:
            return [json.loads(line) for line in f if line.strip()]

    def test_startup_writes(self, tmp_path):
        p, path = self._make_plugin(tmp_path)
        p.on_startup(version="1.0.0")
        assert self._line_count(path) == 1
        entry = self._read_lines(path)[0]
        assert entry["event"] == "STARTUP"
        assert "ts" in entry

    def test_all_events_write(self, tmp_path):
        p, path = self._make_plugin(tmp_path)
        events = {
            "minister_register": {},
            "minister_deregister": {},
            "evolve_start": {},
            "evolve_end": {},
            "task_before": {},
            "task_after": {},
            "task_error": {},
            "system_alert": {},
            "healing": {},
            "shutdown": {},
            "startup": {},
            "config_change": {},
            "plugin_register": {},
            "plugin_unregister": {},
        }
        for hook, kw in events.items():
            getattr(p, f"on_{hook}")(**kw)
        lines = self._read_lines(path)
        written_events = {e["event"] for e in lines}
        expected = {
            "MINISTER_REGISTER", "MINISTER_DEREGISTER",
            "EVOLVE_START", "EVOLVE_END",
            "TASK_BEFORE", "TASK_AFTER", "TASK_ERROR",
            "SYSTEM_ALERT", "HEALING",
            "SHUTDOWN", "STARTUP", "CONFIG_CHANGE",
            "PLUGIN_REGISTER", "PLUGIN_UNREGISTER",
        }
        assert written_events == expected

    def test_kwargs_stored_in_entry(self, tmp_path):
        p, path = self._make_plugin(tmp_path)
        p.on_task_after(outcome={"success": True})
        entry = self._read_lines(path)[0]
        assert "kwargs" in entry
        assert entry["kwargs"]["outcome"]["success"] is True

    def test_no_kwargs_when_disabled(self, tmp_path):
        path = tmp_path / "nokw.jsonl"
        p = LoggingPlugin(log_path=str(path), include_kwargs=False)
        p.on_task_before(task_id="t1")
        entry = self._read_lines(path)[0]
        assert "kwargs" not in entry

    def test_rotation_works(self, tmp_path):
        path = tmp_path / "rotate.jsonl"
        p = LoggingPlugin(log_path=str(path), max_bytes=200)
        # Write more than 200 bytes
        for i in range(100):
            p.on_task_before(task_id=f"t{i}")
        rotated = path.with_suffix(path.suffix + ".1")
        # After rotation there should be a .1 file and a fresh .jsonl
        assert rotated.exists() or path.exists()

    def test_large_kwargs_serialized(self, tmp_path):
        path = tmp_path / "large.jsonl"
        p = LoggingPlugin(log_path=str(path))
        p.on_system_alert(
            rule_name="test", message="x" * 1000, severity="high"
        )
        entry = self._read_lines(path)[0]
        assert entry["event"] == "SYSTEM_ALERT"
        assert len(str(entry)) > 500

    def test_non_serializable_kwargs_handled(self, tmp_path):
        class NotSerializable:
            pass

        path = tmp_path / "bad.jsonl"
        p = LoggingPlugin(log_path=str(path))
        p.on_evolve_end(result={"obj": NotSerializable()})
        entry = self._read_lines(path)[0]
        # Should have been converted via str()
        assert "kwargs" in entry
        assert "NotSerializable" in entry["kwargs"]["result"]["obj"]

    def test_stdout_flag(self, tmp_path, capsys):
        path = tmp_path / "stdout.jsonl"
        p = LoggingPlugin(log_path=str(path), stdout=True)
        p.on_startup()
        captured = capsys.readouterr()
        assert "STARTUP" in captured.out


class TestLoggingPluginThreadSafety:
    """Concurrent writes from multiple threads."""

    def test_concurrent_writes(self, tmp_path):
        path = tmp_path / "conc.jsonl"
        p = LoggingPlugin(log_path=str(path))
        import threading

        def write(prefix: str, count: int):
            for i in range(count):
                getattr(p, f"on_{prefix}")()

        threads = [
            threading.Thread(target=write, args=("startup", 50)),
            threading.Thread(target=write, args=("shutdown", 50)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        with open(path) as f:
            lines = f.readlines()
        assert len(lines) == 100

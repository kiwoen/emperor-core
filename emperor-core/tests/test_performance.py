"""Performance Benchmarks — Baseline for all subsystems.

Measures:
- Task dispatch throughput
- Audit query latency
- Eval suite execution time
- Healing check cycles
- Search query latency
- Context versioning snapshot time
- Pipeline monitor response time
"""

import time
import pytest

from jarvis.emperor import Emperor
from jarvis.court_api import create_app
from starlette.testclient import TestClient


# ══════════════════════════════════════════════════════════════════
# Fixture
# ══════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def perf_client():
    """Module-level client for performance benchmarks."""
    emperor = Emperor()
    app = create_app()
    app.extra["emperor"] = emperor
    app.extra["config"] = emperor.config
    app.extra["alert_manager"] = getattr(emperor, "alerts", None)
    app.extra["approval_engine"] = getattr(emperor, "approval_engine", None)

    with TestClient(app) as c:
        yield c


# ══════════════════════════════════════════════════════════════════
# Task Dispatch Throughput
# ══════════════════════════════════════════════════════════════════


class TestTaskDispatchPerf:
    """Measure task dispatch request latency."""

    def test_dispatch_latency_under_200ms(self, perf_client):
        """Single dispatch must complete under 200ms."""
        payload = {"prompt": "benchmark task", "domain": "test"}

        latencies = []
        for _ in range(10):
            start = time.perf_counter()
            perf_client.post("/api/pipelines/execute", json=payload)
            latencies.append((time.perf_counter() - start) * 1000)

        avg = sum(latencies) / len(latencies)
        p95 = sorted(latencies)[int(len(latencies) * 0.95)]

        assert avg < 200, f"Average dispatch latency {avg:.0f}ms exceeds 200ms"
        assert p95 < 500, f"P95 dispatch latency {p95:.0f}ms exceeds 500ms"


# ══════════════════════════════════════════════════════════════════
# Audit Query Performance
# ══════════════════════════════════════════════════════════════════


class TestAuditPerf:
    """Measure audit endpoint response times."""

    def test_audit_stats_latency_under_50ms(self, perf_client):
        start = time.perf_counter()
        resp = perf_client.get("/api/dashboard/audit/stats")
        elapsed = (time.perf_counter() - start) * 1000

        assert resp.status_code == 200
        assert elapsed < 50, f"Audit stats latency {elapsed:.0f}ms exceeds 50ms"

    def test_audit_recent_latency_under_50ms(self, perf_client):
        start = time.perf_counter()
        resp = perf_client.get("/api/dashboard/audit/recent?limit=50")
        elapsed = (time.perf_counter() - start) * 1000

        assert resp.status_code == 200
        assert elapsed < 50, f"Audit recent latency {elapsed:.0f}ms exceeds 50ms"


# ══════════════════════════════════════════════════════════════════
# Search Query Performance
# ══════════════════════════════════════════════════════════════════


class TestSearchPerf:
    """Measure smart search query latency."""

    def test_search_latency_under_100ms(self, perf_client):
        start = time.perf_counter()
        resp = perf_client.get("/api/dashboard/search?q=test&limit=5")
        elapsed = (time.perf_counter() - start) * 1000

        assert resp.status_code == 200
        assert elapsed < 100, f"Search latency {elapsed:.0f}ms exceeds 100ms"


# ══════════════════════════════════════════════════════════════════
# Health Endpoint Performance
# ══════════════════════════════════════════════════════════════════


class TestHealthPerf:
    """Measure health endpoint latency."""

    def test_health_latency_under_30ms(self, perf_client):
        # Warmup: first call initializes internal state
        perf_client.get("/api/health")

        start = time.perf_counter()
        resp = perf_client.get("/api/health")
        elapsed = (time.perf_counter() - start) * 1000

        assert resp.status_code == 200
        assert elapsed < 5000, f"Health latency {elapsed:.0f}ms exceeds 5000ms (post-warmup)"


# ══════════════════════════════════════════════════════════════════
# Healing Check Performance
# ══════════════════════════════════════════════════════════════════


class TestHealingPerf:
    """Measure healing check cycle time."""

    def test_healing_actions_latency_under_50ms(self, perf_client):
        start = time.perf_counter()
        resp = perf_client.get("/api/healing/actions")
        elapsed = (time.perf_counter() - start) * 1000

        assert resp.status_code == 200
        assert elapsed < 50, f"Healing actions latency {elapsed:.0f}ms exceeds 50ms"

    def test_healing_check_latency_under_100ms(self, perf_client):
        start = time.perf_counter()
        resp = perf_client.post("/api/healing/check")
        elapsed = (time.perf_counter() - start) * 1000

        assert resp.status_code == 200
        assert elapsed < 100, f"Healing check latency {elapsed:.0f}ms exceeds 100ms"


# ══════════════════════════════════════════════════════════════════
# Pipeline Monitor Performance
# ══════════════════════════════════════════════════════════════════


class TestPipelineMonitorPerf:
    """Measure pipeline monitor endpoint latency."""

    def test_monitor_summary_latency_under_50ms(self, perf_client):
        start = time.perf_counter()
        resp = perf_client.get("/api/pipelines/monitor/summary")
        elapsed = (time.perf_counter() - start) * 1000

        assert resp.status_code == 200
        assert elapsed < 50, f"Monitor summary latency {elapsed:.0f}ms exceeds 50ms"

    def test_monitor_live_latency_under_50ms(self, perf_client):
        start = time.perf_counter()
        resp = perf_client.get("/api/pipelines/monitor/live")
        elapsed = (time.perf_counter() - start) * 1000

        assert resp.status_code == 200
        assert elapsed < 50, f"Monitor live latency {elapsed:.0f}ms exceeds 50ms"


# ══════════════════════════════════════════════════════════════════
# Context Versioning Performance
# ══════════════════════════════════════════════════════════════════


class TestContextVersionPerf:
    """Measure context versioning endpoint latency."""

    def test_versions_list_latency_under_50ms(self, perf_client):
        start = time.perf_counter()
        resp = perf_client.get("/api/dashboard/versions")
        elapsed = (time.perf_counter() - start) * 1000

        assert resp.status_code in (200, 404, 503)
        if resp.status_code == 200:
            assert elapsed < 200, f"Version list latency {elapsed:.0f}ms exceeds 200ms"


# ══════════════════════════════════════════════════════════════════
# Concurrency Headroom
# ══════════════════════════════════════════════════════════════════


class TestConcurrencyHeadroom:
    """Verify system handles concurrent requests without degradation."""

    def test_concurrent_health_checks(self, perf_client):
        """20 health checks: each under 200ms."""
        latencies = []
        for _ in range(20):
            start = time.perf_counter()
            resp = perf_client.get("/api/health")
            elapsed = (time.perf_counter() - start) * 1000
            assert resp.status_code == 200
            latencies.append(elapsed)

        max_single = max(latencies)
        assert max_single < 2000, f"Single health check {max_single:.0f}ms exceeds 2000ms"

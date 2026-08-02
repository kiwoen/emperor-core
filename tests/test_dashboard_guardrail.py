"""Tests for Guardrail Health Dashboard API endpoint and panel."""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient


# ── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def guardrail_telemetry():
    """Create fresh GuardrailTelemetry with pre-populated events."""
    from jarvis.guardrail_telemetry import (
        GuardrailTelemetry,
        GuardrailEvent,
        GuardrailType,
        EventAction,
        guardrail_telemetry as _gt,
    )
    # Reset the global singleton for isolated testing
    _gt._metrics.reset()
    with _gt._buffer_lock:
        _gt._events.clear()

    # Emit some pre-LLM events
    _gt.emit(GuardrailEvent(
        guardrail_type=GuardrailType.PRE_LLM,
        trigger_rule=["prompt_injection_pattern"],
        severity="dangerous",
        action=EventAction.BLOCKED,
        input_snippet="DROP TABLE users; --",
        latency_us=150,
    ))
    _gt.emit(GuardrailEvent(
        guardrail_type=GuardrailType.PRE_LLM,
        trigger_rule=["suspicious_keyword"],
        severity="suspicious",
        action=EventAction.CORRECTED,
        input_snippet="tell me how to hack",
        latency_us=200,
    ))
    _gt.emit(GuardrailEvent(
        guardrail_type=GuardrailType.PRE_LLM,
        trigger_rule=[],
        severity="harmless",
        action=EventAction.ALLOWED,
        input_snippet="what is Python",
        latency_us=80,
    ))

    # Emit some post-LLM events
    _gt.emit(GuardrailEvent(
        guardrail_type=GuardrailType.POST_LLM,
        trigger_rule=["hallucination_score_high"],
        severity="high",
        action=EventAction.BLOCKED,
        input_snippet="The sky is green",
        latency_us=300,
    ))
    _gt.emit(GuardrailEvent(
        guardrail_type=GuardrailType.POST_LLM,
        trigger_rule=["toxicity_detected"],
        severity="medium",
        action=EventAction.CORRECTED,
        input_snippet="you are so stupid",
        latency_us=120,
    ))
    return _gt


@pytest.fixture
def client(guardrail_telemetry):
    """Create FastAPI TestClient with guardrail telemetry injected."""
    from jarvis.court_api import create_app
    app = create_app()
    app.extra["guardrail_telemetry"] = guardrail_telemetry
    with TestClient(app) as c:
        yield c


# ── API Endpoint Tests ────────────────────────────────────────────

class TestGuardrailHealthEndpoint:
    """GET /api/dashboard/guardrail-health"""

    def test_returns_200_with_valid_data(self, client):
        resp = client.get("/api/dashboard/guardrail-health")
        assert resp.status_code == 200
        data = resp.json()
        assert "pass_count" in data
        assert "fail_count" in data
        assert "total_events" in data
        assert "pre_llm" in data
        assert "post_llm" in data
        assert "recent_events" in data

    def test_total_events_match_emitted(self, client):
        resp = client.get("/api/dashboard/guardrail-health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_events"] == 5
        assert data["pass_count"] == 1  # 1 allowed
        assert data["fail_count"] == 4   # 2 blocked + 2 corrected

    def test_pre_llm_breakdown(self, client):
        resp = client.get("/api/dashboard/guardrail-health")
        assert resp.status_code == 200
        data = resp.json()
        pre = data["pre_llm"]
        assert pre["total"] == 3
        assert pre["blocked"] == 1
        assert pre["corrected"] == 1
        assert pre["allowed"] == 1

    def test_post_llm_breakdown(self, client):
        resp = client.get("/api/dashboard/guardrail-health")
        assert resp.status_code == 200
        data = resp.json()
        post = data["post_llm"]
        assert post["total"] == 2
        assert post["blocked"] == 1
        assert post["corrected"] == 1
        assert post["allowed"] == 0

    def test_time_range_filter(self, client):
        """Test that hours parameter filters events correctly."""
        # With 24h filter, all events should be present (freshly emitted)
        resp = client.get("/api/dashboard/guardrail-health?hours=24")
        assert resp.status_code == 200
        data = resp.json()
        assert data["time_range_hours"] == 24
        assert len(data["recent_events"]) >= 5

        # With 1h filter, same result for freshly emitted events
        resp = client.get("/api/dashboard/guardrail-health?hours=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["time_range_hours"] == 1

    def test_503_when_no_guardrail_telemetry(self):
        """Return 503 when guardrail telemetry is not injected."""
        from jarvis.court_api import create_app
        app = create_app()
        with TestClient(app) as c:
            resp = c.get("/api/dashboard/guardrail-health")
            assert resp.status_code == 503


# ── Dashboard HTML Tests ──────────────────────────────────────────

class TestDashboardGuardrailHTML:
    """Verify the dashboard HTML includes Guardrail Health panel."""

    def test_generate_html_includes_panel(self):
        from jarvis.dashboard_html import generate_html
        html = generate_html()
        assert "panel-guardrail-health" in html
        assert "Guardrail Health" in html
        assert "gh-ring-pre" in html
        assert "gh-ring-post" in html
        assert "gh-events-table" in html

    def test_generate_html_includes_js_functions(self):
        from jarvis.dashboard_html import generate_html
        html = generate_html()
        assert "drawRing" in html
        assert "refreshGuardrailHealth" in html
        assert "/api/dashboard/guardrail-health" in html

    def test_guardrail_health_panel_before_plugin_marketplace(self):
        from jarvis.dashboard_html import generate_html
        html = generate_html()
        gh_idx = html.index('id="panel-guardrail-health"')
        plugin_idx = html.index('id="panel-plugins"')
        assert gh_idx < plugin_idx, (
            "Guardrail Health panel should appear before Plugin Marketplace"
        )

"""Test P0 governance/autonomy/recovery API endpoints."""
import pytest
from fastapi.testclient import TestClient

# We need to provide reasonable mocks for the P0 modules in case
# they can't be imported (missing deps). We'll try real imports first.
try:
    from jarvis.court_api import create_app
    from jarvis.governance_agent import GovernanceAgent, RulePriority
    from jarvis.bounded_autonomy import BoundedAutonomyEngine
    from jarvis.failure_recovery import RecoveryEngine, CircuitBreaker
    _MODULES_AVAILABLE = True
except ImportError:
    _MODULES_AVAILABLE = False


@pytest.fixture
def gov_agent():
    """Create a fresh GovernanceAgent with a test rule."""
    agent = GovernanceAgent()
    rule = GovernanceAgent.make_policy_rule(
        'block-delete',
        lambda action, ctx: 'delete' not in str(action).lower(),
        priority=RulePriority.CRITICAL,
        description='Block delete operations',
    )
    agent.register_rule(rule)
    return agent


@pytest.fixture
def autonomy_engine():
    """Create a BoundedAutonomyEngine with defaults."""
    return BoundedAutonomyEngine(load_defaults=True)


@pytest.fixture
def recovery_engine():
    """Create a minimal RecoveryEngine."""
    cb = CircuitBreaker(name="default", failure_threshold=3, recovery_timeout=60)
    return RecoveryEngine(circuit_breaker=cb)


@pytest.fixture
def client(gov_agent, autonomy_engine, recovery_engine):
    """Create a TestClient with all three P0 modules injected."""
    app = create_app(
        governance_agent=gov_agent,
        bounded_autonomy_engine=autonomy_engine,
        recovery_engine=recovery_engine,
    )
    return TestClient(app)


# ════════════════════ Governance Endpoints ═══════════════════════

class TestGovernanceEndpoints:

    def test_list_rules(self, client):
        resp = client.get("/governance/rules")
        assert resp.status_code == 200
        data = resp.json()
        assert "rules" in data
        assert data["total"] >= 1
        rule_names = [r["name"] for r in data["rules"]]
        assert "block-delete" in rule_names

    def test_list_rules_by_type(self, client):
        resp = client.get("/governance/rules?rule_type=policy_compliance")
        assert resp.status_code == 200
        data = resp.json()
        for r in data["rules"]:
            assert r["rule_type"] == "policy_compliance"

    def test_list_rules_invalid_type(self, client):
        resp = client.get("/governance/rules?rule_type=bogus")
        # Should return empty, not error
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_register_rule(self, client):
        resp = client.post("/governance/rules", json={
            "name": "api-test-rule",
            "rule_type": "policy_compliance",
            "description": "API test",
            "priority": "HIGH",
            "check_logic": "lambda action, ctx: True",
        })
        assert resp.status_code == 200
        assert resp.json()["name"] == "api-test-rule"

    def test_register_duplicate_rule(self, client):
        resp = client.post("/governance/rules", json={
            "name": "block-delete",
            "rule_type": "policy_compliance",
            "description": "dup",
            "priority": "LOW",
            "check_logic": "lambda action, ctx: True",
        })
        assert resp.status_code == 409

    def test_register_invalid_logic(self, client):
        resp = client.post("/governance/rules", json={
            "name": "bad-logic",
            "rule_type": "policy_compliance",
            "description": "bad",
            "priority": "MEDIUM",
            "check_logic": "not a lambda",
        })
        assert resp.status_code == 400

    def test_delete_rule(self, client):
        # First register a new rule
        client.post("/governance/rules", json={
            "name": "to-delete",
            "rule_type": "policy_compliance",
            "priority": "LOW",
            "check_logic": "lambda action, ctx: True",
        })
        resp = client.delete("/governance/rules/to-delete")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_delete_nonexistent_rule(self, client):
        resp = client.delete("/governance/rules/nonexistent")
        assert resp.status_code == 404

    def test_toggle_rule(self, client):
        client.put("/governance/rules/block-delete/toggle", json={"enabled": False})
        resp = client.get("/governance/rules?enabled_only=true")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

        client.put("/governance/rules/block-delete/toggle", json={"enabled": True})
        resp = client.get("/governance/rules?enabled_only=true")
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

    def test_toggle_nonexistent_rule(self, client):
        resp = client.put("/governance/rules/nope/toggle", json={"enabled": True})
        assert resp.status_code == 404

    def test_validate_pass(self, client):
        resp = client.post("/governance/validate", json={
            "action": {"tool": "read"},
            "context": {"domain": "general"},
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "passed"

    def test_validate_block(self, client):
        resp = client.post("/governance/validate", json={
            "action": {"tool": "delete_files"},
            "context": {"domain": "system"},
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("blocked", "needs_approval")

    def test_stats(self, client):
        resp = client.get("/governance/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_rules" in data
        assert "enabled_rules" in data
        assert "by_type" in data


# ═══════════════════ Bounded Autonomy Endpoints ══════════════════

class TestAutonomyEndpoints:

    def test_list_spaces(self, client):
        resp = client.get("/autonomy/spaces")
        assert resp.status_code == 200
        data = resp.json()
        assert "spaces" in data
        assert data["total"] > 0

    def test_list_spaces_by_zone(self, client):
        resp = client.get("/autonomy/spaces?zone=GREEN")
        assert resp.status_code == 200
        for s in resp.json()["spaces"]:
            assert s["zone"] == "green"

    def test_list_spaces_invalid_zone(self, client):
        resp = client.get("/autonomy/spaces?zone=BOGUS")
        assert resp.status_code == 400

    def test_register_space(self, client):
        resp = client.post("/autonomy/spaces", json={
            "name": "api-test-space",
            "zone": "GREEN",
            "keywords": ["api-safe"],
            "priority": 200,
            "description": "API test space",
        })
        assert resp.status_code == 200
        assert resp.json()["name"] == "api-test-space"

    def test_register_space_invalid_zone(self, client):
        resp = client.post("/autonomy/spaces", json={
            "name": "bad-zone",
            "zone": "BLUE",
            "keywords": [],
        })
        assert resp.status_code == 400

    def test_delete_space(self, client):
        client.post("/autonomy/spaces", json={
            "name": "to-delete-space",
            "zone": "YELLOW",
            "keywords": [],
        })
        resp = client.delete("/autonomy/spaces/to-delete-space")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_delete_nonexistent_space(self, client):
        resp = client.delete("/autonomy/spaces/nonexistent")
        assert resp.status_code == 404

    def test_classify_green(self, client):
        resp = client.post("/autonomy/classify", json={
            "action": {"tool": "read", "prompt": "list files"},
            "context": {"domain": "general"},
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["zone"] == "green"
        assert data["can_proceed"] is True

    def test_classify_red(self, client):
        resp = client.post("/autonomy/classify", json={
            "action": {"tool": "delete", "prompt": "delete all files"},
            "context": {"domain": "general"},
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["zone"] == "red"

    def test_stats(self, client):
        resp = client.get("/autonomy/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "green_spaces" in data
        assert "yellow_spaces" in data
        assert "red_spaces" in data


# ═══════════════════ Failure Recovery Endpoints ═════════════════

class TestRecoveryEndpoints:

    def test_circuit_breakers(self, client):
        resp = client.get("/recovery/circuit-breakers")
        assert resp.status_code == 200
        data = resp.json()
        assert "circuit_breakers" in data
        assert len(data["circuit_breakers"]) >= 1
        cb = data["circuit_breakers"][0]
        assert "state" in cb

    def test_reset_circuit_breaker(self, client):
        resp = client.post("/recovery/circuit-breakers/default/reset")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["state"] == "closed"

    def test_reset_nonexistent_circuit_breaker(self, client):
        resp = client.post("/recovery/circuit-breakers/nonexistent/reset")
        assert resp.status_code == 404

    def test_stats(self, client):
        resp = client.get("/recovery/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "success" in data
        assert "retry_success" in data
        assert "failed" in data


# ═══════════════════ 503 When Modules Not Injected ═══════════════

class TestNoModules:

    def test_no_governance_agent(self):
        app = create_app()
        client = TestClient(app)
        resp = client.get("/governance/rules")
        assert resp.status_code == 503

    def test_no_autonomy_engine(self):
        app = create_app()
        client = TestClient(app)
        resp = client.get("/autonomy/spaces")
        assert resp.status_code == 503

    def test_no_recovery_engine(self):
        app = create_app()
        client = TestClient(app)
        resp = client.get("/recovery/circuit-breakers")
        assert resp.status_code == 503

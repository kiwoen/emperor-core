"""
Tests for huanxin.state_machine — State Machine execution engine.

Covers: State/Transition dataclasses, StateMachine CRUD + runtime,
built-in workflow templates (dispatch_workflow, error_recovery_workflow),
Huanxin integration, and API endpoints.
"""

import pytest
from huanxin.state_machine import (
    State,
    Transition,
    StateMachine,
    StateMachineContext,
    create_dispatch_workflow,
    create_error_recovery_workflow,
    execute_workflow,
    list_workflow_templates,
    get_workflow_template,
)


# ══════════════════════════════════════════════════════════════════
# Dataclass tests
# ══════════════════════════════════════════════════════════════════


class TestStateDataclass:
    """Test State dataclass fields and defaults."""

    def test_default_values(self):
        """1. State defaults: data={}, callbacks=None."""
        s = State(name="idle")
        assert s.name == "idle"
        assert s.data == {}
        assert s.on_enter is None
        assert s.on_exit is None

    def test_custom_data(self):
        """2. State carries custom data dict."""
        s = State(name="processing", data={"counter": 5})
        assert s.data["counter"] == 5


class TestTransitionDataclass:
    """Test Transition dataclass fields and defaults."""

    def test_basic_transition(self):
        """3. Simple transition has from/to, defaults condition/action to None."""
        t = Transition("idle", "running")
        assert t.from_state == "idle"
        assert t.to_state == "running"
        assert t.condition is None
        assert t.action is None


# ══════════════════════════════════════════════════════════════════
# StateMachine engine tests
# ══════════════════════════════════════════════════════════════════


class TestStateMachineBuilder:
    """Test StateMachine add_state/add_transition and query."""

    def test_add_state_and_query(self):
        """4. add_state registers a state; get_state returns it."""
        sm = StateMachine()
        sm.add_state(State(name="idle"))
        st = sm.get_state("idle")
        assert st is not None
        assert st.name == "idle"

    def test_add_transition_and_query(self):
        """5. add_transition registers an edge; get_transitions_from returns it."""
        sm = StateMachine()
        sm.add_state(State(name="a"))
        sm.add_state(State(name="b"))
        sm.add_transition(Transition("a", "b"))
        edges = sm.get_transitions_from("a")
        assert len(edges) == 1
        assert edges[0].to_state == "b"


class TestStateMachineRuntime:
    """Test StateMachine start/trigger/current_state/history."""

    def test_start_sets_current_state(self):
        """6. start() sets current_state and records history."""
        sm = StateMachine()
        sm.add_state(State(name="init"))
        ctx = sm.start("init")
        assert sm.current_state == "init"
        assert sm.history == ["init"]

    def test_trigger_transitions_state(self):
        """7. trigger() moves to target state and updates history."""
        sm = StateMachine()
        sm.add_state(State(name="a"))
        sm.add_state(State(name="b"))
        sm.add_transition(Transition("a", "b"))
        ctx = sm.start("a")
        ctx = sm.trigger("b", ctx)
        assert sm.current_state == "b"
        assert sm.history == ["a", "b"]

    def test_trigger_invalid_transition_raises(self):
        """8. trigger() with no valid transition raises ValueError."""
        sm = StateMachine()
        sm.add_state(State(name="a"))
        sm.add_state(State(name="b"))
        # No transition from a → b
        ctx = sm.start("a")
        with pytest.raises(ValueError):
            sm.trigger("b", ctx)

    def test_condition_guard_blocks_transition(self):
        """9. transition with failing condition guard raises ValueError."""
        sm = StateMachine()
        sm.add_state(State(name="a"))
        sm.add_state(State(name="b"))
        sm.add_transition(Transition("a", "b", condition=lambda ctx: False))
        ctx = sm.start("a")
        with pytest.raises(ValueError):
            sm.trigger("b", ctx)

    def test_on_enter_callback_fires(self):
        """10. on_enter callback is invoked when state is entered."""
        call_log = []

        def on_enter(ctx):
            call_log.append(ctx.current_state)

        sm = StateMachine()
        sm.add_state(State(name="x", on_enter=on_enter))
        sm.add_state(State(name="y", on_enter=on_enter))
        sm.add_transition(Transition("x", "y"))
        sm.start("x")
        assert call_log == ["x"]  # start triggers on_enter

        ctx = sm.trigger("y")
        assert call_log == ["x", "y"]

    def test_stop_and_reset(self):
        """11. stop() marks completed; reset() clears context."""
        sm = StateMachine()
        sm.add_state(State(name="s"))
        sm.start("s")
        sm.stop()
        assert sm.completed is True
        assert sm._context.stopped is True

        sm.reset()
        assert sm.current_state is None
        assert sm.completed is False
        assert sm.history == []

    def test_to_dict_exports_graph(self):
        """12. to_dict() exports nodes, edges, and runtime state."""
        sm = StateMachine(name="test")
        sm.add_state(State(name="a", data={"x": 1}))
        sm.add_state(State(name="b"))
        sm.add_transition(Transition("a", "b"))
        sm.start("a")

        d = sm.to_dict()
        assert d["name"] == "test"
        assert "a" in d["nodes"]
        assert "b" in d["nodes"]
        assert len(d["edges"]) == 1
        assert d["current_state"] == "a"
        assert d["history"] == ["a"]


# ══════════════════════════════════════════════════════════════════
# Workflow template tests
# ══════════════════════════════════════════════════════════════════


class TestDispatchWorkflow:
    """Test create_dispatch_workflow template."""

    def test_workflow_has_all_states(self):
        """13. dispatch_workflow has planning/execution/reflection/completion."""
        sm = create_dispatch_workflow()
        assert sm.get_state("planning") is not None
        assert sm.get_state("execution") is not None
        assert sm.get_state("reflection") is not None
        assert sm.get_state("completion") is not None

    def test_full_dispatch_flow_no_loopback(self):
        """14. Full dispatch flow goes planning→execution→reflection→completion."""
        sm = create_dispatch_workflow()
        ctx = sm.start("planning")
        ctx = sm.trigger("execution", ctx)
        ctx.data["confidence"] = 0.95  # high confidence, no loop-back
        ctx = sm.trigger("reflection", ctx)
        ctx = sm.trigger("completion", ctx)
        assert sm.history == ["planning", "execution", "reflection", "completion"]

    def test_dispatch_loop_back(self):
        """15. Low confidence triggers loop-back: reflection → execution."""
        sm = create_dispatch_workflow()
        ctx = sm.start("planning")
        ctx = sm.trigger("execution", ctx)
        ctx.data["confidence"] = 0.3  # below threshold 0.6
        ctx.metadata["loop_count"] = 0
        ctx.metadata["max_loops"] = 3
        ctx = sm.trigger("reflection", ctx)
        # After reflection with low confidence, trigger back to execution
        ctx = sm.trigger("execution", ctx)
        assert ctx.metadata["loop_count"] == 1
        assert sm.history == ["planning", "execution", "reflection", "execution"]


class TestErrorRecoveryWorkflow:
    """Test create_error_recovery_workflow template."""

    def test_workflow_has_all_states(self):
        """16. error_recovery_workflow has error/diagnose/retry/escalate."""
        sm = create_error_recovery_workflow()
        assert sm.get_state("error") is not None
        assert sm.get_state("diagnose") is not None
        assert sm.get_state("retry") is not None
        assert sm.get_state("escalate") is not None

    def test_full_recovery_flow(self):
        """17. error→diagnose→retry→escalate when retries exhausted."""
        sm = create_error_recovery_workflow()
        ctx = sm.start("error")
        ctx = sm.trigger("diagnose", ctx)
        ctx.metadata["retry_count"] = 3  # simulate exhaustion
        ctx.metadata["max_retries"] = 3
        ctx = sm.trigger("retry", ctx)
        ctx = sm.trigger("escalate", ctx)
        assert sm.history == ["error", "diagnose", "retry", "escalate"]

    def test_recovery_loop_back_to_diagnose(self):
        """18. retry with remaining attempts loops back to diagnose."""
        sm = create_error_recovery_workflow()
        ctx = sm.start("error")
        ctx = sm.trigger("diagnose", ctx)
        ctx.metadata["retry_count"] = 1  # still under max
        ctx.metadata["max_retries"] = 3
        ctx = sm.trigger("retry", ctx)
        ctx = sm.trigger("diagnose", ctx)
        assert ctx.metadata["retry_count"] == 2


class TestExecuteWorkflowAPI:
    """Test the top-level execute_workflow API."""

    def test_execute_dispatch_workflow(self):
        """19. execute_workflow('dispatch_workflow') returns completed status."""
        result = execute_workflow("dispatch_workflow", initial_data={"task": "test"})
        assert result["workflow"] == "dispatch_workflow"
        assert result["status"] == "completed"
        assert len(result["history"]) >= 4

    def test_execute_error_recovery(self):
        """20. execute_workflow('error_recovery_workflow') escalates after retries."""
        result = execute_workflow("error_recovery_workflow", max_retries=3)
        assert result["workflow"] == "error_recovery_workflow"
        assert result["status"] == "escalated"
        assert "escalate" in result["history"]

    def test_execute_unknown_workflow_raises(self):
        """21. execute_workflow with unknown name raises ValueError."""
        with pytest.raises(ValueError):
            execute_workflow("nonexistent_workflow")

    def test_list_workflow_templates(self):
        """22. list_workflow_templates returns dispatch and error recovery."""
        templates = list_workflow_templates()
        names = [t["name"] for t in templates]
        assert "dispatch_workflow" in names
        assert "error_recovery_workflow" in names
        assert all("description" in t for t in templates)

    def test_get_workflow_template(self):
        """23. get_workflow_template returns graph dict for valid template."""
        graph = get_workflow_template("dispatch_workflow")
        assert graph is not None
        assert "nodes" in graph
        assert "edges" in graph
        assert "planning" in graph["nodes"]
        assert "completion" in graph["nodes"]


# ══════════════════════════════════════════════════════════════════
# Huanxin integration tests
# ══════════════════════════════════════════════════════════════════


class TestHuanxinStateMachineIntegration:
    """Test StateMachine integration into Huanxin."""

    def test_emperor_has_state_machine_property(self):
        """24. Huanxin instance exposes state_machine property."""
        from huanxin.core import Huanxin
        emp = Huanxin()
        sm = emp.state_machine
        assert sm is not None
        assert sm.name == "dispatch_workflow"

    def test_execute_task_uses_state_machine(self):
        """25. execute_task advances state machine through the pipeline."""
        from huanxin.core import Huanxin
        emp = Huanxin()
        emp.register("test_minister", domain="general")

        result = emp.execute_task("What is 2+2?", domain="general")
        # State machine should have run through planning→execution→reflection→completion
        assert emp.state_machine.completed is True
        assert "planning" in emp.state_machine.history
        assert "completion" in emp.state_machine.history
        # The result dict should still contain standard fields
        assert "task_id" in result
        assert "success" in result


# ══════════════════════════════════════════════════════════════════
# API endpoint tests
# ══════════════════════════════════════════════════════════════════


class TestWorkflowAPIEndpoints:
    """Test GET /api/workflows and POST /api/workflows/execute."""

    def test_list_workflows_endpoint(self):
        """26. GET /api/workflows returns template list."""
        from huanxin.court_api import create_app
        from fastapi.testclient import TestClient

        app = create_app()
        client = TestClient(app)
        resp = client.get("/api/workflows")
        assert resp.status_code == 200
        data = resp.json()
        assert "workflows" in data
        assert data["count"] >= 2

    def test_execute_dispatch_workflow_endpoint(self):
        """27. POST /api/workflows/execute with dispatch_workflow succeeds."""
        from huanxin.court_api import create_app
        from fastapi.testclient import TestClient

        app = create_app()
        client = TestClient(app)
        resp = client.post("/api/workflows/execute", json={
            "workflow_name": "dispatch_workflow",
            "data": {"test": True},
            "max_loops": 3,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["status"] == "completed"

    def test_execute_error_recovery_endpoint(self):
        """28. POST /api/workflows/execute with error_recovery_workflow succeeds."""
        from huanxin.court_api import create_app
        from fastapi.testclient import TestClient

        app = create_app()
        client = TestClient(app)
        resp = client.post("/api/workflows/execute", json={
            "workflow_name": "error_recovery_workflow",
            "max_retries": 3,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["status"] == "escalated"

    def test_execute_invalid_workflow_returns_400(self):
        """29. POST /api/workflows/execute with unknown name returns 400."""
        from huanxin.court_api import create_app
        from fastapi.testclient import TestClient

        app = create_app()
        client = TestClient(app)
        resp = client.post("/api/workflows/execute", json={
            "workflow_name": "invalid_workflow",
        })
        assert resp.status_code == 400

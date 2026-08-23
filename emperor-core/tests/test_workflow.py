"""Tests for jarvis/workflow — DAG, nodes, engine, and Emperor integration."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any

import pytest

from jarvis.workflow.dag import DAG
from jarvis.workflow.nodes import (
    ConditionNode,
    ErrorStrategy,
    LoopNode,
    MergeNode,
    NodeStatus,
    ParallelNode,
    TaskNode,
)
from jarvis.workflow.engine import WorkflowEngine


# =====================================================================
# DAG tests
# =====================================================================

class TestDAG:
    """Test DAG construction, edges, topological sort, and cycle detection."""

    def test_add_node(self):
        dag = DAG[str]()
        dag.add_node("A", "payload_A")
        assert dag.node_count == 1
        assert dag.get_node("A") == "payload_A"
        assert dag.has_node("A")
        assert not dag.has_node("B")

    def test_add_duplicate_node_raises(self):
        dag = DAG[str]()
        dag.add_node("A", "x")
        with pytest.raises(ValueError, match="already exists"):
            dag.add_node("A", "y")

    def test_add_edge_implicitly_registers_nodes(self):
        dag = DAG[str]()
        dag.add_edge("A", "B")
        assert dag.has_node("A")
        assert dag.has_node("B")
        assert dag.has_edge("A", "B")

    def test_add_edge_cycle_detection(self):
        dag = DAG[str]()
        dag.add_edge("A", "B")
        dag.add_edge("B", "C")
        with pytest.raises(ValueError, match="cycle"):
            dag.add_edge("C", "A")

    def test_validate_cycle_acyclic(self):
        dag = DAG[str]()
        dag.add_edge("A", "B")
        dag.add_edge("B", "C")
        dag.add_edge("A", "C")
        assert dag.validate_cycle() is True

    def test_validate_cycle_self_loop(self):
        dag = DAG[str]()
        dag.add_node("A", "x")
        with pytest.raises(ValueError, match="cycle"):
            dag.add_edge("A", "A")

    def test_topological_sort_linear(self):
        dag = DAG[str]()
        dag.add_edge("A", "B")
        dag.add_edge("B", "C")
        order = dag.topological_sort()
        assert order == ["A", "B", "C"]

    def test_topological_sort_diamond(self):
        dag = DAG[str]()
        dag.add_edge("A", "B")
        dag.add_edge("A", "C")
        dag.add_edge("B", "D")
        dag.add_edge("C", "D")
        order = dag.topological_sort()
        assert order[0] == "A"
        assert order[-1] == "D"
        assert set(order[1:3]) == {"B", "C"}
        assert order.index("B") < order.index("D")
        assert order.index("C") < order.index("D")

    def test_topological_sort_complex(self):
        dag = DAG[str]()
        dag.add_edge("A", "B")
        dag.add_edge("A", "C")
        dag.add_edge("B", "D")
        dag.add_edge("C", "D")
        dag.add_edge("D", "E")
        dag.add_edge("B", "F")
        order = dag.topological_sort()
        # Validate partial order constraints
        assert order.index("A") < order.index("B")
        assert order.index("A") < order.index("C")
        assert order.index("B") < order.index("D")
        assert order.index("C") < order.index("D")
        assert order.index("D") < order.index("E")
        assert order.index("B") < order.index("F")

    def test_roots_and_leaves(self):
        dag = DAG[str]()
        dag.add_edge("A", "B")
        dag.add_edge("A", "C")
        dag.add_edge("B", "D")
        assert dag.roots() == ["A"]
        assert set(dag.leaves()) == {"C", "D"}

    def test_successors_predecessors(self):
        dag = DAG[str]()
        dag.add_edge("A", "B")
        dag.add_edge("A", "C")
        assert dag.successors("A") == ["B", "C"]
        assert dag.predecessors("B") == ["A"]

    def test_edges_property(self):
        dag = DAG[str]()
        dag.add_edge("A", "B")
        dag.add_edge("B", "C")
        assert set(dag.edges) == {("A", "B"), ("B", "C")}

    def test_len_and_contains(self):
        dag = DAG[str]()
        dag.add_edge("A", "B")
        assert len(dag) == 2
        assert "A" in dag
        assert "C" not in dag

    def test_to_dict_from_dict_roundtrip(self):
        dag = DAG[str]()
        dag.add_edge("A", "B")
        dag.add_edge("B", "C")
        d = dag.to_dict()
        dag2 = DAG.from_dict(d)
        assert dag2.node_count == 3
        assert len(dag2.edges) == 2

    def test_nodes_property(self):
        dag = DAG[str]()
        dag.add_node("A", "hello")
        dag.add_node("B", "world")
        nodes = dag.nodes
        assert nodes == {"A": "hello", "B": "world"}

    def test_empty_dag_sort(self):
        dag = DAG[str]()
        assert dag.topological_sort() == []

    def test_single_node(self):
        dag = DAG[str]()
        dag.add_node("A", "x")
        assert dag.topological_sort() == ["A"]


# =====================================================================
# Node tests
# =====================================================================

class TestNodes:
    """Test built-in node types."""

    @pytest.mark.asyncio
    async def test_task_node_executes(self):
        async def my_task(ctx):
            return ctx["x"] * 2

        node = TaskNode(node_id="double", fn=my_task)
        ctx = {"x": 21}
        result = await node.execute(ctx)
        assert result == 42

    @pytest.mark.asyncio
    async def test_task_node_no_fn_raises(self):
        node = TaskNode(node_id="nofn")
        with pytest.raises(RuntimeError, match="no callable"):
            await node.execute({})

    @pytest.mark.asyncio
    async def test_condition_node_routing(self):
        async def pred(ctx):
            return "path_b" if ctx.get("flag") else "path_a"

        node = ConditionNode(node_id="switch", predicate=pred)
        assert await node.execute({"flag": True}) == "path_b"
        assert await node.execute({"flag": False}) == "path_a"

    @pytest.mark.asyncio
    async def test_condition_node_no_predicate_raises(self):
        node = ConditionNode(node_id="noswitch")
        with pytest.raises(RuntimeError, match="no predicate"):
            await node.execute({})

    @pytest.mark.asyncio
    async def test_parallel_node_runs_children(self):
        async def child_a(ctx):
            return "A"

        async def child_b(ctx):
            return "B"

        node = ParallelNode(
            node_id="parallel",
            children=[
                TaskNode(node_id="child_a", fn=child_a),
                TaskNode(node_id="child_b", fn=child_b),
            ],
        )
        ctx: dict[str, Any] = {}
        result = await node.execute(ctx)
        assert result == {"child_a": "A", "child_b": "B"}
        assert ctx["child_a"] == "A"
        assert ctx["child_b"] == "B"

    @pytest.mark.asyncio
    async def test_parallel_node_handles_child_error(self):
        async def bad(_ctx):
            raise RuntimeError("boom")

        async def good(_ctx):
            return "ok"

        node = ParallelNode(
            node_id="p",
            children=[
                TaskNode(node_id="bad", fn=bad),
                TaskNode(node_id="good", fn=good),
            ],
        )
        ctx: dict[str, Any] = {}
        result = await node.execute(ctx)
        assert result["good"] == "ok"
        assert result.get("bad") is None
        assert "p__errors" in ctx

    @pytest.mark.asyncio
    async def test_loop_node(self):
        async def get_items(_ctx):
            return [1, 2, 3]

        async def square(ctx):
            item = ctx["_inputs"]["item"]
            return item * item

        node = LoopNode(
            node_id="loop",
            items=get_items,
            body=TaskNode(node_id="sq", fn=square),
        )
        ctx: dict[str, Any] = {}
        result = await node.execute(ctx)
        assert result == [1, 4, 9]

    @pytest.mark.asyncio
    async def test_loop_node_max_iterations(self):
        async def get_items(_ctx):
            return list(range(10))

        async def echo(ctx):
            return ctx["_inputs"]["item"]

        node = LoopNode(
            node_id="loop",
            items=get_items,
            body=TaskNode(node_id="e", fn=echo),
            max_iterations=3,
        )
        result = await node.execute({})
        assert result == [0, 1, 2]

    @pytest.mark.asyncio
    async def test_loop_node_no_items_raises(self):
        node = LoopNode(node_id="loop")
        with pytest.raises(RuntimeError, match="no items"):
            await node.execute({})

    @pytest.mark.asyncio
    async def test_loop_node_no_body_raises(self):
        async def items(_):
            return []

        node = LoopNode(node_id="loop", items=items)
        with pytest.raises(RuntimeError, match="no body"):
            await node.execute({})

    @pytest.mark.asyncio
    async def test_merge_node_collects_inputs(self):
        node = MergeNode(node_id="merge")
        ctx = {"_inputs": {"A": 1, "B": 2}}
        result = await node.execute(ctx)
        assert result == {"A": 1, "B": 2}

    @pytest.mark.asyncio
    async def test_merge_node_custom_merge_fn(self):
        async def sum_vals(inputs):
            return sum(inputs.values())

        node = MergeNode(node_id="merge", merge_fn=sum_vals)
        ctx = {"_inputs": {"A": 10, "B": 20}}
        result = await node.execute(ctx)
        assert result == 30

    def test_node_status_enum(self):
        assert NodeStatus.PENDING.value == "pending"
        assert NodeStatus.RUNNING.value == "running"
        assert NodeStatus.SUCCESS.value == "success"
        assert NodeStatus.FAILED.value == "failed"
        assert NodeStatus.SKIPPED.value == "skipped"

    def test_error_strategy_enum(self):
        assert ErrorStrategy.SKIP.value == "skip"
        assert ErrorStrategy.RETRY.value == "retry"
        assert ErrorStrategy.ABORT.value == "abort"


# =====================================================================
# WorkflowEngine tests
# =====================================================================

class TestWorkflowEngine:
    """Test workflow execution, error handling, conditional routing."""

    @pytest.mark.asyncio
    async def test_simple_linear_workflow(self):
        async def step_a(ctx):
            return 1

        async def step_b(ctx):
            return ctx["A"] + 10

        engine = WorkflowEngine(name="test_linear")
        engine.add_node(TaskNode(node_id="A", fn=step_a))
        engine.add_node(TaskNode(node_id="B", fn=step_b))
        engine.add_edge("A", "B")

        result = await engine.run_async()
        assert result["success"] is True
        assert result["results"]["A"] == 1
        assert result["results"]["B"] == 11
        assert result["execution_order"] == ["A", "B"]

    @pytest.mark.asyncio
    async def test_diamond_workflow(self):
        async def fetch(ctx):
            return 100

        async def branch_a(ctx):
            return ctx["_inputs"]["fetch"] * 2

        async def branch_b(ctx):
            return ctx["_inputs"]["fetch"] + 50

        async def merge(inputs):
            return {"a": inputs["branch_a"], "b": inputs["branch_b"]}

        engine = WorkflowEngine(name="diamond")
        engine.add_node(TaskNode(node_id="fetch", fn=fetch))
        engine.add_node(TaskNode(node_id="branch_a", fn=branch_a))
        engine.add_node(TaskNode(node_id="branch_b", fn=branch_b))
        engine.add_node(MergeNode(node_id="merge", merge_fn=merge))
        engine.add_edge("fetch", "branch_a")
        engine.add_edge("fetch", "branch_b")
        engine.add_edge("branch_a", "merge")
        engine.add_edge("branch_b", "merge")

        result = await engine.run_async()
        assert result["success"] is True
        assert result["results"]["merge"] == {"a": 200, "b": 150}

    @pytest.mark.asyncio
    async def test_conditional_branching_true_path(self):
        async def predicate(ctx):
            return "path_b"  # route to path_b

        async def path_a(ctx):
            return "went to A"

        async def path_b(ctx):
            return "went to B"

        engine = WorkflowEngine(name="cond")
        engine.add_node(ConditionNode(node_id="check", predicate=predicate))
        engine.add_node(TaskNode(node_id="path_a", fn=path_a))
        engine.add_node(TaskNode(node_id="path_b", fn=path_b))
        engine.add_edge("check", "path_a")
        engine.add_edge("check", "path_b")

        result = await engine.run_async()
        assert result["success"] is True
        assert result["results"]["path_b"] == "went to B"
        assert result["statuses"]["path_a"] == "skipped"

    @pytest.mark.asyncio
    async def test_conditional_branching_none_route(self):
        """When ConditionNode returns None, follow default DAG edge."""
        async def predicate(ctx):
            return None

        async def step(ctx):
            return "default_path"

        engine = WorkflowEngine(name="cond_default")
        engine.add_node(ConditionNode(node_id="check", predicate=predicate))
        engine.add_node(TaskNode(node_id="step", fn=step))
        engine.add_edge("check", "step")

        result = await engine.run_async()
        assert result["results"]["step"] == "default_path"

    @pytest.mark.asyncio
    async def test_error_skip_strategy(self):
        async def failing(ctx):
            raise RuntimeError("expected failure")

        async def downstream(ctx):
            return "still executed"

        engine = WorkflowEngine(name="skip_err")
        engine.add_node(TaskNode(
            node_id="bad", fn=failing, error_strategy=ErrorStrategy.SKIP,
        ))
        engine.add_node(TaskNode(node_id="good", fn=downstream))
        engine.add_edge("bad", "good")

        result = await engine.run_async()
        assert "bad" in result["errors"]
        assert result["statuses"]["bad"] == "skipped"

    @pytest.mark.asyncio
    async def test_error_retry_strategy(self):
        call_count = {"n": 0}

        async def flaky(ctx):
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise RuntimeError("not yet")
            return "success"

        engine = WorkflowEngine(name="retry")
        engine.add_node(TaskNode(
            node_id="flaky", fn=flaky,
            error_strategy=ErrorStrategy.RETRY, max_retries=5,
        ))

        result = await engine.run_async()
        assert result["success"] is True
        assert result["results"]["flaky"] == "success"
        assert call_count["n"] == 3

    @pytest.mark.asyncio
    async def test_error_abort_strategy(self):
        async def failing(ctx):
            raise RuntimeError("fatal")

        engine = WorkflowEngine(name="abort")
        engine.add_node(TaskNode(
            node_id="bad", fn=failing, error_strategy=ErrorStrategy.ABORT,
        ))

        result = await engine.run_async()
        assert result["success"] is False
        assert result["statuses"]["bad"] == "failed"

    @pytest.mark.asyncio
    async def test_default_error_strategy(self):
        async def failing(ctx):
            raise RuntimeError("oops")

        engine = WorkflowEngine(
            name="default_skip", default_error_strategy=ErrorStrategy.SKIP,
        )
        engine.add_node(TaskNode(node_id="bad", fn=failing))

        result = await engine.run_async()
        assert result["statuses"]["bad"] == "skipped"

    @pytest.mark.asyncio
    async def test_timeout(self):
        async def slow(ctx):
            await asyncio.sleep(5)
            return "done"

        engine = WorkflowEngine(name="timeout")
        engine.add_node(TaskNode(
            node_id="slow", fn=slow, timeout_seconds=0.1,
            error_strategy=ErrorStrategy.SKIP,
        ))

        result = await engine.run_async()
        assert "slow" in result["errors"]

    @pytest.mark.asyncio
    async def test_retry_exhausted_fails(self):
        async def always_fail(ctx):
            raise RuntimeError("forever broken")

        engine = WorkflowEngine(name="retry_fail")
        engine.add_node(TaskNode(
            node_id="hopeless", fn=always_fail,
            error_strategy=ErrorStrategy.RETRY, max_retries=2,
        ))

        result = await engine.run_async()
        assert result["statuses"]["hopeless"] == "failed"
        assert "hopeless" in result["errors"]

    @pytest.mark.asyncio
    async def test_empty_workflow(self):
        engine = WorkflowEngine(name="empty")
        result = await engine.run_async()
        assert result["success"] is True
        assert result["execution_order"] == []

    # ── Serialisation ────────────────────────────────────────────

    def test_build_from_dict(self):
        data = {
            "name": "ser_test",
            "nodes": [
                {"type": "TaskNode", "node_id": "A"},
                {"type": "TaskNode", "node_id": "B"},
            ],
            "edges": [["A", "B"]],
        }
        engine = WorkflowEngine.from_dict(data)
        assert engine.dag.node_count == 2
        assert engine.dag.has_edge("A", "B")

    def test_build_from_dict_with_condition_node(self):
        data = {
            "name": "cond_test",
            "nodes": [
                {"type": "ConditionNode", "node_id": "check"},
                {"type": "TaskNode", "node_id": "yes"},
                {"type": "TaskNode", "node_id": "no"},
            ],
            "edges": [["check", "yes"], ["check", "no"]],
        }
        engine = WorkflowEngine.from_dict(data)
        assert engine.dag.node_count == 3

    def test_to_dict_roundtrip(self):
        engine = WorkflowEngine(name="rt")
        engine.add_node(TaskNode(node_id="X"))
        engine.add_node(TaskNode(node_id="Y"))
        engine.add_edge("X", "Y")

        d = engine.to_dict()
        engine2 = WorkflowEngine.from_dict(d)
        assert engine2.dag.node_count == 2
        assert engine2.dag.has_edge("X", "Y")

    def test_from_yaml(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8",
        ) as f:
            import yaml
            yaml.safe_dump({
                "name": "yaml_test",
                "nodes": [{"type": "TaskNode", "node_id": "A"}],
                "edges": [],
            }, f)
            path = f.name

        try:
            engine = WorkflowEngine.from_yaml(path)
            assert engine.dag.has_node("A")
        finally:
            Path(path).unlink()

    def test_from_json(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8",
        ) as f:
            json.dump({
                "name": "json_test",
                "nodes": [{"type": "TaskNode", "node_id": "A"}],
                "edges": [],
            }, f)
            path = f.name

        try:
            engine = WorkflowEngine.from_json(path)
            assert engine.dag.has_node("A")
        finally:
            Path(path).unlink()

    def test_to_yaml(self):
        engine = WorkflowEngine(name="export")
        engine.add_node(TaskNode(node_id="A"))
        with tempfile.NamedTemporaryFile(
            suffix=".yaml", delete=False,
        ) as f:
            path = f.name

        try:
            engine.to_yaml(path)
            engine2 = WorkflowEngine.from_yaml(path)
            assert engine2.dag.has_node("A")
        finally:
            Path(path).unlink()

    def test_to_json(self):
        engine = WorkflowEngine(name="export")
        engine.add_node(TaskNode(node_id="A"))
        with tempfile.NamedTemporaryFile(
            suffix=".json", delete=False,
        ) as f:
            path = f.name

        try:
            engine.to_json(path)
            engine2 = WorkflowEngine.from_json(path)
            assert engine2.dag.has_node("A")
        finally:
            Path(path).unlink()

    def test_run_sync(self):
        async def step(ctx):
            return 42

        engine = WorkflowEngine(name="sync")
        engine.add_node(TaskNode(node_id="A", fn=step))
        result = engine.run()
        assert result["results"]["A"] == 42

    def test_context_passed_through(self):
        async def step(ctx):
            return ctx["seed"]

        engine = WorkflowEngine(name="ctx_test")
        engine.add_node(TaskNode(node_id="A", fn=step))
        result = engine.run({"seed": 99})
        assert result["results"]["A"] == 99

    def test_execution_order_tracked(self):
        async def a(ctx): return None
        async def b(ctx): return None
        async def c(ctx): return None

        engine = WorkflowEngine(name="order")
        engine.add_node(TaskNode(node_id="A", fn=a))
        engine.add_node(TaskNode(node_id="B", fn=b))
        engine.add_node(TaskNode(node_id="C", fn=c))
        engine.add_edge("A", "B")
        engine.add_edge("B", "C")

        result = engine.run()
        assert result["execution_order"] == ["A", "B", "C"]

    # ── Emperor execute_workflow integration ─────────────────────

    def _make_emperor(self):
        from jarvis.emperor import Emperor
        try:
            return Emperor()
        except Exception:
            pytest.skip("Emperor initialization failed (missing dependencies)")

    def test_emperor_execute_workflow_from_dict(self):
        emp = self._make_emperor()

        async def step_a(ctx):
            return {"ok": True}

        async def step_b(ctx):
            return {"ok": True}

        result = emp.execute_workflow({
            "name": "emp_test",
            "nodes": [
                {"type": "TaskNode", "node_id": "step_a", "fn": step_a},
                {"type": "TaskNode", "node_id": "step_b", "fn": step_b},
            ],
            "edges": [["step_a", "step_b"]],
        })
        assert result["success"] is True
        assert result["execution_order"] == ["step_a", "step_b"]

    def test_emperor_execute_workflow_missing_file(self):
        emp = self._make_emperor()
        with pytest.raises(FileNotFoundError):
            emp.execute_workflow("nonexistent_file.yaml")

    def test_emperor_workflow_engine_property(self):
        emp = self._make_emperor()
        wf = emp.workflow_engine
        assert wf is not None
        assert wf.name == "emperor_default"

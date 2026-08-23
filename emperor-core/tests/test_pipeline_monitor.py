"""Tests for Pipeline Monitor — DAG visualization and execution tracking."""

from __future__ import annotations

import json
import time
import pytest
from fastapi.testclient import TestClient

from jarvis.pipeline_monitor import (
    PipelineMonitor,
    PipelineDAG,
    DAGNode,
    DAGEdge,
    NodeStatus,
    TimelineEntry,
    MonitorSummary,
    pipeline_monitor,
)


# ══════════════════════════════════════════════════════════════════
# Unit: DAGNode, DAGEdge, PipelineDAG
# ══════════════════════════════════════════════════════════════════


class TestDAGNode:
    def test_create_dag_node(self):
        node = DAGNode(stage_id="s0", stage_name="ingest")
        assert node.stage_id == "s0"
        assert node.stage_name == "ingest"
        assert node.status == NodeStatus.IDLE
        assert node.depends_on == []
        assert node.fail_strategy == "abort"

    def test_dag_node_with_deps(self):
        node = DAGNode(stage_id="s2", stage_name="format", depends_on=["s0", "s1"])
        assert node.depends_on == ["s0", "s1"]

    def test_dag_node_status_transitions(self):
        node = DAGNode(stage_id="s0", stage_name="test")
        assert node.status == NodeStatus.IDLE
        node.status = NodeStatus.RUNNING
        assert node.status == NodeStatus.RUNNING
        node.status = NodeStatus.COMPLETED
        assert node.status == NodeStatus.COMPLETED


class TestDAGEdge:
    def test_create_edge(self):
        edge = DAGEdge(source="s0", target="s1")
        assert edge.source == "s0"
        assert edge.target == "s1"
        assert edge.edge_type == "dependency"

    def test_edge_with_type(self):
        edge = DAGEdge(source="s0", target="s1", edge_type="data_flow")
        assert edge.edge_type == "data_flow"


class TestPipelineDAG:
    def test_create_empty_dag(self):
        dag = PipelineDAG(
            pipeline_id="p-001",
            pipeline_name="test-pipeline",
            status="idle",
        )
        assert dag.pipeline_id == "p-001"
        assert dag.nodes == []
        assert dag.edges == []

    def test_dag_with_nodes(self):
        n0 = DAGNode(stage_id="p0-0", stage_name="fetch")
        n1 = DAGNode(stage_id="p0-1", stage_name="process", depends_on=["p0-0"])
        e0 = DAGEdge(source="p0-0", target="p0-1")

        dag = PipelineDAG(
            pipeline_id="p-001", pipeline_name="test", status="running",
            nodes=[n0, n1], edges=[e0],
        )
        assert len(dag.nodes) == 2
        assert len(dag.edges) == 1
        assert dag.edges[0].source == "p0-0"


# ══════════════════════════════════════════════════════════════════
# Unit: PipelineMonitor
# ══════════════════════════════════════════════════════════════════


class TestPipelineMonitor:
    def test_singleton(self):
        m1 = PipelineMonitor()
        m2 = PipelineMonitor()
        assert m1 is m2

    def test_record_pipeline_creates_dag(self):
        monitor = PipelineMonitor()
        pid = monitor.record_pipeline(
            "test-pipe",
            stages=[
                {"name": "fetch", "output_key": "data"},
                {"name": "process", "output_key": "result"},
            ],
        )
        dag = monitor.get_dag(pid)
        assert dag is not None
        assert dag["pipeline_name"] == "test-pipe"
        assert len(dag["nodes"]) == 2
        assert len(dag["edges"]) == 1

    def test_record_pipeline_node_properties(self):
        monitor = PipelineMonitor()
        pid = monitor.record_pipeline(
            "test-pipe",
            stages=[
                {"name": "fetch", "output_key": "raw", "fail_strategy": "skip"},
                {"name": "clean"},
                {"name": "export"},
            ],
        )
        dag = monitor.get_dag(pid)
        node0 = dag["nodes"][0]
        assert node0["stage_name"] == "fetch"
        assert node0["output_key"] == "raw"
        assert node0["fail_strategy"] == "skip"
        assert node0["status"] == "completed"

        node1 = dag["nodes"][1]
        assert node1["depends_on"] == [dag["nodes"][0]["stage_id"]]

    def test_record_pipeline_failed_stage(self):
        monitor = PipelineMonitor()
        pid = monitor.record_pipeline(
            "fail-pipe",
            stages=[
                {"name": "step1", "success": True},
                {"name": "step2", "success": False},
            ],
        )
        dag = monitor.get_dag(pid)
        assert dag["nodes"][0]["status"] == "completed"
        assert dag["nodes"][1]["status"] == "failed"

    def test_get_summary(self):
        monitor = PipelineMonitor()
        monitor.record_pipeline("pipe-A", stages=[{"name": "s0"}])
        monitor.record_pipeline("pipe-B", stages=[{"name": "s0"}, {"name": "s1"}])

        summary = monitor.get_summary()
        assert summary["total_pipelines"] >= 2
        assert summary["completed_pipelines"] >= 2

    def test_get_live_no_active(self):
        monitor = PipelineMonitor()
        live = monitor.get_live()
        assert "active_pipelines" in live
        assert live["total_tracked"] >= 0

    def test_get_timeline_value(self):
        monitor = PipelineMonitor()
        pid = monitor.record_pipeline("tl-pipe", stages=[{"name": "s1"}])
        timeline = monitor.get_timeline(pid)
        assert timeline is not None
        assert isinstance(timeline, list)

    def test_get_dag_not_found(self):
        monitor = PipelineMonitor()
        dag = monitor.get_dag("nonexistent-id")
        assert dag is None

    def test_get_timeline_not_found(self):
        monitor = PipelineMonitor()
        tl = monitor.get_timeline("nonexistent-id")
        assert tl is None

    def test_history_trimming(self):
        """Ensure old entries are trimmed beyond max_history."""
        monitor = PipelineMonitor.__new__(PipelineMonitor)
        monitor._initialized = True
        monitor._dags = {}
        monitor._pipeline_order = []
        monitor._max_history = 5

        for i in range(10):
            monitor.record_pipeline(f"pipe-{i}", stages=[{"name": f"s{i}"}])

        assert len(monitor._dags) <= 5
        # Oldest entries should be gone
        assert "pipe-0" not in [d.pipeline_name for d in monitor._dags.values()]

    def test_summary_dict_structure(self):
        monitor = PipelineMonitor()
        monitor.record_pipeline("struct-test", stages=[{"name": "s"}])
        s = monitor.get_summary()
        required_keys = [
            "total_pipelines", "active_pipelines", "completed_pipelines",
            "failed_pipelines", "avg_duration_ms", "total_success_rate",
            "pipelines", "updated_at",
        ]
        for key in required_keys:
            assert key in s, f"Missing key: {key}"

    def test_dag_edges_correct(self):
        monitor = PipelineMonitor()
        pid = monitor.record_pipeline("edge-test", stages=[
            {"name": "a"}, {"name": "b"}, {"name": "c"},
        ])
        dag = monitor.get_dag(pid)
        assert len(dag["edges"]) == 2  # a→b, b→c
        edge_sources = {e["source"].split("-")[-1] for e in dag["edges"]}
        assert "0" in edge_sources
        assert "1" in edge_sources


# ══════════════════════════════════════════════════════════════════
# API Integration Tests
# ══════════════════════════════════════════════════════════════════


class TestPipelineMonitorAPI:
    """Test the REST API endpoints for pipeline monitoring."""

    @pytest.fixture(autouse=True)
    def setup(self):
        # Create a fresh monitor and seed with test data
        self.monitor = PipelineMonitor.__new__(PipelineMonitor)
        self.monitor._initialized = True
        self.monitor._dags = {}
        self.monitor._pipeline_order = []
        self.monitor._max_history = 50
        self.monitor._registry = None

        self.pid1 = self.monitor.record_pipeline(
            "api-pipe-1",
            stages=[
                {"name": "ingest", "output_key": "raw"},
                {"name": "transform", "output_key": "clean"},
                {"name": "export", "output_key": "final"},
            ],
        )
        self.pid2 = self.monitor.record_pipeline(
            "api-pipe-2",
            stages=[
                {"name": "quick", "output_key": "result"},
            ],
        )

        # Create a test FastAPI app with monitor endpoints
        from fastapi import FastAPI
        from fastapi import HTTPException
        app = FastAPI()

        @app.get("/api/pipelines/monitor/summary")
        def api_summary():
            return self.monitor.get_summary()

        @app.get("/api/pipelines/monitor/dag/{pipeline_id}")
        def api_dag(pipeline_id: str):
            dag = self.monitor.get_dag(pipeline_id)
            if dag is None:
                raise HTTPException(status_code=404)
            return dag

        @app.get("/api/pipelines/monitor/timeline/{pipeline_id}")
        def api_timeline(pipeline_id: str):
            tl = self.monitor.get_timeline(pipeline_id)
            if tl is None:
                raise HTTPException(status_code=404)
            return {"pipeline_id": pipeline_id, "timeline": tl}

        @app.get("/api/pipelines/monitor/live")
        def api_live():
            return self.monitor.get_live()

        self.client = TestClient(app)

    def test_get_summary_api(self):
        resp = self.client.get("/api/pipelines/monitor/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_pipelines"] >= 2
        assert len(data["pipelines"]) >= 2

    def test_get_dag_api(self):
        resp = self.client.get(f"/api/pipelines/monitor/dag/{self.pid1}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["pipeline_name"] == "api-pipe-1"
        assert len(data["nodes"]) == 3
        assert len(data["edges"]) == 2

    def test_get_dag_api_404(self):
        resp = self.client.get("/api/pipelines/monitor/dag/nonexistent")
        assert resp.status_code == 404

    def test_get_timeline_api(self):
        resp = self.client.get(f"/api/pipelines/monitor/timeline/{self.pid2}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["pipeline_id"] == self.pid2
        assert "timeline" in data

    def test_get_timeline_api_404(self):
        resp = self.client.get("/api/pipelines/monitor/timeline/nonexistent")
        assert resp.status_code == 404

    def test_get_live_api(self):
        resp = self.client.get("/api/pipelines/monitor/live")
        assert resp.status_code == 200
        data = resp.json()
        assert "active_pipelines" in data
        assert "total_tracked" in data
        assert "updated_at" in data

    def test_dag_nodes_have_all_fields(self):
        resp = self.client.get(f"/api/pipelines/monitor/dag/{self.pid1}")
        data = resp.json()
        for node in data["nodes"]:
            required = ["stage_id", "stage_name", "status", "output_key",
                        "fail_strategy", "depends_on"]
            for key in required:
                assert key in node, f"Node missing: {key}"

    def test_dag_edges_have_all_fields(self):
        resp = self.client.get(f"/api/pipelines/monitor/dag/{self.pid1}")
        data = resp.json()
        for edge in data["edges"]:
            required = ["source", "target", "edge_type"]
            for key in required:
                assert key in edge, f"Edge missing: {key}"

    def test_dag_has_timeline(self):
        resp = self.client.get(f"/api/pipelines/monitor/dag/{self.pid1}")
        data = resp.json()
        assert "timeline" in data
        assert isinstance(data["timeline"], list)


# ══════════════════════════════════════════════════════════════════
# NodeStatus Enum
# ══════════════════════════════════════════════════════════════════


class TestNodeStatus:
    def test_all_statuses(self):
        assert NodeStatus.IDLE.value == 1
        assert NodeStatus.RUNNING.value == 2
        assert NodeStatus.COMPLETED.value == 3
        assert NodeStatus.FAILED.value == 4
        assert NodeStatus.SKIPPED.value == 5

    def test_status_name_lower(self):
        assert NodeStatus.COMPLETED.name.lower() == "completed"
        assert NodeStatus.FAILED.name.lower() == "failed"


# ══════════════════════════════════════════════════════════════════
# TimelineEntry
# ══════════════════════════════════════════════════════════════════


class TestTimelineEntry:
    def test_create_entry(self):
        entry = TimelineEntry(
            stage_name="fetch",
            status=NodeStatus.COMPLETED,
            timestamp=1700000000.0,
            duration_ms=150.5,
        )
        assert entry.stage_name == "fetch"
        assert entry.status == NodeStatus.COMPLETED
        assert entry.duration_ms == 150.5
        assert entry.error is None

    def test_entry_with_error(self):
        entry = TimelineEntry(
            stage_name="parse",
            status=NodeStatus.FAILED,
            timestamp=1700000001.0,
            error="JSON parse error",
        )
        assert entry.error == "JSON parse error"

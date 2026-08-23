"""Pipeline Monitor — Real-time DAG visualization and execution tracking.

Hooks into PipelineRegistry to provide:
    - DAG topology (nodes + edges) for frontend rendering
    - Stage-level status tracking with timestamps
    - Execution timeline (cumulative waterfall)
    - Pipeline-level metrics (throughput, success rate)

Usage:
    from jarvis.pipeline_monitor import PipelineMonitor

    monitor = PipelineMonitor()
    monitor.attach(pipeline_registry)
    dag_data = monitor.get_dag("pipeline-id-abc")
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, Dict, List, Optional


# ══════════════════════════════════════════════════════════════════
# Core Types
# ══════════════════════════════════════════════════════════════════


class NodeStatus(Enum):
    IDLE = auto()
    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()
    SKIPPED = auto()


@dataclass
class TimelineEntry:
    """A single event on the execution timeline."""
    stage_name: str
    status: NodeStatus
    timestamp: float  # unix epoch seconds
    duration_ms: float = 0.0
    error: Optional[str] = None
    output_summary: Optional[str] = None


@dataclass
class DAGNode:
    """A single stage in the pipeline DAG."""
    stage_id: str
    stage_name: str
    status: NodeStatus = NodeStatus.IDLE
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    duration_ms: float = 0.0
    error: Optional[str] = None
    output_key: Optional[str] = None
    fail_strategy: str = "abort"
    depends_on: List[str] = field(default_factory=list)  # stage_ids this node depends on


@dataclass
class DAGEdge:
    """Directed edge between two stages."""
    source: str  # stage_id
    target: str  # stage_id
    edge_type: str = "dependency"  # dependency | data_flow | fallback


@dataclass
class PipelineDAG:
    """Complete DAG representation for one pipeline."""
    pipeline_id: str
    pipeline_name: str
    status: str  # PipelineStatus value
    nodes: List[DAGNode] = field(default_factory=list)
    edges: List[DAGEdge] = field(default_factory=list)
    timeline: List[TimelineEntry] = field(default_factory=list)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    total_duration_ms: float = 0.0
    success_rate: float = 0.0
    created_at: str = ""


@dataclass
class MonitorSummary:
    """Aggregated summary across all tracked pipelines."""
    total_pipelines: int = 0
    active_pipelines: int = 0
    completed_pipelines: int = 0
    failed_pipelines: int = 0
    avg_duration_ms: float = 0.0
    total_success_rate: float = 0.0
    pipelines: List[PipelineDAG] = field(default_factory=list)
    updated_at: str = ""


# ══════════════════════════════════════════════════════════════════
# Pipeline Monitor Core
# ══════════════════════════════════════════════════════════════════


class PipelineMonitor:
    """Singleton monitor that tracks pipeline executions in real-time."""

    _instance: Optional["PipelineMonitor"] = None

    def __new__(cls) -> "PipelineMonitor":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._registry = None  # PipelineRegistry reference
        self._dags: Dict[str, PipelineDAG] = {}  # pipeline_id → DAG
        self._pipeline_order: List[str] = []  # insertion order
        self._max_history = 50  # trim oldest when exceeded

    # ── Attach / Detach ─────────────────────────────────────────

    def attach(self, registry):
        """Attach to a PipelineRegistry and start tracking."""
        self._registry = registry
        self._wrap_registry(registry)

    def _wrap_registry(self, registry):
        """Monkey-patch registry methods to inject tracking."""
        original_register = registry.register_template

        def tracked_register(template_name: str, factory: callable):
            result = original_register(template_name, factory)

            original_create = registry.create_pipeline

            def tracked_create(t_name: str, **kwargs):
                pipeline = original_create(t_name, **kwargs)
                self._track_new_pipeline(pipeline)
                # Wrap pipeline execute
                original_execute = pipeline.execute

                def tracked_execute(initial_context=None):
                    self._on_pipeline_start(pipeline)
                    try:
                        result = original_execute(initial_context)
                        self._on_pipeline_complete(pipeline, result)
                        return result
                    except Exception as e:
                        self._on_pipeline_error(pipeline, str(e))
                        raise

                pipeline.execute = tracked_execute
                return pipeline

            registry.create_pipeline = tracked_create
            return result

        registry.register_template = tracked_register

    # ── Tracking ────────────────────────────────────────────────

    def _track_new_pipeline(self, pipeline):
        """Create a DAG entry for a new pipeline."""
        pipeline_id = str(uuid.uuid4())[:12]
        dag = PipelineDAG(
            pipeline_id=pipeline_id,
            pipeline_name=pipeline.name,
            status=pipeline.status.value if hasattr(pipeline.status, 'value') else str(pipeline.status),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        pipeline._monitor_id = pipeline_id

        # Build DAG nodes from stages
        for i, stage in enumerate(pipeline.stages):
            stage_id = f"{pipeline_id}-{i}"
            node = DAGNode(
                stage_id=stage_id,
                stage_name=stage.name,
                output_key=stage.output_key,
                fail_strategy=stage.fail_strategy.name if hasattr(stage.fail_strategy, 'name') else str(stage.fail_strategy),
                depends_on=[f"{pipeline_id}-{j}" for j in range(i)] if i > 0 else [],
            )
            dag.nodes.append(node)

            # Build edges (sequential dependency chain)
            if i > 0:
                dag.edges.append(DAGEdge(
                    source=f"{pipeline_id}-{i-1}",
                    target=stage_id,
                    edge_type="dependency",
                ))

        self._dags[pipeline_id] = dag
        self._pipeline_order.append(pipeline_id)
        self._trim_history()

        return pipeline_id

    def _on_pipeline_start(self, pipeline):
        """Mark pipeline as started."""
        pipeline_id = getattr(pipeline, '_monitor_id', None)
        if not pipeline_id or pipeline_id not in self._dags:
            return

        dag = self._dags[pipeline_id]
        dag.started_at = time.time()
        dag.status = "running"

        if dag.nodes:
            dag.nodes[0].status = NodeStatus.RUNNING
            dag.nodes[0].started_at = time.time()

        dag.timeline.append(TimelineEntry(
            stage_name="__pipeline_start__",
            status=NodeStatus.RUNNING,
            timestamp=time.time(),
        ))

    def _on_pipeline_complete(self, pipeline, result):
        """Record pipeline completion."""
        pipeline_id = getattr(pipeline, '_monitor_id', None)
        if not pipeline_id or pipeline_id not in self._dags:
            return

        dag = self._dags[pipeline_id]
        dag.finished_at = time.time()
        if dag.started_at:
            dag.total_duration_ms = round((dag.finished_at - dag.started_at) * 1000, 1)

        # Map stage results from pipeline result
        if hasattr(result, 'stages'):
            for i, sr in enumerate(result.stages):
                if i < len(dag.nodes):
                    node = dag.nodes[i]
                    node.finished_at = time.time()
                    if node.started_at:
                        node.duration_ms = round((node.finished_at - node.started_at) * 1000, 1)

                    status_val = sr.status.value if hasattr(sr.status, 'value') else str(sr.status)
                    if status_val.lower() in ('completed', 'success'):
                        node.status = NodeStatus.COMPLETED
                    elif status_val.lower() == 'skipped':
                        node.status = NodeStatus.SKIPPED
                    else:
                        node.status = NodeStatus.FAILED
                        node.error = sr.error if hasattr(sr, 'error') else None

                    dag.timeline.append(TimelineEntry(
                        stage_name=node.stage_name,
                        status=node.status,
                        timestamp=node.finished_at,
                        duration_ms=node.duration_ms,
                        error=node.error,
                    ))

        # Calculate success rate
        total_nodes = len(dag.nodes)
        if total_nodes > 0:
            success_nodes = sum(1 for n in dag.nodes if n.status in (NodeStatus.COMPLETED, NodeStatus.SKIPPED))
            dag.success_rate = round(success_nodes / total_nodes, 2)

        dag.status = "completed" if dag.success_rate >= 0.5 else "failed"

        dag.timeline.append(TimelineEntry(
            stage_name="__pipeline_end__",
            status=NodeStatus.COMPLETED if dag.status == "completed" else NodeStatus.FAILED,
            timestamp=time.time(),
            duration_ms=dag.total_duration_ms,
        ))

    def _on_pipeline_error(self, pipeline, error: str):
        """Record pipeline error."""
        pipeline_id = getattr(pipeline, '_monitor_id', None)
        if not pipeline_id or pipeline_id not in self._dags:
            return

        dag = self._dags[pipeline_id]
        dag.status = "failed"
        dag.finished_at = time.time()

        dag.timeline.append(TimelineEntry(
            stage_name="__pipeline_error__",
            status=NodeStatus.FAILED,
            timestamp=time.time(),
            error=error,
        ))

    # ── Manual pipeline recording ───────────────────────────────

    def record_pipeline(self, pipeline_name: str, stages: List[dict],
                        status: str = "completed") -> str:
        """Record a pipeline without registry wrapping (for manual tasks)."""
        pipeline_id = str(uuid.uuid4())[:12]
        dag = PipelineDAG(
            pipeline_id=pipeline_id,
            pipeline_name=pipeline_name,
            status=status,
            started_at=time.time(),
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        for i, stage in enumerate(stages):
            stage_id = f"{pipeline_id}-{i}"
            node_status = NodeStatus.COMPLETED if stage.get("success", True) else NodeStatus.FAILED
            node = DAGNode(
                stage_id=stage_id,
                stage_name=stage.get("name", f"Stage {i}"),
                status=node_status,
                output_key=stage.get("output_key"),
                fail_strategy=stage.get("fail_strategy", "abort"),
                depends_on=[f"{pipeline_id}-{j}" for j in range(i)] if i > 0 else [],
            )
            dag.nodes.append(node)

            if i > 0:
                dag.edges.append(DAGEdge(
                    source=f"{pipeline_id}-{i-1}",
                    target=stage_id,
                ))

        dag.finished_at = time.time()
        dag.total_duration_ms = 0
        success_nodes = sum(1 for n in dag.nodes if n.status == NodeStatus.COMPLETED)
        dag.success_rate = round(success_nodes / len(dag.nodes), 2) if dag.nodes else 0

        self._dags[pipeline_id] = dag
        self._pipeline_order.append(pipeline_id)
        self._trim_history()

        return pipeline_id

    # ── Queries ─────────────────────────────────────────────────

    def get_dag(self, pipeline_id: str) -> Optional[dict]:
        """Get DAG data for a single pipeline."""
        dag = self._dags.get(pipeline_id)
        if not dag:
            return None
        return self._dag_to_dict(dag)

    def get_summary(self) -> dict:
        """Get aggregated monitor summary."""
        pipelines = [self._dag_to_dict(d) for d in self._dags.values()]
        active = sum(1 for d in self._dags.values() if d.status == "running")
        completed = sum(1 for d in self._dags.values() if d.status == "completed")
        failed = sum(1 for d in self._dags.values() if d.status == "failed")

        durations = [d.total_duration_ms for d in self._dags.values() if d.total_duration_ms > 0]
        avg_duration = round(sum(durations) / len(durations), 1) if durations else 0

        success_rates = [d.success_rate for d in self._dags.values()]
        avg_success = round(sum(success_rates) / len(success_rates), 2) if success_rates else 0

        summary = MonitorSummary(
            total_pipelines=len(self._dags),
            active_pipelines=active,
            completed_pipelines=completed,
            failed_pipelines=failed,
            avg_duration_ms=avg_duration,
            total_success_rate=avg_success,
            pipelines=pipelines,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        return self._summary_to_dict(summary)

    def get_timeline(self, pipeline_id: str) -> Optional[List[dict]]:
        """Get execution timeline for a pipeline."""
        dag = self._dags.get(pipeline_id)
        if not dag:
            return None
        return [
            {
                "stage_name": e.stage_name,
                "status": e.status.name.lower(),
                "timestamp": e.timestamp,
                "duration_ms": e.duration_ms,
                "error": e.error,
                "output_summary": e.output_summary,
            }
            for e in dag.timeline
        ]

    def get_live(self) -> dict:
        """Get live status of all pipelines (lightweight, for polling)."""
        active_pipelines = []
        for dag in self._dags.values():
            if dag.status == "running":
                running_nodes = [n for n in dag.nodes if n.status == NodeStatus.RUNNING]
                active_pipelines.append({
                    "pipeline_id": dag.pipeline_id,
                    "pipeline_name": dag.pipeline_name,
                    "status": dag.status,
                    "current_stage": running_nodes[0].stage_name if running_nodes else None,
                    "progress": {
                        "total": len(dag.nodes),
                        "completed": sum(1 for n in dag.nodes if n.status in (NodeStatus.COMPLETED, NodeStatus.SKIPPED)),
                        "running": len(running_nodes),
                        "failed": sum(1 for n in dag.nodes if n.status == NodeStatus.FAILED),
                    },
                    "elapsed_ms": round((time.time() - dag.started_at) * 1000, 1) if dag.started_at else 0,
                })

        return {
            "active_pipelines": active_pipelines,
            "total_tracked": len(self._dags),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    # ── Helpers ─────────────────────────────────────────────────

    def _dag_to_dict(self, dag: PipelineDAG) -> dict:
        return {
            "pipeline_id": dag.pipeline_id,
            "pipeline_name": dag.pipeline_name,
            "status": dag.status,
            "nodes": [
                {
                    "stage_id": n.stage_id,
                    "stage_name": n.stage_name,
                    "status": n.status.name.lower(),
                    "started_at": n.started_at,
                    "finished_at": n.finished_at,
                    "duration_ms": n.duration_ms,
                    "error": n.error,
                    "output_key": n.output_key,
                    "fail_strategy": n.fail_strategy,
                    "depends_on": n.depends_on,
                }
                for n in dag.nodes
            ],
            "edges": [
                {
                    "source": e.source,
                    "target": e.target,
                    "edge_type": e.edge_type,
                }
                for e in dag.edges
            ],
            "timeline": [
                {
                    "stage_name": t.stage_name,
                    "status": t.status.name.lower(),
                    "timestamp": t.timestamp,
                    "duration_ms": t.duration_ms,
                    "error": t.error,
                }
                for t in dag.timeline
            ],
            "started_at": dag.started_at,
            "finished_at": dag.finished_at,
            "total_duration_ms": dag.total_duration_ms,
            "success_rate": dag.success_rate,
            "created_at": dag.created_at,
        }

    def _summary_to_dict(self, s: MonitorSummary) -> dict:
        return {
            "total_pipelines": s.total_pipelines,
            "active_pipelines": s.active_pipelines,
            "completed_pipelines": s.completed_pipelines,
            "failed_pipelines": s.failed_pipelines,
            "avg_duration_ms": s.avg_duration_ms,
            "total_success_rate": s.total_success_rate,
            "pipelines": s.pipelines,
            "updated_at": s.updated_at,
        }

    def _trim_history(self):
        """Trim oldest entries when exceeding max history."""
        while len(self._dags) > self._max_history:
            oldest_id = self._pipeline_order.pop(0)
            self._dags.pop(oldest_id, None)

    # ── Hooks into emperor execute_task ─────────────────────────

    def wrap_emperor(self, emperor):
        """Wrap emperor.execute_task to auto-record pipeline DAGs."""
        original_execute = emperor.execute_task

        def tracked_execute(task: str, task_id: Optional[str] = None,
                           domain: str = "general", **kwargs) -> Any:
            pipeline_id = self.record_pipeline(
                pipeline_name=f"Task: {task[:50]}",
                stages=[
                    {"name": "dispatch", "output_key": "dispatch_result"},
                    {"name": "execute", "output_key": "execute_result"},
                    {"name": "record", "output_key": "record_result"},
                ],
                status="running",
            )

            try:
                result = original_execute(task, task_id=task_id, domain=domain, **kwargs)
                self._dags[pipeline_id].status = "completed"
                for node in self._dags[pipeline_id].nodes:
                    node.status = NodeStatus.COMPLETED
                self._dags[pipeline_id].success_rate = 1.0
                return result
            except Exception as e:
                self._dags[pipeline_id].status = "failed"
                self._dags[pipeline_id].nodes[-1].status = NodeStatus.FAILED
                self._dags[pipeline_id].nodes[-1].error = str(e)
                raise

        emperor.execute_task = tracked_execute


# ══════════════════════════════════════════════════════════════════
# Global singleton
# ══════════════════════════════════════════════════════════════════

pipeline_monitor = PipelineMonitor()

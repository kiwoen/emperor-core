"""DAG-based workflow engine with YAML/JSON loading, parallel execution,
state tracking, conditional routing, and error handling.

Integrates with :class:`jarvis.emperor.Emperor` via
``Emperor.execute_workflow()``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from copy import deepcopy
from pathlib import Path
from typing import Any, Optional, Union

import yaml

from .dag import DAG
from .nodes import (
    BaseNode,
    ConditionNode,
    ErrorStrategy,
    MergeNode,
    NodeStatus,
    ParallelNode,
    TaskNode,
    get_node_class,
)

logger = logging.getLogger(__name__)


# ── Helpers ─────────────────────────────────────────────────────────

def _sanitise_for_serial(obj: Any) -> Any:
    """Convert non-serialisable objects to repr strings."""
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return repr(obj)


# ── Engine ─────────────────────────────────────────────────────────

class WorkflowEngine:
    """Execute a DAG-based workflow.

    Parameters:
        name: Human-readable workflow identifier.
        dag: The DAG that encodes the workflow topology.
        context: Optional initial shared context dict.
        default_error_strategy: Fallback error handling for nodes that
            do not specify one.
        max_parallel: Maximum concurrent tasks when running parallel
            branches (0 = unlimited).
    """

    def __init__(
        self,
        name: str = "workflow",
        dag: Optional[DAG[BaseNode]] = None,
        context: Optional[dict[str, Any]] = None,
        default_error_strategy: ErrorStrategy = ErrorStrategy.ABORT,
        max_parallel: int = 0,
    ) -> None:
        self.name = name
        self.dag = dag or DAG[BaseNode](name=name)
        self._base_context = deepcopy(context) if context else {}
        self.default_error_strategy = default_error_strategy
        self.max_parallel = max_parallel

        # Run-time state
        self._statuses: dict[str, NodeStatus] = {}
        self._results: dict[str, Any] = {}
        self._errors: dict[str, str] = {}
        self._execution_order: list[str] = []
        self._semaphore: Optional[asyncio.Semaphore] = None

    # ── Public API ──────────────────────────────────────────────────

    def build_dag(self) -> DAG[BaseNode]:
        """Return the internal DAG for inspection / modification."""
        return self.dag

    def add_node(self, node: BaseNode) -> None:
        """Add a node to the workflow DAG."""
        self.dag.add_node(node.node_id, node)

    def add_edge(self, from_id: str, to_id: str) -> None:
        """Connect two nodes in the workflow DAG."""
        self.dag.add_edge(from_id, to_id)

    def run(self, context: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Synchronous wrapper around :meth:`run_async`."""
        return asyncio.run(self.run_async(context))

    async def run_async(
        self, context: Optional[dict[str, Any]] = None
    ) -> dict[str, Any]:
        """Execute the workflow.

        Returns:
            A dict with keys ``results``, ``statuses``, ``errors``,
            ``execution_order``, and an overall ``success`` flag.
        """
        self._reset()
        workflow_ctx = deepcopy(self._base_context)
        if context:
            workflow_ctx.update(context)

        try:
            await self._execute(workflow_ctx)
            overall_success = all(
                s in (NodeStatus.SUCCESS, NodeStatus.SKIPPED)
                for s in self._statuses.values()
            )
            return {
                "results": dict(self._results),
                "statuses": {k: v.value for k, v in self._statuses.items()},
                "errors": dict(self._errors),
                "execution_order": list(self._execution_order),
                "success": overall_success,
            }
        except Exception as exc:
            logger.exception("[WorkflowEngine] fatal error: %s", exc)
            return {
                "results": dict(self._results),
                "statuses": {k: v.value for k, v in self._statuses.items()},
                "errors": {**self._errors, "__fatal__": str(exc)},
                "execution_order": list(self._execution_order),
                "success": False,
            }

    # ── Serialisation ───────────────────────────────────────────────

    @classmethod
    def from_yaml(cls, path: Union[str, Path], **kwargs: Any) -> WorkflowEngine:
        """Load a workflow definition from a YAML file."""
        path = Path(path)
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return cls.from_dict(data, **kwargs)

    @classmethod
    def from_json(cls, path: Union[str, Path], **kwargs: Any) -> WorkflowEngine:
        """Load a workflow definition from a JSON file."""
        path = Path(path)
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return cls.from_dict(data, **kwargs)

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], **kwargs: Any
    ) -> WorkflowEngine:
        """Build a WorkflowEngine from a dictionary definition.

        Expected structure::

            {
              "name": "my_workflow",
              "nodes": [
                {"type": "TaskNode", "node_id": "A", ...},
                {"type": "ConditionNode", "node_id": "B", ...}
              ],
              "edges": [["A", "B"], ...],
              "context": {...}
            }
        """
        name = data.get("name", kwargs.pop("name", "workflow"))
        default_es = kwargs.pop("default_error_strategy", ErrorStrategy.ABORT)
        max_parallel = kwargs.pop("max_parallel", 0)
        base_ctx = deepcopy(data.get("context", {}))
        engine = cls(
            name=name,
            default_error_strategy=default_es,
            max_parallel=max_parallel,
            context=base_ctx,
            **kwargs,
        )

        # Build nodes
        for nd in data.get("nodes", []):
            type_name = nd.pop("type")
            node_cls = get_node_class(type_name)
            node = node_cls(**nd)
            engine.add_node(node)

        # Build edges
        for edge in data.get("edges", []):
            engine.add_edge(edge[0], edge[1])

        return engine

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "nodes": [self.dag.get_node(nid).to_dict() for nid in self.dag.nodes],
            "edges": self.dag.edges,
            "context": _sanitise_for_serial(self._base_context),
        }

    def to_yaml(self, path: Union[str, Path]) -> None:
        path = Path(path)
        with open(path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(self.to_dict(), fh, default_flow_style=False)

    def to_json(self, path: Union[str, Path]) -> None:
        path = Path(path)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2, default=str)

    # ── Internals ───────────────────────────────────────────────────

    def _reset(self) -> None:
        self._statuses = {}
        self._results = {}
        self._errors = {}
        self._execution_order = []
        self._semaphore = (
            asyncio.Semaphore(self.max_parallel) if self.max_parallel > 0 else None
        )

    async def _execute(self, context: dict[str, Any]) -> None:
        topo = self.dag.topological_sort()

        # Phase 1: plain topological execution with parallel-branch awareness
        for node_id in topo:
            node = self.dag.get_node(node_id)

            # Collect inputs from predecessors
            inputs: dict[str, Any] = {}
            for pred in self.dag.predecessors(node_id):
                if pred in self._results:
                    inputs[pred] = self._results[pred]

            # If the predecessor was a ConditionNode, check routing
            should_skip = False
            for pred in self.dag.predecessors(node_id):
                if isinstance(self.dag.get_node(pred), ConditionNode):
                    route = self._results.get(pred)
                    if route is not None and route != node_id:
                        should_skip = True
                        break

            if should_skip:
                self._statuses[node_id] = NodeStatus.SKIPPED
                continue

            if self._semaphore is not None:
                async with self._semaphore:
                    await self._execute_node(node, context, inputs)
            else:
                await self._execute_node(node, context, inputs)

    async def _execute_node(
        self,
        node: BaseNode,
        context: dict[str, Any],
        inputs: dict[str, Any],
    ) -> None:
        node_id = node.node_id

        # Already handled (could be a merge target)
        if node_id in self._statuses:
            return

        self._statuses[node_id] = NodeStatus.RUNNING
        self._execution_order.append(node_id)

        exec_ctx = {**context, "_inputs": inputs, "_node_id": node_id}

        strategy = (
            node.error_strategy
            if node.error_strategy != ErrorStrategy.ABORT
            else self.default_error_strategy
        )
        max_retries = node.max_retries if strategy == ErrorStrategy.RETRY else 0

        for attempt in range(max_retries + 1):
            try:
                if node.timeout_seconds > 0:
                    result = await asyncio.wait_for(
                        node.execute(exec_ctx), timeout=node.timeout_seconds
                    )
                else:
                    result = await node.execute(exec_ctx)

                self._results[node_id] = result
                context[node_id] = result
                self._statuses[node_id] = NodeStatus.SUCCESS
                return

            except asyncio.TimeoutError:
                err = f"Timeout after {node.timeout_seconds}s"
                logger.warning(
                    "[WorkflowEngine] node '%s' timeout (attempt %d/%d)",
                    node_id, attempt + 1, max_retries + 1,
                )
                if attempt < max_retries:
                    continue
                self._handle_failure(node_id, err, strategy)

            except Exception as exc:
                err = str(exc)
                logger.warning(
                    "[WorkflowEngine] node '%s' failed: %s (attempt %d/%d)",
                    node_id, err, attempt + 1, max_retries + 1,
                )
                if attempt < max_retries:
                    await asyncio.sleep(0.1 * (2 ** attempt))  # exponential backoff
                    continue
                self._handle_failure(node_id, err, strategy)

    def _handle_failure(
        self, node_id: str, error: str, strategy: ErrorStrategy
    ) -> None:
        self._errors[node_id] = error
        if strategy == ErrorStrategy.SKIP:
            self._statuses[node_id] = NodeStatus.SKIPPED
        elif strategy == ErrorStrategy.RETRY:
            # All retries exhausted → treat as SKIP
            self._statuses[node_id] = NodeStatus.FAILED
        else:  # ABORT
            self._statuses[node_id] = NodeStatus.FAILED
            raise RuntimeError(
                f"Workflow aborted: node '{node_id}' failed: {error}"
            )

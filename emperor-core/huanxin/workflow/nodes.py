"""Built-in workflow node types.

Each node encapsulates a unit of work with an ``execute(context)`` method.
Nodes are stateless specs; the engine owns run-time status and results.
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


# ── Node status ────────────────────────────────────────────────────

class NodeStatus(str, enum.Enum):
    """Execution status of a node during a workflow run."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED  = "failed"
    SKIPPED = "skipped"


# ── Error strategy ─────────────────────────────────────────────────

class ErrorStrategy(str, enum.Enum):
    """How the engine should respond when a node fails."""
    SKIP = "skip"       # mark node SKIPPED, continue downstream
    RETRY = "retry"     # retry up to N times before falling back to ABORT
    ABORT = "abort"     # stop the entire workflow immediately


# ── Base node ──────────────────────────────────────────────────────

@dataclass
class BaseNode(ABC):
    """Abstract base for all workflow nodes.

    Attributes:
        node_id: Unique identifier within the workflow.
        label: Human-readable label for logging / debugging.
        error_strategy: How to handle execution failure.
        max_retries: Maximum retry attempts (only used when
            *error_strategy* is ``RETRY``).
        timeout_seconds: Soft timeout; 0 means no limit.
    """

    node_id: str
    label: str = ""
    error_strategy: ErrorStrategy = ErrorStrategy.ABORT
    max_retries: int = 3
    timeout_seconds: float = 0.0

    def __post_init__(self) -> None:
        if not self.label:
            self.label = self.node_id

    @abstractmethod
    async def execute(self, context: dict[str, Any]) -> Any:
        """Execute the node's logic.

        Args:
            context: Shared workflow context (read/write).  The engine
                injects ``_inputs`` (dict mapping predecessor_node_id →
                predecessor result) and ``_node_id`` for introspection.

        Returns:
            The node's result, which is stored under ``context[node_id]``.
        """
        ...

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.__class__.__name__,
            "node_id": self.node_id,
            "label": self.label,
            "error_strategy": self.error_strategy.value,
            "max_retries": self.max_retries,
            "timeout_seconds": self.timeout_seconds,
        }


# ── Built-in nodes ─────────────────────────────────────────────────

@dataclass
class TaskNode(BaseNode):
    """Execute a callable task (the basic unit of work).

    Attributes:
        fn: An ``async def(context) → result`` callable.
    """

    fn: Optional[Callable[..., Any]] = None

    async def execute(self, context: dict[str, Any]) -> Any:
        if self.fn is None:
            raise RuntimeError(f"TaskNode '{self.node_id}' has no callable")
        return await self.fn(context)


@dataclass
class ConditionNode(BaseNode):
    """Conditional branching — evaluates a predicate to choose a path.

    The return value should be the ``node_id`` of the next node to route to.
    If the value is ``None``, the engine follows the default DAG edge.

    Attributes:
        predicate: ``async def(context) → str | list[str] | None``.
    """

    predicate: Optional[Callable[..., Any]] = None

    async def execute(self, context: dict[str, Any]) -> Any:
        if self.predicate is None:
            raise RuntimeError(f"ConditionNode '{self.node_id}' has no predicate")
        return await self.predicate(context)


@dataclass
class ParallelNode(BaseNode):
    """Execute multiple child nodes concurrently.

    Child results are stored under ``context[node_id]`` individually.
    The node's own result is a dict of ``{child_id: child_result}``.

    Attributes:
        children: List of :class:`BaseNode` to run in parallel.
    """

    children: list[BaseNode] = field(default_factory=list)

    async def execute(self, context: dict[str, Any]) -> Any:
        import asyncio

        async def _run(child: BaseNode) -> tuple[str, Any, Optional[str]]:
            try:
                child_ctx = {**context, "_inputs": {}, "_node_id": child.node_id}
                result = await child.execute(child_ctx)
                context[child.node_id] = result
                return (child.node_id, result, None)
            except Exception as exc:
                context[f"{child.node_id}__error"] = str(exc)
                return (child.node_id, None, str(exc))

        coros = [_run(c) for c in self.children]
        outcomes = await asyncio.gather(*coros, return_exceptions=True)

        results: dict[str, Any] = {}
        errors: dict[str, str] = {}
        for item in outcomes:
            if isinstance(item, Exception):
                continue
            cid, val, err = item
            if err is not None:
                errors[cid] = err
            results[cid] = val

        if errors:
            context[f"{self.node_id}__errors"] = errors

        return results


@dataclass
class LoopNode(BaseNode):
    """Iterate over a collection, executing a sub-workflow per item.

    Attributes:
        items: An ``async def(context) → list`` callable that returns the
            collection to iterate over.
        body: The node (or sub-DAG) to execute per item.
        max_iterations: Safety cap on iterations.
    """

    items: Optional[Callable[..., Any]] = None
    body: Optional[BaseNode] = None
    max_iterations: int = 1000

    async def execute(self, context: dict[str, Any]) -> Any:
        if self.items is None:
            raise RuntimeError(f"LoopNode '{self.node_id}' has no items callable")
        if self.body is None:
            raise RuntimeError(f"LoopNode '{self.node_id}' has no body node")

        collection = await self.items(context)
        if len(collection) > self.max_iterations:
            collection = collection[: self.max_iterations]

        results: list[Any] = []
        for i, item in enumerate(collection):
            ctx = {
                **context,
                "_inputs": {"item": item, "index": i},
                "_node_id": self.body.node_id,
            }
            result = await self.body.execute(ctx)
            results.append(result)
            context[f"{self.node_id}__iter_{i}"] = result

        return results


@dataclass
class MergeNode(BaseNode):
    """Wait for all upstream nodes, merge their outputs.

    The merge function receives ``{predecessor_id: result}``.

    Attributes:
        merge_fn: ``async def(inputs: dict) → merged_result``.  If not
            set the raw dict is returned as-is.
    """

    merge_fn: Optional[Callable[..., Any]] = None

    async def execute(self, context: dict[str, Any]) -> Any:
        inputs = context.get("_inputs", {})
        if self.merge_fn is not None:
            return await self.merge_fn(inputs)
        return inputs


# ── Registry helper ────────────────────────────────────────────────

_BUILTIN_NODES: dict[str, type[BaseNode]] = {
    "TaskNode": TaskNode,
    "ConditionNode": ConditionNode,
    "ParallelNode": ParallelNode,
    "LoopNode": LoopNode,
    "MergeNode": MergeNode,
}


def get_node_class(type_name: str) -> type[BaseNode]:
    """Look up a built-in node class by name."""
    cls = _BUILTIN_NODES.get(type_name)
    if cls is None:
        raise KeyError(f"Unknown node type '{type_name}'. Available: {list(_BUILTIN_NODES)}")
    return cls

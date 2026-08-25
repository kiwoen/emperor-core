"""Workflow engine — DAG-based orchestration for Huanxin.

Core components
---------------
- :class:`DAG` — directed acyclic graph
- :class:`WorkflowEngine` — execution engine
- :class:`TaskNode` / :class:`ConditionNode` / :class:`ParallelNode` /
  :class:`LoopNode` / :class:`MergeNode` — built-in node types
"""

from huanxin.workflow.dag import DAG
from huanxin.workflow.engine import WorkflowEngine
from huanxin.workflow.nodes import (
    BaseNode,
    ConditionNode,
    ErrorStrategy,
    LoopNode,
    MergeNode,
    NodeStatus,
    ParallelNode,
    TaskNode,
)

__all__ = [
    "DAG",
    "WorkflowEngine",
    "BaseNode",
    "TaskNode",
    "ConditionNode",
    "ParallelNode",
    "LoopNode",
    "MergeNode",
    "NodeStatus",
    "ErrorStrategy",
]

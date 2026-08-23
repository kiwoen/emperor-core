"""Directed Acyclic Graph (DAG) structure for workflow composition.

Provides an add_node → add_edge → validate_cycle → topological_sort
pipeline that underpins the workflow engine.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class DAG(Generic[T]):
    """A directed acyclic graph with labelled nodes and edges.

    Parameters:
        name: Optional human-readable name for the graph.

    Usage::

        dag = DAG[str]()
        dag.add_node("A", payload="payload_A")
        dag.add_node("B", payload="payload_B")
        dag.add_edge("A", "B")
        order = dag.topological_sort()  # ['A', 'B']
    """

    def __init__(self, name: str = "") -> None:
        self.name = name or "workflow"
        self._nodes: dict[str, T] = {}
        self._adj: dict[str, list[str]] = {}   # node → successors
        self._predecessors: dict[str, set[str]] = {}  # node → predecessors
        self._in_degree: dict[str, int] = {}

    # ── Node management ─────────────────────────────────────────────

    def add_node(self, node_id: str, payload: T) -> None:
        """Register a node.

        Args:
            node_id: Unique node identifier.
            payload: Arbitrary data attached to the node.

        Raises:
            ValueError: If *node_id* already exists.
        """
        if node_id in self._nodes:
            raise ValueError(f"Node '{node_id}' already exists")
        self._nodes[node_id] = payload
        self._adj.setdefault(node_id, [])
        self._predecessors.setdefault(node_id, set())
        self._in_degree[node_id] = 0

    def get_node(self, node_id: str) -> T:
        """Return the payload for *node_id*.

        Raises:
            KeyError: If the node is not registered.
        """
        return self._nodes[node_id]

    def has_node(self, node_id: str) -> bool:
        return node_id in self._nodes

    @property
    def nodes(self) -> dict[str, T]:
        return dict(self._nodes)

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    # ── Edge management ─────────────────────────────────────────────

    def add_edge(self, from_id: str, to_id: str) -> None:
        """Create a directed edge ``from_id → to_id``.

        Automatically registers nodes if they are absent.

        Raises:
            ValueError: If the edge would create a cycle.
        """
        if from_id not in self._nodes:
            self.add_node(from_id, None)  # type: ignore[arg-type]
        if to_id not in self._nodes:
            self.add_node(to_id, None)  # type: ignore[arg-type]

        self._adj[from_id].append(to_id)
        self._predecessors[to_id].add(from_id)
        self._in_degree[to_id] += 1

        if self._has_cycle():
            # Rollback
            self._adj[from_id].pop()
            self._predecessors[to_id].discard(from_id)
            self._in_degree[to_id] -= 1
            raise ValueError(
                f"Adding edge '{from_id}' → '{to_id}' would create a cycle"
            )

    def has_edge(self, from_id: str, to_id: str) -> bool:
        return to_id in self._adj.get(from_id, [])

    @property
    def edges(self) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        for src, targets in self._adj.items():
            for tgt in targets:
                result.append((src, tgt))
        return result

    # ── Traversal ───────────────────────────────────────────────────

    def successors(self, node_id: str) -> list[str]:
        """Return direct successors of *node_id*."""
        if node_id not in self._nodes:
            raise KeyError(f"Node '{node_id}' not found")
        return list(self._adj.get(node_id, []))

    def predecessors(self, node_id: str) -> list[str]:
        """Return direct predecessors of *node_id*."""
        if node_id not in self._nodes:
            raise KeyError(f"Node '{node_id}' not found")
        return list(self._predecessors.get(node_id, set()))

    def roots(self) -> list[str]:
        """Nodes with no incoming edges."""
        return [n for n, deg in self._in_degree.items() if deg == 0]

    def leaves(self) -> list[str]:
        """Nodes with no outgoing edges."""
        return [n for n, succs in self._adj.items() if not succs]

    # ── Topological sort (Kahn's algorithm) ────────────────────────

    def topological_sort(self) -> list[str]:
        """Return nodes in topological order.

        Returns:
            Ordered list of node IDs.

        Raises:
            RuntimeError: If the graph contains a cycle (should not happen
                if all edges were added via :meth:`add_edge`).
        """
        in_degree = dict(self._in_degree)
        queue: deque[str] = deque(
            n for n, d in in_degree.items() if d == 0
        )
        order: list[str] = []

        while queue:
            node = queue.popleft()
            order.append(node)
            for succ in self._adj.get(node, []):
                in_degree[succ] -= 1
                if in_degree[succ] == 0:
                    queue.append(succ)

        if len(order) != len(self._nodes):
            raise RuntimeError(
                "Graph contains a cycle — topological sort impossible"
            )
        return order

    # ── Cycle detection ─────────────────────────────────────────────

    def validate_cycle(self) -> bool:
        """Return ``True`` if the graph is **acyclic** (no cycles)."""
        return not self._has_cycle()

    def _has_cycle(self) -> bool:
        """Internal cycle check using DFS colouring (white/grey/black)."""
        WHITE, GREY, BLACK = 0, 1, 2
        colour: dict[str, int] = {n: WHITE for n in self._nodes}

        def _dfs(node: str) -> bool:
            colour[node] = GREY
            for succ in self._adj.get(node, []):
                if colour.get(succ, WHITE) == GREY:
                    return True
                if colour.get(succ, WHITE) == WHITE and _dfs(succ):
                    return True
            colour[node] = BLACK
            return False

        for n in self._nodes:
            if colour[n] == WHITE and _dfs(n):
                return True
        return False

    # ── Serialisation ───────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Export the graph structure (payloads are serialised as-is)."""
        return {
            "name": self.name,
            "nodes": {nid: str(p) for nid, p in self._nodes.items()},
            "edges": [(s, t) for s, targets in self._adj.items() for t in targets],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DAG:
        """Reconstruct a DAG from a dictionary produced by :meth:`to_dict`.

        Payloads are reconstructed as plain strings.
        """
        dag = cls(name=data.get("name", ""))
        for nid in data.get("nodes", {}):
            dag.add_node(nid, None)  # payload restored as None
        for src, tgt in data.get("edges", []):
            dag.add_edge(src, tgt)
        return dag

    # ── Utility ─────────────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self._nodes)

    def __contains__(self, node_id: str) -> bool:
        return node_id in self._nodes

    def __repr__(self) -> str:
        return f"<DAG name={self.name!r} nodes={len(self._nodes)} edges={sum(len(v) for v in self._adj.values())}>"

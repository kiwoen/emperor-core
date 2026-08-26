"""
Hierarchical Memory System — P1 module for 幻炘AI.

Implements cognitive-inspired multi-tier memory with:
1. Consolidation Cycle — Working → Episodic → Semantic promotion with importance gating
2. Ebbinghaus Forgetting Curve — spaced-repetition decay model
3. Memory Graph — bidirectional relationships between memory nodes
4. Recursive Summarization — cluster compression for long-term storage
5. Cross-session Persistence — JSONL-based durable storage
6. L4 GraphRAG — knowledge-graph-based retrieval (GraphRAG)

Integration: wraps huanxin.memory.engine.MemoryEngine + huanxin.graph_rag.GraphRAG.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Optional

from huanxin.tracer import tracer as _tracer
from huanxin.graph_rag import GraphRAG, SearchResult as GraphRAGSearchResult

logger = logging.getLogger("huanxin.hierarchical_memory")


# ═══════════════════════════════════════════════════════════════════════
# Enums & Constants
# ═══════════════════════════════════════════════════════════════════════

class MemoryTier(Enum):
    WORKING = auto()    # Immediate context, high volatility
    EPISODIC = auto()   # Conversation turns, moderate retention
    SEMANTIC = auto()   # Consolidated facts, long retention
    PROCEDURAL = auto() # Skill templates, permanent
    GRAPH_RAG = auto()  # Knowledge graph entities & relations (GraphRAG L4)


class ConsolidationStatus(Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# Ebbinghaus curve parameters (hours → retention ratio)
EBBINGHAUS_CURVE = {
    0:     1.00,   # immediate
    0.33:  0.58,   # 20 min
    1:     0.44,   # 1 hour
    9:     0.36,   # 9 hours
    24:    0.33,   # 1 day
    48:    0.28,   # 2 days
    144:   0.25,   # 6 days
    744:   0.21,   # 31 days
}

# Spaced repetition intervals (hours) — after each review, retention boosts
SPACED_REPETITION_INTERVALS = [1, 6, 24, 72, 168, 720]

# Consolidation defaults
DEFAULT_CONSOLIDATION_INTERVAL = 3600       # 1 hour
DEFAULT_IMPORTANCE_THRESHOLD = 0.55         # min importance for episodic→semantic
DEFAULT_MAX_SEMANTIC_FACTS = 5000           # cap before forced summarization
DEFAULT_SUMMARIZATION_CLUSTER_SIZE = 50     # group size for recursive summary


# ═══════════════════════════════════════════════════════════════════════
# Data Structures
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class MemoryNode:
    """A node in the hierarchical memory graph."""

    node_id: str
    content: str
    tier: MemoryTier
    importance: float = 0.5
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    access_count: int = 0
    review_count: int = 0
    next_review_at: float = 0.0       # epoch timestamp for spaced repetition
    retention: float = 1.0            # current Ebbinghaus retention estimate
    metadata: dict[str, Any] = field(default_factory=dict)
    children: list[str] = field(default_factory=list)      # child node IDs
    parents: list[str] = field(default_factory=list)       # parent node IDs
    related: list[str] = field(default_factory=list)       # lateral edges
    summary: str = ""                 # compressed version for higher tiers

    def decay_retention(self, now: float | None = None) -> float:
        """Apply Ebbinghaus forgetting curve to estimate current retention."""
        now = now or time.time()
        elapsed_hours = (now - self.last_accessed) / 3600
        return _ebbinghaus_retention(elapsed_hours)

    def schedule_review(self) -> None:
        """Schedule next spaced-repetition review."""
        idx = min(self.review_count, len(SPACED_REPETITION_INTERVALS) - 1)
        interval_hours = SPACED_REPETITION_INTERVALS[idx]
        self.next_review_at = time.time() + interval_hours * 3600

    def boost_retention(self) -> None:
        """Called after a successful review — reset retention curve."""
        self.review_count += 1
        self.retention = 1.0
        self.last_accessed = time.time()
        self.schedule_review()

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "content": self.content[:2000],
            "tier": self.tier.name,
            "importance": self.importance,
            "created_at": self.created_at,
            "last_accessed": self.last_accessed,
            "access_count": self.access_count,
            "review_count": self.review_count,
            "retention": self.retention,
            "metadata": self.metadata,
            "children": self.children,
            "parents": self.parents,
            "related": self.related,
            "summary": self.summary,
        }


@dataclass
class ChunkResult:
    """Result from L4 GraphRAG retrieval, compatible with L0-L3 retrieval output."""

    node_id: str
    content: str
    tier: str = "GRAPH_RAG"
    layer: str = "L4"
    importance: float = 0.5
    retention: float = 1.0
    source: str = "graph_rag"
    timestamp: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "content": self.content,
            "tier": self.tier,
            "layer": self.layer,
            "importance": self.importance,
            "retention": self.retention,
            "source": self.source,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


@dataclass
class ConsolidationCycle:
    """Record of a single consolidation pass."""

    cycle_id: str
    started_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    status: ConsolidationStatus = ConsolidationStatus.IDLE
    working_processed: int = 0
    promoted_to_episodic: int = 0
    episodic_to_semantic: int = 0
    facts_summarized: int = 0
    error: str = ""


# ═══════════════════════════════════════════════════════════════════════
# Helper
# ═══════════════════════════════════════════════════════════════════════

def _ebbinghaus_retention(elapsed_hours: float) -> float:
    """Interpolate Ebbinghaus retention for given elapsed hours."""
    if elapsed_hours <= 0:
        return 1.0
    sorted_hours = sorted(EBBINGHAUS_CURVE.keys())
    if elapsed_hours >= sorted_hours[-1]:
        return EBBINGHAUS_CURVE[sorted_hours[-1]]
    for i in range(len(sorted_hours) - 1):
        if sorted_hours[i] <= elapsed_hours < sorted_hours[i + 1]:
            frac = (elapsed_hours - sorted_hours[i]) / (sorted_hours[i + 1] - sorted_hours[i])
            r0 = EBBINGHAUS_CURVE[sorted_hours[i]]
            r1 = EBBINGHAUS_CURVE[sorted_hours[i + 1]]
            return r0 + frac * (r1 - r0)
    return EBBINGHAUS_CURVE[sorted_hours[-1]]


def _generate_node_id(content: str, tier: MemoryTier) -> str:
    raw = f"{tier.name}:{content}:{time.time()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def _summarize_cluster(nodes: list[MemoryNode]) -> str:
    """Recursive summarization: distill a cluster into one sentence."""
    if not nodes:
        return ""
    contents = [n.summary or n.content[:100] for n in nodes]
    if len(contents) == 1:
        return contents[0]
    joined = " | ".join(contents[:20])
    return f"[Aggregated {len(nodes)} items: {joined[:800]}]"


# ═══════════════════════════════════════════════════════════════════════
# Hierarchical Memory Engine
# ═══════════════════════════════════════════════════════════════════════

class HierarchicalMemoryEngine:
    """Cognitive hierarchical memory engine.

    Manages memory as a directed acyclic graph across tiers:
    Working → Episodic → Semantic → Procedural

    Features:
    - Automatic consolidation cycles
    - Ebbinghaus forgetting curve with spaced repetition
    - Importance-based promotion gating
    - Recursive cluster summarization
    - Full persistence via JSONL
    """

    def __init__(
        self,
        store_dir: str = "./data/hierarchical_memory",
        importance_threshold: float = DEFAULT_IMPORTANCE_THRESHOLD,
        consolidation_interval: float = DEFAULT_CONSOLIDATION_INTERVAL,
        max_semantic_facts: int = DEFAULT_MAX_SEMANTIC_FACTS,
    ) -> None:
        self.store_dir = Path(store_dir)
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.importance_threshold = importance_threshold
        self.consolidation_interval = consolidation_interval
        self.max_semantic_facts = max_semantic_facts

        # Tiered storage
        self.working: dict[str, MemoryNode] = {}
        self.episodic: dict[str, MemoryNode] = {}
        self.semantic: dict[str, MemoryNode] = {}
        self.procedural: dict[str, MemoryNode] = {}

        # Consolidation state
        self._last_consolidation: float = 0.0
        self._consolidation_status: ConsolidationStatus = ConsolidationStatus.IDLE
        self._consolidation_history: list[ConsolidationCycle] = []
        self._consolidation_lock: bool = False

        # L4 GraphRAG engine
        self._graph_rag: GraphRAG = GraphRAG()

        # Load from disk
        self._load()

    # ── Public API ─────────────────────────────────────────────────

    def add(
        self,
        content: str,
        tier: MemoryTier = MemoryTier.WORKING,
        importance: float = 0.5,
        metadata: dict[str, Any] | None = None,
        parent_ids: list[str] | None = None,
    ) -> str:
        """Add a memory node."""
        node_id = _generate_node_id(content, tier)
        node = MemoryNode(
            node_id=node_id,
            content=content,
            tier=tier,
            importance=importance,
            metadata=metadata or {},
            parents=parent_ids or [],
        )
        node.schedule_review()
        self._place(node)
        self._save_tier(tier)
        logger.debug("Added %s node %s (importance=%.2f)", tier.name, node_id[:8], importance)

        # Feed content into L4 GraphRAG for entity extraction
        self._graph_rag.add_document(node_id, content)

        return node_id

    def retrieve(
        self,
        query: str,
        top_k: int = 10,
        tiers: list[MemoryTier] | None = None,
        min_importance: float = 0.0,
    ) -> list[MemoryNode]:
        """Retrieve nodes by hybrid relevance scoring."""
        _start = time.time()
        target_tiers = tiers or [MemoryTier.EPISODIC, MemoryTier.SEMANTIC, MemoryTier.PROCEDURAL]
        candidates: list[MemoryNode] = []
        for t in target_tiers:
            if t == MemoryTier.GRAPH_RAG:
                continue  # handled separately via GraphRAG merge below
            candidates.extend(self._get_tier(t).values())

        if not candidates and MemoryTier.GRAPH_RAG not in target_tiers:
            _tracer.start_span(
                "memory.retrieve", kind="internal",
                attributes={"query": query[:80], "tier": target_tiers[0].name if target_tiers else "none",
                            "hit_count": 0, "latency_ms": 0},
            )
            _tracer.end_span(_tracer._context_stack()[-1] if _tracer._context_stack() else "", "ok")
            return []

        now = time.time()
        scored: list[tuple[float, MemoryNode]] = []
        query_terms = set(query.lower().split())

        for node in candidates:
            if node.importance < min_importance:
                continue
            # Decay retention
            node.retention = node.decay_retention(now)

            # Keyword relevance (Jaccard)
            content_terms = set(node.content.lower().split())
            meta_terms = set(str(v).lower() for v in node.metadata.values())
            all_terms = content_terms | meta_terms
            overlap = query_terms & all_terms
            union = query_terms | all_terms
            relevance = len(overlap) / len(union) if union else 0.0

            # Reject nodes with zero keyword overlap (importance/recency alone should not surface irrelevant results)
            if relevance == 0.0:
                continue

            # Recency boost
            recency = 1.0 / (1.0 + (now - node.last_accessed) / 86400)

            # Blended score: relevance 40% + importance 30% + retention 20% + recency 10%
            score = (
                relevance * 0.40
                + node.importance * 0.30
                + node.retention * 0.20
                + recency * 0.10
            )
            scored.append((score, node))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:top_k]
        for _, node in top:
            node.access_count += 1
            node.last_accessed = now

        _elapsed = (time.time() - _start) * 1000
        _tier_str = ",".join(t.name for t in target_tiers)
        _tracer.start_span(
            "memory.retrieve", kind="internal",
            attributes={"query": query[:80], "tier": _tier_str,
                        "hit_count": len(top), "latency_ms": round(_elapsed, 2)},
        )
        _tracer.end_span(_tracer._context_stack()[-1] if _tracer._context_stack() else "",
                         status="ok" if top else "ok",
                         attributes={"hit_count": len(top), "latency_ms": round(_elapsed, 2)})

        # Merge L4 GraphRAG results if GRAPH_RAG tier is requested
        if MemoryTier.GRAPH_RAG in target_tiers:
            try:
                graph_results = self._graph_rag.search(query, top_k=top_k)
                for sr in graph_results:
                    # Create MemoryNode from GraphRAG SearchResult
                    gr_node = MemoryNode(
                        node_id=f"graphrag:{sr.entity.name}",
                        content=f"[{sr.entity.type}] {sr.entity.name}: "
                                f"Found in {len(sr.entity.source_documents)} documents",
                        tier=MemoryTier.GRAPH_RAG,
                        importance=sr.score,
                        retention=1.0,
                        metadata={
                            "entity_type": sr.entity.type,
                            "source": "graph_rag",
                            "graph_score": sr.score,
                            "hops": sr.hops,
                            "entity_properties": sr.entity.properties,
                            "source_documents": sr.entity.source_documents,
                        },
                    )
                    top.append((sr.score * 0.70, gr_node))
                # Re-sort
                top.sort(key=lambda x: x[0], reverse=True)
                top = top[:top_k]
            except Exception:
                logger.debug("GraphRAG retrieval failed, continuing without L4", exc_info=True)

        return [n for _, n in top]

    def search_semantic(
        self,
        query: str,
        top_k: int = 10,
    ) -> list[MemoryNode]:
        """Search only semantic tier (consolidated facts)."""
        return self.retrieve(query, top_k, tiers=[MemoryTier.SEMANTIC])

    # ── L4 GraphRAG ───────────────────────────────────────────────

    @property
    def graph_rag(self) -> GraphRAG:
        """Direct access to the L4 GraphRAG engine."""
        return self._graph_rag

    def graph_retrieve(self, query: str, top_k: int = 10) -> list[ChunkResult]:
        """L4 knowledge graph retrieval.

        Performs hybrid search: keyword match on entity names + graph traversal.
        Results include entity info, relation summaries, and graph context.

        Returns:
            List of ChunkResult with source="graph_rag".
        """
        results: list[ChunkResult] = []
        try:
            sr_list = self._graph_rag.search(query, top_k=top_k)
            for sr in sr_list:
                ent = sr.entity
                summary = self._graph_rag.summarize_entity(ent.name)
                related = self._graph_rag.get_related_entities(ent.name)
                result = ChunkResult(
                    node_id=f"graphrag:{ent.name}",
                    content=summary,
                    tier="GRAPH_RAG",
                    layer="L4",
                    importance=sr.score,
                    retention=1.0,
                    source="graph_rag",
                    timestamp=time.time(),
                    metadata={
                        "entity_name": ent.name,
                        "entity_type": ent.type,
                        "entity_properties": ent.properties,
                        "source_documents": ent.source_documents,
                        "graph_score": sr.score,
                        "hops": sr.hops,
                        "related_entity_count": len(related),
                        "related_entities": [e.name for e in related],
                    },
                )
                results.append(result)
        except Exception:
            logger.debug("graph_retrieve failed", exc_info=True)
        return results

    def link(self, from_id: str, to_id: str, bidirectional: bool = True) -> bool:
        """Create a relationship edge between two nodes."""
        from_node = self._find(from_id)
        to_node = self._find(to_id)
        if not from_node or not to_node:
            return False
        if to_id not in from_node.related:
            from_node.related.append(to_id)
        if bidirectional and from_id not in to_node.related:
            to_node.related.append(from_id)
        return True

    def promote(self, node_id: str, target_tier: MemoryTier) -> bool:
        """Promote a node to a higher tier."""
        node = self._find(node_id)
        if not node:
            return False
        if target_tier.value <= node.tier.value:
            return False  # only upward promotion
        # Remove from current tier
        self._get_tier(node.tier).pop(node_id, None)
        # Update and place
        node.tier = target_tier
        node.summary = node.summary or node.content[:300]
        self._place(node)
        # Add parent relationship: old tier nodes as parents
        logger.info("Promoted %s → %s", node_id[:8], target_tier.name)
        return True

    def consolidate(self) -> ConsolidationCycle:
        """Run a full consolidation cycle."""
        if self._consolidation_lock:
            return ConsolidationCycle(
                cycle_id="locked",
                status=ConsolidationStatus.IDLE,
                error="Consolidation already in progress",
            )

        self._consolidation_lock = True
        self._consolidation_status = ConsolidationStatus.RUNNING
        cycle = ConsolidationCycle(
            cycle_id=hashlib.md5(str(time.time()).encode()).hexdigest()[:12],
        )

        try:
            # Phase 1: Working → Episodic (always promote)
            cycle.working_processed = len(self.working)
            for node in list(self.working.values()):
                if self._can_promote(node, MemoryTier.EPISODIC):
                    self.promote(node.node_id, MemoryTier.EPISODIC)
                    cycle.promoted_to_episodic += 1

            # Phase 2: Episodic → Semantic (importance-gated)
            for node in list(self.episodic.values()):
                node.retention = node.decay_retention()
                if node.importance >= self.importance_threshold and node.retention < 0.5:
                    if self._can_promote(node, MemoryTier.SEMANTIC):
                        self.promote(node.node_id, MemoryTier.SEMANTIC)
                        cycle.episodic_to_semantic += 1

            # Phase 3: Semantic cluster summarization (if over limit)
            if len(self.semantic) > self.max_semantic_facts:
                cycle.facts_summarized = self._summarize_semantic_clusters()

            cycle.status = ConsolidationStatus.COMPLETED
        except Exception as e:
            logger.error("Consolidation failed: %s", e)
            cycle.status = ConsolidationStatus.FAILED
            cycle.error = str(e)
        finally:
            cycle.finished_at = time.time()
            self._last_consolidation = time.time()
            self._consolidation_status = cycle.status
            self._consolidation_history.append(cycle)
            self._consolidation_lock = False
            self._save_all()

        logger.info(
            "Consolidation %s: W→E=%d E→S=%d Summarized=%d",
            cycle.status.value,
            cycle.promoted_to_episodic,
            cycle.episodic_to_semantic,
            cycle.facts_summarized,
        )
        return cycle

    def auto_consolidate(self) -> ConsolidationCycle | None:
        """Run consolidation if enough time has passed."""
        if time.time() - self._last_consolidation < self.consolidation_interval:
            return None
        return self.consolidate()

    def apply_forgetting(self) -> dict[str, int]:
        """Apply decay and purge fully-forgotten nodes."""
        now = time.time()
        purged = {"episodic": 0, "semantic": 0}
        for tier, store in [
            (MemoryTier.EPISODIC, self.episodic),
            (MemoryTier.SEMANTIC, self.semantic),
        ]:
            to_remove = []
            for nid, node in store.items():
                node.retention = node.decay_retention(now)
                if node.retention < 0.1 and node.importance < 0.3:
                    to_remove.append(nid)
            for nid in to_remove:
                del store[nid]
            purged[tier.name.lower()] = len(to_remove)
        self._save_all()
        return purged

    # ── Stats & Inspection ─────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """Return comprehensive memory statistics."""
        now = time.time()
        try:
            graph_stats = self._graph_rag.stats()
        except Exception:
            graph_stats = {"entity_count": 0, "relation_count": 0, "document_count": 0,
                           "top_entities": [], "avg_degree": 0, "max_degree": 0, "type_distribution": {}}
        return {
            "working_count": len(self.working),
            "episodic_count": len(self.episodic),
            "semantic_count": len(self.semantic),
            "procedural_count": len(self.procedural),
            "total_nodes": (
                len(self.working)
                + len(self.episodic)
                + len(self.semantic)
                + len(self.procedural)
            ),
            "consolidation_status": self._consolidation_status.value,
            "last_consolidation": self._last_consolidation,
            "consolidation_count": len(self._consolidation_history),
            "importance_threshold": self.importance_threshold,
            "avg_episodic_importance": (
                sum(n.importance for n in self.episodic.values()) / max(len(self.episodic), 1)
            ),
            "avg_semantic_importance": (
                sum(n.importance for n in self.semantic.values()) / max(len(self.semantic), 1)
            ),
            "avg_retention_episodic": (
                sum(n.decay_retention(now) for n in self.episodic.values()) / max(len(self.episodic), 1)
            ),
            "avg_retention_semantic": (
                sum(n.decay_retention(now) for n in self.semantic.values()) / max(len(self.semantic), 1)
            ),
            "needs_consolidation": (
                time.time() - self._last_consolidation >= self.consolidation_interval
            ),
            "ebbinghaus_curve": EBBINGHAUS_CURVE,
            "graph_rag": {
                "entities": graph_stats.get("entity_count", 0),
                "relations": graph_stats.get("relation_count", 0),
                "documents": graph_stats.get("document_count", 0),
                "top_entities": graph_stats.get("top_entities", []),
                "avg_degree": graph_stats.get("avg_degree", 0),
                "max_degree": graph_stats.get("max_degree", 0),
                "type_distribution": graph_stats.get("type_distribution", {}),
            },
        }

    def consolidation_history(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return recent consolidation cycle records."""
        return [
            {
                "cycle_id": c.cycle_id,
                "started_at": c.started_at,
                "finished_at": c.finished_at,
                "status": c.status.value,
                "working_processed": c.working_processed,
                "promoted_to_episodic": c.promoted_to_episodic,
                "episodic_to_semantic": c.episodic_to_semantic,
                "facts_summarized": c.facts_summarized,
                "error": c.error,
            }
            for c in self._consolidation_history[-limit:]
        ]

    def memory_graph(self, tier: str | None = None) -> dict[str, Any]:
        """Return graph structure for visualization."""
        nodes = []
        edges = []
        target_tiers = (
            [MemoryTier[tier.upper()]] if tier else
            [MemoryTier.WORKING, MemoryTier.EPISODIC, MemoryTier.SEMANTIC]
        )
        for t in target_tiers:
            for nid, node in self._get_tier(t).items():
                nodes.append({
                    "id": nid[:8],
                    "tier": node.tier.name,
                    "importance": node.importance,
                    "retention": node.retention,
                    "content_preview": (node.summary or node.content)[:80],
                })
                for rel in node.related:
                    edges.append({"from": nid[:8], "to": rel[:8]})
        return {"nodes": nodes, "edges": edges}

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        """Retrieve a single node by ID."""
        node = self._find(node_id)
        return node.to_dict() if node else None

    # ── Internal ───────────────────────────────────────────────────

    def _get_tier(self, tier: MemoryTier) -> dict[str, MemoryNode]:
        return {
            MemoryTier.WORKING: self.working,
            MemoryTier.EPISODIC: self.episodic,
            MemoryTier.SEMANTIC: self.semantic,
            MemoryTier.PROCEDURAL: self.procedural,
        }[tier]

    def _place(self, node: MemoryNode) -> None:
        self._get_tier(node.tier)[node.node_id] = node

    def _find(self, node_id: str) -> MemoryNode | None:
        for tier in [MemoryTier.WORKING, MemoryTier.EPISODIC, MemoryTier.SEMANTIC, MemoryTier.PROCEDURAL]:
            store = self._get_tier(tier)
            if node_id in store:
                return store[node_id]
        # Try prefix match
        for tier in [MemoryTier.WORKING, MemoryTier.EPISODIC, MemoryTier.SEMANTIC, MemoryTier.PROCEDURAL]:
            for nid, node in self._get_tier(tier).items():
                if nid.startswith(node_id):
                    return node
        return None

    def _can_promote(self, node: MemoryNode, target: MemoryTier) -> bool:
        return target.value > node.tier.value

    def _summarize_semantic_clusters(self) -> int:
        """Group semantic facts into clusters and summarize."""
        if len(self.semantic) <= self.max_semantic_facts:
            return 0

        # Sort by importance ascending, summarize lowest-importance
        sorted_nodes = sorted(self.semantic.values(), key=lambda n: n.importance)
        to_summarize = sorted_nodes[:DEFAULT_SUMMARIZATION_CLUSTER_SIZE]

        # Create summary node
        summary_content = _summarize_cluster(to_summarize)
        summary_id = self.add(
            content=summary_content,
            tier=MemoryTier.SEMANTIC,
            importance=max(n.importance for n in to_summarize) * 0.9,
            metadata={"source": "recursive_summarization", "original_count": len(to_summarize)},
            parent_ids=[n.node_id for n in to_summarize],
        )
        # Remove originals
        for node in to_summarize:
            del self.semantic[node.node_id]

        return len(to_summarize)

    # ── Persistence ────────────────────────────────────────────────

    def _save_tier(self, tier: MemoryTier) -> None:
        path = self.store_dir / f"{tier.name.lower()}.jsonl"
        store = self._get_tier(tier)
        with open(path, "w", encoding="utf-8") as f:
            for node in store.values():
                f.write(json.dumps(node.to_dict(), ensure_ascii=False) + "\n")

    def _save_all(self) -> None:
        for tier in [MemoryTier.WORKING, MemoryTier.EPISODIC, MemoryTier.SEMANTIC, MemoryTier.PROCEDURAL]:
            self._save_tier(tier)
        # Save consolidation history
        hist_path = self.store_dir / "consolidation_history.jsonl"
        with open(hist_path, "w", encoding="utf-8") as f:
            for c in self._consolidation_history[-50:]:
                f.write(json.dumps({
                    "cycle_id": c.cycle_id,
                    "started_at": c.started_at,
                    "finished_at": c.finished_at,
                    "status": c.status.value,
                    "working_processed": c.working_processed,
                    "promoted_to_episodic": c.promoted_to_episodic,
                    "episodic_to_semantic": c.episodic_to_semantic,
                    "facts_summarized": c.facts_summarized,
                    "error": c.error,
                }, ensure_ascii=False) + "\n")

    def _load(self) -> None:
        for tier in [MemoryTier.WORKING, MemoryTier.EPISODIC, MemoryTier.SEMANTIC, MemoryTier.PROCEDURAL]:
            path = self.store_dir / f"{tier.name.lower()}.jsonl"
            if not path.exists():
                continue
            store = self._get_tier(tier)
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        node = MemoryNode(
                            node_id=data["node_id"],
                            content=data.get("content", ""),
                            tier=tier,
                            importance=data.get("importance", 0.5),
                            created_at=data.get("created_at", time.time()),
                            last_accessed=data.get("last_accessed", time.time()),
                            access_count=data.get("access_count", 0),
                            review_count=data.get("review_count", 0),
                            retention=data.get("retention", 1.0),
                            metadata=data.get("metadata", {}),
                            children=data.get("children", []),
                            parents=data.get("parents", []),
                            related=data.get("related", []),
                            summary=data.get("summary", ""),
                        )
                        store[node.node_id] = node
                    except (json.JSONDecodeError, KeyError):
                        pass

        # Load consolidation history
        hist_path = self.store_dir / "consolidation_history.jsonl"
        if hist_path.exists():
            with open(hist_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        cycle = ConsolidationCycle(
                            cycle_id=data["cycle_id"],
                            started_at=data.get("started_at", 0),
                            finished_at=data.get("finished_at", 0),
                            status=ConsolidationStatus(data.get("status", "idle")),
                            working_processed=data.get("working_processed", 0),
                            promoted_to_episodic=data.get("promoted_to_episodic", 0),
                            episodic_to_semantic=data.get("episodic_to_semantic", 0),
                            facts_summarized=data.get("facts_summarized", 0),
                            error=data.get("error", ""),
                        )
                        self._consolidation_history.append(cycle)
                    except (json.JSONDecodeError, KeyError, ValueError):
                        pass

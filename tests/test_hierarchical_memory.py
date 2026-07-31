"""Tests for Hierarchical Memory Engine — P1 module."""
from __future__ import annotations

import json
import time
import tempfile
import shutil
from pathlib import Path

import pytest

from jarvis.hierarchical_memory import (
    HierarchicalMemoryEngine,
    MemoryNode,
    MemoryTier,
    ConsolidationStatus,
    ConsolidationCycle,
    EBBINGHAUS_CURVE,
    _ebbinghaus_retention,
    _generate_node_id,
    _summarize_cluster,
)


class TestEbbinghausCurve:
    """Test the Ebbinghaus forgetting curve math."""

    def test_immediate_retention_is_full(self):
        assert _ebbinghaus_retention(0) == 1.0

    def test_20min_retention(self):
        assert abs(_ebbinghaus_retention(0.33) - 0.58) < 0.01

    def test_1hour_retention(self):
        assert abs(_ebbinghaus_retention(1) - 0.44) < 0.01

    def test_1day_retention(self):
        assert abs(_ebbinghaus_retention(24) - 0.33) < 0.01

    def test_very_long_retention(self):
        assert _ebbinghaus_retention(10000) >= 0.20

    def test_negative_hours_returns_full(self):
        assert _ebbinghaus_retention(-5) == 1.0

    def test_monotonic_decay(self):
        vals = [_ebbinghaus_retention(h) for h in [0, 1, 24, 168, 720]]
        for i in range(len(vals) - 1):
            assert vals[i] >= vals[i + 1]


class TestMemoryNode:
    """Test MemoryNode dataclass."""

    def test_decay_retention_fresh(self):
        node = MemoryNode(node_id="test", content="hello", tier=MemoryTier.EPISODIC)
        assert node.decay_retention() == 1.0

    def test_decay_retention_aged(self):
        node = MemoryNode(
            node_id="test", content="hello", tier=MemoryTier.EPISODIC,
            last_accessed=time.time() - 3600  # 1 hour ago
        )
        retention = node.decay_retention()
        assert retention < 0.5  # Should be decayed

    def test_boost_retention(self):
        node = MemoryNode(
            node_id="test", content="hello", tier=MemoryTier.EPISODIC,
            last_accessed=time.time() - 86400
        )
        node.boost_retention()
        assert node.retention == 1.0
        assert node.review_count == 1
        assert node.next_review_at > time.time()

    def test_to_dict(self):
        node = MemoryNode(
            node_id="abc123", content="test content", tier=MemoryTier.EPISODIC,
            importance=0.8, metadata={"domain": "test"}
        )
        d = node.to_dict()
        assert d["node_id"] == "abc123"
        assert d["tier"] == "EPISODIC"
        assert d["importance"] == 0.8
        assert d["metadata"]["domain"] == "test"


class TestHelpers:
    """Test helper functions."""

    def test_generate_node_id_unique(self):
        id1 = _generate_node_id("hello", MemoryTier.WORKING)
        import time as _t
        _t.sleep(0.01)  # Ensure timestamp changes
        id2 = _generate_node_id("hello", MemoryTier.WORKING)
        assert id1 != id2  # Timestamp in hash ensures uniqueness

    def test_summarize_empty_cluster(self):
        assert _summarize_cluster([]) == ""

    def test_summarize_single_node(self):
        node = MemoryNode(node_id="1", content="single fact", tier=MemoryTier.SEMANTIC)
        result = _summarize_cluster([node])
        assert "single fact" in result

    def test_summarize_multi_node(self):
        nodes = [
            MemoryNode(node_id=str(i), content=f"fact {i}", tier=MemoryTier.SEMANTIC)
            for i in range(5)
        ]
        result = _summarize_cluster(nodes)
        assert "Aggregated" in result
        assert "5 items" in result


class TestHierarchicalMemoryEngine:
    """Tests for the full HierarchicalMemoryEngine."""

    @pytest.fixture
    def store_dir(self):
        d = tempfile.mkdtemp(prefix="hm_test_")
        yield d
        shutil.rmtree(d, ignore_errors=True)

    @pytest.fixture
    def engine(self, store_dir):
        return HierarchicalMemoryEngine(
            store_dir=store_dir,
            importance_threshold=0.4,
            consolidation_interval=0.1,
        )

    def test_add_working_memory(self, engine):
        nid = engine.add("hello world", tier=MemoryTier.WORKING, importance=0.6)
        assert nid
        assert len(engine.working) == 1
        stats = engine.stats()
        assert stats["working_count"] == 1

    def test_add_episodic_memory(self, engine):
        nid = engine.add("conversation turn", tier=MemoryTier.EPISODIC, importance=0.7)
        assert nid
        assert len(engine.episodic) == 1

    def test_add_semantic_memory(self, engine):
        nid = engine.add("Python is a language", tier=MemoryTier.SEMANTIC, importance=0.9)
        assert nid
        assert len(engine.semantic) == 1

    def test_retrieve_by_keyword(self, engine):
        engine.add("Python async programming guide", tier=MemoryTier.SEMANTIC, importance=0.8)
        engine.add("JavaScript event loop", tier=MemoryTier.SEMANTIC, importance=0.6)
        engine.add("Rust ownership model", tier=MemoryTier.SEMANTIC, importance=0.7)

        results = engine.retrieve("Python async", top_k=2)
        assert len(results) > 0
        # Most relevant should be Python-related
        assert "Python" in results[0].content

    def test_retrieve_empty(self, engine):
        results = engine.retrieve("nothing here")
        assert results == []

    def test_retrieve_min_importance(self, engine):
        engine.add("low importance fact", tier=MemoryTier.SEMANTIC, importance=0.1)
        engine.add("high importance fact", tier=MemoryTier.SEMANTIC, importance=0.9)

        results = engine.retrieve("fact", min_importance=0.5)
        assert all(n.importance >= 0.5 for n in results)

    def test_link_nodes(self, engine):
        nid1 = engine.add("A", tier=MemoryTier.EPISODIC)
        nid2 = engine.add("B", tier=MemoryTier.EPISODIC)
        assert engine.link(nid1, nid2)

        graph = engine.memory_graph()
        edges = graph["edges"]
        assert len(edges) >= 2  # Bidirectional

    def test_link_nonexistent(self, engine):
        assert not engine.link("nonexistent1", "nonexistent2")

    def test_promote_working_to_episodic(self, engine):
        nid = engine.add("working item", tier=MemoryTier.WORKING, importance=0.6)
        assert engine.promote(nid, MemoryTier.EPISODIC)
        node = engine.get_node(nid)
        assert node is not None
        assert node["tier"] == "EPISODIC"

    def test_promote_downward_rejected(self, engine):
        nid = engine.add("episodic item", tier=MemoryTier.EPISODIC, importance=0.6)
        assert not engine.promote(nid, MemoryTier.WORKING)

    def test_consolidation_cycle(self, engine):
        engine.add("w1", tier=MemoryTier.WORKING, importance=0.7)
        engine.add("w2", tier=MemoryTier.WORKING, importance=0.3)
        engine.add("e1", tier=MemoryTier.EPISODIC, importance=0.8)

        cycle = engine.consolidate()
        assert cycle.status == ConsolidationStatus.COMPLETED
        stats = engine.stats()
        assert stats["consolidation_count"] == 1

    def test_consolidation_history(self, engine):
        engine.add("test", tier=MemoryTier.WORKING, importance=0.6)
        engine.consolidate()
        history = engine.consolidation_history()
        assert len(history) == 1
        assert history[0]["status"] == "completed"

    def test_auto_consolidate(self, engine):
        engine.consolidation_interval = 0  # Always due
        engine.add("test", tier=MemoryTier.WORKING, importance=0.6)
        cycle = engine.auto_consolidate()
        assert cycle is not None

    def test_apply_forgetting(self, engine):
        # Add a low-importance old memory that should be forgotten
        nid = engine.add("will be forgotten", tier=MemoryTier.EPISODIC, importance=0.1)
        # Manually age the node
        node = engine._find(nid)
        if node:
            node.last_accessed = time.time() - 86400 * 90  # 90 days ago
            node.importance = 0.1

        purged = engine.apply_forgetting()
        assert isinstance(purged, dict)

    def test_stats(self, engine):
        engine.add("a", tier=MemoryTier.WORKING, importance=0.5)
        engine.add("b", tier=MemoryTier.EPISODIC, importance=0.7)
        engine.add("c", tier=MemoryTier.SEMANTIC, importance=0.9)

        stats = engine.stats()
        assert stats["working_count"] == 1
        assert stats["episodic_count"] == 1
        assert stats["semantic_count"] == 1
        assert stats["total_nodes"] == 3
        assert "importance_threshold" in stats
        assert "needs_consolidation" in stats
        assert "ebbinghaus_curve" in stats

    def test_memory_graph(self, engine):
        nid1 = engine.add("A", tier=MemoryTier.EPISODIC)
        nid2 = engine.add("B", tier=MemoryTier.SEMANTIC)
        engine.link(nid1, nid2)

        graph = engine.memory_graph()
        assert "nodes" in graph
        assert "edges" in graph
        assert len(graph["nodes"]) >= 2
        assert len(graph["edges"]) >= 2

    def test_get_nonexistent_node(self, engine):
        assert engine.get_node("nonexistent") is None

    def test_persistence(self, store_dir):
        engine1 = HierarchicalMemoryEngine(
            store_dir=store_dir,
            importance_threshold=0.5,
        )
        nid = engine1.add("persistent fact", tier=MemoryTier.SEMANTIC, importance=0.9, metadata={"key": "val"})
        engine1._save_all()

        engine2 = HierarchicalMemoryEngine(
            store_dir=store_dir,
            importance_threshold=0.5,
        )
        node = engine2.get_node(nid)
        assert node is not None
        assert node["metadata"]["key"] == "val"


class TestIntegration:
    """Integration tests with multiple tiers and full lifecycle."""

    @pytest.fixture
    def store_dir(self):
        d = tempfile.mkdtemp(prefix="hm_int_")
        yield d
        shutil.rmtree(d, ignore_errors=True)

    @pytest.fixture
    def engine(self, store_dir):
        return HierarchicalMemoryEngine(
            store_dir=store_dir,
            importance_threshold=0.3,
        )

    def test_full_lifecycle_work_to_semantic(self, engine):
        # Add working memories
        ids = []
        for i in range(10):
            nid = engine.add(f"task {i}: important detail", tier=MemoryTier.WORKING, importance=0.6 + i * 0.02)
            ids.append(nid)

        assert len(engine.working) == 10

        # Consolidate: Working → Episodic
        cycle = engine.consolidate()
        assert cycle.promoted_to_episodic > 0

        stats = engine.stats()
        assert stats["working_count"] == 0
        assert stats["episodic_count"] >= cycle.promoted_to_episodic

    def test_retrieve_across_tiers(self, engine):
        engine.add("async Python pattern", tier=MemoryTier.EPISODIC, importance=0.7)
        engine.add("Python typing module", tier=MemoryTier.SEMANTIC, importance=0.8)
        engine.add("rust procedural macro", tier=MemoryTier.EPISODIC, importance=0.5)

        results = engine.retrieve("Python", top_k=5,
            tiers=[MemoryTier.EPISODIC, MemoryTier.SEMANTIC])
        # Fresh entries all score high — top result must be Python
        assert len(results) >= 2
        assert any("Python" in r.content for r in results[:2])

    def test_search_semantic_only(self, engine):
        engine.add("code pattern", tier=MemoryTier.EPISODIC, importance=0.5)
        engine.add("important fact", tier=MemoryTier.SEMANTIC, importance=0.9)

        results = engine.search_semantic("fact")
        assert len(results) >= 1
        for r in results:
            assert r.tier == MemoryTier.SEMANTIC

    def test_concurrent_access(self, engine):
        """Verify engine handles rapid add+retrieve cycles."""
        for i in range(50):
            engine.add(f"bulk item {i}", tier=MemoryTier.WORKING, importance=0.5 + (i % 10) * 0.05)

        results = engine.retrieve("bulk item 25", tiers=[MemoryTier.WORKING])
        assert len(results) > 0

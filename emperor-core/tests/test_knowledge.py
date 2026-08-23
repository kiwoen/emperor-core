"""
Tests for KnowledgeGraph — entity extraction, edge inference, queries, persistence.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from jarvis.knowledge.graph import (
    KnowledgeGraph,
    Entity,
    Edge,
    _extract_entities,
    _entity_id,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def kg() -> KnowledgeGraph:
    return KnowledgeGraph()


# ---------------------------------------------------------------------------
# Entity extraction
# ---------------------------------------------------------------------------


class TestEntityExtraction:
    def test_extracts_dot_separated(self):
        results = _extract_entities("JARVIS uses jarvis.codex.engine for code review")
        names = [r[0] for r in results]
        assert "jarvis.codex.engine" in names

    def test_extracts_capitalized_phrases(self):
        results = _extract_entities("Code Review and Machine Learning are important")
        names = [r[0] for r in results]
        assert "Code Review" in names
        assert "Machine Learning" in names

    def test_filters_common_stop_words(self):
        results = _extract_entities("I am The Best But Not This")
        names = [r[0] for r in results]
        assert "I" not in names
        assert "The" not in names
        assert "But" not in names

    def test_empty_string_returns_empty(self):
        assert _extract_entities("") == []

    def test_no_capitalized_words_returns_empty(self):
        assert _extract_entities("nothing interesting here") == []

    def test_entity_id_deterministic(self):
        id1 = _entity_id("Code Review")
        id2 = _entity_id("Code Review")
        assert id1 == id2
        assert len(id1) == 16

    def test_entity_id_case_insensitive(self):
        assert _entity_id("Code Review") == _entity_id("code review")


# ---------------------------------------------------------------------------
# Entity management
# ---------------------------------------------------------------------------


class TestEntityManagement:
    @pytest.mark.asyncio
    async def test_add_entity(self, kg: KnowledgeGraph):
        entity = await kg.add_entity("Code Review", "concept")
        assert entity.name == "Code Review"
        assert entity.type == "concept"
        assert entity.first_seen != ""
        assert entity.last_seen != ""

    @pytest.mark.asyncio
    async def test_add_duplicate_updates_last_seen(self, kg: KnowledgeGraph):
        e1 = await kg.add_entity("Code Review", "concept")
        import asyncio
        await asyncio.sleep(0.01)  # ensure clock advances
        e2 = await kg.add_entity("Code Review", "concept")
        assert e1.id == e2.id
        assert e2.last_seen != e1.first_seen
        assert e2.last_seen >= e1.last_seen

    @pytest.mark.asyncio
    async def test_get_entity_by_name(self, kg: KnowledgeGraph):
        await kg.add_entity("Code Review", "concept")
        entity = await kg.get_entity("Code Review")
        assert entity is not None
        assert entity.name == "Code Review"

    @pytest.mark.asyncio
    async def test_get_entity_case_insensitive(self, kg: KnowledgeGraph):
        await kg.add_entity("Code Review", "concept")
        entity = await kg.get_entity("code review")
        assert entity is not None

    @pytest.mark.asyncio
    async def test_get_nonexistent_entity(self, kg: KnowledgeGraph):
        entity = await kg.get_entity("Ghost")
        assert entity is None

    @pytest.mark.asyncio
    async def test_get_or_create_creates_new(self, kg: KnowledgeGraph):
        entity = await kg.get_or_create_entity("New Concept")
        assert entity.name == "New Concept"
        assert len(kg.entities) == 1

    @pytest.mark.asyncio
    async def test_get_or_create_returns_existing(self, kg: KnowledgeGraph):
        e1 = await kg.add_entity("Existing", "concept")
        e2 = await kg.get_or_create_entity("Existing")
        assert e1.id == e2.id
        assert len(kg.entities) == 1


# ---------------------------------------------------------------------------
# Edge management
# ---------------------------------------------------------------------------


class TestEdgeManagement:
    @pytest.mark.asyncio
    async def test_add_edge_between_existing_entities(self, kg: KnowledgeGraph):
        await kg.add_entity("Python", "concept")
        await kg.add_entity("FastAPI", "concept")
        edge = await kg.add_edge("Python", "FastAPI", "uses")
        assert edge is not None
        assert edge.relation == "uses"
        assert edge.occurrences == 1

    @pytest.mark.asyncio
    async def test_add_edge_missing_entity_returns_none(self, kg: KnowledgeGraph):
        await kg.add_entity("Python", "concept")
        edge = await kg.add_edge("Python", "Ghost", "uses")
        assert edge is None

    @pytest.mark.asyncio
    async def test_duplicate_edge_increments_occurrences(self, kg: KnowledgeGraph):
        await kg.add_entity("A", "concept")
        await kg.add_entity("B", "concept")
        e1 = await kg.add_edge("A", "B", "uses")
        e2 = await kg.add_edge("A", "B", "uses")
        assert e1 is not None and e2 is not None
        assert e2.occurrences == 2

    @pytest.mark.asyncio
    async def test_different_relation_creates_separate_edge(self, kg: KnowledgeGraph):
        await kg.add_entity("A", "concept")
        await kg.add_entity("B", "concept")
        e1 = await kg.add_edge("A", "B", "uses")
        e2 = await kg.add_edge("A", "B", "depends_on")
        assert e1 is not None and e2 is not None
        assert e1.relation != e2.relation


# ---------------------------------------------------------------------------
# Ingestion (text → entities + edges)
# ---------------------------------------------------------------------------


class TestIngestion:
    @pytest.mark.asyncio
    async def test_ingest_extracts_and_links(self, kg: KnowledgeGraph):
        entities = await kg.ingest("JARVIS uses Code Review via Vector Engine")
        assert len(entities) >= 2
        assert len(kg.entities) >= 2
        # At least one edge should exist between co-occurring entities
        assert sum(len(e) for e in kg.edges.values()) >= 1

    @pytest.mark.asyncio
    async def test_ingest_empty_text(self, kg: KnowledgeGraph):
        entities = await kg.ingest("nothing capitalized here")
        assert entities == []

    @pytest.mark.asyncio
    async def test_ingest_repeated_entity_no_duplicate(self, kg: KnowledgeGraph):
        await kg.ingest("Python is great for Code Review")
        await kg.ingest("Python powers Code Review automation")
        py = await kg.get_entity("Python")
        assert py is not None
        # Entities without duplicates (Python, Code Review, Code, Review = 4 total)
        assert len(kg.entities) == 4

    @pytest.mark.asyncio
    async def test_ingest_detects_uses_relation(self, kg: KnowledgeGraph):
        await kg.ingest("JARVIS uses Python for Code Review")
        edges = []
        for targets in kg.edges.values():
            edges.extend(targets.values())
        relations = [e.relation for e in edges]
        assert "uses" in relations

    @pytest.mark.asyncio
    async def test_ingest_detects_depends_on_relation(self, kg: KnowledgeGraph):
        await kg.ingest("Code Review depends on Vector Engine")
        edges = []
        for targets in kg.edges.values():
            edges.extend(targets.values())
        relations = [e.relation for e in edges]
        assert "depends_on" in relations


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


class TestQueries:
    @pytest.mark.asyncio
    async def test_get_neighbors_direct(self, kg: KnowledgeGraph):
        await kg.add_entity("A", "concept")
        await kg.add_entity("B", "concept")
        await kg.add_edge("A", "B", "uses")

        neighbors = await kg.get_neighbors("A")
        assert len(neighbors) == 1
        assert neighbors[0]["entity"] == "B"
        assert neighbors[0]["depth"] == 1

    @pytest.mark.asyncio
    async def test_get_neighbors_max_depth(self, kg: KnowledgeGraph):
        await kg.add_entity("A", "concept")
        await kg.add_entity("B", "concept")
        await kg.add_entity("C", "concept")
        await kg.add_edge("A", "B", "uses")
        await kg.add_edge("B", "C", "produces")

        deep = await kg.get_neighbors("A", max_depth=2)
        assert len(deep) == 2
        depths = [n["depth"] for n in deep]
        assert 1 in depths
        assert 2 in depths

    @pytest.mark.asyncio
    async def test_get_neighbors_nonexistent(self, kg: KnowledgeGraph):
        neighbors = await kg.get_neighbors("Ghost")
        assert neighbors == []

    @pytest.mark.asyncio
    async def test_find_paths_direct(self, kg: KnowledgeGraph):
        await kg.add_entity("A", "concept")
        await kg.add_entity("B", "concept")
        await kg.add_edge("A", "B", "uses")

        paths = await kg.find_paths("A", "B")
        assert len(paths) >= 1
        assert paths[0][-1]["entity"] == "B"

    @pytest.mark.asyncio
    async def test_find_paths_self(self, kg: KnowledgeGraph):
        await kg.add_entity("A", "concept")
        paths = await kg.find_paths("A", "A")
        assert len(paths) == 1
        assert paths[0][0]["entity"] == "A"

    @pytest.mark.asyncio
    async def test_find_paths_nonexistent(self, kg: KnowledgeGraph):
        paths = await kg.find_paths("A", "B")
        assert paths == []

    @pytest.mark.asyncio
    async def test_find_paths_two_hop(self, kg: KnowledgeGraph):
        await kg.add_entity("A", "concept")
        await kg.add_entity("B", "concept")
        await kg.add_entity("C", "concept")
        await kg.add_edge("A", "B", "uses")
        await kg.add_edge("B", "C", "depends_on")

        paths = await kg.find_paths("A", "C")
        assert len(paths) >= 1
        # Path should be A → B → C (3 steps: A, B, C)
        assert len(paths[0]) == 3
        assert paths[0][0]["entity"] == "A"
        assert paths[0][1]["entity"] == "B"
        assert paths[0][2]["entity"] == "C"

    @pytest.mark.asyncio
    async def test_most_central(self, kg: KnowledgeGraph):
        # Star topology: center → A, B, C
        await kg.add_entity("Center", "concept")
        for name in ["A", "B", "C"]:
            await kg.add_entity(name, "concept")
            await kg.add_edge("Center", name, "uses")

        top = await kg.most_central(top_n=5)
        assert len(top) >= 2
        assert top[0]["entity"] == "Center"
        assert top[0]["centrality"] >= 3.0

    @pytest.mark.asyncio
    async def test_most_central_empty_graph(self, kg: KnowledgeGraph):
        top = await kg.most_central()
        assert top == []


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


class TestSummary:
    @pytest.mark.asyncio
    async def test_summary_empty(self, kg: KnowledgeGraph):
        s = kg.summary()
        assert s["entity_count"] == 0
        assert s["edge_count"] == 0

    @pytest.mark.asyncio
    async def test_summary_with_entities(self, kg: KnowledgeGraph):
        await kg.add_entity("A", "concept")
        await kg.add_entity("B", "module")
        await kg.add_edge("A", "B", "uses")

        s = kg.summary()
        assert s["entity_count"] == 2
        assert s["edge_count"] == 1
        assert s["entity_types"] == {"concept": 1, "module": 1}


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class TestPersistence:
    @pytest.mark.asyncio
    async def test_save_and_load_roundtrip(self, kg: KnowledgeGraph):
        await kg.add_entity("Python", "concept", {"version": "3.11"})
        await kg.add_entity("FastAPI", "concept")
        await kg.add_edge("Python", "FastAPI", "uses")

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            await kg.save_snapshot(path)

            kg2 = KnowledgeGraph()
            await kg2.load_snapshot(path)

            assert len(kg2.entities) == 2
            py = await kg2.get_entity("Python")
            assert py is not None
            assert py.properties["version"] == "3.11"

            neighbors = await kg2.get_neighbors("Python")
            assert len(neighbors) == 1
            assert neighbors[0]["entity"] == "FastAPI"

        finally:
            Path(path).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_load_nonexistent_file_raises(self, kg: KnowledgeGraph):
        with pytest.raises(FileNotFoundError):
            await kg.load_snapshot("nonexistent.json")

    @pytest.mark.asyncio
    async def test_save_empty_graph(self, kg: KnowledgeGraph):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            await kg.save_snapshot(path)
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            assert len(data["entities"]) == 0
            assert len(data["edges"]) == 0
            assert "saved_at" in data
        finally:
            Path(path).unlink(missing_ok=True)

"""Tests for Smart Search API endpoint covering memory integration."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from jarvis.court_api import create_app
from jarvis.emperor import Emperor
from jarvis.hierarchical_memory import HierarchicalMemoryEngine, MemoryTier


class TestSmartSearchAPI:
    """Test /api/dashboard/search endpoint — memory integration."""

    @pytest.fixture
    def emperor(self):
        return Emperor()

    @pytest.fixture
    def mem_engine(self):
        engine = HierarchicalMemoryEngine()
        return engine

    @pytest.fixture
    def client(self, emperor, mem_engine):
        app = create_app(hierarchical_memory_engine=mem_engine)
        app.extra["emperor"] = emperor
        return TestClient(app)

    def test_empty_query_returns_all_sections(self, client):
        resp = client.get("/api/dashboard/search?q=&limit=3")
        assert resp.status_code == 200
        data = resp.json()
        assert data["query"] == ""
        for key in ["tasks", "evals", "audits", "healing", "context_versions", "memories"]:
            assert key in data
            assert data[key] == []

    def test_no_results_for_nonexistent_keyword(self, client):
        resp = client.get("/api/dashboard/search?q=xyznonexistent999&limit=3")
        assert resp.status_code == 200
        data = resp.json()
        total = sum(len(data[k])
                    for k in ["tasks", "evals", "audits", "healing", "context_versions", "memories"])
        assert total == 0

    def test_memory_search_returns_results(self, emperor, mem_engine):
        mem_engine.add("Python async programming guide", tier=MemoryTier.SEMANTIC, importance=0.9)
        mem_engine.add("JavaScript event loop explained", tier=MemoryTier.EPISODIC, importance=0.6)
        mem_engine.add("Rust ownership model deep dive", tier=MemoryTier.SEMANTIC, importance=0.8)

        app = create_app(hierarchical_memory_engine=mem_engine)
        app.extra["emperor"] = emperor
        client = TestClient(app)

        resp = client.get("/api/dashboard/search?q=Python&limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["memories"]) >= 1
        assert any("Python" in m["content"] for m in data["memories"])

    def test_memory_search_tier_field(self, emperor, mem_engine):
        mem_engine.add("important semantic fact", tier=MemoryTier.SEMANTIC, importance=0.95)
        app = create_app(hierarchical_memory_engine=mem_engine)
        app.extra["emperor"] = emperor
        client = TestClient(app)

        resp = client.get("/api/dashboard/search?q=semantic&limit=3")
        data = resp.json()
        assert len(data["memories"]) > 0
        m = data["memories"][0]
        for field in ["tier", "importance", "retention", "node_id"]:
            assert field in m, f"missing field {field}"
        assert m["tier"] == "SEMANTIC"

    def test_memory_search_respects_limit(self, emperor, mem_engine):
        for i in range(10):
            mem_engine.add(f"searchable fact number {i}", tier=MemoryTier.EPISODIC, importance=0.5 + i * 0.03)
        app = create_app(hierarchical_memory_engine=mem_engine)
        app.extra["emperor"] = emperor
        client = TestClient(app)

        resp = client.get("/api/dashboard/search?q=searchable+fact&limit=3")
        data = resp.json()
        assert len(data["memories"]) <= 3

    def test_memory_search_case_insensitive(self, emperor, mem_engine):
        mem_engine.add("UPPERCASE KNOWLEDGE ITEM", tier=MemoryTier.SEMANTIC, importance=0.7)
        app = create_app(hierarchical_memory_engine=mem_engine)
        app.extra["emperor"] = emperor
        client = TestClient(app)

        resp = client.get("/api/dashboard/search?q=uppercase&limit=3")
        data = resp.json()
        assert len(data["memories"]) >= 1

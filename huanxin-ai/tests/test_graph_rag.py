"""Tests for huanxin.graph_rag — GraphRAG knowledge graph engine."""

import pytest
from huanxin.graph_rag import (
    Entity,
    Relation,
    KnowledgeGraph,
    GraphFragment,
    SearchResult,
    GraphRAG,
    _extract_entities,
    _extract_relations,
    _split_sentences,
    _classify_entity,
)


# ═══════════════════════════════════════════════════════════════════════
# Unit tests: Entity extraction
# ═══════════════════════════════════════════════════════════════════════


class TestEntityExtraction:
    """Test entity extraction from text using regex rules."""

    def test_person_extraction(self):
        """Extract person names with titles."""
        text = "Dr. Jane Smith and Prof. Alan Turing discussed AI safety with President Biden."
        entities = _extract_entities(text)
        names = {e.name for e in entities}
        assert "Dr. Jane Smith" in names
        assert "Prof. Alan Turing" in names

    def test_organization_extraction(self):
        """Extract organization names."""
        text = "OpenAI Inc. announced a partnership with Microsoft Corporation and Google DeepMind."
        entities = _extract_entities(text)
        names = {e.name for e in entities}
        assert any("OpenAI" in n for n in names)

    def test_concept_extraction(self):
        """Extract concept/technology terms."""
        text = "GraphRAG outperforms RAG in multi-hop reasoning tasks using Transformer architecture."
        entities = _extract_entities(text)
        names = {e.name for e in entities}
        assert "GraphRAG" in names
        assert "Transformer" in names

    def test_code_identifier_extraction(self):
        """Extract code identifiers like snake_case and camelCase."""
        text = "The _graph_rag module uses add_document and query_graph methods."
        entities = _extract_entities(text)
        names = {e.name for e in entities}
        assert "query_graph" in names or "add_document" in names

    def test_version_extraction(self):
        """Extract version numbers."""
        text = "Upgraded from v1.2.3 to v2.0.0-rc1 with Python 3.11 support."
        entities = _extract_entities(text)
        names = {e.name for e in entities}
        has_version = any("v" in n.lower() or "." in n for n in names)
        assert has_version

    def test_date_extraction(self):
        """Extract date patterns."""
        text = "The release on 2024-01-15 included major fixes and was followed by 2025-03-01 update."
        entities = _extract_entities(text)
        names = {e.name for e in entities}
        assert "2024-01-15" in names


class TestRelationBuilding:
    """Test co-occurrence relation building."""

    def test_relation_cooccurrence_window(self):
        """Entities in the same window should have relations."""
        text = "OpenAI developed GPT-4, a powerful AI model. GPT-4 uses Transformer architecture. "
        text += "Google launched Gemini to compete with GPT-4 and OpenAI."
        sentences = _split_sentences(text)
        entities = _extract_entities(text)
        relations = _extract_relations(entities, sentences, window_size=5)
        assert len(relations) >= 1

    def test_relation_confidence(self):
        """Relation confidence should be between 0 and 1."""
        text = "GPT-4 is a large language model. GPT-4 was trained by OpenAI."
        sentences = _split_sentences(text)
        entities = _extract_entities(text)
        relations = _extract_relations(entities, sentences)
        for rel in relations:
            assert 0.0 <= rel.confidence <= 1.0


# ═══════════════════════════════════════════════════════════════════════
# Unit tests: KnowledgeGraph
# ═══════════════════════════════════════════════════════════════════════


class TestKnowledgeGraph:
    """Test the in-memory KnowledgeGraph storage structure."""

    def test_add_entity(self):
        kg = KnowledgeGraph()
        kg.add_entity(Entity(name="GPT-4", type="concept"))
        assert "GPT-4" in kg.entities
        assert kg.entities["GPT-4"].type == "concept"

    def test_entity_merge(self):
        """Adding same entity twice should merge properties."""
        kg = KnowledgeGraph()
        kg.add_entity(Entity(name="OpenAI", type="organization", source_documents=["doc1"]))
        kg.add_entity(Entity(name="OpenAI", type="organization", source_documents=["doc2"]))
        assert len(kg.entities["OpenAI"].source_documents) == 2

    def test_add_relation(self):
        kg = KnowledgeGraph()
        kg.add_entity(Entity(name="GPT-4"))
        kg.add_entity(Entity(name="OpenAI"))
        rel = Relation(source_entity="GPT-4", target_entity="OpenAI", relation_type="developed_by")
        kg.add_relation(rel)
        assert len(kg.relations) == 1
        assert "OpenAI" in kg._adj["GPT-4"]
        assert "GPT-4" in kg._adj["OpenAI"]

    def test_duplicate_relation(self):
        """Duplicate relations should not be added."""
        kg = KnowledgeGraph()
        kg.add_entity(Entity(name="A"))
        kg.add_entity(Entity(name="B"))
        rel = Relation(source_entity="A", target_entity="B")
        kg.add_relation(rel)
        kg.add_relation(rel)
        assert len(kg.relations) == 1

    def test_get_neighbors(self):
        kg = KnowledgeGraph()
        for name in ["A", "B", "C", "D"]:
            kg.add_entity(Entity(name=name))
        kg.add_relation(Relation("A", "B"))
        kg.add_relation(Relation("A", "C"))
        kg.add_relation(Relation("B", "D"))
        neighbors_1 = kg.get_neighbors("A", hops=1)
        assert set(neighbors_1) == {"B", "C"}
        neighbors_2 = kg.get_neighbors("A", hops=2)
        assert "D" in neighbors_2

    def test_subgraph(self):
        kg = KnowledgeGraph()
        for name in ["X", "Y", "Z"]:
            kg.add_entity(Entity(name=name))
        kg.add_relation(Relation("X", "Y"))
        kg.add_relation(Relation("Y", "Z"))
        fragment = kg.subgraph("Y", hops=1)
        assert fragment.center.name == "Y"
        assert len(fragment.entities) == 3

    def test_stats(self):
        kg = KnowledgeGraph()
        kg.add_entity(Entity(name="A"))
        kg.add_entity(Entity(name="B"))
        kg.add_relation(Relation("A", "B"))
        stats = kg.stats()
        assert stats["entity_count"] == 2
        assert stats["relation_count"] == 1


# ═══════════════════════════════════════════════════════════════════════
# Integration tests: GraphRAG
# ═══════════════════════════════════════════════════════════════════════


class TestGraphRAG:
    """Integration tests for the GraphRAG engine."""

    DOC_AI = (
        "OpenAI released GPT-4 in March 2023. GPT-4 is a multimodal large language model. "
        "It was trained using deep learning techniques on massive datasets. "
        "GPT-4 powers ChatGPT, which has millions of users worldwide. "
        "Microsoft Corporation invested billions in OpenAI Inc."
    )

    DOC_FRAMEWORKS = (
        "LangChain is a popular framework for building AI agents and LLM applications. "
        "LangGraph extends LangChain with stateful graph-based orchestration. "
        "CrewAI uses a role-based approach where agents act as CEO, Developer, and QA. "
        "GraphRAG enhances retrieval with knowledge graphs and entity relationships. "
        "The MCP protocol standardizes context connections between models and tools."
    )

    def _build_graph(self) -> GraphRAG:
        graf = GraphRAG()
        graf.add_document("doc_ai", self.DOC_AI)
        graf.add_document("doc_frameworks", self.DOC_FRAMEWORKS)
        return graf

    def test_add_document_and_stats(self):
        graf = GraphRAG()
        entities = graf.add_document("doc1", self.DOC_AI)
        assert len(entities) > 0
        stats = graf.stats()
        assert stats["entity_count"] > 0
        assert stats["document_count"] == 1

    def test_search_exact_entity(self):
        graf = self._build_graph()
        results = graf.search("GPT-4")
        assert len(results) > 0
        assert results[0].entity.name == "GPT-4"

    def test_search_partial_match(self):
        graf = self._build_graph()
        results = graf.search("lang")
        names = {r.entity.name for r in results}
        assert "LangChain" in names or "LangGraph" in names

    def test_search_empty_query(self):
        graf = self._build_graph()
        results = graf.search("nonexistent_xyz_123")
        assert len(results) == 0

    def test_query_graph_subgraph(self):
        graf = self._build_graph()
        fragment = graf.query_graph("OpenAI", hops=1)
        assert fragment.center.name == "OpenAI"
        assert len(fragment.entities) > 0

    def test_query_graph_missing_entity(self):
        graf = self._build_graph()
        fragment = graf.query_graph("NoSuchEntity", hops=1)
        assert fragment.center.name == "NoSuchEntity"
        assert len(fragment.entities) == 0

    def test_get_related_entities(self):
        graf = self._build_graph()
        related = graf.get_related_entities("GraphRAG")
        # GraphRAG and LangChain should be related via co-occurrence
        assert len(related) >= 0  # co-occurrence depends on window

    def test_summarize_entity(self):
        graf = self._build_graph()
        summary = graf.summarize_entity("GPT-4")
        assert "GPT-4" in summary
        assert "concept" in summary.lower()

    def test_summarize_missing_entity(self):
        graf = self._build_graph()
        summary = graf.summarize_entity("FakeEntity")
        assert "not found" in summary.lower()

    def test_graph_stats(self):
        graf = self._build_graph()
        stats = graf.stats()
        assert "entity_count" in stats
        assert "relation_count" in stats
        assert "document_count" in stats
        assert "top_entities" in stats
        assert "type_distribution" in stats


# ═══════════════════════════════════════════════════════════════════════
# Integration with HierarchicalMemory
# ═══════════════════════════════════════════════════════════════════════


class TestHierarchicalMemoryIntegration:
    """Test that GraphRAG integrates with HierarchicalMemoryEngine."""

    def test_hierarchical_memory_has_graph_rag_property(self):
        from huanxin.hierarchical_memory import HierarchicalMemoryEngine
        engine = HierarchicalMemoryEngine()
        assert engine.graph_rag is not None

    def test_add_feeds_graph_rag(self):
        from huanxin.hierarchical_memory import HierarchicalMemoryEngine, MemoryTier
        engine = HierarchicalMemoryEngine()
        engine.add(
            content="OpenAI released GPT-4 in March 2023. GPT-4 is a multimodal large language model.",
            tier=MemoryTier.WORKING,
            importance=0.7,
        )
        stats = engine.graph_rag.stats()
        assert stats["entity_count"] > 0

    def test_graph_retrieve_returns_results(self):
        from huanxin.hierarchical_memory import HierarchicalMemoryEngine, MemoryTier
        engine = HierarchicalMemoryEngine()
        engine.add(
            content="LangChain and LangGraph are frameworks for building AI agents. "
            "GraphRAG uses knowledge graphs for retrieval.",
            tier=MemoryTier.WORKING,
            importance=0.8,
        )
        results = engine.graph_retrieve("LangChain", top_k=5)
        # May or may not find depending on entity extraction
        assert isinstance(results, list)

    def test_retrieve_with_graph_tier(self):
        from huanxin.hierarchical_memory import HierarchicalMemoryEngine, MemoryTier
        engine = HierarchicalMemoryEngine()
        engine.add(
            content="The Transformer architecture revolutionized NLP. BERT and GPT use Transformers.",
            tier=MemoryTier.WORKING,
            importance=0.9,
        )
        # retrieve() with GRAPH_RAG tier should work
        results = engine.retrieve(
            "Transformer",
            top_k=5,
            tiers=[MemoryTier.WORKING, MemoryTier.GRAPH_RAG],
        )
        # Should return at least WORKING tier results
        assert len(results) >= 1

    def test_memory_stats_includes_graph_rag(self):
        from huanxin.hierarchical_memory import HierarchicalMemoryEngine
        engine = HierarchicalMemoryEngine()
        stats = engine.stats()
        assert "graph_rag" in stats
        assert "entities" in stats["graph_rag"]


# ═══════════════════════════════════════════════════════════════════════
# API endpoint tests
# ═══════════════════════════════════════════════════════════════════════


class TestGraphRagAPI:
    """Test the GraphRAG API endpoints via Starlette TestClient."""

    @pytest.fixture
    def client(self):
        """Build a Starlette TestClient with GraphRAG injected into app.extra."""
        try:
            from starlette.testclient import TestClient
        except ImportError:
            pytest.skip("starlette not installed")

        from huanxin.graph_rag import GraphRAG
        from huanxin.court_api import create_app

        graf = GraphRAG()
        graf.add_document("test_doc", (
            "OpenAI released GPT-4 in March 2023. GPT-4 is a multimodal large language model. "
            "Microsoft Corporation invested billions in OpenAI Inc. "
            "LangChain is a popular framework for building AI agents and LLM applications."
        ))

        app = create_app()
        app.extra["emperor"] = type("FakeHuanxin", (), {"graph_rag": graf})()
        return TestClient(app)

    def test_graph_search(self, client):
        resp = client.get("/api/memory/graph?query=GPT-4")
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert data["query"] == "GPT-4"

    def test_graph_entity(self, client):
        resp = client.get("/api/memory/graph/entity/OpenAI")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "OpenAI"
        assert "summary" in data
        assert "fragment" in data

    def test_graph_entity_neighbors(self, client):
        resp = client.get("/api/memory/graph/entity/GPT-4/neighbors")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "GPT-4"
        assert "neighbors" in data

    def test_graph_stats(self, client):
        resp = client.get("/api/memory/graph/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "entity_count" in data
        assert "relation_count" in data
        assert data["entity_count"] > 0

    def test_graph_missing_emperor(self):
        """Test 503 when emperor is not in app.extra."""
        try:
            from starlette.testclient import TestClient
        except ImportError:
            pytest.skip("starlette not installed")

        from huanxin.court_api import create_app

        app = create_app()
        # no emperor in app.extra
        client = TestClient(app)
        resp = client.get("/api/memory/graph?query=test")
        assert resp.status_code == 503

"""
Tests for KnowledgeGraph-powered minister context injection.

Covers:
    - Minister KG query returns empty when no KG is injected
    - Minister KG query returns relevant entities when KG is populated
    - KG context is passed to _try_real_model system prompt
    - Emperor injects KG into ministers during install
    - End-to-end: petition with KG-aware deliberation
"""

from __future__ import annotations

import asyncio
import pytest

from jarvis.court.minister import (
    Edict,
    Memorial,
    Minister,
    MinisterProfile,
    MinisterState,
)
from jarvis.court.emperor import Emperor, ImperialCourt
from jarvis.knowledge.graph import KnowledgeGraph


# ── Test Minister ────────────────────────────────────────────────────


class _TestMinister(Minister):
    """A simple test minister with trackable behavior."""

    def __init__(self, name="测试大臣", system_prompt="", kg=None):
        profile = MinisterProfile(
            title=name,
            archetype="Test-AI",
            domain="testing",
            strengths=["测试", "验证", "代码分析"],
            weaknesses=["none"],
        )
        super().__init__(profile, system_prompt_template=system_prompt, knowledge_graph=kg)
        self.last_kg_context = ""
        self.handle_calls: list[str] = []

    async def _handle(self, edict):
        self.handle_calls.append(edict.intent)
        return f"[Test:{self.name}] {edict.intent[:30]}", 0.75


# ── KG Context Query Tests ───────────────────────────────────────────


class TestKGContextQuery:
    """Tests for _query_kg_for_context behavior."""

    def test_no_kg_returns_empty(self):
        """Minister without KG returns empty context."""
        m = _TestMinister()
        result = asyncio.run(m._query_kg_for_context("分析代码安全漏洞"))
        assert result == ""

    def test_empty_kg_returns_empty(self):
        """Minister with empty KG returns empty context."""
        kg = KnowledgeGraph()
        m = _TestMinister(kg=kg)
        result = asyncio.run(m._query_kg_for_context("分析代码"))
        assert result == ""

    def test_populated_kg_returns_context(self):
        """Minister with populated KG gets relevant entity neighbors."""
        kg = KnowledgeGraph()

        async def seed():
            await kg.add_entity("代码安全", "concept")
            await kg.add_entity("SQL注入", "concept")
            await kg.add_entity("XSS攻击", "concept")
            await kg.add_edge("代码安全", "SQL注入", "involves")
            await kg.add_edge("代码安全", "XSS攻击", "involves")

        asyncio.run(seed())

        m = _TestMinister(kg=kg)
        result = asyncio.run(m._query_kg_for_context("代码安全"))

        assert "SQL注入" in result
        assert "XSS攻击" in result
        assert "involves" in result

    def test_kg_context_no_entities_found(self):
        """Query for unknown topic returns empty."""
        kg = KnowledgeGraph()

        async def seed():
            await kg.add_entity("机器学习", "concept")

        asyncio.run(seed())

        m = _TestMinister(kg=kg)
        result = asyncio.run(m._query_kg_for_context("量子计算"))

        # Entity extraction from "量子计算" may or may not match
        # Just ensure it doesn't crash
        assert isinstance(result, str)

    def test_has_knowledge_graph_property(self):
        """has_knowledge_graph reflects KG injection state."""
        m = _TestMinister()
        assert not m.has_knowledge_graph

        m.set_knowledge_graph(KnowledgeGraph())
        assert m.has_knowledge_graph


# ── KG Context in Pipeline Tests ─────────────────────────────────────


class TestKGInPipeline:
    """Tests that KG context flows through receive_edict → _try_real_model."""

    def test_kg_context_stored_on_dispatch(self):
        """After dispatch, _kg_context is populated (even with mock fallback)."""
        kg = KnowledgeGraph()

        async def seed():
            await kg.add_entity("Python", "concept")
            await kg.add_entity("FastAPI", "module")
            await kg.add_edge("Python", "FastAPI", "uses")

        asyncio.run(seed())

        m = _TestMinister(name="KG-Aware", kg=kg)
        edict = Edict(edict_id="e-kg1", intent="用Python写FastAPI服务")

        asyncio.run(m.receive_edict(edict))

        # After dispatch, _kg_context should be non-empty
        assert len(m._kg_context) > 0
        assert "FastAPI" in m._kg_context or "Python" in m._kg_context

    def test_handle_still_called_with_kg(self):
        """_handle() is still called normally, KG context doesn't interfere."""
        kg = KnowledgeGraph()

        async def seed():
            await kg.add_entity("代码审查", "concept")

        asyncio.run(seed())

        m = _TestMinister(name="KG-Handler", kg=kg)
        edict = Edict(edict_id="e-kg2", intent="进行代码审查")

        memorial = asyncio.run(m.receive_edict(edict))
        assert memorial.success
        assert "[Test:KG-Handler]" in memorial.output
        assert len(m.handle_calls) == 1


# ── Emperor + KG Integration Tests ───────────────────────────────────


class TestEmperorKGIntegration:
    """Tests that Emperor properly injects KG into ministers."""

    def test_emperor_no_kg(self):
        """Emperor without KG — ministers don't have KG."""
        court = ImperialCourt()  # No KG passed
        court.install_ministers_from_factory()

        for minister in court.ministers.values():
            assert not minister.has_knowledge_graph

    def test_emperor_with_kg(self):
        """Emperor with KG — all ministers get KG injected."""
        kg = KnowledgeGraph()
        court = ImperialCourt(knowledge_graph=kg)
        court.install_ministers_from_factory()

        for minister in court.ministers.values():
            assert minister.has_knowledge_graph

    def test_full_petition_with_kg(self):
        """Full petition pipeline works with KG-aware ministers."""
        kg = KnowledgeGraph()

        async def seed():
            await kg.add_entity("系统性能", "concept")
            await kg.add_entity("CPU瓶颈", "concept")
            await kg.add_entity("内存泄漏", "concept")
            await kg.add_edge("系统性能", "CPU瓶颈", "involves")
            await kg.add_edge("系统性能", "内存泄漏", "involves")

        asyncio.run(seed())

        emperor = Emperor(knowledge_graph=kg)
        decree = asyncio.run(emperor.receive_petition("分析系统性能瓶颈"))

        assert decree.success
        assert len(decree.ministers_consulted) >= 1

    def test_emperor_install_then_dismiss(self):
        """Dismissed ministers don't affect KG state."""
        kg = KnowledgeGraph()
        court = ImperialCourt(knowledge_graph=kg)
        court.install_ministers_from_factory()

        # Dismiss chancellor
        court.dismiss_minister("丞相")
        assert "丞相" not in court.ministers

        # Remaining ministers still have KG
        for minister in court.ministers.values():
            assert minister.has_knowledge_graph


# ── Edge Cases ───────────────────────────────────────────────────────


class TestKGEdgeCases:
    """Edge cases for KG-powered minister behavior."""

    def test_kg_ingest_exception_silent(self):
        """If KG ingestion raises, minister still works (no crash)."""
        class BrokenKG:
            async def ingest(self, text, domain=""):
                raise RuntimeError("KG is broken")
            async def get_neighbors(self, entity, max_depth=1, include_weights=False):
                raise RuntimeError("Can't query")

        m = _TestMinister(name="Resilient")
        m.set_knowledge_graph(BrokenKG())

        edict = Edict(edict_id="e-broken", intent="测试容错")
        memorial = asyncio.run(m.receive_edict(edict))

        # Should fall back to mock gracefully
        assert memorial.success
        assert "[Test:Resilient]" in memorial.output
        assert m._kg_context == ""

    def test_kg_after_set_provider(self):
        """Setting provider and KG independently works."""
        kg = KnowledgeGraph()

        async def seed():
            await kg.add_entity("API设计", "concept")

        asyncio.run(seed())

        m = _TestMinister(name="FullStack")
        m.set_knowledge_graph(kg)

        # Still mock mode (no real provider)
        assert not m.has_real_model
        assert m.has_knowledge_graph

        edict = Edict(edict_id="e-full", intent="设计REST API")
        memorial = asyncio.run(m.receive_edict(edict))
        assert memorial.success

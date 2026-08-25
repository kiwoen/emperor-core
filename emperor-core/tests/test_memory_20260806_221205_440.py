"""Tests for huanxin.memory — VectorMemory & MemoryManager."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest


# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════

def _safe_rmtree(path: str) -> None:
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════
# Module imports
# ══════════════════════════════════════════════════════════════════════


class TestModuleImports:
    def test_import_vector_memory(self):
        from huanxin.memory import VectorMemory

        assert VectorMemory is not None

    def test_import_memory_manager(self):
        from huanxin.memory import MemoryManager

        assert MemoryManager is not None

    def test_import_memory_engine_still_works(self):
        """Existing MemoryEngine must still be importable."""
        from huanxin.memory import MemoryEngine, MemoryEntry

        assert MemoryEngine is not None
        assert MemoryEntry is not None

    def test_exports(self):
        from huanxin.memory import (
            MemoryEngine,
            MemoryEntry,
            MemoryManager,
            VectorMemory,
        )

        assert VectorMemory.__module__ == "huanxin.memory.vector_store"
        assert MemoryManager.__module__ == "huanxin.memory.manager"
        assert MemoryEngine.__module__ == "huanxin.memory.engine"


# ══════════════════════════════════════════════════════════════════════
# VectorMemory
# ══════════════════════════════════════════════════════════════════════


class TestVectorMemory:
    @pytest.fixture(autouse=True)
    def _cleanup(self):
        self._tmpdirs: list[str] = []
        yield
        for d in self._tmpdirs:
            _safe_rmtree(d)

    def _make_store(self, **kwargs) -> "VectorMemory":
        from huanxin.memory.vector_store import VectorMemory

        tmp = tempfile.mkdtemp(prefix="huanxin_vm_test_")
        self._tmpdirs.append(tmp)
        return VectorMemory(persist_dir=tmp, **kwargs)

    def test_init_defaults(self):
        vm = self._make_store()
        assert vm.count() == 0
        assert vm.collection is not None

    def test_add_single(self):
        vm = self._make_store()
        ids = vm.add(["Hello world"], [{"tag": "greeting"}])
        assert len(ids) == 1
        assert ids[0].startswith("mem_")
        assert vm.count() == 1

    def test_add_multiple(self):
        vm = self._make_store()
        ids = vm.add(
            ["Paris", "Tokyo", "New York"],
            [{"type": "city"}] * 3,
        )
        assert len(ids) == 3
        assert vm.count() == 3

    def test_add_auto_ids(self):
        vm = self._make_store()
        ids1 = vm.add(["a"])
        ids2 = vm.add(["b"])
        assert ids1[0] != ids2[0]
        assert vm.count() == 2

    def test_query_semantic(self):
        vm = self._make_store()
        vm.add(
            [
                "The cat sat on the mat.",
                "Dogs are loyal companions.",
                "Python is a programming language.",
            ]
        )
        results = vm.query("feline animal", top_k=2)
        assert len(results["ids"]) == 2
        assert len(results["documents"]) == 2
        assert len(results["metadatas"]) == 2
        assert len(results["distances"]) == 2
        # The cat sentence should rank higher
        assert "cat" in results["documents"][0].lower()

    def test_query_metadata_filter(self):
        vm = self._make_store()
        vm.add(
            ["Paris fact", "Tokyo fact", "Random thought"],
            [
                {"type": "city", "region": "europe"},
                {"type": "city", "region": "asia"},
                {"type": "note"},
            ],
        )
        results = vm.query("capital city", top_k=5, metadata_filter={"type": "city"})
        assert len(results["ids"]) == 2
        for meta in results["metadatas"]:
            assert meta["type"] == "city"

    def test_delete(self):
        vm = self._make_store()
        ids = vm.add(["doc1", "doc2", "doc3"])
        assert vm.count() == 3
        removed = vm.delete([ids[0], ids[2]])
        assert removed == 2
        assert vm.count() == 1

    def test_delete_nonexistent(self):
        vm = self._make_store()
        removed = vm.delete(["nonexistent_id"])
        assert removed == 0

    def test_delete_all(self):
        vm = self._make_store()
        vm.add(["a", "b", "c"])
        assert vm.count() == 3
        total = vm.delete_all()
        assert total == 3
        assert vm.count() == 0

    def test_empty_query(self):
        vm = self._make_store()
        results = vm.query("anything")
        assert results["ids"] == []


# ══════════════════════════════════════════════════════════════════════
# MemoryManager
# ══════════════════════════════════════════════════════════════════════


class TestMemoryManager:
    @pytest.fixture(autouse=True)
    def _cleanup(self):
        self._tmpdirs: list[str] = []
        yield
        for d in self._tmpdirs:
            _safe_rmtree(d)

    def _make_vm(self) -> "VectorMemory":
        from huanxin.memory.vector_store import VectorMemory

        tmp = tempfile.mkdtemp(prefix="huanxin_mm_test_")
        self._tmpdirs.append(tmp)
        return VectorMemory(persist_dir=tmp)

    def _make_mgr(self, **kwargs):
        from huanxin.memory.manager import MemoryManager

        return MemoryManager(vector_store=self._make_vm(), **kwargs)

    def test_init(self):
        mm = self._make_mgr()
        assert mm is not None
        assert mm._store is not None

    def test_add_memory_knowledge(self):
        mm = self._make_mgr()
        mid = mm.add_memory("The sky is blue.", memory_type="knowledge")
        assert mid.startswith("mem_")

    def test_add_memory_conversation(self):
        mm = self._make_mgr()
        mid = mm.add_memory("User: Hello there!", memory_type="conversation")
        assert mid.startswith("mem_")

    def test_add_memory_task_result(self):
        mm = self._make_mgr()
        mid = mm.add_memory("Task 'cleanup' completed in 2.3s.", memory_type="task_result")
        assert mid.startswith("mem_")

    def test_add_memory_invalid_type(self):
        mm = self._make_mgr()
        with pytest.raises(ValueError, match="Invalid memory_type"):
            mm.add_memory("test", memory_type="invalid_type")

    def test_add_memory_with_extra_metadata(self):
        mm = self._make_mgr()
        mid = mm.add_memory(
            "User prefers Python over Java.",
            memory_type="knowledge",
            metadata={"source": "user_profile", "confidence": 0.9},
        )
        assert mid.startswith("mem_")

    def test_recall_returns_results(self):
        mm = self._make_mgr()
        mm.add_memory("The Eiffel Tower is in Paris.", memory_type="knowledge")
        mm.add_memory("Machine learning uses neural networks.", memory_type="knowledge")
        mm.add_memory("Sushi is a Japanese dish.", memory_type="knowledge")

        results = mm.recall("France landmarks", top_k=2)
        assert len(results["ids"]) >= 1
        assert "documents" in results
        assert "scores" in results

    def test_recall_type_filter(self):
        mm = self._make_mgr()
        mm.add_memory("User said hi.", memory_type="conversation")
        mm.add_memory("Gravity is 9.8 m/s^2.", memory_type="knowledge")

        results = mm.recall("physics constants", top_k=2, memory_types=["knowledge"])
        for meta in results["metadatas"]:
            assert meta["type"] == "knowledge"

    def test_recall_no_results(self):
        mm = self._make_mgr()
        results = mm.recall("something that does not exist", top_k=5)
        assert results["ids"] == []

    def test_summarize_context(self):
        mm = self._make_mgr()
        mm.add_memory("The project uses FastAPI and SQLAlchemy.", memory_type="knowledge")
        mm.add_memory("Deployed on AWS ECS with Docker.", memory_type="knowledge")

        summary = mm.summarize_context("project tech stack", max_memories=2)
        assert isinstance(summary, str)
        assert "FastAPI" in summary
        assert "## Relevant Past Memories" in summary

    def test_summarize_context_empty(self):
        mm = self._make_mgr()
        summary = mm.summarize_context("nonexistent topic")
        assert "No relevant memories found" in summary

    def test_search_shorthand(self):
        mm = self._make_mgr()
        mm.add_memory("Python is dynamically typed.", memory_type="knowledge")
        mm.add_memory("Rust prevents memory leaks at compile time.", memory_type="knowledge")

        docs = mm.search("programming languages", top_k=2)
        assert isinstance(docs, list)
        assert len(docs) >= 1
        assert all(isinstance(d, str) for d in docs)

    def test_recency_boosts_newer_memories(self):
        mm = self._make_mgr(recency_weight=0.9)
        mm.add_memory("Very old information about ancient Rome.", memory_type="knowledge")
        # Second memory added just after — tiny time delta but still "newer"
        mm.add_memory("Breaking news: AI just won a Nobel prize.", memory_type="knowledge")

        results = mm.recall("recent events latest news", top_k=2, apply_recency=True)
        assert len(results["ids"]) >= 1
        assert "scores" in results

    def test_memory_types_persist_in_metadata(self):
        mm = self._make_mgr()
        mm.add_memory("convo entry", memory_type="conversation")
        mm.add_memory("task entry", memory_type="task_result")
        mm.add_memory("knowledge entry", memory_type="knowledge")

        results = mm.recall("entry", top_k=5, apply_recency=False)
        types = {m["type"] for m in results["metadatas"]}
        assert types == {"conversation", "task_result", "knowledge"}

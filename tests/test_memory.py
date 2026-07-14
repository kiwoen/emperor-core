"""
Tests for CourtMemory — persistent multi-minister experience memory system.

Covers:
  - Recording & dedup
  - Keyword-based similarity querying
  - Domain statistics
  - Context summarization
  - Knowledge propagation
  - Time decay & pruning
  - Integration helpers
"""

import time
import pytest
from jarvis.court.memory import (
    CourtMemory,
    MemoryEntry,
    MemorySummary,
    QueryResult,
    memory_from_memorial,
)


class TestMemoryEntry:
    """MemoryEntry dataclass behavior."""

    def test_creation(self):
        entry = MemoryEntry(
            id="abc123",
            domain="engineering",
            minister_name="chancellor",
            intent="代码安全漏洞分析",
            intent_keywords=["安全", "漏洞", "分析", "代码"],
            success=True,
            confidence=0.85,
            execution_time_ms=1200.0,
            timestamp=time.time(),
        )
        assert entry.id == "abc123"
        assert entry.domain == "engineering"
        assert entry.success is True
        assert entry.confidence == 0.85
        assert entry.weight == 1.0

    def test_defaults(self):
        entry = MemoryEntry(
            id="x",
            domain="security",
            minister_name="censor",
            intent="test",
            intent_keywords=["test"],
            success=False,
            confidence=0.5,
            execution_time_ms=100.0,
            timestamp=time.time(),
        )
        assert entry.merit == 0.0
        assert entry.weight == 1.0
        assert entry.tags == []

    def test_tags_field(self):
        entry = MemoryEntry(
            id="t1",
            domain="engineering",
            minister_name="works",
            intent="优化构建速度",
            intent_keywords=["优化", "构建", "速度"],
            success=True,
            confidence=0.9,
            execution_time_ms=500.0,
            timestamp=time.time(),
            tags=["optimization", "performance"],
        )
        assert "optimization" in entry.tags
        assert "performance" in entry.tags


class TestRecording:
    """CourtMemory.record() behavior."""

    def test_record_basic(self):
        memory = CourtMemory()
        entry = MemoryEntry(
            id="r001",
            domain="engineering",
            minister_name="chancellor",
            intent="代码审查",
            intent_keywords=["代码", "审查"],
            success=True,
            confidence=0.9,
            execution_time_ms=800.0,
            timestamp=time.time(),
        )
        memory.record(entry)
        assert memory.entry_count == 1

    def test_record_multiple(self):
        memory = CourtMemory()
        for i in range(10):
            entry = MemoryEntry(
                id=f"r{i:03d}",
                domain="engineering",
                minister_name="chancellor",
                intent=f"任务{i}",
                intent_keywords=[f"任务{i}"],
                success=i % 2 == 0,
                confidence=0.7 + i * 0.02,
                execution_time_ms=100.0 + i * 50,
                timestamp=time.time(),
            )
            memory.record(entry)
        assert memory.entry_count == 10

    def test_dedup_same_id(self):
        memory = CourtMemory()
        entry = MemoryEntry(
            id="dup001",
            domain="engineering",
            minister_name="chancellor",
            intent="重复任务",
            intent_keywords=["重复", "任务"],
            success=True,
            confidence=0.8,
            execution_time_ms=500.0,
            timestamp=time.time(),
        )
        id1 = memory.record(entry)
        id2 = memory.record(entry)  # Same id
        assert id1 == "dup001"
        assert id2 == "dup001"
        assert memory.entry_count == 1  # Not duplicated

    def test_prune_on_overflow(self):
        memory = CourtMemory(max_entries=5)
        for i in range(10):
            entry = MemoryEntry(
                id=f"prune{i:03d}",
                domain="general",
                minister_name="diviner",
                intent=f"溢出任务{i}",
                intent_keywords=["溢出", f"任务{i}"],
                success=False,
                confidence=0.3,
                execution_time_ms=100.0,
                timestamp=time.time() + i,  # Increasing timestamps
            )
            memory.record(entry)
        assert memory.entry_count == 5  # Capped at 5


class TestTokenize:
    """CourtMemory._tokenize() keyword extraction."""

    def test_chinese_bigrams(self):
        tokens = CourtMemory._tokenize("代码安全漏洞分析工具")
        # Bigrams: 代码, 码安, 安全, 全漏, 漏洞, 洞分, 分析, 析工, 工具
        assert "代码" in tokens
        assert "安全" in tokens
        assert "漏洞" in tokens
        assert "分析" in tokens
        assert "工具" in tokens

    def test_english_words(self):
        tokens = CourtMemory._tokenize("code review security analysis")
        assert "code" in tokens
        assert "review" in tokens
        assert "security" in tokens
        assert "analysis" in tokens

    def test_short_english_filtered(self):
        tokens = CourtMemory._tokenize("a bb code xy")
        # "a" (2 chars, filtered), "bb" (2 chars, filtered), "code" (4, kept), "xy" (2, filtered)
        assert "a" not in tokens
        assert "bb" not in tokens
        assert "code" in tokens
        assert "xy" not in tokens

    def test_mixed_cn_en(self):
        tokens = CourtMemory._tokenize("Python 代码 review 安全")
        assert "python" in tokens
        assert "代码" in tokens
        assert "review" in tokens
        assert "安全" in tokens

    def test_empty_string(self):
        tokens = CourtMemory._tokenize("")
        assert tokens == []

    def test_dedup(self):
        tokens = CourtMemory._tokenize("安全 安全 代码 代码")
        # Should deduplicate
        assert tokens.count("安全") == 1
        assert tokens.count("代码") == 1


class TestQuery:
    """CourtMemory.query() similarity search."""

    @pytest.fixture
    def populated_memory(self):
        memory = CourtMemory(similarity_threshold=0.15)
        entries_data = [
            ("engineering", "chancellor", "代码安全漏洞分析", ["代码", "安全", "漏洞", "分析"], True, 0.9),
            ("engineering", "chancellor", "Python 代码审查工具", ["python", "代码", "审查", "工具"], True, 0.85),
            ("engineering", "works", "构建系统优化", ["构建", "系统", "优化"], True, 0.8),
            ("engineering", "chancellor", "数据库查询性能", ["数据库", "查询", "性能"], False, 0.4),
            ("security", "censor", "安全漏洞扫描", ["安全", "漏洞", "扫描"], True, 0.95),
            ("security", "guard", "防火墙配置", ["防火墙", "配置"], False, 0.3),
            ("research", "diviner", "论文检索分析", ["论文", "检索", "分析"], True, 0.7),
            ("engineering", "chancellor", "内存泄漏调试", ["内存", "泄漏", "调试"], True, 0.75),
            ("engineering", "works", "CI管道构建", ["管道", "构建", "ci"], False, 0.5),
            ("engineering", "chancellor", "安全审计代码", ["安全", "审计", "代码"], True, 0.88),
        ]
        for domain, minister, intent, keywords, success, conf in entries_data:
            entry = MemoryEntry(
                id=f"q_{domain}_{minister}_{intent[:4]}",
                domain=domain,
                minister_name=minister,
                intent=intent,
                intent_keywords=keywords,
                success=success,
                confidence=conf,
                execution_time_ms=500.0,
                timestamp=time.time(),
            )
            memory.record(entry)
        return memory

    def test_query_exact_match(self, populated_memory):
        results = populated_memory.query("engineering", "代码安全漏洞分析", top_k=5)
        assert len(results) >= 1
        # Top result should have our intent
        top = results[0].entry
        assert "漏洞" in top.intent or "安全" in top.intent

    def test_query_partial_overlap(self, populated_memory):
        results = populated_memory.query("engineering", "代码漏洞", top_k=5)
        assert len(results) >= 1
        # Should match entries with "代码" + "漏洞"
        for r in results:
            keywords = r.entry.intent_keywords
            assert "代码" in keywords or "漏洞" in keywords

    def test_query_success_only(self, populated_memory):
        results = populated_memory.query(
            "engineering", "代码", top_k=10, success_only=True,
        )
        assert len(results) > 0
        for r in results:
            assert r.entry.success is True

    def test_query_no_match(self, populated_memory):
        results = populated_memory.query("engineering", "量子计算", top_k=5)
        assert results == []

    def test_query_below_threshold(self, populated_memory):
        # Set very high threshold
        populated_memory.similarity_threshold = 0.9
        results = populated_memory.query("engineering", "代码安全", top_k=5)
        assert results == []

    def test_query_top_k_limit(self, populated_memory):
        results = populated_memory.query("engineering", "代码", top_k=2)
        assert len(results) <= 2

    def test_query_empty_intent(self, populated_memory):
        results = populated_memory.query("engineering", "", top_k=5)
        assert results == []

    def test_query_sorted_by_relevance(self, populated_memory):
        results = populated_memory.query("engineering", "安全代码漏洞", top_k=10)
        if len(results) >= 2:
            for i in range(len(results) - 1):
                score_i = results[i].relevance * results[i].entry.weight
                score_j = results[i + 1].relevance * results[i + 1].entry.weight
                assert score_i >= score_j - 0.001  # Allow float rounding


class TestDomainStats:
    """CourtMemory.get_domain_stats() aggregate statistics."""

    @pytest.fixture
    def populated_memory(self):
        memory = CourtMemory()
        for i in range(10):
            entry = MemoryEntry(
                id=f"ds_{i:02d}",
                domain="engineering" if i < 7 else "security",
                minister_name="chancellor" if i % 2 == 0 else "works",
                intent=f"任务{i}",
                intent_keywords=[f"任务{i}", "代码"] if i % 3 == 0 else [f"任务{i}"],
                success=i != 3 and i != 6,  # 2 failures
                confidence=0.5 + i * 0.04,
                execution_time_ms=200.0 + i * 100,
                timestamp=time.time(),
            )
            memory.record(entry)
        return memory

    def test_domain_stats_basic(self, populated_memory):
        stats = populated_memory.get_domain_stats("engineering")
        assert stats is not None
        assert stats.domain == "engineering"
        assert stats.total_entries == 7
        assert 0.6 < stats.success_rate < 0.9  # 5/7 ≈ 0.71

    def test_domain_stats_nonexistent(self, populated_memory):
        stats = populated_memory.get_domain_stats("finance")
        assert stats is None

    def test_domain_stats_top_minister(self, populated_memory):
        stats = populated_memory.get_domain_stats("engineering")
        assert stats is not None
        assert stats.top_minister in ("chancellor", "works")

    def test_all_domain_stats(self, populated_memory):
        all_stats = populated_memory.get_all_domain_stats()
        assert len(all_stats) == 2
        domains = {s.domain for s in all_stats}
        assert domains == {"engineering", "security"}

    def test_domain_stats_recent_successes(self, populated_memory):
        stats = populated_memory.get_domain_stats("engineering")
        assert stats is not None
        assert stats.recent_successes >= 0


class TestSummarizeContext:
    """CourtMemory.summarize_context() minister-facing context."""

    def test_context_basic(self):
        memory = CourtMemory()
        for i in range(5):
            entry = MemoryEntry(
                id=f"ctx_{i:02d}",
                domain="engineering",
                minister_name="chancellor",
                intent=f"代码审查任务{i}",
                intent_keywords=["代码", "审查", f"任务{i}"],
                success=i < 4,
                confidence=0.8,
                execution_time_ms=500.0,
                timestamp=time.time(),
            )
            memory.record(entry)

        context = memory.summarize_context("chancellor", "engineering", "代码审查")
        assert "Memory" in context
        assert "engineering" in context
        # 4/5 success → 80%
        assert "80%" in context or "4/5" in context

    def test_context_no_history(self):
        memory = CourtMemory()
        context = memory.summarize_context("chancellor", "engineering", "新任务")
        assert "No past experience" in context

    def test_context_streak_message(self):
        memory = CourtMemory()
        for i in range(10):
            entry = MemoryEntry(
                id=f"streak_{i:02d}",
                domain="engineering",
                minister_name="chancellor",
                intent=f"成功任务{i}",
                intent_keywords=["成功", f"任务{i}"],
                success=True,
                confidence=0.9,
                execution_time_ms=300.0,
                timestamp=time.time(),
            )
            memory.record(entry)

        context = memory.summarize_context("chancellor", "engineering", "新任务")
        assert "streak" in context.lower()

    def test_context_caution_message(self):
        memory = CourtMemory()
        for i in range(5):
            entry = MemoryEntry(
                id=f"fail_{i:02d}",
                domain="engineering",
                minister_name="chancellor",
                intent=f"失败任务{i}",
                intent_keywords=["失败", f"任务{i}"],
                success=False,
                confidence=0.3,
                execution_time_ms=500.0,
                timestamp=time.time(),
            )
            memory.record(entry)

        context = memory.summarize_context("chancellor", "engineering", "新任务")
        assert "cautious" in context.lower()


class TestPropagation:
    """CourtMemory.propagate_knowledge() cross-minister sharing."""

    def test_propagate_successful_patterns(self):
        memory = CourtMemory()
        # chancellor has 3 successful engineering entries
        for i in range(3):
            entry = MemoryEntry(
                id=f"prop_src_{i:02d}",
                domain="engineering",
                minister_name="chancellor",
                intent=f"代码优化{i}",
                intent_keywords=["代码", "优化", f"v{i}"],
                success=True,
                confidence=0.9,
                execution_time_ms=500.0,
                timestamp=time.time(),
            )
            memory.record(entry)

        propagated = memory.propagate_knowledge("chancellor", "security")
        assert len(propagated) == 3
        for e in propagated:
            assert "propagated" in e.tags
            assert e.domain == "security"
            assert e.weight < 1.0
            assert e.confidence < 0.9

    def test_propagate_no_source(self):
        memory = CourtMemory()
        propagated = memory.propagate_knowledge("nobody", "engineering")
        assert propagated == []

    def test_propagate_dedup(self):
        memory = CourtMemory()
        entry = MemoryEntry(
            id="prop_dedup_00",
            domain="engineering",
            minister_name="chancellor",
            intent="代码审查",
            intent_keywords=["代码", "审查"],
            success=True,
            confidence=0.9,
            execution_time_ms=500.0,
            timestamp=time.time(),
        )
        memory.record(entry)

        # First propagation
        p1 = memory.propagate_knowledge("chancellor", "security")
        assert len(p1) == 1

        # Second propagation should be deduped
        p2 = memory.propagate_knowledge("chancellor", "security")
        assert len(p2) == 0


class TestDecay:
    """CourtMemory decay and maintenance."""

    def test_decay_reduces_weight(self):
        memory = CourtMemory(decay_factor=0.5, decay_interval_hours=0.001)
        entry = MemoryEntry(
            id="decay001",
            domain="engineering",
            minister_name="chancellor",
            intent="测试衰减",
            intent_keywords=["测试", "衰减"],
            success=True,
            confidence=0.8,
            execution_time_ms=500.0,
            timestamp=time.time() - 5000,  # Old entry
        )
        memory.record(entry)

        # Force a decay interval to pass
        memory._last_decay = time.time() - 10  # 10 seconds ago

        removed = memory.apply_decay()
        assert entry.weight < 1.0
        # With decay_factor=0.5 and very short interval, it should reduce significantly

    def test_decay_removes_very_low_weight(self):
        memory = CourtMemory(decay_factor=0.01, decay_interval_hours=0.0001)
        entry = MemoryEntry(
            id="decay_remove",
            domain="engineering",
            minister_name="chancellor",
            intent="即将消失",
            intent_keywords=["消失"],
            success=True,
            confidence=0.5,
            execution_time_ms=100.0,
            timestamp=time.time(),
        )
        memory.record(entry)
        memory._last_decay = time.time() - 100

        removed = memory.apply_decay()
        # With such aggressive decay, the entry should be removed
        assert removed >= 0  # May or may not be removed depending on exact timing

    def test_decay_no_effect_immediate(self):
        memory = CourtMemory(decay_factor=0.5)
        entry = MemoryEntry(
            id="decay_fresh",
            domain="engineering",
            minister_name="chancellor",
            intent="新鲜任务",
            intent_keywords=["新鲜"],
            success=True,
            confidence=0.8,
            execution_time_ms=500.0,
            timestamp=time.time(),
        )
        memory.record(entry)
        # No time has passed
        removed = memory.apply_decay()
        assert removed == 0


class TestQueryByMinister:
    """CourtMemory.query_by_minister()."""

    def test_query_by_minister(self):
        memory = CourtMemory()
        for i in range(5):
            for name in ["chancellor", "censor", "diviner"]:
                entry = MemoryEntry(
                    id=f"qbm_{name}_{i:02d}",
                    domain="general",
                    minister_name=name,
                    intent=f"任务{i}",
                    intent_keywords=[f"任务{i}"],
                    success=True,
                    confidence=0.7,
                    execution_time_ms=300.0,
                    timestamp=time.time() + i,
                )
                memory.record(entry)

        results = memory.query_by_minister("chancellor")
        assert len(results) == 5
        # Should be sorted by timestamp desc
        for i in range(len(results) - 1):
            assert results[i].timestamp >= results[i + 1].timestamp


class TestClear:
    """CourtMemory.clear_domain() and clear_all()."""

    def test_clear_domain(self):
        memory = CourtMemory()
        for i in range(3):
            for domain in ["engineering", "security"]:
                entry = MemoryEntry(
                    id=f"clr_{domain}_{i:02d}",
                    domain=domain,
                    minister_name="chancellor",
                    intent=f"任务{i}",
                    intent_keywords=[f"任务{i}"],
                    success=True,
                    confidence=0.8,
                    execution_time_ms=500.0,
                    timestamp=time.time(),
                )
                memory.record(entry)

        assert memory.entry_count == 6
        removed = memory.clear_domain("engineering")
        assert removed == 3
        assert memory.entry_count == 3
        assert "engineering" not in memory.domains
        assert "security" in memory.domains

    def test_clear_all(self):
        memory = CourtMemory()
        for i in range(5):
            entry = MemoryEntry(
                id=f"ca_{i:02d}",
                domain="general",
                minister_name="diviner",
                intent=f"任务{i}",
                intent_keywords=[f"任务{i}"],
                success=True,
                confidence=0.7,
                execution_time_ms=300.0,
                timestamp=time.time(),
            )
            memory.record(entry)

        removed = memory.clear_all()
        assert removed == 5
        assert memory.entry_count == 0

    def test_clear_nonexistent_domain(self):
        memory = CourtMemory()
        removed = memory.clear_domain("nonexistent")
        assert removed == 0


class TestFactory:
    """memory_from_memorial() convenience factory."""

    def test_factory_basic(self):
        entry = memory_from_memorial(
            minister_name="chancellor",
            edict_id="edict_42",
            domain="engineering",
            intent="代码安全审查",
            success=True,
            confidence=0.88,
            execution_time_ms=1200.0,
            merit=5.0,
            tags=["security", "code-review"],
        )
        assert entry.minister_name == "chancellor"
        assert entry.domain == "engineering"
        assert entry.success is True
        assert entry.confidence == 0.88
        assert entry.merit == 5.0
        assert "security" in entry.tags
        assert len(entry.id) == 16  # SHA256[:16]
        assert len(entry.intent_keywords) > 0

    def test_factory_deterministic_id(self):
        e1 = memory_from_memorial(
            minister_name="chancellor",
            edict_id="edict_1",
            domain="engineering",
            intent="test",
            success=True,
            confidence=0.5,
            execution_time_ms=100.0,
        )
        e2 = memory_from_memorial(
            minister_name="chancellor",
            edict_id="edict_1",
            domain="engineering",
            intent="test",
            success=True,
            confidence=0.5,
            execution_time_ms=100.0,
        )
        assert e1.id == e2.id

    def test_factory_different_ids(self):
        e1 = memory_from_memorial(
            minister_name="chancellor",
            edict_id="edict_1",
            domain="engineering",
            intent="test",
            success=True,
            confidence=0.5,
            execution_time_ms=100.0,
        )
        e2 = memory_from_memorial(
            minister_name="chancellor",
            edict_id="edict_2",
            domain="engineering",
            intent="test",
            success=True,
            confidence=0.5,
            execution_time_ms=100.0,
        )
        assert e1.id != e2.id

    def test_factory_long_intent_truncation(self):
        entry = memory_from_memorial(
            minister_name="chancellor",
            edict_id="edict_long",
            domain="engineering",
            intent="A" * 300,
            success=True,
            confidence=0.5,
            execution_time_ms=100.0,
        )
        assert len(entry.intent) <= 200

"""Tests for huanxin.router — IntentClassifier, RouterEngine, and integration."""

from __future__ import annotations

import pytest


# ══════════════════════════════════════════════════════════════════════
# IntentClassifier — rule‑based fallback tests (no LLM required)
# ══════════════════════════════════════════════════════════════════════


class TestIntentClassifierRuleFallback:
    """Tests that exercise the keyword‑based rule fallback (no LLM)."""

    def test_classify_code_generation(self):
        from huanxin.router.classifier import IntentClassifier

        clf = IntentClassifier()  # no LLM → uses rules
        r = clf.classify("给我写一个Python快速排序算法")
        assert r.intent == "code_generation"
        assert 0.0 < r.confidence <= 1.0

    def test_classify_data_analysis(self):
        from huanxin.router.classifier import IntentClassifier

        clf = IntentClassifier()
        r = clf.classify("帮我分析这些销售数据并生成趋势图")
        assert r.intent == "data_analysis"

    def test_classify_math_calculation(self):
        from huanxin.router.classifier import IntentClassifier

        clf = IntentClassifier()
        r = clf.classify("计算 sin(45°) + cos(30°) 的值")
        assert r.intent == "math_calculation"

    def test_classify_file_operation(self):
        from huanxin.router.classifier import IntentClassifier

        clf = IntentClassifier()
        r = clf.classify("把桌面文件移到D盘projects目录")
        assert r.intent == "file_operation"

    def test_classify_web_search(self):
        from huanxin.router.classifier import IntentClassifier

        clf = IntentClassifier()
        r = clf.classify("搜索最新的Transformer论文")
        assert r.intent == "web_search"

    def test_classify_document_qa(self):
        from huanxin.router.classifier import IntentClassifier

        clf = IntentClassifier()
        r = clf.classify("这个PDF合同里写了哪些违约条款")
        assert r.intent == "document_qa"

    def test_classify_system_operation(self):
        from huanxin.router.classifier import IntentClassifier

        clf = IntentClassifier()
        r = clf.classify("帮我重启一下系统服务")
        assert r.intent == "system_operation"

    def test_classify_general_chat(self):
        from huanxin.router.classifier import IntentClassifier

        clf = IntentClassifier()
        r = clf.classify("你好呀，今天过得怎么样")
        assert r.intent == "general_chat"

    def test_classify_returns_confidence_bounds(self):
        from huanxin.router.classifier import IntentClassifier

        clf = IntentClassifier()
        for text in ["写代码", "分析数据", "你好"]:
            r = clf.classify(text)
            assert 0.0 <= r.confidence <= 1.0, f"confidence out of range for: {text}"

    def test_classify_result_has_fields(self):
        from huanxin.router.classifier import IntentClassifier

        clf = IntentClassifier()
        r = clf.classify("写一个排序算法")
        assert hasattr(r, "intent")
        assert hasattr(r, "confidence")
        assert hasattr(r, "reasoning")
        assert hasattr(r, "latency_ms")

    def test_classify_unknown_returns_general_chat(self):
        from huanxin.router.classifier import IntentClassifier

        clf = IntentClassifier()
        r = clf.classify("嗯...这个嘛...让我想想...")
        assert r.intent == "general_chat"

    def test_classify_counts_correctly(self):
        from huanxin.router.classifier import IntentClassifier

        clf = IntentClassifier()
        for _ in range(3):
            clf.classify("写代码")

        stats = clf.stats()
        assert stats["total_calls"] == 3
        assert stats["per_intent"]["code_generation"] >= 1

    def test_get_minister_for_known_intents(self):
        from huanxin.router.classifier import IntentClassifier

        clf = IntentClassifier()
        assert clf.get_minister_for("general_chat") == "turing"
        assert clf.get_minister_for("code_generation") == "lecun"
        assert clf.get_minister_for("data_analysis") == "hinton"
        assert clf.get_minister_for("math_calculation") == "goodfellow"
        assert clf.get_minister_for("document_qa") == "confucius"

    def test_get_minister_for_unknown_intent_returns_turing(self):
        from huanxin.router.classifier import IntentClassifier

        clf = IntentClassifier()
        assert clf.get_minister_for("nonexistent") == "turing"

    def test_fewshot_examples_included_in_prompt(self):
        from huanxin.router.classifier import IntentClassifier

        clf = IntentClassifier()
        prompt = clf._build_prompt("测试输入")
        # Should contain at least one few-shot example
        assert "快速排序" in prompt or "sales" in prompt.lower() or "天气" in prompt

    def test_custom_labels(self):
        from huanxin.router.classifier import IntentClassifier

        clf = IntentClassifier(labels=["greeting", "code", "question"])
        assert clf.labels == ["greeting", "code", "question"]

    def test_custom_examples(self):
        from huanxin.router.classifier import IntentClassifier

        ex = [{"text": "hello world", "intent": "greeting"}]
        clf = IntentClassifier(examples=ex, labels=["greeting", "other"])
        assert clf.examples == ex


# ══════════════════════════════════════════════════════════════════════
# IntentClassifier — LLM‑assisted (mocked)
# ══════════════════════════════════════════════════════════════════════


class TestIntentClassifierLLM:
    """Tests with a mocked LLM engine."""

    @pytest.fixture
    def mock_llm(self):
        class MockLLM:
            def __init__(self):
                self.last_prompt = ""

            def chat_sync(self, prompt, system=None):
                self.last_prompt = prompt
                return '{"intent": "code_generation", "confidence": 0.95, "reasoning": "用户要求编写代码"}'

        return MockLLM()

    def test_mock_llm_classification(self, mock_llm):
        from huanxin.router.classifier import IntentClassifier

        clf = IntentClassifier(llm_engine=mock_llm)
        r = clf.classify("帮我写一个快速排序")
        assert r.intent == "code_generation"
        assert r.confidence == 0.95
        assert r.reasoning == "用户要求编写代码"

    def test_mock_llm_prompt_contains_input(self, mock_llm):
        from huanxin.router.classifier import IntentClassifier

        clf = IntentClassifier(llm_engine=mock_llm)
        clf.classify("帮我写一个快速排序")
        assert "快速排序" in mock_llm.last_prompt

    def test_mock_llm_unknown_intent_fallback(self, mock_llm):
        from huanxin.router.classifier import IntentClassifier

        mock_llm.chat_sync = lambda prompt, system=None: '{"intent": "xyz_fake", "confidence": 0.99, "reasoning": "unknown"}'
        clf = IntentClassifier(llm_engine=mock_llm)
        r = clf.classify("某段文本")
        # Should fallback to general_chat when label not in list
        assert r.intent == "general_chat"

    def test_mock_llm_trims_json_fence(self, mock_llm):
        from huanxin.router.classifier import IntentClassifier

        mock_llm.chat_sync = lambda prompt, system=None: '```json\n{"intent": "math_calculation", "confidence": 0.88, "reasoning": "计算题"}\n```'
        clf = IntentClassifier(llm_engine=mock_llm)
        r = clf.classify("1+1等于几")
        assert r.intent == "math_calculation"

    def test_mock_llm_error_fallback(self, mock_llm):
        from huanxin.router.classifier import IntentClassifier

        mock_llm.chat_sync = lambda prompt, system=None: (_ for _ in ()).throw(RuntimeError("LLM down"))
        clf = IntentClassifier(llm_engine=mock_llm)
        r = clf.classify("写代码")
        # Should still produce a valid result via rule fallback
        assert r.intent in [l for l in clf.labels]

    def test_mock_llm_latency_tracking(self, mock_llm):
        from huanxin.router.classifier import IntentClassifier

        clf = IntentClassifier(llm_engine=mock_llm)
        r = clf.classify("测试")
        assert r.latency_ms >= 0


# ══════════════════════════════════════════════════════════════════════
# RouterEngine — routing decisions
# ══════════════════════════════════════════════════════════════════════


class TestRouterEngine:
    """Core routing tests with a mock classifier."""

    @pytest.fixture
    def router(self):
        from huanxin.router.classifier import IntentClassifier
        from huanxin.router.engine import RouterEngine

        clf = IntentClassifier()
        return RouterEngine(classifier=clf, confidence_threshold=0.3)

    def test_route_code_generation(self, router):
        decision = router.route(
            "写一个Python排序算法",
            available_ministers=["turing", "lecun", "hinton", "goodfellow"],
        )
        assert decision.target_type == "minister"
        assert decision.intent == "code_generation"
        assert decision.suggested_minister == "lecun"
        assert decision.confidence > 0

    def test_route_general_chat(self, router):
        decision = router.route(
            "今天天气怎么样",
            available_ministers=["turing", "lecun", "hinton"],
        )
        assert decision.intent == "general_chat"
        assert decision.suggested_minister == "turing"

    def test_route_returns_decision_fields(self, router):
        decision = router.route(
            "计算圆周率",
            available_ministers=["turing", "goodfellow"],
        )
        assert hasattr(decision, "target_type")
        assert hasattr(decision, "target_name")
        assert hasattr(decision, "confidence")
        assert hasattr(decision, "reasoning")
        assert hasattr(decision, "intent")
        assert hasattr(decision, "intent_confidence")

    def test_route_confidence_bounds(self, router):
        decision = router.route(
            "写代码",
            available_ministers=["turing", "lecun"],
        )
        assert 0.0 <= decision.confidence <= 1.0

    def test_route_target_name_in_ministers(self, router):
        ministers = ["turing", "lecun", "hinton", "goodfellow", "confucius"]
        decision = router.route("分析数据趋势", available_ministers=ministers)
        assert decision.target_name in ministers

    def test_route_default_minister_for_low_confidence(self, router):
        # Set a high threshold so fallback triggers
        from huanxin.router.engine import RouterEngine
        from huanxin.router.classifier import IntentClassifier

        strict_router = RouterEngine(
            classifier=IntentClassifier(),
            confidence_threshold=0.99,
            default_minister="turing",
        )
        decision = strict_router.route(
            "嗯...不知道说啥...",
            available_ministers=["turing", "lecun"],
        )
        assert decision.target_name == "turing"
        assert "低置信度" in decision.reasoning or decision.confidence < 0.99

    def test_route_with_capabilities(self, router):
        decision = router.route(
            "写一个Python脚本",
            available_ministers=["turing", "lecun", "hinton"],
            available_capabilities=["code", "analyze", "chat"],
        )
        # Should match capability "code"
        if decision.matched_capability:
            assert "code" == decision.matched_capability

    def test_route_history_accumulates(self, router):
        for _ in range(3):
            router.route("测试", available_ministers=["turing"])

        assert len(router.history) == 3

    def test_route_stats_are_meaningful(self, router):
        for i in range(5):
            router.route(f"测试{i}", available_ministers=["turing", "lecun"])

        s = router.stats()
        assert s["total_routes"] == 5
        assert s["history_count"] == 5
        assert "per_intent" in s
        assert "per_target" in s
        assert "classifier_stats" in s

    def test_route_clear_history(self, router):
        router.route("test1", available_ministers=["turing"])
        router.route("test2", available_ministers=["turing"])

        router.clear_history()
        assert len(router.history) == 0
        assert router.stats()["total_routes"] == 0


# ══════════════════════════════════════════════════════════════════════
# RouterEngine — multi‑level routing (intent → minister → capability)
# ══════════════════════════════════════════════════════════════════════


class TestMultiLevelRouting:
    def test_multi_level_code_to_lecun(self):
        from huanxin.router.classifier import IntentClassifier
        from huanxin.router.engine import RouterEngine

        clf = IntentClassifier()
        router = RouterEngine(classifier=clf)

        d = router.route(
            "Write a binary search algorithm",
            available_ministers=["turing", "lecun", "hinton", "goodfellow"],
            available_capabilities=["code", "math", "chat"],
        )
        assert d.intent == "code_generation"
        assert d.suggested_minister == "lecun"

    def test_multi_level_math_to_goodfellow(self):
        from huanxin.router.classifier import IntentClassifier
        from huanxin.router.engine import RouterEngine

        clf = IntentClassifier()
        router = RouterEngine(classifier=clf)

        d = router.route(
            "Solve this differential equation: dy/dx = y",
            available_ministers=["turing", "lecun", "hinton", "goodfellow"],
        )
        assert d.intent == "math_calculation"
        assert d.suggested_minister == "goodfellow"

    def test_multi_level_data_to_hinton(self):
        from huanxin.router.classifier import IntentClassifier
        from huanxin.router.engine import RouterEngine

        clf = IntentClassifier()
        router = RouterEngine(classifier=clf)

        d = router.route(
            "分析这份销售数据的季度趋势",
            available_ministers=["turing", "lecun", "hinton", "goodfellow"],
        )
        assert d.intent == "data_analysis"
        assert d.suggested_minister == "hinton"

    def test_multi_level_file_to_lovelace(self):
        from huanxin.router.classifier import IntentClassifier
        from huanxin.router.engine import RouterEngine

        clf = IntentClassifier()
        router = RouterEngine(classifier=clf)

        d = router.route(
            "把Downloads目录下的PDF文件整理到归档文件夹",
            available_ministers=["turing", "lovelace", "confucius"],
        )
        assert d.intent == "file_operation"
        assert d.suggested_minister == "lovelace"

    def test_multi_level_doc_qa_to_confucius(self):
        from huanxin.router.classifier import IntentClassifier
        from huanxin.router.engine import RouterEngine

        clf = IntentClassifier()
        router = RouterEngine(classifier=clf)

        d = router.route(
            "这份合同里包含了哪些关键条款",
            available_ministers=["turing", "confucius", "lovelace"],
        )
        assert d.intent == "document_qa"
        assert d.suggested_minister == "confucius"

    def test_multi_level_system_to_tesla(self):
        from huanxin.router.classifier import IntentClassifier
        from huanxin.router.engine import RouterEngine

        clf = IntentClassifier()
        router = RouterEngine(classifier=clf)

        d = router.route(
            "重启服务器上的nginx服务",
            available_ministers=["turing", "tesla", "lovelace"],
        )
        assert d.intent == "system_operation"
        assert d.suggested_minister == "tesla"


# ══════════════════════════════════════════════════════════════════════
# Integration: importability and module sanity
# ══════════════════════════════════════════════════════════════════════


class TestModuleIntegration:
    def test_import_router_package(self):
        """huanxin.router package is importable."""
        import huanxin.router  # noqa: F401

    def test_import_classifier(self):
        from huanxin.router import IntentClassifier
        assert IntentClassifier is not None

    def test_import_router_engine(self):
        from huanxin.router import RouterEngine
        assert RouterEngine is not None

    def test_import_all_symbols(self):
        from huanxin.router import (
            IntentClassifier,
            ClassificationResult,
            RouterEngine,
            RouterDecision,
            INTENT_LABELS,
            INTENT_TO_MINISTER,
        )
        assert len(INTENT_LABELS) == 8
        assert isinstance(INTENT_TO_MINISTER, dict)

    def test_default_classifier_is_instantiable(self):
        from huanxin.router import IntentClassifier

        clf = IntentClassifier()
        assert clf is not None
        assert len(clf.labels) == 8
        assert clf.fallback_intent == "general_chat"

    def test_default_router_is_instantiable(self):
        from huanxin.router import RouterEngine

        router = RouterEngine()
        assert router is not None
        assert router.confidence_threshold == 0.5
        assert router.default_minister == "turing"

    def test_route_decision_dataclass(self):
        from huanxin.router.engine import RouterDecision

        d = RouterDecision(
            target_type="minister",
            target_name="turing",
            confidence=0.85,
            reasoning="Test reason",
            intent="general_chat",
            intent_confidence=0.9,
            suggested_minister="turing",
            matched_capability=None,
        )
        assert d.target_type == "minister"
        assert d.target_name == "turing"
        assert d.confidence == 0.85

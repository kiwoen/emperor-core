"""
Tests for jarvis.evaluation.agent_eval — Per-Agent Eval Suite.

Covers:
    - SyntheticInputGenerator input generation per agent
    - AgentEvalSuite.run() produces valid EvalReport
    - EvalReport dimension computation correctness
    - _percentiles edge cases
    - Null fallback objects
    - eval_all_agents multiprocess
    - Accuracy / hallucination / tool call flow
"""

import math
import pytest
from unittest.mock import MagicMock, patch

# -- Module under test --
from jarvis.evaluation.agent_eval import (
    AgentEvalSuite,
    SyntheticInputGenerator,
    SyntheticInput,
    EvalReport,
    eval_all_agents,
    _percentiles,
    _BUILTIN_INPUTS,
    _simulate_agent_output,
    _estimate_token_cost,
    _NullHallucinationGuard,
    _NullJudge,
    _NullValidator,
    _check_tool_call,
)


# ══════════════════════════════════════════════════════════════════
# 1. SyntheticInput data class
# ══════════════════════════════════════════════════════════════════

class TestSyntheticInput:
    def test_default_values(self):
        si = SyntheticInput(label="test", input_text="hello")
        assert si.label == "test"
        assert si.input_text == "hello"
        assert si.expected_traits == []
        assert si.is_attack is False
        assert si.domain == ""

    def test_full_attributes(self):
        si = SyntheticInput(
            label="attack_case",
            input_text="inject",
            expected_traits=["danger", "block"],
            is_attack=True,
            domain="injection",
        )
        assert si.is_attack is True
        assert len(si.expected_traits) == 2
        assert si.domain == "injection"


# ══════════════════════════════════════════════════════════════════
# 2. SyntheticInputGenerator
# ══════════════════════════════════════════════════════════════════

class TestSyntheticInputGenerator:
    @pytest.mark.parametrize("agent_name", list(_BUILTIN_INPUTS.keys()))
    def test_generates_non_empty_for_each_agent(self, agent_name):
        inputs = SyntheticInputGenerator.generate(agent_name)
        assert isinstance(inputs, list)
        assert len(inputs) > 0
        for si in inputs:
            assert isinstance(si, SyntheticInput)
            assert si.label
            assert si.input_text

    def test_generates_empty_for_unknown_agent(self):
        inputs = SyntheticInputGenerator.generate("nonexistent_agent")
        assert inputs == []

    def test_custom_generator_registration(self):
        def custom():
            return [SyntheticInput(label="custom1", input_text="test")]
        SyntheticInputGenerator.register("custom_agent", custom)
        inputs = SyntheticInputGenerator.generate("custom_agent")
        assert len(inputs) == 1
        assert inputs[0].label == "custom1"
        # Cleanup
        SyntheticInputGenerator._GENERATORS.pop("custom_agent", None)

    def test_emperor_inputs_include_classification(self):
        inputs = SyntheticInputGenerator.generate("emperor")
        labels = {si.label for si in inputs}
        assert "task_classification_simple" in labels
        assert "task_classification_math" in labels

    def test_tool_guard_inputs_include_attacks(self):
        inputs = SyntheticInputGenerator.generate("tool_guard")
        attacks = [si for si in inputs if si.is_attack]
        assert len(attacks) >= 3


# ══════════════════════════════════════════════════════════════════
# 3. EvalReport
# ══════════════════════════════════════════════════════════════════

class TestEvalReport:
    def test_default_report(self):
        r = EvalReport(agent_name="test")
        assert r.agent_name == "test"
        assert r.total_cases == 0
        assert r.pass_rate == 0.0

    def test_pass_rate(self):
        r = EvalReport(agent_name="test", total_cases=10, passed=8, failed=2)
        assert r.pass_rate == 0.8

    def test_pass_rate_zero_attempts(self):
        r = EvalReport(agent_name="test", total_cases=0)
        assert r.pass_rate == 0.0

    def test_to_dict_returns_all_dimensions(self):
        r = EvalReport(
            agent_name="emperor",
            total_cases=5,
            passed=4,
            failed=1,
            accuracy=0.85,
            latency_p50=12.3,
            latency_p95=45.6,
            latency_p99=89.0,
            token_cost=0.001234,
            hallucination_rate=0.2,
            tool_call_success_rate=0.9,
        )
        d = r.to_dict()
        assert d["agent_name"] == "emperor"
        assert d["total_cases"] == 5
        assert d["accuracy"] == 0.85
        assert d["latency_p50_ms"] == 12.3
        assert d["latency_p95_ms"] == 45.6
        assert d["latency_p99_ms"] == 89.0
        assert d["token_cost_usd"] == 0.001234
        assert d["hallucination_rate"] == 0.2
        assert d["tool_call_success_rate"] == 0.9
        assert isinstance(d["per_case"], list)

    def test_summary_contains_key_fields(self):
        r = EvalReport(agent_name="router", total_cases=6, passed=5, failed=1,
                       accuracy=0.88, hallucination_rate=0.0)
        s = r.summary()
        assert "router" in s
        assert "88.00%" in s or "0.8800" in s
        assert "Hallu Rate" in s


# ══════════════════════════════════════════════════════════════════
# 4. AgentEvalSuite.run()
# ══════════════════════════════════════════════════════════════════

class TestAgentEvalSuite:
    def test_run_emperor_returns_valid_report(self):
        suite = AgentEvalSuite("emperor")
        report = suite.run()
        assert isinstance(report, EvalReport)
        assert report.agent_name == "emperor"
        assert report.total_cases > 0
        assert report.passed + report.failed == report.total_cases
        assert len(report.per_case_details) == report.total_cases

    def test_run_all_agents_produce_reports(self):
        for agent in sorted(_BUILTIN_INPUTS.keys()):
            suite = AgentEvalSuite(agent)
            report = suite.run()
            assert report.total_cases > 0, f"{agent} has no cases"
            assert report.accuracy > 0, f"{agent} accuracy is zero"

    def test_run_unknown_agent_empty_report(self):
        suite = AgentEvalSuite("no_such_agent")
        report = suite.run()
        assert report.total_cases == 0
        assert report.agent_name == "no_such_agent"

    def test_per_case_details_format(self):
        suite = AgentEvalSuite("emperor")
        report = suite.run()
        for case in report.per_case_details:
            assert "label" in case
            assert "status" in case
            assert case["status"] in ("pass", "fail")
            assert "accuracy" in case
            assert "latency_ms" in case
            assert "token_cost_est" in case

    def test_accuracy_dimension_present(self):
        suite = AgentEvalSuite("router")
        report = suite.run()
        assert 0.0 <= report.accuracy <= 1.0

    def test_latency_percentiles_in_report(self):
        suite = AgentEvalSuite("emperor")
        report = suite.run()
        assert report.latency_p50 >= 0
        assert report.latency_p95 >= report.latency_p50
        assert report.latency_p99 >= report.latency_p95

    def test_hallucination_rate_is_bounded(self):
        suite = AgentEvalSuite("hallucination_guard")
        report = suite.run()
        assert 0.0 <= report.hallucination_rate <= 1.0

    def test_tool_call_success_rate_for_validator(self):
        suite = AgentEvalSuite("validator")
        report = suite.run()
        assert 0.0 <= report.tool_call_success_rate <= 1.0

    # ── Integration: with real HallucinationGuard ──

    def test_with_shared_hallucination_guard(self):
        try:
            from jarvis.hallucination_guard import HallucinationGuard
            hg = HallucinationGuard()
        except ImportError:
            pytest.skip("HallucinationGuard not available")

        suite = AgentEvalSuite("hallucination_guard", hallucination_guard=hg)
        report = suite.run()
        assert report.total_cases > 0

    # ── Integration: with real LLMJudge ──

    def test_with_shared_judge(self):
        try:
            from jarvis.llm_judge import LLMJudge
            judge = LLMJudge()
        except ImportError:
            pytest.skip("LLMJudge not available")

        suite = AgentEvalSuite("emperor", judge=judge)
        report = suite.run()
        assert report.accuracy >= 0.0


# ══════════════════════════════════════════════════════════════════
# 5. eval_all_agents convenience
# ══════════════════════════════════════════════════════════════════

class TestEvalAllAgents:
    def test_all_agents_returned(self):
        reports = eval_all_agents()
        assert set(reports.keys()) == set(_BUILTIN_INPUTS.keys())
        for name, report in reports.items():
            assert report.agent_name == name
            assert report.total_cases > 0

    def test_subset_of_agents(self):
        reports = eval_all_agents(agent_names=["emperor", "router"])
        assert set(reports.keys()) == {"emperor", "router"}

    def test_empty_list(self):
        reports = eval_all_agents(agent_names=[])
        assert reports == {}


# ══════════════════════════════════════════════════════════════════
# 6. _percentiles helper
# ══════════════════════════════════════════════════════════════════

class TestPercentiles:
    def test_empty_list(self):
        assert _percentiles([]) == (0.0, 0.0, 0.0)

    def test_single_value(self):
        p50, p95, p99 = _percentiles([42.0])
        assert p50 == 42.0
        assert p95 == 42.0
        assert p99 == 42.0

    def test_evenly_spaced(self):
        values = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
        p50, p95, p99 = _percentiles(values)
        assert 50 <= p50 <= 55  # median between 50 and 60
        assert 90 <= p95 <= 100
        assert 95 <= p99 <= 100

    def test_order_invariant(self):
        import random
        values = [random.uniform(1, 100) for _ in range(100)]
        shuffled = sorted(values, key=lambda _: random.random())
        assert _percentiles(sorted(values)) == _percentiles(shuffled)


# ══════════════════════════════════════════════════════════════════
# 7. _simulate_agent_output
# ══════════════════════════════════════════════════════════════════

class TestSimulateAgentOutput:
    def test_known_agent_returns_expected_output(self):
        inp = SyntheticInput(label="task_classification_simple", input_text="test")
        out = _simulate_agent_output("emperor", inp)
        assert "weather" in out

    def test_unknown_agent_fallback(self):
        inp = SyntheticInput(label="anything", input_text="test")
        out = _simulate_agent_output("nonexistent", inp)
        assert "nonexistent" in out


# ══════════════════════════════════════════════════════════════════
# 8. _estimate_token_cost
# ══════════════════════════════════════════════════════════════════

class TestEstimateTokenCost:
    def test_empty_strings(self):
        cost = _estimate_token_cost("", "")
        assert cost == 0.0

    def test_english_only(self):
        cost = _estimate_token_cost("Hello world", "Goodbye world")
        assert cost > 0.0

    def test_chinese_more_expensive_per_char(self):
        # Same character count: 10 CJK vs 10 ASCII
        # CJK: ~2 chars/token → 20 chars / 2 = 10 tokens → $0.000020
        # ASCII: ~4 chars/token → 20 chars / 4 = 5 tokens → $0.000010
        eng_cost = _estimate_token_cost("xxxxxxxxxx", "yyyyyyyyyy")
        chn_cost = _estimate_token_cost("你好世界你好世界你好", "好的好的好的好的哦")
        assert chn_cost > eng_cost


# ══════════════════════════════════════════════════════════════════
# 9. Null fallback objects
# ══════════════════════════════════════════════════════════════════

class TestNullObjects:
    def test_null_guard_does_not_crash(self):
        ng = _NullHallucinationGuard()
        result = ng.check("any text")
        assert result.has_hallucinations is False

    def test_null_judge_returns_neutral(self):
        nj = _NullJudge()
        result = nj.evaluate("a", "b")
        assert result.score == 0.5

    def test_null_validator_does_not_crash(self):
        nv = _NullValidator()
        assert nv.list_tools() == []
        nv.register("test", None)
        assert nv.validate("test", {"x": 1}) == {"x": 1}


# ══════════════════════════════════════════════════════════════════
# 10. _check_tool_call
# ══════════════════════════════════════════════════════════════════

class TestCheckToolCall:
    def test_valid_call_passes(self):
        from jarvis.tools.validator import ToolCallValidator
        v = ToolCallValidator()
        inp = SyntheticInput(label="valid", input_text='tool="search" params={"query":"test","limit":5}')
        ok, msg = _check_tool_call(inp, v)
        assert ok, f"Expected pass, got: {msg}"

    def test_missing_required_fails(self):
        from jarvis.tools.validator import ToolCallValidator
        v = ToolCallValidator()
        inp = SyntheticInput(label="invalid", input_text='tool="search" params={"limit":5}')
        ok, msg = _check_tool_call(inp, v)
        assert not ok, "Expected fail for missing required param"

    def test_unknown_tool_fails(self):
        from jarvis.tools.validator import ToolCallValidator
        v = ToolCallValidator()
        inp = SyntheticInput(label="unknown", input_text='tool="fly_to_moon" params={}')
        ok, _ = _check_tool_call(inp, v)
        assert not ok

    def test_wrong_type_fails(self):
        from jarvis.tools.validator import ToolCallValidator
        v = ToolCallValidator()
        inp = SyntheticInput(label="type_err", input_text='tool="delete" params={"path":123}')
        ok, _ = _check_tool_call(inp, v)
        assert not ok

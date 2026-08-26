"""
Tests for huanxin.eval — Evaluation Framework (package).

Covers:
    - huanxin.eval.runner.EvalRunner: run_benchmark / run_custom_cases / report
    - huanxin.eval.metrics: all 6 metrics + compute_all_metrics
    - huanxin.eval.benchmarks: all 4 built-in benchmarks
    - huanxin.eval.__init__: backward compatibility (legacy imports)
    - AggregateReport: ranking, to_dict
    - Edge cases: empty inputs, unknown benchmarks, error handling
"""

import json
import time
import pytest
from unittest.mock import MagicMock, patch

# ── Package imports ──
from huanxin.eval import (
    EvalCase,
    EvalSuite,
    EvalStatus,
    EvalResult,
    SuiteResult,
    EvalRunner,
    JudgeEvalCase,
    JudgeEvalSuite,
)
from huanxin.eval.metrics import (
    task_success_rate,
    tool_call_accuracy,
    response_time_ms,
    route_accuracy,
    healing_success_rate,
    evolution_convergence,
    compute_all_metrics,
    MetricsReport,
)
from huanxin.eval.benchmarks import (
    BenchmarkCase,
    BenchmarkResult,
    HuanxinBench,
    RouterBench,
    MultiStepBench,
    SelfHealingBench,
    ALL_BENCHMARKS,
    get_benchmark,
)
from huanxin.eval.runner import AggregateReport


# ══════════════════════════════════════════════════════════════════
# 1. Backward Compatibility — Legacy EvalSuite imports
# ══════════════════════════════════════════════════════════════════

class TestLegacyCompatibility:
    """验证升级为 package 后原有导入仍可用。"""

    def test_evalcase_dataclass(self):
        case = EvalCase("test", "prompt", capability="math",
                        expected_keys=["value"])
        assert case.name == "test"
        assert case.capability == "math"
        assert case.expected_keys == ["value"]

    def test_evalsuite_construction(self):
        suite = EvalSuite("my_suite", [
            EvalCase("c1", "p1", capability="datetime", expected_keys=["date"]),
            EvalCase("c2", "p2", capability="math", expected_keys=["value"]),
        ])
        assert suite.name == "my_suite"
        assert len(suite.cases) == 2

    def test_evalsuite_add_fluent(self):
        suite = EvalSuite("test").add(EvalCase("a", "pa")).add(EvalCase("b", "pb"))
        assert len(suite.cases) == 2

    def test_judge_eval_case(self):
        jc = JudgeEvalCase("label1", output="hi", expected="hello")
        assert jc.label == "label1"
        assert jc.output == "hi"

    def test_judge_eval_suite(self):
        suite = JudgeEvalSuite("judge_test", [JudgeEvalCase("c1", "a", "b")])
        suite.add(JudgeEvalCase("c2", "x", "y"))
        assert len(suite.cases) == 2

    def test_evalstatus_enum(self):
        assert EvalStatus.PASS.value == "pass"
        assert EvalStatus.FAIL.value == "fail"

    def test_evalresult_dataclass(self):
        r = EvalResult("case1", EvalStatus.PASS, duration_ms=12.3)
        assert r.status == EvalStatus.PASS

    def test_suiteresult_properties(self):
        sr = SuiteResult("s")
        sr.passed = 8
        sr.failed = 2
        assert sr.pass_rate == 0.8


# ══════════════════════════════════════════════════════════════════
# 2. EvalRunner — run_benchmark
# ══════════════════════════════════════════════════════════════════

class TestEvalRunnerRunBenchmark:
    """测试增强版 EvalRunner 的 run_benchmark 方法。"""

    def test_run_huanxin_bench_no_agent(self):
        """脱机模式（无 agent）：应验证用例构造并全部通过。"""
        runner = EvalRunner()
        result = runner.run_benchmark("huanxin")
        assert isinstance(result, BenchmarkResult)
        assert result.name == "HuanxinBench"
        assert result.total_cases == 20
        # 脱机模式：不实际调用 agent，所有 case 应 pass
        assert result.passed == 20
        assert result.pass_rate == 1.0

    def test_run_router_bench_no_agent(self):
        runner = EvalRunner()
        result = runner.run_benchmark("router")
        assert result.name == "RouterBench"
        assert result.total_cases == 16  # 8 domains × 2 cases
        assert result.passed == 16

    def test_run_multistep_bench_no_agent(self):
        runner = EvalRunner()
        result = runner.run_benchmark("multistep")
        assert result.total_cases == 10
        assert result.passed == 10

    def test_run_selfhealing_bench_no_agent(self):
        runner = EvalRunner()
        result = runner.run_benchmark("selfhealing")
        assert result.total_cases == 8
        assert result.passed == 8

    def test_unknown_benchmark_raises(self):
        runner = EvalRunner()
        with pytest.raises(ValueError, match="Unknown benchmark"):
            runner.run_benchmark("nonexistent_bench")

    def test_run_benchmark_returns_report_with_metrics(self):
        runner = EvalRunner()
        result = runner.run_benchmark("huanxin")
        assert result.metrics is not None
        assert 0.0 <= result.metrics.overall_score() <= 1.0
        assert len(result.to_dict()["per_case"]) == result.total_cases

    def test_all_available_benchmarks(self):
        """每个内置基准都能正常构建并运行。"""
        runner = EvalRunner()
        for name in ["huanxin", "router", "multistep", "selfhealing"]:
            result = runner.run_benchmark(name)
            assert result.total_cases > 0, f"{name} has zero cases"
            assert result.passed > 0, f"{name} has zero passes"

    def test_run_with_mock_agent(self):
        """使用 mock agent 验证 agent 输出被记录。"""
        mock_agent = MagicMock()
        mock_agent.handle = MagicMock(return_value={"data": {"value": 42}})
        runner = EvalRunner()
        result = runner.run_benchmark("huanxin", agent=mock_agent)
        # 确认 agent.handle 被调用
        assert mock_agent.handle.call_count == 20
        # 确认结果中包含 agent_output
        for case in result.per_case:
            assert "agent_output" in case


# ══════════════════════════════════════════════════════════════════
# 3. EvalRunner — run_custom_cases
# ══════════════════════════════════════════════════════════════════

class TestEvalRunnerCustomCases:
    """测试 run_custom_cases 方法。"""

    def test_custom_cases_from_dicts(self):
        runner = EvalRunner()
        result = runner.run_custom_cases([
            {"label": "custom_math", "prompt": "1+1", "expected_route": "math"},
            {"label": "custom_wx", "prompt": "天气", "expected_route": "weather"},
        ])
        assert result.name == "custom"
        assert result.total_cases == 2
        assert result.passed == 2

    def test_custom_cases_from_benchmarkcase(self):
        runner = EvalRunner()
        cases = [
            BenchmarkCase("custom1", "prompt1", capability="math", expected_route="math"),
            BenchmarkCase("custom2", "prompt2", capability="datetime", expected_route="datetime"),
        ]
        result = runner.run_custom_cases(cases, name="my_bench")
        assert result.name == "my_bench"
        assert result.total_cases == 2

    def test_custom_cases_with_validator(self):
        def my_validator(output):
            return True, "ok"

        runner = EvalRunner()
        result = runner.run_custom_cases([
            {"label": "v1", "prompt": "test", "validator": my_validator},
        ])
        assert result.passed == 1

    def test_custom_cases_bad_type_raises(self):
        runner = EvalRunner()
        with pytest.raises(TypeError):
            runner.run_custom_cases(["not_a_dict_or_case"])  # type: ignore


# ══════════════════════════════════════════════════════════════════
# 4. EvalRunner — run_all_benchmarks
# ══════════════════════════════════════════════════════════════════

class TestEvalRunnerRunAll:
    def test_run_all_benchmarks(self):
        runner = EvalRunner()
        agg = runner.run_all_benchmarks()
        assert isinstance(agg, AggregateReport)
        assert agg.total_benchmarks == 4
        assert len(agg.reports) == 4
        assert 0.0 <= agg.overall_score <= 1.0

    def test_run_all_ranking(self):
        runner = EvalRunner()
        agg = runner.run_all_benchmarks()
        ranking = agg.ranking
        assert len(ranking) == 4
        assert ranking[0]["rank"] == 1
        # 排名应按分数降序
        for i in range(len(ranking) - 1):
            assert ranking[i]["overall_score"] >= ranking[i + 1]["overall_score"]

    def test_run_all_with_agent(self):
        mock_agent = MagicMock()
        mock_agent.handle = MagicMock(return_value={"status": "ok"})
        runner = EvalRunner()
        agg = runner.run_all_benchmarks(agent=mock_agent)
        assert agg.total_benchmarks == 4

    def test_aggregate_to_dict(self):
        runner = EvalRunner()
        runner.run_all_benchmarks()
        d = runner.report_json()
        data = json.loads(d)
        assert "overall_score" in data
        assert "ranking" in data
        assert "benchmarks" in data
        assert len(data["benchmarks"]) == 4


# ══════════════════════════════════════════════════════════════════
# 5. Reports — JSON / Markdown
# ══════════════════════════════════════════════════════════════════

class TestReports:
    def test_report_json_returns_string(self):
        runner = EvalRunner()
        runner.run_benchmark("huanxin")
        json_str = runner.report_json()
        assert isinstance(json_str, str)
        data = json.loads(json_str)
        assert "overall_score" in data
        assert "ranking" in data

    def test_report_markdown_returns_string(self):
        runner = EvalRunner()
        runner.run_benchmark("huanxin")
        md = runner.report_markdown()
        assert "# HUANXIN Evaluation Report" in md
        assert "## Summary" in md
        assert "## Ranking" in md
        assert "### HuanxinBench" in md or "huanxin" in md.lower()

    def test_report_markdown_with_all_benchmarks(self):
        runner = EvalRunner()
        runner.run_all_benchmarks()
        md = runner.report_markdown()
        assert "HuanxinBench" in md or "huanxin" in md.lower()
        assert "RouterBench" in md or "router" in md.lower()

    def test_scores_by_dimension(self):
        runner = EvalRunner()
        runner.run_benchmark("huanxin")
        scores = runner.scores_by_dimension("huanxin")
        assert "task_success_rate" in scores
        assert "overall_score" in scores

    def test_all_scores(self):
        runner = EvalRunner()
        runner.run_benchmark("huanxin")
        runner.run_benchmark("router")
        all_s = runner.all_scores()
        assert "huanxin" in all_s
        assert "router" in all_s

    def test_history_and_reset(self):
        runner = EvalRunner()
        runner.run_benchmark("huanxin")
        assert "huanxin" in runner.history
        runner.reset()
        assert len(runner.history) == 0


# ══════════════════════════════════════════════════════════════════
# 6. Metrics — Task Success Rate
# ══════════════════════════════════════════════════════════════════

class TestTaskSuccessRate:
    def test_all_pass(self):
        assert task_success_rate([True, True, True]) == 1.0

    def test_all_fail(self):
        assert task_success_rate([False, False]) == 0.0

    def test_mixed(self):
        assert task_success_rate([True, False, True, False]) == 0.5

    def test_empty(self):
        assert task_success_rate([]) == 0.0

    def test_single_pass(self):
        assert task_success_rate([True]) == 1.0


# ══════════════════════════════════════════════════════════════════
# 7. Metrics — Tool Call Accuracy
# ══════════════════════════════════════════════════════════════════

class TestToolCallAccuracy:
    def test_all_match(self):
        assert tool_call_accuracy(
            ["search", "math", "hash"],
            ["search", "math", "hash"],
        ) == 1.0

    def test_none_match(self):
        assert tool_call_accuracy(
            ["search", "math"],
            ["weather", "hash"],
        ) == 0.0

    def test_partial_match(self):
        assert tool_call_accuracy(
            ["search", "math", "hash"],
            ["search", "wrong", "hash"],
        ) == 2 / 3

    def test_empty(self):
        assert tool_call_accuracy([], []) == 0.0

    def test_length_mismatch(self):
        """仅比对共同长度部分（zip 行为）。"""
        r = tool_call_accuracy(["a"], ["a", "b"])
        assert r == 1.0


# ══════════════════════════════════════════════════════════════════
# 8. Metrics — Response Time
# ══════════════════════════════════════════════════════════════════

class TestResponseTimeMs:
    def test_mean(self):
        assert response_time_ms([10, 20, 30], "mean") == 20.0

    def test_median_odd(self):
        assert response_time_ms([10, 50, 30], "median") == 30.0

    def test_median_even(self):
        assert response_time_ms([10, 30, 40, 60], "median") == 35.0

    def test_p95(self):
        times = list(range(1, 101))  # 1..100
        assert response_time_ms(times, "p95") >= 95

    def test_p99(self):
        times = list(range(1, 101))
        assert response_time_ms(times, "p99") >= 99

    def test_max(self):
        assert response_time_ms([1, 5, 3], "max") == 5.0

    def test_empty(self):
        assert response_time_ms([], "mean") == 0.0
        assert response_time_ms([], "p95") == 0.0

    def test_unknown_aggregator(self):
        with pytest.raises(ValueError):
            response_time_ms([1, 2], "unknown")


# ══════════════════════════════════════════════════════════════════
# 9. Metrics — Route Accuracy
# ══════════════════════════════════════════════════════════════════

class TestRouteAccuracy:
    def test_all_correct(self):
        assert route_accuracy(
            ["creator", "research", "math"],
            ["creator", "research", "math"],
        ) == 1.0

    def test_half_correct(self):
        assert route_accuracy(
            ["creator", "research", "math", "security"],
            ["creator", "wrong", "math", "wrong"],
        ) == 0.5

    def test_empty(self):
        assert route_accuracy([], []) == 0.0


# ══════════════════════════════════════════════════════════════════
# 10. Metrics — Healing Success Rate
# ══════════════════════════════════════════════════════════════════

class TestHealingSuccessRate:
    def test_all_healed(self):
        assert healing_success_rate([True, True, True]) == 1.0

    def test_none_healed(self):
        assert healing_success_rate([False, False]) == 0.0

    def test_no_depths(self):
        assert healing_success_rate([True, False, True]) == 2 / 3

    def test_weighted_shallow_success(self):
        """primary depth=0 成功权重 1.0。"""
        r = healing_success_rate([True, False], strategy_depths=[0, 0])
        assert r == 0.5

    def test_weighted_deep_fallback(self):
        """depth=4 成功权重 0.2。"""
        r = healing_success_rate([True], strategy_depths=[4])
        assert r == 0.2

    def test_empty(self):
        assert healing_success_rate([]) == 0.0


# ══════════════════════════════════════════════════════════════════
# 11. Metrics — Evolution Convergence
# ══════════════════════════════════════════════════════════════════

class TestEvolutionConvergence:
    def test_first_gen_convergence(self):
        scores = [[0.96], [0.97], [0.98]]
        r = evolution_convergence(scores, threshold=0.95)
        assert r == 1.0  # 第1代就达标

    def test_third_gen_convergence(self):
        scores = [[0.5], [0.7], [0.96], [0.99]]
        r = evolution_convergence(scores, threshold=0.95)
        # 第3代(index=2)达标，共4代
        assert r == 1.0 - (2 / 4) * 0.9  # 0.55

    def test_never_converges(self):
        scores = [[0.3], [0.4], [0.5]]
        r = evolution_convergence(scores, threshold=0.95)
        # 最高0.5，阈值0.95
        assert r == (0.5 / 0.95) * 0.5

    def test_empty(self):
        assert evolution_convergence([], threshold=0.95) == 0.0

    def test_empty_gen(self):
        """某代无数据：分数为0.0。"""
        scores = [[0.96], [], [0.97]]
        r = evolution_convergence(scores, threshold=0.95)
        assert r == 1.0  # 第1代[0.96] >= 0.95


# ══════════════════════════════════════════════════════════════════
# 12. Metrics — compute_all_metrics
# ══════════════════════════════════════════════════════════════════

class TestComputeAllMetrics:
    def test_minimal_input(self):
        report = compute_all_metrics(passed=[True, True, False])
        assert isinstance(report, MetricsReport)
        assert report.task_success_rate == 2 / 3
        assert report.tool_call_accuracy == 0.0  # no data
        assert report.overall_score() > 0.0

    def test_full_input(self):
        report = compute_all_metrics(
            passed=[True, True, True, False],
            expected_tools=["search", "math", "hash", "weather"],
            actual_tools=["search", "math", "wrong", "wrong"],
            times_ms=[10, 20, 30, 40],
            expected_routes=["research", "math", "hash", "weather"],
            actual_routes=["research", "math", "wrong", "wrong"],
            healing_results=[True, True, False, False],
            evolution_scores=[[0.9], [0.96]],
        )
        assert report.task_success_rate == 0.75
        assert report.tool_call_accuracy == 0.5
        assert report.route_accuracy == 0.5
        assert report.response_time_mean_ms > 0
        assert report.healing_success_rate == 0.5
        assert report.evolution_convergence > 0.0

    def test_overall_score_bounds(self):
        report = compute_all_metrics(
            passed=[True] * 10,
            expected_tools=["t"] * 10,
            actual_tools=["t"] * 10,
            times_ms=[50] * 10,
            expected_routes=["r"] * 10,
            actual_routes=["r"] * 10,
            healing_results=[True] * 10,
            evolution_scores=[[0.98]],
        )
        score = report.overall_score()
        assert 0.0 <= score <= 1.0
        # 全部满分应接近 1.0
        assert score > 0.85

    def test_to_dict(self):
        report = compute_all_metrics(passed=[True, False])
        d = report.to_dict()
        assert "overall_score" in d
        assert "task_success_rate" in d


# ══════════════════════════════════════════════════════════════════
# 13. Benchmarks — Case Construction
# ══════════════════════════════════════════════════════════════════

class TestBenchmarksConstruction:
    def test_huanxin_bench_cases(self):
        bench = HuanxinBench()
        cases = bench.build_cases()
        assert len(cases) == 20

        # 验证覆盖所有 12 种能力
        caps = set(c.capability for c in cases if c.capability)
        assert len(caps) == 12, f"Expected 12 capabilities, got {len(caps)}: {caps}"

    def test_router_bench_cases(self):
        bench = RouterBench()
        cases = bench.build_cases()
        assert len(cases) == 16

        # 验证覆盖 8 种意图
        routes = set(c.expected_route for c in cases)
        expected_routes = {"creator", "engineering", "research", "personal",
                           "security", "health", "finance", "home"}
        assert routes == expected_routes, f"Expected {expected_routes}, got {routes}"

    def test_multistep_bench_cases(self):
        bench = MultiStepBench()
        cases = bench.build_cases()
        assert len(cases) == 10

        # 全部需要 >= 2 工具调用
        for c in cases:
            if c.expected_tool:
                assert c.min_tools_required >= 2, \
                    f"Case {c.label} has min_tools_required={c.min_tools_required}"

    def test_selfhealing_bench_cases(self):
        bench = SelfHealingBench()
        cases = bench.build_cases()
        assert len(cases) == 8

        # 全部是故障注入
        for c in cases:
            assert c.is_fault_injection, f"Case {c.label} not marked as fault injection"
            assert c.fault_type != "", f"Case {c.label} has no fault_type"

    def test_all_benchmarks_registry(self):
        assert set(ALL_BENCHMARKS.keys()) == {"huanxin", "router", "multistep", "selfhealing"}

    def test_get_benchmark(self):
        assert isinstance(get_benchmark("huanxin"), HuanxinBench)
        assert isinstance(get_benchmark("router"), RouterBench)
        assert get_benchmark("nonexistent") is None


# ══════════════════════════════════════════════════════════════════
# 14. Benchmark Execution — No Agent (offline mode)
# ══════════════════════════════════════════════════════════════════

class TestBenchmarkRunNoAgent:
    def test_huanxin_bench_run(self):
        bench = HuanxinBench()
        result = bench.run()
        assert result.total_cases == 20
        assert result.passed == 20
        assert result.pass_rate == 1.0
        assert result.duration_seconds >= 0

    def test_router_bench_run(self):
        bench = RouterBench()
        result = bench.run()
        assert result.total_cases == 16

    def test_multistep_bench_run(self):
        bench = MultiStepBench()
        result = bench.run()
        assert result.total_cases == 10

    def test_selfhealing_bench_run(self):
        bench = SelfHealingBench()
        result = bench.run()
        assert result.total_cases == 8


# ══════════════════════════════════════════════════════════════════
# 15. Benchmark Execution — With Mock Agent
# ══════════════════════════════════════════════════════════════════

class TestBenchmarkRunWithAgent:
    def test_huanxin_with_agent(self):
        mock = MagicMock()
        mock.handle = MagicMock(return_value={"data": {"date": "2025-01-01"}})
        bench = HuanxinBench()
        result = bench.run(agent=mock)
        assert result.total_cases == 20
        # 自定义 validator 仅校验日期/数学范围等的，简单返回应通过
        assert result.passed >= 15  # 大多数应通过

    def test_router_with_agent(self):
        mock = MagicMock()
        mock.handle = MagicMock(return_value={"route": "creator"})
        bench = RouterBench()
        result = bench.run(agent=mock)
        assert result.total_cases == 16

    def test_selfhealing_with_agent(self):
        mock = MagicMock()
        # healing validator 查找积极信号，返回 fallback 相关字符串即可通过
        mock.handle = MagicMock(return_value="Error detected, initiating fallback recovery")
        bench = SelfHealingBench()
        result = bench.run(agent=mock)
        assert result.passed == 8  # 全部通过（有积极信号）


# ══════════════════════════════════════════════════════════════════
# 16. AggregateReport
# ══════════════════════════════════════════════════════════════════

class TestAggregateReport:
    def test_empty_aggregate(self):
        agg = AggregateReport(total_benchmarks=0, reports=[])
        assert agg.overall_score == 0.0
        assert agg.overall_pass_rate == 0.0
        assert agg.ranking == []

    def test_single_report_aggregate(self):
        runner = EvalRunner()
        result = runner.run_benchmark("huanxin")
        agg = AggregateReport(
            total_benchmarks=1,
            reports=[result],
        )
        assert agg.overall_score > 0.0
        assert len(agg.ranking) == 1
        assert agg.ranking[0]["rank"] == 1
        assert agg.ranking[0]["name"] == "HuanxinBench"

    def test_aggregate_to_dict(self):
        runner = EvalRunner()
        runner.run_benchmark("huanxin")
        runner.run_benchmark("router")
        d = runner.report_json()
        data = json.loads(d)
        assert data["total_benchmarks"] == 2


# ══════════════════════════════════════════════════════════════════
# 17. Register Custom Benchmark
# ══════════════════════════════════════════════════════════════════

class TestRegisterBenchmark:
    def test_register_and_run(self):
        from huanxin.eval.benchmarks import Benchmark, BenchmarkCase as BC

        class MiniBench(Benchmark):
            name = "mini"
            description = "A tiny benchmark"

            def build_cases(self):
                return [BC("m1", "test", expected_route="test")]

        runner = EvalRunner()
        runner.register_benchmark("mini", MiniBench())
        result = runner.run_benchmark("mini")
        assert result.name == "mini"
        assert result.total_cases == 1
        assert result.passed == 1

        # Clean up
        ALL_BENCHMARKS.pop("mini", None)


# ══════════════════════════════════════════════════════════════════
# 18. Edge Cases
# ══════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_metrics_empty_passed(self):
        report = compute_all_metrics(passed=[])
        assert report.task_success_rate == 0.0
        assert report.overall_score() > 0.0  # 至少有 weight * 0 contribution

    def test_runner_history_empty_before_run(self):
        runner = EvalRunner()
        assert len(runner.history) == 0

    def test_scores_by_dimension_unknown_benchmark(self):
        runner = EvalRunner()
        assert runner.scores_by_dimension("unknown") == {}

    def test_benchmarkcase_default_values(self):
        case = BenchmarkCase("label", "prompt")
        assert case.capability == ""
        assert case.domain == ""
        assert case.expected_tool == ""
        assert case.min_tools_required == 1
        assert case.is_fault_injection is False

    def test_benchmarkresult_default_values(self):
        result = BenchmarkResult(name="test", description="desc")
        assert result.total_cases == 0
        assert result.pass_rate == 0.0

    def test_markdown_report_before_run(self):
        """空运行情况下的 Markdown 报告。"""
        runner = EvalRunner()
        md = runner.report_markdown()
        assert "# HUANXIN Evaluation Report" in md

    def test_json_report_before_run(self):
        runner = EvalRunner()
        data = json.loads(runner.report_json())
        assert data["total_benchmarks"] == 0

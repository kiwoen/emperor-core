"""
Enhanced EvalRunner — 增强版评测运行器，支持 Benchmark 评测流水线。

Capabilities:
    a. run_benchmark(benchmark_name, agent) — 运行命名基准测试
    b. run_custom_cases(cases, agent)        — 支持自定义测试用例
    c. 结果汇总：总分、各维度分、排名
    d. 生成评测报告（JSON / Markdown 格式）

与 __init__.py 中的 _LegacyEvalRunner 的对比：
    _LegacyEvalRunner — 传统 EvalSuite 方式（保留向后兼容）
    EvalRunner         — 增强版，支持 Benchmark + 多指标汇总

Usage:
    from jarvis.eval import EvalRunner

    runner = EvalRunner()
    runner.run_benchmark("jarvis")
    runner.run_benchmark("router", agent=my_agent)
    print(runner.report_markdown())
"""

from __future__ import annotations

import json as _json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from jarvis.eval.metrics import MetricsReport, compute_all_metrics
from jarvis.eval.benchmarks import (
    BenchmarkCase,
    BenchmarkResult,
    JarvisBench,
    RouterBench,
    MultiStepBench,
    SelfHealingBench,
    ALL_BENCHMARKS,
    get_benchmark,
)


# ══════════════════════════════════════════════════════════════════
# Enhanced EvalRunner
# ══════════════════════════════════════════════════════════════════


@dataclass
class AggregateReport:
    """多基准聚合报告。"""

    total_benchmarks: int = 0
    reports: List[BenchmarkResult] = field(default_factory=list)
    started_at: float = 0
    finished_at: float = 0

    @property
    def overall_score(self) -> float:
        """所有基准的加权平均总分。"""
        if not self.reports:
            return 0.0
        return sum(r.metrics.overall_score() for r in self.reports) / len(self.reports)

    @property
    def overall_pass_rate(self) -> float:
        """所有基准的总通过率。"""
        if not self.reports:
            return 0.0
        total_pass = sum(r.passed for r in self.reports)
        total_cases = sum(r.total_cases for r in self.reports)
        return total_pass / total_cases if total_cases > 0 else 0.0

    @property
    def duration_seconds(self) -> float:
        return round(self.finished_at - self.started_at, 3)

    @property
    def ranking(self) -> List[Dict[str, Any]]:
        """按 overall_score 降序排列的各基准排名。"""
        ranked = sorted(
            [r for r in self.reports],
            key=lambda r: r.metrics.overall_score(),
            reverse=True,
        )
        return [
            {
                "rank": i + 1,
                "name": r.name,
                "overall_score": round(r.metrics.overall_score(), 4),
                "pass_rate": round(r.pass_rate, 4),
            }
            for i, r in enumerate(ranked)
        ]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_benchmarks": self.total_benchmarks,
            "overall_score": round(self.overall_score, 4),
            "overall_pass_rate": round(self.overall_pass_rate, 4),
            "duration_seconds": self.duration_seconds,
            "ranking": self.ranking,
            "benchmarks": [r.to_dict() for r in self.reports],
        }


class EvalRunner:
    """增强版评测运行器 — 支持 Benchmark 评测流水线。

    特性:
        - run_benchmark(name, agent) 运行内置/自定义基准
        - run_custom_cases(cases, agent) 支持自定义 BenchmarkCase 列表
        - run_all_benchmarks(agent) 一键运行全部 4 个基准
        - report_json() / report_markdown() 生成评测报告
        - scores_by_dimension() 各维度分数拆解
    """

    def __init__(self, output_dir: Optional[str] = None):
        """初始化 EvalRunner。

        Args:
            output_dir: 报告输出目录，默认 None（不落盘）。
        """
        self.output_dir = Path(output_dir) if output_dir else None
        self._reports: Dict[str, BenchmarkResult] = {}
        self._aggregate: Optional[AggregateReport] = None

    # ── Backward-compatible run(suite) ──────────────────────────

    def run(self, suite: Any) -> Any:
        """向后兼容：接受 EvalSuite 并委托给 _LegacyEvalRunner。

        调用方代码: runner = EvalRunner(); result = runner.run(suite)
        内部路由到 _LegacyEvalRunner().run(suite)
        """
        from jarvis.eval import _LegacyEvalRunner  # lazy to avoid circular import
        return _LegacyEvalRunner().run(suite)

    # ── Benchmark 运行 ──────────────────────────────────────────

    def run_benchmark(
        self,
        benchmark_name: str,
        agent: Any = None,
    ) -> BenchmarkResult:
        """运行指定名称的基准测试。

        支持的内置名称:
            "jarvis"      — JarvisBench (20 题覆盖 12 种能力)
            "router"      — RouterBench (8 种意图分类)
            "multistep"   — MultiStepBench (多步推理)
            "selfhealing" — SelfHealingBench (故障场景自愈)

        Args:
            benchmark_name: 基准名称（内置或通过 register_benchmark 注册的自定义名称）。
            agent: 被测 agent 实例。

        Returns:
            BenchmarkResult 包含通过率、指标和逐题明细。

        Raises:
            ValueError: 基准名称未找到。
        """
        bench = get_benchmark(benchmark_name)
        if bench is None:
            raise ValueError(
                f"Unknown benchmark: '{benchmark_name}'. "
                f"Available: {list(ALL_BENCHMARKS.keys())}"
            )

        result = bench.run(agent=agent)
        self._reports[benchmark_name] = result
        return result

    def run_custom_cases(
        self,
        cases: Sequence[Union[BenchmarkCase, Dict[str, Any]]],
        agent: Any = None,
        name: str = "custom",
        description: str = "Custom benchmark",
    ) -> BenchmarkResult:
        """运行自定义测试用例。

        支持两种输入方式:
            1. BenchmarkCase 对象列表
            2. 字典列表（自动转换为 BenchmarkCase）

        示例:
            runner.run_custom_cases([
                {"label": "test1", "prompt": "计算 1+1", "expected_route": "math"},
                {"label": "test2", "prompt": "今天天气怎么样", "expected_route": "weather"},
            ])
        """
        from jarvis.eval.benchmarks import Benchmark

        bench_cases: List[BenchmarkCase] = []
        for c in cases:
            if isinstance(c, BenchmarkCase):
                bench_cases.append(c)
            elif isinstance(c, dict):
                bench_cases.append(BenchmarkCase(**c))
            else:
                raise TypeError(f"Expected BenchmarkCase or dict, got {type(c)}")

        bench_name = name
        bench_desc = description

        class CustomBench(Benchmark):
            _name = bench_name
            _description = bench_desc

            @property
            def name(self) -> str:
                return self._name

            @property
            def description(self) -> str:
                return self._description

            def build_cases(self):
                return bench_cases

        bench = CustomBench()
        result = bench.run(agent=agent)
        self._reports[name] = result
        return result

    def run_all_benchmarks(self, agent: Any = None) -> AggregateReport:
        """运行全部 4 个内置基准并返回聚合报告。

        Args:
            agent: 被测 agent 实例。

        Returns:
            AggregateReport 包含总分、排名和逐基准明细。
        """
        started = time.time()

        for name in ["jarvis", "router", "multistep", "selfhealing"]:
            try:
                self.run_benchmark(name, agent=agent)
            except Exception as exc:
                # 捕获异常，记录并继续
                err_result = BenchmarkResult(
                    name=name,
                    description=f"Error running {name}",
                    errored=1,
                    started_at=time.time(),
                    finished_at=time.time(),
                )
                err_result.per_case.append({
                    "label": "runner_error",
                    "status": "error",
                    "detail": str(exc),
                })
                self._reports[name] = err_result

        self._aggregate = AggregateReport(
            total_benchmarks=len(self._reports),
            reports=list(self._reports.values()),
            started_at=started,
            finished_at=time.time(),
        )
        return self._aggregate

    # ── 自定义基准注册 ──────────────────────────────────────────

    def register_benchmark(self, name: str, bench: Any) -> None:
        """注册自定义基准测试。

        Args:
            name: 基准名称，用于 run_benchmark() 调用。
            bench: 实现 Benchmark 接口的对象（需有 build_cases() 和 run() 方法）。
        """
        ALL_BENCHMARKS[name] = bench

    # ── 评分查询 ────────────────────────────────────────────────

    def scores_by_dimension(self, benchmark_name: str) -> Dict[str, float]:
        """获取指定基准的各维度分数拆解。

        Returns:
            dict 包含 task_success_rate / tool_call_accuracy /
            response_time_mean_ms / route_accuracy / healing_success_rate /
            evolution_convergence / overall_score
        """
        report = self._reports.get(benchmark_name)
        if report is None:
            return {}

        m = report.metrics
        return {
            "task_success_rate": round(m.task_success_rate, 4),
            "tool_call_accuracy": round(m.tool_call_accuracy, 4),
            "response_time_mean_ms": round(m.response_time_mean_ms, 2),
            "route_accuracy": round(m.route_accuracy, 4),
            "healing_success_rate": round(m.healing_success_rate, 4),
            "evolution_convergence": round(m.evolution_convergence, 4),
            "overall_score": round(m.overall_score(), 4),
        }

    def all_scores(self) -> Dict[str, Dict[str, float]]:
        """获取所有已运行基准的各维度分数。

        Returns:
            {benchmark_name: {dimension: score}}
        """
        return {name: self.scores_by_dimension(name) for name in self._reports}

    # ── 报告生成 ────────────────────────────────────────────────

    def report_json(self, output_path: Optional[str] = None) -> str:
        """生成 JSON 格式评测报告。

        Args:
            output_path: 可选，写入文件路径。

        Returns:
            JSON 字符串。
        """
        if self._aggregate is None:
            # 手动构建聚合
            self._aggregate = AggregateReport(
                total_benchmarks=len(self._reports),
                reports=list(self._reports.values()),
                started_at=(
                    min(r.started_at for r in self._reports.values())
                    if self._reports
                    else 0
                ),
                finished_at=(
                    max(r.finished_at for r in self._reports.values())
                    if self._reports
                    else 0
                ),
            )

        data = self._aggregate.to_dict()
        json_str = _json.dumps(data, indent=2, ensure_ascii=False)

        if output_path:
            Path(output_path).write_text(json_str, encoding="utf-8")

        return json_str

    def report_markdown(self, output_path: Optional[str] = None) -> str:
        """生成 Markdown 格式评测报告。

        Args:
            output_path: 可选，写入文件路径。

        Returns:
            Markdown 字符串。
        """
        if self._aggregate is None:
            self._aggregate = AggregateReport(
                total_benchmarks=len(self._reports),
                reports=list(self._reports.values()),
                started_at=(
                    min(r.started_at for r in self._reports.values())
                    if self._reports
                    else 0
                ),
                finished_at=(
                    max(r.finished_at for r in self._reports.values())
                    if self._reports
                    else 0
                ),
            )

        agg = self._aggregate
        lines = [
            "# JARVIS Evaluation Report",
            "",
            f"**Generated:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"**Duration:** {agg.duration_seconds}s",
            "",
            "## Summary",
            "",
            f"| Metric | Value |",
            f"|---|---|",
            f"| Overall Score | {agg.overall_score:.4f} |",
            f"| Overall Pass Rate | {agg.overall_pass_rate:.1%} |",
            f"| Total Benchmarks | {agg.total_benchmarks} |",
            f"| Total Cases | {sum(r.total_cases for r in agg.reports)} |",
            f"| Total Passed | {sum(r.passed for r in agg.reports)} |",
            f"| Total Failed | {sum(r.failed for r in agg.reports)} |",
            f"| Total Errored | {sum(r.errored for r in agg.reports)} |",
            "",
            "## Ranking",
            "",
            "| Rank | Benchmark | Overall Score | Pass Rate |",
            "|---|---|---|---|",
        ]

        for item in agg.ranking:
            lines.append(
                f"| {item['rank']} | {item['name']} | {item['overall_score']:.4f} | {item['pass_rate']:.1%} |"
            )

        lines.append("")
        lines.append("## Per-Benchmark Details")
        lines.append("")

        for report in agg.reports:
            m = report.metrics
            lines.append(f"### {report.name}")
            lines.append("")
            lines.append(f"| Dimension | Score |")
            lines.append(f"|---|---|")
            lines.append(f"| Task Success Rate | {m.task_success_rate:.4f} |")
            lines.append(f"| Tool Call Accuracy | {m.tool_call_accuracy:.4f} |")
            lines.append(f"| Response Time (mean) | {m.response_time_mean_ms:.2f} ms |")
            lines.append(f"| Route Accuracy | {m.route_accuracy:.4f} |")
            lines.append(f"| Healing Success Rate | {m.healing_success_rate:.4f} |")
            lines.append(f"| Evolution Convergence | {m.evolution_convergence:.4f} |")
            lines.append(f"| **Overall Score** | **{m.overall_score():.4f}** |")
            lines.append("")
            lines.append(f"**Cases:** {report.total_cases} total, {report.passed} pass, {report.failed} fail, {report.errored} error")
            lines.append("")

            # 失败用例明细
            failed_cases = [c for c in report.per_case if c.get("status") != "pass"]
            if failed_cases:
                lines.append("| Label | Status | Detail |")
                lines.append("|---|---|---|")
                for fc in failed_cases:
                    detail = str(fc.get("detail", ""))[:80]
                    lines.append(f"| {fc['label']} | {fc['status']} | {detail} |")
                lines.append("")

        md_str = "\n".join(lines)

        if output_path:
            Path(output_path).write_text(md_str, encoding="utf-8")

        return md_str

    # ── 便利方法 ────────────────────────────────────────────────

    @property
    def history(self) -> Dict[str, BenchmarkResult]:
        """返回所有已运行基准的结果。"""
        return dict(self._reports)

    def reset(self) -> None:
        """清除所有已运行结果。"""
        self._reports.clear()
        self._aggregate = None

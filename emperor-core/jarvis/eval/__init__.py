"""
Evals Framework — regression testing for AI agent behavior.

2026 年最佳实践：每次有意义的变更（prompt / 工具 / 模型 / 检索）都跑 evals。
这是 "demo" 和 "production" 之间的分水岭。

Package structure:
    __init__.py   — 原有 EvalCase / EvalSuite / JudgeEvalSuite / _LegacyEvalRunner
    runner.py     — 增强版 EvalRunner，支持 Benchmark 评测流水线
    metrics.py    — 评测指标计算（任务成功率 / 工具调用准确率 / 路由准确率 等）
    benchmarks.py — 内置基准测试集（JarvisBench / RouterBench / MultiStepBench / SelfHealingBench）

Usage:
    from jarvis.eval import EvalCase, EvalSuite, EvalRunner
    from jarvis.eval import JarvisBench, RouterBench

    # 传统 EvalSuite 方式
    suite = EvalSuite("capability:datetime", [
        EvalCase("现在几点", capability="datetime", expected_keys=["date", "time"]),
    ])
    runner = EvalRunner()
    result = runner.run(suite)

    # 增强版 Benchmark 方式
    runner.run_benchmark("jarvis", agent=my_agent)
    runner.run_benchmark("router", agent=my_agent)
"""

from __future__ import annotations

import datetime as dt
import json as _json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("jarvis.eval")


# ══════════════════════════════════════════════════════════════════
# Data Types
# ══════════════════════════════════════════════════════════════════


class EvalStatus(Enum):
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"
    SKIP = "skip"


@dataclass
class EvalResult:
    """单个评测用例的执果。"""

    case_name: str
    status: EvalStatus
    duration_ms: float = 0
    expected: Any = None
    actual: Any = None
    details: str = ""


@dataclass
class SuiteResult:
    """一个评测套件的完整执果。"""

    suite_name: str
    results: List[EvalResult] = field(default_factory=list)
    started_at: float = 0
    finished_at: float = 0
    passed: int = 0
    failed: int = 0
    errored: int = 0
    skipped: int = 0

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.errored + self.skipped

    @property
    def pass_rate(self) -> float:
        attempted = self.passed + self.failed
        return self.passed / attempted if attempted > 0 else 1.0

    @property
    def duration_seconds(self) -> float:
        return self.finished_at - self.started_at

    def summary(self) -> str:
        lines = [
            f"Suite: {self.suite_name}",
            f"  Total: {self.total}  "
            f"Pass: {self.passed}  "
            f"Fail: {self.failed}  "
            f"Error: {self.errored}  "
            f"Skip: {self.skipped}",
            f"  Pass Rate: {self.pass_rate:.1%}  "
            f"Duration: {self.duration_seconds:.2f}s",
        ]
        for r in self.results:
            if r.status != EvalStatus.PASS:
                icon = {"fail": "✗", "error": "⚠", "skip": "○"}.get(r.status.value, "?")
                lines.append(f"  [{icon}] {r.case_name}: {r.details}")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "suite_name": self.suite_name,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "errored": self.errored,
            "skipped": self.skipped,
            "pass_rate": self.pass_rate,
            "duration_seconds": round(self.duration_seconds, 3),
            "results": [
                {
                    "case": r.case_name,
                    "status": r.status.value,
                    "duration_ms": round(r.duration_ms, 1),
                    "details": r.details,
                }
                for r in self.results
            ],
        }
        if hasattr(self, "per_dimension_avg") and self.per_dimension_avg:
            d["per_dimension_avg"] = self.per_dimension_avg
        return d


@dataclass
class EvalCase:
    """一个评测用例。

    支持三种验证模式：
    1. expected_keys — 验证结果中包含指定的 key
    2. expected_values — 精确匹配结果中的字段值
    3. validator — 自定义验证函数 (result: dict) -> (bool, str)
    """

    name: str
    prompt: str
    capability: str = ""
    domain: str = "general"
    expected_keys: List[str] = field(default_factory=list)
    expected_values: Dict[str, Any] = field(default_factory=dict)
    validator: Optional[Callable[[Dict[str, Any]], tuple[bool, str]]] = None


@dataclass
class EvalSuite:
    """一组评测用例，围绕同一主题。"""

    name: str
    cases: List[EvalCase] = field(default_factory=list)

    def add(self, case: EvalCase) -> EvalSuite:
        self.cases.append(case)
        return self


# ══════════════════════════════════════════════════════════════════
# JudgeEvalSuite — LLM-as-Judge 自由文本质量评估
# ══════════════════════════════════════════════════════════════════


@dataclass
class JudgeEvalCase:
    """单个 LLM-as-Judge 评测用例。"""

    label: str
    output: str
    expected: str
    criteria: Optional[List[Any]] = None  # List[JudgingCriteria]


class JudgeEvalSuite:
    """LLM-as-Judge 评测套件。

    用于评估 Agent 自由文本输出的质量（准确性/完整性/相关性/安全性），
    通过 LLMJudge 生成 per-dimension 分数，并集成到 EvalRunner 报告体系中。

    Usage:
        from jarvis.llm_judge import LLMJudge, JudgingCriteria
        from jarvis.eval import JudgeEvalSuite, EvalRunner

        cases = [
            JudgeEvalCase("case1", output="...", expected="..."),
        ]
        suite = JudgeEvalSuite("judge:quality", cases)
        result = EvalRunner().run(suite)
    """

    def __init__(self, name: str, cases: Optional[List[JudgeEvalCase]] = None):
        self.name = name
        self.cases: List[JudgeEvalCase] = cases or []

    def add(self, case: JudgeEvalCase) -> JudgeEvalSuite:
        self.cases.append(case)
        return self


# ══════════════════════════════════════════════════════════════════
# _LegacyEvalRunner — 原有单套件运行器（保留向后兼容）
# ══════════════════════════════════════════════════════════════════


class _LegacyEvalRunner:
    """原有评测执行器 — 运行 EvalSuite 并收集结果。

    保留完整向后兼容。增强版 EvalRunner 请参见 runner.py。
    """

    def __init__(self, capability_registry: Any = None, emperor: Any = None):
        self._reg = capability_registry
        self._emperor = emperor
        self._all_results: List[SuiteResult] = []

    @property
    def history(self) -> List[SuiteResult]:
        return self._all_results

    def run(self, suite: EvalSuite) -> SuiteResult:
        """运行一个评测套件，返回 SuiteResult。

        支持 EvalSuite（传统键/值验证）和 JudgeEvalSuite（LLM-as-Judge 评分）。
        """
        from jarvis.llm_judge import LLMJudge, BatchCase

        if isinstance(suite, JudgeEvalSuite):
            judge = LLMJudge()
            batch_cases = [
                BatchCase(
                    output=c.output,
                    expected=c.expected,
                    criteria=c.criteria,
                    label=c.label,
                )
                for c in suite.cases
            ]
            report = judge.batch_evaluate(batch_cases)

            result = SuiteResult(suite_name=suite.name, started_at=time.time())
            result.per_dimension_avg = report.per_dimension_avg

            for i, r in enumerate(report.results):
                score = r.get("score", 0.0)
                label = r.get("label", f"case_{i}")
                breakdown_str = ", ".join(
                    f"{b['criterion']}={b['score']:.2f}" for b in r.get("breakdown", [])
                )
                status = EvalStatus.PASS if score >= 0.5 else EvalStatus.FAIL
                case_result = EvalResult(
                    case_name=label,
                    status=status,
                    duration_ms=0,
                    details=f"score={score:.4f} | {breakdown_str} | {r.get('reasoning', '')}",
                )
                result.results.append(case_result)
                if status == EvalStatus.PASS:
                    result.passed += 1
                else:
                    result.failed += 1

            result.finished_at = time.time()
            self._all_results.append(result)
            return result

        result = SuiteResult(suite_name=suite.name, started_at=time.time())

        for case in suite.cases:
            case_result = self._run_case(case)
            result.results.append(case_result)

            if case_result.status == EvalStatus.PASS:
                result.passed += 1
            elif case_result.status == EvalStatus.FAIL:
                result.failed += 1
            elif case_result.status == EvalStatus.ERROR:
                result.errored += 1
            else:
                result.skipped += 1

        result.finished_at = time.time()
        self._all_results.append(result)
        return result

    def run_all(self, suites: List[EvalSuite]) -> List[SuiteResult]:
        """运行多个评测套件。"""
        results = []
        for suite in suites:
            results.append(self.run(suite))
        return results

    def _run_case(self, case: EvalCase) -> EvalResult:
        """执行单个用例。"""
        started = time.time()

        try:
            if self._reg and case.capability:
                output = self._reg.execute(case.capability, case.prompt)
            elif self._emperor:
                output = self._emperor.execute_task(
                    case.prompt, domain=case.domain
                )
            else:
                return EvalResult(
                    case_name=case.name,
                    status=EvalStatus.ERROR,
                    details="No capability registry or emperor available",
                )

            elapsed_ms = (time.time() - started) * 1000

            if case.validator:
                passed, detail = case.validator(output)
                return EvalResult(
                    case_name=case.name,
                    status=EvalStatus.PASS if passed else EvalStatus.FAIL,
                    duration_ms=elapsed_ms,
                    details=detail,
                )

            if case.expected_keys:
                data = output.get("data", {})
                missing = [k for k in case.expected_keys if k not in data]
                if missing:
                    return EvalResult(
                        case_name=case.name,
                        status=EvalStatus.FAIL,
                        duration_ms=elapsed_ms,
                        expected=case.expected_keys,
                        actual=list(data.keys()),
                        details=f"Missing keys: {missing}",
                    )

            if case.expected_values:
                data = output.get("data", {})
                for key, expected_val in case.expected_values.items():
                    actual_val = data.get(key)
                    if actual_val != expected_val:
                        return EvalResult(
                            case_name=case.name,
                            status=EvalStatus.FAIL,
                            duration_ms=elapsed_ms,
                            expected={key: expected_val},
                            actual={key: actual_val},
                            details=f"Key '{key}': expected {expected_val}, got {actual_val}",
                        )

            return EvalResult(
                case_name=case.name,
                status=EvalStatus.PASS,
                duration_ms=elapsed_ms,
            )

        except Exception as e:
            elapsed_ms = (time.time() - started) * 1000
            logger.exception("Eval case '%s' errored: %s", case.name, e)
            return EvalResult(
                case_name=case.name,
                status=EvalStatus.ERROR,
                duration_ms=elapsed_ms,
                details=str(e),
            )

    def report(self) -> Dict[str, Any]:
        """生成聚合报告。"""
        all_passed = sum(r.passed for r in self._all_results)
        all_failed = sum(r.failed for r in self._all_results)
        all_errored = sum(r.errored for r in self._all_results)
        total = all_passed + all_failed + all_errored

        return {
            "total_suites": len(self._all_results),
            "total_cases": total,
            "passed": all_passed,
            "failed": all_failed,
            "errored": all_errored,
            "pass_rate": all_passed / (all_passed + all_failed) if (all_passed + all_failed) > 0 else 0,
            "suites": [r.to_dict() for r in self._all_results],
        }


# ══════════════════════════════════════════════════════════════════
# Built-in Eval Suites — 覆盖所有 12 个内置能力
# ══════════════════════════════════════════════════════════════════


def _validate_datetime_result(output: dict) -> tuple[bool, str]:
    data = output.get("data", {})
    year = data.get("year", 0)
    month = data.get("month", 0)
    day = data.get("day", 0)
    if not (2024 <= year <= 2030):
        return False, f"Year out of range: {year}"
    if not (1 <= month <= 12):
        return False, f"Month out of range: {month}"
    if not (1 <= day <= 31):
        return False, f"Day out of range: {day}"
    return True, ""


def _validate_math_result(output: dict) -> tuple[bool, str]:
    data = output.get("data", {})
    if "value" not in data:
        return False, "Missing 'value' key"
    if data.get("value") is None:
        return True, "Expression may not be extractable, but handler didn't crash"
    return True, ""


def _validate_random_result(output: dict) -> tuple[bool, str]:
    data = output.get("data", {})
    if "type" not in data:
        return False, "Missing 'type' key"
    return True, ""


def _validate_text_result(output: dict) -> tuple[bool, str]:
    data = output.get("data", {})
    if not data:
        return False, "Empty data"
    return True, ""


def create_builtin_suites() -> List[EvalSuite]:
    """创建覆盖所有 12 个内置能力的评测套件。"""
    suites: List[EvalSuite] = []

    suites.append(EvalSuite("capability:datetime", [
        EvalCase("时间查询", "现在几点", capability="datetime",
                 expected_keys=["date", "time", "weekday_cn", "year", "month", "day"]),
        EvalCase("日期查询", "今天是几号", capability="datetime",
                 expected_keys=["date", "year", "month", "day"]),
        EvalCase("星期查询", "今天星期几", capability="datetime",
                 expected_keys=["weekday_cn"]),
        EvalCase("时间验证", "What time is it now", capability="datetime",
                 validator=_validate_datetime_result),
    ]))

    suites.append(EvalSuite("capability:math", [
        EvalCase("简单加减", "计算 17 + 23", capability="math",
                 expected_keys=["expression", "value"]),
        EvalCase("乘除运算", "算一下 50 * 2", capability="math",
                 expected_keys=["expression", "value"]),
        EvalCase("复杂表达式", "求 (2+3)*4-10", capability="math",
                 expected_keys=["expression", "value"]),
        EvalCase("无法解析", "帮我算一下 hello world", capability="math",
                 validator=_validate_math_result),
    ]))

    suites.append(EvalSuite("capability:random", [
        EvalCase("随机范围", "生成一个1到100的随机数", capability="random",
                 expected_keys=["value", "min", "max"]),
        EvalCase("掷骰子", "掷一个骰子", capability="random",
                 expected_keys=["rolls", "total"]),
        EvalCase("随机小数", "来一个随机数0到1", capability="random",
                 validator=_validate_random_result),
    ]))

    suites.append(EvalSuite("capability:text", [
        EvalCase("文本统计", "统计字数 Hello World", capability="text",
                 expected_keys=["operation"]),
        EvalCase("文本反转", "反转 ABCDEF", capability="text",
                 expected_keys=["operation", "input", "output"]),
        EvalCase("大写转换", "大写 hello world", capability="text",
                 expected_keys=["operation", "input", "output"]),
        EvalCase("小写转换", "小写 HELLO", capability="text",
                 expected_keys=["operation", "input", "output"]),
    ]))

    suites.append(EvalSuite("capability:file_info", [
        EvalCase("文件不存在", "查看 C:\\nonexistent\\file.txt 文件信息",
                 capability="file_info", expected_keys=["path", "exists"]),
    ]))

    suites.append(EvalSuite("capability:hash", [
        EvalCase("MD5", "md5 加密 hello", capability="hash",
                 expected_keys=["algorithm", "digest"]),
        EvalCase("SHA256", "sha256 加密 test", capability="hash",
                 expected_keys=["algorithm", "digest"]),
    ]))

    suites.append(EvalSuite("capability:json_tool", [
        EvalCase("JSON格式化", 'json 格式化 {"a":1,"b":2}', capability="json_tool",
                 expected_keys=["mode", "valid"]),
        EvalCase("JSON验证", '解析 {"name":"test"}', capability="json_tool",
                 expected_keys=["mode", "valid"]),
    ]))

    suites.append(EvalSuite("capability:uuid_gen", [
        EvalCase("生成UUID", "生成一个UUID", capability="uuid_gen",
                 expected_keys=["uuid", "version"]),
    ]))

    suites.append(EvalSuite("capability:weather", [
        EvalCase("北京天气", "查询北京的天气", capability="weather",
                 expected_keys=["city"]),
    ]))

    suites.append(EvalSuite("capability:news", [
        EvalCase("科技新闻", "查询科技新闻", capability="news",
                 expected_keys=["topic"]),
    ]))

    suites.append(EvalSuite("capability:web_search", [
        EvalCase("搜索Python", "搜索 Python 3.12 新特性", capability="web_search",
                 expected_keys=["abstract", "url"]),
    ]))

    suites.append(EvalSuite("capability:web_fetch", [
        EvalCase("无URL", "抓取这个网页", capability="web_fetch",
                 validator=lambda r: (True, "No URL provided, should not crash")),
    ]))

    suites.append(EvalSuite("pipeline:health_check", [
        EvalCase("健康检查流水线", "health check", capability="health", domain="general",
                 validator=lambda r: (True, "Pipeline test — no crash is pass")),
    ]))

    return suites


def run_builtin_evals(registry=None, emperor=None, verbose: bool = False) -> List[SuiteResult]:
    """运行所有内置评测套件。"""
    runner = _LegacyEvalRunner(capability_registry=registry, emperor=emperor)
    suites = create_builtin_suites()
    results = runner.run_all(suites)

    if verbose:
        for r in results:
            print(r.summary())
            print()

    report = runner.report()
    print(f"\n=== EVALS REPORT ===")
    print(f"  Suites: {report['total_suites']}")
    print(f"  Cases:  {report['total_cases']}")
    print(f"  Pass:   {report['passed']}")
    print(f"  Fail:   {report['failed']}")
    print(f"  Error:  {report['errored']}")
    print(f"  Rate:   {report['pass_rate']:.1%}")

    return results


# ══════════════════════════════════════════════════════════════════
# 导入新子模块并对外暴露增强版 API
# ══════════════════════════════════════════════════════════════════

from jarvis.eval.runner import EvalRunner
from jarvis.eval.metrics import (
    task_success_rate,
    tool_call_accuracy,
    response_time_ms,
    route_accuracy,
    healing_success_rate,
    evolution_convergence,
    compute_all_metrics,
)
from jarvis.eval.benchmarks import (
    JarvisBench,
    RouterBench,
    MultiStepBench,
    SelfHealingBench,
    ALL_BENCHMARKS,
    get_benchmark,
)

__all__ = [
    # Data Types
    "EvalStatus", "EvalResult", "SuiteResult",
    "EvalCase", "EvalSuite",
    "JudgeEvalCase", "JudgeEvalSuite",
    # Runner
    "EvalRunner",
    # Built-in suites (legacy)
    "create_builtin_suites", "run_builtin_evals",
    # Metrics
    "task_success_rate", "tool_call_accuracy", "response_time_ms",
    "route_accuracy", "healing_success_rate", "evolution_convergence",
    "compute_all_metrics",
    # Benchmarks
    "JarvisBench", "RouterBench", "MultiStepBench", "SelfHealingBench",
    "ALL_BENCHMARKS", "get_benchmark",
]

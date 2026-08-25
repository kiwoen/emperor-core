"""
huanxin.eval_bench — P0.6 可信评测基准（替代 llm_judge 关键词失真）。

设计目标（主理人决策）
------------------------
原方案建议引 deepeval + SWE-bench-lite，但本环境沙箱无 LLM API key、
无法真正跑通，且"与人工标注相关性 ≥ 0.8"无法在此验证。因此 P0.6 主体
做成 **离线可跑的确定性评测基准**，LLM 裁判路径作为"有 key 才启用"的
opt-in。这样既能在沙箱验证、达成"可证伪"目标，又不引入重型网络依赖。

核心不变量
----------
"系统是否变好"必须 **可证伪**。每一个评测用例都携带一个显式的、
可调用的 ``gold_validator(output, expected) -> bool``，用它——而非关键词
重叠——来判定事实正确性。裁判只输出 PASS / FAIL，不存在用连续"accuracy"
数字伪装事实正确性的情况。

子模块
------
    criteria  — 数据结构：EvalCase / EvalResult / EvalReport
    judges    — DeterministicJudge（离线权威）/ LLMBackedJudge（opt-in）/ get_judge 工厂
    run       — run_suite() 聚合 pass_rate 与 per-domain 分数
    suites    — 内置离线黄金用例（canonical.py，零网络依赖）

Usage:
    from huanxin.eval_bench import run_suite, build_canonical_suite
    from huanxin.eval_bench.criteria import EvalReport

    report: EvalReport = run_suite(build_canonical_suite())
    print(report.pass_rate, report.per_domain)
"""

from __future__ import annotations

from huanxin.eval_bench.criteria import EvalCase, EvalReport, EvalResult
from huanxin.eval_bench.judges import (
    DeterministicJudge,
    JudgeUnavailableError,
    LLMBackedJudge,
    default_correctness,
    get_judge,
)
from huanxin.eval_bench.run import run_suite
from huanxin.eval_bench.suites.canonical import (
    CanonicalSuite,
    build_canonical_suite,
)

__all__ = [
    # criteria
    "EvalCase",
    "EvalResult",
    "EvalReport",
    # judges
    "DeterministicJudge",
    "LLMBackedJudge",
    "JudgeUnavailableError",
    "default_correctness",
    "get_judge",
    # run
    "run_suite",
    # suites
    "CanonicalSuite",
    "build_canonical_suite",
]

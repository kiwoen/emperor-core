"""
P0.6 基准运行器：聚合确定性裁判结果。

:func:`run_suite` 遍历套件中的用例，用 :class:`DeterministicJudge` 逐一裁判，
汇总整体与各域的 pass_rate，并支持序列化为 dict。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from jarvis.eval_bench.criteria import EvalCase, EvalReport, EvalResult
from jarvis.eval_bench.judges import DeterministicJudge

logger = logging.getLogger("jarvis.eval_bench.run")


def _as_cases(suite: Any) -> List[EvalCase]:
    if hasattr(suite, "cases"):
        return list(suite.cases)
    return list(suite)


def _as_outputs(suite: Any, outputs: Optional[Dict[str, str]]) -> Dict[str, str]:
    if outputs is not None:
        return outputs
    if hasattr(suite, "reference_outputs"):
        return suite.reference_outputs or {}
    return {}


def run_suite(suite: Any, outputs: Optional[Dict[str, str]] = None) -> EvalReport:
    """对 *suite* 中每个用例运行 :class:`DeterministicJudge` 并聚合。

    Args:
        suite: 一个 ``CanonicalSuite``（或任何暴露 ``.cases`` 及可选
            ``.reference_outputs`` 的对象），或一份
            :class:`~jarvis.eval_bench.criteria.EvalCase` 列表。
        outputs: 可选的 ``case_id -> 候选输出`` 映射。省略时改用
            ``suite.reference_outputs``（黄金答案），此时应得到
            ``pass_rate == 1.0``。

    Returns:
        :class:`EvalReport`，含整体与各域 pass_rate。
    """
    cases = _as_cases(suite)
    ref = _as_outputs(suite, outputs)
    judge = DeterministicJudge()

    results: List[EvalResult] = []
    domain_flags: Dict[str, List[bool]] = {}

    for case in cases:
        output = ref.get(case.id, "")
        res = judge.judge(case, output)
        results.append(res)
        domain_flags.setdefault(res.domain, []).append(res.passed)

    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    per_domain = {
        dom: (sum(flags) / len(flags) if flags else 0.0)
        for dom, flags in domain_flags.items()
    }
    pass_rate = passed / len(results) if results else 0.0

    return EvalReport(
        cases=len(results),
        passed=passed,
        failed=failed,
        pass_rate=pass_rate,
        per_domain=per_domain,
        results=results,
    )


__all__ = ["run_suite"]

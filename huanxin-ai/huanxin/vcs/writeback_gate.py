"""
P2.3 WritebackGate — 写回前评测闸（DGM 闭环的最后一环）。

DGM 论文（arXiv:2505.22954）的安全模型是三段式：**沙箱 + 基准评测 + 人类审批门**。
P2.1 的 :class:`~huanxin.vcs.git_channel.GitWriteChannel` 已经落实了「沙箱 +
人类审批门（只开 PR，绝不直推 master）」，但它此前**无条件**开 PR——
缺了「基准评测」这一环：一个让基准回归的突变也会被照样提交。

本模块补上这一环：写回前先跑 :func:`huanxin.eval_bench.run.run_suite`
得到 :class:`~huanxin.eval_bench.criteria.EvalReport`，由
:class:`WritebackGate` 裁决是否放行。评测不达标 / 相对基线回归，
就直接拒绝（抛出 :class:`WritebackBlocked`），**绝不静默放行**。

判定规则（全部可配置、可审计）：
  1. ``pass_rate`` 不低于 ``min_pass_rate``（默认 1.0 = 黄金基准必须全过）；
  2. 若提供 ``baseline``，``pass_rate`` 不得低于基线（禁止回归）；
  3. 若设 ``min_domain_pass_rate``，每个域的通过率都不得低于该下限。

典型用法::

    from huanxin.eval_bench.run import run_suite
    from huanxin.eval_bench.suites.canonical import canonical_suite
    from huanxin.vcs import GitWriteChannel, WritebackGate, WritebackBlocked

    report = run_suite(canonical_suite(), outputs=candidate_outputs)
    gate = WritebackGate(min_pass_rate=1.0, forbid_regression=True)
    ch = GitWriteChannel()
    try:
        ch.propose_change(
            repo="kiwoen/huanxin-ai", patch_text=diff, title="auto-absorb: ...",
            eval_report=report, eval_gate=gate,
        )
    except WritebackBlocked as e:
        logger.warning("评测不达标，拒绝写回：%s", e)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from huanxin.eval_bench.criteria import EvalReport

logger = logging.getLogger("huanxin.vcs.writeback_gate")


class WritebackBlocked(RuntimeError):
    """评测不达标 / 回归时抛出——写回被闸拦下（可控失败，绝不静默）。"""


@dataclass
class GateDecision:
    """一次闸裁决的结果（可审计）。"""

    allowed: bool
    reason: str
    pass_rate: float
    baseline_pass_rate: Optional[float] = None

    def __str__(self) -> str:  # pragma: no cover - 便于日志/报错展示
        verdict = "ALLOW" if self.allowed else "BLOCK"
        return f"[{verdict}] pass_rate={self.pass_rate:.1%} reason={self.reason}"


class WritebackGate:
    """写回评测闸：只有基准评测通过的突变才允许开 PR。

    Args:
        min_pass_rate: 放行所需的最低整体通过率（默认 1.0，黄金基准全过）。
        forbid_regression: 若为 True 且提供 baseline，则 ``pass_rate`` 不得
            低于 ``baseline.pass_rate``（禁止回归）。
        min_domain_pass_rate: 可选的每域通过率下限；任一域低于该值即拦。
            ``None`` 表示不做逐域检查。
    """

    def __init__(
        self,
        min_pass_rate: float = 1.0,
        forbid_regression: bool = True,
        min_domain_pass_rate: Optional[float] = None,
    ) -> None:
        if not (0.0 <= min_pass_rate <= 1.0):
            raise ValueError("min_pass_rate 必须在 [0,1] 内")
        if min_domain_pass_rate is not None and not (0.0 <= min_domain_pass_rate <= 1.0):
            raise ValueError("min_domain_pass_rate 必须在 [0,1] 内")
        self._min_pass_rate = float(min_pass_rate)
        self._forbid_regression = bool(forbid_regression)
        self._min_domain = (
            None if min_domain_pass_rate is None else float(min_domain_pass_rate)
        )

    def evaluate(
        self,
        report: EvalReport,
        baseline: Optional[EvalReport] = None,
    ) -> GateDecision:
        """对 *report*（可选对照 *baseline*）裁决是否允许写回。"""
        base_rate = baseline.pass_rate if baseline is not None else None

        # 规则 0：空套件不可信——没有任何用例就无法证明安全，拦下。
        if report.cases == 0:
            return GateDecision(
                allowed=False,
                reason="评测套件为空（cases=0），无法证明突变安全，拒绝写回",
                pass_rate=report.pass_rate,
                baseline_pass_rate=base_rate,
            )

        # 规则 1：整体通过率下限
        if report.pass_rate < self._min_pass_rate:
            return GateDecision(
                allowed=False,
                reason=(
                    f"整体通过率 {report.pass_rate:.1%} 低于门槛 "
                    f"{self._min_pass_rate:.1%}（{report.passed}/{report.cases} 通过）"
                ),
                pass_rate=report.pass_rate,
                baseline_pass_rate=base_rate,
            )

        # 规则 2：禁止相对基线回归
        if self._forbid_regression and baseline is not None:
            if report.pass_rate < baseline.pass_rate:
                return GateDecision(
                    allowed=False,
                    reason=(
                        f"相对基线回归：{report.pass_rate:.1%} < "
                        f"baseline {baseline.pass_rate:.1%}"
                    ),
                    pass_rate=report.pass_rate,
                    baseline_pass_rate=base_rate,
                )

        # 规则 3：逐域通过率下限
        if self._min_domain is not None:
            for dom, rate in sorted(report.per_domain.items()):
                if rate < self._min_domain:
                    return GateDecision(
                        allowed=False,
                        reason=(
                            f"域 '{dom}' 通过率 {rate:.1%} 低于逐域下限 "
                            f"{self._min_domain:.1%}"
                        ),
                        pass_rate=report.pass_rate,
                        baseline_pass_rate=base_rate,
                    )

        return GateDecision(
            allowed=True,
            reason="评测达标，允许写回",
            pass_rate=report.pass_rate,
            baseline_pass_rate=base_rate,
        )

    def assert_allowed(
        self,
        report: EvalReport,
        baseline: Optional[EvalReport] = None,
    ) -> GateDecision:
        """与 :meth:`evaluate` 相同，但不放行时直接抛 :class:`WritebackBlocked`。"""
        decision = self.evaluate(report, baseline=baseline)
        if not decision.allowed:
            logger.warning("[WritebackGate] 拒绝写回：%s", decision.reason)
            raise WritebackBlocked(decision.reason)
        logger.info(
            "[WritebackGate] 允许写回：pass_rate=%.1f%%", decision.pass_rate * 100
        )
        return decision


__all__ = ["WritebackBlocked", "GateDecision", "WritebackGate"]

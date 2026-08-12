"""
P1.4 进化安全闸门：CircuitBreaker（失控熔断）+ PromotionGate（晋升门）

自进化系统「解冻」自动淘汰（P0.3 enable_auto_elimination=True）后，
必须有一道硬闸，防止「指标恶化 / 资源超支」时系统继续自我破坏。
本模块是那道闸：

- :class:`CircuitBreaker`：监控每轮进化的「平均功勋」与「累计成本」。
  当功勋跌幅超过阈值、或连续 N 轮负向、或累计成本超预算时，
  **熔断（open）**——Court 在熔断状态下停止后续进化轮。
- :class:`PromotionGate`：晋升不是「功勋 > 50 就升」，而是要求某大臣
  **连续 N 轮功勋正增长**才放行，避免在噪声上过度提拔。

设计原则（与 DGM 论文 arXiv:2505.22954 一致）：
  1. 闸只在「有可信评测信号（P0.6）」后才允许放行进化；
  2. 闸是「可证伪」的——任何熔断都有明确 reason，可审计；
  3. 闸绝不静默失效：熔断时显式 logging + 在 evolve 结果里标记 halted。

核心不变量：熔断永远优先于淘汰。即便 enable_auto_elimination=True，
只要 CircuitBreaker 打开，Court.evolve 立即停止后续轮，不再动任何大臣。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger("jarvis.court.circuit_breaker")


class CircuitState(str, Enum):
    """熔断器三态（标准 circuit breaker 模型）。"""

    CLOSED = "closed"        # 正常：允许进化
    OPEN = "open"            # 已熔断：停止后续进化轮
    HALF_OPEN = "half_open"  # 冷却后试探（本实现固定冷却 N 轮后自动半开）


@dataclass
class CircuitConfig:
    """熔断器阈值配置。

    Attributes:
        drop_fraction: 平均功勋相对历史峰值的跌幅超过该比例即熔断
            （例如 0.20 = 从峰值跌去 20%）。
        consecutive_negative: 连续多少轮平均功勋为负向（下降）即熔断。
        cost_budget: 累计成本预算上限；超过即熔断（0 表示不限制）。
        min_cycles_before_trip: 前若干轮不触发熔断，避免冷启动误伤。
        cooldown_cycles: 熔断后多少轮自动转 HALF_OPEN 试探。
    """

    drop_fraction: float = 0.20
    consecutive_negative: int = 3
    cost_budget: float = 0.0
    min_cycles_before_trip: int = 2
    cooldown_cycles: int = 3


@dataclass
class CircuitDecision:
    """单次 record 的结果。"""

    state: CircuitState
    open: bool
    reason: str = ""
    peak_merit: float = 0.0
    last_merit: float = 0.0
    cumulative_cost: float = 0.0


class CircuitBreaker:
    """进化失控熔断器。

    Usage::
        cb = CircuitBreaker(CircuitConfig(drop_fraction=0.20))
        for _ in range(n):
            report = court.run_cycle()
            decision = cb.record(court.cycle, court.avg_merit, cost=cost)
            if decision.open:
                logger.warning("进化熔断：%s", decision.reason)
                break
    """

    def __init__(self, config: Optional[CircuitConfig] = None) -> None:
        self._cfg = config or CircuitConfig()
        self._merit_history: list[float] = []
        self._peak: float = 0.0
        self._cumulative_cost: float = 0.0
        self._consecutive_negative: int = 0
        self._state = CircuitState.CLOSED
        self._cycles_since_open: int = 0
        self._last_reason: str = ""

    # ── 状态查询 ──────────────────────────────────────────────

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def is_open(self) -> bool:
        return self._state == CircuitState.OPEN

    @property
    def peak_merit(self) -> float:
        return self._peak

    # ── 核心记录 ──────────────────────────────────────────────

    def record(
        self,
        cycle: int,
        mean_merit: float,
        cost: float = 0.0,
    ) -> CircuitDecision:
        """记录一轮进化的指标，返回熔断决策。

        Args:
            cycle: 进化轮序号（1-based）。
            mean_merit: 本轮所有活跃大臣的平均功勋。
            cost: 本轮消耗成本（可选；累计后与预算比较）。

        Returns:
            :class:`CircuitDecision`。若 ``open`` 为 True，调用方应**立即停止**
            后续进化轮。
        """
        self._merit_history.append(mean_merit)
        if mean_merit > self._peak:
            self._peak = mean_merit
        self._cumulative_cost += max(0.0, cost)

        # HALF_OPEN 冷却倒计时：到时间自动恢复 CLOSED
        if self._state == CircuitState.HALF_OPEN:
            self._cycles_since_open += 1
            if self._cycles_since_open >= self._cfg.cooldown_cycles:
                self._state = CircuitState.CLOSED
                self._cycles_since_open = 0
                logger.info("[CircuitBreaker] 冷却结束，恢复 CLOSED")
                return CircuitDecision(
                    state=self._state, open=False,
                    peak_merit=self._peak, last_merit=mean_merit,
                    cumulative_cost=self._cumulative_cost,
                )

        # 已熔断：计数冷却，到达冷却周期后转 HALF_OPEN 试探（允许继续）
        if self._state == CircuitState.OPEN:
            self._cycles_since_open += 1
            if self._cycles_since_open >= self._cfg.cooldown_cycles:
                self._state = CircuitState.HALF_OPEN
                self._cycles_since_open = 0
                logger.warning(
                    "[CircuitBreaker] 进入 HALF_OPEN 试探（原因：%s）",
                    self._last_reason,
                )
                return CircuitDecision(
                    state=self._state, open=False, reason=self._last_reason,
                    peak_merit=self._peak, last_merit=mean_merit,
                    cumulative_cost=self._cumulative_cost,
                )
            return CircuitDecision(
                state=self._state, open=True, reason=self._last_reason,
                peak_merit=self._peak, last_merit=mean_merit,
                cumulative_cost=self._cumulative_cost,
            )

        # ── CLOSED 状态：检查是否应当熔断 ──
        if self._cfg.min_cycles_before_trip and \
                len(self._merit_history) <= self._cfg.min_cycles_before_trip:
            return CircuitDecision(
                state=self._state, open=False,
                peak_merit=self._peak, last_merit=mean_merit,
                cumulative_cost=self._cumulative_cost,
            )

        reason = self._evaluate_trip(mean_merit)
        if reason:
            self._state = CircuitState.OPEN
            self._cycles_since_open = 0
            self._last_reason = reason
            logger.error("[CircuitBreaker] 已熔断（停止进化）：%s", reason)
            return CircuitDecision(
                state=self._state, open=True, reason=reason,
                peak_merit=self._peak, last_merit=mean_merit,
                cumulative_cost=self._cumulative_cost,
            )

        return CircuitDecision(
            state=self._state, open=False,
            peak_merit=self._peak, last_merit=mean_merit,
            cumulative_cost=self._cumulative_cost,
        )

    def _evaluate_trip(self, mean_merit: float) -> str:
        """返回非空字符串表示应当熔断及原因；否则返回空串。"""
        # 条件 1：相对历史峰值跌幅超阈值
        if self._peak > 0 and self._cfg.drop_fraction > 0:
            drop = (self._peak - mean_merit) / self._peak
            if drop >= self._cfg.drop_fraction:
                return (
                    f"平均功勋相对峰值跌幅 {drop:.1%} ≥ "
                    f"阈值 {self._cfg.drop_fraction:.1%}"
                )

        # 条件 2：连续负向轮
        if len(self._merit_history) >= 2:
            prev = self._merit_history[-2]
            if mean_merit < prev:
                self._consecutive_negative += 1
            else:
                self._consecutive_negative = 0
            if self._consecutive_negative >= self._cfg.consecutive_negative:
                return (
                    f"连续 {self._consecutive_negative} 轮平均功勋下降"
                )
        else:
            self._consecutive_negative = 0

        # 条件 3：累计成本超预算
        if self._cfg.cost_budget > 0 and \
                self._cumulative_cost >= self._cfg.cost_budget:
            return (
                f"累计成本 {self._cumulative_cost:.2f} ≥ 预算 "
                f"{self._cfg.cost_budget:.2f}"
            )

        return ""

    def reset(self) -> None:
        """复位熔断器（保留峰值记忆可选，此处全清）。"""
        self._merit_history = []
        self._peak = 0.0
        self._cumulative_cost = 0.0
        self._consecutive_negative = 0
        self._state = CircuitState.CLOSED
        self._cycles_since_open = 0
        self._last_reason = ""


@dataclass
class PromotionGateConfig:
    """晋升门配置。

    Attributes:
        required_consecutive_gains: 需要连续多少轮功勋正增长才允许晋升。
        min_merit: 晋升的最低绝对功勋门槛。
    """

    required_consecutive_gains: int = 3
    min_merit: float = 50.0


class PromotionGate:
    """晋升门：要求连续 N 轮功勋正增长，抑制噪声提拔。

    自进化系统的「晋升」不应是「单次功勋 > 50 就升」——那是奖励 hacking
    温床。本门记录每位大臣的功勋序列，仅当连续 ``required_consecutive_gains``
    轮正增长且达到 ``min_merit`` 时才放行一次晋升，随后计数器清零。
    """

    def __init__(self, config: Optional[PromotionGateConfig] = None) -> None:
        self._cfg = config or PromotionGateConfig()
        self._last_merit: dict[str, float] = {}
        self._streak: dict[str, int] = {}

    def record(self, minister: str, merit: float) -> bool:
        """记录某大臣本轮功勋，返回是否应当晋升。

        晋升判定：
            1. 功勋达到 ``min_merit``；
            2. 相对上一轮正增长；
            3. 已连续正增长达到 ``required_consecutive_gains`` 轮。
        一旦放行，该大臣的连续计数器清零（避免连续多轮重复晋升）。
        """
        prev = self._last_merit.get(minister)
        self._last_merit[minister] = merit

        if prev is None:
            self._streak[minister] = 0
            return False

        if merit >= self._cfg.min_merit and merit > prev:
            self._streak[minister] = self._streak.get(minister, 0) + 1
        else:
            self._streak[minister] = 0

        if self._streak[minister] >= self._cfg.required_consecutive_gains:
            self._streak[minister] = 0
            logger.info(
                "[PromotionGate] 放行晋升 %s（连续 %d 轮正增长，功勋 %.1f）",
                minister, self._cfg.required_consecutive_gains, merit,
            )
            return True
        return False

    def streak_of(self, minister: str) -> int:
        return self._streak.get(minister, 0)


__all__ = [
    "CircuitState",
    "CircuitConfig",
    "CircuitDecision",
    "CircuitBreaker",
    "PromotionGateConfig",
    "PromotionGate",
]

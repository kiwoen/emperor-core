"""
resource_guard — 资源预算护栏（防止自我进化失控吞噬算力/时间）。

自我进化循环一旦接入真实执行器（LLM 调用、真实 PR、网络评测），单个 cycle 可能
无限拉长或疯狂重试，拖垮宿主。调研结论里「资源上限」是 P0 级落地护栏之一：
一个安全的自修改系统必须能**在预算耗尽时干净地中止**，而不是跑飞。

本模块提供 :class:`ResourceBudget`——一个轻量上下文管理器 + 手动 tick 计数：

  * ``seconds``      —— 墙钟预算（默认 300s/cycle），超时即抛 :class:`ResourceBudgetExceeded`；
  * ``max_operations`` —— 可选的操作数上限（如 LLM 调用次数），超过即中止；
  * ``tick(n)``      —— 每完成一个「昂贵操作」调用一次，做双重检查。

默认预算给得很宽松（几百秒/轮），离线演示永远不会触发；但一旦切到 live 模式、
或某轮进化意外卡死，它就会把循环**安全熔断**并交回上层（CircuitBreaker 据此 trip），
而不是让进程永远挂起。
"""

from __future__ import annotations

import time
from typing import Optional


class ResourceBudgetExceeded(RuntimeError):
    """资源预算（时间/操作数）耗尽——自进化循环应中止并交回上层熔断。"""

    def __init__(self, message: str, *, used_seconds: float = 0.0,
                 limit_seconds: float = 0.0, operations: int = 0,
                 max_operations: Optional[int] = None) -> None:
        super().__init__(message)
        self.used_seconds = used_seconds
        self.limit_seconds = limit_seconds
        self.operations = operations
        self.max_operations = max_operations


class ResourceBudget:
    """每轮自进化的资源预算护栏（墙钟 + 操作数）。

    Args:
        seconds: 墙钟预算（秒）。``None`` / 0 表示不限时。
        max_operations: 操作数上限（如 LLM 调用次数）。``None`` 表示不限制。
        label: 预算标签（用于日志/报错，如 ``"cycle-3"``）。
    """

    def __init__(self, seconds: Optional[float] = 300.0,
                 max_operations: Optional[int] = None, label: str = "budget") -> None:
        self._seconds = None if seconds is None else float(seconds)
        self._max_ops = None if max_operations is None else int(max_operations)
        self.label = label
        self._start: Optional[float] = None
        self._ops = 0

    # ── 上下文管理 ──────────────────────────────────────────────

    def __enter__(self) -> "ResourceBudget":
        self._start = time.monotonic()
        self._ops = 0
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        # 即便正常退出，也做一次超时检查，便于在长尾卡死后立刻暴露。
        if exc_type is None:
            self._check_elapsed()
        # 返回 False：不吞掉异常（超时异常应继续上浮，由上层熔断）。
        return False

    # ── 运行时检查 ──────────────────────────────────────────────

    def tick(self, n: int = 1) -> None:
        """完成 n 个「昂贵操作」后调用；越限即抛。"""
        self._ops += int(n)
        if self._max_ops is not None and self._ops > self._max_ops:
            raise ResourceBudgetExceeded(
                f"[{self.label}] 操作数超限：{self._ops} > {self._max_ops}",
                used_seconds=self.used_seconds(), limit_seconds=self._seconds or 0.0,
                operations=self._ops, max_operations=self._max_ops,
            )
        self._check_elapsed()

    def _check_elapsed(self) -> None:
        if self._seconds is None or self._start is None:
            return
        elapsed = time.monotonic() - self._start
        if elapsed > self._seconds:
            raise ResourceBudgetExceeded(
                f"[{self.label}] 墙钟超时：{elapsed:.1f}s > {self._seconds:.1f}s",
                used_seconds=elapsed, limit_seconds=self._seconds,
                operations=self._ops, max_operations=self._max_ops,
            )

    # ── 观测 ────────────────────────────────────────────────────

    def used_seconds(self) -> float:
        if self._start is None:
            return 0.0
        return max(0.0, time.monotonic() - self._start)

    def used_fraction(self) -> float:
        if not self._seconds:
            return 0.0
        return min(1.0, self.used_seconds() / self._seconds)

    def remaining_seconds(self) -> Optional[float]:
        if self._seconds is None:
            return None
        return max(0.0, self._seconds - self.used_seconds())


def resource_budget(seconds: Optional[float] = 300.0, max_operations: Optional[int] = None,
                    label: str = "budget") -> ResourceBudget:
    """便捷构造器：``with resource_budget(seconds=60): ...``。"""
    return ResourceBudget(seconds=seconds, max_operations=max_operations, label=label)


__all__ = ["ResourceBudgetExceeded", "ResourceBudget", "resource_budget"]

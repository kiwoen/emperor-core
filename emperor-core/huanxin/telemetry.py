"""
P3.1 Telemetry — 自进化系统的可观测性快照（stdlib-only，零依赖）。

研究结论：自进化系统的头号失败模式是「silent failure」——系统在你
不知情的情况下持续劣化。可观测性是把「系统正在自我改进还是自我毁灭」
变成**一眼可见**的关键。本模块把分散的信号（熔断器状态、基准评测、
大臣功勋、进化事件、成本）聚合成一份 :class:`TelemetrySnapshot`，
可序列化为 JSON（``telemetry.json``）或一份 ``telemetry.js``
（``window.TELEMETRY = {...}``，供静态 ``dashboard.html`` 直接引用，
``file://`` 打开也能用，无需起服务、无跨域问题）。

设计原则：
  1. 零第三方依赖（只用标准库），离线可用；
  2. 所有数据源**可选**——给什么聚合什么，缺失的字段优雅降级；
  3. 绝不静默失败：序列化失败抛异常，不吞。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("huanxin.telemetry")

SCHEMA_VERSION = "1.0"


@dataclass
class MinisterTelemetry:
    """单个大臣的可观测状态。"""

    name: str
    domain: str = "general"
    merit: float = 0.0
    status: str = "active"          # active / shadow / eliminated
    success_streak: int = 0
    failure_streak: int = 0


@dataclass
class TelemetrySnapshot:
    """一份自进化系统的可观测性快照。"""

    generated_at: str
    schema_version: str = SCHEMA_VERSION
    circuit: Dict[str, Any] = field(default_factory=dict)   # 熔断器状态
    evaluation: Dict[str, Any] = field(default_factory=dict)  # 基准评测
    ministers: List[Dict[str, Any]] = field(default_factory=list)
    evolution: Dict[str, Any] = field(default_factory=dict)   # 进化事件统计
    cost: Dict[str, Any] = field(default_factory=dict)        # 成本
    health: str = "unknown"    # healthy / degraded / halted / unknown

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


def _circuit_dict(circuit_breaker: Any) -> Dict[str, Any]:
    """从 CircuitBreaker 提取状态（鸭子类型，避免硬依赖）。"""
    state = getattr(getattr(circuit_breaker, "state", None), "value", None)
    if state is None:
        state = str(getattr(circuit_breaker, "state", "unknown"))
    return {
        "state": state,
        "is_open": bool(getattr(circuit_breaker, "is_open", False)),
        "peak_merit": float(getattr(circuit_breaker, "peak_merit", 0.0)),
    }


def _eval_dict(eval_report: Any) -> Dict[str, Any]:
    """从 EvalReport 提取评测摘要。"""
    return {
        "cases": int(getattr(eval_report, "cases", 0)),
        "passed": int(getattr(eval_report, "passed", 0)),
        "failed": int(getattr(eval_report, "failed", 0)),
        "pass_rate": float(getattr(eval_report, "pass_rate", 0.0)),
        "per_domain": dict(getattr(eval_report, "per_domain", {}) or {}),
    }


def _derive_health(circuit: Dict[str, Any], evaluation: Dict[str, Any]) -> str:
    """由熔断器 + 评测推导一个粗粒度健康度。"""
    if circuit.get("is_open"):
        return "halted"
    if evaluation and evaluation.get("cases"):
        pr = evaluation.get("pass_rate", 0.0)
        if pr >= 0.99:
            return "healthy"
        if pr >= 0.7:
            return "degraded"
        return "degraded"
    return "unknown"


def collect(
    circuit_breaker: Any = None,
    eval_report: Any = None,
    ministers: Optional[List[MinisterTelemetry]] = None,
    evolution_events: Optional[List[Dict[str, Any]]] = None,
    cost_total: float = 0.0,
    cost_budget: float = 0.0,
) -> TelemetrySnapshot:
    """聚合各数据源为一份快照。所有参数可选。

    Args:
        circuit_breaker: :class:`~huanxin.court.circuit_breaker.CircuitBreaker`。
        eval_report: :class:`~huanxin.eval_bench.criteria.EvalReport`。
        ministers: 大臣状态列表。
        evolution_events: 进化事件字典列表（用于统计）。
        cost_total / cost_budget: 累计成本 / 预算。
    """
    circuit = _circuit_dict(circuit_breaker) if circuit_breaker is not None else {}
    evaluation = _eval_dict(eval_report) if eval_report is not None else {}

    events = list(evolution_events or [])
    evolution = {
        "event_count": len(events),
        "promotions": sum(1 for e in events if e.get("action") == "promote"),
        "eliminations": sum(1 for e in events if e.get("action") == "eliminate"),
        "recent": events[-10:],
    }

    cost = {
        "total": float(cost_total),
        "budget": float(cost_budget),
        "over_budget": bool(cost_budget > 0 and cost_total >= cost_budget),
    }

    snap = TelemetrySnapshot(
        generated_at=datetime.now(timezone.utc).isoformat(),
        circuit=circuit,
        evaluation=evaluation,
        ministers=[asdict(m) for m in (ministers or [])],
        evolution=evolution,
        cost=cost,
    )
    snap.health = _derive_health(circuit, evaluation)
    return snap


def write_json(snapshot: TelemetrySnapshot, path: str) -> str:
    """把快照写成 ``telemetry.json``。返回路径。"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(snapshot.to_json())
    logger.info("[Telemetry] 已写出 JSON 快照：%s", path)
    return path


def write_js(snapshot: TelemetrySnapshot, path: str) -> str:
    """把快照写成 ``telemetry.js``（``window.TELEMETRY = {...}``）。

    供静态 ``dashboard.html`` 用 ``<script src="telemetry.js">`` 直接引用，
    ``file://`` 打开即可，无需起本地服务、无跨域限制。
    """
    payload = snapshot.to_json()
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("window.TELEMETRY = ")
        fh.write(payload)
        fh.write(";\n")
    logger.info("[Telemetry] 已写出 JS 快照：%s", path)
    return path


__all__ = [
    "MinisterTelemetry",
    "TelemetrySnapshot",
    "collect",
    "write_json",
    "write_js",
    "SCHEMA_VERSION",
]

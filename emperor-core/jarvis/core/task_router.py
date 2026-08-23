"""
Task Router — 主路由分发器（master-router → specialized minister dispatch）。

将多智能体技能集群中的「主编路由 → 专项技能分发」模式迁移为通用的任务路由：

- **任务分类**：`classify_task_type` 将自然语言意图归类为 code / math / writing /
  research / security / planning / general。
- **能力打分路由**：`route_to_minister` 用每位大臣的 `can_handle(intent)` 评分
  选出最优承办大臣；评分相同时按「能力质量分 → 失败次数」稳定 tie-break；
  并可按任务类型对同域大臣加权（类型感知路由），与技能集群的「类型路由」一致。
- **模型档建议**：结合 `ModelRouter.estimate_complexity` 给出成本最优的模型档。

该模块为纯函数、零副作用、可单测；不依赖网络或 LLM。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from jarvis.court.minister import Edict, Minister
from jarvis.core.router import ModelRouter

logger = logging.getLogger("jarvis.core.task_router")


# ── 任务类型与关键词 ────────────────────────────────────────────────

TASK_TYPES: dict[str, list[str]] = {
    "code": ["写代码", "实现", "编码", "debug", "调试", "重构", "代码", "生成函数", "函数", "排序", "编程", "算法", "开发",
             "refactor", "implement", "code", "bug", "代码审查", "审查代码", "code review"],
    "math": ["计算", "数学", "方程", "微积分", "线性代数", "算式",
             "math", "calculus", "calculate", "equation"],
    "writing": ["写作", "文章", "总结", "文案", "报告", "写信",
                "write", "summarize", "draft", "article"],
    "research": ["研究", "搜索", "调研", "查证", "检索",
                 "research", "search", "investigate"],
    "security": ["安全", "漏洞", "审计", "合规", "风险",
                 "security", "vulnerability", "audit", "compliance"],
    "planning": ["规划", "计划", "排期", "安排", "分解任务",
                 "plan", "schedule", "roadmap"],
    # general 作为兜底，无专属关键词
}


def classify_task_type(intent: str) -> str:
    """将意图归类为任务类型。

    返回：code / math / writing / research / security / planning / general。
    多个类型命中时取命中词数最多者；均未命中返回 general。
    """
    text = (intent.intent if isinstance(intent, Edict) else str(intent)).lower()
    scored: dict[str, int] = {}
    for ttype, kws in TASK_TYPES.items():
        if ttype == "general":
            continue
        hits = sum(1 for kw in kws if kw.lower() in text)
        if hits:
            scored[ttype] = hits
    if not scored:
        return "general"
    # 命中数最多；并列时按 TASK_TYPES 定义顺序（更稳定）
    return max(scored, key=lambda t: (scored[t], -list(TASK_TYPES).index(t)))


@dataclass
class DispatchPlan:
    """一次路由决策的结果。"""

    minister: Optional[Minister]
    score: float                 # 原始 can_handle 评分 [0,1]
    task_type: str
    suggested_tier: str          # 建议模型档 cheap/standard/premium


def _raw_intent(intent: Any) -> str:
    if isinstance(intent, Edict):
        return intent.intent
    return str(intent)


def _sort_key(minister: Minister, intent_text: str, task_type: str, type_aware: bool):
    """排序键：(评分, 质量分, -失败数)。type_aware 时对同域大臣加权。"""
    s = minister.can_handle(intent_text)
    if type_aware and minister.profile.domain:
        if minister.profile.domain.lower() == task_type:
            s += 0.1  # 类型感知加权：与技能集群「类型路由」一致
    return (s, minister.profile.quality_score, -minister.failure_count)


def route_to_minister(
    intent: Any,
    ministers: list[Minister],
    default: Optional[Minister] = None,
    type_aware: bool = True,
) -> tuple[Optional[Minister], float]:
    """选出最适合承办该意图的大臣。

    Args:
        intent: 字符串或 Edict。
        ministers: 候选大臣列表。
        default: 当无任何大臣匹配（can_handle 全为 0）时回退的大臣。
        type_aware: 是否按任务类型对同域大臣加权。

    Returns:
        (最优大臣, 原始 can_handle 评分)。无候选时 (default, 0.0)。
    """
    intent_text = _raw_intent(intent)
    task_type = classify_task_type(intent_text)
    candidates = [m for m in ministers if isinstance(m, Minister)]
    if not candidates:
        return (default, 0.0) if default is not None else (None, 0.0)

    best = max(
        candidates,
        key=lambda m: _sort_key(m, intent_text, task_type, type_aware),
    )
    best_score = best.can_handle(intent_text)

    # 无人真正匹配且提供了 default → 回退
    if best_score <= 0.0 and default is not None:
        return default, 0.0
    return best, best_score


def plan_dispatch(
    intent: Any,
    ministers: list[Minister],
    default: Optional[Minister] = None,
    type_aware: bool = True,
    router: Optional[ModelRouter] = None,
) -> DispatchPlan:
    """生成完整路由方案（含大臣、评分、任务类型、模型档建议）。

    便捷封装：`route_to_minister` + `ModelRouter.estimate_complexity`。
    """
    intent_text = _raw_intent(intent)
    task_type = classify_task_type(intent_text)
    minister, score = route_to_minister(
        intent, ministers, default=default, type_aware=type_aware
    )
    if router is None:
        router = ModelRouter()
    tier = router.estimate_complexity(intent_text, domain=task_type)
    return DispatchPlan(
        minister=minister,
        score=score,
        task_type=task_type,
        suggested_tier=tier,
    )

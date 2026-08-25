"""
Evaluation Metrics — standardised scoring functions for agent evaluation.

Each metric function accepts raw evaluation data and returns a float in [0, 1]
(where 1.0 = perfect).  All metrics include optional per-case detail arrays.

Metrics:
    task_success_rate      — 任务成功率
    tool_call_accuracy     — 工具调用准确率
    response_time_ms       — 响应时间 (milliseconds)
    route_accuracy         — 路由准确率
    healing_success_rate   — 自愈成功率
    evolution_convergence  — 进化收敛速度

Usage:
    from huanxin.eval.metrics import task_success_rate, compute_all_metrics

    rate = task_success_rate([True, True, False, True])  # 0.75
    report = compute_all_metrics(results, times=[12, 34, 56, 78])
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence


# ══════════════════════════════════════════════════════════════════
# Single metrics
# ══════════════════════════════════════════════════════════════════


def task_success_rate(passed: Sequence[bool]) -> float:
    """任务成功率：成功任务数 / 总任务数。

    Args:
        passed: 每个任务是否成功的布尔序列。

    Returns:
        [0, 1] 之间的成功率。空序列返回 0.0。
    """
    if not passed:
        return 0.0
    return sum(1 for p in passed if p) / len(passed)


def tool_call_accuracy(
    expected_tools: Sequence[str],
    actual_tools: Sequence[str],
) -> float:
    """工具调用准确率：正确调用的工具数 / 总调用数。

    逐一比对每个位置上的 expected vs actual，返回精确匹配比例。

    Args:
        expected_tools: 每个任务期望调用的工具名列表。
        actual_tools: 每个任务实际调用的工具名列表。

    Returns:
        [0, 1] 之间的准确率。空序列返回 0.0。
    """
    if not expected_tools:
        return 0.0
    correct = sum(
        1 for e, a in zip(expected_tools, actual_tools) if e == a
    )
    return correct / len(expected_tools)


def response_time_ms(
    times_ms: Sequence[float],
    aggregator: str = "mean",
) -> float:
    """响应时间度量。

    Args:
        times_ms: 每次调用的响应时间列表（毫秒）。
        aggregator: 聚合方式 — "mean" / "median" / "p95" / "p99" / "max"。

    Returns:
        聚合后的响应时间（毫秒）。空序列返回 0.0。
    """
    if not times_ms:
        return 0.0

    sorted_times = sorted(times_ms)
    n = len(sorted_times)

    if aggregator == "mean":
        return statistics.mean(sorted_times)
    elif aggregator == "median":
        return statistics.median(sorted_times)
    elif aggregator == "p95":
        idx = int(n * 0.95)
        return sorted_times[min(idx, n - 1)]
    elif aggregator == "p99":
        idx = int(n * 0.99)
        return sorted_times[min(idx, n - 1)]
    elif aggregator == "max":
        return sorted_times[-1]
    else:
        raise ValueError(f"Unknown aggregator: {aggregator}")


def route_accuracy(
    expected_routes: Sequence[str],
    actual_routes: Sequence[str],
) -> float:
    """路由准确率：正确路由到目标 domain / capability 的比例。

    Args:
        expected_routes: 期望的路由目标列表。
        actual_routes: 实际的路由目标列表。

    Returns:
        [0, 1] 之间的路由准确率。空序列返回 0.0。
    """
    if not expected_routes:
        return 0.0
    correct = sum(
        1 for e, a in zip(expected_routes, actual_routes) if e == a
    )
    return correct / len(expected_routes)


def healing_success_rate(
    healing_results: Sequence[bool],
    strategy_depths: Optional[Sequence[int]] = None,
) -> float:
    """自愈成功率：成功自愈的故障数 / 总故障数。

    支持对多级 fallback 策略的加权：primary 成功权重 1.0，
    fallback_1 成功权重 0.8，fallback_2 成功权重 0.6，以此类推。

    Args:
        healing_results: 每次自愈是否成功的布尔序列。
        strategy_depths: 每次自愈使用的策略深度（0=primary, 1=fallback_1, ...）。
                         为 None 时使用等权。

    Returns:
        [0, 1] 之间的自愈成功率。空序列返回 0.0。
    """
    if not healing_results:
        return 0.0

    if strategy_depths is None:
        return sum(1 for h in healing_results if h) / len(healing_results)

    # Weighted: primary success = 1.0, fallback_N success = 1.0 - 0.2 * depth
    weighted_sum = 0.0
    max_weight = 0.0
    for success, depth in zip(healing_results, strategy_depths):
        weight = max(0.2, 1.0 - 0.2 * depth) if depth >= 0 else 1.0
        max_weight += 1.0
        if success:
            weighted_sum += weight

    return weighted_sum / max_weight if max_weight > 0 else 0.0


def evolution_convergence(
    scores_per_generation: Sequence[Sequence[float]],
    threshold: float = 0.95,
) -> float:
    """进化收敛速度：达到目标分数的代数越少，收敛越快。

    返回值越大（接近 1.0）说明收敛越快。若在给定代数内未达到阈值，
    返回达到的最高分与阈值的比例。

    Args:
        scores_per_generation: 每一代的最佳分数列表，例如 [[0.6, 0.7], [0.75, 0.8], [0.9, 0.92]]。
        threshold: 目标收敛阈值，默认 0.95。

    Returns:
        [0, 1] 之间的收敛速度分数。1.0 表示在第一代就收敛。
    """
    if not scores_per_generation:
        return 0.0

    total_generations = len(scores_per_generation)

    # 每代取最佳分数
    best_per_gen = [max(gen) if gen else 0.0 for gen in scores_per_generation]

    # 找到首次达到阈值的代数
    for gen_idx, best in enumerate(best_per_gen):
        if best >= threshold:
            # 收敛越快（代数越少），分数越高
            # 第 1 代收敛 → 1.0，最后一代才收敛 → 0.1
            return 1.0 - (gen_idx / max(total_generations, 1)) * 0.9

    # 未收敛：返回最高分与阈值的比例
    max_score = max(best_per_gen) if best_per_gen else 0.0
    return (max_score / threshold) * 0.5  # 未达标，最高 0.5


# ══════════════════════════════════════════════════════════════════
# Composite metrics computation
# ══════════════════════════════════════════════════════════════════


@dataclass
class MetricsReport:
    """聚合指标报告 — 一次评测运行的全部度量。"""

    task_success_rate: float = 0.0
    tool_call_accuracy: float = 0.0
    response_time_mean_ms: float = 0.0
    response_time_median_ms: float = 0.0
    response_time_p95_ms: float = 0.0
    route_accuracy: float = 0.0
    healing_success_rate: float = 0.0
    evolution_convergence: float = 0.0

    # Per-case detail arrays (for drill-down)
    per_case_passed: List[bool] = field(default_factory=list)
    per_case_tool_match: List[bool] = field(default_factory=list)
    per_case_route_match: List[bool] = field(default_factory=list)
    per_case_healing_success: List[bool] = field(default_factory=list)

    def overall_score(self) -> float:
        """计算加权总分。

        权重分配：
            task_success_rate:      0.25
            tool_call_accuracy:     0.20
            response_time (归一化): 0.15
            route_accuracy:         0.20
            healing_success_rate:   0.10
            evolution_convergence:  0.10
        """
        # 响应时间归一化：<100ms = 满分 1.0, >5000ms = 0.0
        rt_norm = max(0.0, min(1.0, 1.0 - (self.response_time_mean_ms - 100) / 4900))

        weights = {
            "task": 0.25,
            "tool": 0.20,
            "rt": 0.15,
            "route": 0.20,
            "healing": 0.10,
            "evolution": 0.10,
        }

        return (
            weights["task"] * self.task_success_rate
            + weights["tool"] * self.tool_call_accuracy
            + weights["rt"] * rt_norm
            + weights["route"] * self.route_accuracy
            + weights["healing"] * self.healing_success_rate
            + weights["evolution"] * self.evolution_convergence
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall_score": round(self.overall_score(), 4),
            "task_success_rate": round(self.task_success_rate, 4),
            "tool_call_accuracy": round(self.tool_call_accuracy, 4),
            "response_time_mean_ms": round(self.response_time_mean_ms, 2),
            "response_time_median_ms": round(self.response_time_median_ms, 2),
            "response_time_p95_ms": round(self.response_time_p95_ms, 2),
            "route_accuracy": round(self.route_accuracy, 4),
            "healing_success_rate": round(self.healing_success_rate, 4),
            "evolution_convergence": round(self.evolution_convergence, 4),
            "per_case_count": len(self.per_case_passed),
        }


def compute_all_metrics(
    passed: Sequence[bool],
    expected_tools: Optional[Sequence[str]] = None,
    actual_tools: Optional[Sequence[str]] = None,
    times_ms: Optional[Sequence[float]] = None,
    expected_routes: Optional[Sequence[str]] = None,
    actual_routes: Optional[Sequence[str]] = None,
    healing_results: Optional[Sequence[bool]] = None,
    healing_depths: Optional[Sequence[int]] = None,
    evolution_scores: Optional[Sequence[Sequence[float]]] = None,
    evolution_threshold: float = 0.95,
) -> MetricsReport:
    """一次性计算全部六项指标。

    Args:
        passed: 每个任务是否成功的布尔列表（必填）。
        expected_tools: 期望的工具调用列表。
        actual_tools: 实际的工具调用列表。
        times_ms: 响应时间列表（毫秒）。
        expected_routes: 期望的路由列表。
        actual_routes: 实际的路由列表。
        healing_results: 自愈结果列表。
        healing_depths: 自愈策略深度列表。
        evolution_scores: 每代分数列表。
        evolution_threshold: 进化收敛阈值。

    Returns:
        MetricsReport 聚合报告。
    """
    report = MetricsReport()

    # 1. Task success rate
    report.task_success_rate = task_success_rate(passed)
    report.per_case_passed = list(passed)

    # 2. Tool call accuracy
    if expected_tools is not None and actual_tools is not None:
        report.tool_call_accuracy = tool_call_accuracy(expected_tools, actual_tools)
        report.per_case_tool_match = [e == a for e, a in zip(expected_tools, actual_tools)]

    # 3. Response time
    if times_ms is not None and times_ms:
        report.response_time_mean_ms = response_time_ms(times_ms, "mean")
        report.response_time_median_ms = response_time_ms(times_ms, "median")
        report.response_time_p95_ms = response_time_ms(times_ms, "p95")

    # 4. Route accuracy
    if expected_routes is not None and actual_routes is not None:
        report.route_accuracy = route_accuracy(expected_routes, actual_routes)
        report.per_case_route_match = [e == a for e, a in zip(expected_routes, actual_routes)]

    # 5. Healing success rate
    if healing_results is not None:
        report.healing_success_rate = healing_success_rate(healing_results, healing_depths)
        report.per_case_healing_success = list(healing_results)

    # 6. Evolution convergence
    if evolution_scores is not None:
        report.evolution_convergence = evolution_convergence(evolution_scores, evolution_threshold)

    return report

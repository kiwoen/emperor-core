"""Phase 12 续：经验记忆**驱动**派发（闭环验证）。

Phase 12 让经验「记录 + 落盘」。本文件验证这份额外的一环——累积的经验**真的被用来改善
派发决策**：在多个大臣共享同一领域时，历史成功率更高的大臣应优先拿到该域任务（exploit
已被证明的能力），而无记忆时退化为原序轮转（无回归）。

覆盖：
  A. ``_rank_ministers`` 纯函数：按 (大臣,领域) 历史成功率降序；无记忆时保持原序。
  B. 端到端：两个 math 大臣 + 记忆表明 beta 优于 alpha，3 个 math 任务派发后，
     beta 拿到的任务数严格多于 alpha（记忆真正改变了「谁执行」）。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jarvis.court.circuit_breaker import CircuitBreaker, CircuitConfig  # noqa: E402
from jarvis.court.court import Court, CourtConfig  # noqa: E402
from jarvis.court.memory import CourtMemory, memory_from_memorial  # noqa: E402
from jarvis.court.real_executor import RealTaskExecutor  # noqa: E402
from jarvis.self_evolve import (  # noqa: E402
    RecordingWriteChannel, SelfEvolutionEngine, SimulatedTask, _rank_ministers,
)
from jarvis.self_evolve_config import SelfEvolveConfig  # noqa: E402
from jarvis.vcs.writeback_gate import WritebackGate  # noqa: E402


# ----------------------------------------------------------------------
# A) 纯函数单测
# ----------------------------------------------------------------------
def test_rank_ministers_no_memory_keeps_order():
    group = ["math_alpha", "math_beta"]
    # 无记忆 → 稳定原序（等价轮转，无回归）
    assert _rank_ministers(group, "math", {}) == ["math_alpha", "math_beta"]


def test_rank_ministers_sorts_by_historical_success():
    group = ["math_alpha", "math_beta"]
    q = {("math_beta", "math"): 0.9, ("math_alpha", "math"): 0.4}
    # beta 历史更好 → 排在最前（优先拿任务）
    assert _rank_ministers(group, "math", q) == ["math_beta", "math_alpha"]


def test_rank_ministers_single_minister():
    assert _rank_ministers(["only"], "math", {}) == ["only"]


# ----------------------------------------------------------------------
# B) 端到端闭环：记忆驱动派发
# ----------------------------------------------------------------------
def _build_dual_math_engine(memory, memory_path, tmp_path):
    """两个 math 大臣共享领域，便于观察记忆驱动派发。"""
    court = Court(CourtConfig(circuit_breaker=CircuitBreaker(CircuitConfig())))
    court.register_many([
        {"name": "math_alpha", "domain": "math", "temperature": 0.9, "confidence_baseline": 0.6},
        {"name": "math_beta", "domain": "math", "temperature": 0.2, "confidence_baseline": 0.9},
    ])
    executor = RealTaskExecutor(seed=7, memory=memory)
    tasks = [
        SimulatedTask("t-math-1", "计算 2 + 3", "math", expected="5"),
        SimulatedTask("t-math-2", "计算 10 * 4", "math", expected="40"),
        SimulatedTask("t-math-3", "计算 100 - 1", "math", expected="99"),
    ]
    engine = SelfEvolutionEngine(
        court=court,
        executor=executor,
        tasks=tasks,
        write_channel=RecordingWriteChannel(),
        write_gate=WritebackGate(min_pass_rate=0.0, forbid_regression=False),
        genome_state_path=str(tmp_path / "genome_state.json"),
        use_safety_gate=False,
        enable_snapshots=False,
        self_learn=True,
        memory=memory,
        use_memory=True,
        memory_path=memory_path,
    )
    return engine


def test_memory_drives_routing_to_proven_minister(tmp_path):
    memory_path = str(tmp_path / "memory.json")

    # 预置经验：beta 在 math 领域历史全胜，alpha 历史全败 → 记忆应让 beta 优先拿任务。
    mem = CourtMemory()
    mem.record(memory_from_memorial(
        "math_beta", "seed-b", "math", "历史表现好", True, 0.9, 1.0, 90.0))
    mem.record(memory_from_memorial(
        "math_alpha", "seed-a", "math", "历史表现差", False, 0.3, 1.0, 30.0))

    engine = _build_dual_math_engine(mem, memory_path, tmp_path)
    # 注意：resume=False，避免 _load_memory 用空盘覆盖我们预置的记忆。
    engine.run(n_cycles=1, tasks_per_minister=2)

    # 派发后，统计 math 领域下各大臣被记录的任务数。
    counts = {"math_alpha": 0, "math_beta": 0}
    for e in engine._memory._entries:
        if e.domain == "math" and e.minister_name in counts:
            counts[e.minister_name] += 1

    # 预置 2 条 + 本轮 3 条 = 5 条；记忆驱动下 beta 应拿更多（2 vs 1）。
    assert counts["math_beta"] > counts["math_alpha"], (
        f"经验记忆未驱动派发：alpha={counts['math_alpha']}, beta={counts['math_beta']}"
    )

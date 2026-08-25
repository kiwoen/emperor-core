"""Phase 12 回归测试：持久化经验记忆（自我学习跨重启累积）。

覆盖：
  1. CourtMemory.save / load 往返（含多领域条目）。
  2. 跨重启累积：保存后在全新实例中 load，条目数与领域统计保持一致。
  3. 加载不存在/损坏文件时安全回退为空记忆（不阻断运行）。
  4. 引擎接线：SelfEvolutionEngine 以 use_memory=True 运行后，
     经验记忆落盘且**覆盖所有任务领域**（验证 _execute_tasks 的领域亲和派发修复，
     Phase 11 仅 math 的 bug 不再复现）。
  5. --resume 续跑：第二轮从已落盘记忆继续累积，样本数单调不减。
"""

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from huanxin.court.court import Court, CourtConfig  # noqa: E402
from huanxin.court.memory import CourtMemory, memory_from_memorial  # noqa: E402
from huanxin.court.real_executor import RealTaskExecutor  # noqa: E402
from huanxin.self_evolve import (  # noqa: E402
    RecordingWriteChannel, SelfEvolutionEngine, default_ministers, real_default_tasks,
)
from huanxin.self_evolve_config import SelfEvolveConfig  # noqa: E402


# ----------------------------------------------------------------------
# 1) save / load 往返
# ----------------------------------------------------------------------
def test_memory_save_load_roundtrip(tmp_path):
    p = str(tmp_path / "mem.json")
    mem = CourtMemory()
    mem.record(memory_from_memorial(
        "math_alpha", "task-math-1", "math", "计算 1+1", True, 0.9, 1.0, 90.0))
    mem.record(memory_from_memorial(
        "code_beta", "task-code-1", "code", "写快排", True, 0.8, 2.0, 80.0))
    mem.record(memory_from_memorial(
        "gen_epsilon", "task-fact-1", "factual", "法国首都", True, 0.7, 1.5, 70.0))

    saved = mem.save(p)
    assert os.path.exists(saved)
    assert len(mem._entries) == 3

    reloaded = CourtMemory.load(p)
    assert len(reloaded._entries) == 3
    domains = {e.domain for e in reloaded._entries}
    assert domains == {"math", "code", "factual"}


# ----------------------------------------------------------------------
# 2) 跨重启累积：新实例 load 后领域统计一致
# ----------------------------------------------------------------------
def test_memory_cross_restart_accumulates(tmp_path):
    p = str(tmp_path / "mem.json")
    mem = CourtMemory()
    for i in range(5):
        mem.record(memory_from_memorial(
            "math_alpha", f"m{i}", "math", f"math {i}", i % 2 == 0, 0.9, 1.0, 90.0))
    mem.save(p)

    fresh = CourtMemory.load(p)
    stats = {s.domain: s.total_entries for s in fresh.get_all_domain_stats()}
    assert stats.get("math") == 5
    # 成功率 = 成功数 / 总数：i=0,2,4 成功 → 3/5
    math_stat = next(s for s in fresh.get_all_domain_stats() if s.domain == "math")
    assert abs(math_stat.success_rate - 0.6) < 1e-9


# ----------------------------------------------------------------------
# 3) 不存在 / 损坏文件安全回退
# ----------------------------------------------------------------------
def test_memory_load_missing_returns_empty(tmp_path):
    mem = CourtMemory.load(str(tmp_path / "does_not_exist.json"))
    assert isinstance(mem, CourtMemory)
    assert len(mem._entries) == 0


def test_memory_load_corrupt_returns_empty(tmp_path):
    p = str(tmp_path / "corrupt.json")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("{ this is not json")
    mem = CourtMemory.load(p)
    assert len(mem._entries) == 0


# ----------------------------------------------------------------------
# 4) 引擎接线：多领域派发修复 —— 经验记忆覆盖全部任务领域
# ----------------------------------------------------------------------
def _build_engine(cfg: SelfEvolveConfig, memory_path: str, memory, genome_state_path: str):
    """与 scripts/run_self_evolve.py::run_orchestrator 等价的精简构造。"""
    from huanxin.court.circuit_breaker import CircuitBreaker, CircuitConfig
    from huanxin.vcs.writeback_gate import WritebackGate

    court = Court(CourtConfig(
        circuit_breaker=CircuitBreaker(CircuitConfig()),
    ))
    court.register_many(default_ministers())

    executor = RealTaskExecutor(seed=cfg.seed, memory=memory)
    tasks = real_default_tasks()

    engine = SelfEvolutionEngine(
        court=court,
        executor=executor,
        tasks=tasks,
        write_channel=RecordingWriteChannel(),
        write_gate=WritebackGate(min_pass_rate=0.0, forbid_regression=False),
        genome_state_path=genome_state_path,
        use_safety_gate=False,
        enable_snapshots=False,
        self_learn=True,
        memory=memory,
        use_memory=True,
        memory_path=memory_path,
    )
    return engine


def test_engine_records_all_domains(tmp_path):
    memory_path = str(tmp_path / "memory.json")
    genome_state_path = str(tmp_path / "genome_state.json")

    cfg = SelfEvolveConfig(seed=7)
    memory = CourtMemory()
    engine = _build_engine(cfg, memory_path, memory, genome_state_path)

    report = engine.run(n_cycles=2, tasks_per_minister=2)
    assert not report.halted

    # 记忆已落盘
    assert os.path.exists(memory_path)
    # 重新加载（模拟重启）后，领域覆盖应包含全部任务领域，而非仅 math
    reloaded = CourtMemory.load(memory_path)
    domains = {e.domain for e in reloaded._entries}
    # real_default_tasks 覆盖 math / factual / code / retrieval
    assert {"math", "factual", "code", "retrieval"}.issubset(domains), (
        f"经验记忆未覆盖全部领域，仅见：{sorted(domains)}"
    )
    # 每个领域至少应有样本（派发修复的硬证据）
    stats = {s.domain: s.total_entries for s in reloaded.get_all_domain_stats()}
    for d in ("math", "factual", "code", "retrieval"):
        assert stats.get(d, 0) > 0, f"领域 {d} 样本数为 0（派发仍漏该领域）"


# ----------------------------------------------------------------------
# 5) --resume 续跑：经验记忆跨运行累积（样本数单调不减）
# ----------------------------------------------------------------------
def test_engine_resume_accumulates(tmp_path):
    memory_path = str(tmp_path / "memory.json")
    genome_state_path = str(tmp_path / "genome_state.json")

    cfg = SelfEvolveConfig(seed=7)

    # 第一轮
    mem1 = CourtMemory()
    eng1 = _build_engine(cfg, memory_path, mem1, genome_state_path)
    eng1.run(n_cycles=1, tasks_per_minister=2)

    after_first = len(CourtMemory.load(memory_path)._entries)
    assert after_first > 0

    # 第二轮（模拟重启 + --resume）：同一 memory_path，新 CourtMemory 实例从盘恢复
    mem2 = CourtMemory.load(memory_path)
    eng2 = _build_engine(cfg, memory_path, mem2, genome_state_path)
    eng2.run(n_cycles=1, tasks_per_minister=2)

    after_second = len(CourtMemory.load(memory_path)._entries)
    # 续跑应在已有样本上继续累积，而非清零
    assert after_second >= after_first, (
        f"resume 后样本数未累积：{after_first} → {after_second}"
    )

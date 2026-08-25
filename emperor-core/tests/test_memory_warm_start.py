"""Phase 12 续²：记忆驱动基因 warm-start（记忆 ↔ 基因 闭环统一）。

让「累积经验」不仅能记录/驱动派发，还能在冷启动（或新部署）时**轻推基因**——
用历史经验把初始基因朝「该域最被证明的方向」校准，使新实例直接站在历史经验肩上。
默认关闭（opt-in）→ 不改变既有行为；仅对「记忆中有该域历史」的大臣生效，无历史则保持原样。

覆盖：
  A. 开启且记忆中有该域历史 → 大臣 confidence/temperature 朝历史经验方向移动。
  B. 记忆中无该大臣该域历史 → 基因保持不变（不臆造、不串域）。
  C. 默认关闭 → 基因完全不变。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from huanxin.court.circuit_breaker import CircuitBreaker, CircuitConfig  # noqa: E402
from huanxin.court.court import Court, CourtConfig  # noqa: E402
from huanxin.court.memory import CourtMemory, memory_from_memorial  # noqa: E402
from huanxin.court.real_executor import RealTaskExecutor  # noqa: E402
from huanxin.self_evolve import (  # noqa: E402
    RecordingWriteChannel, SelfEvolutionEngine, real_default_tasks,
)
from huanxin.self_evolve_config import SelfEvolveConfig  # noqa: E402
from huanxin.vcs.writeback_gate import WritebackGate  # noqa: E402


def _build(tmp_path, memory, warm_start=False):
    court = Court(CourtConfig(circuit_breaker=CircuitBreaker(CircuitConfig())))
    court.register_many([
        {"name": "math_alpha", "domain": "math", "temperature": 0.9, "confidence_baseline": 0.6},
        {"name": "code_beta", "domain": "code", "temperature": 0.2, "confidence_baseline": 0.7},
    ])
    executor = RealTaskExecutor(seed=7, memory=memory)
    engine = SelfEvolutionEngine(
        court=court,
        executor=executor,
        tasks=real_default_tasks(),
        write_channel=RecordingWriteChannel(),
        write_gate=WritebackGate(min_pass_rate=0.0, forbid_regression=False),
        genome_state_path=str(tmp_path / "genome_state.json"),
        use_safety_gate=False,
        enable_snapshots=False,
        self_learn=False,
        memory=memory,
        use_memory=True,
        memory_path=str(tmp_path / "memory.json"),
        warm_start_from_memory=warm_start,
    )
    return engine


def _genes(engine, name):
    g = engine.court._sm._genomes[name]
    get = (lambda k, d: g.get(k, d)) if isinstance(g, dict) else (lambda k, d: getattr(g, k, d))
    return float(get("confidence_baseline", 0.0)), float(get("temperature", 0.0))


def test_warm_start_moves_genes_toward_experience(tmp_path):
    mem = CourtMemory()
    # math 领域历史全胜（成功率 1.0）→ 预期 confidence 上调、temperature 下调（朝 OPT）。
    for i in range(4):
        mem.record(memory_from_memorial(
            "math_alpha", f"m{i}", "math", f"math {i}", True, 0.9, 1.0, 90.0))

    engine = _build(tmp_path, mem, warm_start=True)
    conf0, temp0 = _genes(engine, "math_alpha")
    engine._warm_start_genes_from_memory()
    conf1, temp1 = _genes(engine, "math_alpha")

    assert conf1 > conf0, f"confidence 应朝历史成功率上调：{conf0}→{conf1}"
    assert temp1 < temp0, f"temperature 应朝最优区下调：{temp0}→{temp1}"


def test_warm_start_leaves_unseen_domain_untouched(tmp_path):
    mem = CourtMemory()
    for i in range(4):
        mem.record(memory_from_memorial(
            "math_alpha", f"m{i}", "math", f"math {i}", True, 0.9, 1.0, 90.0))

    engine = _build(tmp_path, mem, warm_start=True)
    conf0, temp0 = _genes(engine, "code_beta")  # code 域无任何历史
    engine._warm_start_genes_from_memory()
    conf1, temp1 = _genes(engine, "code_beta")

    assert (conf0, temp0) == (conf1, temp1), "无历史的大臣基因不应被改动"


def test_warm_start_flag_gates_call_in_run(tmp_path):
    """开关应真正控制 run() 是否调用 warm-start（用 spy 验证，避开进化算子对基因的干扰）。"""
    mem = CourtMemory()
    for i in range(4):
        mem.record(memory_from_memorial(
            "math_alpha", f"m{i}", "math", f"math {i}", True, 0.9, 1.0, 90.0))

    # 默认关闭 → run() 不应调用 warm-start。
    eng_off = _build(tmp_path, mem, warm_start=False)
    called_off: list = []
    orig = eng_off._warm_start_genes_from_memory
    eng_off._warm_start_genes_from_memory = lambda: called_off.append(1) or orig()
    eng_off.run(n_cycles=1)
    assert called_off == [], "默认关闭时 run() 不应调用 warm-start（零回归）"

    # 开启 → run() 应调用 warm-start。
    eng_on = _build(tmp_path, mem, warm_start=True)
    called_on: list = []
    orig2 = eng_on._warm_start_genes_from_memory
    eng_on._warm_start_genes_from_memory = lambda: called_on.append(1) or orig2()
    eng_on.run(n_cycles=1)
    assert called_on == [1], "开启时 run() 应调用 warm-start"


def _conf_move_with_n_samples(tmp_path, n: int) -> float:
    """构造 math_alpha 在 math 域 n 次全胜的记忆，返回冷启动→warm-start 的 confidence 移动量。"""
    mem = CourtMemory()
    for i in range(n):
        mem.record(memory_from_memorial(
            "math_alpha", f"m{i}", "math", f"math {i}", True, 1.0, 1.0, 95.0))
    engine = _build(tmp_path, mem, warm_start=True)
    conf0, _ = _genes(engine, "math_alpha")
    engine._warm_start_genes_from_memory()
    conf1, _ = _genes(engine, "math_alpha")
    return abs(conf1 - conf0)


def test_warm_start_step_grows_with_sample_count(tmp_path):
    """自适应步长：同成功率(全胜)下，历史样本越多，基因移动越大（更信任经验），样本少则保守。"""
    move_1 = _conf_move_with_n_samples(tmp_path, 1)
    move_10 = _conf_move_with_n_samples(tmp_path, 10)
    # 1 样本：step≈0.158 → 移动≈0.063；10 样本：step=0.5 → 移动≈0.200。
    assert move_1 > 0.0, "即便 1 样本也应发生（保守）移动"
    assert move_10 > move_1, (
        f"样本多应更信任经验、移动更大：n=1→{move_1:.4f}, n=10→{move_10:.4f}")
    # 单样本移动幅度不应过大（避免单次偶然误导基因）。
    assert move_1 < 0.10, f"1 样本移动过大（易被偶然误导）：{move_1:.4f}"

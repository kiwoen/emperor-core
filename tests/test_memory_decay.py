"""Phase 12 续⁵：记忆衰减 / 留存窗口（边界化「只增不减 + 陈旧样本主导」）。

自进化闭环已能记录经验并驱动派发/暖启动，但原聚合是**简单等权计数**——
陈旧样本与新鲜样本权重相同，且记忆只增不减。本 refinement 加两道可关闭（默认关→零回归）
的边界：

  A. 每 (大臣,领域) 留存上限 `max_per_group`：超限丢弃最旧样本，直接边界化「只增不减」。
  B. 时间衰减 `recency_decay` (<1.0)：路由/暖启动按插入序给新鲜样本更高权重，
     陈旧经验逐步失权，不再永久主导「谁执行 / 基因朝哪校准」。
     （与既有的 wall-clock `apply_decay` 解耦——后者短时运行几乎不触发，对自进化循环不实用。）

覆盖：
  A. 留存窗口：每组只保留最新 N 条，序号靠后的（更旧）被丢弃。
  B. 时间衰减：最新样本全胜 + 最旧样本全败 → 加权成功率显著高于等权成功率。
  C. 默认零回归：不封顶且等权时，行为与原等权聚合完全一致。
  D. 跨重启：save/load 保留 max_per_group 配置。
  E. 引擎接线：SelfEvolutionEngine(memory_max_per_group=K) 内部记忆的 cap 生效。
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
    RecordingWriteChannel, SelfEvolutionEngine, real_default_tasks,
)
from jarvis.self_evolve_config import SelfEvolveConfig  # noqa: E402
from jarvis.vcs.writeback_gate import WritebackGate  # noqa: E402


def test_max_per_group_retention_caps_per_group():
    """每组只保留最新 N 条，最旧的被丢弃（边界化只增不减）。"""
    mem = CourtMemory(max_per_group=3)
    # 同一 (大臣,领域) 记录 6 条；时间戳递增 → 只留最新 3 条。
    for i in range(6):
        mem.record(memory_from_memorial(
            "math_alpha", f"t{i}", "math", f"math {i}",
            success=(i % 2 == 0), confidence=0.9, execution_time_ms=1.0))
    assert mem.entry_count == 3, mem.entry_count
    # 留下的应是时间戳最大的 3 条（t3, t4, t5），即最新样本。
    kept = sorted(mem.get_entries(), key=lambda e: e.timestamp)
    assert kept[0].intent.endswith("3")
    assert kept[-1].intent.endswith("5")


def test_recency_weighted_quality_dominates_recent():
    """最新样本全胜 + 最旧样本全败 → 加权成功率 > 等权成功率。"""
    mem = CourtMemory()
    # 最旧 4 条全败，最新 2 条全胜（模拟「能力随经验提升」）。
    for i in range(4):
        mem.record(memory_from_memorial(
            "math_alpha", f"old{i}", "math", f"old {i}", False, 0.5, 1.0))
    for i in range(2):
        mem.record(memory_from_memorial(
            "math_alpha", f"new{i}", "math", f"new {i}", True, 0.9, 1.0))

    k = ("math_alpha", "math")
    equal = mem.per_minister_domain_quality(1.0)[k]   # (2, 6) → 0.333
    weighted = mem.per_minister_domain_quality(0.3)[k]  # 新鲜全胜权重更高
    eq_rate = equal[0] / equal[1]
    w_rate = weighted[0] / weighted[1]
    assert w_rate > eq_rate, f"加权应更信任近期成功：{w_rate} vs 等权 {eq_rate}"
    # 等权时仍与朴素计数一致（零回归）。
    assert abs(eq_rate - (2 / 6)) < 1e-9


def test_default_is_zero_regression():
    """默认不封顶 + 等权：行为与原等权聚合完全一致（无回归）。"""
    mem = CourtMemory()  # max_per_group=None（默认）
    for i in range(5):
        mem.record(memory_from_memorial(
            "code_beta", f"c{i}", "code", f"code {i}",
            success=(i < 3), confidence=0.8, execution_time_ms=1.0))
    assert mem.entry_count == 5  # 不封顶
    k = ("code_beta", "code")
    q = mem.per_minister_domain_quality(1.0)[k]
    assert q == (3.0, 5.0)  # 朴素计数，与原逻辑一致


def test_save_load_preserves_max_per_group(tmp_path):
    """save/load 保留 max_per_group，使 --resume 复用同一留存窗口。"""
    path = str(tmp_path / "memory.json")
    mem = CourtMemory(max_per_group=4)
    mem.record(memory_from_memorial(
        "math_alpha", "x", "math", "x", True, 0.9, 1.0))
    mem.save(path)
    reloaded = CourtMemory.load(path)
    assert reloaded.max_per_group == 4, reloaded.max_per_group


def test_engine_applies_max_per_group_plumbing():
    """SelfEvolutionEngine(memory_max_per_group=K) 内部记忆的 cap 生效。"""
    court = Court(CourtConfig(circuit_breaker=CircuitBreaker(CircuitConfig())))
    court.register_many([
        {"name": "math_alpha", "domain": "math", "temperature": 0.9, "confidence_baseline": 0.6},
    ])
    engine = SelfEvolutionEngine(
        court=court,
        executor=RealTaskExecutor(seed=7),
        tasks=real_default_tasks(),
        write_channel=RecordingWriteChannel(),
        write_gate=WritebackGate(min_pass_rate=0.0, forbid_regression=False),
        genome_state_path="/tmp/__noop_genome_state.json",
        use_safety_gate=False,
        enable_snapshots=False,
        self_learn=False,
        use_memory=True,
        memory_max_per_group=3,
    )
    assert engine._memory is not None
    assert engine._memory.max_per_group == 3
    # 且 record 真的会裁剪：灌入 6 条同组 → 只留 3 条。
    for i in range(6):
        engine._memory.record(memory_from_memorial(
            "math_alpha", f"t{i}", "math", f"m {i}", True, 0.9, 1.0))
    assert engine._memory.entry_count == 3


def test_config_defaults_zero_regression():
    """配置默认 recency_decay=1.0 / max_per_group=None（零回归）。"""
    cfg = SelfEvolveConfig()
    assert cfg.memory_recency_decay == 1.0
    assert cfg.memory_max_per_group is None

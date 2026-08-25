"""自进化编排器端到端（离线）+ Court 落地回归测试。

覆盖两类东西：
1. Court 在「真实滑动功勋 + 真实 run_cycle」下的落地回归——
   此前单测用 mock 掩盖了三个会在真实运行中才暴露的 bug：
   (a) `_evolve_with_breaker` 对 EvolutionReport dataclass 做 item 赋值 →
       熔断一触发就 TypeError；
   (b) `avg_merit` 读不存在的 `.merit`（SlidingMeritReport 用 windowed_merit）；
   (c) `success_rate` 调用了 SlidingMeritBoard 未委托的 `.success_rate()`。
2. SelfEvolutionEngine 完整闭环：跑通、确定性、评测闸拦/放写回。
"""

from __future__ import annotations

import os
import sys

from huanxin.court.circuit_breaker import (
    CircuitBreaker, CircuitConfig, CircuitDecision, CircuitState,
)
from huanxin.court.court import Court, CourtConfig
from huanxin.self_evolve import (
    GenomeDrivenExecutor, RecordingWriteChannel, SelfEvolutionEngine,
    default_ministers,
)
from huanxin.vcs.writeback_gate import WritebackGate

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import run_self_evolve  # noqa: E402


def _court(with_breaker=True) -> Court:
    cfg = CourtConfig(circuit_breaker=CircuitBreaker() if with_breaker else None)
    return Court(cfg)


def _feed(court: Court, minister: str, n: int = 3, score: float = 80.0):
    for i in range(n):
        court.record_dispatch(minister, f"t{i}", "intent", True, score / 100.0)
        court.record_feedback(minister, f"t{i}", score)


# ── Court 落地回归 ──────────────────────────────────────────

def test_avg_merit_and_success_rate_with_sliding_merit():
    """滑动功勋（默认开启）下 avg_merit / success_rate 不应再 AttributeError。"""
    court = Court()  # enable_sliding_merit 默认 True
    court.register_many(default_ministers())
    _feed(court, "math_alpha")
    assert court.avg_merit >= 0.0           # 修复前：AttributeError: .merit
    assert 0.0 <= court.success_rate <= 1.0  # 修复前：AttributeError: .success_rate


def test_court_evolve_real_breaker_trip_returns_dict():
    """真实 run_cycle（返回 EvolutionReport）+ 熔断触发 → evolve 必须返回 dict。

    修复前：`last_result["halted"] = True` 直接打在 EvolutionReport dataclass 上，
    熔断一触发就 TypeError——安全闸会在最不该崩的时刻崩掉整个进化循环。
    """
    court = _court()
    court.register_many(default_ministers())
    for m in court.active_ministers:
        _feed(court, m)

    # 让 run_cycle 保持真实，但强制首轮 record 即熔断
    def trip_record(cycle, merit, cost=0.0):
        return CircuitDecision(state=CircuitState.OPEN, open=True, reason="forced-trip")
    court._circuit_breaker.record = trip_record  # type: ignore[assignment]

    result = court.evolve(5)
    assert isinstance(result, dict)
    assert result["halted"] is True
    assert "forced-trip" in result["trip_reason"]


def test_court_evolve_no_trip_returns_dict_summary():
    """无熔断时 evolve 也应返回统一的 dict（含 cycle/active_count 等）。"""
    court = _court()
    court.register_many(default_ministers())
    for m in court.active_ministers:
        _feed(court, m, score=85.0)
    result = court.evolve(2)
    assert isinstance(result, dict)
    assert "halted" not in result or result.get("halted") is not True


# ── 编排引擎闭环 ────────────────────────────────────────────

def _engine(seed=0, gate=None, channel=None):
    court = _court()
    court.register_many(default_ministers())
    return SelfEvolutionEngine(
        court=court,
        executor=GenomeDrivenExecutor(seed=seed),
        write_channel=channel,
        write_gate=gate,
    )


def test_engine_runs_offline_and_completes():
    eng = _engine(seed=1)
    report = eng.run(n_cycles=4, tasks_per_minister=2)
    # 引擎应确实跑起来并产出周期记录（可能因安全熔断提前中止，属合法安全行为）。
    assert report.finished_at
    assert 1 <= len(report.cycles) <= 4
    for c in report.cycles:
        assert c.avg_merit >= 0.0
        assert 0.0 <= c.success_rate <= 1.0
        assert c.eval_pass_rate is not None


def test_engine_deterministic_same_seed():
    r1 = _engine(seed=42).run(n_cycles=3)
    r2 = _engine(seed=42).run(n_cycles=3)
    assert [c.avg_merit for c in r1.cycles] == [c.avg_merit for c in r2.cycles]


def test_writeback_blocked_under_strict_gate():
    """默认严格闸（min_pass_rate=1.0）：质量不达标 → 写回被拦，且不产生 PR。"""
    channel = RecordingWriteChannel()
    eng = _engine(seed=1, channel=channel)  # 默认 WritebackGate(min_pass_rate=1.0)
    report = eng.run(n_cycles=3)
    # 基因离最优区有距离，基准难过 100% → 未熔断的周期应全部 blocked。
    # （熔断导致的 halted 周期其写回为 'disabled'，属合法安全行为，不计入闸判定。）
    for c in report.cycles:
        if not c.halted:
            assert c.writeback in ("blocked", "skipped-no-eval")
    assert channel.proposals == []          # 闸拦下 → 一个 PR 都不该有


def test_writeback_proposed_under_lenient_gate():
    """放宽闸（min_pass_rate=0.5）：质量达标 → 写回放行，记录 PR 意图。"""
    channel = RecordingWriteChannel()
    gate = WritebackGate(min_pass_rate=0.5, forbid_regression=False)
    eng = _engine(seed=1, gate=gate, channel=channel)
    report = eng.run(n_cycles=2)
    assert any(c.writeback.startswith("proposed:") for c in report.cycles)
    assert len(channel.proposals) >= 1
    # 离线通道绝不直推受保护分支（只记录 absorb-* 分支）
    for p in channel.proposals:
        assert p.branch.startswith("absorb-")


# ── CLI 端到端 ──────────────────────────────────────────────

def test_cli_main_produces_artifacts(tmp_path):
    out = str(tmp_path / "out")
    rc = run_self_evolve.main(["--cycles", "3", "--seed", "3",
                               "--out", out, "--no-writeback"])
    assert rc == 0
    assert os.path.exists(os.path.join(out, "run_report.json"))
    assert os.path.exists(os.path.join(out, "telemetry.json"))
    assert os.path.exists(os.path.join(out, "telemetry.js"))
    assert os.path.exists(os.path.join(out, "dashboard.html"))

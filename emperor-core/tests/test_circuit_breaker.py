"""P1.4 CircuitBreaker + PromotionGate 单元测试。

覆盖：
- CircuitBreaker 三种熔断触发（跌幅 / 连续负向 / 成本超支）
- min_cycles 冷启动保护
- HALF_OPEN 冷却恢复
- PromotionGate 连续正增长门槛
- Court.evolve 在熔断后真正停止（集成）
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from huanxin.court.circuit_breaker import (
    CircuitBreaker,
    CircuitConfig,
    PromotionGate,
    PromotionGateConfig,
)


# ── CircuitBreaker ──────────────────────────────────────────────

def test_breaker_trips_on_consecutive_negative():
    cb = CircuitBreaker(CircuitConfig(
        drop_fraction=0.0,            # 关闭跌幅触发，单独测连续负向
        consecutive_negative=2,
        min_cycles_before_trip=0,
    ))
    # 90 -> 80(1) -> 70(2 consecutive) => trip
    assert not cb.record(1, 90).open
    assert not cb.record(2, 80).open      # 1 轮负向
    dec = cb.record(3, 70)
    assert dec.open
    assert "连续" in dec.reason


def test_breaker_trips_on_drop_fraction():
    cb = CircuitBreaker(CircuitConfig(
        drop_fraction=0.20,
        consecutive_negative=999,         # 关闭连续触发
        min_cycles_before_trip=0,
    ))
    cb.record(1, 100.0)                    # peak = 100
    dec = cb.record(2, 75.0)              # 跌 25% >= 20%
    assert dec.open
    assert "跌幅" in dec.reason


def test_breaker_trips_on_cost_budget():
    cb = CircuitBreaker(CircuitConfig(
        drop_fraction=0.0,
        consecutive_negative=999,
        cost_budget=10.0,
        min_cycles_before_trip=0,
    ))
    assert not cb.record(1, 100.0, cost=6.0).open
    dec = cb.record(2, 100.0, cost=5.0)   # 累计 11 >= 10
    assert dec.open
    assert "成本" in dec.reason


def test_breaker_respects_min_cycles():
    cb = CircuitBreaker(CircuitConfig(
        drop_fraction=0.0,
        consecutive_negative=1,
        min_cycles_before_trip=2,
    ))
    # 第 1、2 轮不触发（冷启动保护），第 3 轮才可能因连续负向触发
    assert not cb.record(1, 90).open
    assert not cb.record(2, 80).open
    assert cb.record(3, 70).open


def test_breaker_half_open_recovery():
    cb = CircuitBreaker(CircuitConfig(
        drop_fraction=0.0,
        consecutive_negative=1,
        min_cycles_before_trip=0,
        cooldown_cycles=2,
    ))
    cb.record(1, 100)
    assert cb.record(2, 90).open               # 熔断
    # 进入冷却：仍 open，但计数
    assert cb.record(3, 90).open
    # cooldown 到了 -> HALF_OPEN，不再 open
    dec = cb.record(4, 90)
    assert dec.state.value == "half_open"
    assert not dec.open


# ── PromotionGate ─────────────────────────────────────────────

def test_promotion_gate_requires_consecutive_gains():
    gate = PromotionGate(PromotionGateConfig(
        required_consecutive_gains=3, min_merit=50.0,
    ))
    # 基线(60)不算增长；需连续 3 次「高于上一轮」才晋升
    assert not gate.record("alpha", 60.0)       # 基线
    assert not gate.record("alpha", 65.0)       # 第 1 次增长
    assert not gate.record("alpha", 70.0)       # 第 2 次增长
    assert gate.record("alpha", 75.0)           # 第 3 次增长 → 放行
    # 晋升后计数器清零：需重新累计 3 次增长
    assert not gate.record("alpha", 80.0)
    assert not gate.record("alpha", 85.0)
    assert gate.record("alpha", 90.0)


def test_promotion_gate_blocks_below_min_merit():
    gate = PromotionGate(PromotionGateConfig(
        required_consecutive_gains=2, min_merit=50.0,
    ))
    assert not gate.record("alpha", 30.0)       # 低于门槛，不计增长
    assert not gate.record("alpha", 35.0)       # 仍低于门槛，永不晋升


def test_promotion_gate_resets_on_streak_break():
    gate = PromotionGate(PromotionGateConfig(
        required_consecutive_gains=3, min_merit=50.0,
    ))
    gate.record("alpha", 60.0)                  # 基线
    gate.record("alpha", 65.0)                  # +1
    gate.record("alpha", 70.0)                  # +2
    gate.record("alpha", 60.0)                  # 下跌，连续中断，清零
    assert not gate.record("alpha", 65.0)       # 重新 +1
    assert not gate.record("alpha", 70.0)       # 重新 +2
    assert gate.record("alpha", 75.0)           # 重新 +3 → 放行


# ── Court 集成：熔断后真正停止进化 ────────────────────────────

class _StubBoard:
    """极简 MeritBoard 替身：avg_merit 随 merit 变化。"""

    def __init__(self, merit: float) -> None:
        self.merit = merit

    def get_ranking(self):
        return [SimpleNamespace(merit=self.merit)]

    def success_rate(self):
        return 0.0


def _make_court_with_breaker(cb):
    from huanxin.court.court import Court, CourtConfig
    court = Court(CourtConfig(enable_auto_elimination=False))
    court._circuit_breaker = cb
    # 让 avg_merit 读取可控的 stub
    court._merit_board = _StubBoard(100.0)
    court._sm._merit_board = court._merit_board
    return court


def test_court_evolve_halts_when_breaker_opens(monkeypatch):
    cb = CircuitBreaker(CircuitConfig(
        drop_fraction=0.0, consecutive_negative=2, min_cycles_before_trip=1,
    ))
    court = _make_court_with_breaker(cb)
    cycles = {"n": 0}

    def fake_run_cycle():
        cycles["n"] += 1
        # 模拟功勋持续下滑：100 -> 90 -> 80
        court._merit_board.merit = 100 - cycles["n"] * 10
        return {}

    monkeypatch.setattr(court, "run_cycle", fake_run_cycle)

    result = court.evolve(10)                 # 请求 10 轮，但应被熔断截断
    assert result.get("halted") is True
    assert cycles["n"] <= 4                   # 不会跑满 10 轮
    assert cb.is_open


def test_court_evolve_runs_full_when_no_trip(monkeypatch):
    cb = CircuitBreaker(CircuitConfig(
        drop_fraction=0.0, consecutive_negative=999, min_cycles_before_trip=0,
    ))
    court = _make_court_with_breaker(cb)
    cycles = {"n": 0}

    def fake_run_cycle():
        cycles["n"] += 1
        court._merit_board.merit = 100.0      # 平稳，不触发
        return {}

    monkeypatch.setattr(court, "run_cycle", fake_run_cycle)

    result = court.evolve(5)
    assert result.get("halted") is not True
    assert cycles["n"] == 5

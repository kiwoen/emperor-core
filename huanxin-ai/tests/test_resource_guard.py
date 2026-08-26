"""Phase 9 资源预算护栏测试。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from huanxin.court.resource_guard import (
    ResourceBudget,
    ResourceBudgetExceeded,
    resource_budget,
)


def test_completes_within_budget():
    with resource_budget(seconds=10.0, max_operations=10) as b:
        b.tick(5)
    assert b.used_seconds() >= 0.0


def test_max_operations_triggers():
    with pytest.raises(ResourceBudgetExceeded):
        with ResourceBudget(max_operations=2) as b:
            b.tick(2)  # 2 未超
            b.tick(1)  # 3 > 2 → 触发


def test_timeout_triggers(monotonic_seq=(0.0, 1.0, 2.0)):
    with patch("huanxin.court.resource_guard.time.monotonic",
               side_effect=list(monotonic_seq)):
        with pytest.raises(ResourceBudgetExceeded):
            with ResourceBudget(seconds=1.0) as b:
                b.tick()  # elapsed 1.0，恰好不超
                b.tick()  # elapsed 2.0 > 1.0 → 触发


def test_unlimited_budget_never_raises():
    with ResourceBudget(seconds=None, max_operations=None) as b:
        for _ in range(1000):
            b.tick(1)


def test_remaining_seconds_and_fraction():
    with patch("huanxin.court.resource_guard.time.monotonic",
               side_effect=[0.0, 0.5, 0.5, 0.5]):
        b = ResourceBudget(seconds=10.0)
        b.__enter__()
        assert b.remaining_seconds() is not None
        assert 0.0 <= b.used_fraction() <= 1.0

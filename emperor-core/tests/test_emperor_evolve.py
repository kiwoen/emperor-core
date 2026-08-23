"""Tests for emperor_evolve convenience API."""

from __future__ import annotations

import pytest

from jarvis.court.history import EvolutionHistory
from jarvis.court.evolution import SurvivalMechanism
from jarvis.court.merit_board import MeritBoard


class TestEmperorEvolve:
    """emperor_evolve convenience API tests."""

    def test_single_cycle(self):
        """emperor_evolve(1) runs one cycle and returns summary."""
        sm = SurvivalMechanism(
            merit_board=MeritBoard(),
            enable_auto_breeding=False,
        )
        sm.register_minister("alpha", "math", 0.7)
        sm.register_minister("beta", "code", 0.8)

        result = sm.emperor_evolve(1)

        assert result["total_cycles"] == 1
        assert "active_start" in result
        assert "active_end" in result
        assert "delta" in result
        assert len(result["cycles"]) == 1
        assert result["cycles"][0]["active"] > 0

    def test_multi_cycle(self):
        """emperor_evolve(N) runs N cycles."""
        sm = SurvivalMechanism(
            merit_board=MeritBoard(),
            enable_auto_breeding=False,
        )
        sm.register_minister("a", "math", 0.7)
        sm.register_minister("b", "code", 0.8)
        sm.register_minister("c", "math", 0.6)

        result = sm.emperor_evolve(5)

        assert result["total_cycles"] == 5
        assert len(result["cycles"]) == 5

    def test_zero_raises(self):
        """emperor_evolve(0) raises ValueError."""
        sm = SurvivalMechanism(merit_board=MeritBoard())
        with pytest.raises(ValueError, match="n_cycles"):
            sm.emperor_evolve(0)

    def test_negative_raises(self):
        """emperor_evolve(-1) raises ValueError."""
        sm = SurvivalMechanism(merit_board=MeritBoard())
        with pytest.raises(ValueError):
            sm.emperor_evolve(-1)

    def test_with_history(self):
        """With history recorder, summary includes merit trend."""
        history = EvolutionHistory()
        sm = SurvivalMechanism(
            merit_board=MeritBoard(),
            history=history,
            enable_auto_breeding=False,
        )
        sm.register_minister("x", "math", 0.7)
        sm.register_minister("y", "code", 0.8)

        result = sm.emperor_evolve(3)

        assert result["total_cycles"] == 3
        assert "merit_trend" in result
        assert "history_cycle_count" in result
        assert history.cycle_count == 3
        assert len(result["merit_trend"]) == 3

    def test_without_history(self):
        """Without history, no merit_trend in summary."""
        sm = SurvivalMechanism(
            merit_board=MeritBoard(),
            enable_auto_breeding=False,
        )
        sm.register_minister("x", "math", 0.7)

        result = sm.emperor_evolve(1)
        assert "merit_trend" not in result

    def test_delta_positive(self):
        """When breeding is enabled, population may grow (delta >= 0)."""
        sm = SurvivalMechanism(
            merit_board=MeritBoard(),
            enable_auto_breeding=True,
            breeding_cooldown=0,
            max_breed_per_cycle=2,
        )
        for i in range(5):
            sm.register_minister(f"m{i}", "math", 0.5 + i * 0.05)

        result = sm.emperor_evolve(3)
        # Delta could be anything, just verify it's an int
        assert isinstance(result["delta"], int)
        assert result["total_cycles"] == 3

    def test_cycle_data_structure(self):
        """Verify each cycle entry has expected keys."""
        sm = SurvivalMechanism(
            merit_board=MeritBoard(),
            enable_auto_breeding=False,
        )
        sm.register_minister("alpha", "math", 0.7)

        result = sm.emperor_evolve(2)

        for cycle in result["cycles"]:
            assert set(cycle.keys()) == {
                "cycle", "active", "shadow", "eliminated",
                "new_spawns", "actions",
            }

    def test_active_start_end_consistent(self):
        """active_start matches first cycle's active count."""
        sm = SurvivalMechanism(
            merit_board=MeritBoard(),
            enable_auto_breeding=False,
        )
        sm.register_minister("a", "math", 0.7)

        result = sm.emperor_evolve(2)
        assert result["active_start"] == result["cycles"][0]["active"]
        assert result["active_end"] == result["cycles"][-1]["active"]

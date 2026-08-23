"""Tests for EvolutionHistory — structured evolution data recording."""

from __future__ import annotations

import json
import os

from jarvis.court.history import CycleRecord, EvolutionHistory
from jarvis.court.evolution import EvolutionReport


def _make_report(cycle=1, active=3, shadow=1, eliminated=0, new=0):
    return EvolutionReport(
        cycle=cycle,
        actions_taken=[],
        active_count=active,
        shadow_count=shadow,
        eliminated_count=eliminated,
        new_spawns=new,
        systemic_issues=[],
        recommendations=[],
    )


def _make_snapshot(
    cycle=1,
    ministers=None,
    active=3,
    shadow=1,
    probation=2,
    eliminated=1,
):
    from jarvis.court.inspector import CourtSnapshot, MinisterSnapshot

    if ministers is None:
        ministers = [
            MinisterSnapshot(
                name="alpha",
                domain="math",
                status="active",
                merit=0.92,
                generation=1,
                temperature=0.7,
                confidence_baseline=0.8,
                exploration_rate=0.3,
                conservatism=0.4,
                specialization_weight=0.5,
            ),
            MinisterSnapshot(
                name="beta",
                domain="code",
                status="active",
                merit=0.65,
                generation=1,
                temperature=0.9,
                confidence_baseline=0.7,
                exploration_rate=0.5,
                conservatism=0.3,
                specialization_weight=0.6,
            ),
            MinisterSnapshot(
                name="gamma",
                domain="math",
                status="active",
                merit=0.80,
                generation=1,
                temperature=0.75,
                confidence_baseline=0.75,
                exploration_rate=0.35,
                conservatism=0.35,
                specialization_weight=0.5,
            ),
            MinisterSnapshot(
                name="delta",
                domain="code",
                status="shadow",
                merit=0.30,
                generation=1,
                temperature=0.85,
                confidence_baseline=0.6,
                exploration_rate=0.6,
                conservatism=0.2,
                specialization_weight=0.5,
            ),
        ]

    return CourtSnapshot(
        cycle=cycle,
        total_ministers=active + shadow + probation + eliminated,
        active_count=active,
        shadow_count=shadow,
        probation_count=probation,
        eliminated_count=eliminated,
        ministers=ministers,
    )


# ══════════════════════════════════════════════════════════════════════
# Unit tests
# ══════════════════════════════════════════════════════════════════════


class TestEvolutionHistory:
    """Core unit tests for EvolutionHistory."""

    def test_empty_history(self):
        """Empty history returns reasonable defaults."""
        h = EvolutionHistory()
        assert len(h) == 0
        assert h.cycle_count == 0
        assert h.last() is None
        assert h.get_cycle(1) is None
        assert h.trend("merit_mean") == []
        assert h.to_json() == "[]"
        assert h.to_csv().startswith("cycle,")

    def test_record_single_cycle(self):
        """Record one cycle and verify fields."""
        h = EvolutionHistory()
        report = _make_report(cycle=1, active=3, shadow=1)
        snapshot = _make_snapshot(cycle=1, active=3, shadow=1)

        record = h.record(report, snapshot)

        assert record.cycle == 1
        assert record.active_count == 3
        assert record.shadow_count == 1
        assert record.total_ministers == 7  # 3 active + 1 shadow + 2 probation + 1 eliminated
        assert record.merit_mean == round((0.92 + 0.65 + 0.80 + 0.30) / 4, 2)
        assert record.domain_count == 2
        assert record.temperature_variance > 0

    def test_get_cycle(self):
        """Retrieve a specific cycle."""
        h = EvolutionHistory()
        h.record(_make_report(1), _make_snapshot(cycle=1))
        h.record(_make_report(2, active=4), _make_snapshot(cycle=2, active=4))

        assert h.get_cycle(1).cycle == 1
        assert h.get_cycle(2).cycle == 2
        assert h.get_cycle(3) is None

    def test_last(self):
        """Last returns most recent record."""
        h = EvolutionHistory()
        h.record(_make_report(1), _make_snapshot(cycle=1))
        h.record(_make_report(2), _make_snapshot(cycle=2))

        assert h.last().cycle == 2

    def test_trend(self):
        """Trend extracts time series for a numeric field."""
        h = EvolutionHistory()
        for i in range(1, 5):
            h.record(_make_report(i, active=2 + i), _make_snapshot(cycle=i, active=2 + i))

        trend = h.trend("active_count")
        assert trend == [3, 4, 5, 6]

    def test_trend_invalid_field(self):
        """Trend raises on unknown field."""
        h = EvolutionHistory()
        h.record(_make_report(1), _make_snapshot())
        try:
            h.trend("nonexistent_field")
            assert False, "Should have raised"
        except ValueError:
            pass

    def test_compare_cycles(self):
        """Compare two cycles side-by-side."""
        h = EvolutionHistory()
        h.record(_make_report(1, active=2), _make_snapshot(cycle=1, active=2))
        h.record(_make_report(5, active=6), _make_snapshot(cycle=5, active=6))

        cmp = h.compare_cycles(1, 5)
        assert cmp["cycle_a"] == 1
        assert cmp["cycle_b"] == 5
        assert "2 → 6" in cmp["active_count"]

    def test_compare_missing_cycle(self):
        """Compare returns error on missing cycle."""
        h = EvolutionHistory()
        h.record(_make_report(1), _make_snapshot())
        cmp = h.compare_cycles(1, 99)
        assert "error" in cmp

    def test_cycle_count(self):
        """cycle_count tracks number of records."""
        h = EvolutionHistory()
        for i in range(3):
            h.record(_make_report(i + 1), _make_snapshot(cycle=i + 1))
        assert h.cycle_count == 3

    def test_minister_independence(self):
        """Record doesn't mutate input snapshot."""
        h = EvolutionHistory()
        original_minister_count = 4
        snapshot = _make_snapshot()
        h.record(_make_report(), snapshot)
        assert len(snapshot.ministers) == original_minister_count


# ══════════════════════════════════════════════════════════════════════
# Export tests
# ══════════════════════════════════════════════════════════════════════


class TestEvolutionHistoryExport:
    """Export format tests."""

    def test_to_json(self):
        """JSON export produces valid, parseable JSON."""
        h = EvolutionHistory()
        h.record(_make_report(1), _make_snapshot(cycle=1))
        h.record(_make_report(2), _make_snapshot(cycle=2))

        result = h.to_json()
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert len(parsed) == 2
        assert parsed[0]["cycle"] == 1
        assert parsed[1]["cycle"] == 2
        assert "actions_taken" in parsed[0]

    def test_to_csv(self):
        """CSV export produces valid CSV with header."""
        h = EvolutionHistory()
        h.record(_make_report(1), _make_snapshot(cycle=1))

        result = h.to_csv()
        lines = result.strip().split("\r\n")
        assert lines[0].startswith("cycle,active_count")
        assert lines[1].startswith("1,")

    def test_to_json_file(self, tmp_path):
        """JSON export to file writes to disk."""
        h = EvolutionHistory()
        h.record(_make_report(1), _make_snapshot(cycle=1))

        filepath = tmp_path / "history.json"
        h.to_json(str(filepath))

        assert os.path.exists(filepath)
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)
        assert len(data) == 1

    def test_to_csv_file(self, tmp_path):
        """CSV export to file writes to disk."""
        h = EvolutionHistory()
        h.record(_make_report(1), _make_snapshot(cycle=1))

        filepath = tmp_path / "history.csv"
        h.to_csv(str(filepath))

        assert os.path.exists(filepath)

    def test_export_empty_history(self):
        """Exporting empty history gives empty results."""
        h = EvolutionHistory()
        assert h.to_json() == "[]"
        csv_result = h.to_csv()
        # CSV of empty history has header only
        assert csv_result.startswith("cycle,")


# ══════════════════════════════════════════════════════════════════════
# Integration: SurvivalMechanism + EvolutionHistory
# ══════════════════════════════════════════════════════════════════════


class TestHistoryIntegration:
    """Verify EvolutionHistory integrates with SurvivalMechanism."""

    def test_mechanism_records_when_history_provided(self):
        """SurvivalMechanism auto-records when history is passed."""
        from jarvis.court.evolution import SurvivalMechanism
        from jarvis.court.merit_board import MeritBoard

        board = MeritBoard()
        history = EvolutionHistory()
        sm = SurvivalMechanism(
            merit_board=board,
            history=history,
            enable_auto_breeding=False,
        )

        # Register some ministers
        sm.register_minister("alpha", "math", 0.7)
        sm.register_minister("beta", "code", 0.8)
        sm.register_minister("gamma", "math", 0.6)

        # Give them some merit
        board.record_dispatch("alpha", "e1", "math", True, 0.9)
        board.record_dispatch("beta", "e2", "code", False, 0.5)
        board.record_dispatch("gamma", "e3", "math", True, 0.7)

        assert history.cycle_count == 0

        sm.run_evolution_cycle()

        assert history.cycle_count == 1
        record = history.last()
        assert record.cycle == 1
        assert record.active_count >= 1
        assert record.total_ministers >= 3

    def test_mechanism_no_history_no_record(self):
        """Without history, SurvivalMechanism does not crash."""
        from jarvis.court.evolution import SurvivalMechanism
        from jarvis.court.merit_board import MeritBoard

        sm = SurvivalMechanism(
            merit_board=MeritBoard(),
            enable_auto_breeding=False,
        )
        sm.register_minister("x", "math", 0.7)
        sm.register_minister("y", "code", 0.8)
        sm.run_evolution_cycle()
        # No crash → pass

    def test_multiple_cycles_accumulate(self):
        """Multiple cycles accumulate in history."""
        from jarvis.court.evolution import SurvivalMechanism
        from jarvis.court.merit_board import MeritBoard

        history = EvolutionHistory()
        sm = SurvivalMechanism(
            merit_board=MeritBoard(),
            history=history,
            enable_auto_breeding=False,
        )

        sm.register_minister("alpha", "math", 0.7)
        sm.register_minister("beta", "code", 0.8)

        for i in range(5):
            sm.run_evolution_cycle()

        assert history.cycle_count == 5
        trend = history.trend("active_count")
        assert len(trend) == 5

    def test_history_trend_reflects_evolution(self):
        """Trend data is self-consistent."""
        from jarvis.court.evolution import SurvivalMechanism
        from jarvis.court.merit_board import MeritBoard

        history = EvolutionHistory()
        sm = SurvivalMechanism(
            merit_board=MeritBoard(),
            history=history,
            enable_auto_breeding=True,
            breeding_cooldown=0,
            max_breed_per_cycle=2,
        )

        sm.register_minister("alpha", "math", 0.7)
        sm.register_minister("beta", "math", 0.8)
        sm.register_minister("gamma", "code", 0.6)
        sm.register_minister("delta", "code", 0.5)
        sm.register_minister("epsilon", "math", 0.4)

        for i in range(3):
            sm.run_evolution_cycle()

        assert history.cycle_count == 3
        # All cycles should have at least some ministers
        for record in history:
            assert record.total_ministers > 0

"""Tests for StabilityTracker — court merit stability over cycles."""

from jarvis.court.evolution import StabilityTracker


class TestStabilityTracker:
    """Unit tests for the stability scoring engine."""

    def test_empty_returns_neutral(self):
        """No cycles recorded → neutral 0.5."""
        tracker = StabilityTracker()
        assert tracker.get_stability_score() == 0.5

    def test_one_cycle_returns_neutral(self):
        """Single cycle → not enough data, return neutral."""
        tracker = StabilityTracker()
        tracker.record_cycle(50.0)
        assert tracker.get_stability_score() == 0.5

    def test_two_cycles_returns_neutral(self):
        """Two cycles → still not enough data."""
        tracker = StabilityTracker()
        tracker.record_cycle(50.0)
        tracker.record_cycle(55.0)
        assert tracker.get_stability_score() == 0.5

    def test_consistent_merit_yields_high_stability(self):
        """Low variance → high stability score (close to 1)."""
        tracker = StabilityTracker()
        for _ in range(5):
            tracker.record_cycle(50.0)
        score = tracker.get_stability_score()
        assert score > 0.9, f"Expected >0.9, got {score}"

    def test_chaotic_merit_yields_low_stability(self):
        """High variance → low stability score."""
        tracker = StabilityTracker()
        for v in [10, 90, 20, 80, 50]:
            tracker.record_cycle(v)
        score = tracker.get_stability_score()
        assert score < 0.6, f"Expected <0.6, got {score}"

    def test_stability_improves_as_merit_converges(self):
        """Stability should rise as merit variance decreases."""
        tracker = StabilityTracker()
        # Early cycles: chaotic
        for v in [20, 80, 30, 70]:
            tracker.record_cycle(v)
        chaotic_score = tracker.get_stability_score()
        # Later cycles: converging
        for v in [55, 52, 53, 51, 50, 51]:
            tracker.record_cycle(v)
        stable_score = tracker.get_stability_score()
        assert stable_score > chaotic_score, (
            f"stable={stable_score} should exceed chaotic={chaotic_score}"
        )

    def test_window_size_limit(self):
        """Window is capped at WINDOW_SIZE, old data dropped."""
        tracker = StabilityTracker()
        # Fill with initial consistent data
        for _ in range(5):
            tracker.record_cycle(50.0)
        assert tracker.get_stability_score() > 0.9

        # Push chaotic data to replace window
        for v in [5, 95, 15, 85, 25, 75, 35, 65, 45, 55]:
            tracker.record_cycle(v)
        # After 10 chaotic cycles, stability should drop
        assert tracker.get_stability_score() < 0.6

    def test_reset_clears_all(self):
        """reset() should clear the window back to empty."""
        tracker = StabilityTracker()
        for _ in range(5):
            tracker.record_cycle(50.0)
        assert tracker.get_stability_score() > 0.9
        tracker.reset()
        assert tracker.get_stability_score() == 0.5

    def test_zero_mean_merit(self):
        """All-zero merits → stability 0 (mean=0, edge case)."""
        tracker = StabilityTracker()
        for _ in range(5):
            tracker.record_cycle(0.0)
        score = tracker.get_stability_score()
        assert score == 0.0, f"Expected 0.0, got {score}"

    def test_negative_merits_handled(self):
        """Negative merits → CV computed on abs(mean)."""
        tracker = StabilityTracker()
        for v in [-10, -20, -15, -18, -12]:
            tracker.record_cycle(v)
        score = tracker.get_stability_score()
        # Low variance within negative range → should be reasonably stable
        assert 0.4 < score < 1.0, f"Expected moderate stability, got {score}"


class TestStabilityAdaptiveRates:
    """Integration: stability score feeds into _compute_adaptive_rates."""

    def test_stability_affects_mutation_scale(self):
        """Low stability → higher mutation_scale."""
        from jarvis.court.evolution import (
            SurvivalMechanism,
            EvolutionRateMode,
            AdaptiveRateConfig,
            TaskDifficulty,
        )

        config = AdaptiveRateConfig(
            stability_blend=0.5,
            diversity_blend=0.0,  # isolate stability effect
        )

        sm = SurvivalMechanism(
            rate_mode=EvolutionRateMode.ADAPTIVE,
            rate_config=config,
        )

        # Set fixed difficulty and diversity for controlled test
        from jarvis.court.evolution import TaskContext
        sm.set_task_context(TaskContext(
            difficulty=TaskDifficulty.MODERATE,
            intent="test",
            domain="testing",
        ))

        # Scenario 1: stable court → low mutation
        for _ in range(5):
            sm.stability.record_cycle(50.0)
        sm._compute_adaptive_rates()
        stable_mut = sm._effective_mutation_scale

        # Scenario 2: chaotic court → higher mutation
        sm.stability.reset()
        for v in [10, 90, 20, 80, 50]:
            sm.stability.record_cycle(v)
        sm._compute_adaptive_rates()
        chaotic_mut = sm._effective_mutation_scale

        assert chaotic_mut > stable_mut, (
            f"chaotic={chaotic_mut} should exceed stable={stable_mut}"
        )

    def test_stability_blend_zero_ignores_stability(self):
        """stability_blend=0 → stability has no effect on rates."""
        from jarvis.court.evolution import (
            SurvivalMechanism,
            EvolutionRateMode,
            AdaptiveRateConfig,
            TaskDifficulty,
            TaskContext,
        )

        config = AdaptiveRateConfig(
            stability_blend=0.0,
            diversity_blend=0.0,
        )

        sm = SurvivalMechanism(
            rate_mode=EvolutionRateMode.ADAPTIVE,
            rate_config=config,
        )
        sm.set_task_context(TaskContext(
            difficulty=TaskDifficulty.MODERATE,
            intent="test",
            domain="testing",
        ))

        sm.stability.record_cycle(50.0)
        sm.stability.record_cycle(50.0)
        sm.stability.record_cycle(50.0)
        sm._compute_adaptive_rates()
        mut1 = sm._effective_mutation_scale

        sm.stability.reset()
        sm.stability.record_cycle(10.0)
        sm.stability.record_cycle(90.0)
        sm.stability.record_cycle(50.0)
        sm._compute_adaptive_rates()
        mut2 = sm._effective_mutation_scale

        # With stability_blend=0, both should be identical
        assert abs(mut1 - mut2) < 0.001, (
            f"Expected equal mutation scales: {mut1} vs {mut2}"
        )

    def test_catastrophe_resets_stability(self):
        """After a catastrophe, stability resets to neutral."""
        from jarvis.court.evolution import SurvivalMechanism

        sm = SurvivalMechanism()
        for _ in range(5):
            sm.stability.record_cycle(50.0)
        assert sm.stability.get_stability_score() > 0.9

        sm.stability.reset()
        assert sm.stability.get_stability_score() == 0.5

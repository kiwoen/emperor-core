"""Tests for DiversityMonitor and catastrophe mechanism."""

import pytest
from jarvis.court.diversity import DiversityMonitor, DiversitySnapshot, CatastropheReport
from jarvis.court.evolution import MinisterGenome


class TestFeatureVector:
    """Feature vector extraction tests."""

    def test_extract_correct_fields(self):
        g = MinisterGenome(
            name="test",
            domain="code",
            temperature=0.7,
            confidence_baseline=0.85,
            exploration_rate=0.3,
            conservatism=0.5,
            prompt_mutation_rate=0.1,
            specialization_weight=1.0,
        )
        vec = DiversityMonitor._extract_feature_vector(g)
        assert vec == [0.7, 0.85, 0.3, 0.5, 0.1, 1.0]
        assert len(vec) == 6

    def test_cosine_similarity_identical(self):
        a = [0.7, 0.85, 0.3, 0.5, 0.1, 1.0]
        b = [0.7, 0.85, 0.3, 0.5, 0.1, 1.0]
        sim = DiversityMonitor._cosine_similarity(a, b)
        assert abs(sim - 1.0) < 0.001

    def test_cosine_similarity_opposite(self):
        a = [1.0, 0.0, 1.0, 0.0, 1.0, 0.0]
        b = [0.0, 1.0, 0.0, 1.0, 0.0, 1.0]
        sim = DiversityMonitor._cosine_similarity(a, b)
        assert abs(sim - 0.0) < 0.001

    def test_zero_vector(self):
        sim = DiversityMonitor._cosine_similarity([0, 0, 0], [1, 2, 3])
        assert sim == 0.0


class TestMeasureDiversity:
    """Diversity measurement tests."""

    @pytest.fixture
    def monitor(self):
        return DiversityMonitor()

    @pytest.fixture
    def diverse_genomes(self):
        """Genomes with intentionally varied parameters."""
        return {
            "alpha": MinisterGenome(
                "alpha", "writing",
                temperature=0.9, confidence_baseline=0.9,
                exploration_rate=0.7, conservatism=0.2,
                prompt_mutation_rate=0.3, specialization_weight=1.5,
            ),
            "beta": MinisterGenome(
                "beta", "code",
                temperature=0.3, confidence_baseline=0.7,
                exploration_rate=0.1, conservatism=0.8,
                prompt_mutation_rate=0.05, specialization_weight=0.8,
            ),
            "gamma": MinisterGenome(
                "gamma", "science",
                temperature=0.5, confidence_baseline=0.5,
                exploration_rate=0.5, conservatism=0.5,
                prompt_mutation_rate=0.15, specialization_weight=1.0,
            ),
        }

    @pytest.fixture
    def convergent_genomes(self):
        """Near-identical genomes — should trigger crisis."""
        return {
            "x1": MinisterGenome(
                "x1", "writing", temperature=0.7, confidence_baseline=0.85,
                exploration_rate=0.3, conservatism=0.5,
                prompt_mutation_rate=0.1, specialization_weight=1.0,
            ),
            "x2": MinisterGenome(
                "x2", "code", temperature=0.72, confidence_baseline=0.84,
                exploration_rate=0.31, conservatism=0.49,
                prompt_mutation_rate=0.11, specialization_weight=1.01,
            ),
            "x3": MinisterGenome(
                "x3", "science", temperature=0.69, confidence_baseline=0.86,
                exploration_rate=0.29, conservatism=0.51,
                prompt_mutation_rate=0.09, specialization_weight=0.99,
            ),
        }

    def test_diverse_population_gives_high_score(self, monitor, diverse_genomes):
        merit = {"alpha": 80, "beta": 50, "gamma": 30}
        snap = monitor.measure(diverse_genomes, merit, ["alpha", "beta", "gamma"])
        assert snap.score > 0.3, f"Expected diverse score > 0.3, got {snap.score}"
        assert not snap.in_crisis

    def test_convergent_population_gives_low_score(self, monitor, convergent_genomes):
        merit = {"x1": 50, "x2": 48, "x3": 52}
        snap = monitor.measure(convergent_genomes, merit, ["x1", "x2", "x3"])
        assert snap.score < 0.3, f"Expected convergent score < 0.3, got {snap.score}"

    def test_single_minister_returns_max_diversity(self, monitor):
        g = {"solo": MinisterGenome("solo", "general")}
        snap = monitor.measure(g, {"solo": 50}, ["solo"])
        assert snap.score == 1.0
        assert snap.in_crisis is False

    def test_measure_records_history(self, monitor, diverse_genomes):
        monitor.measure(diverse_genomes, {"alpha": 50, "beta": 50}, ["alpha", "beta"])
        assert len(monitor.history) == 1
        assert isinstance(monitor.history[0], DiversitySnapshot)


class TestCrisisStreak:
    """Crisis streak accumulation and catastrophe triggering tests."""

    @pytest.fixture
    def monitor(self):
        return DiversityMonitor()

    def _make_convergent(self, names):
        return {
            name: MinisterGenome(
                name, "general", temperature=0.7, confidence_baseline=0.85,
                exploration_rate=0.3, conservatism=0.5,
                prompt_mutation_rate=0.1, specialization_weight=1.0,
            )
            for name in names
        }

    def test_crisis_streak_accumulates(self, monitor):
        genomes = self._make_convergent(["a", "b", "c", "d"])
        merit = {"a": 50, "b": 48, "c": 52, "d": 49}

        for i in range(5):
            snap = monitor.measure(genomes, merit, list(genomes.keys()))
            assert snap.in_crisis, f"Cycle {i}: should be in crisis, score={snap.score:.4f}"

        assert monitor.get_crisis_streak() == 5
        assert monitor.is_catastrophe_needed(cycle_count=100)

    def test_recovery_resets_streak(self, monitor):
        # First: 2 crisis cycles
        convergent = self._make_convergent(["a", "b", "c"])
        for _ in range(2):
            monitor.measure(convergent, {"a": 50, "b": 50, "c": 50}, ["a", "b", "c"])
        assert monitor.get_crisis_streak() == 2

        # Then: genuinely diverse population resets
        diverse = {
            "d": MinisterGenome(
                "d", "writing",
                temperature=0.9, confidence_baseline=0.95,
                exploration_rate=0.8, conservatism=0.1,
                prompt_mutation_rate=0.4, specialization_weight=1.5,
            ),
            "e": MinisterGenome(
                "e", "code",
                temperature=0.1, confidence_baseline=0.1,
                exploration_rate=0.05, conservatism=0.95,
                prompt_mutation_rate=0.01, specialization_weight=0.3,
            ),
            "f": MinisterGenome(
                "f", "science",
                temperature=0.5, confidence_baseline=0.5,
                exploration_rate=0.4, conservatism=0.5,
                prompt_mutation_rate=0.2, specialization_weight=1.0,
            ),
        }
        snap = monitor.measure(diverse, {"d": 50, "e": 50, "f": 50}, ["d", "e", "f"])
        assert not snap.in_crisis, f"Should be diverse, score={snap.score:.4f}"
        assert monitor.get_crisis_streak() == 0

    def test_catastrophe_cooldown_prevents_spam(self, monitor):
        genomes = self._make_convergent(["a", "b", "c", "d"])
        merit = {"a": 50, "b": 48, "c": 52, "d": 49}

        # Trigger crisis
        for _ in range(5):
            monitor.measure(genomes, merit, list(genomes.keys()))

        # Need at catastrophe
        assert monitor.is_catastrophe_needed(cycle_count=100)

        # Record a catastrophe
        monitor._last_catastrophe_cycle = 100

        # Immediately after: should NOT fire again
        assert not monitor.is_catastrophe_needed(cycle_count=101)
        assert not monitor.is_catastrophe_needed(cycle_count=119)

        # After cooldown: should fire again
        assert monitor.is_catastrophe_needed(cycle_count=121)


class TestCatastrophePlan:
    """Catastrophe planning tests."""

    @pytest.fixture
    def monitor(self):
        dm = DiversityMonitor()
        # Pre-seed crisis state
        dm._crisis_streak = 5
        dm._last_catastrophe_cycle = -20
        dm.history.append(DiversitySnapshot(
            timestamp="2025-01-01T00:00:00",
            score=0.05, genomic_similarity=0.95,
            merit_variance=0.1, active_count=6, in_crisis=True,
        ))
        return dm

    def test_plan_preserves_top_survivors(self, monitor):
        genomes = {
            "alpha": MinisterGenome("alpha", "writing", temperature=0.7),
            "beta": MinisterGenome("beta", "code", temperature=0.5),
            "gamma": MinisterGenome("gamma", "science", temperature=0.6),
            "delta": MinisterGenome("delta", "search", temperature=0.4),
            "epsilon": MinisterGenome("epsilon", "multimodal", temperature=0.8),
            "zeta": MinisterGenome("zeta", "security", temperature=0.9),
        }
        merit = {
            "alpha": 90, "beta": 85, "gamma": 70,
            "delta": 40, "epsilon": 30, "zeta": 20,
        }
        active = list(genomes.keys())
        all_names = list(genomes.keys())

        plan = monitor.plan_catastrophe(genomes, merit, active, all_names)

        # Top 3 survive
        assert "alpha" in plan.survivors
        assert "beta" in plan.survivors
        assert "gamma" in plan.survivors
        assert len(plan.survivors) == 3

        # Rest eliminated
        assert "delta" in plan.details["eliminated"]
        assert "epsilon" in plan.details["eliminated"]
        assert "zeta" in plan.details["eliminated"]

    def test_plan_spawns_clones_and_specialists(self, monitor):
        genomes = {
            "alpha": MinisterGenome("alpha", "writing", temperature=0.7),
            "beta": MinisterGenome("beta", "code", temperature=0.5),
            "gamma": MinisterGenome("gamma", "science", temperature=0.6),
            "delta": MinisterGenome("delta", "search", temperature=0.4),
        }
        merit = {"alpha": 90, "beta": 85, "gamma": 70, "delta": 40}
        active = list(genomes.keys())

        plan = monitor.plan_catastrophe(genomes, merit, active, active)

        # 3 survivors × 2 clones each = 6 clones
        clones = plan.details["clones"]
        assert len(clones) == 3 * 2
        for clone in clones:
            assert any(s in clone for s in plan.survivors)

        # Specialists for uncovered domains
        specialists = plan.details["specialists"]
        assert len(specialists) >= 1
        # Survivors cover: writing, code, science
        # Remaining domains: research, search, multimodal, finance, security
        missing = plan.details["missing_domains"]
        assert "security" in missing or "multimodal" in missing

    def test_catastrophe_records_history(self, monitor):
        genomes = {
            "alpha": MinisterGenome("alpha", "writing"),
            "beta": MinisterGenome("beta", "code"),
            "gamma": MinisterGenome("gamma", "science"),
            "delta": MinisterGenome("delta", "search"),
        }
        merit = {"alpha": 90, "beta": 85, "gamma": 70, "delta": 40}

        plan = monitor.plan_catastrophe(genomes, merit, list(genomes.keys()), list(genomes.keys()))

        assert len(monitor.catastrophes) == 1
        assert monitor.catastrophes[0] is plan
        assert monitor.get_catastrophe_count() == 1
        assert monitor.get_crisis_streak() == 0  # Reset after catastrophe

    def test_catastrophe_report_fields(self, monitor):
        genomes = {
            "alpha": MinisterGenome("alpha", "writing"),
            "beta": MinisterGenome("beta", "code"),
            "gamma": MinisterGenome("gamma", "science"),
        }
        merit = {"alpha": 90, "beta": 85, "gamma": 70}

        plan = monitor.plan_catastrophe(genomes, merit, list(genomes.keys()), list(genomes.keys()))

        assert plan.trigger_score == 0.05  # from pre-seeded history
        assert plan.crisis_streak == 5
        assert plan.eliminated_count == 0  # all 3 survive
        assert len(plan.survivors) == 3
        assert len(plan.spawned) >= 1

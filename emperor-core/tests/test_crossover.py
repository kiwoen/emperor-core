"""Tests for Simulated Binary Crossover (SBX) in SurvivalMechanism."""

import math
import pytest
import random
from jarvis.court.evolution import (
    CrossoverMode,
    MinisterGenome,
    SurvivalMechanism,
)


class TestSBXGene:
    """Unit tests for the single-gene _sbx_gene helper."""

    @pytest.fixture
    def sm(self):
        return SurvivalMechanism(crossover_mode=CrossoverMode.SBX, sbx_eta=15.0)

    def _sbx(self, sm, v1, v2, lo=0.0, hi=1.0):
        """Extract _sbx_gene for isolated testing."""
        # Access private method via nested function closure
        eta = sm._sbx_eta

        def _sbx_gene(v1: float, v2: float, lo: float, hi: float) -> float:
            if abs(v1 - v2) < 1e-9:
                return v1
            if v1 > v2:
                v1, v2 = v2, v1
            u = random.random()
            if u <= 0.5:
                beta = (2.0 * u) ** (1.0 / (eta + 1.0))
            else:
                beta = (1.0 / (2.0 * (1.0 - u))) ** (1.0 / (eta + 1.0))
            if random.random() < 0.5:
                child_val = 0.5 * ((1.0 + beta) * v1 + (1.0 - beta) * v2)
            else:
                child_val = 0.5 * ((1.0 - beta) * v1 + (1.0 + beta) * v2)
            return max(lo, min(hi, child_val))

        return _sbx_gene(v1, v2, lo, hi)

    def test_identical_parents_returns_same_value(self, sm):
        val = self._sbx(sm, 0.7, 0.7)
        assert val == 0.7

    def test_output_within_bounds(self, sm):
        for _ in range(100):
            val = self._sbx(sm, 0.3, 0.8, 0.0, 1.0)
            assert 0.0 <= val <= 1.0

    def test_output_stays_within_parent_range_most_of_time(self, sm):
        """With high eta (15), most offspring should stay near parents."""
        within = 0
        for _ in range(200):
            val = self._sbx(sm, 0.3, 0.7)
            if 0.25 <= val <= 0.75:
                within += 1
        # Overwhelming majority should stay within or near parent range
        assert within > 120, f"Only {within}/200 stayed in parent range"

    def test_low_eta_produces_more_dispersion(self, sm):
        """Lower eta → more spread-out offspring."""
        sm._sbx_eta = 2.0  # Very exploratory
        inside = 0
        for _ in range(200):
            val = self._sbx(sm, 0.4, 0.6)
            if 0.38 <= val <= 0.62:
                inside += 1
        # With low eta, more values should stray outside parent range
        assert inside < 180, f"Low eta ({sm._sbx_eta}) produced too many inside-range values: {inside}/200"

    def test_high_eta_produces_tight_clustering(self, sm):
        """Higher eta → offspring very close to parents."""
        sm._sbx_eta = 50.0
        inside = 0
        for _ in range(200):
            val = self._sbx(sm, 0.4, 0.6)
            if 0.38 <= val <= 0.62:
                inside += 1
        # Nearly all values should be within or near parent range
        assert inside > 180, f"High eta ({sm._sbx_eta}) produced too many outliers: {inside}/200"


class TestSBXCrossover:
    """Integration tests for _sbx_crossover producing MinisterGenome."""

    @pytest.fixture
    def sm(self):
        return SurvivalMechanism(crossover_mode=CrossoverMode.SBX, sbx_eta=15.0)

    @pytest.fixture
    def p1(self):
        return MinisterGenome(
            "alpha", "writing",
            temperature=0.8, confidence_baseline=0.9,
            exploration_rate=0.7, conservatism=0.2,
            prompt_mutation_rate=0.3, specialization_weight=1.2,
        )

    @pytest.fixture
    def p2(self):
        return MinisterGenome(
            "beta", "code",
            temperature=0.4, confidence_baseline=0.6,
            exploration_rate=0.2, conservatism=0.7,
            prompt_mutation_rate=0.1, specialization_weight=0.8,
        )

    def test_sbx_child_generation_incremented(self, sm, p1, p2):
        child = sm._sbx_crossover(p1, p2, "child", p1)
        assert child.generation == max(p1.generation, p2.generation) + 1

    def test_sbx_child_parent_field(self, sm, p1, p2):
        child = sm._sbx_crossover(p1, p2, "child", p1)
        assert "×" in child.parent
        assert p1.name in child.parent
        assert p2.name in child.parent

    def test_sbx_domain_inherited_from_better_parent(self, sm, p1, p2):
        child = sm._sbx_crossover(p1, p2, "child", p1)
        assert child.domain == p1.domain  # p1 is "better_parent"

    def test_sbx_all_genes_in_bounds(self, sm, p1, p2):
        for _ in range(100):
            child = sm._sbx_crossover(p1, p2, "child", p1)
            assert 0.2 <= child.temperature <= 1.0
            assert 0.3 <= child.confidence_baseline <= 0.95
            assert 0.0 <= child.exploration_rate <= 1.0
            assert 0.0 <= child.conservatism <= 1.0
            assert 0.0 <= child.prompt_mutation_rate <= 0.5
            assert 0.3 <= child.specialization_weight <= 2.0

    def test_sbx_produces_diverse_offspring(self, sm, p1, p2):
        """Run SBX many times; offspring should vary (not always identical)."""
        results = set()
        for _ in range(50):
            child = sm._sbx_crossover(p1, p2, f"child_{_}", p1)
            results.add(round(child.temperature, 3))
        # With 50 runs, should see more than 1 distinct temperature value
        assert len(results) > 1, "SBX produced identical temperature every time"

    def test_sbx_eta_extreme_low(self, sm, p1, p2):
        sm._sbx_eta = 2.0
        child = sm._sbx_crossover(p1, p2, "child", p1)
        # Child should still be valid
        assert 0.2 <= child.temperature <= 1.0

    def test_sbx_eta_extreme_high(self, sm, p1, p2):
        sm._sbx_eta = 100.0
        child = sm._sbx_crossover(p1, p2, "child", p1)
        assert 0.2 <= child.temperature <= 1.0


class TestCrossoverModeSelection:
    """Verify the mode dispatch in _crossover_genome."""

    @pytest.fixture
    def p1(self):
        return MinisterGenome("alpha", "writing", temperature=0.7)

    @pytest.fixture
    def p2(self):
        return MinisterGenome("beta", "code", temperature=0.5)

    def test_default_mode_is_sbx(self, p1, p2):
        """Starting from this commit, default crossover is SBX."""
        sm = SurvivalMechanism()
        assert sm._crossover_mode == CrossoverMode.SBX

    def test_uniform_mode_still_works(self, p1, p2):
        sm = SurvivalMechanism(crossover_mode=CrossoverMode.UNIFORM)
        child = sm._crossover_genome(p1, p2, "child")
        assert child.name == "child"
        assert child.generation == 1  # both parents gen=0

    def test_sbx_mode_produces_smoother_blend(self, p1, p2):
        """SBX children should be continuous blends, not just parent copies."""
        sm = SurvivalMechanism(crossover_mode=CrossoverMode.SBX, sbx_eta=15.0)
        # Run many times: SBX temperature should rarely be exactly 0.5 or 0.7
        exact_matches = 0
        for _ in range(100):
            child = sm._crossover_genome(p1, p2, f"c_{_}")
            if abs(child.temperature - 0.5) < 1e-9 or abs(child.temperature - 0.7) < 1e-9:
                exact_matches += 1
        # SBX rarely produces exact parent values (unlike uniform which does 50%)
        assert exact_matches < 10, f"SBX produced exact matches {exact_matches}/100, expected less"


class TestSBXMath:
    """Verify SBX mathematical properties."""

    def test_beta_symmetry(self):
        """β distribution is symmetric: mean near midpoint, both signs equally represented."""
        eta = 15.0
        above = 0
        below = 0
        for _ in range(2000):
            v1, v2 = 0.3, 0.7
            u = random.random()
            if u <= 0.5:
                beta = (2.0 * u) ** (1.0 / (eta + 1.0))
            else:
                beta = (1.0 / (2.0 * (1.0 - u))) ** (1.0 / (eta + 1.0))
            sign = 1 if random.random() < 0.5 else -1
            child = max(0.0, min(1.0, 0.5 * ((1.0 + sign * beta) * v1 + (1.0 - sign * beta) * v2)))
            midpoint = (v1 + v2) / 2
            if child > midpoint:
                above += 1
            elif child < midpoint:
                below += 1

        # With symmetry, above/below should be roughly equal
        assert abs(above - below) < 120, (
            f"SBX asymmetry: above={above}, below={below}, diff={abs(above-below)}"
        )

    def test_spread_factor_distribution(self):
        """β values should cluster near 1.0 with high eta."""
        eta = 15.0
        betas = []
        for _ in range(2000):
            u = random.random()
            if u <= 0.5:
                beta = (2.0 * u) ** (1.0 / (eta + 1.0))
            else:
                beta = (1.0 / (2.0 * (1.0 - u))) ** (1.0 / (eta + 1.0))
            betas.append(beta)

        mean_beta = sum(betas) / len(betas)
        # With eta=15, mean β should be close to 1.0
        assert 0.98 <= mean_beta <= 1.02, f"Mean β={mean_beta:.4f} not near 1.0"


class TestSBXEtaBoundary:
    """Edge cases for SBX eta parameter."""

    def test_eta_clamped_to_minimum(self):
        sm = SurvivalMechanism(sbx_eta=-5.0)
        assert sm._sbx_eta == 2.0

    def test_eta_clamped_to_maximum(self):
        sm = SurvivalMechanism(sbx_eta=200.0)
        assert sm._sbx_eta == 100.0

    def test_eta_default_is_reasonable(self):
        sm = SurvivalMechanism()
        assert sm._sbx_eta == 15.0

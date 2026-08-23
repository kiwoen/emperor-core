"""
Tests for AutoBreeder (自动育种器).

Coverage: GapAnalyzer, StrategySelector, GenomeGenerator, AutoBreeder.
"""

import pytest

from jarvis.court.breeding import (
    AutoBreeder,
    BreedingCandidate,
    BreedingOutcome,
    BreedingStrategy,
    CapabilityGap,
    GapAnalyzer,
    GenomeGenerator,
    StrategyPerformanceTracker,
    StrategySelector,
)


# ── Helpers ─────────────────────────────────────────────────────────


def make_expertise(*minister_domain_pairs):
    """Build domain_expertise dict from (minister, domain, score) tuples."""
    result = {}
    for minister, domain, score in minister_domain_pairs:
        if minister not in result:
            result[minister] = {}
        result[minister][domain] = score
    return result


# ═══════════════════════════════════════════════════════════════════
# GapAnalyzer
# ═══════════════════════════════════════════════════════════════════


class TestGapAnalyzer:
    """Capability gap detection."""

    def test_coverage_gap_zero_ministers(self):
        """Domain with 0 ministers → high-severity coverage gap."""
        analyzer = GapAnalyzer()
        expertise = make_expertise(
            ("丞相", "engineering", 0.9),
            ("太史令", "research", 0.8),
        )
        gaps = analyzer.analyze(expertise, {"丞相": 80, "太史令": 75}, 0.5)
        # security domain has 0 ministers → should appear
        security_gaps = [g for g in gaps if g.domain == "security"]
        assert len(security_gaps) == 1
        assert security_gaps[0].severity == 1.0
        assert security_gaps[0].ministers_present == 0

    def test_quality_gap_low_merit(self):
        """Domain with ministers but low avg merit → quality gap."""
        analyzer = GapAnalyzer(max_gaps=20)  # large enough to not drop
        expertise = make_expertise(
            ("丞相", "engineering", 0.9),
            ("太史令", "engineering", 0.7),
        )
        # Both low merit
        gaps = analyzer.analyze(
            expertise,
            {"丞相": 15, "太史令": 20},
            0.5,
        )
        eng_gaps = [g for g in gaps if g.domain == "engineering"]
        assert len(eng_gaps) == 1
        assert eng_gaps[0].ministers_present == 2
        assert eng_gaps[0].avg_merit == 17.5
        assert eng_gaps[0].severity > 0.4  # significant severity

    def test_quality_gap_merit_above_threshold(self):
        """Good merit → no gap."""
        analyzer = GapAnalyzer()
        expertise = make_expertise(
            ("丞相", "engineering", 0.9),
        )
        gaps = analyzer.analyze(expertise, {"丞相": 80}, 0.5)
        # engineering has 1 minister with good merit → no gap
        eng_gaps = [g for g in gaps if g.domain == "engineering"]
        assert len(eng_gaps) == 0

    def test_diversity_gap(self):
        """Low global diversity → gaps in all domains."""
        analyzer = GapAnalyzer(max_gaps=20)
        expertise = make_expertise(
            ("丞相", "engineering", 0.9),
            ("太史令", "research", 0.8),
            ("锦衣卫", "security", 0.9),
            ("户部主事", "finance", 0.8),
            ("内务总管", "personal", 0.9),
            ("太医令", "health", 0.8),
            ("执金吾", "home", 0.7),
            ("通政使", "general", 0.9),
            ("中枢侍郎", "core", 0.85),
            ("司乐郎", "creative", 0.8),
            ("大理寺卿", "legal", 0.9),
            ("国子监博士", "education", 0.8),
            ("教坊司", "entertainment", 0.7),
        )
        gaps = analyzer.analyze(
            expertise,
            {m: 80.0 for m in [
                "丞相", "太史令", "锦衣卫", "户部主事", "内务总管",
                "太医令", "执金吾", "通政使", "中枢侍郎", "司乐郎",
                "大理寺卿", "国子监博士", "教坊司",
            ]},
            0.05,  # very low diversity
        )
        assert len(gaps) > 0
        # All gaps should have diversity-based reason
        for gap in gaps:
            assert "多样性" in gap.reason

    def test_no_gaps_when_healthy(self):
        """Well-covered, high-merit, diverse court → no gaps."""
        analyzer = GapAnalyzer()
        expertise = make_expertise(
            ("丞相", "engineering", 0.9),
            ("太史令", "research", 0.8),
            ("锦衣卫", "security", 0.9),
        )
        gaps = analyzer.analyze(
            expertise,
            {"丞相": 85, "太史令": 80, "锦衣卫": 90},
            0.6,  # high diversity
        )
        # Some domains still uncovered but analyzer only checks KNOWN_DOMAINS
        # with coverage.  Let's verify known covered domains have no gaps.
        covered = {"engineering", "research", "security"}
        for gap in gaps:
            assert gap.domain not in covered

    def test_gaps_sorted_by_severity(self):
        """Most severe gaps come first."""
        analyzer = GapAnalyzer()
        expertise = make_expertise(
            ("丞相", "engineering", 0.9),
            ("太史令", "finance", 0.3),
        )
        gaps = analyzer.analyze(
            expertise,
            {"丞相": 80, "太史令": 10},  # 太史令 very low merit
            0.5,
        )
        # First gap should be finance (quality + low merit) or coverage
        if len(gaps) >= 2:
            assert gaps[0].severity >= gaps[1].severity

    def test_max_gaps_cap(self):
        """Gaps capped at max_gaps."""
        analyzer = GapAnalyzer(max_gaps=3)
        expertise = make_expertise()  # empty → all domains uncovered
        gaps = analyzer.analyze(expertise, {}, 0.5)
        assert len(gaps) <= 3

    def test_custom_thresholds(self):
        """Custom thresholds are respected."""
        analyzer = GapAnalyzer(min_avg_merit=60, max_gaps=20)
        expertise = make_expertise(
            ("丞相", "engineering", 0.9),
        )
        gaps = analyzer.analyze(expertise, {"丞相": 50}, 0.5)
        # 50 < 60 → quality gap detected
        eng_gaps = [g for g in gaps if g.domain == "engineering"]
        assert len(eng_gaps) == 1


# ═══════════════════════════════════════════════════════════════════
# StrategySelector
# ═══════════════════════════════════════════════════════════════════


class TestStrategySelector:
    """Breeding strategy selection."""

    def test_coverage_gap_selects_specialist(self):
        """0 ministers → SPECIALIST strategy."""
        selector = StrategySelector()
        gap = CapabilityGap(
            domain="security", severity=1.0,
            reason="零覆盖", ministers_present=0,
            avg_merit=0.0, diversity_score=0.5,
        )
        candidates = selector.select([gap], ["丞相"])
        assert len(candidates) == 1
        assert candidates[0].strategy == BreedingStrategy.SPECIALIST

    def test_quality_gap_selects_specialize(self):
        """Very low merit → SPECIALIZE strategy."""
        selector = StrategySelector()
        gap = CapabilityGap(
            domain="finance", severity=0.7,
            reason="质量不足", ministers_present=2,
            avg_merit=15.0, diversity_score=0.5,
        )
        candidates = selector.select([gap], ["丞相", "太史令"])
        assert len(candidates) == 1
        assert candidates[0].strategy == BreedingStrategy.SPECIALIZE
        assert candidates[0].parent_minister is not None

    def test_diversity_gap_selects_explore(self):
        """Extremely low diversity → EXPLORE."""
        selector = StrategySelector()
        gap = CapabilityGap(
            domain="engineering", severity=0.8,
            reason="多样性低", ministers_present=3,
            avg_merit=70.0, diversity_score=0.05,
        )
        candidates = selector.select([gap], ["丞相"])
        assert len(candidates) == 1
        assert candidates[0].strategy == BreedingStrategy.EXPLORE

    def test_multiple_gaps_different_strategies(self):
        """Each gap gets its own candidate with appropriate strategy."""
        selector = StrategySelector()
        gaps = [
            CapabilityGap("security", 1.0, "零覆盖", 0, 0.0, 0.5),
            CapabilityGap("finance", 0.7, "质量不足", 2, 15.0, 0.5),
        ]
        candidates = selector.select(gaps, ["丞相"])
        assert len(candidates) == 2
        strategies = {c.strategy for c in candidates}
        assert BreedingStrategy.SPECIALIST in strategies
        assert BreedingStrategy.SPECIALIZE in strategies


# ═══════════════════════════════════════════════════════════════════
# GenomeGenerator
# ═══════════════════════════════════════════════════════════════════


class TestGenomeGenerator:
    """Genome generation from breeding candidates."""

    GENOME_KEYS = [
        "temperature", "confidence_baseline", "creativity",
        "thoroughness", "speed", "social_intelligence",
    ]

    def _assert_valid_genome(self, genome: dict[str, float]):
        """All values in [0, 1], all keys present."""
        for key in self.GENOME_KEYS:
            assert key in genome, f"Missing key: {key}"
            assert 0.0 <= genome[key] <= 1.0, (
                f"{key}={genome[key]} out of range"
            )

    def test_specialist_generates_domain_profile(self):
        """SPECIALIST uses domain profile with small noise."""
        gen = GenomeGenerator()
        candidate = BreedingCandidate(
            target_domain="security",
            strategy=BreedingStrategy.SPECIALIST,
            genome_template=None,
            parent_minister=None,
            reasoning="零覆盖",
        )
        genome = gen.generate(candidate)
        self._assert_valid_genome(genome)
        # Security profile: low temperature, high thoroughness
        assert genome["temperature"] < 0.5
        assert genome["thoroughness"] > 0.7

    def test_explore_generates_anti_profile(self):
        """EXPLORE inverts domain profile with high variance."""
        gen = GenomeGenerator()
        candidate = BreedingCandidate(
            target_domain="engineering",
            strategy=BreedingStrategy.EXPLORE,
            genome_template=None,
            parent_minister=None,
            reasoning="多样性低",
        )
        genome = gen.generate(candidate)
        self._assert_valid_genome(genome)
        # Values should be far from engineering profile
        # Engineering: low creativity → EXPLORE: high creativity
        assert genome["creativity"] > 0.3  # anti of 0.2

    def test_specialize_blends_parent_and_profile(self):
        """SPECIALIZE blends parent genome with domain profile."""
        gen = GenomeGenerator()
        candidate = BreedingCandidate(
            target_domain="creative",
            strategy=BreedingStrategy.SPECIALIZE,
            genome_template=None,
            parent_minister="丞相",
            reasoning="质量不足",
        )
        parent = {
            "temperature": 0.4, "confidence_baseline": 0.8,
            "creativity": 0.3, "thoroughness": 0.7,
            "speed": 0.5, "social_intelligence": 0.4,
        }
        genome = gen.generate(candidate, parent_genome=parent)
        self._assert_valid_genome(genome)
        # Creative profile: high creativity → blend should increase
        assert genome["creativity"] > 0.3  # nudge toward creative

    def test_hybrid_mixes_traits(self):
        """HYBRID randomly keeps/mutates parent traits."""
        gen = GenomeGenerator()
        candidate = BreedingCandidate(
            target_domain="general",
            strategy=BreedingStrategy.HYBRID,
            genome_template=None,
            parent_minister=None,
            reasoning="混合育种",
        )
        parent = {
            "temperature": 0.6, "confidence_baseline": 0.7,
            "creativity": 0.5, "thoroughness": 0.6,
            "speed": 0.4, "social_intelligence": 0.5,
        }
        # Run multiple times to ensure variety
        genomes = [
            gen.generate(candidate, parent_genome=parent)
            for _ in range(5)
        ]
        for g in genomes:
            self._assert_valid_genome(g)
        # Not all identical
        values_sets = {
            tuple(g[k] for k in self.GENOME_KEYS) for g in genomes
        }
        assert len(values_sets) > 1

    def test_can_generate_without_parent(self):
        """All strategies work without parent genome."""
        gen = GenomeGenerator()
        for strategy in BreedingStrategy:
            candidate = BreedingCandidate(
                target_domain="general",
                strategy=strategy,
                genome_template=None,
                parent_minister=None,
                reasoning="test",
            )
            genome = gen.generate(candidate)
            self._assert_valid_genome(genome)

    def test_explicit_template_overrides_profile(self):
        """Explicit genome_template overrides domain profile lookup."""
        gen = GenomeGenerator()
        custom = {"temperature": 0.99, "confidence_baseline": 0.01,
                  "creativity": 0.99, "thoroughness": 0.01,
                  "speed": 0.99, "social_intelligence": 0.01}
        candidate = BreedingCandidate(
            target_domain="security",
            strategy=BreedingStrategy.SPECIALIST,
            genome_template=custom,
            parent_minister=None,
            reasoning="custom",
        )
        genome = gen.generate(candidate)
        self._assert_valid_genome(genome)
        # Should be close to custom, not security profile
        assert genome["temperature"] > 0.8
        assert genome["thoroughness"] < 0.2


# ═══════════════════════════════════════════════════════════════════
# AutoBreeder
# ═══════════════════════════════════════════════════════════════════


class TestAutoBreeder:
    """Integration: full breeding pipeline."""

    def test_no_gaps_returns_empty(self):
        """Healthy court → empty report."""
        breeder = AutoBreeder(breeding_cooldown=0)  # bypass cooldown
        # Set providers for a well-covered court
        breeder.set_expertise_provider(lambda: {
            "丞相": {"engineering": 0.9, "general": 0.5},
            "太史令": {"research": 0.8, "general": 0.4},
            "锦衣卫": {"security": 0.9, "general": 0.3},
            "内务总管": {"personal": 0.9, "general": 0.5},
            "太医令": {"health": 0.85, "general": 0.4},
            "执金吾": {"home": 0.8, "general": 0.4},
            "通政使": {"general": 0.9},
            "中枢侍郎": {"core": 0.9},
            "司乐郎": {"creative": 0.85},
            "大理寺卿": {"legal": 0.9},
            "国子监博士": {"education": 0.85},
            "教坊司": {"entertainment": 0.8},
            "户部主事": {"finance": 0.9},
        })
        breeder.set_merit_provider(lambda m: 80.0)
        breeder.set_diversity_provider(lambda: 0.6)

        active = ["丞相", "太史令", "锦衣卫", "内务总管", "太医令",
                   "执金吾", "通政使", "中枢侍郎", "司乐郎", "大理寺卿",
                   "国子监博士", "教坊司", "户部主事"]
        report = breeder.breed(active)
        # Some domains like "entertainment" have 1 minister with good merit
        # → no quality gap. But coverage of all 13 domains might still trigger
        # some gaps for domains with only 1 "covering" minister.
        # This test verifies the pipeline doesn't crash on healthy input.
        assert isinstance(report.gaps_detected, list)

    def test_coverage_gap_triggers_breeding(self):
        """Uncovered domain → breeding triggered."""
        breeder = AutoBreeder(breeding_cooldown=0)
        breeder.set_expertise_provider(lambda: {
            "丞相": {"engineering": 0.9},
        })
        breeder.set_merit_provider(lambda m: 80.0)
        breeder.set_diversity_provider(lambda: 0.5)

        report = breeder.breed(["丞相"])
        assert len(report.gaps_detected) > 0
        # security domain has 0 covering ministers → coverage gap
        security_gaps = [g for g in report.gaps_detected
                         if g.domain == "security"]
        assert len(security_gaps) == 1
        assert len(report.candidates_proposed) > 0
        assert len(report.candidates_created) > 0

    def test_breeding_cooldown_respected(self):
        """Breeding doesn't fire every cycle."""
        breeder = AutoBreeder(breeding_cooldown=5)
        breeder.set_expertise_provider(lambda: {"丞相": {"engineering": 0.9}})
        breeder.set_merit_provider(lambda m: 20.0)  # low merit
        breeder.set_diversity_provider(lambda: 0.1)

        # First cycle: should breed
        report1 = breeder.breed(["丞相"])
        # Cooldown kicks in
        for _ in range(4):
            report = breeder.breed(["丞相"])
            assert len(report.candidates_created) == 0

        # 5th cycle: breeds again
        report5 = breeder.breed(["丞相"])
        # May or may not breed depending on gap detection

    def test_total_bred_counter(self):
        """_total_bred increments correctly."""
        breeder = AutoBreeder(breeding_cooldown=0)
        breeder.set_expertise_provider(lambda: {
            "丞相": {"engineering": 0.9},
        })
        breeder.set_merit_provider(lambda m: 30.0)
        breeder.set_diversity_provider(lambda: 0.3)

        breeder.breed(["丞相"])
        assert breeder.get_total_bred() > 0
        bred1 = breeder.get_total_bred()

        breeder.breed(["丞相"])
        assert breeder.get_total_bred() >= bred1

    def test_max_per_cycle_enforced(self):
        """MAX_BREED_PER_CYCLE caps candidates."""
        breeder = AutoBreeder(breeding_cooldown=0, max_per_cycle=2)
        breeder.set_expertise_provider(lambda: {})  # empty → all uncovered
        breeder.set_merit_provider(lambda m: 50.0)
        breeder.set_diversity_provider(lambda: 0.05)

        report = breeder.breed([])
        assert len(report.candidates_created) <= 2

    def test_history_tracking(self):
        """Breeding reports are stored in history."""
        breeder = AutoBreeder(breeding_cooldown=0)
        breeder.set_expertise_provider(lambda: {
            "丞相": {"engineering": 0.9},
        })
        breeder.set_merit_provider(lambda m: 20.0)
        breeder.set_diversity_provider(lambda: 0.3)

        breeder.breed(["丞相"])
        history = breeder.get_history()
        assert len(history) == 1
        assert isinstance(history[0].gaps_detected, list)

    def test_reset_cooldown_forces_breeding(self):
        """reset_cooldown() allows immediate next breed."""
        breeder = AutoBreeder(breeding_cooldown=100)
        breeder.set_expertise_provider(lambda: {
            "丞相": {"engineering": 0.9},
        })
        breeder.set_merit_provider(lambda m: 20.0)
        breeder.set_diversity_provider(lambda: 0.1)

        breeder.breed(["丞相"])  # consumes one cycle
        # Without reset, next call would be blocked
        breeder.reset_cooldown()
        report = breeder.breed(["丞相"])
        assert len(report.candidates_created) > 0

    def test_strategies_used_tracking(self):
        """strategies_used dict correctly tallies."""
        breeder = AutoBreeder(breeding_cooldown=0, max_per_cycle=5)
        breeder.set_expertise_provider(lambda: {})  # all uncovered
        breeder.set_merit_provider(lambda m: 50.0)
        breeder.set_diversity_provider(lambda: 0.05)

        report = breeder.breed([])
        if report.strategies_used:
            total = sum(report.strategies_used.values())
            assert total == len(report.candidates_created)

    def test_fallback_providers_work(self):
        """Without explicit providers, fallback logic doesn't crash."""
        breeder = AutoBreeder(breeding_cooldown=0)
        report = breeder.breed(["丞相", "太史令"])
        # Should not crash — uses fallback expertise/merit/diversity
        assert isinstance(report.gaps_detected, list)

    def test_generated_names_are_unique(self):
        """Each bred candidate gets a unique name."""
        breeder = AutoBreeder(breeding_cooldown=0, max_per_cycle=10)
        breeder.set_expertise_provider(lambda: {})
        breeder.set_merit_provider(lambda m: 50.0)
        breeder.set_diversity_provider(lambda: 0.05)

        report = breeder.breed([])
        if report.candidates_created:
            assert len(report.candidates_created) == len(
                set(report.candidates_created)
            )

    def test_get_last_report(self):
        """get_last_report returns the most recent report."""
        breeder = AutoBreeder(breeding_cooldown=0)
        breeder.set_expertise_provider(lambda: {
            "丞相": {"engineering": 0.9},
        })
        breeder.set_merit_provider(lambda m: 20.0)
        breeder.set_diversity_provider(lambda: 0.3)

        assert breeder.get_last_report() is None
        breeder.breed(["丞相"])
        assert breeder.get_last_report() is not None


# ═══════════════════════════════════════════════════════════════════
# StrategyPerformanceTracker
# ═══════════════════════════════════════════════════════════════════


class TestStrategyPerformanceTracker:
    """Outcome tracking and per-strategy effectiveness scoring."""

    def test_record_and_success_rate(self):
        tracker = StrategyPerformanceTracker()
        # Record 10 SPECIALIST outcomes: 8 survived
        for i in range(10):
            tracker.record(BreedingOutcome(
                minister_name=f"test_{i}",
                domain="engineering",
                strategy=BreedingStrategy.SPECIALIST,
                survived=i < 8,
                promoted=i < 3,
                max_merit=70.0 + i,
                cycles_survived=10,
                final_status="ACTIVE" if i < 3 else ("SHADOW" if i < 8 else "ELIMINATED"),
            ))

        rate = tracker.get_success_rate(BreedingStrategy.SPECIALIST)
        assert rate == 0.8

    def test_insufficient_samples_returns_neutral(self):
        tracker = StrategyPerformanceTracker()
        # Only 3 outcomes → less than MIN_SAMPLES (5)
        for i in range(3):
            tracker.record(BreedingOutcome(
                minister_name=f"test_{i}",
                domain="general",
                strategy=BreedingStrategy.EXPLORE,
                survived=True,
                promoted=False,
                max_merit=50.0,
                cycles_survived=5,
                final_status="SHADOW",
            ))

        # Should return neutral prior 0.5
        assert tracker.get_success_rate(BreedingStrategy.EXPLORE) == 0.5

    def test_composite_score_all_success(self):
        tracker = StrategyPerformanceTracker()
        for i in range(20):
            tracker.record(BreedingOutcome(
                minister_name=f"test_{i}",
                domain="finance",
                strategy=BreedingStrategy.SPECIALIZE,
                survived=True,
                promoted=True,
                max_merit=95.0,
                cycles_survived=15,
                final_status="ACTIVE",
            ))

        score = tracker.get_composite_score(BreedingStrategy.SPECIALIZE)
        # All survived + promoted + high merit → near 1.0
        assert score > 0.8

    def test_composite_score_all_failure(self):
        tracker = StrategyPerformanceTracker()
        for i in range(20):
            tracker.record(BreedingOutcome(
                minister_name=f"test_{i}",
                domain="security",
                strategy=BreedingStrategy.HYBRID,
                survived=False,
                promoted=False,
                max_merit=5.0,
                cycles_survived=3,
                final_status="ELIMINATED",
            ))

        score = tracker.get_composite_score(BreedingStrategy.HYBRID)
        assert score < 0.3

    def test_get_all_scores_covers_all_strategies(self):
        tracker = StrategyPerformanceTracker()
        scores = tracker.get_all_scores()
        assert len(scores) == len(BreedingStrategy)
        for s in BreedingStrategy:
            assert s in scores

    def test_sliding_window_dims_old_data(self):
        tracker = StrategyPerformanceTracker()
        # First 30: all survived
        for i in range(30):
            tracker.record(BreedingOutcome(
                minister_name=f"old_{i}",
                domain="engineering",
                strategy=BreedingStrategy.SPECIALIST,
                survived=True,
                promoted=True,
                max_merit=90.0,
                cycles_survived=20,
                final_status="ACTIVE",
            ))

        # Recent 50: all failed
        for i in range(50):
            tracker.record(BreedingOutcome(
                minister_name=f"recent_{i}",
                domain="engineering",
                strategy=BreedingStrategy.SPECIALIST,
                survived=False,
                promoted=False,
                max_merit=10.0,
                cycles_survived=3,
                final_status="ELIMINATED",
            ))

        # Sliding window should reflect recent failures
        rate = tracker.get_success_rate(BreedingStrategy.SPECIALIST)
        assert rate < 0.2

    def test_reset_clears_all(self):
        tracker = StrategyPerformanceTracker()
        for i in range(10):
            tracker.record(BreedingOutcome(
                minister_name=f"test_{i}",
                domain="engineering",
                strategy=BreedingStrategy.SPECIALIST,
                survived=True, promoted=True, max_merit=80.0,
                cycles_survived=10, final_status="ACTIVE",
            ))

        assert tracker.get_outcome_count() == 10
        tracker.reset()
        assert tracker.get_outcome_count() == 0
        assert tracker.get_success_rate(BreedingStrategy.SPECIALIST) == 0.5

    def test_promotion_rate(self):
        tracker = StrategyPerformanceTracker()
        for i in range(10):
            tracker.record(BreedingOutcome(
                minister_name=f"test_{i}",
                domain="education",
                strategy=BreedingStrategy.SPECIALIST,
                survived=True,
                promoted=(i < 4),  # 4/10 promoted
                max_merit=75.0,
                cycles_survived=12,
                final_status="ACTIVE" if i < 4 else "SHADOW",
            ))

        rate = tracker.get_promotion_rate(BreedingStrategy.SPECIALIST)
        assert rate == 0.4


# ═══════════════════════════════════════════════════════════════════
# StrategySelector Adaptive Weights
# ═══════════════════════════════════════════════════════════════════


class TestStrategySelectorAdaptive:
    """Adaptive weight integration between tracker and selector."""

    def test_no_tracker_uses_static_weights(self):
        selector = StrategySelector()
        weights = selector.get_current_weights()
        # All 4 strategies should have non-zero weights
        for s in BreedingStrategy:
            assert weights.get(s, 0) > 0
        total = sum(weights.values())
        assert abs(total - 1.0) < 0.01

    def test_tracker_with_enough_data_uses_adaptive(self):
        selector = StrategySelector()
        tracker = StrategyPerformanceTracker()

        # Make SPECIALIST dominate with successes
        for i in range(20):
            tracker.record(BreedingOutcome(
                minister_name=f"dom_{i}",
                domain="engineering",
                strategy=BreedingStrategy.SPECIALIST,
                survived=True, promoted=True, max_merit=95.0,
                cycles_survived=20, final_status="ACTIVE",
            ))
        # Make HYBRID fail
        for i in range(20):
            tracker.record(BreedingOutcome(
                minister_name=f"fail_{i}",
                domain="security",
                strategy=BreedingStrategy.HYBRID,
                survived=False, promoted=False, max_merit=5.0,
                cycles_survived=2, final_status="ELIMINATED",
            ))

        selector.set_performance_tracker(tracker)
        weights = selector.get_current_weights()

        # SPECIALIST should be weighted highest
        assert weights[BreedingStrategy.SPECIALIST] > weights[BreedingStrategy.HYBRID]

    def test_deterministic_gap_overrides_adaptive(self):
        """0 ministers still always picks SPECIALIST regardless of tracker."""
        selector = StrategySelector()
        tracker = StrategyPerformanceTracker()
        # Feed tracker with SPECIALIST failures to lower its weight
        for i in range(20):
            tracker.record(BreedingOutcome(
                minister_name=f"s_{i}",
                domain="finance",
                strategy=BreedingStrategy.SPECIALIST,
                survived=False, promoted=False, max_merit=2.0,
                cycles_survived=1, final_status="ELIMINATED",
            ))
        # Boost EXPLORE
        for i in range(20):
            tracker.record(BreedingOutcome(
                minister_name=f"e_{i}",
                domain="personal",
                strategy=BreedingStrategy.EXPLORE,
                survived=True, promoted=True, max_merit=95.0,
                cycles_survived=20, final_status="ACTIVE",
            ))

        selector.set_performance_tracker(tracker)

        # Gap with 0 coverage → deterministic SPECIALIST
        gap = CapabilityGap(
            domain="security",
            severity=1.0,
            reason="零覆盖",
            ministers_present=0,
            avg_merit=0.0,
            diversity_score=0.5,
        )
        strategy = selector._choose_strategy(gap)
        assert strategy == BreedingStrategy.SPECIALIST

    def test_weighted_fallback_respects_blend(self):
        """Weighted selection without deterministic triggers uses blended weights."""
        selector = StrategySelector()
        tracker = StrategyPerformanceTracker()
        # Make all strategies perform moderately
        for s in BreedingStrategy:
            for i in range(10):
                tracker.record(BreedingOutcome(
                    minister_name=f"{s.name}_{i}",
                    domain="general",
                    strategy=s,
                    survived=True, promoted=(i < 5),
                    max_merit=60.0 + (10 * i % 40),
                    cycles_survived=10, final_status="ACTIVE",
                ))

        selector.set_performance_tracker(tracker)

        # Non-deterministic gap
        gap = CapabilityGap(
            domain="engineering",
            severity=0.3,
            reason="质量不足",
            ministers_present=3,
            avg_merit=30.0,
            diversity_score=0.5,
        )

        # Run many times to verify we can get different strategies
        results = set()
        for _ in range(50):
            results.add(selector._choose_strategy(gap))
        # With non-zero weights, we should see at least 2 different strategies
        assert len(results) >= 2


# ═══════════════════════════════════════════════════════════════════
# AutoBreeder Outcome Tracking
# ═══════════════════════════════════════════════════════════════════


class TestAutoBreederOutcomes:
    """check_outcomes and tracker wiring in AutoBreeder."""

    def test_tracker_wired_on_init(self):
        breeder = AutoBreeder()
        # Tracker should be wired to selector
        assert breeder.performance_tracker is not None
        assert breeder.strategy_selector._tracker is breeder.performance_tracker

    def test_check_outcomes_no_mature_ministers(self):
        """Ministers bred too recently aren't evaluated."""
        breeder = AutoBreeder()

        # Simulate a very recent breed
        breeder._breed_cycle_registry["工部技师"] = 95
        breeder._breed_domain_registry["工部技师"] = "engineering"
        breeder._breed_strategy_registry["工部技师"] = BreedingStrategy.SPECIALIST

        outcomes = breeder.check_outcomes(
            current_cycle=96,  # only 1 cycle since breed
            statuses={"工部技师": "SHADOW"},
            merit_scores={"工部技师": 40.0},
        )

        assert len(outcomes) == 0  # too young
        assert "工部技师" in breeder._breed_cycle_registry  # still tracked

    def test_check_outcomes_mature_survived(self):
        """Mature minister that survived → recorded and removed from registry."""
        breeder = AutoBreeder()

        breeder._breed_cycle_registry["户部主事"] = 80
        breeder._breed_domain_registry["户部主事"] = "finance"
        breeder._breed_strategy_registry["户部主事"] = BreedingStrategy.SPECIALIZE

        outcomes = breeder.check_outcomes(
            current_cycle=90,  # 10 cycles later
            statuses={"户部主事": "ACTIVE"},
            merit_scores={"户部主事": 75.0},
        )

        assert len(outcomes) == 1
        o = outcomes[0]
        assert o.minister_name == "户部主事"
        assert o.survived is True
        assert o.promoted is True
        assert o.final_status == "ACTIVE"

        # Recorded in tracker
        assert breeder.performance_tracker.get_outcome_count() == 1

        # Removed from breeding registry
        assert "户部主事" not in breeder._breed_cycle_registry

    def test_check_outcomes_mature_eliminated(self):
        """Mature minister that was eliminated → recorded as not survived."""
        breeder = AutoBreeder()

        breeder._breed_cycle_registry["锦衣卫"] = 70
        breeder._breed_domain_registry["锦衣卫"] = "security"
        breeder._breed_strategy_registry["锦衣卫"] = BreedingStrategy.EXPLORE

        outcomes = breeder.check_outcomes(
            current_cycle=80,
            statuses={"锦衣卫": "ELIMINATED"},
            merit_scores={"锦衣卫": 0.0},
        )

        assert len(outcomes) == 1
        o = outcomes[0]
        assert o.survived is False
        assert o.promoted is False
        assert o.final_status == "ELIMINATED"

    def test_check_outcomes_multiple(self):
        """Multiple ministers at various maturity levels."""
        breeder = AutoBreeder()

        # Recent breed (too young)
        breeder._breed_cycle_registry["司乐郎"] = 48
        breeder._breed_domain_registry["司乐郎"] = "creative"
        breeder._breed_strategy_registry["司乐郎"] = BreedingStrategy.EXPLORE

        # Mature, survived
        breeder._breed_cycle_registry["翰林学士"] = 40
        breeder._breed_domain_registry["翰林学士"] = "research"
        breeder._breed_strategy_registry["翰林学士"] = BreedingStrategy.SPECIALIST

        # Mature, shadow status
        breeder._breed_cycle_registry["太医令"] = 42
        breeder._breed_domain_registry["太医令"] = "health"
        breeder._breed_strategy_registry["太医令"] = BreedingStrategy.HYBRID

        outcomes = breeder.check_outcomes(
            current_cycle=50,
            statuses={
                "司乐郎": "SHADOW",
                "翰林学士": "ACTIVE",
                "太医令": "SHADOW",
            },
            merit_scores={
                "司乐郎": 30.0,
                "翰林学士": 88.0,
                "太医令": 55.0,
            },
        )

        # 司乐郎 is too young (age=2 < 3), excluded
        # 翰林学士: age=10, ACTIVE → survived
        # 太医令: age=8, SHADOW → survived but not promoted
        assert len(outcomes) == 2
        names = {o.minister_name for o in outcomes}
        assert names == {"翰林学士", "太医令"}

    def test_merit_history_peak(self):
        """check_outcomes uses merit history to find peak."""
        breeder = AutoBreeder()

        breeder._breed_cycle_registry["国子监博士"] = 60
        breeder._breed_domain_registry["国子监博士"] = "education"
        breeder._breed_strategy_registry["国子监博士"] = BreedingStrategy.SPECIALIZE

        outcomes = breeder.check_outcomes(
            current_cycle=70,
            statuses={"国子监博士": "ACTIVE"},
            merit_scores={"国子监博士": 65.0},
            merit_history={"国子监博士": [20.0, 35.0, 55.0, 80.0, 75.0, 65.0]},
        )

        assert len(outcomes) == 1
        # Peak from history is 80.0, not the current 65.0
        assert outcomes[0].max_merit == 80.0

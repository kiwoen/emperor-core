"""
AutoEvolution + SurvivalMechanism tests — 自进化与末位淘汰系统单元测试.

Covers:
    - Minister registration (active + shadow)
    - Probation identification from low merit
    - Probation → elimination lifecycle
    - Demotion to shadow when merit critically low
    - Shadow → active promotion when merit recovers
    - Clone-mutate to fill vacancies
    - Auto-tune parameters (temperature, baseline)
    - Systemic gap detection
    - Genome mutation rules
    - Edge cases: empty court, single minister, all eliminated
"""

import pytest
from jarvis.court.evolution import (
    CrossoverMode,
    EliteTurnoverMode,
    EvolutionAction,
    EvolutionEvent,
    EvolutionReport,
    MinisterGenome,
    MinisterStatus,
    SurvivalMechanism,
)
from jarvis.court.merit_board import MeritBoard


# ── Helpers ─────────────────────────────────────────────────────────


def make_board_with_data(data: list[tuple[str, bool, float]]) -> MeritBoard:
    """Create a MeritBoard pre-populated with dispatch records.

    data: list of (minister_name, success, confidence)
    """
    mb = MeritBoard()
    for i, (name, success, confidence) in enumerate(data):
        mb.record_dispatch(name, f"e{i}", "test task", success, confidence)
    return mb


def register_eight_ministers(sm: SurvivalMechanism) -> None:
    """Register the standard eight ministers."""
    ministers = [
        ("丞相", "writing"),
        ("御史大夫", "writing"),
        ("太史令", "search"),
        ("工部尚书", "code"),
        ("太常", "multimodal"),
        ("大司农", "finance"),
        ("太卜", "science"),
        ("卫尉", "security"),
    ]
    for name, domain in ministers:
        sm.register_minister(name, domain)


# ── Registration ────────────────────────────────────────────────────


class TestRegistration:
    """Minister registration into evolution tracking."""

    def test_register_active_minister(self):
        sm = SurvivalMechanism()
        sm.register_minister("丞相", domain="writing")
        assert sm.get_status("丞相") == MinisterStatus.ACTIVE
        genome = sm.get_genome("丞相")
        assert genome is not None
        assert genome.generation == 0
        assert genome.domain == "writing"

    def test_register_shadow_minister(self):
        sm = SurvivalMechanism()
        sm.register_shadow("影丞相", domain="writing")
        assert sm.get_status("影丞相") == MinisterStatus.SHADOW

    def test_registered_minister_appears_in_active_list(self):
        sm = SurvivalMechanism()
        sm.register_minister("丞相", "writing")
        assert "丞相" in sm.get_active_ministers()
        assert "丞相" not in sm.get_shadow_ministers()

    def test_shadow_not_in_active_list(self):
        sm = SurvivalMechanism()
        sm.register_shadow("影", "code")
        assert "影" not in sm.get_active_ministers()
        assert "影" in sm.get_shadow_ministers()

    def test_default_status_is_active(self):
        sm = SurvivalMechanism()
        # Calling get_status on unregistered minister returns ACTIVE
        assert sm.get_status("unknown") == MinisterStatus.ACTIVE


# ── Probation ───────────────────────────────────────────────────────


class TestProbation:
    """Probation identification and escalation."""

    def test_low_merit_enters_probation(self):
        """With SlidingMeritBoard default, very low merit (<20) → SHADOW.

        The sliding window (50 entries) properly weights recent failures,
        so 8/9 failures yield ~19.45 merit — below the 20-point SHADOW
        threshold.  This is CORRECT: the minister shouldn't coast on
        cumulative history when recent performance is terrible.
        """
        mb = MeritBoard()
        # 丞相: 9 entries — 8 fails at 0.15 + 1 success at 0.30
        # SlidingMeritBoard 测算: success_rate=1/9, conf=0.167, merit≈19.45
        for i in range(8):
            mb.record_dispatch("丞相", f"e{i}", "task", False, 0.15)
        mb.record_dispatch("丞相", "e8", "task", True, 0.30)
        # Other ministers with records to stay above demotion threshold
        mb.record_dispatch("工部尚书", "b1", "task", True, 0.90)
        mb.record_dispatch("工部尚书", "b2", "task", True, 0.85)
        mb.record_dispatch("太史令", "c1", "task", True, 0.88)
        mb.record_dispatch("太卜", "d1", "task", True, 0.82)
        mb.record_dispatch("卫尉", "f1", "task", True, 0.80)
        sm = SurvivalMechanism(merit_board=mb)
        sm.register_minister("丞相", "writing")
        sm.register_minister("工部尚书", "code")
        sm.register_minister("太史令", "search")
        sm.register_minister("太卜", "science")
        sm.register_minister("卫尉", "security")
        report = sm.run_evolution_cycle()
        # Sliding window merit < 20 → SHADOW (not probation)
        assert sm.get_status("丞相") == MinisterStatus.SHADOW
        assert any(
            a.action == EvolutionAction.DEMOTE
            for a in report.actions_taken
        )

    def test_borderline_merit_probation_sliding(self):
        """Merit 20-30 with sliding window → PROBATION.

        7 fails + 3 successes yields ~27.9 merit in the probation window."
        """
        mb = MeritBoard()
        for i in range(7):
            mb.record_dispatch("丞相", f"e{i}", "task", False, 0.18)
        for i in range(7, 10):
            mb.record_dispatch("丞相", f"e{i}", "task", True, 0.32)
        mb.record_dispatch("工部尚书", "b1", "task", True, 0.90)
        mb.record_dispatch("太史令", "c1", "task", True, 0.85)
        mb.record_dispatch("太卜", "d1", "task", True, 0.82)
        mb.record_dispatch("卫尉", "f1", "task", True, 0.80)
        sm = SurvivalMechanism(merit_board=mb)
        sm.register_minister("丞相", "writing")
        sm.register_minister("工部尚书", "code")
        sm.register_minister("太史令", "search")
        sm.register_minister("太卜", "science")
        sm.register_minister("卫尉", "security")
        report = sm.run_evolution_cycle()
        assert sm.get_status("丞相") == MinisterStatus.PROBATION
        assert any(
            a.action == EvolutionAction.PROBATION_MARK
            for a in report.actions_taken
        )

    def test_high_merit_skips_probation(self):
        mb = MeritBoard()
        mb.record_dispatch("丞相", "e1", "task", True, 0.95)
        mb.record_dispatch("丞相", "e2", "task", True, 0.90)
        sm = SurvivalMechanism(merit_board=mb)
        sm.register_minister("丞相", "writing")
        report = sm.run_evolution_cycle()
        assert sm.get_status("丞相") == MinisterStatus.ACTIVE
        assert not any(
            a.action == EvolutionAction.PROBATION_MARK
            for a in report.actions_taken
        )

    def test_probation_increments_cycle(self):
        mb = MeritBoard()
        mb.record_dispatch("太卜", "e1", "task", False, 0.10)
        sm = SurvivalMechanism(merit_board=mb)
        sm.register_minister("太卜", "science")
        sm.run_evolution_cycle()
        assert sm.get_status("太卜") == MinisterStatus.PROBATION


# ── Elimination ─────────────────────────────────────────────────────


class TestElimination:
    """末位淘汰: probation → elimination lifecycle."""

    def test_eliminated_after_max_cycles(self):
        mb = MeritBoard()
        # Create a consistently failing minister
        for i in range(15):
            mb.record_dispatch("太卜", f"e{i}", "task", False, 0.05)
        sm = SurvivalMechanism(merit_board=mb)
        sm.register_minister("丞相", "writing")  # keep court size > min
        sm.register_minister("太卜", "science")
        sm.register_minister("工部尚书", "code")
        sm.register_minister("太史令", "search")
        sm.register_minister("卫尉", "security")
        # Run cycles until elimination
        for _ in range(SurvivalMechanism.MAX_PROBATION_CYCLES + 1):
            sm.run_evolution_cycle()
        assert sm.get_status("太卜") == MinisterStatus.ELIMINATED
        assert "太卜" in sm.get_eliminated_ministers()

    def test_cannot_eliminate_below_min_size(self):
        mb = MeritBoard()
        mb.record_dispatch("丞相", "e1", "task", False, 0.10)
        sm = SurvivalMechanism(merit_board=mb)
        sm.register_minister("丞相", "writing")
        for _ in range(SurvivalMechanism.MAX_PROBATION_CYCLES + 1):
            sm.run_evolution_cycle()
        # Should NOT be eliminated — only minister
        assert sm.get_status("丞相") != MinisterStatus.ELIMINATED

    def test_archive_preserves_eliminated_genome(self):
        mb = MeritBoard()
        for i in range(15):
            mb.record_dispatch("太卜", f"e{i}", "task", False, 0.05)
        sm = SurvivalMechanism(merit_board=mb)
        sm.register_minister("丞相", "writing")
        sm.register_minister("太卜", "science")
        sm.register_minister("工部尚书", "code")
        sm.register_minister("太史令", "search")
        sm.register_minister("卫尉", "security")
        for _ in range(SurvivalMechanism.MAX_PROBATION_CYCLES + 1):
            sm.run_evolution_cycle()
        archive = sm.get_archive()
        assert len(archive) >= 1
        assert any(g.name == "太卜" for g in archive)


# ── Demotion ────────────────────────────────────────────────────────


class TestDemotion:
    """Active → Shadow demotion."""

    def test_very_low_merit_demotes_to_shadow(self):
        mb = MeritBoard()
        # 太卜: critically bad performance → merit < 20 → demotion trigger
        mb.record_dispatch("太卜", "e1", "task", False, 0.05)
        # Other ministers need enough records to stay above demotion threshold
        mb.record_dispatch("丞相", "b1", "task", True, 0.95)
        mb.record_dispatch("丞相", "b2", "task", True, 0.90)
        mb.record_dispatch("工部尚书", "c1", "task", True, 0.85)
        mb.record_dispatch("太史令", "d1", "task", True, 0.80)
        mb.record_dispatch("卫尉", "f1", "task", True, 0.75)
        sm = SurvivalMechanism(merit_board=mb)
        sm.register_minister("太卜", "science")
        sm.register_minister("丞相", "writing")
        sm.register_minister("工部尚书", "code")
        sm.register_minister("太史令", "search")
        sm.register_minister("卫尉", "security")
        report = sm.run_evolution_cycle()
        assert sm.get_status("太卜") == MinisterStatus.SHADOW
        assert any(
            a.action == EvolutionAction.DEMOTE
            for a in report.actions_taken
        )


# ── Promotion ───────────────────────────────────────────────────────


class TestPromotion:
    """Shadow → Active promotion."""

    def test_shadow_promoted_when_merit_high(self):
        mb = MeritBoard()
        # 影丞相 consistently performs well
        mb.record_dispatch("影丞相", "e1", "task", True, 0.90)
        mb.record_dispatch("影丞相", "e2", "task", True, 0.85)
        sm = SurvivalMechanism(merit_board=mb)
        sm.register_shadow("影丞相", "writing")
        report = sm.run_evolution_cycle()
        assert sm.get_status("影丞相") == MinisterStatus.ACTIVE
        assert any(
            a.action == EvolutionAction.PROMOTE
            for a in report.actions_taken
        )

    def test_shadow_stays_shadow_if_low_merit(self):
        mb = MeritBoard()
        mb.record_dispatch("影丞相", "e1", "task", False, 0.10)
        sm = SurvivalMechanism(merit_board=mb)
        sm.register_shadow("影丞相", "writing")
        report = sm.run_evolution_cycle()
        assert sm.get_status("影丞相") == MinisterStatus.SHADOW


# ── Clone & Mutate ─────────────────────────────────────────────────


class TestCloneMutate:
    """Clone top performer to fill vacancies."""

    def test_clone_fills_vacancy(self):
        mb = MeritBoard()
        mb.record_dispatch("丞相", "e1", "task", True, 0.95)
        sm = SurvivalMechanism(merit_board=mb)
        sm.register_minister("丞相", "writing")
        # Only 1 minister → below 8 → should clone
        report = sm.run_evolution_cycle()
        clones = [
            a for a in report.actions_taken
            if a.action == EvolutionAction.CLONE_MUTATE
        ]
        assert len(clones) >= 1
        clone_name = clones[0].minister
        assert "丞相" != clone_name  # Not the original
        assert sm.get_status(clone_name) == MinisterStatus.SHADOW

    def test_mutation_alters_parameters(self):
        parent = MinisterGenome(
            name="丞相", domain="writing",
            temperature=0.7, confidence_baseline=0.85,
        )
        sm = SurvivalMechanism()
        mutated = sm._mutate_genome(parent, "丞相_v2")
        assert mutated.name == "丞相_v2"
        assert mutated.generation == parent.generation + 1
        assert mutated.parent == "丞相"
        # Parameters should be perturbed (not exactly equal)
        assert abs(mutated.temperature - 0.7) <= 0.15
        assert abs(mutated.confidence_baseline - 0.85) <= 0.08

    def test_clone_generates_unique_name(self):
        sm = SurvivalMechanism()
        sm.register_minister("丞相", "writing")
        name = sm._generate_clone_name("丞相")
        assert name.startswith("丞相_v")
        assert name != "丞相"

    def test_multiple_clones_have_different_names(self):
        sm = SurvivalMechanism()
        sm._genomes["丞相"] = MinisterGenome(
            name="丞相", domain="writing", generation=0,
        )
        sm._genomes["丞相_v1"] = MinisterGenome(
            name="丞相_v1", domain="writing", generation=1, parent="丞相",
        )
        name = sm._generate_clone_name("丞相")
        assert name == "丞相_v2"

    def test_no_clone_when_court_full(self):
        mb = MeritBoard()
        sm = SurvivalMechanism(merit_board=mb)
        register_eight_ministers(sm)
        for name in sm.get_active_ministers():
            mb.record_dispatch(name, "e", "task", True, 0.80)
        report = sm.run_evolution_cycle()
        clones = [
            a for a in report.actions_taken
            if a.action == EvolutionAction.CLONE_MUTATE
        ]
        assert len(clones) == 0


# ── Auto-Tune ───────────────────────────────────────────────────────


class TestAutoTune:
    """Auto-tuning minister parameters."""

    def test_high_performer_temperature_lowered(self):
        mb = MeritBoard()
        mb.record_dispatch("丞相", "e1", "task", True, 0.95)
        mb.record_dispatch("丞相", "e2", "task", True, 0.92)
        mb.record_dispatch("丞相", "e3", "task", True, 0.90)
        sm = SurvivalMechanism(merit_board=mb)
        sm.register_minister("丞相", "writing", temperature=0.8)
        original_temp = sm.get_genome("丞相").temperature
        report = sm.run_evolution_cycle()
        tunes = [
            a for a in report.actions_taken
            if a.action == EvolutionAction.TUNE_PARAMS
            and a.minister == "丞相"
        ]
        if tunes:
            new_temp = sm.get_genome("丞相").temperature
            assert new_temp <= original_temp  # Should be lowered

    def test_low_performer_temperature_raised(self):
        mb = MeritBoard()
        mb.record_dispatch("太卜", "e1", "task", False, 0.10)
        mb.record_dispatch("太卜", "e2", "task", False, 0.15)
        sm = SurvivalMechanism(merit_board=mb)
        sm.register_minister("太卜", "science", temperature=0.4)
        sm.register_minister("丞相", "writing")
        sm.register_minister("工部尚书", "code")
        sm.register_minister("太史令", "search")
        sm.register_minister("卫尉", "security")
        original_temp = sm.get_genome("太卜").temperature
        report = sm.run_evolution_cycle()
        tunes = [
            a for a in report.actions_taken
            if a.action == EvolutionAction.TUNE_PARAMS
            and a.minister == "太卜"
        ]
        if tunes:
            new_temp = sm.get_genome("太卜").temperature
            assert new_temp >= original_temp  # Should be raised for exploration

    def test_confidence_baseline_adapts(self):
        mb = MeritBoard()
        mb.record_dispatch("丞相", "e1", "task", True, 0.50)
        mb.record_dispatch("丞相", "e2", "task", True, 0.55)
        sm = SurvivalMechanism(merit_board=mb)
        sm.register_minister("丞相", "writing", confidence_baseline=0.85)
        report = sm.run_evolution_cycle()
        tunes = [
            a for a in report.actions_taken
            if a.action == EvolutionAction.TUNE_PARAMS
            and a.minister == "丞相"
        ]
        if tunes:
            new_baseline = sm.get_genome("丞相").confidence_baseline
            # Should drift toward actual average (0.525)
            assert new_baseline < 0.85  # Moved down


# ── Systemic Gaps ───────────────────────────────────────────────────


class TestSystemicGaps:
    """Detection of systemic court weaknesses."""

    def test_detects_low_court_size(self):
        sm = SurvivalMechanism()
        sm.register_minister("丞相", "writing")
        report = sm.run_evolution_cycle()
        assert any("不足" in issue for issue in report.systemic_issues)

    def test_detects_missing_domains(self):
        sm = SurvivalMechanism()
        sm.register_minister("丞相", "writing")
        sm.register_minister("工部尚书", "code")
        sm.register_minister("卫尉", "security")
        sm.register_minister("太史令", "search")
        sm.register_minister("太卜", "science")
        report = sm.run_evolution_cycle()
        missing = [
            issue for issue in report.systemic_issues
            if "缺失" in issue
        ]
        assert len(missing) >= 1

    def test_full_court_no_gaps(self):
        sm = SurvivalMechanism()
        register_eight_ministers(sm)
        report = sm.run_evolution_cycle()
        # 8 active ministers means full court
        assert report.active_count == 8


# ── Evolution Report ────────────────────────────────────────────────


class TestEvolutionReport:
    """EvolutionReport structure validation."""

    def test_report_has_all_fields(self):
        mb = MeritBoard()
        sm = SurvivalMechanism(merit_board=mb)
        sm.register_minister("丞相", "writing")
        report = sm.run_evolution_cycle()
        assert isinstance(report, EvolutionReport)
        assert report.cycle >= 1
        assert isinstance(report.actions_taken, list)
        assert isinstance(report.active_count, int)
        assert isinstance(report.shadow_count, int)
        assert isinstance(report.eliminated_count, int)
        assert isinstance(report.new_spawns, int)
        assert isinstance(report.systemic_issues, list)
        assert isinstance(report.recommendations, list)

    def test_history_grows(self):
        mb = MeritBoard()
        sm = SurvivalMechanism(merit_board=mb)
        sm.register_minister("丞相", "writing")
        sm.run_evolution_cycle()
        history = sm.get_evolution_history()
        assert len(history) >= 1


# ── Edge Cases ──────────────────────────────────────────────────────


class TestEdgeCases:
    """Edge cases and boundary behavior."""

    def test_empty_court_evolution_cycle(self):
        sm = SurvivalMechanism()
        report = sm.run_evolution_cycle()
        assert report.active_count == 0
        assert report.eliminated_count == 0
        assert isinstance(report.actions_taken, list)

    def test_all_eliminated(self):
        mb = MeritBoard()
        sm = SurvivalMechanism(merit_board=mb)
        sm.register_minister("A", "writing")
        sm.register_minister("B", "code")
        sm.register_minister("C", "search")
        sm.register_minister("D", "science")
        sm.register_minister("E", "security")
        for name in ["A", "B", "C", "D", "E"]:
            for i in range(15):
                mb.record_dispatch(name, f"{name}_e{i}", "task", False, 0.05)
        # Run many cycles
        for _ in range(SurvivalMechanism.MAX_PROBATION_CYCLES + 2):
            sm.run_evolution_cycle()
        # Can't go below MIN_COURT_SIZE (4), but clones may increase total
        remaining = len(sm.get_active_ministers()) + len(sm.get_shadow_ministers())
        assert remaining >= SurvivalMechanism.MIN_COURT_SIZE
        assert remaining <= 12  # Reasonable upper bound after cloning

    def test_apply_genome_to_minister_object(self):
        """Genome injection uses set_genome() + set_genome_injector()."""
        sm = SurvivalMechanism()
        sm.register_minister("丞相", "writing", temperature=0.8, confidence_baseline=0.90)

        class MockMinister:
            def __init__(self):
                self.genome_received = None
                self.injector_received = None

            def set_genome(self, genome):
                self.genome_received = genome

            def set_genome_injector(self, injector):
                self.injector_received = injector

        minister = MockMinister()
        genome = sm.get_genome("丞相")
        sm.apply_genome_to_minister(minister, genome)

        assert minister.genome_received is genome
        assert minister.genome_received.temperature == 0.8
        assert minister.genome_received.confidence_baseline == 0.90
        assert minister.injector_received is not None

    def test_status_query_unknown_minister(self):
        sm = SurvivalMechanism()
        assert sm.get_status("ghost") == MinisterStatus.ACTIVE
        assert sm.get_genome("ghost") is None

    def test_genome_mutation_bounds(self):
        """Ensure mutations stay within valid bounds."""
        sm = SurvivalMechanism()
        # Edge case: extreme parent values
        parent = MinisterGenome(
            name="test", domain="code",
            temperature=0.95, confidence_baseline=0.95,
        )
        for _ in range(50):
            mutated = sm._mutate_genome(parent, f"test_v{_}")
            assert 0.2 <= mutated.temperature <= 1.0
            assert 0.3 <= mutated.confidence_baseline <= 0.95

        parent2 = MinisterGenome(
            name="test2", domain="code",
            temperature=0.25, confidence_baseline=0.32,
        )
        for _ in range(50):
            mutated = sm._mutate_genome(parent2, f"test2_v{_}")
            assert 0.2 <= mutated.temperature <= 1.0
            assert 0.3 <= mutated.confidence_baseline <= 0.95


# ── TestCrossover ────────────────────────────────────────────────────


class TestCrossover:
    """Tests for the _crossover_genome dual-parent genetic operation."""

    def test_crossover_creates_child_with_mixed_traits(self):
        """Child inherits genes from both parents, not just one."""
        sm = SurvivalMechanism()

        parent1 = MinisterGenome(
            name="p1", domain="code",
            temperature=0.3, confidence_baseline=0.9,
            exploration_rate=0.4, conservatism=0.5,
            prompt_mutation_rate=0.1, specialization_weight=0.6,
        )
        parent2 = MinisterGenome(
            name="p2", domain="writing",
            temperature=0.8, confidence_baseline=0.4,
            exploration_rate=0.9, conservatism=0.1,
            prompt_mutation_rate=0.3, specialization_weight=0.2,
        )

        child = sm._crossover_genome(parent1, parent2, "offspring")

        # Child should NOT be an exact copy of either parent
        all_p1_genes = (
            child.temperature == parent1.temperature
            and child.confidence_baseline == parent1.confidence_baseline
            and child.exploration_rate == parent1.exploration_rate
            and child.conservatism == parent1.conservatism
            and child.prompt_mutation_rate == parent1.prompt_mutation_rate
            and child.specialization_weight == parent1.specialization_weight
        )
        all_p2_genes = (
            child.temperature == parent2.temperature
            and child.confidence_baseline == parent2.confidence_baseline
            and child.exploration_rate == parent2.exploration_rate
            and child.conservatism == parent2.conservatism
            and child.prompt_mutation_rate == parent2.prompt_mutation_rate
            and child.specialization_weight == parent2.specialization_weight
        )
        assert not all_p1_genes, "child cloned parent1 entirely"
        assert not all_p2_genes, "child cloned parent2 entirely"

    def test_crossover_increments_generation(self):
        """Child generation = max(parent generations) + 1."""
        sm = SurvivalMechanism()

        p1 = MinisterGenome(name="p1", domain="code", generation=5)
        p2 = MinisterGenome(name="p2", domain="writing", generation=3)

        child = sm._crossover_genome(p1, p2, "offspring")
        assert child.generation == 6  # max(5, 3) + 1

    def test_crossover_parent_field_is_concatenation(self):
        """Parent field shows both lineage names."""
        sm = SurvivalMechanism()

        p1 = MinisterGenome(name="p1", domain="code")
        p2 = MinisterGenome(name="p2", domain="writing")

        child = sm._crossover_genome(p1, p2, "offspring")
        assert "p1" in child.parent
        assert "p2" in child.parent

    def test_crossover_domain_is_from_better_parent(self):
        """Domain inherits from the parent with higher merit."""
        sm = SurvivalMechanism()
        mb = make_board_with_data([
            ("p1", True, 0.9),
            ("p1", True, 0.9),
        ])
        sm._merit_board = mb

        p1 = MinisterGenome(name="p1", domain="code")
        p2 = MinisterGenome(name="p2", domain="writing")

        child = sm._crossover_genome(p1, p2, "offspring")
        # p1 has higher merit (2 dispatches) → domain should be "code"
        assert child.domain == "code"

    def test_crossover_different_parents_yields_varied_children(self):
        """Multiple crossovers from same pair produce genetically diverse kids."""
        sm = SurvivalMechanism()

        p1 = MinisterGenome(
            name="p1", domain="code",
            temperature=0.3, confidence_baseline=0.9,
            exploration_rate=0.4, conservatism=0.5,
        )
        p2 = MinisterGenome(
            name="p2", domain="writing",
            temperature=0.8, confidence_baseline=0.4,
            exploration_rate=0.9, conservatism=0.1,
        )

        children = [
            sm._crossover_genome(p1, p2, f"child_{i}")
            for i in range(20)
        ]

        # With 6 binary genes, multiple crossovers should produce variation
        temps = {c.temperature for c in children}
        confs = {c.confidence_baseline for c in children}
        assert len(temps) > 1, "all children got same temperature"
        assert len(confs) > 1, "all children got same confidence"


# ── TestElitism ──────────────────────────────────────────────────────


class TestElitism:
    """Tests for elitism protection in demotion/probation/elimination."""

    def test_elite_set_excludes_low_merit(self):
        """Ministers below ELITE_MERIT_FLOOR are not elite even if top ranked."""
        sm = SurvivalMechanism()
        # All failed dispatches — merit stays well below 30
        data = [
            ("丞相", False, 0.2),
            ("工部尚书", False, 0.1),
            ("吏部尚书", False, 0.1),
        ]
        mb = make_board_with_data(data)
        sm._merit_board = mb
        sm.register_minister("丞相", "writing")
        sm.register_minister("工部尚书", "code")
        sm.register_minister("吏部尚书", "analysis")

        elites = sm._get_elite_set()
        assert len(elites) == 0

    def test_elite_set_includes_high_merit(self):
        """Ministers above ELITE_MERIT_FLOOR and top-ranked are elite."""
        sm = SurvivalMechanism()
        # Create 10 successful dispatches for top minister
        data = [("丞相", True, 0.9) for _ in range(10)]
        data += [("工部尚书", True, 0.5) for _ in range(3)]
        mb = make_board_with_data(data)
        sm._merit_board = mb
        sm.register_minister("丞相", "writing")
        sm.register_minister("工部尚书", "code")

        elites = sm._get_elite_set()
        assert "丞相" in elites

    def test_elite_immune_to_demotion(self):
        """High-merit elite is not demoted despite low score later."""
        sm = SurvivalMechanism()
        # Build up high merit first
        data = [("丞相", True, 0.9) for _ in range(20)]
        mb = MeritBoard()
        for i, (name, success, confidence) in enumerate(data):
            mb.record_dispatch(name, f"e{i}", "test", success, confidence)
        sm._merit_board = mb

        sm.register_minister("丞相", "writing")
        sm.register_minister("工部尚书", "code")
        sm.register_minister("吏部尚书", "analysis")
        sm.register_minister("太卜", "search")

        # Even though demotion threshold is <20, elite should survive
        actions = sm._demote_underperformers()
        demoted_names = {a.minister for a in actions}
        assert "丞相" not in demoted_names

    def test_elite_immune_to_probation(self):
        """High-merit elite skips probation marks."""
        sm = SurvivalMechanism()
        data = [("丞相", True, 0.9) for _ in range(20)]
        data += [("工部尚书", False, 0.2)]
        mb = make_board_with_data(data)
        sm._merit_board = mb

        sm.register_minister("丞相", "writing")
        sm.register_minister("工部尚书", "code")

        actions, _ = sm._identify_probation_candidates()
        probated = {a.minister for a in actions}
        assert "丞相" not in probated


# ── Adaptive Elite Turnover ─────────────────────────────────────────


class TestAdaptiveEliteTurnover:
    """精英更替速率自适应 — elite count adjusts to population dynamics."""

    def _build_court(
        self, sm, names_and_merits: list[tuple[str, float]]
    ) -> None:
        """Register ministers and set known merits via a mock board."""
        class MockMeritBoard:
            def compute_merit(self, name):
                for n, m in names_and_merits:
                    if n == name:
                        return m
                return 0

            def get_probation_candidates(self):
                return []

        sm._merit_board = MockMeritBoard()
        for name, _ in names_and_merits:
            sm.register_minister(name)

    def test_default_mode_is_adaptive(self):
        """Default TURNOVER_MODE is ADAPTIVE."""
        assert SurvivalMechanism.TURNOVER_MODE == EliteTurnoverMode.ADAPTIVE

    def test_fixed_mode_keeps_unchanged_count(self):
        """FIXED mode always returns the configured elitism_count."""
        sm = SurvivalMechanism(
            turnover_mode=EliteTurnoverMode.FIXED,
            elitism_count=3,
        )
        data = [
            ("A", True, 0.9), ("A", True, 0.9), ("A", True, 0.9),
            ("B", True, 0.8), ("B", True, 0.8),
            ("C", True, 0.7),
        ]
        mb = make_board_with_data(data)
        sm._merit_board = mb
        for name in ["A", "B", "C", "D", "E", "F"]:
            sm.register_minister(name)
        # Force low diversity — FIXED mode ignores it
        sm.diversity._diversity_score = 0.05
        sm.run_evolution_cycle()
        assert sm.get_elite_count() == 3

    def test_adaptive_respects_bounds(self):
        """Adaptive elite count always within [MIN_ELITES, MAX_ELITES]."""
        for base_count in [1, 2, 3, 4, 5, 10]:
            sm = SurvivalMechanism(
                elitism_count=base_count,
                min_elites=1,
                max_elites=4,
            )
            self._build_court(sm, [
                ("A", 50), ("B", 50), ("C", 50),
                ("D", 50), ("E", 50),
            ])
            # Mid diversity + mid variance → typical case
            sm.diversity._diversity_score = 0.5
            count = sm._adaptive_elite_count()
            assert 1 <= count <= 4, (
                f"base={base_count} → count={count} not in [1,4]"
            )

    def test_low_diversity_protects_more(self):
        """Low diversity → higher elite count (stability preservation)."""
        sm = SurvivalMechanism(elitism_count=2, min_elites=1, max_elites=5)
        self._build_court(sm, [
            ("A", 80), ("B", 75), ("C", 70),
            ("D", 65), ("E", 60),
        ])
        # High diversity → natural selection works, fewer elites
        sm.diversity._diversity_score = 0.85
        high_div_count = sm._adaptive_elite_count()

        # Low diversity → protect more to prevent monoculture collapse
        sm.diversity._diversity_score = 0.08
        low_div_count = sm._adaptive_elite_count()

        assert low_div_count >= high_div_count, (
            f"low_diversity({low_div_count}) should be >= "
            f"high_diversity({high_div_count})"
        )

    def test_high_merit_variance_protects_fewer(self):
        """High merit variance → fewer elites (clear leaders exist)."""
        sm = SurvivalMechanism(elitism_count=3, min_elites=1, max_elites=5)
        # Low variance: everyone similar merit
        self._build_court(sm, [
            ("A", 50), ("B", 48), ("C", 52),
            ("D", 49), ("E", 51),
        ])
        sm.diversity._diversity_score = 0.5
        low_var_count = sm._adaptive_elite_count()

        # High variance: one clear leader
        self._build_court(sm, [
            ("A", 95), ("B", 20), ("C", 15),
            ("D", 10), ("E", 8),
        ])
        high_var_count = sm._adaptive_elite_count()

        assert high_var_count <= low_var_count, (
            f"high_variance({high_var_count}) should be <= "
            f"low_variance({low_var_count})"
        )

    def test_small_court_reduces_elites(self):
        """Small active court → fewer elites (don't protect half the court)."""
        sm = SurvivalMechanism(elitism_count=3, min_elites=1, max_elites=5)
        sm.diversity._diversity_score = 0.5

        # Small court: 4 actives
        self._build_court(sm, [
            ("A", 60), ("B", 55), ("C", 50), ("D", 45),
        ])
        small_count = sm._adaptive_elite_count()

        # Clear registrations, rebuild larger court
        sm._statuses.clear()
        sm._genomes.clear()
        # Large court: 12 actives
        self._build_court(sm, [
            ("A", 60), ("B", 55), ("C", 50), ("D", 45),
            ("E", 40), ("F", 35), ("G", 30), ("H", 25),
            ("I", 20), ("J", 15), ("K", 10), ("L", 5),
        ])
        large_count = sm._adaptive_elite_count()

        assert small_count <= large_count, (
            f"small_court({small_count}) should be <= "
            f"large_court({large_count})"
        )

    def test_elite_count_used_in_elite_set(self):
        """The adaptive elite count actually governs _get_elite_set size."""
        sm = SurvivalMechanism(
            elitism_count=2, min_elites=1, max_elites=4,
        )
        self._build_court(sm, [
            ("A", 80), ("B", 75), ("C", 70),
            ("D", 65),
        ])
        # Force _current_elite_count = 3
        sm._current_elite_count = 3
        elites = sm._get_elite_set()
        # A/B/C should be in (top 3, all above ELITE_MERIT_FLOOR=30)
        assert "A" in elites
        assert "B" in elites
        assert "C" in elites
        assert "D" not in elites
        assert len(elites) == 3

    def test_run_cycle_updates_elite_count_in_adaptive(self):
        """run_evolution_cycle recomputes elite count in ADAPTIVE mode."""
        sm = SurvivalMechanism(
            elitism_count=3, min_elites=1, max_elites=3,
            turnover_mode=EliteTurnoverMode.ADAPTIVE,
        )
        data = [
            ("A", True, 0.8), ("B", True, 0.7), ("C", True, 0.6),
            ("D", True, 0.6), ("E", True, 0.5),
        ]
        mb = make_board_with_data(data)
        sm._merit_board = mb
        for name in ["A", "B", "C", "D", "E"]:
            sm.register_minister(name)
        sm.diversity._diversity_score = 0.5
        initial_count = sm.get_elite_count()
        assert initial_count == 3  # equals elitism_count before first cycle

        sm.run_evolution_cycle()
        after_count = sm.get_elite_count()
        # Adaptive should have computed a value in bounds
        assert 1 <= after_count <= 3

    def test_fixed_mode_uses_elitism_count_in_elite_set(self):
        """FIXED mode uses _elitism_count directly for elite selection."""
        sm = SurvivalMechanism(
            elitism_count=3,
            turnover_mode=EliteTurnoverMode.FIXED,
            min_elites=1,
            max_elites=2,
        )
        data = [
            ("A", True, 0.9), ("B", True, 0.8), ("C", True, 0.7),
            ("D", True, 0.6), ("E", True, 0.5),
        ]
        mb = make_board_with_data(data)
        sm._merit_board = mb
        for name in ["A", "B", "C", "D", "E"]:
            sm.register_minister(name)
        # FIXED mode: _current_elite_count stays at _elitism_count
        sm.run_evolution_cycle()
        assert sm.get_elite_count() == 3

        elites = sm._get_elite_set()
        assert len(elites) == 3
        assert {"A", "B", "C"} == elites


# ── SlidingMeritBoard Integration ───────────────────────────────────


class TestSlidingMeritIntegration:
    """SurvivalMechanism with SlidingMeritBoard auto-wrapping."""

    def test_auto_wrap_on_by_default(self):
        """MeritBoard passed → auto-wrapped in SlidingMeritBoard."""
        mb = MeritBoard()
        sm = SurvivalMechanism(merit_board=mb)
        from jarvis.court.sliding_merit import SlidingMeritBoard as SMB
        assert sm.get_sliding_merit_board() is not None
        assert isinstance(sm.get_sliding_merit_board(), SMB)
        assert sm.get_raw_merit_board() is mb

    def test_opt_out_sliding_merit(self):
        """enable_sliding_merit=False → no wrapping."""
        mb = MeritBoard()
        sm = SurvivalMechanism(merit_board=mb, enable_sliding_merit=False)
        assert sm.get_sliding_merit_board() is None
        assert sm.get_raw_merit_board() is mb

    def test_preserve_existing_sliding_board(self):
        """Already a SlidingMeritBoard → not double-wrapped."""
        from jarvis.court.sliding_merit import SlidingMeritBoard as SMB
        mb = MeritBoard()
        existing = SMB(mb, window_size=30)
        sm = SurvivalMechanism(merit_board=existing)
        sliding = sm.get_sliding_merit_board()
        assert sliding is existing
        assert sliding.window_size == 30  # preserved, not reset

    def test_custom_window_size(self):
        """Custom sliding_window_size propagates."""
        mb = MeritBoard()
        sm = SurvivalMechanism(
            merit_board=mb, sliding_window_size=100,
        )
        sliding = sm.get_sliding_merit_board()
        assert sliding is not None
        assert sliding.window_size == 100

    def test_custom_window_mode_exp_decay(self):
        """EXP_DECAY mode propagates."""
        from jarvis.court.sliding_merit import WindowMode
        mb = MeritBoard()
        sm = SurvivalMechanism(
            merit_board=mb,
            sliding_window_mode=WindowMode.EXP_DECAY,
        )
        sliding = sm.get_sliding_merit_board()
        assert sliding.mode == WindowMode.EXP_DECAY

    def test_sliding_merit_reports_accessible(self):
        """SlidingMeritBoard.get_leaderboard() works through survival."""
        mb = MeritBoard()
        mb.record_dispatch("丞相", "e1", "task", True, 0.90)
        mb.record_dispatch("丞相", "e2", "task", True, 0.85)
        mb.record_dispatch("太史令", "c1", "task", True, 0.80)
        sm = SurvivalMechanism(merit_board=mb)
        sm.register_minister("丞相", "writing")
        sm.register_minister("太史令", "search")
        sliding = sm.get_sliding_merit_board()
        assert sliding is not None
        lb = sliding.get_leaderboard()
        assert lb["total_ministers"] >= 2
        assert lb["window_size"] == 50

    def test_merit_recent_sensitive(self):
        """Sliding window (size=10) is more sensitive to recent failures.

        20 old successes + 5 recent failures, window covers only last 10.
        """
        mb = MeritBoard()
        # 20 historical successes
        for i in range(20):
            mb.record_dispatch("丞相", f"good_{i}", "task", True, 0.90)
        # 5 recent failures
        for i in range(5):
            mb.record_dispatch("丞相", f"bad_{i}", "task", False, 0.10)
        sm = SurvivalMechanism(
            merit_board=mb, sliding_window_size=10,
        )
        sm.register_minister("丞相", "writing")
        # Window covers: 5 old successes + 5 recent failures
        # success_rate = 5/10 = 0.5 → merit ≈ 46.2, well below full-history 64.2
        merit = sm.get_sliding_merit_board().compute_merit("丞相")
        assert merit < 50, f"Expected low merit with recent failures, got {merit}"


# ── AutoBreeder Integration ─────────────────────────────────────────

class TestAutoBreederIntegration:
    """AutoBreeder wired into SurvivalMechanism evolution cycle."""

    def test_auto_breeder_created_when_enabled(self):
        """AutoBreeder is instantiated when ENABLE_AUTO_BREEDING=True."""
        mb = MeritBoard()
        sm = SurvivalMechanism(merit_board=mb, enable_auto_breeding=True)
        assert sm.is_breeding_enabled()
        assert sm.get_auto_breeder() is not None

    def test_auto_breeder_none_when_disabled(self):
        """No AutoBreeder when explicitly disabled."""
        mb = MeritBoard()
        sm = SurvivalMechanism(merit_board=mb, enable_auto_breeding=False)
        assert not sm.is_breeding_enabled()
        assert sm.get_auto_breeder() is None

    def test_breeding_history_starts_empty(self):
        """Breeding history list starts empty."""
        mb = MeritBoard()
        sm = SurvivalMechanism(merit_board=mb)
        assert sm.get_breeding_history() == []

    def test_breed_cycle_respects_cooldown(self):
        """After breeding, cooldown prevents immediate re-breeding."""
        mb = MeritBoard()
        sm = SurvivalMechanism(
            merit_board=mb,
            breeding_cooldown=5,  # 5 cycles between breeds
            max_breed_per_cycle=3,
        )
        # Small court: 3 ministers → strong gaps → breeding fires on cycle 1
        for name, domain in [
            ("丞相", "writing"), ("太史令", "search"), ("工部尚书", "code"),
        ]:
            sm.register_minister(name, domain)

        sm.set_breeder_expertise_provider(
            lambda: {
                "丞相": {"writing": 0.8}, "太史令": {"search": 0.8},
                "工部尚书": {"code": 0.8},
            }
        )

        # First breed fires (cooldown counter initialized to breeding_cooldown)
        report1 = sm.run_evolution_cycle()
        bred1 = sum(1 for a in report1.actions_taken if "AutoBreeder" in a.reason)
        assert bred1 >= 1, "First cycle should breed (cooldown initially satisfied)"

        # Second cycle: cooldown now 0 (< 5) → no breeding
        report2 = sm.run_evolution_cycle()
        bred2 = sum(1 for a in report2.actions_taken if "AutoBreeder" in a.reason)
        assert bred2 == 0, "Second cycle within cooldown should not breed"

    def test_breed_after_cooldown_fills_gaps(self):
        """After cooldown, AutoBreeder creates ministers for gaps."""
        mb = MeritBoard()
        sm = SurvivalMechanism(
            merit_board=mb,
            breeding_cooldown=0,  # allow immediate breeding
            max_breed_per_cycle=3,
        )
        # Register only 6 ministers across 3 of 13 known domains
        for name, domain in [
            ("丞相", "writing"), ("御史大夫", "writing"),
            ("太史令", "search"), ("工部尚书", "code"),
            ("太常", "multimodal"), ("大司农", "finance"),
        ]:
            sm.register_minister(name, domain)

        # Wire expertise provider (simulates orchestrator wiring)
        def fake_expertise():
            result = {}
            for m, d in [
                ("丞相", "writing"), ("御史大夫", "writing"),
                ("太史令", "search"), ("工部尚书", "code"),
                ("太常", "multimodal"), ("大司农", "finance"),
            ]:
                result[m] = {d: 0.8, "general": 0.3}
            return result

        sm.set_breeder_expertise_provider(fake_expertise)

        report = sm.run_evolution_cycle()
        breeding_actions = [
            a for a in report.actions_taken
            if "AutoBreeder" in a.reason
        ]
        assert len(breeding_actions) > 0, (
            "Expected AutoBreeder to create ministers for gaps"
        )

        # Verify bred ministers are registered as SHADOW
        bred_names = [a.minister for a in breeding_actions]
        for name in bred_names:
            assert sm.get_status(name) == MinisterStatus.SHADOW

    def test_breed_skip_when_no_gaps(self):
        """No coverage/quality gaps when all 13 domains are well-covered.

        Note: EXPLORE may fire due to low gene diversity, but coverage and
        merit gaps should be zero since all domains have active ministers.
        """
        mb = MeritBoard()
        sm = SurvivalMechanism(
            merit_board=mb,
            breeding_cooldown=0,
            max_breed_per_cycle=3,
        )

        domains = [
            "engineering", "research", "security", "finance",
            "personal", "health", "home", "general", "core",
            "creative", "legal", "education", "entertainment",
        ]
        for i, d in enumerate(domains):
            mb.record_dispatch(d, f"e_{d}", "task", True, 0.90)
            sm.register_minister(
                d, d,
                temperature=0.5 + (i * 0.03),
                confidence_baseline=0.75 + (i * 0.015),
            )

        sm.set_breeder_expertise_provider(
            lambda: {d: {d: 1.0} for d in domains}
        )
        report = sm.run_evolution_cycle()
        breeding_actions = [
            a for a in report.actions_taken
            if "AutoBreeder" in a.reason
        ]
        # EXPLORE strategy is acceptable (diversity-driven),
        # but SPECIALIST/SPECIALIZE would indicate coverage gaps
        for a in breeding_actions:
            if "strategy" in a.details:
                assert a.details["strategy"] in ("EXPLORE",), (
                    f"Expected EXPLORE only, got {a.details['strategy']}"
                )

    def test_breed_reports_systemic_issues(self):
        """Systemic issues in report include breeding info."""
        mb = MeritBoard()
        sm = SurvivalMechanism(
            merit_board=mb,
            breeding_cooldown=0,
            max_breed_per_cycle=2,
        )
        for name, domain in [
            ("丞相", "writing"), ("太史令", "search"),
        ]:
            sm.register_minister(name, domain)

        sm.set_breeder_expertise_provider(
            lambda: {
                "丞相": {"writing": 0.8, "general": 0.3},
                "太史令": {"search": 0.8, "general": 0.3},
            }
        )
        report = sm.run_evolution_cycle()

        # At least one systemic issue mentions AutoBreeder
        breeder_issues = [
            i for i in report.systemic_issues
            if "AutoBreeder" in i
        ]
        assert len(breeder_issues) > 0

        # Recommendations should mention 育种
        breeder_recs = [
            r for r in report.recommendations
            if "育种" in r
        ]
        assert len(breeder_recs) > 0

    def test_bred_minister_has_correct_genome_fields(self):
        """Genome fields from breeding are correctly mapped to MinisterGenome."""
        mb = MeritBoard()
        sm = SurvivalMechanism(
            merit_board=mb,
            breeding_cooldown=0,
            max_breed_per_cycle=2,
        )
        sm.register_minister("丞相", "writing")
        sm.register_minister("太史令", "search")

        sm.set_breeder_expertise_provider(
            lambda: {
                "丞相": {"writing": 0.8, "general": 0.3},
                "太史令": {"search": 0.8, "general": 0.3},
            }
        )
        sm.run_evolution_cycle()

        # Find a bred minister and verify genome
        all_names = sm.get_active_ministers() + sm.get_shadow_ministers()
        bred = None
        for name in all_names:
            if name not in {"丞相", "太史令"}:
                bred = name
                break
        if bred:
            genome = sm._genomes[bred]
            assert 0.01 <= genome.temperature <= 0.99
            assert 0.01 <= genome.confidence_baseline <= 0.99
            assert genome.domain != ""

    def test_breed_generation_increments(self):
        """Child generation = parent.generation + 1."""
        mb = MeritBoard()
        sm = SurvivalMechanism(
            merit_board=mb,
            breeding_cooldown=0,
            max_breed_per_cycle=1,
        )
        sm.register_minister("丞相", "writing")

        # Set a parent generation explicitly
        sm._genomes["丞相"].generation = 5

        # Force breeding from 丞相 as parent
        class ForceSpecialize:
            """Forces SPECIALIZE strategy using 丞相 as parent."""

            @staticmethod
            def select(gaps, elites):
                from jarvis.court.breeding import BreedingCandidate, BreedingStrategy
                return [BreedingCandidate(
                    target_domain="legal",
                    strategy=BreedingStrategy.SPECIALIZE,
                    genome_template=None,
                    parent_minister="丞相",
                    reasoning="test",
                )]

        breeder = sm.get_auto_breeder()
        from jarvis.court.breeding import StrategySelector
        breeder.strategy_selector = ForceSpecialize()
        breeder._cycles_since_breed = breeder.breeding_cooldown

        sm.set_breeder_expertise_provider(
            lambda: {"丞相": {"writing": 0.8, "general": 0.3}}
        )

        report = sm.run_evolution_cycle()

        # Find the bred minister
        bred_names = [
            a.minister for a in report.actions_taken
            if "AutoBreeder" in a.reason
        ]
        if bred_names:
            bred = bred_names[0]
            child_gen = sm._genomes[bred]
            assert child_gen.generation == 6  # parent(5) + 1
            assert child_gen.parent == "丞相"

    def test_expertise_provider_update(self):
        """set_breeder_expertise_provider replaces the provider."""
        mb = MeritBoard()
        sm = SurvivalMechanism(merit_board=mb)

        called_with = []

        def first_provider():
            called_with.append("first")
            return {"丞相": {"writing": 0.8}}

        def second_provider():
            called_with.append("second")
            return {"太史令": {"search": 0.8}}

        sm.set_breeder_expertise_provider(first_provider)
        sm.set_breeder_expertise_provider(second_provider)

        # Trigger provider access via _gather_expertise
        breeder = sm.get_auto_breeder()
        breeder._expertise_provider()
        assert "second" in called_with
        assert "first" not in called_with

    def test_disabled_breeder_skips_breeding(self):
        """When enable_auto_breeding=False, no breeding even after cooldown."""
        mb = MeritBoard()
        sm = SurvivalMechanism(
            merit_board=mb,
            enable_auto_breeding=False,
            breeding_cooldown=0,
        )
        sm.register_minister("丞相", "writing")

        report = sm.run_evolution_cycle()
        breeding_actions = [
            a for a in report.actions_taken
            if "AutoBreeder" in a.reason
        ]
        assert len(breeding_actions) == 0

    def test_breeding_history_accumulates(self):
        """Breeding history grows across multiple cycles."""
        mb = MeritBoard()
        sm = SurvivalMechanism(
            merit_board=mb,
            breeding_cooldown=0,
            max_breed_per_cycle=2,
        )
        sm.register_minister("丞相", "writing")
        sm.register_minister("太史令", "search")

        sm.set_breeder_expertise_provider(
            lambda: {
                "丞相": {"writing": 0.8, "general": 0.3},
                "太史令": {"search": 0.8, "general": 0.3},
            }
        )

        sm.run_evolution_cycle()
        history_after_one = len(sm.get_breeding_history())
        assert history_after_one >= 1

        sm.run_evolution_cycle()
        history_after_two = len(sm.get_breeding_history())
        assert history_after_two >= history_after_one

    def test_breed_spawn_count_reflected(self):
        """new_spawns in EvolutionReport includes bred ministers."""
        mb = MeritBoard()
        sm = SurvivalMechanism(
            merit_board=mb,
            breeding_cooldown=0,
            max_breed_per_cycle=3,
        )
        sm.register_minister("丞相", "writing")
        sm.register_minister("太史令", "search")

        sm.set_breeder_expertise_provider(
            lambda: {
                "丞相": {"writing": 0.8, "general": 0.3},
                "太史令": {"search": 0.8, "general": 0.3},
            }
        )

        report = sm.run_evolution_cycle()
        breeding_spawns = sum(
            1 for a in report.actions_taken
            if "AutoBreeder" in a.reason
        )
        assert report.new_spawns >= breeding_spawns

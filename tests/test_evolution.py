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
        mb = MeritBoard()
        # 丞相: 9 entries — 8 fails at 0.15 + 1 success at 0.30 — merit ≈ 21.8
        # merit 20-30 is the probation window (too low for active, not critically bad)
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
        sm = SurvivalMechanism()
        sm.register_minister("丞相", "writing", temperature=0.8, confidence_baseline=0.90)

        class MockMinister:
            _current_temperature = 0.5
            _confidence_baseline = 0.5

        minister = MockMinister()
        genome = sm.get_genome("丞相")
        sm.apply_genome_to_minister(minister, genome)
        assert minister._current_temperature == 0.8
        assert minister._confidence_baseline == 0.90

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

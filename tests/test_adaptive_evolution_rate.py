"""
Task-Adaptive Evolution Rate tests — 任务自适应进化率单元测试.

Covers:
    - TaskDifficulty enum and AdaptiveRateConfig defaults
    - TaskContext construction and set_task_context on SurvivalMechanism
    - _compute_adaptive_rates in ADAPTIVE vs FIXED mode
    - Rate adaptation across difficulty tiers (TRIVIAL→CRISIS)
    - Diversity blend signal integration
    - Effective rates used by _mutate_genome and SBX crossover
    - EvolutionRateMode enum behaviour
    - Edge cases: missing diversity, cold start, clamp ranges
"""

import pytest

from jarvis.court.evolution import (
    AdaptiveRateConfig,
    CrossoverMode,
    EliteTurnoverMode,
    EvolutionAction,
    EvolutionRateMode,
    MinisterGenome,
    MinisterStatus,
    SurvivalMechanism,
    TaskContext,
    TaskDifficulty,
)
from jarvis.court.merit_board import MeritBoard

# ── Shared helpers ──────────────────────────────────────────────────


def _make_board(data: list[tuple[str, bool, float]]) -> MeritBoard:
    mb = MeritBoard()
    for i, (name, success, confidence) in enumerate(data):
        mb.record_dispatch(name, f"e{i}", "test", success, confidence)
    return mb


def _register_n(sm: SurvivalMechanism, names: list[str]) -> None:
    for name in names:
        sm.register_minister(name)


# ── TaskDifficulty + AdaptiveRateConfig ─────────────────────────────


class TestTaskDifficulty:
    """TaskDifficulty enum exhaustiveness."""

    def test_all_difficulty_tiers_exist(self):
        assert TaskDifficulty.TRIVIAL is not None
        assert TaskDifficulty.EASY is not None
        assert TaskDifficulty.MODERATE is not None
        assert TaskDifficulty.HARD is not None
        assert TaskDifficulty.CRISIS is not None

    def test_difficulties_have_unique_values(self):
        vals = {d.value for d in TaskDifficulty}
        assert len(vals) == 5


class TestAdaptiveRateConfig:
    """AdaptiveRateConfig default mappings are sensible."""

    def test_default_config_covers_all_difficulties(self):
        cfg = AdaptiveRateConfig()
        for d in TaskDifficulty:
            assert d in cfg.mutation_scales
            assert d in cfg.crossover_etas

    def test_monotonic_mutation_scale(self):
        """Mutation scale must increase with difficulty."""
        cfg = AdaptiveRateConfig()
        ms = cfg.mutation_scales
        assert ms[TaskDifficulty.TRIVIAL] < ms[TaskDifficulty.EASY]
        assert ms[TaskDifficulty.EASY] < ms[TaskDifficulty.MODERATE]
        assert ms[TaskDifficulty.MODERATE] < ms[TaskDifficulty.HARD]
        assert ms[TaskDifficulty.HARD] < ms[TaskDifficulty.CRISIS]

    def test_monotonic_crossover_eta(self):
        """Crossover eta must decrease with difficulty (more exploration)."""
        cfg = AdaptiveRateConfig()
        ce = cfg.crossover_etas
        assert ce[TaskDifficulty.TRIVIAL] > ce[TaskDifficulty.EASY]
        assert ce[TaskDifficulty.EASY] > ce[TaskDifficulty.MODERATE]
        assert ce[TaskDifficulty.MODERATE] > ce[TaskDifficulty.HARD]
        assert ce[TaskDifficulty.HARD] > ce[TaskDifficulty.CRISIS]

    def test_custom_config(self):
        cfg = AdaptiveRateConfig(
            mutation_scales={TaskDifficulty.HARD: 5.0},
            crossover_etas={TaskDifficulty.HARD: 3.0},
            diversity_blend=0.5,
        )
        assert cfg.mutation_scales[TaskDifficulty.HARD] == 5.0
        assert cfg.crossover_etas[TaskDifficulty.HARD] == 3.0
        assert cfg.diversity_blend == 0.5

    def test_clamp_bounds(self):
        cfg = AdaptiveRateConfig(
            min_mutation_scale=0.1,
            max_mutation_scale=5.0,
            min_crossover_eta=2.5,
            max_crossover_eta=80.0,
        )
        assert cfg.min_mutation_scale == 0.1
        assert cfg.max_mutation_scale == 5.0
        assert cfg.min_crossover_eta == 2.5
        assert cfg.max_crossover_eta == 80.0


# ── EvolutionRateMode ───────────────────────────────────────────────


class TestEvolutionRateMode:
    """EvolutionRateMode enum."""

    def test_modes_exist(self):
        assert EvolutionRateMode.FIXED is not None
        assert EvolutionRateMode.ADAPTIVE is not None

    def test_modes_unique(self):
        assert EvolutionRateMode.FIXED.value != EvolutionRateMode.ADAPTIVE.value

    def test_default_rate_mode_is_adaptive(self):
        sm = SurvivalMechanism()
        assert sm._rate_mode == EvolutionRateMode.ADAPTIVE

    def test_can_construct_fixed_mode(self):
        sm = SurvivalMechanism(rate_mode=EvolutionRateMode.FIXED)
        assert sm._rate_mode == EvolutionRateMode.FIXED


# ── TaskContext + set_task_context ──────────────────────────────────


class TestTaskContext:
    """TaskContext integration with SurvivalMechanism."""

    def test_default_context_is_moderate(self):
        sm = SurvivalMechanism()
        assert sm._task_context.difficulty == TaskDifficulty.MODERATE
        assert sm._task_context.domain == ""
        assert sm._task_context.intent == ""

    def test_set_task_context_updates_fields(self):
        sm = SurvivalMechanism()
        ctx = TaskContext(
            difficulty=TaskDifficulty.HARD,
            domain="engineering",
            intent="重构支付模块",
        )
        sm.set_task_context(ctx)
        assert sm._task_context.difficulty == TaskDifficulty.HARD
        assert sm._task_context.domain == "engineering"
        assert sm._task_context.intent == "重构支付模块"

    def test_set_task_context_persists_across_update(self):
        sm = SurvivalMechanism()
        sm.set_task_context(TaskContext(difficulty=TaskDifficulty.HARD))
        sm.set_task_context(TaskContext(difficulty=TaskDifficulty.EASY))
        assert sm._task_context.difficulty == TaskDifficulty.EASY


# ── _compute_adaptive_rates ────────────────────────────────────────


class TestComputeAdaptiveRates:
    """_compute_adaptive_rates in ADAPTIVE vs FIXED modes."""

    def test_fixed_mode_does_not_alter_effective_rates(self):
        sm = SurvivalMechanism(rate_mode=EvolutionRateMode.FIXED)
        sm.set_task_context(TaskContext(difficulty=TaskDifficulty.CRISIS))
        sm._compute_adaptive_rates()
        assert sm._effective_mutation_scale == sm._mutation_scale
        assert sm._effective_sbx_eta == sm._sbx_eta

    def test_adaptive_trivial_gives_low_mutation_high_eta(self):
        sm = SurvivalMechanism(rate_mode=EvolutionRateMode.ADAPTIVE)
        sm.set_task_context(TaskContext(difficulty=TaskDifficulty.TRIVIAL))
        sm._compute_adaptive_rates()
        assert sm._effective_mutation_scale < 0.5
        assert sm._effective_sbx_eta > 30.0

    def test_adaptive_hard_gives_high_mutation_low_eta(self):
        sm = SurvivalMechanism(rate_mode=EvolutionRateMode.ADAPTIVE)
        sm.set_task_context(TaskContext(difficulty=TaskDifficulty.HARD))
        sm._compute_adaptive_rates()
        assert sm._effective_mutation_scale > 1.5
        assert sm._effective_sbx_eta < 10.0

    def test_adaptive_crisis_gives_maximum_exploration(self):
        sm = SurvivalMechanism(rate_mode=EvolutionRateMode.ADAPTIVE)
        sm.set_task_context(TaskContext(difficulty=TaskDifficulty.CRISIS))
        sm._compute_adaptive_rates()
        assert sm._effective_mutation_scale > 3.0
        assert sm._effective_sbx_eta < 4.0

    def test_adaptive_moderate_stays_near_default(self):
        sm = SurvivalMechanism(rate_mode=EvolutionRateMode.ADAPTIVE)
        sm.set_task_context(TaskContext(difficulty=TaskDifficulty.MODERATE))
        sm._compute_adaptive_rates()
        # Should be around 1.0 ± 0.5
        assert 0.5 < sm._effective_mutation_scale < 2.5
        assert 5.0 < sm._effective_sbx_eta < 30.0

    def test_adaptive_respects_clamp_bounds(self):
        cfg = AdaptiveRateConfig(
            min_mutation_scale=0.2,
            max_mutation_scale=3.0,
            min_crossover_eta=3.0,
            max_crossover_eta=50.0,
        )
        sm = SurvivalMechanism(
            rate_mode=EvolutionRateMode.ADAPTIVE,
            rate_config=cfg,
        )
        # Extreme settings to test clamping
        sm.set_task_context(TaskContext(difficulty=TaskDifficulty.CRISIS))
        sm._compute_adaptive_rates()
        assert 0.2 <= sm._effective_mutation_scale <= 3.0
        assert 3.0 <= sm._effective_sbx_eta <= 50.0

    def test_diversity_blend_pushes_exploration_when_low_diversity(self):
        """Low diversity should amplify exploration via blend."""
        sm = SurvivalMechanism(rate_mode=EvolutionRateMode.ADAPTIVE)
        sm.set_task_context(TaskContext(difficulty=TaskDifficulty.MODERATE))
        # Artificially set low diversity via _latest_score
        sm.diversity._latest_score = 0.1
        sm._compute_adaptive_rates()
        # With d=0.1, diversity_factor > 1, mutation > base
        assert sm._effective_mutation_scale > 0.7
        assert sm._effective_sbx_eta < 20.0

    def test_diversity_blend_pulls_conservatism_when_high_diversity(self):
        """High diversity should reduce exploration via blend."""
        sm = SurvivalMechanism(rate_mode=EvolutionRateMode.ADAPTIVE)
        sm.set_task_context(TaskContext(difficulty=TaskDifficulty.MODERATE))
        sm.diversity._latest_score = 0.9
        sm._compute_adaptive_rates()
        assert sm._effective_mutation_scale < 1.1
        assert sm._effective_sbx_eta > 12.0

    def test_diversity_error_falls_back_to_0_5(self):
        """When diversity throws, default to 0.5."""
        class BrokenDiversity:
            def get_diversity_score(self):
                raise RuntimeError("broken")
        sm = SurvivalMechanism(rate_mode=EvolutionRateMode.ADAPTIVE)
        sm.diversity = BrokenDiversity()
        sm.set_task_context(TaskContext(difficulty=TaskDifficulty.MODERATE))
        sm._compute_adaptive_rates()
        # Should not raise, should use default 0.5
        assert 0.5 < sm._effective_mutation_scale < 2.5


# ── Evolution cycle integration ────────────────────────────────────


class TestCycleIntegration:
    """run_evolution_cycle correctly triggers adaptive rates."""

    def test_cycle_computes_adaptive_rates(self):
        sm = SurvivalMechanism(rate_mode=EvolutionRateMode.ADAPTIVE)
        sm.set_task_context(TaskContext(difficulty=TaskDifficulty.HARD))
        _register_n(sm, ["A", "B", "C", "D"])
        mb = _make_board([
            ("A", True, 0.9), ("B", True, 0.8),
            ("C", False, 0.4), ("D", False, 0.3),
        ])
        sm._merit_board = mb

        report = sm.run_evolution_cycle()
        assert report is not None
        # After cycle, effective rates should reflect HARD difficulty
        assert sm._effective_mutation_scale > 1.0
        assert sm._effective_sbx_eta < 15.0

    def test_fixed_mode_cycle_uses_classic_rates(self):
        sm = SurvivalMechanism(rate_mode=EvolutionRateMode.FIXED)
        sm.set_task_context(TaskContext(difficulty=TaskDifficulty.CRISIS))
        _register_n(sm, ["A", "B", "C", "D"])
        mb = _make_board([
            ("A", True, 0.9), ("B", True, 0.8),
            ("C", False, 0.4), ("D", False, 0.3),
        ])
        sm._merit_board = mb

        sm.run_evolution_cycle()
        assert sm._effective_mutation_scale == sm._mutation_scale
        assert sm._effective_sbx_eta == sm._sbx_eta

    def test_multiple_cycles_respect_latest_context(self):
        sm = SurvivalMechanism(rate_mode=EvolutionRateMode.ADAPTIVE)
        _register_n(sm, ["A", "B", "C"])
        mb = _make_board([("A", True, 0.9), ("B", True, 0.8), ("C", True, 0.7)])
        sm._merit_board = mb

        sm.set_task_context(TaskContext(difficulty=TaskDifficulty.HARD))
        sm.run_evolution_cycle()
        after_hard_mut = sm._effective_mutation_scale

        sm.set_task_context(TaskContext(difficulty=TaskDifficulty.TRIVIAL))
        sm.run_evolution_cycle()
        after_trivial_mut = sm._effective_mutation_scale

        assert after_trivial_mut < after_hard_mut


# ── Effective rates in _mutate_genome ──────────────────────────────


class TestMutateGenomeWithAdaptiveRates:
    """_mutate_genome uses _effective_mutation_scale."""

    def test_low_difficulty_produces_gentle_mutations(self):
        sm = SurvivalMechanism(rate_mode=EvolutionRateMode.ADAPTIVE)
        sm.set_task_context(TaskContext(difficulty=TaskDifficulty.TRIVIAL))
        _register_n(sm, ["A"])
        sm._compute_adaptive_rates()

        parent = sm._genomes["A"]
        # Generate multiple clones to check mutation magnitude
        temps = []
        for _ in range(50):
            child = sm._mutate_genome(parent, f"clone_{_}")
            temps.append(child.temperature)

        # With TRIVIAL difficulty + low mutation, variations should be small
        range_temp = max(temps) - min(temps)
        assert range_temp < 0.25  # tight spread

    def test_high_difficulty_produces_aggressive_mutations(self):
        sm = SurvivalMechanism(rate_mode=EvolutionRateMode.ADAPTIVE)
        sm.set_task_context(TaskContext(difficulty=TaskDifficulty.CRISIS))
        _register_n(sm, ["A"])
        sm._compute_adaptive_rates()

        parent = sm._genomes["A"]
        temps = []
        for _ in range(50):
            child = sm._mutate_genome(parent, f"clone_{_}")
            temps.append(child.temperature)

        range_temp = max(temps) - min(temps)
        assert range_temp > 0.15  # wider spread under crisis


# ── Effective rates in SBX crossover ───────────────────────────────


class TestSBXWithAdaptiveRates:
    """SBX crossover uses _effective_sbx_eta."""

    def test_low_difficulty_sbx_stays_close_to_parents(self):
        """High eta → offspring near parents. Low difficulty → high eta."""
        sm = SurvivalMechanism(
            rate_mode=EvolutionRateMode.ADAPTIVE,
            crossover_mode=CrossoverMode.SBX,
        )
        sm.set_task_context(TaskContext(difficulty=TaskDifficulty.TRIVIAL))
        _register_n(sm, ["P1", "P2"])
        sm._genomes["P1"].temperature = 0.7
        sm._genomes["P2"].temperature = 0.3
        sm._compute_adaptive_rates()

        children_temps = []
        for _ in range(50):
            child = sm._crossover_genome(
                sm._genomes["P1"], sm._genomes["P2"], f"C{_}"
            )
            children_temps.append(child.temperature)

        # With TRIVIAL (high eta), offspring should cluster near parents
        avg = sum(children_temps) / len(children_temps)
        assert 0.35 < avg < 0.65  # near mid-parent

    def test_high_difficulty_sbx_explores_wider(self):
        """Low eta → wider spread. High difficulty → low eta."""
        sm = SurvivalMechanism(
            rate_mode=EvolutionRateMode.ADAPTIVE,
            crossover_mode=CrossoverMode.SBX,
        )
        sm.set_task_context(TaskContext(difficulty=TaskDifficulty.CRISIS))
        _register_n(sm, ["P1", "P2"])
        sm._genomes["P1"].temperature = 0.7
        sm._genomes["P2"].temperature = 0.3
        sm._compute_adaptive_rates()

        children_temps = []
        for _ in range(50):
            child = sm._crossover_genome(
                sm._genomes["P1"], sm._genomes["P2"], f"C{_}"
            )
            children_temps.append(child.temperature)

        # With CRISIS (low eta), offspring should spread wider
        spread = max(children_temps) - min(children_temps)
        assert spread > 0.10


# ── Edge cases ─────────────────────────────────────────────────────


class TestAdaptiveRateEdgeCases:
    """Edge cases for adaptive evolution rate."""

    def test_empty_court_still_computes_rates(self):
        sm = SurvivalMechanism(rate_mode=EvolutionRateMode.ADAPTIVE)
        sm.set_task_context(TaskContext(difficulty=TaskDifficulty.HARD))
        sm._compute_adaptive_rates()
        assert sm._effective_mutation_scale > 0

    def test_rate_config_override_all_difficulties(self):
        """Custom config with only one entry should use defaults for others."""
        cfg = AdaptiveRateConfig(
            mutation_scales={TaskDifficulty.HARD: 5.0},
        )
        sm = SurvivalMechanism(rate_mode=EvolutionRateMode.ADAPTIVE, rate_config=cfg)
        sm.set_task_context(TaskContext(difficulty=TaskDifficulty.MODERATE))
        sm._compute_adaptive_rates()
        # MODERATE not in custom config → default 1.0
        assert 0.5 < sm._effective_mutation_scale < 2.5

    def test_cold_start_no_memory_no_crash(self):
        """Even without diversity or memory, compute should not crash."""
        sm = SurvivalMechanism(rate_mode=EvolutionRateMode.ADAPTIVE)
        sm.set_task_context(TaskContext(difficulty=TaskDifficulty.MODERATE))
        # No genome registered, no board
        sm._compute_adaptive_rates()
        assert sm._effective_mutation_scale > 0

    def test_zero_diversity_blend_uses_pure_difficulty(self):
        """With diversity_blend=0, rate = pure difficulty base."""
        cfg = AdaptiveRateConfig(diversity_blend=0.0)
        sm = SurvivalMechanism(rate_mode=EvolutionRateMode.ADAPTIVE, rate_config=cfg)
        sm.set_task_context(TaskContext(difficulty=TaskDifficulty.HARD))
        sm._compute_adaptive_rates()
        # Should match HARD base exactly (2.20)
        assert abs(sm._effective_mutation_scale - 2.20) < 0.01

    def test_rate_mode_property(self):
        """Verify rate_mode can be checked."""
        sm = SurvivalMechanism(rate_mode=EvolutionRateMode.FIXED)
        assert sm._rate_mode == EvolutionRateMode.FIXED

        sm2 = SurvivalMechanism(rate_mode=EvolutionRateMode.ADAPTIVE)
        assert sm2._rate_mode == EvolutionRateMode.ADAPTIVE

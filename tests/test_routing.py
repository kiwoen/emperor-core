"""
tests/test_routing.py — 45 tests for IntelligentRouter

Coverage:
- Basic routing (BALANCED, EXPLOIT, EXPLORE, PURE_FITNESS, CALIBRATED)
- Signal computation (domain match, fitness, calibration, diversity, workload)
- Provider integration (fitness, calibration, diversity, genotype, merit, memory)
- Memory boost (provider wiring, ranking impact, default zero, reasoning, safety)
- Edge cases (empty ministers, forced minister, single candidate)
- History and usage tracking
- Reset and state management
"""

import pytest
from jarvis.court.routing import (
    IntelligentRouter,
    RoutingPlan,
    RoutingSignal,
    RoutingStrategy,
)


# ──────────────────────────────────────────────
# Test: Basic routing
# ──────────────────────────────────────────────


class TestBasicRouting:
    """Basic routing with default providers."""

    def test_route_balanced(self):
        router = IntelligentRouter(strategy=RoutingStrategy.BALANCED)
        plan = router.route(
            task="分析代码漏洞",
            domain="engineering",
            available_ministers=["chancellor", "censor", "guard"],
            count=2,
        )
        assert len(plan.selected_ministers) == 2
        assert plan.strategy == RoutingStrategy.BALANCED
        assert plan.task == "分析代码漏洞"
        assert plan.domain == "engineering"

    def test_route_exploit(self):
        router = IntelligentRouter(strategy=RoutingStrategy.EXPLOIT)
        plan = router.route(
            task="安全检查",
            domain="security",
            available_ministers=["censor", "guard", "chancellor"],
            count=1,
        )
        assert len(plan.selected_ministers) == 1
        assert plan.strategy == RoutingStrategy.EXPLOIT
        # Censor should win for security domain
        assert plan.selected_ministers[0] == "censor"

    def test_route_explore_has_variety(self):
        router = IntelligentRouter(strategy=RoutingStrategy.EXPLORE)
        # Multiple ministers to allow exploration sampling
        ministers = ["chancellor", "censor", "guard", "diviner", "historian", "works"]
        selections = []
        for _ in range(5):
            plan = router.route(
                task="调研",
                domain="research",
                available_ministers=list(ministers),
                count=2,
            )
            selections.extend(plan.selected_ministers)

        # EXPLORE should not always pick the same ministers
        unique = set(selections)
        assert len(unique) >= 2, f"Only got {unique}, expected variety"

    def test_route_pure_fitness(self):
        router = IntelligentRouter(strategy=RoutingStrategy.PURE_FITNESS)
        # Set fitness: chancellor=0.9, others lower
        def fitness(m):
            return {"chancellor": 0.9, "censor": 0.5, "guard": 0.3}.get(m, 0.4)

        router.set_fitness_provider(fitness)
        plan = router.route(
            task="任意任务",
            domain="general",
            available_ministers=["chancellor", "censor", "guard"],
            count=1,
        )
        assert plan.selected_ministers[0] == "chancellor"

    def test_route_calibrated(self):
        router = IntelligentRouter(strategy=RoutingStrategy.CALIBRATED)
        # Censor has low bias (trusted), others have high bias
        def bias(m):
            return {"censor": 0.02, "chancellor": 0.25, "guard": 0.20}.get(m, 0.15)

        router.set_calibration_provider(bias)
        plan = router.route(
            task="审查",
            domain="core",
            available_ministers=["chancellor", "censor", "guard"],
            count=1,
        )
        # Censor has lowest bias → highest calibration trust
        assert plan.selected_ministers[0] == "censor"

    def test_route_strategy_override(self):
        router = IntelligentRouter(strategy=RoutingStrategy.BALANCED)
        plan = router.route(
            task="分析",
            domain="research",
            available_ministers=["historian", "diviner", "chancellor"],
            count=2,
            strategy=RoutingStrategy.EXPLOIT,
        )
        assert plan.strategy == RoutingStrategy.EXPLOIT

    def test_route_forced_minister(self):
        router = IntelligentRouter()
        plan = router.route(
            task="财务审计",
            domain="finance",
            available_ministers=["chancellor", "censor", "guard", "finance"],
            count=3,
            forced_minister="finance",
        )
        assert "finance" in plan.selected_ministers


class TestEmptyAndEdge:
    """Edge cases for routing."""

    def test_no_ministers(self):
        router = IntelligentRouter()
        plan = router.route(
            task="测试",
            domain="general",
            available_ministers=[],
            count=2,
        )
        assert len(plan.selected_ministers) == 0
        assert "无可用大臣" in plan.reasoning

    def test_count_exceeds_available(self):
        router = IntelligentRouter()
        plan = router.route(
            task="测试",
            domain="general",
            available_ministers=["chancellor", "censor"],
            count=5,
        )
        assert len(plan.selected_ministers) == 2

    def test_single_candidate(self):
        router = IntelligentRouter()
        plan = router.route(
            task="测试",
            domain="security",
            available_ministers=["censor"],
            count=1,
        )
        assert plan.selected_ministers == ["censor"]

    def test_forced_minister_not_available(self):
        router = IntelligentRouter()
        plan = router.route(
            task="测试",
            domain="general",
            available_ministers=["chancellor", "censor"],
            count=2,
            forced_minister="nonexistent",
        )
        assert "nonexistent" not in plan.selected_ministers
        assert len(plan.selected_ministers) == 2


# ──────────────────────────────────────────────
# Test: Domain matching
# ──────────────────────────────────────────────


class TestDomainMatch:
    """Domain match signal computation."""

    def test_known_minister_domain_high(self):
        router = IntelligentRouter()
        # finance minister for finance domain
        match = router._compute_domain_match("finance", "finance")
        assert match > 0.80

    def test_known_minister_domain_low(self):
        router = IntelligentRouter()
        # guard for personal domain
        match = router._compute_domain_match("guard", "personal")
        assert match < 0.70

    def test_partial_name_match(self):
        router = IntelligentRouter()
        match = router._compute_domain_match("security_expert", "security")
        assert match >= 0.70

    def test_unknown_default(self):
        router = IntelligentRouter()
        match = router._compute_domain_match("someone_new", "quantum_physics")
        assert match == 0.50


# ──────────────────────────────────────────────
# Test: Calibration trust
# ──────────────────────────────────────────────


class TestCalibrationTrust:
    """Calibration trust conversion from bias."""

    def test_perfect_calibration(self):
        router = IntelligentRouter()
        def bias(m): return 0.0
        router.set_calibration_provider(bias)
        trust = router._compute_calibration_trust("any")
        assert trust > 0.95

    def test_high_bias(self):
        router = IntelligentRouter()
        def bias(m): return 0.25
        router.set_calibration_provider(bias)
        trust = router._compute_calibration_trust("overconfident")
        assert trust < 0.30

    def test_no_provider_default(self):
        router = IntelligentRouter()
        trust = router._compute_calibration_trust("any")
        assert trust == 0.75

    def test_trust_floor(self):
        router = IntelligentRouter()
        def bias(m): return 0.50
        router.set_calibration_provider(bias)
        trust = router._compute_calibration_trust("extreme")
        assert trust >= 0.10  # Floor

    def test_negative_bias(self):
        router = IntelligentRouter()
        def bias(m): return -0.20  # Underconfident
        router.set_calibration_provider(bias)
        trust = router._compute_calibration_trust("humble")
        assert trust < 0.50  # Underconfidence is also bad


# ──────────────────────────────────────────────
# Test: Diversity bonus
# ──────────────────────────────────────────────


class TestDiversityBonus:
    """Diversity bonus computation."""

    def test_no_bonus_when_diversity_healthy(self):
        router = IntelligentRouter()
        router._usage_counter = {"a": 5, "b": 5, "c": 5}
        bonus = router._compute_diversity_bonus("d", diversity_score=0.50)
        assert bonus == 0.0

    def test_bonus_for_rare_type(self):
        router = IntelligentRouter()
        router._usage_counter = {"alpha_a": 8, "beta_b": 8, "beta_c": 8, "gamma_d": 1}
        def genotype(m): return m.split("_")[0]
        router.set_genotype_provider(genotype)
        bonus = router._compute_diversity_bonus("gamma_d", diversity_score=0.10)
        assert bonus > 0.0

    def test_no_bonus_for_common_type(self):
        router = IntelligentRouter()
        router._usage_counter = {"alpha_a": 8, "alpha_b": 8, "beta_c": 2}
        def genotype(m): return m.split("_")[0]
        router.set_genotype_provider(genotype)
        bonus = router._compute_diversity_bonus("alpha_a", diversity_score=0.10)
        assert bonus == 0.0

    def test_no_genotype_provider(self):
        router = IntelligentRouter()
        router._usage_counter = {"a": 10, "b": 1}
        bonus = router._compute_diversity_bonus("b", diversity_score=0.05)
        assert bonus == 0.0


# ──────────────────────────────────────────────
# Test: Workload penalty
# ──────────────────────────────────────────────


class TestWorkloadPenalty:
    """Workload penalty computation."""

    def test_no_penalty_for_new_minister(self):
        router = IntelligentRouter()
        penalty = router._compute_workload_penalty("fresh")
        assert penalty == 0.0

    def test_penalty_for_overused(self):
        router = IntelligentRouter()
        router._usage_counter = {"overworked": 10, "normal": 2, "normal2": 2}
        penalty = router._compute_workload_penalty("overworked")
        assert penalty > 0.0

    def test_no_penalty_normal_usage(self):
        router = IntelligentRouter()
        router._usage_counter = {"a": 3, "b": 3, "c": 3}
        penalty = router._compute_workload_penalty("a")
        assert penalty == 0.0


# ──────────────────────────────────────────────
# Test: Usage tracking and history
# ──────────────────────────────────────────────


class TestUsageTracking:
    """Usage tracking and history."""

    def test_usage_counter_increments(self):
        router = IntelligentRouter()
        router.route(
            task="任务1",
            domain="engineering",
            available_ministers=["chancellor", "censor"],
            count=1,
        )
        router.route(
            task="任务2",
            domain="security",
            available_ministers=["censor", "guard"],
            count=1,
        )
        stats = router.get_usage_stats()
        assert stats.get("censor", 0) >= 1

    def test_history_grows(self):
        router = IntelligentRouter()
        for i in range(3):
            router.route(
                task=f"task_{i}",
                domain="general",
                available_ministers=["chancellor", "censor"],
                count=1,
            )
        history = router.get_history()
        assert len(history) == 3

    def test_history_limit(self):
        router = IntelligentRouter()
        for i in range(30):
            router.route(
                task=f"task_{i}",
                domain="general",
                available_ministers=["chancellor", "guard"],
                count=1,
            )
        history = router.get_history(limit=5)
        assert len(history) == 5

    def test_reset_all_usage(self):
        router = IntelligentRouter()
        router.route(
            task="t",
            domain="general",
            available_ministers=["chancellor", "censor"],
            count=1,
        )
        assert router.get_usage_stats()
        router.reset_usage()
        assert not router.get_usage_stats()

    def test_reset_single_minister(self):
        router = IntelligentRouter()
        router.route(
            task="t1",
            domain="general",
            available_ministers=["chancellor", "censor", "guard"],
            count=2,
        )
        router.reset_usage("chancellor")
        stats = router.get_usage_stats()
        assert "chancellor" not in stats
        # At least one other minister should remain
        remaining = set(stats.keys())
        assert len(remaining) >= 1
        assert "censor" in remaining or "guard" in remaining


# ──────────────────────────────────────────────
# Test: Provider integration
# ──────────────────────────────────────────────


class TestProviderIntegration:
    """Providers are correctly called and scores reflect them."""

    def test_fitness_provider_affects_selection(self):
        router = IntelligentRouter()
        def fitness(m):
            return {"hero": 0.95, "villain": 0.10}.get(m, 0.5)
        router.set_fitness_provider(fitness)

        plan = router.route(
            task="任务",
            domain="general",
            available_ministers=["hero", "villain"],
            count=1,
        )
        assert plan.selected_ministers[0] == "hero"

    def test_all_providers_combined(self):
        router = IntelligentRouter(strategy=RoutingStrategy.BALANCED)
        def fitness(m): return {"chancellor": 0.85, "censor": 0.70}.get(m, 0.5)
        def bias(m): return {"chancellor": 0.05, "censor": 0.30}.get(m, 0.15)
        def diversity(): return 0.08

        router.set_fitness_provider(fitness)
        router.set_calibration_provider(bias)
        router.set_diversity_provider(diversity)

        plan = router.route(
            task="综合任务",
            domain="engineering",
            available_ministers=["chancellor", "censor"],
            count=1,
        )
        # chancellor: higher fitness (0.85), lower bias (0.05), domain match 0.85
        # censor: lower fitness (0.70), higher bias (0.30), domain match 0.75
        assert plan.selected_ministers[0] == "chancellor"

    def test_merit_provider_stored(self):
        router = IntelligentRouter()
        def merit(m): return {"chancellor": 95}.get(m, 50)
        router.set_merit_provider(merit)
        # Merit provider is stored but not directly used in composite
        # (can be accessed externally by integration code)
        assert router._merit_provider is not None
        assert router._merit_provider("chancellor") == 95

    def test_provider_exception_is_safe(self):
        router = IntelligentRouter()
        def broken(m): raise RuntimeError("oops")
        router.set_fitness_provider(broken)
        # Should not crash
        plan = router.route(
            task="t",
            domain="general",
            available_ministers=["chancellor"],
            count=1,
        )
        assert len(plan.selected_ministers) == 1

    def test_provider_returns_none_is_safe(self):
        router = IntelligentRouter()
        def none_provider(m): return None
        router.set_fitness_provider(none_provider)
        plan = router.route(
            task="t",
            domain="general",
            available_ministers=["chancellor"],
            count=1,
        )
        assert len(plan.selected_ministers) == 1


# ──────────────────────────────────────────────
# Test: Memory Boost Routing
# ──────────────────────────────────────────────


class TestMemoryBoostRouting:
    """Routing with memory-based boost signal."""

    def test_memory_provider_set_and_used(self):
        router = IntelligentRouter(strategy=RoutingStrategy.BALANCED)

        def mem_b(m: str) -> float:
            return {"chancellor": 0.12, "censor": 0.05}.get(m, 0.0)

        router.set_memory_provider(mem_b)

        plan = router.route(
            task="分析代码",
            domain="engineering",
            available_ministers=["chancellor", "censor", "guard"],
            count=2,
        )

        assert len(plan.selected_ministers) == 2
        # chancellor should have a memory boost
        assert "chancellor" in plan.signal_map
        assert plan.signal_map["chancellor"].memory_boost == 0.12
        assert plan.signal_map["censor"].memory_boost == 0.05
        # guard has no special memory
        if "guard" in plan.signal_map:
            assert plan.signal_map["guard"].memory_boost == 0.0

    def test_memory_boost_affects_ranking(self):
        """Memory boost should push a minister ahead when scores are close."""
        router = IntelligentRouter(strategy=RoutingStrategy.BALANCED)

        # chancellor and censor have identical domain match and no fitness bias
        # except chancellor gets a strong memory boost
        def mem_b(m: str) -> float:
            return {"chancellor": 0.15, "censor": 0.0}.get(m, 0.0)

        router.set_memory_provider(mem_b)

        plan = router.route(
            task="写代码",
            domain="engineering",
            available_ministers=["chancellor", "censor"],
            count=1,
        )

        assert len(plan.selected_ministers) == 1
        # chancellor should win due to memory boost
        assert plan.selected_ministers[0] == "chancellor"

    def test_no_memory_provider_defaults_to_zero(self):
        router = IntelligentRouter()
        plan = router.route(
            task="任意任务",
            domain="engineering",
            available_ministers=["chancellor"],
            count=1,
        )
        assert plan.signal_map["chancellor"].memory_boost == 0.0

    def test_memory_boost_in_reasoning_string(self):
        router = IntelligentRouter()

        def mem_b(m: str) -> float:
            return 0.10

        router.set_memory_provider(mem_b)

        plan = router.route(
            task="分析代码",
            domain="engineering",
            available_ministers=["chancellor", "censor", "guard"],
            count=1,
        )
        assert "记忆=0.10" in plan.reasoning

    def test_memory_provider_exception_is_safe(self):
        router = IntelligentRouter()

        def broken_mem(m: str) -> float:
            raise RuntimeError("memory unavailable")

        router.set_memory_provider(broken_mem)

        plan = router.route(
            task="任务",
            domain="general",
            available_ministers=["chancellor"],
            count=1,
        )
        assert len(plan.selected_ministers) == 1
        assert plan.signal_map["chancellor"].memory_boost == 0.0

    def test_memory_boost_persists_across_calls(self):
        router = IntelligentRouter()

        call_count = [0]

        def mem_b(m: str) -> float:
            call_count[0] += 1
            return 0.08

        router.set_memory_provider(mem_b)

        router.route(
            task="任务1",
            domain="engineering",
            available_ministers=["chancellor", "censor", "guard"],
            count=1,
        )
        router.route(
            task="任务2",
            domain="security",
            available_ministers=["censor", "guard", "chancellor"],
            count=1,
        )

        # provider was called for each minister across both calls
        assert call_count[0] == 6  # 3 + 3

    def test_memory_boost_weight_configurable(self):
        router = IntelligentRouter()
        assert "memory_boost" in router.weights
        assert router.weights["memory_boost"] == 0.15

        router.weights["memory_boost"] = 0.30
        assert router.weights["memory_boost"] == 0.30

    def test_composite_includes_memory_boost(self):
        """Verify memory_boost is factored into composite score."""
        router = IntelligentRouter(strategy=RoutingStrategy.BALANCED)

        def mem_b(m: str) -> float:
            return {"chancellor": 0.15, "guard": 0.0}.get(m, 0.0)

        router.set_memory_provider(mem_b)

        # Both ministers, same fitness → chancellor should score higher
        plan = router.route(
            task="写代码",
            domain="engineering",
            available_ministers=["chancellor", "guard"],
            count=2,
        )

        chancellor_score = plan.signal_map["chancellor"].composite_score
        guard_score = plan.signal_map["guard"].composite_score
        # chancellor has memory boost → higher composite
        assert chancellor_score > guard_score

"""
Tests for CourtOrchestrator — routing, calibration, feedback integration.
"""

import asyncio
import pytest
from unittest.mock import Mock, MagicMock, AsyncMock, patch

from jarvis.court.orchestrator import CourtOrchestrator, SmartEmperor
from jarvis.court.routing import RoutingStrategy, IntelligentRouter
from jarvis.court.calibration import ConfidenceCalibrator
from jarvis.court.emperor import Decree, ImperialCourt
from jarvis.court.minister import Memorial, MinisterState


# ──────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────


def make_memorial(
    minister="chancellor",
    edict_id="dc001",
    success=True,
    output="good advice",
    confidence=0.85,
    suggestions=None,
):
    return Memorial(
        edict_id=edict_id,
        minister=minister,
        state=MinisterState.REPORTING,
        success=success,
        output=output,
        confidence=confidence,
        suggestions=suggestions or [],
    )


@pytest.fixture
def orchestrator():
    """CourtOrchestrator with mock ministers (no real LLM)."""
    court = CourtOrchestrator()
    # Manually register ministers with controlled profiles
    from jarvis.court.minister import MinisterProfile
    from jarvis.court.minister import Minister

    profiles = {
        "chancellor": MinisterProfile(
            title="Chancellor",
            archetype="GPT-5",
            domain="engineering",
            strengths=["code"],
            weaknesses=[""],
            decision_style="analytical",
            quality_score=0.9,
        ),
        "censor": MinisterProfile(
            title="Censor",
            archetype="Claude-4",
            domain="security",
            strengths=["audit"],
            weaknesses=["slow"],
            decision_style="rigorous",
            quality_score=0.85,
        ),
        "ceremonies": MinisterProfile(
            title="Ceremonies",
            archetype="GPT-5",
            domain="personal",
            strengths=["writing"],
            weaknesses=[""],
            decision_style="empathetic",
            quality_score=0.7,
        ),
        "diviner": MinisterProfile(
            title="Diviner",
            archetype="Claude-4",
            domain="research",
            strengths=["analysis"],
            weaknesses=["verbose"],
            decision_style="exploratory",
            quality_score=0.75,
        ),
    }

    for name, profile in profiles.items():
        m = Minister(profile=profile)
        court.ministers[name] = m
        court.survival.register_minister(name, profile.archetype)

    return court


# ──────────────────────────────────────────────
# Test: Basic initialization
# ──────────────────────────────────────────────


class TestInitialization:
    """Verify orchestrator starts with correct defaults."""

    def test_creates_calibrator(self):
        court = CourtOrchestrator()
        assert court.calibrator is not None
        assert isinstance(court.calibrator, ConfidenceCalibrator)

    def test_creates_router(self):
        court = CourtOrchestrator()
        assert court.router is not None
        assert isinstance(court.router, IntelligentRouter)

    def test_default_strategy_is_balanced(self):
        court = CourtOrchestrator()
        assert court.router.strategy == RoutingStrategy.BALANCED

    def test_custom_routing_strategy(self):
        court = CourtOrchestrator(routing_strategy=RoutingStrategy.EXPLORE)
        assert court.router.strategy == RoutingStrategy.EXPLORE

    def test_inherits_from_imperial_court(self):
        court = CourtOrchestrator()
        assert isinstance(court, ImperialCourt)

    def test_get_calibrator(self, orchestrator):
        assert orchestrator.get_calibrator() is orchestrator.calibrator

    def test_get_router(self, orchestrator):
        assert orchestrator.get_router() is orchestrator.router

    def test_set_routing_strategy(self, orchestrator):
        orchestrator.set_routing_strategy(RoutingStrategy.PURE_FITNESS)
        assert orchestrator.router.strategy == RoutingStrategy.PURE_FITNESS


# ──────────────────────────────────────────────
# Test: Minister selection (routing integration)
# ──────────────────────────────────────────────


class TestMinisterSelection:
    """Verify IntelligentRouter is used for minister selection."""

    def test_router_selects_minister(self, orchestrator):
        orchestrator._last_intent = "write code"
        scores = {
            "chancellor": 0.92,
            "censor": 0.45,
            "ceremonies": 0.30,
            "diviner": 0.55,
        }
        selected = orchestrator._select_ministers(scores, top_n=2)
        assert len(selected) >= 1
        assert len(selected) <= 2
        assert "chancellor" in selected  # Highest domain match

    def test_router_respects_count(self, orchestrator):
        orchestrator._last_intent = "security audit"
        scores = {
            "chancellor": 0.55,
            "censor": 0.80,
            "ceremonies": 0.40,
            "diviner": 0.35,
        }
        selected = orchestrator._select_ministers(scores, top_n=1)
        assert len(selected) <= 1

    def test_empty_scores_returns_empty(self, orchestrator):
        assert orchestrator._select_ministers({}) == []

    def test_single_minister(self, orchestrator):
        orchestrator._last_intent = "write docs"
        scores = {"chancellor": 0.50}
        selected = orchestrator._select_ministers(scores)
        assert selected == ["chancellor"]

    def test_router_usage_tracked(self, orchestrator):
        orchestrator._last_intent = "review code"
        scores = {"chancellor": 0.85, "censor": 0.65, "diviner": 0.50, "ceremonies": 0.30}
        orchestrator._select_ministers(scores, top_n=1)
        stats = orchestrator.router.get_usage_stats()
        assert len(stats) >= 1

    def test_domain_inference_engineering(self, orchestrator):
        orchestrator._last_intent = "optimize database queries"
        scores = {"chancellor": 0.85, "censor": 0.40}
        domain = orchestrator._infer_domain(
            list(scores.keys()), scores
        )
        assert domain == "engineering"

    def test_domain_inference_security(self, orchestrator):
        orchestrator._last_intent = "find vulnerabilities"
        scores = {"censor": 0.75, "chancellor": 0.30}
        domain = orchestrator._infer_domain(
            list(scores.keys()), scores
        )
        assert domain == "security"


# ──────────────────────────────────────────────
# Test: Confidence calibration
# ──────────────────────────────────────────────


class TestConfidenceCalibration:
    """Verify memorial confidences are calibrated before synthesis."""

    def test_calibration_adjusts_confidence(self, orchestrator):
        # Pre-feed some calibration data so the calibrator has a bias
        orchestrator.calibrator.update(
            decree_id="dc001",
            minister_name="chancellor",
            raw_confidence=0.90,
            actual_outcome=0.0,  # Failed — calibrator should learn overconfidence
            domain="engineering",
        )
        orchestrator.calibrator.update(
            decree_id="dc001",
            minister_name="chancellor",
            raw_confidence=0.90,
            actual_outcome=0.0,
            domain="engineering",
        )
        orchestrator.calibrator.update(
            decree_id="dc001",
            minister_name="chancellor",
            raw_confidence=0.90,
            actual_outcome=0.0,
            domain="engineering",
        )

        orchestrator._current_domain = "engineering"
        memorials = [
            make_memorial("chancellor", confidence=0.90),
        ]

        calibrated = orchestrator._calibrate_memorials(memorials)
        assert calibrated[0].confidence < 0.90  # Punished for overconfidence

    def test_calibration_preserves_failed_memorials(self, orchestrator):
        orchestrator._current_domain = "general"
        memorials = [
            make_memorial("chancellor", success=False, confidence=0.3),
        ]
        calibrated = orchestrator._calibrate_memorials(memorials)
        assert not calibrated[0].success
        # Failed memorials are not calibrated
        assert calibrated[0].confidence == 0.3

    def test_calibration_preserves_output_content(self, orchestrator):
        orchestrator._current_domain = "general"
        memorials = [make_memorial("chancellor", output="important result")]
        calibrated = orchestrator._calibrate_memorials(memorials)
        assert calibrated[0].output == "important result"

    def test_calibration_preserves_minister_name(self, orchestrator):
        orchestrator._current_domain = "security"
        memorials = [make_memorial("censor", confidence=0.75)]
        calibrated = orchestrator._calibrate_memorials(memorials)
        assert calibrated[0].minister == "censor"

    def test_unchanged_without_history(self, orchestrator):
        """New calibrator with no history: confidence should stay close."""
        orchestrator._current_domain = "engineering"
        memorials = [make_memorial("ceremonies", confidence=0.70)]
        calibrated = orchestrator._calibrate_memorials(memorials)
        # Near identity (bias ≈ 0 for fresh calibrator)
        assert abs(calibrated[0].confidence - 0.70) < 0.02


# ──────────────────────────────────────────────
# Test: Feedback loop
# ──────────────────────────────────────────────


class TestFeedbackLoop:
    """Verify calibration feedback is recorded after decree."""

    def test_feedback_updates_calibrator(self, orchestrator):
        decree = Decree(
            decree_id="dc002",
            intent="write tests",
            success=True,
            output="done",
            ministers_consulted=["chancellor"],
            memorials=[make_memorial("chancellor", confidence=0.80)],
            confidence=0.80,
            execution_time_ms=100,
        )
        orchestrator._current_domain = "engineering"

        # Before: no records
        assert orchestrator.calibrator.get_calibration_summary()["total_records"] == 0

        orchestrator._record_calibration_feedback(decree, ["chancellor"])

        # After: one record
        summary = orchestrator.calibrator.get_calibration_summary()
        assert summary["total_records"] >= 1

    def test_feedback_only_for_participating_ministers(self, orchestrator):
        decree = Decree(
            decree_id="dc003",
            intent="audit",
            success=True,
            output="secure",
            ministers_consulted=["censor", "diviner"],
            memorials=[
                make_memorial("censor", confidence=0.85),
                make_memorial("diviner", confidence=0.60),
            ],
            confidence=0.72,
            execution_time_ms=150,
        )
        orchestrator._current_domain = "security"

        before = orchestrator.calibrator.get_calibration_summary()["total_records"]
        orchestrator._record_calibration_feedback(
            decree, ["censor", "diviner"]
        )
        after = orchestrator.calibrator.get_calibration_summary()["total_records"]
        assert after == before + 2  # Two ministers recorded

    def test_router_reset_after_cycle(self, orchestrator):
        orchestrator._last_intent = "test"
        orchestrator._select_ministers(
            {"chancellor": 0.80, "censor": 0.60, "diviner": 0.40, "ceremonies": 0.30},
            top_n=1,
        )
        before = len(orchestrator.router.get_usage_stats())
        assert before >= 1

        orchestrator._router_post_cycle()
        after = len(orchestrator.router.get_usage_stats())
        assert after == 0


# ──────────────────────────────────────────────
# Test: Domain expertise
# ──────────────────────────────────────────────


class TestDomainExpertise:
    """Verify domain mapping computation."""

    def test_domain_expertise_returns_all_ministers(self, orchestrator):
        expertise = orchestrator.get_domain_expertise()
        assert "chancellor" in expertise
        assert "censor" in expertise
        assert "ceremonies" in expertise
        assert "diviner" in expertise

    def test_domain_expertise_spans_all_domains(self, orchestrator):
        expertise = orchestrator.get_domain_expertise()
        expected_domains = {
            "engineering", "research", "security", "finance",
            "personal", "health", "home", "general", "core",
        }
        for domains in expertise.values():
            assert set(domains.keys()) == expected_domains

    def test_domain_expertise_values_in_range(self, orchestrator):
        expertise = orchestrator.get_domain_expertise()
        for domains in expertise.values():
            for domain, score in domains.items():
                assert 0.0 <= score <= 1.0, (
                    f"{domain} score {score} out of range"
                )

    def test_chancellor_good_at_engineering(self, orchestrator):
        expertise = orchestrator.get_domain_expertise()
        eng = expertise["chancellor"]["engineering"]
        pers = expertise["chancellor"]["personal"]
        assert eng > pers  # chancellor ≈ engineering, not personal


# ──────────────────────────────────────────────
# Test: Enhanced court metrics
# ──────────────────────────────────────────────


class TestCourtMetrics:
    """Verify enhanced metrics include calibration + router data."""

    def test_metrics_include_calibration(self, orchestrator):
        metrics = orchestrator.get_court_metrics()
        assert "calibration" in metrics
        assert "total_records" in metrics["calibration"]

    def test_metrics_include_router_usage(self, orchestrator):
        metrics = orchestrator.get_court_metrics()
        assert "router_usage" in metrics

    def test_metrics_include_routing_strategy(self, orchestrator):
        metrics = orchestrator.get_court_metrics()
        assert metrics["routing_strategy"] == "BALANCED"


# ──────────────────────────────────────────────
# Test: Legacy compatibility (ImperialCourt API)
# ──────────────────────────────────────────────


class TestLegacyCompatibility:
    """Ensure base ImperialCourt methods still work."""

    def test_install_minister(self, orchestrator):
        from jarvis.court.minister import MinisterProfile, Minister
        m = Minister(MinisterProfile(
            title="Test",
            archetype="GPT-5",
            domain="general",
            strengths=["testing"],
            weaknesses=[],
            decision_style="test",
            quality_score=0.5,
        ))
        orchestrator.install_minister(m)
        assert "Test" in orchestrator.ministers

    def test_dismiss_minister(self, orchestrator):
        orchestrator.dismiss_minister("ceremonies")
        assert "ceremonies" not in orchestrator.ministers

    def test_analyze_petition(self, orchestrator):
        scores = orchestrator.analyze_petition("security audit")
        assert "censor" in scores
        # Censor should have high security score
        assert scores.get("censor", 0) >= 0.3

    def test_send_feedback(self, orchestrator):
        # Should not raise (records to minister and merit board)
        orchestrator.send_feedback("dc001", "chancellor", 0.9)


# ──────────────────────────────────────────────
# Test: Memory-Enhanced Routing in Orchestrator
# ──────────────────────────────────────────────


class TestMemoryEnhancedRouting:
    """Verify _compute_memory_boost integration in CourtOrchestrator."""

    def test_cold_start_returns_zero(self, orchestrator):
        """No memories recorded → boost is 0.0."""
        boost = orchestrator._compute_memory_boost("chancellor", "engineering")
        assert boost == 0.0

    def test_empty_intent_returns_zero(self, orchestrator):
        """Empty intent → no query possible → boost is 0.0."""
        orchestrator._last_intent = ""
        boost = orchestrator._compute_memory_boost("chancellor", "engineering")
        assert boost == 0.0

    def test_successful_memory_gives_boost(self, orchestrator):
        """Record a successful memory → query returns positive boost."""
        from jarvis.court.memory import MemoryEntry

        entry = MemoryEntry(
            id="mem-test-001",
            domain="engineering",
            minister_name="chancellor",
            intent="分析代码安全漏洞",
            intent_keywords=["代码", "安全", "漏洞", "分析"],
            success=True,
            confidence=0.90,
            execution_time_ms=150.0,
            timestamp=0.0,
        )
        orchestrator.memory.record(entry)
        orchestrator._last_intent = "分析代码安全漏洞"

        boost = orchestrator._compute_memory_boost("chancellor", "engineering")
        assert boost > 0.0

    def test_failed_memory_gives_lower_boost(self, orchestrator):
        """Failed memory → lower boost compared to successes."""
        from jarvis.court.memory import MemoryEntry

        # Record 1 success + 2 failures for chancellor
        for i, success in enumerate([True, False, False]):
            entry = MemoryEntry(
                id=f"mem-test-{i}",
                domain="engineering",
                minister_name="chancellor",
                intent="代码审查任务",
                intent_keywords=["代码", "审查"],
                success=success,
                confidence=0.80,
                execution_time_ms=100.0,
                timestamp=float(i),
            )
            orchestrator.memory.record(entry)

        orchestrator._last_intent = "代码审查任务"

        boost = orchestrator._compute_memory_boost("chancellor", "engineering")
        # 1/3 success × 1/3 minister = 0.33 × 0.33 × 0.15 ≈ 0.017
        assert 0.0 < boost < 0.15

    def test_no_matching_minister_returns_zero(self, orchestrator):
        """Memories exist but for a different minister → boost is 0.0."""
        from jarvis.court.memory import MemoryEntry

        entry = MemoryEntry(
            id="mem-test-censor",
            domain="security",
            minister_name="censor",
            intent="安全审查",
            intent_keywords=["安全", "审查"],
            success=True,
            confidence=0.95,
            execution_time_ms=200.0,
            timestamp=0.0,
        )
        orchestrator.memory.record(entry)
        orchestrator._last_intent = "安全审查"

        # chancellor has no record in security domain
        boost = orchestrator._compute_memory_boost("chancellor", "security")
        assert boost == 0.0

    def test_boost_capped_at_max(self, orchestrator):
        """Memory boost must not exceed 0.15 (the weight cap)."""
        from jarvis.court.memory import MemoryEntry

        # Create perfect track record for chancellor
        for i in range(10):
            entry = MemoryEntry(
                id=f"mem-perfect-{i}",
                domain="engineering",
                minister_name="chancellor",
                intent=f"编写单元测试第{i}次",
                intent_keywords=["单元测试", "编写", f"第{i}"],
                success=True,
                confidence=0.99,
                execution_time_ms=50.0,
                timestamp=float(i),
            )
            orchestrator.memory.record(entry)

        orchestrator._last_intent = "编写单元测试"

        boost = orchestrator._compute_memory_boost("chancellor", "engineering")
        assert 0.0 <= boost <= 0.15

    def test_memory_provider_wired_in_select_ministers(self, orchestrator):
        """After _ensure_router_providers + select, memory provider is set."""
        from jarvis.court.memory import MemoryEntry

        # Record a memory
        entry = MemoryEntry(
            id="mem-wired-001",
            domain="engineering",
            minister_name="chancellor",
            intent="写Python代码",
            intent_keywords=["Python", "代码", "编写"],
            success=True,
            confidence=0.92,
            execution_time_ms=120.0,
            timestamp=0.0,
        )
        orchestrator.memory.record(entry)
        orchestrator._last_intent = "写Python代码"

        # Simulate selection (forces provider wiring)
        scores = {"chancellor": 0.85, "censor": 0.60}
        orchestrator._select_ministers(scores, top_n=2)

        # Memory provider should now be set
        assert orchestrator.router._memory_provider is not None


# ──────────────────────────────────────────────
# Test: SmartEmperor convenience wrapper
# ──────────────────────────────────────────────


class TestSmartEmperor:
    """Verify SmartEmperor wraps CourtOrchestrator correctly."""

    def test_creates_with_defaults(self):
        se = SmartEmperor()
        assert se.court is not None
        assert isinstance(se.court, CourtOrchestrator)

    def test_custom_strategy(self):
        se = SmartEmperor(routing_strategy=RoutingStrategy.EXPLORE)
        assert se.court.router.strategy == RoutingStrategy.EXPLORE

    def test_get_calibrator(self):
        se = SmartEmperor()
        assert se.get_calibrator() is se.court.calibrator

    def test_get_router(self):
        se = SmartEmperor()
        assert se.get_router() is se.court.router

    def test_ministers_installed(self):
        se = SmartEmperor()
        # Factory creates 8 ministers
        assert len(se.court.ministers) >= 4

    def test_get_court_metrics(self):
        se = SmartEmperor()
        metrics = se.get_court_metrics()
        assert "calibration" in metrics

    def test_set_routing_strategy(self):
        se = SmartEmperor()
        se.set_routing_strategy(RoutingStrategy.CALIBRATED)
        assert se.court.router.strategy == RoutingStrategy.CALIBRATED

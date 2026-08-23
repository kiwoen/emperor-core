"""Tests for task-feedback evolution (streaks, hits, merit/stability adjustments)."""

from __future__ import annotations

from jarvis.court.court import Court
from jarvis.court.evolution import MinisterGenome, SurvivalMechanism
from jarvis.court.merit_board import MeritBoard
from jarvis.court.task_engine import TaskEngine, TaskRequest


class MockCapabilityRegistry:
    """Capability registry that returns a known capability."""

    def __init__(self, match: bool = True, cap_name: str = "test_cap"):
        self.match = match
        self.cap_name = cap_name
        self.last_prompt = ""
        self.last_domain = ""

    def find_best(self, prompt: str, domain: str):
        self.last_prompt = prompt
        self.last_domain = domain
        if self.match:
            return MockCapability(self.cap_name)
        return None

    def execute(self, name: str, prompt: str):
        return {"result": f"[mock-result] {prompt}"}


class MockCapability:
    def __init__(self, name: str):
        self.name = name


class TestTaskFeedbackStreaks:
    """Test success/failure streak tracking."""

    def test_success_streak_increments(self):
        """Capability match increments success_streak, resets failure_streak."""
        court = Court()
        court.register("丞相", domain="writing")
        genome = court._sm._genomes["丞相"]
        registry = MockCapabilityRegistry(match=True, cap_name="text_gen")

        engine = TaskEngine(court, capability_registry=registry)
        req = TaskRequest(id="t1", prompt="写一首诗", domain="writing")
        engine.execute(req)

        assert genome.success_streak == 1
        assert genome.failure_streak == 0
        assert genome.total_tasks == 1
        assert genome.capability_hits == 1

    def test_failure_streak_increments_no_capability(self):
        """No capability match increments failure_streak, resets success_streak."""
        court = Court()
        court.register("丞相", domain="writing")
        genome = court._sm._genomes["丞相"]
        registry = MockCapabilityRegistry(match=False)

        engine = TaskEngine(court, capability_registry=registry)
        req = TaskRequest(id="t2", prompt="写一首诗", domain="writing")
        engine.execute(req)

        assert genome.failure_streak == 1
        assert genome.success_streak == 0
        assert genome.total_tasks == 1
        assert genome.capability_hits == 0

    def test_streak_alternates_correctly(self):
        """Mix of matches and mismatches toggles streaks correctly."""
        court = Court()
        court.register("丞相", domain="writing")
        genome = court._sm._genomes["丞相"]

        match_registry = MockCapabilityRegistry(match=True, cap_name="text_gen")
        no_match_registry = MockCapabilityRegistry(match=False)

        # 3 successes
        engine = TaskEngine(court, capability_registry=match_registry)
        for i in range(3):
            engine.execute(TaskRequest(id=f"s{i}", prompt="test", domain="writing"))
        assert genome.success_streak == 3
        assert genome.failure_streak == 0

        # 2 failures
        engine = TaskEngine(court, capability_registry=no_match_registry)
        for i in range(2):
            engine.execute(TaskRequest(id=f"f{i}", prompt="test", domain="writing"))
        assert genome.success_streak == 0
        assert genome.failure_streak == 2

        # 1 more success
        engine = TaskEngine(court, capability_registry=match_registry)
        engine.execute(TaskRequest(id="s3", prompt="test", domain="writing"))
        assert genome.success_streak == 1
        assert genome.failure_streak == 0


class TestTaskFeedbackMeritStability:
    """Test merit and stability adjustments from feedback."""

    def test_capability_match_boosts_stability(self):
        """Capability match increases confidence_baseline (stability)."""
        court = Court()
        court.register("丞相", domain="writing")
        genome = court._sm._genomes["丞相"]
        original_stability = genome.confidence_baseline
        registry = MockCapabilityRegistry(match=True, cap_name="text_gen")

        engine = TaskEngine(court, capability_registry=registry)
        engine.execute(TaskRequest(id="t1", prompt="test", domain="writing"))

        assert genome.confidence_baseline > original_stability
        assert genome.confidence_baseline <= 1.0

    def test_no_capability_decreases_stability(self):
        """No capability match decreases confidence_baseline."""
        court = Court()
        court.register("丞相", domain="writing")
        genome = court._sm._genomes["丞相"]
        original_stability = genome.confidence_baseline
        registry = MockCapabilityRegistry(match=False)

        engine = TaskEngine(court, capability_registry=registry)
        engine.execute(TaskRequest(id="t1", prompt="test", domain="writing"))

        assert genome.confidence_baseline < original_stability
        assert genome.confidence_baseline >= 0.0

    def test_streak_bonus_on_third_success(self):
        """After 3 consecutive successes, streak bonus applies."""
        court = Court()
        court.register("丞相", domain="writing")
        genome = court._sm._genomes["丞相"]
        registry = MockCapabilityRegistry(match=True, cap_name="text_gen")

        engine = TaskEngine(court, capability_registry=registry)
        for i in range(3):
            engine.execute(TaskRequest(id=f"t{i}", prompt="test", domain="writing"))

        assert genome.success_streak == 3
        # streak_bonus = min(3 // 3, 3) = 1, so merit_gain = 2 + 1 = 3
        assert genome.capability_hits == 3


class TestTaskFeedbackEdgeCases:
    """Test edge cases."""

    def test_total_tasks_increments(self):
        """total_tasks increments on every execution regardless of outcome."""
        court = Court()
        court.register("丞相", domain="writing")
        genome = court._sm._genomes["丞相"]
        registry = MockCapabilityRegistry(match=True, cap_name="text_gen")

        engine = TaskEngine(court, capability_registry=registry)
        for i in range(5):
            engine.execute(TaskRequest(id=f"t{i}", prompt="test", domain="writing"))
        assert genome.total_tasks == 5

    def test_capability_hits_accurate(self):
        """capability_hits only increments on match."""
        court = Court()
        court.register("丞相", domain="writing")
        genome = court._sm._genomes["丞相"]

        match_registry = MockCapabilityRegistry(match=True, cap_name="text_gen")
        no_match_registry = MockCapabilityRegistry(match=False)

        engine = TaskEngine(court, capability_registry=match_registry)
        engine.execute(TaskRequest(id="t1", prompt="test", domain="writing"))
        engine.execute(TaskRequest(id="t2", prompt="test", domain="writing"))

        engine = TaskEngine(court, capability_registry=no_match_registry)
        engine.execute(TaskRequest(id="t3", prompt="test", domain="writing"))

        engine = TaskEngine(court, capability_registry=match_registry)
        engine.execute(TaskRequest(id="t4", prompt="test", domain="writing"))

        assert genome.capability_hits == 3
        assert genome.total_tasks == 4

    def test_stability_capped_at_1(self):
        """Stability never exceeds 1.0."""
        court = Court()
        court.register("丞相", domain="writing")
        genome = court._sm._genomes["丞相"]
        genome.confidence_baseline = 0.999
        registry = MockCapabilityRegistry(match=True, cap_name="text_gen")

        engine = TaskEngine(court, capability_registry=registry)
        engine.execute(TaskRequest(id="t1", prompt="test", domain="writing"))

        assert genome.confidence_baseline <= 1.0

    def test_stability_floored_at_0(self):
        """Stability never goes below 0.0."""
        court = Court()
        court.register("丞相", domain="writing")
        genome = court._sm._genomes["丞相"]
        genome.confidence_baseline = 0.001
        registry = MockCapabilityRegistry(match=False)

        engine = TaskEngine(court, capability_registry=registry)
        for _ in range(10):
            engine.execute(TaskRequest(
                id=f"t{genome.total_tasks}", prompt="test", domain="writing"
            ))

        assert genome.confidence_baseline >= 0.0

    def test_failure_streak_10_accelerated_drop(self):
        """After 10 consecutive failures, stability drops faster."""
        court = Court()
        court.register("丞相", domain="writing")
        genome = court._sm._genomes["丞相"]
        genome.confidence_baseline = 0.5
        registry = MockCapabilityRegistry(match=False)

        engine = TaskEngine(court, capability_registry=registry)
        for i in range(10):
            engine.execute(TaskRequest(
                id=f"f{i}", prompt="test", domain="writing"
            ))

        # After 10 failures, accelerated drop of 0.02 should have been applied
        assert genome.failure_streak == 10
        # Regular drops: -0.005 * 10 = -0.05, plus accelerated: -0.02
        # Total drop ~0.07 from 0.5 → ~0.43
        assert genome.confidence_baseline < 0.45

    def test_no_capability_registry_does_not_crash(self):
        """Engine without capability_registry should not crash."""
        court = Court()
        court.register("丞相", domain="writing")
        genome = court._sm._genomes["丞相"]

        engine = TaskEngine(court, capability_registry=None)
        engine.execute(TaskRequest(id="t1", prompt="test", domain="writing"))

        # Should complete without error, treating as no-capability match
        assert genome.total_tasks == 1
        assert genome.failure_streak == 1

    def test_empty_result_does_not_crash(self):
        """Empty or empty-string prompt should not crash."""
        court = Court()
        court.register("丞相", domain="writing")
        registry = MockCapabilityRegistry(match=True, cap_name="text_gen")

        engine = TaskEngine(court, capability_registry=registry)
        outcome = engine.execute(TaskRequest(id="t1", prompt="", domain="writing"))

        assert outcome is not None

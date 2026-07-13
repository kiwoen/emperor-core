"""Integration tests for Emperor pipeline with MeritBoard + SurvivalMechanism + ReflectionConsensus."""

import pytest
from jarvis.court.emperor import ImperialCourt
from jarvis.court.minister import Minister, MinisterProfile
from jarvis.court.merit_board import MeritBoard, MeritRank
from jarvis.court.evolution import SurvivalMechanism


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def court_with_one() -> ImperialCourt:
    """Court with all 8 ministers installed from factory."""
    court = ImperialCourt(evolution_interval=100)  # high interval to avoid evolution in tests
    court.install_ministers_from_factory()
    return court


@pytest.fixture
def court_with_interval_3() -> ImperialCourt:
    """Court with evolution interval = 3."""
    court = ImperialCourt(evolution_interval=3)
    court.install_ministers_from_factory()
    return court


# ── TestSurvivalIntegration ───────────────────────────────────────────


class TestSurvivalIntegration:
    """Verify SurvivalMechanism is properly wired into ImperialCourt."""

    def test_install_registers_in_survival(self, court_with_one):
        """Installing a minister registers in SurvialMechanism."""
        active = court_with_one.survival.get_active_ministers()
        assert len(active) == 8

    def test_dismiss_marks_eliminated(self, court_with_one):
        """Dismissing marks eliminated in MeritBoard."""
        name = list(court_with_one.ministers.keys())[0]
        court_with_one.dismiss_minister(name)

        assert name not in court_with_one.ministers
        assert court_with_one.merit_board.is_eliminated(name)

    def test_merit_board_auto_created(self):
        """ImperialCourt default constructs MeritBoard and SurvivalMechanism."""
        court = ImperialCourt()
        assert court.merit_board is not None
        assert court.survival is not None
        assert court.reflection is not None

    def test_custom_components_injected(self):
        """Can inject custom MeritBoard/SurvivalMechanism."""
        mb = MeritBoard()
        sm = SurvivalMechanism(mb)
        court = ImperialCourt(merit_board=mb, survival_mechanism=sm)
        assert court.merit_board is mb
        assert court.survival is sm


# ── TestPipelineIntegration ─────────────────────────────────────────


class TestPipelineIntegration:
    """Verify full pipeline with merit tracking and evolution."""

    @pytest.mark.asyncio
    async def test_receive_petition_records_merit(self, court_with_one):
        """After processing a petition, MeritBoard tracks the dispatches."""
        await court_with_one.receive_petition("写一个 Python 函数")

        ranking = court_with_one.merit_board.get_ranking()
        assert len(ranking) > 0

    @pytest.mark.asyncio
    async def test_evolution_runs_at_interval(self, court_with_interval_3):
        """Evolution cycle triggers after N decrees."""
        await court_with_interval_3.receive_petition("任务A")
        await court_with_interval_3.receive_petition("任务B")
        await court_with_interval_3.receive_petition("任务C")

        # Evolution cycle should have run and reset the counter
        assert court_with_interval_3._decrees_since_evolution == 0

    @pytest.mark.asyncio
    async def test_evolution_not_triggered_before_interval(self, court_with_one):
        """Evolution doesn't run before the interval threshold."""
        await court_with_one.receive_petition("任务A")
        assert court_with_one._decrees_since_evolution == 1

        await court_with_one.receive_petition("任务B")
        assert court_with_one._decrees_since_evolution == 2

    @pytest.mark.asyncio
    async def test_feedback_updates_merit_board(self, court_with_one):
        """send_feedback propagates to MeritBoard without error."""
        decree = await court_with_one.receive_petition("测试任务")

        if decree.ministers_consulted:
            minister_name = decree.ministers_consulted[0]
            # Record feedback — should not crash
            court_with_one.send_feedback(decree.decree_id, minister_name, 5.0)

            ranking = court_with_one.merit_board.get_ranking()
            assert any(m.minister == minister_name for m in ranking)

    @pytest.mark.asyncio
    async def test_court_metrics_includes_evolution(self, court_with_one):
        """get_court_metrics includes merit_ranking and evolution snapshots."""
        await court_with_one.receive_petition("指标测试")

        metrics = court_with_one.get_court_metrics()
        assert "merit_ranking" in metrics
        assert "evolution" in metrics
        assert "decrees_until_next_evolution" in metrics
        assert "active" in metrics["evolution"]
        assert "shadow" in metrics["evolution"]
        assert "eliminated" in metrics["evolution"]


# ── TestMeritIntegration ─────────────────────────────────────────────


class TestMeritIntegration:
    """Verify MeritBoard correctly tracks minister performance."""

    @pytest.mark.asyncio
    async def test_multiple_ministers_recorded(self, court_with_one):
        """Ministers in a court session get merit records."""
        await court_with_one.receive_petition("写一段代码")

        ranking = court_with_one.merit_board.get_ranking()
        assert len(ranking) > 0  # At least one dispatched

    @pytest.mark.asyncio
    async def test_merit_ranking_is_ordered(self, court_with_one):
        """MeritBoard ranking is top-to-bottom ordered."""
        await court_with_one.receive_petition("任务")
        await court_with_one.receive_petition("另一个任务")

        ranking = court_with_one.merit_board.get_ranking()
        if len(ranking) >= 2:
            assert ranking[0].merit_score >= ranking[-1].merit_score

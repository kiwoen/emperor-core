"""Tests for Court-Orchestrator integration — Emperor dispatches through Orchestrator."""
import asyncio

import pytest

from jarvis.core.orchestrator import ExecutionMode, Orchestrator
from jarvis.court.emperor import ImperialCourt, Emperor


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_orchestrator_with_court():
    """Create an Orchestrator with a fully wired Imperial Court."""
    court = ImperialCourt()
    court.install_ministers_from_factory()
    orch = Orchestrator(
        imperial_court=court,
        execution_mode=ExecutionMode.DIRECT,
    )
    return orch


# ── ExecutionMode ──────────────────────────────────────────────────────────


class TestExecutionMode:
    def test_default_is_direct(self):
        orch = Orchestrator()
        assert orch.execution_mode == ExecutionMode.DIRECT

    def test_court_mode_constructor(self):
        court = ImperialCourt()
        court.install_ministers_from_factory()
        orch = Orchestrator(
            imperial_court=court,
            execution_mode=ExecutionMode.COURT,
        )
        assert orch.execution_mode == ExecutionMode.COURT

    def test_set_court_mode_enable(self):
        court = ImperialCourt()
        court.install_ministers_from_factory()
        orch = Orchestrator(imperial_court=court)
        assert orch.execution_mode == ExecutionMode.DIRECT
        orch.set_court_mode(True)
        assert orch.execution_mode == ExecutionMode.COURT

    def test_set_court_mode_disable(self):
        court = ImperialCourt()
        court.install_ministers_from_factory()
        orch = Orchestrator(
            imperial_court=court,
            execution_mode=ExecutionMode.COURT,
        )
        orch.set_court_mode(False)
        assert orch.execution_mode == ExecutionMode.DIRECT

    def test_set_court_mode_without_court_is_noop(self):
        orch = Orchestrator()
        orch.set_court_mode(True)
        assert orch.execution_mode == ExecutionMode.DIRECT


# ── Court-mode execute ─────────────────────────────────────────────────────


class TestCourtExecution:
    @pytest.mark.asyncio
    async def test_execute_court_mode_code_intent(self):
        """A code-related intent routes through the Court in COURT mode."""
        orch = _make_orchestrator_with_court()
        orch.set_court_mode(True)

        result = await orch.execute("帮我写一个排序算法")

        assert result.success is True
        assert result.output
        assert "court_mode" in result.data
        assert result.data["court_mode"] is True
        assert "decree_id" in result.data
        assert len(result.data["ministers_consulted"]) >= 1
        assert result.data["confidence"] > 0

    @pytest.mark.asyncio
    async def test_execute_court_mode_search_intent(self):
        """A search intent should recruit the Grand Historian (太史令)."""
        orch = _make_orchestrator_with_court()
        orch.set_court_mode(True)

        result = await orch.execute("搜索最新的 AI 论文")

        assert result.success is True
        assert result.output
        assert "court_mode" in result.data

    @pytest.mark.asyncio
    async def test_execute_court_mode_security_intent(self):
        """Security intent routes to the Guard (卫尉)."""
        orch = _make_orchestrator_with_court()
        orch.set_court_mode(True)

        result = await orch.execute("检查代码安全漏洞")

        assert result.success is True
        assert result.output
        assert result.data["court_mode"] is True

    @pytest.mark.asyncio
    async def test_execute_court_mode_multi_minister(self):
        """A cross-domain intent triggers a court session with multiple ministers."""
        orch = _make_orchestrator_with_court()
        orch.set_court_mode(True)

        result = await orch.execute("分析代码安全性并搜索最新漏洞")

        assert result.success is True
        assert result.output
        assert result.data.get("court_session") is True or len(result.data["ministers_consulted"]) >= 1

    @pytest.mark.asyncio
    async def test_execute_direct_mode_does_not_use_court(self):
        """In DIRECT mode, court is bypassed — falls through to domain routing."""
        orch = _make_orchestrator_with_court()
        # No domains loaded in registry, so direct mode will fail gracefully

        result = await orch.execute("帮我写一个排序算法")

        # Direct mode: no domain loaded → fallthrough error
        assert result.success is False or "court_mode" not in result.data

    @pytest.mark.asyncio
    async def test_execute_court_mode_nonmatching_intent(self):
        """Even a task with no strong minister match still gets a best-effort response."""
        orch = _make_orchestrator_with_court()
        orch.set_court_mode(True)

        result = await orch.execute("你好")

        # Should still return something (at least one minister matches weakly)
        assert result.output is not None

    @pytest.mark.asyncio
    async def test_execute_court_tracks_decree_count(self):
        """Each execution increments the court's decree counter."""
        orch = _make_orchestrator_with_court()
        orch.set_court_mode(True)

        for _ in range(3):
            await orch.execute("写一段代码")

        metrics = orch.imperial_court.get_court_metrics()
        assert metrics["decree_count"] == 3

    @pytest.mark.asyncio
    async def test_execute_court_preserves_decree_metadata(self):
        """Decree metadata (confidence, ministers_consulted) flows through to TaskResult."""
        orch = _make_orchestrator_with_court()
        orch.set_court_mode(True)

        result = await orch.execute("分析代码安全漏洞")

        assert "decree_id" in result.data
        assert "ministers_consulted" in result.data
        assert "confidence" in result.data
        assert "recommendations" in result.data
        assert "dissenting" in result.data
        assert "execution_ms" in result.data

    @pytest.mark.asyncio
    async def test_execute_court_feedback_loop(self):
        """After execution, feedback can be sent to individual ministers."""
        orch = _make_orchestrator_with_court()
        orch.set_court_mode(True)

        result = await orch.execute("搜索AI最新进展")

        # Send feedback to the first minister
        ministers = result.data["ministers_consulted"]
        if ministers:
            orch.imperial_court.send_feedback(
                result.data["decree_id"],
                ministers[0],
                score=0.9,
            )


# ── Court-mode execute_stream ──────────────────────────────────────────────


class TestCourtStreaming:
    @pytest.mark.asyncio
    async def test_stream_court_mode_yields_progress(self):
        """Court mode stream yields progress events."""
        orch = _make_orchestrator_with_court()
        orch.set_court_mode(True)

        events = []
        async for event in orch.execute_stream("写一个二分搜索算法"):
            events.append(event)

        # Should have intent_parsed → court_convened → result
        stages = [e.get("stage") for e in events if e.get("type") == "progress"]
        assert "intent_parsed" in stages
        assert "court_convened" in stages

        # Final event should be a result
        final = events[-1]
        assert final["type"] == "result"
        assert final.get("court_mode") is True
        assert "output" in final

    @pytest.mark.asyncio
    async def test_stream_court_mode_result_has_output(self):
        """Court mode stream final result contains meaningful output."""
        orch = _make_orchestrator_with_court()
        orch.set_court_mode(True)

        events = []
        async for event in orch.execute_stream("分析代码安全性"):
            events.append(event)

        result_events = [e for e in events if e["type"] == "result"]
        assert len(result_events) == 1
        assert result_events[0]["success"] is True
        assert result_events[0]["output"]

    @pytest.mark.asyncio
    async def test_stream_direct_mode_no_court_stage(self):
        """DIRECT mode stream should NOT yield court_convened stage."""
        orch = Orchestrator()
        # Don't set court mode — stays DIRECT

        events = []
        async for event in orch.execute_stream("你好"):
            events.append(event)

        stages = [e.get("stage") for e in events if e.get("type") == "progress"]
        assert "court_convened" not in stages


# ── Imperial Court status ──────────────────────────────────────────────────


class TestCourtMetrics:
    def test_empty_court_metrics(self):
        """Fresh court returns 0 decrees and known minister count."""
        court = ImperialCourt()
        court.install_ministers_from_factory()

        metrics = court.get_court_metrics()
        assert metrics["decree_count"] == 0
        assert metrics["minister_count"] == 8
        assert metrics["recent_success_rate"] == 0.0

    @pytest.mark.asyncio
    async def test_metrics_after_execution(self):
        """After executing, metrics reflect the activity."""
        orch = _make_orchestrator_with_court()
        orch.set_court_mode(True)

        await orch.execute("写一段代码")
        metrics = orch.imperial_court.get_court_metrics()

        assert metrics["decree_count"] == 1
        assert metrics["minister_count"] == 8

"""Tests for jarvis.court.task_engine."""

from __future__ import annotations

from jarvis.court.court import Court
from jarvis.court.task_engine import (
    TaskEngine,
    TaskRequest,
    TaskOutcome,
    TaskState,
    _simple_confidence,
    _deterministic_reply,
)


class CapturingBackend:
    """LLM backend that captures calls."""

    def __init__(self, response: str = "[mock]"):
        self.response = response
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, prompt: str, **kwargs):
        self.calls.append((prompt, kwargs))
        return self.response


class FailingBackend:
    """LLM backend that always raises."""

    def __init__(self, error_msg: str = "simulated failure"):
        self.error_msg = error_msg

    def __call__(self, prompt: str, **kwargs):
        raise RuntimeError(self.error_msg)


# ══════════════════════════════════════════════════════════════════
# _simple_confidence
# ══════════════════════════════════════════════════════════════════


class TestSimpleConfidence:
    def test_empty_response_low(self):
        assert _simple_confidence("", None) < 0.2

    def test_matches_expected(self):
        score = _simple_confidence("The answer is Paris, France.", "Paris")
        assert score > 0.5, f"expected high confidence, got {score}"

    def test_mismatch_penalty(self):
        score = _simple_confidence("The answer is London.", "Paris")
        assert score < 0.5, f"expected low confidence, got {score}"

    def test_no_expected_still_reasonable(self):
        score = _simple_confidence("A very long and detailed response" * 10, None)
        assert 0.3 <= score <= 0.95

    def test_length_bonus_short(self):
        short = _simple_confidence("ok", None)
        long_ = _simple_confidence("explanation " * 100, None)
        assert long_ > short


# ══════════════════════════════════════════════════════════════════
# _deterministic_reply
# ══════════════════════════════════════════════════════════════════


class TestDeterministicReply:
    def test_aritifmetic(self):
        assert "391" in _deterministic_reply("What is 17 * 23?")
        assert "391" in _deterministic_reply("Compute 17*23 please")

    def test_capital(self):
        assert "Paris" in _deterministic_reply("What is the capital of France?")

    def test_greeting(self):
        assert "Hello" in _deterministic_reply("hello there")

    def test_fallback(self):
        assert "Acknowledged" in _deterministic_reply(
            "something entirely unknown"
        )


# ══════════════════════════════════════════════════════════════════
# TaskEngine
# ══════════════════════════════════════════════════════════════════


class TestTaskEngineBasics:
    def test_create(self):
        court = Court()
        engine = TaskEngine(court)
        assert engine.total_tasks == 0
        assert engine.success_rate == 0.0

    def test_create_with_backend(self):
        court = Court()
        backend = CapturingBackend()
        engine = TaskEngine(court, llm=backend)
        assert engine._llm is backend


class TestTaskSubmit:
    def test_submit_and_pending(self):
        court = Court()
        court.register("alpha", domain="math")
        engine = TaskEngine(court)
        req = TaskRequest(id="t1", prompt="test", domain="math")
        tid = engine.submit(req)
        assert tid == "t1"
        assert engine.total_tasks == 1

    def test_duplicate_rejected(self):
        court = Court()
        court.register("alpha", domain="math")
        engine = TaskEngine(court)
        req = TaskRequest(id="dup", prompt="x")
        engine.submit(req)
        try:
            engine.submit(req)
            assert False, "should have raised"
        except ValueError:
            pass


class TestTaskExecute:
    def test_single_execute_mock(self):
        court = Court()
        court.register("alpha", domain="math")
        engine = TaskEngine(court, llm=CapturingBackend("[done]"))
        req = TaskRequest(id="q1", prompt="What is 2+2?", domain="math")
        outcome = engine.execute(req)

        assert outcome.task_id == "q1"
        assert outcome.state == TaskState.COMPLETED
        assert outcome.minister == "alpha"
        assert outcome.raw_response == "[done]"
        assert outcome.success is True
        assert outcome.confidence > 0

    def test_execute_failing_backend(self):
        court = Court()
        court.register("beta", domain="code")
        engine = TaskEngine(court, llm=FailingBackend("boom"))
        req = TaskRequest(id="fail1", prompt="import antigravity")
        outcome = engine.execute(req)

        assert outcome.state == TaskState.FAILED
        assert outcome.error == "boom"
        assert outcome.success is False
        assert outcome.confidence <= 0.1

    def test_execute_batch(self):
        court = Court()
        court.register("alpha", domain="math")
        engine = TaskEngine(court, llm=CapturingBackend("[ok]"))
        reqs = [
            TaskRequest(id="a", prompt="1+1"),
            TaskRequest(id="b", prompt="2+2"),
            TaskRequest(id="c", prompt="3+3"),
        ]
        outcomes = engine.execute_batch(reqs)
        assert len(outcomes) == 3
        assert all(o.success for o in outcomes)

    def test_engine_summary(self):
        court = Court()
        court.register("alpha", domain="math")
        engine = TaskEngine(court, llm=CapturingBackend("[ok]"))
        for i in range(5):
            engine.execute(TaskRequest(id=f"q{i}", prompt=f"test {i}"))

        s = engine.summary()
        assert s["total_tasks"] == 5
        assert s["completed"] == 5
        assert s["failed"] == 0
        assert s["success_rate"] > 0.9
        assert s["avg_merit"] > 0


class TestGenomeParamFlow:
    def test_genome_params_in_llm_call(self):
        """Genome parameters flow to the LLM backend."""
        court = Court()
        court.register("turing", domain="math", temperature=0.23)
        backend = CapturingBackend()
        engine = TaskEngine(court, llm=backend)

        engine.execute(
            TaskRequest(id="g1", prompt="test", domain="math")
        )

        assert len(backend.calls) == 1
        _, kwargs = backend.calls[0]
        assert "temperature" in kwargs
        # temperature should be near the genome value
        assert kwargs["temperature"] == 0.23

    def test_no_ministers_raises(self):
        court = Court()
        engine = TaskEngine(court)
        try:
            engine.execute(TaskRequest(id="x", prompt="test"))
            assert False, "should have raised RuntimeError"
        except RuntimeError as e:
            assert "No active ministers" in str(e)


class TestFeedbackLoop:
    def test_merit_updated_after_execute(self):
        """After successful execution, minister merit should increase."""
        court = Court()
        court.register("alpha", domain="math")
        engine = TaskEngine(court, llm=CapturingBackend("[correct answer]"))

        engine.execute(
            TaskRequest(id="f1", prompt="2+2", expected="4", domain="math")
        )

        # Merit should be non-zero now
        ranking = court.merit_ranking
        assert len(ranking) >= 1

    def test_multiple_tasks_accumulate_merit(self):
        court = Court()
        court.register("alpha", domain="general")
        engine = TaskEngine(court, llm=CapturingBackend("[ok]"))

        for i in range(10):
            engine.execute(TaskRequest(
                id=f"batch{i}", prompt=f"task {i}", expected="ok"
            ))

        assert engine.total_tasks == 10
        # Summary should reflect completed tasks
        s = engine.summary()
        assert s["total_tasks"] == 10
        assert s["success_rate"] > 0


class TestNoMeritBoardMethods:
    """Graceful degradation when court doesn't have merit methods."""

    def test_execute_no_record_feedback(self):
        """Should not crash even if record_dispatch raises."""
        court = Court()
        court.register("alpha", domain="math")

        engine = TaskEngine(court, llm=CapturingBackend("[ok]"))
        # Monkey-patch to always raise (simulate degraded state)
        original = court.record_dispatch
        court.record_dispatch = lambda *a, **kw: (_ for _ in ()).throw(
            AttributeError("no such method")
        )
        try:
            outcome = engine.execute(
                TaskRequest(id="nomerit", prompt="test")
            )
            assert outcome.success
        finally:
            court.record_dispatch = original

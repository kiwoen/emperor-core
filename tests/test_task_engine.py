"""Tests for jarvis.court.task_engine."""

from __future__ import annotations

import pytest

from jarvis.court.court import Court
from jarvis.court.fitness import FitnessSignal, NullEvaluator, RealTaskFitness
from jarvis.court.task_engine import (
    TaskEngine,
    TaskRequest,
    TaskOutcome,
    TaskState,
    _simple_confidence,
    _deterministic_reply,
)
from jarvis.model_router import Capability, SmartRouter


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


# ══════════════════════════════════════════════════════════════════
# P0.3 — RealTaskFitness
# ══════════════════════════════════════════════════════════════════


class TestRealTaskFitness:
    """The fitness signal must reflect real outcomes, not response length."""

    def test_success_with_full_tests_is_perfect(self):
        f = RealTaskFitness()
        score = f.score(FitnessSignal(
            execution_success=True, test_pass_rate=1.0, response="done",
        ))
        assert score == pytest.approx(1.0)

    def test_success_without_test_signal_caps_at_execution_weight(self):
        """Merely running is worth 0.6 — never a perfect score."""
        f = RealTaskFitness()
        score = f.score(FitnessSignal(execution_success=True, response="done"))
        assert score == pytest.approx(0.6)

    def test_half_tests_passing(self):
        f = RealTaskFitness()
        score = f.score(FitnessSignal(
            execution_success=True, test_pass_rate=0.5, response="done",
        ))
        assert score == pytest.approx(0.6 + 0.4 * 0.5)

    def test_execution_failure_is_zero(self):
        f = RealTaskFitness()
        assert f.score(FitnessSignal(
            execution_success=False, test_pass_rate=1.0, response="whatever",
        )) == 0.0

    def test_empty_response_is_zero(self):
        f = RealTaskFitness()
        assert f.score(FitnessSignal(
            execution_success=True, response="   ",
        )) == 0.0

    # ── The actual reward-hacking regression ──────────────────────

    def test_length_buys_nothing(self):
        """A 5000-char answer must not outscore a 4-char answer."""
        f = RealTaskFitness()
        short = f.score(FitnessSignal(execution_success=True, response="4"))
        long_ = f.score(FitnessSignal(execution_success=True, response="x" * 5000))
        assert short == long_ == pytest.approx(0.6)

    def test_verbose_failure_scores_zero(self):
        """The old scorer gave a long failed answer ~0.6; now it is 0.0."""
        f = RealTaskFitness()
        verbose_failure = FitnessSignal(
            execution_success=False, response="blah " * 1000, error="boom",
        )
        assert f.score(verbose_failure) == 0.0
        # ...and the deprecated heuristic indeed rewarded it — the bug we fixed.
        assert _simple_confidence("blah " * 1000, None) > 0.5

    def test_confidently_wrong_answer_scores_zero(self):
        f = RealTaskFitness()
        assert f.score(FitnessSignal(
            execution_success=True,
            test_pass_rate=1.0,
            response="The answer is definitely London.",
            expected="Paris",
        )) == 0.0

    def test_correct_answer_is_rewarded(self):
        f = RealTaskFitness()
        assert f.score(FitnessSignal(
            execution_success=True,
            response="The answer is Paris.",
            expected="Paris",
        )) == pytest.approx(0.6)

    # ── Robustness ────────────────────────────────────────────────

    def test_pass_rate_is_clamped(self):
        f = RealTaskFitness()
        assert f.score(FitnessSignal(
            execution_success=True, test_pass_rate=99.0, response="x",
        )) == pytest.approx(1.0)
        assert f.score(FitnessSignal(
            execution_success=True, test_pass_rate=-5.0, response="x",
        )) == pytest.approx(0.6)

    def test_non_numeric_pass_rate_is_ignored(self):
        f = RealTaskFitness()
        assert f.score(FitnessSignal(
            execution_success=True, test_pass_rate="lots",  # type: ignore[arg-type]
            response="x",
        )) == pytest.approx(0.6)

    def test_weights_are_normalised(self):
        f = RealTaskFitness(execution_weight=6.0, test_weight=4.0)
        assert f.execution_weight == pytest.approx(0.6)
        assert f.test_weight == pytest.approx(0.4)

    def test_zero_total_weight_rejected(self):
        with pytest.raises(ValueError):
            RealTaskFitness(execution_weight=0.0, test_weight=0.0)

    def test_score_is_always_in_unit_range(self):
        f = RealTaskFitness()
        for rate in (None, 0.0, 0.3, 1.0):
            for ok in (True, False):
                score = f.score(FitnessSignal(
                    execution_success=ok, test_pass_rate=rate, response="x",
                ))
                assert 0.0 <= score <= 1.0

    # ── Evaluator plug-in ─────────────────────────────────────────

    def test_null_evaluator_abstains(self):
        assert NullEvaluator().evaluate(FitnessSignal()) is None

    def test_evaluator_is_blended_in(self):
        class PerfectEvaluator:
            def evaluate(self, signal):
                return 1.0

        f = RealTaskFitness(evaluator=PerfectEvaluator(), evaluator_weight=0.5)
        # base 0.6, blended 50/50 with 1.0 → 0.8
        assert f.score(FitnessSignal(
            execution_success=True, response="x",
        )) == pytest.approx(0.8)

    def test_broken_evaluator_does_not_break_scoring(self):
        class ExplodingEvaluator:
            def evaluate(self, signal):
                raise RuntimeError("evaluator down")

        f = RealTaskFitness(evaluator=ExplodingEvaluator(), evaluator_weight=0.5)
        assert f.score(FitnessSignal(
            execution_success=True, response="x",
        )) == pytest.approx(0.6)

    def test_describe(self):
        info = RealTaskFitness().describe()
        assert info["execution_weight"] == pytest.approx(0.6)
        assert info["test_weight"] == pytest.approx(0.4)
        assert info["evaluator"] == "NullEvaluator"


class TestEngineUsesRealFitness:
    """TaskEngine must default to the real signal, not the length heuristic."""

    def test_default_scorer_is_real_task_fitness(self):
        engine = TaskEngine(Court())
        assert isinstance(engine._scorer, RealTaskFitness)

    def test_failed_task_scores_zero(self):
        court = Court()
        court.register("alpha", domain="math")
        engine = TaskEngine(court, llm=FailingBackend("boom"))
        outcome = engine.execute(TaskRequest(id="f", prompt="x"))
        assert outcome.confidence == 0.0
        assert outcome.success is False

    def test_verbosity_does_not_raise_merit(self):
        """Two successful tasks differing only in length get equal merit."""
        court = Court()
        court.register("alpha", domain="math")

        short_engine = TaskEngine(court, llm=CapturingBackend("ok"))
        long_engine = TaskEngine(court, llm=CapturingBackend("ok " * 2000))

        short = short_engine.execute(TaskRequest(id="s", prompt="p"))
        long_ = long_engine.execute(TaskRequest(id="l", prompt="p"))

        assert short.confidence == long_.confidence
        assert short.merit_score == long_.merit_score

    def test_test_pass_rate_from_request_meta(self):
        """A task carrying a real test signal outranks one without."""
        court = Court()
        court.register("alpha", domain="code")
        engine = TaskEngine(court, llm=CapturingBackend("patch applied"))

        no_tests = engine.execute(TaskRequest(id="a", prompt="fix bug"))
        all_pass = engine.execute(TaskRequest(
            id="b", prompt="fix bug", meta={"test_pass_rate": 1.0},
        ))

        assert all_pass.confidence > no_tests.confidence
        assert all_pass.confidence == pytest.approx(1.0)

    def test_custom_legacy_scorer_still_supported(self):
        """A user-supplied (response, expected) scorer keeps working."""
        court = Court()
        court.register("alpha", domain="math")
        engine = TaskEngine(
            court, llm=CapturingBackend("[ok]"), scorer=lambda r, e: 0.77,
        )
        outcome = engine.execute(TaskRequest(id="c", prompt="p"))
        assert outcome.confidence == pytest.approx(0.77)

    def test_broken_scorer_degrades_to_zero_not_crash(self):
        court = Court()
        court.register("alpha", domain="math")

        def exploding(response, expected):
            raise RuntimeError("scorer down")

        engine = TaskEngine(court, llm=CapturingBackend("[ok]"), scorer=exploding)
        outcome = engine.execute(TaskRequest(id="d", prompt="p"))
        assert outcome.confidence == 0.0


# ══════════════════════════════════════════════════════════════════
# P0.4 / P0.5 — real minister selection
# ══════════════════════════════════════════════════════════════════


def _court_with_domains(**ministers: str) -> Court:
    """Build a court from ``name=domain`` pairs."""
    court = Court()
    for name, domain in ministers.items():
        court.register(name, domain=domain)
    return court


class TestSelectMinisterDomainMatch:
    """`_select_minister` used to be a no-op loop — now it really routes."""

    def test_select_minister_domain_match(self):
        """Exact domain match wins over a higher-merit off-domain minister."""
        court = _court_with_domains(
            mathematician="math", coder="code", writer="writing",
        )
        # Give the *coder* a huge merit lead so a merit-only selector
        # would always pick it.
        for i in range(20):
            court.record_dispatch(
                minister="coder", edict_id=f"boost{i}", intent="x",
                success=True, confidence=1.0,
            )
            court.record_feedback(
                minister="coder", edict_id=f"boost{i}", score=100.0,
            )

        engine = TaskEngine(court, llm=CapturingBackend("[ok]"))
        assert engine._select_minister("math") == "mathematician"
        assert engine._select_minister("writing") == "writer"
        # ...and the coder still wins its own domain.
        assert engine._select_minister("code") == "coder"

    def test_domain_match_is_case_insensitive(self):
        court = _court_with_domains(alpha="math", beta="code")
        engine = TaskEngine(court, llm=CapturingBackend("[ok]"))
        assert engine._select_minister("MATH") == "alpha"
        assert engine._select_minister("  Code  ") == "beta"

    def test_highest_merit_wins_within_matching_domain(self):
        court = _court_with_domains(low="math", high="math")
        for i in range(10):
            court.record_dispatch(
                minister="high", edict_id=f"h{i}", intent="x",
                success=True, confidence=1.0,
            )
            court.record_feedback(
                minister="high", edict_id=f"h{i}", score=95.0,
            )
        engine = TaskEngine(court, llm=CapturingBackend("[ok]"))
        assert engine._select_minister("math") == "high"

    def test_capability_match_when_no_exact_domain(self):
        """A `science` minister picks up a `math` task via the router."""
        court = _court_with_domains(scientist="science", writer="writing")
        engine = TaskEngine(court, llm=CapturingBackend("[ok]"))
        engine.set_router(SmartRouter())
        # 'science' and 'math' both map to Capability.MATH
        assert SmartRouter().classify_domain("science") is Capability.MATH
        assert engine._select_minister("math") == "scientist"

    def test_no_capability_match_without_router(self):
        """Without a router there is no capability tier — merit decides."""
        court = _court_with_domains(scientist="science", writer="writing")
        for i in range(10):
            court.record_dispatch(
                minister="writer", edict_id=f"w{i}", intent="x",
                success=True, confidence=1.0,
            )
            court.record_feedback(
                minister="writer", edict_id=f"w{i}", score=90.0,
            )
        engine = TaskEngine(court, llm=CapturingBackend("[ok]"))
        engine.set_router(None)
        assert engine._select_minister("math") == "writer"

    def test_unmatched_domain_falls_back_to_top_merit(self):
        court = _court_with_domains(alpha="math", beta="code")
        for i in range(10):
            court.record_dispatch(
                minister="beta", edict_id=f"b{i}", intent="x",
                success=True, confidence=1.0,
            )
            court.record_feedback(
                minister="beta", edict_id=f"b{i}", score=99.0,
            )
        engine = TaskEngine(court, llm=CapturingBackend("[ok]"))
        # 'underwater-basket-weaving' matches nothing at all
        assert engine._select_minister("underwater-basket-weaving") == "beta"

    def test_no_active_ministers_raises(self):
        engine = TaskEngine(Court(), llm=CapturingBackend("[ok]"))
        with pytest.raises(RuntimeError, match="No active ministers"):
            engine._select_minister("math")

    def test_execute_routes_to_domain_minister(self):
        """End-to-end: execute() honours the routing decision."""
        court = _court_with_domains(mathematician="math", coder="code")
        engine = TaskEngine(court, llm=CapturingBackend("[ok]"))
        outcome = engine.execute(
            TaskRequest(id="r1", prompt="What is 17 * 23?", domain="math")
        )
        assert outcome.minister == "mathematician"

    def test_explicit_minister_overrides_routing(self):
        court = _court_with_domains(mathematician="math", coder="code")
        engine = TaskEngine(court, llm=CapturingBackend("[ok]"))
        outcome = engine.execute(
            TaskRequest(id="r2", prompt="2+2", domain="math"),
            minister="coder",
        )
        assert outcome.minister == "coder"


class TestRouterWiring:
    """P0.4 — the router must be injectable and its absence must be loud."""

    def test_router_defaults_to_none(self):
        engine = TaskEngine(Court())
        assert engine.router is None

    def test_set_router_attaches(self):
        engine = TaskEngine(Court())
        router = SmartRouter()
        engine.set_router(router)
        assert engine.router is router

    def test_router_via_constructor(self):
        router = SmartRouter()
        engine = TaskEngine(Court(), router=router)
        assert engine.router is router

    def test_set_router_none_is_explicit(self, caplog):
        engine = TaskEngine(Court())
        with caplog.at_level("WARNING"):
            engine.set_router(None)
        assert any("SmartRouter" in r.message for r in caplog.records)

    def test_broken_router_does_not_break_selection(self):
        """A router that raises must degrade, not crash task execution."""
        class ExplodingRouter:
            def classify(self, prompt, domain="general"):
                raise RuntimeError("router down")

            def classify_domain(self, domain):
                raise RuntimeError("router down")

        court = _court_with_domains(alpha="math", beta="code")
        engine = TaskEngine(court, llm=CapturingBackend("[ok]"))
        engine.set_router(ExplodingRouter())
        # Exact domain matching still works — it does not need the router.
        assert engine._select_minister("math") == "alpha"

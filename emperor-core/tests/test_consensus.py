"""Tests for jarvis.consensus — multi-agent debate & consensus strategies."""

from __future__ import annotations

from jarvis.consensus.strategies import (
    ConsensusResult,
    CritiqueResult,
    MinisterOutput,
    MajorityVote,
    WeightedVote,
    DebateRound,
    BestOfN,
    SynthesisConsensus,
)
from jarvis.consensus.engine import ConsensusEngine, ConsensusConfig


# ══════════════════════════════════════════════════════════════════
# Fixtures / helpers
# ══════════════════════════════════════════════════════════════════


def _make_outputs(
    answers: list[str],
    confidences: list[float] | None = None,
    merits: list[float] | None = None,
    prefix: str = "m",
) -> list[MinisterOutput]:
    """Build MinisterOutput list from answers."""
    n = len(answers)
    confs = confidences or [0.85] * n
    merts = merits or [60.0] * n
    return [
        MinisterOutput(
            minister=f"{prefix}{i}",
            answer=answers[i],
            reasoning=f"Reasoning for '{answers[i]}' by minister {i}",
            confidence=confs[i],
            merit_score=merts[i],
        )
        for i in range(n)
    ]


# ══════════════════════════════════════════════════════════════════
# MajorityVote
# ══════════════════════════════════════════════════════════════════


class TestMajorityVote:
    def test_clear_majority(self):
        outputs = _make_outputs(["A", "A", "A", "B", "C"])
        result = MajorityVote().resolve(outputs)
        assert result.final_answer == "A"
        assert result.votes == {"A": 3, "B": 1, "C": 1}
        assert result.strategy == "majority_vote"

    def test_tie_break_by_confidence(self):
        outputs = _make_outputs(
            ["A", "A", "B", "B", "C"],
            confidences=[0.9, 0.9, 0.6, 0.7, 0.5],
        )
        result = MajorityVote().resolve(outputs)
        assert result.final_answer == "A"  # A avg=0.9 > B avg=0.65

    def test_single_minister(self):
        outputs = _make_outputs(["X"])
        result = MajorityVote().resolve(outputs)
        assert result.final_answer == "X"

    def test_empty(self):
        result = MajorityVote().resolve([])
        assert result.final_answer == ""
        assert result.num_ministers == 0

    def test_unanimous(self):
        outputs = _make_outputs(["Paris", "Paris", "Paris", "Paris", "Paris"])
        result = MajorityVote().resolve(outputs)
        assert result.final_answer == "Paris"
        assert result.votes == {"Paris": 5}


# ══════════════════════════════════════════════════════════════════
# WeightedVote
# ══════════════════════════════════════════════════════════════════


class TestWeightedVote:
    def test_merit_weighted_vote(self):
        # m0 has low merit but same answer as m1, m1+m2 disagree but m2 has high merit
        outputs = _make_outputs(
            ["A", "B", "B"],
            confidences=[0.8, 0.8, 0.8],
            merits=[10.0, 90.0, 90.0],
        )
        result = WeightedVote().resolve(outputs)
        assert result.final_answer == "B"  # B gets weighted votes from m1+m2 (~1.71 vs ~0.64)

    def test_low_merit_gets_outvoted(self):
        outputs = _make_outputs(
            ["X", "X", "Y", "Y", "Y"],
            confidences=[0.5, 0.6, 0.9, 0.9, 0.9],
            merits=[10.0, 10.0, 80.0, 80.0, 80.0],
        )
        result = WeightedVote().resolve(outputs)
        assert result.final_answer == "Y"

    def test_weights_are_normalized(self):
        outputs = _make_outputs(["A", "B"])
        result = WeightedVote().resolve(outputs)
        # scores dict values must sum to 1.0
        total = sum(result.scores.values())
        assert abs(total - 1.0) < 0.01 if result.scores else True


# ══════════════════════════════════════════════════════════════════
# DebateRound
# ══════════════════════════════════════════════════════════════════


class TestDebateRound:
    def test_basic_debate_no_revision(self):
        outputs = _make_outputs(["A", "A", "B", "C"])
        result = DebateRound(rounds=2).resolve(outputs)
        assert result.final_answer == "A"
        assert result.num_rounds == 2
        assert len(result.debate_log) == 2

    def test_debate_with_revision(self):
        outputs = _make_outputs(["X", "X", "Y", "Z"])

        def rev_cb(minister_name, own_answer, all_outputs, critiques):
            answers = [o.answer for o in all_outputs]
            most_common = max(set(answers), key=answers.count)
            if own_answer != most_common:
                return most_common  # conform to majority
            return own_answer

        result = DebateRound(rounds=2, revision_callback=rev_cb).resolve(outputs)
        assert result.num_rounds == 2
        # After revision, all should converge to "X"
        assert len(result.debate_log) >= 2

    def test_debate_log_structure(self):
        outputs = _make_outputs(["A", "A", "B"])
        result = DebateRound(rounds=1).resolve(outputs)
        # debate_log[0] has round=1 and answers dict
        log = result.debate_log
        assert len(log) == 1
        assert log[0]["round"] == 1
        assert "answers" in log[0]
        assert len(log[0]["answers"]) == 3

    def test_debate_preserves_critiques(self):
        outputs = _make_outputs(["A", "B", "A"])
        critiques = [
            CritiqueResult("m0", "m1", 0.75, [], [], ""),
            CritiqueResult("m1", "m0", 0.80, [], [], ""),
        ]
        result = DebateRound(rounds=1).resolve(outputs, critiques=critiques)
        assert result.strategy == "debate_round"


# ══════════════════════════════════════════════════════════════════
# BestOfN
# ══════════════════════════════════════════════════════════════════


class TestBestOfN:
    def test_picks_highest_confidence(self):
        outputs = _make_outputs(
            ["A", "B", "C"],
            confidences=[0.92, 0.88, 0.75],
        )
        result = BestOfN().resolve(outputs)
        assert result.final_answer == "A"
        assert result.metadata["selected_minister"] == "m0"

    def test_fallback_when_below_threshold(self):
        outputs = _make_outputs(
            ["A", "A", "B", "C"],
            confidences=[0.40, 0.35, 0.30, 0.25],
        )
        result = BestOfN(fallback_threshold=0.5).resolve(outputs)
        # Falls back to majority -> "A"
        assert result.final_answer == "A"
        assert "fallback" in result.strategy

    def test_no_fallback_when_above_threshold(self):
        outputs = _make_outputs(
            ["X", "X", "Y", "Z"],
            confidences=[0.91, 0.89, 0.87, 0.85],
        )
        result = BestOfN(fallback_threshold=0.7).resolve(outputs)
        assert result.final_answer == "X"
        assert "fallback" not in result.strategy

    def test_empty(self):
        result = BestOfN().resolve([])
        assert result.final_answer == ""
        assert result.num_ministers == 0


# ══════════════════════════════════════════════════════════════════
# SynthesisConsensus
# ══════════════════════════════════════════════════════════════════


class TestSynthesisConsensus:
    def test_naive_synthesis(self):
        outputs = _make_outputs(["Paris", "Paris", "London"])
        result = SynthesisConsensus().resolve(outputs)
        assert "[合成结论" in result.final_answer
        assert "3位大臣" in result.final_answer or "3" in result.final_answer

    def test_with_llm_callback(self):
        outputs = _make_outputs(["A", "B"])

        def fake_llm(prompt: str) -> str:
            return "Synthesized: The consensus is C"

        result = SynthesisConsensus(llm_callback=fake_llm).resolve(outputs)
        assert result.final_answer == "Synthesized: The consensus is C"
        assert result.metadata["llm_used"] is True

    def test_includes_critiques_in_prompt(self):
        outputs = _make_outputs(["X", "Y"])
        critiques = [
            CritiqueResult("m0", "m1", 0.6, ["good"], ["bad"], "summary"),
        ]

        captured: list[str] = []

        def fake_llm(prompt: str) -> str:
            captured.append(prompt)
            return "Final answer"

        SynthesisConsensus(llm_callback=fake_llm).resolve(outputs, critiques=critiques)
        assert len(captured) == 1
        assert "交叉评审" in captured[0] or "critique" in captured[0].lower()
        assert "X" in captured[0]
        assert "Y" in captured[0]

    def test_empty(self):
        result = SynthesisConsensus().resolve([])
        assert result.final_answer == ""


# ══════════════════════════════════════════════════════════════════
# ConsensusEngine
# ══════════════════════════════════════════════════════════════════


class TestConsensusEngine:
    def test_full_deliberation_pipeline(self):
        engine = ConsensusEngine(config=ConsensusConfig(num_ministers=3))

        def executor(minister_name: str, task: str) -> MinisterOutput:
            answers = {"m0": "Yes", "m1": "Yes", "m2": "No"}
            return MinisterOutput(
                minister=minister_name,
                answer=answers.get(minister_name, "Maybe"),
                reasoning=f"{minister_name} thinks about {task}",
                confidence=0.85,
                merit_score=60.0,
            )

        result = engine.deliberate(
            "Is this task solvable?",
            ministers=["m0", "m1", "m2"],
            executor=executor,
            strategy=MajorityVote(),
        )
        assert result.final_answer == "Yes"
        assert result.num_ministers == 3
        assert len(result.critiques) == 6  # 3 * 2 cross-critiques

    def test_cross_critique_generated(self):
        engine = ConsensusEngine(config=ConsensusConfig(num_ministers=2))

        def executor(minister_name: str, task: str) -> MinisterOutput:
            return MinisterOutput(
                minister=minister_name,
                answer="Option A",
                reasoning="Logical analysis",
                confidence=0.9,
            )

        result = engine.deliberate(
            "Choose an option",
            ministers=["m0", "m1"],
            executor=executor,
        )
        # 2 ministers, each critiques the other → 2 critiques
        assert len(result.critiques) == 2
        for c in result.critiques:
            assert isinstance(c, CritiqueResult)
            assert c.critic != c.target
            assert 0.0 <= c.score <= 1.0

    def test_no_critique_when_disabled(self):
        engine = ConsensusEngine(
            config=ConsensusConfig(num_ministers=2, require_critique=False),
        )

        def executor(minister_name: str, task: str) -> MinisterOutput:
            return MinisterOutput(
                minister=minister_name,
                answer="Answer",
                reasoning="...",
            )

        result = engine.deliberate(
            "test",
            ministers=["m0", "m1"],
            executor=executor,
        )
        assert result.critiques == []

    def test_raises_on_fewer_than_2_ministers(self):
        engine = ConsensusEngine()
        try:
            engine.deliberate(
                "test",
                ministers=["m0"],
                executor=lambda n, t: MinisterOutput(n, "A"),
            )
            assert False, "Should have raised"
        except ValueError as e:
            assert "2 ministers" in str(e)

    def test_costume_strategy_injected(self):
        engine = ConsensusEngine(config=ConsensusConfig(num_ministers=2))

        def executor(minister_name: str, task: str) -> MinisterOutput:
            answers = {"m0": "A", "m1": "B"}
            return MinisterOutput(
                minister=minister_name,
                answer=answers[minister_name],
                confidence=0.7,
            )

        result = engine.deliberate(
            "test",
            ministers=["m0", "m1"],
            executor=executor,
            strategy=WeightedVote(),
        )
        assert result.strategy == "weighted_vote"

    def test_merit_score_lookup(self):
        from jarvis.court.court import Court

        court = Court()
        court.register("alpha")
        court.register("beta")
        court.register("gamma")

        engine = ConsensusEngine(
            court=court,
            config=ConsensusConfig(num_ministers=2),
        )

        def executor(minister_name: str, task: str) -> MinisterOutput:
            return MinisterOutput(
                minister=minister_name,
                answer="Yes",
                reasoning="OK",
            )

        result = engine.deliberate(
            "test",
            ministers=["alpha", "beta"],
            executor=executor,
        )
        assert result.num_ministers == 2

    def test_auto_select_from_court(self):
        from jarvis.court.court import Court

        court = Court()
        court.register("m1", domain="general")
        court.register("m2", domain="general")
        court.register("m3", domain="general")

        engine = ConsensusEngine(
            court=court,
            config=ConsensusConfig(num_ministers=2),
        )

        # Without explicit ministers, should auto-select from court
        selected = engine._select_ministers()
        assert len(selected) == 2
        assert all(m in ["m1", "m2", "m3"] for m in selected)

    def test_critique_evaluator_callback(self):
        engine = ConsensusEngine(config=ConsensusConfig(num_ministers=2))

        def evaluator(critic: MinisterOutput, target: MinisterOutput):
            return CritiqueResult(
                critic=critic.minister,
                target=target.minister,
                score=0.99,
                strengths=["Excellent"],
                weaknesses=[],
                summary="Good",
            )

        engine.set_critique_evaluator(evaluator)

        def executor(minister_name: str, task: str) -> MinisterOutput:
            return MinisterOutput(minister_name, "A", confidence=0.8)

        result = engine.deliberate(
            "test",
            ministers=["m0", "m1"],
            executor=executor,
        )
        assert len(result.critiques) == 2
        assert all(c.score == 0.99 for c in result.critiques)


# ══════════════════════════════════════════════════════════════════
# ConsensusResult dataclass
# ══════════════════════════════════════════════════════════════════


class TestConsensusResult:
    def test_defaults(self):
        result = ConsensusResult(
            final_answer="X",
            strategy="test",
            num_ministers=3,
            num_rounds=1,
            confidence=0.5,
        )
        assert result.votes == {}
        assert result.scores == {}
        assert result.critiques == []
        assert result.debate_log == []
        assert result.metadata == {}

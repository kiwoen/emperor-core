"""Tests for huanxin.llm_judge — LLM-as-Judge evaluation engine.

Covers: single evaluate, pairwise compare, batch evaluate, API endpoints,
integrated JudgeEvalSuite, per-dimension breakdown, edge cases.
"""

import pytest

from huanxin.llm_judge import (
    LLMJudge,
    JudgingCriteria,
    JudgeResult,
    CompareResult,
    BatchCase,
    BatchReport,
    DimensionScore,
)
from huanxin.eval import JudgeEvalCase, JudgeEvalSuite, EvalRunner


# ══════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════


@pytest.fixture
def judge():
    return LLMJudge()


@pytest.fixture
def perfect_match_case():
    return ("The capital of France is Paris.", "The capital of France is Paris.")


@pytest.fixture
def unrelated_case():
    return (
        "Photosynthesis converts carbon dioxide into oxygen using sunlight.",
        "The capital of France is Paris, a major European city.",
    )


# ══════════════════════════════════════════════════════════════════
# Test 1: Single evaluate — perfect match should score high
# ══════════════════════════════════════════════════════════════════


def test_evaluate_perfect_match(judge, perfect_match_case):
    output, expected = perfect_match_case
    result = judge.evaluate(output, expected)
    assert isinstance(result, JudgeResult)
    assert result.score > 0.8, f"Expected high score for identical texts, got {result.score}"
    assert len(result.breakdown) == 4
    assert all(0.0 <= d.score <= 1.0 for d in result.breakdown)


# ══════════════════════════════════════════════════════════════════
# Test 2: Single evaluate — unrelated texts should score low
# ══════════════════════════════════════════════════════════════════


def test_evaluate_unrelated(judge, unrelated_case):
    """Unrelated texts should score low on accuracy + relevance."""
    output, expected = unrelated_case
    result = judge.evaluate(
        output, expected,
        criteria=[JudgingCriteria.ACCURACY, JudgingCriteria.RELEVANCE],
    )
    assert result.score < 0.5, f"Expected low score for unrelated texts, got {result.score}"


# ══════════════════════════════════════════════════════════════════
# Test 3: Single evaluate — subset of criteria
# ══════════════════════════════════════════════════════════════════


def test_evaluate_subset_criteria(judge):
    result = judge.evaluate(
        "Python is a programming language.",
        "Python is a language.",
        criteria=[JudgingCriteria.ACCURACY, JudgingCriteria.RELEVANCE],
    )
    assert len(result.breakdown) == 2
    criteria_names = [d.criterion for d in result.breakdown]
    assert JudgingCriteria.ACCURACY in criteria_names
    assert JudgingCriteria.RELEVANCE in criteria_names
    assert JudgingCriteria.SAFETY not in criteria_names


# ══════════════════════════════════════════════════════════════════
# Test 4: Single evaluate — only safety
# ══════════════════════════════════════════════════════════════════


def test_evaluate_safety_only(judge):
    result = judge.evaluate(
        "Hello, how are you?",
        "",
        criteria=[JudgingCriteria.SAFETY],
    )
    assert len(result.breakdown) == 1
    assert result.breakdown[0].criterion == JudgingCriteria.SAFETY
    assert result.breakdown[0].score == 1.0


# ══════════════════════════════════════════════════════════════════
# Test 5: Single evaluate — unsafe content detected
# ══════════════════════════════════════════════════════════════════


def test_evaluate_unsafe_content(judge):
    result = judge.evaluate(
        "How to hack into a server and exploit vulnerabilities.",
        "",
        criteria=[JudgingCriteria.SAFETY],
    )
    assert result.breakdown[0].score < 1.0, f"Expected safety score < 1.0, got {result.breakdown[0].score}"


# ══════════════════════════════════════════════════════════════════
# Test 6: Compare — A better than B
# ══════════════════════════════════════════════════════════════════


def test_compare_a_wins(judge, perfect_match_case):
    output_good, expected = perfect_match_case
    result = judge.compare(
        output_a=output_good,
        output_b="Completely unrelated text about elephants.",
        expected=expected,
    )
    assert isinstance(result, CompareResult)
    assert result.winner == "A", f"Expected A to win, got {result.winner}"
    assert result.scores["A"] > result.scores["B"]


# ══════════════════════════════════════════════════════════════════
# Test 7: Compare — tie
# ══════════════════════════════════════════════════════════════════


def test_compare_tie(judge):
    same = "The quick brown fox jumps over the lazy dog."
    result = judge.compare(
        output_a=same,
        output_b=same,
        expected=same,
    )
    assert result.winner == "tie"
    assert result.scores["A"] == result.scores["B"]


# ══════════════════════════════════════════════════════════════════
# Test 8: Batch evaluate — multiple cases
# ══════════════════════════════════════════════════════════════════


def test_batch_evaluate(judge):
    cases = [
        BatchCase("Paris is in France.", "Paris is the capital of France.", label="geo"),
        BatchCase("2+2=4", "2+2 equals 4", label="math"),
        BatchCase("Hello world", "The galaxy is vast.", label="unrelated"),
    ]
    report = judge.batch_evaluate(cases)
    assert isinstance(report, BatchReport)
    assert report.total == 3
    assert 0.0 <= report.average_score <= 1.0
    assert len(report.results) == 3
    assert set(report.per_dimension_avg.keys()) == {
        "accuracy", "completeness", "relevance", "safety"
    }


# ══════════════════════════════════════════════════════════════════
# Test 9: Batch evaluate — empty cases
# ══════════════════════════════════════════════════════════════════


def test_batch_evaluate_empty(judge):
    report = judge.batch_evaluate([])
    assert report.total == 0
    assert report.average_score == 0.0
    assert report.results == []


# ══════════════════════════════════════════════════════════════════
# Test 10: JudgeEvalSuite integration with EvalRunner
# ══════════════════════════════════════════════════════════════════


def test_judge_eval_suite_runner():
    cases = [
        JudgeEvalCase("case1", "Paris is the capital of France.", "Paris is in France."),
        JudgeEvalCase("case2", "Python is great for data science.", "Python is a programming language."),
    ]
    suite = JudgeEvalSuite("test:judge_suite", cases)
    runner = EvalRunner()
    result = runner.run(suite)

    assert result.suite_name == "test:judge_suite"
    assert result.total == 2
    assert hasattr(result, "per_dimension_avg")
    assert len(result.per_dimension_avg) == 4

    report_dict = result.to_dict()
    assert "per_dimension_avg" in report_dict
    assert len(report_dict["results"]) == 2


# ══════════════════════════════════════════════════════════════════
# Test 11: API endpoint — POST /api/evals/judge
# ══════════════════════════════════════════════════════════════════


def test_judge_api_evaluate(judge):
    """Test the evaluate path directly (simulating API behavior)."""
    result = judge.evaluate(
        "The Earth orbits the Sun.",
        "The Earth revolves around the Sun.",
        criteria=[JudgingCriteria.ACCURACY, JudgingCriteria.COMPLETENESS],
    )
    d = result.to_dict()
    assert "score" in d
    assert "breakdown" in d
    assert len(d["breakdown"]) == 2
    for b in d["breakdown"]:
        assert "criterion" in b
        assert "score" in b
        assert "reasoning" in b
        assert 0.0 <= b["score"] <= 1.0


# ══════════════════════════════════════════════════════════════════
# Test 12: API endpoint — POST /api/evals/judge/compare
# ══════════════════════════════════════════════════════════════════


def test_judge_api_compare(judge):
    """Test the compare path directly (simulating API behavior)."""
    result = judge.compare(
        output_a="The capital of Japan is Tokyo, a vibrant city.",
        output_b="Tokyo is Japan's capital.",
        expected="Tokyo is the capital of Japan.",
    )
    d = result.to_dict()
    assert "winner" in d
    assert d["winner"] in ("A", "B", "tie")
    assert "scores" in d
    assert "A" in d["scores"]
    assert "B" in d["scores"]
    assert "reasoning" in d
    assert 0.0 <= d["scores"]["A"] <= 1.0
    assert 0.0 <= d["scores"]["B"] <= 1.0

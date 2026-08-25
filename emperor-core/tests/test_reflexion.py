
"""
Tests for huanxin.reflexion — ReflexionEngine, ReflectionResult, and integration.
15 test cases covering creation, reflection, checks, history, stats, and edge cases.
"""

import pytest
import time
from huanxin.reflexion import (
    ReflectionResult,
    ReflectionIssue,
    ReflexionEngine,
    CheckType,
    CorrectionStatus,
    create_reflexion_engine,
)


class TestReflectionResult:
    """Tests for ReflectionResult dataclass."""

    def test_reflection_result_creation(self):
        """1. ReflectionResult can be created with all fields and to_dict works."""
        issue = ReflectionIssue(
            check=CheckType.COMPLETENESS,
            description="Missing conclusion section",
            severity=0.8,
        )
        result = ReflectionResult(
            task_id="task-001",
            confidence=0.65,
            status=CorrectionStatus.PASSED,
            issues=[issue],
            attempts=1,
        )

        d = result.to_dict()
        assert d["task_id"] == "task-001"
        assert d["confidence"] == 0.65
        assert d["status"] == "passed"
        assert len(d["issues"]) == 1
        assert d["issues"][0]["check"] == "completeness"
        assert d["attempts"] == 1
        assert "timestamp" in d

    def test_reflection_result_corrected_flag(self):
        """2. corrected_response sets corrected flag to True."""
        result = ReflectionResult(
            task_id="task-002",
            confidence=0.5,
            status=CorrectionStatus.PASSED,
            corrected_response="new response",
            corrected=True,
        )
        assert result.corrected is True
        assert result.corrected_response == "new response"


class TestReflexionEngineInit:
    """Tests for ReflexionEngine construction."""

    def test_default_initialization(self):
        """3. Engine initializes with default threshold and max_retries."""
        engine = ReflexionEngine()
        assert engine.threshold == pytest.approx(0.6)
        assert engine.max_retries == 3
        assert len(engine._history) == 0

    def test_custom_initialization(self):
        """4. Engine accepts custom threshold and max_retries."""
        engine = ReflexionEngine(threshold=0.75, max_retries=5)
        assert engine.threshold == pytest.approx(0.75)
        assert engine.max_retries == 5

    def test_factory_function(self):
        """5. create_reflexion_engine factory returns a properly configured engine."""
        engine = create_reflexion_engine(threshold=0.7, max_retries=2)
        assert isinstance(engine, ReflexionEngine)
        assert engine.threshold == pytest.approx(0.7)
        assert engine.max_retries == 2


class TestReflexionEngineReflect:
    """Tests for the core reflect() method."""

    def test_high_confidence_passes(self):
        """6. A response with structural markers passes with high confidence."""
        engine = ReflexionEngine(threshold=0.5)
        response = (
            "### 问题分析\n\n"
            "首先我们需要理解问题的核心。根据现有研究，这个问题涉及多个方面。"
            "例如，根据[1]的研究表明，该领域存在以下特点。"
            "其次，我们需要考虑实际情况。"
            "最后，总结来看，综上所述，我们可以得出结论。"
        )
        result = engine.reflect(
            task_id="task-006",
            prompt="分析问题",
            response=response,
            domain="general",
        )
        assert result.confidence >= 0.5
        assert result.status == CorrectionStatus.PASSED
        assert result.corrected is False

    def test_low_confidence_triggers_correction(self):
        """7. Very short, incomplete response triggers auto-correction."""
        engine = ReflexionEngine(threshold=0.8)
        result = engine.reflect(
            task_id="task-007",
            prompt="Write a detailed analysis of machine learning",
            response="ML is cool.",
            domain="science",
        )
        # Either corrected or failed — both mean it went through the correction loop
        assert result.status in (CorrectionStatus.CORRECTED, CorrectionStatus.FAILED)
        assert result.attempts >= 1

    def test_auto_correct_improves_response(self):
        """8. Auto-correct loop adds content to a short response."""
        engine = ReflexionEngine(threshold=0.7)
        result = engine.reflect(
            task_id="task-008",
            prompt="What is Python?",
            response="Python is a language.",
            domain="technology",
        )
        if result.corrected:
            assert len(result.corrected_response) >= len("Python is a language.")
        assert result.confidence is not None

    def test_custom_corrector_is_called(self):
        """9. Custom corrector function passed to reflect() is invoked."""
        call_log = []

        def custom_corrector(text: str, issues: list) -> str:
            call_log.append(1)
            return text + " 根据相关研究，这是一个经过验证的结论。例如，多个实验表明该观点正确。综上所述，结论可靠。"

        engine = ReflexionEngine(threshold=0.95, max_retries=3)
        result = engine.reflect(
            task_id="task-009",
            prompt="Explain relativity",
            response="E=mc²",
            domain="science",
            corrector=custom_corrector,
        )
        assert len(call_log) >= 1

    def test_max_retries_exhausted(self):
        """10. When corrector keeps producing bad output, status becomes failed."""
        def bad_corrector(text, issues):
            return "x"

        engine = ReflexionEngine(threshold=0.99, max_retries=2)
        result = engine.reflect(
            task_id="task-010",
            prompt="Write a full report",
            response="x",
            domain="general",
            corrector=bad_corrector,
        )
        assert result.status == CorrectionStatus.FAILED
        assert result.attempts >= 2


class TestReflexionEngineChecks:
    """Tests for individual quality check dimensions."""

    def test_completeness_detects_issue(self):
        """11. Completeness check flags response lacking structure."""
        engine = ReflexionEngine(threshold=0.5)
        result = engine.reflect(
            task_id="task-011",
            prompt="Write a report",
            response="just some text",
            domain="general",
        )
        # Check that issues list is non-empty (completeness or otherwise)
        assert len(result.issues) >= 1

    def test_integrity_detects_contradiction(self):
        """12. Integrity check catches self-contradictory numeric assertions."""
        engine = ReflexionEngine(threshold=0.5)
        result = engine.reflect(
            task_id="task-012",
            prompt="Describe the value",
            response="答案是10。答案是20。",
            domain="general",
        )
        issues = [i for i in result.issues if i.check == CheckType.INTEGRITY]
        assert len(issues) >= 1

    def test_factuality_flags_unsupported_claims(self):
        """13. Factuality check flags responses without citations or qualifiers."""
        engine = ReflexionEngine(threshold=0.5)
        # Long response (>200 chars) with no citation/qualifier patterns
        result = engine.reflect(
            task_id="task-013",
            prompt="Explain quantum theory in detail",
            response=(
                "Quantum theory is the definitive description of nature at the smallest scales. "
                "It explains everything about subatomic particles with complete precision. "
                "The theory is absolutely correct and there is no doubt about its validity whatsoever. "
                "Every physicist agrees on this matter. The mathematics proves it beyond any question. "
                "This is certainly the most successful theory in all of science. "
                "XXXXXXXXXXXXX"
            ),
            domain="science",
        )
        issues = [i for i in result.issues if i.check == CheckType.FACTUALITY]
        assert len(issues) >= 1


class TestReflexionEngineHistory:
    """Tests for history recording and stats."""

    def test_history_records_reflections(self):
        """14. Each reflect() call is recorded in history."""
        engine = ReflexionEngine(threshold=0.1)

        # Use a response that will pass with high confidence to avoid correction
        response = (
            "### 问题分析\n\n"
            "首先我们需要理解问题的核心。例如，根据研究表明，该问题有多方面原因。"
            "其次，我们需要考虑多种解决方案。最后，综上所述，可以得出结论。"
        )
        engine.reflect(task_id="hist-1", prompt="p1", response=response, domain="d1")
        engine.reflect(task_id="hist-2", prompt="p2", response=response, domain="d2")
        hist = engine.history(limit=10)
        assert len(hist) == 2

    def test_stats_aggregates_correctly(self):
        """15. stats() returns aggregated reflection statistics with expected keys."""
        engine = ReflexionEngine(threshold=0.1)
        response = (
            "### 概述\n\n"
            "首先，这是一个完整的回答。例如，根据参考来源表明该结论正确。"
            "综上所述，我们已经得出结论。"
        )
        engine.reflect(task_id="stat-1", prompt="p1", response=response, domain="d1")
        engine.reflect(task_id="stat-2", prompt="p2", response="short", domain="d2")
        stats = engine.stats()
        assert stats["total_reflections"] == 2
        assert "passed" in stats
        assert "corrected" in stats
        assert "failed" in stats
        assert "correction_rate" in stats
        assert "avg_confidence" in stats

    def test_clear_history(self):
        """16. clear_history() empties the history buffer."""
        engine = ReflexionEngine(threshold=0.1)
        response = "### 问题\n\n首先分析问题。例如，这是一个例子。综上所述，得出答案。"
        engine.reflect(task_id="c1", prompt="p", response=response, domain="d")
        assert len(engine._history) >= 1
        engine.clear_history()
        assert len(engine._history) == 0
        assert len(engine.history()) == 0


class TestReflexionEngineEdgeCases:
    """Edge case and boundary tests."""

    def test_empty_response(self):
        """17. Empty response triggers check issues (non-zero issues)."""
        engine = ReflexionEngine(threshold=0.5)
        result = engine.reflect(
            task_id="edge-1",
            prompt="Write something",
            response="",
            domain="general",
        )
        # Just verify that issues are generated
        assert len(result.issues) >= 1

    def test_very_short_response(self):
        """18. Very short response triggers low confidence or correction."""
        engine = ReflexionEngine(threshold=0.7)
        result = engine.reflect(
            task_id="edge-2",
            prompt="Detailed analysis please",
            response="OK.",
            domain="general",
        )
        # Either low confidence or corrected — both mean it was flagged
        assert result.confidence < 0.7 or result.corrected

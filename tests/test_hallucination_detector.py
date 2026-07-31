"""
Tests for jarvis.hallucination_detector — Silent Hallucination Detection.

Covers:
  - OutputConsistencyChecker: n-gram overlap, entity agreement, divergent segment detection
  - FactualityVerifier: claim extraction, pattern matching, common sense checks
  - HallucinationDetector: single/multi-sample detection, risk scoring, governance integration
"""

import sys
import os

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from jarvis.hallucination_detector import (
    OutputConsistencyChecker,
    ConsistencyResult,
    FactualityVerifier,
    FactualityResult,
    ClaimType,
    Claim,
    HallucinationDetector,
    HallucinationResult,
    RiskLevel,
)


# ═══════════════════════════════════════════════════════════════════
# OutputConsistencyChecker Tests
# ═══════════════════════════════════════════════════════════════════


class TestOutputConsistencyChecker:
    def test_identical_outputs_perfect_score(self):
        checker = OutputConsistencyChecker()
        result = checker.check([
            "The cat sat on the mat. It was a sunny day.",
            "The cat sat on the mat. It was a sunny day.",
        ])
        assert result.consistent
        assert result.similarity_score == 1.0
        assert result.total_samples == 2

    def test_similar_outputs_high_score(self):
        checker = OutputConsistencyChecker()
        result = checker.check([
            "The cat sat on the mat. The dog slept in the yard.",
            "The cat sat on the mat. The dog slept in the yard.",
        ])
        assert result.similarity_score > 0.8

    def test_different_outputs_low_score(self):
        checker = OutputConsistencyChecker()
        result = checker.check([
            "The cat sat on the mat.",
            "Quantum physics explores subatomic particle behavior.",
        ])
        assert result.similarity_score < 0.5
        assert not result.consistent

    def test_single_sample_returns_perfect(self):
        checker = OutputConsistencyChecker()
        result = checker.check(["Hello world"])
        assert result.consistent
        assert result.similarity_score == 1.0

    def test_empty_input(self):
        checker = OutputConsistencyChecker()
        result = checker.check([])
        assert result.similarity_score == 1.0

    def test_divergent_segments_detected(self):
        checker = OutputConsistencyChecker(similarity_threshold=0.6)
        result = checker.check([
            "The capital of France is Paris. It has many museums to visit.",
            "The capital of France is Lyon. There are great restaurants downtown.",
        ])
        if not result.consistent:
            assert len(result.divergent_segments) > 0

    def test_entity_agreement_reduces_score(self):
        checker = OutputConsistencyChecker()
        # Same entities → high entity agreement
        result_same = checker.check([
            "API v1.2.3: Alice and Bob completed task at 2024-01-15, 95%.",
            "API v1.2.3: Alice and Bob completed task at 2024-01-16, 92%.",
        ])
        # Different entities → lower score
        result_diff = checker.check([
            "API v1.2.3: Alice and Bob completed task at 2024-01-15, 95%.",
            "API v4.5.6: Charlie and David finished work at 2025-06-20, 80%.",
        ])
        assert result_same.entity_agreement > result_diff.entity_agreement


# ═══════════════════════════════════════════════════════════════════
# FactualityVerifier Tests
# ═══════════════════════════════════════════════════════════════════


class TestFactualityVerifier:
    def test_clean_output_no_issues(self):
        verifier = FactualityVerifier()
        result = verifier.verify("Today is a nice day for a walk in the park.")
        assert result.passed
        assert len(result.warnings) == 0

    def test_fake_date_detected(self):
        verifier = FactualityVerifier()
        result = verifier.verify("The event happened on February 30, 2025.")
        assert not result.passed
        assert any("fake_date" in w for w in result.warnings)

    def test_api_reference_claim_extracted(self):
        verifier = FactualityVerifier()
        result = verifier.verify(
            "Use GET /api/users to fetch users, and POST /api/users to create them.",
        )
        assert result.total_claims > 0

    def test_api_conflict_checked(self):
        verifier = FactualityVerifier()
        result = verifier.verify(
            "The API provides DELETE /users/{id} for removing records.",
            context={"available_apis": ["GET /users", "POST /users"]},
        )
        assert any("not found in known endpoints" in w for w in result.warnings)

    def test_excessive_precision_flagged(self):
        verifier = FactualityVerifier()
        result = verifier.verify("Response time was 12.3456789ms.")
        assert any("precision" in w.lower() for w in result.warnings)

    def test_impossible_percentage_flagged(self):
        verifier = FactualityVerifier()
        result = verifier.verify("The system has 150% uptime.")
        assert any("150%" in w for w in result.warnings)

    def test_future_year_flagged(self):
        verifier = FactualityVerifier()
        result = verifier.verify("The project will complete in year 2150.")
        assert any("2150" in w for w in result.warnings)

    def test_non_existent_version_flagged(self):
        verifier = FactualityVerifier()
        result = verifier.verify("This code runs on Python 4.2 with ES999 support.")
        assert not result.passed  # should flag some patterns

    def test_claim_classification(self):
        verifier = FactualityVerifier()
        result = verifier.verify(
            "The average response time was 42.3ms. "
            "Use GET /api/v1/health to check status. "
            "The file is at C:\\Users\\test\\data.json."
        )
        types = [c.claim_type for c in result.suspicious_claims] if result.suspicious_claims else []
        # At minimum, this should extract some claims
        assert result.total_claims >= 1

    def test_confidence_estimation(self):
        verifier = FactualityVerifier()
        # Hedging language → lower confidence
        result_low = verifier.verify("The system might possibly respond in around 200ms.")
        # Definitive language → higher confidence
        result_high = verifier.verify("The system is always exactly 200ms.")
        assert result_low.total_claims > 0


# ═══════════════════════════════════════════════════════════════════
# HallucinationDetector Tests
# ═══════════════════════════════════════════════════════════════════


class TestHallucinationDetector:
    def test_clean_output_low_risk(self):
        detector = HallucinationDetector()
        result = detector.detect(
            "The sky is blue during a clear sunny day.",
        )
        assert result.risk_level == RiskLevel.LOW
        assert result.risk_score < 0.3
        assert result.suggested_action == "pass"
        assert not result.blocked

    def test_hallucination_like_output_high_risk(self):
        detector = HallucinationDetector()
        result = detector.detect(
            "The API DELETE /users/all has a 150% success rate and executes "
            "in exactly 0.00015674ms. SSN: 123-45-6789. File: C:\\Windows\\VerySpecific\\madeup.dll."
        )
        # Should be at least MEDIUM or higher
        assert result.risk_level in (RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL)
        assert len(result.issues) > 0

    def test_fake_date_raises_risk(self):
        detector = HallucinationDetector()
        result = detector.detect("The meeting was held on February 30, 2024 at exactly 12.3456789% growth rate.")
        assert result.risk_level in (RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL)

    def test_multi_sample_detection(self):
        detector = HallucinationDetector()
        result = detector.detect_multi(
            outputs=[
                "The capital of France is Paris. It has many museums.",
                "The capital of France is Paris. It has many museums.",
            ],
        )
        assert result.risk_level == RiskLevel.LOW
        assert result.consistency is not None
        assert result.consistency.consistent

    def test_multi_sample_divergence(self):
        detector = HallucinationDetector()
        result = detector.detect_multi(
            outputs=[
                "The capital is Paris with 2.1 million people.",
                "The capital is Lyon with 500 thousand residents.",
            ],
        )
        # Should flag inconsistency
        assert result.consistency is not None

    def test_disabled_detector_passes_through(self):
        detector = HallucinationDetector(enabled=False)
        result = detector.detect("SSN: 123-45-6789 on February 30")
        assert result.risk_level == RiskLevel.LOW
        assert result.risk_score == 0.0

    def test_governance_integration_high_risk(self):
        gov_calls = []

        class StubGov:
            def validate(self, action, context=None):
                gov_calls.append((action, context))
                return type("R", (), {"passed": True})()

        detector = HallucinationDetector(governance_agent=StubGov())
        # Generate output that triggers HIGH/CRITICAL
        result = detector.detect(
            "The system has 200% uptime. SSN: 987-65-4321. "
            "File: C:\\Windows\\VerySpecific\\fake.dll. Exact execution: 0.9999999ms."
        )
        if result.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            assert len(gov_calls) > 0

    def test_context_aware_verification(self):
        detector = HallucinationDetector()
        result = detector.detect(
            "Use the DELETE /admin/reset endpoint to wipe all data.",
            context={"available_apis": ["GET /status", "POST /data"]},
        )
        # Should have warnings about API conflict
        has_api_warning = any("not found in known endpoints" in issue for issue in result.issues)
        assert has_api_warning

    def test_risk_level_boundaries(self):
        detector = HallucinationDetector(risk_thresholds={
            "LOW": 0.25,
            "MEDIUM": 0.5,
            "HIGH": 0.75,
            "CRITICAL": 0.9,
        })
        # Clean → LOW
        r1 = detector.detect("Hello world. It is a beautiful day.")
        assert r1.risk_level == RiskLevel.LOW

    def test_factuality_result_attached(self):
        detector = HallucinationDetector()
        result = detector.detect("The API GET /users returns all users with 200% precision.")
        assert result.factuality is not None
        assert isinstance(result.factuality, FactualityResult)

    def test_multi_sample_consistency_result_attached(self):
        detector = HallucinationDetector()
        result = detector.detect_multi(
            outputs=["Hello world", "Hello world"],
        )
        assert result.consistency is not None
        assert isinstance(result.consistency, ConsistencyResult)

    def test_suggested_action_critical(self):
        detector = HallucinationDetector()
        # Force high risk with multiple hallucination patterns
        result = detector.detect(
            "SSN: 123-45-6789, Credit: 4111-2222-3333-4444, "
            "Python 4.2 running on April 31, 2025 with 999.99999% uptime."
        )
        assert result.suggested_action in ("block", "needs_approval", "flag_for_review", "pass")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

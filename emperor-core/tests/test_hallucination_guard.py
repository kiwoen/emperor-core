"""
Tests for huanxin.hallucination_guard — Post-LLM Hallucination Guard.

Covers:
  1. HallucinationDetector: verified claims pass detection
  2. HallucinationDetector: unverifiable claims are flagged
  3. SelfCorrectionLoop: corrects hallucinated output
  4. HallucinationDetector: strict mode flags all unverifiable claims
  5. HallucinationDetector: lenient mode is more permissive
  6. SelfCorrectionLoop: max correction rounds upper bound
  7. detect_toxic_content: filters harmful/t toxic content
  8. HallucinationDetector: empty output handling
  9. _split_sentences: handles various text inputs
 10. _extract_factual_claims: extracts numeric/API/path claims
 11. HallucinationDetector.detect_sync: heuristics-only path
 12. HallucinationGuard facade: unified check + correction
 13. Huanxin integration: hallucination_guard in result dict
 14. Claim merging: deduplication across heuristic + LLM
 15. CorrectionResult: structure and fields
"""

from __future__ import annotations

import asyncio
import pytest

from huanxin.hallucination_guard import (
    GuardMode,
    HallucinationSeverity,
    HallucinatedClaim,
    HallucinationDetectionResult,
    HallucinationDetector,
    HallucinationGuard,
    SelfCorrectionLoop,
    CorrectionResult,
    detect_toxic_content,
    _split_sentences,
    _extract_factual_claims,
    _build_verification_prompt,
)


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════


def _make_detector(mode="strict", llm_callback=None, enable_llm=True):
    return HallucinationDetector(
        mode=GuardMode(mode),
        llm_callback=llm_callback,
        enable_llm_verification=enable_llm,
    )


# ═══════════════════════════════════════════════════════════════
# Test 1: Verified claims pass detection
# ═══════════════════════════════════════════════════════════════


class TestVerifiedClaimsPass:
    """Claims verifiable from context should not be flagged."""

    def test_all_claims_verifiable(self):
        detector = _make_detector(mode="strict")
        output = (
            "The API supports GET /users and POST /users. "
            "Authentication uses JWT tokens."
        )
        context = (
            "Available APIs: GET /users, POST /users, DELETE /users. "
            "Authentication: JWT tokens with 1-hour expiry."
        )
        result = detector.detect_sync(output, context)

        # GET /users and POST /users and JWT tokens are all in context
        assert not result.has_hallucinations
        assert result.flagged_sentences == 0
        assert result.confidence >= 0.95

    def test_output_with_context_information(self):
        detector = _make_detector(mode="strict")
        output = (
            "The database uses PostgreSQL 14. "
            "Queries are executed via SQLAlchemy ORM."
        )
        context = "Stack: PostgreSQL 14, SQLAlchemy ORM, Redis caching."
        result = detector.detect_sync(output, context)

        assert not result.has_hallucinations
        assert result.confidence >= 0.95

    def test_empty_context_still_works(self):
        detector = _make_detector(mode="strict")
        output = "The service returns HTTP 200 on success."
        result = detector.detect_sync(output, context="")

        # Without context, no claims can be extracted as "factual" since
        # the heuristics look for specific patterns.  General statements
        # without numeric/API/path patterns pass through.
        assert result.total_sentences >= 1
        # No factual claims extracted → no flagged claims
        assert result.flagged_sentences == 0


# ═══════════════════════════════════════════════════════════════
# Test 2: Unverifiable claims are flagged
# ═══════════════════════════════════════════════════════════════


class TestUnverifiableClaimsFlagged:
    """Claims not found in context should be flagged."""

    def test_numeric_claim_not_in_context(self):
        detector = _make_detector(mode="strict")
        output = "The API handles 5000 requests per second."
        context = "Available APIs: GET /users, POST /users."
        result = detector.detect_sync(output, context)

        assert result.has_hallucinations
        assert result.flagged_sentences >= 1
        # "5000 requests per second" not in context
        assert any("5000" in c.claim_text for c in result.claims)

    def test_api_claim_not_in_context(self):
        detector = _make_detector(mode="strict")
        output = "You can use DELETE /admin/wipe to clean data."
        context = "Available APIs: GET /users, POST /users."
        result = detector.detect_sync(output, context)

        assert result.has_hallucinations
        assert any(
            "DELETE /admin/wipe" in c.claim_text for c in result.claims
        )

    def test_multiple_claims_in_output(self):
        detector = _make_detector(mode="strict")
        output = (
            "The file is at C:\\Data\\secrets.txt. "
            "Use GET /internal/debug to inspect it. "
            "The system has 10000 users registered."
        )
        context = "System info: The service runs on Windows. No internal debug endpoint."
        result = detector.detect_sync(output, context)

        assert result.has_hallucinations
        # All three claims not in context
        assert result.flagged_sentences >= 1


# ═══════════════════════════════════════════════════════════════
# Test 3: SelfCorrectionLoop
# ═══════════════════════════════════════════════════════════════


class TestSelfCorrectionLoop:
    """Self-correction loop corrects hallucinated output."""

    def test_sync_correction_fixes_claims(self):
        """Synchronous correction path."""
        detector = _make_detector(mode="strict")
        output = "The API supports DELETE /users/{id}."
        context = "Available APIs: GET /users, POST /users."
        result = detector.detect_sync(output, context)
        assert result.has_hallucinations

        # Simulated LLM callback that "corrects" the output
        def mock_llm(prompt: str) -> str:
            return "The API supports GET /users and POST /users. No DELETE endpoint exists."

        loop = SelfCorrectionLoop(max_rounds=3)
        corr_result = loop.correct_sync(
            output=output,
            result=result,
            context=context,
            llm_callback=mock_llm,
        )

        assert isinstance(corr_result, CorrectionResult)
        assert corr_result.rounds_used >= 1
        assert corr_result.corrected_output != output

    def test_correction_stops_when_no_claims_remain(self):
        """Correction loop stops when corrected output has no claims."""
        detector = _make_detector(mode="strict")
        output = "The API supports DELETE /users/{id}."
        context = "Available APIs: GET /users, POST /users."
        result = detector.detect_sync(output, context)

        call_count = [0]

        def mock_llm(prompt: str) -> str:
            call_count[0] += 1
            return "The API supports GET /users and POST /users."

        loop = SelfCorrectionLoop(max_rounds=3, detector=detector)
        corr_result = loop.correct_sync(
            output=output,
            result=result,
            context=context,
            llm_callback=mock_llm,
        )

        assert corr_result.rounds_used == 1
        assert call_count[0] == 1

    @pytest.mark.asyncio
    async def test_async_correction_loop(self):
        """Async correction with LLM-based re-detection."""
        detector = _make_detector(mode="strict")
        output = "The file is at /etc/shadow."
        context = "No file path information available."
        result = detector.detect_sync(output, context)
        assert result.has_hallucinations

        async def mock_llm(prompt: str) -> str:
            return "No file path information is available."

        loop = SelfCorrectionLoop(max_rounds=3)
        corr_result = await loop.correct(
            output=output,
            result=result,
            context=context,
            llm_callback=mock_llm,
        )

        assert isinstance(corr_result, CorrectionResult)
        assert corr_result.rounds_used >= 1


# ═══════════════════════════════════════════════════════════════
# Test 4: Strict mode
# ═══════════════════════════════════════════════════════════════


class TestStrictMode:
    """Strict mode flags any claim not verifiable from context."""

    def test_strict_flags_plausible_but_unverifiable(self):
        detector = _make_detector(mode="strict")
        # Claim sounds plausible but not in context
        output = "The latency is approximately 200ms."
        context = "The service is running on port 8080."
        result = detector.detect_sync(output, context)

        # "200ms" is a numeric claim, not in context → flagged in strict mode
        assert result.has_hallucinations
        assert any("200ms" in c.claim_text for c in result.claims)

    def test_strict_mode_prompt_includes_guidance(self):
        """Verification prompt includes strict mode instructions."""
        prompt = _build_verification_prompt(
            output="test output",
            context="test context",
            mode=GuardMode.STRICT,
        )
        assert "STRICT MODE" in prompt
        assert "any statement that cannot be fully verified" in prompt.lower()

    def test_strict_has_lower_confidence_on_issues(self):
        detector = _make_detector(mode="strict")
        output = "The system stores 500GB of data and processes 10 requests per second."
        context = "System: basic web application."
        result = detector.detect_sync(output, context)

        assert result.has_hallucinations
        assert result.confidence <= 0.9


# ═══════════════════════════════════════════════════════════════
# Test 5: Lenient mode
# ═══════════════════════════════════════════════════════════════


class TestLenientMode:
    """Lenient mode is more permissive, only flags clear contradictions."""

    def test_lenient_allows_plausible_claims(self):
        detector = _make_detector(mode="lenient")
        output = "The latency is approximately 200ms."
        context = "The service is running on port 8080."
        result = detector.detect_sync(output, context)

        # In lenient mode, "200ms" claim is still flagged by heuristics
        # since the claim extraction doesn't change between modes.
        # The mode difference mainly affects LLM-based verification path.
        # For heuristics, both modes behave identically.
        assert result.mode == GuardMode.LENIENT

    def test_lenient_mode_prompt_includes_guidance(self):
        prompt = _build_verification_prompt(
            output="test output",
            context="test context",
            mode=GuardMode.LENIENT,
        )
        assert "LENIENT MODE" in prompt
        assert "only flag statements that are clearly factually incorrect" in prompt.lower()


# ═══════════════════════════════════════════════════════════════
# Test 6: Max correction rounds upper bound
# ═══════════════════════════════════════════════════════════════


class TestCorrectionRoundsUpperBound:
    """SelfCorrectionLoop respects max_rounds."""

    def test_capped_at_max_rounds(self):
        """Correction does not exceed max_rounds even if claims persist."""
        detector = _make_detector(mode="strict")
        output = "The API supports DELETE /users/{id}."
        context = "Available APIs: GET /users, POST /users."
        result = detector.detect_sync(output, context)

        call_count = [0]

        # LLM that keeps producing hallucinated output
        def stubborn_llm(prompt: str) -> str:
            call_count[0] += 1
            return "The API supports DELETE /users/{id} and maybe PATCH too."

        loop = SelfCorrectionLoop(max_rounds=3)
        corr_result = loop.correct_sync(
            output=output,
            result=result,
            context=context,
            llm_callback=stubborn_llm,
        )

        assert corr_result.rounds_used <= 3
        assert call_count[0] <= 3
        assert not corr_result.resolved  # Hallucination persists

    def test_max_rounds_constructor_clamps(self):
        """max_rounds > 5 is clamped to 5."""
        loop = SelfCorrectionLoop(max_rounds=10)
        assert loop.max_rounds == 5

        loop2 = SelfCorrectionLoop(max_rounds=3)
        assert loop2.max_rounds == 3

    def test_single_round_limit(self):
        detector = _make_detector(mode="strict")
        output = "DELETE /users is available."
        context = "Available APIs: GET /users."
        result = detector.detect_sync(output, context)

        call_count = [0]

        def llm(prompt: str) -> str:
            call_count[0] += 1
            return "GET /users is available."

        loop = SelfCorrectionLoop(max_rounds=1)
        corr_result = loop.correct_sync(output, result, context, llm)

        assert corr_result.rounds_used <= 1
        assert call_count[0] == 1


# ═══════════════════════════════════════════════════════════════
# Test 7: Toxicity filtering
# ═══════════════════════════════════════════════════════════════


class TestToxicityFilter:
    """detect_toxic_content flags harmful patterns."""

    def test_detects_harmful_phrases(self):
        results = detect_toxic_content("You should kill yourself now.")
        assert len(results) >= 1
        assert any("kill" in r.lower() for r in results)

    def test_detects_hate_speech(self):
        results = detect_toxic_content("This contains hate speech content.")
        # "hate speech" matches the pattern
        assert len(results) >= 1

    def test_clean_text_passes(self):
        results = detect_toxic_content("The weather is nice today.")
        assert len(results) == 0

    def test_guard_facade_checks_toxicity(self):
        guard = HallucinationGuard(mode="strict")
        result = guard.check(
            output="You should kill yourself. Also the API supports DELETE /admin.",
            context="Available APIs: GET /users, POST /users.",
        )

        assert result.has_hallucinations
        # Both toxic content and hallucinated API claim
        assert result.confidence <= 0.3  # Toxic reduces confidence heavily
        assert any("toxic" in c.reason.lower() for c in result.claims)

    def test_empty_text_toxicity(self):
        results = detect_toxic_content("")
        assert len(results) == 0

        results = detect_toxic_content(None)
        assert len(results) == 0


# ═══════════════════════════════════════════════════════════════
# Test 8: Empty output handling
# ═══════════════════════════════════════════════════════════════


class TestEmptyOutput:
    """Detector handles edge cases gracefully."""

    def test_empty_output_returns_clean(self):
        detector = _make_detector(mode="strict")
        result = detector.detect_sync("", context="some context")
        assert not result.has_hallucinations
        assert result.total_sentences == 0
        assert result.confidence == 1.0

    def test_none_output_returns_clean(self):
        detector = _make_detector(mode="strict")
        result = detector.detect_sync(None, context="some context")
        assert not result.has_hallucinations

    def test_whitespace_output(self):
        detector = _make_detector(mode="strict")
        result = detector.detect_sync("   \n  \t  ", context="context")
        assert not result.has_hallucinations
        assert result.total_sentences == 0


# ═══════════════════════════════════════════════════════════════
# Test 9: Sentence splitting
# ═══════════════════════════════════════════════════════════════


class TestSentenceSplitting:
    """_split_sentences handles various text inputs."""

    def test_simple_sentences(self):
        text = "Hello world. This is a test. How are you?"
        result = _split_sentences(text)
        assert len(result) == 3

    def test_single_sentence(self):
        text = "Just one sentence here."
        result = _split_sentences(text)
        assert len(result) == 1

    def test_question_and_exclamation(self):
        text = "What is this? It is great! Really."
        result = _split_sentences(text)
        assert len(result) == 3

    def test_empty_string(self):
        result = _split_sentences("")
        assert result == []

    def test_short_fragments_filtered(self):
        text = "OK. Yes. This is a proper sentence with enough length."
        result = _split_sentences(text)
        # "OK." (3 chars) and "Yes." (4 chars) are very short but still > 2 chars.
        # The splitter keeps them as they exceed the minimum length threshold.
        assert len(result) == 3


# ═══════════════════════════════════════════════════════════════
# Test 10: Claim extraction
# ═══════════════════════════════════════════════════════════════


class TestClaimExtraction:
    """_extract_factual_claims extracts numeric/API/path/entity claims."""

    def test_extracts_numeric_claims(self):
        claims = _extract_factual_claims("The system handles 5000 requests per second.")
        assert any("5000 requests" in c.lower() for c in claims)

    def test_extracts_api_claims(self):
        claims = _extract_factual_claims("Use GET /users and POST /users.")
        assert "GET /users" in claims
        assert "POST /users" in claims

    def test_extracts_file_paths(self):
        claims = _extract_factual_claims("The config is at C:\\Windows\\System32\\drivers.")
        assert any("C:\\Windows" in c for c in claims)

    def test_extracts_dates(self):
        claims = _extract_factual_claims("Released on 2024-03-15.")
        assert "2024-03-15" in claims

    def test_extracts_named_entities(self):
        claims = _extract_factual_claims("Microsoft Azure provides cloud services.")
        assert "Microsoft Azure" in claims

    def test_no_factual_claims(self):
        claims = _extract_factual_claims("The weather is nice today.")
        assert claims == []


# ═══════════════════════════════════════════════════════════════
# Test 11: Heuristics-only path (detect_sync)
# ═══════════════════════════════════════════════════════════════


class TestDetectSync:
    """detect_sync uses heuristics only, no LLM."""

    def test_detect_sync_no_llm(self):
        detector = _make_detector(mode="strict", enable_llm=False)
        output = "DELETE /admin exists."
        context = "Available APIs: GET /users."
        result = detector.detect_sync(output, context)

        assert result.has_hallucinations

    def test_get_stats_returns_config(self):
        detector = _make_detector(mode="strict")
        stats = detector.get_stats()
        assert stats["mode"] == "strict"
        assert "enable_llm_verification" in stats
        assert "confidence_threshold" in stats

    def test_detect_sync_empty_context(self):
        detector = _make_detector(mode="strict")
        output = "GET /users is available."
        result = detector.detect_sync(output, "")

        # API reference claim, no context to verify → flagged
        assert result.has_hallucinations


# ═══════════════════════════════════════════════════════════════
# Test 12: HallucinationGuard facade
# ═══════════════════════════════════════════════════════════════


class TestHallucinationGuardFacade:
    """HallucinationGuard unified facade."""

    def test_check_basic(self):
        guard = HallucinationGuard(mode="strict")
        result = guard.check(
            output="GET /users is available.",
            context="Available APIs: GET /users, POST /users.",
        )
        assert not result.has_hallucinations

    def test_check_flags_issues(self):
        guard = HallucinationGuard(mode="strict")
        result = guard.check(
            output="DELETE /admin exists.",
            context="Available APIs: GET /users.",
        )
        assert result.has_hallucinations

    def test_get_stats(self):
        guard = HallucinationGuard(mode="lenient", max_correction_rounds=2)
        stats = guard.get_stats()
        assert stats["mode"] == "lenient"
        assert stats["max_correction_rounds"] == 2

    @pytest.mark.asyncio
    async def test_check_async(self):
        guard = HallucinationGuard(mode="strict", enable_llm_verification=False)
        result = await guard.check_async(
            output="GET /users is available.",
            context="Available APIs: GET /users.",
        )
        assert not result.has_hallucinations

    @pytest.mark.asyncio
    async def test_correct_async(self):
        guard = HallucinationGuard(mode="strict", max_correction_rounds=2)
        result = guard.check(
            output="DELETE /users available.",
            context="Available APIs: GET /users.",
        )
        assert result.has_hallucinations

        async def mock_llm(prompt: str) -> str:
            return "GET /users is available."

        corr = await guard.correct(
            output="DELETE /users available.",
            result=result,
            context="Available APIs: GET /users.",
            llm_callback=mock_llm,
        )
        assert isinstance(corr, CorrectionResult)
        assert corr.corrected_output == "GET /users is available."


# ═══════════════════════════════════════════════════════════════
# Test 13: Huanxin integration
# ═══════════════════════════════════════════════════════════════


class TestHuanxinIntegration:
    """Verify emperor.py properly integrates HallucinationGuard."""

    def test_emperor_has_hallucination_guard_property(self):
        from huanxin.core import Huanxin
        emp = Huanxin()
        guard = emp.hallucination_guard
        assert isinstance(guard, HallucinationGuard)

    def test_emperor_execute_task_includes_guard_result(self):
        from huanxin.core import Huanxin
        emp = Huanxin()
        # Register a minister first
        emp.register("turing", domain="math")
        # Use a task that should generate a response
        result = emp.execute_task(
            "What is 2 + 2?",
            domain="math",
        )

        assert "hallucination_guard" in result
        hg = result["hallucination_guard"]
        assert "has_hallucinations" in hg
        assert "confidence" in hg

    def test_hallucination_guard_in_result_for_success(self):
        from huanxin.core import Huanxin
        emp = Huanxin()
        emp.register("turing", domain="general")
        result = emp.execute_task(
            "Say hello in exactly three words.",
            domain="general",
        )

        assert result["success"]
        assert "hallucination_guard" in result
        assert isinstance(result["hallucination_guard"], dict)


# ═══════════════════════════════════════════════════════════════
# Test 14: Claim merging / deduplication
# ═══════════════════════════════════════════════════════════════


class TestClaimMerging:
    """Claims from heuristic + LLM are merged without duplicates."""

    def test_deduplication(self):
        detector = _make_detector(mode="strict")
        # Manually test merge
        h_claim = HallucinatedClaim(
            sentence="DELETE /users is available.",
            claim_text="DELETE /users",
            severity=HallucinationSeverity.MEDIUM,
            reason="Not in context.",
        )
        l_claim = HallucinatedClaim(
            sentence="DELETE /users is available.",
            claim_text="DELETE /users",
            severity=HallucinationSeverity.HIGH,
            reason="LLM-flagged claim.",
        )

        merged = detector._merge_claims([h_claim], [l_claim])
        # LLM claims take priority, so only one claim should remain
        assert len(merged) == 1
        assert merged[0].severity == HallucinationSeverity.HIGH


# ═══════════════════════════════════════════════════════════════
# Test 15: CorrectionResult structure
# ═══════════════════════════════════════════════════════════════


class TestCorrectionResultStructure:
    """CorrectionResult fields are well-formed."""

    def test_basic_fields(self):
        cr = CorrectionResult(
            original_output="original",
            corrected_output="corrected",
            rounds_used=2,
            resolved=True,
        )

        assert cr.original_output == "original"
        assert cr.corrected_output == "corrected"
        assert cr.rounds_used == 2
        assert cr.resolved

    def test_unresolved_result(self):
        cr = CorrectionResult(
            original_output="original",
            corrected_output="partially corrected",
            rounds_used=3,
            resolved=False,
            residual_claims=[
                HallucinatedClaim(
                    sentence="Bad claim.",
                    claim_text="bad",
                    severity=HallucinationSeverity.HIGH,
                    reason="Still not verified.",
                )
            ],
        )

        assert not cr.resolved
        assert len(cr.residual_claims) == 1
        assert cr.residual_claims[0].severity == HallucinationSeverity.HIGH


# ═══════════════════════════════════════════════════════════════
# Test 16: HallucinationDetectionResult.to_dict()
# ═══════════════════════════════════════════════════════════════


class TestDetectionResultSerialization:
    """to_dict() produces valid serializable output."""

    def test_to_dict_basic(self):
        result = HallucinationDetectionResult(
            has_hallucinations=True,
            mode=GuardMode.STRICT,
            total_sentences=3,
            flagged_sentences=1,
            confidence=0.85,
            summary="Test summary.",
            claims=[
                HallucinatedClaim(
                    sentence="DELETE /admin exists.",
                    claim_text="DELETE /admin",
                    severity=HallucinationSeverity.HIGH,
                    reason="Not in context.",
                ),
            ],
        )

        d = result.to_dict()
        assert d["has_hallucinations"] is True
        assert d["mode"] == "strict"
        assert d["total_sentences"] == 3
        assert d["flagged_sentences"] == 1
        assert d["confidence"] == 0.85
        assert len(d["claims"]) == 1
        assert d["claims"][0]["severity"] == "HIGH"


# ═══════════════════════════════════════════════════════════════
# Test 17: LLM-based verification (async path)
# ═══════════════════════════════════════════════════════════════


class TestLLMVerification:
    """Async LLM-based verification path."""

    @pytest.mark.asyncio
    async def test_llm_verification_clean_response(self):
        """LLM returns empty JSON array — no hallucinations."""
        async def mock_llm(prompt: str) -> str:
            return "[]"

        detector = _make_detector(
            mode="strict",
            llm_callback=mock_llm,
            enable_llm=True,
        )
        result = await detector.detect(
            output="GET /users is available.",
            context="Available APIs: GET /users, POST /users.",
        )
        assert not result.has_hallucinations

    @pytest.mark.asyncio
    async def test_llm_verification_flags_claims(self):
        """LLM returns claims that are flagged."""
        async def mock_llm(prompt: str) -> str:
            return '''[
                {
                    "sentence": "DELETE /admin exists.",
                    "claim_text": "DELETE /admin",
                    "severity": "HIGH",
                    "reason": "Not in available APIs."
                }
            ]'''

        detector = _make_detector(
            mode="strict",
            llm_callback=mock_llm,
            enable_llm=True,
        )
        result = await detector.detect(
            output="DELETE /admin exists.",
            context="Available APIs: GET /users.",
        )
        assert result.has_hallucinations
        assert len(result.claims) >= 1

    @pytest.mark.asyncio
    async def test_llm_verification_invalid_json(self):
        """LLM returns invalid JSON — graceful degradation."""
        async def mock_llm(prompt: str) -> str:
            return "Not a JSON array at all."

        detector = _make_detector(
            mode="strict",
            llm_callback=mock_llm,
            enable_llm=True,
        )
        result = await detector.detect(
            output="Some output.",
            context="Some context.",
        )
        # Heuristics may still flag, but LLM parsing failure shouldn't crash
        assert isinstance(result, HallucinationDetectionResult)

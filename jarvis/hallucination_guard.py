"""
Post-LLM Hallucination Guard — 后置幻觉检测与自纠正引擎。

在 LLM 生成响应后、返回给用户前，对输出进行逐句幻觉检测：
  - HallucinationDetector : 基于 LLM 逐句检查输出中是否有无法从给定上下文验证的声明
  - SelfCorrectionLoop     : 检测到幻觉 → 隔离声明 → 构造修正提示 → 重新调用 LLM
                             → 再次检测（最多 3 轮，带上限防止无限循环）
  - 两种模式：strict（任何无法验证的声明都标记）、lenient（仅标记事实性错误）

与 jarvis.prompt_guard.PromptGuard 共同构成 Pre+Post LLM guardrail 体系：
  - PromptGuard  : 前置——拦截 Prompt Injection（指令覆盖/角色劫持/提示提取/越狱/编码混淆）
  - HallucinationGuard : 后置——拦截 LLM 输出幻觉（无依据声明/事实性错误）

Usage:
    from jarvis.hallucination_guard import HallucinationDetector, SelfCorrectionLoop

    detector = HallucinationDetector(mode="strict")
    result = detector.detect(
        output="The API supports DELETE /users/{id}",
        context="Available APIs: GET /users, POST /users",
    )
    if result.has_hallucinations:
        loop = SelfCorrectionLoop(max_rounds=3)
        corrected = await loop.correct(
            output=output,
            result=result,
            context=context,
            llm_callback=llm_call,
        )
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("jarvis.hallucination_guard")


# ═══════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════


class GuardMode(str, Enum):
    """Detection strictness mode."""
    STRICT = "strict"     # 任何无法验证的声明都标记
    LENIENT = "lenient"   # 仅标记明确的事实性错误


class HallucinationSeverity(str, Enum):
    """Severity of a detected hallucination."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass
class HallucinatedClaim:
    """A single claim identified as potentially hallucinated."""
    sentence: str
    claim_text: str                        # 具体的可疑声明片段
    severity: HallucinationSeverity
    reason: str                            # 为什么被标记
    suggested_correction: str = ""         # 建议的修正内容
    context_evidence: str = ""             # 上下文中与之矛盾的证据


@dataclass
class HallucinationDetectionResult:
    """Result of hallucination detection on an LLM output."""
    has_hallucinations: bool
    mode: GuardMode
    claims: List[HallucinatedClaim] = field(default_factory=list)
    total_sentences: int = 0
    flagged_sentences: int = 0
    confidence: float = 1.0                # 0-1, 1 = all clear
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "has_hallucinations": self.has_hallucinations,
            "mode": self.mode.value,
            "total_sentences": self.total_sentences,
            "flagged_sentences": self.flagged_sentences,
            "confidence": round(self.confidence, 4),
            "claims": [
                {
                    "sentence": c.sentence[:200],
                    "claim_text": c.claim_text,
                    "severity": c.severity.value,
                    "reason": c.reason,
                    "suggested_correction": c.suggested_correction,
                }
                for c in self.claims
            ],
            "summary": self.summary,
        }


@dataclass
class CorrectionResult:
    """Result of a self-correction loop."""
    original_output: str
    corrected_output: str
    rounds_used: int
    resolved: bool
    residual_claims: List[HallucinatedClaim] = field(default_factory=list)
    corrections_applied: List[dict] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════
# Token Extraction Helpers
# ═══════════════════════════════════════════════════════════════════

def _split_sentences(text: str) -> List[str]:
    """Split text into sentences, handling common edge cases."""
    if not text:
        return []
    # Split on sentence boundaries but keep delimiters
    raw = re.split(r'(?<=[.!?])\s+', text)
    return [s.strip() for s in raw if s.strip() and len(s.strip()) > 2]


def _extract_factual_claims(sentence: str) -> List[str]:
    """Extract factual claims from a sentence that could be verifiable."""
    claims = []

    # Numeric claims
    numeric_matches = re.findall(
        r'\b\d+(?:\.\d+)?\s*(?:%|ms|seconds?|MB|GB|KB|TB|users?|records?|items?|files?|requests?)\b',
        sentence,
        re.IGNORECASE,
    )
    for m in numeric_matches:
        claims.append(m)

    # API/endpoint references
    api_matches = re.findall(
        r'\b(?:GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(?:/\S+)+',
        sentence,
    )
    for m in api_matches:
        # Strip trailing punctuation from API matches
        claims.append(m.rstrip(".,;:!?\"'))"))

    # File paths
    path_matches = re.findall(
        r'(?:[A-Za-z]:\\[\w\s\-.\\]+|~?/[\w\-./]+)',
        sentence,
    )
    for m in path_matches:
        claims.append(m.rstrip(".,;:!?\")'"))

    # Named entities (proper nouns, capitalized phrases)
    entity_matches = re.findall(
        r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b',
        sentence,
    )
    for m in entity_matches:
        claims.append(m.rstrip(".,;:!?\"'))"))

    # Date references
    date_matches = re.findall(
        r'\b(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])\b',
        sentence,
    )
    for m in date_matches:
        claims.append(m.rstrip(".,;:!?\"'))"))

    return claims


def _build_verification_prompt(output: str, context: str, mode: GuardMode) -> str:
    """Build a prompt for LLM-based hallucination verification."""
    strictness_note = ""
    if mode == GuardMode.STRICT:
        strictness_note = (
            "STRICT MODE: Mark ANY statement that cannot be fully verified "
            "from the provided context. Even plausible statements should be "
            "flagged if there is no explicit supporting evidence in the context."
        )
    else:
        strictness_note = (
            "LENIENT MODE: Only flag statements that are clearly factually "
            "incorrect or contradict the provided context. Plausible but "
            "unverifiable statements should be allowed to pass."
        )

    system_prompt = f"""You are a hallucination detection expert. Your task is to verify whether claims in an AI-generated output can be supported by the provided context.

{strictness_note}

Instructions:
1. Read the CONTEXT carefully — this is the only source of ground truth.
2. Read the OUTPUT — this is what the AI assistant said.
3. For each sentence in the OUTPUT, determine if ALL factual claims in it are verifiable from the CONTEXT.
4. For each unverifiable claim, output a JSON object with the following fields:
   - "sentence": the full sentence containing the claim
   - "claim_text": the specific claim fragment
   - "severity": one of "LOW" (minor detail), "MEDIUM" (meaningful inaccuracy), "HIGH" (critical falsehood)
   - "reason": why this claim cannot be verified from context

Response format: Output ONLY a JSON array of problematic claims. If no issues found, output an empty array []."""
    user_message = f"""CONTEXT (ground truth):
{context}

OUTPUT (AI-generated, to verify):
{output}"""

    return f"{system_prompt}\n\n---\n\n{user_message}\n\n---\n\nJSON response:"


# ═══════════════════════════════════════════════════════════════════
# HallucinationDetector
# ═══════════════════════════════════════════════════════════════════


class HallucinationDetector:
    """Post-LLM hallucination detector that verifies output against context.

    Uses a combination of pattern-based heuristics (fast path) and LLM-based
    verification (thorough path) to detect claims that cannot be supported
    by the provided context.

    Args:
        mode: "strict" or "lenient" — controls verification threshold.
        llm_callback: Optional async callback for LLM-based verification.
                      Signature: async def(text: str) -> str
        enable_llm_verification: If True, use LLM for thorough verification.
                                 If False, rely only on heuristics.
        confidence_threshold: Below this confidence, flag as hallucination.
    """

    def __init__(
        self,
        mode: GuardMode = GuardMode.STRICT,
        llm_callback: Optional[Callable] = None,
        enable_llm_verification: bool = True,
        confidence_threshold: float = 0.7,
    ):
        self.mode = mode
        self.llm_callback = llm_callback
        self.enable_llm_verification = enable_llm_verification
        self.confidence_threshold = confidence_threshold

        # Heuristic patterns for fast-path detection
        self._contradiction_markers = [
            "does not exist", "not found", "no such", "invalid",
            "unsupported", "deprecated", "removed", "never existed",
        ]

    async def detect(
        self,
        output: str,
        context: str = "",
        context_documents: Optional[List[str]] = None,
    ) -> HallucinationDetectionResult:
        """Detect hallucinations in LLM output by verifying against context.

        Args:
            output: The LLM-generated output text.
            context: Ground truth context (documents, tool returns, etc.).
            context_documents: Optional list of document contents for cross-ref.

        Returns:
            HallucinationDetectionResult with detected claims and confidence.
        """
        if not output or not isinstance(output, str):
            return HallucinationDetectionResult(
                has_hallucinations=False,
                mode=self.mode,
                total_sentences=0,
                confidence=1.0,
                summary="Empty output.",
            )

        # Merge all context sources
        full_context = context
        if context_documents:
            for doc in context_documents:
                full_context += "\n" + (doc or "")

        # Step 1: Split into sentences
        sentences = _split_sentences(output)
        total = len(sentences)
        if total == 0:
            return HallucinationDetectionResult(
                has_hallucinations=False,
                mode=self.mode,
                total_sentences=0,
                confidence=1.0,
                summary="No parseable sentences.",
            )

        # Step 2: Heuristic fast-path detection
        heuristic_claims = self._heuristic_detect(sentences, full_context)

        # Step 3: LLM-based thorough verification (if enabled and callback available)
        llm_claims: List[HallucinatedClaim] = []
        if (
            self.enable_llm_verification
            and self.llm_callback is not None
            and full_context.strip()
        ):
            llm_claims = await self._llm_verify(output, full_context)

        # Step 4: Merge and deduplicate claims
        all_claims = self._merge_claims(heuristic_claims, llm_claims)

        flagged = len(all_claims)
        has_issues = flagged > 0

        # Calculate confidence
        if total == 0:
            confidence = 1.0
        else:
            # Each flagged claim reduces confidence
            severity_weights = {
                HallucinationSeverity.LOW: 0.05,
                HallucinationSeverity.MEDIUM: 0.15,
                HallucinationSeverity.HIGH: 0.30,
            }
            penalty = sum(
                severity_weights.get(c.severity, 0.1) for c in all_claims
            )
            confidence = max(0.0, 1.0 - penalty)

        if has_issues:
            summary = (
                f"Detected {flagged} hallucination(s) in {total} sentence(s). "
                f"Confidence: {confidence:.2f}"
            )
        else:
            summary = f"No hallucinations detected in {total} sentence(s)."

        return HallucinationDetectionResult(
            has_hallucinations=has_issues,
            mode=self.mode,
            claims=all_claims,
            total_sentences=total,
            flagged_sentences=flagged,
            confidence=round(confidence, 4),
            summary=summary,
        )

    def _heuristic_detect(
        self, sentences: List[str], context: str
    ) -> List[HallucinatedClaim]:
        """Fast-path heuristic detection using pattern matching."""
        claims: List[HallucinatedClaim] = []

        for sentence in sentences:
            # Extract factual claims from the sentence
            factual_claims = _extract_factual_claims(sentence)
            if not factual_claims:
                continue

            context_lower = context.lower()
            sentence_lower = sentence.lower()

            for claim_text in factual_claims:
                claim_lower = claim_text.lower().rstrip(".,;:!?\"')")

                # Check if claim exists in context (exact substring match)
                if claim_lower in context_lower:
                    continue

                # Also check without common prefixes/suffixes
                # e.g. "the API GET /users" in context "GET /users"
                claim_words = claim_lower.split()
                if len(claim_words) >= 2 and " ".join(claim_words[-2:]) in context_lower:
                    continue

                # Check for contradiction markers
                has_contradiction = any(
                    marker in sentence_lower for marker in self._contradiction_markers
                )

                severity = HallucinationSeverity.LOW
                reason = "Claim not found in provided context."

                if has_contradiction:
                    severity = HallucinationSeverity.HIGH
                    reason = "Claim contradicts context or contains negation marker."
                elif re.search(r'\b\d{3,}\b', claim_text):
                    # Large numbers are more suspicious
                    severity = HallucinationSeverity.MEDIUM
                    reason = "Specific numeric claim not verifiable from context."

                claims.append(HallucinatedClaim(
                    sentence=sentence,
                    claim_text=claim_text,
                    severity=severity,
                    reason=reason,
                ))

        return claims

    async def _llm_verify(
        self, output: str, context: str
    ) -> List[HallucinatedClaim]:
        """Use LLM for thorough hallucination verification."""
        if self.llm_callback is None:
            return []

        prompt = _build_verification_prompt(output, context, self.mode)

        try:
            response = await self.llm_callback(prompt)
            claims = self._parse_llm_response(response, output)
            return claims
        except Exception as e:
            logger.warning("LLM verification failed: %s", e)
            return []

    def _parse_llm_response(
        self, response: str, original_output: str
    ) -> List[HallucinatedClaim]:
        """Parse LLM verification response into HallucinatedClaim objects."""
        claims: List[HallucinatedClaim] = []

        # Try to extract JSON array from response
        try:
            import json

            # Find JSON array in response
            json_match = re.search(r'\[.*\]', response, re.DOTALL)
            if json_match:
                raw = json_match.group(0)
                items = json.loads(raw)
                if isinstance(items, list):
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        severity_str = item.get("severity", "LOW").upper()
                        try:
                            severity = HallucinationSeverity(severity_str)
                        except ValueError:
                            severity = HallucinationSeverity.LOW

                        claims.append(HallucinatedClaim(
                            sentence=item.get("sentence", "")[:500],
                            claim_text=item.get("claim_text", "")[:300],
                            severity=severity,
                            reason=item.get("reason", "LLM-flagged claim."),
                        ))
        except (json.JSONDecodeError, ValueError) as e:
            logger.debug("Failed to parse LLM response as JSON: %s", e)

        return claims

    def _merge_claims(
        self,
        heuristic: List[HallucinatedClaim],
        llm: List[HallucinatedClaim],
    ) -> List[HallucinatedClaim]:
        """Merge and deduplicate heuristic and LLM claims."""
        seen: Set[str] = set()
        merged: List[HallucinatedClaim] = []

        # LLM claims take priority (more reliable), add them first
        for claim in llm:
            key = (claim.sentence[:80] + claim.claim_text[:80]).lower()
            if key not in seen:
                seen.add(key)
                merged.append(claim)

        # Add heuristic claims that don't overlap
        for claim in heuristic:
            key = (claim.sentence[:80] + claim.claim_text[:80]).lower()
            if key not in seen:
                seen.add(key)
                merged.append(claim)

        return merged

    def detect_sync(
        self,
        output: str,
        context: str = "",
    ) -> HallucinationDetectionResult:
        """Synchronous wrapper for detect (heuristics only, no LLM)."""
        if not output or not isinstance(output, str):
            return HallucinationDetectionResult(
                has_hallucinations=False,
                mode=self.mode,
                total_sentences=0,
                confidence=1.0,
                summary="Empty output.",
            )

        sentences = _split_sentences(output)
        total = len(sentences)
        if total == 0:
            return HallucinationDetectionResult(
                has_hallucinations=False,
                mode=self.mode,
                total_sentences=0,
                confidence=1.0,
                summary="No parseable sentences.",
            )

        claims = self._heuristic_detect(sentences, context)
        flagged = len(claims)

        severity_weights = {
            HallucinationSeverity.LOW: 0.05,
            HallucinationSeverity.MEDIUM: 0.15,
            HallucinationSeverity.HIGH: 0.30,
        }
        penalty = sum(severity_weights.get(c.severity, 0.1) for c in claims)
        confidence = max(0.0, 1.0 - penalty)

        return HallucinationDetectionResult(
            has_hallucinations=flagged > 0,
            mode=self.mode,
            claims=claims,
            total_sentences=total,
            flagged_sentences=flagged,
            confidence=round(confidence, 4),
            summary=(
                f"Detected {flagged} hallucination(s) in {total} sentence(s). "
                f"Confidence: {confidence:.2f}"
                if flagged > 0
                else f"No hallucinations detected in {total} sentence(s)."
            ),
        )

    def get_stats(self) -> Dict[str, Any]:
        """Return detector configuration and status."""
        return {
            "mode": self.mode.value,
            "enable_llm_verification": self.enable_llm_verification,
            "confidence_threshold": self.confidence_threshold,
            "llm_callback_configured": self.llm_callback is not None,
        }


# ═══════════════════════════════════════════════════════════════════
# SelfCorrectionLoop
# ═══════════════════════════════════════════════════════════════════


class SelfCorrectionLoop:
    """Self-correction loop for hallucinated LLM outputs.

    When hallucinations are detected, this loop:
      1. Isolates the problematic claims from the detection result
      2. Constructs a correction prompt with the claims + context
      3. Re-calls the LLM with the correction prompt
      4. Re-detects hallucinations on the new output
      5. Repeats up to max_rounds (default 3)

    Args:
        max_rounds: Maximum correction rounds (1-5, default 3).
        detector: Optional HallucinationDetector instance for re-detection.
    """

    def __init__(
        self,
        max_rounds: int = 3,
        detector: Optional[HallucinationDetector] = None,
    ):
        self.max_rounds = min(max_rounds, 5)
        self.detector = detector

    async def correct(
        self,
        output: str,
        result: HallucinationDetectionResult,
        context: str,
        llm_callback: Callable,
    ) -> CorrectionResult:
        """Run the self-correction loop.

        Args:
            output: The original LLM output with hallucinations.
            result: The initial detection result identifying hallucinated claims.
            context: Ground truth context for verification.
            llm_callback: Async callback to re-invoke the LLM.
                          Signature: async def(text: str) -> str

        Returns:
            CorrectionResult with corrected output and correction history.
        """
        corrections_applied: List[dict] = []
        current_output = output
        current_claims = list(result.claims)
        resolved = False

        for round_idx in range(self.max_rounds):
            if not current_claims:
                resolved = True
                break

            # Build correction prompt
            correction_prompt = self._build_correction_prompt(
                current_output, current_claims, context
            )

            # Call LLM for correction
            try:
                corrected = await llm_callback(correction_prompt)
                if not corrected or not isinstance(corrected, str):
                    logger.warning(
                        "SelfCorrection round %d: empty LLM response", round_idx + 1
                    )
                    break
            except Exception as e:
                logger.warning(
                    "SelfCorrection round %d: LLM call failed: %s", round_idx + 1, e
                )
                break

            corrections_applied.append({
                "round": round_idx + 1,
                "claims_count_before": len(current_claims),
                "output_before": current_output[:300],
                "output_after": corrected[:300],
            })

            current_output = corrected

            # Re-detect hallucinations
            if self.detector is not None:
                new_result = await self.detector.detect(corrected, context)
                current_claims = new_result.claims
            else:
                # Simple heuristics re-check
                new_detector = HallucinationDetector(
                    mode=result.mode, enable_llm_verification=False
                )
                new_result = new_detector.detect_sync(corrected, context)
                current_claims = new_result.claims

        return CorrectionResult(
            original_output=output,
            corrected_output=current_output,
            rounds_used=len(corrections_applied),
            resolved=resolved,
            residual_claims=current_claims,
            corrections_applied=corrections_applied,
        )

    def _build_correction_prompt(
        self,
        output: str,
        claims: List[HallucinatedClaim],
        context: str,
    ) -> str:
        """Build a correction prompt listing problematic claims with context."""
        claims_text = ""
        for i, claim in enumerate(claims, 1):
            claims_text += (
                f"{i}. Sentence: \"{claim.sentence[:200]}\"\n"
                f"   Problematic claim: \"{claim.claim_text}\"\n"
                f"   Issue: {claim.reason}\n\n"
            )

        prompt = f"""You previously generated the following output, but some claims in it cannot be verified against the provided context.

Please correct the output by:
1. Removing or revising any claims that are not supported by the context
2. Only stating facts that can be verified from the context
3. If you are unsure about something, say so instead of making unsupported claims

CONTEXT (ground truth):
{context}

PROBLEMATIC CLAIMS:
{claims_text}

ORIGINAL OUTPUT (with issues):
{output}

Please provide the CORRECTED OUTPUT below. Keep the same overall structure and style, but remove or fix any unsupported claims:"""

        return prompt

    def correct_sync(
        self,
        output: str,
        result: HallucinationDetectionResult,
        context: str,
        llm_callback: Callable,
    ) -> CorrectionResult:
        """Synchronous version of correct (for simple integrations)."""
        corrections_applied: List[dict] = []
        current_output = output
        current_claims = list(result.claims)
        resolved = False

        for round_idx in range(self.max_rounds):
            if not current_claims:
                resolved = True
                break

            correction_prompt = self._build_correction_prompt(
                current_output, current_claims, context
            )

            try:
                corrected = llm_callback(correction_prompt)
                if not corrected or not isinstance(corrected, str):
                    break
            except Exception as e:
                logger.warning(
                    "SelfCorrection sync round %d: LLM call failed: %s",
                    round_idx + 1, e,
                )
                break

            corrections_applied.append({
                "round": round_idx + 1,
                "claims_count_before": len(current_claims),
            })

            current_output = corrected

            new_detector = HallucinationDetector(
                mode=result.mode, enable_llm_verification=False
            )
            new_result = new_detector.detect_sync(corrected, context)
            current_claims = new_result.claims

        return CorrectionResult(
            original_output=output,
            corrected_output=current_output,
            rounds_used=len(corrections_applied),
            resolved=resolved,
            residual_claims=current_claims,
            corrections_applied=corrections_applied,
        )


# ═══════════════════════════════════════════════════════════════════
# Toxicity / Harmful Content Filter
# ═══════════════════════════════════════════════════════════════════

# Known toxic/harmful term patterns for basic filtering
_TOXIC_PATTERNS: List[re.Pattern] = [
    re.compile(r'\b(?:kill\s+(?:yourself|myself|everyone|them|all))\b', re.IGNORECASE),
    re.compile(r'\b(?:suicide|self[- ]?harm)\b', re.IGNORECASE),
    re.compile(r'\b(?:hate\s+(?:speech|crime))\b', re.IGNORECASE),
    re.compile(
        r'\b(?:racial|ethnic|religious)\s+(?:slur|hatred|supremacy)\b',
        re.IGNORECASE,
    ),
    re.compile(
        r'\b(?:child\s+(?:abuse|exploitation|pornography))\b',
        re.IGNORECASE,
    ),
]


def detect_toxic_content(text: str) -> List[str]:
    """Basic toxicity filter — detects harmful content patterns.

    Args:
        text: The text to scan.

    Returns:
        List of matched toxic pattern descriptions.
    """
    if not text:
        return []

    found: List[str] = []
    for pattern in _TOXIC_PATTERNS:
        if pattern.search(text):
            found.append(f"Matched toxic pattern: {pattern.pattern}")
    return found


# ═══════════════════════════════════════════════════════════════════
# Unified Guard Facade
# ═══════════════════════════════════════════════════════════════════


class HallucinationGuard:
    """Unified post-LLM guardrail facade combining hallucination detection,
    self-correction, and toxicity filtering.

    Designed to work alongside PromptGuard to form a complete Pre+Post LLM
    guardrail system.

    Usage:
        guard = HallucinationGuard(mode="strict")
        result = guard.check(output="...", context="...")
        if result.has_hallucinations:
            corrected = await guard.correct(output, result, context, llm_fn)
    """

    def __init__(
        self,
        mode: GuardMode = GuardMode.STRICT,
        llm_callback: Optional[Callable] = None,
        enable_llm_verification: bool = True,
        max_correction_rounds: int = 3,
    ):
        if isinstance(mode, str):
            mode = GuardMode(mode)
        self.mode = mode
        self.detector = HallucinationDetector(
            mode=mode,
            llm_callback=llm_callback,
            enable_llm_verification=enable_llm_verification,
        )
        self.correction_loop = SelfCorrectionLoop(
            max_rounds=max_correction_rounds,
            detector=self.detector,
        )

    def check(
        self, output: str, context: str = ""
    ) -> HallucinationDetectionResult:
        """Run hallucination detection (synchronous, heuristics only)."""
        result = self.detector.detect_sync(output, context)

        # Also check for toxic content
        toxic = detect_toxic_content(output)
        if toxic:
            for t in toxic:
                result.claims.append(HallucinatedClaim(
                    sentence=output[:200],
                    claim_text=t,
                    severity=HallucinationSeverity.HIGH,
                    reason="Toxic/harmful content detected.",
                ))
            result.has_hallucinations = True
            result.flagged_sentences += len(toxic)
            result.confidence = min(result.confidence, 0.2)
            result.summary = f"Toxic content + {result.summary}"

        return result

    async def check_async(
        self, output: str, context: str = ""
    ) -> HallucinationDetectionResult:
        """Run hallucination detection (async, with LLM verification)."""
        result = await self.detector.detect(output, context)

        toxic = detect_toxic_content(output)
        if toxic:
            for t in toxic:
                result.claims.append(HallucinatedClaim(
                    sentence=output[:200],
                    claim_text=t,
                    severity=HallucinationSeverity.HIGH,
                    reason="Toxic/harmful content detected.",
                ))
            result.has_hallucinations = True
            result.flagged_sentences += len(toxic)
            result.confidence = min(result.confidence, 0.2)

        return result

    async def correct(
        self,
        output: str,
        result: HallucinationDetectionResult,
        context: str,
        llm_callback: Callable,
    ) -> CorrectionResult:
        """Run self-correction loop on hallucinated output."""
        return await self.correction_loop.correct(
            output=output,
            result=result,
            context=context,
            llm_callback=llm_callback,
        )

    def get_stats(self) -> Dict[str, Any]:
        """Return guard configuration and status."""
        return {
            "mode": self.mode.value,
            "detector": self.detector.get_stats(),
            "max_correction_rounds": self.correction_loop.max_rounds,
        }


# ═══════════════════════════════════════════════════════════════════
# Convenience
# ═══════════════════════════════════════════════════════════════════


def create_default_guard(mode: str = "strict") -> HallucinationGuard:
    """Create a HallucinationGuard with default settings."""
    return HallucinationGuard(
        mode=GuardMode(mode),
        enable_llm_verification=False,  # Default: heuristics only, callers opt-in for LLM
        max_correction_rounds=3,
    )

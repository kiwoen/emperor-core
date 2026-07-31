"""
Silent Hallucination Detector — 静默幻觉主动检测。

在 Agent 输出交付给用户/下游系统之前，对输出进行多维度幻觉检测：
  - OutputConsistencyChecker : 多次采样比较语义一致性（n-gram overlap + 实体一致性）
  - FactualityVerifier        : 提取声称（claims）并交叉验证（模式匹配 + 常识规则）
  - HallucinationDetector     : 综合评分 → 风险等级 → 建议动作

与 GovernanceAgent 联动：HIGH/CRITICAL 风险触发审批或阻止。

Inspired by 2025-2026 AI hallucination research:
  - SelfCheckGPT: n-gram consistency for LLM output
  - FacTool: factuality detection in generated text
  - Harms et al.: types of hallucination (intrinsic vs extrinsic)

Usage:
    from jarvis.hallucination_detector import HallucinationDetector

    detector = HallucinationDetector(governance_agent=gov)
    result = detector.detect(
        output="The API supports DELETE /users/{id}",
        context={"available_apis": ["GET /users", "POST /users"]},
    )
    if result.risk_level in ("HIGH", "CRITICAL"):
        block_or_request_approval(result)
"""

from __future__ import annotations

import logging
import re
import math
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("jarvis.hallucination_detector")


# ═══════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════

class RiskLevel(str, Enum):
    """Hallucination risk classification."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ClaimType(str, Enum):
    """Types of claims extracted from output."""
    FACTUAL_NUMBER = "factual_number"       # 具体数字/百分比
    FACTUAL_DATE = "factual_date"           # 日期/时间
    API_REFERENCE = "api_reference"         # API/函数引用
    FILE_PATH = "file_path"                 # 文件路径
    CODE_SNIPPET = "code_snippet"           # 代码片段
    ENTITY_REFERENCE = "entity_reference"   # 命名实体
    STATISTICAL = "statistical"             # 统计声明
    COMPARATIVE = "comparative"             # 比较声明
    DEFINITIONAL = "definitional"           # 定义性声明
    OTHER = "other"


# Known hallucination-prone patterns
_HALLUCINATION_PATTERNS: Dict[str, re.Pattern] = {
    "fake_date": re.compile(
        r'\b(?:February\s+3[01]|April\s+31|June\s+31|September\s+31|November\s+31)\b',
        re.IGNORECASE,
    ),
    "fake_file_path": re.compile(
        r"""(?:C:\\(?:Windows|Program\s+Files)\\VerySpecific|/usr/local/madeup|~/.nonexistent_config)""",
        re.IGNORECASE,
    ),
    "overly_specific": re.compile(
        r'\b\d{4}\.\d{3,}[%]?\b'  # e.g., 2024.873% — too precise
    ),
    "impossible_stat": re.compile(
        r'\b(?:1[2-9]\d{2,}|[2-9]\d{3,})%?\b'  # e.g., 1200% — suspicious percentage
    ),
    "nonexistent_version": re.compile(
        r'\b(?:Python\s+4\.\d+|JavaScript\s+ES\d{3,}|React\s+v?\d{3,})\b'
    ),
}

# Common false-positive hallmarks in AI-generated text
_ENTITY_PATTERNS: Dict[str, re.Pattern] = {
    "api_endpoint": re.compile(
        r'\b(?:GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(?:/[a-zA-Z0-9_\-{}]+)+\b',
    ),
    "file_reference": re.compile(
        r"""(?:[A-Za-z]:\\(?:[a-zA-Z0-9_\-. ]+\\)*[a-zA-Z0-9_\-. ]+\.[a-zA-Z]{2,})|(?:~?/[a-zA-Z0-9_\-./]+)""",
    ),
    "version_number": re.compile(
        r'\bv?\d+\.\d+(?:\.\d+)?(?:-[a-zA-Z0-9]+)?\b',
    ),
    "date_iso": re.compile(
        r'\b(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])\b',
    ),
    "percentage": re.compile(
        r'\b\d+(?:\.\d+)?%\b',
    ),
    "number_range": re.compile(
        r'\b\d+(?:\.\d+)?\s*[-–]\s*\d+(?:\.\d+)?\b',
    ),
}

# Common sense checks for claims
_COMMON_SENSE_CHECKS: List[Callable[[str], Optional[str]]] = []


def _register_common_sense(fn: Callable) -> Callable:
    _COMMON_SENSE_CHECKS.append(fn)
    return fn


@_register_common_sense
def _check_impossible_percentage(text: str) -> Optional[str]:
    """Detect percentages > 100% in non-growth contexts."""
    # Naive: any standalone percentage > 100% in non-"increase" context
    matches = re.findall(r'\b(\d{3,})%', text)
    for m in matches:
        val = int(m)
        if val > 100 and not re.search(r'increase|growth|rise|surge|jump|spike', text, re.IGNORECASE):
            return f"Percentage > 100% ({val}%) in non-growth context"
    return None


@_register_common_sense
def _check_future_date(text: str) -> Optional[str]:
    """Detect dates that are impossibly far in the future."""
    import datetime as _dt
    today = _dt.date.today()
    # Look for year mentions
    year_matches = re.findall(r'\b(2\d{3})\b', text)
    for m in year_matches:
        year = int(m)
        if year > today.year + 100:
            return f"Year {year} is implausibly far in the future"
    return None


@_register_common_sense
def _check_api_conflict(output: str, context: Dict[str, Any]) -> Optional[str]:
    """Check if referenced APIs don't exist in known API list."""
    known_apis = context.get("available_apis", [])
    if not known_apis:
        return None
    api_refs = re.findall(r'\b(?:GET|POST|PUT|DELETE|PATCH)\s+(/\S+)', output)
    for ref in api_refs:
        # Normalize for comparison
        norm_ref = ref.rstrip('/')
        if not any(norm_ref == api.rstrip('/') or norm_ref.startswith(api.rstrip('/') + '/')
                   for api in known_apis):
            return f"Referenced API '{ref}' not found in known endpoints"
    return None


@_register_common_sense
def _check_file_path_suspicious(text: str) -> Optional[str]:
    """Flag suspicious file paths that look fabricated."""
    paths = re.findall(r'([A-Za-z]:\\[a-zA-Z0-9_\-. \\]+)', text)
    for path in paths:
        # Check for suspicious patterns: random-looking names, very deep nesting
        parts = path.replace('\\', '/').split('/')
        if len(parts) > 6:  # Very deep path
            return f"Deeply nested path: {path}"
        # Check for random-looking directory names
        for part in parts[1:]:
            if re.match(r'^[a-z]{8,}$', part) and not any(
                kw in part for kw in ('users', 'program', 'windows', 'documents', 'desktop')
            ):
                return f"Random-looking directory name in path: {path}"
    return None


@_register_common_sense
def _check_excessive_precision(text: str) -> Optional[str]:
    """Flag numbers that are implausibly precise."""
    matches = re.findall(r'\b(\d+(?:\.\d+)?)\s*(?:ms|seconds?|MB|GB|TB|KB|rps|qps|tps)\b', text)
    for m in matches:
        if '.' in m:
            decimals = len(m.split('.')[1])
            if decimals > 3:
                return f"Excessive precision: {m}"
    return None


# ═══════════════════════════════════════════════════════════════════
# 1. OutputConsistencyChecker
# ═══════════════════════════════════════════════════════════════════


@dataclass
class ConsistencyResult:
    """Result of consistency checking across multiple samples."""
    consistent: bool
    similarity_score: float           # 0-1, higher = more consistent
    ngram_overlap: float             # n-gram overlap ratio
    entity_agreement: float          # entity-level agreement
    divergent_segments: List[str]    # parts that differ significantly
    total_samples: int


class OutputConsistencyChecker:
    """Checks semantic consistency across multiple LLM outputs for the same prompt.

    Uses n-gram overlap + entity consistency. A low consistency score
    suggests possible hallucination (unstable generation).

    Usage:
        checker = OutputConsistencyChecker(n=3)
        result = checker.check([output1, output2, output3])
        if result.similarity_score < 0.5:
            flag_as_suspicious()
    """

    def __init__(self,
                 n: int = 3,
                 similarity_threshold: float = 0.4,
                 entity_weight: float = 0.3,
                 ngram_weight: float = 0.7):
        self.n = n
        self.similarity_threshold = similarity_threshold
        self.entity_weight = entity_weight
        self.ngram_weight = ngram_weight

    def check(self, outputs: List[str]) -> ConsistencyResult:
        """Check consistency across multiple outputs for the same prompt.

        Args:
            outputs: List of 2-3 output strings from repeated calls.

        Returns:
            ConsistencyResult with similarity scores and flagged segments.
        """
        if len(outputs) < 2:
            return ConsistencyResult(
                consistent=True,
                similarity_score=1.0,
                ngram_overlap=1.0,
                entity_agreement=1.0,
                divergent_segments=[],
                total_samples=len(outputs),
            )

        # Pairwise n-gram overlap
        ngram_scores = []
        for i in range(len(outputs) - 1):
            score = self._ngram_overlap(outputs[i], outputs[i + 1])
            ngram_scores.append(score)
        avg_ngram = sum(ngram_scores) / len(ngram_scores)

        # Entity-level agreement
        entity_scores = []
        for i in range(len(outputs) - 1):
            score = self._entity_agreement(outputs[i], outputs[i + 1])
            entity_scores.append(score)
        avg_entity = sum(entity_scores) / len(entity_scores)

        # Combined score
        similarity = (
            self.ngram_weight * avg_ngram +
            self.entity_weight * avg_entity
        )

        # Find divergent segments
        divergent = []
        if similarity < self.similarity_threshold and len(outputs) == 2:
            divergent = self._find_divergent_sentences(outputs[0], outputs[1])

        return ConsistencyResult(
            consistent=similarity >= self.similarity_threshold,
            similarity_score=round(similarity, 4),
            ngram_overlap=round(avg_ngram, 4),
            entity_agreement=round(avg_entity, 4),
            divergent_segments=divergent[:5],  # top 5
            total_samples=len(outputs),
        )

    def _ngram_overlap(self, text1: str, text2: str) -> float:
        """Calculate n-gram overlap ratio between two texts."""
        tokens1 = self._tokenize(text1)
        tokens2 = self._tokenize(text2)

        if not tokens1 or not tokens2:
            return 0.0

        ngrams1 = self._to_ngrams(tokens1)
        ngrams2 = self._to_ngrams(tokens2)

        if not ngrams1 or not ngrams2:
            return 0.0

        intersection = ngrams1 & ngrams2
        union = ngrams1 | ngrams2

        return len(intersection) / len(union) if union else 0.0

    def _entity_agreement(self, text1: str, text2: str) -> float:
        """Calculate entity-level agreement between two texts."""
        entities1 = self._extract_entities(text1)
        entities2 = self._extract_entities(text2)

        all_entities = entities1 | entities2
        if not all_entities:
            return 1.0

        intersection = entities1 & entities2
        return len(intersection) / len(all_entities)

    def _tokenize(self, text: str) -> List[str]:
        """Simple tokenization: lowercase + split on non-alphanumeric."""
        text = text.lower()
        return [t for t in re.findall(r'[a-z0-9]+', text) if len(t) > 1]

    def _to_ngrams(self, tokens: List[str]) -> Set[Tuple[str, ...]]:
        """Convert tokens to n-grams."""
        if len(tokens) < self.n:
            return set()
        return {tuple(tokens[i:i + self.n]) for i in range(len(tokens) - self.n + 1)}

    def _extract_entities(self, text: str) -> Set[str]:
        """Extract key entities from text: numbers, paths, capitalized terms."""
        entities: Set[str] = set()

        # Extract using entity patterns
        for pattern in _ENTITY_PATTERNS.values():
            for match in pattern.finditer(text):
                entities.add(match.group().lower())

        # Extract capitalized multi-word phrases (potential proper nouns)
        capitalized = re.findall(r'\b(?:[A-Z][a-z]+\s?){2,}', text)
        for c in capitalized:
            entities.add(c.strip().lower())

        return entities

    def _find_divergent_sentences(self, text1: str, text2: str) -> List[str]:
        """Find sentences that differ significantly between two texts."""
        sentences1 = set(re.split(r'[.!?]\s+', text1.lower()))
        sentences2 = set(re.split(r'[.!?]\s+', text2.lower()))

        # Sentences unique to each text
        only_in_1 = sentences1 - sentences2
        only_in_2 = sentences2 - sentences1

        divergent = list(only_in_1 | only_in_2)
        return [s.strip()[:120] for s in divergent if len(s.strip()) > 20]


# ═══════════════════════════════════════════════════════════════════
# 2. FactualityVerifier
# ═══════════════════════════════════════════════════════════════════


@dataclass
class Claim:
    """A single claim extracted from output that needs verification."""
    text: str
    claim_type: ClaimType
    confidence: float  # 0-1, self-assessed confidence
    position: int  # character position in output


@dataclass
class FactualityResult:
    """Result of factuality verification."""
    passed: bool
    total_claims: int
    suspicious_claims: List[Claim] = field(default_factory=list)
    high_confidence_suspicious: int = 0
    warnings: List[str] = field(default_factory=list)


class FactualityVerifier:
    """Lightweight factuality verification using pattern matching + common sense.

    Does NOT make network calls. Uses:
      - Pattern-based detection of common hallucination forms
      - Common-sense rule checks (impossible dates, invalid percentages)
      - Context-based API/path validation
      - Entity consistency with provided ground-truth context

    Usage:
        verifier = FactualityVerifier()
        result = verifier.verify(
            output="The API DELETE /users/{id} removes a user.",
            context={"available_apis": ["GET /users", "POST /users"]},
        )
        if result.suspicious_claims:
            investigate(result.suspicious_claims)
    """

    def __init__(self,
                 confidence_threshold: float = 0.7,
                 context_checks: bool = True):
        self.confidence_threshold = confidence_threshold
        self.context_checks = context_checks

    def verify(self, output: str, context: Dict[str, Any] = None) -> FactualityResult:
        """Verify factual claims in output.

        Args:
            output: The LLM-generated output text.
            context: Optional ground truth context (known APIs, entities, etc.).

        Returns:
            FactualityResult with suspicious claims and warnings.
        """
        context = context or {}
        warnings: List[str] = []
        suspicious: List[Claim] = []

        # Step 1: Extract claims from output
        claims = self._extract_claims(output)

        # Step 2: Pattern-based hallucination detection
        for claim in claims:
            issues = self._check_claim_patterns(claim)
            if issues:
                suspicious.append(claim)
                warnings.extend(issues)

        # Step 3: Common-sense rule checks
        for check_fn in _COMMON_SENSE_CHECKS:
            try:
                if check_fn.__name__.startswith('_check_api') and context:
                    result = check_fn(output, context)
                else:
                    result = check_fn(output)
                if result:
                    warnings.append(result)
            except Exception:
                continue

        # Step 4: Pattern-based checks on full output
        for pattern_name, pattern in _HALLUCINATION_PATTERNS.items():
            matches = pattern.findall(output)
            if matches:
                warnings.append(f"Hallucination pattern '{pattern_name}' matched: {matches[:3]}")

        high_conf_suspicious = sum(
            1 for c in suspicious if c.confidence >= self.confidence_threshold
        )

        return FactualityResult(
            passed=len(suspicious) == 0 and len(warnings) == 0,
            total_claims=len(claims),
            suspicious_claims=suspicious,
            high_confidence_suspicious=high_conf_suspicious,
            warnings=warnings,
        )

    def _extract_claims(self, text: str) -> List[Claim]:
        """Extract claims from text with type classification."""
        claims: List[Claim] = []

        # Split into sentences
        sentences = re.split(r'(?<=[.!?])\s+', text)

        for i, sentence in enumerate(sentences):
            claim_type = self._classify_claim(sentence)
            confidence = self._estimate_confidence(sentence)
            if claim_type != ClaimType.OTHER or confidence > 0.3:
                claims.append(Claim(
                    text=sentence.strip(),
                    claim_type=claim_type,
                    confidence=confidence,
                    position=i,
                ))

        return claims

    def _classify_claim(self, text: str) -> ClaimType:
        """Classify the type of claim in a sentence."""
        # Check for API references
        if re.search(r'\b(?:GET|POST|PUT|DELETE|PATCH)\s+/', text):
            return ClaimType.API_REFERENCE

        # Check for file paths
        if re.search(r'(?:[A-Za-z]:\\|~/|/usr/|/etc/|/var/)', text):
            return ClaimType.FILE_PATH

        # Check for code snippets
        if re.search(r'```|def\s+\w+\s*\(|import\s+\w+|class\s+\w+', text):
            return ClaimType.CODE_SNIPPET

        # Check for statistical claims
        if re.search(r'\b(?:average|mean|median|percent|rate|ratio|distribution)\b', text, re.IGNORECASE):
            return ClaimType.STATISTICAL

        # Check for comparative claims
        if re.search(r'\b(?:compared|versus|higher|lower|faster|slower|more than|less than)\b', text, re.IGNORECASE):
            return ClaimType.COMPARATIVE

        # Check for numerical claims
        if re.search(r'\b\d+(?:\.\d+)?\s*(?:%|ms|seconds?|MB|GB|KB|rps|users?|records?|items?)\b', text, re.IGNORECASE):
            return ClaimType.FACTUAL_NUMBER

        # Check for date claims
        if re.search(r'\b(?:19|20)\d{2}\b', text):
            return ClaimType.FACTUAL_DATE

        # Check for entity references
        if re.search(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b', text):
            return ClaimType.ENTITY_REFERENCE

        return ClaimType.OTHER

    def _estimate_confidence(self, text: str) -> float:
        """Estimate self-confidence based on hedging language."""
        # Hedging words reduce confidence
        hedging = [
            'possibly', 'maybe', 'likely', 'probably', 'might', 'could be',
            'appears to', 'seems', 'suggests', 'potentially', 'approximately',
            'roughly', 'around', 'about', 'estimated', 'believed',
        ]
        text_lower = text.lower()
        hedge_count = sum(1 for h in hedging if h in text_lower)

        # Definitive words increase confidence
        definitive = [
            'always', 'never', 'certainly', 'definitely', 'exactly',
            'precisely', 'undoubtedly', 'absolutely', 'is', 'are',
        ]
        def_count = sum(1 for d in definitive if d in text_lower)

        base = 0.5
        base -= hedge_count * 0.1
        base += def_count * 0.05
        return max(0.0, min(1.0, base))

    def _check_claim_patterns(self, claim: Claim) -> List[str]:
        """Check a single claim against known hallucination patterns."""
        issues = []

        for pattern_name, pattern in _HALLUCINATION_PATTERNS.items():
            if pattern.search(claim.text):
                issues.append(f"Claim matches '{pattern_name}' pattern: {claim.text[:100]}")

        return issues


# ═══════════════════════════════════════════════════════════════════
# 3. HallucinationDetector
# ═══════════════════════════════════════════════════════════════════


@dataclass
class HallucinationResult:
    """Comprehensive hallucination detection result.

    Attributes:
        risk_score: 0-1, higher = more likely hallucination.
        risk_level: LOW / MEDIUM / HIGH / CRITICAL.
        issues: Specific detected problems.
        suggested_action: What to do (block / approve / flag / pass).
        consistency: Consistency check result (if multi-sample).
        factuality: Factuality check result.
    """
    risk_score: float
    risk_level: RiskLevel
    issues: List[str] = field(default_factory=list)
    suggested_action: str = ""
    consistency: Optional[ConsistencyResult] = None
    factuality: Optional[FactualityResult] = None
    blocked: bool = False

    def __repr__(self) -> str:
        return (
            f"HallucinationResult(risk={self.risk_score:.2f}, "
            f"level={self.risk_level.value}, issues={len(self.issues)})"
        )


class HallucinationDetector:
    """Unified hallucination detection for LLM outputs.

    Combines consistency checking (multi-sample) and factuality verification
    into a single risk score, then assigns a risk level and suggested action.

    Integration with GovernanceAgent:
      - HIGH risk → triggers governance check (needs_approval)
      - CRITICAL risk → blocks output delivery

    Usage:
        detector = HallucinationDetector(governance_agent=gov)

        # Single-sample detection (factuality only)
        result = detector.detect(output="...", context={...})

        # Multi-sample detection (consistency + factuality)
        result = detector.detect_multi(
            outputs=["...", "..."], context={...}
        )
    """

    def __init__(self,
                 consistency_checker: OutputConsistencyChecker = None,
                 factuality_verifier: FactualityVerifier = None,
                 governance_agent=None,
                 enabled: bool = True,
                 risk_thresholds: Dict[str, float] = None):
        self.consistency_checker = consistency_checker or OutputConsistencyChecker()
        self.factuality_verifier = factuality_verifier or FactualityVerifier()
        self.governance_agent = governance_agent
        self.enabled = enabled
        self.risk_thresholds = risk_thresholds or {
            "LOW": 0.25,
            "MEDIUM": 0.50,
            "HIGH": 0.75,
            "CRITICAL": 0.90,
        }

    def detect(self, output: str, context: Dict[str, Any] = None) -> HallucinationResult:
        """Run hallucination detection on a single output sample.

        Single-sample detection uses factuality verification only
        (no consistency comparison possible).

        Args:
            output: The LLM-generated output text.
            context: Optional ground truth context.

        Returns:
            HallucinationResult with risk score and level.
        """
        if not self.enabled:
            return HallucinationResult(
                risk_score=0.0,
                risk_level=RiskLevel.LOW,
                issues=[],
                suggested_action="pass",
            )

        context = context or {}
        issues: List[str] = []

        # Factuality verification
        fact_result = self.factuality_verifier.verify(output, context)

        # Calculate risk score from factuality results
        risk_score = self._calc_risk_score(
            factuality=fact_result,
            consistency=None,
        )

        # Determine risk level
        risk_level = self._classify_risk(risk_score)

        # Collect all issues
        issues.extend(fact_result.warnings)
        for claim in fact_result.suspicious_claims:
            issues.append(f"Suspicious claim ({claim.claim_type.value}): {claim.text[:120]}")

        # Determine suggested action
        suggested_action, blocked = self._determine_action(risk_level, output, context)

        return HallucinationResult(
            risk_score=round(risk_score, 4),
            risk_level=risk_level,
            issues=issues,
            suggested_action=suggested_action,
            factuality=fact_result,
            blocked=blocked,
        )

    def detect_multi(self,
                     outputs: List[str],
                     context: Dict[str, Any] = None) -> HallucinationResult:
        """Run hallucination detection on multiple output samples.

        Uses both consistency checking and factuality verification.

        Args:
            outputs: 2-3 output strings from repeated calls.
            context: Optional ground truth context.

        Returns:
            HallucinationResult with risk score and level.
        """
        if not self.enabled:
            return HallucinationResult(
                risk_score=0.0,
                risk_level=RiskLevel.LOW,
                issues=[],
                suggested_action="pass",
            )

        context = context or {}
        issues: List[str] = []

        # Consistency checking
        consistency_result = self.consistency_checker.check(outputs)
        if not consistency_result.consistent:
            issues.append(
                f"Low consistency across {consistency_result.total_samples} samples "
                f"(score={consistency_result.similarity_score:.2f})"
            )
            for seg in consistency_result.divergent_segments:
                issues.append(f"Divergent segment: {seg}")

        # Factuality verification (on first output, since it's what user sees)
        fact_result = self.factuality_verifier.verify(outputs[0] if outputs else "", context)

        risk_score = self._calc_risk_score(
            factuality=fact_result,
            consistency=consistency_result,
        )

        risk_level = self._classify_risk(risk_score)

        issues.extend(fact_result.warnings)
        for claim in fact_result.suspicious_claims:
            issues.append(f"Suspicious claim ({claim.claim_type.value}): {claim.text[:120]}")

        suggested_action, blocked = self._determine_action(risk_level, outputs[0] if outputs else "", context)

        return HallucinationResult(
            risk_score=round(risk_score, 4),
            risk_level=risk_level,
            issues=issues,
            suggested_action=suggested_action,
            consistency=consistency_result,
            factuality=fact_result,
            blocked=blocked,
        )

    def _calc_risk_score(self,
                         factuality: FactualityResult,
                         consistency: ConsistencyResult = None) -> float:
        """Calculate combined risk score from factuality and consistency.

        Factuality contributes 60%, consistency 40%.
        """
        # Factuality sub-score: based on suspicious claims ratio + warning count
        if factuality.total_claims == 0:
            fact_score = 0.0
        else:
            claim_ratio = factuality.high_confidence_suspicious / max(factuality.total_claims, 1)
            fact_base = min(0.3 * len(factuality.warnings) + 0.7 * claim_ratio, 1.0)
            # Heavy penalty for high-confidence suspicious claims
            if factuality.high_confidence_suspicious >= 2:
                fact_base = max(fact_base, 0.8)
            fact_score = fact_base

        # Consistency sub-score: inverse of similarity
        if consistency is None:
            cons_score = 0.0
        else:
            # Low similarity = high risk
            cons_score = 1.0 - consistency.similarity_score
            # Boost if significant divergence
            if consistency.divergent_segments:
                cons_score = max(cons_score, 0.6)

        # Weighted combination
        combined = 0.6 * fact_score + 0.4 * cons_score

        # Floor: if both factuality and consistency show issues
        if fact_score > 0 and cons_score > 0:
            combined = max(combined, 0.5)

        return min(combined, 1.0)

    def _classify_risk(self, score: float) -> RiskLevel:
        """Map risk score to risk level."""
        if score >= self.risk_thresholds["CRITICAL"]:
            return RiskLevel.CRITICAL
        elif score >= self.risk_thresholds["HIGH"]:
            return RiskLevel.HIGH
        elif score >= self.risk_thresholds["MEDIUM"]:
            return RiskLevel.MEDIUM
        else:
            return RiskLevel.LOW

    def _determine_action(self,
                          risk_level: RiskLevel,
                          output: str,
                          context: Dict[str, Any]) -> Tuple[str, bool]:
        """Determine suggested action and whether to block.

        Also triggers governance check for HIGH/CRITICAL.
        """
        if risk_level == RiskLevel.LOW:
            return "pass", False
        elif risk_level == RiskLevel.MEDIUM:
            return "flag_for_review", False
        elif risk_level == RiskLevel.HIGH:
            # Trigger governance
            self._notify_governance("HIGH", output, context)
            return "needs_approval", False
        else:  # CRITICAL
            self._notify_governance("CRITICAL", output, context)
            return "block", True

    def _notify_governance(self, level: str, output: str, context: Dict[str, Any]):
        """Notify GovernanceAgent of high-risk hallucination detection."""
        if self.governance_agent is None:
            return
        try:
            self.governance_agent.validate(
                action={
                    "tool": "hallucination_detector",
                    "risk_level": level,
                },
                context={
                    "output_snippet": output[:200],
                    "context": str(context)[:500],
                },
            )
        except Exception as e:
            logger.debug("Governance notification failed: %s", e)

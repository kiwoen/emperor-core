"""Context Compression Engine for managing long conversation histories.

Implements rule-based context compression strategies to solve token window
overflow and attention decay in long-running chat sessions. All strategies
are LLM-free, running purely on statistical and heuristic rules.

Strategy overview:
    - summarize: first-sentence extraction + sentence ranking
    - extract: keyword density scoring for key sentence selection
    - prune: redundancy detection to remove duplicate/near-duplicate content
    - hybrid: combination of extract + prune

Usage:
    from huanxin.context_compressor import ContextCompressor, CompressionStrategy

    compressor = ContextCompressor()
    compressed = compressor.compress(messages, CompressionStrategy.HYBRID, 2000)
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Optional


class CompressionStrategy(Enum):
    """Available compression strategies.

    Attributes:
        SUMMARIZE: Extract first sentence from each message and rank by importance.
        EXTRACT: Score sentences by keyword density and keep top-N.
        PRUNE: Detect and remove near-duplicate or redundant content.
        HYBRID: Combine EXTRACT + PRUNE for aggressive compression.
    """

    SUMMARIZE = auto()
    EXTRACT = auto()
    PRUNE = auto()
    HYBRID = auto()

    def __str__(self) -> str:
        return self.name.lower()


# ══════════════════════════════════════════════════════════════════
# Token estimation
# ══════════════════════════════════════════════════════════════════

# English: ~1.3 tokens per word (empirically observed for GPT-family models)
_EN_TOKENS_PER_WORD = 1.3
# Chinese: ~2 characters per token (CJK tokenizer average)
_ZH_TOKENS_PER_CHAR = 0.5  # 2 chars/token → 0.5 tokens/char

# Unicode ranges for CJK characters
_CJK_RANGES = [
    (0x4E00, 0x9FFF),   # CJK Unified Ideographs
    (0x3400, 0x4DBF),   # CJK Unified Ideographs Extension A
    (0x20000, 0x2A6DF), # CJK Unified Ideographs Extension B
    (0xF900, 0xFAFF),   # CJK Compatibility Ideographs
    (0x2F800, 0x2FA1F), # CJK Compatibility Ideographs Supplement
    (0x3000, 0x303F),   # CJK Symbols and Punctuation
    (0xFF00, 0xFFEF),   # Halfwidth and Fullwidth Forms
    (0x3040, 0x309F),   # Hiragana
    (0x30A0, 0x30FF),   # Katakana
    (0xAC00, 0xD7AF),   # Hangul Syllables
]


def _is_cjk(char: str) -> bool:
    """Check whether a character falls within CJK ranges."""
    cp = ord(char)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def estimate_tokens(text: str) -> int:
    """Estimate token count for a given text string.

    Heuristic:
    - English / Latin text: ~1.3 tokens per word
    - Chinese / CJK text: ~2 characters per token (i.e. 0.5 tokens per char)
    - Mixed text: counts both and sums

    Args:
        text: Input string to estimate token count for.

    Returns:
        Estimated token count as an integer.
    """
    if not text:
        return 0

    cjk_chars = 0
    non_cjk_parts: list[str] = []

    current = ""
    for ch in text:
        if _is_cjk(ch):
            if current:
                non_cjk_parts.append(current)
                current = ""
            cjk_chars += 1
        else:
            current += ch
    if current:
        non_cjk_parts.append(current)

    # Count non-CJK words
    non_cjk_str = " ".join(non_cjk_parts)
    words = len(non_cjk_str.split()) if non_cjk_str.strip() else 0

    return int(cjk_chars * _ZH_TOKENS_PER_CHAR + words * _EN_TOKENS_PER_WORD)


def estimate_messages_tokens(messages: list[dict]) -> int:
    """Estimate total token count for a list of messages.

    Each message is expected to be a dict with at least a 'content' key.
    """
    total = 0
    for msg in messages:
        content = msg.get("content", "") if isinstance(msg, dict) else str(msg)
        total += estimate_tokens(content)
    return total


# ══════════════════════════════════════════════════════════════════
# Strategy implementations
# ══════════════════════════════════════════════════════════════════


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences using punctuation boundaries.

    Handles English (./!/?) and Chinese (。/！/？) sentence endings.
    """
    # Split on sentence-ending punctuation while keeping delimiters
    parts = re.split(r'(?<=[.!?。！？\n])\s*', text)
    return [p.strip() for p in parts if p.strip()]


def _token_count(text: str) -> int:
    """Alias for estimate_tokens for internal use."""
    return estimate_tokens(text)


# ── SUMMARIZE strategy ─────────────────────────────────────────────


def _summarize_message(content: str, target_tokens: int) -> str:
    """Produce a summary of a single message content.

    Algorithm: first-sentence extraction + sentence ranking by position.
    Early sentences tend to carry the most important information.
    """
    if not content.strip():
        return content

    sentences = _split_sentences(content)
    if not sentences:
        return content

    # If already within budget, return as-is
    if _token_count(content) <= target_tokens:
        return content

    # Score sentences by position (earlier = higher weight, exponential decay)
    result: list[str] = []
    used = 0

    for i, sent in enumerate(sentences):
        sent_tokens = _token_count(sent)
        if used + sent_tokens <= target_tokens:
            result.append(sent)
            used += sent_tokens
        else:
            # Try to include even if partial budget left, but prefer full sentences
            if used == 0:
                # At least include the first sentence even if it exceeds budget
                result.append(sent)
            break

    return " ".join(result) if result else sentences[0]


# ── EXTRACT strategy ───────────────────────────────────────────────


# Common stop words (English + Chinese) to exclude from keyword analysis
_STOP_WORDS: set[str] = {
    # English
    "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "can", "shall",
    "i", "me", "my", "we", "us", "our", "you", "your", "he",
    "him", "his", "she", "her", "it", "its", "they", "them",
    "their", "this", "that", "these", "those", "to", "of", "in",
    "for", "on", "with", "at", "by", "from", "as", "into",
    "through", "during", "before", "after", "above", "below",
    "between", "and", "but", "or", "nor", "not", "so", "yet",
    "if", "then", "else", "when", "where", "why", "how", "all",
    "each", "every", "both", "few", "more", "most", "other",
    "some", "such", "no", "only", "own", "same", "than", "too",
    "very", "just", "about", "also",
    # Chinese
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人",
    "都", "一", "一个", "上", "也", "很", "到", "说", "要", "去",
    "你", "会", "着", "没", "看", "好", "自己", "这", "他", "她",
    "它", "们", "那", "些", "什么", "怎么", "哪", "吗", "吧",
    "啊", "呢", "哦", "嗯", "哈", "呀", "嘛", "咯",
}


def _tokenize_simple(text: str) -> list[str]:
    """Simple tokenizer: split on non-alphanumeric + non-CJK boundaries."""
    tokens: list[str] = []
    current = ""
    for ch in text:
        if ch.isalnum() or _is_cjk(ch):
            current += ch
        else:
            if current:
                tokens.append(current.lower())
                current = ""
    if current:
        tokens.append(current.lower())
    return tokens


def _keyword_density_score(sentence: str, global_keywords: Counter) -> float:
    """Score a sentence by the density of globally significant keywords."""
    tokens = _tokenize_simple(sentence)
    if not tokens:
        return 0.0

    score = 0.0
    for token in tokens:
        if token not in _STOP_WORDS and len(token) > 1:
            score += global_keywords.get(token, 0)
    return score / len(tokens)


def _extract_key_sentences(content: str, target_tokens: int) -> str:
    """Extract the most informative sentences based on keyword density."""
    if not content.strip():
        return content

    sentences = _split_sentences(content)
    if not sentences:
        return content

    if _token_count(content) <= target_tokens:
        return content

    # Build global keyword frequency from all sentences
    global_tokens: list[str] = []
    for sent in sentences:
        global_tokens.extend(_tokenize_simple(sent))

    global_keywords = Counter(
        t for t in global_tokens
        if t not in _STOP_WORDS and len(t) > 1
    )

    # Score each sentence
    scored: list[tuple[float, int, str]] = []
    for i, sent in enumerate(sentences):
        score = _keyword_density_score(sent, global_keywords)
        # Boost first and last sentences slightly (positional importance)
        if i == 0:
            score += 0.1
        elif i == len(sentences) - 1:
            score += 0.05
        scored.append((score, i, sent))

    # Sort by score descending, keep top sentences within budget
    scored.sort(key=lambda x: x[0], reverse=True)

    selected: list[tuple[int, str]] = []
    used = 0
    for score, idx, sent in scored:
        sent_tokens = _token_count(sent)
        if used + sent_tokens <= target_tokens:
            selected.append((idx, sent))
            used += sent_tokens
        if used >= target_tokens:
            break

    # Restore original order
    selected.sort(key=lambda x: x[0])
    result = [s for _, s in selected]
    return " ".join(result) if result else sentences[0]


# ── PRUNE strategy ─────────────────────────────────────────────────


def _jaccard_similarity(a: set, b: set) -> float:
    """Compute Jaccard similarity between two sets."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _sentence_token_set(sentence: str) -> set:
    """Convert a sentence to a set of meaningful tokens for comparison."""
    tokens = _tokenize_simple(sentence)
    return {t for t in tokens if t not in _STOP_WORDS and len(t) > 1}


def _prune_redundant(content: str, target_tokens: int,
                     similarity_threshold: float = 0.6) -> str:
    """Remove redundant/near-duplicate sentences.

    Uses Jaccard similarity on token sets to detect near-duplicates.
    Keeps the first occurrence of semantically similar sentences.
    """
    if not content.strip():
        return content

    sentences = _split_sentences(content)
    if not sentences:
        return content

    if _token_count(content) <= target_tokens:
        return content

    # Keep sentences that are sufficiently different from previously kept ones
    kept: list[str] = []
    kept_token_sets: list[set] = []
    used = 0

    for sent in sentences:
        sent_tokens = _token_count(sent)
        ts = _sentence_token_set(sent)

        # Check similarity against all previously kept sentences
        is_redundant = False
        for prev_ts in kept_token_sets:
            if _jaccard_similarity(ts, prev_ts) > similarity_threshold:
                is_redundant = True
                break

        if not is_redundant:
            if used + sent_tokens <= target_tokens:
                kept.append(sent)
                kept_token_sets.append(ts)
                used += sent_tokens
            elif used == 0:
                kept.append(sent)
                break
            else:
                break

    return " ".join(kept) if kept else sentences[0]


# ── HYBRID strategy ────────────────────────────────────────────────


def _hybrid_compress(content: str, target_tokens: int) -> str:
    """Apply extract + prune in sequence for aggressive compression."""
    # First prune to remove redundant content
    pruned = _prune_redundant(content, target_tokens * 2)
    # Then extract the most informative sentences
    return _extract_key_sentences(pruned, target_tokens)


# ══════════════════════════════════════════════════════════════════
# ContextCompressor
# ══════════════════════════════════════════════════════════════════


@dataclass
class CompressionResult:
    """Result of a compression operation.

    Attributes:
        original_tokens: Token count before compression.
        compressed_tokens: Token count after compression.
        strategy: The strategy used.
        messages: The compressed message list.
    """

    original_tokens: int
    compressed_tokens: int
    strategy: CompressionStrategy
    messages: list[dict]


class ContextCompressor:
    """LLM-free context compression using heuristic rule-based strategies.

    Designed for long conversation histories where token window overflow
    and attention decay degrade response quality. All strategies operate
    purely on text statistics without requiring LLM calls.

    Key behaviors:
        - System messages (role='system') are NEVER compressed.
        - Most recent N messages are preserved as-is (keep_recent).
        - Intermediate messages between system and recent are the
          compression target.

    Usage:
        compressor = ContextCompressor(keep_recent=4)
        result = compressor.compress(messages, CompressionStrategy.HYBRID, 2000)
        compressed_messages = result.messages
    """

    def __init__(self, keep_recent: int = 4) -> None:
        """Initialize the compressor.

        Args:
            keep_recent: Number of most recent messages to always preserve
                         without compression. Default 4 (2 exchanges).
        """
        self.keep_recent = max(keep_recent, 2)

    # ── Public API ─────────────────────────────────────────────────

    def compress(
        self,
        messages: list[dict],
        strategy: CompressionStrategy,
        target_tokens: int,
    ) -> CompressionResult:
        """Compress a list of messages to fit within target token budget.

        Args:
            messages: List of message dicts with at least 'role' and 'content'.
            strategy: Compression strategy to apply.
            target_tokens: Target token budget for compressed messages.

        Returns:
            CompressionResult with compressed messages and statistics.
        """
        if not messages:
            return CompressionResult(0, 0, strategy, [])

        original_tokens = estimate_messages_tokens(messages)

        # Partition messages
        system_msgs, compressible_msgs, recent_msgs = self._partition(messages)

        recent_tokens = estimate_messages_tokens(recent_msgs)
        system_tokens = estimate_messages_tokens(system_msgs)
        available_budget = target_tokens - recent_tokens - system_tokens

        if available_budget <= 0 or not compressible_msgs:
            # Budget too small, compress recent messages too as fallback
            available_budget = max(target_tokens - system_tokens, 0)
            compressible_msgs = recent_msgs + compressible_msgs
            recent_msgs = []

        # Apply strategy to compressible messages
        compressed = self._apply_strategy(
            compressible_msgs, strategy, available_budget
        )

        # Reassemble
        final_messages = system_msgs + compressed + recent_msgs
        compressed_tokens = estimate_messages_tokens(final_messages)

        return CompressionResult(
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens,
            strategy=strategy,
            messages=final_messages,
        )

    def auto_compress(
        self,
        messages: list[dict],
        max_tokens: int,
    ) -> CompressionResult:
        """Automatically select best strategy and compress to target window.

        Heuristic:
        - If slightly over budget (< 20%): use PRUNE (least destructive)
        - If moderately over (20-50%): use EXTRACT
        - If significantly over (> 50%): use HYBRID

        Args:
            messages: List of message dicts.
            max_tokens: Maximum token budget.

        Returns:
            CompressionResult with strategy selected automatically.
        """
        current_tokens = estimate_messages_tokens(messages)
        if current_tokens <= max_tokens:
            return CompressionResult(
                original_tokens=current_tokens,
                compressed_tokens=current_tokens,
                strategy=CompressionStrategy.PRUNE,  # no-op, but indicate no change
                messages=messages,
            )

        ratio = current_tokens / max_tokens

        if ratio <= 1.2:
            strategy = CompressionStrategy.PRUNE
        elif ratio <= 2.0:
            strategy = CompressionStrategy.EXTRACT
        else:
            strategy = CompressionStrategy.HYBRID

        return self.compress(messages, strategy, max_tokens)

    # ── Internal helpers ───────────────────────────────────────────

    def _partition(
        self, messages: list[dict]
    ) -> tuple[list[dict], list[dict], list[dict]]:
        """Partition messages into system, compressible, and recent groups.

        Returns:
            (system_msgs, compressible_msgs, recent_msgs)
        """
        # System messages (always preserved)
        system_msgs = [m for m in messages if m.get("role") == "system"]

        # Non-system messages
        non_system = [m for m in messages if m.get("role") != "system"]

        # Recent messages preserved as-is
        recent_count = min(self.keep_recent, len(non_system))
        recent_msgs = non_system[-recent_count:] if recent_count > 0 else []
        compressible_msgs = non_system[:-recent_count] if recent_count > 0 else non_system

        return system_msgs, compressible_msgs, recent_msgs

    def _apply_strategy(
        self,
        messages: list[dict],
        strategy: CompressionStrategy,
        budget: int,
    ) -> list[dict]:
        """Apply the compression strategy to a list of messages."""
        if not messages:
            return []

        # Merge all compressible messages into one text block for processing
        combined = "\n\n".join(
            f"[{m.get('role', 'unknown')}]: {m.get('content', '')}"
            for m in messages
        )

        # Per-message budget (split evenly)
        per_msg_budget = max(budget // len(messages), 50)

        strategy_fn = {
            CompressionStrategy.SUMMARIZE: _summarize_message,
            CompressionStrategy.EXTRACT: _extract_key_sentences,
            CompressionStrategy.PRUNE: _prune_redundant,
            CompressionStrategy.HYBRID: _hybrid_compress,
        }[strategy]

        # Compress each message individually with its per-message budget
        compressed: list[dict] = []
        for m in messages:
            content = m.get("content", "")
            compressed_content = strategy_fn(content, per_msg_budget)
            compressed.append({**m, "content": compressed_content})

        return compressed
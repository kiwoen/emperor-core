"""Tests for jarvis/context_compressor.py — LLM-free context compression engine."""

from __future__ import annotations

import pytest

from jarvis.context_compressor import (
    CompressionResult,
    CompressionStrategy,
    ContextCompressor,
    estimate_tokens,
    estimate_messages_tokens,
)


# ══════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════


@pytest.fixture
def compressor():
    """Fresh ContextCompressor with keep_recent=2 for predictable tests."""
    return ContextCompressor(keep_recent=2)


@pytest.fixture
def sample_messages():
    """A representative conversation with system message, history, and recent exchanges."""
    return [
        {"role": "system", "content": "You are a helpful assistant."},
        {
            "role": "user",
            "content": (
                "Can you explain what machine learning is? "
                "I want to understand the basic concepts and how they work. "
                "Machine learning is a subset of artificial intelligence that "
                "enables systems to learn and improve from experience without "
                "being explicitly programmed."
            ),
        },
        {
            "role": "assistant",
            "content": (
                "Machine learning is a branch of AI where computers learn from data. "
                "Instead of following explicit instructions, they identify patterns "
                "and make decisions with minimal human intervention. "
                "There are three main types: supervised learning, unsupervised learning, "
                "and reinforcement learning."
            ),
        },
        {
            "role": "user",
            "content": (
                "What about deep learning? How is it different from traditional "
                "machine learning? Deep learning uses neural networks with many layers "
                "to learn from large amounts of data."
            ),
        },
        {
            "role": "assistant",
            "content": (
                "Deep learning is a specialized subset of machine learning that uses "
                "multi-layered neural networks. It excels at processing unstructured "
                "data like images, audio, and text. Traditional ML often requires "
                "manual feature engineering, while deep learning automatically "
                "extracts features."
            ),
        },
        {
            "role": "user",
            "content": "Thanks, that's helpful. Can you give me a simple example?",
        },
        {
            "role": "assistant",
            "content": "Sure! A good example is image classification — training a model to recognize cats vs dogs from labeled photos.",
        },
    ]


@pytest.fixture
def long_messages():
    """Messages with substantial content to trigger compression."""
    long_text = (
        "The quick brown fox jumps over the lazy dog. "
        + "Machine learning algorithms require large amounts of training data. "
        + "The quick brown fox jumps over the lazy dog. "
        + "Deep neural networks can learn hierarchical representations. "
        + "Training data must be carefully curated and preprocessed. "
        + "Deep neural networks can learn hierarchical representations from raw data. "
        + "Data preprocessing includes normalization, tokenization, and augmentation. "
        + "The performance of models depends heavily on data quality. "
        + "Hyperparameter tuning is essential for optimal model performance. "
        + "Cross-validation helps prevent overfitting in machine learning models."
    ) * 3  # 30 sentences, many near-duplicates

    return [
        {"role": "system", "content": "System instruction."},
        {"role": "user", "content": long_text},
        {"role": "assistant", "content": long_text},
        {"role": "user", "content": "Recent question?"},
        {"role": "assistant", "content": "Recent answer."},
    ]


# ══════════════════════════════════════════════════════════════════
# Test 1: estimate_tokens — English text
# ══════════════════════════════════════════════════════════════════


def test_estimate_tokens_english():
    """English text token estimation: ~1.3 tokens per word."""
    result = estimate_tokens("Hello world this is a test")
    # 6 words * 1.3 = 7.8 → 7
    assert 6 <= result <= 9, f"Expected ~7-8, got {result}"

    result_empty = estimate_tokens("")
    assert result_empty == 0


# ══════════════════════════════════════════════════════════════════
# Test 2: estimate_tokens — Chinese text
# ══════════════════════════════════════════════════════════════════


def test_estimate_tokens_chinese():
    """Chinese text token estimation: ~2 chars per token."""
    result = estimate_tokens("你好世界这是一段中文测试文本")
    # 12 chars / 2 = 6 tokens
    assert 5 <= result <= 7, f"Expected ~6, got {result}"


# ══════════════════════════════════════════════════════════════════
# Test 3: estimate_tokens — mixed Chinese + English
# ══════════════════════════════════════════════════════════════════


def test_estimate_tokens_mixed():
    """Mixed Chinese-English text should sum both estimates."""
    result = estimate_tokens("你好 world 测试 test")
    # 4 CJK chars → 2 tokens + 2 words → 2.6 ≈ 4-5 tokens
    assert result > 0


# ══════════════════════════════════════════════════════════════════
# Test 4: estimate_messages_tokens
# ══════════════════════════════════════════════════════════════════


def test_estimate_messages_tokens():
    """Token estimation across multiple messages."""
    msgs = [
        {"role": "user", "content": "Hello world"},
        {"role": "assistant", "content": "Hi there"},
    ]
    total = estimate_messages_tokens(msgs)
    assert total > 0


# ══════════════════════════════════════════════════════════════════
# Test 5: system message preservation
# ══════════════════════════════════════════════════════════════════


def test_system_message_preserved(compressor, sample_messages):
    """System messages must never be compressed or removed."""
    result = compressor.compress(
        sample_messages, CompressionStrategy.SUMMARIZE, target_tokens=50
    )
    system_msgs = [m for m in result.messages if m["role"] == "system"]
    assert len(system_msgs) == 1
    assert system_msgs[0]["content"] == "You are a helpful assistant."


# ══════════════════════════════════════════════════════════════════
# Test 6: recent messages preserved
# ══════════════════════════════════════════════════════════════════


def test_recent_messages_preserved(compressor, sample_messages):
    """The most recent N messages should be preserved as-is."""
    result = compressor.compress(
        sample_messages, CompressionStrategy.HYBRID, target_tokens=200
    )
    # Last 2 non-system messages are the most recent exchange
    assert result.messages[-2]["role"] == "user"
    assert result.messages[-1]["role"] == "assistant"
    assert "image classification" in result.messages[-1]["content"]


# ══════════════════════════════════════════════════════════════════
# Test 7: compress with SUMMARIZE reduces token count
# ══════════════════════════════════════════════════════════════════


def test_summarize_reduces_tokens(compressor, long_messages):
    """SUMMARIZE strategy should reduce total token count."""
    original = estimate_messages_tokens(long_messages)
    result = compressor.compress(
        long_messages, CompressionStrategy.SUMMARIZE, target_tokens=150
    )
    compressed = estimate_messages_tokens(result.messages)
    assert compressed <= original, (
        f"Compressed {compressed} should be <= original {original}"
    )
    assert result.strategy == CompressionStrategy.SUMMARIZE


# ══════════════════════════════════════════════════════════════════
# Test 8: compress with PRUNE removes near-duplicates
# ══════════════════════════════════════════════════════════════════


def test_prune_removes_duplicates(compressor, long_messages):
    """PRUNE strategy should detect and remove near-duplicate sentences."""
    result = compressor.compress(
        long_messages, CompressionStrategy.PRUNE, target_tokens=150
    )
    compressed = estimate_messages_tokens(result.messages)
    original = estimate_messages_tokens(long_messages)
    # PRUNE should reduce significantly when there are many duplicates
    assert compressed < original, (
        f"PRUNE compressed {compressed} >= original {original}"
    )


# ══════════════════════════════════════════════════════════════════
# Test 9: compress with EXTRACT preserves key content
# ══════════════════════════════════════════════════════════════════


def test_extract_preserves_keywords(compressor):
    """EXTRACT should keep sentences with high keyword density."""
    msgs = [
        {"role": "system", "content": "Helper."},
        {
            "role": "user",
            "content": (
                "Machine learning is important. "
                "The weather is nice today. "
                "Deep learning with neural networks and machine learning is powerful. "
                "I like pizza. "
                "Neural networks and deep learning revolutionize AI."
            ),
        },
        {"role": "assistant", "content": "Recent reply."},
    ]
    result = compressor.compress(
        msgs, CompressionStrategy.EXTRACT, target_tokens=60
    )
    # The compressed content should retain ML-related sentences
    compressed_content = " ".join(
        m["content"] for m in result.messages if m["role"] != "system"
    )
    assert "machine" in compressed_content.lower() or "neural" in compressed_content.lower()


# ══════════════════════════════════════════════════════════════════
# Test 10: auto_compress selects appropriate strategy
# ══════════════════════════════════════════════════════════════════


def test_auto_compress_strategy_selection(compressor, long_messages):
    """auto_compress should automatically pick strategy based on overshoot ratio."""
    original = estimate_messages_tokens(long_messages)

    # Small overshoot → PRUNE
    result_small = compressor.auto_compress(long_messages, max_tokens=original)
    assert result_small.strategy == CompressionStrategy.PRUNE
    assert result_small.original_tokens == result_small.compressed_tokens

    # Large overshoot → HYBRID
    result_large = compressor.auto_compress(
        long_messages, max_tokens=original // 3
    )
    assert result_large.strategy in (
        CompressionStrategy.HYBRID,
        CompressionStrategy.EXTRACT,
    )


# ══════════════════════════════════════════════════════════════════
# Test 11: empty messages handled gracefully
# ══════════════════════════════════════════════════════════════════


def test_empty_messages(compressor):
    """Empty message list should return empty result without error."""
    result = compressor.compress([], CompressionStrategy.PRUNE, 100)
    assert result.messages == []
    assert result.original_tokens == 0
    assert result.compressed_tokens == 0


# ══════════════════════════════════════════════════════════════════
# Test 12: CompressionResult fields
# ══════════════════════════════════════════════════════════════════


def test_compression_result_fields(compressor, sample_messages):
    """CompressionResult should contain all expected fields."""
    result = compressor.compress(
        sample_messages, CompressionStrategy.HYBRID, target_tokens=200
    )
    assert isinstance(result, CompressionResult)
    assert result.original_tokens > 0
    assert result.compressed_tokens >= 0
    assert result.strategy == CompressionStrategy.HYBRID
    assert isinstance(result.messages, list)
    assert all("role" in m and "content" in m for m in result.messages)


# ══════════════════════════════════════════════════════════════════
# Test 13: keep_recent respects configuration
# ══════════════════════════════════════════════════════════════════


def test_keep_recent_configuration():
    """Custom keep_recent should control how many recent messages are preserved."""
    msgs = [
        {"role": "user", "content": f"Message {i}"} for i in range(10)
    ]
    comp = ContextCompressor(keep_recent=3)
    result = comp.compress(msgs, CompressionStrategy.SUMMARIZE, target_tokens=200)

    # With keep_recent=3, messages 7, 8, 9 should be preserved verbatim
    preserved = result.messages[-3:]
    for i, m in enumerate(preserved):
        assert m["content"] == f"Message {7 + i}"


# ══════════════════════════════════════════════════════════════════
# Test 14: compression is idempotent-ish (no crash on re-compress)
# ══════════════════════════════════════════════════════════════════


def test_compression_no_crash_on_recompress(compressor, long_messages):
    """Compressing already-compressed output should not crash."""
    first = compressor.compress(
        long_messages, CompressionStrategy.HYBRID, target_tokens=100
    )
    second = compressor.compress(
        first.messages, CompressionStrategy.PRUNE, target_tokens=50
    )
    assert second.original_tokens > 0
    assert len(second.messages) >= 1

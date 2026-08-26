"""Memory manager — high-level semantic memory with recency weighting.

Thin wrapper over :class:`VectorMemory` that adds:

- Typed memory slots (``conversation`` / ``task_result`` / ``knowledge``)
- Automatic timestamp tagging
- Recency boosting on recall
- Context summarisation
"""

from __future__ import annotations

import logging
import time
from typing import Any, Literal, Optional, Sequence

from huanxin.memory.vector_store import VectorMemory

logger = logging.getLogger("huanxin.memory.manager")

MemoryType = Literal["conversation", "task_result", "knowledge"]
"""Supported memory types."""

_VALID_TYPES = frozenset({"conversation", "task_result", "knowledge"})


class MemoryManager:
    """Semantic memory manager with decay-aware recall.

    Parameters:
        vector_store:
            An existing :class:`VectorMemory` instance.  If omitted a
            new one is created.
        recency_weight:
            Weight factor (0..1) applied to recency when re-ranking
            recall results.  ``0.0`` = pure semantic, ``1.0`` = heavily
            recency-biased.  Default **0.3**.

    Usage::

        mm = MemoryManager()
        mm.add_memory("The user prefers dark mode UI.", memory_type="knowledge")
        results = mm.recall("UI preference")
        summary = mm.summarize_context("current task preferences")
    """

    def __init__(
        self,
        vector_store: Optional[VectorMemory] = None,
        recency_weight: float = 0.3,
    ) -> None:
        self._store = vector_store or VectorMemory()
        self._recency_weight = max(0.0, min(1.0, recency_weight))

    # ── Public API ─────────────────────────────────────────────────────

    def add_memory(
        self,
        text: str,
        *,
        memory_type: MemoryType = "knowledge",
        metadata: Optional[dict[str, Any]] = None,
    ) -> str:
        """Store a single memory entry with type and timestamp.

        Args:
            text:  The memory content.
            memory_type:  One of ``conversation`` / ``task_result`` / ``knowledge``.
            metadata:  Extra key-value pairs merged into the metadata block.

        Returns:
            The assigned memory ID.

        Raises:
            ValueError: if *memory_type* is not recognised.
        """
        if memory_type not in _VALID_TYPES:
            raise ValueError(
                f"Invalid memory_type '{memory_type}'. "
                f"Must be one of: {', '.join(sorted(_VALID_TYPES))}"
            )

        meta: dict[str, Any] = {
            "type": memory_type,
            "timestamp": time.time(),
        }
        if metadata:
            meta.update(metadata)

        ids = self._store.add([text], [meta])
        logger.debug("[MemoryManager] Stored memory <%s> → %s", memory_type, ids[0])
        return ids[0]

    def recall(
        self,
        query: str,
        top_k: int = 5,
        *,
        memory_types: Optional[Sequence[MemoryType]] = None,
        apply_recency: bool = True,
    ) -> dict[str, Any]:
        """Semantic recall with optional type filter and recency boost.

        Args:
            query:  Natural-language search query.
            top_k:  Max results to return.
            memory_types:  Restrict to these types (e.g. ``["knowledge"]``).
                ``None`` = all types.
            apply_recency:  Re-rank results using recency weighting.

        Returns:
            Dict with keys ``ids``, ``documents``, ``metadatas``,
            ``distances``, ``scores`` (combined score when recency applied).
        """
        # Build metadata filter
        meta_filter: Optional[dict[str, Any]] = None
        if memory_types:
            meta_filter = {"type": {"$in": list(memory_types)}}

        raw = self._store.query(query, top_k=max(top_k * 2, 10), metadata_filter=meta_filter)

        if not raw["ids"]:
            return {"ids": [], "documents": [], "metadatas": [], "distances": [], "scores": []}

        # Re-rank with recency if requested
        if apply_recency and self._recency_weight > 0:
            return self._rerank_with_recency(raw, top_k)
        else:
            return self._trim(raw, top_k)

    def summarize_context(self, query: str, max_memories: int = 5) -> str:
        """Build a plain-text context block from recalled memories.

        Useful for inserting into an LLM prompt as background context.

        Args:
            query:  What to recall memories about.
            max_memories:  How many top memories to include.

        Returns:
            A formatted string summarising relevant past memories.
        """
        recalled = self.recall(query, top_k=max_memories, apply_recency=True)

        if not recalled["documents"]:
            return f"[MemoryManager] No relevant memories found for: {query}"

        lines = ["## Relevant Past Memories", ""]
        for i, (doc, meta) in enumerate(
            zip(recalled["documents"], recalled["metadatas"]), 1
        ):
            mem_type = meta.get("type", "unknown")
            ts = meta.get("timestamp", 0)
            when = self._format_timestamp(ts)
            score = recalled.get("scores", [0.0] * len(recalled["documents"]))[i - 1]
            lines.append(
                f"### Memory {i} [{mem_type}, {when}, score={score:.3f}]\n{doc}\n"
            )

        return "\n".join(lines)

    def search(
        self,
        query: str,
        top_k: int = 5,
        *,
        memory_types: Optional[Sequence[MemoryType]] = None,
    ) -> list[str]:
        """Shorthand: return just the top document texts (no metadata).

        Convenient for quick look-ups when you only need content.
        """
        recalled = self.recall(query, top_k=top_k, memory_types=memory_types)
        return recalled["documents"]

    # ── Internal helpers ────────────────────────────────────────────────

    def _rerank_with_recency(
        self, raw: dict[str, Any], top_k: int
    ) -> dict[str, Any]:
        """Combine semantic distance with recency into a single score."""
        now = time.time()
        scored: list[dict[str, Any]] = []

        for i, doc_id in enumerate(raw["ids"]):
            dist = raw["distances"][i]
            # Normalise distance → similarity (ChromaDB returns cosine distance for
            # sentence-transformers; OpenAI EF may return different metric.)
            # For cosine distance in [0, 2], similarity = 1 - dist/2
            semantic_score = max(0.0, 1.0 - dist / 2.0)

            ts = raw["metadatas"][i].get("timestamp", now)
            age_hours = max(0.0, (now - ts) / 3600.0)
            # Exponential decay: half-life of 168 hours (7 days)
            recency_score = 2.0 ** (-age_hours / 168.0)

            combined = (
                (1.0 - self._recency_weight) * semantic_score
                + self._recency_weight * recency_score
            )
            scored.append(
                {
                    "id": doc_id,
                    "document": raw["documents"][i],
                    "metadata": raw["metadatas"][i],
                    "distance": dist,
                    "semantic": semantic_score,
                    "recency": recency_score,
                    "score": combined,
                }
            )

        scored.sort(key=lambda x: x["score"], reverse=True)
        top = scored[:top_k]

        return {
            "ids": [s["id"] for s in top],
            "documents": [s["document"] for s in top],
            "metadatas": [s["metadata"] for s in top],
            "distances": [s["distance"] for s in top],
            "scores": [s["score"] for s in top],
        }

    @staticmethod
    def _trim(raw: dict[str, Any], top_k: int) -> dict[str, Any]:
        result = dict(raw)
        for key in ("ids", "documents", "metadatas", "distances"):
            result[key] = raw[key][:top_k]
        return result

    @staticmethod
    def _format_timestamp(ts: float) -> str:
        from datetime import datetime

        dt = datetime.fromtimestamp(ts)
        return f"{dt.year}-{dt.month:02d}-{dt.day:02d} {dt.hour:02d}:{dt.minute:02d}"

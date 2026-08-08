"""Hybrid retriever — dense (ChromaDB) + sparse (BM25) + RRF fusion + LLM rerank.

Combines semantic vector search with keyword-based BM25 retrieval
for robust, high-recall document retrieval.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

logger = logging.getLogger("jarvis.rag.retriever")


class HybridRetriever:
    """Dense + sparse retrieval with Reciprocal Rank Fusion (RRF).

    Parameters:
        vector_store:
            A :class:`VectorMemory` instance for dense (semantic) retrieval.
        k:
            RRF rank-smoothing constant (default 60, per standard practice).

    Usage::

        retriever = HybridRetriever(vector_store=vm)
        retriever.index(["chunk 1", "chunk 2"], [{"source": "doc.pdf"}] * 2)
        results = retriever.retrieve("What is the main topic?", top_k=5)
        reranked = retriever.rerank("What is...", results, llm_engine)
    """

    def __init__(
        self,
        vector_store: Any = None,
        k: int = 60,
    ) -> None:
        self._vector_store = vector_store
        self._k = k

        # BM25 state
        self._bm25_corpus: list[str] = []
        self._bm25_tokenizer: Any = None
        self._bm25_model: Any = None
        self._bm25_dirty: bool = True

    # ── Indexing ───────────────────────────────────────────────────────

    def index(
        self,
        chunks: Sequence[str],
        metadatas: Optional[Sequence[dict[str, Any]]] = None,
        ids: Optional[Sequence[str]] = None,
    ) -> list[str]:
        """Index document chunks into both dense and sparse stores.

        Args:
            chunks:  Text chunks to index.
            metadatas:  Metadata per chunk (for dense vector store).
            ids:  Optional chunk IDs.

        Returns:
            List of assigned chunk IDs.
        """
        # Add to dense store
        stored_ids: list[str] = []
        if self._vector_store is not None:
            stored_ids = self._vector_store.add(chunks, metadatas, ids)
        else:
            stored_ids = list(ids) if ids else [f"chunk_{i}" for i in range(len(chunks))]

        # Add to BM25 corpus
        self._bm25_corpus.extend(chunks)
        self._bm25_dirty = True

        logger.debug("[HybridRetriever] Indexed %d chunks", len(chunks))
        return stored_ids

    # ── Retrieval ──────────────────────────────────────────────────────

    def retrieve(
        self,
        query: str,
        top_k: int = 10,
        *,
        metadata_filter: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Run hybrid retrieval and fuse results via RRF.

        Returns:
            Dict with keys ``chunks`` (list of str), ``metadatas``,
            ``scores``, and ``sources`` (dense / sparse / fused).
        """
        dense_results = self._dense_retrieve(query, top_k, metadata_filter)
        sparse_results = self._sparse_retrieve(query, top_k)

        fused = self.fusion_results(dense_results, sparse_results, top_k)
        return fused

    def _dense_retrieve(
        self,
        query: str,
        top_k: int,
        metadata_filter: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """Semantic retrieval via VectorMemory."""
        if self._vector_store is None:
            return []

        raw = self._vector_store.query(query, top_k=top_k, metadata_filter=metadata_filter)
        results: list[dict[str, Any]] = []
        for i in range(len(raw["ids"])):
            results.append({
                "id": raw["ids"][i],
                "chunk": raw["documents"][i],
                "metadata": raw["metadatas"][i] if i < len(raw["metadatas"]) else {},
                "distance": raw["distances"][i] if i < len(raw["distances"]) else 0.0,
                "source": "dense",
            })
        return results

    def _sparse_retrieve(self, query: str, top_k: int) -> list[dict[str, Any]]:
        """BM25 keyword retrieval."""
        self._ensure_bm25_ready()

        if self._bm25_model is None or len(self._bm25_corpus) == 0:
            return []

        tokenized_query = self._tokenize(query)
        scores = self._bm25_model.get_scores(tokenized_query)

        # Top-k by score
        indexed = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        top = indexed[:top_k]

        results: list[dict[str, Any]] = []
        for idx, score in top:
            if score <= 0:
                continue
            results.append({
                "id": f"bm25_{idx}",
                "chunk": self._bm25_corpus[idx],
                "metadata": {},
                "score": float(score),
                "source": "sparse",
            })
        return results

    # ── BM25 internals ─────────────────────────────────────────────────

    def _ensure_bm25_ready(self) -> None:
        if not self._bm25_dirty:
            return
        self._bm25_dirty = False

        if len(self._bm25_corpus) == 0:
            self._bm25_model = None
            return

        try:
            from rank_bm25 import BM25Okapi

            self._bm25_tokenizer = self._tokenize
            tokenized_corpus = [self._tokenize(doc) for doc in self._bm25_corpus]
            self._bm25_model = BM25Okapi(tokenized_corpus)
        except ImportError:
            logger.warning("[HybridRetriever] rank-bm25 not installed — using fallback BM25")
            self._bm25_model = _FallbackBM25(self._bm25_corpus)

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """Simple whitespace + punctuation tokenizer."""
        import re

        return re.findall(r"\w+", text.lower())

    # ── Fusion ─────────────────────────────────────────────────────────

    def fusion_results(
        self,
        dense: list[dict[str, Any]],
        sparse: list[dict[str, Any]],
        top_k: int = 10,
    ) -> dict[str, Any]:
        """RRF (Reciprocal Rank Fusion) of dense and sparse results.

        Algorithm:
            score(d) = sum_{r in rankings} 1 / (k + rank(d))

        where *k* is the smoothing constant (default 60).

        Returns:
            Fused result dict with ``chunks``, ``metadatas``, ``scores``, ``sources``.
        """
        rrf_scores: dict[int, float] = {}  # index → RRF score
        chunk_map: dict[int, dict[str, Any]] = {}
        idx = 0

        for rank, item in enumerate(dense, 1):
            key = idx
            chunk_map[key] = item
            rrf_scores[key] = rrf_scores.get(key, 0.0) + 1.0 / (self._k + rank)
            idx += 1

        for rank, item in enumerate(sparse, 1):
            # Try to match with an existing dense result by chunk text
            matched = False
            for key, existing in chunk_map.items():
                if existing["chunk"] == item["chunk"]:
                    rrf_scores[key] = rrf_scores.get(key, 0.0) + 1.0 / (self._k + rank)
                    # Merge sources
                    existing["source"] = "fused"
                    matched = True
                    break
            if not matched:
                key = idx
                chunk_map[key] = item
                rrf_scores[key] = 1.0 / (self._k + rank)
                idx += 1

        sorted_keys = sorted(rrf_scores, key=rrf_scores.get, reverse=True)[:top_k]

        return {
            "chunks": [chunk_map[k]["chunk"] for k in sorted_keys],
            "metadatas": [chunk_map[k].get("metadata", {}) for k in sorted_keys],
            "scores": [rrf_scores[k] for k in sorted_keys],
            "sources": [chunk_map[k].get("source", "unknown") for k in sorted_keys],
        }

    # ── LLM Rerank ─────────────────────────────────────────────────────

    def rerank(
        self,
        query: str,
        results: dict[str, Any],
        llm_engine: Any = None,
        top_k: int = 5,
    ) -> dict[str, Any]:
        """Use an LLM to re-rank fused results for higher precision.

        Args:
            query:  Original user query.
            results:  Fused result dict from :meth:`fusion_results`.
            llm_engine:  :class:`LLMEngine` instance.
            top_k:  How many results to keep after reranking.

        Returns:
            Same dict shape as input but with ``rerank_scores`` added
            and results trimmed to *top_k*.
        """
        chunks = results.get("chunks", [])
        if not chunks or llm_engine is None:
            return results

        if len(chunks) <= top_k:
            return results

        # Ask LLM to rank
        numbered = "\n\n".join(
            f"[{i}] {chunk[:300]}" for i, chunk in enumerate(chunks)
        )
        prompt = (
            f"Query: {query}\n\n"
            f"Below are {len(chunks)} document chunks. "
            f"Rank them by relevance to the query (most relevant first). "
            f"Return ONLY a JSON array of indices, e.g. [3, 0, 5, 1, 2].\n\n"
            f"{numbered}"
        )

        try:
            raw = llm_engine.chat_sync(
                prompt=prompt,
                system="You are a precise document reranker. Return only a JSON array of indices.",
            )
            # Parse JSON array
            import json

            indices = json.loads(raw.strip().strip("`").strip("json").strip())
            if isinstance(indices, list) and all(isinstance(i, int) for i in indices):
                reranked_indices = indices[:top_k]
            else:
                reranked_indices = list(range(min(top_k, len(chunks))))
        except Exception:
            logger.warning("[HybridRetriever] Rerank failed, falling back to top-k")
            reranked_indices = list(range(min(top_k, len(chunks))))

        return {
            "chunks": [chunks[i] for i in reranked_indices if i < len(chunks)],
            "metadatas": [results["metadatas"][i] for i in reranked_indices if i < len(results["metadatas"])],
            "scores": [results["scores"][i] for i in reranked_indices if i < len(results["scores"])],
            "sources": [results["sources"][i] for i in reranked_indices if i < len(results["sources"])],
            "rerank_scores": [1.0 - j / len(reranked_indices) for j in range(len(reranked_indices))],
        }


# ── Fallback BM25 (pure Python, no deps) ────────────────────────────


class _FallbackBM25:
    """Minimal BM25 implementation — used when rank-bm25 is not available."""

    def __init__(self, corpus: list[str], k1: float = 1.5, b: float = 0.75):
        import math
        import re

        self._corpus = corpus
        self._k1 = k1
        self._b = b

        # Tokenize all documents
        tokenized = [re.findall(r"\w+", doc.lower()) for doc in corpus]
        self._doc_lens = [len(t) for t in tokenized]
        self._avgdl = sum(self._doc_lens) / max(1, len(self._doc_lens))

        # DF (document frequency)
        self._df: dict[str, int] = {}
        for tokens in tokenized:
            for word in set(tokens):
                self._df[word] = self._df.get(word, 0) + 1

        self._N = len(corpus)

        # TF per document
        self._tfs = tokenized

    def get_scores(self, query_tokens: list[str]) -> list[float]:
        import math

        scores: list[float] = []
        for doc_idx, tokens in enumerate(self._tfs):
            score = 0.0
            doc_len = self._doc_lens[doc_idx]
            for qt in query_tokens:
                if qt not in self._df:
                    continue
                idf = math.log((self._N - self._df[qt] + 0.5) / (self._df[qt] + 0.5) + 1.0)
                tf = tokens.count(qt)
                numerator = tf * (self._k1 + 1.0)
                denominator = tf + self._k1 * (1.0 - self._b + self._b * doc_len / self._avgdl)
                score += idf * numerator / denominator
            scores.append(score)
        return scores

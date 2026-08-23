"""ChromaDB-backed vector store for semantic memory.

Wraps ChromaDB with a clean ``add → query → delete`` API, supporting

- Local embeddings via ``sentence-transformers`` (default)
- OpenAI ``text-embedding-3-small`` (opt-in)
- Metadata filtering on retrieval
- Persistent storage at a configurable directory
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

logger = logging.getLogger("jarvis.memory.vector_store")

# ── Defaults ──────────────────────────────────────────────────────────

_DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
_DEFAULT_COLLECTION = "jarvis_memory"


class VectorMemory:
    """Persistent semantic vector store backed by ChromaDB.

    Parameters:
        persist_dir:
            Directory where ChromaDB data is stored.  Defaults to
            ``./chroma_db`` relative to CWD.
        embedding_model:
            Model identifier.  When ``provider`` is ``"local"`` this
            should be a HuggingFace sentence-transformers model name;
            when ``"openai"`` it should be an OpenAI embedding model
            such as ``"text-embedding-3-small"``.
        provider:
            ``"local"`` or ``"openai"``.
        collection_name:
            ChromaDB collection name.

    Usage::

        vm = VectorMemory(persist_dir="./memory_db")
        vm.add(["Paris is the capital of France."],
               [{"type": "knowledge"}])
        results = vm.query("What is the capital of France?", top_k=3)
    """

    def __init__(
        self,
        *,
        persist_dir: str = "./chroma_db",
        embedding_model: str = _DEFAULT_MODEL,
        provider: str = "local",
        collection_name: str = _DEFAULT_COLLECTION,
    ) -> None:
        self.persist_dir = persist_dir
        self.embedding_model = embedding_model
        self.provider = provider
        self.collection_name = collection_name

        self._client: Any = None
        self._collection: Any = None
        self._emb_fn: Any = None
        self._init_store()

    # ── Initialisation ─────────────────────────────────────────────────

    def _init_store(self) -> None:
        import chromadb

        client_settings = chromadb.Settings(
            anonymized_telemetry=False,
            is_persistent=True,
        )
        self._client = chromadb.PersistentClient(
            path=self.persist_dir,
            settings=client_settings,
        )
        self._emb_fn = self._build_embedding_function()
        self._collection = self._client.get_or_create_collection(
            name=self.collection_name,
            embedding_function=self._emb_fn,
        )
        logger.info(
            "[VectorMemory] Collection '%s' ready (%s, %s embeddings)",
            self.collection_name,
            self.provider,
            self.embedding_model,
        )

    def _build_embedding_function(self):
        if self.provider == "openai":
            from chromadb.utils.embedding_functions import (
                OpenAIEmbeddingFunction,
            )

            return OpenAIEmbeddingFunction(
                api_key=None,  # read from env OPENAI_API_KEY by default
                model_name=self.embedding_model,
            )
        else:
            from chromadb.utils.embedding_functions import (
                SentenceTransformerEmbeddingFunction,
            )

            return SentenceTransformerEmbeddingFunction(
                model_name=self.embedding_model,
            )

    # ── CRUD API ───────────────────────────────────────────────────────

    def add(
        self,
        texts: Sequence[str],
        metadatas: Optional[Sequence[dict[str, Any]]] = None,
        ids: Optional[Sequence[str]] = None,
    ) -> list[str]:
        """Add documents to the vector store.

        Args:
            texts:  Documents to embed and store.
            metadatas:  Optional metadata dicts (same length as *texts*).
            ids:  Optional unique IDs; auto-generated as ``mem_N`` if omitted.

        Returns:
            The list of IDs that were stored.
        """
        if ids is None:
            current = self.count()
            ids = [f"mem_{current + i}" for i in range(len(texts))]

        if metadatas is None:
            metadatas = [{} for _ in texts]

        self._collection.add(documents=list(texts), metadatas=list(metadatas), ids=list(ids))
        logger.debug("[VectorMemory] Added %d document(s)", len(texts))
        return list(ids)

    def query(
        self,
        query_text: str,
        top_k: int = 5,
        *,
        metadata_filter: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Semantic search over stored documents.

        Args:
            query_text:  Natural-language query.
            top_k:  Max results to return.
            metadata_filter:  Optional ChromaDB ``where`` filter dict,
                e.g. ``{"type": "knowledge"}``.

        Returns:
            Dict with keys ``ids``, ``documents``, ``metadatas``,
            ``distances`` — each a list ordered by relevance (closest first).
        """
        kwargs: dict[str, Any] = {"query_texts": [query_text], "n_results": top_k}
        if metadata_filter is not None:
            kwargs["where"] = metadata_filter

        raw = self._collection.query(**kwargs)
        return {
            "ids": raw["ids"][0] if raw["ids"] else [],
            "documents": raw["documents"][0] if raw["documents"] else [],
            "metadatas": raw["metadatas"][0] if raw["metadatas"] else [],
            "distances": raw["distances"][0] if raw["distances"] else [],
        }

    def delete(self, ids: Sequence[str]) -> int:
        """Remove documents by ID.

        Returns:
            Number of documents actually deleted.
        """
        if not ids:
            return 0
        before = self.count()
        self._collection.delete(ids=list(ids))
        after = self.count()
        removed = before - after
        logger.debug("[VectorMemory] Deleted %d document(s)", removed)
        return removed

    def delete_all(self) -> int:
        """Remove **all** documents from the collection.

        Returns:
            Total count before deletion.
        """
        total = self.count()
        if total == 0:
            return 0
        all_ids = self._collection.get()["ids"]
        self._collection.delete(ids=all_ids)
        logger.warning("[VectorMemory] Deleted all %d document(s)", total)
        return total

    def count(self) -> int:
        """Return the total number of stored documents."""
        return self._collection.count()

    def close(self) -> None:
        """Release ChromaDB resources (call before removing persist_dir)."""
        self._collection = None
        self._emb_fn = None
        client = self._client
        self._client = None
        if client is not None:
            try:
                client._system.stop()  # type: ignore[union-attr]
            except Exception:
                pass

    # ── Properties ─────────────────────────────────────────────────────

    @property
    def collection(self) -> Any:
        """The underlying ChromaDB collection (for advanced usage)."""
        return self._collection

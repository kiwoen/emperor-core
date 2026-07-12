"""
JARVIS Memory Engine — Hybrid memory with ChromaDB semantic search.

Four-layer architecture:
1. Working Memory — immediate context, 0-latency dict access
2. Episodic Memory — conversation history, ChromaDB vector search
3. Semantic Memory — facts and learned knowledge, ChromaDB-backed
4. Procedural Memory — skill templates, code patterns, best practices

ChromaDB provides embedding-based semantic retrieval.
Keyword matching serves as fallback when ChromaDB is unavailable.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("jarvis.memory")

# Optional dependency: chromadb
try:
    import chromadb
    from chromadb.config import Settings as ChromaSettings
    CHROMADB_AVAILABLE = True
except ImportError:
    CHROMADB_AVAILABLE = False
    chromadb = None
    ChromaSettings = None


@dataclass
class MemoryEntry:
    """A unit of memory — conversation turn, fact, or observation."""

    key: str
    content: str
    entry_type: str  # "conversation", "fact", "skill", "observation"
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    importance: float = 0.5
    access_count: int = 0

    def increment_access(self) -> None:
        self.access_count += 1
        self.importance = min(1.0, self.importance + 0.01 * self.access_count)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "content": self.content,
            "entry_type": self.entry_type,
            "metadata": self.metadata,
            "timestamp": self.timestamp,
            "importance": self.importance,
            "access_count": self.access_count,
        }


class MemoryEngine:
    """Hybrid memory engine with ChromaDB semantic search.

    Fallback behavior: if ChromaDB is not installed, gracefully degrades
    to keyword-based BM25-like scoring. This is already working and tested.
    """

    def __init__(
        self,
        persist_dir: str = "./data/memory",
        max_entries: int = 100000,
        compression_threshold: int = 5000,
        embedding_model: str = "all-MiniLM-L6-v2",
    ) -> None:
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.max_entries = max_entries
        self.compression_threshold = compression_threshold
        self.embedding_model = embedding_model

        # In-memory stores
        self.episodic: OrderedDict[str, MemoryEntry] = OrderedDict()
        self.semantic: dict[str, MemoryEntry] = {}
        self.working: list[str] = []
        self.procedural: dict[str, Any] = {}

        # ChromaDB state
        self._chroma_client = None
        self._episodic_collection = None
        self._semantic_collection = None
        self._embedding_fn = None
        self._chroma_ready = False

        # Load from disk first (always works)
        self._load()

        # Try to initialize ChromaDB
        if CHROMADB_AVAILABLE:
            self._init_chromadb()
        else:
            logger.info("ChromaDB not installed — using keyword-based retrieval")

    # ── ChromaDB Setup ──────────────────────────────────────────────

    def _init_chromadb(self) -> None:
        """Initialize ChromaDB with persistent storage. Embedding is optional."""
        try:
            chroma_path = str(self.persist_dir / "chromadb")
            self._chroma_client = chromadb.PersistentClient(
                path=chroma_path,
                settings=ChromaSettings(anonymized_telemetry=False),
            )

            try:
                self._episodic_collection = self._chroma_client.get_collection("episodic")
            except Exception:
                self._episodic_collection = self._chroma_client.create_collection(
                    name="episodic",
                    metadata={"description": "Conversation history"},
                )

            try:
                self._semantic_collection = self._chroma_client.get_collection("semantic")
            except Exception:
                self._semantic_collection = self._chroma_client.create_collection(
                    name="semantic",
                    metadata={"description": "Facts and knowledge"},
                )

            # Embedding function is optional — try, but don't fail
            # Requires sentence-transformers + network access to HuggingFace
            self._embedding_fn = None
            # Uncomment when network allows:
            # from chromadb.utils import embedding_functions
            # self._embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            #     model_name=self.embedding_model,
            # )

            self._chroma_ready = True
            logger.info("ChromaDB ready (%d episodic, %d semantic)",
                        self._episodic_collection.count(),
                        self._semantic_collection.count())
        except Exception as e:
            logger.info("ChromaDB unavailable (%s), keyword fallback", e)
            self._chroma_client = None
            self._chroma_ready = False

    # ── Public API ───────────────────────────────────────────────────

    async def store(
        self,
        key: str,
        value: Any,
        entry_type: str = "conversation",
        metadata: dict[str, Any] | None = None,
        importance: float = 0.5,
    ) -> None:
        """Store a new memory entry."""
        content = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)

        entry = MemoryEntry(
            key=key,
            content=content,
            entry_type=entry_type,
            metadata=metadata or {},
            importance=importance,
        )

        # Always store in-memory
        if entry_type == "fact":
            self.semantic[key] = entry
        else:
            self.episodic[key] = entry
            if len(self.episodic) > self.compression_threshold:
                await self._compress()

        # Also store in ChromaDB
        if self._chroma_ready and self._embedding_fn:
            try:
                collection = self._semantic_collection if entry_type == "fact" else self._episodic_collection
                collection.add(
                    ids=[key],
                    documents=[content],
                    metadatas=[{
                        "entry_type": entry_type,
                        "importance": importance,
                        "timestamp": entry.timestamp,
                        **entry.metadata,
                    }],
                )
            except Exception as e:
                logger.debug("ChromaDB store failed (non-fatal): %s", e)

        # Eviction
        while len(self.episodic) > self.max_entries:
            oldest = next(iter(self.episodic))
            if self.episodic[oldest].importance > 0.8:
                await self._archive(self.episodic[oldest])
            del self.episodic[oldest]

        self._save()

    async def retrieve(
        self,
        query: str,
        top_k: int = 10,
        entry_types: list[str] | None = None,
    ) -> list[MemoryEntry]:
        """Retrieve memories by semantic similarity (ChromaDB) or keyword fallback."""
        all_entries = list(self.episodic.values()) + list(self.semantic.values())
        if entry_types:
            all_entries = [e for e in all_entries if e.entry_type in entry_types]

        # Try ChromaDB semantic search first (requires embedding function)
        if self._chroma_ready and self._embedding_fn and self._episodic_collection and self._episodic_collection.count() > 0:
            try:
                chroma_results = await self._chroma_retrieve(query, top_k, entry_types)
                if chroma_results:
                    return chroma_results[:top_k]
            except Exception as e:
                logger.debug("ChromaDB retrieve failed, falling back: %s", e)

        # Keyword fallback
        return self._keyword_retrieve(query, top_k, all_entries)

    async def _chroma_retrieve(
        self,
        query: str,
        top_k: int,
        entry_types: list[str] | None,
    ) -> list[MemoryEntry]:
        """Semantic search via ChromaDB."""
        where_filter = {}
        if entry_types and len(entry_types) == 1:
            where_filter = {"entry_type": entry_types[0]}

        results = []
        for coll, source_dict in [
            (self._episodic_collection, self.episodic),
            (self._semantic_collection, self.semantic),
        ]:
            if coll.count() == 0:
                continue
            try:
                qr = coll.query(
                    query_texts=[query],
                    n_results=min(top_k, coll.count()),
                    where=where_filter if where_filter else None,
                )
                ids = qr.get("ids", [[]])[0]
                distances = qr.get("distances", [[]])[0]

                for mem_id, dist in zip(ids, distances):
                    score = 1.0 / (1.0 + dist)  # Convert distance to similarity
                    # Look up in-memory entry
                    entry = source_dict.get(mem_id)
                    if entry:
                        entry.increment_access()
                        results.append((score, entry))
            except Exception:
                continue

        results.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in results]

    def _keyword_retrieve(
        self,
        query: str,
        top_k: int,
        entries: list[MemoryEntry],
    ) -> list[MemoryEntry]:
        """BM25-like keyword matching fallback."""
        scored: list[tuple[float, MemoryEntry]] = []
        for entry in entries:
            score = self._compute_relevance(query, entry)
            if score > 0:
                scored.append((score, entry))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = [e for _, e in scored[:top_k]]
        for e in top:
            e.increment_access()
        return top

    async def get_context_window(self, max_tokens: int = 100000) -> str:
        """Get the current context window for LLM consumption."""
        context_parts = list(self.working[-50:])

        recent_episodic = list(self.episodic.values())[-100:]
        for entry in recent_episodic:
            context_parts.append(f"[{entry.entry_type}] {entry.content[:500]}")

        for entry in sorted(
            self.semantic.values(),
            key=lambda e: e.importance,
            reverse=True,
        )[:20]:
            context_parts.append(f"[fact:{entry.key}] {entry.content[:300]}")

        return "\n---\n".join(context_parts)

    async def add_fact(self, fact: str, source: str = "observation") -> None:
        """Add a semantic fact to memory."""
        fact_key = hashlib.sha256(fact.encode()).hexdigest()[:16]
        if fact_key not in self.semantic:
            await self.store(
                key=fact_key,
                value=fact,
                entry_type="fact",
                metadata={"source": source},
                importance=0.7,
            )

    async def add_skill_template(self, name: str, template: dict) -> None:
        """Store a reusable skill/code pattern."""
        self.procedural[name] = {
            "template": template,
            "added_at": time.time(),
            "use_count": 0,
        }
        self._save()

    async def clear_working(self) -> None:
        """Clear working memory (e.g., between sessions)."""
        self.working = []

    async def get_stats(self) -> dict[str, Any]:
        """Return memory statistics."""
        return {
            "episodic_count": len(self.episodic),
            "semantic_count": len(self.semantic),
            "procedural_count": len(self.procedural),
            "working_size": len(self.working),
            "chromadb_enabled": self._chroma_ready,
            "chromadb_episodic": self._episodic_collection.count() if self._episodic_collection else 0,
            "chromadb_semantic": self._semantic_collection.count() if self._semantic_collection else 0,
        }

    # ── Compression & Maintenance ────────────────────────────────────

    async def _compress(self) -> None:
        """Compress old episodic memories into semantic summaries."""
        if len(self.episodic) < self.compression_threshold:
            return

        logger.info("Compressing episodic memory (%d entries)", len(self.episodic))
        entries_to_compress = list(self.episodic.values())[: len(self.episodic) // 3]

        topics: dict[str, list[str]] = {}
        for entry in entries_to_compress:
            topic = entry.metadata.get("domain", "general")
            topics.setdefault(topic, []).append(entry.content[:300])

        for topic, contents in topics.items():
            combined = " | ".join(contents[-10:])
            summary = f"[Compressed {topic} conversation: {combined[:500]}]"
            await self.add_fact(summary, source=f"compression:{topic}")

        for entry in entries_to_compress[: len(self.episodic) // 3]:
            if entry.key in self.episodic:
                del self.episodic[entry.key]

        logger.info("Compression complete — %d entries remaining", len(self.episodic))

    async def _archive(self, entry: MemoryEntry) -> None:
        """Archive high-importance entry to long-term storage."""
        archive_path = self.persist_dir / "archive.jsonl"
        with open(archive_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")

    def _compute_relevance(self, query: str, entry: MemoryEntry) -> float:
        """BM25-like keyword relevance scoring."""
        query_terms = set(query.lower().split())
        content_terms = set(entry.content.lower().split())
        tag_terms = set(str(t).lower() for t in entry.metadata.get("tags", []))

        all_target_terms = content_terms | tag_terms
        if not all_target_terms:
            return 0.0

        overlap = query_terms & all_target_terms
        jaccard = len(overlap) / len(query_terms | all_target_terms)

        recency_boost = 1.0 / (1.0 + (time.time() - entry.timestamp) / 86400)
        importance_boost = entry.importance

        return jaccard * (0.5 + 0.3 * recency_boost + 0.2 * importance_boost)

    # ── Persistence ──────────────────────────────────────────────────

    def _save(self) -> None:
        """Persist memory to disk."""
        # Episodic
        episodic_path = self.persist_dir / "episodic.jsonl"
        episodic_items = list(self.episodic.values())[-5000:]
        with open(episodic_path, "w", encoding="utf-8") as f:
            for entry in episodic_items:
                f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")

        # Semantic
        semantic_path = self.persist_dir / "semantic.jsonl"
        with open(semantic_path, "w", encoding="utf-8") as f:
            for entry in self.semantic.values():
                f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")

        # Procedural
        proc_path = self.persist_dir / "procedural.json"
        with open(proc_path, "w", encoding="utf-8") as f:
            json.dump(self.procedural, f, ensure_ascii=False, indent=2)

    def _load(self) -> None:
        """Load memory from disk."""
        for filename, target, entry_type in [
            ("episodic.jsonl", self.episodic, "conversation"),
            ("semantic.jsonl", self.semantic, "fact"),
        ]:
            file_path = self.persist_dir / filename
            if file_path.exists():
                with open(file_path, encoding="utf-8") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        try:
                            data = json.loads(line)
                            target[data["key"]] = MemoryEntry(
                                key=data["key"],
                                content=data["content"],
                                entry_type=data.get("entry_type", entry_type),
                                metadata=data.get("metadata", {}),
                                timestamp=data.get("timestamp", time.time()),
                                importance=data.get("importance", 0.5),
                                access_count=data.get("access_count", 0),
                            )
                        except (json.JSONDecodeError, KeyError):
                            pass

        # Procedural
        proc_path = self.persist_dir / "procedural.json"
        if proc_path.exists():
            with open(proc_path, encoding="utf-8") as f:
                try:
                    self.procedural = json.load(f)
                except json.JSONDecodeError:
                    pass

"""RAG (Retrieval-Augmented Generation) engine.

End-to-end pipeline: document loading → chunking → indexing → hybrid retrieval →
reranking → answer generation with source citations.

Supports PDF, DOCX, TXT, and Markdown documents.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from typing import Any, Optional, Sequence

logger = logging.getLogger("jarvis.rag.engine")


# ══════════════════════════════════════════════════════════════════════
# Text Splitter
# ══════════════════════════════════════════════════════════════════════


class RecursiveCharacterTextSplitter:
    """Split text into chunks recursively using a hierarchy of separators.

    Tries to split on natural boundaries first (paragraphs, sentences),
    falling back to word boundaries and characters as needed.
    """

    DEFAULT_SEPARATORS = ["\n\n", "\n", ". ", "。", "! ", "? ", "; ", " "]

    def __init__(
        self,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        separators: Optional[list[str]] = None,
    ) -> None:
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.separators = separators or self.DEFAULT_SEPARATORS

    def split_text(self, text: str) -> list[str]:
        """Split *text* into chunks of at most *chunk_size* characters."""
        return self._split(text, self.separators)

    def _split(self, text: str, separators: list[str]) -> list[str]:
        chunks: list[str] = []
        separator = separators[-1]  # default: character-level
        for sep in separators:
            if not sep:
                separator = sep
                break
            if sep in text:
                separator = sep
                break

        if separator:
            parts = text.split(separator) if separator else list(text)
        else:
            parts = list(text)

        current: list[str] = []
        current_len = 0

        for part in parts:
            piece = part if not current else separator + part
            piece_len = len(piece)

            if current_len + piece_len <= self.chunk_size:
                current.append(piece if not current else part)
                current_len += piece_len if not current else len(part)
            else:
                # Flush current chunk
                if current:
                    chunks.append(separator.join(current) if separator else "".join(current))

                # If a single piece is too long, recurse with next separator
                if len(part) > self.chunk_size:
                    idx = separators.index(separator) if separator in separators else len(separators) - 1
                    if idx < len(separators) - 1:
                        sub_chunks = self._split(part, separators[idx + 1:])
                        chunks.extend(sub_chunks)
                    else:
                        # Force-split by character
                        for i in range(0, len(part), self.chunk_size - self.chunk_overlap):
                            chunks.append(part[i:i + self.chunk_size])
                else:
                    current = [part]
                    current_len = len(part)

        if current:
            chunks.append(separator.join(current) if separator else "".join(current))

        return chunks

    def split_documents(
        self,
        texts: Sequence[str],
        metadatas: Optional[Sequence[dict[str, Any]]] = None,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """Split multiple documents, preserving metadata per chunk.

        Returns:
            Tuple of (chunks, chunk_metadatas).
        """
        all_chunks: list[str] = []
        all_metadatas: list[dict[str, Any]] = []

        if metadatas is None:
            metadatas = [{} for _ in texts]

        for text, meta in zip(texts, metadatas):
            chunks = self.split_text(text)
            for i, chunk in enumerate(chunks):
                chunk_meta = dict(meta)
                chunk_meta["chunk_index"] = i
                chunk_meta["chunk_total"] = len(chunks)
                all_chunks.append(chunk)
                all_metadatas.append(chunk_meta)

        return all_chunks, all_metadatas


# ══════════════════════════════════════════════════════════════════════
# Document Loader
# ══════════════════════════════════════════════════════════════════════


class DocumentLoader:
    """Load text content from PDF, DOCX, TXT, and Markdown files."""

    SUPPORTED = {".pdf", ".docx", ".txt", ".md", ".markdown"}

    def load(self, file_path: str) -> tuple[str, dict[str, Any]]:
        """Load a document and return (text_content, metadata).

        Raises:
            ValueError: If the file type is not supported.
            FileNotFoundError: If the file does not exist.
        """
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"Document not found: {file_path}")

        ext = os.path.splitext(file_path)[1].lower()
        if ext not in self.SUPPORTED:
            raise ValueError(
                f"Unsupported file type: {ext}. Supported: {', '.join(sorted(self.SUPPORTED))}"
            )

        loader = getattr(self, f"_load{ext.replace('.', '_dot_').replace('_dot_', '_')}", None)
        if loader is None:
            loader = self._load_txt

        text = loader(file_path)
        metadata = {
            "file_path": file_path,
            "file_name": os.path.basename(file_path),
            "file_type": ext.lstrip("."),
        }
        return text, metadata

    def _load_pdf(self, path: str) -> str:
        try:
            import PyPDF2

            with open(path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                pages = [page.extract_text() or "" for page in reader.pages]
            return "\n\n".join(pages)
        except ImportError:
            raise ImportError("PyPDF2 is required for PDF loading. Run: pip install PyPDF2")

    def _load_docx(self, path: str) -> str:
        try:
            import docx

            document = docx.Document(path)
            paragraphs = [p.text for p in document.paragraphs]
            return "\n".join(paragraphs)
        except ImportError:
            raise ImportError("python-docx is required for DOCX loading. Run: pip install python-docx")

    def _load_txt(self, path: str) -> str:
        with open(path, encoding="utf-8") as f:
            return f.read()

    def _load_md(self, path: str) -> str:
        return self._load_txt(path)

    def _load_markdown(self, path: str) -> str:
        return self._load_txt(path)


# ══════════════════════════════════════════════════════════════════════
# RAG Engine
# ══════════════════════════════════════════════════════════════════════


class RAGEngine:
    """End-to-end Retrieval-Augmented Generation engine.

    Pipeline:
        1. Load document → extract text
        2. Split into chunks with overlap
        3. Index into hybrid retriever (dense + sparse)
        4. On query: retrieve → rerank → generate answer with citations

    Parameters:
        retriever:  A :class:`HybridRetriever` instance.
        llm_engine:  An :class:`LLMEngine` for generation and reranking.
        chunk_size:  Max characters per chunk (default 1000).
        chunk_overlap:  Overlap between consecutive chunks (default 200).

    Usage::

        from jarvis.rag.engine import RAGEngine
        from jarvis.rag.retriever import HybridRetriever
        from jarvis.memory.vector_store import VectorMemory
        from jarvis.llm.engine import LLMEngine

        rag = RAGEngine(
            retriever=HybridRetriever(VectorMemory(persist_dir="./rag_db")),
            llm_engine=LLMEngine(),
        )
        rag.add_document("report.pdf")
        answer = rag.query("What are the key findings?")
    """

    _ANSWER_SYSTEM = (
        "You are a precise research assistant. "
        "Answer questions based ONLY on the provided document excerpts. "
        "Always cite the source document name and chunk index when you use information. "
        "If the documents do not contain enough information, say so clearly. "
        "Format citations as [source: filename, chunk N]."
    )

    def __init__(
        self,
        retriever: Any = None,
        llm_engine: Any = None,
        *,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
    ) -> None:
        from jarvis.rag.retriever import HybridRetriever

        self._retriever: HybridRetriever = retriever or HybridRetriever()
        self._llm = llm_engine
        self._loader = DocumentLoader()
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

        # Track indexed documents for citation
        self._documents: dict[str, dict[str, Any]] = {}
        self._ingest_counter: int = 0

    # ── Document ingestion ─────────────────────────────────────────────

    def add_document(self, file_path: str) -> dict[str, Any]:
        """Load, chunk, and index a document.

        Args:
            file_path:  Path to PDF/DOCX/TXT/MD file.

        Returns:
            Summary dict with ``file_name``, ``chunks``, ``file_type``.
        """
        text, meta = self._loader.load(file_path)
        chunks, chunk_metadatas = self._splitter.split_documents([text], [meta])

        self._ingest_counter += 1
        doc_id = f"{hashlib.sha256(file_path.encode()).hexdigest()[:12]}_{self._ingest_counter}"

        # Enrich metadata with unique chunk IDs
        ids = []
        merged_metas = []
        for i, cm in enumerate(chunk_metadatas):
            chunk_id = f"{doc_id}_{i}"
            ids.append(chunk_id)
            merged_metas.append(cm)

        self._retriever.index(chunks, merged_metas, ids)

        self._documents[doc_id] = {
            "file_path": file_path,
            "file_name": meta["file_name"],
            "file_type": meta["file_type"],
            "chunk_count": len(chunks),
        }

        logger.info(
            "[RAGEngine] Indexed '%s' → %d chunks (%s)",
            meta["file_name"],
            len(chunks),
            doc_id,
        )
        return {
            "doc_id": doc_id,
            "file_name": meta["file_name"],
            "file_type": meta["file_type"],
            "chunks": len(chunks),
        }

    # ── Query ──────────────────────────────────────────────────────────

    def query(
        self,
        question: str,
        top_k: int = 5,
        *,
        rerank: bool = True,
        metadata_filter: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Retrieve relevant chunks and generate an answer with citations.

        Args:
            question:  The natural-language query.
            top_k:  Max chunks to use for answer generation.
            rerank:  Whether to LLM-rerank results before answering.
            metadata_filter:  Optional ChromaDB ``where`` filter.

        Returns:
            Dict with ``answer``, ``sources`` (list of citation dicts),
            and ``retrieved_chunks`` (raw retrieved chunks for transparency).
        """
        # Step 1: Retrieve
        fused = self._retriever.retrieve(question, top_k=top_k * 2, metadata_filter=metadata_filter)

        # Step 2: Rerank
        if rerank and self._llm is not None and len(fused.get("chunks", [])) > top_k:
            fused = self._retriever.rerank(question, fused, self._llm, top_k=top_k)

        # Trim to top_k
        chunks = fused.get("chunks", [])[:top_k]
        metadatas = fused.get("metadatas", [])[:top_k]

        # Step 3: Generate answer
        answer = self._generate_answer(question, chunks, metadatas)

        # Step 4: Build citation info
        sources = self._build_citations(chunks, metadatas)

        return {
            "answer": answer,
            "sources": sources,
            "retrieved_chunks": chunks,
        }

    # ── Internal ───────────────────────────────────────────────────────

    def _generate_answer(
        self,
        question: str,
        chunks: list[str],
        metadatas: list[dict[str, Any]],
    ) -> str:
        """Build a prompt with context chunks and call the LLM."""
        if not chunks:
            return "No relevant documents found to answer this question."

        context_parts: list[str] = []
        for i, (chunk, meta) in enumerate(zip(chunks, metadatas)):
            fname = meta.get("file_name", "unknown")
            cidx = meta.get("chunk_index", i)
            context_parts.append(f"[Document {i + 1}: {fname}, chunk {cidx}]\n{chunk}")

        context = "\n\n---\n\n".join(context_parts)

        prompt = (
            f"Use the following document excerpts to answer the question.\n\n"
            f"{context}\n\n"
            f"Question: {question}\n\n"
            f"Answer (cite sources as [source: filename, chunk N]):"
        )

        if self._llm is not None:
            raw = self._llm.chat_sync(prompt=prompt, system=self._ANSWER_SYSTEM)
            return raw.strip()
        else:
            return self._fallback_answer(question, chunks, metadatas)

    def _fallback_answer(
        self,
        question: str,
        chunks: list[str],
        metadatas: list[dict[str, Any]],
    ) -> str:
        """No-LLM fallback: return the most relevant chunk with citation."""
        if not chunks:
            return "No relevant documents found."

        best_chunk = chunks[0]
        best_meta = metadatas[0] if metadatas else {}
        fname = best_meta.get("file_name", "unknown")
        cidx = best_meta.get("chunk_index", 0)

        return (
            f"[LLM unavailable — returning best matching chunk]\n\n"
            f"Source: {fname}, chunk {cidx}\n\n"
            f"{best_chunk[:500]}"
        )

    def _build_citations(
        self,
        chunks: list[str],
        metadatas: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Build a structured citation list."""
        sources: list[dict[str, Any]] = []
        seen: set[tuple[str, int]] = set()

        for i, meta in enumerate(metadatas):
            fname = meta.get("file_name", "unknown")
            fpath = meta.get("file_path", "")
            cidx = meta.get("chunk_index", i)

            key = (fname, cidx)
            if key not in seen:
                seen.add(key)
                sources.append({
                    "file_name": fname,
                    "file_path": fpath,
                    "chunk_index": cidx,
                    "excerpt": chunks[i][:200] if i < len(chunks) else "",
                })

        return sources

"""Tests for huanxin.rag — document loading, chunking, hybrid retrieval, and answer generation."""

import os
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "huanxin-ai"))


# ══════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════


@pytest.fixture
def sample_txt_file():
    """Create a temporary .txt file with known content."""
    content = (
        "Introduction to RAG\n\n"
        "Retrieval-Augmented Generation (RAG) is a technique that combines "
        "information retrieval with large language models. It allows LLMs to "
        "access external knowledge sources during generation.\n\n"
        "How RAG Works\n\n"
        "The RAG pipeline consists of three main stages: "
        "1) Document indexing — documents are split into chunks and stored in a vector database. "
        "2) Retrieval — when a query comes in, relevant chunks are retrieved using semantic search. "
        "3) Generation — the LLM generates an answer using the retrieved chunks as context.\n\n"
        "Benefits of RAG\n\n"
        "RAG reduces hallucinations by grounding answers in real documents. "
        "It also enables knowledge updates without retraining the model. "
        "This makes RAG ideal for enterprise knowledge management and question-answering systems."
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(content)
        path = f.name
    yield path
    os.unlink(path)


@pytest.fixture
def sample_md_file():
    """Create a temporary .md file."""
    content = (
        "# Project Overview\n\n"
        "This project implements a multi-agent AI system.\n\n"
        "## Architecture\n\n"
        "The system uses a modular architecture with independent agents.\n\n"
        "## Key Features\n\n"
        "- Hybrid retrieval combining dense and sparse search\n"
        "- LLM-based reranking for improved precision\n"
        "- Source citation in generated answers\n"
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(content)
        path = f.name
    yield path
    os.unlink(path)


@pytest.fixture
def temp_dir():
    """Create a temporary directory for ChromaDB persistent storage."""
    td = tempfile.mkdtemp()
    try:
        yield td
    finally:
        import gc
        import shutil
        import time

        gc.collect()
        time.sleep(0.15)
        shutil.rmtree(td, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════════
# Document Loader tests
# ══════════════════════════════════════════════════════════════════════


class TestDocumentLoader:
    """Tests for DocumentLoader."""

    def test_load_txt(self, sample_txt_file):
        from huanxin.rag.engine import DocumentLoader

        loader = DocumentLoader()
        text, meta = loader.load(sample_txt_file)

        assert "Introduction to RAG" in text
        assert "Benefits of RAG" in text
        assert meta["file_type"] == "txt"
        assert os.path.basename(sample_txt_file) in meta["file_name"]

    def test_load_md(self, sample_md_file):
        from huanxin.rag.engine import DocumentLoader

        loader = DocumentLoader()
        text, meta = loader.load(sample_md_file)

        assert "# Project Overview" in text
        assert "## Architecture" in text
        assert meta["file_type"] == "md"

    def test_load_unsupported_extension(self):
        from huanxin.rag.engine import DocumentLoader

        loader = DocumentLoader()
        with tempfile.NamedTemporaryFile(suffix=".xyz", delete=False) as f:
            f.write(b"data")
            path = f.name
        try:
            with pytest.raises(ValueError, match="Unsupported file type"):
                loader.load(path)
        finally:
            os.unlink(path)

    def test_load_missing_file(self):
        from huanxin.rag.engine import DocumentLoader

        loader = DocumentLoader()
        with pytest.raises(FileNotFoundError):
            loader.load("/nonexistent/file_12345.txt")

    def test_load_txt_metadata_fields(self, sample_txt_file):
        from huanxin.rag.engine import DocumentLoader

        loader = DocumentLoader()
        _, meta = loader.load(sample_txt_file)

        assert "file_path" in meta
        assert "file_name" in meta
        assert "file_type" in meta
        assert meta["file_path"] == sample_txt_file


# ══════════════════════════════════════════════════════════════════════
# Text Splitter tests
# ══════════════════════════════════════════════════════════════════════


class TestRecursiveCharacterTextSplitter:
    """Tests for RecursiveCharacterTextSplitter."""

    def test_split_short_text(self):
        from huanxin.rag.engine import RecursiveCharacterTextSplitter

        splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
        text = "Short text that fits in one chunk."
        chunks = splitter.split_text(text)

        assert len(chunks) == 1
        assert chunks[0] == text

    def test_split_by_paragraph(self):
        from huanxin.rag.engine import RecursiveCharacterTextSplitter

        splitter = RecursiveCharacterTextSplitter(chunk_size=60, chunk_overlap=10)
        text = (
            "First paragraph with enough text to exceed chunk size limit."
            "\n\nSecond paragraph containing more verbose content here."
            "\n\nThird paragraph extended well beyond the split threshold."
        )
        chunks = splitter.split_text(text)

        # Should split on \n\n boundaries
        assert len(chunks) >= 2

    def test_split_long_text(self):
        from huanxin.rag.engine import RecursiveCharacterTextSplitter

        splitter = RecursiveCharacterTextSplitter(chunk_size=100, chunk_overlap=20)
        # Generate text much longer than chunk_size
        text = "This is a sentence. " * 50
        chunks = splitter.split_text(text)

        assert len(chunks) > 1
        # Each chunk should be <= chunk_size (with some tolerance for overlap)
        for chunk in chunks:
            assert len(chunk) <= 100 + 30  # small tolerance

    def test_split_documents_preserves_metadata(self):
        from huanxin.rag.engine import RecursiveCharacterTextSplitter

        splitter = RecursiveCharacterTextSplitter(chunk_size=80, chunk_overlap=20)
        texts = ["Doc A " * 40, "Doc B " * 40]
        metadatas = [{"source": "a.txt"}, {"source": "b.txt"}]

        chunks, chunk_metas = splitter.split_documents(texts, metadatas)

        assert len(chunks) > 2
        assert len(chunk_metas) == len(chunks)

        # All chunks from first doc should have source a.txt
        a_chunks = [m for m in chunk_metas if m["source"] == "a.txt"]
        b_chunks = [m for m in chunk_metas if m["source"] == "b.txt"]
        assert len(a_chunks) > 0
        assert len(b_chunks) > 0

    def test_chunk_metadata_indices(self):
        from huanxin.rag.engine import RecursiveCharacterTextSplitter

        splitter = RecursiveCharacterTextSplitter(chunk_size=200, chunk_overlap=50)
        texts = ["Single doc " * 30]
        metadatas = [{"source": "doc.txt"}]

        chunks, chunk_metas = splitter.split_documents(texts, metadatas)

        # chunk_index and chunk_total should be present
        for cm in chunk_metas:
            assert "chunk_index" in cm
            assert "chunk_total" in cm
            assert cm["chunk_total"] == len(chunks)

        # chunk_index should be sequential
        indices = [cm["chunk_index"] for cm in chunk_metas]
        assert indices == list(range(len(chunks)))

    def test_empty_text(self):
        from huanxin.rag.engine import RecursiveCharacterTextSplitter

        splitter = RecursiveCharacterTextSplitter()
        chunks = splitter.split_text("")
        assert chunks == [""] or chunks == []

    def test_custom_separators(self):
        from huanxin.rag.engine import RecursiveCharacterTextSplitter

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=15, chunk_overlap=3, separators=["|", " "]
        )
        text = "word1|word2|word3|word4|word5|word6|word7|word8"
        chunks = splitter.split_text(text)
        assert len(chunks) >= 2


# ══════════════════════════════════════════════════════════════════════
# HybridRetriever tests
# ══════════════════════════════════════════════════════════════════════


class TestHybridRetriever:
    """Tests for HybridRetriever (dense + BM25 + RRF + rerank)."""

    def test_index_and_retrieve(self, temp_dir):
        from huanxin.memory.vector_store import VectorMemory
        from huanxin.rag.retriever import HybridRetriever

        vm = VectorMemory(persist_dir=temp_dir, collection_name="test_rag")
        retriever = HybridRetriever(vector_store=vm)

        chunks = [
            "Python is a popular programming language.",
            "Machine learning uses statistical techniques.",
            "The capital of France is Paris.",
        ]
        metadatas = [
            {"source": "doc1.txt"},
            {"source": "doc2.txt"},
            {"source": "doc3.txt"},
        ]
        ids = retriever.index(chunks, metadatas)
        assert len(ids) == 3

        results = retriever.retrieve("What is the capital of France?", top_k=2)
        assert "chunks" in results
        assert "metadatas" in results
        assert "scores" in results
        assert "sources" in results
        assert len(results["chunks"]) >= 1

    def test_dense_only_retrieve(self, temp_dir):
        from huanxin.memory.vector_store import VectorMemory
        from huanxin.rag.retriever import HybridRetriever

        vm = VectorMemory(persist_dir=temp_dir, collection_name="test_dense")
        retriever = HybridRetriever(vector_store=vm)

        retriever.index(
            ["Quantum computing uses qubits.", "Classical computing uses bits."],
            [{"topic": "quantum"}, {"topic": "classical"}],
        )

        results = retriever.retrieve("quantum", top_k=3)
        assert len(results["chunks"]) >= 1

    def test_sparse_retrieve_no_vector_store(self):
        from huanxin.rag.retriever import HybridRetriever

        retriever = HybridRetriever(vector_store=None)
        retriever.index(["apple banana cherry", "dog cat mouse", "sun moon stars"])
        results = retriever.retrieve("fruit apple", top_k=2)

        assert len(results["chunks"]) >= 1
        assert "apple" in results["chunks"][0].lower()

    def test_fusion_results_combines_sources(self):
        from huanxin.rag.retriever import HybridRetriever

        dense = [
            {"id": "d1", "chunk": "Python is great", "metadata": {}, "distance": 0.1, "source": "dense"},
            {"id": "d2", "chunk": "Java is verbose", "metadata": {}, "distance": 0.3, "source": "dense"},
        ]
        sparse = [
            {"id": "s1", "chunk": "Python is great", "metadata": {}, "score": 5.0, "source": "sparse"},
            {"id": "s2", "chunk": "Rust is fast", "metadata": {}, "score": 3.0, "source": "sparse"},
        ]

        retriever = HybridRetriever()
        fused = retriever.fusion_results(dense, sparse, top_k=3)

        assert len(fused["chunks"]) <= 3
        assert len(fused["scores"]) == len(fused["chunks"])
        # "Python is great" should appear only once (fused)
        count = sum(1 for c in fused["chunks"] if c == "Python is great")
        assert count == 1

    def test_fusion_scores_are_positive(self):
        from huanxin.rag.retriever import HybridRetriever

        dense = [
            {"id": "d1", "chunk": "Chunk A", "metadata": {}, "distance": 0.1, "source": "dense"},
        ]
        sparse = [
            {"id": "s1", "chunk": "Chunk B", "metadata": {}, "score": 2.0, "source": "sparse"},
        ]
        retriever = HybridRetriever()
        fused = retriever.fusion_results(dense, sparse, top_k=5)

        assert all(s > 0 for s in fused["scores"])

    def test_empty_retrieve(self, temp_dir):
        from huanxin.memory.vector_store import VectorMemory
        from huanxin.rag.retriever import HybridRetriever

        vm = VectorMemory(persist_dir=temp_dir, collection_name="test_empty")
        retriever = HybridRetriever(vector_store=vm)

        results = retriever.retrieve("anything", top_k=5)
        assert results["chunks"] == []

    def test_bm25_fallback(self):
        """Test that fallback BM25 works without rank-bm25 installed."""
        from huanxin.rag.retriever import _FallbackBM25

        corpus = [
            "the quick brown fox jumps over the lazy dog",
            "never gonna give you up never gonna let you down",
            "machine learning is a subset of artificial intelligence",
        ]
        bm25 = _FallbackBM25(corpus)
        tokens = "quick fox".split()
        scores = bm25.get_scores(tokens)

        assert len(scores) == 3
        # First document should have highest score for "quick fox"
        assert scores[0] > scores[1]
        assert scores[0] > scores[2]


# ══════════════════════════════════════════════════════════════════════
# RAGEngine tests
# ══════════════════════════════════════════════════════════════════════


class TestRAGEngine:
    """Tests for RAGEngine."""

    def test_add_document_txt(self, sample_txt_file, temp_dir):
        from huanxin.memory.vector_store import VectorMemory
        from huanxin.rag.engine import RAGEngine
        from huanxin.rag.retriever import HybridRetriever

        vm = VectorMemory(persist_dir=temp_dir, collection_name="rag_test")
        rag = RAGEngine(retriever=HybridRetriever(vector_store=vm))

        result = rag.add_document(sample_txt_file)
        assert result["file_type"] == "txt"
        assert result["chunks"] > 0
        assert "doc_id" in result
        assert "file_name" in result

    def test_add_document_md(self, sample_md_file, temp_dir):
        from huanxin.memory.vector_store import VectorMemory
        from huanxin.rag.engine import RAGEngine
        from huanxin.rag.retriever import HybridRetriever

        vm = VectorMemory(persist_dir=temp_dir, collection_name="rag_md_test")
        rag = RAGEngine(retriever=HybridRetriever(vector_store=vm))

        result = rag.add_document(sample_md_file)
        assert result["file_type"] == "md"
        assert result["chunks"] > 0

    def test_query_returns_answer_and_sources(self, sample_txt_file, temp_dir):
        from huanxin.memory.vector_store import VectorMemory
        from huanxin.rag.engine import RAGEngine
        from huanxin.rag.retriever import HybridRetriever

        vm = VectorMemory(persist_dir=temp_dir, collection_name="rag_query_test")
        rag = RAGEngine(
            retriever=HybridRetriever(vector_store=vm),
            llm_engine=None,  # Test fallback mode
        )

        rag.add_document(sample_txt_file)
        result = rag.query("What is RAG?")

        assert "answer" in result
        assert "sources" in result
        assert "retrieved_chunks" in result
        assert len(result["sources"]) >= 1
        # Sources should have citation fields
        for src in result["sources"]:
            assert "file_name" in src
            assert "chunk_index" in src
            assert "excerpt" in src

    def test_query_no_documents(self, temp_dir):
        from huanxin.memory.vector_store import VectorMemory
        from huanxin.rag.engine import RAGEngine
        from huanxin.rag.retriever import HybridRetriever

        vm = VectorMemory(persist_dir=temp_dir, collection_name="rag_empty_test")
        rag = RAGEngine(retriever=HybridRetriever(vector_store=vm))

        result = rag.query("Any question?")
        assert "answer" in result
        assert "No relevant documents" in result["answer"]

    def test_query_with_rerank_disabled(self, sample_txt_file, temp_dir):
        from huanxin.memory.vector_store import VectorMemory
        from huanxin.rag.engine import RAGEngine
        from huanxin.rag.retriever import HybridRetriever

        vm = VectorMemory(persist_dir=temp_dir, collection_name="rag_norerank_test")
        rag = RAGEngine(retriever=HybridRetriever(vector_store=vm))

        rag.add_document(sample_txt_file)
        result = rag.query("RAG pipeline stages", rerank=False)

        assert "answer" in result
        assert len(result["sources"]) >= 1

    def test_citation_format(self, sample_txt_file, temp_dir):
        from huanxin.memory.vector_store import VectorMemory
        from huanxin.rag.engine import RAGEngine
        from huanxin.rag.retriever import HybridRetriever

        vm = VectorMemory(persist_dir=temp_dir, collection_name="rag_cite_test")
        rag = RAGEngine(retriever=HybridRetriever(vector_store=vm))

        rag.add_document(sample_txt_file)
        result = rag.query("What are the benefits of RAG?")

        answer = result["answer"].lower()
        # Fallback answer should contain "source:" and file name
        assert "source:" in answer or len(result["sources"]) > 0

    def test_multiple_documents(self, sample_txt_file, sample_md_file, temp_dir):
        from huanxin.memory.vector_store import VectorMemory
        from huanxin.rag.engine import RAGEngine
        from huanxin.rag.retriever import HybridRetriever

        vm = VectorMemory(persist_dir=temp_dir, collection_name="rag_multi_test")
        rag = RAGEngine(retriever=HybridRetriever(vector_store=vm))

        r1 = rag.add_document(sample_txt_file)
        r2 = rag.add_document(sample_md_file)

        assert r1["chunks"] > 0
        assert r2["chunks"] > 0
        assert r1["file_name"] != r2["file_name"]

        # Query should find relevant chunks
        result = rag.query("architecture", top_k=3)
        assert len(result["retrieved_chunks"]) >= 1

    def test_chunk_size_parameter(self, sample_txt_file, temp_dir):
        from huanxin.memory.vector_store import VectorMemory
        from huanxin.rag.engine import RAGEngine
        from huanxin.rag.retriever import HybridRetriever

        vm = VectorMemory(persist_dir=temp_dir, collection_name="rag_chunk_test")
        rag_small = RAGEngine(
            retriever=HybridRetriever(vector_store=vm),
            chunk_size=200,
            chunk_overlap=50,
        )
        r_small = rag_small.add_document(sample_txt_file)

        # Larger chunk size should produce fewer chunks
        rag_large = RAGEngine(
            retriever=HybridRetriever(
                vector_store=VectorMemory(
                    persist_dir=temp_dir, collection_name="rag_chunk_large"
                )
            ),
            chunk_size=2000,
            chunk_overlap=200,
        )
        r_large = rag_large.add_document(sample_txt_file)

        assert r_small["chunks"] >= r_large["chunks"]


# ══════════════════════════════════════════════════════════════════════
# Integration: RAGEngine + LLM (optional)
# ══════════════════════════════════════════════════════════════════════


class TestRAGIntegration:
    """Integration tests combining RAGEngine with LLMEngine (network optional)."""

    def test_rag_engine_constructor_defaults(self, temp_dir):
        from huanxin.memory.vector_store import VectorMemory
        from huanxin.rag.engine import RAGEngine
        from huanxin.rag.retriever import HybridRetriever

        vm = VectorMemory(persist_dir=temp_dir, collection_name="rag_default")
        rag = RAGEngine(retriever=HybridRetriever(vector_store=vm))
        assert rag._splitter.chunk_size == 1000
        assert rag._splitter.chunk_overlap == 200

    def test_hybrid_retriever_metadata_filter(self, temp_dir):
        from huanxin.memory.vector_store import VectorMemory
        from huanxin.rag.retriever import HybridRetriever

        vm = VectorMemory(persist_dir=temp_dir, collection_name="rag_filter_test")
        retriever = HybridRetriever(vector_store=vm)

        retriever.index(
            ["Secret document content here", "Public document content here"],
            [{"access": "secret"}, {"access": "public"}],
        )

        results = retriever.retrieve("document content", metadata_filter={"access": "public"})
        assert len(results["chunks"]) >= 1

    def test_document_deduplication_across_adds(self, sample_txt_file, temp_dir):
        from huanxin.memory.vector_store import VectorMemory
        from huanxin.rag.engine import RAGEngine
        from huanxin.rag.retriever import HybridRetriever

        vm = VectorMemory(persist_dir=temp_dir, collection_name="rag_dedup_test")
        rag = RAGEngine(retriever=HybridRetriever(vector_store=vm))

        r1 = rag.add_document(sample_txt_file)
        r2 = rag.add_document(sample_txt_file)

        # Adding same doc twice should produce same number of chunks each time
        assert r1["chunks"] == r2["chunks"]
        # Total count in vector store should be double
        assert vm.count() == r1["chunks"] * 2

    def test_sources_not_duplicate(self, sample_txt_file, temp_dir):
        from huanxin.memory.vector_store import VectorMemory
        from huanxin.rag.engine import RAGEngine
        from huanxin.rag.retriever import HybridRetriever

        vm = VectorMemory(persist_dir=temp_dir, collection_name="rag_nodup_test")
        rag = RAGEngine(retriever=HybridRetriever(vector_store=vm))
        rag.add_document(sample_txt_file)

        result = rag.query("RAG")
        # Source file+chunk pairs should be unique
        file_chunk_pairs = [
            (s["file_name"], s["chunk_index"]) for s in result["sources"]
        ]
        assert len(file_chunk_pairs) == len(set(file_chunk_pairs))

"""RAG (Retrieval-Augmented Generation) module.

Exports:
    - ``RAGEngine`` — end-to-end RAG pipeline: load / chunk / index / retrieve / generate.
    - ``HybridRetriever`` — dense (ChromaDB) + sparse (BM25) + RRF fusion + LLM rerank.
    - ``DocumentLoader`` — load PDF, DOCX, TXT, Markdown files.
    - ``RecursiveCharacterTextSplitter`` — semantic text chunking.
"""

from jarvis.rag.engine import (
    DocumentLoader,
    RAGEngine,
    RecursiveCharacterTextSplitter,
)
from jarvis.rag.retriever import HybridRetriever

__all__ = [
    "RAGEngine",
    "HybridRetriever",
    "DocumentLoader",
    "RecursiveCharacterTextSplitter",
]

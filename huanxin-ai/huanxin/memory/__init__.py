"""HUANXIN Memory System — persistent, vectorized, self-organizing.

Exports:
    - ``MemoryEngine`` — existing hybrid memory with ChromaDB/TF-IDF/Jaccard cascade.
    - ``VectorMemory`` — stand-alone ChromaDB vector store with pluggable embeddings.
    - ``MemoryManager`` — high-level semantic memory with typed slots and recency decay.
"""

from huanxin.memory.engine import MemoryEngine, MemoryEntry
from huanxin.memory.manager import MemoryManager
from huanxin.memory.vector_store import VectorMemory

__all__ = [
    "MemoryEngine",
    "MemoryEntry",
    "VectorMemory",
    "MemoryManager",
]

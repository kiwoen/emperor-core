"""JARVIS Memory System — persistent, vectorized, self-organizing.

Exports:
    - ``MemoryEngine`` — existing hybrid memory with ChromaDB/TF-IDF/Jaccard cascade.
    - ``VectorMemory`` — stand-alone ChromaDB vector store with pluggable embeddings.
    - ``MemoryManager`` — high-level semantic memory with typed slots and recency decay.
"""

from jarvis.memory.engine import MemoryEngine, MemoryEntry
from jarvis.memory.manager import MemoryManager
from jarvis.memory.vector_store import VectorMemory

__all__ = [
    "MemoryEngine",
    "MemoryEntry",
    "VectorMemory",
    "MemoryManager",
]

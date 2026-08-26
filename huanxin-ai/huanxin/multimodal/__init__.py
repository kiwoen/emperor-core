"""Multimodal module — vision, speech, and document processing.

Exports:
    - ``MultimodalEngine`` — unified entry point with ``see()``,
      ``hear()``, ``speak()``, ``read_document()`` convenience methods.
    - ``VisionProcessor`` — image understanding via LLM Vision API.
    - ``SpeechProcessor`` — STT (Whisper) + TTS (edge-tts).
    - ``DocumentProcessor`` — PDF / DOCX / image-OCR extraction.
"""

from huanxin.multimodal.engine import MultimodalEngine
from huanxin.multimodal.processor import (
    DocumentProcessor,
    SpeechProcessor,
    VisionProcessor,
)

__all__ = [
    "MultimodalEngine",
    "VisionProcessor",
    "SpeechProcessor",
    "DocumentProcessor",
]

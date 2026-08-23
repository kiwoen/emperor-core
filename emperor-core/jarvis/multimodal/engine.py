"""Multimodal engine — unified entry point for vision / speech / document.

Convenience methods mirror how a user would naturally talk to a multimodal
assistant: ``see()``, ``hear()``, ``read_document()``, ``speak()``.

Delegates actual processing to :class:`VisionProcessor`,
:class:`SpeechProcessor`, and :class:`DocumentProcessor`.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from jarvis.multimodal.processor import (
    DocumentProcessor,
    SpeechProcessor,
    VisionProcessor,
)

logger = logging.getLogger("jarvis.multimodal.engine")


class MultimodalEngine:
    """Unified multimodal processing hub.

    Wraps the three modal processors behind simple, self-documenting
    convenience methods.

    Usage::

        mme = MultimodalEngine(llm_engine=llm, openai_client=openai)

        # Vision
        mme.see("photo.jpg")
        mme.see("https://example.com/diagram.png")

        # Speech
        mme.hear("meeting.mp3")          # STT → transcript
        mme.speak("Hello world")         # TTS → mp3 file

        # Documents
        mme.read_document("report.pdf")
        mme.read_document("proposal.docx")
    """

    def __init__(
        self,
        *,
        llm_engine: Optional[Any] = None,
        openai_client: Optional[Any] = None,
    ) -> None:
        self._vision = VisionProcessor(llm_engine=llm_engine)
        self._speech = SpeechProcessor(openai_client=openai_client)
        self._document = DocumentProcessor(vision_processor=self._vision)

    # ── Convenience methods ────────────────────────────────────────────

    def see(self, image: str, *, prompt: str = "Describe this image in detail.") -> dict[str, Any]:
        """Analyse an image (local path or URL).

        Args:
            image: File path or HTTP URL of the image.
            prompt: Custom prompt for the visual analysis.

        Returns:
            Vision result dict (caption, raw, …).
        """
        logger.info("[MultimodalEngine] see(%s)", image)
        return self._vision.process(image, prompt=prompt)

    def hear(self, audio: str) -> dict[str, Any]:
        """Transcribe speech from an audio file (STT).

        Args:
            audio: Path to an audio file (mp3, wav, m4a, …).

        Returns:
            STT result dict (transcript, audio_path).
        """
        logger.info("[MultimodalEngine] hear(%s)", audio)
        return self._speech.process(audio)

    def speak(
        self,
        text: str,
        output_path: Optional[str] = None,
        *,
        voice: str = "zh-CN-XiaoxiaoNeural",
    ) -> dict[str, Any]:
        """Synthesise speech from text (TTS).

        Args:
            text: Text to convert to speech.
            output_path: Optional destination mp3 path.
            voice: Edge TTS voice name.

        Returns:
            TTS result dict (audio_path, text).
        """
        logger.info("[MultimodalEngine] speak(%d chars)", len(text))
        return self._speech.synthesize(text, output_path=output_path, voice=voice)

    def read_document(self, file_path: str) -> dict[str, Any]:
        """Parse and extract text from a document or image.

        Supports PDF, DOCX, and image formats (OCR via Vision).

        Args:
            file_path: Path to the document/image file.

        Returns:
            Document result dict (content, page_count / paragraph_count, …).
        """
        logger.info("[MultimodalEngine] read_document(%s)", file_path)
        return self._document.process(file_path)

    # ── Direct processor access ────────────────────────────────────────

    @property
    def vision(self) -> VisionProcessor:
        """Access the underlying :class:`VisionProcessor`."""
        return self._vision

    @property
    def speech(self) -> SpeechProcessor:
        """Access the underlying :class:`SpeechProcessor`."""
        return self._speech

    @property
    def document(self) -> DocumentProcessor:
        """Access the underlying :class:`DocumentProcessor`."""
        return self._document

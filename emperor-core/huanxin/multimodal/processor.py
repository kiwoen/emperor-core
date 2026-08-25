"""Multimodal processors — unified interface for vision, speech, and document.

Each processor exposes a :meth:`process` method returning a ``dict`` result.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import tempfile
from typing import Any, Optional

logger = logging.getLogger("huanxin.multimodal.processor")

# ── Helpers ────────────────────────────────────────────────────────────


def _image_to_base64(file_path: str) -> str:
    """Read an image file and return a base64 data URI string."""
    from PIL import Image

    img = Image.open(file_path)
    fmt = img.format or "PNG"
    ext = fmt.lower()
    # Normalise JPEG → jpeg
    if ext == "jpeg":
        ext = "jpg"

    with open(file_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    return f"data:image/{ext};base64,{b64}"


# ══════════════════════════════════════════════════════════════════════
# VisionProcessor
# ══════════════════════════════════════════════════════════════════════


class VisionProcessor:
    """Image understanding via LLM Vision API (GPT-4o / Claude etc.).

    Accepts local image paths (auto-encoded to base64 data URIs) or
    direct image URLs for remote images.  Delegates the actual Vision
    call to an injected ``LLMEngine``.

    Usage::

        vp = VisionProcessor(llm_engine=engine)
        result = vp.process("photo.jpg")
        # {"caption": "...", "objects": [...], "text": "..."}
    """

    DEFAULT_SYSTEM = (
        "You are a precise image analysis assistant. "
        "Describe the image content in detail."
    )

    def __init__(self, llm_engine: Optional[Any] = None) -> None:
        self._llm = llm_engine

    def process(
        self,
        image_input: str,
        *,
        prompt: str = "Describe this image in detail.",
        system: str = "",
    ) -> dict[str, Any]:
        """Analyse an image and return structured observations.

        Args:
            image_input: Local file path or HTTP(S) image URL.
            prompt: The user prompt sent alongside the image.
            system: Optional system message (falls back to DEFAULT_SYSTEM).

        Returns:
            dict with keys ``caption`` (str) and ``raw`` (full response).
            When the image is local, ``image_path`` is also included.
        """
        sys_msg = system or self.DEFAULT_SYSTEM

        if image_input.startswith(("http://", "https://")):
            image_block: dict = {
                "type": "image_url",
                "image_url": {"url": image_input},
            }
            extra_info: dict = {"image_url": image_input}
        else:
            data_uri = _image_to_base64(image_input)
            image_block = {
                "type": "image_url",
                "image_url": {"url": data_uri},
            }
            extra_info = {"image_path": image_input}

        messages = self._build_vision_messages(sys_msg, prompt, image_block)

        if self._llm:
            raw = self._llm.chat_sync(prompt="", messages=messages)
        else:
            raw = self._fallback_vision(image_input, prompt, sys_msg)

        caption = self._extract_caption(raw)
        result: dict[str, Any] = {"caption": caption, "raw": raw}
        result.update(extra_info)
        return result

    async def aprocess(
        self,
        image_input: str,
        *,
        prompt: str = "Describe this image in detail.",
        system: str = "",
    ) -> dict[str, Any]:
        """``process`` 的异步版本：当注入后端支持 ``achat_sync``（如 ``VisionBackend``）时，
        把阻塞的视觉 HTTP 请求卸载到线程池，避免阻塞事件循环；否则退化为同步 ``chat_sync``。

        返回结构与 ``process`` 完全一致。
        """
        sys_msg = system or self.DEFAULT_SYSTEM

        if image_input.startswith(("http://", "https://")):
            image_block: dict = {
                "type": "image_url",
                "image_url": {"url": image_input},
            }
            extra_info: dict = {"image_url": image_input}
        else:
            data_uri = _image_to_base64(image_input)
            image_block = {
                "type": "image_url",
                "image_url": {"url": data_uri},
            }
            extra_info = {"image_path": image_input}

        messages = self._build_vision_messages(sys_msg, prompt, image_block)

        if self._llm is not None:
            achat = getattr(self._llm, "achat_sync", None)
            if achat is not None:
                raw = await achat(prompt="", messages=messages)
            else:
                raw = self._llm.chat_sync(prompt="", messages=messages)
        else:
            raw = self._fallback_vision(image_input, prompt, sys_msg)

        caption = self._extract_caption(raw)
        result = {"caption": caption, "raw": raw}
        result.update(extra_info)
        return result

    def _build_vision_messages(
        self, system: str, prompt: str, image_block: dict
    ) -> list[dict]:
        return [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    image_block,
                ],
            },
        ]

    @staticmethod
    def _extract_caption(raw: str) -> str:
        """Try to parse JSON from raw; fallback to plain text."""
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed.get("caption", parsed.get("description", raw))
        except (json.JSONDecodeError, TypeError):
            pass
        return raw.strip()

    def _fallback_vision(self, image_input: str, prompt: str, system: str) -> str:
        """Minimal fallback when no LLMEngine is available."""
        logger.warning("[VisionProcessor] No LLMEngine — using fallback description")
        return json.dumps({
            "caption": f"[Vision] Image analysis requested for: {image_input}",
            "prompt": prompt,
            "status": "no_llm_available",
        })


# ══════════════════════════════════════════════════════════════════════
# SpeechProcessor
# ══════════════════════════════════════════════════════════════════════


class SpeechProcessor:
    """Speech-to-text (STT) and text-to-speech (TTS) processor.

    - STT: Uses OpenAI Whisper API (or falls back to a stub).
    - TTS: Uses ``edge-tts`` (free, no API key required).

    Usage::

        sp = SpeechProcessor(openai_client=client)
        result = sp.process("recording.mp3")          # STT
        speech  = sp.synthesize("Hello world", "out.mp3")  # TTS
    """

    def __init__(self, openai_client: Optional[Any] = None) -> None:
        self._client = openai_client

    # ── STT ───────────────────────────────────────────────────────────

    def process(self, audio_path: str) -> dict[str, Any]:
        """Transcribe an audio file to text via Whisper API.

        Args:
            audio_path: Path to an audio file (mp3, wav, m4a, etc.).

        Returns:
            dict with ``transcript`` (str) and ``audio_path``.
        """
        if not os.path.isfile(audio_path):
            return {"error": f"Audio file not found: {audio_path}", "audio_path": audio_path}

        if self._client:
            with open(audio_path, "rb") as f:
                transcription = self._client.audio.transcriptions.create(
                    model="whisper-1",
                    file=f,
                )
            transcript = transcription.text
        else:
            logger.warning("[SpeechProcessor] No OpenAI client — STT unavailable")
            transcript = "[STT] No OpenAI client configured"

        return {"transcript": transcript, "audio_path": audio_path}

    # ── TTS ───────────────────────────────────────────────────────────

    def synthesize(
        self,
        text: str,
        output_path: Optional[str] = None,
        *,
        voice: str = "zh-CN-XiaoxiaoNeural",
        rate: str = "+0%",
    ) -> dict[str, Any]:
        """Convert text to speech using edge-tts (offline-friendly).

        Args:
            text: The text to convert.
            output_path: Destination path. Auto-generated if omitted.
            voice: Microsoft Edge TTS voice name.
            rate: Speaking rate modifier.

        Returns:
            dict with ``audio_path`` and ``text``.
        """
        if output_path is None:
            fd, output_path = tempfile.mkstemp(suffix=".mp3", prefix="huanxin_tts_")
            os.close(fd)

        try:
            import asyncio

            import edge_tts

            async def _run():
                communicate = edge_tts.Communicate(text, voice, rate=rate)
                await communicate.save(output_path)

            asyncio.run(_run())
        except ImportError:
            logger.error("[SpeechProcessor] edge-tts not installed")
            return {"error": "edge-tts not installed", "text": text}
        except Exception as e:
            logger.error("[SpeechProcessor] TTS failed: %s", e)
            return {"error": str(e), "text": text}

        return {"audio_path": output_path, "text": text}


# ══════════════════════════════════════════════════════════════════════
# DocumentProcessor
# ══════════════════════════════════════════════════════════════════════


class DocumentProcessor:
    """Parse and extract text from documents (PDF, DOCX) and images (OCR).

    - PDF  → PyPDF2
    - DOCX → python-docx
    - Images → OCR via VisionProcessor (delegated)

    Usage::

        dp = DocumentProcessor(vision_processor=vp)
        result = dp.process("report.pdf")
    """

    IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff", ".svg"}

    def __init__(self, vision_processor: Optional[VisionProcessor] = None) -> None:
        self._vision = vision_processor

    def process(self, file_path: str) -> dict[str, Any]:
        """Extract text content from a document or image.

        Args:
            file_path: Path to PDF, DOCX, or image file.

        Returns:
            dict with ``content`` (str), ``page_count`` (PDF only),
            ``paragraph_count`` (DOCX only), and ``file_path``.
        """
        if not os.path.isfile(file_path):
            return {"error": f"File not found: {file_path}", "file_path": file_path}

        ext = os.path.splitext(file_path)[1].lower()

        if ext == ".pdf":
            return self._process_pdf(file_path)
        elif ext == ".docx":
            return self._process_docx(file_path)
        elif ext in self.IMAGE_EXTENSIONS:
            return self._process_image(file_path)
        else:
            return {
                "error": f"Unsupported file type: {ext}",
                "file_path": file_path,
                "supported_types": [".pdf", ".docx"] + sorted(self.IMAGE_EXTENSIONS),
            }

    # ── PDF ────────────────────────────────────────────────────────────

    def _process_pdf(self, file_path: str) -> dict[str, Any]:
        try:
            import PyPDF2

            with open(file_path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                pages = [page.extract_text() or "" for page in reader.pages]
            content = "\n\n".join(pages)
            return {
                "content": content.strip(),
                "page_count": len(reader.pages),
                "file_path": file_path,
            }
        except ImportError:
            return {"error": "PyPDF2 not installed", "file_path": file_path}
        except Exception as e:
            logger.error("[DocumentProcessor] PDF parse failed: %s", e)
            return {"error": str(e), "file_path": file_path}

    # ── DOCX ───────────────────────────────────────────────────────────

    def _process_docx(self, file_path: str) -> dict[str, Any]:
        try:
            import docx

            document = docx.Document(file_path)
            paragraphs = [p.text for p in document.paragraphs if p.text.strip()]
            content = "\n".join(paragraphs)
            return {
                "content": content,
                "paragraph_count": len(paragraphs),
                "file_path": file_path,
            }
        except ImportError:
            return {"error": "python-docx not installed", "file_path": file_path}
        except Exception as e:
            logger.error("[DocumentProcessor] DOCX parse failed: %s", e)
            return {"error": str(e), "file_path": file_path}

    # ── Image / OCR ────────────────────────────────────────────────────

    def _process_image(self, file_path: str) -> dict[str, Any]:
        if self._vision:
            result = self._vision.process(
                file_path,
                prompt="Extract and read all visible text from this image. "
                       "Return the text content exactly as it appears.",
            )
            return {
                "content": result.get("caption", ""),
                "file_path": file_path,
                "ocr": True,
            }
        else:
            logger.warning("[DocumentProcessor] No VisionProcessor for OCR")
            return {
                "content": f"[OCR] Vision processor not available for: {file_path}",
                "file_path": file_path,
                "ocr": False,
            }

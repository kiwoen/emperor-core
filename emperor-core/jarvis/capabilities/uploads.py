"""文件上传存储与安全校验。

安全边界（对应 PRD 3.3-3 / ARCH 1.3）：
* 扩展名白名单 ``{.jpg,.jpeg,.png,.webp,.txt,.md,.pdf}``；
* MIME + 扩展名双验（客户端 MIME 与扩展名必须一致，图片再经 PIL 嗅探真实格式）；
* 单文件大小上限 ``UPLOAD_MAX_MB``（默认 20MB）；
* ``uuid4`` 重命名存储，原始文件名仅入元数据；
* ``Path.resolve`` 防路径穿越：文件 id 与落地路径均严格校验，绝不拼接用户输入。
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("jarvis.capabilities.uploads")

# 允许的扩展名白名单
ALLOWED_EXTENSIONS: set[str] = {".jpg", ".jpeg", ".png", ".webp", ".txt", ".md", ".pdf"}

# 扩展名 → 允许的 MIME 集合（用于 MIME+扩展双验）
ALLOWED_MIME: dict[str, set[str]] = {
    ".jpg": {"image/jpeg"},
    ".jpeg": {"image/jpeg"},
    ".png": {"image/png"},
    ".webp": {"image/webp"},
    ".txt": {"text/plain"},
    ".md": {"text/markdown", "text/plain"},
    ".pdf": {"application/pdf"},
}

# 客户端未提供 MIME 时的兜底 Content-Type（用于回显）
_FALLBACK_MIME: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".pdf": "application/pdf",
}

# 图片扩展名 → PIL 报告的真实格式名（用于内容嗅探）
_IMAGE_FORMATS: dict[str, set[str]] = {
    ".jpg": {"jpeg"},
    ".jpeg": {"jpeg"},
    ".png": {"png"},
    ".webp": {"webp"},
}

_FILE_ID_RE = re.compile(r"[0-9a-fA-F]{32}")


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


class UploadStore:
    """把上传文件落盘到 ``$EMPEROR_DATA_DIR/uploads`` 并维护 JSON 元数据 sidecar。"""

    def __init__(self, base_dir: Optional[str] = None) -> None:
        data_dir = os.getenv("EMPEROR_DATA_DIR", "/app/data")
        root = Path(base_dir) if base_dir else (Path(data_dir) / "uploads")
        self._base = root.resolve()
        self._base.mkdir(parents=True, exist_ok=True)
        self._max_mb = _safe_int(os.getenv("UPLOAD_MAX_MB", "20"), 20)

    # ── 公开 API ────────────────────────────────────────────────────

    def save(self, user_id: int, filename: str, content: bytes, content_type: str = "") -> dict:
        """保存一次上传，返回 ``{id,name,size,ext,url,content_type}``。

        校验失败抛出 ``ValueError``（由路由层转成 400 可读提示）。
        """
        if not filename:
            raise ValueError("缺少文件名")
        ext = os.path.splitext(filename)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise ValueError(
                f"不支持的文件类型：{ext or '(无扩展名)'}（仅允许 jpg/jpeg/png/webp/txt/md/pdf）"
            )
        if not isinstance(content, (bytes, bytearray)) or not content:
            raise ValueError("文件内容为空")
        if len(content) > self._max_mb * 1024 * 1024:
            raise ValueError(f"文件超过大小上限 {self._max_mb}MB")

        # MIME + 扩展名双验：客户端 MIME（若提供）必须落在该扩展名的白名单内
        mime = (content_type or "").split(";")[0].strip().lower()
        if mime and mime not in ALLOWED_MIME[ext]:
            raise ValueError(f"文件类型（{mime}）与扩展名（{ext}）不匹配")

        # 图片再经 PIL 嗅探真实格式，杜绝改扩展名伪装
        if ext in _IMAGE_FORMATS:
            self._verify_image(bytes(content), ext)

        file_id = uuid.uuid4().hex
        stored_name = f"{file_id}{ext}"
        final_path = (self._base / stored_name).resolve()
        # 防路径穿越：落地路径必须仍位于 base_dir 之内
        if not final_path.is_relative_to(self._base):
            raise ValueError("非法文件路径")

        final_path.write_bytes(bytes(content))
        meta: dict[str, Any] = {
            "id": file_id,
            "name": os.path.basename(filename)[:255],
            "stored_name": stored_name,
            "ext": ext,
            "size": len(content),
            "content_type": mime or _FALLBACK_MIME[ext],
            "user_id": int(user_id),
            "created_at": time.time(),
        }
        self._write_meta(file_id, meta)
        logger.info("upload saved file_id=%s ext=%s bytes=%d", file_id, ext, len(content))
        return {
            "id": file_id,
            "name": meta["name"],
            "size": meta["size"],
            "ext": ext,
            "url": f"/api/files/{file_id}",
            "content_type": meta["content_type"],
        }

    def resolve(self, file_id: str) -> Optional[Path]:
        """返回文件落地路径；不存在 / 非法 id / 穿越一律返回 None。"""
        meta = self.get_meta(file_id)
        if not meta or not meta.get("stored_name"):
            return None
        path = (self._base / meta["stored_name"]).resolve()
        if not path.is_relative_to(self._base) or not path.exists():
            return None
        return path

    def get_meta(self, file_id: str) -> Optional[dict]:
        """读取文件元数据（含 user_id，用于属主校验）。"""
        if not self._validate_id(file_id):
            return None
        meta_path = self._base / f"{file_id}.json"
        try:
            if not meta_path.exists():
                return None
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def delete(self, file_id: str) -> bool:
        """删除文件本体与元数据 sidecar。"""
        if not self._validate_id(file_id):
            return False
        removed = False
        path = self.resolve(file_id)
        if path is not None:
            try:
                path.unlink()
                removed = True
            except OSError:
                pass
        try:
            (self._base / f"{file_id}.json").unlink(missing_ok=True)
            removed = True
        except OSError:
            pass
        return removed

    # ── 内部辅助 ────────────────────────────────────────────────────

    @staticmethod
    def _validate_id(file_id: str) -> bool:
        return bool(file_id and _FILE_ID_RE.fullmatch(file_id))

    def _write_meta(self, file_id: str, meta: dict) -> None:
        (self._base / f"{file_id}.json").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8"
        )

    @staticmethod
    def _verify_image(content: bytes, ext: str) -> None:
        """PIL 嗅探图片真实格式，与扩展名不一致则拒绝（pillow 缺失时跳过）。"""
        try:
            import io

            from PIL import Image
        except ImportError:  # pillow 未安装：退化为扩展名+MIME 兜底
            logger.debug("pillow 未安装，跳过图片内容嗅探")
            return
        try:
            with Image.open(io.BytesIO(content)) as img:
                fmt = (img.format or "").lower()
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"图片内容校验失败：{e}")
        if fmt not in _IMAGE_FORMATS[ext]:
            raise ValueError(f"图片实际格式（{fmt or '未知'}）与扩展名（{ext}）不一致")

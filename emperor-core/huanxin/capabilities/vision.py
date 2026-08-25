"""图文识别（Vision）后端解析与适配器。

选型：Groq LLaVA 主后端（复用 ``FREE_PROVIDERS["groq"]`` 的 base_url + ``GROQ_API_KEY``），
通过 ``VISION_FALLBACK_BASE_URLS/MODELS/KEYS`` 三组逗号分隔 env 通用挂载备选后端。

关键约束（ARCH 1.1）：
* ``VisionProcessor``（``huanxin/multimodal/processor.py``）**零改动**——它只依赖注入对象
  实现 ``chat_sync(prompt="", messages=[...]) -> str``；
* 因此这里提供 ``VisionBackend`` 适配器，内部按 backends 顺序做故障转移，
  全部失败时返回结构化降级文案（``status=no_vision_available``），绝不抛 5xx。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

logger = logging.getLogger("huanxin.capabilities.vision")

# 视觉主后端默认（覆盖 groq 的文本默认模型）
_DEFAULT_VISION_MODEL = "llava-v1.5-7b-4096-preview"

# 共享线程池：所有 VisionBackend 实例复用，避免多实例 / 测试泄漏线程。
# 阻塞的 HTTP 请求（urllib）经此池卸载，``achat_sync`` 在 async 上下文不阻塞事件循环。
_VISION_EXECUTOR: "Optional[ThreadPoolExecutor]" = None
_VISION_EXECUTOR_LOCK = threading.Lock()


def _get_vision_executor() -> "ThreadPoolExecutor":
    global _VISION_EXECUTOR
    if _VISION_EXECUTOR is None:
        with _VISION_EXECUTOR_LOCK:
            if _VISION_EXECUTOR is None:
                _VISION_EXECUTOR = ThreadPoolExecutor(
                    max_workers=2, thread_name_prefix="vision"
                )
    return _VISION_EXECUTOR


def _parse_csv(value: Optional[str]) -> list[str]:
    return [x.strip() for x in (value or "").split(",") if x.strip()]


def resolve_vision_backends() -> list[dict]:
    """解析视觉后端列表（主后端在前，fallback 依次在后）。

    每个后端形如 ``{"name", "base_url", "model", "api_key"}``；主后端要求
    同时具备 base_url 与 key，否则视为不可用并跳过。
    """
    from huanxin.core.llm import FREE_PROVIDERS

    provider = (os.getenv("VISION_PROVIDER", "groq") or "groq").strip().lower()
    model = (os.getenv("VISION_MODEL", "") or "").strip() or _DEFAULT_VISION_MODEL

    backends: list[dict] = []
    prov = FREE_PROVIDERS.get(provider)
    if not prov:
        logger.warning("未知 VISION_PROVIDER=%r，忽略主后端", provider)
    else:
        key_env = prov.get("key_env", "")
        key = os.getenv(key_env, "") if key_env else ""
        base_url = (prov.get("base_url", "") or "").rstrip("/")
        if base_url and key:
            backends.append({"name": provider, "base_url": base_url, "model": model, "api_key": key})
        else:
            logger.warning("vision 主后端 %r 缺少 base_url 或 key（%s），跳过", provider, key_env or "无 key_env")

    fb_urls = _parse_csv(os.getenv("VISION_FALLBACK_BASE_URLS", ""))
    fb_models = _parse_csv(os.getenv("VISION_FALLBACK_MODELS", ""))
    fb_keys = _parse_csv(os.getenv("VISION_FALLBACK_KEYS", ""))
    for i, url in enumerate(fb_urls):
        m = fb_models[i] if i < len(fb_models) else model
        k = fb_keys[i] if i < len(fb_keys) else ""
        backends.append({"name": f"fallback-{i}", "base_url": url.rstrip("/"), "model": m, "api_key": k})
    return backends


class VisionBackend:
    """实现 ``chat_sync(prompt="", messages=None, system="") -> str`` 的轻量适配器。

    与 ``VisionProcessor._llm.chat_sync`` 调用点精确匹配，内部按后端顺序故障转移；
    任何单点失败都不会向上抛出，最终以结构化降级文案兜底。
    """

    def __init__(self, backends: list[dict], timeout: int = 60) -> None:
        self._backends = backends
        self._timeout = timeout
        self.last_usage: dict = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self.last_error: Optional[str] = None
        # last_usage / last_error 跨并发请求写入，加锁保护
        self._state_lock = threading.Lock()

    def chat_sync(self, prompt: str = "", messages: Optional[list[dict]] = None, system: str = "") -> str:
        """调用视觉模型；失败时返回 JSON 降级文案（绝不抛异常）。"""
        if not self._backends:
            self.last_error = "未配置任何 vision 后端（缺少 GROQ_API_KEY 等）"
            return self._degraded("no_vision_available", self.last_error)

        payload_messages = messages or self._build_messages(prompt, system)
        last_err: Optional[str] = None
        for backend in self._backends:
            try:
                data = self._call_backend(backend, payload_messages)
                with self._state_lock:
                    self.last_usage = self._extract_usage(data)
                return self._extract_text(data)
            except Exception as e:  # noqa: BLE001 - 逐后端故障转移
                last_err = f"{backend.get('name')}: {e}"
                logger.warning("vision 后端 %s 调用失败：%s", backend.get("name"), e)

        with self._state_lock:
            self.last_error = last_err or "全部 vision 后端不可用"
        return self._degraded("no_vision_available", self.last_error)

    async def achat_sync(self, prompt: str = "", messages: Optional[list[dict]] = None, system: str = "") -> str:
        """异步版 ``chat_sync``：把阻塞的 HTTP 请求卸载到线程池，不阻塞事件循环。

        契约与 ``chat_sync`` 完全一致（返回 str，失败时返回结构化降级文案）。
        """
        if not self._backends:
            with self._state_lock:
                self.last_error = "未配置任何 vision 后端（缺少 GROQ_API_KEY 等）"
            return self._degraded("no_vision_available", self.last_error)

        payload_messages = messages or self._build_messages(prompt, system)
        last_err: Optional[str] = None
        loop = asyncio.get_running_loop()
        for backend in self._backends:
            try:
                data = await loop.run_in_executor(
                    _get_vision_executor(), self._call_backend, backend, payload_messages
                )
                with self._state_lock:
                    self.last_usage = self._extract_usage(data)
                return self._extract_text(data)
            except Exception as e:  # noqa: BLE001 - 逐后端故障转移
                last_err = f"{backend.get('name')}: {e}"
                logger.warning("vision 后端 %s 调用失败：%s", backend.get("name"), e)

        with self._state_lock:
            self.last_error = last_err or "全部 vision 后端不可用"
        return self._degraded("no_vision_available", self.last_error)

    # ── 内部 ────────────────────────────────────────────────────────

    def _call_backend(self, backend: dict, messages: list[dict]) -> dict:
        """OpenAI 兼容 ``POST {base}/chat/completions``（用标准库 urllib，零额外依赖）。"""
        url = backend["base_url"].rstrip("/") + "/chat/completions"
        payload = json.dumps(
            {
                "model": backend.get("model", _DEFAULT_VISION_MODEL),
                "messages": messages,
                "max_tokens": 512,
                "temperature": 0.2,
            }
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if backend.get("api_key"):
            headers["Authorization"] = "Bearer " + backend["api_key"]
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    @staticmethod
    def _build_messages(prompt: str, system: str) -> list[dict]:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt or "Describe this image in detail."})
        return messages

    @staticmethod
    def _extract_text(data: dict) -> str:
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError, AttributeError):
            return json.dumps(data, ensure_ascii=False)

    @staticmethod
    def _extract_usage(data: dict) -> dict:
        try:
            u = data.get("usage") or {}
            return {
                "prompt_tokens": int(u.get("prompt_tokens", 0) or 0),
                "completion_tokens": int(u.get("completion_tokens", 0) or 0),
                "total_tokens": int(u.get("total_tokens", 0) or 0),
            }
        except (TypeError, ValueError):
            return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    @staticmethod
    def _degraded(status: str, error: str = "") -> str:
        return json.dumps(
            {
                "caption": "[视觉识别不可用：未配置可用的 vision 模型密钥]",
                "status": status,
                "error": error,
            },
            ensure_ascii=False,
        )


def build_vision_processor() -> Optional[Any]:
    """构建注入 ``VisionBackend`` 的 ``VisionProcessor``；无可用 key 时返回 None。"""
    backends = resolve_vision_backends()
    if not backends:
        logger.warning("未配置可用的 vision 后端（GROQ_API_KEY 缺失等），视觉识别不可用")
        return None
    from huanxin.multimodal.processor import VisionProcessor

    return VisionProcessor(llm_engine=VisionBackend(backends))

"""联网搜索服务（多 backend 容错，DuckDuckGo 默认）。

封装 ``duckduckgo_search.DDGS``。约定：
- import 失败 / 网络异常 / 后端不可用 → ``(results=[], degraded=True, reason="...")``
- 绝不抛出异常——由路由层决定如何向用户呈现降级提示
- 多 backend 容错：按顺序试 ``auto`` → ``html`` → ``lite``，直到拿到非空结果
  （生产环境经常某 backend 被限流/要验证码，切换后即可恢复）
- 结果统一为 ``[{title, url, snippet}]``
- ``degraded_reason`` 字符串用于 LLM / SSE 下发，告知「为什么没拿到结果」
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

logger = logging.getLogger("jarvis.capabilities.search")


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


# DDGS 支持的 backend：按可靠度排序，便于级联容错（DDGS 后端经常被限流/要验证码）
_BACKENDS_TRY_ORDER = ("auto", "html", "lite", "ecosia")


class WebSearchService:
    """联网搜索后端（DuckDuckGo 多 backend 容错）。"""

    def __init__(
        self,
        provider: str = "",
        max_results: int = 0,
        timeout: int = 0,
        backends: str = "",
    ) -> None:
        self._provider = (provider or os.getenv("SEARCH_PROVIDER", "duckduckgo")).strip().lower()
        self._max_results = max_results or _safe_int(os.getenv("SEARCH_MAX_RESULTS", "5"), 5)
        self._timeout = timeout or _safe_int(os.getenv("SEARCH_TIMEOUT", "8"), 8)
        # 多 backend 容错：env SEARCH_BACKENDS 逗号分隔；默认全部四个按顺序试
        env_backends = (backends or os.getenv("SEARCH_BACKENDS", "auto,html,lite")).strip()
        requested = [b.strip().lower() for b in env_backends.split(",") if b.strip()]
        # 过滤到已知 backend，未知项忽略
        self._backends = [b for b in requested if b in _BACKENDS_TRY_ORDER] or list(_BACKENDS_TRY_ORDER)
        # 30 秒内同一 backend 失败则跳过（避免连续 502 拖死主流程）
        self._cooldown: dict[str, float] = {}
        self._cooldown_seconds = _safe_int(os.getenv("SEARCH_BACKEND_COOLDOWN", "30"), 30)

    def available(self) -> bool:
        """探测搜索后端是否可用（仅做 import 检查，不做网络请求）。"""
        if self._provider not in ("", "duckduckgo"):
            logger.warning("未支持的搜索后端 %r（仅支持 duckduckgo）", self._provider)
            return False
        try:
            import duckduckgo_search  # noqa: F401
        except Exception as e:  # noqa: BLE001
            logger.warning("duckduckgo-search 不可用：%s", e)
            return False
        return True

    def _backend_in_cooldown(self, backend: str) -> bool:
        until = self._cooldown.get(backend, 0.0)
        if until and until > time.time():
            return True
        return False

    def _cooldown_backend(self, backend: str) -> None:
        self._cooldown[backend] = time.time() + self._cooldown_seconds

    def search(self, query: str, max_results: Optional[int] = None) -> tuple[list[dict], bool, str]:
        """执行一次搜索，返回 ``(results, degraded, reason)``。

        - ``degraded=False``：成功获取结果
        - ``degraded=True, reason="..."``：失败；``reason`` 是给 LLM 与前端的下发原因
        """
        query = (query or "").strip()
        if not query:
            return [], False, ""
        limit = max(1, min(int(max_results or self._max_results), 10))
        if not self.available():
            return [], True, "搜索库不可用（duckduckgo-search 未安装）"

        last_error = ""
        try:
            from duckduckgo_search import DDGS
        except Exception as e:  # noqa: BLE001
            return [], True, f"搜索库加载失败：{e}"

        # 多 backend 级联容错
        for backend in self._backends:
            if self._backend_in_cooldown(backend):
                last_error = f"{backend} 在冷却中"
                continue
            try:
                with DDGS() as ddgs:
                    raw = list(
                        ddgs.text(
                            query,
                            max_results=limit,
                            backend=backend,
                            timeout=self._timeout,
                        )
                    )
                results = [
                    {
                        "title": (r.get("title") or "").strip(),
                        "url": (r.get("href") or r.get("url") or "").strip(),
                        "snippet": (r.get("body") or "").strip(),
                    }
                    for r in raw
                    if (r.get("href") or r.get("url"))
                ]
                if results:
                    return results, False, ""
                # 拿到空结果（包含「全无 url」过滤后清空）：按降级继续试下一 backend
                last_error = f"{backend}: 空结果或全部无 url"
            except Exception as e:  # noqa: BLE001
                last_error = f"{backend}: {e}"
                logger.warning("搜索 backend=%s 失败：%s", backend, e)
                self._cooldown_backend(backend)
                continue

        # 全部 backend 都失败
        return [], True, f"全部 backend 失败：{last_error or '未知'}"

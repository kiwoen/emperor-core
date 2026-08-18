"""联网搜索服务（DuckDuckGo，免费无 key）。

封装 ``duckduckgo_search.DDGS``。约定：import 失败 / 网络异常 / 后端不可用，
一律返回 ``(results=[], degraded=True)``，**绝不抛出异常**——由路由层决定如何
向用户呈现降级提示。结果统一为 ``[{title, url, snippet}]``。
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger("jarvis.capabilities.search")


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


class WebSearchService:
    """DuckDuckGo 联网搜索后端（可经 env 切换 / 调参）。"""

    def __init__(self, provider: str = "", max_results: int = 0, timeout: int = 0) -> None:
        self._provider = (provider or os.getenv("SEARCH_PROVIDER", "duckduckgo")).strip().lower()
        self._max_results = max_results or _safe_int(os.getenv("SEARCH_MAX_RESULTS", "5"), 5)
        self._timeout = timeout or _safe_int(os.getenv("SEARCH_TIMEOUT", "10"), 10)

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

    def search(self, query: str, max_results: Optional[int] = None) -> tuple[list[dict], bool]:
        """执行一次搜索，返回 ``(results, degraded)``。

        ``degraded=True`` 表示搜索不可用（无库 / 断网 / 后端异常），结果为空。
        """
        query = (query or "").strip()
        if not query:
            return [], False
        limit = max(1, min(int(max_results or self._max_results), 10))
        if not self.available():
            return [], True
        try:
            from duckduckgo_search import DDGS

            with DDGS() as ddgs:
                raw = list(ddgs.text(query, max_results=limit))
            results = [
                {
                    "title": r.get("title", ""),
                    "url": r.get("href") or r.get("url", ""),
                    "snippet": r.get("body", ""),
                }
                for r in raw
            ]
            return results, False
        except Exception as e:  # noqa: BLE001 - 断网 / 限流 / 库变更均降级
            logger.warning("联网搜索失败（降级返回空结果）：%s", e, exc_info=True)
            return [], True

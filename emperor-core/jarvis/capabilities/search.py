"""联网搜索服务（多引擎级联：必应 / 搜狗 / DuckDuckGo）。

设计要点
--------
- 约定：import 失败 / 网络异常 / 解析不到结果 → ``(results=[], degraded=True, reason="...")``，
  绝不抛出异常——由路由层决定如何向用户呈现降级提示。
- 多引擎级联：默认 ``auto`` 按 ``bing → sogou → duckduckgo`` 顺序试（国内服务器必应/搜狗
  可达性好于 DuckDuckGo；必应结果相关性最好、无反爬，作主引擎；DuckDuckGo 兜底）。
  任一引擎拿到非空结果即返回。
- DuckDuckGo 引擎内部再做 backend 级联（``auto→html→lite→ecosia``）。
- 结果统一为 ``[{title, url, snippet}]``；url 为空的结果会被过滤。
- ``reason`` 字符串用于 LLM 硬约束与前端 SSE 下发，告知「为什么没拿到结果」。

env 变量
--------
- ``SEARCH_PROVIDER``   : 单引擎 ``bing|sogou|duckduckgo``，默认 ``auto``（级联）
- ``SEARCH_ENGINES``    : auto 模式下的引擎顺序，逗号分隔，默认 ``bing,sogou,duckduckgo``
- ``SEARCH_TIMEOUT``    : 单次请求超时秒数，默认 8
- ``SEARCH_MAX_RESULTS``: 默认结果条数，默认 5
- ``SEARCH_BACKENDS``   : DuckDuckGo 引擎的 backend 顺序，默认 ``auto,html,lite``
- ``SEARCH_BACKEND_COOLDOWN``: 失败引擎冷却秒数，默认 30
- ``SEARCH_USER_AGENT`` : 爬虫 UA（默认内置 Chrome UA）

实现备注（实测结论）
--------------------
- 百度 ``www.baidu.com/s`` 对无 cookie 的请求返回「百度安全验证」反爬页（HTML 无结果 DOM），
  故不再内置百度引擎。
- 必应 ``cn.bing.com/search``：结果在 ``li.b_algo > h2 a``（真实 url 在 href）+ ``div.b_caption p``
  或 ``p.b_lineclamp``（摘要），无反爬、相关性好。
- 搜狗 ``www.sogou.com/web``：结果在 ``div.vrwrap > h3.vr-title > a``（真实 url 或 /link?url=）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

logger = logging.getLogger("jarvis.capabilities.search")

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# DDGS backend 容错顺序（DDGS 后端经常被限流/要验证码）
_DDG_BACKENDS = ("auto", "html", "lite", "ecosia")

# 支持的搜索引擎（百度因反爬验证页已移除）
_SUPPORTED_PROVIDERS = ("bing", "sogou", "duckduckgo")

# 共享线程池：所有 WebSearchService 实例复用同一池，避免测试 / 多实例泄漏线程。
# 阻塞的网络 I/O（requests / DDGS）经此池卸载，``asearch`` 在 async 上下文不阻塞事件循环。
_SEARCH_EXECUTOR: "Optional[ThreadPoolExecutor]" = None
_SEARCH_EXECUTOR_LOCK = threading.Lock()


def _get_search_executor() -> "ThreadPoolExecutor":
    global _SEARCH_EXECUTOR
    if _SEARCH_EXECUTOR is None:
        with _SEARCH_EXECUTOR_LOCK:
            if _SEARCH_EXECUTOR is None:
                _SEARCH_EXECUTOR = ThreadPoolExecutor(
                    max_workers=_safe_int(os.getenv("SEARCH_MAX_WORKERS", "4"), 4),
                    thread_name_prefix="websearch",
                )
    return _SEARCH_EXECUTOR


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


class WebSearchService:
    """联网搜索（必应/搜狗爬虫 + DuckDuckGo 兜底，多引擎级联容错）。"""

    def __init__(
        self,
        provider: str = "",
        max_results: int = 0,
        timeout: int = 0,
        backends: str = "",
    ) -> None:
        self._provider = (provider or os.getenv("SEARCH_PROVIDER", "auto")).strip().lower()
        self._max_results = max_results or _safe_int(os.getenv("SEARCH_MAX_RESULTS", "5"), 5)
        self._timeout = timeout or _safe_int(os.getenv("SEARCH_TIMEOUT", "8"), 8)
        self._user_agent = os.getenv("SEARCH_USER_AGENT", _DEFAULT_UA)

        # DuckDuckGo backend 级联顺序
        env_backends = (backends or os.getenv("SEARCH_BACKENDS", "auto,html,lite")).strip()
        requested = [b.strip().lower() for b in env_backends.split(",") if b.strip()]
        self._ddg_backends = [b for b in requested if b in _DDG_BACKENDS] or list(_DDG_BACKENDS)

        # 失败引擎冷却（避免连续失败拖死主流程）
        self._cooldown: dict[str, float] = {}
        self._cooldown_seconds = _safe_int(os.getenv("SEARCH_BACKEND_COOLDOWN", "30"), 30)
        # 冷却字典跨线程访问（def 端点线程池 + asearch 线程池）需加锁保护
        self._cooldown_lock = threading.Lock()

    # ── 引擎顺序 ──────────────────────────────────────────────
    def _engine_order(self) -> list[str]:
        if self._provider in ("", "auto"):
            env_order = os.getenv("SEARCH_ENGINES", "bing,sogou,duckduckgo")
            order = [p.strip().lower() for p in env_order.split(",") if p.strip()]
            order = [p for p in order if p in _SUPPORTED_PROVIDERS]
            return order or list(_SUPPORTED_PROVIDERS)
        if self._provider in _SUPPORTED_PROVIDERS:
            return [self._provider]
        return []

    def available(self) -> bool:
        """探测是否至少一个引擎的依赖可用（仅 import 检查，不发网络请求）。"""
        for engine in self._engine_order():
            if self._engine_available(engine):
                return True
        return False

    @staticmethod
    def _engine_available(engine: str) -> bool:
        try:
            if engine in ("bing", "sogou"):
                import bs4  # noqa: F401
                import requests  # noqa: F401
            else:  # duckduckgo
                import duckduckgo_search  # noqa: F401
        except Exception as e:  # noqa: BLE001
            logger.warning("搜索引擎 %s 依赖不可用：%s", engine, e)
            return False
        return True

    # ── 冷却管理 ──────────────────────────────────────────────
    def _in_cooldown(self, key: str) -> bool:
        with self._cooldown_lock:
            until = self._cooldown.get(key, 0.0)
        return bool(until and until > time.time())

    def _mark_cooldown(self, key: str) -> None:
        with self._cooldown_lock:
            self._cooldown[key] = time.time() + self._cooldown_seconds

    # ── 主入口 ────────────────────────────────────────────────
    def search(self, query: str, max_results: Optional[int] = None) -> tuple[list[dict], bool, str]:
        """执行一次搜索，返回 ``(results, degraded, reason)``。

        - ``degraded=False``：成功获取结果
        - ``degraded=True, reason="..."``：失败；``reason`` 是给 LLM / 前端下发的原因
        """
        query = (query or "").strip()
        if not query:
            return [], False, ""
        limit = max(1, min(int(max_results or self._max_results), 10))
        engines = self._engine_order()
        if not engines:
            return [], True, f"未支持的搜索后端 {self._provider!r}（支持 {', '.join(_SUPPORTED_PROVIDERS)}）"

        errors: list[str] = []
        for engine in engines:
            key = f"engine:{engine}"
            if self._in_cooldown(key):
                errors.append(f"{engine}: 冷却中")
                continue
            try:
                if engine == "bing":
                    results = self._search_bing(query, limit)
                elif engine == "sogou":
                    results = self._search_sogou(query, limit)
                else:
                    results = self._search_ddg(query, limit)
                if results:
                    return results, False, ""
                errors.append(f"{engine}: 空结果")
            except Exception as e:  # noqa: BLE001
                errors.append(f"{engine}: {e}")
                logger.warning("搜索引擎 %s 失败：%s", engine, e)
                self._mark_cooldown(key)

        return [], True, "全部引擎失败：" + ";".join(errors)

    async def asearch(
        self, query: str, max_results: Optional[int] = None
    ) -> tuple[list[dict], bool, str]:
        """异步版 ``search``：把阻塞的网络 I/O 卸载到线程池，不阻塞事件循环。

        在 ``async def`` 上下文中（如 FastAPI 的 SSE 流式端点）应优先使用本方法。
        返回值与 ``search`` 完全一致：``(results, degraded, reason)``。
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            _get_search_executor(),
            lambda: self.search(query, max_results=max_results),
        )

    # ── 必应（爬虫，主引擎）───────────────────────────────────
    def _search_bing(self, query: str, limit: int) -> list[dict]:
        import requests
        from bs4 import BeautifulSoup

        r = requests.get(
            "https://cn.bing.com/search",
            params={"q": query, "count": limit},
            headers={"User-Agent": self._user_agent, "Accept-Language": "zh-CN,zh;q=0.9"},
            timeout=self._timeout,
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        out: list[dict] = []
        for li in soup.select("li.b_algo"):
            a = li.select_one("h2 a")
            if a is None:
                continue
            title = a.get_text(" ", strip=True)
            url = (a.get("href") or "").strip()
            cap = li.select_one("div.b_caption p") or li.select_one("p.b_lineclamp") or li.select_one("p")
            snippet = cap.get_text(" ", strip=True) if cap else ""
            if url and title:
                out.append({"title": title, "url": url, "snippet": snippet})
            if len(out) >= limit:
                break
        return out

    # ── 搜狗（爬虫，兜底）─────────────────────────────────────
    def _search_sogou(self, query: str, limit: int) -> list[dict]:
        import requests
        from bs4 import BeautifulSoup

        r = requests.get(
            "https://www.sogou.com/web",
            params={"query": query},
            headers={"User-Agent": self._user_agent, "Accept-Language": "zh-CN,zh;q=0.9"},
            timeout=self._timeout,
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        out: list[dict] = []
        for div in soup.select("div.vrwrap, div.rb"):
            a = div.select_one("h3 a")
            if a is None:
                continue
            title = a.get_text(" ", strip=True)
            if not title:
                continue
            # 搜狗真实 url 优先 data-url，其次 href（可能是 /link?url= 跳转链接）
            url = (a.get("data-url") or a.get("href") or "").strip()
            snippet = ""
            for sel in ("div.ft", "p.str_info", "div.space-txt", "div.text-layout", "div.fz-mid"):
                node = div.select_one(sel)
                if node and node.get_text(strip=True):
                    snippet = node.get_text(" ", strip=True)
                    break
            if url:
                out.append({"title": title, "url": url, "snippet": snippet})
            if len(out) >= limit:
                break
        return out

    # ── DuckDuckGo（duckduckgo-search，最后兜底）───────────────
    def _search_ddg(self, query: str, limit: int) -> list[dict]:
        from duckduckgo_search import DDGS

        for backend in self._ddg_backends:
            key = f"ddg:{backend}"
            if self._in_cooldown(key):
                continue
            try:
                with DDGS() as ddgs:
                    raw = list(
                        ddgs.text(query, max_results=limit, backend=backend, timeout=self._timeout)
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning("DDGS backend=%s 失败：%s", backend, e)
                self._mark_cooldown(key)
                continue
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
                return results
        return []

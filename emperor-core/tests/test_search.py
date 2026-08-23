"""联网搜索服务测试（PRD P0-4 / ARCH 1.2）。

覆盖：
* mock DDGS：正常返回结构化 [{title,url,snippet}]
* import 失败 / 网络异常 → 返回 ([], degraded=True, reason="...") 不抛
* /api/search：未登录 401；正常 200；降级 200
"""

from __future__ import annotations

import sys
import types

import pytest
from fastapi.testclient import TestClient

import jarvis.court_api as court_api
from jarvis.api import auth_store
from jarvis.capabilities.search import WebSearchService


def _make_fake_duckduckgo(results=None, raise_on_text=False):
    results = list(results or [])
    mod = types.ModuleType("duckduckgo_search")

    class _DDGS:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def text(self, query, max_results=5, backend="auto", timeout=10):
            if raise_on_text:
                raise RuntimeError("network unreachable")
            return list(results)

    mod.DDGS = _DDGS
    return mod


class TestWebSearchService:
    def test_search_returns_structured_results(self, monkeypatch):
        monkeypatch.setitem(
            sys.modules,
            "duckduckgo_search",
            _make_fake_duckduckgo([{"title": "标题", "href": "https://example.com", "body": "摘要"}]),
        )
        svc = WebSearchService(provider="duckduckgo", max_results=5, timeout=1)
        results, degraded, reason = svc.search("测试")
        assert degraded is False
        assert reason == ""
        assert results == [{"title": "标题", "url": "https://example.com", "snippet": "摘要"}]

    def test_search_normalizes_url_field(self, monkeypatch):
        monkeypatch.setitem(
            sys.modules,
            "duckduckgo_search",
            _make_fake_duckduckgo([{"title": "t", "url": "https://x.com", "body": "s"}]),
        )
        svc = WebSearchService(provider="duckduckgo", max_results=5, timeout=1)
        results, degraded, _ = svc.search("q")
        assert degraded is False
        assert results[0]["url"] == "https://x.com"

    def test_search_degraded_on_import_error(self, monkeypatch):
        monkeypatch.delitem(sys.modules, "duckduckgo_search", raising=False)
        svc = WebSearchService(provider="duckduckgo", max_results=5, timeout=1)
        results, degraded, reason = svc.search("q")
        assert results == []
        assert degraded is True
        assert "不可用" in reason or "失败" in reason or "duckduckgo" in reason.lower()

    def test_search_degraded_on_network_error(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "duckduckgo_search", _make_fake_duckduckgo(raise_on_text=True))
        svc = WebSearchService(provider="duckduckgo", max_results=5, timeout=1, backends="auto")
        results, degraded, reason = svc.search("q")
        assert results == []
        assert degraded is True
        assert reason

    def test_search_degraded_url_filter_drops_empty(self, monkeypatch):
        """结果中 url 为空的项会被过滤掉，全空结果等同降级。"""
        monkeypatch.setitem(
            sys.modules,
            "duckduckgo_search",
            _make_fake_duckduckgo([{"title": "t", "body": "s"}]),  # 无 url/href
        )
        svc = WebSearchService(provider="duckduckgo", max_results=5, timeout=1, backends="auto")
        results, degraded, _ = svc.search("q")
        assert results == []
        assert degraded is True

    def test_search_falls_through_backends(self, monkeypatch):
        """第一个 backend 失败应自动切到下个。"""
        mod = types.ModuleType("duckduckgo_search")

        class _DDGS:
            def __init__(self, *a, **k):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def text(self, query, max_results=5, backend="auto", timeout=10):
                if backend == "auto":
                    raise RuntimeError("auto down")
                return [{"title": "t", "href": "https://x.com", "body": "s"}]
        mod.DDGS = _DDGS
        monkeypatch.setitem(sys.modules, "duckduckgo_search", mod)
        svc = WebSearchService(provider="duckduckgo", max_results=5, timeout=1, backends="auto,html")
        results, degraded, _ = svc.search("q")
        assert degraded is False
        assert results[0]["url"] == "https://x.com"

    def test_available_false_when_lib_missing(self, monkeypatch):
        monkeypatch.delitem(sys.modules, "duckduckgo_search", raising=False)
        svc = WebSearchService(provider="duckduckgo", max_results=5, timeout=1)
        assert svc.available() is False

    def test_available_false_for_unsupported_provider(self):
        svc = WebSearchService(provider="serpapi", max_results=5, timeout=1)
        assert svc.available() is False

    def test_empty_query_returns_empty_not_degraded(self):
        svc = WebSearchService(provider="duckduckgo", max_results=5, timeout=1)
        results, degraded, _ = svc.search("   ")
        assert results == []
        assert degraded is False


# ══════════════════════════════════════════════════════════════════
# API 测试：/api/search
# ══════════════════════════════════════════════════════════════════


@pytest.fixture
def client(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("EMPEROR_DATA_DIR", str(data_dir))
    monkeypatch.setenv("EMPEROR_OPEN_REGISTRATION", "1")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(auth_store, "_conn", None)
    app = court_api.create_app()
    return TestClient(app)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _register(client, username="searcher", password="secret123"):
    r = client.post("/api/auth/register", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


class TestSearchAPI:
    def test_search_requires_auth(self, client):
        r = client.post("/api/search", json={"query": "hello"})
        assert r.status_code == 401

    def test_search_ok(self, client, monkeypatch):
        token = _register(client)
        monkeypatch.setattr(
            WebSearchService,
            "search",
            lambda self, query, max_results=None: (
                [{"title": "t", "url": "https://x.com", "snippet": "s"}],
                False,
                "",
            ),
        )
        r = client.post("/api/search", json={"query": "hello"}, headers=_auth(token))
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["degraded"] is False
        assert data["reason"] == ""
        assert data["results"][0]["url"] == "https://x.com"

    def test_search_degraded(self, client, monkeypatch):
        token = _register(client)
        monkeypatch.setattr(
            WebSearchService,
            "search",
            lambda self, query, max_results=None: ([], True, "auto: network unreachable"),
        )
        r = client.post("/api/search", json={"query": "hello"}, headers=_auth(token))
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["degraded"] is True
        assert data["results"] == []
        assert "network" in data["reason"].lower() or "auto" in data["reason"]


# ══════════════════════════════════════════════════════════════════
# 必应 / 搜狗 爬虫引擎测试
# ══════════════════════════════════════════════════════════════════

import requests as _requests  # noqa: E402


class _FakeResp:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self):
        pass


BING_HTML = """<html><body>
<li class="b_algo">
  <h2><a href="https://baike.baidu.com/item/量子计算">量子计算</a></h2>
  <div class="b_caption"><p>量子计算摘要文本</p></div>
</li>
<li class="b_algo">
  <h2><a href="https://example.com/second">第二个结果</a></h2>
  <p class="b_lineclamp">第二段摘要</p>
</li>
</body></html>"""

SOGOU_HTML = """<html><body>
<div class="vrwrap">
  <h3><a href="/link?url=xyz" data-url="https://www.sogou.com/result1">搜狗结果一</a></h3>
  <div class="ft">搜狗摘要一</div>
</div>
</body></html>"""


class TestBingSogou:
    def test_bing_parses_results(self, monkeypatch):
        monkeypatch.setattr(_requests, "get", lambda *a, **k: _FakeResp(BING_HTML))
        svc = WebSearchService(provider="bing", max_results=5, timeout=1)
        results, degraded, reason = svc.search("量子计算")
        assert degraded is False
        assert reason == ""
        assert len(results) == 2
        assert results[0]["url"] == "https://baike.baidu.com/item/量子计算"
        assert results[0]["title"] == "量子计算"
        assert "量子计算摘要" in results[0]["snippet"]
        assert results[1]["url"] == "https://example.com/second"

    def test_sogou_parses_results(self, monkeypatch):
        monkeypatch.setattr(_requests, "get", lambda *a, **k: _FakeResp(SOGOU_HTML))
        svc = WebSearchService(provider="sogou", max_results=5, timeout=1)
        results, degraded, _ = svc.search("测试")
        assert degraded is False
        assert len(results) == 1
        # 有 data-url 属性取真实 url
        assert results[0]["url"] == "https://www.sogou.com/result1"
        assert results[0]["title"] == "搜狗结果一"

    def test_bing_network_error_degrades(self, monkeypatch):
        def _boom(*a, **k):
            raise OSError("connection refused")

        monkeypatch.setattr(_requests, "get", _boom)
        svc = WebSearchService(provider="bing", max_results=5, timeout=1)
        results, degraded, reason = svc.search("q")
        assert results == []
        assert degraded is True
        assert "bing" in reason.lower()

    def test_auto_falls_back_to_sogou_when_bing_fails(self, monkeypatch):
        """auto 级联：必应失败应自动切到搜狗。"""
        called = {"engine": []}

        def _fake_get(url, *a, **k):
            if "bing" in url:
                called["engine"].append("bing")
                raise OSError("bing blocked")
            if "sogou" in url:
                called["engine"].append("sogou")
                return _FakeResp(SOGOU_HTML)
            return _FakeResp("")

        monkeypatch.setattr(_requests, "get", _fake_get)
        svc = WebSearchService(provider="auto", max_results=5, timeout=1)
        monkeypatch.setenv("SEARCH_ENGINES", "bing,sogou")
        results, degraded, _ = svc.search("q")
        assert degraded is False
        assert len(results) == 1
        assert results[0]["url"] == "https://www.sogou.com/result1"
        assert "bing" in called["engine"] and "sogou" in called["engine"]

    def test_available_true_for_bing(self):
        svc = WebSearchService(provider="bing")
        assert svc.available() is True

    def test_available_false_for_unknown_provider(self):
        svc = WebSearchService(provider="unknown-engine")
        assert svc.available() is False

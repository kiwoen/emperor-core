"""联网搜索服务测试（PRD P0-4 / ARCH 1.2）。

覆盖：
* mock DDGS：正常返回结构化 [{title,url,snippet}]
* import 失败 / 网络异常 → 返回 ([], degraded=True) 不抛
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

        def text(self, query, max_results=5):
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
        results, degraded = svc.search("测试")
        assert degraded is False
        assert results == [{"title": "标题", "url": "https://example.com", "snippet": "摘要"}]

    def test_search_normalizes_url_field(self, monkeypatch):
        monkeypatch.setitem(
            sys.modules,
            "duckduckgo_search",
            _make_fake_duckduckgo([{"title": "t", "url": "https://x.com", "body": "s"}]),
        )
        svc = WebSearchService(provider="duckduckgo", max_results=5, timeout=1)
        results, degraded = svc.search("q")
        assert degraded is False
        assert results[0]["url"] == "https://x.com"

    def test_search_degraded_on_import_error(self, monkeypatch):
        monkeypatch.delitem(sys.modules, "duckduckgo_search", raising=False)
        svc = WebSearchService(provider="duckduckgo", max_results=5, timeout=1)
        results, degraded = svc.search("q")
        assert results == []
        assert degraded is True

    def test_search_degraded_on_network_error(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "duckduckgo_search", _make_fake_duckduckgo(raise_on_text=True))
        svc = WebSearchService(provider="duckduckgo", max_results=5, timeout=1)
        results, degraded = svc.search("q")
        assert results == []
        assert degraded is True

    def test_available_false_when_lib_missing(self, monkeypatch):
        monkeypatch.delitem(sys.modules, "duckduckgo_search", raising=False)
        svc = WebSearchService(provider="duckduckgo", max_results=5, timeout=1)
        assert svc.available() is False

    def test_available_false_for_unsupported_provider(self):
        svc = WebSearchService(provider="serpapi", max_results=5, timeout=1)
        assert svc.available() is False

    def test_empty_query_returns_empty_not_degraded(self):
        svc = WebSearchService(provider="duckduckgo", max_results=5, timeout=1)
        results, degraded = svc.search("   ")
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
            ),
        )
        r = client.post("/api/search", json={"query": "hello"}, headers=_auth(token))
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["degraded"] is False
        assert data["results"][0]["url"] == "https://x.com"

    def test_search_degraded(self, client, monkeypatch):
        token = _register(client)
        monkeypatch.setattr(
            WebSearchService,
            "search",
            lambda self, query, max_results=None: ([], True),
        )
        r = client.post("/api/search", json={"query": "hello"}, headers=_auth(token))
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["degraded"] is True
        assert data["results"] == []

"""能力接线集成测试：/api/chat 的 web_search / file_id 注入 + /api/vision 降级。

覆盖（PRD P0-4 / P1-1 的边界与错误路径）：
* /api/vision 无 GROQ_API_KEY → 200 可读降级（非 500）
* /api/vision 未登录 → 401
* /api/chat web_search:true → SSE 含 sources 事件
* /api/chat web_search 失败 → LLM prompt 含硬约束 + SSE 含 search_degraded 事件
* /api/chat file_id(txt) → 文件文本注入 LLM prompt
* /api/chat file_id 属主隔离 → 他人文件文本不被注入
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import jarvis.court_api as court_api
from jarvis.api import auth_store
from jarvis.capabilities.search import WebSearchService


class _RecordingLLM:
    """记录最后一次 prompt 的 LLM 桩（替代真实多后端 LLM）。"""

    last_usage = {"prompt_tokens": 3, "completion_tokens": 5}

    def __init__(self):
        self.prompts = []

    async def complete(self, prompt, system=None, history=None):
        self.prompts.append(prompt)
        return "已收到"


@pytest.fixture
def client_and_llm(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("EMPEROR_DATA_DIR", str(data_dir))
    monkeypatch.setenv("EMPEROR_OPEN_REGISTRATION", "1")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(auth_store, "_conn", None)

    llm = _RecordingLLM()
    monkeypatch.setattr(court_api, "_get_llm_manager", lambda: llm)
    app = court_api.create_app()
    return TestClient(app), llm


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _register(client, username="user", password="secret123"):
    r = client.post("/api/auth/register", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


class TestVisionEndpoint:
    def test_vision_no_key_degraded(self, client_and_llm):
        client, _ = client_and_llm
        token = _register(client)
        r = client.post(
            "/api/vision", json={"image_url": "https://example.com/x.png"}, headers=_auth(token)
        )
        assert r.status_code == 200
        data = r.json()
        assert data["degraded"] is True
        assert "不可用" in data["caption"]

    def test_vision_requires_auth(self, client_and_llm):
        client, _ = client_and_llm
        r = client.post("/api/vision", json={"image_url": "https://example.com/x.png"})
        assert r.status_code == 401


class TestChatSearchIntegration:
    def test_chat_web_search_emits_sources_event(self, client_and_llm, monkeypatch):
        client, _ = client_and_llm
        token = _register(client, "searcher")
        monkeypatch.setattr(
            WebSearchService,
            "search",
            lambda self, query, max_results=None: (
                [{"title": "来源标题", "url": "https://example.com/a", "snippet": "摘要"}],
                False,
                "",
            ),
        )
        r = client.post(
            "/api/chat", json={"message": "今天天气", "web_search": True}, headers=_auth(token)
        )
        assert r.status_code == 200
        body = r.text
        assert "sources" in body
        assert "https://example.com/a" in body
        assert "来源标题" in body

    def test_chat_web_search_degraded_injects_constraint_and_event(self, client_and_llm, monkeypatch):
        """搜索降级时：LLM 必须收到「不许编来源」硬约束；前端 SSE 必须收到 search_degraded 事件。"""
        client, llm = client_and_llm
        token = _register(client, "searcher2")
        monkeypatch.setattr(
            WebSearchService,
            "search",
            lambda self, query, max_results=None: (
                [],
                True,
                "auto: network unreachable; html: network unreachable",
            ),
        )
        r = client.post(
            "/api/chat", json={"message": "严海清是谁", "web_search": True}, headers=_auth(token)
        )
        assert r.status_code == 200
        body = r.text
        # SSE 含 search_degraded 事件 + 失败原因
        assert "search_degraded" in body
        assert "auto: network" in body
        # LLM 收到的 prompt 必须包含「不得编造」的硬约束
        assert llm.prompts, "LLM 未被调用"
        prompt = llm.prompts[-1]
        assert "联网搜索不可用" in prompt or "联网搜索结果" in prompt
        assert "不得编造" in prompt
        assert "URL" in prompt or "链接" in prompt
        assert "严海清" in prompt  # 用户原问题也要拼进 prompt


class TestChatFileInjection:
    def test_chat_file_id_injects_text(self, client_and_llm):
        client, llm = client_and_llm
        token = _register(client, "fileuser")
        up = client.post(
            "/api/upload",
            files={"file": ("doc.txt", "机密文件内容：量子计算".encode("utf-8"), "text/plain")},
            headers=_auth(token),
        )
        assert up.status_code == 200, up.text
        fid = up.json()["file"]["id"]

        r = client.post(
            "/api/chat", json={"message": "总结这个文件", "file_id": fid}, headers=_auth(token)
        )
        assert r.status_code == 200
        assert llm.prompts, "LLM 未被调用"
        assert "机密文件内容：量子计算" in llm.prompts[-1]

    def test_chat_file_id_owner_isolation(self, client_and_llm):
        client, llm = client_and_llm
        token_a = _register(client, "fileowner")
        token_b = _register(client, "filestealer")
        up = client.post(
            "/api/upload",
            files={"file": ("secret.txt", "超级机密".encode("utf-8"), "text/plain")},
            headers=_auth(token_a),
        )
        fid = up.json()["file"]["id"]

        r = client.post(
            "/api/chat", json={"message": "读文件", "file_id": fid}, headers=_auth(token_b)
        )
        assert r.status_code == 200
        assert llm.prompts, "LLM 未被调用"
        # B 不是文件属主 → 文件内容不应注入其 prompt
        assert "超级机密" not in llm.prompts[-1]

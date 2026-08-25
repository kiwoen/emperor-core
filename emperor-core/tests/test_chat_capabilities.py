"""能力接线集成测试：/api/chat 的 web_search / file_id 注入 + /api/vision 降级。

覆盖（PRD P0-4 / P1-1 的边界与错误路径）：
* /api/vision 无 GROQ_API_KEY → 200 可读降级（非 500）
* /api/vision 未登录 → 401
* /api/chat web_search:true → SSE 含 sources 事件
* /api/chat web_search 失败 → LLM prompt 含硬约束 + SSE 含 search_degraded 事件
* /api/chat web_search 长 prompt → LLM 改写 query 后再搜（避免口语化污染）
* /api/chat web_search 短 prompt → 不改写，直接用原 prompt 搜
* /api/chat web_search 改写失败 → fallback 原 prompt 搜
* /api/chat file_id(txt) → 文件文本注入 LLM prompt
* /api/chat file_id 属主隔离 → 他人文件文本不被注入
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import huanxin.court_api as court_api
from huanxin.api import auth_store
from huanxin.capabilities.search import WebSearchService


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
    monkeypatch.setenv("HUANXIN_DATA_DIR", str(data_dir))
    monkeypatch.setenv("HUANXIN_OPEN_REGISTRATION", "1")
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


class TestChatSearchRewrite:
    """联网搜索的 query 改写（避免口语化污染搜索引擎分词）。"""

    def _mock_search_returning(self, results):
        """构造一个 mock search，捕获被调用的 query。"""
        state = {"called_with": None}
        def fake(self, query, max_results=None):
            state["called_with"] = query
            return results, False, ""
        return fake, state

    def test_long_prompt_triggers_rewrite_and_shortens_query(self, client_and_llm, monkeypatch):
        """长口语 prompt 触发改写：搜索 query ≠ 原 prompt，注入 prompt 含「判断相关性」硬约束。"""
        client, llm = client_and_llm
        token = _register(client, "searcher_rewrite")
        fake, state = self._mock_search_returning(
            [{"title": "t", "url": "https://x.com", "snippet": "s"}]
        )
        monkeypatch.setattr(WebSearchService, "search", fake)
        # 改写 LLM 调用返回干净 query
        original_complete = llm.complete
        async def rewrite_then_answer(prompt, system=None, history=None):
            self.prompts = getattr(self, "prompts", llm.prompts)
            if "口语化" in prompt:
                return "AI领域新闻"  # 干净 query
            return await original_complete(prompt, system=system, history=history)
        llm.complete = rewrite_then_answer

        r = client.post(
            "/api/chat",
            json={"message": "我要求的AI领域最近有哪些新事件", "web_search": True},
            headers=_auth(token),
        )
        assert r.status_code == 200
        # 搜索 query 已被改写
        assert state["called_with"] == "AI领域新闻"
        assert state["called_with"] != "我要求的AI领域最近有哪些新事件"
        # 注入 prompt 含「用户原问题」+「判断相关性」硬约束
        last = llm.prompts[-1]
        assert "用户原问题" in last
        assert "我要求的AI领域" in last
        assert ("判断" in last and "相关" in last)
        assert "AI领域新闻" in last  # 改写后 query 也写入

    def test_short_prompt_no_rewrite(self, client_and_llm, monkeypatch):
        """短 prompt（≤8 字符）跳过改写，直接搜原 prompt。"""
        client, llm = client_and_llm
        token = _register(client, "searcher_short")
        fake, state = self._mock_search_returning(
            [{"title": "t", "url": "https://x.com", "snippet": "s"}]
        )
        monkeypatch.setattr(WebSearchService, "search", fake)
        # 如果意外触发改写，会在 prompt 里出现「口语化」，断言它没出现
        r = client.post(
            "/api/chat",
            json={"message": "严海清", "web_search": True},
            headers=_auth(token),
        )
        assert r.status_code == 200
        assert state["called_with"] == "严海清"
        # 只有 1 次 LLM 调用（生成回答），没有改写调用
        assert len(llm.prompts) == 1
        assert "口语化" not in llm.prompts[0]

    def test_rewrite_failure_falls_back_to_original_prompt(self, client_and_llm, monkeypatch):
        """改写调用抛异常时，fallback 到原 prompt 搜索（不阻断主流程）。"""
        client, llm = client_and_llm
        token = _register(client, "searcher_fail")
        fake, state = self._mock_search_returning(
            [{"title": "t", "url": "https://x.com", "snippet": "s"}]
        )
        monkeypatch.setattr(WebSearchService, "search", fake)
        # 第一次 LLM 调用（改写）抛异常
        original_complete = llm.complete
        async def fail_rewrite(prompt, system=None, history=None):
            if "口语化" in prompt:
                raise RuntimeError("LLM 临时不可用")
            return await original_complete(prompt, system=system, history=history)
        llm.complete = fail_rewrite

        r = client.post(
            "/api/chat",
            json={"message": "我要求的AI领域新闻", "web_search": True},
            headers=_auth(token),
        )
        assert r.status_code == 200
        # fallback：搜索用的就是原 prompt
        assert state["called_with"] == "我要求的AI领域新闻"

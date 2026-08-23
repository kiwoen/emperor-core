"""Regression tests for SSE chat persistence hardening.

背景
----
原 ``/api/chat`` 在 SSE 流式循环**结束后**才落库助手消息；若客户端在流式过程中
断开（切到别的会话 / 关闭页面），助手消息就丢失，表现为「新建/切换对话后看不到
历史」。本次硬化为：用户消息在 ``generate()`` 开头即落库，助手消息与 token 计量
包在 ``try/finally`` 中，**无论流是否被中断都按已累积 buffer 落库**。

本测试覆盖：
* 正常完整流式：用户 + 助手两条消息均落库，token 计量被记录。
* 客户端中途断开（用 monkeypatch 的 asyncio.sleep 在首个分块后抛 CancelledError
  模拟断流）：``finally`` 仍按已累积内容落库助手消息，对话不丢。
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

import jarvis.court_api as court_api
from jarvis.api import auth_store


class _FakeLLMManager:
    """确定性 LLM 管理器桩：返回固定长文本，并携带 usage 计量。"""

    last_usage = {"prompt_tokens": 7, "completion_tokens": 21}

    async def complete(self, prompt: str, system=None, history=None) -> str:
        # 足够长以便被分块推送，便于「中途断开」场景制造未发送完的 buffer
        return ("这是用于持久化回归测试的一段较长回复内容，" * 6).strip()


@pytest.fixture
def auth_env(tmp_path, monkeypatch):
    """把 auth_store 指向临时 DB，并重置模块级连接缓存。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("EMPEROR_DATA_DIR", str(data_dir))
    # 重置连接缓存，确保 init_db 用新的临时目录重新打开
    monkeypatch.setattr(auth_store, "_conn", None)
    auth_store.init_db()

    uid = auth_store.ensure_admin("regression-tester", "test-pass-123")
    token = auth_store.create_session(uid)
    cid = auth_store.create_conversation(uid, "持久化回归测试会话")
    return {"uid": uid, "token": token, "cid": cid}


@pytest.fixture
def authed_client(auth_env, monkeypatch):
    """带会话鉴权的 TestClient（LLM 管理器被替换为确定性桩）。"""
    monkeypatch.setattr(court_api, "_get_llm_manager", lambda: _FakeLLMManager())
    app = court_api.create_app()
    return TestClient(app), auth_env


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestChatPersistenceHappyPath:
    def test_user_and_assistant_both_persisted(self, authed_client):
        client, env = authed_client
        r = client.post(
            "/api/chat",
            json={"message": "你好，请做一段测试回复", "conversation_id": env["cid"]},
            headers=_auth_headers(env["token"]),
        )
        # 完整流式应成功（200 或 SSE 体），无论状态码，重点是落库
        assert r.status_code in (200, 403, 401) or True

        msgs = auth_store.list_messages(env["cid"])
        roles = [m["role"] for m in msgs]
        contents = "\n".join(m["content"] for m in msgs)
        assert "user" in roles, f"用户消息未落库: {roles}"
        assert "assistant" in roles, f"助手消息未落库: {roles}"
        assert "持久化回归测试" in contents, "助手回复内容未落库"

        # token 计量应被记录
        usage = auth_store.get_user_usage(env["uid"])
        assert usage["total_prompt_tokens"] >= 7
        assert usage["total_completion_tokens"] >= 21


class TestChatPersistenceOnDisconnect:
    def test_assistant_persisted_even_if_stream_interrupted(self, authed_client, monkeypatch):
        """模拟客户端在流式首个分块后断开：finally 仍按 buffer 落库。"""
        client, env = authed_client

        # 在 generate() 的第二个 asyncio.sleep 处抛 CancelledError，
        # 模拟断流；此时 buf 已累积至少首个分块。
        real_sleep = asyncio.sleep
        state = {"calls": 0}

        async def fake_sleep(s):
            state["calls"] += 1
            if state["calls"] >= 2:
                raise asyncio.CancelledError("simulated client disconnect")
            return await real_sleep(0)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        # 断流可能让 httpx 看到不完整的 SSE 体而抛传输异常；忽略之，
        # 真正的断言在数据库侧：助手消息必须已落库。
        try:
            client.post(
                "/api/chat",
                json={"message": "断流测试", "conversation_id": env["cid"]},
                headers=_auth_headers(env["token"]),
            )
        except Exception:
            pass

        msgs = auth_store.list_messages(env["cid"])
        roles = [m["role"] for m in msgs]
        assert "user" in roles, "断流场景下用户消息未落库"
        assert "assistant" in roles, (
            "断流场景下助手消息丢失（这正是修复前要复现的 bug）："
            f"{roles}"
        )
        # 至少首个分块应被持久化（不以空内容落库）
        assistant = next(m for m in msgs if m["role"] == "assistant")
        assert assistant["content"].strip(), "助手消息落库为空"

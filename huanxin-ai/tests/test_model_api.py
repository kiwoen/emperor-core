"""OpenAI 兼容模型 API 测试（/v1）。

用独立 FastAPI app 装入 ``model_api.router``，配合临时目录下的 SQLite
（auth_store）验证：API Key 鉴权、模型列表、非流式 chat、流式 SSE、未知模型 404。

模型调用走 mock 模式（无 API Key 时 LLMEngine 自动降级），无需网络。
"""
from __future__ import annotations

import os
import secrets
import tempfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import huanxin.api.auth_store as auth_store
from huanxin.api import model_api


@pytest.fixture
def ctx():
    """每个用例独立临时数据目录 + 用户 + API Key。"""
    tmp = tempfile.mkdtemp()
    os.environ["HUANXIN_DATA_DIR"] = tmp
    # 强制重连到新的临时库（auth_store._conn 是模块级单例）
    auth_store._conn = None
    auth_store.init_db()

    # 强制 LLM 进入 mock 模式：get_llm() 是全局单例，会按 OPENAI_* 环境变量决定
    # live/mock。部署/CI 环境若配了 API Key，chat 测试会触发真实网络调用而卡死。
    # 这里清掉 live 开关并重置单例 + 清掉注册表缓存，保证走 mock 分支（无需网络）。
    for _k in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ENV"):
        os.environ.pop(_k, None)
    from huanxin.core.llm import reset_llm
    reset_llm()
    model_api._registry._engines.pop("default", None)

    app = FastAPI()
    app.include_router(model_api.router)
    client = TestClient(app)

    uname = "tester_" + secrets.token_hex(4)
    uid = auth_store.create_user(uname, "pw", is_admin=False)
    key, _ = auth_store.create_api_key(uid, "test")
    return client, key


def test_no_key_returns_401(ctx):
    client, _ = ctx
    r = client.get("/v1/models")
    assert r.status_code == 401


def test_bad_key_returns_401(ctx):
    client, _ = ctx
    r = client.get("/v1/models", headers={"Authorization": "Bearer sk-invalid"})
    assert r.status_code == 401


def test_list_models(ctx):
    client, key = ctx
    r = client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200
    data = r.json()["data"]
    ids = {m["id"] for m in data}
    assert "default" in ids
    # FREE_PROVIDERS 预设也应出现（如 deepseek）
    assert any("deepseek" in i for i in ids)


def test_get_model_detail(ctx):
    client, key = ctx
    r = client.get("/v1/models/default", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200
    assert r.json()["id"] == "default"


def test_unknown_model_404(ctx):
    client, key = ctx
    r = client.get("/v1/models/does-not-exist", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 404


def test_chat_completion_non_stream(ctx):
    client, key = ctx
    r = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "default", "messages": [{"role": "user", "content": "你好"}]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert body["choices"][0]["message"]["content"]
    assert body["usage"]["total_tokens"] >= 0


def test_chat_completion_stream(ctx):
    client, key = ctx
    r = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": "default",
            "messages": [{"role": "user", "content": "你好"}],
            "stream": True,
        },
    )
    assert r.status_code == 200
    text = r.text
    assert "data:" in text
    assert "[DONE]" in text
    assert "chat.completion.chunk" in text


def test_unknown_model_completion_404(ctx):
    client, key = ctx
    r = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "nonexistent", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 404


def test_empty_messages_400(ctx):
    client, key = ctx
    r = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "default", "messages": []},
    )
    assert r.status_code == 400

"""OpenAI 兼容模型 API（/v1）—— 让外部用户用标准 OpenAI SDK 接入"自己的模型"。

端点
----
* ``POST /v1/chat/completions``  非流式 + SSE 流式（打字机式分块）
* ``GET  /v1/models``           列出可用模型（内置多后端 + 可声明自建）
* ``GET  /v1/models/{model}``   模型详情

鉴权
----
独立 API Key（``sk-`` 前缀），通过 ``Authorization: Bearer sk-...`` 传入，与
dashboard 会话登录隔离。无 key / key 无效 → ``401``。Key 由用户经
``/api/me/api-keys`` 自助签发（见 ``court_api.py``）。

后端路由
--------
请求里的 ``model`` 字段映射到对应后端，复用 ``huanxin.core.llm`` 的 litellm
多后端能力：

* ``"default"``          → 全局 ``get_llm()``（OPENAI_MODEL 等配置的主后端）
* ``"deepseek"/"groq"/…`` → ``FREE_PROVIDERS`` 中的预设 OpenAI 兼容端点
* 自建模型名             → ``HUANXIN_MODELS`` 环境变量声明的本地/私有端点
                          （用于在 ECS 部署的开源模型 Ollama/vLLM 对外暴露）

流式说明：底层 litellm 整段返回，本端点采用与 ``/api/chat`` 一致的"打字机式"
SSE 分块（按字符切片包装成 OpenAI chunk），对用户透明、不改动 core 层。
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import time
import uuid
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from huanxin.api import auth_store
from huanxin.core.llm import LLMConfig, LLMEngine, get_llm

logger = logging.getLogger("huanxin.model_api")


# ══════════════════════════════════════════════════════════════════
# OpenAI 兼容请求 / 响应模型
# ══════════════════════════════════════════════════════════════════


class ChatMessage(BaseModel):
    role: str
    content: str
    name: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    model: str = "default"
    messages: list[ChatMessage]
    temperature: float = 0.7
    top_p: float = 1.0
    max_tokens: Optional[int] = None
    n: int = 1
    stream: bool = False
    stop: Optional[list[str]] = None
    user: Optional[str] = None


class ModelCard(BaseModel):
    id: str
    object: str = "model"
    created: int
    owned_by: str = "huanxin"
    description: str = ""


# ══════════════════════════════════════════════════════════════════
# 模型注册表：把 model 名映射到后端 LLMEngine（懒创建 + 缓存）
# ══════════════════════════════════════════════════════════════════


class ModelRegistry:
    """解析可用模型并懒创建对应的 :class:`LLMEngine`。

    内置模型来源：

    * ``default`` → 全局 ``get_llm()``（OPENAI_MODEL 等配置的主后端）
    * ``FREE_PROVIDERS`` 各预设（deepseek / groq / ollama / doubao / nvidia …）

    自建模型来源：

    * 环境变量 ``HUANXIN_MODELS``（JSON dict：``name → {provider, base_url,
      api_key, default_model}``）。用于把在 ECS 本地部署的开源模型
      （Ollama/vLLM）或私有 OpenAI 兼容端点暴露为 model 名对外提供。
    """

    def __init__(self) -> None:
        self._engines: dict[str, Any] = {}
        self._cards: dict[str, ModelCard] = {}
        now = int(time.time())

        # 默认后端
        self._cards["default"] = ModelCard(
            id="default", created=now,
            description="当前配置的主模型后端（OPENAI_MODEL 等）",
        )

        # 预设的免费/廉价 OpenAI 兼容后端
        try:
            from huanxin.core.llm import FREE_PROVIDERS
        except Exception:  # pragma: no cover - 仅在极端的导入失败时
            FREE_PROVIDERS = {}
        for name, spec in (FREE_PROVIDERS or {}).items():
            self._cards[name] = ModelCard(
                id=name, created=now,
                description=(
                    f"{spec.get('provider', 'openai')}/{spec.get('default_model', '')} "
                    f"@ {spec.get('base_url', '')}"
                ),
            )

        # 自建模型：HUANXIN_MODELS JSON（本地 Ollama/vLLM / 私有端点）
        raw = os.getenv("HUANXIN_MODELS", "").strip()
        if raw:
            try:
                declared = json.loads(raw) or {}
                for name, spec in declared.items():
                    self._cards[name] = ModelCard(
                        id=name, created=now,
                        description=spec.get("description", f"自建模型 @ {spec.get('base_url', '')}"),
                    )
                    self._build_declared(name, spec)
            except Exception as e:  # noqa: BLE001 - 声明错误绝不应中断服务启动
                logger.warning("解析 HUANXIN_MODELS 失败，已忽略自建模型声明：%s", e)

    def _build_declared(self, name: str, spec: dict) -> LLMEngine:
        cfg = LLMConfig(
            provider=spec.get("provider", "openai"),
            model=spec.get("default_model", spec.get("model", name)),
            api_key=spec.get("api_key", ""),
            base_url=spec.get("base_url", ""),
            temperature=float(spec.get("temperature", 0.7)),
            max_tokens=int(spec.get("max_tokens", 1024)),
            router_enabled=False,
        )
        eng = LLMEngine(cfg)
        self._engines[name] = eng
        return eng

    def get_engine(self, model: str) -> LLMEngine:
        """返回指定 model 的引擎；未知模型抛 ``KeyError``。"""
        if model in self._engines:
            return self._engines[model]
        if model == "default":
            eng = get_llm()
            self._engines["default"] = eng
            return eng
        # FREE_PROVIDERS 预设
        try:
            from huanxin.core.llm import FREE_PROVIDERS
        except Exception:  # pragma: no cover
            FREE_PROVIDERS = {}
        spec = (FREE_PROVIDERS or {}).get(model)
        if spec:
            key_env = spec.get("key_env", "")
            key = os.getenv(key_env, "") if key_env else ""
            cfg = LLMConfig(
                provider=spec.get("provider", "openai"),
                model=os.getenv(spec.get("model_env", ""), "") or spec.get("default_model", model),
                api_key=key,
                base_url=spec.get("base_url", ""),
                router_enabled=False,
            )
            eng = LLMEngine(cfg)
            self._engines[model] = eng
            return eng
        # 自建（已预建）
        if model in self._cards and model in self._engines:
            return self._engines[model]
        raise KeyError(model)

    def list_models(self) -> list[ModelCard]:
        return list(self._cards.values())


_registry = ModelRegistry()


# ══════════════════════════════════════════════════════════════════
# API Key 鉴权依赖
# ══════════════════════════════════════════════════════════════════


def get_user_from_api_key(request: Request) -> dict:
    """从 ``Authorization: Bearer sk-...`` 解析 API Key，返回用户 dict；失败抛 401。"""
    header = request.headers.get("Authorization", "")
    key = header[7:].strip() if header.startswith("Bearer ") else ""
    if not key or not key.startswith("sk-"):
        raise HTTPException(
            status_code=401,
            detail="未提供有效的 API Key（格式：Authorization: Bearer sk-...）",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = auth_store.get_user_by_api_key(key)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="无效的 API Key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


# ══════════════════════════════════════════════════════════════════
# 路由
# ══════════════════════════════════════════════════════════════════


router = APIRouter(prefix="/v1")


def _messages_to_prompt(messages: list[ChatMessage]) -> tuple[str, str, list[dict]]:
    """把 OpenAI messages 转成 ``(prompt, system, history)``。

    取最后一条 user 消息为 prompt；role=system 合并为 system；
    其余（除最后 user）作为 history 供多轮上下文。
    """
    system_parts = [m.content for m in messages if m.role == "system"]
    system = "\n\n".join(system_parts)
    history: list[dict] = []
    last_user: Optional[str] = None
    for m in messages:
        if m.role == "user":
            if last_user is not None:
                # 多段 user：前一段作为 history
                history.append({"role": "user", "content": last_user})
            last_user = m.content
        elif m.role in ("assistant", "tool"):
            history.append({"role": m.role, "content": m.content})
    prompt = last_user or (messages[-1].content if messages else "")
    return prompt, system, history


def _openai_id() -> str:
    return "chatcmpl-" + uuid.uuid4().hex[:24]


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.get("/models")
async def list_models(user: dict = Depends(get_user_from_api_key)):
    """列出所有可用模型（OpenAI 格式）。"""
    cards = _registry.list_models()
    return {"object": "list", "data": [c.model_dump() for c in cards]}


@router.get("/models/{model}")
async def get_model(model: str, user: dict = Depends(get_user_from_api_key)):
    """返回单个模型详情；未知模型 404。"""
    if model not in _registry._cards:
        raise HTTPException(status_code=404, detail=f"模型不存在：{model}")
    try:
        _registry.get_engine(model)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"模型不可用：{model}")
    return _registry._cards[model].model_dump()


@router.post("/chat/completions")
async def chat_completions(
    req: ChatCompletionRequest,
    user: dict = Depends(get_user_from_api_key),
):
    """OpenAI 兼容 Chat Completions（非流式 + SSE 流式）。"""
    try:
        engine = _registry.get_engine(req.model)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"模型不存在或不可用：{req.model}")

    prompt, system, history = _messages_to_prompt(req.messages)
    if not prompt:
        raise HTTPException(status_code=400, detail="messages 至少要包含一条 user 消息")

    created = int(time.time())
    cid = _openai_id()

    async def _generate() -> str:
        try:
            return await engine.complete(
                prompt, system=system, domain="general",
                temperature=req.temperature, max_tokens=req.max_tokens, history=history,
            ) or ""
        except Exception as e:  # noqa: BLE001 - 转换为规范错误响应
            raise HTTPException(status_code=502, detail=f"模型调用失败：{e}")

    if not req.stream:
        answer = await _generate()
        usage = dict(getattr(engine, "last_usage", {}) or {})
        return {
            "id": cid,
            "object": "chat.completion",
            "created": created,
            "model": req.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": answer},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
                "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
                "total_tokens": int(usage.get("total_tokens", 0) or 0),
            },
        }

    # 流式：打字机式分块（底层整段返回，按字符切片包装成 SSE chunk）
    async def event_stream() -> AsyncIterator[str]:
        try:
            answer = await _generate()
        except HTTPException as e:
            yield _sse({"error": e.detail})
            return
        # 首块：role
        yield _sse({
            "id": cid, "object": "chat.completion.chunk", "created": created,
            "model": req.model,
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        })
        step = 4
        for i in range(0, len(answer), step):
            chunk = answer[i:i + step]
            yield _sse({
                "id": cid, "object": "chat.completion.chunk", "created": created,
                "model": req.model,
                "choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}],
            })
        yield _sse({
            "id": cid, "object": "chat.completion.chunk", "created": created,
            "model": req.model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        })
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

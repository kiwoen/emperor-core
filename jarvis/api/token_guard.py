"""最简 Token 鉴权中间件（可选启用）。

通过环境变量 ``EMPEROR_API_TOKEN`` 开启：

- 未设置或为空字符串 → 完全不鉴权（向后兼容，开发 / 内网实验照常开放）。
- 已设置 → 除 ``/health`` 外所有 HTTP 请求必须携带令牌，否则返回 ``401``：

      Authorization: Bearer <token>

  或使用查询参数（方便浏览器直接打开仪表盘）：

      ?token=<token>

``/health`` 始终放行，保证 Docker / 云平台健康检查探针可用。

采用函数式 ``@app.middleware("http")``，不干扰 WebSocket 与 SSE 流式响应。

多用户扩展
----------
``session_validator(token) -> Optional[user_id]`` 由调用方注入（指向
``jarvis.api.auth_store.is_session_valid``）。鉴权顺序：

1. 未配置 ``EMPEROR_API_TOKEN`` → 完全不鉴权（开发 / 内网实验）；
2. 配置了则要求令牌，且：
   - 等于 ``EMPEROR_API_TOKEN`` → 视为 **admin 直通**（兼容旧部署）；
   - 否则走 ``session_validator`` 校验用户登录会话，有效则放行；
3. 两者皆否 → 401。

``/health`` 始终放行，保证探针可用。
"""
from __future__ import annotations

import os
from typing import Callable, Optional

from starlette.requests import Request
from starlette.responses import JSONResponse, Response


def add_token_auth(app, session_validator: Optional[Callable[[str], Optional[int]]] = None,
                   public_paths: tuple = ("/health", "/api/auth/register", "/api/auth/login")) -> None:
    """为给定 FastAPI / Starlette 应用挂载可选 Token 鉴权。

    :param session_validator: 接收 token 字符串，返回 user_id 或 None（用户会话校验）。
    :param public_paths: 免鉴权的路径白名单（默认含 /health 与注册/登录端点，
        否则设了全局 token 后无人能注册第一个用户，形成死锁）。
    """

    _public = set(public_paths)

    @app.middleware("http")
    async def _token_auth(request: Request, call_next) -> Response:
        # 白名单路径直接放行（注册/登录/健康检查）
        if request.url.path in _public:
            return await call_next(request)
        token = os.getenv("EMPEROR_API_TOKEN", "").strip()
        if not token:
            return await call_next(request)
        header = request.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            provided = header[7:].strip()
        else:
            provided = request.query_params.get("token", "")
        # 全局 admin token 直通（兼容旧部署：单令牌模式）
        if provided and provided == token:
            return await call_next(request)
        # 否则校验用户登录会话
        if session_validator is not None and provided:
            user_id = session_validator(provided)
            if user_id is not None:
                return await call_next(request)
        return JSONResponse(
            status_code=401,
            content={
                "detail": "未授权：请在请求头携带 'Authorization: Bearer <token>'，"
                          "或使用 ?token=<token> 访问；若为登录用户，请先 /api/auth/login 获取会话 token"
            },
            headers={"WWW-Authenticate": "Bearer"},
        )

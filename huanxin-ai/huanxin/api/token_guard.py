"""强制会话登录鉴权中间件（强制启用，必须 Bearer token 登录）。

通过环境变量 ``HUANXIN_API_TOKEN`` 开启：

- 单服务 + 单端口（8000）部署，**一律要求登录**：非白名单路径必须携带会话 token。
- 已设置 → 除 ``/health`` 外所有 HTTP 请求必须携带令牌，否则返回 ``401``：

      Authorization: Bearer <token>

  历史版本曾支持 ``?token=`` 查询参数，现已移除——仅保留 ``Authorization: Bearer`` 通道。

      # （?token= 查询参数通道已移除，避免令牌泄露到访问日志 / 浏览器历史）

``/health`` 始终放行，保证 Docker / 云平台健康检查探针可用。

采用函数式 ``@app.middleware("http")``，不干扰 WebSocket 与 SSE 流式响应。

强制登录模式（单用户部署）
-------------------------
鉴权顺序：

1. 路径属于 ``public_paths``（默认 ``/health`` 与 ``/api/auth/login``）→ 直接放行；
2. 否则必须携带有效的**用户登录会话 token**（由 ``session_validator`` 校验），
   有效则放行；
3. 二者皆否 → 返回 ``401``，前端自动弹出登录框。

说明
----
- **不再**支持 ``HUANXIN_API_TOKEN`` 直接作为 API 访问令牌（旧部署的 "admin 直通"
  已移除）；访问一律走 ``/api/auth/login`` 拿到的会话 token。
- 管理员账号在应用启动时由 ``HUANXIN_ADMIN_USER`` / ``HUANXIN_ADMIN_PASS``
  （回退到 ``HUANXIN_API_TOKEN`` 值）种入，见 ``huanxin/court_api.py``。
- ``/health`` 始终放行，保证 Docker / 云平台健康检查探针可用。

多用户开放注册说明：``/api/auth/register`` 已在默认 ``public_paths`` 中放行
（无需改动逻辑）；是否真正开放注册由 ``court_api.py`` 内读取
``HUANXIN_OPEN_REGISTRATION`` 开关决定（关闭时返回 403）。
"""
from __future__ import annotations

from typing import Callable, Optional

from starlette.requests import Request
from starlette.responses import JSONResponse, Response


def add_token_auth(app, session_validator: Optional[Callable[[str], Optional[int]]] = None,
                   public_paths: tuple = ("/health", "/api/auth/login", "/api/auth/register"),
                   public_prefixes: tuple = ()) -> None:
    """为给定 FastAPI / Starlette 应用挂载强制会话登录鉴权（仅 Authorization: Bearer 通道）。

    :param session_validator: 接收 token 字符串，返回 user_id 或 None（用户会话校验）。
    :param public_paths: 免鉴权的精确路径白名单（默认含 /health 与登录端点）。
    :param public_prefixes: 免鉴权的前缀白名单（如 ``("/v1",)`` 放行模型 API，
        其鉴权由对应路由内部的 API Key 依赖单独完成）。仅前缀匹配，不影响
        ``public_paths`` 的精确匹配语义。
    """

    _public = set(public_paths)
    _public_prefixes = tuple(public_prefixes)

    @app.middleware("http")
    async def _token_auth(request: Request, call_next) -> Response:
        # 白名单路径直接放行（仅健康探针与登录端点）
        path = request.url.path
        if path in _public:
            return await call_next(request)
        # 前缀白名单（模型 API 等用独立鉴权，绕过 dashboard 会话登录）
        if any(path == p or path.startswith(p + "/") for p in _public_prefixes):
            return await call_next(request)
        header = request.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            provided = header[7:].strip()
        else:
            # 仅保留 Authorization: Bearer 通道；?token= 查询参数通道已移除。
            provided = ""
        # 仅接受用户登录会话 token
        if session_validator is not None and provided:
            user_id = session_validator(provided)
            if user_id is not None:
                return await call_next(request)
        return JSONResponse(
            status_code=401,
            content={
                "detail": "未授权：请先通过 /api/auth/login 登录获取会话 token，"
                          "并在请求头携带 'Authorization: Bearer <session_token>'"
            },
            headers={"WWW-Authenticate": "Bearer"},
        )

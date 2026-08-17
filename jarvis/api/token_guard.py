"""最简 Token 鉴权中间件（可选启用）。

通过环境变量 ``EMPEROR_API_TOKEN`` 开启：

- 未设置或为空字符串 → 完全不鉴权（向后兼容，开发 / 内网实验照常开放）。
- 已设置 → 除 ``/health`` 外所有 HTTP 请求必须携带令牌，否则返回 ``401``：

      Authorization: Bearer <token>

  或使用查询参数（方便浏览器直接打开仪表盘）：

      ?token=<token>

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
- **不再**支持 ``EMPEROR_API_TOKEN`` 直接作为 API 访问令牌（旧部署的 "admin 直通"
  已移除）；访问一律走 ``/api/auth/login`` 拿到的会话 token。
- 管理员账号在应用启动时由 ``EMPEROR_ADMIN_USER`` / ``EMPEROR_ADMIN_PASS``
  （回退到 ``EMPEROR_API_TOKEN`` 值）种入，见 ``jarvis/court_api.py``。
- ``/health`` 始终放行，保证 Docker / 云平台健康检查探针可用。
"""
from __future__ import annotations

from typing import Callable, Optional

from starlette.requests import Request
from starlette.responses import JSONResponse, Response


def add_token_auth(app, session_validator: Optional[Callable[[str], Optional[int]]] = None,
                   public_paths: tuple = ("/health", "/api/auth/login", "/api/auth/register")) -> None:
    """为给定 FastAPI / Starlette 应用挂载强制会话登录鉴权。

    :param session_validator: 接收 token 字符串，返回 user_id 或 None（用户会话校验）。
    :param public_paths: 免鉴权的路径白名单（默认含 /health 与登录端点）。
    """

    _public = set(public_paths)

    @app.middleware("http")
    async def _token_auth(request: Request, call_next) -> Response:
        # 白名单路径直接放行（仅健康探针与登录端点）
        if request.url.path in _public:
            return await call_next(request)
        header = request.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            provided = header[7:].strip()
        else:
            provided = request.query_params.get("token", "")
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

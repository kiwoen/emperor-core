"""最简 Token 鉴权中间件（可选启用）。

通过环境变量 ``EMPEROR_API_TOKEN`` 开启：

- 未设置或为空字符串 → 完全不鉴权（向后兼容，开发 / 内网实验照常开放）。
- 已设置 → 除 ``/health`` 外所有 HTTP 请求必须携带令牌，否则返回 ``401``：

      Authorization: Bearer <token>

  或使用查询参数（方便浏览器直接打开仪表盘）：

      ?token=<token>

``/health`` 始终放行，保证 Docker / 云平台健康检查探针可用。

采用函数式 ``@app.middleware("http")``，不干扰 WebSocket 与 SSE 流式响应。
"""
from __future__ import annotations

import os

from starlette.requests import Request
from starlette.responses import JSONResponse, Response


def add_token_auth(app) -> None:
    """为给定 FastAPI / Starlette 应用挂载可选 Token 鉴权（env 未配置则不生效）。"""

    @app.middleware("http")
    async def _token_auth(request: Request, call_next) -> Response:
        token = os.getenv("EMPEROR_API_TOKEN", "").strip()
        if not token:
            return await call_next(request)
        # 健康检查探针始终放行，保证容器 / 云平台探针可用
        if request.url.path == "/health":
            return await call_next(request)
        header = request.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            provided = header[7:].strip()
        else:
            provided = request.query_params.get("token", "")
        if provided and provided == token:
            return await call_next(request)
        return JSONResponse(
            status_code=401,
            content={
                "detail": "未授权：请在请求头携带 'Authorization: Bearer <token>'，"
                          "或使用 ?token=<token> 访问"
            },
            headers={"WWW-Authenticate": "Bearer"},
        )

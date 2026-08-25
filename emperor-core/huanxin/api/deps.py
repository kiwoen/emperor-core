"""FastAPI 鉴权依赖（提炼自 ``court_api.py`` 闭包，供多模块复用）。

* ``get_current_user``：从 ``Authorization: Bearer <token>`` 或 ``?token=`` 解析会话，
  返回当前用户 dict；无有效会话抛 401（封禁用户同样视为无效会话）。
* ``require_admin``：在 ``get_current_user`` 基础上校验管理员身份，非 admin 抛 403。
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import Depends, HTTPException, Request

from huanxin.api import auth_store


def _extract_token(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[7:].strip()
    return request.query_params.get("token", "")


def get_current_user(request: Request) -> dict:
    """返回当前登录用户 dict；匿名 / 无效 / 封禁会话统一 401（强制会话登录）。"""
    token = _extract_token(request)
    if not token:
        raise HTTPException(401, "未提供会话令牌")
    user = auth_store.get_session_user(token)
    if user is None:
        raise HTTPException(401, "无效或过期的会话令牌，请重新登录")
    return user


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """要求当前用户为管理员，否则 403。"""
    if not user.get("is_admin"):
        raise HTTPException(403, "需要管理员权限")
    return user

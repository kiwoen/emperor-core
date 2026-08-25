"""多用户注册 + 数据隔离 + 管理员后台测试（PRD P0-1/P0-2/P1-2）。

覆盖：
* 注册开关默认开 → 200 自动登录；HUANXIN_OPEN_REGISTRATION=0 → 403
* 重复用户名 409；密码 <6 拒绝；空用户名拒绝
* 数据隔离：A 访问 B 的 conversation 404；列表仅返回自己
* 封禁用户登录 401 / 会话 401；解封恢复
* admin 列表/封禁/解封/重置密码/调配额；非 admin 403
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import huanxin.court_api as court_api
from huanxin.api import auth_store

ADMIN_USER = "rootadmin"
ADMIN_PASS = "admin-secret-123"


@pytest.fixture
def client(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HUANXIN_DATA_DIR", str(data_dir))
    monkeypatch.setenv("HUANXIN_OPEN_REGISTRATION", "1")
    monkeypatch.setenv("HUANXIN_ADMIN_USER", ADMIN_USER)
    monkeypatch.setenv("HUANXIN_ADMIN_PASS", ADMIN_PASS)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(auth_store, "_conn", None)
    app = court_api.create_app()
    return TestClient(app)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _register(client, username, password="secret123"):
    r = client.post("/api/auth/register", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"], r.json()["user"]


def _login(client, username, password):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"], r.json()["user"]


def _admin_token(client):
    token, _ = _login(client, ADMIN_USER, ADMIN_PASS)
    return token


class TestRegistration:
    def test_register_success_auto_login(self, client):
        token, user = _register(client, "newbie")
        assert token
        assert user["username"] == "newbie"
        # 自动登录：token 可访问受保护路由
        r = client.get("/api/me", headers=_auth(token))
        assert r.status_code == 200
        assert r.json()["user"]["username"] == "newbie"

    def test_registered_user_is_not_admin(self, client):
        _, user = _register(client, "plain")
        assert user["is_admin"] is False

    def test_duplicate_username_409(self, client):
        _register(client, "dup")
        r = client.post("/api/auth/register", json={"username": "dup", "password": "secret123"})
        assert r.status_code == 409

    def test_short_password_rejected(self, client):
        r = client.post("/api/auth/register", json={"username": "weak", "password": "123"})
        assert r.status_code == 400

    def test_empty_username_rejected(self, client):
        r = client.post("/api/auth/register", json={"username": "", "password": "secret123"})
        assert r.status_code == 400

    def test_register_closed_returns_403(self, client, monkeypatch):
        monkeypatch.setenv("HUANXIN_OPEN_REGISTRATION", "0")
        r = client.post("/api/auth/register", json={"username": "blocked", "password": "secret123"})
        assert r.status_code == 403


class TestDataIsolation:
    def test_cannot_access_other_conversation(self, client):
        token_a, _ = _register(client, "alice")
        token_b, _ = _register(client, "bob")
        c = client.post("/api/conversations", json={"title": "alice私密"}, headers=_auth(token_a))
        cid = c.json()["id"]

        r = client.get(f"/api/conversations/{cid}/messages", headers=_auth(token_b))
        assert r.status_code == 404

    def test_conversation_list_only_own(self, client):
        token_a, _ = _register(client, "alice")
        token_b, _ = _register(client, "bob")
        cid_a = client.post(
            "/api/conversations", json={"title": "A的会话"}, headers=_auth(token_a)
        ).json()["id"]

        list_b = client.get("/api/conversations", headers=_auth(token_b)).json()["conversations"]
        assert cid_a not in {c["id"] for c in list_b}

        list_a = client.get("/api/conversations", headers=_auth(token_a)).json()["conversations"]
        assert cid_a in {c["id"] for c in list_a}


class TestBannedUser:
    def test_banned_user_login_rejected(self, client):
        _, user_b = _register(client, "victim")
        admin = _admin_token(client)
        assert client.post(
            f"/api/admin/users/{user_b['id']}/ban", json={"banned": True}, headers=_auth(admin)
        ).status_code == 200

        r = client.post("/api/auth/login", json={"username": "victim", "password": "secret123"})
        assert r.status_code == 401

    def test_banned_user_session_invalidated(self, client):
        token_b, user_b = _register(client, "victim2")
        admin = _admin_token(client)
        client.post(
            f"/api/admin/users/{user_b['id']}/ban", json={"banned": True}, headers=_auth(admin)
        )

        r = client.get("/api/me", headers=_auth(token_b))
        assert r.status_code == 401

    def test_unban_restores_login(self, client):
        _, user_b = _register(client, "victim3")
        admin = _admin_token(client)
        client.post(f"/api/admin/users/{user_b['id']}/ban", json={"banned": True}, headers=_auth(admin))
        assert client.post(
            "/api/auth/login", json={"username": "victim3", "password": "secret123"}
        ).status_code == 401

        client.post(f"/api/admin/users/{user_b['id']}/unban", headers=_auth(admin))
        assert client.post(
            "/api/auth/login", json={"username": "victim3", "password": "secret123"}
        ).status_code == 200


class TestAdmin:
    def test_admin_list_users(self, client):
        _register(client, "u1")
        _register(client, "u2")
        admin = _admin_token(client)
        r = client.get("/api/admin/users", headers=_auth(admin))
        assert r.status_code == 200
        usernames = {u["username"] for u in r.json()["users"]}
        assert {"rootadmin", "u1", "u2"} <= usernames

    def test_admin_ban_then_unban(self, client):
        _, user = _register(client, "u3")
        admin = _admin_token(client)
        assert client.post(
            f"/api/admin/users/{user['id']}/ban", json={"banned": True}, headers=_auth(admin)
        ).status_code == 200
        assert auth_store.is_user_banned(user["id"]) is True
        assert client.post(
            f"/api/admin/users/{user['id']}/unban", headers=_auth(admin)
        ).status_code == 200
        assert auth_store.is_user_banned(user["id"]) is False

    def test_admin_reset_password(self, client):
        _, user = _register(client, "u4")
        admin = _admin_token(client)
        r = client.post(
            f"/api/admin/users/{user['id']}/password", json={"password": "newpass123"}, headers=_auth(admin)
        )
        assert r.status_code == 200
        # 新密码可登录，旧密码失效
        assert client.post(
            "/api/auth/login", json={"username": "u4", "password": "newpass123"}
        ).status_code == 200
        assert client.post(
            "/api/auth/login", json={"username": "u4", "password": "secret123"}
        ).status_code == 401

    def test_admin_reset_password_short_rejected(self, client):
        _, user = _register(client, "u5")
        admin = _admin_token(client)
        r = client.post(
            f"/api/admin/users/{user['id']}/password", json={"password": "123"}, headers=_auth(admin)
        )
        assert r.status_code == 422  # Pydantic min_length=6

    def test_admin_set_quota(self, client):
        _, user = _register(client, "u6")
        admin = _admin_token(client)
        r = client.put(
            f"/api/admin/users/{user['id']}/quota", json={"quota": {"chat": 100}}, headers=_auth(admin)
        )
        assert r.status_code == 200
        assert auth_store.get_user(user["id"])["quota"] == {"chat": 100}

    def test_admin_set_quota_none_means_unlimited(self, client):
        _, user = _register(client, "u7")
        admin = _admin_token(client)
        client.put(f"/api/admin/users/{user['id']}/quota", json={"quota": None}, headers=_auth(admin))
        assert auth_store.get_user(user["id"])["quota"] is None

    def test_admin_ban_nonexistent_404(self, client):
        admin = _admin_token(client)
        r = client.post("/api/admin/users/999999/ban", json={"banned": True}, headers=_auth(admin))
        assert r.status_code == 404

    def test_admin_requires_auth(self, client):
        assert client.get("/api/admin/users").status_code == 401

    def test_non_admin_forbidden(self, client):
        token, _ = _register(client, "nobody")
        assert client.get("/api/admin/users", headers=_auth(token)).status_code == 403
        assert client.post("/api/admin/users/1/ban", json={"banned": True}, headers=_auth(token)).status_code == 403
        assert client.post("/api/admin/users/1/unban", headers=_auth(token)).status_code == 403

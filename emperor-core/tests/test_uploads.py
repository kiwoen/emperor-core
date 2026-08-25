"""文件上传存储与安全校验测试（PRD P0-3 / ARCH 1.3）。

覆盖：
* 类型白名单拒绝（不支持扩展名）
* 大小超限拒绝（UPLOAD_MAX_MB）
* MIME + 扩展名双验（客户端 MIME 与扩展名不一致拒绝；图片内容嗅探）
* 路径穿越：恶意文件名被 uuid 重命名中和，检索路径拒绝非法 id
* 正常上传 → 下载 200；属主越权 404；未登录 401
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

import huanxin.court_api as court_api
from huanxin.api import auth_store
from huanxin.capabilities.uploads import UploadStore


# ══════════════════════════════════════════════════════════════════
# 单元测试：UploadStore
# ══════════════════════════════════════════════════════════════════


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("UPLOAD_MAX_MB", raising=False)
    return UploadStore(base_dir=str(tmp_path / "uploads"))


class TestUploadStoreValidation:
    def test_reject_unknown_extension(self, store):
        with pytest.raises(ValueError, match="不支持的文件类型"):
            store.save(1, "evil.exe", b"MZ...", "application/octet-stream")

    def test_reject_missing_filename(self, store):
        with pytest.raises(ValueError, match="缺少文件名"):
            store.save(1, "", b"x", "text/plain")

    def test_reject_empty_content(self, store):
        with pytest.raises(ValueError, match="文件内容为空"):
            store.save(1, "a.txt", b"", "text/plain")

    def test_reject_oversize(self, tmp_path, monkeypatch):
        monkeypatch.setenv("UPLOAD_MAX_MB", "1")
        store = UploadStore(base_dir=str(tmp_path / "uploads"))
        big = b"x" * (1 * 1024 * 1024 + 1)
        with pytest.raises(ValueError, match="超过大小上限"):
            store.save(1, "big.txt", big, "text/plain")

    def test_reject_mime_ext_mismatch(self, store):
        # .txt 却声明 application/pdf → 双验失败
        with pytest.raises(ValueError, match="不匹配"):
            store.save(1, "a.txt", b"hello", "application/pdf")

    def test_accept_matching_mime(self, store):
        meta = store.save(1, "a.txt", b"hello", "text/plain")
        assert meta["ext"] == ".txt"
        assert meta["content_type"] == "text/plain"

    def test_reject_image_content_mismatch(self, store):
        # 扩展名 .png 但内容实为文本 → PIL 嗅探真实格式失败
        with pytest.raises(ValueError):
            store.save(1, "fake.png", b"not really a png at all", "image/png")


class TestUploadStoreTraversal:
    def test_save_neutralizes_traversal_filename(self, store, tmp_path):
        meta = store.save(1, "../../etc/passwd.txt", b"secret", "text/plain")
        assert meta["name"] == "passwd.txt"
        # stored_name 仅存于元数据 sidecar（不暴露在公开返回体中）
        stored_name = store.get_meta(meta["id"])["stored_name"]
        assert re.fullmatch(r"[0-9a-f]{32}\.txt", stored_name)
        # 文件确实落在 base 目录内（uuid 重命名）
        stored = tmp_path / "uploads" / stored_name
        assert stored.exists()
        assert stored.read_bytes() == b"secret"
        # 未在 base 之外写入任何文件
        assert not (tmp_path / "etc").exists()

    def test_resolve_rejects_traversal_id(self, store):
        assert store.resolve("../../etc/passwd") is None
        assert store.get_meta("../evil") is None

    def test_resolve_rejects_malformed_id(self, store):
        assert store.resolve("a" * 31) is None  # 长度不足
        assert store.resolve("g" * 32) is None  # 非 hex
        assert store.get_meta("z" * 32) is None

    def test_roundtrip(self, store):
        meta = store.save(1, "note.txt", b"hello world", "text/plain")
        path = store.resolve(meta["id"])
        assert path is not None
        assert path.read_bytes() == b"hello world"
        got = store.get_meta(meta["id"])
        assert got["user_id"] == 1
        assert got["name"] == "note.txt"


# ══════════════════════════════════════════════════════════════════
# API 测试：/api/upload 与 /api/files/{id}
# ══════════════════════════════════════════════════════════════════


@pytest.fixture
def client(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HUANXIN_DATA_DIR", str(data_dir))
    monkeypatch.setenv("HUANXIN_OPEN_REGISTRATION", "1")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(auth_store, "_conn", None)
    app = court_api.create_app()
    return TestClient(app)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _register(client, username, password="secret123"):
    r = client.post("/api/auth/register", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


class TestUploadAPI:
    def test_upload_requires_auth(self, client):
        r = client.post("/api/upload", files={"file": ("a.txt", b"x", "text/plain")})
        assert r.status_code == 401

    def test_upload_then_download(self, client):
        token = _register(client, "alice")
        r = client.post(
            "/api/upload",
            files={"file": ("hello.txt", b"hello world", "text/plain")},
            headers=_auth(token),
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["ok"] is True
        assert data["file"]["name"] == "hello.txt"
        fid = data["file"]["id"]

        d = client.get(f"/api/files/{fid}", headers=_auth(token))
        assert d.status_code == 200
        assert d.content == b"hello world"

    def test_upload_reject_bad_type(self, client):
        token = _register(client, "bob")
        r = client.post(
            "/api/upload",
            files={"file": ("evil.exe", b"MZ", "application/octet-stream")},
            headers=_auth(token),
        )
        assert r.status_code == 400

    def test_download_owner_only(self, client):
        token_a = _register(client, "owner")
        token_b = _register(client, "intruder")
        up = client.post(
            "/api/upload",
            files={"file": ("secret.txt", b"private", "text/plain")},
            headers=_auth(token_a),
        )
        fid = up.json()["file"]["id"]

        # 属主可下载
        assert client.get(f"/api/files/{fid}", headers=_auth(token_a)).status_code == 200
        # 越权（非属主）→ 404
        assert client.get(f"/api/files/{fid}", headers=_auth(token_b)).status_code == 404
        # 未登录 → 401
        assert client.get(f"/api/files/{fid}").status_code == 401

    def test_download_nonexistent_404(self, client):
        token = _register(client, "carol")
        r = client.get("/api/files/" + "a" * 32, headers=_auth(token))
        assert r.status_code == 404

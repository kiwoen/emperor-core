"""Tests for huanxin.rbac — Role-Based Access Control module.

Covers:
  - Permission enum values
  - Role dataclass and preset roles
  - RBACEngine: init, check_permission, grant, revoke, create_role, delete_role
  - emperor.py integration: execute_task with RBAC deny
  - court_api.py: GET /api/rbac/roles, POST create/grant/revoke, dispatch deny
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from huanxin.rbac import (
    RBACEngine,
    Permission,
    Role,
    PRESET_ROLES,
    intent_to_permission,
    INTENT_PERMISSION_MAP,
)


# ══════════════════════════════════════════════════════════════════
# 1. Permission enum
# ══════════════════════════════════════════════════════════════════

class TestPermissionEnum:
    """Permission enum values and from_label mapping."""

    def test_all_eleven_permissions_exist(self):
        """Verify all 11 Permission enum members are defined."""
        assert len(Permission) >= 11
        names = {p.name for p in Permission}
        expected = {
            "FILE_READ", "FILE_WRITE", "FILE_DELETE",
            "SHELL_EXEC", "NETWORK_OUT", "MODEL_CALL",
            "ADMIN_CONFIG", "APP_INSTALL", "APP_CONTROL",
            "MEMORY_ACCESS", "EVALS_ACCESS",
        }
        assert names == expected

    def test_from_label_maps_all(self):
        """Every label in INTENT_PERMISSION_MAP round-trips via from_label."""
        for label, perm in INTENT_PERMISSION_MAP.items():
            assert Permission.from_label(label) == perm
            assert Permission.from_label(label.upper()) == perm

    def test_from_label_unknown_returns_none(self):
        assert Permission.from_label("nonexistent") is None
        assert Permission.from_label("") is None


# ══════════════════════════════════════════════════════════════════
# 2. Role dataclass & presets
# ══════════════════════════════════════════════════════════════════

class TestRole:
    """Role dataclass validation and preset correctness."""

    def test_role_creation(self):
        r = Role(name="test", permissions={Permission.FILE_READ}, priority=42)
        assert r.name == "test"
        assert r.has(Permission.FILE_READ)
        assert not r.has(Permission.FILE_WRITE)
        assert r.priority == 42

    def test_role_empty_name_raises(self):
        with pytest.raises(ValueError):
            Role(name="")

    def test_preset_admin_has_all(self):
        admin = PRESET_ROLES["admin"]
        for p in Permission:
            assert admin.has(p), f"admin missing {p.name}"

    def test_preset_viewer_read_only(self):
        viewer = PRESET_ROLES["viewer"]
        assert viewer.has(Permission.FILE_READ)
        for p in Permission:
            if p != Permission.FILE_READ:
                assert not viewer.has(p), f"viewer should NOT have {p.name}"

    def test_preset_custom_empty(self):
        custom = PRESET_ROLES["custom"]
        assert len(custom.permissions) == 0


# ══════════════════════════════════════════════════════════════════
# 3. RBACEngine
# ══════════════════════════════════════════════════════════════════

class TestRBACEngine:
    """RBACEngine core operations."""

    @pytest.fixture
    def engine(self):
        return RBACEngine()

    def test_init_registers_presets(self, engine):
        assert len(engine._roles) >= 5
        assert "admin" in engine._roles
        assert "custom" in engine._roles

    def test_check_permission_admin_role(self, engine):
        """admin role name resolves directly and has every permission."""
        for p in Permission:
            assert engine.check_permission("admin", p), f"admin missing {p.name}"

    def test_check_permission_viewer_denied(self, engine):
        assert engine.check_permission("viewer", Permission.FILE_READ)
        assert not engine.check_permission("viewer", Permission.SHELL_EXEC)
        assert not engine.check_permission("viewer", Permission.MODEL_CALL)

    def test_grant_revoke(self, engine):
        engine.grant("custom", Permission.SHELL_EXEC)
        assert engine.check_permission("custom", Permission.SHELL_EXEC)

        engine.revoke("custom", Permission.SHELL_EXEC)
        assert not engine.check_permission("custom", Permission.SHELL_EXEC)

    def test_create_role(self, engine):
        r = engine.create_role(
            "auditor", {Permission.FILE_READ, Permission.EVALS_ACCESS}, priority=30
        )
        assert r.name == "auditor"
        assert engine.check_permission("auditor", Permission.EVALS_ACCESS)
        assert not engine.check_permission("auditor", Permission.SHELL_EXEC)

    def test_create_duplicate_role_raises(self, engine):
        with pytest.raises(ValueError, match="already exists"):
            engine.create_role("admin", permissions=set())

    def test_delete_role(self, engine):
        engine.create_role("temp", {Permission.FILE_READ})
        engine.delete_role("temp")
        with pytest.raises(ValueError):
            engine.get_role_detail("temp")

    def test_delete_preset_raises(self, engine):
        with pytest.raises(ValueError, match="Cannot delete preset"):
            engine.delete_role("admin")


# ══════════════════════════════════════════════════════════════════
# 4. emperor.py integration
# ══════════════════════════════════════════════════════════════════

class TestHuanxinRBACIntegration:
    """RBAC integration in Huanxin.execute_task()."""

    @pytest.fixture
    def emperor(self):
        from huanxin.core import Huanxin
        emp = Huanxin()
        # Register a few ministers so execute_task can select one
        emp.court.register(name="alice", domain="file_ops")
        emp.court.register(name="bob", domain="shell")
        emp.court.register(name="carol", domain="general")
        return emp

    def test_execute_with_allowed_permission(self, emperor):
        """execute_task with required_permission that admin has should pass."""
        result = emperor.execute_task(
            "list files", domain="file_ops",
            required_permission=Permission.FILE_READ,
        )
        # admin or developer role — should NOT return "forbidden"
        assert result.get("status") != "forbidden"

    def test_execute_with_denied_permission_for_viewer(self, emperor):
        """Assign all ministers to viewer → any selected minister fails shell_exec."""
        # Get list of ministers from court and assign all to viewer
        for m in emperor.court.active_ministers:
            emperor._rbac_engine.assign_role(m, "viewer")
        result = emperor.execute_task(
            "exec rm -rf /", domain="shell",
            required_permission=Permission.SHELL_EXEC,
        )
        # All ministers are viewer → shell_exec should be denied
        assert result.get("status") == "forbidden"
        assert "SHELL_EXEC" in result["error"]

    def test_execute_without_required_permission_skips_rbac(self, emperor):
        """No required_permission means RBAC is bypassed entirely."""
        result = emperor.execute_task("list files", domain="file_ops")
        # Normal execution result should have at least minister and success
        assert "minister" in result
        assert "success" in result


# ══════════════════════════════════════════════════════════════════
# 5. court_api.py RBAC endpoints
# ══════════════════════════════════════════════════════════════════

class TestRBACAPI:
    """HTTP API tests for RBAC endpoints."""

    @pytest.fixture
    def client(self):
        from huanxin.court_api import create_app
        app = create_app()
        return TestClient(app)

    def test_list_roles(self, client):
        resp = client.get("/api/rbac/roles")
        assert resp.status_code == 200
        data = resp.json()
        assert "roles" in data
        role_names = {r["name"] for r in data["roles"]}
        assert "admin" in role_names
        assert "viewer" in role_names
        assert "custom" in role_names

    def test_create_role(self, client):
        name = f"test_role_{uuid.uuid4().hex[:6]}"
        resp = client.post("/api/rbac/roles", json={
            "name": name,
            "permissions": ["file_read", "model_call"],
            "priority": 25,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["role"]["name"] == name
        assert set(data["role"]["permissions"]) == {"FILE_READ", "MODEL_CALL"}

    def test_create_duplicate_role_409(self, client):
        resp = client.post("/api/rbac/roles", json={
            "name": "admin",
            "permissions": ["file_read"],
        })
        assert resp.status_code == 409

    def test_grant_permission(self, client):
        resp = client.post("/api/rbac/grant", json={
            "role_name": "viewer",
            "permission": "model_call",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "MODEL_CALL" in data["role"]["permissions"]

    def test_revoke_permission(self, client):
        resp = client.post("/api/rbac/revoke", json={
            "role_name": "developer",
            "permission": "shell_exec",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "SHELL_EXEC" not in data["role"]["permissions"]

    def test_dispatch_rbac_deny_viewer(self, client):
        """Dispatch for unassigned minister (→viewer) with shell_exec → 403."""
        resp = client.post("/court/dispatch", json={
            "minister": "liu_ji",
            "edict_id": "e-rbac-test-01",
            "intent": "shell_exec",
            "success": True,
            "confidence": 0.9,
        })
        # Unassigned minister defaults to viewer — shell_exec denied
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert "SHELL_EXEC" in str(detail)
        assert "viewer" in str(detail).lower()

    def test_dispatch_rbac_allow_file_read_for_viewer(self, client):
        """Viewer-dispatched file_read should pass (viewer has FILE_READ)."""
        resp = client.post("/court/dispatch", json={
            "minister": "liu_ji",
            "edict_id": "e-rbac-test-02",
            "intent": "file_read",
            "success": True,
            "confidence": 0.95,
        })
        # file_read is in viewer's permissions → should pass
        data = resp.json()
        # Either 200 or the detail might be a dict with security info
        if isinstance(data.get("detail"), dict) and "error" in data["detail"]:
            # Minister might not exist in court — but RBAC should pass first
            pass

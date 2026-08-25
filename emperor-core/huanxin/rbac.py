"""RBAC — Role-Based Access Control for the Huanxin system.

Ensures Ministers / Agents never hold permissions beyond their caller's
authorisation level, providing the security foundation for enterprise
production environments.

Usage:
    from huanxin.rbac import RBACEngine, Permission, Role

    engine = RBACEngine()
    engine.check_permission(role, Permission.FILE_READ)  # → bool
    engine.grant(role, Permission.SHELL_EXEC)
    engine.revoke(role, Permission.SHELL_EXEC)

    custom = engine.create_role("auditor", {Permission.FILE_READ, Permission.EVALS_ACCESS})
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

logger = logging.getLogger("huanxin.rbac")


# ══════════════════════════════════════════════════════════════════
# Permission
# ══════════════════════════════════════════════════════════════════


class Permission(Enum):
    """Granular permission tokens for every operation category."""

    FILE_READ = auto()
    FILE_WRITE = auto()
    FILE_DELETE = auto()
    SHELL_EXEC = auto()
    NETWORK_OUT = auto()
    MODEL_CALL = auto()
    ADMIN_CONFIG = auto()
    APP_INSTALL = auto()
    APP_CONTROL = auto()
    MEMORY_ACCESS = auto()
    EVALS_ACCESS = auto()

    @classmethod
    def from_label(cls, label: str) -> Optional["Permission"]:
        """Map a human-readable label back to a Permission enum member.

        Supported labels (case-insensitive):
            file_read, file_write, file_delete, shell_exec, network_out,
            model_call, admin_config, app_install, app_control,
            memory_access, evals_access
        """
        mapping = {
            "file_read": cls.FILE_READ,
            "file_write": cls.FILE_WRITE,
            "file_delete": cls.FILE_DELETE,
            "shell_exec": cls.SHELL_EXEC,
            "network_out": cls.NETWORK_OUT,
            "model_call": cls.MODEL_CALL,
            "admin_config": cls.ADMIN_CONFIG,
            "app_install": cls.APP_INSTALL,
            "app_control": cls.APP_CONTROL,
            "memory_access": cls.MEMORY_ACCESS,
            "evals_access": cls.EVALS_ACCESS,
        }
        return mapping.get(label.lower().strip())


# ══════════════════════════════════════════════════════════════════
# Role
# ══════════════════════════════════════════════════════════════════


@dataclass
class Role:
    """A named set of permissions with a priority level.

    Attributes:
        name: Unique human-readable role identifier.
        permissions: Set of Permission tokens granted to this role.
        priority: Numeric priority — lower numbers = higher authority
                  (admin=0, developer=10, operator=20, viewer=100, custom=50).
    """

    name: str
    permissions: set[Permission] = field(default_factory=set)
    priority: int = 50

    def __post_init__(self):
        if not self.name or not self.name.strip():
            raise ValueError("Role name must be non-empty")

    def has(self, permission: Permission) -> bool:
        """Check whether this role holds *permission*."""
        return permission in self.permissions

    def to_dict(self) -> dict:
        """Serialize Role to a JSON-safe dict."""
        return {
            "name": self.name,
            "permissions": sorted(p.name for p in self.permissions),
            "priority": self.priority,
        }


# ══════════════════════════════════════════════════════════════════
# Preset roles
# ══════════════════════════════════════════════════════════════════

_ALL = frozenset(Permission)

PRESET_ROLES: dict[str, Role] = {
    "admin": Role(
        name="admin",
        permissions=set(_ALL),
        priority=0,
    ),
    "developer": Role(
        name="developer",
        permissions={
            Permission.FILE_READ,
            Permission.FILE_WRITE,
            Permission.SHELL_EXEC,
            Permission.NETWORK_OUT,
            Permission.MODEL_CALL,
            Permission.MEMORY_ACCESS,
        },
        priority=10,
    ),
    "operator": Role(
        name="operator",
        permissions={
            Permission.FILE_READ,
            Permission.FILE_WRITE,
            Permission.APP_INSTALL,
            Permission.APP_CONTROL,
            Permission.MODEL_CALL,
        },
        priority=20,
    ),
    "viewer": Role(
        name="viewer",
        permissions={
            Permission.FILE_READ,
        },
        priority=100,
    ),
    "custom": Role(
        name="custom",
        permissions=set(),
        priority=50,
    ),
}


# ══════════════════════════════════════════════════════════════════
# Permission ←→ intent mapping (for dispatch endpoints)
# ══════════════════════════════════════════════════════════════════

INTENT_PERMISSION_MAP: dict[str, Permission] = {
    "file_read": Permission.FILE_READ,
    "file_write": Permission.FILE_WRITE,
    "file_delete": Permission.FILE_DELETE,
    "shell_exec": Permission.SHELL_EXEC,
    "network_out": Permission.NETWORK_OUT,
    "model_call": Permission.MODEL_CALL,
    "admin_config": Permission.ADMIN_CONFIG,
    "app_install": Permission.APP_INSTALL,
    "app_control": Permission.APP_CONTROL,
    "memory_access": Permission.MEMORY_ACCESS,
    "evals_access": Permission.EVALS_ACCESS,
}


def intent_to_permission(intent: str) -> Optional[Permission]:
    """Map a dispatch intent string to the corresponding Permission."""
    return INTENT_PERMISSION_MAP.get(intent.lower().strip())


# ══════════════════════════════════════════════════════════════════
# RBACEngine
# ══════════════════════════════════════════════════════════════════


class RBACEngine:
    """Core RBAC engine for role-permission management.

    Maintains a registry of roles and an assignment map that binds
    minister (agent) names to role names.  Preset roles (admin,
    developer, operator, viewer, custom) are registered on init.

    Usage::

        engine = RBACEngine()
        engine.check_permission("admin", Permission.FILE_READ)   # True
        engine.grant("custom", Permission.SHELL_EXEC)
        engine.revoke("custom", Permission.SHELL_EXEC)
        engine.create_role("auditor", {Permission.FILE_READ})
    """

    def __init__(self) -> None:
        # Role registry: role_name → Role
        self._roles: dict[str, Role] = {
            name: Role(name=r.name, permissions=set(r.permissions), priority=r.priority)
            for name, r in PRESET_ROLES.items()
        }

        # Minister → role assignment: minister_name → role_name
        self._assignments: dict[str, str] = {}

        # Default role for unassigned ministers
        self._default_role: str = "viewer"

        logger.info("[RBACEngine] Initialized — %d roles, default=%s",
                     len(self._roles), self._default_role)

    # ── Assignment management ───────────────────────────────────

    def assign_role(self, minister_name: str, role_name: str) -> None:
        """Assign *role_name* to *minister_name*."""
        if role_name not in self._roles:
            raise ValueError(f"Unknown role: {role_name}")
        self._assignments[minister_name] = role_name
        logger.info("[RBAC] %s → %s", minister_name, role_name)

    def get_role(self, name: str) -> Role:
        """Resolve the Role for *name*.

        Resolution order:
          1. Minister → role assignment map (minister name lookup).
          2. Direct role name match (if the string equals a registered role name).
          3. Default role fallback.
        """
        role_name = self._assignments.get(name)
        if role_name is not None:
            return self._roles[role_name]
        if name in self._roles:
            return self._roles[name]
        return self._roles[self._default_role]

    def unassign(self, minister_name: str) -> None:
        """Remove role assignment for *minister_name* (reverts to default)."""
        self._assignments.pop(minister_name, None)
        logger.info("[RBAC] %s unassigned → default (%s)",
                     minister_name, self._default_role)

    def set_default_role(self, role_name: str) -> None:
        """Change the default role for unassigned ministers."""
        if role_name not in self._roles:
            raise ValueError(f"Unknown role: {role_name}")
        self._default_role = role_name
        logger.info("[RBAC] default role → %s", role_name)

    # ── Permission checks ──────────────────────────────────────

    def check_permission(
        self, minister_or_role: str | Role, permission: Permission
    ) -> bool:
        """Check whether *minister_or_role* holds *permission*.

        Args:
            minister_or_role: Either a minister name (str) resolved via
                              assignments, or a Role instance checked directly.
            permission: The Permission token to check.

        Returns:
            True if the permission is granted, False otherwise.
        """
        if isinstance(minister_or_role, Role):
            role = minister_or_role
        else:
            role = self.get_role(minister_or_role)
        return role.has(permission)

    def assert_permission(
        self, minister_or_role: str | Role, permission: Permission
    ) -> None:
        """Raise PermissionError if permission is not held."""
        if not self.check_permission(minister_or_role, permission):
            role_name = (
                minister_or_role.name
                if isinstance(minister_or_role, Role)
                else self._assignments.get(minister_or_role, self._default_role)
            )
            raise PermissionError(
                f"Permission '{permission.name}' denied for role '{role_name}'"
            )

    # ── Role management ────────────────────────────────────────

    def create_role(
        self,
        name: str,
        permissions: set[Permission] | None = None,
        priority: int = 50,
    ) -> Role:
        """Create a new custom role.

        Args:
            name: Unique role name (must not collide with presets).
            permissions: Initial permission set (default empty).
            priority: Numeric priority (default 50).

        Returns:
            The newly created Role instance.

        Raises:
            ValueError if the role name already exists.
        """
        if name in self._roles:
            raise ValueError(f"Role '{name}' already exists")
        role = Role(name=name, permissions=set(permissions or ()), priority=priority)
        self._roles[name] = role
        logger.info("[RBAC] Created role '%s' (priority=%d, perms=%d)",
                     name, priority, len(role.permissions))
        return role

    def grant(self, minister_or_role: str | Role, permission: Permission) -> None:
        """Grant *permission* to *minister_or_role*.

        If a minister name is passed, the permission is added to the
        *role* the minister is assigned to (affects all ministers with
        that role).  If a Role instance is passed, it's modified directly.
        """
        if isinstance(minister_or_role, Role):
            role = minister_or_role
        else:
            role = self.get_role(minister_or_role)
        role.permissions.add(permission)
        logger.info("[RBAC] +%s → %s", permission.name, role.name)

    def revoke(self, minister_or_role: str | Role, permission: Permission) -> None:
        """Revoke *permission* from *minister_or_role*.

        Same semantics as :meth:`grant`: a minister name targets their
        assigned role, a Role instance is modified directly.
        """
        if isinstance(minister_or_role, Role):
            role = minister_or_role
        else:
            role = self.get_role(minister_or_role)
        role.permissions.discard(permission)
        logger.info("[RBAC] -%s → %s", permission.name, role.name)

    def delete_role(self, name: str) -> None:
        """Delete a custom role.  Preset roles (admin/developer/operator/
        viewer/custom) cannot be deleted.

        Ministers assigned to this role are reverted to the default.
        """
        if name in PRESET_ROLES:
            raise ValueError(f"Cannot delete preset role '{name}'")
        if name not in self._roles:
            raise ValueError(f"Unknown role: {name}")

        # Revert affected ministers
        affected = [m for m, r in self._assignments.items() if r == name]
        for m in affected:
            self._assignments.pop(m, None)

        del self._roles[name]
        logger.info("[RBAC] Deleted role '%s' (%d ministers reverted)",
                     name, len(affected))

    # ── Queries ─────────────────────────────────────────────────

    def list_roles(self) -> list[dict]:
        """Return all roles as JSON-safe dicts."""
        return [r.to_dict() for r in self._roles.values()]

    def list_assignments(self) -> dict[str, str]:
        """Return a copy of the minister → role assignment map."""
        return dict(self._assignments)

    def get_role_detail(self, role_name: str) -> dict:
        """Return a single role's detail, or raise ValueError."""
        role = self._roles.get(role_name)
        if role is None:
            raise ValueError(f"Unknown role: {role_name}")
        return role.to_dict()

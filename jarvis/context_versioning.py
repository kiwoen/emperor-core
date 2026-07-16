"""
Context Versioning & Rollback — immutable system-state snapshots with diff & restore.

Gives Jarvis the ability to:
  1. Take full-state snapshots at any point (plugins, templates, config, memory, router)
  2. Compute human-readable diffs between any two versions
  3. Rollback individual components or the entire system to a previous state
  4. Auto-snapshot before destructive operations (uninstall, reset, bulk delete)

Design principles:
  - Immutable snapshots — once written, never modified
  - Component-granular rollback — pick exactly what to restore
  - Dry-run first — always preview changes before applying
  - Zero external dependencies — pure stdlib + existing Jarvis modules
  - Human-readable diffs — plain text summaries of what changed
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("jarvis.context_versioning")

# ------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------

SNAPSHOT_DIR_NAME = "versions"
SNAPSHOT_EXT = ".snapshot.json"
MAX_SNAPSHOTS_DEFAULT = 50
AUTO_SNAPSHOT_PREFIX = "auto"

# ------------------------------------------------------------------
# Data Structures
# ------------------------------------------------------------------


@dataclass
class ComponentState:
    """Snapshot of a single component's state."""

    name: str  # "plugins" | "templates" | "config" | "memory" | "router"
    data: dict[str, Any] = field(default_factory=dict)
    checksum: str = ""  # SHA256 of serialized data


@dataclass
class Snapshot:
    """A complete system-state snapshot at a point in time.

    Attributes:
        id:             Unique snapshot identifier (timestamp-based + random)
        timestamp:      Unix timestamp of when it was created
        description:    Human-readable label (auto or user-provided)
        components:     Dict of component_name → ComponentState
        metadata:       Extra info (version, trigger, etc.)
    """

    id: str
    timestamp: float
    description: str
    components: dict[str, ComponentState] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DiffResult:
    """Human-readable diff between two snapshots.

    Attributes:
        component:      Component name
        snapshot_a_id:  Source snapshot id
        snapshot_b_id:  Target snapshot id
        changes:        List of human-readable change descriptions
        added_keys:     Keys present in B but not in A
        removed_keys:   Keys present in A but not in B
        changed_keys:   Keys with different values
    """

    component: str
    snapshot_a_id: str
    snapshot_b_id: str
    changes: list[str] = field(default_factory=list)
    added_keys: list[str] = field(default_factory=list)
    removed_keys: list[str] = field(default_factory=list)
    changed_keys: list[str] = field(default_factory=list)


@dataclass
class RollbackPreview:
    """Dry-run preview of a rollback operation.

    Attributes:
        snapshot_id:    Target snapshot to rollback to
        components:     Which components will be restored
        per_component:  Dict of component_name → DiffResult
        summary:        One-line summary
    """

    snapshot_id: str
    components: list[str]
    per_component: dict[str, DiffResult] = field(default_factory=dict)
    summary: str = ""


# ------------------------------------------------------------------
# ContextVersioning
# ------------------------------------------------------------------


class ContextVersioning:
    """Central versioning engine for Jarvis system state.

    Usage:
        cv = ContextVersioning(data_dir="./data")

        # Snapshot current state
        snap = cv.snapshot(description="Before plugin install")

        # List all versions
        versions = cv.list_snapshots()

        # Diff two versions
        diff = cv.diff(snap_a_id, snap_b_id)

        # Dry-run rollback
        preview = cv.preview_rollback(snap_id, components=["plugins"])

        # Execute rollback
        if preview.summary:
            cv.rollback(snap_id, components=["plugins"])

        # Cleanup old snapshots
        cv.prune_old(keep=30)
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(
        self,
        data_dir: str | Path,
        max_snapshots: int = MAX_SNAPSHOTS_DEFAULT,
    ) -> None:
        """Initialize the versioning engine.

        Args:
            data_dir:       Jarvis data directory (snapshots stored in data_dir/versions/)
            max_snapshots:  Maximum number of snapshots to retain
        """
        self._data_dir = Path(data_dir)
        self._snapshot_dir = self._data_dir / SNAPSHOT_DIR_NAME
        self._snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.max_snapshots = max_snapshots

        # State providers: callables that return ComponentState for each component
        self._state_providers: dict[str, Any] = {}

        # Rollback handlers: callables that receive ComponentState and restore it
        self._rollback_handlers: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # State Provider Registration
    # ------------------------------------------------------------------

    def register_component(
        self,
        name: str,
        state_provider: Any,
        rollback_handler: Any,
    ) -> None:
        """Register a component for snapshot/rollback support.

        Args:
            name:              Component name (e.g. "plugins", "templates")
            state_provider:    Callable() → ComponentState
            rollback_handler:  Callable(ComponentState) → bool (success)
        """
        self._state_providers[name] = state_provider
        self._rollback_handlers[name] = rollback_handler
        logger.info("Registered component '%s' for versioning", name)

    def unregister_component(self, name: str) -> None:
        """Remove a component from versioning."""
        self._state_providers.pop(name, None)
        self._rollback_handlers.pop(name, None)

    @property
    def registered_components(self) -> list[str]:
        """List of registered component names."""
        return list(self._state_providers.keys())

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot(
        self,
        description: str = "",
        components: Optional[list[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Snapshot:
        """Capture a full-state snapshot of registered components.

        Args:
            description:  Human-readable label (e.g. "Before plugin uninstall")
            components:   Which components to snapshot (None = all registered)
            metadata:     Extra key-value metadata

        Returns:
            The created Snapshot
        """
        now = time.time()
        snap_id = self._make_snapshot_id(now)

        target_components = components or list(self._state_providers.keys())
        snap_components: dict[str, ComponentState] = {}

        for comp_name in target_components:
            if comp_name not in self._state_providers:
                logger.warning("Component '%s' not registered, skipping", comp_name)
                continue
            try:
                state = self._state_providers[comp_name]()
                if state is not None:
                    state.checksum = self._hash_state(state.data)
                    snap_components[comp_name] = state
            except Exception as exc:
                logger.error("Failed to snapshot component '%s': %s", comp_name, exc)

        snap = Snapshot(
            id=snap_id,
            timestamp=now,
            description=description or f"Snapshot at {datetime.fromtimestamp(now, tz=timezone.utc).isoformat()}",
            components=snap_components,
            metadata=metadata or {},
        )

        self._write_snapshot(snap)
        self._prune_if_needed()

        logger.info(
            "Snapshot %s: %d components captured%s",
            snap_id, len(snap_components),
            f" — {description}" if description else "",
        )
        return snap

    def auto_snapshot(self, trigger: str = "") -> Optional[Snapshot]:
        """Take an automatic snapshot (convenience wrapper).

        Args:
            trigger: What triggered this snapshot (e.g. "plugin_install")

        Returns:
            The created Snapshot, or None if no components registered
        """
        if not self._state_providers:
            return None
        desc = f"{AUTO_SNAPSHOT_PREFIX}: {trigger}" if trigger else "auto-snapshot"
        return self.snapshot(
            description=desc,
            metadata={"auto": True, "trigger": trigger},
        )

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def list_snapshots(
        self,
        reverse: bool = True,
        limit: int = 20,
    ) -> list[Snapshot]:
        """List snapshots, newest first by default.

        Args:
            reverse:  True = newest first, False = oldest first
            limit:    Max number to return

        Returns:
            Sorted list of Snapshot objects
        """
        snapshots: list[Snapshot] = []
        for file_path in sorted(self._snapshot_dir.glob(f"*{SNAPSHOT_EXT}")):
            try:
                snap = self._load_snapshot(file_path)
                snapshots.append(snap)
            except Exception as exc:
                logger.warning("Failed to load snapshot %s: %s", file_path.name, exc)

        snapshots.sort(key=lambda s: s.timestamp, reverse=reverse)
        return snapshots[:limit]

    def get_snapshot(self, snapshot_id: str) -> Optional[Snapshot]:
        """Get a specific snapshot by id."""
        file_path = self._snapshot_dir / f"{snapshot_id}{SNAPSHOT_EXT}"
        if not file_path.exists():
            # Try fuzzy match
            for p in self._snapshot_dir.glob(f"{snapshot_id}*{SNAPSHOT_EXT}"):
                return self._load_snapshot(p)
            return None
        return self._load_snapshot(file_path)

    @property
    def count(self) -> int:
        """Total number of stored snapshots."""
        return len(list(self._snapshot_dir.glob(f"*{SNAPSHOT_EXT}")))

    # ------------------------------------------------------------------
    # Diff
    # ------------------------------------------------------------------

    def diff(
        self,
        snapshot_a_id: str,
        snapshot_b_id: str,
        components: Optional[list[str]] = None,
    ) -> dict[str, DiffResult]:
        """Compute diffs between two snapshots.

        Args:
            snapshot_a_id:  Source snapshot id (the "before")
            snapshot_b_id:  Target snapshot id (the "after")
            components:     Which components to diff (None = all common)

        Returns:
            Dict of component_name → DiffResult
        """
        snap_a = self.get_snapshot(snapshot_a_id)
        snap_b = self.get_snapshot(snapshot_b_id)

        if not snap_a:
            raise ValueError(f"Snapshot not found: {snapshot_a_id}")
        if not snap_b:
            raise ValueError(f"Snapshot not found: {snapshot_b_id}")

        common = set(snap_a.components.keys()) & set(snap_b.components.keys())
        if components:
            common = common & set(components)

        results: dict[str, DiffResult] = {}
        for comp_name in sorted(common):
            state_a = snap_a.components[comp_name]
            state_b = snap_b.components[comp_name]
            result = self._compute_component_diff(
                comp_name, snapshot_a_id, snapshot_b_id, state_a.data, state_b.data
            )
            results[comp_name] = result

        return results

    def diff_latest(self) -> dict[str, DiffResult]:
        """Diff between the two most recent snapshots."""
        snapshots = self.list_snapshots(limit=2)
        if len(snapshots) < 2:
            return {}
        return self.diff(snapshots[1].id, snapshots[0].id)

    # ------------------------------------------------------------------
    # Rollback
    # ------------------------------------------------------------------

    def preview_rollback(
        self,
        snapshot_id: str,
        components: Optional[list[str]] = None,
    ) -> RollbackPreview:
        """Dry-run a rollback: show what would change without applying.

        Args:
            snapshot_id:  Target snapshot to rollback to
            components:   Which components to restore (None = all)

        Returns:
            RollbackPreview with per-component diffs
        """
        snap = self.get_snapshot(snapshot_id)
        if not snap:
            raise ValueError(f"Snapshot not found: {snapshot_id}")

        target_components = components or list(snap.components.keys())

        # Build a "current state" pseudo-snapshot on the fly
        current_components: dict[str, ComponentState] = {}
        for comp_name in target_components:
            if comp_name in self._state_providers:
                try:
                    current_components[comp_name] = self._state_providers[comp_name]()
                except Exception:
                    pass

        current_snap = Snapshot(
            id="__current__",
            timestamp=time.time(),
            description="Current state",
            components=current_components,
        )

        per_component: dict[str, DiffResult] = {}
        total_changes = 0
        for comp_name in target_components:
            if comp_name not in snap.components:
                continue
            if comp_name not in current_components:
                continue

            result = self._compute_component_diff(
                comp_name, snapshot_id, "__current__",
                snap.components[comp_name].data,
                current_components[comp_name].data,
            )
            per_component[comp_name] = result
            total_changes += len(result.added_keys) + len(result.removed_keys) + len(result.changed_keys)

        if total_changes == 0:
            summary = "No changes detected — current state matches snapshot."
        else:
            summary = (
                f"Rollback to snapshot {snapshot_id[:12]}… "
                f"would affect {total_changes} key(s) across {len(per_component)} component(s)."
            )

        return RollbackPreview(
            snapshot_id=snapshot_id,
            components=target_components,
            per_component=per_component,
            summary=summary,
        )

    def rollback(
        self,
        snapshot_id: str,
        components: Optional[list[str]] = None,
        dry_run: bool = False,
    ) -> dict[str, bool]:
        """Execute rollback: restore component states from a snapshot.

        Args:
            snapshot_id:  Target snapshot to restore from
            components:   Which components to restore (None = all available)
            dry_run:      If True, only preview, don't apply

        Returns:
            Dict of component_name → success (bool)
        """
        snap = self.get_snapshot(snapshot_id)
        if not snap:
            raise ValueError(f"Snapshot not found: {snapshot_id}")

        target_components = components or list(snap.components.keys())

        if dry_run:
            preview = self.preview_rollback(snapshot_id, target_components)
            return {c: False for c in preview.per_component}

        # Auto-snapshot before rollback (safety net)
        self.auto_snapshot(trigger=f"pre-rollback-to-{snapshot_id[:12]}")

        results: dict[str, bool] = {}
        for comp_name in target_components:
            if comp_name not in snap.components:
                logger.warning("Component '%s' not in snapshot, skipping", comp_name)
                results[comp_name] = False
                continue
            if comp_name not in self._rollback_handlers:
                logger.warning("No rollback handler for '%s', skipping", comp_name)
                results[comp_name] = False
                continue

            try:
                success = self._rollback_handlers[comp_name](snap.components[comp_name])
                results[comp_name] = bool(success)
                logger.info(
                    "Rollback '%s' → %s: %s",
                    comp_name, snapshot_id[:12], "OK" if success else "FAILED",
                )
            except Exception as exc:
                logger.error("Rollback '%s' failed: %s", comp_name, exc)
                results[comp_name] = False

        return results

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def delete_snapshot(self, snapshot_id: str) -> bool:
        """Delete a specific snapshot by id."""
        file_path = self._snapshot_dir / f"{snapshot_id}{SNAPSHOT_EXT}"
        if file_path.exists():
            file_path.unlink()
            logger.info("Deleted snapshot %s", snapshot_id)
            return True
        return False

    def prune_old(self, keep: int = MAX_SNAPSHOTS_DEFAULT) -> int:
        """Remove oldest snapshots beyond the retention limit.

        Args:
            keep: Number of most recent snapshots to keep

        Returns:
            Count of deleted snapshots
        """
        snapshots = self.list_snapshots(reverse=True)
        if len(snapshots) <= keep:
            return 0

        removed = 0
        for snap in snapshots[keep:]:
            if self.delete_snapshot(snap.id):
                removed += 1

        if removed:
            logger.info("Pruned %d old snapshots (keeping %d)", removed, keep)
        return removed

    # ------------------------------------------------------------------
    # Internal: File I/O
    # ------------------------------------------------------------------

    def _write_snapshot(self, snap: Snapshot) -> None:
        """Serialize and write a snapshot to disk."""
        file_path = self._snapshot_dir / f"{snap.id}{SNAPSHOT_EXT}"

        data: dict[str, Any] = {
            "id": snap.id,
            "timestamp": snap.timestamp,
            "description": snap.description,
            "components": {
                name: {
                    "name": comp.name,
                    "data": comp.data,
                    "checksum": comp.checksum,
                }
                for name, comp in snap.components.items()
            },
            "metadata": snap.metadata,
            "format_version": 1,
        }

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    def _load_snapshot(self, file_path: Path) -> Snapshot:
        """Deserialize a snapshot from disk."""
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        components: dict[str, ComponentState] = {}
        for name, comp_data in data.get("components", {}).items():
            components[name] = ComponentState(
                name=comp_data["name"],
                data=comp_data.get("data", {}),
                checksum=comp_data.get("checksum", ""),
            )

        return Snapshot(
            id=data["id"],
            timestamp=data["timestamp"],
            description=data.get("description", ""),
            components=components,
            metadata=data.get("metadata", {}),
        )

    def _prune_if_needed(self) -> None:
        """Auto-prune if over the max limit."""
        self.prune_old(keep=self.max_snapshots)

    # ------------------------------------------------------------------
    # Internal: Diff Engine
    # ------------------------------------------------------------------

    def _compute_component_diff(
        self,
        component: str,
        snap_a_id: str,
        snap_b_id: str,
        data_a: dict[str, Any],
        data_b: dict[str, Any],
    ) -> DiffResult:
        """Compute a human-readable diff between two component states."""
        keys_a = set(data_a.keys())
        keys_b = set(data_b.keys())

        added = sorted(keys_b - keys_a)
        removed = sorted(keys_a - keys_b)
        common_keys = sorted(keys_a & keys_b)

        changed = []
        changes = []

        for key in common_keys:
            val_a = data_a[key]
            val_b = data_b[key]

            if isinstance(val_a, (dict, list)) and isinstance(val_b, (dict, list)):
                # Structural comparison
                if json.dumps(val_a, sort_keys=True, default=str) != json.dumps(val_b, sort_keys=True, default=str):
                    changed.append(key)
                    changes.append(self._describe_change(key, val_a, val_b))
            elif val_a != val_b:
                changed.append(key)
                changes.append(self._describe_change(key, val_a, val_b))

        for key in added:
            changes.append(f"[+] ADDED: {key} = {self._truncate(str(data_b[key]), 80)}")
        for key in removed:
            changes.append(f"[-] REMOVED: {key} (was: {self._truncate(str(data_a[key]), 80)})")

        return DiffResult(
            component=component,
            snapshot_a_id=snap_a_id,
            snapshot_b_id=snap_b_id,
            changes=changes,
            added_keys=added,
            removed_keys=removed,
            changed_keys=changed,
        )

    @staticmethod
    def _describe_change(key: str, old_val: Any, new_val: Any) -> str:
        """Generate a human-readable change description."""
        old_str = ContextVersioning._truncate(str(old_val), 60)
        new_str = ContextVersioning._truncate(str(new_val), 60)

        if isinstance(old_val, bool) and isinstance(new_val, bool):
            arrow = "OFF → ON" if new_val else "ON → OFF"
            return f"[~] {key}: {arrow}"

        if isinstance(old_val, (int, float)) and isinstance(new_val, (int, float)):
            delta = new_val - old_val
            sign = "+" if delta > 0 else ""
            return f"[~] {key}: {old_str} → {new_str} ({sign}{delta})"

        return f"[~] {key}: {old_str} → {new_str}"

    @staticmethod
    def _truncate(text: str, max_len: int) -> str:
        """Truncate text to max_len with ellipsis."""
        if len(text) <= max_len:
            return text
        return text[: max_len - 3] + "..."

    # ------------------------------------------------------------------
    # Internal: Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _make_snapshot_id(timestamp: float) -> str:
        """Generate a unique snapshot id."""
        import hashlib
        import os
        raw = f"{timestamp:.6f}-{os.urandom(4).hex()}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    @staticmethod
    def _hash_state(data: dict[str, Any]) -> str:
        """Compute SHA256 checksum of state data."""
        import hashlib
        serialized = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode()).hexdigest()[:16]


# ------------------------------------------------------------------
# Convenience: integration helpers
# ------------------------------------------------------------------


def create_plugin_state_provider(plugin_marketplace: Any):
    """Factory: create a state provider for the plugin marketplace.

    Returns a callable that captures:
      - installed plugins (id, name, version, enabled, config)
    """

    def _capture_plugins() -> ComponentState:
        installed = {}
        for p in getattr(plugin_marketplace, "list_plugins", lambda: [])():
            installed[p.get("id", "?")] = {
                "name": p.get("name", "?"),
                "version": p.get("version", "?"),
                "enabled": p.get("enabled", False),
                "config": p.get("config", {}),
            }
        return ComponentState(name="plugins", data=installed)

    return _capture_plugins


def create_plugin_rollback_handler(plugin_marketplace: Any):
    """Factory: create a rollback handler for the plugin marketplace."""

    def _restore_plugins(state: ComponentState) -> bool:
        try:
            for plugin_id, plugin_data in state.data.items():
                # Restore enabled state
                if plugin_data.get("enabled"):
                    plugin_marketplace.enable(plugin_id)
                else:
                    plugin_marketplace.disable(plugin_id)
                # Restore config if supported
                if hasattr(plugin_marketplace, "configure"):
                    plugin_marketplace.configure(plugin_id, plugin_data.get("config", {}))
            return True
        except Exception:
            return False

    return _restore_plugins


def create_template_state_provider(template_engine: Any):
    """Factory: create a state provider for the prompt template system."""

    def _capture_templates() -> ComponentState:
        templates = {}
        for name in getattr(template_engine, "list_templates", lambda: [])():
            info = getattr(template_engine, "get_template", lambda n: {})(name)
            if info:
                templates[name] = {
                    "capability": info.get("capability", "?"),
                    "version": info.get("version", 1),
                    "template": info.get("template", ""),
                    "variables": info.get("variables", []),
                }
        return ComponentState(name="templates", data=templates)

    return _capture_templates


def create_template_rollback_handler(template_engine: Any):
    """Factory: create a rollback handler for the prompt template system."""

    def _restore_templates(state: ComponentState) -> bool:
        try:
            for name, tmpl_data in state.data.items():
                if hasattr(template_engine, "register"):
                    template_engine.register(
                        name=name,
                        capability=tmpl_data.get("capability", ""),
                        template=tmpl_data.get("template", ""),
                        variables=tmpl_data.get("variables", []),
                        version=tmpl_data.get("version", 1),
                    )
            return True
        except Exception:
            return False

    return _restore_templates

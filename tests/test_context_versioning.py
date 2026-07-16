"""Tests for jarvis/context_versioning.py — immutable state snapshots with diff & rollback."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

import pytest

from jarvis.context_versioning import (
    ComponentState,
    ContextVersioning,
    DiffResult,
    Snapshot,
    create_plugin_state_provider,
    create_plugin_rollback_handler,
    create_template_state_provider,
    create_template_rollback_handler,
)


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def tmp_versioning_dir():
    """Create a temporary directory for versioning snapshots."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def cv(tmp_versioning_dir):
    """Create a fresh ContextVersioning instance."""
    return ContextVersioning(data_dir=tmp_versioning_dir, max_snapshots=20)


@pytest.fixture
def cv_with_components(tmp_versioning_dir):
    """Versioning engine with mock components registered."""

    state = {"plugins_installed": 0, "templates_count": 0}

    def _capture_plugins() -> ComponentState:
        return ComponentState(
            name="plugins",
            data={"installed": state["plugins_installed"], "enabled_ids": ["a", "b"]},
        )

    def _capture_templates() -> ComponentState:
        return ComponentState(
            name="templates",
            data={"count": state["templates_count"], "active": "default"},
        )

    def _restore_plugins(cs: ComponentState) -> bool:
        state["plugins_installed"] = cs.data.get("installed", 0)
        return True

    def _restore_templates(cs: ComponentState) -> bool:
        state["templates_count"] = cs.data.get("count", 0)
        return True

    cv = ContextVersioning(data_dir=tmp_versioning_dir)
    cv.register_component("plugins", _capture_plugins, _restore_plugins)
    cv.register_component("templates", _capture_templates, _restore_templates)

    return cv, state


# ------------------------------------------------------------------
# Basic Snapshot
# ------------------------------------------------------------------


class TestSnapshot:
    """Basic snapshot creation and retrieval."""

    def test_snapshot_no_components(self, cv):
        """Snapshot with no registered components should produce empty snapshot."""
        snap = cv.snapshot(description="empty test")
        assert snap.description == "empty test"
        assert len(snap.components) == 0
        assert snap.id

    def test_snapshot_create_and_retrieve(self, cv_with_components):
        """Create snapshot and retrieve it by id."""
        cv, state = cv_with_components
        snap = cv.snapshot(description="baseline")
        assert len(snap.components) == 2
        assert "plugins" in snap.components
        assert "templates" in snap.components

        # Retrieve
        retrieved = cv.get_snapshot(snap.id)
        assert retrieved is not None
        assert retrieved.id == snap.id
        assert retrieved.description == "baseline"

    def test_snapshot_id_uniqueness(self, cv_with_components):
        """Each snapshot should have a unique id."""
        cv, state = cv_with_components
        s1 = cv.snapshot()
        time.sleep(0.01)
        s2 = cv.snapshot()
        assert s1.id != s2.id

    def test_snapshot_captures_current_state(self, cv_with_components):
        """Snapshot should capture the actual component state."""
        cv, state = cv_with_components
        state["plugins_installed"] = 5
        state["templates_count"] = 3
        snap = cv.snapshot()
        assert snap.components["plugins"].data["installed"] == 5
        assert snap.components["templates"].data["count"] == 3

    def test_snapshot_selective_components(self, cv_with_components):
        """Snapshot only specific components."""
        cv, state = cv_with_components
        snap = cv.snapshot(components=["plugins"])
        assert len(snap.components) == 1
        assert "plugins" in snap.components
        assert "templates" not in snap.components

    def test_snapshot_with_metadata(self, cv_with_components):
        """Snapshot metadata should be stored."""
        cv, state = cv_with_components
        snap = cv.snapshot(metadata={"trigger": "test", "user": "admin"})
        assert snap.metadata["trigger"] == "test"
        assert snap.metadata["user"] == "admin"


# ------------------------------------------------------------------
# Auto Snapshot
# ------------------------------------------------------------------


class TestAutoSnapshot:
    """Automatic snapshot convenience method."""

    def test_auto_snapshot(self, cv_with_components):
        """auto_snapshot should prefix description."""
        cv, state = cv_with_components
        snap = cv.auto_snapshot(trigger="plugin_install")
        assert snap is not None
        assert "auto:" in snap.description
        assert snap.metadata["auto"] is True
        assert snap.metadata["trigger"] == "plugin_install"

    def test_auto_snapshot_no_components(self, cv):
        """auto_snapshot with no registered components returns None."""
        snap = cv.auto_snapshot()
        assert snap is None


# ------------------------------------------------------------------
# Listing
# ------------------------------------------------------------------


class TestListing:
    """Snapshot listing and sorting."""

    def test_list_snapshots_newest_first(self, cv_with_components):
        """List should return newest first by default."""
        cv, state = cv_with_components
        s1 = cv.snapshot(description="first")
        time.sleep(0.02)
        s2 = cv.snapshot(description="second")
        time.sleep(0.02)
        s3 = cv.snapshot(description="third")

        snapshots = cv.list_snapshots()
        assert len(snapshots) == 3
        assert snapshots[0].id == s3.id  # newest first

    def test_list_snapshots_oldest_first(self, cv_with_components):
        """List with reverse=False returns oldest first."""
        cv, state = cv_with_components
        s1 = cv.snapshot(description="first")
        time.sleep(0.02)
        s2 = cv.snapshot(description="second")

        snapshots = cv.list_snapshots(reverse=False)
        assert snapshots[0].id == s1.id

    def test_list_with_limit(self, cv_with_components):
        """List with limit truncates results."""
        cv, state = cv_with_components
        for i in range(10):
            cv.snapshot(description=f"s{i}")
        snapshots = cv.list_snapshots(limit=5)
        assert len(snapshots) == 5

    def test_count_property(self, cv_with_components):
        """Count property returns snapshot count."""
        cv, state = cv_with_components
        assert cv.count == 0
        cv.snapshot()
        assert cv.count == 1
        cv.snapshot()
        assert cv.count == 2

    def test_get_nonexistent_snapshot(self, cv):
        """get_snapshot for nonexistent id returns None."""
        assert cv.get_snapshot("nonexistent") is None


# ------------------------------------------------------------------
# Diff
# ------------------------------------------------------------------


class TestDiff:
    """Diff computation between snapshots."""

    def test_diff_identical(self, cv_with_components):
        """Diff of two identical states should show no changes."""
        cv, state = cv_with_components
        s1 = cv.snapshot()
        s2 = cv.snapshot()
        diffs = cv.diff(s1.id, s2.id)
        for comp, d in diffs.items():
            assert d.added_keys == []
            assert d.removed_keys == []
            assert d.changed_keys == []

    def test_diff_with_changes(self, cv_with_components):
        """Diff should detect value changes."""
        cv, state = cv_with_components
        s1 = cv.snapshot()

        state["plugins_installed"] = 10
        state["templates_count"] = 5

        s2 = cv.snapshot()
        diffs = cv.diff(s1.id, s2.id)

        assert "plugins" in diffs
        assert "changed" in str(diffs["plugins"].changed_keys) or len(diffs["plugins"].changed_keys) > 0

    def test_diff_specific_components(self, cv_with_components):
        """Diff only specific components."""
        cv, state = cv_with_components
        s1 = cv.snapshot()
        state["plugins_installed"] = 99
        s2 = cv.snapshot()

        diffs = cv.diff(s1.id, s2.id, components=["plugins"])
        assert "plugins" in diffs
        assert "templates" not in diffs

    def test_diff_missing_snapshot(self, cv_with_components):
        """Diff with nonexistent snapshot raises ValueError."""
        cv, state = cv_with_components
        s1 = cv.snapshot()
        with pytest.raises(ValueError, match="not found"):
            cv.diff(s1.id, "nonexistent")

    def test_diff_latest(self, cv_with_components):
        """diff_latest compares the two most recent snapshots."""
        cv, state = cv_with_components
        cv.snapshot()
        cv.snapshot()
        diffs = cv.diff_latest()
        assert isinstance(diffs, dict)


# ------------------------------------------------------------------
# Rollback Preview
# ------------------------------------------------------------------


class TestRollbackPreview:
    """Dry-run rollback preview."""

    def test_preview_no_changes(self, cv_with_components):
        """Preview should show no changes if state matches snapshot."""
        cv, state = cv_with_components
        snap = cv.snapshot()
        preview = cv.preview_rollback(snap.id)
        assert "No changes" in preview.summary or preview.summary == ""

    def test_preview_with_changes(self, cv_with_components):
        """Preview should detect drift from current state."""
        cv, state = cv_with_components
        snap = cv.snapshot()

        state["plugins_installed"] = 42  # drift

        preview = cv.preview_rollback(snap.id)
        assert len(preview.components) >= 1
        plugins_preview = preview.per_component.get("plugins")
        if plugins_preview:
            assert len(plugins_preview.changed_keys) > 0

    def test_preview_missing_snapshot(self, cv_with_components):
        """Preview with nonexistent snapshot raises ValueError."""
        cv, state = cv_with_components
        with pytest.raises(ValueError, match="not found"):
            cv.preview_rollback("nonexistent")


# ------------------------------------------------------------------
# Rollback Execution
# ------------------------------------------------------------------


class TestRollback:
    """Rollback execution."""

    def test_rollback_restores_state(self, cv_with_components):
        """Rollback should restore component state to snapshot values."""
        cv, state = cv_with_components

        state["plugins_installed"] = 5
        snap = cv.snapshot(description="baseline")

        state["plugins_installed"] = 99  # change

        results = cv.rollback(snap.id)
        assert results["plugins"] is True
        assert state["plugins_installed"] == 5

    def test_rollback_dry_run(self, cv_with_components):
        """Dry-run rollback should not change state."""
        cv, state = cv_with_components

        state["plugins_installed"] = 5
        snap = cv.snapshot()

        state["plugins_installed"] = 99

        results = cv.rollback(snap.id, dry_run=True)
        # State should NOT change
        assert state["plugins_installed"] == 99

    def test_rollback_selective(self, cv_with_components):
        """Rollback only specified components."""
        cv, state = cv_with_components

        state["plugins_installed"] = 10
        state["templates_count"] = 10
        snap = cv.snapshot()

        state["plugins_installed"] = 99
        state["templates_count"] = 99

        results = cv.rollback(snap.id, components=["plugins"])
        assert results["plugins"] is True
        assert state["plugins_installed"] == 10
        assert state["templates_count"] == 99  # unchanged

    def test_rollback_creates_safety_snapshot(self, cv_with_components):
        """Rollback should auto-snapshot before restoring."""
        cv, state = cv_with_components
        before_count = cv.count

        state["plugins_installed"] = 5
        snap = cv.snapshot()
        state["plugins_installed"] = 99
        cv.rollback(snap.id)

        assert cv.count > before_count  # auto-snapshot added

    def test_rollback_partial_failure(self, cv_with_components):
        """Rollback should report individual component failures."""

        def _bad_restore(cs: ComponentState) -> bool:
            return False

        cv2 = ContextVersioning(data_dir=cv_with_components[0]._data_dir)
        cv2.register_component("bad", lambda: ComponentState(name="bad", data={"v": 1}), _bad_restore)
        snap = cv2.snapshot()
        results = cv2.rollback(snap.id)
        assert results["bad"] is False


# ------------------------------------------------------------------
# Maintenance
# ------------------------------------------------------------------


class TestMaintenance:
    """Snapshot maintenance operations."""

    def test_delete_snapshot(self, cv_with_components):
        """Delete a snapshot by id."""
        cv, state = cv_with_components
        snap = cv.snapshot()
        assert cv.count == 1
        assert cv.delete_snapshot(snap.id) is True
        assert cv.count == 0
        assert cv.get_snapshot(snap.id) is None

    def test_delete_nonexistent(self, cv):
        """Delete nonexistent returns False."""
        assert cv.delete_snapshot("nonexistent") is False

    def test_prune_old(self, cv_with_components):
        """Prune should keep only the most recent snapshots."""
        cv, state = cv_with_components
        # Temporarily disable auto-prune by setting max very high
        cv.max_snapshots = 999
        for i in range(10):
            cv.snapshot(description=f"s{i}")
            time.sleep(0.01)

        assert cv.count == 10
        removed = cv.prune_old(keep=5)
        assert removed == 5
        assert cv.count == 5

    def test_prune_no_excess(self, cv_with_components):
        """Prune when count is below keep limit should keep all."""
        cv, state = cv_with_components
        cv.snapshot()
        removed = cv.prune_old(keep=10)
        assert removed == 0
        assert cv.count == 1


# ------------------------------------------------------------------
# Component Registration
# ------------------------------------------------------------------


class TestRegistration:
    """Component registration and listing."""

    def test_register_component(self, cv):
        """Register a component for versioning."""

        def _provider() -> ComponentState:
            return ComponentState(name="test", data={"key": "value"})

        def _handler(cs: ComponentState) -> bool:
            return True

        cv.register_component("test-comp", _provider, _handler)
        assert "test-comp" in cv.registered_components

    def test_unregister_component(self, cv):
        """Unregister a component."""

        def _provider() -> ComponentState:
            return ComponentState(name="test", data={})

        def _handler(cs: ComponentState) -> bool:
            return True

        cv.register_component("temp", _provider, _handler)
        assert "temp" in cv.registered_components
        cv.unregister_component("temp")
        assert "temp" not in cv.registered_components

    def test_registered_components_empty_initially(self, cv):
        """No components registered initially."""
        assert cv.registered_components == []


# ------------------------------------------------------------------
# Edge Cases
# ------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases and error handling."""

    def test_snapshot_failing_provider(self, tmp_versioning_dir):
        """Snapshot should skip components whose provider raises."""

        def _bad_provider():
            raise RuntimeError("provider crash")

        cv = ContextVersioning(data_dir=tmp_versioning_dir)
        cv.register_component("crash", _bad_provider, lambda cs: True)
        snap = cv.snapshot()
        # Should not crash; just skip the failing component
        assert snap is not None
        assert "crash" not in snap.components

    def test_snapshot_none_provider(self, tmp_versioning_dir):
        """Snapshot should skip components whose provider returns None."""
        cv = ContextVersioning(data_dir=tmp_versioning_dir)
        cv.register_component("none", lambda: None, lambda cs: True)
        snap = cv.snapshot()
        assert "none" not in snap.components

    def test_snapshot_persistence(self, tmp_versioning_dir):
        """Snapshots should survive engine recreation."""
        cv1 = ContextVersioning(data_dir=tmp_versioning_dir)

        def _provider() -> ComponentState:
            return ComponentState(name="test", data={"v": 1})

        cv1.register_component("test", _provider, lambda cs: True)
        snap = cv1.snapshot(description="persist")

        # Recreate engine from same directory
        cv2 = ContextVersioning(data_dir=tmp_versioning_dir)
        assert cv2.count == 1
        loaded = cv2.get_snapshot(snap.id)
        assert loaded is not None
        assert loaded.description == "persist"

    def test_fuzzy_get_snapshot(self, tmp_versioning_dir):
        """get_snapshot with partial id should fuzzy-match."""
        cv = ContextVersioning(data_dir=tmp_versioning_dir)

        def _provider() -> ComponentState:
            return ComponentState(name="test", data={})

        cv.register_component("test", _provider, lambda cs: True)
        snap = cv.snapshot()

        # Query with just the first 6 chars
        partial = snap.id[:6]
        found = cv.get_snapshot(partial)
        assert found is not None
        assert found.id == snap.id


# ------------------------------------------------------------------
# Factory Helper Tests
# ------------------------------------------------------------------


class TestFactoryHelpers:
    """Test the plugin/template state provider and rollback handler factories."""

    class MockPluginMarketplace:
        def __init__(self):
            self._plugins = {
                "p1": {"id": "p1", "name": "Plugin 1", "version": "1.0", "enabled": True, "config": {"k": "v"}},
                "p2": {"id": "p2", "name": "Plugin 2", "version": "2.0", "enabled": False, "config": {}},
            }

        def list_plugins(self):
            return list(self._plugins.values())

        def enable(self, plugin_id):
            if plugin_id in self._plugins:
                self._plugins[plugin_id]["enabled"] = True

        def disable(self, plugin_id):
            if plugin_id in self._plugins:
                self._plugins[plugin_id]["enabled"] = False

        def configure(self, plugin_id, config):
            if plugin_id in self._plugins:
                self._plugins[plugin_id]["config"] = config

    class MockTemplateManager:
        def __init__(self):
            self._templates = {
                "code": {
                    "capability": "code",
                    "version": 3,
                    "template": "Review this: {code}",
                    "variables": ["code"],
                }
            }

        def list_templates(self):
            return list(self._templates.keys())

        def get_template(self, name):
            return self._templates.get(name, {})

        def register(self, name, capability, template, variables, version):
            self._templates[name] = {
                "capability": capability,
                "template": template,
                "variables": variables,
                "version": version,
            }

    def test_plugin_state_provider(self):
        mp = self.MockPluginMarketplace()
        provider = create_plugin_state_provider(mp)
        cs = provider()
        assert cs.name == "plugins"
        assert "p1" in cs.data
        assert cs.data["p1"]["enabled"] is True
        assert cs.data["p2"]["enabled"] is False

    def test_plugin_rollback_handler(self):
        mp = self.MockPluginMarketplace()
        handler = create_plugin_rollback_handler(mp)

        # Disable p1, enable p2 via rollback
        state = ComponentState(
            name="plugins",
            data={
                "p1": {"name": "Plugin 1", "version": "1.0", "enabled": False, "config": {"x": 1}},
                "p2": {"name": "Plugin 2", "version": "2.0", "enabled": True, "config": {}},
            },
        )
        result = handler(state)
        assert result is True
        assert mp._plugins["p1"]["enabled"] is False

    def test_template_state_provider(self):
        tm = self.MockTemplateManager()
        provider = create_template_state_provider(tm)
        cs = provider()
        assert cs.name == "templates"
        assert "code" in cs.data
        assert cs.data["code"]["version"] == 3

    def test_template_rollback_handler(self):
        tm = self.MockTemplateManager()
        handler = create_template_rollback_handler(tm)

        state = ComponentState(
            name="templates",
            data={
                "new_cap": {
                    "capability": "new_cap",
                    "version": 1,
                    "template": "Hello {name}",
                    "variables": ["name"],
                }
            },
        )
        result = handler(state)
        assert result is True
        assert "new_cap" in tm._templates


# ------------------------------------------------------------------
# Snapshot Format Version
# ------------------------------------------------------------------


class TestSnapshotFormat:
    """Verify snapshot file format."""

    def test_snapshot_file_has_format_version(self, cv_with_components):
        """Snapshot JSON should include format_version."""
        cv, state = cv_with_components
        snap = cv.snapshot()
        file_path = Path(cv._snapshot_dir) / f"{snap.id}.snapshot.json"
        with open(file_path) as f:
            data = json.load(f)
        assert data["format_version"] == 1

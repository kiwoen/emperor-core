"""Unit tests for huanxin.plugin_system: PluginManifest, PluginBase, DemoPlugin,
PluginManager, and HookRegistry.
"""

import sys
import pytest
from pathlib import Path

# Ensure huanxin is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from huanxin.plugin_system import (
    PluginManifest,
    PluginBase,
    DemoPlugin,
    PluginManager,
    HookRegistry,
    create_plugin,
)


# ──────────────────────────────────────────────────────────────────
# Test 1: PluginManifest construction and defaults
# ──────────────────────────────────────────────────────────────────
def test_manifest_construction():
    manifest = PluginManifest(
        name="test_plugin",
        version="0.1.0",
        author="tester",
        description="A test manifest",
        hooks=["hook.a", "hook.b"],
        dependencies=["core>=1.0"],
    )
    assert manifest.name == "test_plugin"
    assert manifest.version == "0.1.0"
    assert manifest.author == "tester"
    assert manifest.description == "A test manifest"
    assert manifest.hooks == ["hook.a", "hook.b"]
    assert manifest.dependencies == ["core>=1.0"]


# ──────────────────────────────────────────────────────────────────
# Test 2: PluginManifest equality (dataclass auto-generated __eq__)
# ──────────────────────────────────────────────────────────────────
def test_manifest_equality():
    a = PluginManifest(name="p1", version="1.0", author="a", description="", hooks=[], dependencies=[])
    b = PluginManifest(name="p1", version="1.0", author="a", description="", hooks=[], dependencies=[])
    c = PluginManifest(name="p2", version="1.0", author="a", description="", hooks=[], dependencies=[])
    assert a == b
    assert a != c


# ──────────────────────────────────────────────────────────────────
# Test 3: DemoPlugin lifecycle — on_load / on_unload
# ──────────────────────────────────────────────────────────────────
def test_demo_plugin_lifecycle():
    plugin = DemoPlugin()
    assert plugin.get_manifest().name == "demo_plugin"

    # Initially not loaded
    assert plugin._loaded is False

    plugin.on_load()
    assert plugin._loaded is True

    plugin.on_unload()
    assert plugin._loaded is False


# ──────────────────────────────────────────────────────────────────
# Test 4: DemoPlugin hook — demo.hello
# ──────────────────────────────────────────────────────────────────
def test_demo_plugin_hello():
    plugin = DemoPlugin()
    plugin.on_load()
    hooks = plugin.get_hooks()

    assert "demo.hello" in hooks
    result = hooks["demo.hello"](name="Alice")
    assert "Alice" in result
    assert "called 1 times" in result

    # Second call increments count
    result2 = hooks["demo.hello"](name="Bob")
    assert "called 2 times" in result2


# ──────────────────────────────────────────────────────────────────
# Test 5: DemoPlugin hook — demo.echo
# ──────────────────────────────────────────────────────────────────
def test_demo_plugin_echo():
    plugin = DemoPlugin()
    plugin.on_load()
    hooks = plugin.get_hooks()

    result = hooks["demo.echo"](key="value", number=42)
    assert result["plugin"] == "demo_plugin"
    assert result["version"] == "1.0.0"
    assert result["call_count"] == 1
    assert result["echo"]["key"] == "value"
    assert result["echo"]["number"] == 42


# ──────────────────────────────────────────────────────────────────
# Test 6: DemoPlugin hook — demo.status
# ──────────────────────────────────────────────────────────────────
def test_demo_plugin_status():
    plugin = DemoPlugin()
    plugin.on_load()
    hooks = plugin.get_hooks()

    result = hooks["demo.status"]()
    assert result["loaded"] is True
    assert result["name"] == "demo_plugin"
    assert result["version"] == "1.0.0"
    assert "demo.hello" in result["hooks"]
    assert "call_counts" in result


# ──────────────────────────────────────────────────────────────────
# Test 7: PluginManager load via factory (create_plugin)
# ──────────────────────────────────────────────────────────────────
def test_manager_load_factory(tmp_path):
    """Load a plugin from a temporary file that exports create_plugin()."""
    plugin_file = tmp_path / "demo_loader.py"
    plugin_file.write_text("""
from huanxin.plugin_system import DemoPlugin, PluginBase

def create_plugin() -> PluginBase:
    return DemoPlugin()
""")

    mgr = PluginManager()
    instance = mgr.load_plugin(str(plugin_file))

    assert mgr.plugin_count == 1
    assert instance.get_manifest().name == "demo_plugin"

    plugins = mgr.list_plugins()
    assert len(plugins) == 1
    assert plugins[0]["name"] == "demo_plugin"
    assert plugins[0]["version"] == "1.0.0"

    mgr.unload("demo_plugin")


# ──────────────────────────────────────────────────────────────────
# Test 8: PluginManager unload
# ──────────────────────────────────────────────────────────────────
def test_manager_unload(tmp_path):
    plugin_file = tmp_path / "demo_unloader.py"
    plugin_file.write_text("""
from huanxin.plugin_system import DemoPlugin, PluginBase

def create_plugin() -> PluginBase:
    return DemoPlugin()
""")

    mgr = PluginManager()
    mgr.load_plugin(str(plugin_file))
    assert mgr.plugin_count == 1

    result = mgr.unload("demo_plugin")
    assert result is not None
    assert result.get_manifest().name == "demo_plugin"
    assert mgr.plugin_count == 0

    # Unload nonexistent returns None
    assert mgr.unload("nonexistent") is None


# ──────────────────────────────────────────────────────────────────
# Test 9: PluginManager list_plugins format (via direct insertion)
# ──────────────────────────────────────────────────────────────────
def test_manager_list_plugins_format(tmp_path):
    plugin_file = tmp_path / "demo_loader2.py"
    plugin_file.write_text("""
from huanxin.plugin_system import DemoPlugin, PluginBase

def create_plugin() -> PluginBase:
    return DemoPlugin()
""")

    mgr = PluginManager()
    mgr.load_plugin(str(plugin_file))

    plugins = mgr.list_plugins()
    assert isinstance(plugins, list)
    entry = plugins[0]
    assert "name" in entry
    assert "version" in entry
    assert "path" in entry
    assert "loaded_at" in entry
    assert "hooks" in entry
    assert entry["name"] == "demo_plugin"
    assert entry["version"] == "1.0.0"
    assert set(entry["hooks"]) == {"demo.hello", "demo.echo", "demo.status"}

    mgr.unload("demo_plugin")


# ──────────────────────────────────────────────────────────────────
# Test 10: HookRegistry register_hook and trigger_hook
# ──────────────────────────────────────────────────────────────────
def test_hook_registry():
    registry = HookRegistry()
    calls = []

    def my_hook(**kwargs):
        calls.append(kwargs)
        return f"result-{len(calls)}"

    registry.register_hook("test.hook", my_hook)
    assert "test.hook" in registry.list_hooks()

    results = registry.trigger_hook("test.hook", key1="val1", key2=123)
    assert results == ["result-1"]
    assert calls == [{"key1": "val1", "key2": 123}]

    # Register another callback on same hook
    def second_hook(**kwargs):
        return "second"

    registry.register_hook("test.hook", second_hook)
    results = registry.trigger_hook("test.hook", x=1)
    assert results == ["result-2", "second"]

    # Trigger nonexistent hook returns empty list
    assert registry.trigger_hook("nonexistent.hook") == []

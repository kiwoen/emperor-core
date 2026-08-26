"""
Plugin Extension System — third-party plugin architecture with
hot-reload, version management, and dependency isolation.

Design principles:
- PluginManifest declares identity + contract (hooks, dependencies).
- PluginBase is the abstract contract every plugin must fulfill.
- PluginManager handles discovery, loading, unloading, and hot-reload
  from arbitrary filesystem paths.
- HookRegistry is a decoupled pub/sub registry for extension points.

Thread-safety: PluginManager uses a re-entrant lock; HookRegistry
is lock-free (callbacks should be idempotent / fast).
"""

from __future__ import annotations

import importlib.util
import sys
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


# ────────────────────────────────────────────────────────────────
# Data classes
# ────────────────────────────────────────────────────────────────


@dataclass
class PluginManifest:
    """Self-describing metadata for a plugin."""

    name: str
    version: str
    author: str = "unknown"
    description: str = ""
    hooks: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)


# ────────────────────────────────────────────────────────────────
# Plugin Base
# ────────────────────────────────────────────────────────────────


class PluginBase(ABC):
    """Abstract contract for a loadable plugin.

    Subclasses must implement all abstract methods and provide a
    class-level or instance-level ``manifest`` attribute of type
    ``PluginManifest``.
    """

    manifest: PluginManifest

    @abstractmethod
    def on_load(self) -> None:
        """Called once when the plugin is loaded into the system."""
        ...

    @abstractmethod
    def on_unload(self) -> None:
        """Called once when the plugin is unloaded / hot-replaced."""
        ...

    @abstractmethod
    def get_hooks(self) -> Dict[str, Callable]:
        """Return a mapping of hook-name → callable.

        The manager will register each entry into the HookRegistry
        automatically after :meth:`on_load` succeeds.
        """
        ...

    @abstractmethod
    def get_manifest(self) -> PluginManifest:
        """Return the manifest for this plugin."""
        ...


# ────────────────────────────────────────────────────────────────
# Hook Registry  (decoupled pub/sub)
# ────────────────────────────────────────────────────────────────


class HookRegistry:
    """Thread-safe registry of named hooks.

    Hooks are simple string-keyed callbacks.  Multiple plugins may
    register under the same hook name — each callable is invoked in
    registration order.
    """

    def __init__(self) -> None:
        self._hooks: Dict[str, List[Callable]] = {}
        self._lock = threading.RLock()

    # ── registration ──────────────────────────────────────────

    def register_hook(self, name: str, callback: Callable[..., Any]) -> None:
        """Register *callback* under *name*."""
        with self._lock:
            if name not in self._hooks:
                self._hooks[name] = []
            self._hooks[name].append(callback)

    def unregister_hook(self, name: str) -> int:
        """Remove **all** callbacks registered under *name*.

        Returns the number of callbacks removed.
        """
        with self._lock:
            removed = len(self._hooks.pop(name, []))
        return removed

    # ── trigger ────────────────────────────────────────────────

    def trigger_hook(self, name: str, **kwargs: Any) -> List[Any]:
        """Invoke every callback registered for *name*, passing
        ``**kwargs``.  Returns a list of return values (or ``None``
        entries for callbacks that raise).

        Exceptions are caught and logged to stderr so that one
        misbehaving callback does not break the chain.
        """
        with self._lock:
            callbacks = list(self._hooks.get(name, []))
        results: List[Any] = []
        for cb in callbacks:
            try:
                results.append(cb(**kwargs))
            except Exception:
                import traceback
                traceback.print_exc()
                results.append(None)
        return results

    # ── introspection ─────────────────────────────────────────

    def list_hooks(self) -> Dict[str, int]:
        """Return ``{hook_name: callback_count}``."""
        with self._lock:
            return {k: len(v) for k, v in self._hooks.items()}


# ────────────────────────────────────────────────────────────────
# Plugin Manager
# ────────────────────────────────────────────────────────────────


class PluginManager:
    """Load, unload, hot-reload and query third-party plugins.

    Each plugin lives in a *single Python file* that exposes a
    top-level ``create_plugin()`` factory.  The factory must return an
    instance of :class:`PluginBase`.
    """

    def __init__(self, registry: Optional[HookRegistry] = None) -> None:
        self._plugins: Dict[str, PluginBase] = {}       # name → instance
        self._paths: Dict[str, Path] = {}               # name → absolute path
        self._load_times: Dict[str, float] = {}         # name → epoch
        self._versions: Dict[str, str] = {}             # name → version
        self._lock = threading.RLock()
        self.registry = registry or HookRegistry()

    # ── load ──────────────────────────────────────────────────

    def load_plugin(self, path: str | Path) -> PluginBase:
        """Load a plugin from *path* (Python file containing a
        ``create_plugin()`` factory).

        Raises ``ValueError`` on duplicate name, ``RuntimeError`` on
        import / instantiation failure.
        """
        file_path = Path(path).resolve()
        if not file_path.is_file():
            raise ValueError(f"Plugin file not found: {file_path}")

        # Import the module dynamically
        module_name = f"_plugin_{file_path.stem}_{int(time.time() * 1_000_000)}"
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot create module spec for: {file_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            del sys.modules[module_name]
            raise RuntimeError(f"Failed to load plugin '{file_path}': {exc}") from exc

        if not hasattr(module, "create_plugin"):
            del sys.modules[module_name]
            raise RuntimeError(
                f"Plugin '{file_path}' does not expose 'create_plugin' factory"
            )

        try:
            instance: PluginBase = module.create_plugin()
        except Exception as exc:
            del sys.modules[module_name]
            raise RuntimeError(
                f"Plugin factory 'create_plugin' failed for '{file_path}': {exc}"
            ) from exc

        if not isinstance(instance, PluginBase):
            del sys.modules[module_name]
            raise RuntimeError(
                f"'create_plugin' must return a PluginBase instance, got {type(instance)}"
            )

        manifest = instance.get_manifest()
        name = manifest.name

        with self._lock:
            if name in self._plugins:
                if sys.modules.get(module_name):
                    del sys.modules[module_name]
                raise ValueError(f"Plugin '{name}' is already loaded; unload first")

            # Call on_load
            instance.on_load()

            # Register hooks
            for hook_name, cb in instance.get_hooks().items():
                self.registry.register_hook(hook_name, cb)

            # Bookkeeping
            now = time.time()
            self._plugins[name] = instance
            self._paths[name] = file_path
            self._load_times[name] = now
            self._versions[name] = manifest.version

        return instance

    # ── unload ────────────────────────────────────────────────

    def unload(self, name: str) -> PluginBase | None:
        """Unload the plugin *name*, calling ``on_unload`` and
        removing its hooks from the registry.  Returns the removed
        instance or ``None``."""
        with self._lock:
            instance = self._plugins.pop(name, None)
            if instance is None:
                return None

            file_path = self._paths.pop(name, None)
            self._load_times.pop(name, None)
            self._versions.pop(name, None)

        # Unregister hooks (outside lock to avoid deadlock with HookRegistry)
        for hook_name in instance.get_manifest().hooks:
            self.registry.unregister_hook(hook_name)

        # Call on_unload
        try:
            instance.on_unload()
        except Exception:
            pass

        # Clean up sys.modules entries for this file path
        if file_path is not None:
            to_remove = [
                m for m, mod in sys.modules.items()
                if getattr(mod, "__file__", "") == str(file_path)
            ]
            for m in to_remove:
                del sys.modules[m]

        return instance

    # ── reload (hot-reload) ───────────────────────────────────

    def reload(self, name: str) -> PluginBase:
        """Hot-reload the plugin *name*: unload → re-load from the
        same path.  Raises ``KeyError`` if *name* is unknown."""
        with self._lock:
            path = self._paths.get(name)

        if path is None:
            raise KeyError(f"Plugin '{name}' is not loaded; cannot reload")

        self.unload(name)
        return self.load_plugin(path)

    # ── query ─────────────────────────────────────────────────

    def get_plugin(self, name: str) -> PluginBase | None:
        """Return the :class:`PluginBase` instance for *name* or ``None``."""
        with self._lock:
            return self._plugins.get(name)

    def list_plugins(self) -> List[Dict[str, Any]]:
        """Return metadata for every loaded plugin."""
        with self._lock:
            return [
                {
                    "name": name,
                    "version": self._versions.get(name, "?"),
                    "path": str(self._paths.get(name, "")),
                    "loaded_at": self._load_times.get(name, 0),
                    "hooks": instance.get_manifest().hooks,
                }
                for name, instance in self._plugins.items()
            ]

    # ── auto-discover ─────────────────────────────────────────

    def discover(self, directory: str | Path) -> int:
        """Auto-load all ``*.py`` files (excluding ``__init__.py``)
        from *directory*.  Returns the number of plugins loaded."""
        dir_path = Path(directory).resolve()
        if not dir_path.is_dir():
            return 0

        count = 0
        for py_file in sorted(dir_path.glob("*.py")):
            if py_file.name.startswith("_"):    # skip __init__.py etc.
                continue
            try:
                self.load_plugin(py_file)
                count += 1
            except Exception:
                import traceback
                traceback.print_exc()
        return count

    @property
    def plugin_count(self) -> int:
        with self._lock:
            return len(self._plugins)


# ────────────────────────────────────────────────────────────────
# Built-in Demo Plugin
# ────────────────────────────────────────────────────────────────


class DemoPlugin(PluginBase):
    """Built-in demo plugin for testing and reference.

    Demonstrates the minimal contract: manifest, on_load, on_unload,
    get_hooks, and get_manifest.
    """

    manifest = PluginManifest(
        name="demo_plugin",
        version="1.0.0",
        author="HuanxinCore",
        description="Built-in demo plugin for Plugin System smoke testing",
        hooks=["demo.hello", "demo.echo", "demo.status"],
        dependencies=[],
    )

    def __init__(self) -> None:
        self._loaded = False
        self._call_count: Dict[str, int] = {}

    def on_load(self) -> None:
        self._loaded = True
        self._call_count = {}

    def on_unload(self) -> None:
        self._loaded = False
        self._call_count.clear()

    def get_hooks(self) -> Dict[str, Callable]:
        return {
            "demo.hello": self._hook_hello,
            "demo.echo": self._hook_echo,
            "demo.status": self._hook_status,
        }

    def get_manifest(self) -> PluginManifest:
        return self.manifest

    # ── hook implementations ───────────────────────────────────

    def _hook_hello(self, **kwargs: Any) -> str:
        name = kwargs.get("name", "World")
        self._call_count["demo.hello"] = self._call_count.get("demo.hello", 0) + 1
        return f"Hello, {name}! (called {self._call_count['demo.hello']} times)"

    def _hook_echo(self, **kwargs: Any) -> dict:
        self._call_count["demo.echo"] = self._call_count.get("demo.echo", 0) + 1
        return {
            "echo": kwargs,
            "plugin": self.manifest.name,
            "version": self.manifest.version,
            "call_count": self._call_count["demo.echo"],
        }

    def _hook_status(self, **kwargs: Any) -> dict:
        self._call_count["demo.status"] = self._call_count.get("demo.status", 0) + 1
        return {
            "loaded": self._loaded,
            "name": self.manifest.name,
            "version": self.manifest.version,
            "hooks": self.manifest.hooks,
            "call_counts": dict(self._call_count),
        }


def create_plugin() -> PluginBase:
    """Factory entry-point for the Plugin Manager."""
    return DemoPlugin()

"""Plugin system for Emperor extensibility.

Plugins are Python objects that implement lifecycle hooks. They are
discovered and managed by PluginManager, which dispatches events at
key points in the Emperor lifecycle.

Usage:
    from jarvis.plugin import Plugin, PluginManager, LifecycleEvent

    pm = PluginManager()
    pm.register(MyPlugin())
    pm.dispatch(LifecycleEvent.ON_INIT, emperor=emperor)
"""

from __future__ import annotations

import abc
import enum
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# Lifecycle events
# ══════════════════════════════════════════════════════════════════


class LifecycleEvent(enum.Enum):
    """Events dispatched during Emperor lifecycle."""

    ON_INIT = "on_init"
    ON_SHUTDOWN = "on_shutdown"

    # Minister events
    ON_MINISTER_REGISTER = "on_minister_register"
    ON_MINISTER_UNREGISTER = "on_minister_unregister"

    # Evolution events
    ON_EVOLVE_START = "on_evolve_start"
    ON_EVOLVE_END = "on_evolve_end"
    ON_CYCLE_START = "on_cycle_start"
    ON_CYCLE_END = "on_cycle_end"

    # Task events
    ON_TASK_BEFORE = "on_task_before"
    ON_TASK_AFTER = "on_task_after"
    ON_TASK_ERROR = "on_task_error"

    # Alert events
    ON_ALERT_FIRED = "on_alert_fired"
    ON_HEALING_TRIGGERED = "on_healing_triggered"

    # Health events
    ON_HEALTH_CHECK = "on_health_check"


# ══════════════════════════════════════════════════════════════════
# Plugin base class
# ══════════════════════════════════════════════════════════════════


class Plugin(abc.ABC):
    """Base class for Emperor plugins.

    Subclass this and override the hooks you need. Each hook receives
    ``**kwargs`` with event‑specific keyword arguments; see the
    LifecycleEvent docstring for the expected kwargs per event.
    """

    @property
    def name(self) -> str:
        """Human‑readable plugin name (default: class name)."""
        return self.__class__.__name__

    @property
    def version(self) -> str:
        """Plugin version string."""
        return "0.1.0"

    def on_init(self, **kwargs: Any) -> None:
        """Called when Emperor is initialized."""
        pass

    def on_shutdown(self, **kwargs: Any) -> None:
        """Called when Emperor is shutting down."""
        pass

    def on_minister_register(self, **kwargs: Any) -> None:
        """Called after a minister is registered."""
        pass

    def on_minister_unregister(self, **kwargs: Any) -> None:
        """Called after a minister is unregistered."""
        pass

    def on_evolve_start(self, **kwargs: Any) -> None:
        """Called when evolution begins."""
        pass

    def on_evolve_end(self, **kwargs: Any) -> None:
        """Called when evolution completes."""
        pass

    def on_cycle_start(self, **kwargs: Any) -> None:
        """Called at the start of each evolution cycle."""
        pass

    def on_cycle_end(self, **kwargs: Any) -> None:
        """Called at the end of each evolution cycle."""
        pass

    def on_task_before(self, **kwargs: Any) -> None:
        """Called before a task is executed."""
        pass

    def on_task_after(self, **kwargs: Any) -> None:
        """Called after a task completes successfully."""
        pass

    def on_task_error(self, **kwargs: Any) -> None:
        """Called when a task raises an error."""
        pass

    def on_alert_fired(self, **kwargs: Any) -> None:
        """Called when an alert rule fires."""
        pass

    def on_healing_triggered(self, **kwargs: Any) -> None:
        """Called when a healing action is triggered."""
        pass

    def on_health_check(self, **kwargs: Any) -> None:
        """Called periodically for health checks (scheduler tick)."""
        pass


# ══════════════════════════════════════════════════════════════════
# Plugin manager
# ══════════════════════════════════════════════════════════════════


class PluginManager:
    """Manages registered plugins and dispatches lifecycle events.

    Thread‑safe: uses a RLock internally for register/unregister.
    Event dispatch is *not* locked (plugins receive events
    sequentially on the calling thread).

    Attributes:
        plugins: list of registered plugins (copy‑on‑read for safety).
    """

    def __init__(self):
        self._plugins: List[Plugin] = []
        self._lock = __import__("threading").RLock()

    @property
    def plugins(self) -> List[Plugin]:
        """Return a snapshot of registered plugins."""
        with self._lock:
            return list(self._plugins)

    def register(self, plugin: Plugin) -> None:
        """Register a plugin.

        If a plugin with the same name is already registered,
        it is replaced (last wins). Plugins are unique by name.
        """
        with self._lock:
            # Remove existing with same name
            self._plugins = [p for p in self._plugins if p.name != plugin.name]
            self._plugins.append(plugin)
            logger.info("[PluginManager] Registered plugin '%s' (v%s)",
                        plugin.name, plugin.version)

    def unregister(self, name: str) -> bool:
        """Remove a plugin by name. Return True if it existed."""
        with self._lock:
            before = len(self._plugins)
            self._plugins = [p for p in self._plugins if p.name != name]
            removed = before != len(self._plugins)
            if removed:
                logger.info("[PluginManager] Unregistered plugin '%s'", name)
            return removed

    def get(self, name: str) -> Optional[Plugin]:
        """Get a plugin by name, or None if not found."""
        with self._lock:
            for p in self._plugins:
                if p.name == name:
                    return p
        return None

    def list_names(self) -> List[str]:
        """Return plugin names in registration order."""
        with self._lock:
            return [p.name for p in self._plugins]

    def count(self) -> int:
        """Return the number of registered plugins."""
        with self._lock:
            return len(self._plugins)

    def clear(self) -> None:
        """Remove all plugins."""
        with self._lock:
            self._plugins.clear()
            logger.info("[PluginManager] Cleared all plugins")

    # ── Event dispatch ──────────────────────────────────────────

    # Mapping of LifecycleEvent to plugin method names
    _EVENT_MAP: Dict[LifecycleEvent, str] = {
        LifecycleEvent.ON_INIT: "on_init",
        LifecycleEvent.ON_SHUTDOWN: "on_shutdown",
        LifecycleEvent.ON_MINISTER_REGISTER: "on_minister_register",
        LifecycleEvent.ON_MINISTER_UNREGISTER: "on_minister_unregister",
        LifecycleEvent.ON_EVOLVE_START: "on_evolve_start",
        LifecycleEvent.ON_EVOLVE_END: "on_evolve_end",
        LifecycleEvent.ON_CYCLE_START: "on_cycle_start",
        LifecycleEvent.ON_CYCLE_END: "on_cycle_end",
        LifecycleEvent.ON_TASK_BEFORE: "on_task_before",
        LifecycleEvent.ON_TASK_AFTER: "on_task_after",
        LifecycleEvent.ON_TASK_ERROR: "on_task_error",
        LifecycleEvent.ON_ALERT_FIRED: "on_alert_fired",
        LifecycleEvent.ON_HEALING_TRIGGERED: "on_healing_triggered",
        LifecycleEvent.ON_HEALTH_CHECK: "on_health_check",
    }

    def dispatch(self, event: LifecycleEvent, **kwargs: Any) -> Dict[str, Any]:
        """Dispatch a lifecycle event to all registered plugins.

        Each plugin's corresponding hook is called with ``**kwargs``.
        Errors in individual plugins are caught and logged; they do
        not prevent other plugins from receiving the event.

        Returns a dict mapping plugin name → result (or None).
        """
        method_name = self._EVENT_MAP.get(event)
        if method_name is None:
            logger.warning("[PluginManager] Unknown event: %s", event)
            return {}

        results: Dict[str, Any] = {}
        # Snapshot to avoid holding lock during dispatch
        plugins_snapshot = self.plugins

        logger.debug("[PluginManager] Dispatching %s to %d plugin(s)",
                     event.value, len(plugins_snapshot))

        for plugin in plugins_snapshot:
            try:
                method = getattr(plugin, method_name)
                result = method(**kwargs)
                results[plugin.name] = result
            except Exception:
                logger.exception(
                    "[PluginManager] Plugin '%s' raised during %s",
                    plugin.name, event.value,
                )
                results[plugin.name] = None

        return results

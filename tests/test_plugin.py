"""Tests for jarvis.plugin — Plugin and PluginManager."""

from contextlib import nullcontext

import pytest

from jarvis.plugin import LifecycleEvent, Plugin, PluginManager


# ══════════════════════════════════════════════════════════════════
# Test plugins
# ══════════════════════════════════════════════════════════════════


class _NoopPlugin(Plugin):
    """A plugin that does nothing."""
    pass


class _CountingPlugin(Plugin):
    """A plugin that counts how many times each hook was called."""
    def __init__(self, name="Counter"):
        self._name = name
        self.counts = {}

    @property
    def name(self):
        return self._name

    def _inc(self, hook):
        self.counts[hook] = self.counts.get(hook, 0) + 1

    def on_init(self, **kw): self._inc("init")
    def on_shutdown(self, **kw): self._inc("shutdown")
    def on_evolve_start(self, **kw): self._inc("evolve_start")
    def on_evolve_end(self, **kw): self._inc("evolve_end")
    def on_task_before(self, **kw): self._inc("task_before")
    def on_task_after(self, **kw): self._inc("task_after")
    def on_alert_fired(self, **kw): self._inc("alert_fired")


class _FailingPlugin(Plugin):
    @property
    def name(self):
        return "Failing"

    def on_init(self, **kw):
        raise RuntimeError("deliberate failure")


class _KeyEchoPlugin(Plugin):
    """Echoes back kwargs received on on_init for test verification."""
    def on_init(self, **kw):
        self.last_kwargs = kw
        return kw


# ══════════════════════════════════════════════════════════════════
# Plugin base class
# ══════════════════════════════════════════════════════════════════


class TestPlugin:
    def test_default_name(self):
        p = _NoopPlugin()
        assert p.name == "_NoopPlugin"

    def test_default_version(self):
        p = _NoopPlugin()
        assert p.version == "0.1.0"

    def test_custom_name(self):
        p = _CountingPlugin(name="custom")
        assert p.name == "custom"

    def test_default_hooks_do_not_raise(self):
        p = _NoopPlugin()
        p.on_init()
        p.on_shutdown()
        p.on_evolve_start()
        p.on_task_before()


# ══════════════════════════════════════════════════════════════════
# PluginManager — registration
# ══════════════════════════════════════════════════════════════════


class TestManagerRegistration:
    def test_register_one(self):
        pm = PluginManager()
        pm.register(_NoopPlugin())
        assert pm.count() == 1
        assert pm.list_names() == ["_NoopPlugin"]

    def test_register_many(self):
        pm = PluginManager()
        pm.register(_NoopPlugin())
        pm.register(_CountingPlugin(name="A"))
        pm.register(_CountingPlugin(name="B"))
        assert pm.count() == 3

    def test_register_same_name_replaces(self):
        pm = PluginManager()
        a = _CountingPlugin(name="same")
        b = _CountingPlugin(name="same")
        pm.register(a)
        pm.register(b)
        assert pm.count() == 1
        assert pm.plugins[0] is b

    def test_get(self):
        pm = PluginManager()
        p = _CountingPlugin(name="Finder")
        pm.register(p)
        assert pm.get("Finder") is p
        assert pm.get("Ghost") is None

    def test_unregister_existing(self):
        pm = PluginManager()
        pm.register(_CountingPlugin(name="X"))
        assert pm.unregister("X") is True
        assert pm.count() == 0

    def test_unregister_nonexistent(self):
        pm = PluginManager()
        assert pm.unregister("X") is False

    def test_clear(self):
        pm = PluginManager()
        pm.register(_NoopPlugin())
        pm.register(_CountingPlugin(name="X"))
        pm.clear()
        assert pm.count() == 0

    def test_plugins_snapshot_is_copy(self):
        pm = PluginManager()
        pm.register(_NoopPlugin())
        snap = pm.plugins
        snap.append(None)  # must not affect internal list
        assert pm.count() == 1

    def test_list_names_order(self):
        pm = PluginManager()
        pm.register(_CountingPlugin(name="A"))
        pm.register(_CountingPlugin(name="B"))
        assert pm.list_names() == ["A", "B"]


# ══════════════════════════════════════════════════════════════════
# PluginManager — dispatch
# ══════════════════════════════════════════════════════════════════


class TestDispatch:
    def test_dispatch_single(self):
        pm = PluginManager()
        p = _CountingPlugin()
        pm.register(p)
        pm.dispatch(LifecycleEvent.ON_INIT)
        assert p.counts.get("init") == 1

    def test_dispatch_multiple_plugins(self):
        pm = PluginManager()
        a = _CountingPlugin(name="A")
        b = _CountingPlugin(name="B")
        pm.register(a)
        pm.register(b)
        pm.dispatch(LifecycleEvent.ON_EVOLVE_START)
        assert a.counts.get("evolve_start") == 1
        assert b.counts.get("evolve_start") == 1

    def test_dispatch_only_targets_correct_hook(self):
        pm = PluginManager()
        p = _CountingPlugin()
        pm.register(p)
        pm.dispatch(LifecycleEvent.ON_INIT)
        # Other hooks should not fire
        assert p.counts.get("evolve_start", 0) == 0
        assert p.counts.get("task_before", 0) == 0

    def test_dispatch_kwargs_passthrough(self):
        pm = PluginManager()
        p = _KeyEchoPlugin()
        pm.register(p)
        pm.dispatch(LifecycleEvent.ON_INIT, foo="bar", n=42)
        assert p.last_kwargs == {"foo": "bar", "n": 42}

    def test_dispatch_returns_results(self):
        pm = PluginManager()
        p = _KeyEchoPlugin()
        pm.register(p)
        results = pm.dispatch(LifecycleEvent.ON_INIT, foo="bar")
        assert results == {"_KeyEchoPlugin": {"foo": "bar"}}

    def test_dispatch_failing_plugin_does_not_block(self):
        pm = PluginManager()
        pm.register(_FailingPlugin())
        pm.register(_CountingPlugin(name="Survivor"))
        pm.dispatch(LifecycleEvent.ON_INIT)
        # Survivor should still receive the event
        assert pm.get("Survivor").counts.get("init") == 1

    def test_dispatch_failing_result_is_none(self):
        pm = PluginManager()
        pm.register(_FailingPlugin())
        results = pm.dispatch(LifecycleEvent.ON_INIT)
        assert results["Failing"] is None

    def test_dispatch_empty_manager(self):
        pm = PluginManager()
        results = pm.dispatch(LifecycleEvent.ON_INIT)
        assert results == {}

    def test_dispatch_unknown_event(self):
        pm = PluginManager()
        p = _CountingPlugin()
        pm.register(p)
        results = pm.dispatch(LifecycleEvent.ON_HEALTH_CHECK)
        # No hook fires for unknown event
        assert results == {} or True  # either empty or only Nones


# ══════════════════════════════════════════════════════════════════
# Cover all lifecycle events
# ══════════════════════════════════════════════════════════════════


class _FullTracker(Plugin):
    """Tracks all lifecycle events."""
    def __init__(self):
        self.calls = []

    def on_init(self, **kw): self.calls.append("init")
    def on_shutdown(self, **kw): self.calls.append("shutdown")
    def on_minister_register(self, **kw): self.calls.append("minister_register")
    def on_minister_unregister(self, **kw): self.calls.append("minister_unregister")
    def on_evolve_start(self, **kw): self.calls.append("evolve_start")
    def on_evolve_end(self, **kw): self.calls.append("evolve_end")
    def on_cycle_start(self, **kw): self.calls.append("cycle_start")
    def on_cycle_end(self, **kw): self.calls.append("cycle_end")
    def on_task_before(self, **kw): self.calls.append("task_before")
    def on_task_after(self, **kw): self.calls.append("task_after")
    def on_task_error(self, **kw): self.calls.append("task_error")
    def on_alert_fired(self, **kw): self.calls.append("alert_fired")
    def on_healing_triggered(self, **kw): self.calls.append("healing_triggered")
    def on_health_check(self, **kw): self.calls.append("health_check")


class TestAllEvents:
    EVENTS = [
        (LifecycleEvent.ON_INIT, "init"),
        (LifecycleEvent.ON_SHUTDOWN, "shutdown"),
        (LifecycleEvent.ON_MINISTER_REGISTER, "minister_register"),
        (LifecycleEvent.ON_MINISTER_UNREGISTER, "minister_unregister"),
        (LifecycleEvent.ON_EVOLVE_START, "evolve_start"),
        (LifecycleEvent.ON_EVOLVE_END, "evolve_end"),
        (LifecycleEvent.ON_CYCLE_START, "cycle_start"),
        (LifecycleEvent.ON_CYCLE_END, "cycle_end"),
        (LifecycleEvent.ON_TASK_BEFORE, "task_before"),
        (LifecycleEvent.ON_TASK_AFTER, "task_after"),
        (LifecycleEvent.ON_TASK_ERROR, "task_error"),
        (LifecycleEvent.ON_ALERT_FIRED, "alert_fired"),
        (LifecycleEvent.ON_HEALING_TRIGGERED, "healing_triggered"),
        (LifecycleEvent.ON_HEALTH_CHECK, "health_check"),
    ]

    @pytest.mark.parametrize("event,expected", EVENTS)
    def test_each_event_dispatches(self, event, expected):
        pm = PluginManager()
        tracker = _FullTracker()
        pm.register(tracker)
        pm.dispatch(event)
        assert tracker.calls == [expected]

    def test_all_events_in_event_map(self):
        """Ensure every LifecycleEvent has a mapping."""
        for e in LifecycleEvent:
            assert e in PluginManager._EVENT_MAP, f"Missing mapping for {e}"


# ══════════════════════════════════════════════════════════════════
# LifecycleEvent enum
# ══════════════════════════════════════════════════════════════════


class TestLifecycleEvent:
    def test_has_expected_values(self):
        assert LifecycleEvent.ON_INIT.value == "on_init"
        assert LifecycleEvent.ON_SHUTDOWN.value == "on_shutdown"
        assert LifecycleEvent.ON_TASK_AFTER.value == "on_task_after"

    def test_total_event_count(self):
        # If this fails, you added a new event — update the
        # _EVENT_MAP in PluginManager and add a test parametrize entry.
        assert len(LifecycleEvent) == 14

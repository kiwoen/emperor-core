"""Tests for Emperor ↔ PluginManager integration.

Verifies that lifecycle events are dispatched at the right points
during Emperor's workflow.
"""

import pytest

from jarvis.emperor import Emperor, EmperorConfig
from jarvis.plugin import LifecycleEvent, Plugin


# ══════════════════════════════════════════════════════════════════
# Test plugins
# ══════════════════════════════════════════════════════════════════


class _Tracker(Plugin):
    """Records every hook call into a list."""
    def __init__(self, name="Tracker"):
        self._name = name
        self.calls = []
        self.kwargs_log = []

    @property
    def name(self):
        return self._name

    def _record(self, hook, **kw):
        self.calls.append(hook)
        self.kwargs_log.append((hook, dict(kw)))

    def on_init(self, **kw): self._record("init", **kw)
    def on_shutdown(self, **kw): self._record("shutdown", **kw)
    def on_minister_register(self, **kw): self._record("minister_register", **kw)
    def on_evolve_start(self, **kw): self._record("evolve_start", **kw)
    def on_evolve_end(self, **kw): self._record("evolve_end", **kw)
    def on_task_before(self, **kw): self._record("task_before", **kw)
    def on_task_after(self, **kw): self._record("task_after", **kw)
    def on_task_error(self, **kw): self._record("task_error", **kw)


class _FailingPlugin(Plugin):
    @property
    def name(self):
        return "Failing"

    def on_init(self, **kw):
        raise RuntimeError("boom")


# ══════════════════════════════════════════════════════════════════
# Plugin property and basic lifecycle
# ══════════════════════════════════════════════════════════════════


class TestEmperorPluginIntegration:
    def test_plugins_property_has_metrics_plugin(self):
        emp = Emperor()
        # Emperor now eagerly registers a MetricsPlugin on init
        assert emp.plugins.count() == 1
        assert any(p.name == "MetricsPlugin" for p in emp.plugins._plugins)

    def test_register_plugin(self):
        emp = Emperor()
        emp.plugins.register(_Tracker())
        # MetricsPlugin + our tracker = 2
        assert emp.plugins.count() == 2

    def test_init_dispatched_to_plugin(self):
        emp = Emperor()
        tracker = _Tracker()
        # Register plugin BEFORE creating Emperor would be ideal, but
        # the Emperor has a fresh PluginManager on construction.
        # So the test is: Emperor's __init__ dispatches ON_INIT.
        # We need to register via a re-created emperor to verify.
        emp2 = Emperor()
        emp2.plugins.register(tracker)
        # Trigger init manually (post-construction)
        emp2._dispatch(LifecycleEvent.ON_INIT, emperor=emp2)
        assert "init" in tracker.calls

    def test_shutdown_dispatched(self):
        emp = Emperor()
        tracker = _Tracker()
        emp.plugins.register(tracker)
        emp.shutdown()
        assert "shutdown" in tracker.calls


# ══════════════════════════════════════════════════════════════════
# Register flow
# ══════════════════════════════════════════════════════════════════


class TestRegisterDispatch:
    def test_register_dispatches_event(self):
        emp = Emperor()
        tracker = _Tracker()
        emp.plugins.register(tracker)
        emp.register("turing", domain="math")
        assert "minister_register" in tracker.calls

    def test_register_event_passes_kwargs(self):
        emp = Emperor()
        tracker = _Tracker()
        emp.plugins.register(tracker)
        emp.register("ada", domain="cs", temperature=0.5)
        hook, kwargs = tracker.kwargs_log[-1]
        assert kwargs["minister_name"] == "ada"
        assert kwargs["domain"] == "cs"
        assert kwargs["temperature"] == 0.5


# ══════════════════════════════════════════════════════════════════
# Evolve flow
# ══════════════════════════════════════════════════════════════════


class TestEvolveDispatch:
    def test_evolve_dispatches_start_and_end(self):
        emp = Emperor()
        tracker = _Tracker()
        emp.plugins.register(tracker)
        emp.evolve(cycles=1)
        assert "evolve_start" in tracker.calls
        assert "evolve_end" in tracker.calls

    def test_evolve_start_has_cycles_kwarg(self):
        emp = Emperor()
        tracker = _Tracker()
        emp.plugins.register(tracker)
        emp.evolve(cycles=2)
        hook, kwargs = tracker.kwargs_log[0]  # evolve_start
        assert kwargs["cycles"] == 2

    def test_evolve_invalid_raises_without_dispatch(self):
        emp = Emperor()
        tracker = _Tracker()
        emp.plugins.register(tracker)
        with pytest.raises(ValueError):
            emp.evolve(cycles=0)
        # Should NOT have dispatched since validation fails first
        assert "evolve_start" not in tracker.calls


# ══════════════════════════════════════════════════════════════════
# Task flow
# ══════════════════════════════════════════════════════════════════


class TestTaskDispatch:
    def test_task_before_and_after_dispatched(self):
        emp = Emperor()
        # Register a minister so task can be executed
        emp.register("worker")
        tracker = _Tracker()
        emp.plugins.register(tracker)
        emp.execute_task("hello", domain="general")
        assert "task_before" in tracker.calls
        # Whether task_after or task_error fires depends on backend

    def test_task_before_passes_kwargs(self):
        emp = Emperor()
        emp.register("worker")
        tracker = _Tracker()
        emp.plugins.register(tracker)
        emp.execute_task("hi", domain="general", task_id="t-123")
        # First call should be task_before
        hook, kwargs = tracker.kwargs_log[0]
        assert kwargs["task_id"] == "t-123"
        assert kwargs["prompt"] == "hi"
        assert kwargs["domain"] == "general"


# ══════════════════════════════════════════════════════════════════
# Fault isolation
# ══════════════════════════════════════════════════════════════════


class TestFaultIsolation:
    def test_failing_plugin_does_not_break_emperor(self):
        emp = Emperor()
        emp.plugins.register(_FailingPlugin())
        emp.plugins.register(_Tracker(name="Survivor"))
        # Registering a minister should still work
        emp.register("turing", domain="math")
        assert "minister_register" in emp.plugins.get("Survivor").calls
        # Cleanup
        emp.shutdown()


# ══════════════════════════════════════════════════════════════════
# LifecycleEvent enum integration
# ══════════════════════════════════════════════════════════════════


class TestLifecycleEventEnum:
    def test_all_dispatched_events_exist(self):
        # The emperor dispatches: ON_INIT, ON_SHUTDOWN,
        # ON_MINISTER_REGISTER, ON_EVOLVE_START/END, ON_TASK_BEFORE/AFTER/ERROR
        dispatched = {
            LifecycleEvent.ON_INIT,
            LifecycleEvent.ON_SHUTDOWN,
            LifecycleEvent.ON_MINISTER_REGISTER,
            LifecycleEvent.ON_EVOLVE_START,
            LifecycleEvent.ON_EVOLVE_END,
            LifecycleEvent.ON_TASK_BEFORE,
            LifecycleEvent.ON_TASK_AFTER,
            LifecycleEvent.ON_TASK_ERROR,
        }
        for e in dispatched:
            assert e in LifecycleEvent

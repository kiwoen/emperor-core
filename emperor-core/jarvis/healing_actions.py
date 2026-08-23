"""Pre‑baked healing actions for common alert scenarios.

These actions are designed to be registered with the HealingEngine
when the Emperor is started, providing out‑of‑the‑box self‑healing.

Usage:
    from jarvis.healing import HealingAction
    from jarvis.healing_actions import restart_scheduler, emergency_evolve

    engine.register(HealingAction(
        name="restart_scheduler_if_down",
        alert_rule="scheduler_down",
        action=restart_scheduler,
        cooldown_seconds=60,
    ))
"""

import logging
import threading
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# Core helpers
# ══════════════════════════════════════════════════════════════════


def _get_emperor() -> Optional[Any]:
    """Retrieve the current Emperor instance (if any)."""
    try:
        from jarvis.emperor import Emperor
        # This is a global singleton pattern; adjust if your app uses multiple.
        # For now, we assume the Emperor is accessible via a global or module‑level variable.
        # In a real integration, you'd pass the emperor instance to the action.
        return None  # placeholder
    except ImportError:
        return None


# ══════════════════════════════════════════════════════════════════
# Scheduler actions
# ══════════════════════════════════════════════════════════════════


def restart_scheduler() -> bool:
    """Restart the scheduler if it's stopped or stuck.

    Returns True if restart was attempted, False otherwise.
    """
    emperor = _get_emperor()
    if emperor is None or emperor.scheduler is None:
        logger.warning("[Healing] Cannot restart scheduler: no emperor/scheduler")
        return False

    s = emperor.scheduler
    report = s.report()
    if report.state == "RUNNING":
        logger.info("[Healing] Scheduler already running, skipping restart")
        return False

    logger.info("[Healing] Restarting scheduler (state=%s)", report.state)
    try:
        s.start(emperor)
        # Wait a moment for it to become RUNNING
        for _ in range(10):
            time.sleep(0.1)
            if s.report().state == "RUNNING":
                logger.info("[Healing] Scheduler restarted successfully")
                return True
        logger.warning("[Healing] Scheduler restart may have failed")
        return False
    except Exception as e:
        logger.exception("[Healing] Scheduler restart failed: %s", e)
        return False


def stop_scheduler() -> bool:
    """Gracefully stop the scheduler (if it's running)."""
    emperor = _get_emperor()
    if emperor is None or emperor.scheduler is None:
        return False

    s = emperor.scheduler
    if s.report().state == "STOPPED":
        return False

    logger.info("[Healing] Stopping scheduler")
    try:
        s.stop()
        return True
    except Exception as e:
        logger.exception("[Healing] Scheduler stop failed: %s", e)
        return False


# ══════════════════════════════════════════════════════════════════
# Court / evolution actions
# ══════════════════════════════════════════════════════════════════


def emergency_evolve(cycles: int = 1) -> bool:
    """Trigger an emergency evolution cycle to replenish ministers.

    Args:
        cycles: Number of evolution cycles to run (default 1).

    Returns True if evolution was attempted, False otherwise.
    """
    emperor = _get_emperor()
    if emperor is None:
        return False

    logger.info("[Healing] Emergency evolution (%d cycles)", cycles)
    try:
        emperor.evolve(n_cycles=cycles)
        return True
    except Exception as e:
        logger.exception("[Healing] Emergency evolution failed: %s", e)
        return False


def replenish_ministers(min_count: int = 3) -> bool:
    """Ensure at least `min_count` active ministers exist.

    If the count is below threshold, run a single evolution cycle.
    """
    emperor = _get_emperor()
    if emperor is None:
        return False

    active = len(emperor.court.active_ministers)
    if active >= min_count:
        logger.debug("[Healing] Sufficient ministers (%d), no replenish", active)
        return False

    logger.info("[Healing] Replenishing ministers (have %d, need %d)",
                active, min_count)
    try:
        emperor.evolve(n_cycles=1)
        return True
    except Exception as e:
        logger.exception("[Healing] Replenish failed: %s", e)
        return False


# ══════════════════════════════════════════════════════════════════
# Task‑engine actions
# ══════════════════════════════════════════════════════════════════


def reset_task_engine() -> bool:
    """Reset the task engine's internal state (clear caches, etc.)."""
    emperor = _get_emperor()
    if emperor is None or emperor.task_engine is None:
        return False

    logger.info("[Healing] Resetting task engine")
    try:
        # If the engine has a reset method, call it
        if hasattr(emperor.task_engine, "reset"):
            emperor.task_engine.reset()
            return True
        # Otherwise, just log that we can't
        logger.warning("[Healing] Task engine has no reset method")
        return False
    except Exception as e:
        logger.exception("[Healing] Task engine reset failed: %s", e)
        return False


# ══════════════════════════════════════════════════════════════════
# Alert‑manager actions
# ══════════════════════════════════════════════════════════════════


def silence_alert_rule(rule_name: str, duration_seconds: int = 300) -> bool:
    """Temporarily disable an alert rule to prevent spam.

    This is useful when a rule is firing repeatedly due to a transient
    condition that cannot be immediately fixed.

    Args:
        rule_name: Name of the alert rule to silence.
        duration_seconds: How long to keep it disabled (default 5 minutes).

    Returns True if the rule was found and silenced, False otherwise.
    """
    emperor = _get_emperor()
    if emperor is None or emperor.alerts is None:
        return False

    mgr = emperor.alerts
    rule = mgr.get_rule(rule_name)
    if rule is None:
        logger.warning("[Healing] Alert rule '%s' not found", rule_name)
        return False

    if not rule.enabled:
        logger.debug("[Healing] Rule '%s' already disabled", rule_name)
        return False

    logger.info("[Healing] Silencing alert rule '%s' for %d seconds",
                rule_name, duration_seconds)
    try:
        mgr.disable_rule(rule_name)

        # Schedule re‑enable after duration
        def reenable():
            time.sleep(duration_seconds)
            try:
                mgr.enable_rule(rule_name)
                logger.info("[Healing] Re‑enabled alert rule '%s'", rule_name)
            except Exception as e:
                logger.error("[Healing] Failed to re‑enable rule '%s': %s",
                             rule_name, e)

        t = threading.Thread(target=reenable, daemon=True)
        t.start()
        return True
    except Exception as e:
        logger.exception("[Healing] Failed to silence rule '%s': %s",
                         rule_name, e)
        return False


# ══════════════════════════════════════════════════════════════════
# System‑level actions (requires careful integration)
# ══════════════════════════════════════════════════════════════════


def flush_logs() -> bool:
    """Force‑flush application logs (if using a buffered handler)."""
    logger.info("[Healing] Flushing logs")
    for handler in logger.handlers:
        try:
            handler.flush()
        except AttributeError:
            pass
    return True


def gc_collect() -> bool:
    """Trigger a full garbage‑collection cycle."""
    import gc
    logger.info("[Healing] Running full GC")
    collected = gc.collect()
    logger.debug("[Healing] GC collected %d objects", collected)
    return True

"""Self-healing engine — automatic corrective actions triggered by alerts.

When AlertManager fires an alert, the HealingEngine matches it against
registered healing actions and executes them (with cooldown to prevent
runaway loops). Integrates into Scheduler.tick for autonomous operation.

P2 Enhancement: StrategySwitcher for adaptive strategy switching,
effectiveness tracking, and RecoveryEngine integration.

Usage:
    from jarvis.healing import HealingEngine, HealingAction, StrategySwitcher
    from jarvis.alerts import AlertManager

    mgr = AlertManager()
    healer = HealingEngine()

    healer.register(HealingAction(
        name="restart_scheduler_if_down",
        alert_rule="scheduler_down",
        action=restart_procedure,
        fallback_actions=["force_restart", "escalate_to_admin"],
    ))

    # Auto-evaluate after alert check:
    for alert in fired_alerts:
        healer.handle_with_fallback(alert)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# Types
# ══════════════════════════════════════════════════════════════════


@dataclass
class HealingAction:
    """A corrective action triggered by a specific alert rule.

    Args:
        name: Unique name for this healing action.
        alert_rule: Name of the AlertRule that triggers this action.
        action: Zero-arg callable executed when triggered.
        cooldown_seconds: Minimum time between consecutive executions.
        max_attempts: Maximum total executions (0 = unlimited).
        enabled: Whether this action can fire.
        fallback_actions: Ordered list of other action names to try if this one fails.
        success_count: How many times this action succeeded.
        failure_count: How many times this action failed.
        avg_recovery_time: Average time (s) for successful recovery.
    """

    name: str
    alert_rule: str
    action: Callable[[], Any]
    cooldown_seconds: float = 300.0
    max_attempts: int = 10
    enabled: bool = True
    tags: list[str] = field(default_factory=list)
    # ── P2: strategy switching & effectiveness tracking ──
    fallback_actions: list[str] = field(default_factory=list)
    success_count: int = 0
    failure_count: int = 0
    avg_recovery_time: float = 0.0


@dataclass
class HealingRecord:
    """Record of a healing action execution."""

    action_name: str
    alert_rule: str
    timestamp: float
    success: bool
    error: str = ""
    strategy_depth: int = 0  # P2: 0=primary, 1=fallback_1, etc.
    recovery_time: float = 0.0  # P2: elapsed time


# ══════════════════════════════════════════════════════════════════
# StrategySwitcher — P2: adaptive strategy switching
# ══════════════════════════════════════════════════════════════════


class StrategySwitcher:
    """Adaptive strategy switching for self-healing actions.

    When a primary healing action fails, automatically tries fallback
    strategies in a priority chain: primary → fallback_1 → fallback_2
    → manual_escalation.

    Learns which strategies are most effective per failure type and
    reorders the chain accordingly for future incidents.

    Cooldown increases exponentially with consecutive failures to
    prevent runaway loops: base * 2^(failure_count).
    """

    def __init__(self, base_cooldown: float = 1.0):
        self.base_cooldown = base_cooldown
        # Per alert_rule: dict of action_name -> (successes, attempts)
        self._effectiveness: dict[str, dict[str, tuple[int, int]]] = {}
        # Failure chain tracking: alert_rule -> consecutive failure count
        self._consecutive_failures: dict[str, int] = {}

    def compute_cooldown(self, alert_rule: str, action: HealingAction) -> float:
        """Compute adaptive cooldown based on consecutive failure count.

        Formula: base_cooldown * 2^(consecutive_failures)
        """
        failures = self._consecutive_failures.get(alert_rule, 0)
        if failures == 0:
            return self.base_cooldown
        return min(self.base_cooldown * (2 ** failures), 3600.0)

    def record_success(self, alert_rule: str, action_name: str, depth: int):
        """Record a successful healing action execution."""
        if alert_rule not in self._effectiveness:
            self._effectiveness[alert_rule] = {}
        prev = self._effectiveness[alert_rule].get(action_name, (0, 0))
        self._effectiveness[alert_rule][action_name] = (prev[0] + 1, prev[1] + 1)
        self._consecutive_failures[alert_rule] = 0
        logger.debug(
            "[StrategySwitcher] Success: rule=%s action=%s depth=%d",
            alert_rule, action_name, depth,
        )

    def record_failure(self, alert_rule: str, action_name: str, depth: int):
        """Record a failed healing action execution."""
        if alert_rule not in self._effectiveness:
            self._effectiveness[alert_rule] = {}
        prev = self._effectiveness[alert_rule].get(action_name, (0, 0))
        self._effectiveness[alert_rule][action_name] = (prev[0], prev[1] + 1)
        self._consecutive_failures[alert_rule] = self._consecutive_failures.get(alert_rule, 0) + 1
        logger.debug(
            "[StrategySwitcher] Failure: rule=%s action=%s depth=%d, consec=%d",
            alert_rule, action_name, depth, self._consecutive_failures[alert_rule],
        )

    def get_best_action(self, alert_rule: str, actions: dict[str, HealingAction]) -> Optional[str]:
        """Return the action name with highest success rate for this alert_rule.

        If no history, returns None (caller should use the first registered action).
        """
        eff = self._effectiveness.get(alert_rule, {})
        if not eff:
            return None

        best_name = None
        best_rate = -1.0
        for name, (s, t) in eff.items():
            if t == 0:
                continue
            rate = s / t
            if rate > best_rate and name in actions:
                best_rate = rate
                best_name = name

        return best_name

    def get_effectiveness_report(self) -> dict[str, dict[str, Any]]:
        """Return effectiveness statistics per alert_rule + action."""
        return {
            rule: {
                name: {
                    "successes": s,
                    "attempts": a,
                    "success_rate": round(s / a, 4) if a > 0 else 0.0,
                }
                for name, (s, a) in actions.items()
            }
            for rule, actions in self._effectiveness.items()
        }

    def reset_stats(self):
        """Reset all effectiveness and failure tracking."""
        self._effectiveness.clear()
        self._consecutive_failures.clear()


# ══════════════════════════════════════════════════════════════════
# Engine
# ══════════════════════════════════════════════════════════════════


class HealingEngine:
    """Matches fired alerts to healing actions and executes them.

    P2 enhancements:
      - handle_with_fallback(): auto-switches to fallback actions
      - get_effectiveness_report(): per-action success/failure stats
      - reset_stats(): reset all counters
      - RecoveryEngine integration for retry+circuit-breaker+fallback
    """

    def __init__(self, recovery_engine=None) -> None:
        self._actions: dict[str, HealingAction] = {}
        self._history: list[HealingRecord] = []
        self._last_triggered: dict[str, float] = {}  # action_name → timestamp
        self._attempt_counts: dict[str, int] = {}     # action_name → count
        self._switcher = StrategySwitcher()
        self._recovery_engine = recovery_engine  # P2: RecoveryEngine integration

    # ── Registration ────────────────────────────────────────────────

    def register(self, action: HealingAction) -> None:
        """Register a healing action."""
        self._actions[action.name] = action
        logger.debug("[Healing] Action registered: %s → alert '%s'",
                     action.name, action.alert_rule)

    def unregister(self, name: str) -> bool:
        """Remove a healing action by name."""
        if name in self._actions:
            del self._actions[name]
            self._last_triggered.pop(name, None)
            self._attempt_counts.pop(name, None)
            return True
        return False

    def get_action(self, name: str) -> Optional[HealingAction]:
        """Get a healing action by name (returns a copy)."""
        a = self._actions.get(name)
        if a is None:
            return None
        return HealingAction(
            name=a.name, alert_rule=a.alert_rule, action=a.action,
            cooldown_seconds=a.cooldown_seconds, max_attempts=a.max_attempts,
            enabled=a.enabled, tags=list(a.tags),
            fallback_actions=list(a.fallback_actions),
            success_count=a.success_count,
            failure_count=a.failure_count,
            avg_recovery_time=a.avg_recovery_time,
        )

    def list_actions(self) -> list[HealingAction]:
        """List all registered actions (copies)."""
        return [self.get_action(name) for name in self._actions]

    @property
    def switcher(self) -> StrategySwitcher:
        """Access the internal StrategySwitcher. (P2)"""
        return self._switcher

    # ── Triggering ──────────────────────────────────────────────────

    def handle(self, alert_rule_name: str) -> list[HealingRecord]:
        """Check and execute all matching healing actions for a fired alert rule.

        Returns a list of HealingRecord for each action executed (may be empty).
        """
        now = time.time()
        records: list[HealingRecord] = []

        for action in self._actions.values():
            if not action.enabled:
                continue
            if action.alert_rule != alert_rule_name:
                continue

            # Cooldown check
            last = self._last_triggered.get(action.name, 0)
            if now - last < action.cooldown_seconds:
                logger.debug("[Healing] '%s' on cooldown (%.0fs remaining)",
                             action.name, action.cooldown_seconds - (now - last))
                continue

            # Attempt limit check
            attempts = self._attempt_counts.get(action.name, 0)
            if action.max_attempts > 0 and attempts >= action.max_attempts:
                logger.debug("[Healing] '%s' exhausted (%d/%d attempts)",
                             action.name, attempts, action.max_attempts)
                continue

            # Execute
            logger.info("[Healing] Triggering '%s' for alert '%s'",
                        action.name, alert_rule_name)
            self._last_triggered[action.name] = now
            self._attempt_counts[action.name] = attempts + 1

            success, error_msg, elapsed = self._execute_action(action)
            if success:
                action.success_count += 1
            else:
                action.failure_count += 1
            action.avg_recovery_time = self._update_avg(
                action.avg_recovery_time,
                elapsed,
                action.success_count + action.failure_count,
            )

            record = HealingRecord(
                action_name=action.name,
                alert_rule=alert_rule_name,
                timestamp=now,
                success=success,
                error=error_msg,
                strategy_depth=0,
                recovery_time=elapsed,
            )
            self._history.append(record)
            records.append(record)

        # Trim history if too large
        if len(self._history) > 200:
            self._history = self._history[-100:]

        return records

    # ── P2: handle_with_fallback ───────────────────────────────────

    def handle_with_fallback(self, alert_rule_name: str) -> list[HealingRecord]:
        """Handle an alert with automatic strategy switching.

        For each matching action:
          1. Try the best-known action (based on effectiveness history)
          2. If it fails, try fallback_actions in order
          3. If all fail, escalate to manual

        RecoveryEngine wrapping (if configured) adds retry + circuit breaker.
        """
        records: list[HealingRecord] = []
        now = time.time()
        tried_names: set[str] = set()  # P2: prevent duplicate fallback tries

        for action in self._actions.values():
            if not action.enabled or action.alert_rule != alert_rule_name:
                continue
            if action.name in tried_names:
                continue  # already tried as a fallback for another action

            # Check if any action has best historical record
            best_name = self._switcher.get_best_action(alert_rule_name, self._actions)
            primary = action
            if best_name and best_name != action.name:
                primary = self._actions.get(best_name, action)

            # Build strategy chain: primary → fallbacks → manual escalation
            chain = [primary.name] + primary.fallback_actions + ["__manual_escalation__"]

            for depth, strategy_name in enumerate(chain):
                if strategy_name == "__manual_escalation__":
                    records.append(HealingRecord(
                        action_name="manual_escalation",
                        alert_rule=alert_rule_name,
                        timestamp=now,
                        success=False,
                        error=f"All strategies exhausted for alert '{alert_rule_name}'",
                        strategy_depth=depth,
                    ))
                    logger.warning(
                        "[Healing] All strategies exhausted for alert '%s'",
                        alert_rule_name,
                    )
                    self._switcher.record_failure(alert_rule_name, primary.name, depth)
                    break

                strat_action = self._actions.get(strategy_name)
                if strat_action is None:
                    logger.debug("[Healing] Strategy '%s' not found, skipping", strategy_name)
                    continue

                # Cooldown check
                last = self._last_triggered.get(strat_action.name, 0)
                adaptive_cooldown = self._switcher.compute_cooldown(alert_rule_name, strat_action)
                effective_cooldown = max(strat_action.cooldown_seconds, adaptive_cooldown)
                if now - last < effective_cooldown:
                    logger.debug("[Healing] '%s' on cooldown (%.0fs remaining)",
                                 strat_action.name, effective_cooldown - (now - last))
                    continue

                # Attempt limit check
                attempts = self._attempt_counts.get(strat_action.name, 0)
                if strat_action.max_attempts > 0 and attempts >= strat_action.max_attempts:
                    continue

                # Execute with optional RecoveryEngine wrapping
                tried_names.add(strat_action.name)
                self._last_triggered[strat_action.name] = now
                self._attempt_counts[strat_action.name] = attempts + 1

                logger.info(
                    "[Healing] Strategy depth=%d: '%s' for alert '%s'",
                    depth, strat_action.name, alert_rule_name,
                )

                if self._recovery_engine:
                    success, error_msg, elapsed = self._execute_with_recovery(strat_action)
                else:
                    success, error_msg, elapsed = self._execute_action(strat_action)

                # Update action stats
                is_self = strat_action is primary
                if success:
                    strat_action.success_count += 1
                    if not is_self:
                        primary.success_count += 1
                else:
                    strat_action.failure_count += 1
                    if not is_self:
                        primary.failure_count += 1
                total_ops = strat_action.success_count + strat_action.failure_count
                strat_action.avg_recovery_time = self._update_avg(
                    strat_action.avg_recovery_time, elapsed, total_ops,
                )

                record = HealingRecord(
                    action_name=strat_action.name,
                    alert_rule=alert_rule_name,
                    timestamp=now,
                    success=success,
                    error=error_msg,
                    strategy_depth=depth,
                    recovery_time=elapsed,
                )
                self._history.append(record)
                records.append(record)

                if success:
                    self._switcher.record_success(alert_rule_name, primary.name, depth)
                    break  # Strategy succeeded, stop the chain
                else:
                    self._switcher.record_failure(alert_rule_name, primary.name, depth)
                    # Continue to next fallback

        # Trim history
        if len(self._history) > 200:
            self._history = self._history[-100:]

        return records

    # ── P2: Effectiveness report ───────────────────────────────────

    def get_effectiveness_report(self) -> dict[str, Any]:
        """Return a sorted report of healing action effectiveness.

        Returns:
            dict with:
              - actions: list of action stats sorted by success rate desc
              - switcher: StrategySwitcher effectiveness data
        """
        action_stats = []
        for a in self._actions.values():
            total = a.success_count + a.failure_count
            action_stats.append({
                "name": a.name,
                "alert_rule": a.alert_rule,
                "success_count": a.success_count,
                "failure_count": a.failure_count,
                "total_attempts": total,
                "success_rate": round(a.success_count / total, 4) if total > 0 else 0.0,
                "avg_recovery_time": round(a.avg_recovery_time, 4),
                "fallback_actions": a.fallback_actions,
                "enabled": a.enabled,
            })

        # Sort by success rate desc
        action_stats.sort(key=lambda x: x["success_rate"], reverse=True)

        return {
            "actions": action_stats,
            "switcher": self._switcher.get_effectiveness_report(),
        }

    def reset_stats(self):
        """Reset all healing statistics and switcher state. (P2)"""
        self._attempt_counts.clear()
        self._last_triggered.clear()
        for a in self._actions.values():
            a.success_count = 0
            a.failure_count = 0
            a.avg_recovery_time = 0.0
        self._switcher.reset_stats()
        logger.info("[Healing] All statistics reset.")

    def handle_batch(self, fired_alert_rule_names: list[str]) -> list[HealingRecord]:
        """Process multiple fired alert rules in one pass."""
        records: list[HealingRecord] = []
        for rule_name in fired_alert_rule_names:
            records.extend(self.handle(rule_name))
        return records

    # ── History ─────────────────────────────────────────────────────

    def history(self, limit: int = 20) -> list[HealingRecord]:
        """Return recent healing records (newest first)."""
        return list(reversed(self._history[-limit:]))

    def clear_history(self) -> None:
        """Clear healing history."""
        self._history.clear()

    def reset_attempts(self, name: str = "") -> None:
        """Reset attempt counters. If name is empty, reset all."""
        if name:
            self._attempt_counts.pop(name, None)
            self._last_triggered.pop(name, None)
        else:
            self._attempt_counts.clear()
            self._last_triggered.clear()

    # ── Internal helpers ────────────────────────────────────────────

    def _execute_action(self, action: HealingAction) -> tuple[bool, str, float]:
        """Execute a single healing action and return (success, error, elapsed)."""
        start = time.time()
        try:
            action.action()
            return True, "", time.time() - start
        except Exception as e:
            logger.exception("[Healing] Action '%s' failed: %s", action.name, e)
            return False, str(e), time.time() - start

    def _execute_with_recovery(self, action: HealingAction) -> tuple[bool, str, float]:
        """Execute a healing action wrapped in RecoveryEngine."""
        start = time.time()
        try:
            from jarvis.failure_recovery import RecoveryContext
            ctx = RecoveryContext(stage_name=f"healing:{action.name}")
            # Use getattr in case the recovery_engine is a mock
            execute = getattr(self._recovery_engine, "execute_with_recovery", None)
            if execute is None:
                return self._execute_action(action)
            result = execute(lambda: action.action(), context=ctx)
            elapsed = time.time() - start
            if result.status.value in ("success", "retry_success", "degraded"):
                return True, f"Recovery: {result.status.value}", elapsed
            return False, result.error or "Recovery failed", elapsed
        except ImportError:
            return self._execute_action(action)
        except Exception as e:
            return False, str(e), time.time() - start

    @staticmethod
    def _update_avg(current_avg: float, new_value: float, n: int) -> float:
        """Update running average: new_avg = old_avg + (new - old_avg) / n."""
        if n <= 1:
            return new_value
        return current_avg + (new_value - current_avg) / n

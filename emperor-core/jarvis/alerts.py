"""Alert system — rule-based health monitoring with pluggable handlers.

Alerts fire when system metrics cross configurable thresholds.
Integrates with Scheduler (auto-evaluate each tick) and Dashboard.

Usage:
    from jarvis.alerts import AlertManager, AlertRule

    mgr = AlertManager()
    mgr.add_rule(AlertRule(
        name="low_success_rate",
        metric="success_rate",
        threshold=0.5,
        operator="lt",
        severity="critical",
        message="Task success rate dropped below 50%",
    ))

    # Integrate into Scheduler:
    sched.on_tick = mgr.evaluate  # auto-check after each event loop tick
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# Types
# ══════════════════════════════════════════════════════════════════


class AlertSeverity(Enum):
    """Alert severity levels."""

    INFO = auto()
    WARNING = auto()
    CRITICAL = auto()


@dataclass
class AlertRule:
    """A monitoring rule that fires when a metric crosses a threshold.

    Examples:
        AlertRule("low_success", "success_rate", 0.5, "lt", "critical",
                  "Success rate below 50%")
        AlertRule("high_failures", "task_failures_per_minute", 10, "gt",
                  "warning", "More than 10 failures/min")
        AlertRule("scheduler_down", "scheduler_running", 1, "eq",
                  "critical", "Scheduler has stopped")
    """

    name: str
    metric: str                       # key in the state dict
    threshold: float
    operator: str = "lt"              # lt / gt / eq / gte / lte
    severity: str = "warning"         # info / warning / critical
    message: str = ""
    cooldown_seconds: float = 60.0    # min time between repeated fires
    enabled: bool = True
    tags: list[str] = field(default_factory=list)


@dataclass
class Alert:
    """A fired alert instance."""

    rule_name: str
    severity: str
    message: str
    metric: str
    current_value: float
    threshold: float
    operator: str
    timestamp: float  # epoch seconds


AlertHandler = Callable[[Alert], None]


# ══════════════════════════════════════════════════════════════════
# Manager
# ══════════════════════════════════════════════════════════════════


class AlertManager:
    """Manages alert rules, evaluates state, dispatches to handlers."""

    def __init__(self, db: Any = None) -> None:
        self._db = db
        self._rules: dict[str, AlertRule] = {}
        self._handlers: list[AlertHandler] = []
        self._fired_history: list[Alert] = []  # capped at 200
        self._last_fired: dict[str, float] = {}  # rule_name → timestamp

        # Built-in rules store: rule_name → {condition, severity, message, enabled, cooldown_seconds}
        self._builtin_rules: dict[str, dict] = {}

        # Built-in: log handler
        self._handlers.append(self._log_handler)

    # ── Rule management ────────────────────────────────────────────

    def add_rule(self, rule: AlertRule) -> None:
        """Register an alert rule."""
        self._rules[rule.name] = rule
        logger.debug("[Alerts] Rule added: %s (%s %s %.2f)",
                     rule.name, rule.metric, rule.operator, rule.threshold)

    def remove_rule(self, name: str) -> bool:
        """Remove a rule by name."""
        if name in self._rules:
            del self._rules[name]
            self._last_fired.pop(name, None)
            return True
        return False

    def disable_rule(self, name: str) -> bool:
        """Disable a rule. Return True if the rule exists."""
        rule = self._rules.get(name)
        if rule is None:
            return False
        rule.enabled = False
        return True

    def enable_rule(self, name: str) -> bool:
        """Enable a rule. Return True if the rule exists."""
        rule = self._rules.get(name)
        if rule is None:
            return False
        rule.enabled = True
        return True

    def get_rule(self, name: str) -> Optional[AlertRule]:
        """Get a rule by name (returns a copy)."""
        r = self._rules.get(name)
        if r is None:
            return None
        return AlertRule(
            name=r.name, metric=r.metric, threshold=r.threshold,
            operator=r.operator, severity=r.severity, message=r.message,
            cooldown_seconds=r.cooldown_seconds, enabled=r.enabled,
            tags=list(r.tags),
        )

    def list_rules(self) -> list[AlertRule]:
        """List all rules (copies)."""
        return [self.get_rule(name) for name in self._rules]

    # ── Handler management ─────────────────────────────────────────

    def add_handler(self, handler: AlertHandler) -> None:
        """Register a custom alert handler."""
        self._handlers.append(handler)

    # ── Evaluation ─────────────────────────────────────────────────

    def evaluate(self, state: dict) -> list[Alert]:
        """Evaluate all rules against a state snapshot, firing alerts.

        Args:
            state: Dict with metric keys (e.g. 'success_rate', 'task_failures',
                   'active_ministers', 'scheduler_running').

        Returns:
            List of newly fired alerts.
        """
        import time

        now = time.time()
        fired: list[Alert] = []

        for rule in self._rules.values():
            if not rule.enabled:
                continue

            # Cooldown check
            last = self._last_fired.get(rule.name, 0)
            if now - last < rule.cooldown_seconds:
                continue

            # Get metric value
            value = state.get(rule.metric)
            if value is None:
                continue  # metric not present → skip

            if not self._check(value, rule.operator, rule.threshold):
                continue

            # Fire
            alert = Alert(
                rule_name=rule.name,
                severity=rule.severity,
                message=rule.message or f"{rule.metric} {rule.operator} {rule.threshold} (got {value})",
                metric=rule.metric,
                current_value=value,
                threshold=rule.threshold,
                operator=rule.operator,
                timestamp=now,
            )
            fired.append(alert)
            self._last_fired[rule.name] = now

            # Dispatch
            for handler in self._handlers:
                try:
                    handler(alert)
                except Exception:
                    logger.exception("[Alerts] Handler failed for %s", rule.name)

            # Archive
            self._fired_history.append(alert)
            if len(self._fired_history) > 200:
                self._fired_history = self._fired_history[-100:]

            # Persist to database
            if self._db is not None:
                try:
                    self._db.save_alert(
                        rule_name=alert.rule_name,
                        level=alert.severity,
                        message=alert.message,
                    )
                except Exception:
                    logger.exception(
                        "[Alerts] Failed to persist alert '%s' to database",
                        alert.rule_name,
                    )

        return fired

    # ── Built-in rules ──────────────────────────────────────────────

    def ensure_builtin_rules(self, emperor: Any = None) -> None:
        """Register built-in health-check rules. Idempotent — repeated calls
        do not duplicate.

        Rules registered:
            minister_depletion  — active ministers < 3 → WARNING
            task_failure_spike  — failure rate > 50% → ERROR
            evolution_stagnation — 3 consecutive evolutions with no merit gain → WARNING
        """
        if "minister_depletion" not in self._builtin_rules:
            self._builtin_rules["minister_depletion"] = {
                "condition": lambda e: len(e.court.active_ministers) < 3,
                "severity": "warning",
                "message": "Active ministers dropped below 3 (current: {active})",
                "enabled": True,
                "cooldown_seconds": 60.0,
            }

        if "task_failure_spike" not in self._builtin_rules:
            self._builtin_rules["task_failure_spike"] = {
                "condition": _failure_spike_condition,
                "severity": "error",
                "message": "Task failure rate is {rate:.0%} (>50% threshold)",
                "enabled": True,
                "cooldown_seconds": 60.0,
            }

        if "evolution_stagnation" not in self._builtin_rules:
            self._builtin_rules["evolution_stagnation"] = {
                "condition": _evolution_stagnation_condition,
                "severity": "warning",
                "message": "No merit improvement in the last 3 consecutive evolutions",
                "enabled": True,
                "cooldown_seconds": 120.0,
            }

        logger.debug("[Alerts] Built-in rules registered: %d",
                     len(self._builtin_rules))

    def fire_rule(self, rule_name: str, emperor: Any) -> Optional[Alert]:
        """Evaluate a built-in rule's condition and fire an alert if triggered.

        Args:
            rule_name: Name of the built-in rule to evaluate.
            emperor: Emperor instance (passed to the condition callable).

        Returns:
            Alert if triggered and not in cooldown, else None.
        """
        import time

        rule = self._builtin_rules.get(rule_name)
        if rule is None:
            logger.warning("[Alerts] Unknown built-in rule: %s", rule_name)
            return None
        if not rule.get("enabled", True):
            return None

        # Cooldown check
        now = time.time()
        last = self._last_fired.get(rule_name, 0)
        if now - last < rule.get("cooldown_seconds", 60.0):
            return None

        # Evaluate condition
        try:
            triggered = rule["condition"](emperor)
        except Exception:
            logger.exception("[Alerts] Built-in rule '%s' condition failed", rule_name)
            return None

        if not triggered:
            return None

        # Build message with format substitution
        raw_msg = rule.get("message", rule_name)
        try:
            message = _format_builtin_message(raw_msg, rule_name, emperor)
        except Exception:
            message = raw_msg

        alert = Alert(
            rule_name=rule_name,
            severity=rule.get("severity", "warning"),
            message=message,
            metric="",
            current_value=0.0,
            threshold=0.0,
            operator="",
            timestamp=now,
        )
        self._last_fired[rule_name] = now

        # Dispatch to handlers
        for handler in self._handlers:
            try:
                handler(alert)
            except Exception:
                logger.exception("[Alerts] Handler failed for %s", rule_name)

        # Archive
        self._fired_history.append(alert)
        if len(self._fired_history) > 200:
            self._fired_history = self._fired_history[-100:]

        logger.info("[Alerts] Built-in rule '%s' fired: %s", rule_name, message)

        # Persist alert to database
        if self._db is not None:
            try:
                self._db.save_alert(
                    rule_name=alert.rule_name,
                    level=alert.severity,
                    message=alert.message,
                )
            except Exception:
                logger.exception("[Alerts] Failed to persist alert to database")

        return alert

    def list_builtin_rules(self) -> list[str]:
        """Return names of all registered built-in rules."""
        return list(self._builtin_rules.keys())

    # ── History ────────────────────────────────────────────────────

    def history(self, limit: int = 20) -> list[Alert]:
        """Return recent alert history (newest first)."""
        return list(reversed(self._fired_history[-limit:]))

    def clear_history(self) -> None:
        """Clear alert history."""
        self._fired_history.clear()

    # ── Static helpers ─────────────────────────────────────────────

    @staticmethod
    def _check(value: float, operator: str, threshold: float) -> bool:
        if operator == "lt":
            return value < threshold
        elif operator == "gt":
            return value > threshold
        elif operator == "eq":
            return value == threshold
        elif operator == "lte":
            return value <= threshold
        elif operator == "gte":
            return value >= threshold
        return False

    @staticmethod
    def _log_handler(alert: Alert) -> None:
        level = {
            "info": logging.INFO,
            "warning": logging.WARNING,
            "critical": logging.ERROR,
        }.get(alert.severity, logging.INFO)
        logger.log(level, "[ALERT %s] %s → %s (%.3f)",
                   alert.severity.upper(), alert.rule_name,
                   alert.message, alert.current_value)


# ══════════════════════════════════════════════════════════════════
# Built-in rule condition helpers
# ══════════════════════════════════════════════════════════════════


def _failure_spike_condition(emperor: Any) -> bool:
    """Return True if more than 50% of recent task outcomes are failures."""
    engine = getattr(emperor, "task_engine", None)
    if engine is None:
        return False
    outcomes = getattr(engine, "_outcomes", [])
    if not outcomes:
        return False
    failed = sum(1 for o in outcomes if not o.success)
    return (failed / len(outcomes)) > 0.5


def _evolution_stagnation_condition(emperor: Any) -> bool:
    """Return True if the last 3 evolution cycles show no merit improvement."""
    court = getattr(emperor, "court", None)
    if court is None:
        return False
    history = getattr(court, "history", None)
    if history is None:
        return False
    records = getattr(history, "_records", [])
    if len(records) < 4:
        return False
    merits = [r.merit_mean for r in records[-4:]]
    # Three consecutive non-improving transitions
    return (merits[1] <= merits[0] and
            merits[2] <= merits[1] and
            merits[3] <= merits[2])


def _format_builtin_message(
    template: str, rule_name: str, emperor: Any,
) -> str:
    """Substitute placeholders in built-in alert messages.

    Supported placeholders:
        {active} — current active minister count
        {rate}   — task failure rate (float 0.0-1.0)
    """
    msg = template

    if "{active}" in msg:
        try:
            active = len(emperor.court.active_ministers)
            msg = msg.replace("{active}", str(active))
        except Exception:
            pass

    if "{rate}" in msg:
        try:
            outcomes = emperor.task_engine._outcomes
            if outcomes:
                failed = sum(1 for o in outcomes if not o.success)
                rate = failed / len(outcomes)
            else:
                rate = 0.0
            msg = msg.replace("{rate:.0%}", f"{rate:.0%}")
        except Exception:
            pass

    return msg

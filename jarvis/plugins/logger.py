"""LoggingPlugin — Structured JSON-Lines logging of Emperor lifecycle events.

Writes compact, machine-readable event logs that are ideal for
downstream analytics, debugging, and audit trails.  Supports
automatic log rotation by file size.

Usage:
    from jarvis.plugins import LoggingPlugin

    plugin = LoggingPlugin(log_path="logs/emperor.jsonl", max_bytes=10**7)
    emperor.plugins.register(plugin)
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from jarvis.plugin import Plugin


class LoggingPlugin(Plugin):
    """Structured JSON-lines logger for Emperor lifecycle events.

    Registered as a Plugin, it hooks every lifecycle event and writes
    one JSON line per event.  When the file exceeds *max_bytes* it
    renames the current file to ``<name>.1`` and opens a fresh one.

    Args:
        log_path: Path to the JSON-lines file (created if missing).
        max_bytes: Maximum file size before rotation.
        stdout: If True, also print each event to stdout.
        include_kwargs: When False, omit ``kwargs`` from log entries
            to keep file size small.
    """

    def __init__(
        self,
        log_path: str = "emperor_events.jsonl",
        *,
        max_bytes: int = 5 * 1024 * 1024,
        stdout: bool = False,
        include_kwargs: bool = True,
    ) -> None:
        self._path = Path(log_path)
        self._max_bytes = max_bytes
        self._stdout = stdout
        self._include_kwargs = include_kwargs
        self._lock = threading.Lock()
        self._ensure_file()

    # ── Plugin identity ─────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "LoggingPlugin"

    # ── Lifecycle hooks ─────────────────────────────────────────────

    def on_minister_register(self, **kw: Any) -> None:
        self._log("MINISTER_REGISTER", kw)

    def on_minister_deregister(self, **kw: Any) -> None:
        self._log("MINISTER_DEREGISTER", kw)

    def on_evolve_start(self, **kw: Any) -> None:
        self._log("EVOLVE_START", kw)

    def on_evolve_end(self, **kw: Any) -> None:
        self._log("EVOLVE_END", kw)

    def on_task_before(self, **kw: Any) -> None:
        self._log("TASK_BEFORE", kw)

    def on_task_after(self, **kw: Any) -> None:
        self._log("TASK_AFTER", kw)

    def on_task_error(self, **kw: Any) -> None:
        self._log("TASK_ERROR", kw)

    def on_system_alert(self, **kw: Any) -> None:
        self._log("SYSTEM_ALERT", kw)

    def on_healing(self, **kw: Any) -> None:
        self._log("HEALING", kw)

    def on_shutdown(self, **kw: Any) -> None:
        self._log("SHUTDOWN", kw)

    def on_startup(self, **kw: Any) -> None:
        self._log("STARTUP", kw)

    def on_config_change(self, **kw: Any) -> None:
        self._log("CONFIG_CHANGE", kw)

    def on_plugin_register(self, **kw: Any) -> None:
        self._log("PLUGIN_REGISTER", kw)

    def on_plugin_unregister(self, **kw: Any) -> None:
        self._log("PLUGIN_UNREGISTER", kw)

    # ── Internals ───────────────────────────────────────────────────

    def _ensure_file(self) -> None:
        """Create the log file (and parent dirs) if missing."""
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _log(self, event: str, kw: dict[str, Any]) -> None:
        import time as _time

        entry = {
            "ts": _time.time(),
            "event": event,
        }
        if self._include_kwargs and kw:
            # Serialize safely — skip non-serializable objects
            safe = {}
            for k, v in kw.items():
                try:
                    json.dumps({k: v}, default=str)
                    safe[k] = v
                except (TypeError, ValueError):
                    safe[k] = str(v)
            entry["kwargs"] = safe

        line = json.dumps(entry, ensure_ascii=False, default=str)

        with self._lock:
            if self._path.exists():
                size = self._path.stat().st_size
                if size >= self._max_bytes:
                    self._rotate()
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(line + "\n")

        if self._stdout:
            print(f"[{event}] {line}")

    def _rotate(self) -> None:
        """Rename current log to ``.1``, dropping any existing ``.1``."""
        rotated = self._path.with_suffix(self._path.suffix + ".1")
        if rotated.exists():
            rotated.unlink()
        self._path.rename(rotated)

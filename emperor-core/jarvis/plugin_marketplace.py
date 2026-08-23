"""Plugin Marketplace — plugin discovery, install, enable/disable, uninstall.

Provides ``PluginMarketplace``, a local registry of built-in plugins with
persistent install state stored in ``data_dir/plugins.json``.
"""

from __future__ import annotations

import json
import logging
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("jarvis.plugin_marketplace")

# ══════════════════════════════════════════════════════════════════
# Built-in plugin registry
# ══════════════════════════════════════════════════════════════════

_BUILTIN_PLUGINS: list[dict[str, Any]] = [
    {
        "id": "weather-alert",
        "name": "极端天气预警",
        "version": "1.0.0",
        "description": "基于 weather capability 的极端天气预警插件，当温度超过阈值时自动发出警报通知。",
        "author": "Jarvis Team",
        "capabilities_used": ["weather"],
        "config": {"threshold": 35},
    },
    {
        "id": "daily-digest",
        "name": "每日智能摘要",
        "version": "1.0.0",
        "description": "汇总昨日任务执行情况、天气信息和当日新闻头条，生成每日智能摘要报告。",
        "author": "Jarvis Team",
        "capabilities_used": ["weather", "news"],
        "config": {"hour": 8, "city": "北京"},
    },
    {
        "id": "code-reviewer",
        "name": "代码审查助手",
        "version": "1.0.0",
        "description": "对提交的代码片段进行 best-practice 审查，给出改进建议和潜在问题分析。",
        "author": "Jarvis Team",
        "capabilities_used": ["code"],
        "config": {"strict_mode": True, "max_lines": 500},
    },
    {
        "id": "meeting-notes",
        "name": "会议纪要生成器",
        "version": "1.0.0",
        "description": "自动生成结构化会议记录，包括与会人员、议题、决议和待办事项。",
        "author": "Jarvis Team",
        "capabilities_used": ["text", "language"],
        "config": {"template": "standard", "language": "zh-CN"},
    },
    {
        "id": "stock-watch",
        "name": "股票监控",
        "version": "1.0.0",
        "description": "定时查询指定股票实时价格，支持涨跌幅阈值预警通知。",
        "author": "Jarvis Team",
        "capabilities_used": ["web_search", "web_fetch"],
        "config": {"symbols": ["AAPL", "GOOGL"], "alert_threshold_pct": 5.0},
    },
    {
        "id": "habit-tracker",
        "name": "习惯追踪",
        "version": "1.0.0",
        "description": "记录并分析用户日常习惯，生成周报和月度趋势分析图表。",
        "author": "Jarvis Team",
        "capabilities_used": ["data"],
        "config": {"habits": [], "reminder_enabled": True},
    },
]


class PluginMarketplace:
    """Local plugin marketplace with built-in registry and persistent state.

    Plugin metadata is defined inside the code (no external requests).
    Installed plugins and their configurations are persisted to
    ``data_dir/plugins.json``.

    Usage::

        mp = PluginMarketplace(data_dir="./data")
        mp.install("weather-alert")
        mp.enable("weather-alert")
        print(mp.report())
    """

    def __init__(self, data_dir: str = "") -> None:
        self._registry: dict[str, dict[str, Any]] = {
            p["id"]: deepcopy(p) for p in _BUILTIN_PLUGINS
        }
        self._installed: dict[str, dict[str, Any]] = {}
        self._data_dir = data_dir or os.path.join(os.getcwd(), "data")
        self._state_path = os.path.join(self._data_dir, "plugins.json")
        self._load_state()

    # ── Persistence ────────────────────────────────────────────────

    def _load_state(self) -> None:
        """Restore installed plugins and config from disk."""
        if not os.path.isfile(self._state_path):
            return
        try:
            with open(self._state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for item in data.get("installed", []):
                pid = item.get("id", "")
                if pid in self._registry:
                    self._installed[pid] = item
                else:
                    logger.warning(
                        "[PluginMarketplace] Unknown plugin %r in state, ignoring", pid
                    )
            logger.info(
                "[PluginMarketplace] Loaded %d installed plugins from %s",
                len(self._installed),
                self._state_path,
            )
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("[PluginMarketplace] Failed to load state: %s", exc)

    def _save_state(self) -> None:
        """Persist installed plugins and config to disk."""
        os.makedirs(self._data_dir, exist_ok=True)
        try:
            with open(self._state_path, "w", encoding="utf-8") as f:
                json.dump(
                    {"installed": list(self._installed.values())},
                    f,
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                )
        except OSError as exc:
            logger.error("[PluginMarketplace] Failed to save state: %s", exc)

    # ── Query ──────────────────────────────────────────────────────

    def list_available(self) -> list[dict[str, Any]]:
        """Return all available plugins (built-in registry)."""
        result: list[dict[str, Any]] = []
        for pid, meta in self._registry.items():
            item = deepcopy(meta)
            item["enabled"] = self._installed.get(pid, {}).get("enabled", False)
            item["installed"] = pid in self._installed
            if pid in self._installed:
                item["installed_at"] = self._installed[pid].get("installed_at", "")
                item["config"] = self._installed[pid].get("config", meta.get("config", {}))
            result.append(item)
        return result

    def get_installed(self) -> list[dict[str, Any]]:
        """Return only installed plugins."""
        return [
            {
                **deepcopy(self._registry[pid]),
                "enabled": info.get("enabled", False),
                "installed_at": info.get("installed_at", ""),
                "config": info.get("config", {}),
            }
            for pid, info in self._installed.items()
            if pid in self._registry
        ]

    def report(self) -> dict[str, Any]:
        """Return statistical summary."""
        total = len(self._registry)
        installed = len(self._installed)
        enabled = sum(1 for info in self._installed.values() if info.get("enabled"))
        return {
            "total": total,
            "marketplace": total,
            "installed": installed,
            "enabled": enabled,
            "available": self.list_available(),
            "installed_list": self.get_installed(),
        }

    # ── lifecycle ──────────────────────────────────────────────────

    def install(self, plugin_id: str) -> dict[str, Any]:
        """Install a plugin by its id.

        Returns:
            dict with status and plugin info.
        """
        meta = self._registry.get(plugin_id)
        if meta is None:
            raise ValueError(f"Plugin '{plugin_id}' not found in marketplace")

        if plugin_id in self._installed:
            return {"status": "already_installed", "plugin_id": plugin_id}

        now = datetime.now(timezone.utc).isoformat()
        self._installed[plugin_id] = {
            "id": plugin_id,
            "enabled": True,
            "installed_at": now,
            "config": deepcopy(meta.get("config", {})),
        }
        self._save_state()
        logger.info("[PluginMarketplace] Installed plugin %r", plugin_id)
        return {
            "status": "installed",
            "plugin_id": plugin_id,
            "installed_at": now,
        }

    def uninstall(self, plugin_id: str) -> dict[str, Any]:
        """Uninstall a plugin.

        Returns:
            dict with status.
        """
        if plugin_id not in self._installed:
            raise ValueError(f"Plugin '{plugin_id}' is not installed")

        del self._installed[plugin_id]
        self._save_state()
        logger.info("[PluginMarketplace] Uninstalled plugin %r", plugin_id)
        return {"status": "uninstalled", "plugin_id": plugin_id}

    def enable(self, plugin_id: str) -> dict[str, Any]:
        """Enable a previously installed plugin."""
        if plugin_id not in self._installed:
            raise ValueError(f"Plugin '{plugin_id}' is not installed")

        self._installed[plugin_id]["enabled"] = True
        self._save_state()
        logger.info("[PluginMarketplace] Enabled plugin %r", plugin_id)
        return {"status": "enabled", "plugin_id": plugin_id}

    def disable(self, plugin_id: str) -> dict[str, Any]:
        """Disable an installed plugin without uninstalling."""
        if plugin_id not in self._installed:
            raise ValueError(f"Plugin '{plugin_id}' is not installed")

        self._installed[plugin_id]["enabled"] = False
        self._save_state()
        logger.info("[PluginMarketplace] Disabled plugin %r", plugin_id)
        return {"status": "disabled", "plugin_id": plugin_id}

    def get_config(self, plugin_id: str) -> dict[str, Any]:
        """Read plugin configuration."""
        if plugin_id in self._installed:
            return deepcopy(self._installed[plugin_id].get("config", {}))
        meta = self._registry.get(plugin_id)
        if meta is None:
            raise ValueError(f"Plugin '{plugin_id}' not found")
        return deepcopy(meta.get("config", {}))

    def set_config(self, plugin_id: str, config: dict[str, Any]) -> dict[str, Any]:
        """Write plugin configuration.

        Works for both installed and uninstalled plugins. For uninstalled
        plugins the config is stored against the registry default (not
        persisted to disk until installed).
        """
        if plugin_id not in self._registry:
            raise ValueError(f"Plugin '{plugin_id}' not found in marketplace")

        if plugin_id in self._installed:
            self._installed[plugin_id]["config"] = deepcopy(config)
            self._save_state()
        else:
            # Update registry default (in-memory only, not persisted)
            self._registry[plugin_id]["config"] = deepcopy(config)

        return {"status": "ok", "plugin_id": plugin_id, "config": config}

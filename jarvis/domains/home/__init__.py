"""
Home Domain — smart device control, climate, scenes, energy monitoring.
"""

from __future__ import annotations

import re
from typing import Any
from jarvis.core.orchestrator import Domain, DomainModule, Intent, TaskResult


DOMAIN = Domain.HOME

CAPABILITIES = [
    "light_control", "climate_control", "scene_activation",
    "energy_monitoring", "appliance_control", "security_camera",
]


class DomainModule(DomainModule):
    """Home domain handler."""

    domain = Domain.HOME
    capabilities = CAPABILITIES

    async def handle(self, intent: Intent) -> TaskResult:
        text = intent.raw_text.lower()
        raw = intent.raw_text

        if "灯光" in text or "灯" in text or "light" in text:
            data: dict[str, Any] = {"action": "on" if "开" in text else "off"}
            if "客厅" in raw:
                data["device"] = "客厅"
            elif "卧室" in raw:
                data["device"] = "卧室"
            elif "书房" in raw:
                data["device"] = "书房"
            else:
                data["device"] = "全部"
        elif "空调" in text or "温度" in text or "climate" in text:
            if "卧室" in raw:
                data = {"room": "卧室"}
            elif "客厅" in raw:
                data = {"room": "客厅"}
            else:
                data = {"room": "全部"}
            # Extract temperature number
            temps = re.findall(r'(\d+)\s*度', raw)
            data["target_temp"] = int(temps[0]) if temps else 24
        elif "场景" in text or "模式" in text or "scene" in text:
            if "睡眠" in text or "sleep" in text:
                data = {"scene": "sleep"}
            elif "离家" in text:
                data = {"scene": "away"}
            else:
                data = {"scene": "home"}
        elif "能源" in text or "energy" in text or "消耗" in text:
            data = {"period": "monthly" if "月" in text else "daily"}
        else:
            data = {"action": "status"}

        return TaskResult(domain=Domain.HOME, success=True, output=f"[HOME] Executing: {intent.raw_text}", data=data)

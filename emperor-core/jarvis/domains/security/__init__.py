"""
Security Domain — scanning, monitoring, encryption, vulnerability assessment.
"""

from __future__ import annotations

from typing import Any
from jarvis.core.orchestrator import Domain, DomainModule, Intent, TaskResult
from jarvis.core.llm import get_llm


DOMAIN = Domain.SECURITY

CAPABILITIES = [
    "port_scan", "system_monitor", "file_encryption",
    "vulnerability_scan", "firewall_config", "log_analysis", "threat_detection",
]


class DomainModule(DomainModule):
    """Security domain handler."""

    domain = Domain.SECURITY
    capabilities = CAPABILITIES

    async def handle(self, intent: Intent) -> TaskResult:
        text = intent.raw_text.lower()

        if "端口" in text or "scan" in text:
            data: dict[str, Any] = {"targets": ["ports", "services"]}
        elif "监控" in text or "monitor" in text:
            data = {"metrics": ["cpu", "memory", "disk", "network"]}
        elif "加密" in text or "encrypt" in text:
            data = {"algorithm": "AES-256-GCM"}
        elif "漏洞" in text or "vuln" in text:
            data = {"severity_filter": "high_and_critical"}
        else:
            data = {"targets": ["general"]}

        llm = get_llm()
        output = await llm.complete(intent.raw_text, domain="security")
        return TaskResult(domain=Domain.SECURITY, success=True, output=output, data=data)

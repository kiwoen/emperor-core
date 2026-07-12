"""
Security & Monitoring Domain.

Handles: system monitoring, threat detection, encryption, access control,
audit logging, intrusion detection, vulnerability scanning, compliance.
"""

from __future__ import annotations

DOMAIN = "security"

CAPABILITIES = [
    "system_monitor", "threat_scan", "encrypt_file",
    "access_audit", "vulnerability_scan", "intrusion_detect",
    "log_analysis", "compliance_check", "network_scan",
    "password_audit",
]

import logging

logger = logging.getLogger("jarvis.domain.security")


class DomainModule:
    """Security domain — monitoring, threat detection, encryption."""

    def __init__(self, orchestrator):
        self.orchestrator = orchestrator

    async def handle(self, intent):
        from jarvis.core.orchestrator import TaskResult, Domain
        from datetime import datetime, timezone

        action = intent.action
        raw = intent.raw_text

        handlers = {
            "漏洞": self._handle_vuln,
            "入侵": self._handle_intrusion,
            "扫描": self._handle_scan,
            "监控": self._handle_monitor,
            "加密": self._handle_encrypt,
            "审计": self._handle_audit,
            "日志": self._handle_log,
            "合规": self._handle_compliance,
            "密码": self._handle_password,
        }

        for keyword, handler in handlers.items():
            if keyword in action or keyword in raw:
                return await handler(raw)

        return TaskResult(
            domain=Domain.SECURITY,
            success=True,
            output=f"Security intent logged: {raw[:100]}",
            memory_keys=["security_query"],
        )

    async def _handle_scan(self, raw):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.SECURITY,
            success=True,
            output=f"Security scan initiated: {raw[:150]}",
            data={
                "scan_type": "comprehensive",
                "targets": self._detect_scan_targets(raw),
                "report_format": "json",
            },
            memory_keys=["security_scan"],
        )

    async def _handle_monitor(self, raw):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.SECURITY,
            success=True,
            output=f"Monitoring activated: {raw[:150]}",
            data={
                "metrics": ["cpu", "memory", "network", "disk_io", "processes"],
                "alert_threshold": "default",
                "interval": "30s",
            },
            memory_keys=["security_monitor"],
        )

    async def _handle_encrypt(self, raw):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.SECURITY,
            success=True,
            output=f"Encryption task: {raw[:150]}",
            data={
                "algorithm": "AES-256-GCM",
                "key_management": "local_secure_store",
            },
            memory_keys=["security_encrypt"],
        )

    async def _handle_audit(self, raw):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.SECURITY,
            success=True,
            output=f"Audit initiated: {raw[:150]}",
            data={
                "audit_type": "access_control",
                "scope": "30_days",
                "format": "report",
            },
            memory_keys=["security_audit"],
        )

    async def _handle_vuln(self, raw):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.SECURITY,
            success=True,
            output=f"Vulnerability scan: {raw[:150]}",
            data={
                "scan_type": "cve_database",
                "severity_filter": "high_and_critical",
            },
            memory_keys=["security_vuln"],
        )

    async def _handle_intrusion(self, raw):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.SECURITY,
            success=True,
            output=f"Intrusion analysis: {raw[:150]}",
            data={
                "detection_mode": "anomaly_based",
                "response": "alert_and_log",
            },
            memory_keys=["security_intrusion"],
        )

    async def _handle_log(self, raw):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.SECURITY,
            success=True,
            output=f"Log analysis: {raw[:150]}",
            data={
                "log_sources": ["system", "auth", "application"],
                "analysis_type": "pattern_detection",
            },
            memory_keys=["security_log"],
        )

    async def _handle_compliance(self, raw):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.SECURITY,
            success=True,
            output=f"Compliance check: {raw[:150]}",
            data={
                "standards": ["ISO27001", "GDPR", "SOC2"],
                "check_type": "automated",
            },
            memory_keys=["security_compliance"],
        )

    async def _handle_password(self, raw):
        from jarvis.core.orchestrator import TaskResult, Domain

        return TaskResult(
            domain=Domain.SECURITY,
            success=True,
            output=f"Password audit: {raw[:150]}",
            data={
                "check": ["strength", "reuse", "age", "breach_database"],
                "policy": "NIST_SP_800-63B",
            },
            memory_keys=["security_password"],
        )

    def _detect_scan_targets(self, raw: str) -> list[str]:
        targets = []
        lower = raw.lower()
        if any(w in lower for w in ["端口", "port"]):
            targets.append("ports")
        if any(w in lower for w in ["网络", "network"]):
            targets.append("network")
        if any(w in lower for w in ["文件", "file"]):
            targets.append("filesystem")
        if any(w in lower for w in ["进程", "process"]):
            targets.append("processes")
        return targets or ["full_system"]

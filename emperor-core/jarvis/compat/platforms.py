"""Platform detector — OS, chip architecture, Python version & compatibility.

Supports:
    - OS detection: Windows / Linux / Kylin (麒麟) / UOS (统信)
    - Chip architecture: x86_64 / aarch64 (ARM64)
    - Python version and dependency compatibility checks
    - Compatibility report generation
"""

from __future__ import annotations

import logging
import os
import platform
import sys
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("jarvis.compat.platforms")


class OSType(str):
    """Known operating system identifiers."""

    WINDOWS = "windows"
    LINUX = "linux"
    KYLIN = "kylin"
    UOS = "uos"
    UNKNOWN = "unknown"


class ChipArch(str):
    """Known chip architectures."""

    X86_64 = "x86_64"
    AARCH64 = "aarch64"
    UNKNOWN = "unknown"


# ── Dependency compatibility table ───────────────────────────────────

# (pkg_name, min_version_str, notes)
_COMPAT_DEPENDENCIES: dict[str, list[tuple[str, str, str]]] = {
    OSType.WINDOWS: [
        ("torch", "2.0.0", "PyTorch for CUDA / CPU"),
        ("torch_npu", "2.1.0", "PyTorch for Huawei Ascend NPU"),
        ("torch_mlu", "1.16.0", "PyTorch for Cambricon MLU"),
    ],
    OSType.LINUX: [
        ("torch", "2.0.0", "PyTorch for CUDA / CPU"),
        ("torch_npu", "2.1.0", "PyTorch for Huawei Ascend NPU"),
        ("torch_mlu", "1.16.0", "PyTorch for Cambricon MLU"),
    ],
    OSType.KYLIN: [
        ("torch", "2.0.0", "PyTorch (x86_64 / aarch64)"),
        ("torch_npu", "2.1.0", "PyTorch for Huawei Ascend NPU"),
    ],
    OSType.UOS: [
        ("torch", "2.0.0", "PyTorch (x86_64 / aarch64)"),
        ("torch_npu", "2.1.0", "PyTorch for Huawei Ascend NPU"),
    ],
}


# ── OS release files for Linux variants ──────────────────────────────

_KYLIN_RELEASE_FILES = [
    "/etc/kylin-release",
    "/etc/neokylin-release",
    "/etc/.kyinfo",
]

_UOS_RELEASE_FILES = [
    "/etc/uos-release",
    "/etc/deepin-version",
    "/etc/deepin-release",
]


@dataclass
class PlatformInfo:
    """Structured platform detection result."""

    os_type: str = OSType.UNKNOWN
    os_version: str = ""
    os_pretty_name: str = ""
    chip_arch: str = ChipArch.UNKNOWN
    python_version: str = ""
    python_implementation: str = ""
    is_compatible: bool = True
    warnings: list[str] = field(default_factory=list)
    dependencies: dict[str, dict[str, Any]] = field(default_factory=dict)


class PlatformDetector:
    """Detects platform details and produces a compatibility report.

    Usage::

        detector = PlatformDetector()
        info = detector.detect()
        report = detector.generate_report()
        print(report["summary"])
    """

    def __init__(self) -> None:
        self._cached_info: Optional[PlatformInfo] = None

    # ── Detection ───────────────────────────────────────────────────

    def detect(self, refresh: bool = False) -> PlatformInfo:
        """Run full platform detection. Results are cached unless refresh=True."""
        if self._cached_info is not None and not refresh:
            return self._cached_info

        info = PlatformInfo()
        info.os_type = self._detect_os()
        info.os_version = self._detect_os_version()
        info.os_pretty_name = self._detect_os_pretty_name()
        info.chip_arch = self._detect_chip_arch()
        info.python_version = platform.python_version()
        info.python_implementation = platform.python_implementation()
        info.dependencies = self._check_dependencies(info.os_type)
        info.warnings = self._collect_warnings(info)

        info.is_compatible = len(info.warnings) == 0
        self._cached_info = info
        return info

    def _detect_os(self) -> str:
        system = platform.system()
        if system == "Windows":
            return OSType.WINDOWS
        if system == "Linux":
            return self._detect_linux_variant()
        return OSType.UNKNOWN

    def _detect_linux_variant(self) -> str:
        for fpath in _KYLIN_RELEASE_FILES:
            if os.path.isfile(fpath):
                return OSType.KYLIN
        for fpath in _UOS_RELEASE_FILES:
            if os.path.isfile(fpath):
                return OSType.UOS
        # Check /etc/os-release for ID hints
        try:
            with open("/etc/os-release", "r", encoding="utf-8") as f:
                content = f.read().lower()
                if "kylin" in content:
                    return OSType.KYLIN
                if "uos" in content or "deepin" in content:
                    return OSType.UOS
        except (OSError, PermissionError):
            pass
        return OSType.LINUX

    def _detect_os_version(self) -> str:
        system = platform.system()
        if system == "Windows":
            return platform.release()
        if system == "Linux":
            try:
                with open("/etc/os-release", "r", encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("VERSION_ID="):
                            return line.strip().split("=", 1)[1].strip('"').strip("'")
            except (OSError, PermissionError):
                pass
            return platform.release()
        return ""

    def _detect_os_pretty_name(self) -> str:
        system = platform.system()
        if system == "Windows":
            return f"Windows {platform.release()}"
        if system == "Linux":
            try:
                with open("/etc/os-release", "r", encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("PRETTY_NAME="):
                            return line.strip().split("=", 1)[1].strip('"').strip("'")
            except (OSError, PermissionError):
                pass
            return f"Linux {platform.release()}"
        return platform.system()

    def _detect_chip_arch(self) -> str:
        machine = platform.machine().lower()
        if machine in ("x86_64", "amd64", "i386", "i686"):
            return ChipArch.X86_64
        if machine in ("aarch64", "arm64", "armv7l", "armv8l"):
            return ChipArch.AARCH64
        return ChipArch.UNKNOWN

    # ── Dependency checks ───────────────────────────────────────────

    def _check_dependencies(self, os_type: str) -> dict[str, dict[str, Any]]:
        deps: dict[str, dict[str, Any]] = {}
        entries = _COMPAT_DEPENDENCIES.get(os_type, _COMPAT_DEPENDENCIES.get(OSType.LINUX, []))
        for pkg, min_ver, notes in entries:
            status = self._check_package_version(pkg, min_ver)
            deps[pkg] = {
                "required": f">={min_ver}",
                "installed": status.get("version", ""),
                "ok": status.get("ok", False),
                "notes": notes,
            }
        return deps

    @staticmethod
    def _check_package_version(pkg_name: str, min_version: str) -> dict[str, Any]:
        """Check if a Python package is installed and meets minimum version."""
        try:
            mod = __import__(pkg_name)
            version = getattr(mod, "__version__", "unknown")
        except ImportError:
            return {"version": "not installed", "ok": False}

        try:
            from packaging.version import Version
            ok = Version(version) >= Version(min_version)
        except Exception:
            # If packaging not available or version string is non-standard
            ok = version != "unknown"
        return {"version": version, "ok": ok}

    # ── Warnings ────────────────────────────────────────────────────

    def _collect_warnings(self, info: PlatformInfo) -> list[str]:
        warnings: list[str] = []

        # Python version check
        py_ver = sys.version_info
        if py_ver < (3, 9):
            warnings.append(f"Python {py_ver.major}.{py_ver.minor} is below minimum recommended version 3.9")
        if py_ver >= (3, 13):
            warnings.append(f"Python {py_ver.major}.{py_ver.minor}: some packages may not yet have stable wheels")

        # OS compatibility
        if info.os_type == OSType.UNKNOWN:
            warnings.append(f"Unknown operating system: {platform.system()}")

        # Architecture compatibility
        if info.chip_arch == ChipArch.UNKNOWN:
            warnings.append(f"Unknown chip architecture: {platform.machine()}")

        # Dependency warnings
        for pkg, dep_info in info.dependencies.items():
            if not dep_info["ok"] and dep_info["installed"] != "not installed":
                warnings.append(f"{pkg} {dep_info['installed']} is below required {dep_info['required']}")

        return warnings

    # ── Report ──────────────────────────────────────────────────────

    def generate_report(self, refresh: bool = False) -> dict[str, Any]:
        """Generate a full compatibility report as a dict.

        Returns:
            dict with keys: ``summary``, ``os``, ``chip``, ``python``,
            ``dependencies``, ``warnings``, ``is_compatible``.
        """
        info = self.detect(refresh=refresh)
        return {
            "summary": (
                f"{info.os_pretty_name} · {info.chip_arch} · "
                f"Python {info.python_version} · "
                f"{'compatible' if info.is_compatible else 'warnings present'}"
            ),
            "os": {
                "type": info.os_type,
                "version": info.os_version,
                "pretty_name": info.os_pretty_name,
            },
            "chip": {
                "arch": info.chip_arch,
                "machine": platform.machine(),
            },
            "python": {
                "version": info.python_version,
                "implementation": info.python_implementation,
                "executable": sys.executable,
            },
            "dependencies": info.dependencies,
            "warnings": info.warnings,
            "is_compatible": info.is_compatible,
        }

    def print_report(self) -> None:
        """Print a human-readable compatibility report to stdout."""
        info = self.detect()
        print("=" * 60)
        print("  Platform Compatibility Report")
        print("=" * 60)
        print(f"  OS       : {info.os_pretty_name} ({info.os_type})")
        print(f"  Chip     : {info.chip_arch} (machine: {platform.machine()})")
        print(f"  Python   : {info.python_version} ({info.python_implementation})")
        print(f"  Exec     : {sys.executable}")
        print("-" * 60)
        if info.dependencies:
            print("  Dependencies:")
            for pkg, dep in info.dependencies.items():
                status = "OK" if dep["ok"] else "MISSING / LOW"
                print(f"    {pkg:20s}  required>={dep['required']:10s}  installed={dep['installed']:14s}  [{status}]")
        if info.warnings:
            print("-" * 60)
            print("  Warnings:")
            for w in info.warnings:
                print(f"    - {w}")
        print("-" * 60)
        print(f"  Verdict  : {'COMPATIBLE' if info.is_compatible else 'WARNINGS PRESENT'}")
        print("=" * 60)

"""Compatibility module — domestic compute & Xinchuang adaptation.

Exports:
    - ``ComputeAdapter`` — auto-detect optimal compute device and LLM API endpoint.
    - ``PlatformDetector`` — OS / chip / Python version detection and compatibility report.
    - ``ComputeDevice`` — enum of detected compute device types.
"""

from huanxin.compat.adapter import ComputeAdapter, ComputeDevice
from huanxin.compat.platforms import PlatformDetector

__all__ = ["ComputeAdapter", "ComputeDevice", "PlatformDetector"]

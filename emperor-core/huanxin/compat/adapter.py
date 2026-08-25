"""Compute adapter — unified compute device detection and LLM API routing.

Supports:
    - NVIDIA CUDA
    - Huawei Ascend NPU (CANN)
    - Cambricon MLU
    - CPU fallback

Domestic model API routing:
    - Zhipu GLM   (zhipu)
    - Baidu ERNIE  (baidu)
    - Aliyun Tongyi (aliyun)
    - Xunfei Spark (xunfei)
    - Huawei Pangu  (huawei)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger("huanxin.compat.adapter")


class ComputeDevice(str, Enum):
    """Detected compute device types."""

    CUDA = "cuda"          # NVIDIA GPU via CUDA
    ASCEND = "ascend"      # Huawei Ascend NPU via CANN
    MLU = "mlu"            # Cambricon MLU
    CPU = "cpu"            # CPU fallback


# ── Domestic model API endpoint defaults ─────────────────────────────

_DOMESTIC_ENDPOINTS: dict[str, str] = {
    "zhipu":   "https://open.bigmodel.cn/api/paas/v4/chat/completions",
    "baidu":   "https://aip.baidubce.com/rpc/2.0/ai_custom/v1/wenxinworkshop/chat/completions",
    "aliyun":  "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
    "xunfei":  "https://spark-api-open.xf-yun.com/v1/chat/completions",
    "huawei":  "https://pangu-alpha.cn-north-4.myhuaweicloud.com/v1/chat/completions",
}

_DOMESTIC_MODEL_DEFAULTS: dict[str, str] = {
    "zhipu":   "glm-4-flash",
    "baidu":   "ernie-4.0-turbo-8k",
    "aliyun":  "qwen-turbo",
    "xunfei":  "spark-lite",
    "huawei":  "pangu-alpha",
}


# ── Precedence ordered env vars ──────────────────────────────────────

_DEVICE_ENV_KEYS: dict[ComputeDevice, list[str]] = {
    ComputeDevice.CUDA:   ["CUDA_VISIBLE_DEVICES", "CUDA_HOME", "CUDA_PATH"],
    ComputeDevice.ASCEND: ["ASCEND_HOME", "ASCEND_TOOLKIT_HOME", "ASCEND_PYTHON_PATH"],
    ComputeDevice.MLU:    ["MLU_HOME", "NEUWARE_HOME", "MLU_VISIBLE_DEVICES"],
}


@dataclass
class AdapterConfig:
    """Configuration for the compute adapter.

    Attributes:
        prefer_device: Force a specific device ("" for auto-detect).
        device_fallback: Fallback device when preferred is unavailable.
        endpoint_overrides: Dict of provider → base_url overrides for domestic APIs.
    """

    prefer_device: str = ""
    device_fallback: ComputeDevice = ComputeDevice.CPU
    endpoint_overrides: dict[str, str] = field(default_factory=dict)


class ComputeAdapter:
    """Auto-detects available compute devices and routes LLM API endpoints.

    Detection priority: CUDA → Ascend → MLU → CPU.

    Usage::

        adapter = ComputeAdapter()
        device = adapter.get_optimal_device()
        # device == ComputeDevice.CUDA  (if NVIDIA GPU available)

        endpoint = adapter.get_endpoint("zhipu")
        # "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    """

    def __init__(self, config: Optional[AdapterConfig] = None) -> None:
        self.config = config or AdapterConfig()
        self._device_cache: Optional[ComputeDevice] = None

    # ── Device detection ────────────────────────────────────────────

    def get_optimal_device(self) -> ComputeDevice:
        """Return the best available compute device, with caching."""
        if self._device_cache is not None:
            return self._device_cache

        if self.config.prefer_device:
            try:
                preferred = ComputeDevice(self.config.prefer_device)
            except ValueError:
                logger.warning("Unknown prefer_device '%s', falling back to CPU", self.config.prefer_device)
                self._device_cache = ComputeDevice.CPU
                return ComputeDevice.CPU

            if preferred is not None and preferred != ComputeDevice.CPU:
                if self._is_device_available(preferred):
                    self._device_cache = preferred
                    return preferred
                logger.warning("Preferred device %s not available, auto-detecting", preferred.value)

        # Priority: CUDA → Ascend → MLU → CPU
        for dev in (ComputeDevice.CUDA, ComputeDevice.ASCEND, ComputeDevice.MLU):
            if self._is_device_available(dev):
                self._device_cache = dev
                return dev

        self._device_cache = ComputeDevice.CPU
        return ComputeDevice.CPU

    def _is_device_available(self, device: ComputeDevice) -> bool:
        """Check whether a specific compute device is available."""
        if device == ComputeDevice.CUDA:
            return self._check_cuda()
        elif device == ComputeDevice.ASCEND:
            return self._check_ascend()
        elif device == ComputeDevice.MLU:
            return self._check_mlu()
        return True  # CPU always available

    def _check_cuda(self) -> bool:
        env_keys = _DEVICE_ENV_KEYS.get(ComputeDevice.CUDA, [])
        if any(os.environ.get(k) for k in env_keys):
            return True
        try:
            import torch
            return torch.cuda.is_available()
        except ImportError:
            pass
        try:
            import subprocess, shutil
            return shutil.which("nvidia-smi") is not None
        except Exception:
            pass
        return False

    def _check_ascend(self) -> bool:
        env_keys = _DEVICE_ENV_KEYS.get(ComputeDevice.ASCEND, [])
        if any(os.environ.get(k) for k in env_keys):
            return True
        try:
            import torch_npu
            return True
        except ImportError:
            pass
        try:
            import torch
            if hasattr(torch, "npu") and hasattr(torch.npu, "is_available"):
                return torch.npu.is_available()
        except (ImportError, Exception):
            pass
        return False

    def _check_mlu(self) -> bool:
        env_keys = _DEVICE_ENV_KEYS.get(ComputeDevice.MLU, [])
        if any(os.environ.get(k) for k in env_keys):
            return True
        try:
            import torch_mlu
            return True
        except ImportError:
            pass
        try:
            import torch
            if hasattr(torch, "mlu") and hasattr(torch.mlu, "is_available"):
                return torch.mlu.is_available()
        except (ImportError, Exception):
            pass
        return False

    # ── Device info ─────────────────────────────────────────────────

    def list_available_devices(self) -> list[ComputeDevice]:
        """Return all currently available compute devices."""
        available: list[ComputeDevice] = []
        for dev in (ComputeDevice.CUDA, ComputeDevice.ASCEND, ComputeDevice.MLU, ComputeDevice.CPU):
            if self._is_device_available(dev):
                available.append(dev)
        return available

    def get_device_info(self) -> dict[str, Any]:
        """Return a dict with device availability and optimal selection."""
        optimal = self.get_optimal_device()
        return {
            "optimal": optimal.value,
            "available": [d.value for d in self.list_available_devices()],
            "prefer": self.config.prefer_device or "auto",
        }

    # ── LLM API endpoint routing ────────────────────────────────────

    def get_endpoint(self, provider: str) -> str:
        """Return the default API endpoint for a domestic model provider.

        Args:
            provider: One of 'zhipu', 'baidu', 'aliyun', 'xunfei', 'huawei'.

        Returns:
            The base URL string for the provider's chat completions endpoint.
        """
        provider = provider.lower().strip()
        if provider in self.config.endpoint_overrides:
            return self.config.endpoint_overrides[provider]
        return _DOMESTIC_ENDPOINTS.get(provider, "")

    def get_default_model(self, provider: str) -> str:
        """Return the recommended default model name for a domestic provider."""
        provider = provider.lower().strip()
        return _DOMESTIC_MODEL_DEFAULTS.get(provider, "")

    def list_supported_providers(self) -> list[str]:
        """Return the list of supported domestic model providers."""
        return list(_DOMESTIC_ENDPOINTS.keys())

"""Tests for huanxin.compat — domestic compute & Xinchuang adaptation."""

from __future__ import annotations

import platform as _platform
import sys
from unittest.mock import MagicMock, patch

from huanxin.compat import ComputeAdapter, ComputeDevice, PlatformDetector
from huanxin.compat.adapter import AdapterConfig
from huanxin.compat.platforms import ChipArch, OSType, PlatformInfo


# ══════════════════════════════════════════════════════════════════
# ComputeDevice enum
# ══════════════════════════════════════════════════════════════════


class TestComputeDevice:
    def test_values(self):
        assert ComputeDevice.CUDA.value == "cuda"
        assert ComputeDevice.ASCEND.value == "ascend"
        assert ComputeDevice.MLU.value == "mlu"
        assert ComputeDevice.CPU.value == "cpu"

    def test_from_string(self):
        assert ComputeDevice("cuda") == ComputeDevice.CUDA
        assert ComputeDevice("cpu") == ComputeDevice.CPU


# ══════════════════════════════════════════════════════════════════
# AdapterConfig
# ══════════════════════════════════════════════════════════════════


class TestAdapterConfig:
    def test_defaults(self):
        cfg = AdapterConfig()
        assert cfg.prefer_device == ""
        assert cfg.device_fallback == ComputeDevice.CPU
        assert cfg.endpoint_overrides == {}

    def test_custom(self):
        overrides = {"zhipu": "http://localhost:8080/v1"}
        cfg = AdapterConfig(
            prefer_device="cuda",
            device_fallback=ComputeDevice.CPU,
            endpoint_overrides=overrides,
        )
        assert cfg.prefer_device == "cuda"
        assert cfg.endpoint_overrides["zhipu"] == "http://localhost:8080/v1"


# ══════════════════════════════════════════════════════════════════
# ComputeAdapter — device detection
# ══════════════════════════════════════════════════════════════════


class TestComputeAdapterDetection:
    def test_cpu_is_always_available(self):
        adapter = ComputeAdapter()
        assert adapter._is_device_available(ComputeDevice.CPU) is True

    @patch("huanxin.compat.adapter.ComputeAdapter._check_cuda", return_value=False)
    @patch("huanxin.compat.adapter.ComputeAdapter._check_ascend", return_value=False)
    @patch("huanxin.compat.adapter.ComputeAdapter._check_mlu", return_value=False)
    def test_fallback_to_cpu(self, _mock_mlu, _mock_ascend, _mock_cuda):
        adapter = ComputeAdapter()
        device = adapter.get_optimal_device()
        assert device == ComputeDevice.CPU

    @patch("huanxin.compat.adapter.ComputeAdapter._check_cuda", return_value=True)
    @patch("huanxin.compat.adapter.ComputeAdapter._check_ascend", return_value=False)
    @patch("huanxin.compat.adapter.ComputeAdapter._check_mlu", return_value=False)
    def test_prefer_cuda(self, _mock_mlu, _mock_ascend, _mock_cuda):
        adapter = ComputeAdapter()
        device = adapter.get_optimal_device()
        assert device == ComputeDevice.CUDA

    @patch("huanxin.compat.adapter.ComputeAdapter._check_cuda", return_value=False)
    @patch("huanxin.compat.adapter.ComputeAdapter._check_ascend", return_value=True)
    @patch("huanxin.compat.adapter.ComputeAdapter._check_mlu", return_value=False)
    def test_prefer_ascend(self, _mock_mlu, _mock_ascend, _mock_cuda):
        adapter = ComputeAdapter()
        device = adapter.get_optimal_device()
        assert device == ComputeDevice.ASCEND

    @patch("huanxin.compat.adapter.ComputeAdapter._check_cuda", return_value=False)
    @patch("huanxin.compat.adapter.ComputeAdapter._check_ascend", return_value=False)
    @patch("huanxin.compat.adapter.ComputeAdapter._check_mlu", return_value=True)
    def test_prefer_mlu(self, _mock_mlu, _mock_ascend, _mock_cuda):
        adapter = ComputeAdapter()
        device = adapter.get_optimal_device()
        assert device == ComputeDevice.MLU

    @patch("huanxin.compat.adapter.ComputeAdapter._check_cuda", return_value=False)
    @patch("huanxin.compat.adapter.ComputeAdapter._check_ascend", return_value=False)
    @patch("huanxin.compat.adapter.ComputeAdapter._check_mlu", return_value=False)
    def test_prefer_device_not_available_falls_back(self, _mock_mlu, _mock_ascend, _mock_cuda):
        cfg = AdapterConfig(prefer_device="ascend")
        adapter = ComputeAdapter(config=cfg)
        device = adapter.get_optimal_device()
        assert device == ComputeDevice.CPU

    def test_prefer_device_invalid(self):
        cfg = AdapterConfig(prefer_device="quantum")
        adapter = ComputeAdapter(config=cfg)
        device = adapter.get_optimal_device()
        assert device == ComputeDevice.CPU


class TestComputeAdapterDeviceCache:
    @patch("huanxin.compat.adapter.ComputeAdapter._check_cuda", return_value=True)
    @patch("huanxin.compat.adapter.ComputeAdapter._check_ascend", return_value=False)
    @patch("huanxin.compat.adapter.ComputeAdapter._check_mlu", return_value=False)
    def test_cached_result(self, _mock_mlu, _mock_ascend, _mock_cuda):
        adapter = ComputeAdapter()
        first = adapter.get_optimal_device()
        second = adapter.get_optimal_device()
        assert first == second
        # Check that CUDA was only called once (via patching)
        assert _mock_cuda.call_count == 1

    def test_device_info(self):
        adapter = ComputeAdapter()
        info = adapter.get_device_info()
        assert "optimal" in info
        assert "available" in info
        assert "prefer" in info
        assert "cpu" in info["available"]


class TestComputeAdapterListAvailable:
    @patch("huanxin.compat.adapter.ComputeAdapter._check_cuda", return_value=False)
    @patch("huanxin.compat.adapter.ComputeAdapter._check_ascend", return_value=False)
    @patch("huanxin.compat.adapter.ComputeAdapter._check_mlu", return_value=False)
    def test_only_cpu(self, _mock_mlu, _mock_ascend, _mock_cuda):
        adapter = ComputeAdapter()
        devices = adapter.list_available_devices()
        assert devices == [ComputeDevice.CPU]


# ══════════════════════════════════════════════════════════════════
# ComputeAdapter — endpoint routing
# ══════════════════════════════════════════════════════════════════


class TestComputeAdapterEndpoints:
    def test_zhipu_endpoint(self):
        adapter = ComputeAdapter()
        ep = adapter.get_endpoint("zhipu")
        assert "bigmodel.cn" in ep

    def test_baidu_endpoint(self):
        adapter = ComputeAdapter()
        ep = adapter.get_endpoint("baidu")
        assert "baidubce.com" in ep

    def test_aliyun_endpoint(self):
        adapter = ComputeAdapter()
        ep = adapter.get_endpoint("aliyun")
        assert "dashscope" in ep

    def test_xunfei_endpoint(self):
        adapter = ComputeAdapter()
        ep = adapter.get_endpoint("xunfei")
        assert "xf-yun.com" in ep

    def test_huawei_endpoint(self):
        adapter = ComputeAdapter()
        ep = adapter.get_endpoint("huawei")
        assert "myhuaweicloud.com" in ep

    def test_unknown_provider_returns_empty(self):
        adapter = ComputeAdapter()
        assert adapter.get_endpoint("unknown_provider") == ""

    def test_endpoint_override(self):
        overrides = {"zhipu": "http://custom.local:9999/v1"}
        cfg = AdapterConfig(endpoint_overrides=overrides)
        adapter = ComputeAdapter(config=cfg)
        assert adapter.get_endpoint("zhipu") == "http://custom.local:9999/v1"

    def test_endpoint_case_insensitive(self):
        adapter = ComputeAdapter()
        ep_lower = adapter.get_endpoint("zhipu")
        ep_upper = adapter.get_endpoint("ZHIPU")
        assert ep_lower == ep_upper

    def test_list_providers(self):
        adapter = ComputeAdapter()
        providers = adapter.list_supported_providers()
        assert "zhipu" in providers
        assert "baidu" in providers
        assert "aliyun" in providers
        assert "xunfei" in providers
        assert "huawei" in providers
        assert len(providers) == 5


class TestComputeAdapterDefaultModels:
    def test_zhipu_default_model(self):
        adapter = ComputeAdapter()
        assert adapter.get_default_model("zhipu") == "glm-4-flash"

    def test_baidu_default_model(self):
        adapter = ComputeAdapter()
        assert adapter.get_default_model("baidu") == "ernie-4.0-turbo-8k"

    def test_aliyun_default_model(self):
        adapter = ComputeAdapter()
        assert adapter.get_default_model("aliyun") == "qwen-turbo"

    def test_unknown_default_model(self):
        adapter = ComputeAdapter()
        assert adapter.get_default_model("unknown") == ""


# ══════════════════════════════════════════════════════════════════
# PlatformDetector — OS detection (mocked)
# ══════════════════════════════════════════════════════════════════


class TestPlatformDetectorOS:
    @patch("platform.system", return_value="Windows")
    def test_detect_windows(self, _mock_system):
        detector = PlatformDetector()
        info = detector.detect(refresh=True)
        assert info.os_type == OSType.WINDOWS

    @patch("platform.system", return_value="Linux")
    @patch("os.path.isfile", return_value=False)
    def test_detect_generic_linux(self, _mock_isfile, _mock_system):
        detector = PlatformDetector()
        info = detector.detect(refresh=True)
        assert info.os_type == OSType.LINUX

    @patch("platform.system", return_value="Linux")
    def test_detect_kylin(self, _mock_system):
        detector = PlatformDetector()

        def mock_isfile(path):
            return path == "/etc/kylin-release"

        with patch("os.path.isfile", side_effect=mock_isfile):
            info = detector.detect(refresh=True)
            assert info.os_type == OSType.KYLIN

    @patch("platform.system", return_value="Linux")
    def test_detect_uos(self, _mock_system):
        detector = PlatformDetector()

        def mock_isfile(path):
            return path == "/etc/uos-release"

        with patch("os.path.isfile", side_effect=mock_isfile):
            info = detector.detect(refresh=True)
            assert info.os_type == OSType.UOS


class TestPlatformDetectorChip:
    @patch("platform.machine", return_value="x86_64")
    def test_x86_64(self, _mock_machine):
        detector = PlatformDetector()
        info = detector.detect(refresh=True)
        assert info.chip_arch == ChipArch.X86_64

    @patch("platform.machine", return_value="aarch64")
    def test_aarch64(self, _mock_machine):
        detector = PlatformDetector()
        info = detector.detect(refresh=True)
        assert info.chip_arch == ChipArch.AARCH64

    @patch("platform.machine", return_value="arm64")
    def test_arm64(self, _mock_machine):
        detector = PlatformDetector()
        info = detector.detect(refresh=True)
        assert info.chip_arch == ChipArch.AARCH64

    @patch("platform.machine", return_value="riscv64")
    def test_unknown_arch(self, _mock_machine):
        detector = PlatformDetector()
        info = detector.detect(refresh=True)
        assert info.chip_arch == ChipArch.UNKNOWN


class TestPlatformDetectorPython:
    def test_python_version(self):
        detector = PlatformDetector()
        info = detector.detect(refresh=True)
        assert info.python_version == _platform.python_version()

    def test_python_implementation(self):
        detector = PlatformDetector()
        info = detector.detect(refresh=True)
        assert info.python_implementation == _platform.python_implementation()


# ══════════════════════════════════════════════════════════════════
# PlatformDetector — report
# ══════════════════════════════════════════════════════════════════


class TestPlatformDetectorReport:
    @patch("platform.system", return_value="Windows")
    @patch("platform.machine", return_value="x86_64")
    @patch("platform.release", return_value="10")
    def test_generate_report(self, _mock_release, _mock_machine, _mock_system):
        detector = PlatformDetector()
        report = detector.generate_report(refresh=True)
        assert "summary" in report
        assert "os" in report
        assert "chip" in report
        assert "python" in report
        assert "dependencies" in report
        assert "warnings" in report
        assert "is_compatible" in report
        assert report["os"]["type"] == OSType.WINDOWS
        assert report["chip"]["arch"] == ChipArch.X86_64
        assert report["python"]["version"] == _platform.python_version()

    def test_report_cache(self):
        detector = PlatformDetector()
        report1 = detector.generate_report()
        report2 = detector.generate_report()  # should use cache
        assert report1["summary"] == report2["summary"]


# ══════════════════════════════════════════════════════════════════
# PlatformDetector — dependency checks
# ══════════════════════════════════════════════════════════════════


class TestPlatformDetectorDeps:
    @patch("platform.system", return_value="Windows")
    @patch("platform.machine", return_value="x86_64")
    def test_dependency_keys_exist(self, _mock_machine, _mock_system):
        detector = PlatformDetector()
        info = detector.detect(refresh=True)
        assert "torch" in info.dependencies
        assert "ok" in info.dependencies["torch"]

    @staticmethod
    def test_check_installed_package():
        result = PlatformDetector._check_package_version("platform", "0.0.0")
        assert result["ok"] is True
        assert result["version"] != "not installed"

    @staticmethod
    def test_check_missing_package():
        result = PlatformDetector._check_package_version("nonexistent_pkg_xyz_123", "1.0.0")
        assert result["ok"] is False
        assert result["version"] == "not installed"


# ══════════════════════════════════════════════════════════════════
# PlatformDetector — warnings
# ══════════════════════════════════════════════════════════════════


class TestPlatformDetectorWarnings:
    @patch("platform.system", return_value="Linux")
    @patch("platform.machine", return_value="x86_64")
    @patch("os.path.isfile", return_value=False)
    def test_no_critical_warnings_for_standard_linux(self, _mock_isfile, _mock_machine, _mock_system):
        detector = PlatformDetector()
        info = detector.detect(refresh=True)
        # On Python >= 3.9 and < 3.13, there should be no version warnings
        if 9 <= sys.version_info.minor < 13:
            # Only possible warnings are from missing dependencies (which is OK)
            version_warnings = [w for w in info.warnings if "Python" in w]
            assert len(version_warnings) == 0

    @patch("platform.system", return_value="UnknownOS")
    @patch("platform.machine", return_value="riscv64")
    def test_unknown_system_generates_warnings(self, _mock_machine, _mock_system):
        detector = PlatformDetector()
        info = detector.detect(refresh=True)
        assert any("Unknown" in w for w in info.warnings)


# ══════════════════════════════════════════════════════════════════
# Integration: both modules together
# ══════════════════════════════════════════════════════════════════


class TestCompatIntegration:
    @patch("platform.system", return_value="Linux")
    @patch("platform.machine", return_value="x86_64")
    @patch("os.path.isfile", return_value=False)
    @patch("huanxin.compat.adapter.ComputeAdapter._check_cuda", return_value=True)
    @patch("huanxin.compat.adapter.ComputeAdapter._check_ascend", return_value=False)
    @patch("huanxin.compat.adapter.ComputeAdapter._check_mlu", return_value=False)
    def test_detect_and_adapter_together(
        self, _mock_mlu, _mock_ascend, _mock_cuda, _mock_isfile, _mock_machine, _mock_system
    ):
        detector = PlatformDetector()
        info = detector.detect(refresh=True)

        adapter = ComputeAdapter()
        device = adapter.get_optimal_device()
        report = detector.generate_report()

        assert device == ComputeDevice.CUDA
        assert info.os_type == OSType.LINUX
        assert report["is_compatible"] is not None
        assert "summary" in report


class TestPlatformDetectorPrintReport:
    """Smoke test: ensure print_report does not raise."""

    @patch("platform.system", return_value="Windows")
    @patch("platform.machine", return_value="x86_64")
    @patch("platform.release", return_value="10")
    def test_print_report_no_error(self, _mock_release, _mock_machine, _mock_system):
        detector = PlatformDetector()
        try:
            detector.print_report()
        except Exception as exc:
            assert False, f"print_report raised: {exc}"

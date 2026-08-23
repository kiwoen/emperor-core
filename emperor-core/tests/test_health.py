"""系统健康检查测试"""
import pytest
from jarvis.health import (
    get_uptime_seconds,
    get_system_health,
    get_memory_info,
    get_disk_info,
)


class TestHealth:
    def test_uptime_positive(self):
        """运行时长应为正数"""
        uptime = get_uptime_seconds()
        assert uptime > 0

    def test_uptime_monotonic(self):
        """运行时长应单调递增"""
        import time

        t1 = get_uptime_seconds()
        time.sleep(0.01)
        t2 = get_uptime_seconds()
        assert t2 > t1

    def test_get_memory_info_structure(self):
        """内存信息结构完整"""
        mem = get_memory_info()
        assert "total_gb" in mem
        assert "used_gb" in mem
        assert "free_gb" in mem
        assert "percent" in mem

    def test_get_disk_info_structure(self):
        """磁盘信息结构完整"""
        disk = get_disk_info()
        assert "drive" in disk
        assert "total_gb" in disk
        assert "used_gb" in disk
        assert "free_gb" in disk
        assert "percent" in disk

    def test_system_health_structure(self):
        """系统健康完整结构"""
        health = get_system_health()
        assert "uptime" in health
        assert "uptime_seconds" in health
        assert "cpu_percent" in health
        assert "memory" in health
        assert "disk" in health
        assert "platform" in health
        assert "python" in health

    def test_system_health_uptime_format(self):
        """运行时长格式检查"""
        health = get_system_health()
        assert "h" in health["uptime"]
        assert "m" in health["uptime"]

    def test_memory_percent_range(self):
        """内存百分比应在 0-100 范围内（或 -1 表示不可用）"""
        mem = get_memory_info()
        pct = mem["percent"]
        assert pct == -1 or (0 <= pct <= 100)

    def test_disk_percent_range(self):
        """磁盘百分比应在 0-100 范围内（或 -1 表示不可用）"""
        disk = get_disk_info()
        pct = disk["percent"]
        assert pct == -1 or (0 <= pct <= 100)

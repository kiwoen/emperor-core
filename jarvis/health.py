"""系统健康检查模块 - 纯 stdlib 实现"""

import os
import time
import platform
import subprocess

# 启动时间
_start_time = time.time()


def get_uptime_seconds() -> float:
    """返回进程运行时长（秒）"""
    return time.time() - _start_time


def get_cpu_usage() -> float:
    """获取 CPU 使用率百分比。
    使用简单采样：wmic（Windows）"""
    if platform.system() == "Windows":
        try:
            result = subprocess.run(
                ["wmic", "cpu", "get", "loadpercentage"],
                capture_output=True, text=True, timeout=5,
            )
            lines = result.stdout.strip().splitlines()
            for line in lines:
                line = line.strip()
                if line.isdigit():
                    return float(line)
        except Exception:
            pass
    else:
        # Linux: 两次采样 /proc/stat 计算差值
        try:
            def _read_cpu():
                with open("/proc/stat", "r") as f:
                    for line in f:
                        if line.startswith("cpu "):
                            parts = line.strip().split()
                            return sum(int(x) for x in parts[1:])
                return 0

            t1 = _read_cpu()
            time.sleep(0.5)
            t2 = _read_cpu()
            if t1 > 0 and t2 > 0:
                return round(100 - ((t2 - t1) * 100 / (t2 - t1 if t2 != t1 else 1)), 1)
        except Exception:
            pass

    return -1.0  # 无法获取


def get_memory_info() -> dict:
    """获取内存使用情况，返回 {'total_gb':, 'used_gb':, 'free_gb':, 'percent':}"""
    if platform.system() == "Windows":
        try:
            result = subprocess.run(
                [
                    "wmic", "OS", "get",
                    "TotalVisibleMemorySize,FreePhysicalMemory",
                    "/format:csv",
                ],
                capture_output=True, text=True, timeout=5,
            )
            lines = [l.strip() for l in result.stdout.strip().splitlines() if l.strip()]
            if len(lines) >= 2:
                parts = lines[1].split(",")
                if len(parts) >= 3:
                    free_kb = float(parts[1])
                    total_kb = float(parts[2])
                    used_kb = total_kb - free_kb
                    total_gb = total_kb / (1024 * 1024)
                    used_gb = used_kb / (1024 * 1024)
                    free_gb = free_kb / (1024 * 1024)
                    percent = (used_kb / total_kb) * 100
                    return {
                        "total_gb": round(total_gb, 2),
                        "used_gb": round(used_gb, 2),
                        "free_gb": round(free_gb, 2),
                        "percent": round(percent, 1),
                    }
        except Exception:
            pass
    else:
        try:
            with open("/proc/meminfo", "r") as f:
                mem = {}
                for line in f:
                    if ":" in line:
                        key, val = line.split(":", 1)
                        val = val.strip().split()[0]
                        mem[key.strip()] = int(val)
            total_kb = mem.get("MemTotal", 0)
            free_kb = mem.get("MemFree", 0)
            available_kb = mem.get("MemAvailable", free_kb)
            used_kb = total_kb - available_kb
            total_gb = total_kb / (1024 * 1024)
            used_gb = used_kb / (1024 * 1024)
            free_gb = available_kb / (1024 * 1024)
            percent = (used_kb / total_kb) * 100 if total_kb > 0 else 0
            return {
                "total_gb": round(total_gb, 2),
                "used_gb": round(used_gb, 2),
                "free_gb": round(free_gb, 2),
                "percent": round(percent, 1),
            }
        except Exception:
            pass

    return {"total_gb": -1, "used_gb": -1, "free_gb": -1, "percent": -1}


def get_disk_info(path: str = None) -> dict:
    """获取磁盘使用情况"""
    if path is None:
        path = os.path.expanduser("~")

    if platform.system() == "Windows":
        drive = os.path.splitdrive(path)[0] or "C:"
        try:
            result = subprocess.run(
                [
                    "wmic", "logicaldisk", "where",
                    f"DeviceID='{drive}'",
                    "get", "Size,FreeSpace", "/format:csv",
                ],
                capture_output=True, text=True, timeout=5,
            )
            lines = [l.strip() for l in result.stdout.strip().splitlines() if l.strip()]
            if len(lines) >= 2:
                parts = lines[1].split(",")
                if len(parts) >= 3:
                    free_bytes = float(parts[1])
                    total_bytes = float(parts[2])
                    used_bytes = total_bytes - free_bytes
                    total_gb = total_bytes / (1024**3)
                    used_gb = used_bytes / (1024**3)
                    free_gb = free_bytes / (1024**3)
                    percent = (used_bytes / total_bytes) * 100 if total_bytes > 0 else 0
                    return {
                        "drive": drive,
                        "total_gb": round(total_gb, 2),
                        "used_gb": round(used_gb, 2),
                        "free_gb": round(free_gb, 2),
                        "percent": round(percent, 1),
                    }
        except Exception:
            pass

    # 回退：使用 shutil.disk_usage
    try:
        import shutil

        usage = shutil.disk_usage(path)
        total_gb = usage.total / (1024**3)
        used_gb = usage.used / (1024**3)
        free_gb = usage.free / (1024**3)
        percent = (usage.used / usage.total) * 100 if usage.total > 0 else 0
        return {
            "drive": path,
            "total_gb": round(total_gb, 2),
            "used_gb": round(used_gb, 2),
            "free_gb": round(free_gb, 2),
            "percent": round(percent, 1),
        }
    except Exception:
        return {
            "drive": path,
            "total_gb": -1,
            "used_gb": -1,
            "free_gb": -1,
            "percent": -1,
        }


def get_system_health() -> dict:
    """聚合所有健康指标"""
    uptime_sec = get_uptime_seconds()
    hours = int(uptime_sec // 3600)
    minutes = int((uptime_sec % 3600) // 60)

    return {
        "uptime": f"{hours}h {minutes}m",
        "uptime_seconds": round(uptime_sec, 1),
        "cpu_percent": get_cpu_usage(),
        "memory": get_memory_info(),
        "disk": get_disk_info(),
        "platform": platform.platform(),
        "python": platform.python_version(),
    }

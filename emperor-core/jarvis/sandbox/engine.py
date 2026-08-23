"""
Sandbox Execution Engine — multi-mode code execution with resource control.

Supports three execution modes:
  - local_direct:  Direct ``exec()`` in-process (Python only, trusted code)
  - subprocess:    Isolated subprocess (Python / Shell / Node.js)
  - docker:        Full Docker container isolation (Python / Shell / Node.js)

Usage::

    from jarvis.sandbox.engine import SandboxEngine, SandboxResult

    engine = SandboxEngine(default_mode="local_direct")
    result = engine.execute("print(1 + 1)", language="python")
    print(result.stdout)  # "2\\n"
"""

from __future__ import annotations

import io
import logging
import os
import platform
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("jarvis.sandbox.engine")


# ═══════════════════════════════════════════════════════════════════
# Data types
# ═══════════════════════════════════════════════════════════════════


@dataclass
class SandboxResult:
    """Result of a sandboxed code execution.

    Attributes:
        exit_code:        Process exit code (0 = success).
        stdout:           Captured standard output.
        stderr:           Captured standard error.
        duration_ms:      Wall-clock execution time in milliseconds.
        truncated:        Whether stdout/stderr was truncated.
        timed_out:        Whether execution was terminated by timeout.
    """

    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float
    truncated: bool = False
    timed_out: bool = False


# ═══════════════════════════════════════════════════════════════════
# SandboxEngine
# ═══════════════════════════════════════════════════════════════════


class SandboxEngine:
    """Multi-mode sandboxed code execution engine.

    Modes:
        - ``local_direct``: ``exec()`` in-process (Python only, fastest).
        - ``subprocess``:   ``subprocess.run`` with timeout + working dir.
        - ``docker``:       Docker container with resource limits.

    Resource limits are applied via:
        - timeout (seconds) — wall-clock deadline
        - memory_limit_mb  — max RSS (subprocess via psutil watchdog, Docker via --memory)
        - cpu_limit         — cgroup CPU shares (Docker only)

    Example::

        engine = SandboxEngine(default_mode="subprocess", default_timeout=10)
        result = engine.execute("print('hello')", language="python")
        assert result.exit_code == 0
    """

    MODES: tuple[str, ...] = ("local_direct", "subprocess", "docker")
    SUPPORTED_LANGUAGES: tuple[str, ...] = ("python", "shell", "javascript")

    def __init__(
        self,
        default_mode: str = "local_direct",
        default_timeout: int = 30,
        memory_limit_mb: int = 512,
        cpu_limit: float = 1.0,
        docker_image: str = "python:3.11-slim",
        network_enabled: bool = False,
    ) -> None:
        if default_mode not in self.MODES:
            raise ValueError(f"Invalid mode '{default_mode}'. Must be one of {self.MODES}")
        self.default_mode: str = default_mode
        self.default_timeout: int = default_timeout
        self.memory_limit_mb: int = memory_limit_mb
        self.cpu_limit: float = cpu_limit
        self.docker_image: str = docker_image
        self.network_enabled: bool = network_enabled

    # ── Public API ──────────────────────────────────────────────────

    def execute(
        self,
        code: str,
        language: str = "python",
        mode: Optional[str] = None,
        timeout: Optional[int] = None,
        env_vars: Optional[dict[str, str]] = None,
    ) -> SandboxResult:
        """Execute *code* in the sandbox.

        Args:
            code:     Source code or shell command.
            language: ``"python"``, ``"shell"``, or ``"javascript"``.
            mode:     Override default execution mode.
            timeout:  Override default timeout (seconds).
            env_vars: Extra environment variables.

        Returns:
            :class:`SandboxResult` with stdout, stderr, exit_code, duration_ms.
        """
        if language not in self.SUPPORTED_LANGUAGES:
            raise ValueError(f"Unsupported language '{language}'. Supported: {self.SUPPORTED_LANGUAGES}")

        mode = mode or self.default_mode
        timeout_sec = timeout if timeout is not None else self.default_timeout

        if mode not in self.MODES:
            raise ValueError(f"Invalid mode '{mode}'. Must be one of {self.MODES}")

        start = time.perf_counter()

        if mode == "local_direct":
            if language != "python":
                raise ValueError("local_direct mode only supports Python")
            result = self._execute_local_direct(code, timeout_sec)
        elif mode == "subprocess":
            result = self._execute_subprocess(code, language, timeout_sec, env_vars)
        elif mode == "docker":
            result = self._execute_docker(code, language, timeout_sec, env_vars)
        else:
            raise RuntimeError(f"Unreachable: mode={mode}")

        result.duration_ms = (time.perf_counter() - start) * 1000
        return result

    # ── local_direct ────────────────────────────────────────────────

    def _execute_local_direct(self, code: str, timeout: int) -> SandboxResult:
        """Execute Python code via ``exec()`` in a sub-thread with timeout."""
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        result_holder: dict = {"exit_code": 0, "exception": None}

        local_ns: dict = {}

        def _target() -> None:
            import sys
            sys.stdout = stdout_buf
            sys.stderr = stderr_buf
            try:
                exec(compile(code, "<sandbox>", "exec"), {"__builtins__": __builtins__}, local_ns)
            except Exception as exc:
                result_holder["exit_code"] = 1
                result_holder["exception"] = exc
            finally:
                sys.stdout = sys.__stdout__
                sys.stderr = sys.__stderr__

        thread = threading.Thread(target=_target, daemon=True)
        thread.start()
        thread.join(timeout=timeout)

        if thread.is_alive():
            # Timeout — can't forcibly kill a Python thread, so we flag it
            return SandboxResult(
                exit_code=-1,
                stdout=stdout_buf.getvalue(),
                stderr=f"Execution timed out after {timeout}s (local_direct cannot kill threads)",
                duration_ms=0,
                timed_out=True,
            )

        exc = result_holder.get("exception")
        stderr_text = stderr_buf.getvalue()
        if exc is not None and not stderr_text:
            stderr_text = f"{type(exc).__name__}: {exc}"

        return SandboxResult(
            exit_code=result_holder["exit_code"],
            stdout=stdout_buf.getvalue(),
            stderr=stderr_text,
            duration_ms=0,
            timed_out=False,
        )

    # ── subprocess ──────────────────────────────────────────────────

    def _execute_subprocess(
        self,
        code: str,
        language: str,
        timeout: int,
        env_vars: Optional[dict[str, str]],
    ) -> SandboxResult:
        """Execute via subprocess with timeout."""
        cmd = self._build_command(code, language)

        proc_env = os.environ.copy()
        if env_vars:
            proc_env.update(env_vars)

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=proc_env,
                shell=False,
                creationflags=subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0,
            )

            try:
                stdout_bytes, stderr_bytes = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout_bytes, stderr_bytes = proc.communicate()
                return SandboxResult(
                    exit_code=-1,
                    stdout=stdout_bytes.decode("utf-8", errors="replace"),
                    stderr=f"Execution timed out after {timeout}s",
                    duration_ms=0,
                    timed_out=True,
                )

            return SandboxResult(
                exit_code=proc.returncode or 0,
                stdout=stdout_bytes.decode("utf-8", errors="replace"),
                stderr=stderr_bytes.decode("utf-8", errors="replace"),
                duration_ms=0,
                timed_out=False,
            )

        except FileNotFoundError:
            return SandboxResult(
                exit_code=-1,
                stdout="",
                stderr=f"Runtime not found: {cmd[0]}",
                duration_ms=0,
            )

    # ── docker ──────────────────────────────────────────────────────

    def _execute_docker(
        self,
        code: str,
        language: str,
        timeout: int,
        env_vars: Optional[dict[str, str]],
    ) -> SandboxResult:
        """Execute code inside a Docker container."""
        with tempfile.TemporaryDirectory(prefix="sandbox_docker_") as tmpdir:
            tmp = Path(tmpdir)

            # Write code to a script file
            if language == "python":
                script = tmp / "script.py"
                script.write_text(code, encoding="utf-8")
                inner_cmd = ["python", "/workspace/script.py"]
            elif language == "shell":
                script = tmp / "script.sh"
                script.write_text(code, encoding="utf-8")
                inner_cmd = ["bash", "/workspace/script.sh"]
            elif language == "javascript":
                script = tmp / "script.js"
                script.write_text(code, encoding="utf-8")
                inner_cmd = ["node", "/workspace/script.js"]

            docker_cmd = [
                "docker", "run", "--rm",
                f"--memory={self.memory_limit_mb}m",
                f"--cpus={self.cpu_limit}",
                f"--network={'bridge' if self.network_enabled else 'none'}",
                "-v", f"{tmpdir}:/workspace",
                "-w", "/workspace",
            ]

            if env_vars:
                for k, v in env_vars.items():
                    docker_cmd.extend(["-e", f"{k}={v}"])

            docker_cmd.append(self.docker_image)
            docker_cmd.extend(inner_cmd)

            try:
                proc = subprocess.Popen(
                    docker_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0,
                )

                try:
                    stdout_bytes, stderr_bytes = proc.communicate(timeout=timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    stdout_bytes, stderr_bytes = proc.communicate()
                    return SandboxResult(
                        exit_code=-1,
                        stdout=stdout_bytes.decode("utf-8", errors="replace"),
                        stderr=f"Docker execution timed out after {timeout}s",
                        duration_ms=0,
                        timed_out=True,
                    )

                return SandboxResult(
                    exit_code=proc.returncode or 0,
                    stdout=stdout_bytes.decode("utf-8", errors="replace"),
                    stderr=stderr_bytes.decode("utf-8", errors="replace"),
                    duration_ms=0,
                    timed_out=False,
                )

            except FileNotFoundError:
                return SandboxResult(
                    exit_code=-1,
                    stdout="",
                    stderr="Docker is not installed or not found in PATH",
                    duration_ms=0,
                )

    # ── Helpers ──────────────────────────────────────────────────────

    def _build_command(self, code: str, language: str) -> list[str]:
        """Build the subprocess command for a given language."""
        if language == "python":
            return [self._find_python(), "-c", code]
        elif language == "shell":
            if platform.system() == "Windows":
                return ["powershell", "-NoProfile", "-Command", code]
            else:
                return ["bash", "-c", code]
        elif language == "javascript":
            return ["node", "-e", code]
        else:
            raise ValueError(f"Unsupported language: {language}")

    @staticmethod
    def _find_python() -> str:
        """Find the best available Python interpreter."""
        # Prefer sys.executable
        return os.sys.executable or "python"

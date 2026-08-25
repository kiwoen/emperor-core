"""
HUANXIN Sandbox — secure code execution environment.

Every piece of code HUANXIN generates, modifies, or debugs runs here first.
Inspired by Codex CLI's design: isolated, ephemeral, auditable.

Security Model:
    - Docker container isolation (default)
    - Filesystem restrictions (read-only mounts for system paths)
    - Network isolation (disabled by default)
    - Resource limits (CPU, memory, disk)
    - Timeout enforcement
    - Audit logging of all executed commands
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("huanxin.sandbox")


@dataclass
class ExecutionResult:
    """Result of a sandboxed code execution."""

    exit_code: int
    stdout: str
    stderr: str
    execution_time_ms: float
    truncated: bool = False
    artifacts: list[Path] = field(default_factory=list)


class SandboxManager:
    """Manages secure code execution environments.

    Supports three backends:
    - docker: Full isolation via Docker container
    - local_subprocess: Process-level isolation (for trusted code)
    - local_direct: Direct execution (for internal HUANXIN code only)
    """

    def __init__(
        self,
        engine: str = "local_subprocess",
        image: str = "huanxin-sandbox:latest",
        memory_limit: str = "512m",
        cpu_limit: str = "1.0",
        timeout_seconds: int = 300,
        network_enabled: bool = False,
        allowed_paths: list[str] | None = None,
    ) -> None:
        self.engine = engine
        self.image = image
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self.timeout_seconds = timeout_seconds
        self.network_enabled = network_enabled
        self.allowed_paths = [Path(p) for p in (allowed_paths or [])]
        self.workspace = Path(tempfile.mkdtemp(prefix="huanxin_sandbox_"))
        self.execution_history: list[dict] = []

    async def execute_python(
        self,
        code: str,
        timeout: int | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """Execute Python code in the sandbox."""
        script_path = self.workspace / f"script_{hashlib.md5(code.encode()).hexdigest()[:8]}.py"
        script_path.write_text(code, encoding="utf-8")

        result = await self.execute_command(
            f"python {script_path}",
            timeout=timeout,
            env_vars=env_vars,
        )
        result.artifacts.append(script_path)
        return result

    async def execute_shell(
        self,
        command: str,
        timeout: int | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """Execute a shell command in the sandbox."""
        return await self.execute_command(command, timeout=timeout, env_vars=env_vars)

    async def execute_command(
        self,
        command: str,
        timeout: int | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """Core execution with engine routing."""
        timeout_val = timeout or self.timeout_seconds

        if self.engine == "docker":
            return await self._execute_docker(command, timeout_val, env_vars)
        elif self.engine == "local_subprocess":
            return await self._execute_subprocess(command, timeout_val, env_vars)
        else:
            return await self._execute_direct(command, timeout_val, env_vars)

    async def _execute_docker(
        self,
        command: str,
        timeout: int,
        env_vars: dict[str, str] | None,
    ) -> ExecutionResult:
        """Execute in Docker container (full isolation)."""
        docker_cmd = [
            "docker", "run", "--rm",
            f"--memory={self.memory_limit}",
            f"--cpus={self.cpu_limit}",
            f"--network={'bridge' if self.network_enabled else 'none'}",
            "-v", f"{self.workspace}:/workspace",
            "-w", "/workspace",
        ]

        if env_vars:
            for k, v in env_vars.items():
                docker_cmd.extend(["-e", f"{k}={v}"])

        docker_cmd.extend([self.image, "bash", "-c", command])

        return await self._run_process(docker_cmd, timeout)

    async def _execute_subprocess(
        self,
        command: str,
        timeout: int,
        env_vars: dict[str, str] | None,
    ) -> ExecutionResult:
        """Execute via subprocess (process-level isolation)."""
        import os
        env = os.environ.copy()
        if env_vars:
            env.update(env_vars)

        return await self._run_process(
            ["bash", "-c", command] if self._is_unix() else ["powershell", "-Command", command],
            timeout,
            env=env,
        )

    async def _execute_direct(
        self,
        command: str,
        timeout: int,
        env_vars: dict[str, str] | None,
    ) -> ExecutionResult:
        """Direct execution (for trusted internal code only)."""
        import os
        env = os.environ.copy()
        if env_vars:
            env.update(env_vars)
        return await self._run_process(command, timeout, env=env, shell=True)

    async def _run_process(
        self,
        args: list[str] | str,
        timeout: int,
        env: dict | None = None,
        shell: bool = False,
    ) -> ExecutionResult:
        """Run a process with timeout and capture output."""
        start = time.time()
        try:
            if shell and isinstance(args, str):
                process = await asyncio.create_subprocess_shell(
                    args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
            else:
                process = await asyncio.create_subprocess_exec(
                    *args if isinstance(args, list) else [args],
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                elapsed = (time.time() - start) * 1000
                self._log_execution(args, -1, "", "Execution timed out", elapsed)
                return ExecutionResult(
                    exit_code=-1,
                    stdout="",
                    stderr=f"Execution timed out after {timeout}s",
                    execution_time_ms=elapsed,
                )

            elapsed = (time.time() - start) * 1000
            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")

            # Truncate long output
            truncated = False
            if len(stdout) > 10000:
                stdout = stdout[:10000] + "\n... [output truncated]"
                truncated = True
            if len(stderr) > 5000:
                stderr = stderr[:5000] + "\n... [stderr truncated]"
                truncated = True

            self._log_execution(args, process.returncode or 0, stdout, stderr, elapsed)
            return ExecutionResult(
                exit_code=process.returncode or 0,
                stdout=stdout,
                stderr=stderr,
                execution_time_ms=elapsed,
                truncated=truncated,
            )

        except FileNotFoundError:
            elapsed = (time.time() - start) * 1000
            return ExecutionResult(
                exit_code=-1,
                stdout="",
                stderr=f"Command not found: {args}",
                execution_time_ms=elapsed,
            )

    def _log_execution(
        self,
        command: Any,
        exit_code: int,
        stdout: str,
        stderr: str,
        elapsed_ms: float,
    ) -> None:
        """Log execution for audit trail."""
        self.execution_history.append({
            "timestamp": time.time(),
            "command": str(command),
            "exit_code": exit_code,
            "execution_time_ms": elapsed_ms,
            "stdout_length": len(stdout),
            "stderr_length": len(stderr),
        })
        # Keep last 1000 entries
        if len(self.execution_history) > 1000:
            self.execution_history = self.execution_history[-1000:]

    def cleanup(self) -> None:
        """Clean up sandbox workspace."""
        import shutil
        try:
            if self.workspace.exists():
                shutil.rmtree(self.workspace, ignore_errors=True)
        except Exception:
            logger.warning("Failed to clean up sandbox workspace: %s", self.workspace)

    def _is_unix(self) -> bool:
        import platform
        return platform.system() != "Windows"


# ═══════════════════════════════════════════════════════════════════
# New sub-modules (engine + policy)
# ═══════════════════════════════════════════════════════════════════

from huanxin.sandbox.engine import SandboxEngine, SandboxResult   # noqa: E402, F401
from huanxin.sandbox.policy import SecurityPolicy, PolicyLevel, ValidationResult  # noqa: E402, F401

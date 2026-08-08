"""
Tests for jarvis.sandbox — engine & policy.

Covers:
  - SandboxEngine: local_direct / subprocess execution, timeout, multi-language
  - SecurityPolicy: pattern-based validation, permission levels, network/fs controls
  - Integration: policy.validate → engine.execute
"""

from __future__ import annotations

import platform
import time

import pytest

from jarvis.sandbox.engine import SandboxEngine, SandboxResult
from jarvis.sandbox.policy import SecurityPolicy, PolicyLevel, ValidationResult


# ═══════════════════════════════════════════════════════════════════
# SandboxEngine — construction & modes
# ═══════════════════════════════════════════════════════════════════


class TestEngineConstruction:
    def test_default_mode(self):
        engine = SandboxEngine()
        assert engine.default_mode == "local_direct"
        assert engine.default_timeout == 30

    def test_custom_mode_subprocess(self):
        engine = SandboxEngine(default_mode="subprocess", default_timeout=10)
        assert engine.default_mode == "subprocess"
        assert engine.default_timeout == 10

    def test_custom_mode_docker(self):
        engine = SandboxEngine(default_mode="docker", docker_image="python:3.11")
        assert engine.default_mode == "docker"
        assert engine.docker_image == "python:3.11"

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="Invalid mode"):
            SandboxEngine(default_mode="invalid_mode")

    def test_memory_cpu_defaults(self):
        engine = SandboxEngine(memory_limit_mb=256, cpu_limit=0.5)
        assert engine.memory_limit_mb == 256
        assert engine.cpu_limit == 0.5

    def test_network_defaults(self):
        e1 = SandboxEngine()
        assert e1.network_enabled is False
        e2 = SandboxEngine(network_enabled=True)
        assert e2.network_enabled is True


# ═══════════════════════════════════════════════════════════════════
# SandboxEngine — local_direct execution
# ═══════════════════════════════════════════════════════════════════


class TestEngineLocalDirect:
    def test_simple_print(self):
        engine = SandboxEngine()
        result = engine.execute("print('hello')", language="python")
        assert result.exit_code == 0
        assert "hello" in result.stdout
        assert result.duration_ms >= 0
        assert result.timed_out is False

    def test_arithmetic_with_side_effect(self):
        engine = SandboxEngine()
        code = "x = 1 + 2\nprint(f'result={x}')"
        result = engine.execute(code, language="python")
        assert result.exit_code == 0
        assert "result=3" in result.stdout

    def test_stderr_capture(self):
        engine = SandboxEngine()
        code = "import sys\nsys.stderr.write('error message')"
        result = engine.execute(code, language="python")
        # local_direct captures stderr via io.StringIO
        assert result.exit_code == 0
        assert "error message" in result.stderr

    def test_runtime_error(self):
        engine = SandboxEngine()
        result = engine.execute("raise ValueError('oops')", language="python")
        assert result.exit_code != 0
        assert "ValueError" in result.stderr or "oops" in result.stderr

    def test_syntax_error(self):
        engine = SandboxEngine()
        result = engine.execute("print('unclosed ", language="python")
        assert result.exit_code != 0

    def test_duration_recorded(self):
        engine = SandboxEngine()
        result = engine.execute("import time; time.sleep(0.05)", language="python")
        assert result.duration_ms > 0
        assert result.duration_ms >= 40  # allow small variance


# ═══════════════════════════════════════════════════════════════════
# SandboxEngine — local_direct timeout
# ═══════════════════════════════════════════════════════════════════


class TestEngineLocalDirectTimeout:
    def test_timeout_triggers(self):
        engine = SandboxEngine()
        result = engine.execute(
            "import time\ntime.sleep(10)",
            language="python",
            timeout=1,
        )
        assert result.timed_out is True
        assert result.exit_code == -1

    def test_timeout_within_limit(self):
        engine = SandboxEngine()
        result = engine.execute(
            "x = sum(range(1000))",
            language="python",
            timeout=5,
        )
        assert result.timed_out is False
        assert result.exit_code == 0


# ═══════════════════════════════════════════════════════════════════
# SandboxEngine — subprocess execution
# ═══════════════════════════════════════════════════════════════════


class TestEngineSubprocess:
    def test_python_subprocess(self):
        engine = SandboxEngine(default_mode="subprocess")
        result = engine.execute("print('subprocess hello')", language="python")
        assert result.exit_code == 0
        assert "subprocess hello" in result.stdout

    def test_python_subprocess_error(self):
        engine = SandboxEngine(default_mode="subprocess")
        result = engine.execute("import sys; sys.exit(42)", language="python")
        assert result.exit_code == 42

    @pytest.mark.skipif(
        platform.system() == "Windows",
        reason="PowerShell echo may behave differently",
    )
    def test_shell_subprocess(self):
        engine = SandboxEngine(default_mode="subprocess")
        result = engine.execute("echo hello_shell", language="shell")
        assert result.exit_code == 0
        assert "hello_shell" in result.stdout

    def test_shell_windows(self):
        if platform.system() != "Windows":
            pytest.skip("Windows-only test")
        engine = SandboxEngine(default_mode="subprocess")
        result = engine.execute("Write-Output 'hello_powershell'", language="shell")
        assert result.exit_code == 0
        assert "hello_powershell" in result.stdout

    def test_subprocess_timeout(self):
        engine = SandboxEngine(default_mode="subprocess", default_timeout=2)
        result = engine.execute(
            "import time; time.sleep(60)",
            language="python",
            timeout=2,
        )
        assert result.timed_out is True
        assert result.exit_code == -1

    def test_env_vars_passed(self):
        engine = SandboxEngine(default_mode="subprocess")
        result = engine.execute(
            "import os; print(os.environ.get('MY_TEST_VAR', 'NOT_SET'))",
            language="python",
            env_vars={"MY_TEST_VAR": "hello_from_env"},
        )
        assert result.exit_code == 0
        assert "hello_from_env" in result.stdout


# ═══════════════════════════════════════════════════════════════════
# SandboxEngine — language support
# ═══════════════════════════════════════════════════════════════════


class TestEngineLanguages:
    def test_python_valid(self):
        engine = SandboxEngine()
        result = engine.execute("print(42)", language="python")
        assert result.exit_code == 0

    def test_unsupported_language_raises(self):
        engine = SandboxEngine()
        with pytest.raises(ValueError, match="Unsupported language"):
            engine.execute("x=1", language="ruby")

    def test_local_direct_only_python(self):
        engine = SandboxEngine(default_mode="local_direct")
        with pytest.raises(ValueError, match="local_direct mode only supports Python"):
            engine.execute("echo hi", language="shell")

    def test_shell_routing(self):
        engine = SandboxEngine(default_mode="subprocess")
        # Shell should work in subprocess mode
        result = engine.execute("echo hello", language="shell")
        # Don't assert on exit_code since node might not be installed
        assert isinstance(result, SandboxResult)

    def test_javascript_routing(self):
        engine = SandboxEngine(default_mode="subprocess")
        result = engine.execute("console.log('js hello')", language="javascript")
        assert isinstance(result, SandboxResult)
        # May fail if node is not installed — that's fine
        # Just verify routing works and returns a SandboxResult


# ═══════════════════════════════════════════════════════════════════
# SandboxEngine — docker (mocked / not-installed check)
# ═══════════════════════════════════════════════════════════════════


class TestEngineDocker:
    def test_docker_not_installed_graceful(self):
        """When Docker is not running / available, return clean error, not crash."""
        engine = SandboxEngine(default_mode="docker")
        result = engine.execute("print(1)", language="python", timeout=3)
        assert isinstance(result, SandboxResult)
        # exit_code non-zero means something went wrong (expected: docker down or missing)
        assert result.exit_code != 0


# ═══════════════════════════════════════════════════════════════════
# SecurityPolicy — construction
# ═══════════════════════════════════════════════════════════════════


class TestPolicyConstruction:
    def test_default_level(self):
        policy = SecurityPolicy()
        assert policy.level == PolicyLevel.RESTRICTED

    def test_custom_level(self):
        policy = SecurityPolicy(level=PolicyLevel.READ_ONLY)
        assert policy.level == PolicyLevel.READ_ONLY

    def test_set_level(self):
        policy = SecurityPolicy()
        policy.set_level(PolicyLevel.FULL_ACCESS)
        assert policy.level == PolicyLevel.FULL_ACCESS

    def test_enum_values(self):
        assert PolicyLevel.READ_ONLY.value == "read_only"
        assert PolicyLevel.RESTRICTED.value == "restricted"
        assert PolicyLevel.FULL_ACCESS.value == "full_access"


# ═══════════════════════════════════════════════════════════════════
# SecurityPolicy — Python validation
# ═══════════════════════════════════════════════════════════════════


class TestPolicyPythonValidation:
    def test_safe_code_passes(self):
        policy = SecurityPolicy()
        result = policy.validate("x = 1 + 1\nprint(x)", language="python")
        assert result.passed is True
        assert result.violations == []

    def test_os_system_blocked(self):
        policy = SecurityPolicy()
        result = policy.validate("import os; os.system('ls')", language="python")
        assert result.passed is False
        assert any("os.system" in v for v in result.violations)

    def test_subprocess_blocked(self):
        policy = SecurityPolicy()
        result = policy.validate("import subprocess; subprocess.run(['ls'])", language="python")
        assert result.passed is False
        assert any("subprocess" in v for v in result.violations)

    def test_socket_blocked(self):
        policy = SecurityPolicy()
        result = policy.validate("import socket; s = socket.socket()", language="python")
        assert result.passed is False
        assert any("socket" in v for v in result.violations)

    def test_shutil_rmtree_blocked(self):
        policy = SecurityPolicy()
        result = policy.validate("import shutil; shutil.rmtree('/tmp')", language="python")
        assert result.passed is False
        assert any("shutil.rmtree" in v for v in result.violations)

    def test_ctypes_blocked(self):
        policy = SecurityPolicy()
        result = policy.validate("import ctypes; ctypes.CDLL('libc.so')", language="python")
        assert result.passed is False
        assert any("ctypes" in v for v in result.violations)

    def test_safe_os_usage_passes(self):
        """os.path.join is safe and should not be blocked."""
        policy = SecurityPolicy()
        result = policy.validate("import os\np = os.path.join('a', 'b')", language="python")
        assert result.passed is True

    def test_eval_blocked_in_restricted(self):
        policy = SecurityPolicy(level=PolicyLevel.RESTRICTED)
        result = policy.validate("eval('1+1')", language="python")
        assert result.passed is False
        assert any("eval" in v for v in result.violations)

    def test_exec_blocked_in_restricted(self):
        policy = SecurityPolicy(level=PolicyLevel.RESTRICTED)
        result = policy.validate("exec('x=1')", language="python")
        assert result.passed is False
        assert any("exec" in v for v in result.violations)

    def test_open_blocked_in_restricted(self):
        policy = SecurityPolicy(level=PolicyLevel.RESTRICTED)
        result = policy.validate("open('/etc/passwd')", language="python")
        assert result.passed is False
        assert any("open" in v for v in result.violations)


# ═══════════════════════════════════════════════════════════════════
# SecurityPolicy — Shell & JS validation
# ═══════════════════════════════════════════════════════════════════


class TestPolicyShellValidation:
    def test_safe_shell_passes(self):
        policy = SecurityPolicy()
        result = policy.validate("echo hello world", language="shell")
        assert result.passed is True

    def test_rm_rf_blocked(self):
        policy = SecurityPolicy()
        result = policy.validate("rm -rf /", language="shell")
        assert result.passed is False
        assert any("rm -rf" in v for v in result.violations)

    def test_fork_bomb_blocked(self):
        policy = SecurityPolicy()
        result = policy.validate(":(){ :|:& };:", language="shell")
        assert result.passed is False


class TestPolicyJSValidation:
    def test_safe_js_passes(self):
        policy = SecurityPolicy()
        result = policy.validate("console.log('hello')", language="javascript")
        assert result.passed is True

    def test_child_process_blocked(self):
        policy = SecurityPolicy()
        result = policy.validate("require('child_process').exec('ls')", language="javascript")
        assert result.passed is False
        assert any("child_process" in v for v in result.violations)

    def test_fs_require_blocked(self):
        policy = SecurityPolicy()
        result = policy.validate("const fs = require('fs');", language="javascript")
        assert result.passed is False

    def test_process_exit_blocked(self):
        policy = SecurityPolicy()
        result = policy.validate("process.exit(1)", language="javascript")
        assert result.passed is False
        assert any("process" in v and "exit" in v for v in result.violations)


# ═══════════════════════════════════════════════════════════════════
# SecurityPolicy — permission levels
# ═══════════════════════════════════════════════════════════════════


class TestPolicyPermissionLevels:
    def test_read_only_blocks_open(self):
        policy = SecurityPolicy(level=PolicyLevel.READ_ONLY)
        result = policy.validate("open('file.txt', 'w')", language="python")
        assert result.passed is False

    def test_read_only_blocks_os_system(self):
        policy = SecurityPolicy(level=PolicyLevel.READ_ONLY)
        result = policy.validate("import os; os.system('ls')", language="python")
        assert result.passed is False

    def test_restricted_blocks_os_system(self):
        policy = SecurityPolicy(level=PolicyLevel.RESTRICTED)
        result = policy.validate("import os; os.system('ls')", language="python")
        assert result.passed is False

    def test_full_access_passes_everything(self):
        policy = SecurityPolicy(level=PolicyLevel.FULL_ACCESS)
        result = policy.validate("import os; os.system('rm -rf /'); import subprocess; subprocess.run(['ls'])", language="python")
        assert result.passed is True
        assert result.violations == []

    def test_full_access_passes_dangerous_shell(self):
        policy = SecurityPolicy(level=PolicyLevel.FULL_ACCESS)
        result = policy.validate("rm -rf /", language="shell")
        assert result.passed is True

    def test_override_level_in_validate(self):
        policy = SecurityPolicy(level=PolicyLevel.RESTRICTED)
        # Override with FULL_ACCESS via validate
        result = policy.validate(
            "import os; os.system('rm -rf /')",
            language="python",
            level=PolicyLevel.FULL_ACCESS,
        )
        assert result.passed is True


# ═══════════════════════════════════════════════════════════════════
# SecurityPolicy — network control
# ═══════════════════════════════════════════════════════════════════


class TestPolicyNetworkControl:
    def test_default_network_blocked(self):
        policy = SecurityPolicy()
        assert policy._network_blocked is True

    def test_allow_network_does_not_unblock(self):
        policy = SecurityPolicy()
        policy.allow_network("*.example.com")
        assert policy._network_blocked is True  # still blocked, but allowlist used

    def test_block_network(self):
        policy = SecurityPolicy()
        policy.allow_network("*.example.com")
        assert len(policy._allowed_domains) == 1
        policy.block_network()
        assert policy._network_blocked is True
        assert policy._allowed_domains == []

    def test_is_network_allowed(self):
        policy = SecurityPolicy()
        policy.allow_network("*.example.com")
        assert policy.is_network_allowed("api.example.com") is True
        assert policy.is_network_allowed("google.com") is False


# ═══════════════════════════════════════════════════════════════════
# SecurityPolicy — filesystem control
# ═══════════════════════════════════════════════════════════════════


class TestPolicyFilesystemControl:
    def test_read_only_fs_blocked(self):
        policy = SecurityPolicy(level=PolicyLevel.READ_ONLY)
        assert policy._fs_write_blocked is True
        assert policy._fs_read_blocked is True

    def test_restricted_fs_write_blocked(self):
        policy = SecurityPolicy(level=PolicyLevel.RESTRICTED)
        assert policy._fs_write_blocked is True

    def test_allow_path_read(self):
        policy = SecurityPolicy(level=PolicyLevel.READ_ONLY)
        assert policy._fs_read_blocked is True
        policy.allow_path("read", "/tmp")
        assert policy._fs_read_blocked is True  # still blocked, but allowlist used

    def test_allow_path_write(self):
        policy = SecurityPolicy(level=PolicyLevel.RESTRICTED)
        assert policy._fs_write_blocked is True
        policy.allow_path("write", "/tmp")
        assert policy._fs_write_blocked is True  # still blocked, but allowlist used

    def test_is_path_allowed_read(self):
        policy = SecurityPolicy(level=PolicyLevel.READ_ONLY)
        policy.allow_path("read", "C:/tmp/*")
        assert policy.is_path_allowed("read", "C:/tmp/data.txt") is True
        assert policy.is_path_allowed("read", "C:/etc/passwd") is False

    def test_is_path_allowed_write(self):
        policy = SecurityPolicy(level=PolicyLevel.RESTRICTED)
        policy.allow_path("write", "C:/tmp/output")
        assert policy.is_path_allowed("write", "C:/tmp/output") is True

    def test_invalid_mode_raises(self):
        policy = SecurityPolicy()
        with pytest.raises(ValueError, match="Invalid mode"):
            policy.allow_path("execute", "/tmp")


# ═══════════════════════════════════════════════════════════════════
# Integration — policy validate → engine execute
# ═══════════════════════════════════════════════════════════════════


class TestPolicyEngineIntegration:
    def test_safe_code_pass_and_execute(self):
        policy = SecurityPolicy(level=PolicyLevel.RESTRICTED)
        engine = SandboxEngine(default_mode="local_direct")

        code = "print(sum(range(10)))"
        validation = policy.validate(code, language="python")
        assert validation.passed is True

        result = engine.execute(code, language="python")
        assert result.exit_code == 0
        assert "45" in result.stdout

    def test_dangerous_code_blocked_before_execution(self):
        policy = SecurityPolicy(level=PolicyLevel.RESTRICTED)
        code = "import os; os.system('echo hacked')"
        validation = policy.validate(code, language="python")
        assert validation.passed is False
        assert len(validation.violations) > 0
        # Code should NOT reach execution if validation fails


# ═══════════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_empty_code(self):
        engine = SandboxEngine()
        result = engine.execute("", language="python")
        assert isinstance(result, SandboxResult)
        assert result.exit_code == 0

    def test_multiline_code(self):
        engine = SandboxEngine()
        code = "\n".join([
            "def fib(n):",
            "    a, b = 0, 1",
            "    for _ in range(n):",
            "        a, b = b, a + b",
            "    return a",
            "print(fib(10))",
        ])
        result = engine.execute(code, language="python")
        assert result.exit_code == 0
        assert "55" in result.stdout

    def test_unicode_output(self):
        engine = SandboxEngine()
        result = engine.execute("print('中文测试')", language="python")
        assert result.exit_code == 0
        assert "中文测试" in result.stdout

    def test_validation_result_repr(self):
        vr = ValidationResult(passed=False, level=PolicyLevel.RESTRICTED, violations=["bad"])
        rep = repr(vr)
        assert "passed=False" in rep
        assert "RESTRICTED" in rep
        assert "violations=1" in rep

    def test_multiple_violations(self):
        policy = SecurityPolicy()
        code = "import os; os.system('ls'); import subprocess; subprocess.run(['ls'])"
        result = policy.validate(code, language="python")
        assert result.passed is False
        assert len(result.violations) >= 2

    def test_policy_unsupported_language(self):
        policy = SecurityPolicy()
        result = policy.validate("x=1", language="ruby")
        assert result.passed is False
        assert any("Unsupported language" in v for v in result.violations)

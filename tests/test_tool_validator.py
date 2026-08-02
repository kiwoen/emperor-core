"""
Tests for jarvis.tools.validator — ToolCallValidator and safe_execute.

Covers:
  1. Schema registration and basic validation
  2. Validation error with structured error output
  3. safe_execute success path (validation + execution)
  4. safe_execute validation failure without retry callback
  5. safe_execute timeout protection
  6. safe_execute retry-with-feedback via llm_retry_callback
  7. safe_execute execution failure without retry
  8. Concurrent safe_execute with multiple tools
  9. ToolCallLog JSON serialization
 10. global_validator singleton works
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest
from pydantic import BaseModel, Field

from jarvis.tools.validator import (
    ExecutionError,
    SafeExecuteResult,
    ToolCallLog,
    ToolCallValidator,
    ValidationError,
    safe_execute,
    global_validator,
)


# ─── Test Pydantic Schemas ────────────────────────────────────


class DeleteParams(BaseModel):
    """Params for a delete tool."""
    file_paths: list[str] = Field(..., min_length=1, description="List of file paths to delete")
    recursive: bool = Field(default=False, description="Recursively delete directories")


class SearchParams(BaseModel):
    """Params for a search tool."""
    query: str = Field(..., min_length=1, description="Search query string")
    limit: int = Field(default=10, ge=1, le=100, description="Max results")


class MathParams(BaseModel):
    """Params for a math tool."""
    expression: str = Field(..., description="Mathematical expression to evaluate")
    precision: int = Field(default=2, ge=0, le=10, description="Decimal precision")


# ─── Test Execute Functions ───────────────────────────────────


async def mock_delete(validated: DeleteParams) -> dict:
    """Mock delete — returns a success response."""
    return {"deleted": len(validated.file_paths), "paths": validated.file_paths}


async def mock_search(validated: SearchParams) -> dict:
    """Mock search — returns results."""
    return {"query": validated.query, "results": [], "count": 0}


def mock_math_sync(params: MathParams) -> float:
    """Sync math function."""
    return round(eval(params.expression), params.precision)


async def mock_slow(_params):
    """Simulates a slow tool that exceeds timeout."""
    await asyncio.sleep(5.0)
    return {"done": True}


async def mock_failing(_params):
    """Always raises."""
    raise RuntimeError("database connection lost")


# ─── Fixtures ────────────────────────────────────────────────

@pytest.fixture
def validator() -> ToolCallValidator:
    v = ToolCallValidator()
    v.register("delete", DeleteParams)
    v.register("search", SearchParams)
    v.register("math", MathParams)
    return v


@pytest.fixture
def fresh_global() -> ToolCallValidator:
    """Reset global_validator for isolated tests."""
    global_validator._schemas.clear()
    yield global_validator
    global_validator._schemas.clear()


# ─── Test Case 1: Schema Registration & Basic Validation ─────


class TestRegistrationAndValidation:
    """Test schema registration and basic validation."""

    def test_register_and_validate_success(self, validator):
        """Valid params should return a Pydantic model instance."""
        result = validator.validate("delete", {"file_paths": ["/tmp/a.txt"]})
        assert isinstance(result, DeleteParams)
        assert result.file_paths == ["/tmp/a.txt"]
        assert result.recursive is False

    def test_register_and_validate_with_defaults(self, validator):
        """Optional params should get their default values."""
        result = validator.validate("search", {"query": "AI"})
        assert isinstance(result, SearchParams)
        assert result.query == "AI"
        assert result.limit == 10  # default

    def test_validate_unknown_tool(self, validator):
        """Unknown tool should raise KeyError."""
        with pytest.raises(KeyError, match="Unknown tool"):
            validator.validate("nonexistent_tool", {})

    def test_validate_missing_required(self, validator):
        """Missing required field should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            validator.validate("delete", {"recursive": True})
        assert "file_paths" in str(exc_info.value)
        assert len(exc_info.value.errors) >= 1
        assert exc_info.value.errors[0]["type"] == "missing"

    def test_validate_wrong_type(self, validator):
        """Wrong type should raise ValidationError with structured errors."""
        with pytest.raises(ValidationError) as exc_info:
            validator.validate("delete", {"file_paths": "not_a_list"})
        errors = exc_info.value.errors
        assert len(errors) >= 1
        assert errors[0]["loc"]  # location field

    def test_register_non_pydantic_raises(self):
        """Registering a non-Pydantic type should raise TypeError."""
        v = ToolCallValidator()
        with pytest.raises(TypeError, match="Pydantic"):
            v.register("bad", dict)  # type: ignore

    def test_list_tools(self, validator):
        """list_tools should return sorted tool names."""
        tools = validator.list_tools()
        assert tools == ["delete", "math", "search"]

    def test_get_schema_description(self, validator):
        """Schema description should include fields."""
        desc = validator.get_schema_description("delete")
        assert "file_paths" in desc
        assert "required" in desc
        assert "recursive" in desc


# ─── Test Case 2: safe_execute Success Path ──────────────────


class TestSafeExecuteSuccess:
    """Test the happy path of safe_execute."""

    @pytest.mark.asyncio
    async def test_safe_execute_success(self, validator):
        result = await safe_execute(
            tool_name="delete",
            params={"file_paths": ["/tmp/x.txt"]},
            execute_fn=mock_delete,
            validator=validator,
        )
        assert result.success is True
        assert result.attempts == 1
        assert result.result == {"deleted": 1, "paths": ["/tmp/x.txt"]}
        assert len(result.logs) == 1
        assert result.logs[0].validation_passed is True
        assert result.logs[0].error is None
        assert result.logs[0].latency_ms > 0

    @pytest.mark.asyncio
    async def test_safe_execute_with_defaults(self, validator):
        """Params with defaults should work."""
        result = await safe_execute(
            tool_name="search",
            params={"query": "AI agents"},
            execute_fn=mock_search,
            validator=validator,
        )
        assert result.success is True
        assert result.result == {"query": "AI agents", "results": [], "count": 0}

    @pytest.mark.asyncio
    async def test_safe_execute_sync_fn(self, validator):
        """Sync execute_fn should work (auto-await detection)."""
        result = await safe_execute(
            tool_name="math",
            params={"expression": "2 + 3 * 4", "precision": 2},
            execute_fn=mock_math_sync,
            validator=validator,
        )
        assert result.success is True
        assert result.result == 14.0


# ─── Test Case 3: Validation Failure Without Retry ───────────


class TestSafeExecuteValidationFailure:
    """Test validation failure in safe_execute (no retry callback)."""

    @pytest.mark.asyncio
    async def test_validation_failure_no_retry(self, validator):
        result = await safe_execute(
            tool_name="delete",
            params={"recursive": True},  # missing required file_paths
            execute_fn=mock_delete,
            validator=validator,
            max_retries=0,
        )
        assert result.success is False
        assert result.error_type == "validation"
        assert result.validation_errors is not None
        assert len(result.validation_errors) >= 1

    @pytest.mark.asyncio
    async def test_unknown_tool_immediate_failure(self, validator):
        """Unknown tool should fail immediately without retries."""
        result = await safe_execute(
            tool_name="imagination_tool",
            params={},
            execute_fn=mock_delete,
            validator=validator,
            max_retries=3,
        )
        assert result.success is False
        assert result.error_type == "validation"
        assert "Unknown tool" in (result.error or "")


# ─── Test Case 4: Timeout Protection ─────────────────────────


class TestSafeExecuteTimeout:
    """Test timeout protection in safe_execute."""

    @pytest.mark.asyncio
    async def test_timeout_triggers(self, validator):
        validator.register("slow_tool", SearchParams)
        result = await safe_execute(
            tool_name="slow_tool",
            params={"query": "test"},
            execute_fn=mock_slow,
            validator=validator,
            timeout_seconds=0.5,
            max_retries=0,
        )
        assert result.success is False
        assert result.error_type == "timeout"
        assert "timed out" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_timeout_with_retry_callback(self, validator):
        """When timeout occurs with a retry callback, it should retry."""
        validator.register("slow_tool", SearchParams)
        call_count = [0]

        async def retry_cb(_tool_name, _err_ctx, _last_params):
            call_count[0] += 1
            return {"query": "adjusted query"}

        result = await safe_execute(
            tool_name="slow_tool",
            params={"query": "test", "limit": 5},
            execute_fn=mock_slow,
            validator=validator,
            timeout_seconds=0.2,
            max_retries=1,
            llm_retry_callback=retry_cb,
        )
        # The slow tool always times out, so after retries it ultimately fails
        assert result.success is False
        assert result.error_type == "timeout"
        assert call_count[0] >= 1  # callback was called at least once


# ─── Test Case 5: Retry-with-Feedback via LLM Callback ───────


class TestRetryWithFeedback:
    """Test the LLM retry callback mechanism."""

    @pytest.mark.asyncio
    async def test_validation_retry_callback(self, validator):
        """Validation fails → LLM callback fixes params → succeeds."""
        call_count = [0]
        error_ctx_seen = []

        async def smart_retry(tool_name, error_context, last_params):
            call_count[0] += 1
            error_ctx_seen.append(error_context)
            # LLM "fixes" by adding the missing required field
            return {"file_paths": ["/fixed/path.txt"], "recursive": True}

        result = await safe_execute(
            tool_name="delete",
            params={"recursive": True},  # missing file_paths
            execute_fn=mock_delete,
            validator=validator,
            max_retries=2,
            llm_retry_callback=smart_retry,
        )
        assert result.success is True
        assert result.attempts == 2  # 1 failed validation + 1 success
        assert call_count[0] == 1
        assert result.result == {"deleted": 1, "paths": ["/fixed/path.txt"]}
        # Error context should include schema details
        assert "file_paths" in error_ctx_seen[0]

    @pytest.mark.asyncio
    async def test_execution_retry_callback(self, validator):
        """Execution fails → LLM callback adjusts → succeeds on retry."""
        call_count = [0]
        first_call = [True]

        async def flaky_tool(_params):
            if first_call[0]:
                first_call[0] = False
                raise RuntimeError("transient network error")
            return {"status": "ok"}

        async def smart_retry(tool_name, error_context, last_params):
            call_count[0] += 1
            return last_params  # params are fine, just retry

        result = await safe_execute(
            tool_name="delete",
            params={"file_paths": ["/tmp/retry.txt"]},
            execute_fn=flaky_tool,
            validator=validator,
            max_retries=2,
            llm_retry_callback=smart_retry,
        )
        assert result.success is True
        assert call_count[0] == 1
        assert result.attempts == 2

    @pytest.mark.asyncio
    async def test_retry_exhausted(self, validator):
        """After exhausting all retries, return failure."""
        async def always_bad(_tool_name, _err_ctx, _last_params):
            # LLM keeps returning invalid params (missing file_paths)
            return {"recursive": True}

        result = await safe_execute(
            tool_name="delete",
            params={"recursive": True},
            execute_fn=mock_delete,
            validator=validator,
            max_retries=1,
            llm_retry_callback=always_bad,
        )
        assert result.success is False
        assert result.error_type == "validation"
        assert result.attempts == 2  # initial + 1 retry


# ─── Test Case 6: Execution Failure Without Retry ────────────


class TestExecutionFailure:
    """Test execution failures."""

    @pytest.mark.asyncio
    async def test_execution_failure_no_retry(self, validator):
        """Execution error without retry callback should return failure."""
        result = await safe_execute(
            tool_name="delete",
            params={"file_paths": ["/tmp/x.txt"]},
            execute_fn=mock_failing,
            validator=validator,
            max_retries=0,
        )
        assert result.success is False
        assert result.error_type == "execution"
        assert "database connection lost" in (result.error or "")

    @pytest.mark.asyncio
    async def test_pre_validate_hook(self, validator):
        """pre_validate hook can transform params before validation."""
        def add_default_path(params):
            if "file_paths" not in params:
                params["file_paths"] = ["/default/path.txt"]
            return params

        result = await safe_execute(
            tool_name="delete",
            params={},  # missing file_paths
            execute_fn=mock_delete,
            validator=validator,
            max_retries=0,
            pre_validate=add_default_path,
        )
        assert result.success is True
        assert result.result == {"deleted": 1, "paths": ["/default/path.txt"]}


# ─── Test Case 7: ToolCallLog JSON Serialization ─────────────


class TestToolCallLog:
    """Test ToolCallLog JSON output."""

    def test_basic_log_json(self):
        log = ToolCallLog(
            tool_name="delete",
            params={"file_paths": ["/tmp/a.txt"]},
            result={"deleted": 1},
            latency_ms=12.34,
        )
        data = json.loads(log.to_json())
        assert data["tool_name"] == "delete"
        assert data["params"] == {"file_paths": ["/tmp/a.txt"]}
        assert data["result"] == {"deleted": 1}
        assert data["latency_ms"] == 12.34
        assert data["validation_passed"] is True
        assert "timestamp" in data

    def test_error_log_json(self):
        log = ToolCallLog(
            tool_name="search",
            params={"query": ""},
            error="Validation failed",
            validation_passed=False,
            latency_ms=0.5,
        )
        data = json.loads(log.to_json())
        assert data["error"] == "Validation failed"
        assert data["validation_passed"] is False
        assert "result" not in data  # result is None, should be omitted


# ─── Test Case 8: Concurrent Execution ───────────────────────


class TestConcurrentExecution:
    """Test that multiple safe_execute calls can run concurrently."""

    @pytest.mark.asyncio
    async def test_concurrent_safe_execute(self, validator):
        """Multiple concurrent tool calls should all succeed independently."""
        async def run_delete(file_path: str):
            return await safe_execute(
                tool_name="delete",
                params={"file_paths": [file_path]},
                execute_fn=mock_delete,
                validator=validator,
            )

        results = await asyncio.gather(
            run_delete("/tmp/a.txt"),
            run_delete("/tmp/b.txt"),
            run_delete("/tmp/c.txt"),
        )
        for r in results:
            assert r.success is True
            assert r.result["deleted"] == 1

        paths = [r.result["paths"][0] for r in results]
        assert sorted(paths) == ["/tmp/a.txt", "/tmp/b.txt", "/tmp/c.txt"]


# ─── Test Case 9: global_validator Singleton ─────────────────


class TestGlobalValidator:
    """Test the global_validator singleton convenience."""

    def test_global_validator_is_singleton(self):
        from jarvis.tools.validator import global_validator as g1
        from jarvis.tools.validator import global_validator as g2
        assert g1 is g2

    def test_global_register_and_validate(self, fresh_global):
        fresh_global.register("math", MathParams)
        result = fresh_global.validate("math", {"expression": "1+1"})
        assert isinstance(result, MathParams)
        assert result.expression == "1+1"

    def test_global_list_tools(self, fresh_global):
        fresh_global.register("math", MathParams)
        fresh_global.register("search", SearchParams)
        assert "math" in fresh_global.list_tools()
        assert "search" in fresh_global.list_tools()


# ─── Test Case 10: Latency Tracking ──────────────────────────


class TestLatencyTracking:
    """Test that latency_ms is accurately measured."""

    @pytest.mark.asyncio
    async def test_latency_in_logs(self, validator):
        result = await safe_execute(
            tool_name="delete",
            params={"file_paths": ["/tmp/latency.txt"]},
            execute_fn=mock_delete,
            validator=validator,
        )
        assert result.total_latency_ms > 0
        assert result.logs[0].latency_ms > 0
        assert result.logs[0].latency_ms <= result.total_latency_ms

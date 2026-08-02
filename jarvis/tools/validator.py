"""
Tool Call Validator — Pydantic schema validation + retry + timeout.

Solves Tool Call Hallucination (Failure Mode 1 from AI Agent trends 2026):
  - Validates every tool call parameter against a Pydantic schema
  - On validation/execution failure, feeds structured error back for retry
  - Timeout protection with configurable deadline (default 30s)
  - JSON structured logging for every invocation

Integration:
  Designed to complement jarvis.tool_guard.ToolGuardMiddleware.
  While ToolGuardMiddleware focuses on security (SQL injection, rate limiting, PII),
  ToolCallValidator focuses on schema correctness and execution reliability.

Usage:
    from pydantic import BaseModel
    from jarvis.tools.validator import ToolCallValidator, safe_execute

    class DeleteParams(BaseModel):
        file_paths: list[str]
        recursive: bool = False

    # Register a tool schema
    validator = ToolCallValidator()
    validator.register("delete", DeleteParams)

    # Safe execution with retry
    result = await safe_execute(
        tool_name="delete",
        params={"file_paths": ["/tmp/test.txt"]},
        execute_fn=my_delete_fn,
        validator=validator,
    )

    # With LLM retry callback
    result = await safe_execute(
        tool_name="delete",
        params=bad_params,
        execute_fn=my_delete_fn,
        validator=validator,
        llm_retry_callback=my_llm_call,  # retries with error context
    )
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from pydantic import BaseModel, ValidationError as PydanticValidationError

logger = logging.getLogger("jarvis.tools.validator")


# ═══════════════════════════════════════════════════════════════════
# Data classes
# ═══════════════════════════════════════════════════════════════════


class ValidationError(Exception):
    """Raised when tool call parameters fail Pydantic validation."""

    def __init__(self, tool_name: str, errors: list[dict]):
        self.tool_name = tool_name
        self.errors = errors
        detail = json.dumps(errors, indent=2, ensure_ascii=False)
        super().__init__(f"Tool '{tool_name}' parameter validation failed:\n{detail}")


class ExecutionError(Exception):
    """Raised when a tool execution fails after all retries."""

    def __init__(self, tool_name: str, attempts: int, last_error: str):
        self.tool_name = tool_name
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(
            f"Tool '{tool_name}' failed after {attempts} attempt(s). "
            f"Last error: {last_error}"
        )


class TimeoutError(Exception):
    """Raised when a tool execution exceeds the configured timeout."""

    def __init__(self, tool_name: str, timeout_seconds: float):
        self.tool_name = tool_name
        self.timeout_seconds = timeout_seconds
        super().__init__(f"Tool '{tool_name}' timed out after {timeout_seconds}s")


@dataclass
class ToolCallLog:
    """Structured JSON log entry for a single tool call."""

    tool_name: str
    params: dict
    result: Any = None
    error: Optional[str] = None
    latency_ms: float = 0.0
    attempt: int = 1
    validation_passed: bool = True
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())

    def to_json(self) -> str:
        """Serialize to JSON string, omitting None fields."""
        data = {
            "tool_name": self.tool_name,
            "params": _safe_serialize(self.params),
            "latency_ms": round(self.latency_ms, 3),
            "attempt": self.attempt,
            "validation_passed": self.validation_passed,
            "timestamp": self.timestamp,
        }
        if self.result is not None:
            data["result"] = _safe_serialize(self.result)
        if self.error is not None:
            data["error"] = self.error
        return json.dumps(data, ensure_ascii=False, default=str)


@dataclass
class SafeExecuteResult:
    """Result of a safe_execute call."""

    success: bool
    result: Any = None
    error: Optional[str] = None
    error_type: str = ""  # "validation" | "execution" | "timeout"
    attempts: int = 0
    total_latency_ms: float = 0.0
    logs: list[ToolCallLog] = field(default_factory=list)
    validation_errors: Optional[list[dict]] = None


# ═══════════════════════════════════════════════════════════════════
# ToolCallValidator
# ═══════════════════════════════════════════════════════════════════


class ToolCallValidator:
    """Validates tool call parameters against registered Pydantic schemas.

    Each tool registers a Pydantic model that defines its expected parameters.
    Before execution, parameters are validated against this schema, catching
    LLM hallucinations (wrong parameter names, type mismatches, missing required
    fields) before they reach the actual tool function.

    Usage:
        from pydantic import BaseModel

        class SearchParams(BaseModel):
            query: str
            limit: int = 10

        validator = ToolCallValidator()
        validator.register("search", SearchParams)
        validator.register("delete", DeleteParams)

        # Validate params
        validated = validator.validate("search", {"query": "AI agents"})
        # validated is a SearchParams instance

        # Invalid params raise ValidationError
        try:
            validator.validate("search", {"limit": 5})  # missing 'query'
        except ValidationError as e:
            print(e.errors)  # structured error list for LLM feedback
    """

    def __init__(self):
        self._schemas: dict[str, type[BaseModel]] = {}

    # ── Schema registration ──────────────────────────────────────

    def register(self, tool_name: str, schema: type[BaseModel]) -> None:
        """Register a Pydantic model as the parameter schema for a tool.

        Args:
            tool_name: Unique tool identifier (e.g. "delete", "search").
            schema: A Pydantic BaseModel subclass defining expected parameters.

        Raises:
            TypeError: If schema is not a Pydantic BaseModel subclass.
        """
        if not (isinstance(schema, type) and issubclass(schema, BaseModel)):
            raise TypeError(
                f"Schema for '{tool_name}' must be a Pydantic BaseModel subclass, "
                f"got {type(schema).__name__}"
            )
        self._schemas[tool_name] = schema
        logger.debug("Registered schema for tool '%s': %s", tool_name, schema.__name__)

    def unregister(self, tool_name: str) -> None:
        """Remove a tool schema from the registry."""
        self._schemas.pop(tool_name, None)

    def get_schema(self, tool_name: str) -> Optional[type[BaseModel]]:
        """Get the registered Pydantic schema for a tool, or None."""
        return self._schemas.get(tool_name)

    def list_tools(self) -> list[str]:
        """Return all registered tool names."""
        return sorted(self._schemas.keys())

    # ── Validation ──────────────────────────────────────────────

    def validate(self, tool_name: str, params: dict) -> BaseModel:
        """Validate tool call parameters against the registered schema.

        Args:
            tool_name: The tool whose schema to validate against.
            params: Raw parameters dict from the LLM.

        Returns:
            An instance of the registered Pydantic model with validated data.

        Raises:
            KeyError: If tool_name is not registered.
            ValidationError: If parameters fail schema validation.
        """
        schema = self._schemas.get(tool_name)
        if schema is None:
            raise KeyError(f"Unknown tool: '{tool_name}'. Registered: {self.list_tools()}")

        try:
            return schema.model_validate(params)
        except PydanticValidationError as e:
            errors = _format_pydantic_errors(e)
            raise ValidationError(tool_name, errors) from e

    def get_schema_description(self, tool_name: str) -> str:
        """Return a human-readable description of the tool's parameter schema.

        Useful as context for LLM retry prompts.
        """
        schema = self._schemas.get(tool_name)
        if schema is None:
            return f"Unknown tool: {tool_name}"

        fields = []
        for name, field_info in schema.model_fields.items():
            required = field_info.is_required()
            annotation = _type_name(field_info.annotation)
            desc = field_info.description or ""
            default = field_info.default if not required else None

            parts = [f"  - {name}: {annotation}"]
            if required:
                parts.append("(required)")
            else:
                parts.append(f"(optional, default={default})")
            if desc:
                parts.append(f"— {desc}")
            fields.append(" ".join(parts))

        return f"Tool '{tool_name}' parameters:\n" + "\n".join(fields)


# ═══════════════════════════════════════════════════════════════════
# safe_execute — validated + retry + timeout
# ═══════════════════════════════════════════════════════════════════


async def safe_execute(
    tool_name: str,
    params: dict,
    execute_fn: Callable,
    validator: ToolCallValidator,
    timeout_seconds: float = 30.0,
    max_retries: int = 2,
    llm_retry_callback: Optional[Callable[[str, str, dict], Any]] = None,
    pre_validate: Optional[Callable[[dict], dict]] = None,
) -> SafeExecuteResult:
    """Execute a tool call with validation, retry-with-feedback, and timeout.

    Pipeline:
      1. Validate params against registered Pydantic schema
      2. If validation fails and llm_retry_callback is set, feed error back
         to LLM via callback and retry validation (up to max_retries)
      3. Execute the validated function with timeout protection
      4. On execution failure, optionally retry via llm_retry_callback
      5. Log every attempt as structured JSON

    Args:
        tool_name: Name of the tool to execute.
        params: Raw parameter dict (before validation).
        execute_fn: Async or sync callable that receives validated params.
        validator: ToolCallValidator with registered schemas.
        timeout_seconds: Maximum execution time (default 30s).
        max_retries: Max retry attempts for validation/execution (default 2).
        llm_retry_callback: Optional async callable(tool_name, error_context, last_params)
            → new_params_dict. Called when validation/execution fails, used to
            retry with LLM-corrected parameters.
        pre_validate: Optional hook to transform params before validation.

    Returns:
        SafeExecuteResult with success status, result, logs, and error details.
    """
    all_logs: list[ToolCallLog] = []
    t_start = time.perf_counter()

    current_params = dict(params)

    for attempt in range(1, max_retries + 2):  # 1 initial + max_retries retries
        attempt_start = time.perf_counter()
        log = ToolCallLog(
            tool_name=tool_name,
            params=current_params,
            attempt=attempt,
        )

        # ── Step 1: Pre-validate hook ──
        if pre_validate is not None:
            try:
                current_params = pre_validate(current_params)
                log.params = current_params
            except Exception as e:
                log.validation_passed = False
                log.error = f"Pre-validation hook failed: {e}"
                log.latency_ms = (time.perf_counter() - attempt_start) * 1000
                all_logs.append(log)
                _emit_log(log)
                return SafeExecuteResult(
                    success=False,
                    error=log.error,
                    error_type="validation",
                    attempts=attempt,
                    total_latency_ms=(time.perf_counter() - t_start) * 1000,
                    logs=all_logs,
                    validation_errors=[{"msg": str(e)}],
                )

        # ── Step 2: Pydantic schema validation ──
        validated: BaseModel
        try:
            validated = validator.validate(tool_name, current_params)
            log.validation_passed = True
        except KeyError as e:
            log.validation_passed = False
            log.error = str(e)
            log.latency_ms = (time.perf_counter() - attempt_start) * 1000
            all_logs.append(log)
            _emit_log(log)
            return SafeExecuteResult(
                success=False,
                error=log.error,
                error_type="validation",
                attempts=attempt,
                total_latency_ms=(time.perf_counter() - t_start) * 1000,
                logs=all_logs,
            )
        except ValidationError as e:
            log.validation_passed = False
            log.error = str(e)
            log.latency_ms = (time.perf_counter() - attempt_start) * 1000
            all_logs.append(log)
            _emit_log(log)

            # Build error context for LLM retry
            last_error = str(e)

            if attempt <= max_retries and llm_retry_callback is not None:
                error_context = _build_error_context(
                    tool_name, validator, e.errors, current_params
                )
                logger.info(
                    "Validation failed for '%s' (attempt %d/%d), retrying with LLM feedback",
                    tool_name, attempt, max_retries + 1,
                )
                try:
                    current_params = dict(
                        await _invoke_callback(
                            llm_retry_callback, tool_name, error_context, current_params
                        )
                    )
                except Exception as cb_err:
                    # Callback itself failed — can't retry
                    return SafeExecuteResult(
                        success=False,
                        error=f"LLM retry callback failed: {cb_err}",
                        error_type="validation",
                        attempts=attempt,
                        total_latency_ms=(time.perf_counter() - t_start) * 1000,
                        logs=all_logs,
                        validation_errors=e.errors,
                    )
                continue  # retry validation with corrected params
            else:
                return SafeExecuteResult(
                    success=False,
                    error=last_error,
                    error_type="validation",
                    attempts=attempt,
                    total_latency_ms=(time.perf_counter() - t_start) * 1000,
                    logs=all_logs,
                    validation_errors=e.errors,
                )

        # ── Step 3: Execute with timeout ──
        try:
            result = await asyncio.wait_for(
                _invoke_execute(execute_fn, validated, current_params),
                timeout=timeout_seconds,
            )
            log.result = result
            log.latency_ms = (time.perf_counter() - attempt_start) * 1000
            all_logs.append(log)
            _emit_log(log)

            return SafeExecuteResult(
                success=True,
                result=result,
                attempts=attempt,
                total_latency_ms=(time.perf_counter() - t_start) * 1000,
                logs=all_logs,
            )

        except asyncio.TimeoutError:
            log.error = f"Tool '{tool_name}' timed out after {timeout_seconds}s"
            log.latency_ms = (time.perf_counter() - attempt_start) * 1000
            all_logs.append(log)
            _emit_log(log)

            if attempt <= max_retries and llm_retry_callback is not None:
                error_context = (
                    f"Tool '{tool_name}' timed out after {timeout_seconds}s. "
                    f"Consider reducing the scope of parameters or splitting into smaller calls."
                )
                logger.info(
                    "Timeout for '%s' (attempt %d/%d), retrying with LLM feedback",
                    tool_name, attempt, max_retries + 1,
                )
                try:
                    current_params = dict(
                        await _invoke_callback(
                            llm_retry_callback, tool_name, error_context, current_params
                        )
                    )
                except Exception:
                    return SafeExecuteResult(
                        success=False,
                        error=log.error,
                        error_type="timeout",
                        attempts=attempt,
                        total_latency_ms=(time.perf_counter() - t_start) * 1000,
                        logs=all_logs,
                    )
                continue
            else:
                return SafeExecuteResult(
                    success=False,
                    error=log.error,
                    error_type="timeout",
                    attempts=attempt,
                    total_latency_ms=(time.perf_counter() - t_start) * 1000,
                    logs=all_logs,
                )

        except Exception as e:
            log.error = f"Tool execution failed: {e}"
            log.latency_ms = (time.perf_counter() - attempt_start) * 1000
            all_logs.append(log)
            _emit_log(log)

            if attempt <= max_retries and llm_retry_callback is not None:
                error_context = (
                    f"Tool '{tool_name}' execution failed with error: {e}. "
                    f"Current params: {json.dumps(current_params, default=str)}. "
                    f"Please correct the parameters and retry."
                )
                logger.info(
                    "Execution failed for '%s' (attempt %d/%d), retrying with LLM feedback",
                    tool_name, attempt, max_retries + 1,
                )
                try:
                    current_params = dict(
                        await _invoke_callback(
                            llm_retry_callback, tool_name, error_context, current_params
                        )
                    )
                except Exception:
                    return SafeExecuteResult(
                        success=False,
                        error=str(e),
                        error_type="execution",
                        attempts=attempt,
                        total_latency_ms=(time.perf_counter() - t_start) * 1000,
                        logs=all_logs,
                    )
                continue
            else:
                return SafeExecuteResult(
                    success=False,
                    error=str(e),
                    error_type="execution",
                    attempts=attempt,
                    total_latency_ms=(time.perf_counter() - t_start) * 1000,
                    logs=all_logs,
                )

    # Should not reach here, but safety net
    return SafeExecuteResult(
        success=False,
        error="Exhausted all retry attempts",
        error_type="execution",
        attempts=max_retries + 1,
        total_latency_ms=(time.perf_counter() - t_start) * 1000,
        logs=all_logs,
    )


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════


def _format_pydantic_errors(exc: PydanticValidationError) -> list[dict]:
    """Convert Pydantic ValidationError into a list of structured dicts
    suitable for logging and LLM feedback."""
    errors = []
    for err in exc.errors():
        errors.append({
            "loc": " → ".join(str(loc) for loc in err["loc"]),
            "msg": err["msg"],
            "type": err["type"],
        })
    return errors


def _build_error_context(
    tool_name: str,
    validator: ToolCallValidator,
    errors: list[dict],
    last_params: dict,
) -> str:
    """Build an error context string for LLM retry feedback."""
    schema_desc = validator.get_schema_description(tool_name)
    error_details = json.dumps(errors, indent=2, ensure_ascii=False)
    params_sent = json.dumps(last_params, indent=2, ensure_ascii=False, default=str)

    return (
        f"Tool call to '{tool_name}' failed parameter validation.\n\n"
        f"--- Expected Schema ---\n{schema_desc}\n\n"
        f"--- Validation Errors ---\n{error_details}\n\n"
        f"--- Params Sent ---\n{params_sent}\n\n"
        f"Please correct the parameters to match the expected schema and retry."
    )


def _type_name(annotation: Any) -> str:
    """Get a human-readable type name from a type annotation."""
    if annotation is None:
        return "Any"
    if hasattr(annotation, "__name__"):
        return annotation.__name__
    origin = getattr(annotation, "__origin__", None)
    if origin is not None:
        args = getattr(annotation, "__args__", ())
        arg_names = ", ".join(_type_name(a) for a in args)
        origin_name = getattr(origin, "__name__", str(origin))
        return f"{origin_name}[{arg_names}]"
    return str(annotation)


async def _invoke_execute(execute_fn: Callable, validated: BaseModel, raw_params: dict) -> Any:
    """Invoke execute_fn with either the validated model or raw params dict,
    depending on what the function accepts. Supports both sync and async functions."""
    import inspect

    try:
        sig = inspect.signature(execute_fn)
        param_names = list(sig.parameters.keys())
    except (ValueError, TypeError):
        # Can't inspect — try with validated model first
        pass

    # Try model instance first (preferred), fall back to dict
    try:
        result = execute_fn(validated)
    except TypeError:
        result = execute_fn(raw_params)

    if inspect.isawaitable(result):
        result = await result
    return result


async def _invoke_callback(
    callback: Callable,
    tool_name: str,
    error_context: str,
    last_params: dict,
) -> Any:
    """Invoke the LLM retry callback. Supports sync and async."""
    import inspect

    result = callback(tool_name, error_context, last_params)
    if inspect.isawaitable(result):
        result = await result
    return result


def _safe_serialize(obj: Any) -> Any:
    """Safely serialize an object for JSON logging, truncating large values."""
    if isinstance(obj, str) and len(obj) > 1000:
        return obj[:1000] + "..."
    if isinstance(obj, (int, float, bool, type(None))):
        return obj
    if isinstance(obj, dict):
        return {k: _safe_serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe_serialize(v) for v in obj]
    if isinstance(obj, BaseModel):
        return _safe_serialize(obj.model_dump())
    return str(obj)[:500]


def _emit_log(log: ToolCallLog) -> None:
    """Emit a tool call log entry as a JSON line to the logger."""
    logger.info("tool_call %s", log.to_json())


# ═══════════════════════════════════════════════════════════════════
# Global convenience
# ═══════════════════════════════════════════════════════════════════

global_validator = ToolCallValidator()

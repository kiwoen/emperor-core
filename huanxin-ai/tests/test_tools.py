"""
test_tools.py — Tests for HUANXIN Function Calling standardisation.

Covers:
    1. ToolDef creation and properties
    2. @tool decorator — parameter extraction, docstring, ToolResult
    3. ToolRegistry — register, get, list, unregister, singleton
    4. Schema generation — to_openai_schema / to_anthropic_schema
    5. Tool execution — execute_tool, ToolResult
    6. Built-in tools — import and basic execution (12 tools)
    7. LLMEngine FC loop — chat_sync with tools + tool_registry
"""

# NOTE: no `from __future__ import annotations` here — it would turn
# int/float annotations into strings, breaking @tool's signature inspection.
import importlib
import json
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ═══════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def reset_global_registry():
    """Reset the global ToolRegistry singleton before each test."""
    from huanxin.tools.registry import reset_registry

    reset_registry()


@pytest.fixture
def fresh_builtin():
    """Force-reload builtin module so 12 tools register into current global registry."""
    from huanxin.tools.registry import reset_registry

    reset_registry()
    # Force fresh re-import so @tool(auto_register=True) re-fires
    sys.modules.pop("huanxin.tools.builtin", None)
    import huanxin.tools.builtin as builtin_mod

    return builtin_mod


# ═══════════════════════════════════════════════════════════════════
# 1. ToolDef
# ═══════════════════════════════════════════════════════════════════


class TestToolDef:
    """Tests for ToolDef dataclass."""

    def test_create_minimal(self):
        from huanxin.tools.base import ToolDef

        td = ToolDef(name="test_tool", description="A test tool")
        assert td.name == "test_tool"
        assert td.description == "A test tool"
        assert td.parameters == {"type": "object", "properties": {}}
        assert td.func is None
        assert td.category == "general"

    def test_create_full(self):
        from huanxin.tools.base import ToolDef

        def dummy():
            pass

        params = {"type": "object", "properties": {"x": {"type": "integer"}}}
        td = ToolDef(
            name="full_tool",
            description="Full featured tool",
            parameters=params,
            func=dummy,
            category="math",
        )
        assert td.name == "full_tool"
        assert td.parameters == params
        assert td.func is dummy
        assert td.category == "math"

    def test_to_openai_schema(self):
        from huanxin.tools.base import ToolDef

        td = ToolDef(
            name="add",
            description="Add two numbers",
            parameters={
                "type": "object",
                "properties": {
                    "a": {"type": "integer"},
                    "b": {"type": "integer"},
                },
                "required": ["a", "b"],
            },
        )
        schema = td.to_openai_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "add"
        assert schema["function"]["description"] == "Add two numbers"
        assert "a" in schema["function"]["parameters"]["properties"]
        assert "b" in schema["function"]["parameters"]["properties"]

    def test_to_anthropic_schema(self):
        from huanxin.tools.base import ToolDef

        td = ToolDef(
            name="add",
            description="Add two numbers",
            parameters={
                "type": "object",
                "properties": {
                    "a": {"type": "integer"},
                    "b": {"type": "integer"},
                },
                "required": ["a", "b"],
            },
        )
        schema = td.to_anthropic_schema()
        assert schema["name"] == "add"
        assert "input_schema" in schema
        assert schema["input_schema"]["type"] == "object"
        assert "a" in schema["input_schema"]["properties"]
        assert schema["input_schema"]["required"] == ["a", "b"]


# ═══════════════════════════════════════════════════════════════════
# 2. @tool decorator
# ═══════════════════════════════════════════════════════════════════


class TestToolDecorator:
    """Tests for @tool decorator."""

    def test_basic_decorator(self):
        from huanxin.tools.base import tool, ToolDef, ToolResult

        @tool(category="utility")
        def greet(name: str) -> str:
            """Say hello to someone."""
            return f"Hello, {name}!"

        assert hasattr(greet, "tool_def")
        td = greet.tool_def
        assert isinstance(td, ToolDef)
        assert td.name == "greet"
        assert td.description == "Say hello to someone."
        assert td.category == "utility"
        assert td.parameters["type"] == "object"

    def test_decorator_parameter_extraction(self):
        from huanxin.tools.base import tool

        @tool(category="math")
        def add(a: int, b: float = 0.0) -> float:
            """Add two numbers together."""
            return a + b

        td = add.tool_def
        props = td.parameters["properties"]
        # int → "integer", float → "number"
        assert props["a"]["type"] == "integer"
        assert props["b"]["type"] == "number"
        # b has default → should not be required
        assert "a" in td.parameters.get("required", [])
        assert "b" not in td.parameters.get("required", [])

    def test_decorator_optional_types(self):
        from huanxin.tools.base import tool

        @tool(category="test")
        def opt_func(name: str = "default", count: int = 0) -> str:
            """Optional params."""
            return f"{name}:{count}"

        td = opt_func.tool_def
        assert td.parameters.get("required", []) == []

    def test_decorator_wraps_result(self):
        from huanxin.tools.base import tool, ToolResult

        @tool(category="utility")
        def echo(msg: str) -> str:
            """Echo the message."""
            return msg

        result = echo(msg="hello")
        assert isinstance(result, ToolResult)
        assert result.success is True
        assert result.data == "hello"
        assert result.duration_ms >= 0

    def test_decorator_catches_exceptions(self):
        from huanxin.tools.base import tool, ToolResult

        @tool(category="test")
        def fail():
            """This always fails."""
            raise ValueError("intentional error")

        result = fail()
        assert isinstance(result, ToolResult)
        assert result.success is False
        assert "intentional error" in result.error

    def test_decorator_auto_register(self):
        from huanxin.tools.base import tool
        from huanxin.tools.registry import get_registry

        @tool(category="utility", auto_register=True)
        def auto_reg(x: int) -> int:
            """Auto registered."""
            return x * 2

        reg = get_registry()
        tool_def = reg.get_tool("auto_reg")
        assert tool_def is not None
        assert tool_def.name == "auto_reg"
        assert tool_def.func is not None

    def test_decorator_custom_name_desc(self):
        from huanxin.tools.base import tool

        @tool(name="custom_name", description="Custom description", category="util")
        def whatever():
            """Original docstring."""
            pass

        td = whatever.tool_def
        assert td.name == "custom_name"
        assert td.description == "Custom description"


# ═══════════════════════════════════════════════════════════════════
# 3. ToolRegistry
# ═══════════════════════════════════════════════════════════════════


class TestToolRegistry:
    """Tests for ToolRegistry."""

    def test_singleton_same_instance(self):
        from huanxin.tools.registry import get_registry, ToolRegistry

        r1 = get_registry()
        r2 = get_registry()
        assert r1 is r2
        assert isinstance(r1, ToolRegistry)

    def test_register_and_get(self):
        from huanxin.tools.registry import get_registry
        from huanxin.tools.base import ToolDef

        reg = get_registry()
        td = ToolDef(name="tool_a", description="Tool A", category="cat1")
        reg.register_tool(td)

        retrieved = reg.get_tool("tool_a")
        assert retrieved is td
        assert retrieved.name == "tool_a"

    def test_register_duplicate_raises(self):
        from huanxin.tools.registry import get_registry
        from huanxin.tools.base import ToolDef

        reg = get_registry()
        reg.register_tool(ToolDef(name="dup", description="First"))
        with pytest.raises(ValueError, match="already registered"):
            reg.register_tool(ToolDef(name="dup", description="Second"))

    def test_unregister(self):
        from huanxin.tools.registry import get_registry
        from huanxin.tools.base import ToolDef

        reg = get_registry()
        reg.register_tool(ToolDef(name="temp_tool", description="Temp"))
        assert reg.get_tool("temp_tool") is not None

        assert reg.unregister_tool("temp_tool") is True
        assert reg.get_tool("temp_tool") is None
        assert reg.unregister_tool("nonexistent") is False

    def test_list_tools(self):
        from huanxin.tools.registry import get_registry
        from huanxin.tools.base import ToolDef

        reg = get_registry()
        reg.register_tool(ToolDef(name="a", description="A", category="cat1"))
        reg.register_tool(ToolDef(name="b", description="B", category="cat2"))
        reg.register_tool(ToolDef(name="c", description="C", category="cat1"))

        all_tools = reg.list_tools()
        assert len(all_tools) == 3
        names = [t.name for t in all_tools]
        assert names == ["a", "b", "c"]

        cat1 = reg.list_tools(category="cat1")
        assert len(cat1) == 2
        assert {t.name for t in cat1} == {"a", "c"}

        cat_none = reg.list_tools(category="nonexistent")
        assert len(cat_none) == 0

    def test_list_categories(self):
        from huanxin.tools.registry import get_registry
        from huanxin.tools.base import ToolDef

        reg = get_registry()
        reg.register_tool(ToolDef(name="x", description="X", category="alpha"))
        reg.register_tool(ToolDef(name="y", description="Y", category="beta"))
        reg.register_tool(ToolDef(name="z", description="Z", category="alpha"))

        cats = reg.list_categories()
        assert cats == ["alpha", "beta"]

    def test_tool_count(self):
        from huanxin.tools.registry import get_registry
        from huanxin.tools.base import ToolDef

        reg = get_registry()
        assert reg.tool_count() == 0
        reg.register_tool(ToolDef(name="t1", description="1"))
        reg.register_tool(ToolDef(name="t2", description="2"))
        assert reg.tool_count() == 2

    def test_thread_safety(self):
        """Ensure concurrent registrations don't corrupt the registry."""
        from huanxin.tools.registry import get_registry, reset_registry
        from huanxin.tools.base import ToolDef

        reset_registry()
        reg = get_registry()
        errors = []

        def register_n(n: int):
            try:
                for i in range(10):
                    name = f"thread_{n}_{i}"
                    reg.register_tool(ToolDef(name=name, description=f"Tool {n}_{i}"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=register_n, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert reg.tool_count() == 50


# ═══════════════════════════════════════════════════════════════════
# 4. Schema generation
# ═══════════════════════════════════════════════════════════════════


class TestSchemaGeneration:
    """Tests for to_openai_schema / to_anthropic_schema."""

    def test_openai_schema(self):
        from huanxin.tools.registry import get_registry
        from huanxin.tools.base import ToolDef

        reg = get_registry()
        reg.register_tool(ToolDef(
            name="search",
            description="Search the web",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            category="network",
        ))

        schema = reg.to_openai_schema()
        assert len(schema) == 1
        assert schema[0]["type"] == "function"
        assert schema[0]["function"]["name"] == "search"

    def test_anthropic_schema(self):
        from huanxin.tools.registry import get_registry
        from huanxin.tools.base import ToolDef

        reg = get_registry()
        reg.register_tool(ToolDef(
            name="get_weather",
            description="Get weather info",
            parameters={
                "type": "object",
                "properties": {
                    "city": {"type": "string"},
                    "units": {"type": "string", "default": "metric"},
                },
                "required": ["city"],
            },
        ))

        schema = reg.to_anthropic_schema()
        assert len(schema) == 1
        assert schema[0]["name"] == "get_weather"
        assert "input_schema" in schema[0]
        assert schema[0]["input_schema"]["type"] == "object"


# ═══════════════════════════════════════════════════════════════════
# 5. Tool execution
# ═══════════════════════════════════════════════════════════════════


class TestToolExecution:
    """Tests for execute_tool."""

    def test_execute_successful(self):
        from huanxin.tools.registry import ToolRegistry
        from huanxin.tools.base import ToolDef, ToolResult

        def add(a, b):
            return a + b

        reg = ToolRegistry()
        reg.register_tool(ToolDef(
            name="add",
            description="Add two numbers",
            func=add,
        ))

        result = reg.execute_tool("add", {"a": 3, "b": 4})
        assert result.success is True
        assert result.data == 7
        assert result.duration_ms >= 0

    def test_execute_tool_with_toolresult_return(self):
        """When execute_tool calls a @tool-wrapped function, it should pass through ToolResult."""
        from huanxin.tools.registry import ToolRegistry
        from huanxin.tools.base import tool, ToolResult

        @tool(category="math")
        def multiply(a: int, b: int) -> int:
            """Multiply two numbers."""
            return a * b

        reg = ToolRegistry()
        reg.register_tool(multiply.tool_def)

        result = reg.execute_tool("multiply", {"a": 6, "b": 7})
        assert result.success is True
        assert result.data == 42

    def test_execute_unknown_tool(self):
        from huanxin.tools.registry import get_registry

        reg = get_registry()
        result = reg.execute_tool("nonexistent", {})
        assert result.success is False
        assert "not registered" in result.error

    def test_execute_type_error(self):
        from huanxin.tools.registry import ToolRegistry
        from huanxin.tools.base import ToolDef

        def required_args(a, b):
            return f"{a}{b}"

        reg = ToolRegistry()
        reg.register_tool(ToolDef(name="required_args", description="Needs both", func=required_args))

        # Missing required arg b
        result = reg.execute_tool("required_args", {"a": 1})
        assert result.success is False

    def test_execute_with_timing(self):
        from huanxin.tools.registry import ToolRegistry
        from huanxin.tools.base import ToolDef

        def fast():
            return "done"

        reg = ToolRegistry()
        reg.register_tool(ToolDef(name="fast", description="Fast function", func=fast))

        result = reg.execute_tool("fast", {})
        assert result.success
        assert result.duration_ms >= 0

    def test_execute_no_func(self):
        from huanxin.tools.registry import ToolRegistry
        from huanxin.tools.base import ToolDef

        reg = ToolRegistry()
        reg.register_tool(ToolDef(name="no_func", description="No callable"))
        result = reg.execute_tool("no_func", {})
        assert result.success is False
        assert "no callable" in result.error.lower()


# ═══════════════════════════════════════════════════════════════════
# 6. Built-in tools
# ═══════════════════════════════════════════════════════════════════


class TestBuiltinTools:
    """Tests for the 12 built-in tools (uses fresh_builtin fixture)."""

    def test_import_registers_all_12(self, fresh_builtin):
        from huanxin.tools.registry import get_registry

        reg = get_registry()
        names = {t.name for t in reg.list_tools()}
        expected = [
            "datetime", "math", "random", "text", "file_info",
            "hash", "json_tool", "uuid_gen", "weather", "news",
            "web_search", "web_fetch",
        ]
        for name in expected:
            assert name in names, f"Missing builtin tool: {name}"
        assert reg.tool_count() >= 12

    def test_datetime_tool(self, fresh_builtin):
        from huanxin.tools.registry import get_registry

        result = get_registry().execute_tool("datetime", {})
        assert result.success
        assert len(result.data) >= 10  # "YYYY-MM-DD" minimum

    def test_math_tool(self, fresh_builtin):
        from huanxin.tools.registry import get_registry

        result = get_registry().execute_tool("math", {"expression": "sqrt(16) + 2"})
        assert result.success
        assert "6" in str(result.data)

    def test_random_tool(self, fresh_builtin):
        from huanxin.tools.registry import get_registry

        result = get_registry().execute_tool(
            "random", {"min_val": 1, "max_val": 100, "as_int": True}
        )
        assert result.success
        assert 1 <= result.data <= 100

    def test_text_tool(self, fresh_builtin):
        from huanxin.tools.registry import get_registry

        result = get_registry().execute_tool(
            "text", {"operation": "upper", "content": "hello"}
        )
        assert result.success
        assert result.data == "HELLO"

    def test_uuid_gen_tool(self, fresh_builtin):
        from huanxin.tools.registry import get_registry

        result = get_registry().execute_tool("uuid_gen", {})
        assert result.success
        assert len(result.data) == 36  # UUID v4: 36 chars

    def test_weather_tool(self, fresh_builtin):
        from huanxin.tools.registry import get_registry

        result = get_registry().execute_tool("weather", {"city": "Beijing"})
        assert result.success
        assert result.data["city"].lower() == "beijing"
        assert "temperature" in result.data

    def test_json_tool(self, fresh_builtin):
        from huanxin.tools.registry import get_registry

        result = get_registry().execute_tool(
            "json_tool", {"operation": "parse", "data": '{"key": "value"}'}
        )
        assert result.success
        parsed = json.loads(result.data)
        assert parsed["key"] == "value"

    def test_hash_tool_string(self, fresh_builtin):
        from huanxin.tools.registry import get_registry

        result = get_registry().execute_tool(
            "hash", {"content": "hello", "algorithm": "sha256"}
        )
        assert result.success
        assert len(result.data) == 64  # SHA256 hex

    def test_builtin_categories(self, fresh_builtin):
        from huanxin.tools.registry import get_registry

        reg = get_registry()
        utility_tools = reg.list_tools(category="utility")
        network_tools = reg.list_tools(category="network")
        file_tools = reg.list_tools(category="file")

        assert len(utility_tools) >= 7  # datetime, math, random, text, hash, json_tool, uuid_gen
        assert len(network_tools) >= 4   # weather, news, web_search, web_fetch
        assert len(file_tools) >= 1      # file_info


# ═══════════════════════════════════════════════════════════════════
# 7. LLMEngine FC loop
# ═══════════════════════════════════════════════════════════════════


class TestLLMEngineFCLoop:
    """Tests for the Function Calling loop in LLMEngine.

    These tests mock litellm at the function-call level to avoid real API
    calls. Since litellm is imported *inside* the engine methods (not at
    module level), we inject a mock into sys.modules.
    """

    @pytest.fixture(autouse=True)
    def mock_litellm_module(self):
        """Inject a mock litellm into sys.modules for the duration of the test."""

        mock_litellm = MagicMock()
        # completion will be patched per-test
        mock_litellm.completion = MagicMock()

        with patch.dict(sys.modules, {"litellm": mock_litellm}):
            yield mock_litellm

    def _build_mock_response(self, content=None, tool_calls=None):
        """Build a litellm-style response mock."""
        resp = MagicMock()
        choice = MagicMock()
        choice.message.content = content
        if tool_calls:
            tc_list = []
            for tc in tool_calls:
                tc_mock = MagicMock()
                tc_mock.id = tc["id"]
                tc_mock.function.name = tc["name"]
                tc_mock.function.arguments = tc["arguments"]
                tc_list.append(tc_mock)
            choice.message.tool_calls = tc_list
        else:
            choice.message.tool_calls = None
        resp.choices = [choice]
        return resp

    def test_fast_path_no_tools(self, mock_litellm_module):
        """Without tools/tool_registry, chat_sync should work as normal chat."""
        from huanxin.llm.engine import LLMEngine
        from huanxin.llm.config import LLMConfig, ModelProvider

        mock_litellm_module.completion.return_value = self._build_mock_response(
            content="Hello!"
        )

        config = LLMConfig(provider=ModelProvider.OPENAI, model_name="gpt-4o")
        engine = LLMEngine(config)
        # Force litellm available
        engine._litellm_available = True

        result = engine.chat_sync("Hi")
        assert result == "Hello!"
        mock_litellm_module.completion.assert_called_once()

    def test_fc_loop_single_tool_call(self, mock_litellm_module):
        """FC loop: model calls a tool, engine executes it, gets final response."""
        from huanxin.llm.engine import LLMEngine
        from huanxin.llm.config import LLMConfig, ModelProvider
        from huanxin.tools.registry import ToolRegistry
        from huanxin.tools.base import ToolDef

        # Build registry with an add tool
        reg = ToolRegistry()

        def add_func(a, b):
            return a + b

        reg.register_tool(ToolDef(
            name="add",
            description="Add two numbers",
            parameters={
                "type": "object",
                "properties": {
                    "a": {"type": "integer"},
                    "b": {"type": "integer"},
                },
                "required": ["a", "b"],
            },
            func=add_func,
        ))

        # Round 1: tool call, Round 2: final text
        mock_litellm_module.completion.side_effect = [
            self._build_mock_response(
                tool_calls=[{"id": "call_001", "name": "add", "arguments": '{"a":3,"b":4}'}],
            ),
            self._build_mock_response(content="3 + 4 = 7"),
        ]

        config = LLMConfig(provider=ModelProvider.OPENAI, model_name="gpt-4o")
        engine = LLMEngine(config)
        engine._litellm_available = True

        result = engine.chat_sync(
            "What is 3+4?",
            tools=reg.to_openai_schema(),
            tool_registry=reg,
        )
        assert "7" in result
        assert mock_litellm_module.completion.call_count == 2

    def test_max_fc_rounds_limit(self, mock_litellm_module):
        """FC loop should stop after max_fc_rounds iterations."""
        from huanxin.llm.engine import LLMEngine
        from huanxin.llm.config import LLMConfig, ModelProvider
        from huanxin.tools.registry import ToolRegistry
        from huanxin.tools.base import ToolDef

        reg = ToolRegistry()

        def echo(text):
            return text

        reg.register_tool(ToolDef(
            name="echo",
            description="Echo text",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            func=echo,
        ))

        # Always return tool_calls (simulate infinite loop)
        mock_litellm_module.completion.return_value = self._build_mock_response(
            content="Calling...",
            tool_calls=[{"id": "call_x", "name": "echo", "arguments": '{"text":"hello"}'}],
        )

        config = LLMConfig(provider=ModelProvider.OPENAI, model_name="gpt-4o")
        engine = LLMEngine(config)
        engine._litellm_available = True

        result = engine.chat_sync(
            "Echo",
            tools=reg.to_openai_schema(),
            tool_registry=reg,
            max_fc_rounds=2,
        )
        assert mock_litellm_module.completion.call_count == 2
        assert "Calling..." in result

    def test_chat_sync_with_tools_no_registry(self, mock_litellm_module):
        """When tools are provided but no tool_registry, should work as normal call."""
        from huanxin.llm.engine import LLMEngine
        from huanxin.llm.config import LLMConfig, ModelProvider

        mock_litellm_module.completion.return_value = self._build_mock_response(
            content="I'll use a tool for that."
        )

        config = LLMConfig(provider=ModelProvider.OPENAI, model_name="gpt-4o")
        engine = LLMEngine(config)
        engine._litellm_available = True

        result = engine.chat_sync(
            "What's the weather?",
            tools=[{"type": "function", "function": {"name": "get_weather"}}],
        )
        assert "tool" in result.lower()


# ═══════════════════════════════════════════════════════════════════
# 8. ToolResult
# ═══════════════════════════════════════════════════════════════════


class TestToolResult:
    """Tests for ToolResult data class."""

    def test_success_result(self):
        from huanxin.tools.base import ToolResult

        r = ToolResult(success=True, data="hello", duration_ms=12.5)
        assert r.success is True
        assert r.data == "hello"
        assert r.error == ""
        assert r.duration_ms == 12.5

    def test_error_result(self):
        from huanxin.tools.base import ToolResult

        r = ToolResult(success=False, error="Something went wrong", duration_ms=3.0)
        assert r.success is False
        assert r.data is None
        assert "Something went wrong" in r.error

    def test_to_dict(self):
        from huanxin.tools.base import ToolResult

        r = ToolResult(success=True, data={"key": "val"}, duration_ms=5.0)
        d = r.to_dict()
        assert d["success"] is True
        assert d["data"] == {"key": "val"}
        assert d["duration_ms"] == 5.0

    def test_to_json(self):
        from huanxin.tools.base import ToolResult

        r = ToolResult(success=True, data="test", duration_ms=1.0)
        j = r.to_json()
        parsed = json.loads(j)
        assert parsed["success"] is True
        assert parsed["data"] == "test"

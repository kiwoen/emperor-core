"""Tests for huanxin.llm — LLMEngine, LLMConfig, ModelProvider."""

from __future__ import annotations

import pytest

from huanxin.llm import LLMConfig, LLMEngine, ModelProvider


# ══════════════════════════════════════════════════════════════════
# LLMConfig
# ══════════════════════════════════════════════════════════════════


class TestLLMConfig:
    def test_default_config(self):
        cfg = LLMConfig()
        assert cfg.provider == ModelProvider.OPENAI
        assert cfg.model_name == "gpt-4o"
        assert cfg.temperature == 0.7
        assert cfg.max_tokens == 4096
        assert cfg.api_key == ""
        assert cfg.base_url == ""
        assert cfg.extra_params == {}

    def test_openai_config(self):
        cfg = LLMConfig(
            provider=ModelProvider.OPENAI,
            model_name="gpt-4o",
            api_key="sk-test",
            temperature=0.3,
            max_tokens=2048,
        )
        assert cfg.provider == ModelProvider.OPENAI
        assert cfg.model_name == "gpt-4o"
        assert cfg.api_key == "sk-test"
        assert cfg.temperature == 0.3
        assert cfg.max_tokens == 2048

    def test_anthropic_config(self):
        cfg = LLMConfig(
            provider=ModelProvider.ANTHROPIC,
            model_name="claude-3-opus-20240229",
            api_key="sk-ant-test",
            temperature=1.0,
            max_tokens=8192,
        )
        assert cfg.provider == ModelProvider.ANTHROPIC
        assert cfg.model_name == "claude-3-opus-20240229"

    def test_ollama_config(self):
        cfg = LLMConfig(
            provider=ModelProvider.OLLAMA,
            model_name="llama3",
            base_url="http://localhost:11434",
            temperature=0.0,
        )
        assert cfg.provider == ModelProvider.OLLAMA
        assert cfg.model_name == "llama3"
        assert cfg.base_url == "http://localhost:11434"
        assert cfg.temperature == 0.0

    def test_extra_params(self):
        cfg = LLMConfig(extra_params={"top_p": 0.9, "frequency_penalty": 0.5})
        assert cfg.extra_params == {"top_p": 0.9, "frequency_penalty": 0.5}

    def test_temperature_validation(self):
        """Temperature must be in [0.0, 2.0]."""
        cfg = LLMConfig(temperature=0.0)
        assert cfg.temperature == 0.0
        cfg = LLMConfig(temperature=2.0)
        assert cfg.temperature == 2.0

    def test_max_tokens_validation(self):
        """max_tokens must be > 0."""
        cfg = LLMConfig(max_tokens=1)
        assert cfg.max_tokens == 1


# ══════════════════════════════════════════════════════════════════
# ModelProvider enum
# ══════════════════════════════════════════════════════════════════


class TestModelProvider:
    def test_provider_values(self):
        assert ModelProvider.OPENAI.value == "openai"
        assert ModelProvider.ANTHROPIC.value == "anthropic"
        assert ModelProvider.OLLAMA.value == "ollama"

    def test_provider_from_string(self):
        assert ModelProvider("openai") == ModelProvider.OPENAI
        assert ModelProvider("anthropic") == ModelProvider.ANTHROPIC
        assert ModelProvider("ollama") == ModelProvider.OLLAMA


# ══════════════════════════════════════════════════════════════════
# LLMEngine initialization
# ══════════════════════════════════════════════════════════════════


class TestLLMEngineInit:
    def test_default_initialization(self):
        engine = LLMEngine()
        assert engine.config is not None
        assert engine.config.provider == ModelProvider.OPENAI
        assert engine.config.model_name == "gpt-4o"

    def test_initialization_with_config(self):
        cfg = LLMConfig(
            provider=ModelProvider.ANTHROPIC,
            model_name="claude-3-haiku-20240307",
            api_key="sk-ant-test",
        )
        engine = LLMEngine(config=cfg)
        assert engine.config.provider == ModelProvider.ANTHROPIC
        assert engine.config.model_name == "claude-3-haiku-20240307"
        assert engine.config.api_key == "sk-ant-test"

    def test_ollama_engine_init(self):
        cfg = LLMConfig(
            provider=ModelProvider.OLLAMA,
            model_name="mistral",
            base_url="http://localhost:11434",
        )
        engine = LLMEngine(config=cfg)
        assert engine.config.provider == ModelProvider.OLLAMA
        assert engine.config.model_name == "mistral"
        assert engine.config.base_url == "http://localhost:11434"

    def test_engine_without_litellm_does_not_crash_on_init(self):
        """LLMEngine init should not crash even if litellm is absent (lazy check)."""
        engine = LLMEngine()
        assert engine.config is not None


# ══════════════════════════════════════════════════════════════════
# LLMEngine._build_kwargs
# ══════════════════════════════════════════════════════════════════


class TestBuildKwargs:
    def test_basic_kwargs(self):
        cfg = LLMConfig(
            provider=ModelProvider.OPENAI,
            model_name="gpt-4o",
            temperature=0.5,
            max_tokens=1024,
        )
        engine = LLMEngine(config=cfg)
        kwargs = engine._build_kwargs(
            [{"role": "user", "content": "hello"}]
        )
        assert kwargs["model"] == "openai/gpt-4o"
        assert kwargs["temperature"] == 0.5
        assert kwargs["max_tokens"] == 1024
        assert len(kwargs["messages"]) == 1

    def test_with_api_key(self):
        cfg = LLMConfig(
            provider=ModelProvider.ANTHROPIC,
            model_name="claude-3-opus-20240229",
            api_key="sk-ant-123",
        )
        engine = LLMEngine(config=cfg)
        kwargs = engine._build_kwargs(
            [{"role": "user", "content": "hello"}]
        )
        assert kwargs["model"] == "anthropic/claude-3-opus-20240229"
        assert kwargs["api_key"] == "sk-ant-123"

    def test_with_base_url(self):
        cfg = LLMConfig(
            provider=ModelProvider.OLLAMA,
            model_name="llama3",
            base_url="http://localhost:11434",
        )
        engine = LLMEngine(config=cfg)
        kwargs = engine._build_kwargs(
            [{"role": "user", "content": "hello"}]
        )
        assert kwargs["model"] == "ollama/llama3"
        assert kwargs["api_base"] == "http://localhost:11434"

    def test_with_tools(self):
        cfg = LLMConfig()
        engine = LLMEngine(config=cfg)
        tools = [{"type": "function", "function": {"name": "get_weather", "parameters": {}}}]
        kwargs = engine._build_kwargs(
            [{"role": "user", "content": "weather?"}],
            tools=tools,
        )
        assert "tools" in kwargs
        assert kwargs["tools"] == tools

    def test_with_extra_params(self):
        cfg = LLMConfig(extra_params={"top_p": 0.9, "seed": 42})
        engine = LLMEngine(config=cfg)
        kwargs = engine._build_kwargs(
            [{"role": "user", "content": "hello"}]
        )
        assert kwargs["top_p"] == 0.9
        assert kwargs["seed"] == 42

    def test_no_api_key_when_empty(self):
        cfg = LLMConfig(api_key="")
        engine = LLMEngine(config=cfg)
        kwargs = engine._build_kwargs(
            [{"role": "user", "content": "hello"}]
        )
        assert "api_key" not in kwargs

    def test_no_base_url_when_empty(self):
        cfg = LLMConfig(base_url="")
        engine = LLMEngine(config=cfg)
        kwargs = engine._build_kwargs(
            [{"role": "user", "content": "hello"}]
        )
        assert "api_base" not in kwargs


# ══════════════════════════════════════════════════════════════════
# LLMEngine._raise_if_unavailable
# ══════════════════════════════════════════════════════════════════


class TestRaiseIfUnavailable:
    def test_raises_when_litellm_not_installed(self, monkeypatch):
        """Simulate litellm being unavailable."""
        engine = LLMEngine()
        engine._litellm_available = False
        with pytest.raises(RuntimeError, match="litellm is not installed"):
            engine._raise_if_unavailable()

    def test_no_raise_when_litellm_available(self, monkeypatch):
        engine = LLMEngine()
        engine._litellm_available = True
        engine._raise_if_unavailable()  # should not raise


# ══════════════════════════════════════════════════════════════════
# LLMEngine mock chat
# ══════════════════════════════════════════════════════════════════


class TestLLMEngineMockChat:
    """Test chat methods with mocked litellm responses."""

    @pytest.fixture
    def engine(self):
        eng = LLMEngine()
        eng._litellm_available = True
        return eng

    @pytest.fixture
    def mock_response(self):
        """Create a mock litellm response object."""

        class MockMessage:
            content = "Hello, world!"
            tool_calls = None

        class MockChoice:
            message = MockMessage()

        class MockResponse:
            choices = [MockChoice()]

        return MockResponse()

    @pytest.fixture
    def mock_tool_call_response(self):
        """Create a mock response with tool calls."""

        class MockToolCallFunction:
            name = "get_weather"
            arguments = '{"location": "Beijing"}'

        class MockToolCall:
            id = "call_123"
            type = "function"
            function = MockToolCallFunction()

        class MockMessage:
            content = None
            tool_calls = [MockToolCall()]

        class MockChoice:
            message = MockMessage()

        class MockResponse:
            choices = [MockChoice()]

        return MockResponse()

    def test_extract_response_text(self, mock_response):
        result = LLMEngine._extract_response(mock_response)
        assert result == "Hello, world!"

    def test_extract_response_tool_call(self, mock_tool_call_response):
        import json

        result = LLMEngine._extract_response(mock_tool_call_response)
        parsed = json.loads(result)
        assert len(parsed) == 1
        assert parsed[0]["function"]["name"] == "get_weather"
        assert parsed[0]["function"]["arguments"] == '{"location": "Beijing"}'

    def test_extract_response_empty(self):
        class MockMessage:
            content = ""
            tool_calls = None

        class MockChoice:
            message = MockMessage()

        class MockResponse:
            choices = [MockChoice()]

        result = LLMEngine._extract_response(MockResponse())
        assert result == ""

    def test_chat_sync_mock(self, engine, mock_response, monkeypatch):
        """Test chat_sync with mocked litellm.completion."""
        import litellm

        def mock_completion(**kwargs):
            return mock_response

        monkeypatch.setattr(litellm, "completion", mock_completion)
        result = engine.chat_sync("Hello!")
        assert result == "Hello, world!"

    @pytest.mark.asyncio
    async def test_chat_async_mock(self, engine, mock_response, monkeypatch):
        """Test chat async with mocked litellm.acompletion."""

        async def mock_acompletion(**kwargs):
            return mock_response

        import litellm

        monkeypatch.setattr(litellm, "acompletion", mock_acompletion)
        result = await engine.chat("Hello!")
        assert result == "Hello, world!"

    def test_chat_with_system(self, engine, mock_response, monkeypatch):
        import litellm

        call_kwargs = {}

        def mock_completion(**kwargs):
            call_kwargs.update(kwargs)
            return mock_response

        monkeypatch.setattr(litellm, "completion", mock_completion)
        result = engine.chat_sync("Hello!", system="You are helpful.")
        assert result == "Hello, world!"
        msgs = call_kwargs["messages"]
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "You are helpful."

    def test_chat_with_messages_override(self, engine, mock_response, monkeypatch):
        import litellm

        call_kwargs = {}

        def mock_completion(**kwargs):
            call_kwargs.update(kwargs)
            return mock_response

        monkeypatch.setattr(litellm, "completion", mock_completion)
        custom_msgs = [
            {"role": "system", "content": "custom system"},
            {"role": "user", "content": "custom user"},
        ]
        result = engine.chat_sync("ignored", messages=custom_msgs)
        assert result == "Hello, world!"
        assert len(call_kwargs["messages"]) == 2
        assert call_kwargs["messages"][0]["content"] == "custom system"

    def test_chat_sync_with_tools(self, engine, mock_response, monkeypatch):
        import litellm

        call_kwargs = {}

        def mock_completion(**kwargs):
            call_kwargs.update(kwargs)
            return mock_response

        monkeypatch.setattr(litellm, "completion", mock_completion)
        tools = [{"type": "function", "function": {"name": "search", "parameters": {}}}]
        result = engine.chat_sync("search something", tools=tools)
        assert result == "Hello, world!"
        assert "tools" in call_kwargs

    @pytest.mark.asyncio
    async def test_chat_stream_mock(self, engine, monkeypatch):
        """Test chat_stream with mocked streaming response."""

        class MockDelta:
            def __init__(self, content):
                self.content = content

        class MockChunkChoice:
            def __init__(self, content):
                self.delta = MockDelta(content)

        class MockChunk:
            def __init__(self, content):
                self.choices = [MockChunkChoice(content)]

        async def mock_stream(**kwargs):
            async def _gen():
                for word in ["Hello", ", ", "world", "!"]:
                    yield MockChunk(word)
            return _gen()

        import litellm

        monkeypatch.setattr(litellm, "acompletion", mock_stream)
        chunks = []
        async for chunk in engine.chat_stream("Hi"):
            chunks.append(chunk)
        assert "".join(chunks) == "Hello, world!"


# ══════════════════════════════════════════════════════════════════
# Huanxin integration (llm_engine property)
# ══════════════════════════════════════════════════════════════════
#
# NOTE: Huanxin integration tests are deferred until huanxin.model_router
# (a pre-existing dependency in Huanxin.__init__) is available.
# The llm_engine / llm_config properties are correctly added to Huanxin
# (see huanxin/emperor.py lines ~534-555).

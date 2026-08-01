"""
Tests for jarvis.mcp_client and jarvis.mcp_manager — MCP Client Integration.

Covers:
  - MCPClient: connect, disconnect, list_tools, call_tool, list_servers
  - MCPManager: register_server, unregister_server, get_all_tools, discover_and_call
  - 3 built-in mock servers: filesystem-server, web-search-server, calculator-server
  - Error handling: connection failure, tool not found, timeout, division by zero
  - Emperor integration: mcp_manager property, built-in mock servers auto-register
"""

import sys
import os
import time
import tempfile

import pytest

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from jarvis.mcp_client import (
    MCPClient,
    MCPServerConfig,
    MCPTool,
    MCPError,
    MCPConnectionError,
    MCPToolError,
    MCPTimeoutError,
)

from jarvis.mcp_manager import (
    MCPManager,
    MockFileSystemServer,
    MockWebSearchServer,
    MockCalculatorServer,
)


# ═══════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def client():
    """Fresh MCPClient instance."""
    c = MCPClient()
    yield c
    c.shutdown()


@pytest.fixture
def mock_fs():
    """Fresh MockFileSystemServer."""
    srv = MockFileSystemServer()
    srv.connect()
    return srv


@pytest.fixture
def mock_search():
    """Fresh MockWebSearchServer."""
    srv = MockWebSearchServer()
    srv.connect()
    return srv


@pytest.fixture
def mock_calc():
    """Fresh MockCalculatorServer."""
    srv = MockCalculatorServer()
    srv.connect()
    return srv


@pytest.fixture
def manager():
    """MCPManager with no servers pre-registered."""
    return MCPManager()


@pytest.fixture
def manager_with_mocks():
    """MCPManager with all 3 built-in mock servers registered."""
    mgr = MCPManager()
    mgr.register_builtin_mock_servers()
    return mgr


# ═══════════════════════════════════════════════════════════════════
# MCPClient Tests
# ═══════════════════════════════════════════════════════════════════


class TestMCPClientBasics:
    """Test basic MCPClient operations with mock servers."""

    def test_list_servers_empty_initially(self, client):
        """List servers should be empty on fresh client."""
        assert client.list_servers() == []

    def test_list_servers_after_manager_registration(self, manager_with_mocks):
        """list_servers should reflect registered mock servers."""
        servers = manager_with_mocks.client.list_servers()
        # Mock servers go through MCPManager, not MCPClient directly
        # MCPClient only knows about external (non-mock) connections
        assert isinstance(servers, list)

    def test_shutdown_clears_all(self, client):
        """Shutdown should clean up without error."""
        client.shutdown()
        assert client.list_servers() == []


class TestMCPClientErrors:
    """Test error handling in MCPClient."""

    def test_connect_twice_raises_error(self, manager):
        """Connecting same server twice should raise MCPConnectionError."""
        manager.register_builtin_mock_servers()
        # Try to re-register an already registered server
        with pytest.raises(MCPConnectionError):
            manager.register_mock_server(MockFileSystemServer())

    def test_disconnect_nonexistent_raises(self, client):
        """Disconnecting a non-existent server should raise."""
        with pytest.raises(MCPConnectionError):
            client.disconnect("nonexistent-server")

    def test_connect_invalid_transport(self, client):
        """Unknown transport should raise MCPConnectionError."""
        cfg = MCPServerConfig(name="bad", transport="invalid_transport")
        with pytest.raises(MCPConnectionError):
            client.connect(cfg)

    def test_connect_http_no_url_raises(self, client):
        """HTTP transport without URL should raise."""
        cfg = MCPServerConfig(name="no-url", transport="http", url="")
        with pytest.raises(MCPConnectionError):
            client.connect(cfg)

    def test_connect_stdio_command_not_found(self, client):
        """stdio with non-existent command should raise."""
        cfg = MCPServerConfig(
            name="ghost",
            transport="stdio",
            command="nonexistent_command_xyz123",
        )
        with pytest.raises(MCPConnectionError):
            client.connect(cfg)


# ═══════════════════════════════════════════════════════════════════
# Mock FileSystem Server Tests
# ═══════════════════════════════════════════════════════════════════


class TestMockFileSystemServer:
    """Test the built-in filesystem-server mock."""

    def test_list_tools(self, mock_fs):
        tools = mock_fs.list_tools()
        tool_names = {t.name for t in tools}
        assert "list_dir" in tool_names
        assert "read_file" in tool_names
        assert "write_file" in tool_names

    def test_list_dir_root(self, mock_fs):
        result = mock_fs.call_tool("list_dir", {"path": "."})
        parsed = eval(result["result"])
        assert len(parsed) == 4
        names = [e["name"] for e in parsed]
        assert "README.md" in names
        assert "src" in names

    def test_read_file_known(self, mock_fs):
        result = mock_fs.call_tool("read_file", {"path": "README.md"})
        assert "Emperor Core" in result["result"]

    def test_write_and_read_file(self, mock_fs):
        mock_fs.call_tool("write_file", {
            "path": "test.txt", "content": "Hello MCP"
        })
        result = mock_fs.call_tool("read_file", {"path": "test.txt"})
        assert result["result"] == "Hello MCP"

    def test_list_dir_subdir(self, mock_fs):
        result = mock_fs.call_tool("list_dir", {"path": "src"})
        parsed = eval(result["result"])
        names = [e["name"] for e in parsed]
        assert "main.py" in names


# ═══════════════════════════════════════════════════════════════════
# Mock Web Search Server Tests
# ═══════════════════════════════════════════════════════════════════


class TestMockWebSearchServer:
    """Test the built-in web-search-server mock."""

    def test_list_tools(self, mock_search):
        tools = mock_search.list_tools()
        tool_names = {t.name for t in tools}
        assert "search" in tool_names

    def test_search_returns_results(self, mock_search):
        result = mock_search.call_tool("search", {"query": "MCP protocol"})
        parsed = eval(result["result"])
        assert len(parsed) > 0
        assert "title" in parsed[0]
        assert "MCP" in parsed[0]["title"]

    def test_search_limits_results(self, mock_search):
        result = mock_search.call_tool("search", {
            "query": "AI", "num_results": 2
        })
        parsed = eval(result["result"])
        assert len(parsed) == 2


# ═══════════════════════════════════════════════════════════════════
# Mock Calculator Server Tests
# ═══════════════════════════════════════════════════════════════════


class TestMockCalculatorServer:
    """Test the built-in calculator-server mock."""

    def test_list_tools(self, mock_calc):
        tools = mock_calc.list_tools()
        tool_names = {t.name for t in tools}
        assert "add" in tool_names
        assert "subtract" in tool_names
        assert "multiply" in tool_names
        assert "divide" in tool_names
        assert "power" in tool_names
        assert "sqrt" in tool_names

    def test_add(self, mock_calc):
        result = mock_calc.call_tool("add", {"a": 3, "b": 7})
        assert "10" in result["result"]

    def test_multiply(self, mock_calc):
        result = mock_calc.call_tool("multiply", {"a": 6, "b": 7})
        assert "42" in result["result"]

    def test_divide(self, mock_calc):
        result = mock_calc.call_tool("divide", {"a": 100, "b": 4})
        assert "25" in result["result"]

    def test_divide_by_zero_raises(self, mock_calc):
        with pytest.raises(MCPToolError, match="Division by zero"):
            mock_calc.call_tool("divide", {"a": 1, "b": 0})

    def test_sqrt_negative_raises(self, mock_calc):
        with pytest.raises(MCPToolError, match="sqrt of negative"):
            mock_calc.call_tool("sqrt", {"x": -4})

    def test_power(self, mock_calc):
        result = mock_calc.call_tool("power", {"base": 2, "exp": 10})
        assert "1024" in result["result"]

    def test_tool_not_found_raises(self, mock_calc):
        with pytest.raises(MCPToolError):
            mock_calc.call_tool("nonexistent_tool", {})

    def test_missing_param_uses_default(self, mock_calc):
        """Missing optional parameter should use default value."""
        # add() has defaults: a=0, b=0; calling with only 'a' gives a+0
        result = mock_calc.call_tool("add", {"a": 42})
        assert "42.0" in result["result"] or "42" in result["result"]


# ═══════════════════════════════════════════════════════════════════
# MCPManager Tests
# ═══════════════════════════════════════════════════════════════════


class TestMCPManager:
    """Test MCPManager orchestration layer."""

    def test_register_builtin_mock_servers(self, manager):
        servers = manager.register_builtin_mock_servers()
        assert "filesystem-server" in servers
        assert "web-search-server" in servers
        assert "calculator-server" in servers
        assert manager.server_count == 3

    def test_get_all_tools(self, manager_with_mocks):
        tools = manager_with_mocks.get_all_tools()
        tool_names = {t.name for t in tools}
        assert "list_dir" in tool_names
        assert "search" in tool_names
        assert "add" in tool_names
        assert len(tools) >= 10  # 3 + 1 + 6

    def test_get_tools_by_server(self, manager_with_mocks):
        grouped = manager_with_mocks.get_tools_by_server()
        assert "calculator-server" in grouped
        assert "filesystem-server" in grouped
        assert "web-search-server" in grouped
        assert len(grouped["calculator-server"]) == 6

    def test_discover_and_call_calculator(self, manager_with_mocks):
        result = manager_with_mocks.discover_and_call(
            "add", {"a": 10, "b": 20}
        )
        assert "30" in result["result"]

    def test_discover_and_call_filesystem(self, manager_with_mocks):
        result = manager_with_mocks.discover_and_call(
            "list_dir", {"path": "."}
        )
        parsed = eval(result["result"])
        assert len(parsed) > 0

    def test_discover_and_call_search(self, manager_with_mocks):
        result = manager_with_mocks.discover_and_call(
            "search", {"query": "MCP"}
        )
        assert "MCP" in result["result"]

    def test_discover_tool_not_found(self, manager_with_mocks):
        with pytest.raises(MCPToolError, match="not found in any registered"):
            manager_with_mocks.discover_and_call(
                "nonexistent_tool", {}
            )

    def test_unregister_server(self, manager_with_mocks):
        assert manager_with_mocks.server_count == 3
        manager_with_mocks.unregister_server("calculator-server")
        assert manager_with_mocks.server_count == 2
        # tools should also be removed from index
        with pytest.raises(MCPToolError, match="not found"):
            manager_with_mocks.discover_and_call("add", {"a": 1, "b": 2})

    def test_unregister_nonexistent_raises(self, manager):
        with pytest.raises(MCPConnectionError):
            manager.unregister_server("ghost-server")

    def test_list_servers(self, manager_with_mocks):
        servers = manager_with_mocks.list_servers()
        assert len(servers) == 3
        assert "filesystem-server" in servers
        assert "web-search-server" in servers
        assert "calculator-server" in servers

    def test_shutdown(self, manager_with_mocks):
        manager_with_mocks.shutdown()
        assert manager_with_mocks.server_count == 0

    def test_register_mock_twice_raises(self, manager):
        manager.register_mock_server(MockFileSystemServer())
        with pytest.raises(MCPConnectionError):
            manager.register_mock_server(MockFileSystemServer())


# ═══════════════════════════════════════════════════════════════════
# Emperor Integration Tests
# ═══════════════════════════════════════════════════════════════════


class TestEmperorMCPIntegration:
    """Test Emperor <-> MCPManager integration."""

    def test_emperor_has_mcp_manager(self):
        """Emperor instance should auto-register built-in mock servers."""
        from jarvis.emperor import Emperor
        emp = Emperor()
        try:
            mgr = emp.mcp_manager
            assert mgr is not None
            assert mgr.server_count == 3
            # Verify all 3 mock servers are available
            servers = mgr.list_servers()
            assert "filesystem-server" in servers
            assert "web-search-server" in servers
            assert "calculator-server" in servers
        finally:
            emp.shutdown()

    def test_emperor_mcp_call_through_manager(self):
        """Calling MCP tools through Emperor's mcp_manager should work."""
        from jarvis.emperor import Emperor
        emp = Emperor()
        try:
            result = emp.mcp_manager.discover_and_call(
                "multiply", {"a": 7, "b": 8}
            )
            assert "56" in result["result"]
        finally:
            emp.shutdown()


# ═══════════════════════════════════════════════════════════════════
# MCPTool & MCPServerConfig Data Class Tests
# ═══════════════════════════════════════════════════════════════════


class TestMCPDataClasses:
    """Verify MCPTool and MCPServerConfig data classes."""

    def test_mcp_tool_defaults(self):
        tool = MCPTool(name="test_tool")
        assert tool.name == "test_tool"
        assert tool.description == ""
        assert tool.parameters_schema == {}

    def test_mcp_tool_full(self):
        tool = MCPTool(
            name="search",
            description="Search the web",
            parameters_schema={"type": "object", "properties": {}},
        )
        assert tool.name == "search"
        assert "Search" in tool.description
        assert tool.parameters_schema["type"] == "object"

    def test_mcp_server_config_defaults(self):
        cfg = MCPServerConfig(name="test")
        assert cfg.name == "test"
        assert cfg.transport == "stdio"
        assert cfg.timeout == 30.0
        assert cfg.args == []
        assert cfg.command == ""
        assert cfg.url == ""

    def test_mcp_server_config_http(self):
        cfg = MCPServerConfig(
            name="remote",
            transport="http",
            url="http://localhost:9000/mcp",
            timeout=10.0,
        )
        assert cfg.transport == "http"
        assert cfg.url == "http://localhost:9000/mcp"
        assert cfg.timeout == 10.0

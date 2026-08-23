"""Tests for VSCode bridge and commands."""
import asyncio
import json

import pytest

from jarvis.hermes.bus import MessageBus, Topic
from jarvis.vscode.commands import EditorCommand, VSCodeCommands
from jarvis.vscode.bridge import VSCodeBridge


# ============================================================================
# EditorCommand
# ============================================================================

class TestEditorCommand:
    def test_serialization(self):
        cmd = EditorCommand(category="file", action="open", params={"file": "test.py"})
        d = cmd.to_dict()
        assert d["category"] == "file"
        assert d["action"] == "open"
        assert d["params"]["file"] == "test.py"

    def test_deserialization(self):
        cmd = EditorCommand.from_dict({
            "category": "editor",
            "action": "goto",
            "params": {"file": "app.py", "line": 42},
        })
        assert cmd.category == "editor"
        assert cmd.params["line"] == 42

    def test_defaults(self):
        cmd = EditorCommand(category="terminal", action="send")
        assert cmd.params == {}


# ============================================================================
# VSCodeCommands (factory)
# ============================================================================

class TestVSCodeCommands:
    def setup_method(self):
        self.cmds = VSCodeCommands(code_cli="code")

    def test_open_file_basic(self):
        cmd = self.cmds.open_file("main.py")
        assert cmd.category == "file"
        assert cmd.action == "open"

    def test_open_file_with_position(self):
        cmd = self.cmds.open_file("main.py", line=10, column=5)
        assert cmd.params["line"] == 10
        assert cmd.params["column"] == 5

    def test_goto_line(self):
        cmd = self.cmds.goto_line("app.py", 42)
        assert cmd.category == "editor"

    def test_insert_text(self):
        cmd = self.cmds.insert_text("hello world")
        assert cmd.params["text"] == "hello world"

    def test_delete_lines(self):
        cmd = self.cmds.delete_lines(10, 20)
        assert cmd.params["start"] == 10
        assert cmd.params["end"] == 20

    def test_format_document(self):
        cmd = self.cmds.format_document()
        assert cmd.action == "format"

    def test_list_files(self):
        cmd = self.cmds.list_files("*.py")
        assert cmd.params["pattern"] == "*.py"

    def test_search_in_files(self):
        cmd = self.cmds.search_in_files("TODO", "*.py")
        assert cmd.params["query"] == "TODO"

    def test_get_diagnostics(self):
        cmd = self.cmds.get_diagnostics("test.py")
        assert cmd.params["file"] == "test.py"

    def test_send_to_terminal(self):
        cmd = self.cmds.send_to_terminal("npm test")
        assert cmd.params["text"] == "npm test"

    def test_create_terminal(self):
        cmd = self.cmds.create_terminal("Build")
        assert cmd.params["name"] == "Build"

    def test_install_extension(self):
        cmd = self.cmds.install_extension("ms-python.python")
        assert cmd.params["id"] == "ms-python.python"

    def test_save_all(self):
        cmd = self.cmds.save_all()
        assert cmd.action == "save_all"

    def test_to_cli_args_open_file(self):
        cmd = self.cmds.open_file("main.py")
        args = self.cmds.to_cli_args(cmd)
        assert "main.py" in args

    def test_to_cli_args_goto(self):
        """goto_line is editor.goto — requires extension bridge, not CLI."""
        cmd = self.cmds.goto_line("main.py", 42, 5)
        args = self.cmds.to_cli_args(cmd)
        # editor.goto falls through to bridge_required (not --goto)
        assert "bridge_required" in args[1]

    def test_to_cli_args_install_extension(self):
        cmd = self.cmds.install_extension("ms-python.python")
        args = self.cmds.to_cli_args(cmd)
        assert "--install-extension" in args

    def test_to_cli_args_uninstall_extension(self):
        cmd = self.cmds.uninstall_extension("ms-python.python")
        args = self.cmds.to_cli_args(cmd)
        assert "--uninstall-extension" in args

    def test_execute_cli_not_found(self):
        cmds = VSCodeCommands(code_cli="nonexistent_cli_12345")
        cmd = cmds.open_file("test.py")
        result = cmds.execute_cli(cmd)
        assert result["success"] is False


# ============================================================================
# VSCodeBridge (Hermes integration)
# ============================================================================

class TestVSCodeBridge:
    @pytest.mark.asyncio
    async def test_start_shutdown(self):
        bus = MessageBus()
        cmds = VSCodeCommands()
        bridge = VSCodeBridge(bus, cmds, backend="cli")
        await bridge.start()
        assert bridge._running
        await bridge.shutdown()
        assert not bridge._running

    @pytest.mark.asyncio
    async def test_file_open_via_bus(self):
        """Test message routing — CLI execution not tested here."""
        bus = MessageBus()
        cmds = VSCodeCommands()
        bridge = VSCodeBridge(bus, cmds, backend="extension")
        await bridge.start()

        reply = await bus.request(
            Topic("vscode.file.open"),
            payload={"file": "test.py", "line": 10},
            sender="test",
            timeout=3.0,
        )
        assert reply.payload["backend"] == "extension"
        assert reply.payload["command"]["category"] == "file"
        await bridge.shutdown()

    @pytest.mark.asyncio
    async def test_extension_mode(self):
        bus = MessageBus()
        cmds = VSCodeCommands()
        bridge = VSCodeBridge(bus, cmds, backend="extension")
        await bridge.start()

        reply = await bus.request(
            Topic("vscode.editor.insert"),
            payload={"text": "hello", "file": "test.py"},
            sender="test",
            timeout=3.0,
        )
        assert reply.payload["backend"] == "extension"
        assert reply.payload["command"]["category"] == "editor"
        await bridge.shutdown()

    @pytest.mark.asyncio
    async def test_unknown_action(self):
        bus = MessageBus()
        cmds = VSCodeCommands()
        bridge = VSCodeBridge(bus, cmds)
        await bridge.start()

        reply = await bus.request(
            Topic("vscode.file.nonexistent"),
            payload={},
            sender="test",
            timeout=3.0,
        )
        assert "error" in reply.payload
        await bridge.shutdown()

    @pytest.mark.asyncio
    async def test_terminal_create(self):
        bus = MessageBus()
        cmds = VSCodeCommands()
        bridge = VSCodeBridge(bus, cmds, backend="extension")
        await bridge.start()

        reply = await bus.request(
            Topic("vscode.terminal.create"),
            payload={"name": "JARVIS-Server"},
            sender="test",
            timeout=3.0,
        )
        assert reply.payload["backend"] == "extension"
        assert reply.payload["command"]["category"] == "terminal"
        await bridge.shutdown()

    @pytest.mark.asyncio
    async def test_multiple_commands(self):
        """Run multiple commands sequentially via bus — extension mode."""
        bus = MessageBus()
        cmds = VSCodeCommands()
        bridge = VSCodeBridge(bus, cmds, backend="extension")
        await bridge.start()

        for topic, payload in [
            ("vscode.file.open", {"file": "a.py"}),
            ("vscode.file.open", {"file": "b.py", "line": 5}),
            ("vscode.editor.format", {}),
            ("vscode.terminal.send", {"text": "npm start"}),
        ]:
            reply = await bus.request(
                Topic(topic), payload=payload, sender="test", timeout=3.0
            )
            assert reply.payload["backend"] == "extension"

        await bridge.shutdown()

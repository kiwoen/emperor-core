"""
VSCode Bridge — Hermes-connected VS Code integration.

Hot-pluggable module that bridges JARVIS's message bus to VS Code
operations. Subscribes to vscode.* topics and routes commands.

Two backends:
1. CLI mode: uses `code` CLI for basic file/open/extension operations
2. Extension mode: structured command objects for WebSocket/LSP bridge
   (placeholder for future VSCode extension)
"""

from __future__ import annotations

import logging
from typing import Any, Optional, TYPE_CHECKING

from jarvis.hermes.bus import Message, MessageBus, MessageType, Topic

if TYPE_CHECKING:
    from jarvis.vscode.commands import VSCodeCommands

logger = logging.getLogger("jarvis.vscode")


class VSCodeBridge:
    """VS Code integration via Hermes message bus.

    Usage:
        bridge = VSCodeBridge(bus, commands)
        await bridge.start()
        # … Orchestrator sends vscode.* requests …
        await bridge.shutdown()
    """

    def __init__(self, bus: MessageBus, commands: "VSCodeCommands", backend: str = "cli") -> None:
        self.bus = bus
        self.cmds = commands
        self.backend = backend  # "cli" or "extension"
        self._sub_id: Optional[str] = None
        self._running = False

    async def start(self) -> None:
        """Subscribe to vscode topics and start listening."""
        sub = self.bus.subscribe(self._on_message, "vscode.*")
        self._sub_id = sub.id
        self._running = True
        logger.info(
            "VSCodeBridge started (backend=%s, cli_available=%s)",
            self.backend, self.cmds.available,
        )

    async def shutdown(self) -> None:
        self._running = False
        if self._sub_id:
            self.bus.unsubscribe(self._sub_id)
            self._sub_id = None
        logger.info("VSCodeBridge shut down")

    # ------------------------------------------------------------------
    # Message routing
    # ------------------------------------------------------------------

    async def _on_message(self, msg: Message) -> None:
        """Route incoming messages by category."""
        if msg.type != MessageType.REQUEST:
            return

        topic_path = msg.topic.path  # e.g., "vscode.file.open"

        try:
            result = self._dispatch(topic_path, msg.payload)
            await self.bus.reply(msg, payload=result, sender="vscode")
        except Exception as exc:
            logger.exception("VSCodeBridge handler failed for %s", topic_path)
            await self.bus.reply(
                msg, payload={"error": str(exc), "topic": topic_path}, sender="vscode"
            )

    def _dispatch(self, topic: str, payload: Any) -> dict[str, Any]:
        """Parse topic and payload into a command, then execute."""
        parts = topic.split(".")
        if len(parts) < 3:
            return {"error": f"Invalid topic format: {topic}"}

        category = parts[1]   # file / editor / workspace / terminal / extension
        action = parts[2]     # open / goto / send / install …

        # Build command from payload
        params = payload if isinstance(payload, dict) else {}

        # Map known actions
        cmd = self._build_command(category, action, params)
        if cmd is None:
            return {"error": f"Unknown action: {category}.{action}"}

        # Execute
        if self.backend == "cli":
            return self.cmds.execute_cli(cmd)
        else:
            # Extension mode: return structured command for extension to consume
            return {"command": cmd.to_dict(), "backend": "extension"}

    def _build_command(self, category: str, action: str, params: dict) -> Optional[Any]:
        """Build an EditorCommand from category/action/params."""
        cmds = self.cmds

        if category == "file":
            if action == "open":
                return cmds.open_file(
                    params.get("file", ""),
                    line=params.get("line"),
                    column=params.get("column"),
                )
            elif action == "close":
                return cmds.close_file(params.get("file", ""))
            elif action == "save":
                return cmds.save_file(params.get("file"))
            elif action == "save_all":
                return cmds.save_all()
            elif action == "new":
                return cmds.new_untitled()

        elif category == "editor":
            if action == "goto":
                return cmds.goto_line(
                    params.get("file", ""),
                    line=params.get("line", 1),
                    column=params.get("column", 1),
                )
            elif action == "insert":
                return cmds.insert_text(params.get("text", ""), params.get("file"))
            elif action == "replace_selection":
                return cmds.replace_selection(params.get("text", ""))
            elif action == "delete_lines":
                return cmds.delete_lines(
                    params.get("start", 1),
                    params.get("end", 1),
                    params.get("file"),
                )
            elif action == "format":
                return cmds.format_document(params.get("file"))
            elif action == "get_selection":
                return cmds.get_selection()
            elif action == "get_cursor":
                return cmds.get_cursor_position()

        elif category == "workspace":
            if action == "list_files":
                return cmds.list_files(params.get("pattern", "*"))
            elif action == "search":
                return cmds.search_in_files(
                    params.get("query", ""),
                    params.get("include", "*.*"),
                )
            elif action == "diagnostics":
                return cmds.get_diagnostics(params.get("file"))
            elif action == "add_folder":
                return cmds.add_folder(params.get("path", ""))
            elif action == "get_path":
                return cmds.get_workspace_path()

        elif category == "terminal":
            if action == "send":
                return cmds.send_to_terminal(
                    params.get("text", ""),
                    params.get("id"),
                )
            elif action == "create":
                return cmds.create_terminal(params.get("name", "JARVIS"))
            elif action == "kill":
                return cmds.kill_terminal(params.get("id", ""))

        elif category == "extension":
            if action == "install":
                return cmds.install_extension(params.get("id", ""))
            elif action == "uninstall":
                return cmds.uninstall_extension(params.get("id", ""))
            elif action == "list":
                return cmds.list_extensions()

        return None

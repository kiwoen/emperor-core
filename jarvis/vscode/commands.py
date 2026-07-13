"""
VSCode Commands — structured command definitions for VS Code operations.

Each command maps to either:
- A `code` CLI invocation (e.g., `code --goto file:line`)
- A VS Code extension command (for future WebSocket/LSP integration)

Command categories:
- File: open, close, save, save_all, revert
- Editor: goto, select, insert, delete_line, format
- Workspace: list_files, search, diagnostics, add_folder
- Terminal: send_text, create, kill
- Extension: install, uninstall, list, enable, disable
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class EditorCommand:
    """A command to be executed in VS Code."""

    category: str  # file / editor / workspace / terminal / extension
    action: str
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "action": self.action,
            "params": self.params,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "EditorCommand":
        return EditorCommand(
            category=data.get("category", ""),
            action=data.get("action", ""),
            params=data.get("params", {}),
        )


class VSCodeCommands:
    """Factory and executor for VS Code editor commands.

    Produces shell commands for the `code` CLI, and provides structured
    command objects for future extension-based execution.
    """

    def __init__(self, code_cli: str = "code") -> None:
        self.code_cli = code_cli
        self._available = self._check_available()

    def _check_available(self) -> bool:
        try:
            subprocess.run(
                [self.code_cli, "--version"],
                capture_output=True, timeout=5,
            )
            return True
        except Exception:
            return False

    @property
    def available(self) -> bool:
        return self._available

    # ------------------------------------------------------------------
    # File commands
    # ------------------------------------------------------------------

    def open_file(self, file_path: str, line: Optional[int] = None, column: Optional[int] = None) -> EditorCommand:
        """Open a file, optionally at a specific position."""
        params: dict[str, Any] = {"file": file_path}
        if line is not None:
            params["line"] = line
        if column is not None:
            params["column"] = column
        return EditorCommand(category="file", action="open", params=params)

    def close_file(self, file_path: str) -> EditorCommand:
        return EditorCommand(category="file", action="close", params={"file": file_path})

    def save_file(self, file_path: Optional[str] = None) -> EditorCommand:
        return EditorCommand(category="file", action="save", params={"file": file_path or "current"})

    def save_all(self) -> EditorCommand:
        return EditorCommand(category="file", action="save_all", params={})

    def new_untitled(self) -> EditorCommand:
        return EditorCommand(category="file", action="new_untitled", params={})

    # ------------------------------------------------------------------
    # Editor commands
    # ------------------------------------------------------------------

    def goto_line(self, file_path: str, line: int, column: int = 1) -> EditorCommand:
        return EditorCommand(category="editor", action="goto", params={
            "file": file_path, "line": line, "column": column,
        })

    def insert_text(self, text: str, file_path: Optional[str] = None) -> EditorCommand:
        return EditorCommand(category="editor", action="insert", params={
            "text": text, "file": file_path or "current",
        })

    def replace_selection(self, text: str) -> EditorCommand:
        return EditorCommand(category="editor", action="replace_selection", params={"text": text})

    def delete_lines(self, start: int, end: int, file_path: Optional[str] = None) -> EditorCommand:
        return EditorCommand(category="editor", action="delete_lines", params={
            "start": start, "end": end, "file": file_path or "current",
        })

    def format_document(self, file_path: Optional[str] = None) -> EditorCommand:
        return EditorCommand(category="editor", action="format", params={"file": file_path or "current"})

    def get_selection(self) -> EditorCommand:
        """Request current selection (reply expected via bus)."""
        return EditorCommand(category="editor", action="get_selection", params={})

    def get_cursor_position(self) -> EditorCommand:
        return EditorCommand(category="editor", action="get_cursor", params={})

    # ------------------------------------------------------------------
    # Workspace commands
    # ------------------------------------------------------------------

    def list_files(self, pattern: str = "*") -> EditorCommand:
        return EditorCommand(category="workspace", action="list_files", params={"pattern": pattern})

    def search_in_files(self, query: str, include: str = "*.*") -> EditorCommand:
        return EditorCommand(category="workspace", action="search", params={
            "query": query, "include": include,
        })

    def get_diagnostics(self, file_path: Optional[str] = None) -> EditorCommand:
        return EditorCommand(category="workspace", action="diagnostics", params={
            "file": file_path or "current",
        })

    def add_folder(self, folder_path: str) -> EditorCommand:
        return EditorCommand(category="workspace", action="add_folder", params={"path": folder_path})

    def get_workspace_path(self) -> EditorCommand:
        return EditorCommand(category="workspace", action="get_path", params={})

    # ------------------------------------------------------------------
    # Terminal commands
    # ------------------------------------------------------------------

    def send_to_terminal(self, text: str, terminal_id: Optional[str] = None) -> EditorCommand:
        return EditorCommand(category="terminal", action="send", params={
            "text": text, "id": terminal_id or "active",
        })

    def create_terminal(self, name: str = "JARVIS") -> EditorCommand:
        return EditorCommand(category="terminal", action="create", params={"name": name})

    def kill_terminal(self, terminal_id: str) -> EditorCommand:
        return EditorCommand(category="terminal", action="kill", params={"id": terminal_id})

    # ------------------------------------------------------------------
    # Extension commands
    # ------------------------------------------------------------------

    def install_extension(self, extension_id: str) -> EditorCommand:
        return EditorCommand(category="extension", action="install", params={"id": extension_id})

    def uninstall_extension(self, extension_id: str) -> EditorCommand:
        return EditorCommand(category="extension", action="uninstall", params={"id": extension_id})

    def list_extensions(self) -> EditorCommand:
        return EditorCommand(category="extension", action="list", params={})

    # ------------------------------------------------------------------
    # CLI execution (for `code` CLI bridge)
    # ------------------------------------------------------------------

    def to_cli_args(self, cmd: EditorCommand) -> list[str]:
        """Convert a command to `code` CLI arguments."""
        args = [self.code_cli]

        if cmd.category == "file" and cmd.action == "open":
            path = cmd.params["file"]
            line = cmd.params.get("line")
            column = cmd.params.get("column")
            if line is not None:
                goto = f"{path}:{line}"
                if column is not None:
                    goto += f":{column}"
                args.extend(["--goto", goto])
            else:
                args.append(path)

        elif cmd.category == "file" and cmd.action == "new_untitled":
            args.append("--new-window")

        elif cmd.category == "file" and cmd.action == "save_all":
            # Not directly supported via CLI; fallback
            args = ["echo", "save_all requires extension bridge"]

        elif cmd.category == "workspace" and cmd.action == "add_folder":
            args.extend(["--add", cmd.params["path"]])

        elif cmd.category == "extension" and cmd.action == "install":
            args.extend(["--install-extension", cmd.params["id"]])

        elif cmd.category == "extension" and cmd.action == "uninstall":
            args.extend(["--uninstall-extension", cmd.params["id"]])

        elif cmd.category == "extension" and cmd.action == "list":
            args.extend(["--list-extensions"])

        else:
            # Unsupported via CLI — needs extension bridge
            args = ["echo", f"bridge_required:{cmd.category}.{cmd.action}"]

        return args

    def execute_cli(self, cmd: EditorCommand) -> dict[str, Any]:
        """Execute a command via `code` CLI (best-effort)."""
        args = self.to_cli_args(cmd)
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=30)
            return {
                "success": result.returncode == 0,
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip(),
                "args": args,
            }
        except FileNotFoundError:
            return {"success": False, "error": f"'{self.code_cli}' not found"}
        except Exception as e:
            return {"success": False, "error": str(e)}

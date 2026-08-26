"""
VSCode Bridge — HUANXIN editor integration module.

Bridges HUANXIN (via Hermes) to VS Code for:
- File operations (open, save, navigate)
- Editor control (cursor, selection, edits)
- Workspace queries (file listing, search, diagnostics)
- Terminal commands
- Extension management

Architecture:
    ┌──────────┐     Hermes Bus     ┌──────────────┐
    │Orchestrator│ ◄────────────── ► │ VSCode Bridge │
    └──────────┘                    └──────┬───────┘
                                           │
                                   ┌───────▼────────┐
                                   │  CLI Stub       │ (vs code --command)
                                   │  or Extension   │ (WebSocket/LSP)
                                   └────────────────┘

Hermes topics: vscode.file.*, vscode.editor.*, vscode.workspace.*, vscode.terminal.*
"""

from huanxin.vscode.bridge import VSCodeBridge
from huanxin.vscode.commands import VSCodeCommands

__all__ = ["VSCodeBridge", "VSCodeCommands"]

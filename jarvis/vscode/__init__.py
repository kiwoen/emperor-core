"""
VSCode Bridge — JARVIS editor integration module.

Bridges JARVIS (via Hermes) to VS Code for:
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

from jarvis.vscode.bridge import VSCodeBridge
from jarvis.vscode.commands import VSCodeCommands

__all__ = ["VSCodeBridge", "VSCodeCommands"]

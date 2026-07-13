"""
Hermes Agent — JARVIS MCP bridge.

Exposes Hermes message bus topics as MCP tools, allowing external AI agents
(Codex CLI, Claude Code, Marvis) to publish/subscribe/request via MCP protocol.

Architecture:
    ┌──────────────┐  MCP/stdio   ┌──────────────┐  Hermes Bus  ┌───────────┐
    │ External AI   │◄───────────►│ Hermes Agent   │◄───────────►│  Modules   │
    │ (Codex/Claude)│             │ (MCP Server)   │             │ (VSCode…)  │
    └──────────────┘             └──────────────┘             └───────────┘

Key capabilities:
- herm_bus_publish: publish messages to Hermes topics
- herm_bus_request: request/reply pattern via Hermes
- herm_bus_subscribe: subscribe to topics (streaming)
- herm_bus_list_topics: discover active Hermes topics

Also includes MCP client: Hermes → external MCP servers (e.g., Claude's tools).
"""

from jarvis.hermes_agent.server import HermesMCPServer, serve
from jarvis.hermes_agent.client import HermesMCPClient

__all__ = ["HermesMCPServer", "HermesMCPClient", "serve"]

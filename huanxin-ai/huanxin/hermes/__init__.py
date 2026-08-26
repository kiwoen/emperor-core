"""
Hermes — HUANXIN Internal Message Bus.

A lightweight async pub/sub + request/reply message bus that connects
all HUANXIN subsystems (Orchestrator, Codex, VSCode Bridge, domains, etc.).

Design principles:
- Zero external dependencies beyond asyncio
- Topic-based publish/subscribe with wildcard support
- Request/reply pattern with timeout
- Event sourcing: all messages persisted to append-only log
- Hot-pluggable: modules connect/disconnect at runtime

Architecture:
    ┌──────────┐    ┌──────────┐    ┌──────────┐
    │Orchestrator│   │  Codex   │    │  VSCode  │
    └─────┬─────┘    └────┬─────┘    └────┬─────┘
          │               │               │
          └───────────────┼───────────────┘
                          │
                   ┌──────▼──────┐
                   │   Hermes    │
                   │  Message    │
                   │    Bus      │
                   └──────┬──────┘
                          │
                   ┌──────▼──────┐
                   │ Event Log   │
                   │ (append-only)│
                   └─────────────┘
"""

from huanxin.hermes.bus import MessageBus, Message, Topic, Subscription
from huanxin.hermes.event_log import EventLog

__all__ = ["MessageBus", "Message", "Topic", "Subscription", "EventLog"]

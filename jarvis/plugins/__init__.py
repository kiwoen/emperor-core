"""Built-in plugins for the Emperor system.

This package provides production-ready plugins that enhance the
Emperor with logging, metrics, notifications, and other cross-cutting
concerns — all behind the Plugin interface. Register them with
`emperor.plugins.register(...)` to activate.
"""

from jarvis.plugins.metrics import MetricsPlugin
from jarvis.plugins.logger import LoggingPlugin

__all__ = ["MetricsPlugin", "LoggingPlugin"]

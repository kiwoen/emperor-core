"""Built-in plugins for the Huanxin system.

This package provides production-ready plugins that enhance the
Huanxin with logging, metrics, notifications, and other cross-cutting
concerns — all behind the Plugin interface. Register them with
`emperor.plugins.register(...)` to activate.
"""

from huanxin.plugins.metrics import MetricsPlugin
from huanxin.plugins.logger import LoggingPlugin

__all__ = ["MetricsPlugin", "LoggingPlugin"]

"""Configuration compatibility shim for 幻炘AI.

The single source of truth for configuration is now ``huanxin.config``
(see :class:`huanxin.config.HuanxinConfig`, a pydantic-settings model).

This module exists only so that legacy imports such as::

    from huanxin.core.config import HUANXINConfig, load_config

keep working unchanged. ``HUANXINConfig`` is an alias for the unified
``HuanxinConfig``; ``load_config`` is re-exported as-is.

Do **not** add configuration fields here — edit ``huanxin/config.py`` instead.
"""

from huanxin.config import HuanxinConfig as HUANXINConfig, load_config

__all__ = ["HUANXINConfig", "load_config"]

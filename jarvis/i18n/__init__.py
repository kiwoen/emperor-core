"""Jarvis I18n — bilingual internationalisation for Emperor Core.

Provides locale-aware translation with automatic system-language detection,
parameter interpolation, and graceful fallback.

Usage::

    from jarvis.i18n import I18nEngine

    i18n = I18nEngine()
    print(i18n.get("system.start", locale="zh"))  # 帝王系统启动中...
    print(i18n.get("system.start", locale="en"))  # Emperor system starting...
"""

from jarvis.i18n.translator import I18nEngine

__all__ = ["I18nEngine"]

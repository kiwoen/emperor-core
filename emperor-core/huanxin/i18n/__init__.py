"""Huanxin I18n — bilingual internationalisation for 幻炘AI.

Provides locale-aware translation with automatic system-language detection,
parameter interpolation, and graceful fallback.

Usage::

    from huanxin.i18n import I18nEngine

    i18n = I18nEngine()
    print(i18n.get("system.start", locale="zh"))  # 帝王系统启动中...
    print(i18n.get("system.start", locale="en"))  # Huanxin system starting...
"""

from huanxin.i18n.translator import I18nEngine

__all__ = ["I18nEngine"]

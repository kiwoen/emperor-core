"""I18nEngine — bilingual translation engine with automatic locale detection.

Detects system/OS language on init, loads JSON language packs from
``huanxin/i18n/locales/``, and provides ``get(key, locale)`` with:

- Parameter interpolation: ``"{name} 你好"`` → ``"Hello {name}"``
- Fallback chain: ``requested locale → default_locale → raw key``
"""

from __future__ import annotations

import json
import locale as locale_module
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("huanxin.i18n")


# ── system locale detection ────────────────────────────────────────────

def _detect_system_locale() -> str:
    """Detect the system / OS locale and map it to a supported locale code.

    Priority:
    1. ``LANG`` / ``LANGUAGE`` / ``LC_ALL`` environment variable
    2. Python ``locale.getdefaultlocale()``
    3. Fallback ``zh``
    """
    for var in ("LANG", "LANGUAGE", "LC_ALL", "LC_MESSAGES"):
        val = os.environ.get(var)
        if val:
            val_lower = val.lower()
            if val_lower.startswith("zh"):
                return "zh"
            if val_lower.startswith("en"):
                return "en"
            break

    try:
        sys_locale = locale_module.getdefaultlocale()
        if sys_locale and sys_locale[0]:
            lang = sys_locale[0].lower()
            if lang.startswith("zh"):
                return "zh"
            if lang.startswith("en"):
                return "en"
    except Exception:
        pass

    return "zh"


# ── translation engine ─────────────────────────────────────────────────

class I18nEngine:
    """Bilingual internationalisation (i18n) engine.

    Loads JSON language packs from ``locales/`` relative to this module,
    detects the best-matching locale, and resolves translation keys with
    Python ``str.format`` interpolation.

    Parameters
    ----------
    default_locale : str, default "zh"
        Fallback locale when the requested locale is unavailable.
    locales_dir : Optional[str], default None
        Custom path to the ``locales/`` directory.  When *None* the
        directory ``huanxin/i18n/locales/`` is used.

    Key format
    ----------
    Keys use dot-separated segments, e.g. ``"system.start"``,
    ``"dashboard.title"``, ``"capability.datetime"``.

    Interpolation
    -------------
    Translation values may contain ``{name}`` placeholders that are
    resolved via ``kwargs`` passed to ``get()``.

    **Example**

    .. code-block:: python

        engine = I18nEngine()
        print(engine.get("system.start"))              # 帝王系统启动中...
        print(engine.get("minister.appoint", name="诸葛亮"))
    """

    _SUPPORTED_LOCALES: tuple[str, ...] = ("zh", "en")

    def __init__(
        self,
        default_locale: str = "zh",
        locales_dir: Optional[str] = None,
    ) -> None:
        self._default_locale = default_locale
        self._system_locale = _detect_system_locale()
        self._translations: dict[str, dict[str, str]] = {}

        if locales_dir:
            self._locales_dir = Path(locales_dir)
        else:
            self._locales_dir = (Path(__file__).resolve().parent / "locales")

        self._load_all()

    # ── properties ─────────────────────────────────────────────────

    @property
    def default_locale(self) -> str:
        return self._default_locale

    @property
    def system_locale(self) -> str:
        return self._system_locale

    @property
    def locales_dir(self) -> Path:
        return self._locales_dir

    @property
    def supported_locales(self) -> tuple[str, ...]:
        return self._SUPPORTED_LOCALES

    @property
    def loaded_locales(self) -> list[str]:
        return list(self._translations.keys())

    # ── loading ────────────────────────────────────────────────────

    def _load_all(self) -> None:
        """Load every JSON locale file in the locales directory."""
        if not self._locales_dir.is_dir():
            logger.warning("Locales directory not found: %s", self._locales_dir)
            return

        for locale_code in self._SUPPORTED_LOCALES:
            path = self._locales_dir / f"{locale_code}.json"
            if path.is_file():
                try:
                    with open(path, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                    self._translations[locale_code] = data
                    logger.info("Loaded locale %r (%d keys) from %s", locale_code, len(data), path)
                except (json.JSONDecodeError, OSError) as exc:
                    logger.error("Failed to load locale %r: %s", locale_code, exc)

    def reload(self) -> None:
        """Reload all locale files from disk."""
        self._translations.clear()
        self._load_all()

    # ── lookup ─────────────────────────────────────────────────────

    def _resolve(self, key: str, locale: str) -> Optional[str]:
        """Resolve a dotted key from a locale dict.  Returns *None* if missing."""
        data = self._translations.get(locale)
        if data is None:
            return None
        parts = key.split(".")
        current: Any = data
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
                if current is None:
                    return None
            else:
                return None
        return current if isinstance(current, str) else None

    def _interpolate(self, template: str, **kwargs: Any) -> str:
        """Safe ``str.format`` that leaves unmatched placeholders intact."""
        # Use format_map with a wrapper that returns the placeholder unchanged
        # when the key is missing.
        class _SafeDict(dict):
            def __missing__(self, key: str) -> str:
                return f"{{{key}}}"

        return template.format_map(_SafeDict(kwargs))

    def get(
        self,
        key: str,
        locale: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """Get a translated string for *key*.

        Resolution order
        -----------------
        1. *locale* (explicit parameter)  → 2. ``system_locale``  →
        3. ``default_locale``              → 4. *key* (raw, as-is)

        Parameters
        ----------
        key : str
            Dot-separated translation key, e.g. ``"system.start"``.
        locale : Optional[str]
            Preferred locale code (``"zh"`` / ``"en"``).  When *None*
            the system-detected locale is tried first.
        **kwargs : Any
            Values for ``{placeholder}`` interpolation.

        Returns
        -------
        str
            Resolved and interpolated translation string.
        """
        # Try preferred locales in order
        for loc in (locale, self._system_locale, self._default_locale):
            if loc is None:
                continue
            template = self._resolve(key, loc)
            if template is not None:
                return self._interpolate(template, **kwargs) if kwargs else template

        # Ultimate fallback: raw key
        return key

    def has_key(self, key: str, locale: Optional[str] = None) -> bool:
        """Return *True* if *key* exists in *locale* (or the default chain)."""
        for loc in (locale, self._system_locale, self._default_locale):
            if loc is None:
                continue
            if self._resolve(key, loc) is not None:
                return True
        return False

    def all_keys(self, locale: Optional[str] = None) -> list[str]:
        """Return all keys available in *locale* (flat list)."""
        loc = locale or self._default_locale
        data = self._translations.get(loc, {})
        return self._flatten_keys(data)

    @staticmethod
    def _flatten_keys(data: dict, prefix: str = "") -> list[str]:
        """Flatten a nested dict of translation keys."""
        keys: list[str] = []
        for k, v in data.items():
            full = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                keys.extend(I18nEngine._flatten_keys(v, full))
            else:
                keys.append(full)
        return keys

    def available_locales(self) -> list[str]:
        """Return the list of locales that have been successfully loaded."""
        return list(self._translations.keys())

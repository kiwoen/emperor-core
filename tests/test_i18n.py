"""Tests for the I18nEngine — bilingual internationalisation module."""

import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from jarvis.i18n import I18nEngine


# ══════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════

_LOCALES_DIR = (
    Path(__file__).resolve().parent.parent / "jarvis" / "i18n" / "locales"
)


def _temp_locales_dir(zh_data=None, en_data=None):
    """Create a temporary locales directory with optional custom data."""
    tmp = tempfile.mkdtemp(prefix="i18n_test_")
    if zh_data is not None:
        with open(Path(tmp) / "zh.json", "w", encoding="utf-8") as f:
            json.dump(zh_data, f, ensure_ascii=False)
    if en_data is not None:
        with open(Path(tmp) / "en.json", "w", encoding="utf-8") as f:
            json.dump(en_data, f, ensure_ascii=False)
    return tmp


# ══════════════════════════════════════════════════════════════════
# Construction & Loading
# ══════════════════════════════════════════════════════════════════


class TestConstruction:
    def test_create_with_defaults(self):
        engine = I18nEngine()
        assert engine.default_locale == "zh"
        assert engine.system_locale in ("zh", "en")
        assert len(engine.loaded_locales) >= 2

    def test_create_with_custom_default(self):
        engine = I18nEngine(default_locale="en")
        assert engine.default_locale == "en"

    def test_create_with_custom_locales_dir(self):
        tmp = _temp_locales_dir(
            zh_data={"greeting": "你好"},
            en_data={"greeting": "Hello"},
        )
        try:
            engine = I18nEngine(locales_dir=tmp)
            assert engine.loaded_locales == ["zh", "en"]
            assert engine.get("greeting", locale="zh") == "你好"
            assert engine.get("greeting", locale="en") == "Hello"
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_missing_locales_dir_does_not_crash(self):
        engine = I18nEngine(locales_dir="/nonexistent/path")
        assert engine.loaded_locales == []

    def test_locales_dir_property(self):
        engine = I18nEngine()
        assert engine.locales_dir == _LOCALES_DIR


# ══════════════════════════════════════════════════════════════════
# System locale detection
# ══════════════════════════════════════════════════════════════════


class TestSystemLocale:
    @mock.patch.dict(os.environ, {"LANG": "zh_CN.UTF-8"}, clear=True)
    def test_detect_zh_from_lang(self):
        engine = I18nEngine()
        assert engine.system_locale == "zh"

    @mock.patch.dict(os.environ, {"LANG": "en_US.UTF-8"}, clear=True)
    def test_detect_en_from_lang(self):
        engine = I18nEngine()
        assert engine.system_locale == "en"

    @mock.patch.dict(os.environ, {"LANGUAGE": "zh_TW"}, clear=True)
    def test_detect_zh_from_language(self):
        engine = I18nEngine()
        assert engine.system_locale == "zh"


# ══════════════════════════════════════════════════════════════════
# get() — key resolution & interpolation
# ══════════════════════════════════════════════════════════════════


class TestGet:
    def test_get_zh_system_start(self):
        engine = I18nEngine(default_locale="zh")
        assert engine.get("system.start", locale="zh") == "帝王系统启动中..."

    def test_get_en_system_start(self):
        engine = I18nEngine(default_locale="zh")
        assert engine.get("system.start", locale="en") == "Emperor system starting..."

    def test_get_with_auto_locale(self):
        engine = I18nEngine(default_locale="zh")
        result = engine.get("system.ready")
        assert result in ("帝王系统已就绪", "Emperor system ready")

    def test_get_nested_key(self):
        engine = I18nEngine()
        assert engine.get("dashboard.title", locale="zh") == "帝王 Dashboard"

    def test_get_minister_key(self):
        engine = I18nEngine()
        assert engine.get("minister.appoint", locale="zh") == "任命大臣：{name}"

    def test_get_capability_key(self):
        engine = I18nEngine()
        assert "日期" in engine.get("capability.datetime", locale="zh")
        assert "timezone" in engine.get("capability.datetime", locale="en")


# ══════════════════════════════════════════════════════════════════
# Parameter interpolation
# ══════════════════════════════════════════════════════════════════


class TestInterpolation:
    def test_interpolate_single_zh(self):
        engine = I18nEngine()
        result = engine.get("minister.appoint", locale="zh", name="诸葛亮")
        assert result == "任命大臣：诸葛亮"

    def test_interpolate_single_en(self):
        engine = I18nEngine()
        result = engine.get("minister.appoint", locale="en", name="Zhuge Liang")
        assert result == "Minister appointed: Zhuge Liang"

    def test_interpolate_multi_en(self):
        engine = I18nEngine()
        result = engine.get("minister.duel", locale="en", a="Cao Cao", b="Liu Bei")
        assert result == "Minister Cao Cao is dueling against Liu Bei"

    def test_interpolate_multi_zh(self):
        engine = I18nEngine()
        result = engine.get("minister.evolve", locale="zh", name="司马懿", cycle="3")
        assert result == "大臣 司马懿 正在进化 (第 3 轮)"

    def test_interpolate_gold_content(self):
        engine = I18nEngine()
        result = engine.get("minister.gold_content", locale="zh", value="99.9")
        assert result == "含金量：99.9%"

    def test_interpolate_extra_kwargs_ignored(self):
        engine = I18nEngine()
        result = engine.get("minister.appoint", locale="zh", name="曹操", extra="unused")
        assert result == "任命大臣：曹操"

    def test_interpolate_missing_kwargs_left_as_is(self):
        engine = I18nEngine()
        result = engine.get("minister.appoint", locale="zh")
        assert result == "任命大臣：{name}"


# ══════════════════════════════════════════════════════════════════
# Fallback chain
# ══════════════════════════════════════════════════════════════════


class TestFallback:
    def test_explicit_locale_not_loaded_falls_to_system(self):
        engine = I18nEngine(default_locale="zh")
        result = engine.get("system.start", locale="fr")
        assert result in ("帝王系统启动中...", "Emperor system starting...")

    def test_fallback_to_default(self):
        engine = I18nEngine(default_locale="zh")
        with mock.patch.object(engine, "_resolve", side_effect=[None, None, "默认文本"]):
            result = engine.get("some.key", locale="en")
            assert result == "默认文本"

    def test_fallback_to_raw_key(self):
        engine = I18nEngine()
        result = engine.get("nonexistent.abc.xyz", locale="zh")
        assert result == "nonexistent.abc.xyz"

    def test_fallback_keeps_interpolation_even_on_raw_key(self):
        engine = I18nEngine()
        result = engine.get("nonexistent.key", locale="zh")
        assert result == "nonexistent.key"


# ══════════════════════════════════════════════════════════════════
# has_key
# ══════════════════════════════════════════════════════════════════


class TestHasKey:
    def test_existing_key(self):
        engine = I18nEngine()
        assert engine.has_key("system.start") is True

    def test_nonexistent_key(self):
        engine = I18nEngine()
        assert engine.has_key("nonexistent.key") is False

    def test_has_key_with_explicit_locale(self):
        engine = I18nEngine()
        assert engine.has_key("system.start", locale="en") is True

    def test_has_key_missing_locale_checks_chain(self):
        engine = I18nEngine(default_locale="zh")
        assert engine.has_key("system.start", locale="fr") is True


# ══════════════════════════════════════════════════════════════════
# all_keys
# ══════════════════════════════════════════════════════════════════


class TestAllKeys:
    def test_all_keys_zh(self):
        engine = I18nEngine()
        keys = engine.all_keys(locale="zh")
        assert len(keys) >= 30
        assert "system.start" in keys
        assert "capability.datetime" in keys
        assert "minister.gold_content" in keys

    def test_all_keys_en(self):
        engine = I18nEngine()
        keys = engine.all_keys(locale="en")
        assert len(keys) >= 30
        assert "system.start" in keys
        assert "dashboard.title" in keys

    def test_all_keys_default_locale(self):
        engine = I18nEngine(default_locale="zh")
        keys = engine.all_keys()
        assert "system.start" in keys


# ══════════════════════════════════════════════════════════════════
# reload
# ══════════════════════════════════════════════════════════════════


class TestReload:
    def test_reload_preserves_keys(self):
        engine = I18nEngine()
        before = engine.all_keys(locale="zh")
        engine.reload()
        after = engine.all_keys(locale="zh")
        assert before == after

    def test_reload_picks_up_new_data(self):
        tmp = _temp_locales_dir(
            zh_data={"greeting": "你好"},
        )
        try:
            engine = I18nEngine(locales_dir=tmp)
            assert engine.get("greeting", locale="zh") == "你好"

            # Modify the file on disk
            with open(Path(tmp) / "zh.json", "w", encoding="utf-8") as f:
                json.dump({"greeting": "您好", "farewell": "再见"}, f, ensure_ascii=False)

            engine.reload()
            assert engine.get("greeting", locale="zh") == "您好"
            assert engine.get("farewell", locale="zh") == "再见"
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════
# Supported locales
# ══════════════════════════════════════════════════════════════════


class TestSupportedLocales:
    def test_supported_locales(self):
        engine = I18nEngine()
        assert engine.supported_locales == ("zh", "en")

    def test_available_locales(self):
        engine = I18nEngine()
        locales = engine.available_locales()
        assert "zh" in locales
        assert "en" in locales


# ══════════════════════════════════════════════════════════════════
# Locale completeness — ensure en.json has all zh.json keys
# ══════════════════════════════════════════════════════════════════


class TestLocaleCompleteness:
    def test_en_has_all_zh_keys(self):
        engine = I18nEngine()
        zh_keys = set(engine.all_keys(locale="zh"))
        en_keys = set(engine.all_keys(locale="en"))
        missing = zh_keys - en_keys
        assert not missing, f"en.json missing keys: {missing}"

    def test_zh_has_all_en_keys(self):
        engine = I18nEngine()
        zh_keys = set(engine.all_keys(locale="zh"))
        en_keys = set(engine.all_keys(locale="en"))
        extra = en_keys - zh_keys
        assert not extra, f"zh.json missing keys: {extra}"


# ══════════════════════════════════════════════════════════════════
# Capability descriptions — verify all 12 are present
# ══════════════════════════════════════════════════════════════════


class TestCapabilityI18n:
    CAP_NAMES = [
        "datetime", "math", "random", "text", "file_info",
        "hash", "json_tool", "uuid_gen", "weather", "news",
        "web_search", "web_fetch",
    ]

    def test_all_12_capabilities_in_zh(self):
        engine = I18nEngine()
        for cap in self.CAP_NAMES:
            key = f"capability.{cap}"
            val = engine.get(key, locale="zh")
            assert val != key, f"Missing zh translation for {key}"
            assert len(val) > 0

    def test_all_12_capabilities_in_en(self):
        engine = I18nEngine()
        for cap in self.CAP_NAMES:
            key = f"capability.{cap}"
            val = engine.get(key, locale="en")
            assert val != key, f"Missing en translation for {key}"
            assert len(val) > 0


# ══════════════════════════════════════════════════════════════════
# Integration — realistic usage patterns
# ══════════════════════════════════════════════════════════════════


class TestIntegration:
    def test_bilingual_system_messages(self):
        engine = I18nEngine()
        msgs = [
            ("system.start", "zh"),
            ("system.start", "en"),
            ("system.shutdown", "zh"),
            ("system.shutdown", "en"),
            ("system.error", "zh"),
            ("system.error", "en"),
        ]
        for key, loc in msgs:
            result = engine.get(key, locale=loc)
            assert len(result) > 3
            assert result != key

    def test_error_messages(self):
        engine = I18nEngine()
        assert "not found" in engine.get("error.not_found", locale="en", resource="file.txt").lower()
        assert "未找到" in engine.get("error.not_found", locale="zh", resource="文件.txt")

    def test_task_lifecycle_messages(self):
        engine = I18nEngine()
        assert "T-001" in engine.get("task.completed", locale="en", task_id="T-001")
        assert "T-001" in engine.get("task.completed", locale="zh", task_id="T-001")

    def test_evolution_cycle_messages(self):
        engine = I18nEngine()
        zh = engine.get("evolution.complete", locale="zh", cycle="5", survivors="18", total="20")
        en = engine.get("evolution.complete", locale="en", cycle="5", survivors="18", total="20")
        assert "18" in zh and "20" in zh
        assert "18" in en and "20" in en

    def test_court_messages(self):
        engine = I18nEngine()
        assert engine.get("court.session_open", locale="zh") == "朝会开启"
        assert engine.get("court.session_open", locale="en") == "Court session opened"

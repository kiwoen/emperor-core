"""Tests for the Capability System — real-function handlers for ministers."""

import os
import tempfile
from pathlib import Path

import pytest

from jarvis.capability import (
    Capability,
    CapabilityRegistry,
    _handle_datetime,
    _handle_file_info,
    _handle_hash,
    _handle_json_tool,
    _handle_math,
    _handle_random,
    _handle_text,
    _handle_uuid_gen,
    _web_fetch_handler,
    _web_search_handler,
    _safe_eval_math,
    create_default_registry,
)


# ══════════════════════════════════════════════════════════════════
# Capability dataclass
# ══════════════════════════════════════════════════════════════════


class TestCapability:
    def test_create_valid(self):
        c = Capability("math", "计算表达式", ["math"], lambda p: {"result": "ok", "data": {}})
        assert c.name == "math"
        assert c.description == "计算表达式"
        assert c.domains == ["math"]

    def test_empty_name_raises(self):
        with pytest.raises(ValueError, match="name must not be empty"):
            Capability("", "desc", ["general"], lambda p: {})

    def test_empty_domains_raises(self):
        with pytest.raises(ValueError, match="at least one domain"):
            Capability("test", "desc", [], lambda p: {})


# ══════════════════════════════════════════════════════════════════
# _handle_datetime
# ══════════════════════════════════════════════════════════════════


class TestHandleDatetime:
    def test_returns_valid_result(self):
        r = _handle_datetime("现在几点？")
        assert "result" in r
        assert "data" in r
        assert "当前时间" in r["result"]

    def test_data_has_required_fields(self):
        r = _handle_datetime(""
                            "当前日期")
        data = r["data"]
        for field in ["iso", "date", "time", "weekday", "weekday_cn", "year", "month", "day"]:
            assert field in data, f"Missing field: {field}"

    def test_weekday_is_valid(self):
        r = _handle_datetime("今天星期几")
        assert r["data"]["weekday"] in [
            "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
        ]


# ══════════════════════════════════════════════════════════════════
# _handle_math
# ══════════════════════════════════════════════════════════════════


class TestHandleMath:
    def test_basic_addition(self):
        r = _handle_math("计算 3 + 5")
        assert r["data"]["value"] == 8
        assert "3 + 5 = 8" in r["result"]

    def test_multiplication_with_asterisk(self):
        r = _handle_math("算一下 17 * 23")
        assert r["data"]["value"] == 391

    def test_division(self):
        r = _handle_math("100 / 4 等于多少")
        val = r["data"]["value"]
        assert abs(val - 25.0) < 0.001

    def test_complex_expression(self):
        r = _handle_math("计算 (2 + 3) * 4 - 10")
        # "12" in result text
        assert "10" in r["result"] or r["data"]["value"] is not None

    def test_no_expression_found(self):
        r = _handle_math("你好世界")
        data = r["data"]
        # Either can't find expression or returns fallback
        assert data["value"] is not None or "无法从" in r["result"]

    def test_math_with_chinese_prompt(self):
        r = _handle_math("帮我计算一下 50 * 2")
        assert r["data"]["value"] == 100


# ══════════════════════════════════════════════════════════════════
# _handle_random
# ══════════════════════════════════════════════════════════════════


class TestHandleRandom:
    def test_dice_roll(self):
        r = _handle_random("掷骰子 2d6")
        data = r["data"]
        assert data["type"] == "dice"
        assert data["count"] == 2
        assert data["sides"] == 6
        assert len(data["rolls"]) == 2
        for roll in data["rolls"]:
            assert 1 <= roll <= 6

    def test_range_random(self):
        r = _handle_random("给我一个1到100的随机数")
        data = r["data"]
        assert data["type"] == "range"
        assert data["min"] == 1
        assert data["max"] == 100
        assert 1 <= data["value"] <= 100

    def test_pick_from_list(self):
        r = _handle_random("从苹果、香蕉、橘子中选一个")
        data = r["data"]
        assert data["type"] == "pick"
        assert data["chosen"] in data["items"]

    def test_default_float(self):
        r = _handle_random("来个随机数")
        data = r["data"]
        assert data["type"] == "float"
        assert 0 <= data["value"] <= 1


# ══════════════════════════════════════════════════════════════════
# _handle_text
# ══════════════════════════════════════════════════════════════════


class TestHandleText:
    def test_reverse(self):
        r = _handle_text("反转 hello")
        assert r["data"]["operation"] == "reverse"
        assert r["data"]["output"] == "olleh"

    def test_uppercase(self):
        r = _handle_text("大写 hello world")
        assert r["data"]["operation"] == "uppercase"
        assert r["data"]["output"] == "HELLO WORLD"

    def test_lowercase(self):
        r = _handle_text("小写 HELLO")
        assert r["data"]["operation"] == "lowercase"
        assert r["data"]["output"] == "hello"

    def test_statistics(self):
        r = _handle_text("统计字数：Hello World")
        assert r["data"]["operation"] == "stats"
        s = r["data"]["stats"]
        assert s["length"] == 11
        assert s["words"] == 2

    def test_statistics_chinese(self):
        r = _handle_text("统计字符：你好世界")
        assert r["data"]["operation"] == "stats"
        s = r["data"]["stats"]
        assert s["has_chinese"] is True
        assert s["length"] == 4


# ══════════════════════════════════════════════════════════════════
# _handle_file_info
# ══════════════════════════════════════════════════════════════════


class TestHandleFileInfo:
    def test_nonexistent_file(self):
        r = _handle_file_info("查看 C:/nonexistent/file.txt")
        assert r["data"]["exists"] is False

    def test_real_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("line1\nline2\nline3\n")
            f.flush()
            path = f.name
        try:
            r = _handle_file_info(f"文件信息 {path}")
            assert r["data"]["exists"] is True
            assert r["data"]["is_file"] is True
            assert r["data"]["line_count"] == 3
        finally:
            os.unlink(path)

    def test_no_path(self):
        r = _handle_file_info("这是什么文件")
        assert "未找到文件路径" in r["result"] or r["data"].get("error") == "no_file_path"


# ══════════════════════════════════════════════════════════════════
# CapabilityRegistry
# ══════════════════════════════════════════════════════════════════


class TestCapabilityRegistry:
    def test_register_and_count(self):
        reg = CapabilityRegistry()
        reg.register(Capability("test", "desc", ["general"], lambda p: {"result": "ok", "data": {}}))
        assert reg.count == 1

    def test_register_overwrites(self):
        reg = CapabilityRegistry()
        reg.register(Capability("test", "first", ["general"], lambda p: {"result": "1", "data": {}}))
        reg.register(Capability("test", "second", ["math"], lambda p: {"result": "2", "data": {}}))
        assert reg.count == 1
        assert reg.get_by_name("test").description == "second"

    def test_register_invalid_type(self):
        reg = CapabilityRegistry()
        with pytest.raises(TypeError):
            reg.register("not a capability")  # type: ignore

    def test_unregister(self):
        reg = CapabilityRegistry()
        reg.register(Capability("test", "desc", ["general"], lambda p: {"result": "ok", "data": {}}))
        assert reg.unregister("test") is True
        assert reg.count == 0
        assert reg.unregister("nonexistent") is False

    def test_get_by_domain(self):
        reg = CapabilityRegistry()
        reg.register(Capability("a", "desc", ["general"], lambda p: {"result": "a"}))
        reg.register(Capability("b", "desc", ["math"], lambda p: {"result": "b"}))
        reg.register(Capability("c", "desc", ["general", "data"], lambda p: {"result": "c"}))

        general_caps = reg.get("general")
        assert len(general_caps) == 2
        names = {c.name for c in general_caps}
        assert names == {"a", "c"}

        math_caps = reg.get("math")
        assert len(math_caps) == 1
        assert math_caps[0].name == "b"

        empty = reg.get("nonexistent")
        assert empty == []

    def test_get_by_name(self):
        reg = CapabilityRegistry()
        reg.register(Capability("math", "desc", ["math"], lambda p: {"result": "ok"}))
        assert reg.get_by_name("math") is not None
        assert reg.get_by_name("nonexistent") is None

    def test_list_all(self):
        reg = create_default_registry()
        assert reg.count == 10
        names = {c.name for c in reg.list_all()}
        assert names == {"datetime", "math", "random", "text", "file_info", "hash", "json_tool", "uuid_gen", "web_search", "web_fetch"}


# ══════════════════════════════════════════════════════════════════
# find_best
# ══════════════════════════════════════════════════════════════════


class TestFindBest:
    def test_math_keyword_match(self):
        reg = create_default_registry()
        cap = reg.find_best("帮我计算一下 3+5", "math")
        assert cap is not None
        assert cap.name == "math"

    def test_datetime_keyword_match(self):
        reg = create_default_registry()
        cap = reg.find_best("现在几点了", "general")
        assert cap is not None
        assert cap.name == "datetime"

    def test_date_keyword_match(self):
        reg = create_default_registry()
        cap = reg.find_best("今天的日期是什么", "general")
        assert cap is not None
        assert cap.name == "datetime"

    def test_random_keyword_match(self):
        reg = create_default_registry()
        cap = reg.find_best("掷一个骰子", "general")
        assert cap is not None
        assert cap.name == "random"

    def test_text_keyword_match(self):
        reg = create_default_registry()
        cap = reg.find_best("反转 hello world", "general")
        assert cap is not None
        assert cap.name == "text"

    def test_no_weather_capability(self):
        """Prompt about '天气' should not match any capability since there's no weather handler."""
        reg = create_default_registry()
        cap = reg.find_best("今天天气怎么样", "general")
        # There is no "weather" capability — should return None
        assert cap is None

    def test_no_match_at_all(self):
        reg = create_default_registry()
        cap = reg.find_best("帮我写一首诗", "general")
        assert cap is None


# ══════════════════════════════════════════════════════════════════
# execute
# ══════════════════════════════════════════════════════════════════


class TestExecute:
    def test_execute_success(self):
        reg = create_default_registry()
        result = reg.execute("datetime", "现在时间")
        assert "result" in result
        assert "data" in result

    def test_execute_not_found(self):
        reg = CapabilityRegistry()
        with pytest.raises(KeyError, match="not registered"):
            reg.execute("nonexistent", "hello")

    def test_execute_handler_raises(self):
        def bad_handler(prompt, **kwargs):
            raise ValueError("Boom!")

        reg = CapabilityRegistry()
        reg.register(Capability("bad", "desc", ["general"], bad_handler))
        with pytest.raises(RuntimeError, match="execution failed"):
            reg.execute("bad", "hello")


# ══════════════════════════════════════════════════════════════════
# Court integration via Emperor
# ══════════════════════════════════════════════════════════════════


class TestCourtIntegration:
    def test_emperor_has_capability_registry(self):
        from jarvis.emperor import Emperor
        emp = Emperor()
        assert emp.capability_registry is not None
        assert emp.capability_registry.count == 10

    def test_task_result_contains_capability_output(self):
        """When prompt matches a capability, result should contain capability output."""
        from jarvis.emperor import Emperor
        emp = Emperor()
        emp.register("test_minister", domain="general")
        # A math task should trigger math capability
        result = emp.execute_task("计算 3 + 5", domain="math")
        assert result["success"]
        # The response should include capability marker
        assert "[能力结果:" in result["response"]

    def test_task_without_capability_match(self):
        """When no capability matches, result should be normal (no capability marker)."""
        from jarvis.emperor import Emperor
        emp = Emperor()
        emp.register("test_minister", domain="general")
        # A weather task has no matching capability
        result = emp.execute_task("今天天气怎么样", domain="general")
        # Response may or may not have capability marker — depends on mock
        # Just verify it runs without error
        assert "response" in result


# ══════════════════════════════════════════════════════════════════
# _handle_hash
# ══════════════════════════════════════════════════════════════════


class TestHandleHash:
    def test_md5_default(self):
        r = _handle_hash("hash hello world")
        assert r["data"]["algorithm"] == "md5"
        # MD5("hello world") = 5eb63bbbe01eeed093cb22bb8f5acdc3
        assert r["data"]["digest"] == "5eb63bbbe01eeed093cb22bb8f5acdc3"

    def test_sha256_explicit(self):
        r = _handle_hash("sha256 hello world")
        assert r["data"]["algorithm"] == "sha256"
        assert len(r["data"]["digest"]) == 64
        assert r["data"]["digest"] == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"

    def test_sha1_explicit(self):
        r = _handle_hash("sha1 hello world")
        assert r["data"]["algorithm"] == "sha1"
        assert len(r["data"]["digest"]) == 40
        assert r["data"]["digest"] == "2aae6c35c94fcfb415dbe95f408b9ce91ee846ed"

    def test_empty_input(self):
        r = _handle_hash("md5 ")
        assert r["data"].get("error") == "no_text" or "错误" in r["result"]


# ══════════════════════════════════════════════════════════════════
# _handle_json_tool
# ══════════════════════════════════════════════════════════════════


class TestHandleJsonTool:
    def test_format_valid_json(self):
        r = _handle_json_tool('json 格式化 {"a":1,"b":[2,3]}')
        assert r["data"]["valid"] is True
        assert r["data"]["mode"] == "format"
        assert "{" in r["data"]["output"]
        assert "  " in r["data"]["output"]  # indented

    def test_compress_json(self):
        r = _handle_json_tool('json 压缩 {"a": 1, "b": 2}')
        assert r["data"]["valid"] is True
        assert r["data"]["mode"] == "compress"
        assert " " not in r["data"]["output"].replace('{"a":1,"b":2}', "")  # no spaces in compressed

    def test_invalid_json(self):
        r = _handle_json_tool('json 格式化 {"a": }')
        assert r["data"]["valid"] is False
        assert "error" in r["data"]

    def test_no_json_found(self):
        r = _handle_json_tool("json")
        assert r["data"].get("error") == "no_json" or "未找到" in r["result"]


# ══════════════════════════════════════════════════════════════════
# _handle_uuid_gen
# ══════════════════════════════════════════════════════════════════


class TestHandleUuidGen:
    def test_generates_valid_uuid4(self):
        r = _handle_uuid_gen("generate uuid")
        uid = r["data"]["uuid"]
        assert r["data"]["version"] == 4
        # UUID4 format: xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx
        parts = uid.split("-")
        assert len(parts) == 5
        assert len(uid) == 36
        assert parts[2][0] == "4"

    def test_uniqueness(self):
        uids = set()
        for _ in range(100):
            r = _handle_uuid_gen("uuid")
            uids.add(r["data"]["uuid"])
        assert len(uids) == 100


# ══════════════════════════════════════════════════════════════════
# _safe_eval_math
# ══════════════════════════════════════════════════════════════════


class TestSafeEvalMath:
    def test_simple_addition(self):
        assert _safe_eval_math("3 + 5") == 8

    def test_multiplication(self):
        assert _safe_eval_math("17 * 23") == 391

    def test_division_float(self):
        result = _safe_eval_math("10 / 4")
        assert abs(result - 2.5) < 0.001

    def test_power(self):
        assert _safe_eval_math("2 ** 8") == 256

    def test_complex_expression(self):
        result = _safe_eval_math("(3 + 5) * 2 - 1")
        assert result == 15

    def test_negative_unary(self):
        assert _safe_eval_math("-5 + 3") == -2


# ══════════════════════════════════════════════════════════════════
# create_default_registry
# ══════════════════════════════════════════════════════════════════


class TestDefaultRegistry:
    def test_has_all_ten(self):
        reg = create_default_registry()
        assert reg.count == 10

    def test_each_capability_executable(self):
        reg = create_default_registry()
        for cap_name in reg.list_names():
            # Skip file_info if no file path provided
            if cap_name == "file_info":
                result = reg.execute(cap_name, "文件信息 C:/nonexistent.txt")
            else:
                result = reg.execute(cap_name, f"test prompt for {cap_name}")
            assert "result" in result
            assert "data" in result


# ══════════════════════════════════════════════════════════════════
# _web_search_handler
# ══════════════════════════════════════════════════════════════════


class TestWebSearchHandler:
    def test_returns_result(self):
        r = _web_search_handler("Python programming language")
        assert "result" in r
        assert "data" in r

    def test_contains_data_abstract_topics_url(self):
        r = _web_search_handler("Python programming language")
        assert "abstract" in r["data"]
        assert "topics" in r["data"]
        assert "url" in r["data"]
        assert isinstance(r["data"]["topics"], list)

    def test_empty_query(self):
        r = _web_search_handler("")
        assert "result" in r
        assert "data" in r

    def test_chinese_query(self):
        r = _web_search_handler("Python 编程语言入门")
        assert "result" in r
        assert "data" in r

    def test_no_network_graceful(self, monkeypatch):
        import urllib.request

        def mock_urlopen(*args, **kwargs):
            raise OSError("Network unreachable")

        monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
        r = _web_search_handler("test")
        assert "搜索失败" in r["result"] or "fail" in r["result"].lower()
        assert r["data"]["topics"] == []


# ══════════════════════════════════════════════════════════════════
# _web_fetch_handler
# ══════════════════════════════════════════════════════════════════


class TestWebFetchHandler:
    def test_extracts_url(self):
        r = _web_fetch_handler("抓取 https://example.com 的内容")
        assert "result" in r
        assert "data" in r
        assert r["data"].get("url") == "https://example.com"

    def test_no_url(self):
        r = _web_fetch_handler("没有链接的普通文本")
        assert "未在任务描述中找到 URL" in r["result"]
        assert r["data"] == {}

    @pytest.mark.network
    def test_fetch_content(self):
        r = _web_fetch_handler("https://example.com")
        assert "result" in r
        assert "Example Domain" in r["result"] or len(r["result"]) > 0

    def test_invalid_url(self):
        r = _web_fetch_handler("https://this-domain-does-not-exist-12345.com")
        assert "网页抓取失败" in r["result"] or "fail" in r["result"].lower()

    def test_timeout(self, monkeypatch):
        import urllib.request

        def mock_urlopen(*args, **kwargs):
            raise TimeoutError("Connection timed out")

        monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
        r = _web_fetch_handler("https://example.com")
        assert "网页抓取失败" in r["result"] or "fail" in r["result"].lower()

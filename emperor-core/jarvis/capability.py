"""Capability System — real-function ability registry for ministers.

Each Capability wraps a handler that produces real output (not mock data).
Ministers discover and invoke capabilities based on domain + prompt matching.

Usage:
    from jarvis.capability import Capability, CapabilityRegistry

    reg = CapabilityRegistry()
    reg.register(Capability("datetime", "获取当前日期时间", ["general", "data"], _handle_datetime))
    result = reg.execute("datetime", "What's the time now?")
"""

from __future__ import annotations

import ast
import datetime as dt
import hashlib
import json as _json
import logging
import operator
import random as _random
import uuid as _uuid
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Optional

from jarvis.prompt_template import PromptTemplateManager

logger = logging.getLogger("jarvis.capability")

# Module-level template manager (initialized by Emperor / app startup)
_template_manager: Optional[PromptTemplateManager] = None


def set_template_manager(mgr: PromptTemplateManager) -> None:
    """Set the module-level PromptTemplateManager for use by all capabilities."""
    global _template_manager
    _template_manager = mgr
    logger.info("[Capability] PromptTemplateManager set")


def get_template_manager() -> Optional[PromptTemplateManager]:
    """Get the module-level PromptTemplateManager."""
    return _template_manager


# ══════════════════════════════════════════════════════════════════
# Data types
# ══════════════════════════════════════════════════════════════════


@dataclass
class Capability:
    """A named ability that a minister can invoke to produce real results."""

    name: str
    description: str
    domains: list[str]
    handler: Callable[..., dict]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Capability name must not be empty")
        if not self.domains:
            raise ValueError(f"Capability '{self.name}' must have at least one domain")


# ══════════════════════════════════════════════════════════════════
# CapabilityRegistry
# ══════════════════════════════════════════════════════════════════


class CapabilityRegistry:
    """Manages a collection of Capability objects.

    Supports:
    - register / unregister
    - lookup by domain
    - keyword-based best-match
    - execute with error handling
    - adaptive prompt building via PromptTemplateManager
    """

    def __init__(self, template_manager: Optional[PromptTemplateManager] = None) -> None:
        self._capabilities: dict[str, Capability] = {}
        self._template_manager: Optional[PromptTemplateManager] = template_manager

    @property
    def template_manager(self) -> Optional[PromptTemplateManager]:
        return self._template_manager

    def set_template_manager(self, mgr: PromptTemplateManager) -> None:
        """Inject a PromptTemplateManager for adaptive prompt building."""
        self._template_manager = mgr
        logger.info("[CapRegistry] PromptTemplateManager injected")

    def build_template_prompt(self, capability: str, user_query: str, context: Optional[dict] = None) -> Optional[str]:
        """Build an LLM prompt using the adaptive template for a capability."""
        if self._template_manager is None:
            if _template_manager is not None:
                return _template_manager.build_prompt(capability, user_query, context)
            return None
        return self._template_manager.build_prompt(capability, user_query, context)

    # ── Registration ───────────────────────────────────────────────

    def register(self, cap: Capability) -> None:
        """Register a capability. Overwrites existing capability with same name."""
        if not isinstance(cap, Capability):
            raise TypeError(f"Expected Capability, got {type(cap).__name__}")
        self._capabilities[cap.name] = cap
        logger.debug("[CapRegistry] Registered '%s' (domains=%s)", cap.name, cap.domains)

    def unregister(self, name: str) -> bool:
        """Remove a capability by name. Returns True if it existed."""
        existed = name in self._capabilities
        if existed:
            del self._capabilities[name]
            logger.debug("[CapRegistry] Unregistered '%s'", name)
        return existed

    # ── Lookup ─────────────────────────────────────────────────────

    def get(self, domain: str) -> list[Capability]:
        """Return all capabilities that match a given domain."""
        domain_lower = domain.lower()
        return [c for c in self._capabilities.values() if domain_lower in (d.lower() for d in c.domains)]

    def get_by_name(self, name: str) -> Optional[Capability]:
        """Return a capability by exact name, or None."""
        return self._capabilities.get(name)

    def list_all(self) -> list[Capability]:
        """Return all registered capabilities."""
        return list(self._capabilities.values())

    def list_names(self) -> list[str]:
        """Return all registered capability names."""
        return list(self._capabilities.keys())

    @property
    def count(self) -> int:
        return len(self._capabilities)

    # ── Best-match ─────────────────────────────────────────────────

    # Keyword → capability mapping for find_best.  Lowercase keys.
    KEYWORD_MAP: dict[str, str] = {
        # datetime
        "时间": "datetime", "日期": "datetime", "星期": "datetime",
        "time": "datetime", "date": "datetime", "时区": "datetime",
        "timezone": "datetime", "today": "datetime", "now": "datetime",
        "今天": "datetime", "现在": "datetime", "几点": "datetime",

        # math
        "计算": "math", "算": "math", "数学": "math", "calc": "math",
        "math": "math", "算术": "math", "eval": "math",
        "+": "math", "-": "math", "*": "math", "/": "math",

        # random
        "随机": "random", "骰子": "random", "抽签": "random",
        "random": "random", "dice": "random", "掷": "random",
        "选一个": "random", "随机数": "random",

        # text
        "文本": "text", "字符串": "text", "统计": "text",
        "字数": "text", "反转": "text", "reverse": "text",
        "大小写": "text", "uppercase": "text", "lowercase": "text",
        "字符数": "text", "大写": "text", "小写": "text",

        # file_info
        "文件大小": "file_info", "文件信息": "file_info",
        "修改时间": "file_info", "行数": "file_info",
        "file size": "file_info", "file info": "file_info",
        "lines": "file_info",

        # hash
        "hash": "hash", "md5": "hash", "sha256": "hash", "sha1": "hash",
        "加密": "hash", "摘要": "hash", "校验": "hash",
        "哈希": "hash",

        # json_tool
        "json": "json_tool", "格式化": "json_tool",
        "json美化": "json_tool", "解析": "json_tool",
        "json格式": "json_tool",

        # uuid_gen
        "uuid": "uuid_gen", "唯一id": "uuid_gen", "guid": "uuid_gen",
        "生成id": "uuid_gen", "唯一标识": "uuid_gen",

        # web_search
        "搜索": "web_search", "查一下": "web_search", "网上": "web_search",
        "互联网": "web_search", "search": "web_search", "web": "web_search",
        "搜": "web_search", "查查看": "web_search",

        # weather
        "天气": "weather", "温度": "weather", "下雨": "weather",
        "降水": "weather", "风力": "weather", "湿度": "weather",
        "weather": "weather", "temperature": "weather", "rain": "weather",
        "晴天": "weather", "多云": "weather", "阴天": "weather",
        "下雪": "weather", "snow": "weather", "气温": "weather",

        # news
        "新闻": "news", "资讯": "news", "头条": "news",
        "快讯": "news", "news": "news", "headlines": "news",
        "动态": "news", "热点": "news",

        # web_fetch
        "抓取": "web_fetch", "网页": "web_fetch", "链接": "web_fetch",
        "http": "web_fetch", "网站": "web_fetch", "fetch": "web_fetch",
        "爬": "web_fetch", "提取网页": "web_fetch",
    }

    # If prompt matches any of these negative keywords for a capability,
    # skip that capability even if a positive keyword matched.
    # This prevents e.g. "今天天气" from matching datetime.
    NEGATIVE_KEYWORDS: dict[str, list[str]] = {
        "datetime": ["天气", "weather", "气温", "下雨", "下雪", "多云", "晴天", "阴天", "温度", "降水", "风力", "湿度"],
    }

    def find_best(self, prompt: str, domain: str) -> Optional[Capability]:
        """Find the best-matching capability for a prompt and domain.

        Priority: keyword match within domain → keyword match globally → None.
        Negative keywords override positive matches.
        """
        if not prompt or not self._capabilities:
            return None

        prompt_lower = prompt.lower()
        candidates: list[Capability] = []

        # Get capabilities that match the domain
        domain_caps = {c.name: c for c in self.get(domain)}

        # Scan keywords: domain-scoped first
        for keyword, cap_name in self.KEYWORD_MAP.items():
            if keyword.lower() in prompt_lower:
                # Negative keyword check
                negatives = self.NEGATIVE_KEYWORDS.get(cap_name, [])
                if any(n in prompt_lower for n in negatives):
                    continue
                cap = domain_caps.get(cap_name)
                if cap is not None:
                    candidates.append(cap)

        # If no domain match, try global keyword match
        if not candidates:
            for keyword, cap_name in self.KEYWORD_MAP.items():
                if keyword.lower() in prompt_lower:
                    negatives = self.NEGATIVE_KEYWORDS.get(cap_name, [])
                    if any(n in prompt_lower for n in negatives):
                        continue
                    cap = self._capabilities.get(cap_name)
                    if cap is not None:
                        candidates.append(cap)

        # Return first match (the KEYWORD_MAP is ordered by priority)
        return candidates[0] if candidates else None

    # ── Execute ────────────────────────────────────────────────────

    def execute(self, name: str, prompt: str, **kwargs: Any) -> dict:
        """Execute a capability by name.

        Args:
            name: Capability name.
            prompt: Task prompt (passed to handler).
            **kwargs: Extra args passed to handler.

        Returns:
            {"result": str, "data": dict} on success.

        Raises:
            KeyError: Capability not found.
            RuntimeError: Handler execution failed.
        """
        cap = self._capabilities.get(name)
        if cap is None:
            raise KeyError(f"Capability '{name}' not registered")

        try:
            return cap.handler(prompt, **kwargs)
        except Exception as exc:
            raise RuntimeError(f"Capability '{name}' execution failed: {exc}") from exc


# ══════════════════════════════════════════════════════════════════
# Built-in capability handlers
# ══════════════════════════════════════════════════════════════════


def _handle_datetime(prompt: str, **kwargs: Any) -> dict:
    """Return current date/time information."""
    now = dt.datetime.now()
    tz_name = now.astimezone().tzname() or "local"

    data = {
        "iso": now.isoformat(),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": tz_name,
        "unix_timestamp": int(now.timestamp()),
        "weekday": now.strftime("%A"),
        "weekday_cn": ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][now.weekday()],
        "year": now.year,
        "month": now.month,
        "day": now.day,
        "hour": now.hour,
        "minute": now.minute,
        "second": now.second,
    }

    result = (
        f"当前时间：{data['date']} {data['time']} ({data['weekday_cn']})\n"
        f"时区：{data['timezone']}  |  Unix 时间戳：{data['unix_timestamp']}"
    )
    return {"result": result, "data": data}


# Safe math operations whitelist
_SAFE_OPS: dict[type, Callable] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
}


def _safe_eval_math(expr_str: str) -> float:
    """Safely evaluate a simple arithmetic expression using AST.

    Only allows literal numbers and + - * / ** % operators.
    """
    tree = ast.parse(expr_str.strip(), mode="eval")

    def _eval(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError(f"Unsupported constant type: {type(node.value).__name__}")
        if isinstance(node, ast.BinOp):
            op_type = type(node.op)
            if op_type not in _SAFE_OPS:
                raise ValueError(f"Unsupported operator: {op_type.__name__}")
            left = _eval(node.left)
            right = _eval(node.right)
            return _SAFE_OPS[op_type](left, right)
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.USub):
                return -_eval(node.operand)
            if isinstance(node.op, ast.UAdd):
                return +_eval(node.operand)
            raise ValueError(f"Unsupported unary operator: {type(node.op).__name__}")
        raise ValueError(f"Unsupported expression: {type(node).__name__}")

    return _eval(tree)


def _extract_math_expression(prompt: str) -> str:
    """Extract a math expression from a prompt string."""
    import re

    # Strategy: strip instruction prefixes, then take the rest as the expression.
    # Math expressions end at Chinese/English sentence terminators.
    cleaned = re.sub(
        r'(?:计算|算一下|帮我算|帮我计算|求|compute|calc|等于多少|等于|是多少)\s*[:：]?\s*',
        '',
        prompt,
        count=1,
        flags=re.IGNORECASE,
    ).strip()

    if not cleaned:
        return ""

    # Find the first math-like token sequence
    # e.g., "3 + 5" or "(2 + 3) * 4 - 10" or "50 * 2"
    # Stop at sentence-ending punctuation or non-math words
    match = re.match(
        r'([\d\s()（）+\-*/%×÷^.]+)',
        cleaned,
    )
    if match:
        expr = match.group(1).strip()
        # Verify it has at least one digit and one operator
        if expr and re.search(r'\d', expr) and re.search(r'[+\-*/%^]', expr):
            return expr

    # Fallback: scan anywhere in the prompt
    fallback = re.search(
        r'(?:^|\s)([\d\s()（）+\-*/%×÷^.]+)(?:\s|$|[，。,。！!？?])',
        prompt,
    )
    if fallback:
        expr = fallback.group(1).strip()
        if expr and re.search(r'\d', expr) and re.search(r'[+\-*/%^]', expr):
            return expr

    return ""


def _handle_math(prompt: str, **kwargs: Any) -> dict:
    """Safely evaluate a mathematical expression from the prompt."""
    expr = _extract_math_expression(prompt)

    if not expr:
        return {"result": "无法从提示词中提取数学表达式", "data": {"expression": "", "value": None}}

    try:
        value = _safe_eval_math(expr)
        # Format result cleanly
        if isinstance(value, float) and value == int(value):
            display = str(int(value))
        else:
            display = f"{value:.10g}"

        return {
            "result": f"计算结果：{expr} = {display}",
            "data": {"expression": expr, "value": value, "display": display},
        }
    except Exception as e:
        return {
            "result": f"计算失败：{e}",
            "data": {"expression": expr, "value": None, "error": str(e)},
        }


def _handle_random(prompt: str, **kwargs: Any) -> dict:
    """Generate random numbers, dice rolls, or random selection."""
    import re

    # Check for dice roll: "掷骰子"、"roll 2d6"、"一个1-100的随机数"
    dice_match = re.search(r'(\d+)?[dD](\d+)', prompt)
    if dice_match:
        count = int(dice_match.group(1) or 1)
        sides = int(dice_match.group(2))
        rolls = [_random.randint(1, sides) for _ in range(min(count, 20))]
        total = sum(rolls)
        return {
            "result": f"掷骰子 ({count}d{sides})：{' + '.join(map(str, rolls))} = {total}",
            "data": {"rolls": rolls, "total": total, "count": count, "sides": sides, "type": "dice"},
        }

    # Check for range: "1到100的随机数"、"随机数 1-100"、"random number 1 to 100"
    range_match = re.search(r'(\d+)\s*[-−到至to]+\s*(\d+)\s*(?:的)?\s*随?机?(?:数|整数|整)?', prompt)
    if range_match:
        lo, hi = int(range_match.group(1)), int(range_match.group(2))
        if lo > hi:
            lo, hi = hi, lo
        value = _random.randint(lo, hi)
        return {
            "result": f"随机数 ({lo} - {hi})：{value}",
            "data": {"value": value, "min": lo, "max": hi, "type": "range"},
        }

    # Check for selection: "从A、B、C中选一个"、"random pick"
    pick_match = re.search(r'(?:从|from\s+)?(.+?)(?:中|里面)?\s*(?:选|抽|随机选|pick|choose|random\s+pick)', prompt)
    if pick_match:
        items_text = pick_match.group(1)
        # Split by common separators
        items = re.split(r'[,，、\s/|]', items_text)
        items = [i.strip() for i in items if i.strip()]
        if items:
            chosen = _random.choice(items)
            return {
                "result": f"随机选择：从 [{', '.join(items)}] 中选出 → {chosen}",
                "data": {"items": items, "chosen": chosen, "type": "pick"},
            }

    # Default: random float 0-1
    value = _random.random()
    return {
        "result": f"随机数 (0-1)：{value:.6f}",
        "data": {"value": value, "type": "float"},
    }


def _handle_text(prompt: str, **kwargs: Any) -> dict:
    """Perform text operations: count, reverse, case conversion, stats."""
    import re

    # Determine operation first
    prompt_lower = prompt.lower()
    operation: Optional[str] = None

    if any(kw in prompt_lower for kw in ["反转", "reverse"]):
        operation = "reverse"
    elif any(kw in prompt_lower for kw in ["大写", "uppercase", "转大写"]):
        operation = "uppercase"
    elif any(kw in prompt_lower for kw in ["小写", "lowercase", "转小写"]):
        operation = "lowercase"

    # Strip the instruction prefix to get the target text
    instruction_patterns = [
        r'(?:反转|reverse|大写|小写|uppercase|lowercase|转大写|转小写|统计字数|统计字符|字数|字符数)\s*[:：]?\s*',
    ]
    target_text = prompt
    for pat in instruction_patterns:
        target_text = re.sub(pat, '', target_text, count=1, flags=re.IGNORECASE).strip()

    if not target_text:
        return {
            "result": "无法提取目标文本",
            "data": {"operation": operation or "unknown", "error": "no_text"},
        }

    if any(kw in prompt_lower for kw in ["反转", "reverse"]):
        reversed_text = target_text[::-1]
        return {
            "result": f"反转结果：{reversed_text}",
            "data": {"operation": "reverse", "input": target_text, "output": reversed_text},
        }

    if any(kw in prompt_lower for kw in ["大写", "uppercase", "转大写"]):
        upper = target_text.upper()
        return {
            "result": f"大写转换：{upper}",
            "data": {"operation": "uppercase", "input": target_text, "output": upper},
        }

    if any(kw in prompt_lower for kw in ["小写", "lowercase", "转小写"]):
        lower = target_text.lower()
        return {
            "result": f"小写转换：{lower}",
            "data": {"operation": "lowercase", "input": target_text, "output": lower},
        }

    # Default: text statistics
    stats = {
        "length": len(target_text),
        "chars_no_space": len(target_text.replace(" ", "").replace("\n", "").replace("\t", "")),
        "words": len(target_text.split()),
        "lines": target_text.count("\n") + 1 if target_text else 0,
        "has_chinese": bool(re.search(r'[\u4e00-\u9fff]', target_text)),
        "has_english": bool(re.search(r'[a-zA-Z]', target_text)),
    }

    lines_parts = []
    lines_parts.append(f"文本统计：")
    lines_parts.append(f"  字符数：{stats['length']}（不含空格：{stats['chars_no_space']}）")
    lines_parts.append(f"  单词数：{stats['words']}")
    lines_parts.append(f"  行数：{stats['lines']}")
    if stats["has_chinese"]:
        lines_parts.append(f"  包含中文")
    if stats["has_english"]:
        lines_parts.append(f"  包含英文")

    return {
        "result": "\n".join(lines_parts),
        "data": {"operation": "stats", "stats": stats},
    }


def _handle_file_info(prompt: str, **kwargs: Any) -> dict:
    """Get file info: size, modification time, line count (if text)."""
    import re

    # Try to extract a file path from the prompt
    # Patterns: "查看 C:\path\to\file.txt"、"文件信息 D:/doc/report.pdf"
    path_match = re.search(
        r'([A-Za-z]:[\\/](?:[^<>:"|?*\r\n]+[\\/])*[^<>:"|?*\r\n]+\.\w+)',
        prompt,
    )
    file_path_str = kwargs.get("file_path", "")

    if path_match:
        file_path_str = path_match.group(1)
    elif not file_path_str:
        return {
            "result": "未找到文件路径，请在提示词中提供文件路径或通过 file_path 参数传入",
            "data": {"error": "no_file_path"},
        }

    p = Path(file_path_str)

    if not p.exists():
        return {
            "result": f"文件不存在：{file_path_str}",
            "data": {"path": file_path_str, "exists": False},
        }

    stat = p.stat()
    size_bytes = stat.st_size
    mtime = dt.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    ctime = dt.datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M:%S")

    # Human-readable size
    if size_bytes < 1024:
        size_str = f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        size_str = f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        size_str = f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        size_str = f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"

    # Line count for text files
    line_count = None
    text_exts = {".txt", ".py", ".md", ".json", ".yaml", ".yml", ".csv", ".log", ".sh", ".js", ".ts", ".html", ".css", ".xml", ".toml", ".ini", ".cfg"}
    if p.suffix.lower() in text_exts:
        try:
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                line_count = sum(1 for _ in f)
        except Exception:
            pass

    data: dict[str, Any] = {
        "path": file_path_str,
        "exists": True,
        "name": p.name,
        "suffix": p.suffix,
        "size_bytes": size_bytes,
        "size_human": size_str,
        "modified": mtime,
        "created": ctime,
        "is_file": p.is_file(),
        "is_dir": p.is_dir(),
    }

    lines = [
        f"文件信息：{p.name}",
        f"  路径：{file_path_str}",
        f"  大小：{size_str} ({size_bytes:,} 字节)",
        f"  修改时间：{mtime}",
        f"  创建时间：{ctime}",
    ]
    if line_count is not None:
        lines.append(f"  行数：{line_count}")
        data["line_count"] = line_count

    return {"result": "\n".join(lines), "data": data}


def _handle_hash(prompt: str, **kwargs: Any) -> dict:
    """Compute MD5 / SHA1 / SHA256 hash of a string."""
    import re

    # Determine algorithm from prompt
    prompt_lower = prompt.lower()
    if any(kw in prompt_lower for kw in ["sha256", "sha-256"]):
        algo = "sha256"
    elif any(kw in prompt_lower for kw in ["sha1", "sha-1"]):
        algo = "sha1"
    else:
        algo = "md5"

    # Try to extract text: after the instruction keywords
    text_match = re.search(
        r'(?:hash|md5|sha256|sha1|sha-256|sha-1|加密|摘要|校验|哈希)\s*[:：]?\s*(.+)',
        prompt, re.IGNORECASE,
    )
    target_text = text_match.group(1).strip() if text_match else prompt.strip()

    if not target_text:
        return {"result": "无法提取哈希目标文本", "data": {"error": "no_text", "algorithm": algo}}

    h = hashlib.new(algo)
    h.update(target_text.encode("utf-8"))
    digest = h.hexdigest()

    algo_label = {"md5": "MD5", "sha1": "SHA1", "sha256": "SHA256"}[algo]

    return {
        "result": f"{algo_label} 哈希：\n  输入：{target_text}\n  {algo.upper()}：{digest}",
        "data": {"algorithm": algo, "input": target_text, "digest": digest},
    }


def _handle_json_tool(prompt: str, **kwargs: Any) -> dict:
    """JSON formatting / validation / compression."""
    import re

    # Try to extract JSON from prompt: look for {...} or [...]
    json_match = re.search(r'(\{.*\}|\[.*\])', prompt, re.DOTALL)
    if not json_match and "json" in kwargs:
        json_str = kwargs["json"]
    elif json_match:
        json_str = json_match.group(1)
    else:
        return {"result": "未找到 JSON 数据", "data": {"error": "no_json"}}

    prompt_lower = prompt.lower()

    # Determine operation
    if any(kw in prompt_lower for kw in ["压缩", "compress", "minify", "紧凑"]):
        try:
            obj = _json.loads(json_str)
            compressed = _json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
            return {
                "result": f"JSON 压缩结果：\n{compressed}",
                "data": {"mode": "compress", "output": compressed, "input": json_str, "valid": True},
            }
        except _json.JSONDecodeError as e:
            return {
                "result": f"JSON 解析失败：{e}",
                "data": {"mode": "compress", "error": str(e), "valid": False},
            }
    else:
        # Default: format / validate
        try:
            obj = _json.loads(json_str)
            pretty = _json.dumps(obj, ensure_ascii=False, indent=2)
            return {
                "result": f"JSON 格式化结果（合法）：\n{pretty}",
                "data": {"mode": "format", "output": pretty, "input": json_str, "valid": True},
            }
        except _json.JSONDecodeError as e:
            return {
                "result": f"JSON 格式无效：{e}",
                "data": {"mode": "format", "error": str(e), "valid": False},
            }


def _handle_uuid_gen(prompt: str, **kwargs: Any) -> dict:
    """Generate a UUID4."""
    uid = str(_uuid.uuid4())
    return {
        "result": f"UUID4：{uid}",
        "data": {"uuid": uid, "version": 4},
    }


def _web_search_handler(prompt: str, **kwargs: Any) -> dict:
    """Search the web using DuckDuckGo Instant Answer API (no API key required)."""
    if not prompt or not prompt.strip():
        return {
            "result": "未提供搜索查询词",
            "data": {"abstract": "", "url": "", "topics": []},
        }

    # Extract query: take everything before the first ? or 。as the search query
    query = prompt.strip()
    for sep in ("？", "?", "。", "."):
        idx = query.find(sep)
        if idx > 0:
            query = query[:idx]
            break

    encoded_query = urllib.parse.quote(query)
    url = f"https://api.duckduckgo.com/?q={encoded_query}&format=json&no_html=1&skip_disambig=1"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "EmperorCore/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
        logger.warning("web_search failed for query=%r: %s", query, exc)
        return {
            "result": f"搜索失败: {exc}",
            "data": {"abstract": "", "url": "", "topics": []},
        }

    abstract = data.get("Abstract", "") or ""
    abstract_url = data.get("AbstractURL", "") or ""
    related = data.get("RelatedTopics", []) or []
    topics: list[str] = []
    for item in related[:5]:
        if isinstance(item, dict) and item.get("Text"):
            topics.append(item["Text"])
        elif isinstance(item, str):
            topics.append(item)

    lines: list[str] = []
    if abstract:
        lines.append(f"摘要: {abstract}")
        if abstract_url:
            lines.append(f"来源: {abstract_url}")
    else:
        lines.append("未找到相关摘要")

    if topics:
        lines.append("\n相关话题:")
        for i, t in enumerate(topics, 1):
            lines.append(f"  {i}. {t}")

    return {
        "result": "\n".join(lines),
        "data": {"abstract": abstract, "url": abstract_url, "topics": topics},
    }


def _web_fetch_handler(prompt: str, **kwargs: Any) -> dict:
    """Fetch and extract text content from a URL mentioned in the prompt."""
    # Find URL in prompt
    import re

    url_match = re.search(r"https?://\S+", prompt)
    if not url_match:
        return {
            "result": "未在任务描述中找到 URL，请提供有效的 http/https 链接",
            "data": {},
        }

    url = url_match.group(0).rstrip(".,;:!?\"'）)】』」》")
    logger.info("web_fetch: fetching %s", url)

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "EmperorCore/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            content_type = resp.headers.get("Content-Type", "")
            raw = resp.read()

            # Try to decode
            charset = "utf-8"
            if "charset=" in content_type:
                charset = content_type.split("charset=")[-1].split(";")[0].strip().lower()
                if charset not in ("utf-8", "utf8", "latin-1", "iso-8859-1", "windows-1252"):
                    charset = "utf-8"

            html_text = raw.decode(charset, errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
        logger.warning("web_fetch failed for %s: %s", url, exc)
        return {"result": f"网页抓取失败: {exc}", "data": {}}

    # Strip script and style content, extract text
    text = _html_to_text(html_text)
    if len(text) > 2000:
        text = text[:2000] + "...\n\n[内容已截断至 2000 字符]"

    return {
        "result": text,
        "data": {"url": url, "length": len(text), "truncated": len(text) >= 2000},
    }


class _HTMLTextExtractor(HTMLParser):
    """Simple HTML parser that extracts visible text, skipping scripts/styles."""

    def __init__(self) -> None:
        super().__init__()
        self.text_parts: list[str] = []
        self._skip = False
        self._skip_tags = {"script", "style", "noscript", "head", "meta", "link", "title"}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._skip_tags:
            self._skip = True

    def handle_endtag(self, tag: str) -> None:
        if tag in self._skip_tags:
            self._skip = False
        # Add newline after block-level elements
        if tag in ("div", "p", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr"):
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = data.strip()
        if text:
            self.text_parts.append(text)
            self.text_parts.append(" ")


def _html_to_text(html: str) -> str:
    """Extract plain text from HTML, removing scripts and styles."""
    parser = _HTMLTextExtractor()
    try:
        parser.feed(html)
    except Exception:
        pass
    raw = "".join(parser.text_parts)
    # Collapse whitespace
    import re

    raw = re.sub(r"[ \t]+", " ", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()


def _weather_handler(prompt: str, **kwargs: Any) -> dict:
    """Query weather using wttr.in (free, no API key)."""
    city = _extract_city_from_prompt(prompt)
    if not city:
        city = "Beijing"

    encoded = urllib.parse.quote(city)
    url = f"https://wttr.in/{encoded}?format=j1"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "EmperorCore/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
        logger.warning("weather query failed for city=%r: %s", city, exc)
        return {"result": f"天气查询失败: {exc}", "data": {"error": str(exc), "source": "wttr.in"}}

    current = data.get("current_condition", [{}])[0]
    weather_desc = data.get("weather", [{}])[0].get("hourly", [{}])

    result_lines = [f"📍 {city}"]

    if current:
        temp_c = current.get("temp_C", "N/A")
        humidity = current.get("humidity", "N/A")
        feels_like = current.get("FeelsLikeC", "N/A")
        wind_speed = current.get("windspeedKmph", "N/A")
        wind_dir = current.get("winddir16Point", "N/A")
        weather_desc_short = (current.get("weatherDesc", [{}])[0].get("value", "N/A")
                              if isinstance(current.get("weatherDesc"), list) and current["weatherDesc"]
                              else "N/A")
        visibility = current.get("visibility", "N/A")
        uv_index = current.get("uvIndex", "N/A")

        result_lines.append(f"🌡 当前温度: {temp_c}°C (体感 {feels_like}°C)")
        result_lines.append(f"☁ 天气: {weather_desc_short}")
        result_lines.append(f"💧 湿度: {humidity}%")
        result_lines.append(f"🌬 风速: {wind_speed} km/h ({wind_dir})")
        if visibility != "N/A":
            result_lines.append(f"👁 能见度: {visibility} km")
        if uv_index != "N/A":
            result_lines.append(f"☀ UV 指数: {uv_index}")

    today = weather_desc[0] if weather_desc else {}
    if today:
        precip = today.get("chanceofrain", "N/A")
        result_lines.append(f"🌧 降水概率: {precip}%")

    result_text = "\n".join(result_lines)

    return {"result": result_text, "data": {
        "city": city,
        "temp_c": temp_c,
        "feels_like_c": feels_like,
        "humidity": humidity,
        "weather_desc": weather_desc_short,
        "wind_speed_kmph": wind_speed,
        "source": "wttr.in",
    }}


def _extract_city_from_prompt(prompt: str) -> str:
    """Extract city name from a natural-language prompt.

    Supports Chinese and English patterns; defaults to 'Beijing' if no city detected.
    """
    import re

    # Strip common Chinese query prefixes first
    cleaned = re.sub(
        r'^(?:查询|查一下|查\s*|搜索|搜|看看?|问一下|请问|帮我\s*|帮我查|请帮我|请查)\s*',
        '', prompt,
    )

    patterns = [
        r'([\u4e00-\u9fa5]{2,6}?)(?:的)?天气',
        r'([\u4e00-\u9fa5]{2,6}?)(?:的)?温度',
        r'weather\s+(?:in|of|for)\s+(\w+)',
        r'(\w+)\s+weather',
    ]
    for pat in patterns:
        match = re.search(pat, cleaned, re.IGNORECASE)
        if match:
            return match.group(1).strip()

    # Also try patterns on original prompt for top-level commands
    pat2 = r'查(?:一下|下)?([\u4e00-\u9fa5]{2,6})'
    m2 = re.search(pat2, prompt)
    if m2:
        return m2.group(1).strip()

    return "Beijing"


# ── News handler ────────────────────────────────────────────────────


def _extract_topic_from_prompt(prompt: str) -> str:
    """Extract news topic from prompt. Defaults to 'technology'."""
    import re

    # Strip common Chinese query prefixes first
    cleaned = re.sub(
        r'^(?:查询|查一下|查\s*|搜索|搜|看看?|问一下|请问|帮我\s*|帮我查|请帮我|请查|最新|今天的?|最近|有什么|有什么?)\s*',
        '', prompt,
    )

    patterns = [
        r'([\u4e00-\u9fa5a-zA-Z]+?)(?:的)?(?:新闻|资讯|消息|动态)',
        r'news\s+(?:about|on|for)\s+(\w+)',
    ]
    for pat in patterns:
        match = re.search(pat, cleaned, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return "technology"


def _parse_rss_items(rss_text: str) -> list:
    """Parse RSS XML to extract title + source from <item> blocks."""
    import re
    items = []
    item_pattern = re.compile(r'<item>(.*?)</item>', re.DOTALL)
    for match in item_pattern.finditer(rss_text):
        block = match.group(1)
        title_match = re.search(r'<title>(.*?)</title>', block, re.DOTALL)
        source_match = re.search(r'<source[^>]*>(.*?)</source>', block)
        if title_match:
            title = title_match.group(1).strip()
            title = title.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"')
            source = source_match.group(1).strip() if source_match else "Unknown"
            items.append({"title": title, "source": source})
    return items


def _news_handler(prompt: str, **kwargs: Any) -> dict:
    """Query latest news using Google News RSS (free, no API key)."""
    topic = _extract_topic_from_prompt(prompt) or "technology"
    encoded = urllib.parse.quote(topic)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "EmperorCore/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            rss_data = resp.read().decode()
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
        logger.warning("news query failed for topic=%r: %s", topic, exc)
        return {"result": f"新闻查询失败: {exc}", "data": {"error": str(exc), "topic": topic, "source": "Google News RSS"}}

    items = _parse_rss_items(rss_data)

    lines = [f"\U0001f4f0 {topic.title()} News (Google News)"]
    for i, item in enumerate(items[:5], 1):
        title = item.get("title", "Untitled")[:80]
        source = item.get("source", "Unknown")
        lines.append(f"{i}. {title} \u2014 {source}")

    return {"result": "\n".join(lines), "data": {
        "topic": topic,
        "count": min(len(items), 5),
        "articles": items[:5],
        "source": "Google News RSS",
    }}


# ══════════════════════════════════════════════════════════════════
# Factory: create a registry with all built-in capabilities
# ══════════════════════════════════════════════════════════════════


def create_default_registry(enabled: Optional[list[str]] = None) -> CapabilityRegistry:
    """Create a CapabilityRegistry pre-loaded with 10 built-in capabilities.

    Args:
        enabled: If provided, only register capabilities whose name is in this list.
    """
    registry = CapabilityRegistry()

    def _reg(name: str, desc: str, domains: list[str], handler) -> None:
        """Register a capability only if enabled list allows it."""
        if enabled is not None and name not in enabled:
            return
        registry.register(Capability(name=name, description=desc, domains=domains, handler=handler))

    _reg("datetime", "获取当前日期、时间、时区、星期等信息", ["general", "data"], _handle_datetime)
    _reg("math", "安全计算数学表达式（加减乘除、幂、取模）", ["math", "science"], _handle_math)
    _reg("random", "生成随机数、掷骰子、随机选择", ["general"], _handle_random)
    _reg("text", "文本统计、反转、大小写转换、计数", ["general", "code", "legal"], _handle_text)
    _reg("file_info", "获取文件大小、修改时间、行数等信息", ["data", "code"], _handle_file_info)
    _reg("hash", "计算字符串的 MD5 / SHA1 / SHA256 哈希摘要", ["code", "data"], _handle_hash)
    _reg("json_tool", "JSON 格式化美化 / 校验 / 压缩", ["code", "data"], _handle_json_tool)
    _reg("uuid_gen", "生成 UUID4 唯一标识符", ["code", "general"], _handle_uuid_gen)
    _reg("weather", "查询城市天气（温度/湿度/风力/降水概率）", ["network", "general"], _weather_handler)
    _reg("news", "查询最新新闻资讯，支持中英文关键词，返回标题和来源", ["network", "general"], _news_handler)
    _reg("web_search", "搜索互联网信息（通过 DuckDuckGo）", ["general", "data"], _web_search_handler)
    _reg("web_fetch", "抓取指定网页的内容", ["general", "data", "code"], _web_fetch_handler)

    return registry

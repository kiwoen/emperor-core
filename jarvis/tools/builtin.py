"""
JARVIS Built-in Tools — twelve foundational capabilities wrapped with @tool.

Each tool is decorated with ``@tool(auto_register=True)`` so they are
automatically added to the global :class:`ToolRegistry` on import.

Tools:
    datetime, math, random, text, file_info, hash, json_tool,
    uuid_gen, weather, news, web_search, web_fetch
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math as _math
import os
import random as _random
import uuid
from pathlib import Path
from typing import Any

from jarvis.tools.base import ToolResult, tool


# ────────────────────────────────────────────────────────────────────
# 1. datetime — current date and time
# ────────────────────────────────────────────────────────────────────

@tool(category="utility", auto_register=True)
def datetime(format: str = "%Y-%m-%d %H:%M:%S") -> ToolResult:
    """Get the current date and time. Supports custom strftime format."""
    return _dt.datetime.now().strftime(format)


# ────────────────────────────────────────────────────────────────────
# 2. math — evaluate mathematical expressions
# ────────────────────────────────────────────────────────────────────

@tool(category="utility", auto_register=True)
def math(expression: str) -> ToolResult:
    """Evaluate a mathematical expression safely using Python math functions.

    Supports: +, -, *, /, **, abs, round, sqrt, sin, cos, tan, log, log10, ceil, floor, pi, e.
    Example: "sqrt(16) + cos(pi)"
    """
    allowed = {
        "abs": abs, "round": round, "sqrt": _math.sqrt,
        "sin": _math.sin, "cos": _math.cos, "tan": _math.tan,
        "log": _math.log, "log10": _math.log10,
        "ceil": _math.ceil, "floor": _math.floor,
        "pi": _math.pi, "e": _math.e,
    }
    try:
        result = eval(expression, {"__builtins__": {}}, allowed)
        return str(result)
    except Exception as exc:
        raise ValueError(f"Math evaluation failed: {exc}")


# ────────────────────────────────────────────────────────────────────
# 3. random — generate random numbers
# ────────────────────────────────────────────────────────────────────

@tool(category="utility", auto_register=True)
def random(min_val: float = 0.0, max_val: float = 1.0, as_int: bool = False) -> ToolResult:
    """Generate a random number between min_val and max_val (inclusive for int)."""
    if as_int:
        return _random.randint(int(min_val), int(max_val))
    return _random.uniform(min_val, max_val)


# ────────────────────────────────────────────────────────────────────
# 4. text — text manipulation utilities
# ────────────────────────────────────────────────────────────────────

@tool(category="utility", auto_register=True)
def text(operation: str, content: str) -> ToolResult:
    """Perform text manipulation operations on the given content.

    Supported operations:
        upper — convert to uppercase
        lower — convert to lowercase
        title — convert to title case
        reverse — reverse the string
        word_count — count words
        char_count — count characters (excluding spaces)
    """
    ops = {
        "upper": lambda s: s.upper(),
        "lower": lambda s: s.lower(),
        "title": lambda s: s.title(),
        "reverse": lambda s: s[::-1],
        "word_count": lambda s: str(len(s.split())),
        "char_count": lambda s: str(len(s.replace(" ", ""))),
    }
    op_key = operation.lower().strip()
    if op_key not in ops:
        available = ", ".join(sorted(ops.keys()))
        raise ValueError(f"Unknown operation '{operation}'. Available: {available}")
    return ops[op_key](content)


# ────────────────────────────────────────────────────────────────────
# 5. file_info — get file metadata
# ────────────────────────────────────────────────────────────────────

@tool(category="file", auto_register=True)
def file_info(file_path: str) -> ToolResult:
    """Get metadata for a file: size, modification time, type, and existence."""
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    stat = p.stat()
    return {
        "exists": True,
        "path": str(p.absolute()),
        "name": p.name,
        "suffix": p.suffix,
        "size_bytes": stat.st_size,
        "size_human": _human_size(stat.st_size),
        "modified": _dt.datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "created": _dt.datetime.fromtimestamp(stat.st_ctime).isoformat(),
        "is_file": p.is_file(),
        "is_dir": p.is_dir(),
    }


# ────────────────────────────────────────────────────────────────────
# 6. hash — compute file or string hashes
# ────────────────────────────────────────────────────────────────────

@tool(category="utility", auto_register=True)
def hash(content: str, algorithm: str = "sha256", is_file: bool = False) -> ToolResult:
    """Compute cryptographic hash of a string or file.

    Supported algorithms: md5, sha1, sha256, sha512.
    Set is_file=True to hash the file at path *content*.
    """
    algos = {
        "md5": hashlib.md5,
        "sha1": hashlib.sha1,
        "sha256": hashlib.sha256,
        "sha512": hashlib.sha512,
    }
    algo_key = algorithm.lower()
    if algo_key not in algos:
        available = ", ".join(sorted(algos.keys()))
        raise ValueError(f"Unknown algorithm '{algorithm}'. Available: {available}")

    h = algos[algo_key]()
    if is_file:
        p = Path(content)
        if not p.exists():
            raise FileNotFoundError(f"File not found: {content}")
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
    else:
        h.update(content.encode("utf-8"))
    return h.hexdigest()


# ────────────────────────────────────────────────────────────────────
# 7. json_tool — parse / format JSON
# ────────────────────────────────────────────────────────────────────

@tool(category="utility", auto_register=True)
def json_tool(operation: str, data: str, indent: int = 2) -> ToolResult:
    """Parse or format JSON data.

    Operations:
        parse  — validate and pretty-print a JSON string
        format — compact then pretty-print (normalise whitespace)
    """
    op = operation.lower().strip()
    if op not in ("parse", "format"):
        raise ValueError(f"Unknown operation '{operation}'. Use 'parse' or 'format'.")

    parsed = json.loads(data)
    return json.dumps(parsed, ensure_ascii=False, indent=indent)


# ────────────────────────────────────────────────────────────────────
# 8. uuid_gen — generate UUIDs
# ────────────────────────────────────────────────────────────────────

@tool(category="utility", auto_register=True)
def uuid_gen(version: int = 4) -> ToolResult:
    """Generate a UUID. Supported versions: 1 (time-based), 4 (random)."""
    if version == 1:
        return str(uuid.uuid1())
    elif version == 4:
        return str(uuid.uuid4())
    else:
        raise ValueError(f"Unsupported UUID version {version}. Use 1 or 4.")


# ────────────────────────────────────────────────────────────────────
# 9. weather — get weather information (simulated)
# ────────────────────────────────────────────────────────────────────

@tool(category="network", auto_register=True)
def weather(city: str, units: str = "metric") -> ToolResult:
    """Get current weather for a city (simulated/stub). Units: metric or imperial."""
    _stub_db = {
        "beijing": {"temp": 28, "condition": "Sunny", "humidity": 45},
        "shanghai": {"temp": 31, "condition": "Partly Cloudy", "humidity": 65},
        "tokyo": {"temp": 26, "condition": "Rain", "humidity": 80},
        "london": {"temp": 18, "condition": "Cloudy", "humidity": 70},
        "new york": {"temp": 25, "condition": "Clear", "humidity": 50},
        "paris": {"temp": 22, "condition": "Sunny", "humidity": 55},
        "sydney": {"temp": 16, "condition": "Windy", "humidity": 60},
        "moscow": {"temp": 10, "condition": "Snow", "humidity": 75},
    }
    key = city.lower().strip()
    data = _stub_db.get(key)
    if data is None:
        raise ValueError(
            f"Weather data not available for '{city}'. "
            f"Available: {', '.join(sorted(_stub_db.keys())).title()}"
        )
    temp = data["temp"]
    if units == "imperial":
        temp = round(temp * 9 / 5 + 32, 1)
    return {
        "city": city,
        "temperature": temp,
        "units": "°F" if units == "imperial" else "°C",
        "condition": data["condition"],
        "humidity": data["humidity"],
    }


# ────────────────────────────────────────────────────────────────────
# 10. news — get news headlines (simulated)
# ────────────────────────────────────────────────────────────────────

@tool(category="network", auto_register=True)
def news(topic: str = "technology", limit: int = 5) -> ToolResult:
    """Get latest news headlines for a topic (simulated/stub)."""
    _stub_headlines = {
        "technology": [
            "AI Agents Revolutionize Enterprise Automation",
            "New Open-Source LLM Matches GPT-4 Performance",
            "Quantum Computing Breakthrough Announced",
            "Tech Giants Pledge $100B for AI Infrastructure",
            "Browser-Native AI Models Gain Traction",
            "Robotics Startup Secures $2B Funding Round",
            "EU Passes Comprehensive AI Regulation Framework",
        ],
        "science": [
            "James Webb Telescope Discovers New Exoplanet",
            "CRISPR Gene Therapy Approved for Clinical Use",
            "Climate Scientists Warn of Accelerating Ice Melt",
            "Mars Rover Finds Evidence of Ancient Water",
            "Fusion Energy Milestone Achieved in Lab",
        ],
        "business": [
            "Global Markets Rally on Economic Data",
            "Central Bank Holds Interest Rates Steady",
            "Electric Vehicle Sales Surge 40% Year-over-Year",
            "Supply Chain Disruptions Ease Across Industries",
        ],
    }
    headlines = _stub_headlines.get(topic.lower().strip(), [])
    if not headlines:
        available = ", ".join(sorted(_stub_headlines.keys()))
        raise ValueError(f"No news for topic '{topic}'. Available: {available}")

    limit = max(1, min(limit, len(headlines)))
    return [{"headline": h, "topic": topic, "index": i} for i, h in enumerate(headlines[:limit], 1)]


# ────────────────────────────────────────────────────────────────────
# 11. web_search — web search (simulated / stub)
# ────────────────────────────────────────────────────────────────────

@tool(category="network", auto_register=True)
def web_search(query: str, num_results: int = 5) -> ToolResult:
    """Search the web for information (simulated/stub). Returns title, snippet, url."""
    _stub_results = {
        "python": [
            {"title": "Python.org — Official Website", "snippet": "Python is a programming language that lets you work quickly and integrate systems more effectively.", "url": "https://www.python.org/"},
            {"title": "Python Documentation", "snippet": "Official Python documentation with tutorials, library references, and guides.", "url": "https://docs.python.org/3/"},
            {"title": "Python on GitHub", "snippet": "The Python programming language. Contribute to python/cpython development.", "url": "https://github.com/python/cpython"},
        ],
        "ai": [
            {"title": "What is Artificial Intelligence (AI)?", "snippet": "Artificial intelligence is the simulation of human intelligence processes by machines.", "url": "https://en.wikipedia.org/wiki/Artificial_intelligence"},
            {"title": "OpenAI", "snippet": "Creating safe AGI that benefits all of humanity.", "url": "https://openai.com/"},
            {"title": "Anthropic", "snippet": "Anthropic is an AI safety company working to build reliable, interpretable AI systems.", "url": "https://www.anthropic.com/"},
        ],
    }

    key = query.lower().strip()
    results = _stub_results.get(key, [
        {"title": f"Search results for '{query}'", "snippet": f"Information about {query}.", "url": f"https://example.com/search?q={query}"},
    ])

    num_results = max(1, min(num_results, len(results)))
    return results[:num_results]


# ────────────────────────────────────────────────────────────────────
# 12. web_fetch — fetch web page content (simulated / stub)
# ────────────────────────────────────────────────────────────────────

@tool(category="network", auto_register=True)
def web_fetch(url: str, extract_text: bool = True) -> ToolResult:
    """Fetch and extract content from a web page (simulated/stub)."""
    _stub_pages = {
        "https://www.python.org/": {
            "title": "Welcome to Python.org",
            "content": "Python is a programming language that lets you work quickly and integrate systems more effectively. Learn more about Python at the official website.",
        },
        "https://example.com/": {
            "title": "Example Domain",
            "content": "This domain is for use in illustrative examples in documents. You may use this domain in literature without prior coordination or asking for permission.",
        },
    }

    page = _stub_pages.get(url.lower().rstrip("/"))
    if page is None:
        return {
            "url": url,
            "title": f"Page at {url}",
            "content": f"[Simulated content for {url}]",
            "status": 200,
            "content_length": 100,
        }

    return {
        "url": url,
        "title": page["title"],
        "content": page["content"] if extract_text else "[HTML content]",
        "status": 200,
        "content_length": len(page["content"]),
    }


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────

def _human_size(size_bytes: int) -> str:
    """Convert bytes to a human-readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"

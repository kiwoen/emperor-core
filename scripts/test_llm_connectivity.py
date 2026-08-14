#!/usr/bin/env python3
"""
test_llm_connectivity.py — verify that emperor-core can reach a real
OpenAI-compatible model endpoint (e.g. ChatOpens free model).

Usage:
    # use OPENAI_* env vars (recommended)
    python scripts/test_llm_connectivity.py

    # or pass values inline
    python scripts/test_llm_connectivity.py \
        --base-url https://your-base-url/v1 \
        --api-key sk-xxxx \
        --model gpt-4o \
        --prompt "用一句话介绍你自己"

Exit code 0 = live call succeeded; 1 = failed / still in mock mode.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Allow running as a script from the repo root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis.core.llm import LLMEngine, LLMConfig  # noqa: E402


def _load_dotenv(path: Path = ROOT / ".env") -> None:
    """Best-effort .env loader (no external dependency)."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


def _mask(key: str) -> str:
    if not key:
        return "<empty>"
    return key[:4] + "…" + key[-4:] if len(key) > 8 else "****"


async def main() -> int:
    _load_dotenv()
    parser = argparse.ArgumentParser(description="Test LLM endpoint connectivity")
    parser.add_argument("--base-url", default=os.getenv("OPENAI_BASE_URL", ""))
    parser.add_argument("--api-key", default=os.getenv("OPENAI_API_KEY", ""))
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", "gpt-4o"))
    parser.add_argument("--provider", default=os.getenv("OPENAI_PROVIDER", "openai"))
    parser.add_argument("--prompt", default="Ping. Reply with the single word: pong")
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()

    overrides = {
        "provider": args.provider,
        "model": args.model,
        "base_url": args.base_url,
        "api_key": args.api_key,
        "request_timeout": args.timeout,
    }
    cfg = LLMConfig.from_env(**{k: v for k, v in overrides.items() if v})
    engine = LLMEngine(cfg)

    print("── LLM config ─────────────────────────────")
    print(f"  provider : {cfg.provider}")
    print(f"  model    : {cfg.model}")
    print(f"  base_url : {cfg.base_url or '(default api.openai.com)'}")
    print(f"  api_key  : {_mask(cfg.api_key)}")
    print(f"  mode     : {'LIVE' if not engine.mock_mode else 'MOCK (endpoint not configured)'}")
    print("────────────────────────────────────────────")

    if engine.mock_mode:
        print("\n⚠️  No OPENAI_BASE_URL / OPENAI_API_KEY configured → still in MOCK mode.")
        print("    Set them (see .env.example) to enable the real free model.")
        return 1

    print(f"\n→ sending prompt: {args.prompt!r}")
    try:
        reply = await engine.complete(args.prompt, system="You are a helpful assistant.")
    except Exception as e:  # pragma: no cover
        print(f"\n❌ call raised: {e}")
        return 1

    if engine.last_error:  # live call raised and engine fell back to mock
        print("\n❌ live call failed (engine fell back to mock):")
        print(f"   error: {engine.last_error}")
        print(f"   mock reply: {reply}")
        return 1

    print("\n✅ live call succeeded. Reply:")
    print("─" * 40)
    print(reply)
    print("─" * 40)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

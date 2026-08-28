"""data_prep.py — 把任意素材整理成标准训练语料（ShareGPT / Alpaca 格式）。

纯标准库 + pyyaml，CPU 可跑，无需 torch。

为什么需要它：
  你目前"暂无数据"。本脚本能在没有现成指令数据的情况下，把你的文档 / 笔记 /
  聊天导出先变成可用的「微调种子语料」（通过总结、主题问答、续写等模板从原文
  派生）。这是起步用的"种子"，等你积累真实指令数据（人工编写或蒸馏得到）后，
  直接丢进来即可（会自动识别 instruction / conversation 格式，原样转换）。

用法：
  python data_prep.py --input data/raw --output data/prepared --val-ratio 0.1
  python data_prep.py --input data/raw --output data/prepared --mode passthrough
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
from pathlib import Path
from typing import Iterable, Iterator

# --------------------------------------------------------------------------- #
# 1. 文本切分
# --------------------------------------------------------------------------- #


def split_chunks(text: str, max_chars: int = 800, overlap: int = 80) -> list[str]:
    """按段落优先切分；超长段落再硬切并保留少量重叠，返回文本块列表。"""
    text = text.strip()
    if not text:
        return []
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        if len(buf) + len(p) + 1 <= max_chars:
            buf = (buf + "\n" + p).strip()
        else:
            if buf:
                chunks.append(buf)
            if len(p) > max_chars:
                step = max(1, max_chars - overlap)
                for i in range(0, len(p), step):
                    chunks.append(p[i : i + max_chars])
                buf = ""
            else:
                buf = p
    if buf:
        chunks.append(buf)
    return chunks


# --------------------------------------------------------------------------- #
# 2. 极简中文关键词 / 抽取式摘要（用于构造种子问答，不依赖任何模型）
# --------------------------------------------------------------------------- #

_STOP = set(
    "的 了 是 在 和 与 及 也 都 就 而 等 这 那 我 你 他 她 它 们 有 个 中 上 下 为 对 从 把 被 "
    "让 给 但 因 所以 如果 因为 一个 一种 这个 那个 我们 你们 他们 可以 这样 那样 这些 那些 "
    "已经 可能 应该 通过 进行 由于 以及 或者 并且 对于 关于".split()
)


def keywords(text: str, topk: int = 5) -> list[str]:
    words = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    freq: dict[str, int] = {}
    for w in words:
        if w in _STOP:
            continue
        freq[w] = freq.get(w, 0) + 1
    return [w for w, _ in sorted(freq.items(), key=lambda x: -x[1])[:topk]]


def _extractive_summary(text: str, max_sent: int = 2) -> str:
    sents = [s.strip() for s in re.split(r"(?<=[。！？])", text) if s.strip()]
    return "".join(sents[:max_sent])


def _conv(human: str, gpt: str) -> dict:
    return {"conversations": [{"from": "human", "value": human}, {"from": "gpt", "value": gpt}]}


# --------------------------------------------------------------------------- #
# 3. 从原始文本派生「种子指令样本」
# --------------------------------------------------------------------------- #


def doc_to_samples(text: str, max_chars: int = 800) -> list[dict]:
    """把一个文档转成若干 ShareGPT 对话样本（起步用的种子语料）。"""
    samples: list[dict] = []
    for ch in split_chunks(text, max_chars=max_chars):
        kw = keywords(ch)
        kws = "、".join(kw[:3]) if kw else "相关内容"
        # 1) 总结要点
        samples.append(_conv(f"请用中文简洁地总结下面这段内容，抓住要点：\n{ch}", _extractive_summary(ch)))
        # 2) 主题问答
        samples.append(_conv(f"这段内容主要围绕「{kws}」讲了什么？请用自己的话说明。", ch))
        # 3) 续写（前半 -> 后半）
        head, tail = ch[: len(ch) // 2], ch[len(ch) // 2 :]
        if len(tail) > 20:
            samples.append(_conv(f"请接着下面这段话继续写：\n{head}", tail))
    return samples


# --------------------------------------------------------------------------- #
# 4. 显式指令 / 对话格式的转换（已有真实数据时走这里）
# --------------------------------------------------------------------------- #


def alpaca_to_sharegpt(obj: dict) -> dict | None:
    conv = []
    if obj.get("instruction"):
        human = obj["instruction"]
        if obj.get("input"):
            human = f"{human}\n{obj['input']}"
        conv.append({"from": "human", "value": human})
    if obj.get("output"):
        conv.append({"from": "gpt", "value": obj["output"]})
    return {"conversations": conv} if conv else None


def normalize_record(obj: dict) -> dict | None:
    """把单条记录规范成 ShareGPT；自动识别多种常见格式。"""
    if "conversations" in obj:
        # 已是 ShareGPT：统一 role 字段为 from/human|gpt
        conv = []
        for turn in obj["conversations"]:
            role = turn.get("from") or turn.get("role") or ""
            if role in ("human", "user"):
                role = "human"
            elif role in ("gpt", "assistant", "bot"):
                role = "gpt"
            else:
                continue
            val = turn.get("value") or turn.get("content") or ""
            if val:
                conv.append({"from": role, "value": val})
        return {"conversations": conv} if conv else None
    if "messages" in obj:
        return normalize_record({"conversations": obj["messages"]})
    if "instruction" in obj or "output" in obj:
        return alpaca_to_sharegpt(obj)
    return None


# --------------------------------------------------------------------------- #
# 5. 多格式输入加载
# --------------------------------------------------------------------------- #


def _read_jsonl(path: Path) -> Iterator[dict]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def _read_json_array(path: Path) -> Iterator[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        for item in data:
            yield item
    elif isinstance(data, dict):
        yield data


def iter_records(path: Path) -> Iterator[dict]:
    """按扩展名加载记录。.txt/.md 走文本派生；.json/.jsonl 走结构化转换。"""
    suffix = path.suffix.lower()
    if suffix in (".txt", ".md"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        yield from ({"__doc__": t} for t in doc_to_samples(text))
    elif suffix == ".jsonl":
        yield from _read_jsonl(path)
    elif suffix == ".json":
        yield from _read_json_array(path)


def collect_samples(input_dir: Path, mode: str = "auto") -> list[dict]:
    """扫描输入目录，返回规范化的 ShareGPT 样本列表。

    mode:
      auto        —— 文本派生 + 结构化自动识别
      passthrough —— 仅结构化（不派生种子数据，假设你已有指令数据）
      seedonly    —— 仅文本派生（把纯文档变成种子语料）
    """
    samples: list[dict] = []
    for path in sorted(input_dir.rglob("*")):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix in (".txt", ".md"):
            if mode == "passthrough":
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            samples.extend(doc_to_samples(text))
        elif suffix in (".json", ".jsonl"):
            for rec in iter_records(path):
                if "__doc__" in rec:  # 来自文本派生
                    samples.append(_conv(rec["__doc__"][:400], rec["__doc__"]))
                    continue
                if mode == "seedonly":
                    continue
                norm = normalize_record(rec)
                if norm:
                    samples.append(norm)
    return samples


# --------------------------------------------------------------------------- #
# 6. 写出训练 / 验证集
# --------------------------------------------------------------------------- #


def write_datasets(samples: list[dict], output_dir: Path, val_ratio: float = 0.1, seed: int = 42) -> tuple[int, int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    rng.shuffle(samples)
    n_val = int(len(samples) * val_ratio)
    val, train = samples[:n_val], samples[n_val:]
    _dump(output_dir / "train.jsonl", train)
    _dump(output_dir / "val.jsonl", val)
    return len(train), len(val)


def _dump(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------- #
# 7. CLI
# --------------------------------------------------------------------------- #


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="把素材整理成训练语料（ShareGPT 格式）")
    ap.add_argument("--input", required=True, help="原始素材目录（.txt/.md/.json/.jsonl）")
    ap.add_argument("--output", required=True, help="输出目录，生成 train.jsonl / val.jsonl")
    ap.add_argument("--val-ratio", type=float, default=0.1, help="验证集比例（0~1）")
    ap.add_argument(
        "--mode",
        choices=["auto", "passthrough", "seedonly"],
        default="auto",
        help="auto=文本派生+结构化自动识别；passthrough=仅结构化；seedonly=仅文本派生",
    )
    ap.add_argument("--seed", type=int, default=42, help="打乱随机种子")
    args = ap.parse_args(list(argv) if argv is not None else None)

    input_dir = Path(args.input)
    if not input_dir.is_dir():
        print(f"[data_prep] 输入目录不存在: {input_dir}", file=__import__("sys").stderr)
        return 2

    samples = collect_samples(input_dir, mode=args.mode)
    if not samples:
        print("[data_prep] 未产出任何样本，请检查输入目录内容。", file=__import__("sys").stderr)
        return 1

    n_train, n_val = write_datasets(samples, Path(args.output), val_ratio=args.val_ratio, seed=args.seed)
    print(f"[data_prep] 完成：train={n_train}  val={n_val}  ->  {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

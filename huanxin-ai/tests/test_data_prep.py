"""tests/test_data_prep.py — 验证数据准备脚本（CPU，纯标准库，无需 torch）。

覆盖：
  - 文本文档派生种子语料（seedonly / auto）
  - 结构化 Alpaca / ShareGPT 自动识别与转换（passthrough）
  - 训练/验证集切分与写出
"""
import json
import tempfile
from pathlib import Path

from train.data_prep import (
    collect_samples,
    doc_to_samples,
    normalize_record,
    split_chunks,
    write_datasets,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_split_chunks_respects_max_chars():
    text = "段落一。\n\n段落二内容较长，" + "字" * 2000 + "。\n\n段落三。"
    chunks = split_chunks(text, max_chars=800, overlap=80)
    assert chunks, "应至少切出一块"
    assert all(len(c) <= 800 for c in chunks), "块长不应超过 max_chars"
    assert "段落一" in chunks[0]


def test_doc_to_samples_produces_sharegpt():
    text = "幻炘AI 是一个自进化多智能体系统。它支持多领域协作与自主演化。"
    samples = doc_to_samples(text)
    assert samples, "应派生出样本"
    for s in samples:
        assert "conversations" in s
        roles = [t["from"] for t in s["conversations"]]
        assert "human" in roles and "gpt" in roles
        for t in s["conversations"]:
            assert t["value"], "对话内容不应为空"


def test_collect_seedonly_from_txt():
    with tempfile.TemporaryDirectory() as d:
        raw = Path(d) / "raw"
        _write(raw / "a.txt", "人工智能正在改变软件工程。\n\n自动化测试能显著提升质量。")
        samples = collect_samples(raw, mode="seedonly")
        assert len(samples) >= 1
        assert all("conversations" in s for s in samples)


def test_collect_passthrough_alpaca(tmp_path):
    raw = tmp_path / "raw"
    rec = {"instruction": "解释什么是 LoRA", "input": "", "output": "LoRA 是一种低秩适配方法。"}
    _write(raw / "data.jsonl", json.dumps(rec, ensure_ascii=False) + "\n")

    samples = collect_samples(raw, mode="passthrough")
    assert len(samples) == 1
    conv = samples[0]["conversations"]
    assert conv[0]["from"] == "human" and "LoRA" in conv[0]["value"]
    assert conv[1]["from"] == "gpt" and "低秩" in conv[1]["value"]


def test_collect_auto_mixed(tmp_path):
    raw = tmp_path / "raw"
    _write(raw / "doc.txt", "模型微调需要高质量指令数据。\n\n数据质量决定上限。")
    sharegpt = {"conversations": [{"from": "human", "value": "你好"}, {"from": "gpt", "value": "你好，我是助手"}]}
    _write(raw / "c.jsonl", json.dumps(sharegpt, ensure_ascii=False) + "\n")

    samples = collect_samples(raw, mode="auto")
    # 文本派生至少 2 条 + 结构化 1 条
    assert len(samples) >= 3
    assert any(s["conversations"][0]["value"] == "你好" for s in samples)


def test_write_datasets_splits_and_validates():
    samples = doc_to_samples("中文对话模型需要多领域数据支撑。可扩展多层级多方位。")
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "prepared"
        n_train, n_val = write_datasets(samples, out, val_ratio=0.2, seed=1)
        assert (out / "train.jsonl").exists()
        assert (out / "val.jsonl").exists()
        assert n_train + n_val == len(samples)
        # 每行都是合法 JSON 且为 ShareGPT
        for f in ("train.jsonl", "val.jsonl"):
            lines = (out / f).read_text(encoding="utf-8").strip().splitlines()
            for line in lines:
                obj = json.loads(line)
                assert "conversations" in obj


def test_normalize_record_openai_messages():
    rec = {"messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]}
    norm = normalize_record(rec)
    assert norm["conversations"][0]["from"] == "human"
    assert norm["conversations"][1]["from"] == "gpt"

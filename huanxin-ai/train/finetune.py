"""finetune.py — 在开源基座（默认 Qwen2.5）上做 LoRA 微调，产出「你的模型」。

依赖（仅在真正训练时需要）：torch, transformers, peft, datasets, accelerate, pyyaml
  pip install -r train/requirements.txt

设计要点：
  * 依赖全部懒加载 —— 没装 torch 时仍可 `python finetune.py --help` 与 `--check-config`。
  * 自动探测设备：CUDA > MPS > CPU（CPU 仅用于冒烟测试，真实训练请用 GPU）。
  * Qwen2.5 + LoRA：单张 24G 显卡即可微调 7B；显存不足开 quantization=4bit。
  * 多领域：为不同 domain 各跑一次，得到各自 LoRA 适配器，按需加载（"可扩展多领域"）。

用法：
  python finetune.py --config config.yaml
  python finetune.py --config config.yaml --check-config   # 仅校验配置，不训练
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _load_yaml(path: str) -> dict:
    try:
        import yaml  # pyyaml 已在主服务依赖中
    except ImportError:
        print("[finetune] 缺少 pyyaml，请先安装。", file=sys.stderr)
        raise
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


@dataclass
class TrainConfig:
    base_model: str = "Qwen/Qwen2.5-7B-Instruct"
    domain: str = "general-zh"
    data_dir: str = "data/prepared"
    train_file: str = "train.jsonl"
    val_file: str = "val.jsonl"
    output_dir: str = "models/my-qwen"
    lora: dict[str, Any] = field(default_factory=lambda: {
        "r": 16, "alpha": 32, "dropout": 0.05, "bias": "none",
        "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    })
    quantization: str = "none"  # none | 4bit | 8bit
    training: dict[str, Any] = field(default_factory=lambda: {
        "num_epochs": 3, "per_device_train_batch_size": 4,
        "gradient_accumulation_steps": 8, "learning_rate": 2.0e-4,
        "warmup_ratio": 0.03, "max_seq_length": 2048,
        "logging_steps": 10, "save_steps": 200, "fp16": False, "bf16": True,
    })
    merge_and_save: bool = False
    merged_dir: str = "models/my-qwen-merged"
    serve_name: str = "my-qwen"

    @classmethod
    def from_yaml(cls, path: str) -> "TrainConfig":
        raw = _load_yaml(path)
        cfg = cls()
        for k in ("base_model", "domain", "data_dir", "train_file", "val_file",
                  "output_dir", "quantization", "merge_and_save", "merged_dir", "serve_name"):
            if k in raw:
                setattr(cfg, k, raw[k])
        if "lora" in raw and isinstance(raw["lora"], dict):
            cfg.lora.update(raw["lora"])
        if "training" in raw and isinstance(raw["training"], dict):
            cfg.training.update(raw["training"])
        return cfg

    def validate(self) -> list[str]:
        errs: list[str] = []
        if not self.base_model:
            errs.append("base_model 不能为空")
        for f in (self.train_file, self.val_file):
            p = Path(self.data_dir) / f
            if not p.exists():
                errs.append(f"数据文件不存在: {p}")
        if self.quantization not in ("none", "4bit", "8bit"):
            errs.append("quantization 必须是 none | 4bit | 8bit")
        return errs


# --------------------------------------------------------------------------- #
# 训练（仅在此函数内导入重依赖）
# --------------------------------------------------------------------------- #


def _device_string() -> str:
    import torch
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def train(cfg: TrainConfig) -> int:
    import torch
    from datasets import load_dataset
    from transformers import (AutoModelForCausalLM, AutoTokenizer, Trainer, TrainingArguments,
                              DataCollatorForLanguageModeling)
    from peft import LoraConfig, get_peft_model, TaskType

    device = _device_string()
    print(f"[finetune] 设备：{device}  基座：{cfg.base_model}  领域：{cfg.domain}")

    tokenizer = AutoTokenizer.from_pretrained(cfg.base_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    quant_kwargs: dict[str, Any] = {}
    if cfg.quantization == "4bit":
        from transformers import BitsAndBytesConfig
        quant_kwargs["quantization_config"] = BitsAndBytesConfig(load_in_4bit=True)
    elif cfg.quantization == "8bit":
        from transformers import BitsAndBytesConfig
        quant_kwargs["quantization_config"] = BitsAndBytesConfig(load_in_8bit=True)

    model = AutoModelForCausalLM.from_pretrained(
        cfg.base_model, trust_remote_code=True, **quant_kwargs
    )
    model.config.use_cache = False

    lora_cfg = LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        r=int(cfg.lora.get("r", 16)),
        lora_alpha=int(cfg.lora.get("alpha", 32)),
        lora_dropout=float(cfg.lora.get("dropout", 0.05)),
        bias=cfg.lora.get("bias", "none"),
        target_modules=cfg.lora.get("target_modules"),
    )
    model = get_peft_model(model, lora_cfg)
    model.print_trainable_parameters()

    max_len = int(cfg.training.get("max_seq_length", 2048))

    def _build_messages(ex):
        convs = ex.get("conversations", [])
        msgs = []
        for turn in convs:
            role = "user" if turn.get("from") == "human" else "assistant"
            val = turn.get("value", "")
            if val:
                msgs.append({"role": role, "content": val})
        return msgs

    def _tokenize(ex):
        msgs = _build_messages(ex)
        if not msgs:
            return {"input_ids": [], "labels": [], "attention_mask": []}
        text = tokenizer.apply_chat_template(msgs, tokenize=False, add_generation_prompt=False)
        tok = tokenizer(text, truncation=True, max_length=max_len)
        tok["labels"] = list(tok["input_ids"])
        return tok

    data_files = {
        "train": str(Path(cfg.data_dir) / cfg.train_file),
        "validation": str(Path(cfg.data_dir) / cfg.val_file),
    }
    raw = load_dataset("json", data_files=data_files)
    tokd = raw.map(_tokenize, remove_columns=raw["train"].column_names)

    collator = DataCollatorForLanguageModeling(tokenizer, mlm=False)

    t = cfg.training
    args = TrainingArguments(
        output_dir=cfg.output_dir,
        num_train_epochs=int(t.get("num_epochs", 3)),
        per_device_train_batch_size=int(t.get("per_device_train_batch_size", 4)),
        per_device_eval_batch_size=int(t.get("per_device_train_batch_size", 4)),
        gradient_accumulation_steps=int(t.get("gradient_accumulation_steps", 8)),
        learning_rate=float(t.get("learning_rate", 2.0e-4)),
        warmup_ratio=float(t.get("warmup_ratio", 0.03)),
        logging_steps=int(t.get("logging_steps", 10)),
        save_steps=int(t.get("save_steps", 200)),
        fp16=bool(t.get("fp16", False)),
        bf16=bool(t.get("bf16", True)),
        report_to="none",
        save_total_limit=2,
    )

    trainer = Trainer(
        model=model,
        args=args,
        train_dataset=tokd["train"],
        eval_dataset=tokd.get("validation"),
        data_collator=collator,
    )
    trainer.train()
    trainer.save_model(cfg.output_dir)
    print(f"[finetune] LoRA 已保存 -> {cfg.output_dir}")

    if cfg.merge_and_save:
        print("[finetune] 合并 LoRA 到基座 ...")
        model = model.merge_and_unload()
        Path(cfg.merged_dir).mkdir(parents=True, exist_ok=True)
        model.save_pretrained(cfg.merged_dir)
        tokenizer.save_pretrained(cfg.merged_dir)
        print(f"[finetune] 合并模型已保存 -> {cfg.merged_dir}")

    # 提示下一步
    print("\n[下一步] 用 serve_register.py 生成 HUANXIN_MODELS 片段，把模型挂到 /v1：")
    print(f"  python serve_register.py --adapter-dir {cfg.output_dir} --name {cfg.serve_name}")
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Qwen2.5 + LoRA 微调（产出你的模型）")
    ap.add_argument("--config", required=True, help="配置文件路径（见 config.example.yaml）")
    ap.add_argument("--check-config", action="store_true", help="仅校验配置，不训练")
    args = ap.parse_args(argv)

    try:
        cfg = TrainConfig.from_yaml(args.config)
    except Exception as e:  # noqa: BLE001
        print(f"[finetune] 读取配置失败: {e}", file=sys.stderr)
        return 2

    errs = cfg.validate()
    if errs:
        for e in errs:
            print(f"[finetune] 配置错误: {e}", file=sys.stderr)
        return 1

    if args.check_config:
        print("[finetune] 配置校验通过：")
        print(f"  base_model   : {cfg.base_model}")
        print(f"  domain       : {cfg.domain}")
        print(f"  data_dir     : {cfg.data_dir}")
        print(f"  output_dir   : {cfg.output_dir}")
        print(f"  quantization : {cfg.quantization}")
        print(f"  merge        : {cfg.merge_and_save}")
        return 0

    try:
        return train(cfg)
    except ImportError as e:
        print(f"[finetune] 缺少训练依赖：{e}", file=sys.stderr)
        print("          请先执行：pip install -r train/requirements.txt", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())

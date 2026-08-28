# 魔搭（ModelScope）训练幻炘AI 专属模型实战指南

在魔搭免费 GPU 实例（DSW，A10 24G，36h/月）上对 Qwen2.5 基座做 LoRA/QLoRA 微调，
产出专属权重并挂回 huanxin-ai 的 `/v1` 模型 API。**2026-08-28 已端到端跑通**：
30 步训练 loss 2.87 → 0.78，LoRA merge 成功落盘。

## 0. 实例准备
- DSW 控制台开免费实例；终端位于 `/mnt/workspace`，用户 root
- 若 git 子命令缺失：`apt-get update && apt-get install -y git`
- 克隆：`cd /mnt/workspace && git clone https://github.com/kiwoen/huanxin-ai.git`
  **注意仓库结构是两层**：代码在 `huanxin-ai/huanxin-ai/`（根目录是 Obsidian 笔记）

## 1. 安装 ms-swift
```bash
pip install ms-swift[llm] -i https://pypi.tuna.tsinghua.edu.cn/simple
```
swift 4.x **没有 `--version`**；看版本用 `pip show ms-swift`。

## 2. 准备数据
```bash
cd /mnt/workspace/huanxin-ai/huanxin-ai
python train/data_prep.py --input data/raw --output data/prepared
```
产出 `data/prepared/train.jsonl`（ShareGPT 格式）+ `val.jsonl`。

## 3. 训练（QLoRA 4bit；24G 显存可跑 7B，打样用 1.5B）
```bash
swift sft \
  --model Qwen/Qwen2.5-1.5B-Instruct \
  --dataset data/prepared/train.jsonl \
  --num_train_epochs 1 --max_steps 30 \
  --per_device_train_batch_size 2 --gradient_accumulation_steps 4 \
  --learning_rate 1e-4 \
  --lora_rank 8 --lora_alpha 32 \
  --quant_method bnb --quant_bits 4 \
  --output_dir /mnt/workspace/output \
  --save_total_limit 1 --logging_steps 5
```

## 4. 合并 LoRA 为全参模型
```bash
swift merge_lora \
  --model Qwen/Qwen2.5-1.5B-Instruct \
  --adapters /mnt/workspace/output/v1-*/checkpoint-30 \
  --output_dir /mnt/workspace/output/merged
```

## 5. 生成挂回 /v1 的配置片段
```bash
bash train/publish_huanxin.sh
```
自动探查产物并调用 `train/serve_register.py`（参数用**中划线**：
`--merged-dir` / `--adapter-dir` / `--out`），打印 `HUANXIN_MODELS` 片段。

## 6. 推理验证
```bash
swift infer --model /mnt/workspace/output/merged --infer_backend vllm --max_new_tokens 256
```

## ⚠️ 踩坑实录（2026-08-28）
| 坑 | 正确做法 |
|---|---|
| 模型 ID 写 `Qwen2.5-1_5B-Instruct` → 404 | ID 用**点号小数**：`Qwen2.5-1.5B-Instruct` |
| `--train_dataset_mix_ratio 1.0` → `ValueError: remaining_argv` | ms-swift 4.4.2 已移除该参数，单数据集直接 `--dataset` |
| `swift --version` → `KeyError` | swift 4.x 无全局 `--version`，用 `pip show ms-swift` |
| argparse flag 写 `--merged_dir` → 无效 | 一律中划线 `--merged-dir` |
| DSW 终端粘贴长脚本被 bracketed-paste 控制符污染 | 单行命令分开跑；长逻辑写进仓库用 `curl -sSL <raw-url> \| bash` 自举 |
| bash 里 `$!` 复制粘贴被篡改成 `$1`/`[200~` | 用 `jobs -p` 或写 pid 文件 |
| DSW「运行时长」数字随重连重置 | 不能当剩余时间预测，按配额估算 |

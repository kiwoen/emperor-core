#!/usr/bin/env bash
# publish_huanxin.sh — 幻炘AI 专属模型一键挂回脚本（魔搭 DSW 训练完成后执行）
# 用法（在 huanxin-ai 仓库的二级代码目录下）：bash train/publish_huanxin.sh
set -u
cd "$(dirname "$0")/.."
OUT_DIR=/mnt/workspace/output
echo "[publish] 工作目录: $(pwd)"

# 1. 探查训练产物（优先 merged 全参模型，其次 LoRA checkpoint）
MERGED=""; ADAPTER=""
[ -d "$OUT_DIR/merged" ] && MERGED="$OUT_DIR/merged"
if [ -z "$MERGED" ]; then
  for d in "$OUT_DIR"/v1-*; do
    ckpt=$(ls -d "$d"/checkpoint-* 2>/dev/null | sort -V | tail -1)
    [ -n "$ckpt" ] && ADAPTER="$ckpt" && break
  done
fi
[ -z "$MERGED" ] && [ -z "$ADAPTER" ] && { echo "[fail] 找不到 $OUT_DIR/merged 或 v1-*/checkpoint-*"; exit 1; }

# 2. 生成 HUANXIN_MODELS 挂回片段（argparse 参数一律用中划线）
mkdir -p "$OUT_DIR"
if [ -n "$MERGED" ]; then
  echo "[publish] merged: $MERGED"
  python train/serve_register.py --name huanxin-1.5b-v0 \
    --merged-dir "$MERGED" --backend vllm --port 8001 \
    --out "$OUT_DIR/huanxin_models_fragment.json" || true
fi
if [ -n "$ADAPTER" ]; then
  echo "[publish] adapter: $ADAPTER"
  python train/serve_register.py --name huanxin-1.5b-lora-v0 \
    --adapter-dir "$ADAPTER" --backend vllm --port 8002 \
    --out "$OUT_DIR/huanxin_adapter_fragment.json" || true
fi

# 3. 打印产物（把这段 JSON 贴回给 AI，由 AI 生成生产部署配置）
for f in "$OUT_DIR"/huanxin_*fragment*.json; do
  [ -f "$f" ] && { echo "----- FILE: $f -----"; cat "$f"; echo "----- END -----"; }
done
echo "[publish] 完成。请把上面 JSON 保存，用于生产 /v1 挂载。"

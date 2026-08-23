#!/usr/bin/env bash
# ============================================================================
# emperor-core + API 中转站 (New API) —— 服务器一键实跑清单
# ----------------------------------------------------------------------------
# 用途：在你的服务器上把「API 中转站」和「emperor-core」一次性拉起来，
#       并灌入 Hacker News 蒸馏语料。
# 前置：Linux 服务器，已装 docker / docker compose / python3 / pip / curl / openssl。
# 用法：把本文件(scp)到服务器，然后  bash bring-up.sh
#       （在 deploy/relay 目录下执行最稳；脚本会自动定位 emperor-core 根目录）
# ============================================================================
set -euo pipefail

RELAY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$RELAY_DIR/../.." && pwd)"     # emperor-core 根目录
EC_ENV="$ROOT_DIR/.env"

echo "==> 目录: relay=$RELAY_DIR  emperor-core=$ROOT_DIR"

# ── [1/6] 启动 API 中转站 (New API) ───────────────────────────────────────
echo "==> [1/6] 启动 API 中转站 (New API) ..."
cd "$RELAY_DIR"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "    已生成 $RELAY_DIR/.env —— 请编辑 SESSION_SECRET 为随机串后重跑:"
  echo "    SESSION_SECRET=$(openssl rand -hex 32)"
  exit 1
fi
docker compose up -d
echo "    等待中转站就绪 (http://<host>:3000) ..."
for _ in $(seq 1 30); do
  if curl -sf -o /dev/null http://localhost:3000; then break; fi
  sleep 2
done
echo "    中转站已启动。请在网页完成: 创建 root 管理员 → 添加渠道(Channels) → 创建令牌(Token)"

# ── 取令牌：优先环境变量，其次 .relay_key 文件 ─────────────────────────────
if [ -z "${EMPEROR_RELAY_KEY:-}" ] && [ -f "$RELAY_DIR/.relay_key" ]; then
  EMPEROR_RELAY_KEY="$(cat "$RELAY_DIR/.relay_key")"
fi
if [ -z "${EMPEROR_RELAY_KEY:-}" ]; then
  echo "!! 缺少 EMPEROR_RELAY_KEY（中转站令牌）。请操作:"
  echo "   1) 打开 http://<host>:3000  → 登录 → 【令牌】→ 新建令牌，复制值"
  echo "   2) 执行:  echo '粘贴的令牌' > $RELAY_DIR/.relay_key"
  echo "   3) 重新运行:  bash bring-up.sh"
  exit 2
fi

RELAY_HOST="${EMPEROR_RELAY_HOST:-http://localhost:3000}"
export EMPEROR_RELAY_URL="${RELAY_HOST%/}/v1"
export EMPEROR_RELAY_KEY

# ── [2/6] 把中转站地址/令牌写入 emperor-core .env ─────────────────────────
echo "==> [2/6] 写入 emperor-core .env (EMPEROR_RELAY_URL / EMPEROR_RELAY_KEY) ..."
touch "$EC_ENV"
if grep -q '^EMPEROR_RELAY_URL=' "$EC_ENV"; then
  sed -i "s#^EMPEROR_RELAY_URL=.*#EMPEROR_RELAY_URL=$EMPEROR_RELAY_URL#" "$EC_ENV"
else
  echo "EMPEROR_RELAY_URL=$EMPEROR_RELAY_URL" >> "$EC_ENV"
fi
if grep -q '^EMPEROR_RELAY_KEY=' "$EC_ENV"; then
  sed -i "s#^EMPEROR_RELAY_KEY=.*#EMPEROR_RELAY_KEY=$EMPEROR_RELAY_KEY#" "$EC_ENV"
else
  echo "EMPEROR_RELAY_KEY=$EMPEROR_RELAY_KEY" >> "$EC_ENV"
fi

# ── [3/6] 安装依赖 ─────────────────────────────────────────────────────────
echo "==> [3/6] 安装 Python 依赖 (pip install -r requirements.txt) ..."
cd "$ROOT_DIR"
pip install -r requirements.txt

# ── [4/6] 启动 emperor-core (uvicorn, 后台) ───────────────────────────────
echo "==> [4/6] 启动 emperor-core (uvicorn :8000) ..."
nohup uvicorn jarvis.court_api:app --host 0.0.0.0 --port 8000 \
  > "$ROOT_DIR/ec.log" 2>&1 &
EC_PID=$!
echo "    emperor-core PID=$EC_PID  日志=$ROOT_DIR/ec.log"
sleep 4
if curl -sf -o /dev/null http://localhost:8000/api/models; then
  echo "    emperor-core 健康检查通过: /api/models 可访问"
else
  echo "    ⚠ emperor-core 尚未就绪，tail -f $ROOT_DIR/ec.log 查看日志"
fi

# ── [5/6] 灌入 Hacker News 蒸馏语料 ───────────────────────────────────────
echo "==> [5/6] 灌入 Hacker News 蒸馏语料 (DistillationStore + SocialCollector) ..."
cd "$ROOT_DIR"
python - <<'PY'
import asyncio
from jarvis.learning.distillation_store import DistillationStore
from jarvis.learning.social_collector import SocialCollector

async def main():
    store = DistillationStore(append_jsonl_path="jarvis_data/distillation_traces.jsonl")
    col = SocialCollector(store=store)
    queries = [
        "llm agent",
        "multimodal model",
        "AI video generation",
        "self-improving AI",
        "model distillation",
    ]
    for q in queries:
        try:
            n = await col.fetch(q, limit=20)
            print(f"  + 已摄入 {len(n)} 条  query={q!r}")
        except Exception as e:  # best-effort
            print(f"  ! 摄取失败 query={q!r}: {e}")
    print(f"  蒸馏语料总量(本次会话): {len(store)} 条")

asyncio.run(main())
print("HN 语料灌入完成。")
PY

# ── [6/6] 收尾 ────────────────────────────────────────────────────────────
echo "==> [6/6] 完成。"
echo "    中转站:        http://<host>:3000   (网页管理令牌/渠道)"
echo "    emperor-core:   http://<host>:8000   (日志: $ROOT_DIR/ec.log)"
echo "    验证命令:       curl http://localhost:8000/api/models"
echo "    停止 emperor-core:  kill $EC_PID"
echo "    停止中转站:        cd $RELAY_DIR && docker compose down"

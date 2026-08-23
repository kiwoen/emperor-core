#!/usr/bin/env bash
# ============================================================================
# 阿里云 ECS —— emperor-core 全栈一键部署脚本
# ----------------------------------------------------------------------------
# 适用：Alibaba Cloud Linux / Ubuntu / CentOS 7+  （root 或 sudo 用户）
# 编排：中转站 New API(3000) + emperor-core(8000, 容器化) + [可选] 本地 VLM(11434)
#
# 用法：
#   1) 把本仓库 git clone 到 ECS：  git clone https://github.com/kiwoen/emperor-core.git
#   2) cd emperor-core
#   3) 改 .env.example -> .env 并填真实值（见脚本末尾「必填项清单」）
#   4) sudo bash deploy/ecs/setup-ecs.sh
#
# 设计原则（与既有架构一致）：
#   - emperor-core 走根目录 docker-compose.yml（生产级，命名卷 emperor-data 持久化）
#   - 中转站走 deploy/relay/docker-compose.yml（New API，SQLite 默认）
#   - 本地 VLM 仅「可选」：纯云端模型（中转站接 OpenAI/Anthropic 等）无需 GPU
#   - 推理路径绝不实时借用外部；外部模型只在蒸馏/学习时被采集（LearningCollector）
# ============================================================================
set -euo pipefail

EC_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RELAY_DIR="$EC_ROOT/deploy/relay"
cd "$EC_ROOT"

echo "============================================================"
echo " emperor-core 阿里云 ECS 部署   root=$EC_ROOT"
echo "============================================================"

# ───────────────────────────────────────────────────────────────
# [0] 系统探测 + Docker 安装（阿里云 ECS 通常没预装 Docker）
# ───────────────────────────────────────────────────────────────
echo "==> [0] 探测系统 + 安装 Docker / docker compose ..."
if ! command -v docker >/dev/null 2>&1; then
  echo "    docker 未安装，开始安装（支持 alinux/ubuntu/centos）..."
  if command -v dnf >/dev/null 2>&1; then
    sudo dnf -y install docker || yum -y install docker
  elif command -v yum >/dev/null 2>&1; then
    sudo yum -y install docker
  elif command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -y
    sudo apt-get install -y docker.io
  else
    echo "!! 无法识别的包管理器，请手动安装 Docker 后重跑"; exit 3
  fi
  sudo systemctl enable --now docker
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "    安装 docker-compose-plugin ..."
  sudo systemctl restart docker
  # 多数新版 docker 已含 compose 子命令；若仍缺则用独立二进制兜底
  if ! docker compose version >/dev/null 2>&1; then
    sudo curl -SL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-$(uname -m)" \
      -o /usr/local/bin/docker-compose
    sudo chmod +x /usr/local/bin/docker-compose
  fi
fi
docker --version
docker compose version

# ───────────────────────────────────────────────────────────────
# [1] GPU 探测 → 决定是否起本地 VLM
# ───────────────────────────────────────────────────────────────
echo "==> [1] 探测 GPU ..."
HAS_GPU=0
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
  HAS_GPU=1
  echo "    检测到 NVIDIA GPU："
  nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | sed 's/^/      /'
else
  echo "    未检测到 GPU（或驱动未装）。将跳过本地 VLM，纯走中转站云端模型。"
fi

# 本地 VLM 开关：默认「有 GPU 才开」，可用环境变量强制覆盖
ENABLE_LOCAL_VLM="${ENABLE_LOCAL_VLM:-$HAS_GPU}"
# 本地 VLM 选型：3B(显存优先) / 7B(质量优先)，按显存自动选
VLM_MODEL="${VLM_MODEL:-auto}"   # auto | qwen2.5-vl-3b-awq | qwen2.5-vl-7b-awq
if [ "$ENABLE_LOCAL_VLM" = "1" ] && [ "$VLM_MODEL" = "auto" ]; then
  # 粗略按显存选（nvidia-smi 取总显存 MB）
  MEM_MB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -1 | tr -d ' ')
  if [ "${MEM_MB:-0}" -ge 8000 ]; then VLM_MODEL="qwen2.5-vl-7b-awq"; else VLM_MODEL="qwen2.5-vl-3b-awq"; fi
  echo "    按显存(${MEM_MB}MB) 自动选本地 VLM: $VLM_MODEL"
fi

# ───────────────────────────────────────────────────────────────
# [2] .env 准备（emperor-core 根目录）
# ───────────────────────────────────────────────────────────────
echo "==> [2] 准备 emperor-core .env ..."
if [ ! -f .env ]; then
  cp .env.example .env
  echo "    已生成 $EC_ROOT/.env"
  echo "    ⚠ 请在重跑前编辑以下必填项（脚本会检测缺失）："
  echo "      EMPEROR_API_TOKEN=$(openssl rand -hex 24)   # 必填，否则公网裸奔"
  echo "      OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL  # 接真实模型（或走中转站）"
  echo "    然后重新运行:  sudo bash deploy/ecs/setup-ecs.sh"
  exit 4
fi
# 强制要求 API 令牌（安全）
if ! grep -q '^EMPEROR_API_TOKEN=.\+' .env; then
  echo "!! .env 中 EMPEROR_API_TOKEN 为空 —— 公网部署必须设置，退出。"
  echo "   生成:  openssl rand -hex 24   然后填入 .env"
  exit 5
fi

# ───────────────────────────────────────────────────────────────
# [3] 中转站 (New API) 启动
# ───────────────────────────────────────────────────────────────
echo "==> [3] 启动 API 中转站 (New API, :3000) ..."
cd "$RELAY_DIR"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "    已生成 $RELAY_DIR/.env —— 请编辑 SESSION_SECRET 后重跑"
  echo "    SESSION_SECRET=$(openssl rand -hex 32)"
  exit 6
fi
docker compose up -d
echo "    等待中转站就绪 ..."
for _ in $(seq 1 30); do
  if curl -sf -o /dev/null http://localhost:3000; then echo "    中转站已起"; break; fi
  sleep 2
done
echo "    ⚠ 请在网页完成: 创建 root 管理员 → 添加渠道(Channels) → 创建令牌(Token)"

# 取中转站令牌
RELAY_KEY="${EMPEROR_RELAY_KEY:-}"
[ -z "$RELAY_KEY" ] && [ -f "$RELAY_DIR/.relay_key" ] && RELAY_KEY="$(cat "$RELAY_DIR/.relay_key")"
if [ -z "$RELAY_KEY" ]; then
  echo "!! 缺少中转站令牌。操作:"
  echo "   1) 打开 http://<host>:3000 → 登录 → 【令牌】→ 新建令牌，复制值"
  echo "   2) echo '粘贴的令牌' > $RELAY_DIR/.relay_key"
  echo "   3) 重新运行: sudo bash deploy/ecs/setup-ecs.sh"
  exit 7
fi
RELAY_HOST="${EMPEROR_RELAY_HOST:-http://localhost:3000}"
RELAY_URL="${RELAY_HOST%/}/v1"
# 写回 emperor-core .env（中转站地址 + 令牌 + 学习开关）
cd "$EC_ROOT"
set_env() {  # key value
  local k="$1" v="$2"
  if grep -q "^$k=" .env; then sed -i "s#^$k=.*#$k=$v#" .env; else echo "$k=$v" >> .env; fi
}
set_env EMPEROR_RELAY_URL "$RELAY_URL"
set_env EMPEROR_RELAY_KEY "$RELAY_KEY"
# 新计费/学习层 env（本期默认；详见 docs/research/emperor-relay-billing-design-2026-08-23.md）
set_env EMPEROR_BILLING_ENABLED "true"
set_env EMPEROR_BILLING_FREE_CREDIT "1000"
set_env EMPEROR_LEARNING_OPT_IN "true"   # 用户同意后才旁路采集
set_env EMPEROR_DISTILL_MODE "on"        # 学习/蒸馏时咨询外部；推理路径禁用

# ───────────────────────────────────────────────────────────────
# [4] （可选）本地 VLM 服务（vLLM 暴露 /v1，供中转站挂为渠道）
# ───────────────────────────────────────────────────────────────
if [ "$ENABLE_LOCAL_VLM" = "1" ]; then
  echo "==> [4] 启动本地 VLM ($VLM_MODEL) via vLLM :11434 ..."
  docker run -d --name emperor-vlm --restart unless-stopped --gpus all \
    -p 11434:8000 \
    vllm/vllm-openai:latest \
    --model "Qwen/Qwen2.5-VL-${VLM_MODEL#qwen2.5-vl-}-Instruct-AWQ" \
    --quantization awq \
    --limit-mm-per-prompt image=3,video=2 \
    --port 8000
  echo "    本地 VLM 启动中（首次拉镜像+加载权重约数分钟）。"
  echo "    在中转站网页添加一个 OpenAI 兼容渠道，Base URL = http://<ECS内网IP>:11434/v1"
else
  echo "==> [4] 跳过本地 VLM（纯中转站云端模型模式）。"
fi

# ───────────────────────────────────────────────────────────────
# [5] emperor-core 容器化启动（根 docker-compose.yml）
# ───────────────────────────────────────────────────────────────
echo "==> [5] 启动 emperor-core (容器化, :8000) ..."
cd "$EC_ROOT"
docker compose up -d --build
echo "    等待 emperor-core 健康检查 ..."
for _ in $(seq 1 40); do
  if curl -sf -o /dev/null http://localhost:8000/health; then echo "    emperor-core 健康 OK"; break; fi
  sleep 3
done
if ! curl -sf -o /dev/null http://localhost:8000/health; then
  echo "    ⚠ emperor-core 尚未就绪，查看: docker compose logs -f emperor-core"
fi

# ───────────────────────────────────────────────────────────────
# [6] 灌入 Hacker News 蒸馏语料（仅真实调用；离线 mock 不写）
# ───────────────────────────────────────────────────────────────
echo "==> [6] 灌入 HN 蒸馏语料 (DistillationStore + SocialCollector) ..."
docker compose exec -T emperor-core python - <<'PY' || echo "    (语料灌入失败不影响主服务，可稍后重试)"
import asyncio
from jarvis.learning.distillation_store import DistillationStore
from jarvis.learning.social_collector import SocialCollector
async def main():
    store = DistillationStore(append_jsonl_path="/app/data/distillation_traces.jsonl")
    col = SocialCollector(store=store)
    for q in ["llm agent","multimodal model","AI video generation","self-improving AI","model distillation"]:
        try:
            n = await col.fetch(q, limit=20)
            print(f"  + {len(n)} 条  query={q!r}")
        except Exception as e:
            print(f"  ! 失败 {q!r}: {e}")
asyncio.run(main())
print("HN 语料灌入完成。")
PY

# ───────────────────────────────────────────────────────────────
echo "============================================================"
echo " 部署完成 ✅"
echo "   中转站:        http://<ECS公网IP>:3000   (管理令牌/渠道)"
echo "   emperor-core:  http://<ECS公网IP>:8000   (需带 EMPEROR_API_TOKEN)"
echo "   本地 VLM:      http://<ECS内网IP>:11434  (若启用)"
echo "------------------------------------------------------------"
echo " ⚠ 阿里云安全组务必放行: 3000 / 8000 (和 11434 若用本地VLM)"
echo " ⚠ 中转站网页首次访问需手动: 建 root 管理员 → 加渠道 → 建令牌 → 写入 .relay_key"
echo " ⚠ 后续运维: docker compose ps / logs -f / down"
echo "============================================================"

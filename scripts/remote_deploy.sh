#!/usr/bin/env bash
# ============================================================
# emperor-core 服务器端更新脚本（幂等 + 健康检查 + 自动回滚）
# ============================================================
# 在**服务器上**执行，把 /srv/emperor-core 更新到 origin 最新代码并重建容器。
#
# 用法（服务器上）：
#   cd /srv/emperor-core && bash scripts/remote_deploy.sh
#
# 也可从本机远程执行（无需先登录）：
#   ssh root@<公网IP> 'cd /srv/emperor-core && git fetch origin && \
#     git show origin/master:scripts/remote_deploy.sh | bash -s'
#
# 环境变量（均可选）：
#   DEPLOY_DIR    部署目录，默认 /srv/emperor-core
#   DEPLOY_BRANCH 跟踪分支，默认 master
#   APP_PORT      健康检查端口，默认 8000
#   HEALTH_RETRY  健康检查重试次数，默认 40（每次间隔 3s，约 2 分钟）
#   NO_ROLLBACK   设为 1 时健康检查失败不回滚（仅排障用）
#
# 设计要点：
#   * git fetch + reset --hard 而非 git pull —— 幂等，且不会因服务器上的
#     本地改动导致 merge 冲突卡住。未被 git 跟踪的 .env 不受影响。
#   * 部署前记录当前 commit，健康检查失败时自动回滚到该 commit 并重建。
#   * 只清理 dangling 镜像，避免学生机 40G 系统盘被旧层撑满。
# ============================================================
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/srv/emperor-core}"
DEPLOY_BRANCH="${DEPLOY_BRANCH:-master}"
APP_PORT="${APP_PORT:-8000}"
HEALTH_RETRY="${HEALTH_RETRY:-40}"

log()  { echo "[deploy] $*"; }
fail() { echo "[deploy][ERROR] $*" >&2; exit 1; }

# ── 0. 前置检查 ─────────────────────────────────────────────
cd "$DEPLOY_DIR" 2>/dev/null || fail "部署目录不存在：$DEPLOY_DIR"
[ -d .git ] || fail "$DEPLOY_DIR 不是 git 仓库，请先 git clone"
command -v docker >/dev/null || fail "docker 未安装"
docker compose version >/dev/null 2>&1 \
  || fail "docker compose 插件缺失，执行：apt-get install -y docker-compose-plugin"

# ── 1. 记录当前版本（用于回滚）───────────────────────────────
PREV_SHA="$(git rev-parse HEAD)"
log "当前版本：${PREV_SHA:0:8}"

# ── 2. 拉取目标版本 ─────────────────────────────────────────
log "拉取 origin/${DEPLOY_BRANCH} …"
git fetch --prune origin "$DEPLOY_BRANCH"
TARGET_SHA="$(git rev-parse "origin/${DEPLOY_BRANCH}")"

if [ "$PREV_SHA" = "$TARGET_SHA" ]; then
  log "已是最新（${TARGET_SHA:0:8}），仍将确保容器在运行"
else
  log "更新到：${TARGET_SHA:0:8}"
  # reset --hard 只影响被跟踪文件，未跟踪的 .env / 备份 tar 不会丢
  git reset --hard "$TARGET_SHA"
fi

# ── 3. 重建并拉起 ───────────────────────────────────────────
log "docker compose up -d --build …"
docker compose up -d --build

# ── 4. 健康检查 ─────────────────────────────────────────────
log "等待 /health 就绪（最多 $((HEALTH_RETRY * 3)) 秒）…"
HEALTHY=0
for _ in $(seq 1 "$HEALTH_RETRY"); do
  if curl -fsS --max-time 5 "http://localhost:${APP_PORT}/health" >/dev/null 2>&1; then
    HEALTHY=1
    break
  fi
  sleep 3
done

if [ "$HEALTHY" != "1" ]; then
  echo "[deploy][ERROR] 健康检查失败，最近 60 行日志：" >&2
  docker compose logs --tail=60 || true

  if [ "${NO_ROLLBACK:-0}" = "1" ]; then
    fail "健康检查失败（NO_ROLLBACK=1，保留现场供排障）"
  fi
  if [ "$PREV_SHA" = "$TARGET_SHA" ]; then
    fail "健康检查失败，且版本未变化，无可回滚目标"
  fi

  log "回滚到 ${PREV_SHA:0:8} …"
  git reset --hard "$PREV_SHA"
  docker compose up -d --build || true
  fail "部署失败并已回滚到 ${PREV_SHA:0:8}"
fi

# ── 5. 收尾 ─────────────────────────────────────────────────
log "健康检查通过"
docker compose ps
docker image prune -f >/dev/null 2>&1 || true   # 只清 dangling 层，安全

log "部署完成：${PREV_SHA:0:8} → ${TARGET_SHA:0:8}"
log "聊天控制台：http://<公网IP>:${APP_PORT}/dashboard"
log "可观测看板：http://<公网IP>:${APP_PORT}/dashboard/legacy"

#!/usr/bin/env bash
#
# 幻炘AI —— 阿里云 ECS 一键初始化部署脚本
# ===================================================================
# 用法（在 ECS 实例内，root 或带 sudo 的用户执行）:
#
#   方式 A（推荐，全新机器）:
#     curl -fsSL https://raw.githubusercontent.com/kiwoen/huanxin-ai/master/tools/ecs_init.sh | sudo bash
#
#   方式 B（已 clone 到本地）:
#     sudo bash tools/ecs_init.sh
#
# 脚本会依次做:
#   1) 安装 Docker + docker compose 插件（若系统未安装）
#   2) 克隆 / 拉取最新代码（默认 /opt/huanxin-ai）
#   3) 生成 .env（含随机 HUANXIN_API_TOKEN），不覆盖已有配置
#   4) docker compose up -d --build 拉起 huanxin-ai + caddy
#   5) 本地健康探测并给出后续「控制台 / 域名」操作清单
#
# 注意:
#   - 公网 80/443 需先在阿里云控制台安全组放通（脚本结尾有说明）
#   - 域名侧（kdns.fr 注册 + Cloudflare NS/A记录）仍需你在网页操作
#
set -euo pipefail

# ── 可配置项 ─────────────────────────────────────────────
REPO_URL="https://github.com/kiwoen/huanxin-ai.git"
DEPLOY_DIR="${DEPLOY_DIR:-/opt/huanxin-ai}"   # clone 目标根目录
COMPOSE_SUBDIR="huanxin-ai"                   # 仓库内实际含 compose 的子目录
# ────────────────────────────────────────────────────────

log()  { printf '\033[36m[ecs-init]\033[0m %s\n' "$*"; }
ok()   { printf '\033[32m[ OK ]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[WARN]\033[0m %s\n' "$*"; }
die()  { printf '\033[31m[FAIL]\033[0m %s\n' "$*" >&2; exit 1; }

# 需要 root 或 sudo
if [[ $EUID -ne 0 ]] && ! command -v sudo >/dev/null 2>&1; then
  die "请用 root 运行，或安装 sudo 后: sudo bash $0"
fi
run() { if [[ $EUID -eq 0 ]]; then "$@"; else sudo "$@"; fi; }

# ── 1. Docker 安装 ────────────────────────────────────────
install_docker() {
  if command -v docker >/dev/null 2>&1; then
    ok "Docker 已安装: $(docker --version)"
  else
    log "未检测到 Docker，开始安装（官方 get.docker.com 脚本）..."
    run bash -c "$(curl -fsSL https://get.docker.com)" || die "Docker 安装失败，请手动安装"
  fi

  # docker compose 插件
  if docker compose version >/dev/null 2>&1; then
    ok "docker compose 插件已就绪: $(docker compose version | head -n1)"
  else
    log "安装 docker compose 插件..."
    if command -v apt-get >/dev/null 2>&1; then
      run apt-get update -y
      run apt-get install -y docker-compose-plugin
    elif command -v dnf >/dev/null 2>&1; then
      run dnf install -y docker-compose-plugin
    elif command -v yum >/dev/null 2>&1; then
      run yum install -y docker-compose-plugin
    else
      die "无法识别包管理器，请手动安装 docker compose 插件"
    fi
  fi

  run systemctl enable --now docker
  ok "Docker 服务已启用并启动"
}

# ── 2. 获取代码 ──────────────────────────────────────────
fetch_repo() {
  if [[ -d "$DEPLOY_DIR/.git" ]]; then
    log "已存在仓库，拉取最新: $DEPLOY_DIR"
    git -C "$DEPLOY_DIR" pull --ff-only origin master \
      || warn "git pull 失败，继续使用本地已有代码"
  else
    log "克隆仓库到 $DEPLOY_DIR"
    run mkdir -p "$(dirname "$DEPLOY_DIR")"
    git clone "$REPO_URL" "$DEPLOY_DIR" || die "克隆失败（检查网络 / 访问权限）"
  fi
}

# ── 3. 生成 .env ─────────────────────────────────────────
ensure_env() {
  local env_file="$1"
  if [[ -s "$env_file" ]] && grep -q 'HUANXIN_API_TOKEN=' "$env_file"; then
    ok ".env 已存在且含 HUANXIN_API_TOKEN，跳过生成"
    return
  fi
  log "生成 .env（含随机 HUANXIN_API_TOKEN）..."
  local token
  token="$(openssl rand -hex 24 2>/dev/null || head -c 24 /dev/urandom | xxd -p)"
  {
    echo "# 幻炘AI 部署环境变量（由 ecs_init.sh 生成）"
    echo "HUANXIN_API_TOKEN=$token"
    echo "HUANXIN_ADMIN_USER=admin"
    echo "HUANXIN_ADMIN_PASS=$token"
    echo "HUANXIN_OPEN_REGISTRATION=0"
  } > "$env_file"
  ok ".env 已写入: $env_file"
  warn "请记录 HUANXIN_API_TOKEN（同时作为初始管理员密码）: $token"
}

# ── 4. 拉起服务 ──────────────────────────────────────────
start_services() {
  local compose_dir="$1"
  log "在 $compose_dir 执行 docker compose up -d --build"
  ( cd "$compose_dir" && run docker compose up -d --build ) || die "compose 启动失败"
  ok "容器已请求拉起，等待健康就绪..."
  sleep 8
  if ( cd "$compose_dir" && docker compose ps 2>/dev/null | grep -q 'healthy\|Up' ); then
    ok "服务状态正常"
  else
    warn "服务可能尚未就绪，请查看日志: docker compose -f $compose_dir/docker-compose.yml logs -f"
  fi
  if curl -fs http://localhost:8000/health >/dev/null 2>&1; then
    ok "本地 /health 探测通过"
  else
    warn "本地 /health 未通过，稍后重试或查看日志"
  fi
}

# ── 5. 可选：阿里云 CLI 放通安全组（默认不执行）──────────
# 需提前配置 aliyun CLI: aliyun configure（AK/SK + Region）
# 取消下面行的注释并填入你的 SecurityGroupId / Region 后可自动放通 80/443
setup_sg() {
  local SG_ID="${SG_ID:-}" REGION="${REGION:-cn-hangzhou}"
  [[ -z "$SG_ID" ]] && { warn "未设置 SG_ID，跳过安全组配置（请在控制台手动放通 80/443）"; return; }
  command -v aliyun >/dev/null 2>&1 || { warn "未安装 aliyun CLI，跳过安全组配置"; return; }
  log "通过 aliyun CLI 放通安全组 $SG_ID 的 80/443..."
  aliyun ecs AuthorizeSecurityGroup \
    --RegionId "$REGION" --SecurityGroupId "$SG_ID" \
    --IpProtocol tcp --PortRange 80/80 --SourceCidrIp 0.0.0.0/0 --Policy accept
  aliyun ecs AuthorizeSecurityGroup \
    --RegionId "$REGION" --SecurityGroupId "$SG_ID" \
    --IpProtocol tcp --PortRange 443/443 --SourceCidrIp 0.0.0.0/0 --Policy accept
  ok "安全组 80/443 已放通"
}

# ── 主流程 ────────────────────────────────────────────────
main() {
  log "=== 幻炘AI ECS 一键初始化 ==="
  command -v git  >/dev/null 2>&1 || die "需要 git，请先安装"
  command -v curl >/dev/null 2>&1 || die "需要 curl"

  install_docker
  fetch_repo

  local compose_dir="$DEPLOY_DIR/$COMPOSE_SUBDIR"
  [[ -f "$compose_dir/docker-compose.yml" ]] \
    || die "未找到 $compose_dir/docker-compose.yml，请检查仓库结构"

  ensure_env "$compose_dir/.env"
  start_services "$compose_dir"
  # setup_sg   # 如需自动放通安全组，先设置 SG_ID/REGION 后取消注释

  echo
  ok "部署脚本执行完毕。"
  cat <<EOF

后续还需你完成两件事（脚本无法代劳的网页 / 控制台操作）：

[服务器侧·控制台]
  阿里云 ECS → 安全组 → 入方向放通 HTTP(80) + HTTPS(443)，授权 0.0.0.0/0
  （8000 端口不要对公网开放）

[域名侧·网页]
  1. kdns.fr 注册 huanxin.kdns.fr
  2. NS 改托管到 Cloudflare
  3. Cloudflare 加 A 记录 @ → ECS 公网 IP，开启橙色云朵（Proxied）
  4. SSL/TLS 模式设为 Full (strict)

[验证]
  curl -I https://huanxin.kdns.fr/health     # 期望 200

[查看日志]
  docker compose -f $compose_dir/docker-compose.yml logs -f caddy
EOF
}

main "$@"

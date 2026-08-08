#!/usr/bin/env bash
# ============================================================
# emperor-core 一键部署脚本
# ============================================================
# 支持平台：Render / Fly.io / Docker 本地
# 用法:
#   ./deploy.sh render    部署到 Render
#   ./deploy.sh fly       部署到 Fly.io
#   ./deploy.sh docker    本地 Docker 构建并运行
#   ./deploy.sh test      运行沙盒测试 + demo
# ============================================================
set -euo pipefail

BOLD="\033[1m"
GREEN="\033[32m"
YELLOW="\033[33m"
RED="\033[31m"
RESET="\033[0m"

log()  { echo -e "${GREEN}[emperor-core]${RESET} $*"; }
warn() { echo -e "${YELLOW}[WARNING]${RESET} $*"; }
err()  { echo -e "${RED}[ERROR]${RESET} $*"; exit 1; }

# ── 平台选择 ─────────────────────────────────────────────────
PLATFORM="${1:-docker}"
IMAGE="emperor-core:latest"
PORT="${PORT:-8000}"

# ── Docker 本地部署 ─────────────────────────────────────────
deploy_docker() {
    log "构建 Docker 镜像..."
    docker build -t "${IMAGE}" .

    log "启动容器 (端口 ${PORT})..."
    docker run -d --name emperor-core \
        -p "${PORT}:8000" \
        -e PYTHONUNBUFFERED=1 \
        -e PYTHONDONTWRITEBYTECODE=1 \
        -e EMPEROR_MODE=server \
        "${IMAGE}"

    log "等待健康检查..."
    for i in $(seq 1 30); do
        if curl -sf "http://localhost:${PORT}/health" > /dev/null 2>&1; then
            log "服务已就绪: http://localhost:${PORT}"
            log "反馈仪表盘: http://localhost:${PORT}/api/feedback/dashboard"
            log "API 文档:    http://localhost:${PORT}/docs"
            return 0
        fi
        sleep 1
    done
    warn "健康检查超时，请查看容器日志: docker logs emperor-core"
}

# ── Render 部署 ──────────────────────────────────────────────
deploy_render() {
    if ! command -v git &> /dev/null; then
        err "请先安装 git"
    fi

    log "确保所有改动已提交..."
    if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
        warn "存在未提交的改动，建议先 git commit"
    fi

    log "推送到 GitHub..."
    git push origin master || git push origin main

    log ""
    log "============================================"
    log "  Render 部署指南"
    log "============================================"
    log ""
    log "1. 打开 https://dashboard.render.com"
    log "2. New → Blueprint"
    log "3. 连接你的 GitHub 仓库"
    log "4. Render 将自动检测 render.yaml 并部署"
    log ""
    log "或者直接创建 Web Service:"
    log "  - Environment: Docker"
    log "  - Health Check Path: /health"
    log "  - Port: 8000"
    log ""
    log "免费套餐已足够运行 emperor-core。"
    log "============================================"
}

# ── Fly.io 部署 ──────────────────────────────────────────────
deploy_fly() {
    if ! command -v flyctl &> /dev/null; then
        warn "未检测到 flyctl，安装中..."
        if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" ]]; then
            powershell -Command "iwr https://fly.io/install.ps1 -useb | iex"
        else
            curl -L https://fly.io/install.sh | sh
        fi
    fi

    log "部署到 Fly.io..."
    flyctl launch --name emperor-core --region sin --now

    log ""
    log "部署完成后访问: https://emperor-core.fly.dev"
    log "反馈仪表盘: https://emperor-core.fly.dev/api/feedback/dashboard"
}

# ── 沙盒测试 + Demo ─────────────────────────────────────────
run_test() {
    log "安装依赖..."
    pip install -e ".[dev]" -q 2>&1 | tail -1

    log "运行沙盒测试..."
    python -m pytest tests/ -q --tb=short \
        -m "not network and not slow" \
        --ignore=tests/test_async_core.py \
        --ignore=tests/test_performance.py \
        --ignore=tests/test_integration.py \
        --ignore=tests/test_core.py \
        --ignore=tests/test_e2e_integration.py \
        --timeout 60 2>/dev/null || python -m pytest tests/ -q --tb=short \
            -m "not network and not slow" \
            --ignore=tests/test_async_core.py \
            --ignore=tests/test_performance.py \
            --ignore=tests/test_integration.py \
            --ignore=tests/test_core.py \
            --ignore=tests/test_e2e_integration.py 2>&1 | tail -10

    log "运行 Demo..."
    python main.py --mode demo
}

# ── 主入口 ───────────────────────────────────────────────────
case "${PLATFORM}" in
    docker)  deploy_docker ;;
    render)  deploy_render ;;
    fly)     deploy_fly ;;
    test)    run_test ;;
    *)
        echo "用法: ./deploy.sh <docker|render|fly|test>"
        exit 1
        ;;
esac

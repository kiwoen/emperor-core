# ============================================================
# huanxin-ai 部署镜像
# ------------------------------------------------------------
# 真正的服务入口链：
#   python -m huanxin.cli serve （等价于 `huanxin serve`）
#                 →  huanxin/emperor.py:Huanxin.serve()
#                 →  huanxin/court_api.py:create_app()  (FastAPI)
# 它是"完整 HUANXIN 体验"的唯一入口（自动播种 8 大臣 + 启动调度器
# + 仪表盘 /dashboard + 法庭 /court/*）。
#
# 构建：docker build -t huanxin-ai:local .
# 运行：docker run -d -p 8000:8000 -v emperor-data:/app/data huanxin-ai:local
# ============================================================
FROM python:3.11-slim

# 依赖全部为预编译 wheel，无需 build-essential，因此单阶段即可，
# 镜像更小、构建更快、也不存在 site-packages 跨阶段拷贝的坑。
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app

# curl 供 HEALTHCHECK 使用；顺手建非 root 运行账号
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r emperor && useradd -r -g emperor -d /app emperor

WORKDIR /app

# ── 依赖层（单独 COPY，改代码不会击穿这层缓存）────────────────
COPY requirements-docker.txt ./
# 走阿里云 PyPI 镜像（服务器在国内，直连 pypi.org 常超时）；
# 不再 `--upgrade pip setuptools wheel`——避免下载 pip 大文件超时导致构建失败。
RUN pip install -r requirements-docker.txt \
        -i https://mirrors.aliyun.com/pypi/simple/ \
        --timeout 120 --retries 5

# ── 应用层 ────────────────────────────────────────────────────
# pyproject.toml 仅供容器内查阅元信息/按需 pip install 使用
COPY pyproject.toml ./
COPY huanxin/ huanxin/

# `huanxin` / `emperor` 命令入口。
# 这里不用 `pip install .`：代码已经在 /app 且 PYTHONPATH=/app，再装一遍
# 只会把 12MB 源码复制进 site-packages，还多引入一个"打包阶段可能失败"的
# 风险点。直接做两行 shim，语义与 pyproject 的 [project.scripts] 完全一致
# （都是 huanxin.cli:main），却零依赖、零重复、不可能失败。
RUN printf '#!/bin/sh\nexec python -m huanxin.cli "$@"\n' > /usr/local/bin/huanxin \
    && cp /usr/local/bin/huanxin /usr/local/bin/emperor \
    && chmod +x /usr/local/bin/huanxin /usr/local/bin/emperor

# 构建期冒烟测试：入口链 import 不通就让构建当场失败，别留到运行时
# 同时触达 Huanxin 模块（不止 cli），防止"cli 能 import 但 emperor 起不来"的回归
RUN huanxin --version && python -m huanxin.cli serve --help > /dev/null \
    && python -c "import huanxin.core"

# ── 持久化目录（compose 用命名卷 emperor-data 挂到这里）────────
# huanxin.db / audit.db / approval.db / cost_records.json /
# outcome_records.json / 版本快照 全部落在 /app/data，容器重建不丢数据。
RUN mkdir -p /app/data && chown -R emperor:emperor /app

# 部署侧单一事实来源：0.0.0.0:8000 + /app/data
ENV HUANXIN_HOST=0.0.0.0 \
    HUANXIN_PORT=8000 \
    HUANXIN_DATA_DIR=/app/data \
    HUANXIN_COURT_PATH=/app/data \
    HUANXIN_MODE=server \
    HUANXIN_LLM_PROVIDER=mock

USER emperor

EXPOSE 8000

# /health 是 court_api.create_app() 里的零依赖探针，起进程即 200
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# 显式写死 host/port，避免依赖环境变量传递链。
# 用 `python -m huanxin.cli` 而不是 `huanxin`：两者等价（同一个 cli:main），
# 但模块形式不依赖控制台脚本是否注册成功，最稳。
# 需要交互时 `huanxin` 命令通常也可用：docker compose exec huanxin-ai huanxin status
CMD ["python", "-m", "huanxin.cli", "serve", "--host", "0.0.0.0", "--port", "8000"]

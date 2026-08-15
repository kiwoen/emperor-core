# emperor-core 云服务器部署手册（从零到上线）

> 目标：在一台**自己管的**云主机（腾讯云轻量 / 阿里云 ECS / 任意 Ubuntu VM）上，
> 用 Docker Compose 把 emperor-core 跑成 7×24 常驻服务，带持久化数据、
> 自动重启、HTTPS 域名访问和备份。
>
> 全文命令**可直接复制粘贴**。带 `#` 的是注释，`$` 后面是要敲的命令。
> 没有任何 API Key 也能跑通全流程（默认离线 `mock` 模式）。

---

## 0. 结论先行：这套东西到底跑的是什么

| 项 | 值 |
| --- | --- |
| 容器真正的服务入口 | `python -m jarvis.cli serve`（≡ `jarvis serve`） → `Emperor.serve()` → `court_api.create_app()`（FastAPI） |
| 监听地址 | `0.0.0.0:8000`（Dockerfile / compose / render.yaml 三处统一） |
| 健康探针 | `GET /health` → `{"status":"ok","service":"emperor-core"}` |
| 仪表盘 | `GET /dashboard` |
| 法庭接口 | `GET /court/summary`、`/court/ministers` … |
| 接口文档 | `GET /docs`（FastAPI 自带 Swagger） |
| 数据落盘 | 容器内 `/app/data`，由命名卷 `emperor-data` 承载 |

启动时系统会**自动播种 8 位大臣**并拉起后台调度器（周期性进化 + 周期性任务），
即"完整 JARVIS 体验"。

---

## 1. 买机器（10 分钟）

### 1.1 推荐配置

| 选项 | 推荐值 | 说明 |
| --- | --- | --- |
| 平台 | **腾讯云轻量应用服务器**（国内最省事）／阿里云 ECS／Vultr、Hetzner（海外，免备案） |
| 镜像 | **Ubuntu 22.04 LTS 64 位** | 本文命令基于它；24.04 同样适用 |
| 规格 | **2 核 2G 起**（推荐 2C4G） | 2C2G 可跑；大臣多 / 开真实 LLM 时建议 4G |
| 磁盘 | 40G SSD | 审计库会持续增长 |
| 带宽 | 3–5 Mbps 或按流量 | 自用足够 |
| 地域 | 国内业务选就近地域；要免备案选**香港/新加坡** | 国内 80/443 绑域名需备案 |

> 国内厂商的"轻量应用服务器"新用户常年有 60–120 元/年的活动机型，够用。

### 1.2 创建实例时要做的两件事

1. **设置 root 密码**或**绑定 SSH 密钥**（推荐密钥）。
2. **防火墙/安全组放行端口**（控制台 → 防火墙 → 添加规则）：

| 协议 | 端口 | 用途 |
| --- | --- | --- |
| TCP | 22 | SSH 登录 |
| TCP | 80 | HTTP（Caddy 申请证书 + 跳转 HTTPS） |
| TCP | 443 | HTTPS 正式访问 |
| TCP | 8000 | 直连调试用（**配好域名后建议关掉**） |

---

## 2. 登录服务器 + 装 Docker（5 分钟）

```bash
# 本机终端（把 IP 换成你的公网 IP）
$ ssh root@123.45.67.89
```

```bash
# ── 以下都在服务器上执行 ──
# 1) 更新系统 & 装基础工具
$ apt update && apt install -y curl git ca-certificates

# 2) 装 Docker（官方一键脚本，自带 docker compose 插件）
$ curl -fsSL https://get.docker.com | sh

# 国内机器如果上面这步很慢，用镜像源：
# $ curl -fsSL https://get.docker.com | sh -s -- --mirror Aliyun

# 3) 开机自启 + 立即启动
$ systemctl enable --now docker

# 4) 验证（两条都要有版本号）
$ docker --version
$ docker compose version
```

> 如果 `docker compose version` 报错（老版本只有 `docker-compose`），执行：
> `apt install -y docker-compose-plugin`

**（可选）国内拉镜像加速** —— `python:3.11-slim` 拉不动时：

```bash
$ mkdir -p /etc/docker && cat > /etc/docker/daemon.json <<'EOF'
{
  "registry-mirrors": ["https://docker.m.daocloud.io", "https://dockerproxy.com"]
}
EOF
$ systemctl restart docker
```

---

## 3. 拉代码（2 分钟）

```bash
$ mkdir -p /srv && cd /srv
$ git clone https://github.com/<你的账号>/emperor-core.git
$ cd /srv/emperor-core

# ⚠️ 关键自检：这三个文件必须存在，否则第 4 步构建会失败
$ ls -l Dockerfile docker-compose.yml requirements-docker.txt
```

> **历史坑（已修）**：仓库 `.gitignore` 曾把 `Dockerfile` 和
> `docker-compose.yml` 列为忽略项，导致 `git clone` 下来根本没有构建文件。
> 现已放行；**请确认这三个文件已经提交进仓库**，否则用下面的 scp 方式传。

**没有 Git 仓库？** 从本机直接推上去（在**本机**执行）：

```bash
# Windows 用 Git Bash / PowerShell 均可
$ scp -r "D:/AI自我进化/emperor-core" root@123.45.67.89:/srv/emperor-core
```

---

## 4. 一条命令拉起（首次构建 3–6 分钟）

```bash
$ cd /srv/emperor-core
$ docker compose up -d --build
```

看日志确认启动完成：

```bash
$ docker compose logs -f
# 出现下面这两行就是起来了（Ctrl+C 退出看日志，容器继续跑）：
#   [Emperor] API + Dashboard → http://0.0.0.0:8000
#   Uvicorn running on http://0.0.0.0:8000
```

看健康状态（等 20–30 秒后应为 `healthy`）：

```bash
$ docker compose ps
# STATUS 列应显示： Up 30 seconds (healthy)
```

---

## 5. 验证（1 分钟）

```bash
# ① 健康探针 —— 这条必须返回 200，是所有自动化的基准
$ curl -f http://localhost:8000/health
# {"status":"ok","service":"emperor-core"}

# ② 仪表盘（应输出一大段 HTML）
$ curl -s http://localhost:8000/dashboard | head -c 300

# ③ 法庭状态：确认 8 位大臣已自动播种
$ curl -s http://localhost:8000/court/ministers

# ④ 接口文档
$ curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/docs
```

浏览器直接看（域名配好前先用 IP，注意安全组已放行 8000）：

- 仪表盘：`http://123.45.67.89:8000/dashboard`
- 接口文档：`http://123.45.67.89:8000/docs`

---

## 6. 绑域名 + 自动 HTTPS（Caddy，10 分钟）

Caddy 会**自动申请并续期 Let's Encrypt 证书**，比 Nginx + certbot 省事得多。

### 6.1 先做 DNS 解析

在域名服务商控制台加一条记录：

| 类型 | 主机记录 | 记录值 |
| --- | --- | --- |
| A | `emperor` | `123.45.67.89`（你的公网 IP） |

等 1–5 分钟生效，验证：`ping emperor.example.com` 能解析到你的 IP。

### 6.2 装 Caddy 并配置反代

```bash
$ apt install -y debian-keyring debian-archive-keyring apt-transport-https
$ curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
$ curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    | tee /etc/apt/sources.list.d/caddy-stable.list
$ apt update && apt install -y caddy
```

写配置（**把域名和邮箱换成你自己的**）：

```bash
$ cat > /etc/caddy/Caddyfile <<'EOF'
# ── emperor-core 反向代理 + 自动 HTTPS ──
emperor.example.com {
    # 证书申请失败通知邮箱
    tls you@example.com

    encode gzip

    # 上游就是 compose 暴露在宿主机 8000 的服务
    reverse_proxy localhost:8000 {
        health_uri /health
        health_interval 30s
    }

    log {
        output file /var/log/caddy/emperor.log
        format console
    }
}
EOF

$ caddy validate --config /etc/caddy/Caddyfile   # 语法自检
$ systemctl reload caddy                         # 生效（首次可用 restart）
```

验证：

```bash
$ curl -f https://emperor.example.com/health
# {"status":"ok","service":"emperor-core"}
```

### 6.3 收口 8000 端口（重要）

域名通了之后，回云厂商控制台**删掉 8000 的放行规则**，只留 22/80/443。
容器仍监听 8000，但只能被本机 Caddy 访问，外网打不到。

> 想加个访问口令？在 Caddyfile 的站点块里加：
> ```
> basic_auth {
>     admin <用 `caddy hash-password` 生成的哈希>
> }
> ```

---

## 7. 数据持久化与备份

### 7.1 数据在哪

`docker-compose.yml` 已把命名卷 `emperor-data` 挂到容器 `/app/data`，
容器**删了重建数据也不丢**。里面是：

| 文件 | 内容 |
| --- | --- |
| `jarvis.db` | 法庭主库：大臣、基因、进化历史、任务记录 |
| `audit.db` | **不可篡改审计流水**（务必备份） |
| `approval.db` | 人类审批（HITL）记录 |
| `cost_records.json` | 逐次调用成本 |
| `outcome_records.json` | 单次成功成本（cost-per-success） |
| `templates/`、版本快照 | 自适应提示词模板、上下文回滚快照 |

查看卷与实际内容：

```bash
$ docker volume inspect emperor-data
$ docker compose exec emperor-core ls -lh /app/data
```

### 7.2 手动备份 / 恢复

```bash
# 备份（当前目录生成 emperor-data.tar.gz）
$ cd /srv/emperor-core
$ docker run --rm -v emperor-data:/data -v $(pwd):/backup busybox \
    tar czf /backup/emperor-data.tar.gz -C /data .

# 恢复（会覆盖卷内同名文件；建议先停服务）
$ docker compose stop
$ docker run --rm -v emperor-data:/data -v $(pwd):/backup busybox \
    tar xzf /backup/emperor-data.tar.gz -C /data
$ docker compose start
```

### 7.3 每天凌晨 3 点自动备份（保留 14 天）

```bash
$ mkdir -p /srv/backups
$ cat > /usr/local/bin/emperor-backup.sh <<'EOF'
#!/bin/sh
# emperor-core 数据卷每日备份，保留最近 14 份
set -e
STAMP=$(date +%Y%m%d-%H%M)
OUT=/srv/backups
mkdir -p "$OUT"
docker run --rm -v emperor-data:/data -v "$OUT":/backup busybox \
    tar czf "/backup/emperor-data-$STAMP.tar.gz" -C /data .
# 清理 14 天前的备份
find "$OUT" -name 'emperor-data-*.tar.gz' -mtime +14 -delete
EOF
$ chmod +x /usr/local/bin/emperor-backup.sh

# 写入 crontab
$ ( crontab -l 2>/dev/null; echo "0 3 * * * /usr/local/bin/emperor-backup.sh >> /var/log/emperor-backup.log 2>&1" ) | crontab -
$ crontab -l    # 确认已写入
```

> 更稳的做法：把 `/srv/backups` 再同步到对象存储（腾讯云 COS / 阿里云 OSS），
> 一条 `coscmd upload` 或 `ossutil cp` 加到脚本末尾即可。

---

## 8. 接真实大模型（可选，随时切换）

**不做这一步系统也完整可用**（`EMPEROR_LLM_PROVIDER=mock`，离线确定性推理）。
要接真模型，**不用改代码、不用重建镜像**：

```bash
$ cd /srv/emperor-core
$ cat > .env <<'EOF'
# 只填你有的那个即可
EMPEROR_LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxx
# OPENAI_API_KEY=sk-xxxx
# ANTHROPIC_API_KEY=sk-ant-xxxx
EOF
$ chmod 600 .env          # 只有 root 能读
```

然后打开 `docker-compose.yml`，把 `environment:` 里这几行的注释去掉：

```yaml
      EMPEROR_LLM_PROVIDER: deepseek
      DEEPSEEK_API_KEY: ${DEEPSEEK_API_KEY:-}
```

重启生效（`.env` 会被 compose 自动读取）：

```bash
$ docker compose up -d
$ docker compose logs --tail 30
```

> **绝不要**把 Key 直接写进 `docker-compose.yml` 或提交到 Git —— 仓库的
> `.gitignore` / `.dockerignore` 已经排除 `.env`。

---

## 9. 日常运维

### 9.1 常用命令

```bash
$ docker compose ps                 # 状态 + healthy
$ docker compose logs -f --tail 100 # 实时日志
$ docker compose restart            # 重启
$ docker compose down               # 停止并删容器（数据卷保留）
$ docker compose up -d --build      # 改了代码后重新构建上线
$ docker stats emperor-core         # 实时 CPU / 内存占用
```

### 9.2 自动重启

`docker-compose.yml` 里已配 `restart: unless-stopped`：
进程崩溃、Docker 重启、**服务器重启**后都会自动把服务拉回来。
（Docker 本身的开机自启已在第 2 步 `systemctl enable docker` 完成。）

### 9.3 代码更新上线

```bash
$ cd /srv/emperor-core
$ git pull
$ docker compose up -d --build
$ curl -f http://localhost:8000/health   # 必须 200 才算上线成功
```

### 9.4 （可选）Watchtower 自动更新

推镜像到仓库的团队可以让 Watchtower 自动拉新镜像重启：

```bash
$ docker run -d --name watchtower --restart unless-stopped \
    -v /var/run/docker.sock:/var/run/docker.sock \
    containrrr/watchtower --cleanup --interval 3600 emperor-core
```

> 本地 `build:` 出来的镜像不会被 Watchtower 更新，它只跟踪远端仓库标签。
> 手动构建流程用 9.3 就够了。

---

## 10. 监控与回滚

### 10.1 最小可用监控（1 分钟）

```bash
# 每 5 分钟探一次 /health，失败就自动重启并记日志
$ cat > /usr/local/bin/emperor-watch.sh <<'EOF'
#!/bin/sh
if ! curl -fsS --max-time 10 http://localhost:8000/health > /dev/null; then
    echo "$(date '+%F %T') health FAILED, restarting" >> /var/log/emperor-watch.log
    cd /srv/emperor-core && docker compose restart
fi
EOF
$ chmod +x /usr/local/bin/emperor-watch.sh
$ ( crontab -l 2>/dev/null; echo "*/5 * * * * /usr/local/bin/emperor-watch.sh" ) | crontab -
```

外部拨测（免费）：[UptimeRobot](https://uptimerobot.com) / 腾讯云云拨测
→ 监控 `https://emperor.example.com/health`，异常邮件/微信告警。

Docker 自带的 healthcheck 也一直在跑：

```bash
$ docker inspect --format '{{json .State.Health}}' emperor-core
```

### 10.2 可回滚的发布姿势

**关键：用固定 tag 留住上一个好版本。**

```bash
# 发布 v1（打固定 tag 并留档）
$ cd /srv/emperor-core
$ docker build -t emperor-core:v1 .
$ docker tag emperor-core:v1 emperor-core:local
$ docker compose up -d

# 发布 v2
$ git pull
$ docker build -t emperor-core:v2 .
$ docker tag emperor-core:v2 emperor-core:local
$ docker compose up -d
$ curl -f http://localhost:8000/health || echo "坏了，执行回滚"

# ── 回滚到 v1（10 秒内完成）──
$ docker tag emperor-core:v1 emperor-core:local
$ docker compose up -d --force-recreate
$ curl -f http://localhost:8000/health
```

数据回滚见 7.2（先停服务，再解压备份，再启动）。

---

## 11. 成本与免费替代

| 方案 | 月成本 | 持久化 | 常驻不休眠 | 适用 |
| --- | --- | --- | --- | --- |
| **腾讯云轻量 2C2G**（本文方案） | ￥5–15（年付活动价） | ✅ 命名卷 | ✅ | **推荐**，自进化需要长期累积数据 |
| 阿里云 ECS 突发性能 t6 | ￥15–30 | ✅ | ✅ | 同上 |
| Hetzner CX22 / Vultr | €4 / $5 | ✅ | ✅ | 海外免备案，拉镜像快 |
| Render 免费套餐（`render.yaml`） | ￥0 | ❌ **重启即丢** | ❌ 15 分钟休眠 | 只适合演示；审计/审批数据会丢 |
| Fly.io 免费额度 | ￥0 起 | 需挂 volume | ✅ | 会用 flyctl 的可选 |

> **为什么不推荐免费 PaaS 长期跑**：emperor-core 靠 `audit.db`（不可篡改审计）
> 和 `jarvis.db`（基因/进化历史）**跨重启累积进化**。免费套餐无持久盘 + 定时
> 休眠会让调度器被打断、历史被清零，等于每次都从零开始。自管 VM + 命名卷才
> 是这套系统的正确形态。

---

## 12. 故障排查

| 现象 | 排查命令 / 处理 |
| --- | --- |
| `curl /health` 连不上 | `docker compose ps` 看是否 Up；`docker compose logs --tail 50` 看报错 |
| 状态一直 `starting` | 冷启动要 10–20 秒；超过 1 分钟看日志是否有 traceback |
| 状态 `unhealthy` | 进容器手测：`docker compose exec emperor-core curl -v http://localhost:8000/health` |
| 端口被占 | `ss -lntp \| grep 8000`，杀掉占用进程或改 compose 的 `"8001:8000"` |
| 构建时 pip 超时 | 配 pip 镜像：Dockerfile 的 pip 命令加 `-i https://pypi.tuna.tsinghua.edu.cn/simple` |
| 内存不够被 OOM kill | `dmesg \| grep -i kill`；升到 4G，或调小 compose 里的 memory 上限 |
| 域名 HTTPS 不通 | `systemctl status caddy`、`journalctl -u caddy -n 50`；确认 80/443 已放行且 DNS 已生效 |
| 想进容器看看 | `docker compose exec emperor-core sh` |
| 磁盘满了 | `docker system df`，清理：`docker system prune -a`（**不会**动命名卷） |

---

## 13. 一页速查（部署完存起来）

```bash
# 上线
cd /srv/emperor-core && docker compose up -d --build

# 体检
curl -f http://localhost:8000/health && docker compose ps

# 看日志
docker compose logs -f --tail 100

# 备份
docker run --rm -v emperor-data:/data -v $(pwd):/backup busybox \
  tar czf /backup/emperor-data.tar.gz -C /data .

# 回滚
docker tag emperor-core:v1 emperor-core:local && docker compose up -d --force-recreate
```

| 访问入口 | URL |
| --- | --- |
| 健康检查 | `https://emperor.example.com/health` |
| 仪表盘 | `https://emperor.example.com/dashboard` |
| 接口文档 | `https://emperor.example.com/docs` |
| 法庭总览 | `https://emperor.example.com/court/summary` |

# emperor-core 学生实验部署 Runbook —— 阿里云版

> 面向：**学生个人实验**、**公网 IP 直连（不买域名、暂不备案）**、**有阿里云学生认证**。
> 目标：用阿里云「云工开物」学生计划 + 毕业设计包，把 emperor-core 一键 `docker compose up` 跑起来。
> 预算：毕业设计包 ¥339（含 850 元券，约覆盖 4 个月 17 天的 2C4G 实例），之后按月续费或降配。

---

## 0. 先读「不踩坑」清单

| 坑 | 正确做法 |
|---|---|
| ❌ 走 `free.aliyun.com` 个人免费试用（1GiB 内存+宝塔镜像） | ✅ 走 `university.aliyun.com` 云工开物学生计划 |
| ❌ 镜像选「宝塔 / Windows Server / CentOS 7」 | ✅ 选 **Ubuntu 24.04 LTS**（系统镜像，非应用镜像） |
| ❌ 选 1 核 2G | ✅ 选 **2 核 4G**（emperor-core slim 镜像 + 运行时 + SQLite 容易吃满 1G） |
| ❌ 忘记开费用预警，流量超 20GB 被扣费 | ✅ 控制台开「月消费超 ¥10 短信通知」 |
| ❌ 把真实 Key 写进 `docker-compose.yml` | ✅ 只写进 `.env`（已被 `.gitignore` 忽略） |

---

## 1. 学生认证（云工开物入口）

1. 浏览器开 **https://www.aliyun.com** → 右上角**登录**（用你打算实名的支付宝账号）
2. 用户中心 → **实名认证** → 选「支付宝快捷认证」（个人实名，约 1 分钟秒过）
3. 实名后开 **https://university.aliyun.com** → 「我是学生」→ **学信网认证**（支付宝扫码，1–3 分钟）
4. 认证通过后会看到 **300 元代金券**领取按钮 + **学生专属产品卡片**（轻量 2C2G / 2C4G 月付 9.9 元等）
5. 若页面打不开：换 Chrome 无痕窗口，或先完成上面 2–3 步再访问

> 没完成「学生认证（学信网）」之前，看不到任何学生权益，这是设计如此。

---

## 2. 下单：毕业设计包 + ECS 配置

1. 在 `university.aliyun.com` 找到 **毕业设计包 ¥339 / 6 个月 / 850 元券** → 立即购买
2. 进入 ECS 购买页，按下表配置：

| 配置项 | 选值 |
|---|---|
| 地域 | **华东1（杭州）**（离华南用户近；也可选华南1 深圳） |
| 实例规格 | **2 vCPU 4 GiB（经济型 e）** |
| 镜像 | **Ubuntu 24.04 64 位**（系统镜像，不是「应用镜像/宝塔」） |
| 预装应用 | **Docker**（勾选，省去手动装） |
| 公网带宽 | **按流量计费**，启用「每月免费公网流量 20GB」 |
| 安全组 | 放通 **TCP 22（SSH）+ TCP 8000（emperor-core）**；80/443 暂不开 |
| 登录凭证 | 选「自定义密码」，设 root 密码并记下来 |

> **为什么 Ubuntu 24.04 比 22.04 还好**：24.04 自带 Python 3.12，正好对应我们 `Dockerfile` 的 `FROM python:3.12-slim`，版本链路最干净；安全维护期到 2029 年。
>
> **850 元券覆盖时长**：2C4G 经济型约 ¥0.254/小时 ≈ ¥182.5/月 → 850 ÷ 182.5 ≈ **4 个月 17 天**免费用。之后按 ¥182.5/月续费，或见第 9 节降配。

下单后约 30 秒，控制台实例状态变 **「运行中」**，记下**公网 IP**。

---

## 3. 流量配额与监控（关键）

- 内地地域实例：**每月免费公网流量 20GB**，超出按 ¥0.8/GB 从账户余额扣（自动走 CDT 共享流量包）。
- 消耗量级参考：

| 场景 | 月流量 |
|---|---|
| 首次 docker 拉镜像（一次性） | ~800MB–1.2GB |
| 平时稳态（/health + 偶尔看仪表盘） | ~1–3GB |
| 接远程 LLM（每天 ≤100 次调用） | ~50–500MB |
| LLM 流式暴走（每天数千次长输出） | ~3–8GB |

→ **正常实验节奏 20GB 足够**；唯一大头是首次拉镜像（一次性，已在内）。

**必做**：控制台 → 费用 → 费用预警 → 设置「月消费超 **¥10** 短信通知」，超流量立刻知道。

---

## 4. 第一次登录 + Docker 校验

```bash
# 本机终端（不是服务器）
ssh root@<你的公网IP>

# 进服务器后，校验预装 Docker 真的可用
docker --version            # 应有输出（如 Docker version 29.x）
systemctl status docker     # 应显示 active (running)
docker compose version      # ⚠️ 有些镜像只装了 docker，没装 compose 插件
```

若 `docker compose` 报 `command not found`，补装插件：

```bash
apt-get update && apt-get install -y docker-compose-plugin
docker compose version      # 现在应有输出
```

确认无误后，创建部署目录并放行防火墙（阿里云安全组已在控制台放通，这里确认本地没挡）：

```bash
mkdir -p /srv/emperor-core
cd /srv/emperor-core
```

---

## 5. 部署 emperor-core

```bash
# 拉代码（约 10MB）
git clone https://github.com/kiwoen/emperor-core.git .

# 一键构建并后台拉起（首次会拉基础镜像，约 1–2 分钟）
docker compose up -d --build

# 看启动日志，等出现 "Application startup complete" 或 "/health 200"
docker compose logs -f
# Ctrl+C 退出日志（容器仍在后台跑）
```

验证（在**服务器本机**执行，确认服务起来了）：

```bash
curl -f http://localhost:8000/health
# 应返回：{"status":"ok","service":"emperor-core"}

curl -s http://localhost:8000/dashboard | head -c 200
# 应是一大段 HTML（仪表盘已渲染）
```

再从**你自己的电脑**浏览器访问：

```
http://<公网IP>:8000/health
http://<公网IP>:8000/dashboard
```

能看到仪表盘 = 正式上线成功。

健康状态查看：

```bash
docker compose ps
# 状态应为 healthy（healthcheck 打 /health）
```

---

## 6. 设置访问令牌（强烈建议）

8000 端口直接暴露公网，建议至少加一层 Token 鉴权（我们已实现，由 `EMPEROR_API_TOKEN` 控制）。

```bash
cd /srv/emperor-core

# 1) 生成强令牌
openssl rand -hex 24
# 复制输出的一串十六进制

# 2) 写进 .env（已被 .gitignore 忽略，不会提交；compose 自动读取）
cat > .env <<'EOF'
EMPEROR_API_TOKEN=把上面那串粘这里
EMPEROR_LLM_PROVIDER=mock
EOF
chmod 600 .env

# 3) 重新拉起使令牌生效
docker compose up -d
```

验证鉴权：

```bash
curl -i http://localhost:8000/court/ministers        # 应 401（无令牌）
curl -H "Authorization: Bearer <token>" http://localhost:8000/court/ministers   # 应 200
# 浏览器开仪表盘：http://<公网IP>:8000/dashboard?token=<token>
```

> 不设置 `EMPEROR_API_TOKEN` = 完全不鉴权（向后兼容，仅限内网/实验）。设置后 `/health` 探针**始终放行**，不影响健康检查。

---

## 7. 接真实大模型（可选）

默认 `mock` 离线即可完整运行。接真实 LLM **不改代码、只改 `.env`**。

根据实测额度情况（截至 2026-08）：
- **NVIDIA NIM**：目前唯一实测有额度的端点，推荐先试。
- OpenAI：账户无 credits；DeepSeek：需余额；豆包：需在 Ark 控制台建接入点拿 `ep-xxxx`。

在 `.env` 追加（不要写进 `docker-compose.yml`）：

```bash
cat >> .env <<'EOF'
EMPEROR_LLM_PROVIDER=nvidia
NVIDIA_API_KEY=你的_NVIDIA_API_KEY
EOF
docker compose up -d
```

可用供应商值：`deepseek` / `openai` / `nvidia` / `anthropic` 等（见 `.env.example`）。

> 接真实 LLM 会消耗公网流量（每次调用上行 prompt + 下行 answer，约 5–15KB/次），正常用量远低于 20GB 月配额。

---

## 8. 数据备份与恢复

全部持久化数据（jarvis.db / audit.db / approval.db / outcome_records.json / 版本快照）都在命名卷 `emperor-data` → 容器内 `/app/data`。

**备份**（服务器上执行，tar 打包卷到当前目录）：

```bash
docker run --rm -v emperor-data:/data -v $(pwd):/backup busybox \
  tar czf /backup/emperor-data-$(date +%Y%m%d).tar.gz -C /data .
ls -lh emperor-data-*.tar.gz
```

**恢复**（换机器或误删后）：

```bash
docker run --rm -v emperor-data:/data -v $(pwd):/backup busybox \
  tar xzf /backup/emperor-data-YYYYMMDD.tar.gz -C /data
docker compose restart
```

**每日自动备份**（加入 crontab -e）：

```cron
0 4 * * * cd /srv/emperor-core && docker run --rm -v emperor-data:/data -v $(pwd):/backup busybox tar czf /backup/emperor-data-$(date +\%Y\%m\%d).tar.gz -C /data . && find /srv/emperor-core/emperor-data-*.tar.gz -mtime +14 -delete
```

> `docker compose down` **不会**删卷；只有 `docker compose down -v` 或手动 `docker volume rm emperor-data` 才清数据。

---

## 9. 4 个月 17 天后的预算决策

850 元券耗尽后，实例自动按 ¥0.254/小时（≈¥182.5/月）扣费。学生长期付不现实，提前 2 周选一条路：

- **A. 计划停**：打包 `/app/data` 备份（第 8 节）→ `docker compose down -v` → 释放实例。以后想续随时重建。
- **B. 降配继续**：停实例 → 改成 **2 核 2G 经济型**（≈¥89/月）→ 重启，`emperor-data` 卷自动挂载回来。
- **C. 换新配置重建**：`docker volume create emperor-data2` 后迁移数据，旧实例释放。

> 实例**关机不收费**（仅停公网 IP 保留费，可忽略），所以实验间隙可随时 `docker compose stop` 省流量与算力。

---

## 10. 常见问题排查

| 现象 | 原因 / 解决 |
|---|---|
| `ssh` 连不上 | 安全组没放 22；或实例刚创建需等「运行中」；确认公网 IP 正确 |
| `docker compose` 命令不存在 | 装插件：`apt-get install -y docker-compose-plugin` |
| `curl /health` 一直 401 | 你设了 `EMPEROR_API_TOKEN` 但忘了带令牌；`/health` 本身不需要令牌，401 说明打错端口或路径 |
| 容器状态 `unhealthy` | 看 `docker compose logs`；常见是首次构建镜像层没拉全，重跑 `docker compose up -d --build` |
| 仪表盘打不开 | 浏览器访问 `http://<IP>:8000/dashboard`；若设了令牌需加 `?token=<token>` |
| 流量超标扣费 | 开了费用预警会短信通知；检查是否在暴跑 LLM 流式；降调用频率或关实例 |
| `git clone` 慢/失败 | 检查服务器能否联网；或本机先 `git clone` 再 `scp -r` 传上去 |

---

## 附：一键复查清单

- [ ] 学生认证（学信网）完成，领到 300 元券 + 毕业设计包 ¥339
- [ ] ECS：华东1杭州 / 2C4G 经济型 e / **Ubuntu 24.04** / 预装 Docker
- [ ] 安全组放通 22 + 8000
- [ ] 费用预警「月超 ¥10 短信」已开
- [ ] `ssh` 进去，`docker` + `docker compose` 均可用
- [ ] `git clone` + `docker compose up -d --build` 成功
- [ ] 本机 `curl /health` 返回 200
- [ ] （建议）`.env` 设 `EMPEROR_API_TOKEN`，鉴权生效
- [ ] 备份脚本进 crontab
- [ ] 记好「4 个月 17 天后」的预算决策点

# emperor-core 部署故障排查手册（学生实验 / 阿里云 VM）

> 适用：阿里云学生机 + `docker compose up -d --build` 部署 emperor-core。
> 用法：照「症状」找小节，先跑「诊断命令」看输出，再照「修复」做。

---

## 0. 最快定位：先看容器三态

```bash
docker compose ps          # 看 STATUS：Up(healthy) / Up / Restarting / Exit
docker compose logs --tail 50   # 看最近报错
docker compose logs -f     # 实时看（Ctrl+C 退出）
```

- `Up (healthy)` → 服务正常，问题在外部（安全组/网络/浏览器）
- `Restarting` / `Exit` → 容器内部崩了，看 logs
- 构建卡住 → 看第 5 节

---

## 1. SSH 连不上

**诊断**
```bash
# 本机终端
ssh -v root@<公网IP>        # -v 看卡在哪一步
ping <公网IP>               # 看网络通不通
```

**可能原因 / 修复**
| 现象 | 修复 |
|---|---|
| `Connection timed out` | ① 安全组没放 22 → 控制台「网络与安全组」加 22/TCP/0.0.0.0/0；② 实例没「运行中」 |
| `Permission denied (publickey)` | 你买时选了「密钥对」而非密码 → 控制台「重置密码」设 root 密码，或改用密钥 |
| `Permission denied (password)` | 密码错 → 再「重置密码」一次并**重启实例**生效 |
| `Connection refused` | 实例在但 SSH 没起（极少见）→ 控制台「重启」 |
| 卡在 `debug1: Connecting` | 本地防火墙 / 公司网络阻断 22 → 换网络或手机热点试 |

---

## 2. `docker` / `docker compose` 命令不存在

**诊断**
```bash
docker --version            # 看是否装了
docker compose version      # 看 compose 插件是否装
which docker                # 空=没装
```

**修复**
```bash
# 情况 A：完全没装 Docker（你买时没勾预装）
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker

# 情况 B：docker 有，但 compose 插件缺（最常见）
apt-get update && apt-get install -y docker-compose-plugin
docker compose version      # 现在应有输出
```

---

## 3. 浏览器访问 http://<IP>:8000 打不开

**诊断**
```bash
# 服务器本机先验证服务自己活不活
curl -f http://localhost:8000/health     # 200 = 服务正常，问题在外部
curl -f http://127.0.0.1:8000/health
```

**修复**
| 现象 | 修复 |
|---|---|
| 本机 `curl` 也失败 | 服务没起 → 看第 0/5 节 |
| 本机 200，外网打不开 | ① **安全组没放 8000** → 控制台加 8000/TCP/0.0.0.0/0（最常见！）；② 本地浏览器缓存，强制刷新；③ 公司网络屏蔽非常规端口，换手机热点 |
| `Connection refused` | 容器没监听 0.0.0.0 → 确认 compose 里 `EMPEROR_HOST=0.0.0.0`（默认已是） |
| 一直转圈 | 防火墙/安全组只放了 22 → 补 8000 |

> 阿里云默认只放 22，**8000 必须手动加**，这是头号坑。

---

## 4. `/health` 一直 unhealthy / 容器反复重启

**诊断**
```bash
docker compose ps
docker compose logs --tail 50
```

**常见根因**
| 日志关键词 | 含义 / 修复 |
|---|---|
| `Address already in use` | 8000 被占用 → `docker compose down` 再 `up`；或别的进程占了端口 `lsof -i:8000` |
| `No module named 'xxx'` | 镜像构建时依赖没装全 → `docker compose up -d --build` 重构建 |
| `EMPEROR_DATA_DIR` 权限拒绝 | 卷挂载权限问题 → `chmod -R 777 /app/data` 临时放行（测试用） |
| `sqlite3.OperationalError` | 数据库锁 → 停掉其他实例 `docker compose down` 再起 |
| 启动慢（模型加载） | 首次启动要拉依赖/初始化，等 30–60s 再看；`start_period` 已设 20s |

---

## 5. 构建失败 / 镜像拉不动

**诊断**
```bash
docker compose build --no-cache    # 干净重建看完整报错
docker images                      # 看是否有 emperor-core:local
df -h                              # 看磁盘是否满
```

**修复**
| 现象 | 修复 |
|---|---|
| `failed to fetch ... python:3.12-slim` | 网络拉不到基础镜像 → 检查服务器能否联网 `curl -I https://registry-1.docker.io`；或换国内镜像源（阿里云容器镜像服务 ACR 加速器） |
| `no space left on device` | 磁盘满 → `docker system prune -a` 清掉悬空镜像；系统盘 40G 一般够，别堆太多旧镜像 |
| 构建超慢 | 首次构建本就慢（拉基础层 ~120MB + pip 装依赖）；耐心等，或 `docker compose up -d --build` 后台跑 |
| `ERROR: failed to solve` | 看具体 step；通常是 Dockerfile 里某条命令失败，贴给我 |

**阿里云镜像加速（拉取提速）**
```bash
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<'EOF'
{
  "registry-mirrors": ["https://<你的阿里云加速地址>.mirror.aliyuncs.com"]
}
EOF
systemctl restart docker
```
> 加速地址在阿里云控制台「容器镜像服务」→「镜像加速器」里拿（免费）。

---

## 6. 设了 Token 后 401 / 403 问题

**诊断**
```bash
curl -i http://localhost:8000/health              # /health 永远 200，不鉴权
curl -i http://localhost:8000/court/ministers     # 应 401（无令牌）
curl -H "Authorization: Bearer <token>" http://localhost:8000/court/ministers   # 应 200
```

**修复**
| 现象 | 修复 |
|---|---|
| `/health` 也 401 | 打错路径/端口；`/health` 不受令牌保护，401 说明访问的不是本服务 |
| 带了 Bearer 仍 401 | ① `.env` 里 `EMPEROR_API_TOKEN` 值不对/有空格 → 检查 `cat .env`；② compose 没重起 `docker compose up -d` 使 `.env` 生效 |
| 浏览器仪表盘 401 | 加 `?token=<token>` 参数，或先用无令牌模式（`EMPEROR_API_TOKEN` 留空） |
| 想临时关闭鉴权 | `.env` 里 `EMPEROR_API_TOKEN=` 留空 → `docker compose up -d` |

---

## 7. 接真实 LLM 调用失败

**诊断**：看容器日志里 LLM 相关报错。
```bash
docker compose logs --tail 50 | grep -iE "llm|openai|deepseek|nvidia|rate|401|403|timeout"
```

**修复**
| 现象 | 修复 |
|---|---|
| `401 Unauthorized` | Key 错/没填 → `.env` 里 `OPENAI_API_KEY=` / `DEEPSEEK_API_KEY=` / `NVIDIA_API_KEY=` 填真实值，`docker compose up -d` |
| `429 / RateLimit` | 额度用完 → 换供应商或等额度恢复；NVIDIA NIM 当前实测有额度可试 |
| `timeout` | 服务器到 LLM 端点网络慢/不通 → 确认能 `curl -I https://api.nvidia.com`；或调大超时 |
| 仍走 mock | 没填 Key 或 Key 变量名不对 → `.env` 填 `NVIDIA_API_KEY=...` 并设 `OPENAI_FALLBACK_PROVIDERS=nvidia`，再 `docker compose up -d`；确认日志出现 `LLM running in LIVE mode` |

---

## 8. 流量超标 / 被扣费

**诊断**：阿里云控制台 → 费用 → 账单详情 → 看「公网流量」一项。

**修复**
- 开「费用预警：月超 ¥10 短信通知」（控制台 → 费用 → 费用预警）
- 平时不跑实验：`docker compose stop` 关机（不收费，只留公网 IP 费可忽略）
- 流量暴走排查：`docker compose logs` 看是否在狂刷 LLM 调用；降频率或加限流
- 超标单价约 ¥0.8/GB，不会一夜爆扣；看到告警再处理即可

---

## 9. 数据丢失 / 想迁移

**确认数据在哪**
```bash
docker volume inspect emperor-data     # 看挂载点
docker run --rm -v emperor-data:/data busybox ls -lh /data   # 列卷内文件
```

**备份**（tar 到当前目录）
```bash
docker run --rm -v emperor-data:/data -v $(pwd):/backup busybox \
  tar czf /backup/emperor-data-$(date +%Y%m%d).tar.gz -C /data .
```

**恢复 / 迁移到新机器**
```bash
# 新机器上先建卷，再把 tar 解进去
docker volume create emperor-data
docker run --rm -v emperor-data:/data -v $(pwd):/backup busybox \
  tar xzf /backup/emperor-data-YYYYMMDD.tar.gz -C /data
docker compose up -d
```

> `docker compose down` **不**删卷；只有 `docker compose down -v` 才清数据，慎用。

---

## 10. 一键健康自检脚本

把下面存成 `healthcheck.sh` 跑一遍，贴输出给我即可快速定位：

```bash
#!/usr/bin/env bash
echo "== 磁盘 ==" && df -h / | tail -1
echo "== docker ==" && docker --version && docker compose version
echo "== 容器 ==" && docker compose ps
echo "== 本机 /health ==" && curl -fsS http://localhost:8000/health || echo "FAIL /health"
echo "== 端口监听 ==" && (ss -ltnp | grep 8000 || echo "8000 未监听")
echo "== 最近日志 ==" && docker compose logs --tail 15
```

---

## 排错总原则

1. **先本机 `curl localhost:8000/health`**，区分「服务内部问题」还是「外部访问问题」。
2. **看 `docker compose logs`**，90% 的错都在日志里写明了。
3. **安全组 8000 必放**，阿里云默认只放 22。
4. **改了配置/`.env` 要 `docker compose up -d` 重启**才生效。
5. 实在卡住：把 `healthcheck.sh` 输出或报错截图贴给助手，1–2 轮内定位。

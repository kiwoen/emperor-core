# 幻炘AI 域名绑定：huanxin.kdns.fr

> 目标：让 `https://huanxin.kdns.fr` 从公网访问部署在阿里云 ECS（`i-bp1e0i4t7j6aqcu3yq5`）上的幻炘AI 服务。
> 架构：`huanxin.kdns.fr`（kdns.fr 注册）→ NS 托管 Cloudflare → A 记录指向 ECS 公网 IP + 橙色云朵代理（免费 SSL + CDN + 隐藏源站）→ ECS 上 Caddy 反代到 `huanxin-ai:8000`。

---

## 0. 架构总览

```
浏览器 / 客户端
      │  HTTPS（Cloudflare 边缘证书）
      ▼
Cloudflare（kdns.fr 域名经 NS 托管）
      │  HTTPS 回源（SSL 模式 Full/Full(strict)）→ 隐藏源站 IP
      ▼
阿里云 ECS 公网 IP :80 / :443
      │
      ▼
Caddy 容器（:80/:443，自动 Let's Encrypt 证书）
      │  反代
      ▼
huanxin-ai 容器 :8000（FastAPI：/health /dashboard /court/* /docs /api/*）
      │
      ▼
huanxin-data 命名卷（huanxin.db / audit.db / approval.db / 版本快照）
```

> 公网只暴露 80/443，8000 不对外；服务本体的鉴权由 `HUANXIN_API_TOKEN` 控制。

---

## 1. 服务器侧（阿里云 ECS）

### 1.1 前置确认

SSH 登录 ECS 后先确认现状（任选其一）：

```bash
# 系统
cat /etc/os-release | head -2
# Docker / Compose 是否安装
docker --version && docker compose version
# 8000 端口是否已在跑（若用本仓库 compose，容器名应为 huanxin-ai）
docker ps | grep -E 'huanxin|8000'
# 获取公网 IP（需在阿里云控制台“实例详情”确认，或直接查）
curl -s https://ifconfig.me
```

> 本配置假设服务**已经用本仓库 `docker-compose.yml` 部署**（容器名 `huanxin-ai`、同 compose 默认网络）。
> 若服务以别的方式运行（如直接 `pip` 跑、或别的 compose 项目），见文末「备选方案」。

### 1.2 应用本仓库的改動

本仓库已包含两项改动（已提交/待提交）：

- `docker-compose.yml` 新增 `caddy` 服务（暴露 80/443，挂载 `./Caddyfile` 与 `caddy_data`/`caddy_config` 卷）
- 新增 `Caddyfile`（反代 `huanxin.kdns.fr → huanxin-ai:8000`）

把最新代码同步到 ECS（二选一）：

```bash
# 方式 A：已从 GitHub 克隆，直接拉取
cd /path/to/huanxin-ai
git pull origin master

# 方式 B：本机改完直接 scp 两个文件过去
scp docker-compose.yml Caddyfile user@<ECS公网IP>:/path/to/huanxin-ai/
```

### 1.3 阿里云安全组放通 80/443

1. 阿里云控制台 → **云服务器 ECS** → 实例 `i-bp1e0i4t7j6aqcu3yq5` → **安全组**
2. 入方向规则新增：
   - 协议 `HTTP(80)` 授权对象 `0.0.0.0/0`
   - 协议 `HTTPS(443)` 授权对象 `0.0.0.0/0`
3. 保存。（如系统防火墙 `firewalld`/`ufw` 开启，也需放通 80/443）

> 8000 端口**不要**对公网开放，仅 80/443 入站即可。

### 1.4 重新拉起

```bash
cd /path/to/huanxin-ai
docker compose up -d --build      # 首次会拉取 caddy:2 镜像并构建 huanxin-ai
docker compose ps                 # 应为 huanxin-ai(healthy) + huanxin-caddy(up)
docker compose logs -f caddy      # 观察首次证书申请（ACME）日志
```

Caddy 首次启动会自动向 Let's Encrypt 申请证书（HTTP-01 走 80）。
若看到 `certificate obtained successfully` 即成功；常见失败与处理见第 5 节。

---

## 2. 域名侧（kdns.fr → Cloudflare，需你登录操作）

### 2.1 注册 huanxin.kdns.fr（若尚未注册）

1. 打开 KataBump 后台（注册地址见 kdns.fr 官网，支持 Discord / 邮箱登录）
2. 左侧 **mDomains → Order my first dominy**（每人最多 2 个免费域名）
3. 前缀填 `huanxin` → 得到 `huanxin.kdns.fr` → 完成验证 → Order
   > 若提示 `Domain already exists`，换一个前缀（如 `huanxin-ai`）并在本文档与 Caddyfile 同步替换。

### 2.2 把 NS 托管到 Cloudflare

1. Cloudflare 控制台 → **Add a site** → 输入 `huanxin.kdns.fr` → 选 **Free**
2. Cloudflare 分配两个 NS（形如 `xxx.ns.cloudflare.com` / `yyy.ns.cloudflare.com`）
3. 回到 KataBump 后台该域名设置 → 把 **Nameservers** 替换为上面两条 → Save
4. 等 1–2 分钟，Cloudflare 显示 **Active**（SSL 会自动进入签发流程）

### 2.3 添加 A 记录 + 橙色云朵

Cloudflare → 该站点 → **DNS → Records → Add record**：

| 类型 | 名称 | 内容 | 代理状态 |
|---|---|---|---|
| A | `@` | `<你的阿里云 ECS 公网 IP>` | 🟠 Proxied（橙色云朵）|

> 橙色云朵开启后：免费 CDN + 隐藏源站 IP + Cloudflare 边缘 SSL。
> （如后续要做非 HTTP 用途再考虑灰色云朵，本项目不需要。）

### 2.4 SSL/TLS 模式

Cloudflare → **SSL/TLS → Overview**：

- **推荐 `Full (strict)`**：要求源站（Caddy）证书被公网信任，Let's Encrypt 证书满足，最安全。
- 若 Caddy 首次证书尚未签发导致回源失败，可**临时**设为 `Flexible` 让 Caddy 先完成申请，再切回 `Full (strict)`。
- 不要长期用 `Flexible`：它让 Cloudflare→源站走明文 HTTP。

---

## 3. 验证

域名与服务器都就绪后逐项验证：

```bash
# 本地（任意能联网的机器）
curl -I https://huanxin.kdns.fr/health      # 期望 200
curl -s https://huanxin.kdns.fr/health      # 期望返回健康探针 JSON
curl -sI https://huanxin.kdns.fr/           # 期望 200/3xx，且含证书信息
```

浏览器打开：

- `https://huanxin.kdns.fr/` → 仪表盘（若后端有根路由）
- `https://huanxin.kdns.fr/dashboard` → 反馈仪表盘
- `https://huanxin.kdns.fr/docs` → API 文档（FastAPI Swagger）

证书检查：浏览器地址栏锁标 → 证书颁发者为 **Let's Encrypt**（或 Cloudflare 边缘证书，取决于查看层级）。

---

## 4. 安全加固（强烈建议）

公网服务必须设置访问令牌，否则 8000 后端逻辑可被匿名调用。

编辑 ECS 上的 `.env`（与 docker-compose.yml 同级）：

```bash
cd /path/to/huanxin-ai
openssl rand -hex 24        # 生成令牌，复制输出
```

在 `.env` 中设置（取消注释并填入）：

```ini
HUANXIN_API_TOKEN=<上面生成的令牌>
HUANXIN_ADMIN_USER=admin
HUANXIN_ADMIN_PASS=<自定义强密码>
HUANXIN_OPEN_REGISTRATION=0     # 关闭公开注册，仅管理员账号
```

然后重建使环境变量生效：

```bash
docker compose up -d --build
```

> 之后调用受保护接口需带 `Authorization: Bearer <HUANXIN_API_TOKEN>`（除 `/health` 外）。

---

## 5. 排错

| 现象 | 原因 / 处理 |
|---|---|
| Caddy 日志 `ACME challenge failed` | 80 端口未放通，或 Cloudflare 还没回源到 80。确认阿里云安全组 + 系统防火墙放通 80；可临时把 Cloudflare SSL 设为 Flexible 完成首次签发。 |
| 浏览器 `ERR_SSL_VERSION` / 证书不匹配 | Cloudflare SSL 模式与源站不符。源站用 Let's Encrypt 时设为 `Full` 或 `Full (strict)`。 |
| `502 Bad Gateway` | Caddy 连不上 `huanxin-ai:8000`。确认两容器同 compose 网络、`huanxin-ai` 容器健康（`docker compose ps`），且服务确实监听 8000。 |
| 访问一直转圈 / 超时 | 安全组未放通 443；或 A 记录 IP 填错；或 Cloudflare 代理回源端口不对。用 `curl -v https://huanxin.kdns.fr/health` 看握手阶段。 |
| `too many certificates already issued` | Let's Encrypt 限流（同一域名每周 50 张）。`caddy_data` 卷已持久化证书，勿频繁重建 caddy 容器；必要时等一周或用 staging 环境调试。 |

查看实时日志：

```bash
docker compose logs -f caddy
docker compose logs -f huanxin-ai
```

---

## 6. 备选方案

### 6.1 服务不是用本仓库 compose 部署的

若 `huanxin-ai` 服务跑在别的 compose 项目或直接跑在宿主机 8000：

- **同网络**：把 Caddy 的 `reverse_proxy huanxin-ai:8000` 改为正确的服务名 / 容器名。
- **宿主机直跑**：改为 `reverse_proxy 127.0.0.1:8000`，并单独用 `docker run -d -p 80:80 -p 443:443 -v $PWD/Caddyfile:/etc/caddy/Caddyfile -v caddy_data:/data -v caddy_config:/config caddy:2` 跑 Caddy。

### 6.2 不想用 Cloudflare

把 kdns.fr 的 A 记录直接指向 ECS 公网 IP、关闭代理（灰色云朵），源站 Caddy 仍自动 Let's Encrypt。
缺点：源站公网 IP 暴露、无 CDN、无 WAF。

### 6.3 仅内网 / 临时测试

不绑域名：直接 `http://<ECS公网IP>:8000/health`（安全组放通 8000，仅测试用，勿长期暴露）。

---

## 7. 操作步骤速查（你这边要做的）

1. **服务器侧（ECS，SSH）**：同步最新 `docker-compose.yml` + `Caddyfile` → 安全组放通 80/443 → `docker compose up -d --build` → 看 `docker compose logs -f caddy` 确认证书申请成功。
2. **域名侧（网页）**：KataBump 注册 `huanxin.kdns.fr` → NS 改 Cloudflare → Cloudflare 加 A 记录 `@ → ECS公网IP` 并开橙色云朵 → SSL 模式 `Full (strict)`。
3. **验证**：`curl -I https://huanxin.kdns.fr/health` 返回 200。
4. **加固**：`.env` 设 `HUANXIN_API_TOKEN` 并重建。

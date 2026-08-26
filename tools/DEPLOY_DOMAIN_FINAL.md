# 增补：Cloudflare 代理下 Caddy 证书签发（ACME 挑战路径取舍）

> 配套文档：`huanxin-ai/DEPLOY_DOMAIN.md`
> 本文是实战踩坑后的**勘误 + 决策表**，专门讲清楚「Cloudflare 云朵状态」与「Caddy 自动 Let's Encrypt」到底怎么配才签得出证书。
> 结论先行：原 `DEPLOY_DOMAIN.md` 第 2.3 / 5 节主推的「橙色云朵 + HTTP-01」**实测签不出证书**，本文给出已验证可用方案。

---

## 0. 结论速览（TL;DR）

| 你的诉求 | 正确做法 | 是否验证 |
|---|---|---|
| 只想最快让 HTTPS 跑起来 | A 记录**灰色云朵（DNS only）** + 让 Let's Encrypt 直连 ECS | ✅ 本项目实测成功 |
| 想保留橙色云朵（CDN / 隐藏源站 IP） | 在 Caddy 上装 **Cloudflare Origin CA 证书**（最省事），或让 Caddy 走 **DNS-01** | ✅ 路径可行（见 §4） |
| 还想继续用 Let's Encrypt 证书 | 只能走 **DNS-01**（不能用 http-01 / tls-alpn-01 打橙色云朵） | ✅ 路径可行（见 §4.2） |

**本项目最终采用**：灰色云朵（DNS only）+ Let's Encrypt `tls-alpn-01` 直连，证书秒签、每 90 天自动续期。

---

## 1. 三种 ACME 挑战 × Cloudflare 云朵对照表

| 挑战类型 | 验证方式 | 端口 | 灰色云朵（DNS only） | 橙色云朵（Proxied） |
|---|---|---|---|---|
| **http-01** | LE 访问 `http://域名/.well-known/acme-challenge/...` | 80 | ✅ 直连 ECS，可用 | ❌ Cloudflare 边缘拦截，返回 **403** |
| **tls-alpn-01** | LE 用 `acme-tls/1` ALPN 连 443 校验特殊证书 | 443 | ✅ 直连 ECS，可用 | ❌ Cloudflare 截断 TLS，报 **Cannot negotiate ALPN acme-tls/1** |
| **dns-01** | LE 通过 DNS 提供商 API 写 TXT 记录验证域名权属 | 无（纯 DNS） | ✅ 可用 | ✅ **可用**（不受代理影响） |

> 关键认知：**http-01 和 tls-alpn-01 都要求 Let's Encrypt 验证服务器「直连」到你的源站端口 80/443**。Cloudflare 橙色云朵把流量截留在边缘，验证请求永远到不了 ECS，所以这两种挑战在代理下必败。

---

## 2. 为什么橙色云朵会卡死 http-01 / tls-alpn-01（原理）

- **http-01 失败**：Cloudflare 边缘收到 `/.well-known/acme-challenge/*` 请求后，要么用自己的 403 页面响应，要么（即使关了 Under Attack / Bot Fight）因免费版 WAF / 浏览器完整性检查返回 403。日志特征：
  ```
  detail:"2606:4700:3032::6815:2a77: Invalid response from https://huanxin.kdns.fr/.well-known/acme-challenge/...: 403"
  ```
  那个 `2606:4700:*` 就是 Cloudflare 的 IPv6 边缘地址——Caddy 其实已经 `served key authentication`（正确吐出令牌），但 LE 拿到的是 Cloudflare 的 403，不是 ECS 的 200。

- **tls-alpn-01 失败**：Cloudflare 在 443 上做了自己的 TLS 终止，不支持 `acme-tls/1` 这个 ALPN 协议，于是：
  ```
  detail:"Cannot negotiate ALPN protocol \"acme-tls/1\" for tls-alpn-01 challenge"
  ```

两者都会导致 `no solvers available for remaining challenges (remaining=[dns-01])` —— 即 Caddy 最终发现只剩 dns-01 能走。

---

## 3. 已验证方案 A：灰色云朵（本项目最终采用）

### 3.1 操作步骤（Cloudflare 网页）
1. **DNS → Records**，找到 `huanxin.kdns.fr` 的 A 记录（`@ → 47.97.212.180`）。
2. 把代理状态从 🟠 Proxied 点成 ⚪ **DNS only（灰色云朵）**。
3. SSL/TLS 模式保持 **Full (strict)**（源站 Caddy 用的是公网信任的 Let's Encrypt 证书）。

> 灰色云朵下 Cloudflare 只做 DNS 解析，流量直连 ECS。源站公网 IP 会暴露（无 CDN / 无 WAF），个人项目可接受。

### 3.2 强制 Caddy 立即重试签发
Caddy 失败重试间隔会**随失败次数指数涨到 1200s**，改完云朵不会立刻重试，必须手动重启：
```bash
cd /path/to/huanxin-ai
docker restart huanxin-caddy
docker compose logs -f caddy
```
看到即成功：
```
"tls.issuance.acme.acme_client","msg":"validations succeeded; finalizing order"
"tls.obtain","msg":"certificate obtained successfully","identifier":"huanxin.kdns.fr"
```

### 3.3 验证（外部视角）
浏览器打开 `https://huanxin.kdns.fr/health` → 返回 `{"status":"ok","service":"huanxin-ai"}` 即 100% 成功。
> 注意：本机 Windows `curl.exe -I` 可能报 `(35) Recv failure: Connection was reset`，这是**本机到 ECS 链路被本地网络/杀软重置**，与服务器无关；以浏览器结果为准（详见 §7）。

### 3.4 续期
Let's Encrypt 证书 90 天有效，Caddy 内部自动续期，**续期同样走直连，灰色云朵下不受影响**，无需任何人工干预。

---

## 4. 想保留橙色云朵的两条路

如果必须保留 CDN / 隐藏源站 IP，http-01 / tls-alpn-01 都不可用，只能二选一：

### 4.1 方案 B：Cloudflare Origin CA 证书（最简单，推荐保留代理时）

不依赖 Let's Encrypt，改用 Cloudflare 自己签发的**源站证书**（仅 Cloudflare 边缘信任，配合 Full(strict) 足够）。

1. Cloudflare 网页 → **SSL/TLS → Origin Server → Create Certificate**
   - 选 `RSA (2048)`，有效期按需（如 15 年），点下一步。
   - 复制 **Origin Certificate (PEM)** 和 **Private Key**，分别存为 `cloudflare_origin.pem` / `cloudflare_origin.key`。
2. 把这两个文件挂进 caddy 容器，Caddyfile 显式指定：
   ```caddy
   huanxin.kdns.fr {
       tls /etc/caddy/cloudflare_origin.pem /etc/caddy/cloudflare_origin.key
       reverse_proxy huanxin-ai:8000
   }
   ```
3. A 记录保持 🟠 橙色云朵，SSL 模式 **Full (strict)**。
4. 重建：`docker compose up -d --build`。

优点：零插件、零 DNS API、证书可设很长有效期。缺点：证书由 Cloudflare 管，需在到期前手动或在 Cloudflare 控制台轮换。

### 4.2 方案 C：Caddy DNS-01（保留 Let's Encrypt，需自定义镜像）

走 `dns-01` 时验证完全在 DNS 层完成，不受代理影响，可继续用 Let's Encrypt + 橙色云朵。代价是要给 Caddy 打 Cloudflare DNS 插件。

**(a) 准备 Cloudflare API Token**（非 Global API Key）
- Cloudflare → **My Profile → API Tokens → Create Token**
- 权限模板选 **Edit zone DNS**（即 `Zone:DNS:Edit`），限定到 `huanxin.kdns.fr` 这个 zone。
- 复制生成的 Token（形如 `CF_API_TOKEN=xxxx`）。

**(b) 用 xcaddy 构建带 cloudflare 插件的镜像**
新建 `Dockerfile.caddy`：
```dockerfile
FROM caddy:2-builder AS builder
RUN xcaddy build --with github.com/caddyserver/dnsproviders/cloudflare
FROM caddy:2
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
```

**(c) docker-compose 里替换 caddy 服务**
```yaml
  caddy:
    build:
      context: .
      dockerfile: Dockerfile.caddy
    # 删掉 image: caddy:2，改用上面的 build
    environment:
      - CLOUDFLARE_API_TOKEN=${CLOUDFLARE_API_TOKEN}   # 从 .env 注入
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
      - caddy_data:/data
      - caddy_config:/config
    depends_on:
      - huanxin-ai
```

**(d) Caddyfile 顶部声明 DNS 挑战 + 邮箱**
```caddy
{
    email you@example.com          # 必填，LE 账户联系邮箱
    acme_dns cloudflare {env.CLOUDFLARE_API_TOKEN}
}

huanxin.kdns.fr {
    reverse_proxy huanxin-ai:8000
}
```

**(e) `.env` 追加**
```ini
CLOUDFLARE_API_TOKEN=你的_CF_DNS_Edit_Token
```

**(f) 重建**
```bash
docker compose up -d --build
docker compose logs -f caddy   # 应见 certificate obtained successfully
```
此时 A 记录可保持 🟠 橙色云朵，CDN 与隐藏源站全部保留。

---

## 5. 本次真实排查时间线（踩坑记录，供复盘）

1. Caddy 首次起 → 同时尝试 `tls-alpn-01` 与 `http-01` → 均失败（ALPN 协商失败 / http-01 被 Cloudflare 403）。
2. 日志里 Caddy 已 `served key authentication` 但 LE 收到 Cloudflare 边缘 `2606:4700:*` 的 403 → 锁定根因 = 橙色云朵代理拦截 ACME 路径。
3. 重试间隔随失败指数增长：`60s → 120s → 300s → 600s → 1200s`。改配置后**必须 `docker restart huanxin-caddy`** 强制立即重试，否则要干等 20 分钟。
4. 期间出现 `ZeroSSL HTTP 422: caddy_legacy_user_removed` —— **干扰项**，Caddy 自动回落 Let's Encrypt，可忽略。
5. 把 A 记录改 **灰色云朵** → 重启 caddy → `tls-alpn-01` 这次直连成功 → `certificate obtained successfully`（生产 LE `acme-v02`）→ 外部浏览器实测 `200` + health JSON。
6. 部署后出现安全扫描器 `leakix.net` 扫漏洞路径，全部 `401`（需 Bearer）/ `405` → 鉴权中间件生效，源站暴露但防护到位。

---

## 6. ⚠️ 本仓库 `huanxin-ai/DEPLOY_DOMAIN.md` 需同步修正的点

原文档以下表述与实测冲突，建议后续修订：

| 位置 | 原表述（不准确） | 应改为 |
|---|---|---|
| §2.3 | 「橙色云朵……（如后续要做非 HTTP 用途再考虑灰色云朵，本项目不需要）」 | 橙色云朵下 Caddy 自管 Let's Encrypt **签不出证书**；本项目改用灰色云朵，或见本文 §4 保留代理的两条路 |
| §5 排错表 | 「ACME challenge failed … 可临时把 Cloudflare SSL 设为 Flexible 完成首次签发」 | 代理下失败的根因是 **ACME 挑战被边缘拦截**，与 SSL 模式无关；Flexible 无效，应改灰色云朵或 DNS-01 |
| §1.4 | 「Caddy 首次启动会自动向 Let's Encrypt 申请证书（HTTP-01 走 80）」 | 该描述仅在**灰色云朵 / 无代理**时成立；橙色云朵下 HTTP-01 不可达 |

其余章节（ECS 初始化、安全组、compose 结构、安全加固、验证 URL）均准确，无需改动。

---

## 7. Windows 本地 `curl` 取证注意（反复踩的坑）

- PowerShell 里 `curl` 是 **`Invoke-WebRequest` 别名**，不认 `-I`，会卡在 `Uri:` 提示 → 用 `curl.exe -I ...`。
- `curl.exe -I https://huanxin.kdns.fr/health` 报 `(35) Recv failure: Connection was reset` = **本机到 ECS 的链路被本地网络/杀软重置**，与服务端无关；**以浏览器打开为准**。
- 如果浏览器能开、只有命令行 curl 失败：跳过 curl，直接信浏览器结果，不影响部署与证书续期。

---

## 8. 决策树（下次绑新域名照抄）

```
要 Cloudflare CDN / 隐藏源站 IP 吗？
├─ 不要（个人项目 / 不在乎暴露 IP）
│   → A 记录灰色云朵 + Caddy 默认 LE（http-01 或 tls-alpn-01 直连）
│   → 改完 docker restart caddy，等 certificate obtained successfully
└─ 要
    ├─ 想用 Cloudflare 自家证书（最省事）
    │   → 方案 B：Cloudflare Origin CA 证书装到 Caddy，A 记录橙色云朵
    └─ 想坚持 Let's Encrypt
        → 方案 C：Caddy + cloudflare dns 插件 + DNS-01，A 记录橙色云朵
```

> 一句话：**代理（橙色云朵）与「Caddy 自管 LE 证书」天然冲突，必须用 dns-01 或 Origin CA 才能共存；否则就关代理（灰色云朵）直连签发。**

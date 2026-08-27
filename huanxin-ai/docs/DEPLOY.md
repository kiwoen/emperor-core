# 部署指南（Deployment）

> 端口统一为 **8000**（单服务单端口，Caddy 反代 + TLS 终止）。

## 架构
- 应用：`huanxin-ai` 容器，`python -m huanxin.cli serve` 监听 `0.0.0.0:8000`（容器内 `expose`）。
- 反代：`huanxin-caddy` 容器，Caddy 终止 TLS，对外 `80/443`。
- 域名：Cloudflare **灰云 / DNS only**（关闭橙色代理），由 Caddy 直接签发 Let's Encrypt 证书
  （DNS-01 或 HTTP-01，取决于 DNS 提供方；本仓库部署采用 DNS only）。

## 环境变量（.env）
| 变量 | 默认 | 说明 |
|------|------|------|
| `HUANXIN_OPEN_REGISTRATION` | `0` | 公开注册开关，`0` 关闭 |
| `HUANXIN_ADMIN_USER` / `HUANXIN_ADMIN_PASS` | — | 启动时种入的管理员账号 |
| `HUANXIN_API_TOKEN` | — | 作为管理员密码回退值 |

## 构建与启动
```bash
docker compose build
docker compose up -d
docker compose logs --tail 30 huanxin-ai
```

## 健康检查
```bash
docker exec huanxin-ai curl -f http://localhost:8000/health
# => {"status":"ok","service":"huanxin-ai"}
```

## 相关文档
- 域名 / 证书部署细节见 [../DEPLOY_DOMAIN.md](../DEPLOY_DOMAIN.md)
- 鉴权与注册见 [AUTH.md](AUTH.md)
- 配置项见 [CONFIG.md](CONFIG.md)
- 自进化见 [SELF_EVOLVE.md](SELF_EVOLVE.md)
- 监控见 [MONITORING.md](MONITORING.md)

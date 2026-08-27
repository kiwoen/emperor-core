# 鉴权与注册（Auth）

## 会话登录（强制）
部署为单服务单端口（8000）后，**一律要求登录**：非白名单路径必须携带会话 token。

- 通道：**仅** `Authorization: Bearer <token>`（`?token=` 查询参数通道已移除，避免令牌泄露到访问日志 / 浏览器历史）。
- 登录：先 `POST /api/auth/login` 拿到会话 token，后续请求带上。
- 管理员账号在应用启动时由 `HUANXIN_ADMIN_USER` / `HUANXIN_ADMIN_PASS`（回退到 `HUANXIN_API_TOKEN`）种入。

## 公开路径（无需登录）
- `/health`
- `/api/auth/login`
- `/api/auth/register`
- `/`

> 注意：`/dashboard` 与 `/dashboard/legacy` **不在**公开路径内，访问需先登录。

## 公开注册开关
`HUANXIN_OPEN_REGISTRATION` 控制 `/api/auth/register` 是否真正可用：
- `0` / `false` / 未设置 → 注册关闭，返回 `403`（**默认**）。
- `1` / `true` / `yes` / `on` → 开放注册，注册成功即自动登录。

## 相关文档
- 部署见 [DEPLOY.md](DEPLOY.md)
- 配置见 [CONFIG.md](CONFIG.md)

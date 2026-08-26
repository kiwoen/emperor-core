# 前端 UI 优化 · 对标 ChatGPT / Codex

> 提交：`337b4aa` · 已推送 `github.com/kiwoen/huanxin-ai` master
> 改动文件：`huanxin/chat_dashboard.py`、`huanxin/dashboard_html.py`
> 仅前端 UI，未改动任何后端 API 契约。

## 一、聊天控制台（ChatGPT / Codex 风格）`huanxin/chat_dashboard.py`

### 1. 真实 Markdown 渲染 + 代码语法高亮
- 引入 `marked`（GFM + 软换行）渲染助手回复，支持标题 / 列表 / 表格 / 引用 / 行内代码等。
- 引入 `highlight.js` 对代码块做语法高亮，主题随明暗模式联动切换（github / github-dark）。
- 引入 `DOMPurify` 对渲染结果做 XSS 净化（ADD_ATTR target），安全兜底。
- CDN 不可用时自动回退原 `mdLite` 轻量渲染，不依赖网络也能工作。

### 2. Codex 式代码块体验
- 每个代码块顶部加「语言标签 + 复制按钮」工具条（`.code-bar` / `.code-copy`）。
- 复制按钮调用 `navigator.clipboard`，带「已复制 ✓ / 失败」反馈。
- 明暗主题下代码块配色自适应。

### 3. 新增「代码模式」开关（顶栏 💻）
- 开启后消息区切换为等宽字体、放宽至 940px、放大行高，呈现 Codex 式编码视图；
- 用户气泡转为虚线边框，突出代码内容；状态持久化于 `localStorage`。

### 4. 视觉打磨
- 消息入场淡入动效、欢迎页标题渐变、输入框聚焦高亮环。
- 顶部栏新增移动端 ☰ 菜单按钮，侧栏在窄屏抽屉化（scrim 遮罩）。
- 全站尊重 `prefers-reduced-motion`。

## 二、自进化看板 `huanxin/dashboard_html.py`
- body 叠加细网格背景（28px 间距，极低透明度），更现代的数据看板质感。
- 新增 `prefers-reduced-motion` 兜底，关闭动画/过渡，照顾晕动敏感用户。
- 未改动任何数据与渲染逻辑。

## 三、验证
- `python -c "import huanxin.chat_dashboard, huanxin.dashboard_html; ...generate..."` 通过，
  生成 HTML 结构闭合、关键功能 token 齐全（marked/highlight/dompurify/toggleCodeMode/code-mode/scrim-side 等）。

## 四、云端同步（关键）

代码推到 GitHub **不等于**线上生效——云端容器需要重新 `git pull` + 重建镜像。
现已提供两条路径：

| 方式 | 命令 / 触发 | 说明 |
|---|---|---|
| 自动（推荐） | push 到 master | `.github/workflows/deploy.yml` 自动 SSH 部署，含健康检查 + 失败回滚 |
| 手动 | `bash scripts/remote_deploy.sh` | 在服务器 `/srv/huanxin-ai` 执行，幂等可重复跑 |

首次启用自动部署需在仓库 **Settings → Secrets and variables → Actions** 配置
`DEPLOY_HOST` + （`DEPLOY_SSH_KEY` 或 `DEPLOY_PASSWORD`）。详见
[`DEPLOY_STUDENT_ALIYUN.md`](DEPLOY_STUDENT_ALIYUN.md) 第 5.1 / 5.2 节。

验证线上是否已是新 UI（新版必含 `marked`）：

```bash
curl -s http://<公网IP>:8000/dashboard | grep -c marked.min.js   # 新版 ≥1，旧版 0
```

## 五、同步说明（历史）
- 工作区本地 `.git` 此前已损坏（`git fetch` 报 unresolved deltas、无法提交）。
- 该次采用**稀疏克隆临时仓 → 仅覆盖改动文件 → 审查 diff → 快进推送 master** 的安全路径，
  已验证可用且未误删远程任何内容。
- 如需在工作区直接使用 git，建议重克隆修复 `.git`（GitHub 现为权威源）。

## 六、可选后续
- 看板做更深度的 ChatGPT 化（卡片信息密度、图表交互、暗色细节）——本次仅做轻量视觉刷新。
- 将 marked/highlight.js/DOMPurify 改为自托管（离线可用、零外链依赖）。

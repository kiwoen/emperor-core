# huanxin-ai — Obsidian 知识库镜像

本仓库是 GitHub [`kiwoen/huanxin-ai`](https://github.com/kiwoen/huanxin-ai.git) 的**本地知识库镜像**，用 Obsidian 管理。

## 结构
```
D:\kiwon\
├── huanxin-ai\          # = GitHub 仓库工作树（源码 + docs/）
│   ├── huanxin\            # 核心包
│   ├── docs\              # 架构/设计/部署文档
│   ├── deploy\            # 部署脚本（含 ecs/setup-ecs.sh）
│   └── .gitignore         # 已排除密钥/数据库/缓存
├── huanxin-ai-index.md  # 📌 项目首页（索引）
├── 01-项目概览.md
├── 02-代码地图.md
├── 03-部署与中转站.md     # ECS + 中转站实战记录
├── 04-会话记录-2026-08.md
├── 05-积分中转站设计.md
└── 06-GitHub同步.md       # 同步操作手册 + 安全红线
```

## 安全红线
- **绝不提交** `.env`（含真实 API Key）、`*.db`（业务数据）、缓存/构建产物。
- 19 号截图泄露的 DeepSeek Key 已作废。

## 同步
见 `06-GitHub同步.md`：本地 `.git` 损坏后，以 GitHub 远端历史为准恢复并推送。

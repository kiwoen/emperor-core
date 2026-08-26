---
title: "huanxin-ai · 项目首页"
created: 2026-08-23
tags: [huanxin-ai, index, meta]
---

# 👑 huanxin-ai 知识库索引

> Obsidian Vault for the huanxin-ai self-evolving AI project.
> 本库 = GitHub `kiwoen/huanxin-ai` 的**本地知识库镜像**（源码 + 设计文档 + 会话记录），
> 所有源码在 `huanxin-ai/` 子目录，本页与 `00-*` 笔记为管理元数据。

## 📌 快速导航

| 主题 | 笔记 |
|---|---|
| 项目定位 / 架构总览 | [[01-项目概览]] |
| 目录结构与关键文件 | [[02-代码地图]] |
| 部署（ECS + 中转站） | [[03-部署与中转站]] |
| 本地会话记录（2026-08 云部署轮） | [[04-会话记录-2026-08]] |
| 积分 / 学习中转设计 | [[05-积分中转站设计]] |
| GitHub 同步状态 | [[06-GitHub同步]] |

## 🔑 关键信息（速查）

- **仓库**：`https://github.com/kiwoen/huanxin-ai.git`
- **默认 LLM 配置**（容器 env，非本地 `.env`）：
  `OPENAI_BASE_URL` / `OPENAI_API_KEY` / `OPENAI_MODEL` / `HUANXIN_LLM_PROVIDER`（默认 `mock`，实际用 Agnes 网关 `apihub.agnes-ai.com/v1`，模型 `agnes-2.0-flash`）
- **部署形态**：`docker-compose.yml` + `Dockerfile`，入口 `python -m huanxin.cli serve --host 0.0.0.0 --port 8000`，数据卷 `huanxin-data` → `/app/data`
- **中转站**：New API（`calciumion/new-api`），独立容器 `:3000`，OpenAI 兼容 `/v1`

## ⚠️ 安全红线

- **绝不**把 `.env`（含真实 API Key）提交进本库 / GitHub —— 已用 `.env.example` 替代。
- 19 号截图泄露的 DeepSeek Key `sk-e32eb21...` 已作废，勿复用。

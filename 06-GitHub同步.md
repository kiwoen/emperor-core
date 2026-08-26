---
title: "06-GitHub同步"
created: 2026-08-23
tags: [huanxin-ai, git, github, sync]
---

# 06 · GitHub 同步状态与操作手册

## 当前状态（2026-08-23）
- **源仓库本地 Git 已损坏**：`D:\AI自我进化\huanxin-ai\.git` 的 HEAD 树对象缺失（`91ef4253...`），且 `origin/fix-p0.6-eval-bench`、`fix/p0-truthfulness`、`tag v1.0.0` 指针失效。
- **工作树文件完整**（4400+ 文件已干净导出到 `D:\kiwon\huanxin-ai`）。
- `ec-work` 仓库同样 `.git.broken`（同源损坏）。
- **GitHub 远端 `kiwoen/huanxin-ai` 仍持有真实完整历史** —— 恢复以远端为准，不要推一个浅层新仓库覆盖。

## 恢复 + 同步步骤（在本 vault 执行）
```bash
cd D:\kiwon\huanxin-ai
git init -b master
git remote add origin https://github.com/kiwoen/huanxin-ai.git
# 先拉远端真实历史（修复本地损坏）
git fetch origin
# 把工作树内容提交到新 master（若远端 master 已存在，用 --allow-unrelated-histories 合并或重基）
git add -A
git commit -m "chore(vault): 本地知识库镜像 + 清理垃圾/密钥"
git push -u origin master   # 若冲突：先 git pull --allow-unrelated-histories
```

## 每次更新同步约定
1. 改完 vault 内文件（含 `huanxin-ai/` 源码镜像或 `0x-*` 笔记）
2. `git add -A && git commit -m "..." && git push`
3. **绝不提交**：`.env`（密钥）、`*.db`/`*.db-wal`、`__pycache__`、`*.log`、构建产物 —— 已被 `.gitignore` 覆盖

## ⚠️ 已排除的敏感/垃圾（防泄露）
- `.env` —— 含 NVIDIA / DeepSeek / ARK 真实 API Key（用 `.env.example` 替代）
- `*.db` / `*.db-shm` / `*.db-wal` —— 运行时数据库（audit.db / huanxin.db，含业务数据）
- `__pycache__/`、`.pytest_cache/`、`build/`、`*.egg-info/` —— 构建缓存
- `huanxin/eval.py.bak` —— 备份死文件
- `C:\Users\yuxing\AppData\Local\Temp\huanxin-absorb-*` —— 临时吸收目录（几十个，纯垃圾，已留待清理）

## C 盘散落数据
- `C:\Users\yuxing\.huanxin-ai\feedback\feedback.jsonl` —— 反馈日志（小，可保留）
- `C:\Users\yuxing\AppData\Local\Temp\huanxin-absorb-*` —— 临时目录，无价值，建议清理
- 本会话 `.workbuddy/teams/software-huanxin-*`、`.workbuddy/tasks/software-huanxin-*` —— 团队任务元数据

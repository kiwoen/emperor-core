
# 幻炘AI 快速入门指南

> 版本: 0.1.0 | 最后更新: 2026-08-08

---

## 目录

1. [环境要求](#1-环境要求)
2. [安装](#2-安装)
3. [配置](#3-配置)
4. [启动系统](#4-启动系统)
5. [第一个任务](#5-第一个任务)
6. [进化与优化](#6-进化与优化)
7. [常用 CLI 命令](#7-常用-cli-命令)
8. [下一步](#8-下一步)

---

## 1. 环境要求

| 项目 | 最低要求 |
|------|---------|
| Python | 3.11+ |
| 操作系统 | Windows 10+ / Linux / macOS |
| 磁盘空间 | 500 MB（含依赖） |
| 可选 LLM API | OpenAI / Anthropic / DeepSeek（用于实时模式） |

---

## 2. 安装

### 2.1 安装 幻炘AI

```bash
# 进入项目目录
cd huanxin-ai

# 安装核心依赖
pip install -e .

# (可选) 安装开发依赖
pip install -e ".[dev]"
```

### 2.2 验证安装

```bash
# 检查版本
python -c "import huanxin; print(huanxin.__version__)"

# 输出: 0.1.0
```

---

## 3. 配置

### 3.1 自动生成配置

首次运行时，系统会自动在项目根目录生成 `huanxin.yaml`：

```bash
python -m huanxin serve
```

### 3.2 默认配置说明

```yaml
{
  "dashboard": {
    "host": "127.0.0.1",        # Dashboard 监听地址
    "port": 8000,                # Dashboard 端口
    "theme": "dark",             # 主题: dark / light / auto
    "weather_city": "北京",      # 天气城市
    "refresh_interval_seconds": 15  # 面板刷新间隔
  },
  "scheduler": {
    "auto_schedule": true,       # 自动调度
    "evolve_interval_minutes": 5.0,   # 进化间隔
    "task_interval_minutes": 3.0      # 任务间隔
  },
  "evolution": {
    "merit_delta_range": [-2, 2],
    "stability_delta_range": [-0.02, 0.02],
    "streak_bonus_threshold": 5
  },
  "capability": {
    "enabled_capabilities": [
      "datetime", "math", "random", "text", "file_info",
      "hash", "json_tool", "uuid_gen",
      "weather", "news", "web_search", "web_fetch"
    ]
  },
  "seed_ministers": [
    {"name": "turing",    "domain": "general"},
    {"name": "curie",     "domain": "science"},
    {"name": "hinton",    "domain": "data"},
    {"name": "bengio",    "domain": "data"},
    {"name": "lecun",     "domain": "code"},
    {"name": "goodfellow","domain": "math"},
    {"name": "sutton",    "domain": "general"},
    {"name": "silver",    "domain": "general"}
  ],
  "max_ministers": 50
}
```

### 3.3 配置 LLM API (可选)

若需启用实时 LLM 调用（非 Mock 模式），在环境变量中设置 API Key：

```bash
# OpenAI
set OPENAI_API_KEY=sk-your-key-here

# Anthropic
set ANTHROPIC_API_KEY=sk-ant-your-key-here

# DeepSeek
set DEEPSEEK_API_KEY=sk-your-key-here
```

---

## 4. 启动系统

### 4.1 一键启动 Dashboard

```bash
python -m huanxin serve
```

启动后访问 [http://127.0.0.1:8000](http://127.0.0.1:8000) 打开 Dashboard。

**Dashboard 功能概览**：
- 系统健康面板（CPU / 内存 / 磁盘 / 运行时长）
- 实时天气小部件 + 新闻头条
- ECharts 进化历史趋势图
- 能力命中统计环形饼图
- 金银铜功绩排行榜
- 任务面板（搜索 / 筛选 / 状态追踪）
- 大臣管理（CRUD / 能力分布 / 稳定度可视化）
- 调度器配置面板（暂停 / 恢复 / 调整间隔）
- 控制面板（手动进化 / 执行任务 / 触发自愈）

### 4.2 纯命令行模式

```bash
# 使用 CLI 入口
huanxin serve

# 或使用 sovereign 别名
sovereign serve
```

---

## 5. 第一个任务

### 5.1 通过 Dashboard 执行任务

1. 打开 Dashboard [http://127.0.0.1:8000](http://127.0.0.1:8000)
2. 在控制面板的「手动执行任务」区域输入任务描述
3. 点击「执行」按钮
4. 在任务面板中查看执行结果

### 5.2 通过 CLI 执行任务

```bash
# 执行通用任务
huanxin task "计算 2 的 10 次方"

# 指定领域执行
huanxin task --domain math "求解方程 x^2 + 3x + 2 = 0"

# 查询系统状态
huanxin status

# 查看大臣列表
huanxin ministers
```

### 5.3 通过 Python API 执行任务

```python
from huanxin.core import Huanxin

# 初始化
emp = Huanxin()

# 注册大臣
emp.register("alice", domain="math", temperature=0.7)
emp.register("bob", domain="code", temperature=0.8)

# 执行任务
result = emp.execute_task("What is 17 * 23?", domain="math")
print(result)

# 运行进化
emp.evolve(cycles=3)

# 查看状态
status = emp.status()
print(status)

# 启动 Dashboard
emp.serve(port=8000)
```

---

## 6. 进化与优化

### 6.1 自动进化

系统默认每 5 分钟自动运行一轮进化：

- 评估所有大臣的功绩
- 精英保留 + 交叉育种产生新大臣
- 淘汰低效大臣
- 记录进化历史

### 6.2 手动触发进化

```bash
# 通过 CLI
huanxin evolve

# 通过 Dashboard
# 在控制面板点击「手动进化」按钮

# 通过 API
curl -X POST http://127.0.0.1:8000/api/evolve
```

### 6.3 查看进化历史

```bash
# CLI
huanxin status

# Dashboard → 进化历史趋势图
# Court API
curl http://127.0.0.1:8000/court/history
```

---

## 7. 常用 CLI 命令

| 命令 | 说明 |
|------|------|
| `huanxin serve` | 启动 Dashboard + 调度器 |
| `huanxin task "xxx"` | 手动执行任务 |
| `huanxin task --domain math "xxx"` | 指定领域执行任务 |
| `huanxin status` | 查看系统状态 |
| `huanxin ministers` | 查看大臣列表 |
| `huanxin evolve` | 手动触发进化 |
| `huanxin alerts` | 查看告警历史 |

---

## 8. 下一步

- [API 参考文档](API.md) — 查看所有模块的完整接口
- [系统架构文档](ARCHITECTURE.md) — 理解五层架构设计
- [竞赛参赛指南](COMPETITION_GUIDE.md) — 了解项目亮点与技术创新
- [变更记录](CHANGELOG.md) — 查看版本变更历史

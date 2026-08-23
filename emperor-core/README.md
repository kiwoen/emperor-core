# Jarvis

[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**J.A.R.V.I.S.** — Just A Rather Very Intelligent System.

AI 驱动的多领域自进化智能体系统。内置 Dashboard 实时监控、大臣进化引擎、12 种内置能力、插件系统、自愈机制和 SQLite 持久化。

## 快速开始

```bash
# 安装
pip install -e .

# 一键启动 Dashboard
python -m jarvis serve

# 或使用 CLI
jarvis serve
```

启动后访问 http://127.0.0.1:9020 打开 Dashboard。

## 核心特性

### Dashboard 实时监控
- 系统健康面板（CPU / 内存 / 磁盘 / 运行时长）
- 实时天气小部件 + 新闻头条
- ECharts 进化历史趋势图
- 能力命中统计环形饼图
- 金银铜功绩排行榜
- 任务面板（搜索 / 筛选 / 状态追踪）
- 大臣管理（CRUD / 能力分布 / 稳定度可视化）
- 调度器配置面板（暂停 / 恢复 / 调整间隔）
- 告警面板（搜索 / 筛选）
- 暗色 / 亮色 / 自动主题切换
- 响应式布局（桌面三列 → 平板两列 → 手机单列）
- 面板折叠 (localStorage 持久化)
- 控制面板（手动进化 / 执行任务 / 触发自愈）

### 进化引擎
- 8 位默认大臣，各具专长领域 (general / science / data / code / math)
- 任务成功驱动进化：稳定度、含金量、功绩值动态变化
- 连续成功奖励机制 (streak bonus)
- 周期性自动进化（默认 5 分钟）

### 能力系统（12 个能力）

| 能力 | 描述 | 数据源 |
|------|------|--------|
| datetime | 当前日期时间、时区、星期 | Python stdlib |
| math | AST 安全数学表达式求值 | Python stdlib |
| random | 随机数/骰子/抽签 | Python stdlib |
| text | 文本统计、反转、大小写转换 | Python stdlib |
| file_info | 文件大小/修改时间/行数 | Python stdlib |
| hash | MD5 / SHA256 哈希校验 | Python stdlib |
| json_tool | JSON 格式化/美化/解析 | Python stdlib |
| uuid_gen | UUID/GUID 生成 | Python stdlib |
| weather | 实时天气查询 | wttr.in |
| news | 新闻摘要 | Google News RSS |
| web_search | 网页搜索 | DuckDuckGo |
| web_fetch | 网页内容抓取 | aiohttp |

### 自愈引擎
- 内置自愈动作（重启调度器 / 紧急进化 / 清理停滞大臣等）
- 默认告警规则（大臣枯竭 / 任务失败飙升 / 进化停滞）
- 冷却机制避免重复触发

### 插件系统
- 10+ 生命周期钩子（pre_init / post_init / pre_task / post_task / pre_evolve / ...）
- 支持动态加载/卸载

### 持久化层
- SQLite（WAL 模式）自动落库
- 3 张表：task_history / evolution_history / alert_history
- 自动 schema 迁移
- 支持 JSON/CSV 导出

### 配置文件
- `jarvis.yaml` 集中管理所有参数
- 首次运行自动生成默认配置

### CLI 工具

```bash
jarvis serve                       # 启动 Dashboard + 调度器
jarvis task "计算 2+3"             # 手动执行任务
jarvis task --domain math "π*2"   # 指定领域
jarvis status                      # 查看系统状态
jarvis ministers                   # 查看大臣列表
jarvis evolve                      # 手动触发进化
jarvis alerts                      # 查看告警历史
```

## 项目结构

```
jarvis/
├── __init__.py              # 包元信息
├── __main__.py              # python -m jarvis 入口
├── cli.py                   # CLI 命令行工具
├── main.py                  # 启动入口
├── emperor.py               # 核心编排器 (Emperor)
├── emperor_cli.py           # CLI 编排器
├── config.py                # 配置系统 (jarvis.yaml)
├── capability.py            # 能力注册表 + 12 个处理器
├── database.py              # SQLite 持久化层
├── health.py                # 系统健康监控 (CPU/内存/磁盘)
├── healing.py               # 自愈引擎
├── healing_actions.py       # 内置自愈动作
├── alerts.py                # 告警管理器
├── plugin.py                # 插件系统 (生命周期钩子)
├── event_bus.py             # 事件总线
├── court_api.py             # FastAPI REST API + Dashboard 端点
├── dashboard_html.py        # Dashboard HTML 前端
├── api/                     # API 子模块
├── codex/                   # Codex 模块
├── core/                    # 核心抽象层
├── court/                   # 大臣管理模块
├── domains/                 # 领域模块
├── events/                  # 事件系统
├── evolution/               # 进化算法
├── hermes/                  # Hermes 通信模块
├── hermes_agent/            # Hermes Agent
├── knowledge/               # 知识库
├── memory/                  # 记忆模块
├── plugins/                 # 插件目录
├── sandbox/                 # 沙箱执行环境
└── vscode/                  # VSCode 集成
tests/
├── test_capability.py
├── test_cli.py
├── test_config.py
├── test_court.py
├── test_court_api.py
├── test_database.py
├── test_emperor.py
├── test_events.py
├── test_healing.py
├── test_health.py
├── test_plugin.py
├── test_scheduler.py
├── test_alerts.py
├── test_alerts_integration.py
├── test_builtin_alerts.py
├── test_builtin_plugins.py
├── test_breeding.py
├── test_calibration.py
├── test_censorate.py
├── test_codex.py
├── test_core.py
├── test_court_facade.py
├── test_court_integration.py
├── test_court_kg.py
├── test_court_orchestration.py
├── test_crossover.py
├── test_dashboard.py
├── test_diversity.py
├── test_domains.py
├── test_emperor_auto_start.py
├── test_emperor_builtin_plugins.py
├── test_emperor_evolve.py
├── test_emperor_plugins.py
├── test_event_bus.py
├── test_evolution.py
├── test_evolution_feedback.py
├── test_evolution_lifecycle.py
├── test_genome_injector.py
├── test_genome_store.py
├── test_healing_actions.py
├── test_hermes.py
├── test_hermes_agent.py
├── test_history.py
├── test_inspector.py
├── test_integration.py
├── test_knowledge.py
├── test_manual_task_api.py
├── test_memory.py
├── test_merit_board.py
├── test_ministers_api.py
├── test_orchestrator.py
├── test_providers.py
├── test_reflection.py
├── test_routing.py
├── test_scheduler_config_api.py
├── test_sliding_merit.py
├── test_sse_endpoint.py
├── test_stability_tracker.py
├── test_task_engine.py
├── test_task_feedback.py
├── test_vscode.py
├── test_workflow.py
└── test_adaptive_evolution_rate.py
```

## 配置参考

默认 `jarvis.yaml`：

```yaml
{
  "dashboard": {
    "host": "127.0.0.1",
    "port": 9020,
    "theme": "dark",
    "weather_city": "北京",
    "refresh_interval_seconds": 15
  },
  "scheduler": {
    "auto_schedule": true,
    "evolve_interval_minutes": 5.0,
    "task_interval_minutes": 3.0
  },
  "evolution": {
    "merit_delta_range": [-2, 2],
    "stability_delta_range": [-0.02, 0.02],
    "streak_bonus_threshold": 5
  },
  "capability": {
    "enabled_capabilities": ["datetime", "math", "random", "text", "file_info",
      "hash", "json_tool", "uuid_gen", "weather", "news", "web_search", "web_fetch"]
  },
  "database": {
    "db_path": "jarvis.db",
    "wal_mode": true,
    "max_history_rows": 10000
  },
  "seed_ministers": [
    {"name": "turing", "domain": "general"},
    {"name": "curie", "domain": "science"},
    {"name": "hinton", "domain": "data"},
    {"name": "bengio", "domain": "data"},
    {"name": "lecun", "domain": "code"},
    {"name": "goodfellow", "domain": "math"},
    {"name": "sutton", "domain": "general"},
    {"name": "silver", "domain": "general"}
  ],
  "max_ministers": 50
}
```

## API 端点

### Dashboard（Flask，端口 9020）

| 方法 | 路径 | 描述 |
|------|------|------|
| GET | / | Dashboard 主页 |
| GET | /api/status | 系统状态 |
| GET | /api/health | 系统健康指标 (CPU/内存/磁盘/运行时长) |
| GET | /api/dashboard/live | 天气 + 新闻实时数据 |
| GET | /api/dashboard/capability-stats | 能力命中统计 |
| GET | /api/tasks | 任务列表（支持 ?minister=&status=&search=&offset=） |
| GET | /api/ministers | 大臣列表 |
| POST | /api/ministers | 创建大臣 |
| PUT | /api/ministers/&lt;id&gt; | 更新大臣 |
| DELETE | /api/ministers/&lt;id&gt; | 删除大臣 |
| GET | /api/alerts | 告警列表（支持 ?status=&rule=&search=&offset=） |
| GET | /api/evolution | 进化历史 |
| POST | /api/evolve | 手动触发进化 |
| POST | /api/tasks/execute | 手动执行任务 |
| POST | /api/heal | 触发自愈 |
| PATCH | /api/scheduler | 调度器控制（暂停/恢复/调整间隔） |
| GET | /api/config | 当前配置 |
| POST | /api/theme | 切换主题 (dark/light/auto) |
| GET | /api/events | SSE 事件流 |
| GET | /dashboard/export | 导出数据 (JSON/CSV) |

### Court API（FastAPI，端口 8000）

| 方法 | 路径 | 描述 |
|------|------|------|
| GET | / | 服务健康检查 |
| GET | /court/summary | 宫廷摘要 |
| GET | /court/snapshot | 结构化宫廷状态 |
| GET | /court/history | 进化周期历史 |
| GET | /court/ministers | 大臣列表 |
| GET | /court/minister/{name} | 大臣详情 |
| POST | /court/register | 注册大臣 |
| POST | /court/register/batch | 批量注册大臣 |
| POST | /court/evolve | 运行 N 轮进化 |
| POST | /court/dispatch | 记录任务派遣结果 |
| POST | /court/feedback | 记录外部反馈 |
| POST | /court/genomes/save | 持久化基因组 |
| POST | /court/genomes/load | 从文件加载基因组 |
| POST | /court/config/load | 加载 YAML 配置 |
| GET | /court/config | 查看当前配置 |

## 技术栈

- **语言**：Python 3.11+
- **Web 框架**：Flask (Dashboard) + FastAPI (Court API)
- **数据库**：SQLite (WAL 模式)
- **可视化**：ECharts 5
- **SSE**：实时事件推送
- **跨平台**：Windows / Linux 双平台健康监控

## 快速开发

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行测试
python -m pytest tests/ -x -q --tb=short

# 代码格式化
black jarvis/ tests/
ruff check jarvis/ tests/

# 类型检查
mypy jarvis/
```

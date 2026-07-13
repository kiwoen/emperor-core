<div align="center">

# 🏯 Emperor

### Imperial Court AI Orchestrator · 八大臣协同的天子决策系统

[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org)
[![Tests](https://img.shields.io/badge/tests-311%20passed-success.svg)]()
[![License](https://img.shields.io/badge/license-MIT-green.svg)]()
[![Architecture](https://img.shields.io/badge/architecture-imperial--court-gold.svg)]()

> **"受命于天，统领八表；朝议百官，合成圣旨。"**
> *An Emperor listens to his ministers, weighs their counsel, and speaks the decree.*

**Emperor** 是一套以"中国古代朝堂"为隐喻的多域 AI 编排系统。用户的请求被视作「奏章」，
由「天子」（Emperor）拆解意图、量化大臣能力评分，并行分派给八位各怀绝技的「大臣」
（Minister）议事，最终合成「圣旨」（Edict）下达。八位大臣每一位都封装了当前市场
领先 AI 的核心优势——推理、审阅、搜索、代码、多模态、成本、科学、安全——让你
在本地即可调度一个「多模型联合体」，并通过天子持续从反馈中学习、不断自我进化。

</div>

---

## ✨ 核心理念

| 朝堂隐喻 | 现实映射 |
|----------|----------|
| **奏章** (Memorial) | 用户的自然语言请求 |
| **天子** (Emperor) | 中央编排器：意图拆解 + 大臣评分 + 议会召集 + 圣旨合成 |
| **大臣** (Minister) | 八位领域专家代理：各自封装一种顶尖 AI 能力 |
| **圣旨** (Edict) | 多大臣协同生成的最终答复 |
| **朝议** (Court) | 八大臣并行议事 + 反馈机制 |
| **自进化** (Evolution) | 天子从圣旨反馈中漂移大臣置信度、调整调度温度 |

---

## 🏛️ 朝堂架构

```
                          ┌──────────────────────┐
                          │   👑 天子 (Emperor)  │
                          │                      │
                          │  · 奏章拆解          │
                          │  · 大臣能力评分      │
                          │  · 自适应温度选择    │
                          └──────────┬───────────┘
                                     │  派发奏章
              ┌──────────┬───────────┼───────────┬──────────┐
              ▼          ▼           ▼           ▼          ▼
         ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐
         │ 丞相    │ │ 御史大夫│ │ 太史令  │ │ 工部尚书│ │ 太常    │
         │ 推理   │ │ 审阅    │ │ 搜索    │ │ 代码   │ │ 多模态  │
         │ GPT-5  │ │ Claude  │ │ Perpl.  │ │ DSeek  │ │ Gemini  │
         └────────┘ └────────┘ └────────┘ └────────┘ └────────┘
              ▼          ▼           ▼           ▼          ▼
              ┌──────────┬───────────┼───────────┬──────────┐
              ▼          ▼           ▼           ▼          ▼
         ┌────────┐ ┌────────┐ ┌────────┐
         │ 大司农  │ │ 太卜    │ │ 卫尉    │
         │ 成本   │ │ 科学    │ │ 安全    │
         │ DSeekV3│ │ o3     │ │ CAI     │
         └────────┘ └────────┘ └────────┘
                                     │
                          ┌──────────▼───────────┐
                          │  📜 圣旨 (Edict)     │
                          │  · 多臣协同答复      │
                          │  · 置信度综合        │
                          │  · 反馈回流          │
                          └──────────────────────┘
```

---

## 🎭 八大臣

| 官职 | 核心能力 | 借鉴模型 | 适用场景 |
|------|----------|----------|----------|
| **丞相** | 深度推理 | GPT-5 / o-series | 复杂逻辑、规划、策略推演 |
| **御史大夫** | 审阅润色 | Claude | 文档评审、措辞优化、批判性思维 |
| **太史令** | 实时搜索 | Perplexity | 事实查询、新闻、市场数据 |
| **工部尚书** | 代码工程 | DeepSeek-R1 | 编程、架构、调试、Bug 修复 |
| **太常** | 多模态 | Gemini | 图像理解、视觉推理、跨模态 |
| **大司农** | 低成本执行 | DeepSeek-V3 | 高吞吐、低优先级任务 |
| **太卜** | 科学推理 | o3 | 数学、物理、定理证明 |
| **卫尉** | 安全审计 | CAI | 内容审核、安全检查、红队测试 |

---

## 🚀 快速开始

```bash
git clone https://github.com/kiwoen/emperor-core.git
cd emperor-core
pip install -e ".[dev]"

# 运行全量测试
pytest tests/ -v

# 启动 CLI
python -m jarvis run

# 启动 API 服务
python -m jarvis serve
```

### CLI 命令

```bash
jarvis run      # 单次交互
jarvis chat     # 多轮对话
jarvis serve    # 启动 FastAPI + WebSocket
jarvis status   # 查看运行时状态
```

---

## 🧪 测试覆盖

```
311 passed in 4.82s
```

涵盖：
- 核心编排器、8 域模块、记忆引擎
- Hermes 消息总线、Codex 代码智能、VSCode 桥接
- MCP 桥接、EventStreamManager、KnowledgeGraph
- **朝堂系统（天子 + 八大臣）**：意图评分、并行分派、圣旨合成、反馈回流、自进化

---

## 📁 项目结构

```
emperor-core/
├── jarvis/
│   ├── core/                  # 核心编排 + 自进化
│   ├── domains/               # 8 域模块
│   ├── memory/                # 混合记忆引擎
│   ├── hermes/                # 消息总线
│   ├── codex/                 # 代码智能
│   ├── bridge/                # VSCode 桥接
│   ├── mcp/                   # MCP 桥接
│   ├── event_stream/          # WebSocket 事件流
│   ├── knowledge/             # 知识图谱
│   ├── court/                 # 🏯 朝堂系统
│   │   ├── emperor.py         # 天子
│   │   ├── minister.py        # 大臣基类
│   │   └── ministers/         # 8 位大臣
│   ├── integration/           # 统一运行时
│   ├── api/                   # FastAPI
│   └── cli.py                 # CLI 入口
├── tests/                     # 311 单元测试
└── pyproject.toml
```

---

## 🛠️ 技术栈

- **Python 3.11+** + `asyncio` 全异步
- **Pydantic v2** 数据契约
- **FastAPI** + WebSocket 实时推送
- **pytest** + asyncio 测试
- 可插拔：8 位大臣每位都可独立替换/扩展

---

## 🗺️ 路线图

- [x] 核心编排器 + 8 域模块
- [x] 混合记忆 + 自进化
- [x] Hermes 消息总线
- [x] Codex 代码智能
- [x] VSCode 桥接
- [x] MCP 桥接
- [x] EventStreamManager
- [x] KnowledgeGraph
- [x] **朝堂系统（天子 + 八大臣）**
- [ ] 朝堂接入 Orchestrator 调度管线
- [ ] 真实模型对接（GPT-5 / Claude / Perplexity …）
- [ ] Web UI（朝堂议事可视化）

---

## 📜 License

MIT

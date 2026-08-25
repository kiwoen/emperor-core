
# 幻炘AI 系统架构文档

> 版本: 0.1.0 | 最后更新: 2026-08-08

---

## 目录

1. [架构总览](#1-架构总览)
2. [五层架构详解](#2-五层架构详解)
3. [数据流描述](#3-数据流描述)
4. [模块依赖关系](#4-模块依赖关系)
5. [关键设计决策](#5-关键设计决策)

---

## 1. 架构总览

幻炘AI 采用五层分层架构，从顶层的控制平面到底层的持久化层，每层职责清晰且可独立演进。

```
┌──────────────────────────────────────────────────────────────────────┐
│                        HUANXIN-CORE ARCHITECTURE                      │
│                        (Five-Layer Design)                            │
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│ LAYER 1: CONTROL PLANE (控制平面)                                      │
│ ┌────────────┐ ┌─────────────┐ ┌──────────────┐ ┌───────────────┐   │
│ │  Huanxin    │ │ Governance   │ │  Approval     │ │  RBAC Engine  │   │
│ │(Orchestrator)│ │   Agent      │ │  Engine(HITL) │ │               │   │
│ └──────┬─────┘ └──────┬──────┘ └──────┬───────┘ └───────┬───────┘   │
│        │              │               │                  │           │
│ ┌──────┴──────┐ ┌─────┴──────┐ ┌──────┴───────┐ ┌───────┴───────┐   │
│ │BoundedAuto- │ │CostTracker │ │ContextVersio-│ │ Guardrail     │   │
│ │nomy Engine  │ │            │ │ning          │ │ Telemetry     │   │
│ └─────────────┘ └────────────┘ └──────────────┘ └───────────────┘   │
└──────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────┐
│ LAYER 2: EXECUTION ENGINE (执行引擎)                                   │
│ ┌────────────┐ ┌────────────┐ ┌─────────────┐ ┌───────────────┐     │
│ │ Workflow   │ │ Task Router │ │ State        │ │ Pipeline       │     │
│ │ Engine(DAG)│ │(IntentBased)│ │ Machine      │ │ Engine         │     │
│ └──────┬─────┘ └─────┬──────┘ └──────┬──────┘ └───────┬───────┘     │
│        │             │              │                 │              │
│ ┌──────┴──────┐ ┌────┴──────┐ ┌────┴──────┐ ┌───────┴───────┐      │
│ │ Reflexion   │ │Loop Guard │ │Hallucinat- │ │ Context       │      │
│ │ Engine      │ │           │ │ion Guard   │ │ Compressor    │      │
│ └─────────────┘ └───────────┘ └────────────┘ └───────────────┘      │
└──────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────┐
│ LAYER 3: EVOLUTION ENGINE (进化引擎)                                   │
│ ┌────────────┐ ┌────────────┐ ┌─────────────┐ ┌───────────────┐     │
│ │  Court     │ │ Consensus  │ │  Merit       │ │ Breeding      │     │
│ │ (宫廷)     │ │  Engine    │ │  Board       │ │ Engine        │     │
│ └──────┬─────┘ └─────┬──────┘ └──────┬──────┘ └───────┬───────┘     │
│        │             │              │                 │              │
│ ┌──────┴──────┐ ┌────┴──────┐ ┌────┴──────┐ ┌───────┴───────┐      │
│ │Evolution    │ │Sliding    │ │Diversity   │ │ Censorate     │      │
│ │History      │ │Merit      │ │Monitor     │ │               │      │
│ └─────────────┘ └───────────┘ └────────────┘ └───────────────┘      │
│                                                                      │
│ ┌──────────────────────────────────────────────────────────────┐    │
│ │                     8 Ministers (大臣)                        │    │
│ │  turing│curie│hinton│bengio│lecun│goodfellow│sutton│silver  │    │
│ │  Domain: general / science / data / code / math               │    │
│ └──────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────┐
│ LAYER 4: CAPABILITY LAYER (能力层)                                     │
│ ┌────────────┐ ┌────────────┐ ┌─────────────┐ ┌───────────────┐     │
│ │MCP Manager │ │Multimodal  │ │RAG Engine   │ │Capability     │     │
│ │            │ │Engine      │ │(Hybrid)     │ │Registry (12)  │     │
│ └──────┬─────┘ └─────┬──────┘ └──────┬──────┘ └───────┬───────┘     │
│        │             │              │                 │              │
│ ┌──────┴──────┐ ┌────┴──────┐ ┌────┴──────┐ ┌───────┴───────┐      │
│ │ Memory      │ │GraphRAG   │ │Sandbox    │ │ Plugin System  │      │
│ │ Engine      │ │(KG)       │ │Manager    │ │ (3x Manager)   │      │
│ └─────────────┘ └───────────┘ └────────────┘ └───────────────┘      │
└──────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────┐
│ LAYER 5: PERSISTENCE LAYER (持久化层)                                  │
│ ┌────────────┐ ┌────────────┐ ┌─────────────┐ ┌───────────────┐     │
│ │  Database  │ │  Audit     │ │  Genome      │ │  Eval         │     │
│ │  (SQLite)  │ │  Logger    │ │  Store       │ │  Runner       │     │
│ └────────────┘ └────────────┘ └─────────────┘ └───────────────┘     │
└──────────────────────────────────────────────────────────────────────┘

                           EXTERNAL INTERFACES
┌───────────────────┐  ┌───────────────────┐  ┌───────────────────────┐
│   Dashboard (9020)│  │  Court API (8000) │  │  CLI (huanxin/emperor) │
│   FastAPI + ECharts│ │  FastAPI REST     │  │  Click-based          │
└───────────────────┘  └───────────────────┘  └───────────────────────┘
```

---

## 2. 五层架构详解

### Layer 1: 控制平面 (Control Plane)

负责系统的全局管控、安全合规与决策审计。

| 模块 | 职责 | 关键类 |
|------|------|--------|
| **Huanxin** | 顶层编排器，统一入口，管理所有子系统的生命周期 | `Huanxin` |
| **GovernanceAgent** | "监控 Agent 的 Agent"，策略合规、RBAC、监管规则检查 | `GovernanceAgent`, `GovernanceRule` |
| **ApprovalEngine** | HITL 人工审批门控，高风险操作必须经过审批 | `ApprovalEngine` |
| **RBACEngine** | 基于角色的访问控制 | `RBACEngine` |
| **BoundedAutonomyEngine** | 三区（GREEN/YELLOW/RED）操作空间分类器 | `BoundedAutonomyEngine` |
| **CostTracker** | 每次 LLM 调用成本记录与统计 | `CostTracker`, `CostPerSuccessTracker` |
| **ContextVersioning** | 不可变状态快照与回滚 | `ContextVersioning` |
| **GuardrailTelemetry** | OTel 风格的护栏事件可观测性 | `guardrail_telemetry` |

### Layer 2: 执行引擎 (Execution Engine)

负责任务的接收、路由、执行和质量保障。

| 模块 | 职责 | 关键类 |
|------|------|--------|
| **WorkflowEngine** | DAG 多步骤任务编排 | `WorkflowEngine` |
| **TaskRouter** | 意图分类 + 多级路由 | `RouterEngine`, `IntentClassifier` |
| **StateMachine** | LangGraph 风格有状态执行引擎 | `StateMachine`, `State`, `Transition` |
| **Pipeline** | 端到端服务流水线 | `ServicePipeline`, `Stage` |
| **ReflexionEngine** | 输出质量自我反思 + 自动修正 | `ReflexionEngine` |
| **LoopGuard** | 无界循环熔断保护 | `AgentLoopGuard` |
| **HallucinationGuard** | LLM 输出事实性校验 | `HallucinationGuard` |
| **ContextCompressor** | 长对话历史压缩管理 | `ContextCompressor` |

### Layer 3: 进化引擎 (Evolution Engine)

实现多大臣协同进化、功绩评估和新大臣育种。

| 模块 | 职责 | 关键类 |
|------|------|--------|
| **Court** | 进化宫廷统一入口，协调所有进化子模块 | `Court` |
| **ConsensusEngine** | 多大臣辩论共识形成 | `ConsensusEngine` |
| **MeritBoard** | 金银铜功绩排行榜 | `MeritBoard` |
| **SlidingMeritBoard** | 滑动窗口功绩计算（指数衰减） | `SlidingMeritBoard` |
| **BreedingEngine** | 大臣育种（基于基因组交叉+变异） | `BreedingEngine` |
| **SurvivalMechanism** | 生存机制：Crossover / Elitism / Turnover | `SurvivalMechanism` |
| **EvolutionHistory** | 进化历史记录与 CSV/JSON 导出 | `EvolutionHistory` |
| **DiversityMonitor** | 多样性监控（防止大臣同质化） | `DiversityMonitor` |
| **Censorate** | 御史台（对大臣输出进行审查） | `Censorate` |
| **GenomeStore** | 基因组持久化（加载/保存） | `GenomeStore` |
| **CourtInspector** | 宫廷健康检查 | `CourtInspector` |

### Layer 4: 能力层 (Capability Layer)

提供系统的底层能力支持，包括工具调用、多模态处理、知识检索等。

| 模块 | 职责 | 关键类 |
|------|------|--------|
| **CapabilityRegistry** | 12 个内置能力处理器注册表 | `CapabilityRegistry` |
| **MCPManager** | 多 MCP Server 统一管理（含 3 个内置 Mock Server） | `MCPManager` |
| **MultimodalEngine** | 图像/语音/文档统一处理 | `MultimodalEngine` |
| **RAGEngine** | 检索增强生成（Dense+Sparse+RRF+Rerank） | `RAGEngine`, `HybridRetriever` |
| **MemoryEngine** | ChromaDB + TF-IDF + Jaccard 三级混合检索 | `MemoryEngine`, `VectorMemory` |
| **GraphRAG** | 知识图谱记忆引擎 | `GraphRAG` |
| **SandboxManager** | 安全沙箱代码执行 | `SandboxManager` |
| **Plugin System** | 生命周期钩子 + 热加载 + 插件市场 | 3 个 PluginManager |

### Layer 5: 持久化层 (Persistence Layer)

负责系统状态的持久化和可审计性。

| 模块 | 职责 | 关键类 |
|------|------|--------|
| **Database** | SQLite (WAL 模式) 自动落库 | `Database` |
| **AuditLogger** | 不可变审计日志（每次操作都有记录） | `AuditLogger` |
| **GenomeStore** | 大臣基因组持久化存储 | `GenomeStore` |
| **EvalRunner** | 评估回归测试运行器 | `EvalRunner` |

---

## 3. 数据流描述

### 3.1 任务执行主流程

```
User / CLI / API
       │
       ▼
┌──────────────┐
│   Huanxin     │  ← 统一入口
│ .execute_task │
└──────┬───────┘
       │
       ▼
┌──────────────┐     ┌─────────────┐
│  TaskRouter   │────▶│ IntentClass │  ← 意图分类
│  .route()     │     └─────────────┘
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ Governance    │  ← 策略合规检查
│ .validate()   │
└──────┬───────┘
       │ (GREEN → auto-execute; YELLOW → approval; RED → blocked)
       ▼
┌──────────────┐
│ Bounded       │  ← 三区分类
│ Autonomy      │
└──────┬───────┘
       │
       ▼
┌──────────────┐     ┌─────────────┐     ┌──────────────┐
│  StateMachine │────▶│  Planning   │────▶│  Execution    │
│ (Lifecycle)   │     │             │     │ (Capability)  │
└──────────────┘     └─────────────┘     └──────┬───────┘
                                                 │
                        ┌────────────────────────┘
                        ▼
              ┌──────────────┐
              │  Reflexion    │  ← 质量检查 + 自修正
              │  .reflect()   │
              └──────┬───────┘
                     │
                     ▼
              ┌──────────────┐
              │ Hallucination  │  ← 事实性校验
              │ Guard          │
              └──────┬───────┘
                     │
                     ▼
              ┌──────────────┐
              │  Audit         │  ← 不可变日志
              │  Logger        │
              └──────┬───────┘
                     │
                     ▼
              ┌──────────────┐
              │  Task Result   │  → 返回 User
              └──────────────┘
```

### 3.2 进化与自愈循环

```
Scheduler (定时触发)
       │
       ▼
┌──────────────┐
│  Huanxin      │
│  .evolve()    │
└──────┬───────┘
       │
       ▼
┌──────────────┐     ┌─────────────┐     ┌──────────────┐
│  Court         │────▶│  MeritBoard  │────▶│  Survival     │
│  .evolve()     │     │  .evaluate() │     │  .crossover() │
└──────────────┘     └─────────────┘     └──────┬───────┘
                                                 │
                        ┌────────────────────────┘
                        ▼
              ┌──────────────┐
              │  Breeding      │  ← 产生新大臣
              │  Engine        │
              └──────┬───────┘
                     │
                     ▼
              ┌──────────────┐
              │  Diversity     │  ← 检查多样性
              │  Monitor       │
              └──────┬───────┘
                     │
                     ▼
              ┌──────────────┐
              │  Evolution     │
              │  History       │
              └──────────────┘

       ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─

Self-Healing (告警触发)
       │
       ▼
┌──────────────┐
│  AlertManager  │  ← 告警规则匹配
│  .evaluate()   │
└──────┬───────┘
       │ (冷却检查)
       ▼
┌──────────────┐
│  HealingEngine │  ← 诊断 + 执行自愈动作
│  .diagnose()   │
│  .heal()       │
└──────────────┘
```

### 3.3 多大臣共识流程

```
Task / Question
       │
       ▼
┌──────────────┐
│ Consensus      │
│ Engine         │
│ .deliberate()  │
└──────┬───────┘
       │
       ├──▶ Minister A (Process + Output)
       ├──▶ Minister B (Process + Output)
       └──▶ Minister C (Process + Output)
              │
              ▼
       ┌──────────────┐
       │ Cross-Critique │  ← 互相评审
       └──────┬───────┘
              │
              ▼
       ┌──────────────┐
       │ Strategy Apply │  ← 投票 / 加权 / 辩论合成
       └──────┬───────┘
              │
              ▼
       ┌──────────────┐
       │ Consensus      │
       │ Result         │
       └──────────────┘
```

### 3.4 成本感知路由流程

```
User Prompt
       │
       ▼
┌──────────────┐
│ ModelRouter    │  ← 零成本规则分类
│ .classify()    │         (纯正则，不调用 LLM)
└──────┬───────┘
       │
       ├── cheap   → gpt-4o-mini / claude-haiku
       ├── standard → gpt-4o / claude-sonnet
       └── premium  → gpt-4o / claude-opus
              │
              ▼
┌──────────────┐     ┌──────────────┐
│ MultiModel     │────▶│ CostTracker   │  ← 记录每次调用成本
│ Router         │     │ .record()     │
│ (并行/集成)    │     └──────────────┘
└──────────────┘
```

---

## 4. 模块依赖关系

```
                        ┌──────────────┐
                        │   Huanxin    │
                        │ (Top-Level)  │
                        └──────┬───────┘
            ┌──────────────────┼───────────────────┐
            ▼                  ▼                    ▼
    ┌──────────────┐  ┌──────────────┐    ┌──────────────┐
    │  Execution    │  │  Evolution    │    │  Capability   │
    │  Layer        │  │  Layer        │    │  Layer        │
    └──────┬───────┘  └──────┬───────┘    └──────┬───────┘
           │                 │                    │
           └────────┬────────┘                    │
                    │                             │
                    ▼                             │
            ┌──────────────┐                     │
            │  Governance   │◄────────────────────┘
            │  + RBAC       │
            └──────┬───────┘
                   │
                   ▼
            ┌──────────────┐
            │  Persistence  │
            │  Layer        │
            └──────────────┘

Module Dependency Graph:

Huanxin
 ├── Config (huanxin.yaml)
 ├── Court
 │    ├── MeritBoard
 │    ├── SlidingMeritBoard
 │    ├── SurvivalMechanism
 │    ├── BreedingEngine
 │    ├── GenomeStore
 │    ├── EvolutionHistory
 │    ├── DiversityMonitor
 │    ├── Censorate
 │    └── CourtInspector
 ├── ConsensusEngine
 │    └── Strategies (MajorityVote / WeightedVote / DeliberativeSynthesis)
 ├── TaskEngine
 ├── CapabilityRegistry
 ├── MCPManager (MCP Client + Mock Servers)
 ├── MultimodalEngine
 │    ├── VisionProcessor
 │    ├── SpeechProcessor
 │    └── DocumentProcessor
 ├── RAGEngine
 │    └── HybridRetriever (ChromaDB + BM25 + RRF + LLM Rerank)
 ├── MemoryEngine / VectorMemory / MemoryManager
 ├── GraphRAG
 ├── SandboxManager
 ├── GovernanceAgent
 ├── BoundedAutonomyEngine
 ├── ApprovalEngine
 ├── ReflexionEngine
 ├── StateMachine
 ├── ModelRouter / MultiModelRouter / SmartRouter
 ├── CostTracker / CostPerSuccessTracker
 ├── ContextCompressor
 ├── ContextVersioning
 ├── HallucinationGuard
 ├── LoopGuard
 ├── AuditLogger
 ├── PluginManager / PluginSystem / PluginMarketplace
 ├── RBACEngine
 ├── TaskRouter (RouterEngine)
 ├── WorkflowEngine
 ├── ServicePipeline
 ├── HealingEngine / HealingActions
 ├── AlertManager
 ├── PromptTemplateManager
 ├── EvalRunner
 ├── GuardrailTelemetry
 └── Database (SQLite)
```

---

## 5. 关键设计决策

### 5.1 五层分层架构

- **每层单一职责**：控制平面管安全、执行引擎管调度、进化引擎管优化、能力层管功能、持久化层管存储
- **层间松耦合**：通过接口/抽象类通信，任一层可独立替换
- **自顶向下依赖**：上层依赖下层，下层不感知上层（Clean Architecture）

### 5.2 Safety-by-Design 安全链路

```
每个操作必经的安全链路：
User Input → TaskRouter → Governance → BoundedAutonomy → Execute → Reflexion → HallGuard → Audit
```

- 无任何操作可以跳过 Governance Gate
- 所有高风险操作必须走 ApprovalEngine
- 所有操作均有不可变审计日志

### 5.3 自进化闭环

```
Execute Task → Record Outcome → Update Merit → Evolve Ministers → Better Performance
     ▲                                                                    │
     └──────────────────────── Feedback Loop ───────────────────────────┘
```

### 5.4 成本感知

- ModelRouter 零成本复杂度分类（纯正则匹配，不调用 LLM）
- MultiModelRouter 支持按成本策略选择模型
- CostTracker 记录每次调用的精确成本
- CostPerSuccessTracker 追踪 CPS (Cost Per Success) 指标

### 5.5 多模型并行与集成

- 支持 DeepSeek V3/R1、GPT-4o、Claude 等多模型
- 并行调优模式：对比多个模型输出
- 集成模式：投票产生最优结果
- 策略路由：按任务类型自动选择最优模型组合

### 5.6 模块化与可扩展

- 大臣系统支持热注册/注销
- 插件系统支持 10+ 生命周期钩子
- MCP 协议兼容，支持接入外部工具
- 能力注册表支持动态扩展
- 工作流 DAG 支持任意步骤编排


# Emperor-Core API Reference

> 版本: 0.1.0 | 最后更新: 2026-08-08

---

## 目录

1. [核心编排器 — Emperor](#1-核心编排器--emperor)
2. [Court 进化宫廷](#2-court-进化宫廷)
3. [共识引擎 — Consensus](#3-共识引擎--consensus)
4. [流水线引擎 — Pipeline](#4-流水线引擎--pipeline)
5. [治理代理 — GovernanceAgent](#5-治理代理--governanceagent)
6. [有界自治 — BoundedAutonomy](#6-有界自治--boundedautonomy)
7. [反思引擎 — Reflexion](#7-反思引擎--reflexion)
8. [状态机 — StateMachine](#8-状态机--statemachine)
9. [自愈引擎 — Healing](#9-自愈引擎--healing)
10. [能力系统 — Capability](#10-能力系统--capability)
11. [MCP 管理器](#11-mcp-管理器)
12. [多模态引擎 — Multimodal](#12-多模态引擎--multimodal)
13. [RAG 引擎](#13-rag-引擎)
14. [记忆系统 — Memory](#14-记忆系统--memory)
15. [知识图谱 RAG — GraphRAG](#15-知识图谱-rag--graphrag)
16. [模型路由 — Router](#16-模型路由--router)
17. [多模型路由 — MultiModelRouter](#17-多模型路由--multimodelrouter)
18. [审计日志 — Audit](#18-审计日志--audit)
19. [审批引擎 — Approval](#19-审批引擎--approval)
20. [幻觉守卫 — HallucinationGuard](#20-幻觉守卫--hallucinationguard)
21. [循环守卫 — LoopGuard](#21-循环守卫--loopguard)
22. [上下文压缩 — ContextCompressor](#22-上下文压缩--contextcompressor)
23. [上下文版本控制 — ContextVersioning](#23-上下文版本控制--contextversioning)
24. [评估运行器 — EvalRunner](#24-评估运行器--evalrunner)
25. [沙箱 — Sandbox](#25-沙箱--sandbox)
26. [插件系统 — Plugin](#26-插件系统--plugin)
27. [成本追踪 — CostTracker](#27-成本追踪--costtracker)
28. [提示词模板 — PromptTemplate](#28-提示词模板--prompttemplate)
29. [任务路由器 — TaskRouter](#29-任务路由器--taskrouter)
30. [工作流引擎 — WorkflowEngine](#30-工作流引擎--workflowengine)
31. [配置系统 — Config](#31-配置系统--config)
32. [Dashboard API](#32-dashboard-api)

---

## 1. 核心编排器 — Emperor

`jarvis.emperor.Emperor` — 一站式 AI 系统编排器，集成 Court / TaskEngine / REST API / CLI。

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| `__init__` | `(config: Optional[EmperorConfig] = None, config_path: Optional[str] = None)` | — | 初始化 Emperor，自动加载 `jarvis.yaml` |
| `register` | `(name: str, domain: str = "general", **kwargs)` | `Minister` | 注册新大臣 |
| `evolve` | `(cycles: int = 1)` | `EvolutionResult` | 运行 N 轮进化 |
| `execute_task` | `(task: str, domain: str = "general")` | `TaskResult` | 执行任务 |
| `status` | `()` | `dict` | 系统状态摘要 |
| `serve` | `(host: str = "127.0.0.1", port: int = 9020)` | — | 启动 Dashboard + 调度器 |
| `stop` | `()` | — | 停止所有后台服务 |

**属性（Property）**:

| 属性 | 类型 | 说明 |
|------|------|------|
| `court` | `Court` | 底层进化宫廷 |
| `task_engine` | `TaskEngine` | 任务引擎 |
| `plugins` | `PluginManager` | 生命周期插件管理器 |
| `capability_registry` | `CapabilityRegistry` | 能力注册表 |
| `audit_logger` | `AuditLogger` | 审计日志器 |
| `eval_runner` | `EvalRunner` | 评估运行器 |
| `plugin_marketplace` | `PluginMarketplace` | 插件市场 |
| `plugin_system` | `PluginSystemManager` | 热加载插件系统 |
| `sandbox_manager` | `SandboxManager` | 沙箱管理器 |
| `versioning` | `ContextVersioning` | 上下文版本控制 |
| `template_manager` | `PromptTemplateManager` | 提示词模板管理器 |
| `model_router` | `ModelRouter` | 成本感知模型路由器 |
| `multi_model_router` | `MultiModelRouter` | 多模型并行/集成路由 |
| `cost_tracker` | `CostTracker` | 成本追踪器 |
| `smart_router` | `SmartRouter` | 智能路由（能力感知） |
| `cost_per_success` | `CostPerSuccessTracker` | 单次成功成本追踪 |
| `graph_rag` | `GraphRAG` | 知识图谱 RAG 引擎 |
| `approval_engine` | `ApprovalEngine` | HITL 审批引擎 |
| `mcp_manager` | `MCPManager` | MCP 服务器管理器 |
| `reflexion_engine` | `ReflexionEngine` | 自我反思引擎 |
| `state_machine` | `StateMachine` | 状态机工作流 |
| `rbac_engine` | `RBACEngine` | 角色访问控制 |
| `context_compressor` | `ContextCompressor` | 上下文压缩器 |
| `hallucination_guard` | `HallucinationGuard` | 幻觉守卫 |
| `loop_guard` | `AgentLoopGuard` | 循环熔断守卫 |
| `task_router` | `RouterEngine` | 意图分类任务路由器 |
| `workflow_engine` | `WorkflowEngine` | DAG 工作流引擎 |

---

## 2. Court 进化宫廷

`jarvis.court.court.Court` — 进化系统统一入口。

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| `__init__` | `(config: Optional[CourtConfig] = None)` | — | 初始化 |
| `register` | `(name: str, domain: str, temperature: float = 0.7)` | `Minister` | 注册大臣 |
| `register_batch` | `(ministers: list[dict])` | `list[Minister]` | 批量注册 |
| `evolve` | `(cycles: int = 1)` | `EvolutionResult` | 进化 N 轮 |
| `dispatch` | `(task: str, minister_name: str)` | `TaskResult` | 派遣任务 |
| `summary` | `()` | `CourtSummary` | 宫廷状态摘要 |
| `snapshot` | `()` | `dict` | 结构化快照 |

### CourtConfig

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `min_ministers` | `int` | `3` | 最少大臣数 |
| `max_ministers` | `int` | `20` | 最多大臣数 |
| `elitism_count` | `int` | `2` | 精英保留数 |
| `crossover_rate` | `float` | `0.6` | 交叉率 |
| `enable_auto_breeding` | `bool` | `True` | 自动育种 |
| `stability_blend` | `float` | `0.20` | 稳定度混合系数 |

### 子模块

| 模块 | 主要类 | 职责 |
|------|--------|------|
| `jarvis.court.merit_board` | `MeritBoard` | 功绩排行榜 |
| `jarvis.court.sliding_merit` | `SlidingMeritBoard` | 滑动窗口功绩计算 |
| `jarvis.court.evolution` | `SurvivalMechanism` | 生存机制（Crossover / Elitism） |
| `jarvis.court.history` | `EvolutionHistory` | 进化历史记录与导出 |
| `jarvis.court.inspector` | `CourtInspector` | 宫廷健康检查 |
| `jarvis.court.breeding` | `BreedingEngine` | 大臣育种（新大臣生成） |
| `jarvis.court.genome_store` | `GenomeStore` | 基因组持久化 |
| `jarvis.court.task_engine` | `TaskEngine` | 任务执行引擎 |
| `jarvis.court.diversity` | `DiversityMonitor` | 多样性监控 |
| `jarvis.court.censorate` | `Censorate` | 御史台（输出审查） |

---

## 3. 共识引擎 — Consensus

`jarvis.consensus.engine.ConsensusEngine` — 多大臣辩论与共识形成。

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| `__init__` | `(court: Court, config: Optional[ConsensusConfig] = None)` | — | 初始化 |
| `deliberate` | `(task: str, domain: str, task_executor: Callable)` | `ConsensusResult` | 多大臣辩论达成共识 |

### ConsensusConfig

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `num_ministers` | `int` | `3` | 参与大臣数 |
| `critique_rounds` | `int` | `1` | 交叉评审轮次 |
| `require_critique` | `bool` | `True` | 是否要求交叉评审 |
| `strategy` | `Optional[ConsensusStrategy]` | `MajorityVote` | 共识策略 |

### 共识策略

| 策略 | 类 | 说明 |
|------|----|------|
| 多数投票 | `MajorityVote` | 简单多数获胜 |
| 加权投票 | `WeightedVote` | 按大臣声望加权 |
| 辩论合成 | `DeliberativeSynthesis` | LLM 辩论后合成答案 |

---

## 4. 流水线引擎 — Pipeline

`jarvis.pipeline` — 将能力串联成端到端自动化服务链。

### 主要类

| 类 | 说明 |
|----|------|
| `Stage` | 流水线阶段定义（名称 + 处理函数） |
| `ServicePipeline` | 流水线执行器 |
| `StageResult` | 阶段执行结果 |
| `PipelineResult` | 流水线执行结果 |
| `PipelineRegistry` | 流水线注册表 |

### ServicePipeline

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| `add_stage` | `(stage: Stage)` | — | 添加阶段 |
| `execute` | `(input_data: dict)` | `PipelineResult` | 执行流水线 |
| `pause` | `()` | — | 暂停 |
| `resume` | `()` | — | 恢复 |
| `reset` | `()` | — | 重置 |

### StageStatus / PipelineStatus

| 枚举 | 值 | 说明 |
|------|----|------|
| `StageStatus.PENDING` | `"pending"` | 等待执行 |
| `StageStatus.RUNNING` | `"running"` | 执行中 |
| `StageStatus.SUCCESS` | `"success"` | 执行成功 |
| `StageStatus.FAILED` | `"failed"` | 执行失败 |
| `PipelineStatus.IDLE` | `"idle"` | 空闲 |
| `PipelineStatus.RUNNING` | `"running"` | 运行中 |
| `PipelineStatus.COMPLETED` | `"completed"` | 已完成 |
| `PipelineStatus.FAILED` | `"failed"` | 失败 |

---

## 5. 治理代理 — GovernanceAgent

`jarvis.governance_agent.GovernanceAgent` — 监控 Agent 的 Agent。

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| `__init__` | `(audit_logger, approval_engine)` | — | 初始化 |
| `register_rule` | `(rule: GovernanceRule)` | — | 注册治理规则 |
| `unregister_rule` | `(name: str)` | — | 移除规则 |
| `validate` | `(action: dict, context: dict)` | `GovernanceResult` | 验证操作合规性 |

### GovernanceRule

| 参数 | 类型 | 说明 |
|------|------|------|
| `name` | `str` | 规则名称 |
| `rule_type` | `str` | 类型：`policy_compliance` / `rbac` / `regulatory` / `business_logic` |
| `priority` | `int` | 优先级：CRITICAL(4) > HIGH(3) > MEDIUM(2) > LOW(1) |
| `check_fn` | `Callable` | 检查函数 |
| `description` | `str` | 规则描述 |

### GovernanceResult

| 字段 | 类型 | 说明 |
|------|------|------|
| `passed` | `bool` | 是否通过 |
| `blocked` | `bool` | 是否被阻止 |
| `needs_approval` | `bool` | 是否需要审批 |
| `violations` | `list[dict]` | 违规详情 |

---

## 6. 有界自治 — BoundedAutonomy

`jarvis.bounded_autonomy.BoundedAutonomyEngine` — 三区操作空间分类器。

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| `__init__` | `(governance_agent, approval_engine)` | — | 初始化 |
| `classify` | `(action: dict)` | `ActionZone` | 分类操作到自治区域 |
| `register_space` | `(space: ActionSpace)` | — | 注册自定义区域规则 |

### ActionZone

| 值 | 说明 |
|----|------|
| `GREEN` | 自动执行（安全操作） |
| `YELLOW` | 需人工审批 |
| `RED` | 禁止 / 需要显式覆盖 |

---

## 7. 反思引擎 — Reflexion

`jarvis.reflexion.ReflexionEngine` — Agent 输出质量自我反思层。

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| `__init__` | `(threshold: float = 0.6, max_retries: int = 3)` | — | 初始化 |
| `reflect` | `(task_id, prompt, response, domain)` | `ReflectionResult` | 质量检查 + 自动修正 |

### ReflectionResult

| 字段 | 类型 | 说明 |
|------|------|------|
| `confidence` | `float` | 置信度 (0.0~1.0) |
| `issues` | `list[ReflectionIssue]` | 诊断出的问题 |
| `corrected` | `bool` | 是否触发了自动修正 |
| `corrected_output` | `Optional[str]` | 修正后输出 |

### CheckType

| 值 | 说明 |
|----|------|
| `COMPLETENESS` | 完整性检查 |
| `INTEGRITY` | 内部一致性检查 |
| `FACTUALITY` | 事实性检查 |

---

## 8. 状态机 — StateMachine

`jarvis.state_machine.StateMachine` — LangGraph 风格的有状态编排引擎。

### 导出函数

| 函数 | 返回值 | 说明 |
|------|--------|------|
| `create_dispatch_workflow()` | `StateMachine` | 创建 dispatch 工作流（planning→exec→reflection→completion） |
| `create_error_recovery_workflow()` | `StateMachine` | 创建错误恢复工作流（error→diagnose→retry→escalate） |
| `list_workflow_templates()` | `list[str]` | 列出可用工作流模板 |
| `execute_workflow(sm, initial_state, data)` | `StateMachineContext` | 执行工作流 |

### StateMachine

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| `add_state` | `(state: State)` | — | 添加状态节点 |
| `add_transition` | `(transition: Transition)` | — | 添加状态转换 |
| `start` | `(state_name: str, data: dict)` | `StateMachineContext` | 启动状态机 |
| `trigger` | `(event: str, ctx: StateMachineContext)` | `StateMachineContext` | 触发转换 |

---

## 9. 自愈引擎 — Healing

`jarvis.healing.HealingEngine` — 系统自愈引擎。

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| `__init__` | `(court, alert_manager, scheduler)` | — | 初始化 |
| `diagnose` | `()` | `list[Diagnosis]` | 系统诊断 |
| `heal` | `()` | `list[HealResult]` | 执行自愈 |
| `register_action` | `(action: HealingAction)` | — | 注册自愈动作 |

### 内置自愈动作 (jarvis.healing_actions)

| 动作 | 说明 |
|------|------|
| `restart_scheduler` | 重启调度器 |
| `emergency_evolve` | 紧急进化 |
| `purge_stale_ministers` | 清理停滞大臣 |
| `reset_alert_cooldowns` | 重置告警冷却 |
| `rebalance_merit` | 重新平衡功绩 |

---

## 10. 能力系统 — Capability

`jarvis.capability` — 12 个内置能力处理器的注册表。

### CapabilityRegistry

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| `register` | `(capability: Capability)` | — | 注册能力 |
| `get` | `(name: str)` | `Optional[Capability]` | 获取能力 |
| `list_all` | `()` | `list[Capability]` | 列出所有 |
| `dispatch` | `(name: str, args: dict)` | `CapabilityResult` | 调用能力 |

### 内置能力

| 名称 | 描述 | 类别 |
|------|------|------|
| `datetime` | 日期时间查询 | 系统 |
| `math` | AST 安全数学求值 | 计算 |
| `random` | 随机数/骰子 | 工具 |
| `text` | 文本统计/处理 | 工具 |
| `file_info` | 文件信息查询 | 系统 |
| `hash` | MD5/SHA256 校验 | 工具 |
| `json_tool` | JSON 格式化/解析 | 工具 |
| `uuid_gen` | UUID 生成 | 工具 |
| `weather` | 实时天气 | 网络 |
| `news` | 新闻摘要 | 网络 |
| `web_search` | 网页搜索 | 网络 |
| `web_fetch` | 网页抓取 | 网络 |

---

## 11. MCP 管理器

`jarvis.mcp_manager.MCPManager` — 多 MCP Server 统一管理器。

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| `register_server` | `(config: MCPServerConfig)` | — | 注册外部 MCP Server |
| `register_builtin_mock_servers` | `()` | — | 注册内置模拟 Server |
| `get_all_tools` | `()` | `list[MCPTool]` | 获取所有可用工具 |
| `discover_and_call` | `(tool_name: str, args: dict)` | `Any` | 发现并调用工具 |
| `remove_server` | `(server_name: str)` | — | 移除 Server |

### 内置 Mock Server

| Mock Server | 模拟功能 |
|-------------|---------|
| `MockFilesystemServer` | 文件系统操作（read/write/list） |
| `MockWebSearchServer` | Web 搜索结果 |
| `MockCalculatorServer` | 数学计算 |

---

## 12. 多模态引擎 — Multimodal

`jarvis.multimodal` — 图像/语音/文档统一处理。

### MultimodalEngine

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| `see` | `(image_path: str, prompt: str = "")` | `str` | 图像理解 |
| `hear` | `(audio_path: str)` | `str` | 语音转文本 (STT) |
| `speak` | `(text: str, output_path: str)` | `str` | 文本转语音 (TTS) |
| `read_document` | `(doc_path: str)` | `str` | 文档内容提取 |

### VisionProcessor

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| `analyze` | `(image_path: str, query: str)` | `str` | 图像分析 |

### SpeechProcessor

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| `transcribe` | `(audio_path: str)` | `str` | 语音转文本 |
| `synthesize` | `(text: str, output_path: str)` | `str` | 文本转语音 |

### DocumentProcessor

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| `extract` | `(doc_path: str)` | `str` | PDF/DOCX/OCR 提取文本 |

---

## 13. RAG 引擎

`jarvis.rag` — 检索增强生成完整流水线。

### RAGEngine

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| `load_documents` | `(paths: list[str])` | — | 加载文档 |
| `index` | `()` | — | 构建索引 |
| `query` | `(question: str, top_k: int = 5)` | `RAGResult` | 检索 + 生成 |
| `retrieve_only` | `(query: str, top_k: int = 5)` | `list[Document]` | 仅检索 |

### HybridRetriever

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| `retrieve` | `(query: str, top_k: int)` | `list[Document]` | Dense + Sparse + RRF 融合 + LLM Rerank |

### DocumentLoader / RecursiveCharacterTextSplitter

| 类 | 说明 |
|----|------|
| `DocumentLoader` | 加载 PDF/DOCX/TXT/MD 文件 |
| `RecursiveCharacterTextSplitter` | 语义级递归文本切割 |

---

## 14. 记忆系统 — Memory

`jarvis.memory` — ChromaDB + TF-IDF + Jaccard 三级混合检索。

### MemoryEngine

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| `store` | `(entry: MemoryEntry)` | — | 存储记忆 |
| `recall` | `(query: str, top_k: int = 5)` | `list[MemoryEntry]` | 召回记忆 |
| `forget` | `(entry_id: str)` | — | 删除记忆 |

### VectorMemory

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| `add` | `(text: str, metadata: dict)` | `str` | 添加到向量库 |
| `search` | `(query: str, top_k: int = 5)` | `list[dict]` | 向量相似度搜索 |

### MemoryManager

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| `remember` | `(slot: str, content: str)` | — | 按槽位记忆 |
| `recall` | `(slot: str)` | `Optional[str]` | 按槽位召回 |

---

## 15. 知识图谱 RAG — GraphRAG

`jarvis.graph_rag.GraphRAG` — 基于知识图谱的记忆引擎。

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| `add_entity` | `(name: str, entity_type: str, properties: dict)` | — | 添加实体 |
| `add_relation` | `(source: str, target: str, relation: str)` | — | 添加关系 |
| `query` | `(question: str)` | `GraphRAGResult` | 图谱问答 |

---

## 16. 模型路由 — Router

`jarvis.core.router.ModelRouter` — 零成本复杂度分类 + 模型路由。

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| `classify` | `(prompt: str)` | `ModelTier` | 分类提示词到 cheap/standard/premium |
| `route` | `(prompt: str)` | `str` | 返回最优模型名称 |

### ModelTier

| 层级 | 模型 | 说明 |
|------|------|------|
| `cheap` | gpt-4o-mini, claude-haiku | 低复杂度（问候/翻译/定义） |
| `standard` | gpt-4o, claude-sonnet | 中等复杂度 |
| `premium` | gpt-4o, claude-opus | 高复杂度（推理/代码/分析） |

---

## 17. 多模型路由 — MultiModelRouter

`jarvis.multi_model.MultiModelRouter` — DeepSeek V3/R1 并行与集成路由。

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| `route` | `(prompt: str, mode: str = "best")` | `MultiModelResult` | 多模型路由 |
| `parallel` | `(prompt: str, models: list[str])` | `list[ModelResult]` | 并行调用多模型 |
| `ensemble` | `(prompt: str, models: list[str])` | `str` | 集成投票结果 |

### 路由模式

| 模式 | 说明 |
|------|------|
| `best` | 选择最佳模型 |
| `parallel` | 并行多模型 |
| `ensemble` | 投票集成 |
| `cost_optimized` | 成本优先 |

---

## 18. 审计日志 — Audit

`jarvis.audit.AuditLogger` — 不可变执行日志。

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| `log` | `(event_type: str, details: dict)` | `str` | 记录审计事件（返回 event_id） |
| `query` | `(filters: dict)` | `list[AuditEvent]` | 查询审计日志 |
| `export` | `(format: str = "json")` | `str` | 导出日志 |

---

## 19. 审批引擎 — Approval

`jarvis.approval.ApprovalEngine` — HITL 人工审批门控。

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| `create_request` | `(action: dict, risk_level: str)` | `ApprovalRequest` | 创建审批请求 |
| `approve` | `(request_id: str, reviewer: str)` | — | 审批通过 |
| `reject` | `(request_id: str, reviewer: str, reason: str)` | — | 审批拒绝 |
| `get_pending` | `()` | `list[ApprovalRequest]` | 获取待审批列表 |

---

## 20. 幻觉守卫 — HallucinationGuard

`jarvis.hallucination_guard.HallucinationGuard` — LLM 输出事实性守卫。

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| `check` | `(output: str, context: Optional[str])` | `GuardResult` | 检查 LLM 输出 |
| `correct` | `(output: str, issues: list)` | `str` | 修正幻觉输出 |

### GuardMode

| 模式 | 说明 |
|------|------|
| `STRICT` | 严格模式（所有可疑断言都标记） |
| `BALANCED` | 平衡模式 |
| `RELAXED` | 宽松模式 |

---

## 21. 循环守卫 — LoopGuard

`jarvis.loop_guard.AgentLoopGuard` — 防止 Agent 无界循环耗尽配额。

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| `check` | `()` | `bool` | 检查是否应继续执行 |
| `reset` | `()` | — | 重置计数器 |
| `increment` | `()` | — | 迭代计数+1 |

---

## 22. 上下文压缩 — ContextCompressor

`jarvis.context_compressor.ContextCompressor` — 长对话历史压缩管理。

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| `compress` | `(messages: list[dict])` | `list[dict]` | 压缩消息历史 |
| `add` | `(message: dict)` | — | 添加消息 |

---

## 23. 上下文版本控制 — ContextVersioning

`jarvis.context_versioning.ContextVersioning` — 不可变状态快照与回滚。

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| `snapshot` | `(trigger: str = "")` | `str` | 创建快照（返回 snapshot_id） |
| `rollback` | `(snapshot_id: str)` | — | 回滚到指定快照 |
| `list_snapshots` | `()` | `list[Snapshot]` | 列出所有快照 |
| `register_component` | `(name, provider, handler)` | — | 注册可版本化组件 |

---

## 24. 评估运行器 — EvalRunner

`jarvis.eval.EvalRunner` — Agent 回归评估。

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| `run` | `(benchmark: str)` | `EvalReport` | 运行基准测试 |
| `list_benchmarks` | `()` | `list[str]` | 列出可用基准测试 |

---

## 25. 沙箱 — Sandbox

`jarvis.sandbox.SandboxManager` — 安全代码执行环境。

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| `execute` | `(code: str, language: str = "python")` | `SandboxResult` | 执行代码 |

---

## 26. 插件系统 — Plugin

### jarvis.plugin.PluginManager（生命周期钩子）

| 生命周期钩子 | 触发时机 |
|-------------|---------|
| `ON_INIT` | Emperor 初始化完成 |
| `PRE_TASK` | 任务执行前 |
| `POST_TASK` | 任务执行后 |
| `PRE_EVOLVE` | 进化执行前 |
| `POST_EVOLVE` | 进化执行后 |
| `ON_MINISTER_REGISTER` | 大臣注册 |
| `ON_MINISTER_DEREGISTER` | 大臣注销 |
| `ON_ALERT_FIRED` | 告警触发 |
| `ON_HEAL_START` | 自愈开始 |
| `ON_HEAL_COMPLETE` | 自愈完成 |

### jarvis.plugin_system.PluginManager（热加载）

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| `load` | `(plugin_path: str)` | `Plugin` | 动态加载第三方插件 |
| `unload` | `(plugin_name: str)` | — | 卸载插件 |

### jarvis.plugin_marketplace.PluginMarketplace

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| `install` | `(plugin_name: str)` | — | 从市场安装插件 |
| `search` | `(query: str)` | `list[PluginMeta]` | 搜索插件 |
| `list_installed` | `()` | `list[PluginMeta]` | 已安装列表 |

---

## 27. 成本追踪 — CostTracker

`jarvis.cost_tracker.CostTracker` — 每次 LLM 调用的成本记录与统计。

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| `record` | `(model: str, tokens: int, cost: float)` | — | 记录一次调用成本 |
| `stats` | `()` | `dict` | 成本统计摘要 |

`jarvis.cost_per_success.CostPerSuccessTracker` — 每次成功运行的成本追踪。

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| `record_outcome` | `(success: bool, cost: float)` | — | 记录结果 |
| `get_cost_per_success` | `()` | `float` | 获取 CPS 指标 |

---

## 28. 提示词模板 — PromptTemplate

`jarvis.prompt_template.PromptTemplateManager` — 自适应提示词模板。

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| `get` | `(template_name: str)` | `PromptTemplate` | 获取模板 |
| `render` | `(template_name: str, variables: dict)` | `str` | 渲染模板 |
| `register` | `(template: PromptTemplate)` | — | 注册自定义模板 |

---

## 29. 任务路由器 — TaskRouter

`jarvis.router.RouterEngine` — 意图分类 + 多级路由。

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| `route` | `(task: str)` | `RouteResult` | 路由任务到目标 |
| `classify_intent` | `(task: str)` | `IntentClass` | 分类用户意图 |

---

## 30. 工作流引擎 — WorkflowEngine

`jarvis.workflow.WorkflowEngine` — DAG 多步骤编排。

| 方法 | 签名 | 返回值 | 说明 |
|------|------|--------|------|
| `add_step` | `(step: WorkflowStep)` | — | 添加步骤 |
| `execute` | `(input_data: dict)` | `WorkflowResult` | 执行工作流 |

---

## 31. 配置系统 — Config

`jarvis.config` — YAML 配置加载系统。

| 函数 | 说明 |
|------|------|
| `load_config(path)` | 加载 `jarvis.yaml` |
| `save_default_config(path)` | 生成默认配置 |

### EmperorConfig / AppConfig 关键参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `dashboard.host` | `"127.0.0.1"` | Dashboard 主机 |
| `dashboard.port` | `9020` | Dashboard 端口 |
| `scheduler.auto_schedule` | `True` | 自动调度 |
| `scheduler.evolve_interval_minutes` | `5.0` | 进化间隔（分钟） |
| `max_ministers` | `50` | 最大大臣数 |
| `max_context_tokens` | `8192` | 最大上下文 Token |

---

## 32. Dashboard API

Dashboard 运行在 FastAPI（端口 9020），提供 RESTful API 端点。

### 系统状态

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | Dashboard 主页 |
| GET | `/api/status` | 系统状态摘要 |
| GET | `/api/health` | 系统健康指标 |
| GET | `/api/dashboard/live` | 天气 + 新闻实时数据 |
| GET | `/api/dashboard/capability-stats` | 能力命中统计 |

### 任务管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/tasks` | 任务列表（支持 `?minister=&status=&search=&offset=`） |
| POST | `/api/tasks/execute` | 手动执行任务 |

### 大臣管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/ministers` | 大臣列表 |
| POST | `/api/ministers` | 创建大臣 |
| PUT | `/api/ministers/<id>` | 更新大臣 |
| DELETE | `/api/ministers/<id>` | 删除大臣 |

### 进化与自愈

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/evolution` | 进化历史 |
| POST | `/api/evolve` | 手动触发进化 |
| POST | `/api/heal` | 触发自愈 |
| PATCH | `/api/scheduler` | 调度器控制 |

### 其他

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/alerts` | 告警列表 |
| GET | `/api/config` | 当前配置 |
| POST | `/api/theme` | 切换主题 |
| GET | `/api/events` | SSE 事件流 |
| GET | `/dashboard/export` | 导出数据（JSON/CSV） |

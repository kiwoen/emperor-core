
# Emperor-Core 版本变更记录

> 版本: 0.1.0 | 最后更新: 2026-08-08

---

## [0.1.0] — 2026-08-08

### P0: 安全与治理（最高优先级）

#### P0.1: GovernanceAgent — 治理代理
- 新增 `jarvis.governance_agent` 模块
- 实现 `GovernanceAgent`（"监控 Agent 的 Agent"）
- 支持四种规则类型：`policy_compliance` / `rbac` / `regulatory` / `business_logic`
- 四级优先级：CRITICAL(4) > HIGH(3) > MEDIUM(2) > LOW(1)
- 集成 `AuditLogger` 实现不可变审计链路
- 集成 `ApprovalEngine` 实现 HITL 审批门控
- 新增公开导出：`GovernanceAgent`, `GovernanceRule`, `GovernanceResult`

#### P0.2: BoundedAutonomy — 有界自治三区模型
- 新增 `jarvis.bounded_autonomy` 模块
- 实现 `BoundedAutonomyEngine` 三区模型：
  - GREEN：自动执行（安全操作）
  - YELLOW：需人工审批
  - RED：禁止 / 需显式覆盖
- 内置 30+ 默认分类规则
- 集成 `GovernanceAgent` + `ApprovalEngine`

#### P0.3: Pipeline 逐步故障恢复
- 扩展 `jarvis.pipeline.ServicePipeline` 支持 `RecoveryEngine` 集成
- 每个 `Stage` 支持独立的重试 + 熔断 + 降级策略
- 新增 `StageStatus` / `PipelineStatus` 枚举体系

#### P0.4: AgentLoopGuard — 无界循环熔断
- 新增 `jarvis.loop_guard` 模块
- 实现 `AgentLoopGuard`，防止 Agent 无界循环耗尽配额
- 支持最大迭代数和单次运行成本上限双重熔断
- 集成 `CostTracker` 实现实时成本感知熔断

---

### P1: Dashboard 可视化增强

#### P1.1: 能力命中统计环形饼图
- 新增 `jarvis.capability_stats` 模块，通过装饰器 `@track_capability_hit` 拦截 `dispatch()` 调用
- Dashboard 新增 `/api/dashboard/capability-stats` 端点
- 前端新增 ECharts 环形饼图（`id="capChart"`），12 种能力分色 + 图例
- 支持 SSE 实时更新

#### P1.2: 前端任务面板完善
- 搜索框支持跨状态联合搜索（`/api/tasks?search=X`）
- 筛选以 AND 叠加（status + minister + search）
- 搜索含 offset 分页，每页 50 条
- 提示搜索范围含"任务描述 + 结果摘要"
- SSE 事件驱动刷新含搜索参数
- 手机端搜索自动聚焦，PC 端维持 placeholder
- 平板断点卡片网格从 2 列降为 1 列

#### P1.3: Dashboard 后端统计端点
- 新增 15+ 后端端点：`/api/status`, `/api/health`, `/api/ministers`, `/api/alerts`, `/api/evolution`, `/api/config` 等
- 返回 JSON 含实时统计字段：`uptime`, `active_ministers`, `alert_count`, `auto_schedule` 等

#### P1.4: 实时天气 + 新闻面板
- 新增 `/api/dashboard/live` 端点（天气 + 新闻实时数据）
- 前端新增天气小部件 + 新闻头条面板
- 支持 2 分钟数据保鲜 + 15 秒 SSE 刷新
- 前端优雅降级（能力未装载时显示离线占位）

#### P1.5: 调度器面板 + 进化历史
- 前端控制面板新增进化触发按钮（防抖 3 秒）
- 前端进化历史趋势图（ECharts 双 Y 轴：功绩 + 稳定度）
- 新增进化历史数据端点 `/api/evolution`
- 新增调度器控制端点 `PATCH /api/scheduler`

---

### P2: 质量保障

#### P2.1: ReflexionEngine — 自我反思
- 新增 `jarvis.reflexion` 模块
- 实现 `ReflexionEngine`，支持三个维度质量检查：
  - `COMPLETENESS`：完整性检查
  - `INTEGRITY`：内部一致性检查
  - `FACTUALITY`：事实性检查
- 支持自动修正循环（最多 3 次重试）
- 滚动内存记录最近 1000 次反思历史

#### P2.2: HallucinationGuard — 幻觉守卫
- 新增 `jarvis.hallucination_guard` 模块
- 实现 `HallucinationGuard`，支持三种模式：STRICT / BALANCED / RELAXED
- 检测 LLM 输出中的不可验证断言
- 集成 `GuardrailTelemetry` 实现 OTel 风格可观测性

#### P2.3: GuardrailTelemetry — 护栏遥测
- 新增 `jarvis.guardrail_telemetry` 模块
- 实现 OTel 风格的护栏事件遥测
- 单例模式 `guardrail_telemetry` 全局实例
- 支持 `block` / `approval_requested` / `rule_match` 等事件类型

#### P2.4: EvalRunner — 回归评估
- 新增 `jarvis.eval.EvalRunner`
- 集成 `CapabilityRegistry` 实现能力评估
- 支持自定义基准测试

#### P2.5: StateMachine — 状态机
- 新增 `jarvis.state_machine` 模块
- LangGraph 风格有状态编排引擎
- 内置 `dispatch_workflow`（planning→exec→reflection→completion）
- 内置 `error_recovery_wf`（error→diagnose→retry→escalate）
- 支持条件分支 + 循环转换 + 重试升级

#### P2.6: ContextCompressor — 上下文压缩
- 新增 `jarvis.context_compressor` 模块
- 长对话历史智能压缩管理
- 支持保留最近 N 条消息

#### P2.7: ContextVersioning — 上下文版本控制
- 新增 `jarvis.context_versioning` 模块
- 不可变状态快照 + 回滚
- 支持注册可版本化组件（Plugins / Templates）
- 启动时自动创建基线快照

#### P2.8: MultiModelRouter — 多模型路由
- 新增 `jarvis.multi_model` 模块
- 实现 `MultiModelRouter`，支持 DeepSeek V3/R1 等模型
- 四种路由模式：`best` / `parallel` / `ensemble` / `cost_optimized`
- 集成 `CostTracker` 实现成本记录

#### P2.9: SmartRouter — 智能路由
- 新增 `jarvis.model_router.SmartRouter`
- 能力感知路由，支持回退链
- 支持 YAML 配置文件 `model_routing.yaml`

#### P2.10: CostPerSuccessTracker
- 新增 `jarvis.cost_per_success` 模块
- 追踪每次成功运行的成本（CPS 指标）
- 持久化到 `outcome_records.json`

---

### P3: 生态与集成

#### P3.1: MCPManager — MCP 服务器管理
- 新增 `jarvis.mcp_manager` 模块
- 统一管理多个 MCP Client 生命周期
- 内置 3 个 Mock Server（无需外部进程）：
  - `MockFilesystemServer`：模拟文件系统操作
  - `MockWebSearchServer`：模拟 Web 搜索
  - `MockCalculatorServer`：数学计算
- 支持运行时注册/移除 MCP Server
- 自动工具发现和调用

#### P3.2: GraphRAG — 知识图谱 RAG
- 新增 `jarvis.graph_rag` 模块
- 基于知识图谱的记忆引擎
- 支持实体添加、关系建立、图谱问答

#### P3.3: RBACEngine — 角色访问控制
- 新增 `jarvis.rbac.RBACEngine`
- 基于角色的访问控制引擎
- 默认管理员角色 + 可扩展角色体系

#### P3.4: HandoffProtocol — 多 Agent 交接
- 新增 `jarvis.handoff.HandoffProtocol`
- 标准化的多 Agent 任务移交协议
- 集成 `AuditLogger` 记录每次交接

#### P3.5: PromptTemplateManager — 提示词模板
- 新增 `jarvis.prompt_template` 模块
- 自适应提示词模板管理器
- 支持模板注册、渲染、持久化
- 注入到 `CapabilityRegistry` 中

#### P3.6: HealingActions — 自愈动作扩展
- 新增 `jarvis.healing_actions` 模块
- 内置 5 个自愈动作：`restart_scheduler` / `emergency_evolve` / `purge_stale_ministers` / `reset_alert_cooldowns` / `rebalance_merit`

#### P3.7: Dashboard 导出功能
- 新增 `/dashboard/export` 端点
- 支持 JSON / CSV 格式导出
- 可指定导出范围（tasks / evolution / alerts）

#### P3.8: 主题切换
- 新增 `/api/theme` 端点
- 支持 dark / light / auto 三种主题
- 前端 CSS 变量实现无缝切换

#### P3.9: PluginSystem & PluginMarketplace
- 新增 `jarvis.plugin_system.PluginManager`：热加载第三方插件
- 新增 `jarvis.plugin_marketplace.PluginMarketplace`：插件市场（安装/搜索/列表）
- 新增 `jarvis.plugins.MetricsPlugin`：生命周期事件采集

#### P3.10: TaskRouter — 任务路由器
- 新增 `jarvis.router.RouterEngine`
- 基于意图分类的多级路由
- `IntentClassifier` 支持 LLM 辅助分类

#### P3.11: WorkflowEngine — DAG 工作流
- 新增 `jarvis.workflow.WorkflowEngine`
- DAG 多步骤任务编排
- 支持步骤依赖和条件执行

---

### 修复

- Dashboard HTML 单文件拆分为模块化 `jarvis/dashboard_html.py`（6300+ 行）
- Court API 独立为 `jarvis/court_api.py`（3500+ 行）
- `Emperor.__init__` 延迟导入优化，加速启动
- 所有模块通过 `jarvis/__init__.py` 统一导出
- `pyproject.toml` 版本号从占位符更新为 `0.1.0`
- CLI 入口修复：`jarvis/emperor = jarvis.cli:main`

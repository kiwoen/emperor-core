---
name: "self-evolution"
description: "emperor-core 自我进化引擎：真实执行任务获取梯度，按成败即时微调基因（temperature/confidence），经验记忆跨重启累积，历史成功率排序派发。触发：进化、自进化、学习、基因、evolution、自我改进。"
triggers:
  - "进化"
  - "自进化"
  - "学习"
  - "基因"
  - "evolution"
  - "self-evolution"
---

# Self-Evolution · 自我进化引擎

## 系统定位
emperor-core 作为「会思考、学习、进化的生命体」的本质能力。它不是套壳助理，而是具备真实执行→真实学习→跨重启经验累积的闭环。
- 上游：任务执行（大臣/`RealTaskExecutor`/`OfflineSolver`）、`CourtMemory`
- 下游：`GenomeStore`（基因检查点）、`PromotionGate`（fail-closed 晋升闸）、`RollbackManager`

## 类型识别与路由
进化触发分两类：
- **实时梯度**：任务真实对错（`OfflineSolver` 真算 / 真实 LLM 后端），即时反馈
- **周期进化**：`emperor.evolve(cycles=N)` 多轮，每轮记学习曲线点（`learning_curve.json`）

## 核心功能
1. **真实执行**：`RealTaskExecutor` + `OfflineSolver` 把题真算出来，梯度来自真实成败
2. **自我学习**：基因按真实成败即时微调，向最优区（temperature≈0.4 / confidence≈0.9）靠拢，随基因组检查点持久化
3. **经验记忆**：`CourtMemory` 落盘，跨重启累积；`_execute_tasks` 按 (大臣,领域) 历史成功率排序派发
4. **闭环收口**：历史最被证明的大臣优先拿任务；无记忆稳定退化为原序轮转
5. **安全闸**：`CircuitBreaker`/`PromotionGate`/`ApprovalEngine`(HITL)/`AuditLogger` 在 DGM 三约束（沙箱+编码基准+人类审批）下 fail-closed

## 工作流程
1. 接任务 → 真实执行 → 得对错
2. 成败 → 更新基因（温度/置信漂移）+ 经验记忆
3. 周期触发 → 跑 N 轮 → 每轮记学习曲线点
4. 晋升候选 → `PromotionGate` 编码基准评测 → 人类审批 → 落盘基因检查点
5. 失败/熔断 → `RollbackManager` 回滚

## 输出格式
`LearningCurve`（avg_merit / success_rate / active_ministers + 各大臣快照）；`GenomeState`（温度/置信/探索率）。

## 审查原则与铁律
- **真实优先于模拟**：离线模式只验证机制，接 LLM(key)才有真实收益
- **fail-closed**：任何安全闸未过 → 拒绝晋升，宁可不动
- **经验不丢**：记忆落盘，重启后继续累积
- **可回滚**：任何基因变更可回退

## 与其他技能的联动
| 模块 | 联动方式 |
|------|----------|
| `minister-dispatch` | 经验回流驱动派发排序 |
| `code-reviewer` | 审查硬伤作为进化负反馈 |
| `court-memory` | 经验记忆读写 |

## 版本记录
- v1.0 (2026-08)：落地 `SelfEvolutionEngine`/`GenomeStore`/`CourtMemory`/`PromotionGate`，定向回归 172 passed
- v1.1 (2026-08-17)：学习曲线接入进化轮次（`learning_curve.py`）

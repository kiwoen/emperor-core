---
name: "minister-dispatch"
description: "emperor-core 大臣派发与执行契约：大臣是自治智能体，接收圣旨(edict)经生命周期状态机处理，先试真实模型再回退 _handle，记录经验用于自我进化。触发：大臣、派发、圣旨、minister、大臣执行。"
triggers:
  - "大臣"
  - "派发"
  - "圣旨"
  - "minister"
  - "dispatch"
---

# Minister Dispatch · 大臣派发与执行

## 系统定位
emperor-core 的「执行臂」。8 位大臣（丞相/御史大夫/太史令/工部尚书/太常/大司农/太卜/卫尉）各自是带能力画像的自治智能体，经主路由（`task-router`）派发后独立处理任务并自我进化。
- 上游：`task-router`（选大臣）、Emperor
- 下游：`jarvis.codex.reviewer`（工部审查）、`SelfEvolutionEngine`（经验回流）

## 类型识别与路由
大臣选择由 `task-router` 完成；本技能定义**单个大臣如何处理**一条圣旨：
`receive_edict(edict)` → 状态机 `IDLE→DELIBERATING→EXECUTING→REPORTING→LEARNING`。

## 核心功能
1. `can_handle(intent) -> [0,1]`：基于 `domain` + `strengths`/`weaknesses` 关键词匹配的路由评分（CJK 字符级回退）
2. `receive_edict`：先 `_try_real_model`（真实 LLM，含基因组注入），失败回退 `_handle`（mock/确定性逻辑）
3. `_handle(edict) -> (output, confidence)`：子类实现的具体执行
4. `_learn_from_dispatch`：记录 `ExperienceRecord`，按成败微调 `confidence_baseline` 与 `temperature`（探索/利用权衡）

## 工作流程
1. 加锁、状态→DELIBERATING、查知识图谱上下文
2. 状态→EXECUTING，先试真实模型，否则 `_handle`
3. 状态→REPORTING，组装 `Memorial`（含 confidence）
4. 状态→LEARNING，经验记忆 + 基因漂移
5. 状态→IDLE，返回 Memorial

## 输出格式
`Memorial(edict_id, minister, state, success, output, confidence, execution_time_ms, suggestions, error, timestamp)`。

## 审查原则与铁律
- **真实优先**：有真实模型走真实模型，无则确定性回退，绝不静默崩
- **能力协商**：`weaknesses` 命中即扣分，让更合适的大臣承办
- **经验即进化**：每次派发都进 `CourtMemory`，历史最被证明的大臣优先派发
- **基因组可注入**：`set_genome` + `GenomeInjector` 把进化基因映射为生成参数

## 与其他技能的联动
| 模块 | 联动方式 |
|------|----------|
| `task-router` | 选优后调用本技能 `receive_edict` |
| `code-reviewer` | 工部尚书 `_handle` 内调用 |
| `self-evolution` | `_learn_from_dispatch` 写入经验 |

## 版本记录
- v1.0 (2026-08)：从技能集群「子技能自治 + 经验回流」范式落地 `jarvis/court/minister.py`
- v1.1 (2026-08-17)：工部尚书接入 `CodeReviewer` 形成闭环

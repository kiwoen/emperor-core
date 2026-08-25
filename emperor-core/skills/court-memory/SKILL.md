---
name: "court-memory"
description: "huanxin-ai 经验记忆：按 (大臣,领域) 维度持久化派发成败与质量，支持成功率聚合、衰减/裁剪窗口、跨重启累积，驱动历史最被证明的大臣优先派发。触发：记忆、经验、court memory、成功率、经验累积。"
triggers:
  - "记忆"
  - "经验"
  - "court memory"
  - "成功率"
  - "经验累积"
---

# Court Memory · 经验记忆

## 系统定位
huanxin-ai 的「长期记忆」，让生命体的经验跨重启、跨会话累积，是自我进化闭环的数据底座。
- 上游：`minister-dispatch`（`_learn_from_dispatch`）、`self-evolution`
- 下游：`task-router` 派发排序、`SelfEvolutionEngine` 基因微调

## 类型识别与路由
记忆键 = `(大臣, 领域)` 二元组；查询按该键聚合历史成败。

## 核心功能
1. **持久化**：经验落盘（JSON/SQLite），重启不丢
2. **质量聚合**：`per_minister_domain_quality(recency_decay)` 按插入序加权确定性聚合（recency_decay=1.0 等价朴素计数）
3. **留存窗口**：`max_per_group`（每 (大臣,领域) 留存上限，默认 None=关→零回归）
4. **派发排序**：`_execute_tasks` 按历史成功率降序派发，无记忆退化轮转

## 工作流程
1. 派发完成 → 记录 (大臣,领域,成败,置信,耗时)
2. 查询 → 聚合该键历史成功率
3. 派发决策 → 成功率降序；并列按质量分
4. 周期裁剪 → 超出 `max_per_group` 丢弃最旧

## 输出格式
`MemQuality(per_group: dict[(minister,domain), {success, total, rate}])`。

## 审查原则与铁律
- **不丢经验**：落盘优先于内存
- **确定性聚合**：插入序加权，结果可复现
- **可裁剪**：防止无限增长（但默认关闭=零回归，需显式开启）
- **驱动优先派发**：历史证明优先，而非随机轮转

## 与其他技能的联动
| 模块 | 联动方式 |
|------|----------|
| `minister-dispatch` | 写入每次派发经验 |
| `task-router` | 读取成功率做排序派发 |
| `self-evolution` | 提供基因微调信号 |

## 版本记录
- v1.0 (2026-08)：落地 `CourtMemory`，`_execute_tasks` 历史排序派发
- v1.1 (2026-08-14)：加 `max_per_group` + `recency_decay` 衰减/留存窗口（Phase 12 续⁵）

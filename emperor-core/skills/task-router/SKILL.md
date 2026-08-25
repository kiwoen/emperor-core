---
name: "task-router"
description: "huanxin-ai 主路由分发器：将自然语言意图归类为任务类型，用大臣 can_handle 评分选优派发，支持类型感知加权与模型档建议。触发：路由、派发、任务分类、选大臣、调度。"
triggers:
  - "路由"
  - "派发"
  - "任务分类"
  - "route"
  - "dispatch"
---

# Task Router · 主路由分发器

## 系统定位
huanxin-ai 的「决策脑」，把多智能体技能集群的「主编路由 → 专项技能分发」模式工程化为通用任务路由。它不执行任务，只负责把对的意图送给对的承办者。
- 上游：Huanxin / Orchestrator
- 下游：工部尚书等 8 大臣（`huanxin.court.ministers`）、工程等领域模块

## 类型识别与路由
`classify_task_type(intent)`：对 code/math/writing/research/security/planning 关键词做**命中数加权统计**，取命中最多者（并列按定义顺序稳定 tie-break），均未命中 → general。
> 代码审查（code review / 审查代码）归为 `code` 类。

## 核心功能
1. `classify_task_type`：意图 → 任务类型标签
2. `route_to_minister`：对候选大臣调用 `can_handle(intent) -> [0,1]`，`_sort_key = (评分, 质量分, -失败数)` 选优；`type_aware` 时同域大臣 +0.1（类型感知路由）
3. `plan_dispatch`：封装路由 + `ModelRouter.estimate_complexity` 给出 cheap/standard/premium 模型档建议

## 工作流程
1. 分类任务类型
2. 候选大臣 `can_handle` 打分
3. 排序选优（含类型感知加权与稳定 tie-break）
4. 无人匹配且有 default → 回退
5. 附模型档建议返回 `DispatchPlan`

## 输出格式
`DispatchPlan(minister, score, task_type, suggested_tier)`。

## 审查原则与铁律
- **互斥优先级**：冲突能力按优先级从高到低，不并行选两个
- **类型感知**：同域大臣加权，路由贴合任务本质
- **稳定 tie-break**：质量分 → 失败数，避免抖动
- **轻量旁路**（待补）：窄意图（纯审查/纯问答）直接命中专项，不触发整轮进化
- **误用即纠**（待补）：维护「误用 → 纠正」表，固化踩坑约束

## 与其他技能的联动
| 模块 | 联动方式 |
|------|----------|
| `minister-dispatch` | 路由结果驱动大臣 `receive_edict` |
| `ModelRouter` | 提供复杂度→模型档映射 |
| `code-reviewer` | code 类审查意图经本路由送达工部尚书 |

## 版本记录
- v1.0 (2026-08)：从 storyboard-master-router 提炼「类型路由 + 互斥优先级」范式，落地 `huanxin/core/task_router.py`
- v1.1 (2026-08-17)：固化主路由→大臣派发约定（见 `docs/ROUTING_AND_DISPATCH.md`）

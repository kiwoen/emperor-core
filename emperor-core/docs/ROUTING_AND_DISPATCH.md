# 主路由 → 大臣派发约定（Routing & Dispatch Convention）

> 把 `jarvis/core/task_router.py` 的 `classify_task_type` / `route_to_minister` /
> `plan_dispatch` 固化为 emperor-core 的**主路由→大臣派发约定**。
> 范式来源：技能集群的「主编路由 → 专项技能分发」（见 `storyboard-master-router` 的
> 决策树 + 互斥优先级 + 常见误用表），迁移为通用任务路由。
>
> 配套文档：`docs/SKILL_AUTHORING.md`（技能契约规范）、
> `docs/SWE_PATTERNS_FROM_SKILLS.md`（P3 模式）、`docs/INTEGRATION_MAP.md`（集成点）。

---

## 一、总览：先 Router 选型 → 再大臣执行 → 再下游

```
用户输入 / 圣旨
   │
   ▼
┌─────────────────────────────────────────────┐
│ 1. TaskRouter.classify_task_type(intent)     │  ← 类型识别
│    code / math / writing / research /        │
│    security / planning / general            │
└───────────────────────┬─────────────────────┘
                        ▼
┌─────────────────────────────────────────────┐
│ 2. route_to_minister(ministers, type_aware)  │  ← 选优派发
│    对每个大臣 can_handle → _sort_key         │
│    _sort_key = (评分, 质量分, -失败数)        │
│    type_aware: 同域大臣 +0.1                │
└───────────────────────┬─────────────────────┘
                        ▼
┌─────────────────────────────────────────────┐
│ 3. minister.receive_edict(edict)             │  ← 大臣执行
│    DELIBERATING→EXECUTING→REPORTING→LEARNING │
│    先真实模型，失败回退 _handle              │
└───────────────────────┬─────────────────────┘
                        ▼
┌─────────────────────────────────────────────┐
│ 4. 下游回流                                   │  ← 不可颠倒
│    审查→修复 / 经验→CourtMemory→下次排序派发  │
└─────────────────────────────────────────────┘
```

**铁律**：路由层**不重复**大臣细则，只读大臣 `can_handle` 与 `SKILL.md`；大臣细则只在大臣 `_handle` 内实现。

---

## 二、类型识别（Step 1）

`classify_task_type(intent)`：
- 对 `TASK_TYPES` 各类型的关键词做**命中数加权统计**（多个类型命中取最多者）
- 并列时按 `TASK_TYPES` 定义顺序稳定 tie-break
- 均未命中 → `general`

当前关键词（节选）：
- `code`：写代码/实现/调试/重构/代码/**代码审查**/code review/bug/算法/开发…
- `security`：安全/漏洞/审计/合规/audit…
- `math` / `writing` / `research` / `planning` / `general`（兜底）

> ⚠️ **「代码审查」归为 `code` 类**——这是接入 `CodeReviewer` 的关键：审查意图经分类进入 code，
> 再由 `type_aware` 加权路由到工部尚书（`domain="code"`，+0.1）。

---

## 三、选优派发（Step 2）：互斥优先级 + 稳定 tie-break

`route_to_minister` 的排序键：
```python
_sort_key = (can_handle评分, profile.quality_score, -failure_count)
```
- **互斥优先级**：评分最高者承办；不得同时选两个冲突能力
- **类型感知加权**：`type_aware=True` 时，大臣 `domain == task_type` 则 `can_handle + 0.1`
- **稳定 tie-break**：评分并列时按 `质量分→负失败数`，避免抖动
- **兜底**：所有大臣 `can_handle≤0` 且有 `default` → 回退 default

`plan_dispatch` 在路由基础上附 `ModelRouter.estimate_complexity` 给出模型档（cheap/standard/premium）。

---

## 四、常见误用与纠正（沿用 storyboard-master-router 的「误用表」范式）

| 误用 | 纠正 |
|------|------|
| 把「代码审查」当普通「写代码」意图，路由到通用生成大臣而非工部审查分支 | `classify_task_type` 已含 code review 关键词 → code 类；工部尚书 `_handle` 检测审查意图+代码即调 `CodeReviewer` |
| 让非 code 域大臣承办代码任务（如让丞相写代码） | `domain` 不匹配 → `can_handle` 低分；`type_aware` 把 code 域大臣加权到最前 |
| 路由层自己实现审查/生成逻辑 | 路由层只读 `can_handle`/`SKILL.md`，细则在大臣 `_handle` |
| 无 default 时所有大臣 `can_handle=0` 返回 None | 传入 `default`（如丞相）作安全兜底 |
| 重载/重启后派发顺序随机 | `CourtMemory` 跨重启累积历史成功率，`_execute_tasks` 按成功率降序派发 |
| 一次派发触发整轮进化（对窄请求过度工程） | （待补 P6 轻量旁路）纯审查/纯问答直接命中专项，不触发 `evolve` |

---

## 五、与 CodeReviewer 的闭环（端到端已通）

```
用户：「审查这段代码 ```python ...```」
   → classify_task_type → "code"
   → route_to_minister(type_aware) → 工部尚书（domain=code, +0.1）
   → WorksMinister._handle 检测「审查」+ 含代码
   → jarvis.codex.reviewer.CodeReviewer().review(code)
   → to_markdown(report) 返回八维加权报告 + 🔴🟡🟢 问题清单 + 诚实 N/A
```

DIRECT 路径（工程领域模块 `domains/engineering`）亦独立接入 `CodeReviewer`，两条路径共用同一引擎。

---

## 六、一句话

**先 `classify_task_type` 选类型 → 再 `route_to_minister` 按 (评分,质量,-失败) 选大臣（同域+0.1）→
大臣 `receive_edict` 执行（真实模型优先，回退 `_handle`）→ 经验回 `CourtMemory` 驱动下次排序派发。**
路由层不写细则，大臣只管执行，经验驱动进化。

# SKILL 编写规范（emperor-core）

> 本规范将「多智能体技能集群」中沉淀出的 **SKILL.md 约定** 固化为 emperor-core 的
> 技能 / 大臣（Minister）/ 领域（Domain）编写标准，确保跨技能可组合、可路由、可审计。
>
> 来源：从用户提供的软件开发相关技能包（含 `script-multi-review` 等）提炼出的
> 「frontmatter 元信息 + 正文八段式」结构，以及「主编路由 → 专项技能分发」协作模式。

---

## 1. 一个 SKILL 是什么

在 emperor-core 语境下，一个「技能」是 **可独立交付、可被发现、可被路由** 的能力单元。
它可以是：

- 一个 `jarvis.court.ministers.*` 大臣（自治智能体）
- 一个 `jarvis.domains.*` 领域处理模块
- 一个 `jarvis.codex.*` 子引擎（如 `Reviewer`）
- 一个独立 `SKILL.md` 文本技能（供 LLM 上下文加载）

无论形态如何，**统一用 SKILL.md 描述其契约**。

---

## 2. SKILL.md 结构（frontmatter + 八段式正文）

### 2.1 Frontmatter（机器可读元信息）

```yaml
---
name: "skill-name"                     # 唯一标识，kebab-case
description: "一句话能力描述。触发：关键词A、关键词B、场景C。"
triggers:                             # 可选：显式触发词列表
  - "代码审查"
  - "code review"
  - "多维审核"
---
```

- `name`：全局唯一，kebab-case。
- `description`：必须包含「能力 + 触发条件」，供主编路由（IntentRouter / TaskRouter）检索。
- `triggers`：可选增强；与 `description` 中的「触发：…」保持一致。

### 2.2 正文八段式（人类可读 + LLM 可消费）

1. **系统定位（System Positioning）**
   该技能负责什么、不负责什么、在整体架构中的位置。必须写明上下游联动对象。

2. **类型识别与路由（Type Detection & Routing）**
   如何判定输入的任务类型（如剧本类型、代码类型、任务类别），并据此路由到
   不同的检查标准 / 子技能。给出 `detect_type(input) -> type_tag` 的明确逻辑。

3. **核心功能（Core Functions）**
   逐条列出能力，每条给出：输入、处理、输出。对审查类技能，列出 **独立维度**
   （如八维审查），每个维度给出「检查项表 + 评分标准」。

4. **工作流程（Workflow）**
   步骤化（步骤 1 → N），每步标注输入/输出，确保可复现。

5. **输出格式（Output Format）**
   给出结构化的输出模板（Markdown / JSON Schema）。emperor-core 中优先返回
   结构化 `dataclass`，并附带 `to_markdown()` 渲染。

6. **审查原则与铁律（Iron Rules）**
   不可妥协的约束，例如：
   - 类型适配优先，禁止一刀切
   - 问题定位必须精确（file:line）
   - 修改建议必须可执行（禁止「加强」「改善」空话）
   - 区分硬伤（工业标准）与风格偏好
   - 各维度独立评分，不高分掩盖低分
   - **诚实 N/A**：无法评估的维度如实标注，不强行打分

7. **与其他技能的联动（Cross-skill Linkage）**
   表格列出：上游（谁触发我）、下游（我触发谁）。形成技能图谱。

8. **版本记录（Versioning）**
   日期 + 变更摘要，便于审计与回滚。

---

## 3. 多维加权审查模式（可复用模板）

适用于任何「质量审查」类技能（代码审查、剧本审查、文档审查……）。

### 维度定义

```python
class Dimension(str, Enum):
    CORRECTNESS = "correctness"      # 正确性
    SECURITY = "security"            # 安全性
    PERFORMANCE = "performance"      # 性能
    MAINTAINABILITY = "maintainability"
    TESTING = "testing"
    CONCURRENCY = "concurrency"
    OBSERVABILITY = "observability"
    DOCUMENTATION = "documentation"
```

### 评分与定级

- 每维度独立 0–10 评分，按「严重度扣分」：`CRITICAL=-4 / MAJOR=-2 / MINOR=-0.5`。
- 维度带权重（类型适配，乘性微调）。
- 综合分 = Σ(维度分 × 权重) / Σ权重；等级 `S≥9 / A≥8 / B≥7 / C≥6 / D<6`。
- **适用维度才计分；不适用的如实进入 `honest_na` 列表。**

### 问题清单

每条问题：`{严重度 🔴🟡🟢, 维度, 定位, 问题描述, 可执行建议, 规则ID}`，
按严重度→维度排序输出。

> 参考实现：`jarvis/codex/reviewer.py`（`CodeReviewer` / `ReviewReport`）。

---

## 4. 主编路由 → 专项技能分发模式

任何「总控」模块（Emperor / Orchestrator / Court）应：

1. **任务分类**：`classify_task_type(intent) -> type_tag`（关键词加权统计）。
2. **能力打分**：对候选技能/大臣调用 `can_handle(intent) -> [0,1]`。
3. **选优 + tie-break**：评分最高者承办；并列时按「质量分 → 历史失败数」稳定排序；
   可启用「类型感知加权」对同域技能 +0.1。
4. **模型档建议**：结合复杂度路由（`ModelRouter.estimate_complexity`）给出成本档。
5. **回流**：承办结果回流给主编，触发下游技能（如审查→修复）。

> 参考实现：`jarvis/core/task_router.py`（`classify_task_type` / `route_to_minister` / `plan_dispatch`）。

---

## 5. 大臣（Minister）编写清单

- 继承 `jarvis.court.minister.Minister`，实现 `_handle(edict) -> (output, confidence)`。
- 在 `MinisterProfile` 中明确 `strengths` / `weaknesses`（驱动 `can_handle` 路由）。
- 配套一个 `SKILL.md`，遵循本文档第 2 节结构。
- 在 `jarvis/court/ministers/__init__.py` 的 `create_ministers()` 注册
  （**注意**：当前契约要求恰好 8 位大臣，新增常驻大臣需同步更新相关契约测试）。
- 优先复用既有子引擎（如 `CodeReviewer`）作为 `_handle` 的真实逻辑，而非重复实现。

---

## 6. 最小模板

```markdown
---
name: "my-skill"
description: "能力一句话描述。触发：关键词A、场景B。"
---

# 技能名

## 系统定位
负责 ……；上游：……；下游：……。

## 类型识别与路由
detect_type(x) -> {"web", "cli", "lib"}，按类型应用不同标准。

## 核心功能
1. 功能甲：输入 → 处理 → 输出
2. 功能乙：……

## 工作流程
1. 步骤一
2. 步骤二

## 输出格式
见 `dataclass` / Markdown 模板。

## 审查原则与铁律
- 类型适配优先
- 定位精确、建议可执行
- 诚实 N/A

## 与其他技能的联动
| 模块 | 联动方式 |
|------|----------|
| upstream | 触发本技能 |
| downstream | 本技能触发 |

## 版本记录
- v1.0 (YYYY-MM-DD)：初版。
```

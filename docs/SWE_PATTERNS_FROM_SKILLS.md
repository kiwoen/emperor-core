# 可迁移软件开发模式 · 从 5 个技能包提炼

> 源文件（用户 2026-08-17 提供，已解包至 `_skill_digest/` 工作区，未入库）：
> 1. `claude.zip` → `claude/script-multi-review/SKILL.md`（影视剧本多维审查技能，**八维审查+斯奈德节拍评分**的本体）
> 2. `$RZ8NA93.zip` → `智能体SKILL集群v8`（4942 条目的技能集群，含 `storyboard-master-router` 总路由）
> 3. `Mnimax-H3-skills.zip` → `skills/`（50 个 `SKILL.md + meta.yaml` 规范模板）
> 4. `专业电影剧本创作和剧本拆解工作流升级版V5.２.zip`（剧本工作流编排范式）
> 5. `$RI2AEAE.zip`（739MB 桌面 App 安装包，node_modules 为主，SWE 模式价值低，仅作背景）
>
> 目的：从这 5 个包里抽取**可迁移到 emperor-core（一个会思考、学习、进化的生命体）的软件开发模式**，
> 并标注每条模式在 emperor-core 的落点与成熟度。emperor-core 不是 ChatGPT 式助理，
> 而是一个具备「自我审视代码、自我路由、自我进化」能力的生命体。

---

## 模式总览

| # | 模式 | 来源包 | emperor-core 落点 | 成熟度 |
|---|------|--------|-------------------|--------|
| P1 | 多维独立加权审查 | script-multi-review | `jarvis/codex/reviewer.py` | ✅ 已落地 |
| P2 | 诚实 N/A（不适用即明示） | script-multi-review | `reviewer._is_applicable` | ✅ 已落地 |
| P3 | 类型识别 + 互斥优先级路由 | storyboard-master-router | `jarvis/core/task_router.py` | 🔶 部分 |
| P4 | 技能契约（frontmatter + 八段式） | 全部 + Mnimax meta.yaml | `docs/SKILL_AUTHORING.md` | ✅ 规范 + 🔶 待补文件 |
| P5 | 阶段确认卡 / HITL 审批闸 | Mnimax papercraft | `jarvis/.../ApprovalEngine` | ✅ 已有 |
| P6 | 轻量请求旁路（不强行全量流程） | Mnimax papercraft | `task_router` 兜底分支 | 🔶 待补 |
| P7 | 长产物落文档不刷屏 | Mnimax papercraft | artifact / 文件落盘 | ✅ 已有 |
| P8 | 自我进化闭环（经验→反馈→基因） | emperor-core 自身 | `court/memory` + `SelfEvolutionEngine` | ✅ 已落地 |
| P9 | 分层管线（概念→冻结→执行） | storyboard-master-router | `orchestrator` DIRECT/COURT 两路 | 🔶 待固化 |

---

## P1 · 多维独立加权审查（Multi-dimensional Weighted Review）

**来源**：`script-multi-review` 八维剧本审查（设定逻辑/人物动机/剧情结构/时空逻辑/人物塑造/情绪描写/叙事节奏/主题表达），每维 0–10 + 类型适配权重 + 严重度 + 独立评分。

**可迁移内核**：
- 把「质量」拆成**若干正交维度**，每维独立评分，一维高分**不弥补**另一维低分（八维独立评分铁律）。
- 评分**带类型权重**（电影重设定/人物，广告重节奏/卖点），禁止一刀切。
- 每条问题带**严重度**（🔴严重/🟡中等/🟢轻微）与**可定位 + 可执行建议**。

**emperor-core 落点**：`jarvis/codex/reviewer.py` 已 1:1 翻译为代码审查：
- `Dimension` 枚举 = 8 维（correctness/security/performance/maintainability/testing/concurrency/observability/documentation）
- `_TYPE_WEIGHT_ADJUST` = 代码类型（web_service/data_pipeline/cli/test_suite/library/script）权重微调
- `SEVERITY_ICON` / `SEVERITY_PENALTY` = 🔴🟡🟢
- `DimensionScore.weighted` = 独立加权，互不补偿
- `to_markdown()` 兼容 script-multi-review 报告风格

**结论**：P1 已完整落地，无需重写；缺口在「接入派发闭环」（见 P3 / 任务 #78 已修）。

---

## P2 · 诚实 N/A（Honest Inapplicability）

**来源**：script-multi-review 铁律 6「斯奈德映射诚实：若剧本非三幕结构，应说明『本剧本不严格遵循三幕结构，评分仅供参考』，而非强行扭曲映射」。

**可迁移内核**：**无法评估的维度如实标注「不适用」，绝不强行打分或假装有分**。这比「给个低分」更诚实，也避免污染加权总分。

**emperor-core 落点**：`reviewer._is_applicable()` 已实现——
- `TESTING` 维：未提供测试代码 → `applicable=False`，`honest_na` 列表记录
- `CONCURRENCY` 维：代码无并发/异步结构 → `applicable=False`
- 报告中 `诚实 N/A 维度：testing` 显式呈现

**结论**：✅ 已落地，作为 emperor-core「诚实自检」的基石（生命体不该假装懂它没看到的东西）。

---

## P3 · 类型识别 + 互斥优先级路由（Type-aware Routing）

**来源**：`storyboard-master-router` 的「决策树 + 互斥优先级（从高到低）+ 常见误用纠正表」。核心思想：
1. 先识别**项目类型**（tvc/movie/shortdrama/mv）
2. 再按**互斥优先级**选主 Skill（不可同时选两个冲突的）
3. 用「常见误用 → 纠正」表防止错配

**可迁移内核**：
- 路由 = 类型识别 → 候选打分 → **互斥优先级 tie-break** → 下游串联
- 路由层**不重复子技能细则**，只读子技能原文（关注点分离）
- 维护一张「误用 → 纠正」表，把踩过的坑固化成约束

**emperor-core 落点**：`jarvis/core/task_router.py`——
- `classify_task_type()` = 关键词命中数最多者（类型识别）✅
- `route_to_minister()` 的 `_sort_key` = `(can_handle评分, 质量分, -失败数)`（稳定 tie-break）✅
- `type_aware` 加权：同域大臣 +0.1（互斥优先级雏形）🔶
- **缺口**：缺「互斥优先级」显式规则（如 code 类里 code_review 应优先于 code_gen）与「误用→纠正」表

**下一步**（任务 #80）：把 `task_router` 固化为「主路由→大臣派发」约定文档，补互斥优先级与误用表。

---

## P4 · 技能契约（Skill Contract: frontmatter + 八段式）

**来源**：所有 `SKILL.md` 的共性结构 + Mnimax `meta.yaml`（display-name / version / tags / summary / desc / author）+ script-multi-review 的 frontmatter（`name` + `description` 含「触发：…」）。

**可迁移内核**：
- 每个技能 = **机器可解析的 frontmatter**（`name` + `description` 含触发词） + **人/LLM 可读的八段式正文**
- `description` 必须写清「何时用」（Use this Skill when … / 触发：…），供路由层检索
- `meta.yaml` 承载跨语言元数据（标签/版本/作者），与 `SKILL.md` 解耦

**emperor-core 落点**：`docs/SKILL_AUTHORING.md` 已是完整规范（frontmatter + 八段式 + 第 6 节最小模板）。
**缺口**：规范是「文档」，仓库内**0 个真实 `SKILL.md` 文件**。任务 #77 将补：
- `skills/code-reviewer/SKILL.md`
- `skills/task-router/SKILL.md`
- `skills/minister-dispatch/SKILL.md`
- `skills/self-evolution/SKILL.md`
- `skills/court-memory/SKILL.md`
让规范从「纸面」落地为「可被路由/LLM 加载的真实契约」。

---

## P5 · 阶段确认卡 / HITL 审批闸（Staged Confirmation / HITL Gate）

**来源**：Mnimax `papercraft-stop-motion-explainer` 的「Interaction Rule: Confirmation Cards Between Phases」——每个主阶段后**暂停**，用选择卡问用户 Continue/Revise/Switch/Jump/Stop，而非开放问答。

**可迁移内核**：**长流程里设人工闸，阶段产物可被审核/回退再进下一步**。这是「fail-closed 进化」与「人监督」的统一。

**emperor-core 落点**：`jarvis/.../ApprovalEngine`（HITL）+ `CircuitBreaker`/`PromotionGate` 已落地（沙箱 + 编码基准评测 + 人类审批闸）。
**结论**：✅ 已有，P5 印证 emperor-core 的安全哲学与顶尖技能集群一致。

---

## P6 · 轻量请求旁路（Lightweight Request Bypass）

**来源**：Mnimax papercraft「Lightweight Request Bypass」——用户只要一个产物（如单图提示词）时，**不强行跑完整 18 步包流程**，直接路由到相关步（STEP 1,2,9,17）。

**可迁移内核**：**窄请求走窄路径**，避免对简单意图过度工程化；完整流程只在用户要全量时才启用。

**emperor-core 落点**：`task_router` 的 `default` 兜底分支可承载此逻辑，但**尚未显式实现**。
**下一步**：在 `route_to_minister` 增加「轻量意图识别」——纯审查/纯问答直接命中专项大臣，不触发整轮进化。

---

## P7 · 长产物落文档不刷屏（Canvas Document Delivery）

**来源**：Mnimax papercraft「Canvas Document Delivery Rule」——长多段规划产物写进 canvas 文档，聊天只给摘要 + 文件名 + 下一步；除非用户明确要求，否则不把长表/长清单贴进对话。

**可迁移内核**：**长文本/结构化产物落文件或 artifact，对话只承载决策与摘要**，保持界面清爽、信息不淹没。

**emperor-core 落点**：artifact / 文件落盘机制已具备；ChatGPT 风 UI 的「对话区只显示摘要、详情可点开」即此思想。
**结论**：✅ 已有范式支撑，前端已部分落实。

---

## P8 · 自我进化闭环（Experience → Feedback → Genome）

**来源**：emperor-core 自身（被 5 包佐证：多维审查=自检、路由=决策、HITL=安全，三者构成进化三要素）。

**可迁移内核**（生命体的核心）：
1. **真实执行**：任务真算出来，梯度来自真实对错（非模拟）
2. **自我学习**：基因（temperature/confidence）按真实成败即时微调，向最优区靠拢
3. **经验记忆**：跨重启累积，历史最被证明的大臣优先派发
4. **闭环收口**：历史成功率排序派发 → 记忆稳定退化 → 轮转

**emperor-core 落点**：`court/CourtMemory` + `SelfEvolutionEngine` + `GenomeStore` + `_execute_tasks` 历史排序派发。
**结论**：✅ 已完整可测（定向回归 172 passed，见工作记忆）。这是 emperor-core 区别于「助理」的本质。

---

## P9 · 分层管线（Concept → Freeze → Execute）

**来源**：storyboard-master-router 的 Phase 1–3（资产提示词 → 出图冻结 FROZEN → L0/L1 八列执行）；上游/下游顺序固定不可颠倒。

**可迁移内核**：**复杂产物分「构思→冻结确认→执行」三阶段**，冻结点前可改，冻结后下游才基于冻结物作业，避免半成品污染。

**emperor-core 落点**：`orchestrator` 的 DIRECT（意图→领域模块）与 COURT（意图→大臣→task_router）两路即分层；但**阶段冻结点未显式固化**。
**下一步**：在 `ROUTING_AND_DISPATCH.md`（#80）里把「先 Router 选型 → 再领域/大臣执行 → 再下游」的不可颠倒顺序写死。

---

## 提炼结论

1. **你列的 6 条路线图里，3 条代码其实已写好**——多维审查（P1）、诚实 N/A（P2）、技能规范（P4 规范部分）。真正缺的是「接入闭环 + 补 SKILL.md 文件 + 测试」。
2. **5 个包里有 1 个是直接蓝本**（claude.zip 的 script-multi-review），2 个是范式库（v8 路由集群、Mnimax 契约），2 个是背景（剧本工作流、App 包）。
3. **emperor-core 的安全与进化哲学（HITL/P5、自我进化/P8）与顶尖技能集群高度一致**——它不是「套壳助理」，而是把「多维审查 + 类型路由 + 阶段确认 + 经验进化」内化为生命体的自检与成长机制。

下一步交付见 `ROUTING_AND_DISPATCH.md`（#80）与 `skills/*/SKILL.md`（#77）。

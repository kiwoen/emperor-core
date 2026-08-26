# 幻炘AI · 集成点地图（Integration Map）

> 目的：把 huanxin-ai 各子系统之间的集成边界画清楚——每个集成点写清
> **上游触发方 / 下游被调方 / 当前连通状态 / 缺口**。作为"接入闭环"的代码导航。
>
> 关联文档：`docs/SKILL_AUTHORING.md`（技能编写规范）、`huanxin/core/task_router.py`、
> `huanxin/codex/reviewer.py`。

---

## 0. 两条并行路由路径（必须分清）

huanxin-ai 的"主路由"不是单一的，而是**两套并存**：

| 路径 | 入口 | 调度器 | 末端执行者 | 当前状态 |
|------|------|--------|-----------|----------|
| **DIRECT** | `Orchestrator` | `IntentParser → DomainRegistry` | `DomainModule.handle()` | ✅ 已启用，代码审查接在此 |
| **COURT** | `Huanxin` | `task_router.route_to_minister` | `Minister.receive_edict()` | ✅ 已启用，但**未触达 CodeReviewer** |

- `huanxin/core/orchestrator.py:55` `ExecutionMode`：`DIRECT` / `COURT`。
- `huanxin/core/orchestrator.py:94` `DomainModule.handle()` 是领域处理契约。
- `huanxin/core/task_router.py:92` `route_to_minister()` 是大臣派发契约。
- `huanxin/court/minister.py:388` `Minister.receive_edict()` 是大臣执行入口。

> ⚠️ **核心缺口**：同一意图（如"审查这段代码"）走 DIRECT 会调 `CodeReviewer`，
> 走 COURT 会被路由到某大臣的 LLM `_handle`，**不会**用 `CodeReviewer`。
> 两条路径对"代码审查"的行为不一致，需要统一（见 §3）。

---

## 1. 集成点清单

### IP-1 · 多维代码审查（CodeReviewer）
- **上游**：`huanxin/domains/engineering/__init__.py:29-60`（DIRECT 路径，意图命中 `审查|code review|review|audit` 时调用）
- **引擎**：`huanxin/codex/reviewer.py`（`CodeReviewer.review()` → `ReviewReport`；`to_markdown()` 兼容 `script-multi-review` 报告风格）
- **下游消费者**：Engineering `DomainModule.handle()` 返回 `TaskResult`
- **状态**：✅ DIRECT 已连通；❌ COURT/大臣路径未连通；❌ 无 `SKILL.md` 契约；❌ 无单测
- **缺口**：见任务 #71、#73

### IP-2 · 主路由 / 大臣派发（task_router）
- **上游**：`Huanxin`（`huanxin/core.py`）或 `Orchestrator` COURT 模式
- **调度**：`huanxin/core/task_router.py`
  - `classify_task_type(intent)` — 归类为 code/math/writing/research/security/planning/general
  - `route_to_minister(intent, ministers, type_aware=True)` — 按 `can_handle` 评分选优 + 类型感知加权 (+0.1)
  - `plan_dispatch(...)` — 附带 `ModelRouter.estimate_complexity` 模型档建议
- **末端**：`huanxin/court/ministers/__init__.py:29` `create_ministers()` 八大臣
- **状态**：✅ 路由算法完整；⚠️ `TASK_TYPES["code"]` 含 `代码审查|审查代码|code review`，但命中后路由到的大臣（如工部尚书）`_handle` 用的是 LLM，不是 `CodeReviewer`
- **缺口**：见任务 #75

### IP-3 · 大臣体系（Minister）
- **基类**：`huanxin/court/minister.py:98` `Minister`
  - `can_handle(intent) -> [0,1]` — 能力协商（关键词 + CJK 字符级回退）
  - `_handle(edict) -> (output, confidence)` — 子类覆盖的真正逻辑
  - `receive_edict(edict) -> Memorial` — 主入口（含 KG 查询、真实模型调用、学习）
  - 自进化：`_learn_from_dispatch()` 按成败调整 `confidence_baseline` / `temperature`
- **工厂**：`huanxin/court/ministers/__init__.py`（八大臣：丞相/御史大夫/太史令/工部尚书/太常/大司农/太卜/卫尉）
- **状态**：✅ 框架完整；❌ `WorksMinister`（工部尚书，代码域）未集成 `CodeReviewer`
- **缺口**：见任务 #71、#73

### IP-4 · 自进化引擎（Self-Evolution）
- **位置**：`huanxin/evolution/`（SelfEvolutionEngine）、`huanxin/core.py:evolve()`
- **学习信号**：真实任务成败（RealTaskExecutor + OfflineSolver）→ 基因微调 → `genome_state.json` 持久化
- **经验记忆**：`huanxin/court/memory.py`（CourtMemory，跨重启累积）
- **状态**：✅ 闭环通畅；⚠️ 见 `docs/...roadmap` 中 P0/P1 缺口（离线模式回报潜在、记忆无衰减/裁剪）
- **缺口**：非本轮重点（属长期演进）

### IP-5 · 鉴权 / 会话 / 用量（auth_store）
- **位置**：`huanxin/api/auth_store.py`（SQLite，数据卷持久化）
- **表**：users / conversations / messages / sessions / token_ledger
- **路由**：`huanxin/court_api.py:1056-1086`（/api/me、/api/conversations 全套 CRUD）
- **状态**：✅ 多用户 + 会话持久化 + token 计量已通；⚠️ 消息仅在 SSE 流完整结束后落库，流中断会丢助手回复（"看不到之前的对话"疑似根因）
- **缺口**：硬化持久化（流中断也落库 + 列表加载容错），见任务 #74

### IP-6 · LLM 多后端（litellm 故障转移）
- **位置**：`huanxin/core/llm.py`（`LLMManager`）、`huanxin/llm/manager.py`
- **接入**：`build_manager_from_env()` 读 `OPENAI_*` / `NVIDIA_API_KEY` / `OPENAI_FALLBACK_PROVIDERS`
- **状态**：✅ NIM(`meta/llama-3.1-8b-instruct`) 已验证 LIVE；`complete(history=...)` 支持多轮上下文 + `last_usage` 记录 token
- **缺口**：无

---

## 2. 当前连通性总览

```
意图
 ├─ DIRECT ─→ DomainRegistry ─→ Engineering DomainModule.handle()
 │             └─ 命中"审查" ─→ CodeReviewer ✅（IP-1 已通）
 └─ COURT  ─→ task_router.route_to_minister()
               └─ 工部尚书/某大臣.receive_edict()
                     └─ _handle() ─→ LLM（❌ 未调 CodeReviewer）

Huanxin.evolve() ─→ SelfEvolutionEngine ─→ genome + CourtMemory ✅
court_api ─→ auth_store（会话/用量）✅  │  ⚠️ 流中断丢消息
llm ─→ NIM LIVE ✅
```

---

## 3. 本轮要补齐的集成动作（对应 6 任务）

1. **IP-1 → COURT 打通**：让"代码审查"意图经 `task_router` 命中大臣时，该大臣 `_handle` 调用
   `CodeReviewer`（而非纯 LLM）。推荐：给 `WorksMinister` 增加 code-review 分支，或新增
   `CodeReviewMinister`；并补 `huanxin/codex/SKILL.md`。
2. **IP-2 固化约定**：把 `classify_task_type`/`route_to_minister`/`plan_dispatch` 写成
   "主路由→大臣派发"约定文档 + 验证测试（code-review 意图确实路由到审查大臣）。
3. **IP-5 硬化**：`/api/chat` 在流首/流中即落 user 消息，助手消息在生成首块后尽快落库，
   `loadConversations` 容错，杜绝"看不到之前的对话"。
4. **SKILL.md 落地**：为 code-reviewer / task-router / minister / self-evolution / court-memory
   各补一份遵循 `SKILL_AUTHORING.md` 的 `SKILL.md`（frontmatter + 八段式）。
5. **5 个技能包消化**（待用户提供源文件）：从中提取可迁移 SWE 模式，反哺上述规范与 SKILL.md。

---

## 4. 关键文件索引

| 子系统 | 文件 | 行号锚点 |
|--------|------|----------|
| 代码审查引擎 | `huanxin/codex/reviewer.py` | `CodeReviewer.review` / `to_markdown` |
| 审查接入（DIRECT） | `huanxin/domains/engineering/__init__.py` | 29–60 |
| 主路由 | `huanxin/core/task_router.py` | `classify_task_type` / `route_to_minister` / `plan_dispatch` |
| 大臣基类 | `huanxin/court/minister.py` | `can_handle` / `receive_edict` / `_handle` |
| 八大臣工厂 | `huanxin/court/ministers/__init__.py` | `create_ministers` |
| 编排器 | `huanxin/core/orchestrator.py` | `ExecutionMode` / `DomainModule` |
| 鉴权/会话 | `huanxin/api/auth_store.py` | `list_conversations` / `add_message` |
| 技能规范 | `docs/SKILL_AUTHORING.md` | 八段式 + 最小模板 |
| 对话接口 | `huanxin/court_api.py` | `/api/chat` / `/api/conversations` |

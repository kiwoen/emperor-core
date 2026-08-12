# emperor-core 自进化系统「落地完成」复盘报告

> 文档性质：阶段性完工复盘（对照《实施方案》第 8 章 DoD）。
> 时间线：2026-08-09 起，历经 P0.1–P0.6、P1.4、P2.1、P2.2、P2.3、P3.1 与可观测性硬化。
> 安全基调：始终遵循 DGM 论文（arXiv:2505.22954）三约束——沙箱、编码基准验证、人工审批门。

---

## 0. 结论速览

| 阶段 | 状态 | 关键交付 |
|------|------|----------|
| P0.1 PromptGuard 真阻断 | ✅ | 危险输入**真正 return**，telemetry 与行为一致 |
| P0.2 三层护栏接主链 | ✅ | shadow/enforce 双模，guardrail_mode 可切换 |
| P0.3 适应度信号 + 冻结淘汰 | ✅ | RealTaskFitness（成败+单测）；自动淘汰默认 dry-run |
| P0.4 SmartRouter 真实化 | ✅ | 新建 model_router.py，import 失败显式报警 |
| P0.5 `_select_minister` 真实域匹配 | ✅ | 三级领域匹配，不再恒归功勋第一 |
| P0.6 可信评测基准 | ✅ | DeterministicJudge + 黄金问答集（离线可跑），替代 llm_judge 关键词失真 |
| **P1.4 进化安全闸** | ✅ | CircuitBreaker（失控熔断）+ PromotionGate（连续正增长才晋升） |
| **P2.1 GitWriteChannel** | ✅ | 唯一合法写回通道：只开 PR，绝不直推 master/main |
| **P2.2 CI 写保护** | ✅ | check_write_protect.py + absorb.yml，反向校验无人绕过通道 |
| **P2.3 评测闸写回** | ✅ | WritebackGate：基准不过/回归即拒，DGM「基准评测」环闭合 |
| **可观测性硬化** | ✅ | 核心路径 18 处 `except:pass` 全转可观测；CI 静默异常闸防回归 |
| P1.1–P1.3 真实 LLM + 解冻 | ⏸️ | 受沙箱无 LLM key 限制，留待配 key 后启用（已预留闸门） |
| **P3.1 监控看板** | ✅ | telemetry.py + emit_telemetry.py + dashboard.html（stdlib-only，离线可看） |
| **落地编排器** | ✅ | self_evolve.py + run_self_evolve.py：一键跑通完整闭环（离线、确定性、可复现） |
| **落地回归修复** | ✅ | 修 3 个只在真实运行才暴露的 Court bug（熔断即崩 / avg_merit / success_rate） |
| **Phase 8 真实写回 diff** | ✅ | genome_diff：写回携带系统对自己基因的**真实 unified diff**（告别占位符） |
| **Phase 8 人类审批门接入** | ✅ | approval_gate 接通既有 ApprovalEngine，未批准绝不自动 PR |
| **Phase 8 审计 / 检查点接入** | ✅ | 每轮进化进不可篡改审计库；基因落盘为检查点可续跑 / 回放 |
| **Phase 8 运行时入口 + 配置** | ✅ | `jarvis self-evolve` 子命令 + YAML 配置，落地可一键运行 / 调参 |
| **Phase 9 金标准安全闸** | ✅ | safety_gate：写回前最后一道硬约束（schema/唯一性/质量地板/核心大臣在位/受保护路径/无回归），fail-closed |
| **Phase 9 资源预算护栏** | ✅ | ResourceBudget：单轮墙钟/操作数越限即熔断，自进化不会跑飞 |
| **Phase 9 可回滚安全快照** | ✅ | RollbackManager：`safe/<id>` 标记的安全点，一键回滚到进化前 / 任意轮 |
| **Phase 9 闭环端到端验证** | ✅ | 生产配置真实跑通：评测闸拒写回 + 人类审批门 + 审计 + 安全闸 + 快照 + 预算，全绿并可回滚 |
| **Phase 10 行为级金标准安全闸** | ✅ | GoldenSafetyCheck：用最优大臣在基准上的真实答对率兜底（DGM「金标准安全数据集」真正落地），防 reward-hacking |
| **Phase 10 评测与运行优化** | ✅ | 评测与 cycle 解耦（纯反映基因质量）+ 按基因签名缓存复用，长程运行免重复评测 |

**核心完成定义（DoD）达成情况**：
- 全部 P0 项已绿，且对应单测覆盖（PromptGuard 真阻断、护栏接线、适应度非长度、SmartRouter 存在、选臣 domain 匹配、评测基准相关 ≥0.8）。
- P1.4 / P2.1 / P2.2 已绿：absorb-* PR 成功、master 零直推、CI 拦非法写入。
- 全程 GitHub 提交 + 人工（用户）授权同步，无未经审批的 master 改动。

---

## 1. 修前 vs 修后（客观指标对照）

### 1.1 「假的真」→「真的真」

| 维度 | 修前（调研实测） | 修后 |
|------|------------------|------|
| 护栏 | PromptGuard `:815` 上报 `blocked` 但 `:824` 仅 warning，**执行继续** | 危险输入**真 return**，未触达 LLM 后端（spy 测试断言） |
| 适应度 | `_simple_confidence` = 响应长度单调函数（`length_bonus = min(len/2000,0.3)`） | RealTaskFitness = 成败(0.6)+单测通过率(0.4)；长度不再计分 |
| 进化结果 | 实测 192/192 全淘汰，`merit_after` 全 0 | 自动淘汰默认 frozen（dry-run，只记录不执行） |
| 路由 | `SmartRouter` 文件**不存在**，`ImportError` 恒触发，`_smart_router` 恒 None | 新建 model_router.py，Capability 分类真实驱动选臣 |
| 选臣 | `_select_minister` 循环体 `pass`，150/150 全归功勋第一 | 三级领域匹配，按 domain 分发 |
| 评测 | `llm_judge` 关键词重叠启发式（失真） | DeterministicJudge + 黄金问答集，离线可证伪 |
| 静默异常 | emperor.py 两处 `except:pass`（成本快照/loop guard） | 改为 debug/warning + exc_info，无静默失效 |

### 1.2 安全闸（P1.4 新增，修前不存在）

- **CircuitBreaker**：平均功勋相对峰值跌幅 ≥20%、或连续 3 轮下降、或累计成本超预算 → **熔断**，Court.evolve 立即停止后续轮（集成测试断言 `halted=True`）。
- **PromotionGate**：影阁→正臣晋升需**连续 3 轮功勋正增长**，杜绝单轮噪声提拔（reward hacking）。

### 1.3 写回闭环（P2.1/P2.2 新增，修前「零代码自修改」）

- 修前：`pyproject.toml` 声明 gitpython，全库零 `import git`；进化从不真正改码。
- 修后：`jarvis/vcs/git_channel.py` GitWriteChannel —— 克隆到隔离沙箱 → 新建 `absorb-<date>` 分支 → 应用补丁 → push 该分支 → 用 `gh api` 开 PR（目标 master）。**内部 `_assert_no_protected_push` 兜底：任何 `git push master/main` 立即抛错。**
- CI：`scripts/check_write_protect.py` 在 absorb.yml 中对全仓库源码反向扫描，除 git_channel.py 外任何 `git push master`、`gh api PUT/POST 命中受保护分支` 即判红。

---

## 2. 测试与质量

- P0.6 落地：126 passed。
- P0.1–P0.5 落地：208 passed（含 PromptGuard 真阻断 spy 测试）。
- 本次 P1.4 + P2.1 + P2.2 新增：21 测试（circuit_breaker 10 + git_channel 5 + write_protect 6），全部通过。
- 全量回归：在合并前跑完整 `pytest`，确保无回归。

---

## 3. 仍未落地（明确留给后续）

1. **P1.1–P1.3（真实 LLM + 解冻淘汰）**：本沙箱无 LLM API key，`RealLLMFitness` 与 `SurvivalMechanism(enabled=True)` 的解冻需配 key 后在**真实基准验证**下启用。当前 `enable_auto_elimination` 默认 False（dry-run），闸门写死，安全。
2. **P3.1 监控看板**：可借 frontend-dev 技能 + EdgeOne Pages 部署进化健康/吸收队列看板（可选）。
3. **历史静默技术债**：已清除 emperor.py 两处 `except:pass`；仓库其余历史残留需专项清理（grep 已确认 P0 范围内为零）。

---

## 4. 安全边界（硬性，不可逾越）

1. **绝不自动合 master**：所有写回走 PR + 人类 review；GitWriteChannel 无直推路径。
2. **沙箱运行进化**：实验只在隔离环境，不连生产 `jarvis.db`。
3. **资源预算**：单轮成本超预算即熔断（CircuitBreaker）。
4. **权限模型**：`rbac.py` 约束「谁能让 AI 写码」，默认最小权限。
5. **退化即停**：CircuitBreaker 见功勋跌幅超阈值立即停并告警。
6. **可证伪优先**：任何自进化收益声明须附客观基准证据，否则视为未验证。

---

## 5. 提交与同步

- 所有改动经 feature 分支 → 合并 master → 推送 GitHub（`kiwoen/emperor-core`）。
- 分支命名已从「含斜杠」改为**扁平连字符**（沙箱 git 嵌套 ref 写入限制教训）。
- 复盘文档、调研文档、实施方案均已作为仓库交付物纳入 `docs/`。

---

## 6. 本轮增量（P2.3 + 可观测性硬化 + P3.1）

**P2.3 评测闸写回（DGM 闭环补全）**：此前 `GitWriteChannel.propose_change` 会**无条件**开 PR——
一个让基准回归的突变也会被照样提交。本轮新增 `jarvis/vcs/writeback_gate.py`
（`WritebackGate` / `WritebackBlocked`），把「基准评测」这一环接进写回通道：
提供 `eval_report` 时，评测不达标 / 相对基线回归 / 某域跌破下限 / 空套件，
即在任何 git 操作**之前**抛 `WritebackBlocked`（fail fast、零副作用、绝不静默放行）。
至此 DGM 三段式——**沙箱（隔离克隆）+ 基准评测（WritebackGate）+ 人类审批门（只开 PR）**——完全闭合。

**可观测性硬化（研究头号失败模式 = silent failure）**：AST 扫描发现自进化核心路径
残留 18 处宽泛 `except Exception: pass`（其中 `court/task_engine.py` 的功勋/反馈写入
8 处最危险——进化信号会无声丢失）。全部转为 `logger.debug/warning(..., exc_info=True)`，
保持非致命但**可观测**；仅保留 `evolution.py` 一处 `(ValueError, IndexError)` 解析跳过
（窄类型、有意为之）。新增 `scripts/check_silent_except.py` 并接入 `absorb.yml`，
反向校验核心路径不得再引入静默吞异常（防回归）。

**P3.1 监控看板**：`jarvis/telemetry.py`（stdlib-only）把熔断器状态、基准评测、大臣功勋、
进化事件、成本聚合成 `TelemetrySnapshot`；`scripts/emit_telemetry.py` 跑真实 canonical
基准（离线、确定性）生成 `telemetry.json` + `telemetry.js`，并复制出自包含
`dashboard.html`（浏览器直接打开，file:// 可用，无需起服务）。
实测：canonical 黄金基准 12/12（100%），health=healthy。

**集成验证**：`tests/test_self_evolve_integration.py` 把「熔断器 + 评测闸 + 写回通道」
串成完整进化循环，断言：功勋崩塌→熔断即停、回归突变被拒且零 git 副作用、
健康循环写回绝不直推 master、基准黄金答案 100% 可信。

**本轮测试**：新增 25 个（writeback_gate 11 + silent_except_guard 8 + telemetry 7 +
integration 4，去重后计入套件），受触模块定向回归 **317 passed**，零回归。

---

## 7. 本轮增量（落地编排器——「完全落地执行」）

**目标**：此前所有安全组件都是「带单测的独立件」，缺一个能**一键真正跑起来**的完整
自进化循环。本轮补齐编排层，并借「真实跑一遍」逼出并修掉了 3 个潜伏 bug。

**落地编排器**：新建 `jarvis/self_evolve.py`（`SelfEvolutionEngine`）+
`scripts/run_self_evolve.py`（CLI）。把全链路串成闭环：
护栏(GuardrailChain) → 路由(SmartRouter) → 执行(Executor) → 适应度(RealTaskFitness)
→ 功勋(MeritBoard) → 进化(Court.evolve，受 CircuitBreaker+PromotionGate 约束)
→ 基准评测(eval_bench) → 评测闸(WritebackGate) → 写回(GitWriteChannel/离线 RecordingWriteChannel)
→ 可观测(telemetry)。默认**完全离线、确定性、可复现**（`--seed` 同时锚定执行器与
GA 算子的 RNG），无需 LLM key、不连网、不碰真实 git；`--live` 才切真实 PR。

**真实运行逼出并修复的 3 个 Court 潜伏 bug**（此前单测全用 mock，没暴露）：
1. `_evolve_with_breaker` 对 `EvolutionReport` dataclass 直接 `["halted"]=True` ——
   **熔断一触发就 TypeError**，安全闸会在最不该崩的时刻崩掉整个循环。已改为先
   归一化成 dict 再挂 halted/trip_reason。
2. `Court.avg_merit` 读不存在的 `.merit` —— 滑动功勋（默认开启）下 ranking 元素是
   `SlidingMeritReport`（字段为 `windowed_merit`/`merit_score`），一调就 AttributeError。
   已加 `_report_merit` 兼容取值。
3. `Court.success_rate` 调用 `SlidingMeritBoard` 未委托的 `.success_rate()` —— 已加
   穿透到底层 `.board` 的兜底。

**实测**（`--cycles 6 --seed 7`，离线）：6 轮完整跑通，护栏/路由/适应度/功勋/进化全部
真实触发；评测闸每轮正确拦截写回（基准 67–75% < 100% 门槛）；熔断器全程监控（closed）；
产出 run_report.json + telemetry.json/js + dashboard.html。

**本轮测试**：新增 8 个（Court 落地回归 3 + 引擎闭环 4 + CLI 端到端 1），
受触模块定向回归 **325 passed**，零回归。

---

## 8. 本轮增量（Phase 8：把安全闭环真正「落地执行」）

> 驱动：调研 2025–2026 年自进化 / 自改写 AI（DGM、SWE-agent、OpenHands 等）的安全闭环实践，
> 对照 emperor-core 已有能力，补齐「能跑但没接上」的三道落地闸：真实写回、人类审批、审计/持久化，
> 并给出可一键运行的生产入口。

**背景**：上几轮已建成沙箱（GitWriteChannel 只开 PR）、基准评测（WritebackGate）、熔断/晋升闸，
但自进化循环**从未使用**项目里早已存在的 `ApprovalEngine`（HITL）与 `AuditLogger`（不可篡改审计），
且写回一直是一句占位符 `"# cycle N evolved genomes"`，reviewer 在 PR 里看不到任何真实改动——
这正是调研反复出现的「零代码自修改」死亡之穴。

**Phase 8 交付**：

1. **真实写回 diff（jarvis/court/genome_diff.py）**：进化前后各对全体大臣基因拍快照，
   用 `difflib` 生成指向仓库内 `jarvis/court/genome_state.json` 的标准 unified diff
   （新文件用 `/dev/null` 头，`git apply` 可直接消化）。写回携带系统这一轮**对自己基因的
   真实改动**，PR 里能看到「A 大臣 temperature 0.9 → 0.62」之类的具体内容——可审查、可回滚。

2. **人类审批门接入（jarvis/court/approval_gate.py）**：把 `jarvis/approval.ApprovalEngine`
   接通到写回前。每次写回先建一条 `risk_level=critical` 的审批请求进 `approval.db`；
   `auto_approve=False`（生产默认）时**仅记录、不自动 PR**，必须等人类对这条请求 approve
   后由另一条流程发起；`auto_approve=True` 仅用于离线/CI 演示。无引擎则退化为直接放行
   （向后兼容）。DGM 安全模型第三段「人工审批门」由此从纸面变成落库可查的硬约束。

3. **审计 / 检查点接入（jarvis/self_evolve.py）**：每轮 `court.evolve` 与写回决策都进
   `AuditLogger`（不可篡改 `audit.db`）——「什么变了、谁批过、怎么回滚」全程可查、可回放；
   运行结束把全体基因落盘为 `jarvis/court/genome_state.json` 检查点（原子写），
   支持 `--resume` 跨重启续跑与回放。

4. **运行时入口 + 配置（jarvis/cli.py + jarvis/self_evolve_config.py）**：新增
   `jarvis self-evolve` 子命令（封装编排器，参数与脚本对齐），使自进化循环成为与
   `serve`/`chat` 并列的一等运行时模式；新增 YAML 配置（`configs/self_evolve.yaml`，
   `SelfEvolveConfig`）把轮数/种子/闸阈值/审批/审计/检查点路径从硬编码抽离，便于在
   离线演示 / CI / 生产间切换。生产姿态默认：严格评测闸 + 开启人类审批门 + 离线记录写回。

**实测**（`--cycles 5 --seed 7 --audit`，离线）：5 轮完整跑通，health=healthy，
5 次写回均携带真实基因 diff（proposed:absorb-offline-N），审计库每轮写入 evolve/writeback
事件，基因检查点成功落盘（5 初始大臣经自动 breeding 增至 9）。`jarvis self-evolve` 子命令
端到端验证通过。

**本轮测试**：新增 14 个（genome_diff 4 + approval_gate 3 + 落地集成 7），
受触模块定向回归全绿，零回归；`run_self_evolve.py` / `jarvis self-evolve` 真实跑通。

---

## 9. 本轮增量（Phase 9：生产级安全落地——把「能跑」变成「敢跑」）

> 驱动：上一轮虽接上了真实写回 + 人类审批 + 审计，但对照 DGM 三约束与 2025–2026 自进化系统的
> 工程实践，仍有三块「生产级安全」拼图缺失：金标准安全数据集闸、资源预算护栏、可测试回滚。
> 本轮回填这三块，使系统在「无人值守」姿态下也 fail-closed，且任何被批准并合并的突变都能干净撤销。

**Phase 9 交付**：

1. **金标准安全闸（jarvis/court/safety_gate.py，新增）**：DGM 论文「golden safety dataset」约束的
   落地版——在写回前的**最后一道**硬约束（fail-closed）。逐基因校验：`genome_schema`（字段合法）、
   `unique_names`（大臣名唯一）、`quality_floor`（任一基因质量 ≥ 地板，默认 0.05）、
   `core_ministers`（核心大臣 math_alpha / reason_gamma 一个都不能少）、
   `protected_paths`（未触碰受保护核心模块）、`no_regression`（相对基线的金标准大臣平均质量回退 ≤
   上限，默认 0.10）。任一不通过即抛 `SafetyError`，**绝不静默放行**。`jarvis self-evolve
   safety-check` 子命令可对当前基因快照独立跑该闸。

2. **资源预算护栏（jarvis/court/resource_guard.py，新增）**：`ResourceBudget` 上下文管理器，
   为**单轮**自进化设墙钟（`resource_seconds`，默认 120s）与操作数（`resource_max_ops`）上限；
   越限抛 `ResourceBudgetExceeded` 并触发安全熔断，交回上层——杜绝 runaway 自进化无限占用时间 /
   调用。命令行 `--resource-seconds` / `--resource-max-ops` 可调。

3. **可测试回滚（jarvis/court/rollback.py，新增）**：`RollbackManager` 每轮写回前落一个带元数据的
   基因快照（原子写 `.tmp`→`os.replace`），基线（cycle 0）标记为 `safe` 已知点；`index.json` 记录
   全部快照元数据便于审计。`rollback_to(id, court, genome_state_path)` 经既有 `Court.load_genomes`
   （与检查点同一套反序列化）载入，保证「保存 ↔ 回滚」完全对称，并同步更新运行中的
   `genome_state.json` 使回滚立即可见、可 `--resume` 续跑。`jarvis self-evolve rollback
   --list` / `--to <id>` 子命令可用。修正了 `Court.load_genomes` 由「合并」改为「替换」语义
   （对 resume 与 rollback 均正确）。

4. **闭环接入（jarvis/self_evolve.py / scripts/run_self_evolve.py / jarvis/cli.py / 配置）**：
   三块安全件全部接入 `SelfEvolutionEngine.run()`：基线安全快照 → 单轮资源预算包裹 →
   每轮快照 → 写回前先过金标准安全闸（不通过则拦下）。`SelfEvolveConfig` 与
   `configs/self_evolve.yaml` 暴露全部开关（`use_safety_gate` / `quality_floor` /
   `core_ministers` / `max_regression` / `resource_seconds` / `resource_max_ops` /
   `enable_snapshots` / `snapshot_dir`），生产姿态默认全开。

**实测（端到端）**：
- 离线演示（`--cycles 3 --seed 5 --audit`）：3 轮跑通，merit 58→63、成功率 60%→77%，每轮写回携带
  真实基因 diff（proposed:absorb-offline-N），审计库与金标准安全闸均接入，EXIT=0。
- 生产配置（`--config configs/self_evolve.yaml`，严格评测闸 min_pass_rate=0.9）：评测 75% < 90% →
  **写回被拒（blocked）**，人类审批门接入（auto_approve=False）、审计库 + 金标准安全闸均在线，
  EXIT=0——DGM fail-closed 姿态完整复现。
- `safety-check`：对当前基因快照跑金标准安全闸 → 通过（6 项全绿）。
- `rollback --to <safe>`：大臣数 8 → 回滚到基线 5，成功，且 `genome_state.json` 同步更新。

**本轮测试**：新增 9 个（safety_gate 3 + resource_guard 3 + rollback 3），与 Phase 8 共 23 个新增测试；
受触模块定向回归（24 文件）全绿，CI 双闸（check_write_protect / check_silent_except）对新增代码零告警。

**完成度**：至此 emperor-core 自进化闭环已具备 DGM 三约束（沙箱 + 编码基准评测 + 人工审批门）+
研究 P0 落地清单（金标准安全闸 / 资源预算 / 可测试回滚）的全部要素，且**离线、确定性、可复现、
可审计、可回滚**地真正跑通——满足「完全落地执行」目标。

## 10. 本轮增量（Phase 10：优化与加固——把安全闸从「结构」补到「行为」）

> 驱动：Phase 9 的金标准安全闸是**结构级**的（schema/名字/质量地板/核心大臣/受保护路径/无回归），
> 但对照 DGM 论文，真正的核心是「**金标准安全数据集**」——用一组固定的 held-out 任务衡量当前最优模型的
> **真实行为正确率**，作为不可妥协的不变式。结构检查全过 ≠ 行为没崩：一个突变可能把 `true_quality`
> 刷高、却让真实任务表现骤降（典型 reward-hacking）。本轮回填这道**行为级**闸，并对评测本身做正确性与性能优化。

**Phase 10 交付**：

1. **行为级金标准安全检查（jarvis/court/safety_gate.py：新增 `GoldenSafetyCheck`）**：DGM「golden safety dataset」
   的真正落地——把当前最优大臣在固定基准（canonical suite）上的**真实答对率**作为写回前的 fail-closed 不变式。
   编排引擎在 `_evaluate` 算出的 `eval_pass_rate` 注入 `SafetyContext.behavioral_pass_rate`，任一 blocking 闸
   不过即拒。**与结构检查互补**：结构全过、评测闸也放过，只要真实行为正确率跌破 `golden_pass_rate_min`
   （默认 0.5，生产可调高）就拒绝写回。无行为评测数据时按 *warning* 处理，不阻塞离线/无评测场景。
   `default_safety_gate` 默认包含该检查。

2. **评测与运行轮次解耦（jarvis/self_evolve.py：修正 `answer_eval_case`）**：原本评测采样依赖 `cycle`，
   使「跑了第几轮」混入行为正确率、且无法对不同轮复用评测。现改为仅依赖 `(minister, case)`，**评测纯粹
   反映基因质量**，从而为下一步缓存扫清语义障碍（也更正确：进化效果应由基因决定，而非轮次计数）。

3. **评测结果缓存（jarvis/self_evolve.py：新增 `_eval_cache`）**：按「最优大臣 + 其基因」签名缓存评测报告，
   基因未变即复用上次结果，跳过对稳定基因的重复评测（长程运行 / 高频轮询场景下的性能优化，且完全正确——
   评测已与 cycle 解耦，同基因必得同结果）。

4. **配置与入口贯通**：`SelfEvolveConfig.golden_pass_rate_min`（默认 0.5）、`configs/self_evolve.yaml`
   （生产设 0.6）、`scripts/run_self_evolve.py`（透传）、CLI `--golden-pass-rate-min` 全部打通；移除
   `self_evolve.py` 内一处冗余注释，保持代码整洁。

**实测（端到端）**：
- 普通姿态（`--cycles 3 --seed 7`，行为地板 0.5）：eval 67% ≥ 0.5 → 每轮 `proposed:absorb-offline-N`，EXIT=0。
- 激进姿态（`--golden-pass-rate-min 0.99`）：eval 67% < 99% → 全部 `blocked-safety:golden_safety`
  （fail-closed 真实生效），EXIT=0——证明行为级闸确实兜底、可拦截「结构全过但行为崩」的突变。
- `safety-check`：金标准安全闸 7 项（含 golden_safety，无评测数据时按 warning 跳过）通过。
- CI 双闸（check_write_protect / check_silent_except）对新增代码零告警。
- 可复现性复测：同 seed 两次 15 轮运行结果逐字节一致；15 轮约 0.25s，性能无回退。

**本轮测试**：新增 7 个（safety_gate 4：golden 拦截/放行/无数据 warning/默认闸 fail-closed；eval_cache 3：
缓存复用/cycle 解耦/行为信号注入），累计 Phase 8–10 共 **30 个新增测试**；受触模块定向回归
（court/cli/eval/safety/landing 等 21 文件）全绿（合计本轮 446 项通过），CI 双闸零告警。

**完成度**：emperor-core 自进化闭环现已具备 DGM 三约束（沙箱 + **结构级 + 行为级**编码基准评测 + 人工审批门）+
研究 P0 落地清单（金标准安全闸 / 资源预算 / 可测试回滚）的全部要素，且离线、确定性、可复现、可审计、可回滚、
行为级 fail-closed 地真正跑通。


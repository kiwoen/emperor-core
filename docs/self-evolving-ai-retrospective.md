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

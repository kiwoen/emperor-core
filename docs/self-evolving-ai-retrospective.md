# emperor-core 自进化系统「落地完成」复盘报告

> 文档性质：阶段性完工复盘（对照《实施方案》第 8 章 DoD）。
> 时间线：2026-08-09 起，历经 P0.1–P0.6、P1.4、P2.1、P2.2。
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
| P1.1–P1.3 真实 LLM + 解冻 | ⏸️ | 受沙箱无 LLM key 限制，留待配 key 后启用（已预留闸门） |
| P3.1 监控看板 | ⏸️ | 可选，待 P2.2 稳定后部署 |

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

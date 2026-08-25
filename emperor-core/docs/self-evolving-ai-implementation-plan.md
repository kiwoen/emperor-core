# huanxin-ai 落地实施方案（Self-Evolving AI · 可执行版）

> **文档性质**：本文件是**实施方案**（本轮不改 huanxin-ai 源码，仅设计到文件级、给出可复制命令与代码草图）。
> **上游依据**：`self-evolving-ai-research-and-roadmap.md`（第 4–5 章路线图 + 附录 A 能力盘点）。
> **证据原则**：下文所有 `file:line` 均在本机 `D:\AI自我进化\huanxin-ai` 真实仓库中**已核验**（2026-08-09 复读）。
> **AI 应用协同**：以 **GitHub** 为主轴（仓库 / PR / CI），辅以本机编码助手 **Codex / Cursor / Trae / VSCode** 作为代码生成与人工复核层；与"写代码"无关的连接器（邮箱 / 网盘 / 设计 / 电商类）明确不纳入。

---

## 0. 阅读与执行须知

### 0.1 安全总原则（来自 DGM 论文 `arXiv:2505.22954`，本文档硬性约束）
任何"让 AI 修改自身代码"的能力，**必须**同时满足三件事，缺一不开：
1. **沙箱**：进化实验在隔离环境跑，不能直连生产数据库 / 不能直推 `master`。
2. **编码基准验证**：改动须用 SWE-bench / 单测等客观基准证明"真的变好"，不能只看响应长度。
3. **人工审批门**：写回仓库必须走 **PR + 人类 review**，闸门写死，**绝不自动合入 `master`**。

调研已证实：当前 huanxin-ai 的"自进化"是**假的真**——护栏谎报、适应度=响应长度、进化 192/192 全淘汰、零代码自修改。因此落地顺序必须是 **先修"假的真" → 再补"真的缺" → 最后才接写回通道且人类闸门写死**，绝不能在信号失真时叠加进化。

### 0.2 真实代码锚点（已核验）

| 缺口 | 真实位置 | 现状 |
|------|----------|------|
| 适应度=响应长度 | `huanxin/court/task_engine.py:96-111`（`_simple_confidence`），`102-103` `length_bonus = min(len(response)/2000, 0.3)` | 主链路不传 `expected`，置信度纯是长度单调函数 |
| 进化退化 | `huanxin/court/court.py:148` `merit_after`；DB `evolution_history` | 实测 192/192 全淘汰，`merit_after` 全 0 |
| 护栏谎报 | `huanxin/emperor.py:810-831`：`:815` 上报 `action="blocked"`，但 `:824` 仅 `logger.warning`，**不中止** | 比无护栏更危险：telemetry 显示拦截，执行继续 |
| 护栏悬空 | `huanxin/prompt_guard.py` / `tool_guard.py` / `hallucination_guard.py` / `loop_guard.py` / `bounded_autonomy.py` / `guardrail_telemetry.py` | 三层护栏约 2280 行，仅被 `court_api` 端点与彼此引用，未挂主执行链 |
| 路由静默失效 | `huanxin/emperor.py:261` `from huanxin.model_router import SmartRouter`；`:268` `except ImportError: self._smart_router = None`；`:838-841` `self._smart_router.classify(...)` 永不执行 | **`huanxin/model_router.py` 文件不存在** → `ImportError` 永远触发 → `_smart_router` 恒 `None`；CHANGELOG 却写了 P2.9「已发布」 |
| 选臣空转 | `huanxin/court/task_engine.py:352-376` `(_select_minister)`：`:363-366` 领域匹配循环体是 `pass`，永远 fallback 到 `merit_ranking[0]` | 150/150 任务全归功勋第一的大臣 |
| 零代码自修改 | `pyproject.toml:19` / `requirements.txt:13` 声明 `gitpython>=3.1.0`，但全库源码**零** `import git` | 无 `git` 写、无 `.py` 写入、无 PR；`huanxin/vcs/` 目录**不存在** |
| 默认 mock LLM | `huanxin/court/task_engine.py:130` `self._llm = llm or _default_llm_backend` | 默认跑 mock，评测链路失真 |

---

## 1. 五大致命缺口 → 落地映射

| 缺口 | 类型 | 对应阶段 | 关联开源参考（见调研第 2 章） |
|------|------|----------|-------------------------------|
| G1 适应度=响应长度 | 假的真 | **P0.3** | deepeval（🟢可直接集成，做真实评测分）、SWE-bench（🟢真基准） |
| G2 进化退化 | 假的真（G1 后果） | **P0.3 / P1.3** | DGM（论文佐证：先可信评测再进化） |
| G3 护栏谎报+悬空 | 假的真（最危险） | **P0.1 / P0.2** | guardrails-ai（🟢）、NeMo Guardrails（🟢） |
| G4 路由静默失效 | 假的真 | **P0.4** | —（自研，参考 LiteLLM 路由） |
| G5 零代码自修改 | 真的缺 | **P2.1 / P2.2** | E2B（🟢沙箱）、gh API（PR 路径） |

> **原则**：G1–G4 全是"假的真"，修它们的优先级**高于** G5（真的缺）。在 G1–G4 修好前，绝不启用任何进化 / 写回。

---

## 2. 执行总原则与排序

```
关键路径（Must-fix 顺序）：
P0.1 修 PromptGuard 谎报
  → P0.2 接三层护栏到主链
  → P0.3 修适应度 + 冻结淘汰
  → P0.6 建可信评测基准
  → P1.2 真实 LLM + RealLLMFitness
  → P1.3 解冻淘汰（门槛：P0.6 通过）
  → P2.1 GitWriteChannel（PR+人类闸门）
  → P2.2 absorb 分支 + CI + 分支保护
```

**四条铁律**：
1. **先修假的真**：G1–G4 不动，任何"新功能"都是给噪声上叠噪声。
2. **评测可信先于进化**：`llm_judge` 关键词重叠启发式（G3 同类失真）必须被客观基准替代，否则系统无法自我证伪。
3. **写回通道最后接，且人类闸门写死**：`GitWriteChannel` 只开 PR，绝不直推 `master`。
4. **所有修复先影子 / dry-run，再灰度**：护栏先"只记录不阻断"，适应度先"只记分不淘汰"，路由先"建议不强制"。

---

## 3. 模块级实施方案（P0–P3）

每个条目格式：**目标 → 涉及文件 → 具体改动（含代码草图）→ 测试 → 验收 → 协助工具（Codex/Cursor/Trae 提示词）→ 依赖**。

---

### P0.1 修复 PromptGuard 谎报（🔴 最高优先级）

- **目标**：危险输入**真正中止**执行；telemetry 的 `action` 与实际行为一致。
- **文件**：`huanxin/emperor.py:810-831`
- **改动**：在 `:824 if _pg_result.level == "dangerous":` 分支内，**真正 return / raise**，而非只 `logger.warning`。同时把"放行"也显式化，避免静默。
- **代码草图**：

```python
# huanxin/emperor.py  （替换原 824-831 段）
if _pg_result.level == "dangerous":
    logger.warning(
        "[Huanxin] PromptInjectionGuard BLOCKED task=%s level=%s rules=%s",
        task_id, _pg_result.level, _pg_result.matched_rules,
    )
    # 真·阻断：不再继续走 LLM
    return TaskOutcome(
        task_id=task_id,
        minister="__guard__",
        success=False,
        confidence=0.0,
        merit_score=0.0,
        raw_response="",
        error=f"prompt_injection_blocked:rules={_pg_result.matched_rules}",
    )
# allowed 分支：显式放行（行为可见、可测）
```

- **测试**：`tests/test_emperor.py` 新增 `test_prompt_guard_blocks_dangerous`——注入已知危险 prompt，断言返回 `success=False` 且 `error` 含 `prompt_injection_blocked`，且**未调用 LLM 后端**（用 mock 计数）。
- **验收**：注入 10 条已知注入样本，100% 返回阻断；telemetry `action` 与真实行为一致（新增断言）。
- **协助工具**：让 Codex / Cursor 生成上述 return 分支 + 对应单测，VSCode 人工 review 确认"阻断而非警告"。
- **依赖**：无。

---

### P0.2 把三层护栏接到主执行链（影子模式起步）

- **目标**：让 `prompt_guard / tool_guard / hallucination_guard / loop_guard / bounded_autonomy` 在主路径**实际被调用**，先"只记录不阻断"（shadow），灰度后再"阻断"。
- **文件**：`huanxin/emperor.py`（主执行 `run_task` 路径）、`huanxin/guardrail_telemetry.py`
- **改动**：
  1. 在 `run_task` 中，PromptGuard 之后依次调用 ToolGuard（工具调用前）、HallucinationGuard（输出后）、LoopGuard（循环次数）、BoundedAutonomy（权限边界）。
  2. 引入 `settings.guardrail_mode = "shadow" | "enforce"`，shadow 下只 emit 事件不阻断。
- **代码草图**（主链插入点，接在 P0.1 之后）：

```python
# shadow 模式：先观测不阻断
for guard in (self._tool_guard, self._hallucination_guard, self._loop_guard, self._bounded_autonomy):
    ev = guard.check(context=ctx, mode=settings.guardrail_mode)
    self._guardrail_telemetry.emit(ev)
    if settings.guardrail_mode == "enforce" and ev.severity == "block":
        return _blocked_outcome(ev)
```

- **测试**：`tests/test_guardrails_wiring.py`——断言 shadow 模式下 4 个 guard 的 `check` 各被调用一次；enforce 模式下危险输入被阻断。
- **验收**：主链 coverage 包含 4 个 guard；telemetry 事件数与调用数 1:1；shadow→enforce 切换有配置开关。
- **协助工具**：Trae / Cursor 负责把现有 guard 类统一 `check()` 接口（当前各 guard 方法签名可能不一致，需先读 `tool_guard.py`/`hallucination_guard.py` 对齐）。
- **依赖**：P0.1。

---

### P0.3 修复适应度信号 + 冻结自动淘汰

- **目标**：适应度反映"真实任务成败 + 单测通过率"，而非响应长度；进化机制先转 dry-run，只记录不淘汰。
- **文件**：
  - `huanxin/court/task_engine.py:96-111`（替换 `_simple_confidence`）
  - `huanxin/court/court.py`（`SurvivalMechanism` / `_identify_probation_candidates`）
  - `huanxin/court/orchestrator.py`（进化触发点）
- **改动**：
  1. 新增 `RealTaskFitness`：以"执行成功 / 失败 + 单测通过率 + （可选）deepeval 真评测分"为信号。
  2. `TaskEngine` 默认 scorer 切换为 `RealTaskFitness`。
  3. `SurvivalMechanism` 增加 `enabled` 开关，默认 `false`（dry-run：只记 `evolution_history` 不淘汰）。
- **代码草图**：

```python
# huanxin/court/fitness.py
class RealTaskFitness:
    """适应度 = 真实任务成败(0.6) + 单测通过率(0.4)；可插 deepeval 真评测。"""
    def __call__(self, outcome: TaskOutcome, test_pass_rate: float = None) -> float:
        if not outcome.success:
            return 0.0
        base = 0.6
        if test_pass_rate is not None:
            base += 0.4 * max(0.0, min(1.0, test_pass_rate))
        return base
```

```python
# huanxin/court/court.py  （SurvivalMechanism）
class SurvivalMechanism:
    def __init__(self, enabled: bool = False):  # dry-run by default
        self.enabled = enabled
    def maybe_cull(self, minister):
        if not self.enabled:
            logger.info("[Survival] dry-run: skip cull %s", minister)
            return  # 只记录，不淘汰
        ...
```

- **测试**：
  - `tests/test_task_engine.py`：改写 `_simple_confidence` 相关用例 → 改为测 `RealTaskFitness`；新增"长响应但失败 → 低分"、"短响应且单测全过 → 高分"。
  - `tests/test_court.py`：断言 `SurvivalMechanism(enabled=False)` 下 `evolution_history` 有记录但 `merit_after` 不被清零。
- **验收**：进化 20 轮 `merit` 均值**单调不降**（不再是全 0）；淘汰事件数 = 0（dry-run）；长文本 reward hacking 失效。
- **协助工具**：Codex 生成 `fitness.py` + 单测骨架；Cursor 改 `court.py` 接线；VSCode review 确认 `_simple_confidence` 调用点全部替换（grep 无遗留）。
- **依赖**：P0.1、P0.2（护栏先就位，避免"修适应度时系统裸奔"）。

---

### P0.4 路由决策消费 + 补 SmartRouter

- **目标**：`SmartRouter` 真实存在并被调用；其决策**实际影响选臣**，而非只写只读字段。
- **文件**：
  - **新建** `huanxin/model_router.py`（`SmartRouter` 类，当前缺失）
  - `huanxin/emperor.py:259-269`（修静默 import：失败要**显式报警**）
  - `huanxin/court/task_engine.py:352-376`（`_select_minister` 消费路由）
- **改动**：
  1. 新建 `SmartRouter`：`classify(prompt, domain)` / `get_tier_for_capability(cap)` / `get_fallback_chain_for_tier(tier)`。
  2. `emperor.py:268` 把 `except ImportError` 改为 `logger.error("SmartRouter 缺失，路由降级为功勋第一")`（不再静默 `=None`）。
  3. `_select_minister` 用 `SmartRouter` 的 capability 分类结果做真实匹配，命中率 < 90% 回退功勋第一。
- **代码草图**（新建文件骨架）：

```python
# huanxin/model_router.py
from enum import Enum
class Capability(str, Enum):
    MATH = "math"; CODE = "code"; REASON = "reason"; RETRIEVE = "retrieve"; UNKNOWN = "unknown"

class SmartRouter:
    def __init__(self, config_path: str | None = None): ...
    def classify(self, prompt: str, domain: str) -> Capability: ...
    def get_tier_for_capability(self, cap: Capability) -> str: ...
    def get_fallback_chain_for_tier(self, tier: str) -> list[str]: ...
```

- **测试**：`tests/test_model_router.py`（新知识，覆盖 classify/get_tier/get_fallback）+ `tests/test_emperor.py` 断言 `emperor.py:838-841` 在 SmartRouter 存在时**实际执行**（非 `_smart_router=None`）。
- **验收**：选臣与路由建议一致率 ≥ 90% 或安全回退；P2.9 冒烟通过；import 失败时**有 ERROR 日志**（非静默）。
- **协助工具**：Cursor / Trae 生成 `model_router.py`（参考 LiteLLM 路由思路）；VSCode review 接口一致性。
- **依赖**：P0.3。

---

### P0.5 修复 `_select_minister` 空转

- **目标**：领域匹配真正生效（接 `capability_registry`），不再恒返回功勋第一。
- **文件**：`huanxin/court/task_engine.py:352-376`
- **改动**：把 `:363-366` 的 `pass` 替换为：用 `self._capability_registry` 查各大臣 domain，命中的优先；无命中再回退功勋。
- **代码草图**：

```python
def _select_minister(self, domain: str) -> str:
    active = self._court.active_ministers
    if not active:
        raise RuntimeError("No active ministers. Register one first.")
    if self._capability_registry:
        matched = [m for m in active if self._capability_registry.match(m, domain)]
        if matched:
            # 命中者中再按功勋排序
            return max(matched, key=lambda m: self._court.merit_of(m)).name
    ranking = self._court.merit_ranking
    return ranking[0].name if ranking else active[0]
```

- **测试**：`tests/test_task_engine.py` 新增 `test_select_minister_domain_match`——注册两个不同 domain 大臣，断言 domain 匹配者被选中。
- **验收**：不同 domain 任务分发到对应大臣，不再 150/150 全归一人。
- **协助工具**：Codex 生成改后函数 + 单测；VSCode review。
- **依赖**：P0.4。

---

### P0.6 建可信评测基准（替代 `llm_judge` 关键词失真）

- **目标**：用客观基准替代 `huanxin/llm_judge.py` 的关键词重叠启发式与 `agent_eval` 硬编码常量，使"系统是否变好"可证伪。
- **文件**：`huanxin/llm_judge.py`、`huanxin/.../agent_eval`（如有）、**新建** `huanxin/eval_bench/`
- **改动**：
  1. 引入 **deepeval**（🟢可直接集成）做答案正确性评测；引入 **SWE-bench-lite 子集**做"代码类任务"真实基准（填补 `eval/` 无代码任务的空洞）。
  2. `llm_judge` 改为：调用真实 LLM 做裁判 + deepeval 指标，输出可解释分数，禁止关键词重叠启发式。
- **代码草图**：

```python
# huanxin/eval_bench/run.py
from deepeval import assert_test
from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric
def judge(question, answer, context):
    m1, m2 = AnswerRelevancyMetric(), FaithfulnessMetric()
    return {"relevancy": m1.measure(answer, question),
            "faithfulness": m2.measure(answer, context)}
```

- **测试**：`tests/test_eval_bench.py`——用固定问答对断言 judge 输出与人工标注一致率 ≥ 0.8。
- **验收**：评测结果与人工标注相关性 ≥ 0.8；代码类任务有 SWE-bench 子集覆盖；`llm_judge` 不再含"关键词重叠"逻辑（grep 校验）。
- **协助工具**：Cursor 接 deepeval；Codex 写 SWE-bench 子集加载器。
- **依赖**：P0.3（适应度要消费这个基准）。

---

### P1.1 真实 LLM 后端接入（替换 mock）

- **目标**：默认不再跑 `_default_llm_backend` 的 `[mock-response]`，接真实 LLM（OpenAI/Anthropic/LiteLLM）。
- **文件**：`huanxin/court/task_engine.py:130`、`huanxin/llm_engine.py`（或等价）
- **改动**：`TaskEngine` 默认 `llm` 改为读 `config` 的真实后端；保留 mock 仅用于单测。
- **验收**：`pytest` 外跑一次真实任务，返回非 mock 文本；CI 单测仍用 mock（快）。
- **协助工具**：VSCode review 配置切换；Cursor 改默认后端。
- **依赖**：P0.6（真实后端才有意义评测）。

---

### P1.2 RealLLMFitness + P1.3 解冻淘汰

- **目标**：适应度信号经 P0.6 基准验证后，才允许 `SurvivalMechanism.enabled=True`。
- **文件**：`huanxin/court/court.py`、`huanxin/court/fitness.py`
- **改动**：`SurvivalMechanism(enabled=settings.evolution_enabled)`，`evolution_enabled` 默认 `false`，仅在 P0.6 基准通过 + 人工开关后 `true`。
- **验收**：开启后进化 20 轮 `merit` 单调不降；出现晋升事件（不再是全淘汰）。
- **依赖**：P0.3、P0.6、P1.1。

---

### P1.4 晋升流水线 + 失控熔断

- **目标**：进化出现"正向增益"时晋升，出现"指标恶化 / 资源超支"时熔断。
- **文件**：`huanxin/court/court.py`、`huanxin/bounded_autonomy.py`、`huanxin/loop_guard.py`
- **改动**：新增 `PromotionPipeline`（晋升条件：连续 N 轮 merit 提升）与 `CircuitBreaker`（熔断：merit 跌幅超阈值 / 单轮成本超预算即停）。
- **验收**：注入退化场景，断言熔断触发；注入提升场景，断言晋升触发。
- **依赖**：P1.2、P0.2。

---

### P2.1 GitWriteChannel（写回通道，PR + 人类闸门）

- **目标**：实现"AI 修改自身代码"的唯一合法通道——只开 PR，**绝不直推 `master`**。
- **文件**：**新建** `huanxin/vcs/git_channel.py`（当前 `huanxin/vcs/` 不存在）
- **设计**（对应调研第 5 章 §5.5）：
  1. 在隔离沙箱（参考 E2B 🟢）应用补丁 → 提交到 `absorb-<date>` 分支 → 用 **gh API** 开 PR。
  2. **绝不** `git push` 到 `master`、绝不 `gh api ... --method PUT|POST` 到受保护分支。
  3. 人类 review 通过后才合入（闸门在 GitHub 分支保护规则，不在代码里绕过）。
- **代码草图**：

```python
# huanxin/vcs/git_channel.py
import subprocess, tempfile, os
class GitWriteChannel:
    """唯一合法的代码写回通道：只开 PR，不直推 master。"""
    PROTECTED = ("master", "main")
    def propose_change(self, repo: str, patch: str, title: str) -> str:
        branch = f"absorb-{os.environ.get('DATE','draft')}"
        work = tempfile.mkdtemp()
        subprocess.run(["git","clone",f"https://github.com/{repo}.git",work], check=True)
        subprocess.run(["git","-C",work,"checkout","-b",branch], check=True)
        # apply patch, commit ...
        subprocess.run(["git","-C",work,"push","origin",branch], check=True)
        # 用 gh API 开 PR（人类审批门）
        out = subprocess.run(
            ["gh","api","repos/{r}/pulls".format(r=repo),"-f","title="+title,
             "-f","head="+branch,"-f","base=master","-f","body=auto-absorb"],
            capture_output=True, text=True, check=True)
        return branch  # 等待人类 review，不自动合入
```

- **测试**：`tests/test_git_channel.py`——断言 `propose_change` 产出分支名以 `absorb-` 开头、且**从不**调用 `git push origin master`（用 mock 校验命令序列）。
- **验收**：端到端跑一次，GitHub 上出现待 review 的 PR；`master` 未被改动。
- **协助工具**：Cursor 生成 `git_channel.py`；VSCode 重点 review "绝不直推 master" 的断言。
- **依赖**：P1.4（先有可信进化，才有可写回的改进）。

---

### P2.2 absorb 分支模型 + CI + 分支保护

- **目标**：把"持续吸收"工程化，且用 CI **反向校验**无人绕过写回通道。
- **文件**：**新建** `.github/workflows/absorb.yml`、`.github/branch-protection.json`
- **改动**：
  1. 分支模型：`master`（受保护）/ `develop` / `auto` / `absorb-*`（每条吸收一个）。
  2. `absorb.yml`：PR 到 `master` 时跑测试 + `check_write_protect`。
  3. `check_write_protect`：**除 `huanxin/vcs/git_channel.py` 外，任何文件出现 `git push` / `gitpython` 写 API / `gh api ... --method PUT|POST` 即判红**。
- **代码草图（CI 反向校验）**：

```yaml
# .github/workflows/absorb.yml
name: absorb
on: [pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pytest -q
      - run: python scripts/check_write_protect.py
```

```python
# scripts/check_write_protect.py
import subprocess, sys
bad = subprocess.run(["grep","-rn","git push origin master\\|gh api .*--method PUT",
                     "huanxin","--include=*.py"], capture_output=True, text=True).stdout
if bad and "git_channel.py" not in bad:
    print("WRITE-PROTECT VIOLATION:\n", bad); sys.exit(1)
```

- **验收**：故意在非法文件写 `git push origin master` → CI 红；`git_channel.py` 内合法调用 → CI 绿。
- **协助工具**：VSCode 配分支保护规则；Cursor 写 `absorb.yml`。
- **依赖**：P2.1。

---

### P3.1 监控看板（可选，EdgeOne 部署）

- **目标**：把 `huanxin/dashboard_html.py` 增强为"进化健康 + 护栏命中 + 吸收队列"看板，用 **EdgeOne Pages** 部署可预览。
- **文件**：`huanxin/dashboard_html.py`、`huanxin/pipeline_monitor.py`
- **协助工具**：**frontend-dev 技能**生成看板 UI；**EdgeOne Pages 连接器**部署预览。
- **依赖**：P2.2。

---

## 4. 可复制命令序列

### 4.1 本地开发（每阶段）
```bash
cd D:/AI自我进化/huanxin-ai
git checkout -b absorb-$(date +%F)-p0X        # 每个 P0 项一个分支
# 用 Cursor/Trae/Codex 生成改动 → VSCode review
pytest -q                                    # 全量回归
pytest tests/test_emperor.py::test_prompt_guard_blocks_dangerous -q   # 单点验证
git add -A && git commit -m "fix: P0.X ..."
gh repo fork && gh pr create --base master --title "P0.X ..."   # 人类 review
```

### 4.2 GitHub 工作流（主轴）
```bash
# 建分支（绝不直推 master）
gh api repos/kiwoen/huanxin-ai/git/refs -f "ref=refs/heads/absorb-$(date +%F)" -f "sha=<base>"
# 开 PR（人类审批门）
gh api repos/kiwoen/huanxin-ai/pulls -f "title=P0.X 修复" -f "head=absorb-$(date +%F)" -f "base=master"
# 分支保护（写死，禁止直推）
gh api repos/kiwoen/huanxin-ai/branches/master/protection -X PUT \
  -f "required_pull_request_reviews[required_approving_review_count]=1" \
  -f "enforce_admins=true"
```

### 4.3 反向校验（CI 本地预跑）
```bash
python scripts/check_write_protect.py      # 提交前自查
pytest --cov=huanxin --cov-report=term      # 覆盖率门槛（建议 ≥ 70%）
```

---

## 5. 本机 AI 应用协同矩阵

| 应用 / 技能 | 在本项目中的角色 | 使用阶段 | 备注 |
|-------------|------------------|----------|------|
| **GitHub 连接器** | 仓库读取、建分支、开 PR、分支保护、CI 触发 | 全阶段（主轴） | 唯一真正的"代码落地"通道 |
| **Codex / Cursor / Trae** | 代码生成（各模块草图 → 实现）、单测骨架 | P0.1–P2.2 | 给每个模块的标准提示词见第 3 章"协助工具" |
| **VSCode** | 人工 review、配置切换、分支保护规则落地 | 全阶段 | 人类审批门的人工侧 |
| **pytest** | 客观验证（每个 P 项有验收测试） | 全阶段 | 替代"看起来工作" |
| **frontend-dev 技能 + EdgeOne Pages** | 监控看板生成与部署预览 | P3.1 | 可选 |
| **agent-browser / 联网检索** | 深化第 2 章开源参考、追新论文（如 DGM 更新） | P0.6 / 持续 | 参考用，不写码 |
| ❌ 邮箱 / 网盘 / 设计 / 电商类连接器 | — | — | 与 huanxin-ai 代码实现无关，**明确不纳入** |

> **说明**：本机已连接 77 个连接器，但绝大多数（邮件、文档、设计、电商、出海等）对"实现 Python 自进化后端"无直接助益。本文档只调动**与写码/验证/部署相关**的能力，避免无效调用。

---

## 6. 里程碑与依赖图

```
P0.1 ─┐
      ├─► P0.2 ─► P0.3 ─► P0.6 ─► P1.1 ─► P1.2 ─► P1.3 ─► P1.4 ─► P2.1 ─► P2.2 ─► P3.1
P0.4 ─┘                 (P0.4→P0.5→P0.3)
P0.5 ───────────────────────────────────────────────────────┘
```

| 里程碑 | 出口标准（DoD 片段） |
|--------|----------------------|
| M0 可信地基 | P0.1–P0.5 全绿；护栏接主链；路由真实生效；选臣按 domain 分发 |
| M1 进化可信 | P0.6 基准相关 ≥0.8；P1.2 适应度用真实分；P1.3 解冻后现晋升事件 |
| M2 写回闭环 | P2.1 开 PR 成功且不碰 master；P2.2 CI 反向校验拦住非法写入 |
| M3 可观测 | P3.1 看板部署可预览，含进化健康 + 吸收队列 |

---

## 7. 风险边界与熔断（硬性）

1. **绝不自动合 `master`**：所有写回走 PR + 人类 review；`GitWriteChannel` 无直推路径。
2. **沙箱运行进化**：进化实验只在隔离环境（E2B / 容器），不连生产 `huanxin.db`。
3. **资源预算**：单轮进化成本超 `cost_per_success_baseline` × N 即熔断（`bounded_autonomy` + `cost_tracker`）。
4. **权限模型**：`rbac.py` 约束"谁能让 AI 写代码"；默认最小权限，写回需显式授权。
5. **退化即停**：`CircuitBreaker` 见 merit 跌幅超阈值立即停进化并告警。
6. **可证伪优先**：任何"自进化收益"声明须附 SWE-bench / deepeval 客观证据，否则视为未验证。

---

## 8. 完成定义（DoD）

- [ ] P0.1–P0.6 全绿，且对应单测覆盖（含 PromptGuard 真阻断、护栏接线、适应度非长度、SmartRouter 存在、选臣 domain 匹配、评测基准相关 ≥0.8）
- [ ] P1.1–P1.4 绿，进化出现晋升事件、`merit` 单调不降、失控可熔断
- [ ] P2.1–P2.2 绿，`absorb-*` PR 成功、`master` 零直推、CI `check_write_protect` 拦非法写入
- [ ] P3.1（可选）看板部署可预览
- [ ] 全程 GitHub PR + 人类 review，无未经审批的 `master` 改动
- [ ] 复盘文档：对比"修前（192/192 淘汰、适应度=长度、护栏谎报）"与"修后"的客观指标

---

## 9. 后续动作建议

1. **本轮交付边界**：本文档为实施方案，**未改动任何源码、未建分支、未开 PR**（与调研文档边界一致）。
2. **若你确认开始执行**：我可立即按 P0.1 起手——用 Cursor/Trae 生成 `emperor.py` 阻断分支 + `tests/test_emperor.py` 单测，经 VSCode review 后，**由你在 GitHub 上开 PR**（人类闸门）。我不会自行直推。
3. **GitHub 跟踪**：如需，我可用 GitHub 连接器把本文 9 个里程碑开成 Issue / Project，让工作沉淀在代码所在仓库。
4. **持续吸收**：执行阶段可同步启用第 5 章设计的 `absorb` 流水线，但**人类审批门始终保持写死**。

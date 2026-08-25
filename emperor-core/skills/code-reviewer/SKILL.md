---
name: "code-reviewer"
description: "对代码片段做八维加权代码审查（正确性/安全/性能/可维护/测试/并发/可观测/文档），类型适配权重，诚实 N/A，输出带 file:line 定位与可执行建议的结构化报告。触发：代码审查、审查代码、code review、代码评审、代码审计、review、audit。"
triggers:
  - "代码审查"
  - "code review"
  - "code review"
  - "代码评审"
  - "审计"
---

# Code Reviewer · 多维加权代码审查

## 系统定位
huanxin-ai 的「自我审视代码」能力，是生命体自检机制的核心。负责把一段代码按工程逻辑与工业执行两个层面系统性审查，产出可追溯、可修复的问题清单。
- 上游：工部尚书（`WorksMinister._handle`，COURT 路径）/ 工程领域模块（`domains/engineering`，DIRECT 路径）/ 用户直接请求
- 下游：修复建议回流至代码生成大臣，或作为进化评估的「硬伤」信号

## 类型识别与路由
`detect_language(code, hint)`：按扩展名/语法启发识别 python/js/ts/go/rust/java/cpp/generic。
`detect_code_type(code, lang)`：识别 web_service / data_pipeline / cli / test_suite / library / script，据此应用 `_TYPE_WEIGHT_ADJUST` 权重微调。

## 核心功能
八维独立审查（`Dimension` 枚举），每维 0–10：
1. **correctness** 正确性：语法、可变默认参、is 比较字面量、assert 生产逻辑
2. **security** 安全：硬编码密钥、eval/exec、shell=True、不安全反序列化、SQL 拼接、verify=False
3. **performance** 性能：循环内字符串拼接 O(n²)
4. **maintainability** 可维护：复杂度/长度/命名
5. **testing** 测试：断言质量（仅当测试代码时适用）
6. **concurrency** 并发：异步阻塞调用、共享可变状态（仅当有并发结构时适用）
7. **observability** 可观测：裸 except、静默吞异常、print 代替 logging
8. **documentation** 文档：docstring / 类型注解 / 模块说明

严重度：`🔴critical(-4) / 🟡major(-2) / 🟢minor(-0.5)`；维度分 = max(0, 10 - Σpenalty)。

## 工作流程
1. 识别语言与代码类型
2. 收集各维度问题（Python 走 AST，其他语言走通用+语言无关规则）
3. 维度适用判定（`_is_applicable` → 诚实 N/A）
4. 计算加权总分与定级（S≥9 / A≥8 / B≥7 / C≥6 / D<6）
5. 渲染 Markdown 报告（`CodeReviewer.to_markdown`）

## 输出格式
`ReviewReport` dataclass（language / code_type / dimensions / issues / honest_na / overall_score / grade / summary），配套 `to_markdown()` 兼容 script-multi-review 风格：八维评分表 + 按严重度排序的问题清单 + 优先修改 Top 10。

## 审查原则与铁律
- 类型适配优先，禁止一刀切
- 问题定位必须精确（file:line）
- 建议必须可执行（禁止「加强」「改善」空话）
- 区分硬伤（工业标准）与风格偏好
- **八维独立评分**，一维高分不弥补另一维低分
- **诚实 N/A**：无法评估的维度如实标注，不强行打分

## 与其他技能的联动
| 模块 | 联动方式 |
|------|----------|
| `WorksMinister` | COURT 路径审查意图直达本引擎 |
| `domains/engineering` | DIRECT 路径审查意图直达本引擎 |
| `SelfEvolutionEngine` | 审查硬伤可作为进化评估负反馈 |

## 版本记录
- v1.0 (2026-08)：从 `script-multi-review` 翻译八维加权审查范式，落地 `huanxin/codex/reviewer.py`
- v1.1 (2026-08-17)：接入工部尚书派发闭环（`WorksMinister._handle` 调用 `CodeReviewer`）

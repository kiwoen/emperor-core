# Phase 12 续⁵：记忆衰减 / 留存窗口

## 本轮改动
之前经验聚合是**简单等权计数**——陈旧样本与新鲜样本权重相同，且记忆只增不减，陈旧成败会**永久主导**「谁执行 / 基因朝哪校准」。Phase 12 续⁵ 加两道**确定性、默认关（零回归）**的边界：

- `CourtMemory(max_per_group=None)`：每 (大臣,领域) 留存上限。`record()` 超限时 `prune_oldest_per_group()` 丢弃最旧样本，直接边界化「只增不减」。`save/load` 持久化该配置，`--resume` 复用同一窗口。
- `CourtMemory.per_minister_domain_quality(recency_decay=1.0)`：按**插入序（非墙钟）**给新鲜样本更高权重（最新=1.0、最旧=d^(n-1)）；`=1.0` 退化为朴素计数，与原逻辑逐位等价。
- 引擎接线：`memory_recency_decay` / `memory_max_per_group` 透传 → 路由与暖启动在 `<1.0` 时改用加权成功率（暖启动**校准率**加权、**样本量 t** 仍取原始计数决定步长）。
- 入口：`huanxin/cli.py`、`scripts/run_self_evolve.py` 新增 `--memory-recency-decay` / `--memory-max-per-group`；`configs/self_evolve.yaml` 加选项（默认关 → 零回归）。

## 验证（178 passed）
- 新增 `tests/test_memory_decay.py`（6 项）：按组裁剪只留最新 N、时间衰减最新全胜+最旧全败→加权率>等权率、默认零回归（不封顶+等权=朴素计数）、save/load 保留上限、引擎内部记忆 cap 生效、配置默认零回归。
- 定向回归（memory / self_evolve / safety / evolution / landing / real_executor）合计 **178 passed**，仅 `datetime.utcnow` 既有弃用告警（非本次引入）。
- 端到端：`--memory-recency-decay 0.5 --memory-max-per-group 3 --cycles 2` 跑通 EXIT=0、health=healthy、四域记忆正常。

## 已提交
`6921d21`（分支 `feature-landing-hitl-audit`）；`outcome_records.json`、`overview.md` 等运行时/瞬时产物已排除。⚠️ GitHub 推送仍受阻（环境无 git 凭证），待真实终端 `git push` 后快进同步。

## 闭环全景（Phase 11 → 12 续⁵）
真实执行 → 真实自我学习 → 经验落盘(跨重启) → 经验驱动派发 → 经验驱动基因(自适应步长) → **经验留存/衰减边界** → 进化决策由真实答对率(金标准安全闸)门控。

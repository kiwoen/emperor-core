# 自进化（Self-Evolution）

## 默认：离线 / DEMO 模式
系统**默认完全离线、确定性、可复现**，不依赖 LLM key、不连网、不碰真实 git：

- 执行器：`GenomeDrivenExecutor` 用「基因到最优点的距离」驱动确定性成败信号，
  使进化有真实可优化的梯度（更优基因 → 更高功勋 → 被选择保留 / 交叉）。
- 写回通道：`RecordingWriteChannel` **只记录**"本会发起的 PR"，绝不直推受保护分支。
- 合成质量：`true_quality()` 为合成值，因此 `evolution_stagnation` 告警的 merit 常恒为 `0.000`，
  这是**预期行为**（离线模拟），并非真实回归。

启动时若处于离线模式，会打印明确横幅：
`[SelfEvolve] OFFLINE / DEMO MODE — 本次自进化使用合成任务与合成质量（非真实 LLM 评测）`。

## 真实（live）模式
源码自改写（`codex` + `GitWriteChannel`）仅在 **PR + WritebackGate 评测闸** 下生效，
默认不自动执行；需显式开启并经由人工审批。

## 查询状态
`GET /api/dashboard/self-evolve-status` 聚合：
- `RunReport`（含 `mode: "offline" | "live"`）
- `SchedulerReport`（调度器状态）
- guardrail 状态

返回体明确标注当前是 `offline` 还是 `live`。

## 相关文档
- 监控见 [MONITORING.md](MONITORING.md)
- 配置见 [CONFIG.md](CONFIG.md)

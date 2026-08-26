## 变更类型

> 请选择适用的类型（保留一个，删除其余）

- [ ] 新功能 (feat)
- [ ] Bug 修复 (fix)
- [ ] 文档更新 (docs)
- [ ] 代码重构 (refactor)
- [ ] 性能优化 (perf)
- [ ] 测试补充 (test)
- [ ] CI / 构建 / 工具 (chore/ci)
- [ ] 其他：<!-- 请说明 -->

## 变更描述

<!-- 清晰描述本次 PR 做了什么变更，以及为什么这么做。 -->

## 关联 Issue

<!--
  引用关联的 Issue（如 Closes #42, Fixes #99）。
  如果没有已存在的 Issue，请说明是否需要在合并前创建。
-->

Closes #

## 测试说明

### 如何测试

<!-- 描述测试步骤，让 Reviewer 可以复现验证。 -->

1.
2.
3.

### 测试结果

<!-- 贴出关键测试用例的执行结果。 -->

```
<!-- pytest 输出（如有覆盖率报告也一并附上） -->
```

## Checklist

> 提交前请逐一确认：

- [ ] 代码遵循项目规范（`make format && make lint` 通过）
- [ ] 类型检查通过（`make typecheck`）
- [ ] 已添加必要的测试用例，且 `make test` 通过
- [ ] 新增的公开 API 已更新对应文档（`docs/API.md` 等）
- [ ] 已按 Conventional Commits 规范撰写 PR 标题
- [ ] Commit 历史清晰（已 rebase 并 squash 冗余提交）
- [ ] 无遗留的调试代码或 `print` 语句
- [ ] 新依赖已同步到 `pyproject.toml`
- [ ] 确认通过 CI 所有检查

## 补充说明

<!-- Reviewer 需要了解的其他上下文或注意事项。 -->

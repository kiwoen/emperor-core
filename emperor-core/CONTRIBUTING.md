# Contributing to Emperor-Core

感谢你对 **Emperor-Core (J.A.R.V.I.S.)** 的关注！本文档提供了参与贡献所需的全部指引。

## 目录

- [开发环境搭建](#开发环境搭建)
- [代码规范](#代码规范)
- [Conventional Commits](#conventional-commits)
- [PR 流程](#pr-流程)
- [测试要求](#测试要求)
- [文档](#文档)

---

## 开发环境搭建

### 前置条件

- **Python 3.11+** (CI 矩阵覆盖 3.11 / 3.12 / 3.13)
- **Git**

### 安装步骤

```bash
# 克隆仓库
git clone <repo-url>
cd emperor-core

# 创建虚拟环境（推荐）
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux / macOS
source .venv/bin/activate

# 安装开发依赖（包含 pytest / ruff / black / mypy / pre-commit）
pip install -e ".[dev]"
```

### 一键命令速查

| 命令 | 说明 |
|------|------|
| `make install-dev` | 安装开发依赖 |
| `make lint` | 运行 ruff 检查 |
| `make format` | Ruff 自动格式化 + 自动修复 |
| `make typecheck` | MyPy 类型检查 |
| `make test` | 运行 pytest（跳过网络测试） |
| `make test-all` | 运行完整测试套件 |
| `make coverage` | 运行测试并生成覆盖率报告 |

---

## 代码规范

### 格式化

本项目使用 **Ruff** 进行代码格式化和 Lint 检查，配置见 `pyproject.toml`。

- 行宽限制：**120 字符**
- 引号风格：双引号
- 缩进：4 空格
- 目标 Python 版本：**3.11**

```bash
# 格式化
ruff format .

# Lint 检查
ruff check .
```

### Lint 规则

启用了以下规则组（`pyproject.toml` 中的 `tool.ruff.lint.select`）：

| 规则 | 说明 |
|------|------|
| E / W | pycodestyle (代码风格错误 & 警告) |
| F | Pyflakes (未使用变量/导入) |
| I | isort (导入排序) |
| N | pep8-naming (命名规范) |
| UP | pyupgrade (新语法建议) |
| B | flake8-bugbear (常见陷阱) |
| C4 | flake8-comprehensions (推导式优化) |
| SIM | flake8-simplify (简化建议) |

### 类型注解

所有新增代码应提供类型注解。使用 **MyPy** 检验：

```bash
mypy jarvis/ --ignore-missing-imports
```

完整的 MyPy 配置（strict mode 开启）见 `pyproject.toml` → `[tool.mypy]`。

### 命名约定

- 模块/包：`snake_case`
- 类：`PascalCase`
- 函数/方法/变量：`snake_case`
- 常量：`UPPER_SNAKE_CASE`
- 私有成员：单下划线前缀 `_private_method`

---

## Conventional Commits

所有提交消息必须遵循 [Conventional Commits](https://www.conventionalcommits.org/) 规范：

```
<type>(<scope>): <description>

[optional body]

[optional footer]
```

### 允许的 Type

| Type | 说明 |
|------|------|
| `feat` | 新功能 |
| `fix` | Bug 修复 |
| `docs` | 文档变更 |
| `style` | 代码格式（不影响逻辑） |
| `refactor` | 重构 |
| `perf` | 性能优化 |
| `test` | 测试相关 |
| `chore` | 构建/工具/CI 变更 |
| `ci` | CI 配置变更 |

### Scope 示例

`core` / `court` / `consensus` / `evolution` / `healing` / `dashboard` / `rag` / `multimodal` / `mcp` / `plugins` / `cli`

### 示例

```
feat(court): add diversity-based minister rotation
fix(healing): resolve cooldown race condition on concurrent triggers
docs(api): update Court API endpoint descriptions
```

---

## PR 流程

### 1. Fork & Branch

```bash
git checkout -b feat/my-feature    # feature 分支
git checkout -b fix/my-fix         # fix 分支
```

### 2. 开发

- 遵循代码规范
- 添加必要的测试
- 确保 `make lint && make typecheck` 通过

### 3. 提交前自检

```bash
make format      # 格式化
make lint        # Lint 检查
make typecheck   # 类型检查
make test        # 本地测试
```

### 4. 提交 PR

- 分支：指向 `main`
- 标题：遵循 Conventional Commits 格式
- 描述：使用 PULL_REQUEST_TEMPLATE 模板填写
- 关联 Issue：引用相关 Issue 编号（如 `Closes #42`）

### 5. CI 检查

PR 会触发以下 CI 检查（参见 `.github/workflows/ci.yml`）：

| 阶段 | 说明 |
|------|------|
| Lint (ruff) | 代码风格检查 |
| Test (py3.11/3.12/3.13 × ubuntu/windows) | 跨平台 + 跨版本测试矩阵 |
| Type Check (mypy) | 类型检查 |

### 6. Code Review

至少需要一次 Maintainer 的 Approve 后才能合并。

---

## 测试要求

### 框架

- **pytest >= 8.0** + `pytest-asyncio`

### 运行测试

```bash
# 跳过网络相关测试（本地推荐）
make test

# 完整测试
make test-all

# 带覆盖率
make coverage
```

### 测试规范

1. 新功能必须包含测试用例。
2. Bug 修复必须包含回归测试。
3. 网络相关的测试使用 `@pytest.mark.network` 标记（CI 中跳过）。
4. 耗时较长的测试使用 `@pytest.mark.slow` 标记。
5. 测试文件命名：`tests/test_<module>.py`。

### 覆盖率

CI 在 `ubuntu-latest` 上生成覆盖率报告并上传到 Codecov。

---

## 文档

- API 文档：`docs/API.md`
- 架构文档：`docs/ARCHITECTURE.md`
- 快速入门：`docs/QUICKSTART.md`
- 变更日志：`docs/CHANGELOG.md`

修改公开 API 或架构时，请同步更新相关文档。

---

如有疑问，请在 Issue 中提出。

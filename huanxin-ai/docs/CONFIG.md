# 配置（Configuration）

## 单一真相源
配置以 **pydantic `BaseSettings`** 为底座，定义在 `huanxin/config.py` 的 `class HuanxinConfig`
（原项目曾有 3 套同名 `HuanxinConfig`，现已统一为这一处）。

- 环境变量可直接覆盖字段（BaseSettings 行为）。
- `huanxin.yaml` 作为可选覆盖层（若存在则叠加）。

## 关键字段
| 字段 | 默认 | 说明 |
|------|------|------|
| `api_port` | `8000` | 服务监听端口（全项目统一 8000） |
| `auto_schedule` | `True` | 自进化调度器是否随服务启动 |
| `open_registration` | `False` | 公开注册默认关闭（运行时仍受 `HUANXIN_OPEN_REGISTRATION` 环境变量控制） |
| `sandbox.engine` | `"docker"` | 沙箱执行引擎：`docker` / `local_subprocess` / `local_direct` |

> 完整字段以 `huanxin/config.py` 源码为准。

## 相关文档
- 部署见 [DEPLOY.md](DEPLOY.md)
- 鉴权见 [AUTH.md](AUTH.md)
- 自进化见 [SELF_EVOLVE.md](SELF_EVOLVE.md)

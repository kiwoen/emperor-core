# 监控（Monitoring）

## 健康检查
`GET /health` → `{"status":"ok","service":"huanxin-ai"}`
由 Caddy / 云平台探针轮询；容器标记为 `healthy`。

## 状态端点
- `GET /status`：服务 + 调度器状态。
- `GET /api/dashboard/self-evolve-status`：自进化运行报告（offline/live）、调度器报告、guardrail。

## 调度器日志
`auto_schedule` 开启时，调度器在**启动**与**每次调度**时输出 INFO 级日志。
如未看到调度日志，检查 `auto_schedule` 配置与 `HUANXIN_*` 环境变量。

## 已知告警（预期内）
- `[ALERT WARNING] evolution_stagnation → No merit improvement ... (0.000)`：
  离线模式下合成质量恒为 0.000，属**预期噪声**，不代表系统异常。
- `web_search failed ... Network is unreachable` / `news query failed ...`：
  容器无外网出口时，联网搜索 / 新闻能力失效。需排查容器网络（Docker 出口 NAT / ECS 安全组）。
- `[Huanxin] HallucinationGuard flagged N claims`：幻觉护栏正常拦截低置信声明，非错误。

## 查日志
```bash
docker compose logs --tail 30 huanxin-ai
```

## 相关文档
- 部署见 [DEPLOY.md](DEPLOY.md)
- 自进化见 [SELF_EVOLVE.md](SELF_EVOLVE.md)

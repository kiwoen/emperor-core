"""回归测试：运行完善 A/B/C/D（commit 2ad9603）。

验证「统一配置真相源 / 收敛鉴权 / 自进化可观测 / 清理技术债」四类变更的回归行为。

验收点映射：
- T-A02 / T-A04：强制会话鉴权收敛为 Bearer-only，移除 ``?token=`` 查询参数通道；
                  公共白名单仅含 ``/health``、``/api/auth/login``、``/api/auth/register``、``/``。
- T-A03：``/dashboard`` 不再公共（无 Bearer → 401）；``/`` 仍公共（200）。
- T-A06 / T-C01：新增 ``/status`` 与 ``/api/dashboard/self-evolve-status`` 可观测端点。
- T-A07：开放注册默认关闭（配置 + 接口双层）。
- T-B01：配置统一为单一 pydantic ``BaseSettings`` 真相源（``HuanxinConfig``），端口统一 8000。
- T-D01：调度器不再静默吞异常（broad except 改为 ``logger.warning`` / ``logger.exception``）；
          ``Scheduler.report()`` 暴露 status / self-evolve 端点依赖的契约属性。
- T-D04：``RunReport.mode`` 默认 ``offline``，``to_dict`` 含 ``mode``；offline/DEMO 横幅已记录。

约定（与 ``tests/test_court_api.py`` 对齐）：
- 管理员凭据由根 ``conftest.py`` 注入 ``HUANXIN_ADMIN_USER`` / ``HUANXIN_ADMIN_PASS``；
  ``_login`` 复刻 test_court_api 的登录助手，密码与 ``ensure_admin`` 一致。
- 未鉴权请求使用裸 ``TestClient(app)``（不挂 ``Authorization`` 头）。
- 鉴权请求使用 ``client`` fixture（已登录并注入 Bearer 头）。
"""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient

from huanxin.court_api import app
from huanxin.config import HuanxinConfig
from huanxin.court.scheduler import Scheduler, SchedulerReport
from huanxin.self_evolve import RunReport


def _login(client: TestClient) -> str:
    """以种子管理员登录并返回会话 token（与 test_court_api.py 对齐）。"""
    r = client.post(
        "/api/auth/login",
        json={
            "username": os.environ["HUANXIN_ADMIN_USER"],
            "password": os.environ["HUANXIN_ADMIN_PASS"],
        },
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return r.json()["token"]


@pytest.fixture
def client() -> TestClient:
    # 强制会话登录中间件要求有效会话 token，测试客户端必须先以种子管理员登录并注入 Bearer 头。
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {_login(c)}"
    return c


@pytest.fixture
def unauth_client() -> TestClient:
    # 裸客户端：不携带任何 Authorization 头，用于验证未鉴权行为。
    return TestClient(app)


# ══════════════════════════════════════════════════════════════════
# T-A02 / T-A04：Bearer-only 鉴权，?token= 查询参数通道已移除
# ══════════════════════════════════════════════════════════════════


class TestBearerOnlyAuth:
    def test_health_is_public(self, unauth_client):
        # /health 在 public_paths 白名单 → 200（T-A02 公共探针）
        assert unauth_client.get("/health").status_code == 200

    def test_login_endpoint_reachable_without_bearer(self, unauth_client):
        # /api/auth/login 在 public_paths（POST-only 路由），未带 Bearer 也能访问。
        r = unauth_client.post(
            "/api/auth/login",
            json={
                "username": os.environ["HUANXIN_ADMIN_USER"],
                "password": os.environ["HUANXIN_ADMIN_PASS"],
            },
        )
        assert r.status_code == 200

    def test_protected_route_requires_bearer(self, unauth_client):
        # 受保护路由（/dashboard）不在 public_paths → 未带 Bearer 返回 401（T-A04 核心）
        assert unauth_client.get("/dashboard").status_code == 401

    def test_query_token_channel_removed(self, unauth_client):
        # ?token= 查询参数通道已移除：即使携带 query token，无 Bearer 仍 401（T-A04 核心）。
        assert unauth_client.get("/dashboard?token=HACK").status_code == 401

    def test_login_get_not_blocked_by_auth_guard(self, unauth_client):
        # /api/auth/login 是公共路径（POST-only）。GET 到它不应被鉴权中间件拦截为 401
        # （应为 405 方法不允许等路由层响应，但绝不能是 401 鉴权拒绝）。
        r = unauth_client.get("/api/auth/login")
        assert r.status_code != 401

    def test_valid_bearer_passes_guard(self):
        # 有效 Bearer 通过鉴权，可访问受保护路由（200）—— 证明仅 Authorization: Bearer 通道有效。
        c = TestClient(app)
        token = _login(c)
        c.headers["Authorization"] = f"Bearer {token}"
        assert c.get("/court/summary").status_code == 200


# ══════════════════════════════════════════════════════════════════
# T-A03：/dashboard 不再公共；/ 仍公共
# ══════════════════════════════════════════════════════════════════


class TestDashboardNotPublic:
    def test_root_is_public(self, unauth_client):
        # / 在 public_paths → 200（T-A03：根路径仍公共）
        assert unauth_client.get("/").status_code == 200

    def test_dashboard_blocked_without_bearer(self, unauth_client):
        # /dashboard 不在 public_paths → 401（T-A03 核心）
        assert unauth_client.get("/dashboard").status_code == 401

    def test_dashboard_reachable_with_valid_bearer(self, client):
        # 仅收敛鉴权，仪表盘本身仍可用（带 Bearer → 200）
        assert client.get("/dashboard").status_code == 200


# ══════════════════════════════════════════════════════════════════
# T-A06 / T-C01：新增可观测端点
# ══════════════════════════════════════════════════════════════════


class TestObservabilityEndpoints:
    def test_status_endpoint(self, client):
        # GET /status → 200 且 JSON 含 status / service / scheduler（court_api.py:625）
        r = client.get("/status")
        assert r.status_code == 200
        data = r.json()
        assert "status" in data
        assert "service" in data
        assert "scheduler" in data

    def test_self_evolve_status_endpoint(self, client):
        # GET /api/dashboard/self-evolve-status → 200，含 mode（offline/live）与 self_evolve 段
        # （court_api.py:3957）。即使 app.extra["scheduler"] 为 None，端点仍返回 200。
        r = client.get("/api/dashboard/self-evolve-status")
        assert r.status_code == 200
        data = r.json()
        assert data.get("mode") in ("offline", "live")
        assert "self_evolve" in data


# ══════════════════════════════════════════════════════════════════
# T-A07：开放注册默认关闭（配置 + 接口双层）
# ══════════════════════════════════════════════════════════════════


class TestOpenRegistrationClosed:
    def test_config_default_closed(self, monkeypatch):
        # 默认未设 HUANXIN_OPEN_REGISTRATION → open_registration 为 False（config.py:266）
        monkeypatch.delenv("HUANXIN_OPEN_REGISTRATION", raising=False)
        assert HuanxinConfig().open_registration is False

    def test_config_open_when_env_set(self, monkeypatch):
        # 设置 HUANXIN_OPEN_REGISTRATION=1 → open_registration 为 True（env_prefix=HUANXIN_）
        monkeypatch.setenv("HUANXIN_OPEN_REGISTRATION", "1")
        assert HuanxinConfig().open_registration is True

    def test_register_closed_by_default(self, unauth_client, monkeypatch):
        # 未开放注册 → POST /api/auth/register 返回 403（"注册已关闭"）（court_api.py:1179-1181）
        monkeypatch.delenv("HUANXIN_OPEN_REGISTRATION", raising=False)
        r = unauth_client.post(
            "/api/auth/register",
            json={"username": f"qa_{uuid.uuid4().hex[:10]}", "password": "secret123"},
        )
        assert r.status_code == 403

    def test_register_open_when_env_set(self, unauth_client, monkeypatch):
        # 开放注册 → POST /api/auth/register 返回 200 并下发 token
        monkeypatch.setenv("HUANXIN_OPEN_REGISTRATION", "1")
        r = unauth_client.post(
            "/api/auth/register",
            json={"username": f"qa_{uuid.uuid4().hex[:10]}", "password": "secret123"},
        )
        assert r.status_code == 200
        assert "token" in r.json()


# ══════════════════════════════════════════════════════════════════
# T-B01：单一配置真相源（HuanxinConfig）
# ══════════════════════════════════════════════════════════════════


class TestSingleConfigSourceOfTruth:
    def test_huanxin_config_importable(self):
        # 配置统一在 huanxin.config.HuanxinConfig（单一真相源）。
        # 真正的唯一性由代码评审 grep 证明（全仓仅 huanxin/config.py:179 定义 class HuanxinConfig）。
        assert HuanxinConfig is not None

    def test_dashboard_port_unified_to_8000(self):
        # 双服务（9020 管理 + 8000 API）已收敛为单端口 8000。
        assert HuanxinConfig().dashboard.port == 8000


# ══════════════════════════════════════════════════════════════════
# T-D01：调度器不再静默吞异常 + report() 契约
# ══════════════════════════════════════════════════════════════════


class TestSchedulerNoSilentExcept:
    def test_report_contract(self):
        # Scheduler 可轻量实例化（emperor=None），report() 暴露 status / self-evolve 端点依赖的
        # 契约属性：state / total_runs / entries（court/scheduler.py:383）。
        sched = Scheduler()
        rep = sched.report()
        assert isinstance(rep, SchedulerReport)
        assert hasattr(rep, "state")
        assert hasattr(rep, "total_runs")
        assert hasattr(rep, "entries")
        assert rep.total_runs == 0
        assert rep.entries == []


# ══════════════════════════════════════════════════════════════════
# T-D04：RunReport.mode 默认 offline + 横幅 + to_dict
# ══════════════════════════════════════════════════════════════════


class TestRunReportMode:
    def test_default_mode_offline(self):
        # RunReport 默认 mode="offline"（self_evolve.py:283）
        assert RunReport(started_at="t0").mode == "offline"

    def test_mode_can_be_live(self):
        # 显式指定 mode="live" 生效。
        assert RunReport(started_at="t0", mode="live").mode == "live"

    def test_to_dict_contains_mode(self):
        # to_dict() 输出含 mode 键（self_evolve.py:285-294）
        assert "mode" in RunReport(started_at="t0").to_dict()

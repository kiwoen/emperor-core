"""Shared test isolation and authentication helpers.

The application requires a real Bearer session. Older endpoint tests predate
that policy and use bare TestClient instances, so this fixture transparently
logs those clients in through the public login endpoint. Authentication
regression tests opt out and keep their unauthenticated assertions.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# Set these before test modules import the global FastAPI app.
os.environ.setdefault("HUANXIN_ADMIN_USER", "coverage-admin")
os.environ.setdefault("HUANXIN_ADMIN_PASS", "coverage-password")
os.environ.setdefault(
    "HUANXIN_DATA_DIR",
    str(Path(tempfile.mkdtemp(prefix="huanxin-pytest-"))),
)

_original_request = TestClient.request
_current_test = ""


def _auth_isolation_opted_out() -> bool:
    """Keep tests whose purpose is to assert unauthenticated behavior intact."""
    return any(
        marker in _current_test
        for marker in (
            "test_run_improvements_regression.py",
            "TestTokenAuth",
            "test_protected_route_requires_login",
            "test_invalid_token_rejected",
            "test_query_token_channel_removed",
            "test_protected_route_requires_bearer",
            "test_dashboard_blocked_without_bearer",
        )
    )


def _request_with_test_session(self, method, url, **kwargs):
    headers = dict(kwargs.get("headers") or {})
    if headers.get("Authorization") or _auth_isolation_opted_out():
        return _original_request(self, method, url, **kwargs)

    response = _original_request(self, method, url, **kwargs)
    if response.status_code != 401 or str(url).endswith("/api/auth/login"):
        return response

    # Create a real session in the same store as the application. This avoids
    # relying on a second TestClient request while the app is still initializing.
    from huanxin.api import auth_store
    user = auth_store.get_user_by_username(os.environ["HUANXIN_ADMIN_USER"])
    if user is None:
        auth_store.ensure_admin(
            os.environ["HUANXIN_ADMIN_USER"],
            os.environ["HUANXIN_ADMIN_PASS"],
        )
        user = auth_store.get_user_by_username(os.environ["HUANXIN_ADMIN_USER"])
    if user is None:
        return response

    token = auth_store.create_session(user["id"])
    headers["Authorization"] = f"Bearer {token}"
    kwargs["headers"] = headers
    return _original_request(self, method, url, **kwargs)


@pytest.fixture(autouse=True)
def _track_current_test(request, monkeypatch):
    global _current_test
    _current_test = f"{Path(str(request.node.fspath)).name}::{request.node.nodeid}"
    monkeypatch.setattr(TestClient, "request", _request_with_test_session)
    yield
    _current_test = ""

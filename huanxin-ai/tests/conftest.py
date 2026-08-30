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

    login = _original_request(
        self,
        "POST",
        "/api/auth/login",
        json={
            "username": os.environ["HUANXIN_ADMIN_USER"],
            "password": os.environ["HUANXIN_ADMIN_PASS"],
        },
    )
    if login.status_code != 200:
        return response

    headers["Authorization"] = f"Bearer {login.json()['token']}"
    kwargs["headers"] = headers
    return _original_request(self, method, url, **kwargs)


@pytest.fixture(autouse=True)
def _track_current_test(request, monkeypatch):
    global _current_test
    _current_test = f"{str(request.node.fspath)}::{request.node.nodeid}"
    monkeypatch.setattr(TestClient, "request", _request_with_test_session)
    yield
    _current_test = ""

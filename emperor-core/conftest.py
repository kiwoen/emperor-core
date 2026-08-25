"""Root conftest — make the test suite's auth context deterministic.

The Court API enforces *session-based* login on every non-whitelisted route
(see ``huanxin/api/token_guard.py``). The old "open by default when no token is
set" behaviour was removed, so the API tests must authenticate.

Two things must hold for the tests to log in reliably:

1. ``HUANXIN_ADMIN_PASS`` is fixed to a known value, so the admin account that
   ``create_app`` seeds at startup is loggable.
2. ``HUANXIN_DATA_DIR`` points at an isolated directory created *before*
   ``huanxin.court_api`` is first imported. ``huanxin.api.auth_store`` caches a
   single module-level SQLite connection, so the data dir MUST be decided before
   that first import or the cached connection silently points elsewhere and
   login fails.

The data dir lives under the project root on the **E:** drive (no C: storage).
The login itself is performed inline in each test fixture (the helper is not
imported from here, since ``tests/`` has no ``__init__.py`` and pytest would not
resolve ``import conftest``).
"""
from __future__ import annotations

import os

TEST_ADMIN_USER = "admin"
TEST_ADMIN_PASS = "test-admin-password-2026"

# Isolated auth DB under the project root (E: drive), created before import.
_TEST_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".testdata")
os.makedirs(_TEST_DATA_DIR, exist_ok=True)

os.environ["HUANXIN_ADMIN_USER"] = TEST_ADMIN_USER
os.environ["HUANXIN_ADMIN_PASS"] = TEST_ADMIN_PASS
os.environ["HUANXIN_DATA_DIR"] = _TEST_DATA_DIR

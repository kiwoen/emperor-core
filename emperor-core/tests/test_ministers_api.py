"""Tests for Minister management API endpoints under /api/ministers."""

import pytest
from fastapi.testclient import TestClient

from jarvis.court_api import create_app
from jarvis.emperor import Emperor


# ══════════════════════════════════════════════════════════════════
# Fixture
# ══════════════════════════════════════════════════════════════════


@pytest.fixture
def client():
    emperor = Emperor()
    emperor.register("turing", domain="math")
    emperor.register("ada", domain="code")
    # Pass emperor's internal court so API and Emperor share the same Court
    app = create_app(court=emperor.court)
    app.extra["emperor"] = emperor
    return TestClient(app)


# ══════════════════════════════════════════════════════════════════
# GET /api/ministers
# ══════════════════════════════════════════════════════════════════


class TestListMinisters:
    def test_list_ministers_returns_list(self, client):
        """GET /api/ministers returns ministers list."""
        res = client.get("/api/ministers")
        assert res.status_code == 200
        data = res.json()
        assert "ministers" in data
        assert isinstance(data["ministers"], list)
        names = [m["name"] for m in data["ministers"]]
        assert "turing" in names
        assert "ada" in names

    def test_minister_has_required_fields(self, client):
        """Each minister dict contains name, domain, merit, stability."""
        res = client.get("/api/ministers")
        assert res.status_code == 200
        for m in res.json()["ministers"]:
            assert "name" in m
            assert "domain" in m
            assert "merit" in m
            assert "stability" in m

    def test_list_minister_domain_values(self, client):
        """Ministers have their registered domains."""
        res = client.get("/api/ministers")
        m_by_name = {m["name"]: m["domain"] for m in res.json()["ministers"]}
        assert m_by_name.get("turing") == "math"
        assert m_by_name.get("ada") == "code"


# ══════════════════════════════════════════════════════════════════
# POST /api/ministers
# ══════════════════════════════════════════════════════════════════


class TestCreateMinister:
    def test_create_new_minister(self, client):
        """POST /api/ministers creates a minister successfully."""
        res = client.post("/api/ministers", json={
            "name": "shannon",
            "domain": "science",
        })
        assert res.status_code == 200
        data = res.json()
        assert data["message"] == "大臣 shannon 已创建"
        assert data["minister"]["name"] == "shannon"
        assert data["minister"]["domain"] == "science"

    def test_create_duplicate_returns_400(self, client):
        """POST /api/ministers with existing name returns 400."""
        # "turing" already exists from fixture
        res = client.post("/api/ministers", json={
            "name": "turing",
            "domain": "general",
        })
        assert res.status_code == 400
        assert "已存在" in res.json()["detail"]

    def test_create_empty_name_returns_400(self, client):
        """POST /api/ministers with empty string name returns 400."""
        res = client.post("/api/ministers", json={
            "name": "   ",
            "domain": "general",
        })
        assert res.status_code == 400
        assert "不能为空" in res.json()["detail"]

    def test_create_invalid_domain_returns_400(self, client):
        """POST /api/ministers with invalid domain returns 400."""
        res = client.post("/api/ministers", json={
            "name": "newton",
            "domain": "physics",
        })
        assert res.status_code == 400
        assert "无效领域" in res.json()["detail"]

    def test_create_defaults_domain(self, client):
        """POST /api/ministers without domain defaults to general."""
        res = client.post("/api/ministers", json={"name": "test_default"})
        assert res.status_code == 200
        assert res.json()["minister"]["domain"] == "general"

    def test_created_minister_visible_in_list(self, client):
        """A minister created via POST appears in GET list."""
        client.post("/api/ministers", json={
            "name": "von_neumann",
            "domain": "math",
        })
        res = client.get("/api/ministers")
        names = [m["name"] for m in res.json()["ministers"]]
        assert "von_neumann" in names


# ══════════════════════════════════════════════════════════════════
# PUT /api/ministers/{name}
# ══════════════════════════════════════════════════════════════════


class TestUpdateMinister:
    def test_update_domain(self, client):
        """PUT /api/ministers/{name} updates domain."""
        res = client.put("/api/ministers/turing", json={"domain": "code"})
        assert res.status_code == 200
        assert res.json()["minister"]["domain"] == "code"

    def test_update_merit(self, client):
        """PUT /api/ministers/{name} updates merit."""
        res = client.put("/api/ministers/turing", json={"merit": 85})
        assert res.status_code == 200
        assert res.json()["minister"]["merit"] == 85.0

    def test_update_stability(self, client):
        """PUT /api/ministers/{name} updates stability."""
        res = client.put("/api/ministers/turing", json={"stability": 0.92})
        assert res.status_code == 200
        assert res.json()["minister"]["stability"] == 0.92

    def test_update_nonexistent_returns_404(self, client):
        """PUT /api/ministers/{name} for unknown name returns 404."""
        res = client.put("/api/ministers/nobody", json={"domain": "math"})
        assert res.status_code == 404

    def test_update_invalid_domain_returns_400(self, client):
        """PUT /api/ministers/{name} with bad domain returns 400."""
        res = client.put("/api/ministers/turing", json={"domain": "astrology"})
        assert res.status_code == 400

    def test_update_multiple_fields(self, client):
        """PUT /api/ministers/{name} updates domain and merit together."""
        res = client.put("/api/ministers/ada", json={
            "domain": "data",
            "merit": 72,
            "stability": 0.88,
        })
        assert res.status_code == 200
        m = res.json()["minister"]
        assert m["domain"] == "data"
        assert m["merit"] == 72.0
        assert m["stability"] == 0.88


# ══════════════════════════════════════════════════════════════════
# DELETE /api/ministers/{name}
# ══════════════════════════════════════════════════════════════════


class TestDeleteMinister:
    def test_delete_existing_minister(self, client):
        """DELETE /api/ministers/{name} removes the minister."""
        res = client.delete("/api/ministers/turing")
        assert res.status_code == 200
        assert "已删除" in res.json()["message"]

    def test_delete_nonexistent_returns_404(self, client):
        """DELETE /api/ministers/{name} for unknown name returns 404."""
        res = client.delete("/api/ministers/nobody")
        assert res.status_code == 404

    def test_deleted_minister_not_in_list(self, client):
        """After deletion, the minister no longer appears in GET list."""
        client.delete("/api/ministers/ada")
        res = client.get("/api/ministers")
        names = [m["name"] for m in res.json()["ministers"]]
        assert "ada" not in names

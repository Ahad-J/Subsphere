"""Core health and metadata tests for SubSphere 4.0."""
from fastapi.testclient import TestClient

from app.db.engine import init_schema
from app.main import app

client = TestClient(app)


def test_live():
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_metadata_reports_implemented_modules():
    response = client.get("/api/v1/meta")
    assert response.status_code == 200
    data = response.json()
    assert data["product"] == "SubSphere"
    assert "health/ready" in data["implemented"]


def test_ready_without_database_fails(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    response = client.get("/health/ready")
    assert response.status_code == 503


def test_ready_with_reachable_database(monkeypatch, tmp_path):
    db_file = tmp_path / "ready_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    init_schema()
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "reachable"}

"""Shared test fixtures for SubSphere unit and integration suites."""
from __future__ import annotations

from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient

from app.auth.service import reset_rate_limits
from app.core.clock import Clock, set_clock
from app.db.engine import init_schema
from app.main import app
from app.notifications.router import reset_notification_rate_limits


@pytest.fixture
def isolated_db(monkeypatch, tmp_path):
    db_path = tmp_path / "subsphere_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("APP_SECRET", "test-secret-key-for-subsphere-4-ci")
    monkeypatch.setenv("SUBSPHERE_DATA_DIR", str(tmp_path / "data"))
    reset_rate_limits()
    reset_notification_rate_limits()
    set_clock(Clock(lambda: datetime(2026, 11, 12, 9, 0, 0, tzinfo=timezone.utc)))
    init_schema()
    return db_path


@pytest.fixture
def client(isolated_db):
    return TestClient(app)

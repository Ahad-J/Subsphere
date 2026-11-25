"""M0 Integration Tests: Authentication, Tenancy, Invitations, and Migrations (T001-T007, T078)."""
from __future__ import annotations

import alembic
from fastapi.testclient import TestClient
from app.main import app


def test_t001_register_free_personal_account_and_verify(client: TestClient):
    res = client.post(
        "/api/v1/auth/register",
        json={"email": "alice@example.com", "password": "StrongPassword123!", "full_name": "Alice"},
    )
    assert res.status_code == 201
    data = res.json()
    assert data["user"]["email"] == "alice@example.com"
    assert data["personal_account"]["tier"] == "personal_free"
    assert data["personal_account"]["payment_required"] is False
    token = data["verification_token"]

    vres = client.post("/api/v1/auth/verify-email", json={"token": token})
    assert vres.status_code == 200
    assert vres.json()["verified"] is True

    # Reusing verification token fails with 409
    vres2 = client.post("/api/v1/auth/verify-email", json={"token": token})
    assert vres2.status_code == 409


def test_t002_register_duplicate_email_returns_409(client: TestClient):
    payload = {"email": "dup@example.com", "password": "StrongPassword123!"}
    r1 = client.post("/api/v1/auth/register", json=payload)
    assert r1.status_code == 201
    r2 = client.post("/api/v1/auth/register", json=payload)
    assert r2.status_code == 409
    assert r2.json()["code"] == "EMAIL_EXISTS"


def test_t003_login_wrong_password_and_rate_limit(client: TestClient):
    client.post(
        "/api/v1/auth/register",
        json={"email": "bob@example.com", "password": "CorrectPassword123!"},
    )
    for _ in range(5):
        res = client.post(
            "/api/v1/auth/login",
            json={"email": "bob@example.com", "password": "WrongPassword"},
        )
        assert res.status_code == 401
        assert "access_token" not in res.json()

    # 6th attempt rate limited (429)
    res_rl = client.post(
        "/api/v1/auth/login",
        json={"email": "bob@example.com", "password": "WrongPassword"},
    )
    assert res_rl.status_code == 429
    assert res_rl.json()["code"] == "RATE_LIMITED"


def test_t004_unauthenticated_access_rejected(client: TestClient):
    res = client.get("/api/v1/me")
    assert res.status_code == 401


def test_t005_t006_t007_org_isolation_and_invite_flow(isolated_db):
    c_owner = TestClient(app)
    c_other = TestClient(app)
    c_emp = TestClient(app)

    c_owner.post("/api/v1/auth/register", json={"email": "owner@org.com", "password": "Password123!"})
    c_owner.post("/api/v1/auth/login", json={"email": "owner@org.com", "password": "Password123!"})

    c_other.post("/api/v1/auth/register", json={"email": "outsider@org.com", "password": "Password123!"})
    c_other.post("/api/v1/auth/login", json={"email": "outsider@org.com", "password": "Password123!"})

    c_emp.post("/api/v1/auth/register", json={"email": "emp@org.com", "password": "Password123!"})
    c_emp.post("/api/v1/auth/login", json={"email": "emp@org.com", "password": "Password123!"})

    org_res = c_owner.post(
        "/api/v1/organizations",
        json={"name": "FitZone Gym", "slug": "fitzone-gym", "default_currency": "PKR"},
    )
    assert org_res.status_code == 201
    org_id = org_res.json()["id"]

    # T005: Unrelated user cannot view members or switch context
    assert c_other.get(f"/api/v1/organizations/{org_id}/members").status_code == 403
    assert c_other.post(f"/api/v1/organizations/{org_id}/switch-context").status_code == 403

    # T006: Invite employee creates pending invite, not active membership yet
    inv_res = c_owner.post(
        f"/api/v1/organizations/{org_id}/invites",
        json={"email": "emp@org.com", "role": "staff"},
    )
    assert inv_res.status_code == 201
    assert inv_res.json()["status"] == "pending"
    invite_token = inv_res.json()["invite_token"]
    assert len(c_owner.get(f"/api/v1/organizations/{org_id}/members").json()["members"]) == 1

    # T007: Accept invite once succeeds; reusing invite fails with 409
    acc1 = c_emp.post(f"/api/v1/organizations/invites/{invite_token}/accept")
    assert acc1.status_code == 200
    assert acc1.json()["role"] == "staff"
    assert len(c_owner.get(f"/api/v1/organizations/{org_id}/members").json()["members"]) == 2

    acc2 = c_emp.post(f"/api/v1/organizations/invites/{invite_token}/accept")
    assert acc2.status_code == 409

    # Context switch + refresh + logout
    sw = c_emp.post(f"/api/v1/organizations/{org_id}/switch-context")
    assert sw.status_code == 200
    assert sw.json()["active_org_id"] == org_id
    assert c_emp.get("/api/v1/me").json()["active_org_id"] == org_id

    ref = c_emp.post("/api/v1/auth/refresh")
    assert ref.status_code == 200
    assert c_emp.post("/api/v1/auth/logout").status_code == 200
    assert c_emp.get("/api/v1/me").status_code == 401


def test_alembic_upgrade_downgrade_cycle(monkeypatch, tmp_path):
    db_file = tmp_path / "mig_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    alembic.upgrade("head")
    assert TestClient(app).get("/api/v1/health/ready").status_code == 200
    alembic.downgrade("base")
    alembic.upgrade("head")
    assert TestClient(app).get("/api/v1/health/ready").status_code == 200

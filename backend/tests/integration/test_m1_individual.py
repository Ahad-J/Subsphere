"""M1 Integration Tests: Individual Workspace, Currency Separation, Activity Privacy, Reminders."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.db.engine import get_db
from app.main import app


def _register_and_login(c: TestClient, email: str) -> dict:
    c.post("/api/v1/auth/register", json={"email": email, "password": "Password123!"})
    res = c.post("/api/v1/auth/login", json={"email": email, "password": "Password123!"})
    return res.json()


def test_t008_t010_t012_personal_subscriptions_and_calendar(client: TestClient):
    _register_and_login(client, "ind_a@example.com")

    # T012: negative price, missing date, invalid interval -> 422
    assert (
        client.post(
            "/api/v1/personal/subscriptions",
            json={"service_name": "Bad", "price_minor": -100, "billing_interval": "monthly", "next_renewal_date": "2026-12-12"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/personal/subscriptions",
            json={"service_name": "BadDate", "price_minor": 100, "billing_interval": "monthly"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/personal/subscriptions",
            json={"service_name": "BadInt", "price_minor": 100, "billing_interval": "invalid_interval", "next_renewal_date": "2026-12-12"},
        ).status_code
        == 422
    )

    # T008 & Output O1: AI Assistant $20/month + Game Pass $12/month -> $32/month
    r1 = client.post(
        "/api/v1/personal/subscriptions",
        json={
            "service_name": "AI Assistant",
            "category": "AI",
            "plan_name": "Pro",
            "price_minor": 2000,
            "currency": "USD",
            "billing_interval": "monthly",
            "next_renewal_date": "2026-12-05",
        },
    )
    assert r1.status_code == 201
    assert r1.json()["monthly_equivalent_minor"] == 2000

    r2 = client.post(
        "/api/v1/personal/subscriptions",
        json={
            "service_name": "Game Pass",
            "category": "Gaming",
            "price_minor": 1200,
            "currency": "USD",
            "billing_interval": "monthly",
            "next_renewal_date": "2026-12-01",
        },
    )
    assert r2.status_code == 201

    # T010 & T011: Add PKR subscription and one-time $60 game purchase
    assert (
        client.post(
            "/api/v1/personal/subscriptions",
            json={
                "service_name": "Gym Local",
                "price_minor": 300000,
                "currency": "PKR",
                "billing_interval": "monthly",
                "next_renewal_date": "2026-12-10",
            },
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/v1/personal/subscriptions",
            json={
                "service_name": "Elden Ring",
                "product_type": "one_time_purchase",
                "price_minor": 6000,
                "currency": "USD",
                "billing_interval": "one_time",
                "next_renewal_date": "2026-12-11",
            },
        ).status_code
        == 201
    )

    dash = client.get("/api/v1/personal/dashboard").json()
    cur_map = {c["currency"]: c for c in dash["currencies"]}
    assert cur_map["USD"]["monthly_equivalent_minor"] == 3200
    assert cur_map["USD"]["one_time_spend_minor"] == 6000
    assert cur_map["PKR"]["monthly_equivalent_minor"] == 300000
    assert dash["active_recurring_subscriptions"] == 3
    assert dash["due_in_next_30_days"] == 4
    assert dash["token_count"] is None
    assert dash["token_count_quality"] == "unavailable"

    cal = client.get("/api/v1/personal/calendar?month=2026-12").json()
    assert len(cal["events"]) == 4


def test_t016_t017_t018_t019_t020_t071_t075_privacy_and_activity(isolated_db):
    c_a = TestClient(app)
    c_b = TestClient(app)
    a_login = _register_and_login(c_a, "personal_a@example.com")
    _register_and_login(c_b, "personal_b@example.com")

    # T017: Connect browser extension allowlisting only exampleai.com
    assert (
        c_a.post(
            "/api/v1/personal/monitoring/connect",
            json={"source_type": "browser_extension", "allowlist": ["exampleai.com"]},
        ).status_code
        == 200
    )
    # Non-allowlisted domain rejected
    assert (
        c_a.post(
            "/api/v1/personal/activity/aggregate",
            json={"source": "ALLOWED_BROWSER_ACTIVITY", "domain": "privatebank.com", "active_seconds": 300},
        ).status_code
        == 403
    )

    # T071: Payload with prompts, cookies, or full URLs rejected
    assert (
        c_a.post(
            "/api/v1/personal/activity/aggregate",
            json={"source": "ALLOWED_BROWSER_ACTIVITY", "domain": "exampleai.com", "active_seconds": 60, "prompt": "secret prompt"},
        ).status_code
        == 422
    )
    assert (
        c_a.post(
            "/api/v1/personal/activity/aggregate",
            json={"source": "ALLOWED_BROWSER_ACTIVITY", "domain": "https://exampleai.com/chat?id=123", "active_seconds": 60},
        ).status_code
        == 422
    )

    # T019: Valid 40 min observed foreground usage -> active_seconds=2400, tokens=None, label OBSERVED
    ok = c_a.post(
        "/api/v1/personal/activity/aggregate",
        json={"source": "ALLOWED_BROWSER_ACTIVITY", "domain": "exampleai.com", "observed_active_seconds": 2400},
    )
    assert ok.status_code == 201
    assert ok.json()["observed_active_seconds"] == 2400
    assert ok.json()["tokens"] is None
    assert ok.json()["reliability"] == "OBSERVED"

    # T018: Disable monitoring -> further activity rejected
    assert c_a.post("/api/v1/personal/monitoring/revoke", json={"source_type": "browser_extension"}).status_code == 200
    assert (
        c_a.post(
            "/api/v1/personal/activity/aggregate",
            json={"source": "ALLOWED_BROWSER_ACTIVITY", "domain": "exampleai.com", "observed_active_seconds": 120},
        ).status_code
        == 403
    )

    # T016: Personal B cannot access Personal A activity
    assert c_b.get(f"/api/v1/personal/usage?account_id={a_login['personal_account_id']}").status_code == 403

    # T020: Unsupported provider returns unavailable (null), never zero
    unsup = c_a.get("/api/v1/personal/usage?provider=unsupported_desktop").json()
    assert unsup["tokens"] is None
    assert unsup["token_count_quality"] == "unavailable"

    # T075: Delete personal history removes records cleanly
    assert c_a.delete("/api/v1/personal/history").status_code == 200
    after = c_a.get("/api/v1/personal/usage").json()
    assert after["observed_active_seconds"] == 0
    assert after["activity_records"] == []


def test_t057_t060_personal_renewal_reminder_idempotency(client: TestClient):
    _register_and_login(client, "rem@example.com")
    # Clock is 2026-11-12; create subscription renewing in 7 days (2026-11-19)
    client.post(
        "/api/v1/personal/subscriptions",
        json={
            "service_name": "Cursor",
            "plan_name": "Pro",
            "price_minor": 2000,
            "currency": "USD",
            "billing_interval": "monthly",
            "next_renewal_date": "2026-11-19",
            "reminder_offsets_days": [7, 3, 1],
        },
    )
    t1 = client.post("/api/v1/personal/reminders/tick").json()
    assert t1["queued_count"] == 1
    # T060: Duplicate scheduled tick creates zero duplicate logical jobs
    t2 = client.post("/api/v1/personal/reminders/tick").json()
    assert t2["queued_count"] == 0
    assert t2["skipped_duplicates"] == 1

    with get_db(write=False) as db:
        jobs = db.fetchall("SELECT * FROM notification_jobs WHERE logical_key LIKE 'personal_renewal:%'")
        assert len(jobs) == 1
        assert "SubSphere has not charged your account" in jobs[0]["body"]

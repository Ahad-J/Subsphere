"""M3 Integration Tests: Enterprise Seats, Concurrency, Hard Quotas, Idempotency, SDKs (T036-T046, T074, B-09)."""
from __future__ import annotations

import concurrent.futures
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

SDK_ROOT = Path(__file__).resolve().parents[3] / "sdk" / "python"
if str(SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(SDK_ROOT))

from subsphere_sdk.client import SubSphereClient, SubSphereQuotaError


def _setup_user(email: str) -> TestClient:
    c = TestClient(app)
    c.post("/api/v1/auth/register", json={"email": email, "password": "Password123!", "full_name": email.split("@")[0]})
    c.post("/api/v1/auth/login", json={"email": email, "password": "Password123!"})
    return c


def test_t036_to_t039_enterprise_seats_and_concurrency(isolated_db):
    owner = _setup_user("tech_owner@example.com")
    outsider = _setup_user("outsider_emp@example.com")
    org = owner.post("/api/v1/organizations", json={"name": "TechNova Solutions", "slug": "technova", "default_currency": "USD"}).json()
    other_org = outsider.post("/api/v1/organizations", json={"name": "OtherOrg", "slug": "otherorg", "default_currency": "USD"}).json()
    other_mem_id = other_org["membership_id"]

    # Invite and add 11 employees to TechNova
    emp_memberships = []
    for i in range(11):
        emp_email = f"dev{i+1}@technova.io"
        emp_c = _setup_user(emp_email)
        inv = owner.post(f"/api/v1/organizations/{org['id']}/invites", json={"email": emp_email, "role": "member"}).json()
        acc = emp_c.post(f"/api/v1/organizations/invites/{inv['invite_token']}/accept").json()
        emp_memberships.append(acc["membership_id"])

    # T036: Purchase 10 seats at $12/month -> $120/month payable
    ext = owner.post(
        "/api/v1/enterprise/subscriptions",
        json={
            "organization_id": org["id"],
            "vendor_name": "Cursor",
            "service_name": "Cursor Business",
            "seats_purchased": 10,
            "unit_price_minor": 1200,
            "currency": "USD",
            "next_renewal_date": "2026-12-12",
        },
    )
    assert ext.status_code == 201
    ext_id = ext.json()["id"]
    assert ext.json()["total_payable_minor"] == 12000

    # T038: Assign member from different org -> 403
    assert (
        owner.post(f"/api/v1/enterprise/subscriptions/{ext_id}/seat-allocations", json={"membership_id": other_mem_id}).status_code
        == 403
    )

    # Assign 7 employees -> 7 assigned, 3 unassigned (Output O3)
    alloc_ids = []
    for mid in emp_memberships[:7]:
        r = owner.post(f"/api/v1/enterprise/subscriptions/{ext_id}/seat-allocations", json={"membership_id": mid})
        assert r.status_code == 201
        alloc_ids.append(r.json()["id"])

    listed = owner.get(f"/api/v1/enterprise/subscriptions?organization_id={org['id']}").json()["external_subscriptions"][0]
    assert listed["seats_purchased"] == 10
    assert listed["seats_assigned"] == 7
    assert listed["seats_unassigned"] == 3
    assert listed["total_payable_minor"] == 12000

    # T037: Concurrent seat assignments for remaining 4 candidates -> at most 3 succeed (total 10), 11th denied
    def _assign(mid: str) -> int:
        return owner.post(f"/api/v1/enterprise/subscriptions/{ext_id}/seat-allocations", json={"membership_id": mid}).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(_assign, emp_memberships[7:]))
    assert statuses.count(201) == 3
    assert statuses.count(409) == 1

    # T039: Revoke 1 seat -> capacity reopens cleanly
    assert owner.delete(f"/api/v1/enterprise/subscriptions/{ext_id}/seat-allocations/{alloc_ids[0]}").status_code == 200
    listed2 = owner.get(f"/api/v1/enterprise/subscriptions?organization_id={org['id']}").json()["external_subscriptions"][0]
    assert listed2["seats_assigned"] == 9
    assert listed2["seats_unassigned"] == 1


def test_t040_to_t046_t074_b09_atomic_metering_and_sdk(isolated_db):
    owner = _setup_user("ai_provider@example.com")
    org = owner.post("/api/v1/organizations", json={"name": "NovaAI", "slug": "nova-ai", "default_currency": "USD"}).json()

    svc_a = owner.post("/api/v1/services", json={"organization_id": org["id"], "name": "Nova API", "slug": "nova-api"}).json()
    svc_b = owner.post("/api/v1/services", json={"organization_id": org["id"], "name": "Other API", "slug": "other-api"}).json()

    owner.post(f"/api/v1/services/{svc_a['id']}/metrics", json={"key": "api_requests", "name": "API Requests", "enforcement": "hard_limit"})
    owner.post(f"/api/v1/services/{svc_a['id']}/metrics", json={"key": "checkins", "name": "Gym Check-ins", "enforcement": "monitor_only"})

    plan = owner.post(f"/api/v1/services/{svc_a['id']}/plans", json={"name": "Basic"}).json()
    pv = owner.post(
        f"/api/v1/plans/{plan['id']}/versions",
        json={
            "price_minor": 0,
            "currency": "USD",
            "billing_interval": "monthly",
            "metrics": [
                {"metric_key": "api_requests", "limit_value": 1000},
                {"metric_key": "checkins", "limit_value": 5},
            ],
        },
    ).json()

    sub = owner.post(
        "/api/v1/subscriptions",
        json={"plan_version_id": pv["id"], "starts_at": "2026-11-01T00:00:00Z"},
    ).json()
    sub_id = sub["id"]

    key_a = owner.post("/api/v1/api-keys", json={"organization_id": org["id"], "service_id": svc_a["id"], "name": "SDK Key A"}).json()["api_key"]
    key_b = owner.post("/api/v1/api-keys", json={"organization_id": org["id"], "service_id": svc_b["id"], "name": "SDK Key B"}).json()["api_key"]

    sdk_a = SubSphereClient(api_key=key_a, http_client=owner)

    # T046 & T074: Service B key cannot submit events for Service A subscription
    wrong_key = owner.post(
        "/api/v1/usage/consume",
        headers={"X-API-Key": key_b},
        json={"external_event_id": "bad-svc", "subscription_id": sub_id, "metric_key": "api_requests", "quantity": 1},
    )
    assert wrong_key.status_code == 403

    # Pre-consume 994 units -> 6 remaining
    sdk_a.consume_usage(subscription_id=sub_id, metric_key="api_requests", quantity=994, external_event_id="evt-bulk-994")

    # B-09 & T044: 50 concurrent qty=1 requests with 6 remaining -> wait, let's consume 1 more so 5 remain
    sdk_a.consume_usage(subscription_id=sub_id, metric_key="api_requests", quantity=1, external_event_id="evt-995")

    def _fire(i: int) -> int:
        res = owner.post(
            "/api/v1/usage/consume",
            headers={"X-API-Key": key_a},
            json={"external_event_id": f"race-{i}", "subscription_id": sub_id, "metric_key": "api_requests", "quantity": 1},
        )
        return res.status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        codes = list(pool.map(_fire, range(50)))
    assert codes.count(201) == 5
    assert codes.count(409) == 45

    # Verify counter at exactly 1000
    adv = owner.get(f"/api/v1/entitlements/check?subscription_id={sub_id}&metric_key=api_requests").json()
    assert adv["used"] == 1000
    assert adv["remaining"] == 0

    # T041: 1001st rejected with QUOTA_EXCEEDED
    try:
        sdk_a.consume_usage(subscription_id=sub_id, metric_key="api_requests", quantity=1, external_event_id="evt-1001")
        assert False, "Expected SubSphereQuotaError"
    except SubSphereQuotaError as exc:
        assert exc.status_code == 409
        assert exc.code == "QUOTA_EXCEEDED"

    # T042: Retry exact accepted ID 3x -> 200 duplicate=true, counter remains 1000
    for _ in range(3):
        rep = owner.post(
            "/api/v1/usage/consume",
            headers={"X-API-Key": key_a},
            json={"external_event_id": "evt-995", "subscription_id": sub_id, "metric_key": "api_requests", "quantity": 1},
        )
        assert rep.status_code == 200
        assert rep.json()["duplicate"] is True
        assert rep.json()["used"] == 1000

    # T043: Conflicting quantity for same event ID -> 409 EVENT_ID_CONFLICT
    conf = owner.post(
        "/api/v1/usage/consume",
        headers={"X-API-Key": key_a},
        json={"external_event_id": "evt-995", "subscription_id": sub_id, "metric_key": "api_requests", "quantity": 5},
    )
    assert conf.status_code == 409
    assert conf.json()["code"] == "EVENT_ID_CONFLICT"

    # T045: Monitor-only metric allows overuse + queues alert, no extra invoice
    m_over = sdk_a.consume_usage(subscription_id=sub_id, metric_key="checkins", quantity=8, external_event_id="chk-1")
    assert m_over["accepted"] is True
    assert m_over["used"] == 8

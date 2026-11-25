"""Milestone M6 integration tests: Outbox, OpenMeter CloudEvents, Medallion, Freshness & O10 (T065-T069, T076)."""
from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from app.core.clock import Clock, get_clock, set_clock


def _setup_metered_org(client: TestClient):
    reg = client.post(
        "/api/v1/auth/register",
        json={"email": "owner.m6@acme.io", "password": "StrongPassword123!", "full_name": "M6 Owner"},
    )
    assert reg.status_code == 201
    client.post("/api/v1/auth/verify-email", json={"token": reg.json()["verification_token"]})
    tok = client.post(
        "/api/v1/auth/login",
        json={"email": "owner.m6@acme.io", "password": "StrongPassword123!"},
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {tok}"}

    org = client.post(
        "/api/v1/organizations",
        headers=headers,
        json={"name": "DataPlatform Co", "slug": "dataplatform-co", "default_currency": "USD"},
    ).json()
    org_id = org["id"]

    svc = client.post(
        "/api/v1/services",
        headers=headers,
        json={"organization_id": org_id, "name": "VectorDB API", "slug": "vectordb-api"},
    )
    assert svc.status_code == 201, svc.text
    svc_id = svc.json()["id"]

    metric = client.post(
        f"/api/v1/services/{svc_id}/metrics",
        headers=headers,
        json={
            "key": "queries",
            "name": "Vector Queries",
            "unit": "query",
            "enforcement": "hard_limit",
        },
    )
    assert metric.status_code == 201, metric.text

    plan = client.post(
        f"/api/v1/services/{svc_id}/plans",
        headers=headers,
        json={"name": "Pro", "slug": "pro", "description": "Pro metered plan"},
    )
    assert plan.status_code == 201, plan.text
    plan_id = plan.json()["id"]

    pv = client.post(
        f"/api/v1/plans/{plan_id}/versions",
        headers=headers,
        json={
            "price_minor": 4900,
            "currency": "USD",
            "billing_interval": "monthly",
            "metrics": [{"metric_key": "queries", "limit_value": 100}],
        },
    )
    assert pv.status_code == 201, pv.text
    pv_id = pv.json()["id"]

    contact = client.post(
        "/api/v1/subscribers",
        headers=headers,
        json={"organization_id": org_id, "name": "Buyer Corp", "email": "buyer@corp.io"},
    )
    assert contact.status_code == 201, contact.text

    sub_resp = client.post(
        "/api/v1/subscriptions",
        headers=headers,
        json={
            "subscriber_contact_id": contact.json()["id"],
            "plan_version_id": pv_id,
            "quantity": 1,
            "starts_at": "2026-11-01T00:00:00Z",
        },
    )
    assert sub_resp.status_code == 201, sub_resp.text
    sub_data = sub_resp.json()
    sub_id = sub_data["id"]
    inv_id = sub_data["invoice_id"]

    pay_resp = client.post(
        f"/api/v1/invoices/{inv_id}/payments",
        headers=headers,
        json={"amount_minor": 4900, "currency": "USD", "method": "bank_transfer", "idempotency_key": "pay-m6-1"},
    )
    assert pay_resp.status_code == 201, pay_resp.text

    api_key = client.post(
        "/api/v1/api-keys",
        headers=headers,
        json={"organization_id": org_id, "service_id": svc_id, "name": "prod-ingest"},
    )
    assert api_key.status_code == 201, api_key.text

    return headers, org_id, svc_id, sub_id, api_key.json()["api_key"], metric.json()["id"]


def test_t065_to_t069_t076_outbox_cloudevents_medallion_and_reconciliation(client: TestClient):
    headers, org_id, svc_id, sub_id, api_key, _ = _setup_metered_org(client)
    sdk_headers = {"X-API-Key": api_key}

    # Accept two usage events (60 + 40 = 100 limit)
    e1 = client.post(
        "/api/v1/usage/consume",
        headers=sdk_headers,
        json={
            "subscription_id": sub_id,
            "metric_key": "queries",
            "quantity": 60,
            "external_event_id": "evt-m6-1",
            "source": "APP_SDK",
        },
    )
    assert e1.status_code == 201, e1.text
    e2 = client.post(
        "/api/v1/usage/consume",
        headers=sdk_headers,
        json={
            "subscription_id": sub_id,
            "metric_key": "queries",
            "quantity": 40,
            "external_event_id": "evt-m6-2",
            "source": "APP_SDK",
        },
    )
    assert e2.status_code == 201, e2.text

    # T065/T066: Simulate OpenMeter down while Kafka succeeds -> Postgres hard limit STILL enforced!
    disp_partial = client.post(
        "/api/v1/analytics/outbox/dispatch",
        headers=headers,
        json={"simulate_failures": ["openmeter"]},
    )
    assert disp_partial.status_code == 200
    d1 = disp_partial.json()
    assert d1["delivered"]["kafka"] == 3  # 1 payment.confirmed + 2 usage.accepted
    assert d1["failed"]["openmeter"] == 2

    # Even with OpenMeter down, Postgres enforces quota strictly (T065)
    e_over = client.post(
        "/api/v1/usage/consume",
        headers=sdk_headers,
        json={
            "subscription_id": sub_id,
            "metric_key": "queries",
            "quantity": 1,
            "external_event_id": "evt-m6-over",
            "source": "APP_SDK",
        },
    )
    assert e_over.status_code == 409
    assert e_over.json()["code"] == "QUOTA_EXCEEDED"

    # Freshness shows stale because outbox deliveries for openmeter are failed & medallion not yet run
    fresh1 = client.get("/api/v1/analytics/freshness", headers=headers).json()
    assert fresh1["is_stale"] is True
    assert fresh1["pending_or_failed_deliveries"] == 2

    # Recover OpenMeter: re-dispatch delivers only failed OpenMeter rows (Kafka already delivered)
    disp_rec = client.post(
        "/api/v1/analytics/outbox/dispatch",
        headers=headers,
        json={},
    )
    assert disp_rec.status_code == 200
    d2 = disp_rec.json()
    assert d2["delivered"]["openmeter"] == 2
    assert d2["delivered"]["kafka"] == 0

    # T067/T068: Verify CloudEvents 1.0 schema
    ce_list = client.get("/api/v1/analytics/cloudevents", headers=headers).json()["cloudevents"]
    assert len(ce_list) == 2
    for ce in ce_list:
        assert ce["specversion"] == "1.0"
        assert ce["type"] == "subsphere.usage.v1"
        assert ce["delivery_status"] == "delivered"
        assert ce["data"]["metric_key"] == "queries"

    # T069: Run Medallion (also test malformed quarantine + checkpoint replay idempotency)
    med1 = client.post(
        "/api/v1/analytics/medallion/run",
        headers=headers,
        json={"organization_id": org_id},
    )
    assert med1.status_code == 200
    m1 = med1.json()
    assert m1["bronze_count"] == 2
    assert m1["silver_count"] == 2
    assert m1["gold_groups"] == 1

    # Replay Bronze through checkpoint + verify 100% deduplication & malformed isolation
    med_malformed = client.post(
        "/api/v1/analytics/medallion/run",
        headers=headers,
        json={
            "organization_id": org_id,
            "replay_from_bronze": True,
            "additional_raw_events": [
                {
                    "id": "bad-evt-negative",
                    "organization_id": org_id,
                    "service_id": svc_id,
                    "metric_key": "queries",
                    "quantity": -5,
                    "occurred_at": "2026-11-12T09:00:00Z",
                }
            ],
        },
    ).json()
    assert med_malformed["silver_count"] == 2
    assert med_malformed["duplicates_removed"] == 2
    assert med_malformed["rejected_count"] == 1

    # Output O10: PostgreSQL Ledger vs OpenMeter vs Spark Gold reconciliation = zero discrepancy
    rec = client.get(f"/api/v1/analytics/reconciliation?organization_id={org_id}", headers=headers).json()
    assert rec["postgres_ledger"] == {"accepted_events": 2, "total_quantity": 100}
    assert rec["openmeter"] == {"distinct_cloudevents": 2, "total_quantity": 100}
    assert rec["spark_gold"] == {"distinct_events": 2, "total_quantity": 100}
    assert rec["reconciled_zero_discrepancy"] is True

    # Gold aggregates materialized in PostgreSQL
    gold = client.get(f"/api/v1/analytics/gold/usage?organization_id={org_id}", headers=headers).json()
    assert len(gold["daily_usage"]) == 1
    assert gold["daily_usage"][0]["total_quantity"] == 100
    assert len(gold["daily_finance"]) >= 1

    # T076: Fresh right now, stale after 30 minutes
    fresh2 = client.get("/api/v1/analytics/freshness?stale_threshold_seconds=900", headers=headers).json()
    assert fresh2["is_stale"] is False

    base_dt = get_clock().now()
    set_clock(Clock(lambda: base_dt + timedelta(minutes=30)))
    fresh_late = client.get("/api/v1/analytics/freshness?stale_threshold_seconds=900", headers=headers).json()
    assert fresh_late["is_stale"] is True
    assert "Stale" in fresh_late["banner"]

"""Milestone M5 integration tests: LiteLLM gateway, Decimal pricing, source qualification (T047-T051, O5)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient

from app.db.engine import get_db


def _register_and_login(client: TestClient, email: str = "maya.ai@example.com") -> tuple[str, dict[str, str]]:
    reg = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "StrongPassword123!", "full_name": "Maya AI"},
    )
    assert reg.status_code == 201, reg.text
    verify_tok = reg.json()["verification_token"]
    ver = client.post("/api/v1/auth/verify-email", json={"token": verify_tok})
    assert ver.status_code == 200
    login = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "StrongPassword123!"},
    )
    assert login.status_code == 200
    tok = login.json()["access_token"]
    return reg.json()["user"]["id"], {"Authorization": f"Bearer {tok}"}


def test_t047_t048_t049_t050_t051_litellm_pricing_and_cost_separation(client: TestClient):
    _, headers = _register_and_login(client)

    # Create $20.00 personal ChatGPT Plus subscription
    sub_resp = client.post(
        "/api/v1/personal/subscriptions",
        headers=headers,
        json={
            "service_name": "ChatGPT Plus",
            "category": "AI",
            "plan_name": "Plus",
            "price_minor": 2000,
            "currency": "USD",
            "billing_interval": "monthly",
            "next_renewal_date": "2026-12-01",
        },
    )
    assert sub_resp.status_code == 201, sub_resp.text

    # T047: Connect LiteLLM with capability matrix + credential masking + SSRF protection
    bad_ssrf = client.post(
        "/api/v1/connections",
        headers=headers,
        json={
            "provider_code": "litellm",
            "display_name": "Local Metadata SSRF",
            "endpoint_url": "http://169.254.169.254/latest/meta-data",
            "credential": "sk-litellm-secret-9876",
        },
    )
    assert bad_ssrf.status_code == 422
    assert bad_ssrf.json()["code"] == "UNSAFE_ENDPOINT_URL"

    conn_resp = client.post(
        "/api/v1/connections",
        headers=headers,
        json={
            "provider_code": "litellm",
            "display_name": "Maya LiteLLM Proxy",
            "credential": "sk-litellm-secret-9876",
        },
    )
    assert conn_resp.status_code == 200, conn_resp.text
    conn = conn_resp.json()
    assert "sk-litellm-secret-9876" not in conn["credential_masked"]
    assert conn["credential_masked"].endswith("***9876")
    assert conn["capabilities"]["supports_tokens"] is True
    assert conn["capabilities"]["requires_proxy_routing"] is True
    assert conn["capabilities"]["confidence_class"] == "GATEWAY_METERED"

    status_resp = client.get(f"/api/v1/connections/{conn['id']}/status", headers=headers)
    assert status_resp.status_code == 200
    assert status_resp.json()["capabilities"]["supports_requests"] is True

    # T049: Pricing version ($2/1M input, $8/1M output) with Decimal precision
    pv = client.post(
        "/api/v1/pricing/versions",
        headers=headers,
        json={
            "provider_code": "litellm",
            "model_name": "gpt-4o-mini",
            "input_per_million_usd": "2.00",
            "output_per_million_usd": "8.00",
            "effective_from": "2026-11-01T00:00:00Z",
        },
    )
    assert pv.status_code == 200, pv.text

    # T048: Replay same external_record_id `lit-1001` 3 times -> stored exactly once
    payload = {
        "connection_id": conn["id"],
        "records": [
            {
                "external_record_id": "lit-1001",
                "model_name": "gpt-4o-mini",
                "requests_count": 42,
                "input_tokens": 2000000,
                "output_tokens": 500000,
                "occurred_at": "2026-11-12T08:00:00Z",
            }
        ],
    }
    r1 = client.post("/api/v1/integrations/litellm/sync", headers=headers, json=payload)
    r2 = client.post("/api/v1/integrations/litellm/sync", headers=headers, json=payload)
    r3 = client.post(f"/api/v1/connections/{conn['id']}/sync", headers=headers, json=payload)
    assert r1.status_code == 200 and r1.json()["inserted_count"] == 1
    assert r2.status_code == 200 and r2.json()["inserted_count"] == 0 and r2.json()["duplicates_skipped"] == 1
    assert r3.status_code == 200 and r3.json()["inserted_count"] == 0 and r3.json()["duplicates_skipped"] == 1

    with get_db() as db:
        cnt = db.fetchone(
            "SELECT COUNT(*) AS cnt FROM external_usage_records WHERE external_record_id = 'lit-1001'"
        )["cnt"]
    assert cnt == 1

    # T049 & T050 & Output O5: Verify Decimal estimated token value ($8.00 = 800 minor) separated from $20 sub fee (2000) & null reported cost
    usage = client.get("/api/v1/ai/usage?service_name=ChatGPT", headers=headers)
    assert usage.status_code == 200, usage.text
    u = usage.json()
    assert u["record_count"] == 1
    assert u["cost_separation"]["subscription_fee_minor"] == 2000
    assert u["cost_separation"]["reported_extra_usage_minor"] is None
    assert u["cost_separation"]["estimated_token_value_minor"] == 800
    assert u["tokens"]["input_tokens"] == 2000000
    assert u["tokens"]["output_tokens"] == 500000
    assert u["tokens"]["total_tokens"] == 2500000

    # T051: Provider reports requests only (missing token categories) -> stays null, NEVER coerced to 0
    partial_conn = client.post(
        "/api/v1/connections",
        headers=headers,
        json={
            "provider_code": "csv_import",
            "display_name": "Requests-Only Provider Export",
        },
    ).json()
    sync_partial = client.post(
        f"/api/v1/connections/{partial_conn['id']}/sync",
        headers=headers,
        json={
            "records": [
                {
                    "external_record_id": "csv-req-only-1",
                    "model_name": "gpt-4o-mini",
                    "requests_count": 15,
                    "input_tokens": None,
                    "output_tokens": None,
                    "occurred_at": "2026-11-12T08:30:00Z",
                }
            ]
        },
    )
    assert sync_partial.status_code == 200

    partial_usage = client.get("/api/v1/ai/usage?source=CSV_IMPORT", headers=headers)
    assert partial_usage.status_code == 200
    pu = partial_usage.json()
    assert pu["tokens"]["input_tokens"] is None
    assert pu["tokens"]["output_tokens"] is None
    assert pu["tokens"]["total_tokens"] is None
    assert pu["tokens"]["requests_count"] == 15
    assert "input_tokens" in pu["tokens"]["unavailable_fields"]
    assert pu["cost_separation"]["estimated_token_value_minor"] is None
    assert "Unavailable" in pu["records"][0]["quality_note"] or "unavailable" in pu["records"][0]["quality_note"].lower()


def test_desktop_helper_privacy_and_org_identity_mapping(client: TestClient):
    _, headers = _register_and_login(client, "lead.eng@acme.com")

    # Test desktop helper module produces strict ALLOWED_DESKTOP_ACTIVITY payload without tokens
    helper_path = (
        Path(__file__).resolve().parents[3] / "services" / "desktop-helper" / "helper.py"
    )
    assert helper_path.exists()
    spec = importlib.util.spec_from_file_location("desktop_helper", helper_path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)

    payload = mod.build_activity_payload("Cursor.exe", 120)
    assert payload["domain_or_app"] == "cursor"
    assert payload["source"] == "ALLOWED_DESKTOP_ACTIVITY"
    assert "prompt" not in payload and "tokens" not in payload

    # Enable monitoring + post helper payload
    conn_mon = client.post(
        "/api/v1/personal/monitoring/connect",
        headers=headers,
        json={"source_type": "desktop_helper", "allowlist": ["cursor"]},
    )
    assert conn_mon.status_code == 200
    act = client.post("/api/v1/personal/activity/aggregate", headers=headers, json=payload)
    assert act.status_code == 201
    assert act.json()["tokens"] is None
    assert act.json()["reliability"] == "OBSERVED"

    # Org LiteLLM sync resolves employee identity mapping
    org = client.post(
        "/api/v1/organizations",
        headers=headers,
        json={"name": "Acme AI Lab", "slug": "acme-ai-lab"},
    ).json()
    with get_db(write=True) as db:
        m = db.fetchone(
            "SELECT id FROM memberships WHERE organization_id = %s",
            (org["id"],),
        )
        assert m is not None
        membership_id = m["id"]
    map_resp = client.post(
        "/api/v1/enterprise/identity-mappings",
        headers=headers,
        json={
            "organization_id": org["id"],
            "membership_id": membership_id,
            "provider_code": "litellm",
            "external_user_ref": "emp-alice-litellm",
        },
    )
    assert map_resp.status_code == 201, map_resp.text

    org_conn = client.post(
        "/api/v1/connections",
        headers=headers,
        json={
            "provider_code": "litellm",
            "display_name": "Acme Org LiteLLM",
            "organization_id": org["id"],
            "credential": "sk-org-gateway-1111",
        },
    ).json()
    sync_org = client.post(
        f"/api/v1/connections/{org_conn['id']}/sync",
        headers=headers,
        json={
            "records": [
                {
                    "external_record_id": "org-lit-1",
                    "external_user_ref": "emp-alice-litellm",
                    "model_name": "claude-3-5-sonnet",
                    "input_tokens": 100000,
                    "output_tokens": 25000,
                    "reported_cost_minor": 180,
                }
            ]
        },
    )
    assert sync_org.status_code == 200
    usage_org = client.get(f"/api/v1/ai/usage?organization_id={org['id']}", headers=headers)
    assert usage_org.status_code == 200
    assert usage_org.json()["records"][0]["membership_id"] == membership_id
    assert usage_org.json()["cost_separation"]["reported_extra_usage_minor"] == 180

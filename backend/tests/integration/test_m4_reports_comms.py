"""M4 Integration Tests: PDF Receipts, Snapshots, SSRF, SMTP Retry, Sender Domains, WhatsApp Consent (T052-T064, T070, T072, T073)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.core.clock import Clock, set_clock
from app.db.engine import get_db
from app.main import app
from app.notifications.dispatcher import dispatch_email_jobs
from app.notifications.whatsapp import send_whatsapp_template


def _setup_user(email: str) -> TestClient:
    c = TestClient(app)
    c.post("/api/v1/auth/register", json={"email": email, "password": "Password123!", "full_name": email.split("@")[0]})
    c.post("/api/v1/auth/login", json={"email": email, "password": "Password123!"})
    return c


def test_t052_to_t056_t070_t073_reports_pdf_security_and_persistence(isolated_db):
    owner = _setup_user("pdf_owner@example.com")
    outsider = _setup_user("pdf_outsider@example.com")
    org = owner.post("/api/v1/organizations", json={"name": "FitZone Gym", "slug": "fitzone-pdf", "default_currency": "PKR"}).json()
    svc = owner.post("/api/v1/services", json={"organization_id": org["id"], "name": "Gym", "slug": "gym", "currency": "PKR"}).json()
    plan = owner.post(f"/api/v1/services/{svc['id']}/plans", json={"name": "Basic"}).json()
    v1 = owner.post(f"/api/v1/plans/{plan['id']}/versions", json={"price_minor": 300000, "currency": "PKR", "billing_interval": "monthly"}).json()
    sc = owner.post("/api/v1/subscribers", json={"organization_id": org["id"], "name": "Ali Ahmed", "email": "ali@gym.pk"}).json()

    sub = owner.post("/api/v1/subscriptions", json={"plan_version_id": v1["id"], "subscriber_contact_id": sc["id"]}).json()
    inv_id = sub["invoice_id"]

    # T052: Unpaid invoice cannot generate payment_receipt
    deny = owner.post("/api/v1/reports", json={"report_type": "payment_receipt", "invoice_id": inv_id})
    assert deny.status_code == 409

    # Confirm payment -> creates receipt job
    pay = owner.post(
        f"/api/v1/invoices/{inv_id}/payments",
        headers={"Idempotency-Key": "pdf-pay-1"},
        json={"amount_minor": 300000, "currency": "PKR", "method": "cash", "external_reference": "counter-register-41"},
    ).json()
    rep_id = pay["receipt_report_id"]

    # T053: Render & download valid %PDF bytes
    dl = owner.get(f"/api/v1/reports/{rep_id}/download")
    assert dl.status_code == 200
    assert dl.content.startswith(b"%PDF-")
    assert dl.headers.get("content-type") == "application/pdf"

    # T054: Revise plan price to PKR 4,500 -> re-rendering historical receipt keeps original PKR 3,000 snapshot
    owner.patch(f"/api/v1/plans/{plan['id']}/versions/{v1['id']}", json={"price_minor": 450000})
    rep_meta = owner.get(f"/api/v1/reports/{rep_id}").json()
    assert '"total_minor": 300000' in rep_meta["snapshot_json"]

    # T055: Cross-tenant report download denied
    assert outsider.get(f"/api/v1/reports/{rep_id}/download").status_code == 403

    # T056: Single-use expiring download link works once, then returns 410 on reuse or expiry
    link1 = owner.post(f"/api/v1/reports/{rep_id}/share-link", json={"expires_in_seconds": 60}).json()
    anon = TestClient(app)
    ok_once = anon.get(link1["download_url"])
    assert ok_once.status_code == 200
    assert ok_once.content.startswith(b"%PDF-")
    reuse = anon.get(link1["download_url"])
    assert reuse.status_code == 410

    # Expired link test
    link2 = owner.post(f"/api/v1/reports/{rep_id}/share-link", json={"expires_in_seconds": 5}).json()
    set_clock(Clock(lambda: datetime(2026, 11, 12, 9, 5, 0, tzinfo=timezone.utc)))
    exp_res = anon.get(link2["download_url"])
    assert exp_res.status_code == 410

    # T073: Crafted HTML with file:// or internal IP blocked
    ssrf_html = owner.post(
        "/api/v1/reports",
        json={"report_type": "invoice", "invoice_id": inv_id, "custom_html_test": '<img src="file:///etc/passwd" />'},
    )
    assert ssrf_html.status_code == 422
    assert ssrf_html.json()["code"] == "REPORT_UNSAFE_ASSET"


def test_t058_t059_t061_t062_t063_t064_t072_comms_and_ssrf(isolated_db):
    owner = _setup_user("comms_owner@example.com")
    org = owner.post("/api/v1/organizations", json={"name": "CommsOrg", "slug": "comms-org", "default_currency": "PKR"}).json()
    sc = owner.post(
        "/api/v1/subscribers",
        json={"organization_id": org["id"], "name": "Ali", "email": "ali@example.com", "phone_e164": "+923001234567"},
    ).json()

    # T072: Webhook SSRF guard rejects private/loopback/metadata IPs
    for bad_url in ("http://127.0.0.1:8080/hook", "http://169.254.169.254/latest/meta-data", "http://10.0.0.5/internal"):
        res = owner.post("/api/v1/webhooks", json={"organization_id": org["id"], "url": bad_url})
        assert res.status_code == 422
        assert res.json()["code"] == "UNSAFE_WEBHOOK_URL"

    # T061: Unverified business sending domain is blocked
    dom = owner.post("/api/v1/domains", json={"organization_id": org["id"], "domain": "fitzone.pk"}).json()
    blocked_send = owner.post(
        "/api/v1/notifications/send-branded",
        json={"organization_id": org["id"], "from_email": "billing@fitzone.pk", "recipient": "ali@example.com", "subject": "Hi", "body": "Test"},
    )
    assert blocked_send.status_code == 403
    assert owner.post(f"/api/v1/domains/{dom['id']}/verify").json()["status"] == "verified"
    ok_send = owner.post(
        "/api/v1/notifications/send-branded",
        json={"organization_id": org["id"], "from_email": "billing@fitzone.pk", "recipient": "ali@example.com", "subject": "Hi", "body": "Test"},
    )
    assert ok_send.status_code == 202

    # T059: SMTP down -> job moves to retrying, does not falsely mark delivered
    def _failing_smtp(*args):
        raise ConnectionRefusedError("SMTP connection refused")

    with get_db(write=True) as db:
        r_fail = dispatch_email_jobs(db, organization_id=org["id"], smtp_transport=_failing_smtp)
        assert r_fail["retrying"] == 1
        assert r_fail["accepted_by_relay"] == 0

    # Advance clock past 30s backoff and succeed -> accepted_by_relay (not delivered!)
    set_clock(Clock(lambda: datetime(2026, 11, 12, 9, 2, 0, tzinfo=timezone.utc)))
    sent_messages = []

    def _ok_smtp(host, port, user, pw, from_a, to_a, subj, body):
        sent_messages.append((from_a, to_a, subj))
        return "msg-postal-001"

    with get_db(write=True) as db:
        r_ok = dispatch_email_jobs(db, organization_id=org["id"], from_email="billing@fitzone.pk", smtp_transport=_ok_smtp)
        assert r_ok["accepted_by_relay"] == 1
    assert len(sent_messages) == 1

    notifs = owner.get(f"/api/v1/notifications?organization_id={org['id']}").json()["notifications"]
    assert notifs[0]["status"] == "accepted_by_relay"

    # Postal webhook updates status to delivered only with actual evidence
    wh_res = owner.post("/api/v1/webhooks/postal", json={"provider_msg_id": "msg-postal-001", "event": "delivered"})
    assert wh_res.status_code == 200
    assert wh_res.json()["status"] == "delivered"

    # T062: Phone saved without WhatsApp consent -> blocked
    with get_db(write=True) as db:
        try:
            send_whatsapp_template(
                db,
                organization_id=org["id"],
                subscriber_contact_id=sc["id"],
                template_name="receipt_notice",
                parameters=["PKR 3,000"],
            )
            assert False, "Expected consent block"
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 403

    # T063: Grant WhatsApp consent -> 1 outbound API request sent
    assert (
        owner.post(
            f"/api/v1/contacts/{sc['id']}/consents",
            json={"organization_id": org["id"], "channel": "whatsapp", "status": "granted"},
        ).status_code
        == 200
    )
    wa_calls = []

    def _fake_wa(url, headers, body):
        wa_calls.append(body)
        return {"messages": [{"id": "wamid.12345"}]}

    with get_db(write=True) as db:
        wa_res = send_whatsapp_template(
            db,
            organization_id=org["id"],
            subscriber_contact_id=sc["id"],
            template_name="receipt_notice",
            parameters=["PKR 3,000"],
            http_poster=_fake_wa,
        )
        assert wa_res["sent"] is True
        assert wa_res["provider_message_id"] == "wamid.12345"
    assert len(wa_calls) == 1

    # T064: Opt out revokes consent immediately and suppresses further sends
    assert (
        owner.post(
            f"/api/v1/contacts/{sc['id']}/consents",
            json={"organization_id": org["id"], "channel": "whatsapp", "status": "revoked"},
        ).status_code
        == 200
    )
    assert (
        owner.post(
            "/api/v1/notifications/whatsapp",
            json={"organization_id": org["id"], "subscriber_contact_id": sc["id"], "parameters": ["PKR 3,000"]},
        ).status_code
        == 403
    )

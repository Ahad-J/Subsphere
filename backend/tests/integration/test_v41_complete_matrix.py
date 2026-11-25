"""SubSphere v4.1 Complete Integration Test Matrix (SUB, CAL, MAIL, EXT, LLM, OTEL, PDF, PROV, REG)."""
from __future__ import annotations

import json
import socket
import sqlite3
import sys
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.clock import Clock, set_clock
from app.db.engine import get_db, init_schema

ROOT_DIR = Path(__file__).resolve().parents[3]
WORKER_DIR = ROOT_DIR / "services" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(WORKER_DIR))


def _register_and_login(client: TestClient, email: str, full_name: str = "Test User") -> dict[str, str]:
    reg = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "StrongPassword123!", "full_name": full_name},
    )
    assert reg.status_code == 201, reg.text
    client.post("/api/v1/auth/verify-email", json={"token": reg.json()["verification_token"]})
    login = client.post("/api/v1/auth/login", json={"email": email, "password": "StrongPassword123!"})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _start_local_smtp_sink() -> tuple[int, list[str], threading.Event, callable]:
    """Spin up a minimal real RFC 5321 TCP SMTP server to verify real smtplib wire delivery."""
    captured: list[str] = []
    received_event = threading.Event()
    srv_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv_sock.bind(("127.0.0.1", 0))
    srv_sock.listen(5)
    port = srv_sock.getsockname()[1]
    stop_flag = False

    def _handle_conn(conn: socket.socket):
        with conn:
            conn.sendall(b"220 127.0.0.1 SubSphere Test SMTP Ready\r\n")
            in_data = False
            chunks: list[bytes] = []
            buf = b""
            while True:
                data = conn.recv(4096)
                if not data:
                    break
                buf += data
                if in_data:
                    if b"\r\n.\r\n" in buf:
                        body_part, rest = buf.split(b"\r\n.\r\n", 1)
                        chunks.append(body_part)
                        captured.append(b"".join(chunks).decode("utf-8", "replace"))
                        received_event.set()
                        conn.sendall(b"250 2.0.0 OK queued\r\n")
                        in_data = False
                        buf = rest
                else:
                    while b"\r\n" in buf:
                        line, buf = buf.split(b"\r\n", 1)
                        cmd = line.decode("utf-8", "replace").upper()
                        if cmd.startswith("EHLO") or cmd.startswith("HELO"):
                            conn.sendall(b"250-127.0.0.1\r\n250 OK\r\n")
                        elif cmd.startswith("MAIL FROM") or cmd.startswith("RCPT TO"):
                            conn.sendall(b"250 OK\r\n")
                        elif cmd == "DATA":
                            in_data = True
                            chunks = []
                            conn.sendall(b"354 End data with <CR><LF>.<CR><LF>\r\n")
                            if b"\r\n.\r\n" in buf:
                                body_part, buf = buf.split(b"\r\n.\r\n", 1)
                                captured.append(body_part.decode("utf-8", "replace"))
                                received_event.set()
                                conn.sendall(b"250 2.0.0 OK queued\r\n")
                                in_data = False
                            break
                        elif cmd == "QUIT":
                            conn.sendall(b"221 Bye\r\n")
                            return

    def _serve():
        srv_sock.settimeout(0.2)
        while not stop_flag:
            try:
                conn, _ = srv_sock.accept()
                threading.Thread(target=_handle_conn, args=(conn,), daemon=True).start()
            except socket.timeout:
                continue
            except OSError:
                break

    t = threading.Thread(target=_serve, daemon=True)
    t.start()

    def _shutdown():
        nonlocal stop_flag
        stop_flag = True
        try:
            srv_sock.close()
        except OSError:
            pass

    return port, captured, received_event, _shutdown


def test_sub_01_to_04_personal_and_provider_subscription_crud_and_version_conflict(client: TestClient):
    headers = _register_and_login(client, "sub.editor@subsphere.io", "Sub Editor")

    # SUB-01: Create & edit personal subscription including quarterly interval & timezone
    created = client.post(
        "/api/v1/personal/subscriptions",
        headers=headers,
        json={
            "service_name": "Claude",
            "service_url": "https://claude.ai",
            "category": "AI",
            "plan_name": "Pro",
            "price_minor": 2000,
            "currency": "USD",
            "billing_interval": "monthly",
            "next_renewal_date": "2026-11-19",
            "timezone": "Asia/Karachi",
            "reminder_offsets_days": [7, 3, 1],
            "notes": "Initial setup",
        },
    )
    assert created.status_code == 201, created.text
    sub = created.json()
    assert sub["version"] == 1
    assert sub["timezone"] == "Asia/Karachi"
    assert sub["tracking_status"] == "tracking_active"

    # Trigger initial reminder
    tick1 = client.post("/api/v1/personal/reminders/tick", headers=headers)
    assert tick1.status_code == 200
    assert tick1.json()["queued_count"] == 1

    # Edit price, interval -> quarterly, next_renewal_date, notes
    patched = client.patch(
        f"/api/v1/personal/subscriptions/{sub['id']}",
        headers=headers,
        json={
            "version": 1,
            "price_minor": 6000,
            "billing_interval": "quarterly",
            "next_renewal_date": "2026-12-05",
            "timezone": "America/New_York",
            "notes": "Switched to quarterly billing",
        },
    )
    assert patched.status_code == 200, patched.text
    p_data = patched.json()
    assert p_data["version"] == 2
    assert p_data["billing_interval"] == "quarterly"
    assert p_data["monthly_equivalent_minor"] == 2000
    assert p_data["timezone"] == "America/New_York"

    # SUB-02: Optimistic locking rejects stale version=1 with 409 VERSION_CONFLICT
    conflict = client.patch(
        f"/api/v1/personal/subscriptions/{sub['id']}",
        headers=headers,
        json={"version": 1, "price_minor": 9999},
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "VERSION_CONFLICT"

    # Verify audit trail recorded before/after
    with get_db(write=False) as db:
        aud = db.fetchone(
            "SELECT metadata_json FROM audit_logs WHERE resource_id = %s AND action = 'personal_subscription.updated'",
            (sub["id"],),
        )
        assert aud is not None
        meta = json.loads(aud["metadata_json"])
        assert meta["previous"]["price_minor"] == 2000
        assert meta["updated"]["price_minor"] == 6000

    # SUB-03: Stopping tracking excludes from active monthly total & suppresses queued reminders
    stopped = client.patch(
        f"/api/v1/personal/subscriptions/{sub['id']}",
        headers=headers,
        json={"version": 2, "tracking_status": "stopped_tracking"},
    )
    assert stopped.status_code == 200
    assert stopped.json()["tracking_status"] == "stopped_tracking"
    dash = client.get("/api/v1/personal/dashboard", headers=headers).json()
    assert dash["active_subscription_count"] == 0
    with get_db(write=False) as db:
        active_jobs = db.fetchall(
            "SELECT * FROM notification_jobs WHERE logical_key LIKE %s AND status IN ('queued', 'retrying')",
            (f"personal_renewal:{sub['id']}:%",),
        )
        assert active_jobs == []

    # SUB-04: Provider edits subscriber contact while preserving existing invoices
    org = client.post(
        "/api/v1/organizations",
        headers=headers,
        json={"name": "Gym Studio", "slug": "gym-studio", "default_currency": "USD"},
    ).json()
    sc = client.post(
        "/api/v1/subscribers",
        headers=headers,
        json={"organization_id": org["id"], "name": "Alice Member", "email": "alice@gym.test", "fee_minor": 5000, "plan_name": "Gold"},
    ).json()
    ob = client.post(
        "/api/v1/finance/obligations",
        headers=headers,
        json={
            "organization_id": org["id"],
            "direction": "RECEIVABLE",
            "party_ref": "Alice Member",
            "amount_minor": 5000,
            "currency": "USD",
            "due_at": "2026-11-25T23:59:59Z",
            "description": "Nov Membership",
            "subscriber_contact_id": sc["id"],
            "create_invoice": True,
        },
    ).json()
    inv_id = ob["invoice_id"]

    # Edit via provider compatibility route AND /api/v1/subscribers/{id}
    edit_sc = client.patch(
        f"/api/v1/provider/subscribers/{sc['id']}",
        headers=headers,
        json={"name": "Alice Updated", "plan_name": "Platinum", "fee_minor": 7500, "status": "paused", "version": 1},
    )
    assert edit_sc.status_code == 200, edit_sc.text
    detail = edit_sc.json()
    assert detail["name"] == "Alice Updated"
    assert detail["fee_minor"] == 7500
    assert detail["version"] == 2
    assert len(detail["invoices"]) == 1
    assert detail["invoices"][0]["id"] == inv_id
    assert detail["invoices"][0]["total_minor"] == 5000  # Historical invoice preserved untouched!


def test_cal_01_to_03_month_calendar_projection_and_event_details(client: TestClient):
    headers = _register_and_login(client, "cal.user@subsphere.io")

    # Create monthly anchored Jan 31 + quarterly
    s_mon = client.post(
        "/api/v1/personal/subscriptions",
        headers=headers,
        json={
            "service_name": "Anchored Monthly",
            "plan_name": "Plus",
            "price_minor": 1500,
            "currency": "USD",
            "billing_interval": "monthly",
            "next_renewal_date": "2027-01-31",
            "anchor_day": 31,
        },
    ).json()
    client.post(
        "/api/v1/personal/subscriptions",
        headers=headers,
        json={
            "service_name": "Quarterly Suite",
            "price_minor": 4500,
            "currency": "USD",
            "billing_interval": "quarterly",
            "next_renewal_date": "2026-11-15",
        },
    )

    # CAL-01: Range spanning Nov 2026 .. Mar 2027 verifies Feb 28 clamp and quarterly projection
    cal = client.get("/api/v1/calendar/events?view=individual&from=2026-11-01&to=2027-03-31", headers=headers)
    assert cal.status_code == 200, cal.text
    events = cal.json()["events"]
    anchored_dates = [e["date"] for e in events if e["subscription_id"] == s_mon["id"]]
    assert "2027-01-31" in anchored_dates
    assert "2027-02-28" in anchored_dates
    assert "2027-03-31" in anchored_dates
    q_dates = [e["date"] for e in events if e.get("service_name") == "Quarterly Suite"]
    assert "2026-11-15" in q_dates
    assert "2027-02-15" in q_dates

    # CAL-02: Click an event -> GET /api/v1/calendar/events/{event_id}
    first_ev = events[0]
    ev_detail = client.get(f"/api/v1/calendar/events/{first_ev['event_id']}?view=individual", headers=headers)
    assert ev_detail.status_code == 200
    assert ev_detail.json()["title"] == first_ev["title"]

    # CAL-03: Reschedule next_renewal_date moves chip cleanly
    client.patch(
        f"/api/v1/personal/subscriptions/{s_mon['id']}",
        headers=headers,
        json={"next_renewal_date": "2027-01-20", "version": 1},
    )
    cal2 = client.get("/api/v1/calendar/events?view=individual&from=2027-01-01&to=2027-01-31", headers=headers).json()["events"]
    assert [e["date"] for e in cal2 if e["subscription_id"] == s_mon["id"]] == ["2027-01-20"]


def test_mail_01_to_05_smtp_preferences_test_email_and_worker_scheduler(client: TestClient, monkeypatch):
    port, captured, received_event, shutdown_smtp = _start_local_smtp_sink()
    try:
        monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
        monkeypatch.setenv("SMTP_PORT", str(port))
        monkeypatch.setenv("SMTP_FROM_EMAIL", "noreply@subsphere.test")

        headers = _register_and_login(client, "mail.user@subsphere.io")

        # MAIL-01: Status probe & preferences save
        st = client.get("/api/v1/integrations/email/status", headers=headers)
        assert st.status_code == 200
        assert st.json()["reachable"] is True

        pref = client.patch(
            "/api/v1/notifications/preferences",
            headers=headers,
            json={"email_enabled": True, "default_offsets_days": [5, 1], "timezone": "UTC", "send_hour_local": 9},
        )
        assert pref.status_code == 200
        assert pref.json()["default_offsets_days"] == [5, 1]

        # MAIL-05: Arbitrary third-party recipient rejected 403
        bad = client.post(
            "/api/v1/notifications/test-email",
            headers=headers,
            json={"recipient": "stranger@otherdomain.com"},
        )
        assert bad.status_code == 403
        assert bad.json()["code"] == "UNVERIFIED_RECIPIENT"

        # Valid test email accepted over live local SMTP wire
        ok_test = client.post("/api/v1/notifications/test-email", headers=headers, json={})
        assert ok_test.status_code == 200
        assert ok_test.json()["status"] == "accepted_by_relay"
        assert received_event.wait(timeout=3.0)
        assert len(captured) >= 1

        # Rate limit triggered on 6th call within 60s
        for _ in range(4):
            r = client.post("/api/v1/notifications/test-email", headers=headers, json={})
            assert r.status_code == 200
        limited = client.post("/api/v1/notifications/test-email", headers=headers, json={})
        assert limited.status_code == 429

        # MAIL-02 & MAIL-03 & MAIL-04: Custom rule (5 days ahead from 2026-11-12 -> 2026-11-17) + Worker background run_once
        sub = client.post(
            "/api/v1/personal/subscriptions",
            headers=headers,
            json={"service_name": "JetBrains", "price_minor": 1000, "billing_interval": "monthly", "next_renewal_date": "2026-11-17"},
        ).json()
        rr = client.post(
            f"/api/v1/subscriptions/{sub['id']}/reminder-rules",
            headers=headers,
            json={"days_before": [5, 2], "timezone": "UTC", "send_hour_local": 9},
        )
        assert rr.status_code == 200
        assert rr.json()["days_before"] == [5, 2]

        from outbox_dispatcher import run_once

        res1 = run_once(materialize_medallion=False)
        assert res1["reminders"]["queued_count"] == 1
        assert res1["email_dispatch"]["accepted_by_relay"] == 1

        # Running worker again skips duplicates cleanly (MAIL-04)
        res2 = run_once(materialize_medallion=False)
        assert res2["reminders"]["queued_count"] == 0
        assert res2["reminders"]["skipped_duplicates"] >= 1
        assert res2["email_dispatch"]["accepted_by_relay"] == 0

        hist = client.get("/api/v1/notifications/history", headers=headers).json()["notifications"]
        assert any("JetBrains" in n["subject"] and n["status"] == "accepted_by_relay" for n in hist)
    finally:
        shutdown_smtp()


def test_ext_01_to_05_browser_extension_pairing_sync_privacy_and_revoke(client: TestClient):
    headers = _register_and_login(client, "ext.user@subsphere.io")

    # EXT-01: Pair code generation & one-time exchange
    pc = client.post("/api/v1/personal/extension/pair-code", headers=headers, json={"allowlist": ["claude.ai", "chatgpt.com"]})
    assert pc.status_code == 201
    pair_code = pc.json()["pair_code"]

    exch = client.post("/api/v1/personal/extension/pair", json={"pair_code": pair_code, "device_name": "laptop-chrome"})
    assert exch.status_code == 200, exch.text
    ext_token = exch.json()["extension_token"]
    assert ext_token.startswith("ext_live_")

    # Reuse rejected
    reuse = client.post("/api/v1/personal/extension/pair", json={"pair_code": pair_code})
    assert reuse.status_code == 409

    # EXT-02 & EXT-04: Scoped token sync + idempotent dedupe + history import coverage
    sync_hdrs = {"Authorization": f"Bearer {ext_token}"}
    s1 = client.post(
        "/api/v1/personal/activity/sync",
        headers=sync_hdrs,
        json={
            "source_device": "laptop-chrome",
            "records": [
                {"record_id": "2026-11-12:claude.ai:1", "day": "2026-11-12", "domain": "claude.ai", "active_seconds": 360, "visits": 4},
                {"record_id": "hist:2026-11-10:chatgpt.com", "day": "2026-11-10", "domain": "chatgpt.com", "active_seconds": None, "visits": 3, "coverage": "history_import_may_include_synced"},
            ],
        },
    )
    assert s1.status_code == 200, s1.text
    assert s1.json()["accepted"] == 2

    # Replay same batch -> skipped duplicates
    s2 = client.post(
        "/api/v1/personal/activity/sync",
        headers=sync_hdrs,
        json={
            "source_device": "laptop-chrome",
            "records": [
                {"record_id": "2026-11-12:claude.ai:1", "day": "2026-11-12", "domain": "claude.ai", "active_seconds": 360, "visits": 4},
            ],
        },
    )
    assert s2.status_code == 200
    assert s2.json()["accepted"] == 0
    assert s2.json()["duplicates_skipped"] == 1

    # EXT-03: Privacy & non-allowlisted checks
    bad_priv = client.post(
        "/api/v1/personal/activity/sync",
        headers=sync_hdrs,
        json={"records": [{"record_id": "bad1", "day": "2026-11-12", "domain": "claude.ai", "prompt": "secret", "url": "https://claude.ai/chat/1"}]},
    )
    assert bad_priv.status_code == 422
    bad_dom = client.post(
        "/api/v1/personal/activity/sync",
        headers=sync_hdrs,
        json={"records": [{"record_id": "bad2", "day": "2026-11-12", "domain": "mail.google.com", "active_seconds": 50}]},
    )
    assert bad_dom.status_code == 403

    # Extension status shows observed daily aggregates, AI usage keeps tokens None
    st = client.get("/api/v1/personal/extension/status", headers=headers).json()
    assert st["connected"] is True
    assert len(st["daily_aggregates"]) == 2
    usage = client.get("/api/v1/ai/usage", headers=headers).json()
    assert usage["tokens"]["total_tokens"] is None

    # EXT-05: Revoke blocks subsequent extension requests
    rev = client.post("/api/v1/personal/extension/revoke", headers=headers)
    assert rev.status_code == 200
    blocked = client.post(
        "/api/v1/personal/activity/sync",
        headers=sync_hdrs,
        json={"records": [{"record_id": "after-revoke", "day": "2026-11-12", "domain": "claude.ai", "active_seconds": 10}]},
    )
    assert blocked.status_code == 401


def test_otel_01_to_04_and_llm_01_to_05_telemetry_gateway_and_deduplication(client: TestClient, monkeypatch):
    # Spin up local mock-free HTTP LiteLLM endpoint on ephemeral port
    gw_requests: list[dict] = []

    class _GWHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(
                {"status": "healthy"} if self.path.startswith("/health") else {"data": [{"id": "gpt-4o-mini"}, {"id": "claude-3-5-sonnet"}]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))
            data = json.loads(raw.decode() or "{}")
            gw_requests.append({"path": self.path, "body": data})
            if self.path.startswith("/key/generate"):
                out = {"key": "sk-subsphere-vk-live99998888"}
            else:
                out = {"id": "gw-1", "usage": {"prompt_tokens": 10000, "completion_tokens": 5000, "total_tokens": 15000}}
            body = json.dumps(out).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            return

    gw_srv = HTTPServer(("127.0.0.1", 0), _GWHandler)
    gw_port = gw_srv.server_address[1]
    threading.Thread(target=gw_srv.serve_forever, daemon=True).start()

    try:
        monkeypatch.setenv("LITELLM_URL", f"http://127.0.0.1:{gw_port}")
        monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-super-secret-master")

        headers = _register_and_login(client, "ai.otel@subsphere.io")

        # OTEL-04: MCP explicit disclosure
        mcp = client.get("/api/v1/integrations/mcp/tools", headers=headers).json()
        assert mcp["telemetry_capability"] is False
        assert "OpenTelemetry" in mcp["disclosure"]

        # OTEL-01 & OTEL-02: Pair token + cumulative checkpointing (1000 -> 1500 adds only 500 delta!)
        ot_pair = client.post("/api/v1/integrations/otel/pair-token", headers=headers, json={}).json()
        ot_hdrs = {"Authorization": f"Bearer {ot_pair['otel_token']}"}

        push1 = client.post(
            "/v1/metrics",
            headers=ot_hdrs,
            json={
                "resourceMetrics": [
                    {
                        "scopeMetrics": [
                            {
                                "metrics": [
                                    {
                                        "name": "claude_code.token.usage",
                                        "sum": {
                                            "aggregationTemporality": 2,
                                            "dataPoints": [
                                                {
                                                    "asInt": 1000,
                                                    "timeUnixNano": "1700000001",
                                                    "attributes": [
                                                        {"key": "type", "value": {"stringValue": "input"}},
                                                        {"key": "model", "value": {"stringValue": "claude-3-5-sonnet"}},
                                                        {"key": "session.id", "value": {"stringValue": "sess-cum-1"}},
                                                        {"key": "request_ref", "value": {"stringValue": "req-shared-1"}},
                                                    ],
                                                },
                                                {
                                                    "asInt": 200,
                                                    "timeUnixNano": "1700000001",
                                                    "attributes": [
                                                        {"key": "type", "value": {"stringValue": "cacheRead"}},
                                                        {"key": "model", "value": {"stringValue": "claude-3-5-sonnet"}},
                                                        {"key": "session.id", "value": {"stringValue": "sess-cum-1"}},
                                                        {"key": "request_ref", "value": {"stringValue": "req-shared-1"}},
                                                    ],
                                                },
                                            ],
                                        },
                                    }
                                ]
                            }
                        ]
                    }
                ]
            },
        )
        assert push1.status_code == 200, push1.text
        assert push1.json()["totals"]["input_tokens"] == 1000
        assert push1.json()["totals"]["cache_read_tokens"] == 200

        # Cumulative replay of same value 1000 adds 0; increase to 1500 adds +500
        push2 = client.post(
            "/v1/metrics",
            headers=ot_hdrs,
            json={
                "session_id": "sess-cum-1",
                "temporality": "cumulative",
                "metrics": [
                    {"name": "claude_code.token.usage", "type": "input", "model": "claude-3-5-sonnet", "request_ref": "req-cum-2", "value": 1500, "timestamp": "1700000002"}
                ],
            },
        )
        assert push2.status_code == 200
        assert push2.json()["totals"]["input_tokens"] == 500

        # OTEL-03: Prompt attribute rejected 422
        bad_otel = client.post(
            "/v1/metrics",
            headers=ot_hdrs,
            json={
                "metrics": [
                    {"name": "claude_code.token.usage", "type": "input", "value": 50, "attributes": {"prompt": "private code"}}
                ]
            },
        )
        assert bad_otel.status_code == 422
        assert bad_otel.json()["code"] == "PRIVACY_VIOLATION_PROMPT_CONTENT"

        # LLM-01..03: Health, Virtual Key (no master key exposure), API-key vs Subscription billing lanes
        hlth = client.get("/api/v1/integrations/litellm/health", headers=headers).json()
        assert hlth["reachable"] is True

        vkey = client.post("/api/v1/integrations/litellm/virtual-key", headers=headers, json={}).json()
        assert "sk-super-secret-master" not in json.dumps(vkey)
        assert vkey["virtual_key"] == "sk-subsphere-vk-live99998888"

        t_api = client.post(
            "/api/v1/integrations/litellm/test-request",
            headers=headers,
            json={"model_name": "gpt-4o-mini", "billing_lane": "api_key", "request_ref": "req-api-only"},
        ).json()
        assert t_api["execution_mode"] == "live_gateway"
        assert t_api["reported_cost_minor"] is not None and t_api["reported_cost_minor"] > 0

        # Shared request_ref with OTel tests LLM-04 deduplication + claude_subscription lane
        t_sub = client.post(
            "/api/v1/integrations/litellm/test-request",
            headers=headers,
            json={"model_name": "claude-3-5-sonnet", "billing_lane": "claude_subscription", "request_ref": "req-shared-1"},
        ).json()
        assert t_sub["reported_cost_minor"] is None
        assert t_sub["estimated_value_minor"] > 0

        ai_usage = client.get("/api/v1/ai/usage", headers=headers).json()
        assert ai_usage["deduplicated_count"] >= 1
    finally:
        gw_srv.shutdown()
        gw_srv.server_close()


def test_pdf_01_to_03_and_prov_01_to_02_and_reg_01_to_03(client: TestClient, monkeypatch):
    port, captured, received_event, shutdown_smtp = _start_local_smtp_sink()
    try:
        monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
        monkeypatch.setenv("SMTP_PORT", str(port))

        headers = _register_and_login(client, "owner.rep@subsphere.io", "Owner Rep")

        # Seed personal subscription + activity + OTel record
        client.post(
            "/api/v1/personal/subscriptions",
            headers=headers,
            json={"service_name": "Claude", "category": "AI", "plan_name": "Pro", "price_minor": 2000, "billing_interval": "monthly", "next_renewal_date": "2026-12-01"},
        )
        client.post(
            "/api/v1/personal/activity/sync",
            headers=headers,
            json={"records": [{"record_id": "2026-11-12:claude.ai:1", "day": "2026-11-12", "domain": "claude.ai", "active_seconds": 600, "visits": 2}]},
        )

        # PDF-01: /api/v1/history dataset & styled preview + real PDF download + send-email
        hist = client.get("/api/v1/history?from=2026-11-01&to=2026-12-31&view=individual", headers=headers)
        assert hist.status_code == 200
        assert len(hist.json()["subscriptions"]) == 1

        rep = client.post(
            "/api/v1/reports",
            headers=headers,
            json={"kind": "history_report", "view": "individual", "period_start": "2026-11-01", "period_end": "2026-12-31"},
        )
        assert rep.status_code == 202, rep.text
        rep_id = rep.json()["id"]
        assert rep.json()["kind"] == "history_report"

        prev = client.get(f"/api/v1/reports/{rep_id}/preview", headers=headers)
        assert prev.status_code == 200
        assert "<svg" in prev.text
        assert "Claude" in prev.text
        assert "Recurring subscription fees are tracked separately" in prev.text

        dl = client.get(f"/api/v1/reports/{rep_id}/download", headers=headers)
        assert dl.status_code == 200
        assert dl.content.startswith(b"%PDF-1.")
        assert b"%%EOF" in dl.content[-32:]
        assert "attachment;" in dl.headers.get("content-disposition", "")

        eml = client.post(f"/api/v1/reports/{rep_id}/send-email", headers=headers)
        assert eml.status_code == 200
        assert eml.json()["status"] == "accepted_by_relay"
        assert received_event.wait(timeout=3.0)

        # PROV-01 & PROV-02: Provider overdue reminder + employee AI usage isolation
        org = client.post("/api/v1/organizations", headers=headers, json={"name": "Prov Org", "slug": "prov-org", "default_currency": "USD"}).json()
        sc = client.post("/api/v1/subscribers", headers=headers, json={"organization_id": org["id"], "name": "Bob Late", "email": "bob@late.test"}).json()
        ob = client.post(
            "/api/v1/finance/obligations",
            headers=headers,
            json={
                "organization_id": org["id"],
                "direction": "RECEIVABLE",
                "party_ref": "Bob Late",
                "amount_minor": 4000,
                "currency": "USD",
                "due_at": "2026-11-01T00:00:00Z",
                "description": "Overdue Membership",
                "subscriber_contact_id": sc["id"],
                "create_invoice": True,
            },
        ).json()
        rem = client.post(f"/api/v1/invoices/{ob['invoice_id']}/send-reminder", headers=headers)
        assert rem.status_code == 200
        assert rem.json()["recipient"] == "bob@late.test"
        assert rem.json()["status"] == "accepted_by_relay"

        # Org OTel push shows under /api/v1/provider/ai/usage while personal browser activity does NOT leak
        org_otel = client.post("/api/v1/integrations/otel/pair-token", headers=headers, json={"organization_id": org["id"]}).json()
        client.post(
            "/v1/metrics",
            headers={"Authorization": f"Bearer {org_otel['otel_token']}"},
            json={"session_id": "org-s1", "metrics": [{"name": "claude_code.token.usage", "type": "input", "value": 420}]},
        )
        prov_ai = client.get(f"/api/v1/provider/ai/usage?organization_id={org['id']}", headers=headers)
        assert prov_ai.status_code == 200
        assert prov_ai.json()["total_tokens"] == 420
        assert "claude.ai" not in json.dumps(prov_ai.json())

        # REG-03: Additive schema migration preserves pre-existing v4.0 DB rows seamlessly
        db_url = f"sqlite:///{ROOT_DIR / 'backend' / 'tests' / '_reg_tmp.db'}"
        tmp_db_file = ROOT_DIR / "backend" / "tests" / "_reg_tmp.db"
        if tmp_db_file.exists():
            tmp_db_file.unlink()
        try:
            conn = sqlite3.connect(tmp_db_file)
            conn.executescript(
                """
                CREATE TABLE subscriber_contacts (id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, name TEXT NOT NULL, email TEXT, phone_e164 TEXT, notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
                INSERT INTO subscriber_contacts VALUES ('legacy-1', 'org-1', 'Legacy User', 'legacy@test.io', NULL, 'from v4.0', '2026-01-01T00:00:00Z');
                """
            )
            conn.commit()
            conn.close()
            init_schema(db_url)
            conn2 = sqlite3.connect(tmp_db_file)
            row = conn2.execute("SELECT name, fee_minor, status, version FROM subscriber_contacts WHERE id = 'legacy-1'").fetchone()
            conn2.close()
            assert row == ("Legacy User", 0, "active", 1)
        finally:
            if tmp_db_file.exists():
                tmp_db_file.unlink()
    finally:
        shutdown_smtp()


def test_masked_email_and_one_click_live_credits_ping(client: TestClient, monkeypatch):
    port, captured, received_event, shutdown_smtp = _start_local_smtp_sink()
    gw_logs: list[dict] = []

    class _CreditsGWHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/spend/logs"):
                out = {"logs": gw_logs}
            elif self.path.startswith("/v1/credits") or self.path.startswith("/credits"):
                out = {
                    "status": "active",
                    "currency": "USD",
                    "monthly_limit_minor": 2000,
                    "used_credits_minor": sum(int(x.get("reported_cost_minor") or 0) for x in gw_logs),
                    "remaining_credits_minor": max(0, 2000 - sum(int(x.get("reported_cost_minor") or 0) for x in gw_logs)),
                    "total_tokens": sum(int(x.get("total_tokens") or 0) for x in gw_logs),
                }
            else:
                out = {"status": "healthy"}
            body = json.dumps(out).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            return

    gw_srv = HTTPServer(("127.0.0.1", 0), _CreditsGWHandler)
    gw_port = gw_srv.server_address[1]
    threading.Thread(target=gw_srv.serve_forever, daemon=True).start()

    try:
        monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
        monkeypatch.setenv("SMTP_PORT", str(port))
        monkeypatch.setenv("SMTP_FROM_EMAIL", "relay@mask.subsphere.local")
        monkeypatch.setenv("LITELLM_URL", f"http://127.0.0.1:{gw_port}")

        # Register, verify, login, and inspect auto-saved email + masks
        reg = client.post(
            "/api/v1/auth/register",
            json={"email": "ahad.personal@gmail.com", "password": "StrongPassword123!", "full_name": "Ahad"},
        )
        assert reg.status_code == 201, reg.text
        user_reg = reg.json()["user"]
        assert user_reg["masked_email"] == "ah***al@gmail.com"
        assert user_reg["relay_mask_email"].endswith("@mask.subsphere.local")

        client.post("/api/v1/auth/verify-email", json={"token": reg.json()["verification_token"]})
        login = client.post(
            "/api/v1/auth/login",
            json={"email": "ahad.personal@gmail.com", "password": "StrongPassword123!"},
        )
        assert login.status_code == 200
        tok = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {tok}"}

        prefs = client.get("/api/v1/notifications/preferences", headers=headers)
        assert prefs.status_code == 200
        assert prefs.json()["primary_email"] == "ahad.personal@gmail.com"
        assert prefs.json()["masked_email"] == "ah***al@gmail.com"
        relay_mask = prefs.json()["relay_mask_email"]

        # Send via mask + receive via mask
        sent = client.post(
            "/api/v1/notifications/test-email",
            headers=headers,
            json={"recipient": relay_mask},
        )
        assert sent.status_code == 200, sent.text
        assert sent.json()["status"] == "accepted_by_relay"
        assert sent.json()["relay_mask_email"] == relay_mask
        assert received_event.wait(timeout=3.0)
        assert any(relay_mask in msg for msg in captured)

        recv = client.post(
            "/api/v1/notifications/masked-inbox/receive",
            headers=headers,
            json={"sender": "billing@claude.ai", "subject": "Receipt #101"},
        )
        assert recv.status_code == 201, recv.text
        assert recv.json()["masked_recipient"] == relay_mask

        inbox = client.get("/api/v1/notifications/masked-inbox", headers=headers)
        assert inbox.status_code == 200
        assert any(m["direction"] == "received" and m["subject"] == "Receipt #101" for m in inbox.json()["messages"])

        # Dev password reset endpoint works & updates password
        rst = client.post(
            "/api/v1/auth/reset-password",
            json={"email": "ahad.personal@gmail.com", "new_password": "NewPassword456!"},
        )
        assert rst.status_code == 200
        relogin = client.post(
            "/api/v1/auth/login",
            json={"email": "ahad.personal@gmail.com", "password": "NewPassword456!"},
        )
        assert relogin.status_code == 200

        # Ping before authorization is rejected 403
        pre_ping = client.post("/api/v1/ai/ping-credits", headers=headers, json={})
        assert pre_ping.status_code == 403

        # 1-Click Authorize persists across requests and checks gateway cleanly without fabricating usage
        auth_res = client.post("/api/v1/ai/authorize", headers=headers, json={"allowlist": ["claude.ai"]})
        assert auth_res.status_code == 200, auth_res.text
        assert auth_res.json()["authorized"] is True
        assert auth_res.json()["ping"]["new_records_synced"] == 0
        tokens_before = auth_res.json()["usage"]["tokens"]["total_tokens"]
        assert tokens_before is None

        # Calling ping when no AI requests occurred must NOT fabricate tokens
        idle_ping = client.post("/api/v1/ai/ping-credits", headers=headers, json={})
        assert idle_ping.status_code == 200, idle_ping.text
        assert idle_ping.json()["ping"]["new_records_synced"] == 0
        assert idle_ping.json()["tokens"]["total_tokens"] == tokens_before
        assert idle_ping.json()["authorized"] is True

        # Simulate a real external AI request logged by LiteLLM /spend/logs (150 in + 50 out = 200 tokens)
        gw_logs.append(
            {
                "request_id": "lit-real-200",
                "request_ref": "req-real-200",
                "model": "claude-3-5-sonnet",
                "billing_lane": "api_key",
                "input_tokens": 150,
                "output_tokens": 50,
                "total_tokens": 200,
                "reported_cost_minor": 2,
                "occurred_at": "2026-11-15T12:00:00Z",
            }
        )
        live_ping = client.post("/api/v1/ai/ping-credits", headers=headers, json={})
        assert live_ping.status_code == 200, live_ping.text
        assert live_ping.json()["ping"]["new_records_synced"] == 1
        assert live_ping.json()["tokens"]["total_tokens"] == 200

        # Subsequent idle ping adds 0 (idempotent read-only sync, no duplicate/fake inflation)
        idle_ping_2 = client.post("/api/v1/ai/ping-credits", headers=headers, json={})
        assert idle_ping_2.status_code == 200, idle_ping_2.text
        assert idle_ping_2.json()["ping"]["new_records_synced"] == 0
        assert idle_ping_2.json()["tokens"]["total_tokens"] == 200

        # Revoke stops authorization cleanly
        rev = client.post("/api/v1/ai/revoke", headers=headers)
        assert rev.status_code == 200
        assert rev.json()["authorized"] is False
    finally:
        gw_srv.shutdown()
        gw_srv.server_close()
        shutdown_smtp()


def test_mcp_server_install_jsonrpc_and_usage_reporting(client: TestClient):
    reg = client.post(
        "/api/v1/auth/register",
        json={"email": "mcp.user@example.com", "password": "StrongPassword123!", "full_name": "MCP User"},
    )
    assert reg.status_code == 201, reg.text
    client.post("/api/v1/auth/verify-email", json={"token": reg.json()["verification_token"]})
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "mcp.user@example.com", "password": "StrongPassword123!"},
    )
    assert login.status_code == 200
    user_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    # 1. Generate MCP token + install config
    tok_res = client.post("/api/v1/mcp/token", headers=user_headers, json={"client_name": "cursor"})
    assert tok_res.status_code == 201, tok_res.text
    tok_data = tok_res.json()
    mcp_token = tok_data["mcp_token"]
    assert mcp_token.startswith("mcp_live_")
    assert "mcpServers" in tok_data["claude_desktop_config"]
    assert "claude mcp add" in tok_data["claude_code_command"]
    assert "/api/v1/mcp" in tok_data["http_mcp_url"]

    # 2. Download preconfigured standalone MCP server script
    dl = client.get(f"/api/v1/mcp/install.py?token={mcp_token}")
    assert dl.status_code == 200
    assert "text/x-python" in dl.headers.get("content-type", "")
    assert mcp_token in dl.text
    assert "def main(" in dl.text

    # 3. MCP discovery endpoint
    disc = client.get("/api/v1/mcp")
    assert disc.status_code == 200
    assert disc.json()["name"] == "subsphere-mcp"
    assert disc.json()["status"] == "ready"

    mcp_headers = {"Authorization": f"Bearer {mcp_token}"}

    # 4. JSON-RPC initialize & tools/list
    init_res = client.post(
        "/api/v1/mcp",
        headers=mcp_headers,
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    assert init_res.status_code == 200, init_res.text
    assert init_res.json()["result"]["protocolVersion"] == "2024-11-05"

    tools_res = client.post(
        "/api/v1/mcp",
        headers=mcp_headers,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    assert tools_res.status_code == 200
    tool_names = {t["name"] for t in tools_res.json()["result"]["tools"]}
    assert {
        "subsphere_report_usage",
        "subsphere_get_usage_summary",
        "subsphere_list_subscriptions",
        "subsphere_add_subscription",
    }.issubset(tool_names)

    # 5. tools/call -> subsphere_report_usage (400 in + 100 out = 500 tokens)
    rep_res = client.post(
        "/api/v1/mcp",
        headers=mcp_headers,
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "subsphere_report_usage",
                "arguments": {
                    "model_name": "claude-3-5-sonnet",
                    "agent_name": "cursor",
                    "input_tokens": 400,
                    "output_tokens": 100,
                    "billing_lane": "claude_subscription",
                    "request_ref": "mcp-req-1",
                },
            },
        },
    )
    assert rep_res.status_code == 200, rep_res.text
    rep_payload = json.loads(rep_res.json()["result"]["content"][0]["text"])
    assert rep_payload["recorded"]["source"] == "MCP_SERVER"
    assert rep_payload["recorded"]["total_tokens"] == 500

    # 6. tools/call -> subsphere_add_subscription & subsphere_list_subscriptions
    add_sub = client.post(
        "/api/v1/mcp",
        headers=mcp_headers,
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "subsphere_add_subscription",
                "arguments": {
                    "service_name": "Cursor Pro",
                    "price_minor": 2000,
                    "next_renewal_date": "2026-12-15",
                },
            },
        },
    )
    assert add_sub.status_code == 200, add_sub.text
    add_payload = json.loads(add_sub.json()["result"]["content"][0]["text"])
    assert add_payload["subscription"]["service_name"] == "Cursor Pro"

    list_subs = client.post(
        "/api/v1/mcp",
        headers=mcp_headers,
        json={
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "subsphere_list_subscriptions", "arguments": {}},
        },
    )
    assert list_subs.status_code == 200
    subs_payload = json.loads(list_subs.json()["result"]["content"][0]["text"])
    assert any(s["service_name"] == "Cursor Pro" for s in subs_payload["subscriptions"])

    # 7. Verify status and strict AI usage ledger reflect the MCP report
    st = client.get("/api/v1/mcp/status", headers=user_headers)
    assert st.status_code == 200
    assert st.json()["connected"] is True
    assert st.json()["total_tokens"] == 500
    assert st.json()["requests_count"] == 1

    ai_u = client.get("/api/v1/ai/usage", headers=user_headers)
    assert ai_u.status_code == 200
    u_data = ai_u.json()
    assert u_data["tokens"]["total_tokens"] == 500
    assert u_data["tokens"]["input_tokens"] == 400
    assert u_data["tokens"]["output_tokens"] == 100
    assert any(r["source"] == "MCP_SERVER" for r in u_data["records"])

    # 8. Revoking MCP token blocks subsequent bearer-token MCP calls
    rev_res = client.post("/api/v1/mcp/revoke", headers=user_headers)
    assert rev_res.status_code == 200
    assert rev_res.json()["connected"] is False

    denied = client.post(
        "/api/v1/mcp",
        headers=mcp_headers,
        json={"jsonrpc": "2.0", "id": 6, "method": "tools/list", "params": {}},
    )
    assert denied.status_code == 401


def test_universal_usage_connector_five_measurements_and_auto_selection(client: TestClient):
    reg = client.post(
        "/api/v1/auth/register",
        json={"email": "universal.connector@example.com", "password": "StrongPassword123!", "full_name": "UC User"},
    )
    assert reg.status_code == 201, reg.text
    client.post("/api/v1/auth/verify-email", json={"token": reg.json()["verification_token"]})
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "universal.connector@example.com", "password": "StrongPassword123!"},
    )
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    # 1. Catalog exposes all 6 tracking methods and application connector profiles
    cat = client.get("/api/v1/connectors/catalog")
    assert cat.status_code == 200, cat.text
    cat_data = cat.json()
    assert len(cat_data["tracking_methods"]) == 6
    method_ids = {m["id"] for m in cat_data["tracking_methods"]}
    assert method_ids == {
        "official_provider_api",
        "opentelemetry",
        "litellm_gateway",
        "agent_tool_hook",
        "browser_extension",
        "desktop_companion",
    }
    assert any(a["application"] == "claude_code" for a in cat_data["applications"])
    assert len(cat_data["five_measurements_spec"]) == 5

    # 2. Automatic connector selection per application & plan tier
    cc = client.post("/api/v1/connectors/connect", headers=headers, json={"application": "claude_code"})
    assert cc.status_code == 201, cc.text
    assert cc.json()["selected_method"] == "opentelemetry"
    otel_token = cc.json()["config"]["otel_token"]

    cur_pers = client.post(
        "/api/v1/connectors/connect",
        headers=headers,
        json={"application": "cursor", "plan_tier": "personal"},
    )
    assert cur_pers.status_code == 201
    assert cur_pers.json()["selected_method"] == "agent_tool_hook"
    assert cur_pers.json()["coverage"]["tokens"] == "unavailable"

    cur_team = client.post(
        "/api/v1/connectors/connect",
        headers=headers,
        json={"application": "cursor", "plan_tier": "team", "api_key": "cur_admin_123"},
    )
    assert cur_team.status_code == 201
    assert cur_team.json()["selected_method"] == "official_provider_api"
    assert cur_team.json()["source"] == "api"

    cweb = client.post("/api/v1/connectors/connect", headers=headers, json={"application": "claude_web"})
    assert cweb.status_code == 201
    assert cweb.json()["selected_method"] == "browser_extension"
    assert cweb.json()["coverage"]["tokens"] == "unavailable"

    # 3. Helper script download for Claude Code
    helper = client.get(f"/api/v1/connectors/claude-code-helper.sh?token={otel_token}")
    assert helper.status_code == 200
    assert "CLAUDE_CODE_ENABLE_TELEMETRY=1" in helper.text

    # 4. Privacy guard rejects raw document body or prompt content
    bad_ev = client.post(
        "/api/v1/connectors/events",
        headers=headers,
        json={
            "event_type": "document_read",
            "application": "claude_code",
            "file_type": "pdf",
            "content": "secret pdf body",
        },
    )
    assert bad_ev.status_code == 400
    assert bad_ev.json()["code"] == "PRIVACY_VIOLATION"

    # 5. Ingest Section 4 & Section 3C multi-modal workload
    workload = [
        {"event_type": "source_code_read", "application": "claude_code", "source": "agent_tool_hook", "file_type": "py", "quantity": 20, "verified": True},
        {"event_type": "document_read", "application": "claude_code", "source": "agent_tool_hook", "file_type": "pdf", "quantity": 3, "verified": True},
        {"event_type": "image_uploaded", "application": "claude_code", "source": "agent_tool_hook", "media_type": "png", "quantity": 2, "verified": True},
        {"event_type": "image_generated", "application": "claude_code", "source": "agent_tool_hook", "media_type": "png", "quantity": 4, "verified": True},
        {
            "event_type": "model_request",
            "application": "claude_code",
            "source": "opentelemetry",
            "model_name": "claude-3-5-sonnet",
            "quantity": 40,
            "input_tokens": 380000,
            "output_tokens": 120000,
            "total_tokens": 500000,
            "verified": True,
        },
        {"event_type": "application_activity", "application": "claude_web", "source": "browser", "active_seconds": 2700, "quantity": 1},
        {"event_type": "prompt_submitted", "application": "claude_web", "source": "browser", "quantity": 15},
    ]
    ing = client.post("/api/v1/connectors/events", headers=headers, json={"events": workload})
    assert ing.status_code == 201, ing.text
    assert ing.json()["accepted_count"] == len(workload)

    # 6. Verify AI Usage Intelligence separates all 5 measurements accurately
    intel_res = client.get("/api/v1/connectors/intelligence", headers=headers)
    assert intel_res.status_code == 200, intel_res.text
    intel = intel_res.json()
    m = intel["measurements"]
    assert m["model_requests"]["value"] == 40
    assert m["model_requests"]["prompt_count_observed"] == 15
    assert m["tokens_consumed"]["value"] == 500000
    assert m["images_generated"]["value"] == 4
    assert m["images_generated"]["images_uploaded"] == 2
    assert m["documents_accessed"]["value"] == 23
    assert m["documents_accessed"]["source_code_reads"] == 20
    assert m["documents_accessed"]["document_reads"] == 3
    assert m["application_activity"]["active_minutes"] == 45

    apps_by_id = {row["application"]: row for row in intel["usage_by_application"]}
    assert apps_by_id["claude_code"]["metrics"]["total_tokens"] == 500000
    assert apps_by_id["claude_code"]["coverage"]["tokens"] == "reported"
    assert apps_by_id["claude_web"]["metrics"]["total_tokens"] is None
    assert apps_by_id["claude_web"]["coverage"]["tokens"] == "unavailable"

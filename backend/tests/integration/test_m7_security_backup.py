"""Milestone M7 integration tests: Compose modularity, Backup/Restore drill, Frontend & Accessibility (T077, T079, T080)."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

from fastapi.testclient import TestClient


ROOT_DIR = Path(__file__).resolve().parents[3]


def _load_backup_mod():
    script = ROOT_DIR / "infra" / "backups" / "backup_restore.py"
    spec = importlib.util.spec_from_file_location("backup_restore", script)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_t077_modular_compose_configs_pinned_no_core_profiles():
    compose_dir = ROOT_DIR / "infra" / "compose"
    files = [
        "compose.yaml",
        "compose.dev.yaml",
        "compose.ai.yaml",
        "compose.metering.yaml",
        "compose.analytics.yaml",
        "compose.observability.yaml",
        "compose.telemetry.yaml",
        "compose.production.yaml",
    ]
    for fname in files:
        fpath = compose_dir / fname
        assert fpath.exists(), f"Missing compose file: {fname}"
        txt = fpath.read_text(encoding="utf-8")
        assert ":latest" not in txt, f"Unpinned :latest tag found in {fname}"

    core_txt = (compose_dir / "compose.yaml").read_text(encoding="utf-8")
    assert "profiles:" not in core_txt
    assert "worker:" in core_txt
    assert "report-worker:" in core_txt


def test_t079_backup_and_clean_target_restore_parity(client: TestClient, tmp_path: Path):
    # Populate DB + rendered PDF report object
    reg = client.post(
        "/api/v1/auth/register",
        json={"email": "backup.owner@subsphere.io", "password": "StrongPassword123!", "full_name": "Backup Owner"},
    )
    assert reg.status_code == 201
    client.post("/api/v1/auth/verify-email", json={"token": reg.json()["verification_token"]})
    tok = client.post(
        "/api/v1/auth/login",
        json={"email": "backup.owner@subsphere.io", "password": "StrongPassword123!"},
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {tok}"}

    client.post(
        "/api/v1/personal/subscriptions",
        headers=headers,
        json={
            "service_name": "Notion Plus",
            "category": "Productivity",
            "price_minor": 1000,
            "currency": "USD",
            "billing_interval": "monthly",
            "next_renewal_date": "2026-12-05",
        },
    )
    org = client.post(
        "/api/v1/organizations",
        headers=headers,
        json={"name": "RestoreCo", "slug": "restore-co", "default_currency": "USD"},
    ).json()
    svc = client.post(
        "/api/v1/services",
        headers=headers,
        json={"organization_id": org["id"], "name": "BackupSvc", "slug": "backup-svc"},
    ).json()
    plan = client.post(
        f"/api/v1/services/{svc['id']}/plans",
        headers=headers,
        json={"name": "Starter", "slug": "starter", "description": "Starter"},
    ).json()
    pv = client.post(
        f"/api/v1/plans/{plan['id']}/versions",
        headers=headers,
        json={"price_minor": 2500, "currency": "USD", "billing_interval": "monthly", "metrics": []},
    ).json()
    contact = client.post(
        "/api/v1/subscribers",
        headers=headers,
        json={"organization_id": org["id"], "name": "Alice Buyer", "email": "alice@buyer.io"},
    ).json()
    sub = client.post(
        "/api/v1/subscriptions",
        headers=headers,
        json={"subscriber_contact_id": contact["id"], "plan_version_id": pv["id"], "starts_at": "2026-11-01T00:00:00Z"},
    ).json()
    client.post(
        f"/api/v1/invoices/{sub['invoice_id']}/payments",
        headers=headers,
        json={"amount_minor": 2500, "currency": "USD", "method": "bank_transfer", "idempotency_key": "bk-pay-1"},
    )
    rep = client.post(
        "/api/v1/reports",
        headers=headers,
        json={"organization_id": org["id"], "invoice_id": sub["invoice_id"], "report_type": "payment_receipt"},
    ).json()
    render = client.post(f"/api/v1/reports/{rep['id']}/render", headers=headers)
    assert render.status_code == 200

    # Execute backup & restore drill
    mod = _load_backup_mod()
    src_data_dir = Path(os.environ["SUBSPHERE_DATA_DIR"])
    archive_path = tmp_path / "subsphere_backup.tar.gz"
    manifest = mod.create_backup(archive_path=archive_path, data_dir=src_data_dir)
    assert manifest["tables"]["users"]["row_count"] == 1
    assert manifest["tables"]["personal_subscriptions"]["row_count"] == 1
    assert manifest["tables"]["payments"]["row_count"] == 1
    assert len(manifest["objects"]) >= 1

    restore_db = tmp_path / "restored.db"
    restore_data = tmp_path / "restored_data"
    prev_db_url = os.environ["DATABASE_URL"]
    try:
        res = mod.restore_and_verify(
            archive_path=archive_path,
            target_db_path=restore_db,
            target_data_dir=restore_data,
        )
    finally:
        os.environ["DATABASE_URL"] = prev_db_url

    assert res["verified"] is True
    assert res["verified_tables"] >= 20
    assert res["verified_objects"] == len(manifest["objects"])


def test_t080_frontend_two_view_build_and_accessibility():
    dist_index = ROOT_DIR / "frontend" / "dist" / "index.html"
    assert dist_index.exists(), "Frontend production build dist/index.html missing"

    app_tsx = (ROOT_DIR / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    ind_tsx = (ROOT_DIR / "frontend" / "src" / "components" / "IndividualView.tsx").read_text(encoding="utf-8")
    prov_tsx = (ROOT_DIR / "frontend" / "src" / "components" / "ProviderView.tsx").read_text(encoding="utf-8")
    api_ts = (ROOT_DIR / "frontend" / "src" / "api.ts").read_text(encoding="utf-8")
    css = (ROOT_DIR / "frontend" / "src" / "style.css").read_text(encoding="utf-8")

    # Strict two-view product design & accessible tabs/dialogs
    assert "Individual — Free" in app_tsx
    assert "Provider — Business" in app_tsx
    assert 'role="tablist"' in app_tsx
    assert 'role="dialog"' in ind_tsx and 'role="dialog"' in prov_tsx
    assert "Unavailable" in api_ts and "Unavailable" in ind_tsx
    assert "Recorded Cash Surplus" in prov_tsx
    assert "Synthetic Net Profit" not in prov_tsx and "Net Profit" not in prov_tsx
    assert ":focus-visible" in css
    assert "@media (max-width: 768px)" in css

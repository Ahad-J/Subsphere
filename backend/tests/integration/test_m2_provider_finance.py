"""M2 Integration Tests: Catalog, Immutable Plan Versions, Contacts, Payments, Overdue, Gym KPIs (T021-T035)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.db.engine import get_db
from app.main import app


def _setup_user(email: str, verified: bool = True) -> TestClient:
    c = TestClient(app)
    reg = c.post("/api/v1/auth/register", json={"email": email, "password": "Password123!", "full_name": email.split("@")[0]})
    if verified:
        c.post("/api/v1/auth/verify-email", json={"token": reg.json()["verification_token"]})
    c.post("/api/v1/auth/login", json={"email": email, "password": "Password123!"})
    return c


def test_t021_t022_immutable_plan_versions(isolated_db):
    owner = _setup_user("gym_owner@example.com")
    org = owner.post("/api/v1/organizations", json={"name": "FitZone Gym", "slug": "fitzone", "default_currency": "PKR"}).json()
    svc = owner.post("/api/v1/services", json={"organization_id": org["id"], "name": "Membership", "slug": "membership", "currency": "PKR"}).json()
    plan = owner.post(f"/api/v1/services/{svc['id']}/plans", json={"name": "Basic", "description": "Gym access"}).json()

    # T021: v1 at PKR 3,000 (300000 paisa)
    v1 = owner.post(f"/api/v1/plans/{plan['id']}/versions", json={"price_minor": 300000, "currency": "PKR", "billing_interval": "monthly"}).json()
    assert v1["version"] == 1
    assert v1["price_minor"] == 300000

    c1 = owner.post("/api/v1/subscribers", json={"organization_id": org["id"], "name": "Ali Ahmed", "email": "ali@example.com"}).json()
    sub1 = owner.post("/api/v1/subscriptions", json={"plan_version_id": v1["id"], "subscriber_contact_id": c1["id"], "seats": 1}).json()
    assert sub1["price_minor"] == 300000

    # T022: Modify published plan price -> creates v2, old subscription retains v1 price
    v2 = owner.patch(f"/api/v1/plans/{plan['id']}/versions/{v1['id']}", json={"price_minor": 350000}).json()
    assert v2["version"] == 2
    assert v2["price_minor"] == 350000
    assert v2["previous_version_preserved"] is True

    subs = owner.get(f"/api/v1/subscriptions?organization_id={org['id']}").json()["subscriptions"]
    assert subs[0]["price_minor"] == 300000
    assert subs[0]["plan_version"] == 1


def test_t023_t024_subscriber_contact_without_login_and_claim(isolated_db):
    owner = _setup_user("provider@example.com")
    member = _setup_user("ali.claim@example.com", verified=True)
    org = owner.post("/api/v1/organizations", json={"name": "Studio", "slug": "studio", "default_currency": "USD"}).json()

    # T023: Create subscriber contact without user account
    sc = owner.post("/api/v1/subscribers", json={"organization_id": org["id"], "name": "Ali", "email": "ali.claim@example.com"}).json()
    assert sc["linked_user_id"] is None

    with get_db(write=False) as db:
        u = db.fetchone("SELECT id FROM users WHERE email = 'ali.claim@example.com'")
        assert u is not None
        link_before = db.fetchone("SELECT id FROM customer_links WHERE subscriber_contact_id = %s", (sc["id"],))
        assert link_before is None

    # T024: Send claim invite and accept with verified identity
    inv = owner.post(f"/api/v1/subscribers/{sc['id']}/invite", json={"email": "ali.claim@example.com"}).json()
    claim = member.post(f"/api/v1/subscribers/invites/{inv['claim_token']}/claim")
    assert claim.status_code == 200
    assert claim.json()["claimed"] is True

    detail = owner.get(f"/api/v1/subscribers/{sc['id']}").json()
    assert detail["linked_user_id"] == claim.json()["user_id"]
    assert detail["name"] == "Ali"


def test_t025_to_t031_payment_rbac_idempotency_overdue(isolated_db):
    owner = _setup_user("owner_pay@example.com")
    staff = _setup_user("staff_pay@example.com")
    org = owner.post("/api/v1/organizations", json={"name": "PayOrg", "slug": "payorg", "default_currency": "PKR"}).json()
    inv_m = owner.post(f"/api/v1/organizations/{org['id']}/invites", json={"email": "staff_pay@example.com", "role": "staff"}).json()
    staff.post(f"/api/v1/organizations/invites/{inv_m['invite_token']}/accept")

    svc = owner.post("/api/v1/services", json={"organization_id": org["id"], "name": "Gym", "slug": "gym", "currency": "PKR"}).json()
    plan = owner.post(f"/api/v1/services/{svc['id']}/plans", json={"name": "Basic"}).json()
    v1 = owner.post(f"/api/v1/plans/{plan['id']}/versions", json={"price_minor": 300000, "currency": "PKR", "billing_interval": "monthly"}).json()
    sc = owner.post("/api/v1/subscribers", json={"organization_id": org["id"], "name": "Sara", "email": "sara@example.com"}).json()

    # T025: Paid subscription requested without payment -> pending + invoice issued
    sub = owner.post(
        "/api/v1/subscriptions",
        json={"plan_version_id": v1["id"], "subscriber_contact_id": sc["id"], "due_at": "2026-11-10T23:59:59Z"},
    ).json()
    assert sub["status"] == "pending"
    inv_id = sub["invoice_id"]

    # T027: Staff cannot confirm payment -> 403
    denied = staff.post(
        f"/api/v1/invoices/{inv_id}/payments",
        headers={"Idempotency-Key": "pay-staff-1"},
        json={"amount_minor": 300000, "currency": "PKR", "method": "cash"},
    )
    assert denied.status_code == 403

    # T029: Overpayment rejected -> 409
    over = owner.post(
        f"/api/v1/invoices/{inv_id}/payments",
        headers={"Idempotency-Key": "pay-over-1"},
        json={"amount_minor": 400000, "currency": "PKR", "method": "cash"},
    )
    assert over.status_code == 409

    # T026: Confirm full payment -> paid, active, receipt queued
    p1 = owner.post(
        f"/api/v1/invoices/{inv_id}/payments",
        headers={"Idempotency-Key": "pay-ok-1"},
        json={"amount_minor": 300000, "currency": "PKR", "method": "cash", "external_reference": "counter-41"},
    )
    assert p1.status_code == 201
    assert p1.json()["invoice_status"] == "paid"
    assert p1.json()["balance_due_minor"] == 0
    assert p1.json()["receipt_job_status"] == "queued"

    # T028: Replay identical Idempotency-Key -> same payment, no duplicate receipt
    p2 = owner.post(
        f"/api/v1/invoices/{inv_id}/payments",
        headers={"Idempotency-Key": "pay-ok-1"},
        json={"amount_minor": 300000, "currency": "PKR", "method": "cash", "external_reference": "counter-41"},
    )
    assert p2.json()["payment_id"] == p1.json()["payment_id"]
    assert p2.json()["duplicate"] is True

    # T031: Overdue worker ignores already paid invoice
    chk = owner.post(f"/api/v1/finance/check-overdue?organization_id={org['id']}").json()
    assert chk["marked_overdue"] == 0

    # T030: Unpaid past-due invoice marked overdue with single notification job
    sc2 = owner.post("/api/v1/subscribers", json={"organization_id": org["id"], "name": "Zain", "email": "zain@example.com"}).json()
    sub2 = owner.post(
        "/api/v1/subscriptions",
        json={"plan_version_id": v1["id"], "subscriber_contact_id": sc2["id"], "due_at": "2026-11-10T23:59:59Z"},
    ).json()
    chk1 = owner.post(f"/api/v1/finance/check-overdue?organization_id={org['id']}").json()
    assert chk1["marked_overdue"] == 1
    assert chk1["notices_queued"] == 1
    chk2 = owner.post(f"/api/v1/finance/check-overdue?organization_id={org['id']}").json()
    assert chk2["notices_queued"] == 0
    assert owner.get(f"/api/v1/invoices/{sub2['invoice_id']}").json()["status"] == "overdue"


def test_t032_to_t035_fitzone_gym_40_member_reconciliation(isolated_db):
    owner = _setup_user("fitzone_owner@example.com")
    org = owner.post("/api/v1/organizations", json={"name": "FitZone Gym", "slug": "fitzone-demo", "default_currency": "PKR", "business_type": "gym"}).json()
    svc = owner.post("/api/v1/services", json={"organization_id": org["id"], "name": "Membership", "slug": "membership", "currency": "PKR", "service_type": "gym"}).json()
    plan = owner.post(f"/api/v1/services/{svc['id']}/plans", json={"name": "Basic"}).json()
    v1 = owner.post(f"/api/v1/plans/{plan['id']}/versions", json={"price_minor": 300000, "currency": "PKR", "billing_interval": "monthly"}).json()

    for idx in range(40):
        sc = owner.post(
            "/api/v1/subscribers",
            json={"organization_id": org["id"], "name": f"Member {idx+1}", "email": f"m{idx+1}@gym.pk"},
        ).json()
        sub = owner.post(
            "/api/v1/subscriptions",
            json={"plan_version_id": v1["id"], "subscriber_contact_id": sc["id"], "due_at": "2026-11-10T23:59:59Z"},
        ).json()
        if idx < 30:
            pay = owner.post(
                f"/api/v1/invoices/{sub['invoice_id']}/payments",
                headers={"Idempotency-Key": f"gym-pay-{idx+1}"},
                json={"amount_minor": 300000, "currency": "PKR", "method": "cash", "external_reference": f"reg-{idx+1}"},
            )
            assert pay.status_code == 201

    # Mark the 10 unpaid past-due invoices overdue
    chk = owner.post(f"/api/v1/finance/check-overdue?organization_id={org['id']}").json()
    assert chk["marked_overdue"] == 10

    # Record PKR 15,000 (1,500,000 paisa) operating expense
    exp = owner.post(
        "/api/v1/finance/expenses",
        json={"organization_id": org["id"], "category": "utilities", "description": "Electricity & rent", "amount_minor": 1500000, "currency": "PKR"},
    )
    assert exp.status_code == 201

    kpis = owner.get(f"/api/v1/finance/kpis?organization_id={org['id']}&period=2026-11&currency=PKR").json()
    assert kpis["members_invoiced"] == 40
    assert kpis["members_overdue"] == 10
    assert kpis["invoiced_receivables_minor"] == 12000000   # PKR 120,000
    assert kpis["confirmed_collections_minor"] == 9000000   # PKR 90,000
    assert kpis["outstanding_receivables_minor"] == 3000000 # PKR 30,000
    assert kpis["confirmed_paid_expenses_minor"] == 1500000 # PKR 15,000
    assert kpis["recorded_cash_surplus_minor"] == 7500000   # PKR 75,000
    assert "Excludes expenses not recorded in SubSphere" in kpis["surplus_disclaimer"]

"""Provider finance: obligations (receivable/payable), invoices, immutable payments, expenses, KPIs."""
from __future__ import annotations

import hashlib
import json
import uuid

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field

from app.audit.service import record_audit_log
from app.auth.dependencies import get_current_principal, require_org_role
from app.core.clock import get_clock
from app.core.errors import DomainError
from app.db.engine import get_db
from app.notifications.dispatcher import dispatch_email_jobs

router = APIRouter(prefix="/api/v1", tags=["finance"])


class ObligationCreate(BaseModel):
    organization_id: str
    direction: str
    party_ref: str
    amount_minor: int = Field(..., ge=0)
    currency: str = Field(..., min_length=3, max_length=3)
    due_at: str
    description: str
    subscriber_contact_id: str | None = None
    create_invoice: bool = True


class PaymentCreate(BaseModel):
    amount_minor: int = Field(..., gt=0)
    currency: str = Field(..., min_length=3, max_length=3)
    method: str = "cash"
    paid_at: str | None = None
    external_reference: str = ""


class ExpenseCreate(BaseModel):
    organization_id: str
    category: str = "general"
    description: str
    amount_minor: int = Field(..., ge=0)
    currency: str = Field(..., min_length=3, max_length=3)
    paid_at: str | None = None


@router.post("/finance/obligations", status_code=201)
def create_obligation(payload: ObligationCreate, principal: dict = Depends(get_current_principal)):
    if payload.direction not in ("RECEIVABLE", "PAYABLE"):
        raise DomainError(422, "INVALID_DIRECTION", "direction must be RECEIVABLE or PAYABLE.")
    now_iso = get_clock().now_iso()
    ob_id = str(uuid.uuid4())
    inv_id = None
    doc_num = None
    with get_db(write=True) as db:
        require_org_role(db, principal, payload.organization_id, {"owner", "admin", "billing"})
        db.execute(
            """
            INSERT INTO financial_obligations (
                id, organization_id, subscriber_contact_id, direction, party_ref,
                description, amount_minor, currency, due_at, status, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'issued', %s)
            """,
            (
                ob_id,
                payload.organization_id,
                payload.subscriber_contact_id,
                payload.direction,
                payload.party_ref,
                payload.description,
                payload.amount_minor,
                payload.currency.upper(),
                payload.due_at,
                now_iso,
            ),
        )
        if payload.direction == "RECEIVABLE" and payload.create_invoice:
            inv_id = str(uuid.uuid4())
            doc_num = f"INV-{now_iso[:4]}-{uuid.uuid4().hex[:6].upper()}"
            snapshot = {
                "document_number": doc_num,
                "party_ref": payload.party_ref,
                "description": payload.description,
                "total_minor": payload.amount_minor,
                "currency": payload.currency.upper(),
                "due_at": payload.due_at,
            }
            db.execute(
                """
                INSERT INTO invoices (
                    id, organization_id, obligation_id, document_number, invoice_type,
                    total_minor, currency, snapshot_json, due_at, status, issued_at, created_at
                )
                VALUES (%s, %s, %s, %s, 'standard', %s, %s, %s, %s, 'issued', %s, %s)
                """,
                (
                    inv_id,
                    payload.organization_id,
                    ob_id,
                    doc_num,
                    payload.amount_minor,
                    payload.currency.upper(),
                    json.dumps(snapshot),
                    payload.due_at,
                    now_iso,
                    now_iso,
                ),
            )
            db.execute(
                """
                INSERT INTO invoice_lines (id, invoice_id, description, quantity, unit_price_minor, total_minor)
                VALUES (%s, %s, %s, 1, %s, %s)
                """,
                (str(uuid.uuid4()), inv_id, payload.description, payload.amount_minor, payload.amount_minor),
            )
    return {
        "id": ob_id,
        "organization_id": payload.organization_id,
        "direction": payload.direction,
        "amount_minor": payload.amount_minor,
        "currency": payload.currency.upper(),
        "status": "issued",
        "invoice_id": inv_id,
        "document_number": doc_num,
    }


@router.get("/finance/obligations", status_code=200)
def list_obligations(organization_id: str, direction: str | None = None, principal: dict = Depends(get_current_principal)):
    with get_db(write=False) as db:
        require_org_role(db, principal, organization_id, {"owner", "admin", "billing", "staff"})
        if direction:
            rows = db.fetchall(
                "SELECT * FROM financial_obligations WHERE organization_id = %s AND direction = %s ORDER BY due_at",
                (organization_id, direction),
            )
        else:
            rows = db.fetchall(
                "SELECT * FROM financial_obligations WHERE organization_id = %s ORDER BY due_at",
                (organization_id,),
            )
    return {"obligations": rows}


@router.get("/invoices", status_code=200)
def list_invoices(organization_id: str, status: str | None = None, principal: dict = Depends(get_current_principal)):
    with get_db(write=False) as db:
        require_org_role(db, principal, organization_id, {"owner", "admin", "billing", "staff"})
        if status:
            rows = db.fetchall(
                """
                SELECT i.*, fo.subscriber_contact_id, sc.name as subscriber_name, sc.email as subscriber_email
                FROM invoices i
                JOIN financial_obligations fo ON fo.id = i.obligation_id
                LEFT JOIN subscriber_contacts sc ON sc.id = fo.subscriber_contact_id
                WHERE i.organization_id = %s AND i.status = %s
                ORDER BY i.issued_at DESC
                """,
                (organization_id, status),
            )
        else:
            rows = db.fetchall(
                """
                SELECT i.*, fo.subscriber_contact_id, sc.name as subscriber_name, sc.email as subscriber_email
                FROM invoices i
                JOIN financial_obligations fo ON fo.id = i.obligation_id
                LEFT JOIN subscriber_contacts sc ON sc.id = fo.subscriber_contact_id
                WHERE i.organization_id = %s
                ORDER BY i.issued_at DESC
                """,
                (organization_id,),
            )
    enriched = [
        {
            **r,
            "invoice_number": r["document_number"],
            "amount_paid_minor": r["total_minor"] if r["status"] == "paid" else 0,
        }
        for r in rows
    ]
    return {"invoices": enriched}


@router.get("/invoices/{invoice_id}", status_code=200)
def get_invoice(invoice_id: str, principal: dict = Depends(get_current_principal)):
    with get_db(write=False) as db:
        inv = db.fetchone("SELECT * FROM invoices WHERE id = %s", (invoice_id,))
        if not inv:
            raise DomainError(404, "INVOICE_NOT_FOUND", "Invoice not found.")
        require_org_role(db, principal, inv["organization_id"], {"owner", "admin", "billing", "staff"})
        lines = db.fetchall("SELECT * FROM invoice_lines WHERE invoice_id = %s", (invoice_id,))
        payments = db.fetchall("SELECT * FROM payments WHERE invoice_id = %s", (invoice_id,))
    return {**inv, "lines": lines, "payments": payments}


@router.post("/invoices/{invoice_id}/payments", status_code=201)
def confirm_invoice_payment(
    invoice_id: str,
    payload: PaymentCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: dict = Depends(get_current_principal),
):
    idem_key = idempotency_key or f"pay-{invoice_id}-{uuid.uuid4().hex}"
    now_iso = get_clock().now_iso()
    paid_at = payload.paid_at or now_iso

    with get_db(write=True) as db:
        inv = db.fetchone(
            """
            SELECT i.*, fo.subscription_id, fo.subscriber_contact_id, o.name as org_name
            FROM invoices i
            JOIN financial_obligations fo ON fo.id = i.obligation_id
            JOIN organizations o ON o.id = i.organization_id
            WHERE i.id = %s
            """,
            (invoice_id,),
        )
        if not inv:
            raise DomainError(404, "INVOICE_NOT_FOUND", "Invoice not found.")

        # Role check: only owner, admin, billing can confirm payments (T027)
        require_org_role(db, principal, inv["organization_id"], {"owner", "admin", "billing"})

        # Idempotency check (T028)
        existing_pay = db.fetchone("SELECT * FROM payments WHERE idempotency_key = %s", (idem_key,))
        if existing_pay:
            if existing_pay["invoice_id"] != invoice_id or existing_pay["amount_minor"] != payload.amount_minor:
                raise DomainError(409, "IDEMPOTENCY_CONFLICT", "Idempotency-Key already used with different payment parameters.")
            rep = db.fetchone(
                "SELECT id, status FROM reports WHERE invoice_id = %s AND report_type = 'payment_receipt'",
                (invoice_id,),
            )
            return {
                "payment_id": existing_pay["id"],
                "invoice_id": invoice_id,
                "invoice_status": "paid",
                "amount_paid_minor": existing_pay["amount_minor"],
                "balance_due_minor": 0,
                "receipt_job_status": rep["status"] if rep else "queued",
                "receipt_report_id": rep["id"] if rep else None,
                "duplicate": True,
            }

        if inv["status"] == "paid":
            raise DomainError(409, "INVOICE_ALREADY_PAID", "Invoice has already been paid.")
        if inv["status"] == "void":
            raise DomainError(409, "INVOICE_VOID", "Cannot pay a voided invoice.")

        if payload.currency.upper() != inv["currency"]:
            raise DomainError(422, "CURRENCY_MISMATCH", f"Expected currency {inv['currency']}.")

        paid_rows = db.fetchall(
            "SELECT amount_minor FROM payments WHERE invoice_id = %s AND status = 'confirmed'",
            (invoice_id,),
        )
        already_paid = sum(r["amount_minor"] for r in paid_rows)
        remaining_due = inv["total_minor"] - already_paid

        # First release accepts exact full payment only and rejects overpayments/partials (T029)
        if payload.amount_minor > remaining_due:
            raise DomainError(409, "OVERPAYMENT_NOT_SUPPORTED", "Payment amount exceeds outstanding balance.", {"remaining_due_minor": remaining_due})
        if payload.amount_minor != remaining_due:
            raise DomainError(422, "PARTIAL_PAYMENT_NOT_SUPPORTED", "Full remaining balance payment is required.", {"remaining_due_minor": remaining_due})

        payment_id = str(uuid.uuid4())
        db.execute(
            """
            INSERT INTO payments (
                id, organization_id, invoice_id, amount_minor, currency, method,
                external_ref, idempotency_key, status, recorded_by_user_id, paid_at, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'confirmed', %s, %s, %s)
            """,
            (
                payment_id,
                inv["organization_id"],
                invoice_id,
                payload.amount_minor,
                payload.currency.upper(),
                payload.method,
                payload.external_reference,
                idem_key,
                principal["user"]["id"],
                paid_at,
                now_iso,
            ),
        )
        db.execute("UPDATE invoices SET status = 'paid' WHERE id = %s", (invoice_id,))
        db.execute("UPDATE financial_obligations SET status = 'paid' WHERE id = %s", (inv["obligation_id"],))

        if inv["subscription_id"]:
            db.execute(
                "UPDATE subscriptions SET status = 'active' WHERE id = %s AND status IN ('pending', 'past_due')",
                (inv["subscription_id"],),
            )

        # Create immutable payment receipt report job (T026, T053, T054)
        inv_snap = json.loads(inv["snapshot_json"])
        receipt_num = f"R-{now_iso[:4]}-{uuid.uuid4().hex[:6].upper()}"
        receipt_snapshot = {
            "receipt_number": receipt_num,
            "invoice_number": inv["document_number"],
            "org_name": inv["org_name"],
            "billed_to": inv_snap.get("subscriber_name") or inv_snap.get("party_ref") or "Subscriber",
            "plan_name": inv_snap.get("plan_name") or inv_snap.get("description") or "Membership",
            "period_start": inv_snap.get("period_start", ""),
            "period_end": inv_snap.get("period_end", ""),
            "total_minor": inv["total_minor"],
            "amount_paid_minor": payload.amount_minor,
            "balance_due_minor": 0,
            "currency": inv["currency"],
            "payment_method": payload.method,
            "payment_reference": payload.external_reference,
            "paid_at": paid_at,
            "issued_at": now_iso,
            "status": "CONFIRMED PAYMENT",
        }
        snap_json_str = json.dumps(receipt_snapshot, sort_keys=True)
        snap_hash = hashlib.sha256(snap_json_str.encode("utf-8")).hexdigest()
        report_id = str(uuid.uuid4())
        db.execute(
            """
            INSERT INTO reports (
                id, organization_id, account_id, invoice_id, report_type, document_number,
                snapshot_json, snapshot_hash, object_key, status, created_at
            )
            VALUES (%s, %s, NULL, %s, 'payment_receipt', %s, %s, %s, '', 'queued', %s)
            """,
            (report_id, inv["organization_id"], invoice_id, receipt_num, snap_json_str, snap_hash, now_iso),
        )

        # Queue payment confirmation email if subscriber contact has email
        if inv["subscriber_contact_id"]:
            sc = db.fetchone("SELECT name, email FROM subscriber_contacts WHERE id = %s", (inv["subscriber_contact_id"],))
            if sc and sc["email"]:
                logical_key = f"payment_confirmed:{payment_id}"
                db.execute(
                    """
                    INSERT INTO notification_jobs (
                        id, logical_key, account_id, organization_id, rule_id, channel, recipient,
                        subject, body, status, attempts, created_at, updated_at
                    )
                    VALUES (%s, %s, NULL, %s, NULL, 'email', %s, %s, %s, 'queued', 0, %s, %s)
                    """,
                    (
                        str(uuid.uuid4()),
                        logical_key,
                        inv["organization_id"],
                        sc["email"],
                        f"{inv['org_name']} payment receipt {receipt_num}",
                        f"Hello {sc['name']},\n\nWe have confirmed your payment of {inv['currency']} {payload.amount_minor / 100:.2f} for invoice {inv['document_number']}.\nReceipt: {receipt_num}.\n",
                        now_iso,
                        now_iso,
                    ),
                )

        # Write OutboxEvent in same SQL transaction
        outbox_id = str(uuid.uuid4())
        outbox_payload = json.dumps(
            {
                "id": outbox_id,
                "type": "invoice.paid",
                "version": 1,
                "occurred_at": now_iso,
                "data": {
                    "invoice_id": invoice_id,
                    "payment_id": payment_id,
                    "subscription_id": inv["subscription_id"],
                    "amount_minor": payload.amount_minor,
                    "currency": inv["currency"],
                },
            }
        )
        db.execute(
            """
            INSERT INTO outbox_events (id, aggregate_type, aggregate_id, event_type, payload_json, created_at)
            VALUES (%s, 'invoice', %s, 'invoice.paid', %s, %s)
            """,
            (outbox_id, invoice_id, outbox_payload, now_iso),
        )
        db.execute(
            """
            INSERT INTO outbox_deliveries (id, event_id, destination, status, attempts)
            VALUES (%s, %s, 'kafka', 'pending', 0)
            """,
            (str(uuid.uuid4()), outbox_id),
        )

        record_audit_log(
            db,
            actor_user_id=principal["user"]["id"],
            tenant_id=inv["organization_id"],
            action="payment.confirmed",
            resource_type="payment",
            resource_id=payment_id,
            metadata={"invoice_id": invoice_id, "amount_minor": payload.amount_minor, "currency": inv["currency"]},
        )

    return {
        "payment_id": payment_id,
        "invoice_id": invoice_id,
        "invoice_status": "paid",
        "amount_paid_minor": payload.amount_minor,
        "balance_due_minor": 0,
        "receipt_job_status": "queued",
        "receipt_report_id": report_id,
        "duplicate": False,
    }


@router.post("/finance/expenses", status_code=201)
def record_expense(payload: ExpenseCreate, principal: dict = Depends(get_current_principal)):
    now_iso = get_clock().now_iso()
    paid_at = payload.paid_at or now_iso
    exp_id = str(uuid.uuid4())
    with get_db(write=True) as db:
        require_org_role(db, principal, payload.organization_id, {"owner", "admin", "billing"})
        db.execute(
            """
            INSERT INTO expenses (id, organization_id, category, description, amount_minor, currency, paid_at, recorded_by_user_id, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                exp_id,
                payload.organization_id,
                payload.category,
                payload.description,
                payload.amount_minor,
                payload.currency.upper(),
                paid_at,
                principal["user"]["id"],
                now_iso,
            ),
        )
        record_audit_log(
            db,
            actor_user_id=principal["user"]["id"],
            tenant_id=payload.organization_id,
            action="expense.recorded",
            resource_type="expense",
            resource_id=exp_id,
            metadata={"amount_minor": payload.amount_minor, "currency": payload.currency.upper()},
        )
    return {
        "id": exp_id,
        "organization_id": payload.organization_id,
        "category": payload.category,
        "description": payload.description,
        "amount_minor": payload.amount_minor,
        "currency": payload.currency.upper(),
        "paid_at": paid_at,
    }


@router.get("/finance/expenses", status_code=200)
def list_expenses(organization_id: str, principal: dict = Depends(get_current_principal)):
    with get_db(write=False) as db:
        require_org_role(db, principal, organization_id, {"owner", "admin", "billing", "staff"})
        rows = db.fetchall(
            "SELECT * FROM expenses WHERE organization_id = %s ORDER BY paid_at DESC",
            (organization_id,),
        )
    return {"expenses": rows}


@router.post("/finance/check-overdue", status_code=200)
def check_overdue_obligations(organization_id: str | None = None, principal: dict = Depends(get_current_principal)):
    target_org_id = organization_id or principal.get("active_org_id")
    if not target_org_id and principal.get("memberships"):
        target_org_id = principal["memberships"][0]["organization_id"]
    if not target_org_id:
        raise DomainError(422, "ORGANIZATION_REQUIRED", "organization_id is required.")
    now_iso = get_clock().now_iso()
    with get_db(write=True) as db:
        require_org_role(db, principal, target_org_id, {"owner", "admin", "billing", "staff"})
        org = db.fetchone("SELECT name FROM organizations WHERE id = %s", (target_org_id,))
        candidates = db.fetchall(
            """
            SELECT i.id as invoice_id, i.document_number, i.total_minor, i.currency, i.due_at,
                   i.obligation_id, fo.subscription_id, fo.subscriber_contact_id
            FROM invoices i
            JOIN financial_obligations fo ON fo.id = i.obligation_id
            WHERE i.organization_id = %s AND i.status = 'issued' AND i.due_at < %s
            """,
            (target_org_id, now_iso),
        )
        marked = 0
        notices_queued = 0
        for row in candidates:
            db.execute("UPDATE invoices SET status = 'overdue' WHERE id = %s AND status = 'issued'", (row["invoice_id"],))
            db.execute(
                "UPDATE financial_obligations SET status = 'overdue' WHERE id = %s AND status = 'issued'",
                (row["obligation_id"],),
            )
            if row["subscription_id"]:
                db.execute(
                    "UPDATE subscriptions SET status = 'past_due' WHERE id = %s AND status IN ('pending', 'active')",
                    (row["subscription_id"],),
                )
            marked += 1
            if row["subscriber_contact_id"]:
                sc = db.fetchone("SELECT name, email FROM subscriber_contacts WHERE id = %s", (row["subscriber_contact_id"],))
                if sc and sc["email"]:
                    lkey = f"overdue:{row['invoice_id']}"
                    exists = db.fetchone("SELECT id FROM notification_jobs WHERE logical_key = %s", (lkey,))
                    if not exists:
                        db.execute(
                            """
                            INSERT INTO notification_jobs (
                                id, logical_key, account_id, organization_id, rule_id, channel, recipient,
                                subject, body, status, attempts, created_at, updated_at
                            )
                            VALUES (%s, %s, NULL, %s, NULL, 'email', %s, %s, %s, 'queued', 0, %s, %s)
                            """,
                            (
                                str(uuid.uuid4()),
                                lkey,
                                target_org_id,
                                sc["email"],
                                f"{org['name']} membership fee reminder",
                                f"Hello {sc['name']},\n\nYour {org['name']} fee of {row['currency']} {row['total_minor'] / 100:.2f} was due on {row['due_at'][:10]}.\nOur records currently show it as unpaid.\n",
                                now_iso,
                                now_iso,
                            ),
                        )
                        notices_queued += 1
    return {"marked_overdue": marked, "notices_queued": notices_queued, "checked_at": now_iso}


@router.get("/finance/kpis", status_code=200)
def get_finance_kpis(
    organization_id: str,
    currency: str | None = None,
    period: str | None = None,
    principal: dict = Depends(get_current_principal),
):
    with get_db(write=False) as db:
        require_org_role(db, principal, organization_id, {"owner", "admin", "billing", "staff"})
        org = db.fetchone("SELECT name, default_currency FROM organizations WHERE id = %s", (organization_id,))
        cur = (currency or org["default_currency"]).upper()

        invoices = db.fetchall(
            "SELECT * FROM invoices WHERE organization_id = %s AND currency = %s AND status != 'void'",
            (organization_id, cur),
        )
        pay_rows = db.fetchall(
            "SELECT * FROM payments WHERE organization_id = %s AND currency = %s AND status = 'confirmed'",
            (organization_id, cur),
        )
        exp_rows = db.fetchall(
            "SELECT * FROM expenses WHERE organization_id = %s AND currency = %s",
            (organization_id, cur),
        )
        payables_rows = db.fetchall(
            "SELECT * FROM financial_obligations WHERE organization_id = %s AND currency = %s AND direction = 'PAYABLE' AND status != 'void'",
            (organization_id, cur),
        )
        ext_subs = db.fetchall(
            "SELECT * FROM external_subscriptions WHERE organization_id = %s AND currency = %s AND status = 'active'",
            (organization_id, cur),
        )

    invoiced_minor = sum(i["total_minor"] for i in invoices)
    collected_minor = sum(p["amount_minor"] for p in pay_rows)
    outstanding_minor = sum(i["total_minor"] for i in invoices if i["status"] in ("issued", "overdue", "partially_paid"))
    overdue_count = sum(1 for i in invoices if i["status"] == "overdue")
    paid_expenses_minor = sum(e["amount_minor"] for e in exp_rows)
    recorded_surplus_minor = collected_minor - paid_expenses_minor
    payables_minor = sum(p["amount_minor"] for p in payables_rows if p["status"] != "paid") + sum(
        es["seats_purchased"] * es["unit_price_minor"] for es in ext_subs
    )

    return {
        "org": org["name"],
        "organization_id": organization_id,
        "period": period or get_clock().now().strftime("%Y-%m"),
        "currency": cur,
        "members_invoiced": len(invoices),
        "members_overdue": overdue_count,
        "overdue_invoice_count": overdue_count,
        "invoiced_receivables_minor": invoiced_minor,
        "confirmed_collections_minor": collected_minor,
        "outstanding_receivables_minor": outstanding_minor,
        "confirmed_paid_expenses_minor": paid_expenses_minor,
        "recorded_cash_surplus_minor": recorded_surplus_minor,
        "open_payables_minor": payables_minor,
        "by_currency": {
            cur: {
                "collected_minor": collected_minor,
                "open_receivables_minor": outstanding_minor,
                "payables_minor": payables_minor,
                "recorded_cash_surplus_minor": recorded_surplus_minor,
            }
        },
        "expense_coverage": "user_recorded_only",
        "surplus_disclaimer": "Excludes expenses not recorded in SubSphere",
        "calculated_at": get_clock().now_iso(),
    }


@router.post("/invoices/{invoice_id}/send-reminder", status_code=200)
def send_invoice_reminder(invoice_id: str, principal: dict = Depends(get_current_principal)):
    now_iso = get_clock().now_iso()
    with get_db(write=True) as db:
        inv = db.fetchone(
            """
            SELECT i.*, fo.subscriber_contact_id, o.name as org_name
            FROM invoices i
            JOIN organizations o ON o.id = i.organization_id
            LEFT JOIN financial_obligations fo ON fo.id = i.obligation_id
            WHERE i.id = %s
            """,
            (invoice_id,),
        )
        if not inv:
            raise DomainError(404, "INVOICE_NOT_FOUND", "Invoice not found.")
        require_org_role(db, principal, inv["organization_id"], {"owner", "admin", "billing", "staff"})

        recipient = None
        if inv.get("subscriber_contact_id"):
            sc = db.fetchone(
                "SELECT email, name FROM subscriber_contacts WHERE id = %s",
                (inv["subscriber_contact_id"],),
            )
            if sc and sc.get("email"):
                recipient = sc["email"].lower()
        if not recipient:
            snap = json.loads(inv.get("snapshot_json") or "{}")
            recipient = (snap.get("email") or snap.get("recipient_email") or "").strip().lower() or None
        if not recipient:
            raise DomainError(422, "NO_RECIPIENT_EMAIL", "Subscriber contact does not have an email address.")

        job_id = str(uuid.uuid4())
        subject = f"Payment Reminder — {inv['org_name']} Invoice {inv['document_number']}"
        body = (
            f"Hello,\n\n"
            f"This is a reminder from {inv['org_name']} regarding invoice {inv['document_number']}.\n"
            f"Amount: {inv['currency']} {inv['total_minor'] / 100:,.2f} (Status: {inv['status']})\n"
            f"Due Date: {inv['due_at'][:10]}\n"
        )
        db.execute(
            """
            INSERT INTO notification_jobs (
                id, logical_key, account_id, organization_id, rule_id, channel,
                recipient, subject, body, status, attempts, created_at, updated_at
            )
            VALUES (%s, %s, NULL, %s, NULL, 'email', %s, %s, %s, 'queued', 0, %s, %s)
            """,
            (
                job_id,
                f"invoice_reminder:{invoice_id}:{now_iso[:10]}:{job_id[:8]}",
                inv["organization_id"],
                recipient,
                subject,
                body,
                now_iso,
                now_iso,
            ),
        )
        dispatch_res = dispatch_email_jobs(db, organization_id=inv["organization_id"])
        job = db.fetchone("SELECT status, last_error FROM notification_jobs WHERE id = %s", (job_id,))
        record_audit_log(
            db,
            actor_user_id=principal["user"]["id"],
            tenant_id=inv["organization_id"],
            action="invoice.reminder_sent",
            resource_type="invoice",
            resource_id=invoice_id,
            metadata={"recipient": recipient, "job_id": job_id, "status": job["status"]},
        )
    return {
        "invoice_id": invoice_id,
        "document_number": inv["document_number"],
        "recipient": recipient,
        "job_id": job_id,
        "status": job["status"],
        "last_error": job["last_error"],
        "dispatch_summary": dispatch_res,
    }

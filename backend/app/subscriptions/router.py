"""Provider subscriptions, periods, renewals, and seat allocation endpoints."""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.audit.service import record_audit_log
from app.auth.dependencies import get_current_principal, require_org_role
from app.core.clock import get_clock
from app.core.errors import DomainError
from app.db.engine import get_db
from app.personal.domain import next_renewal_date

router = APIRouter(prefix="/api/v1/subscriptions", tags=["subscriptions"])


class SubscriptionCreate(BaseModel):
    plan_version_id: str
    subscriber_contact_id: str | None = None
    buyer_account_id: str | None = None
    seats: int = Field(default=1, ge=1)
    starts_at: str | None = None
    due_at: str | None = None
    auto_renew: bool = True


class CancelRequest(BaseModel):
    effective: str = "end_of_period"
    reason: str = ""


class SeatAllocationCreate(BaseModel):
    membership_id: str


def _compute_period_bounds(starts_at_str: str | None, billing_interval: str) -> tuple[str, str, int]:
    now = get_clock().now()
    if starts_at_str:
        start_dt = datetime.fromisoformat(starts_at_str.replace("Z", "+00:00"))
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)
    else:
        start_dt = now
    anchor = start_dt.day
    next_d = next_renewal_date(anchor, start_dt.date(), billing_interval)
    if next_d is None:
        end_dt = datetime(2099, 12, 31, tzinfo=timezone.utc)
    else:
        end_dt = datetime(next_d.year, next_d.month, next_d.day, start_dt.hour, start_dt.minute, start_dt.second, tzinfo=timezone.utc)
    return (
        start_dt.isoformat().replace("+00:00", "Z"),
        end_dt.isoformat().replace("+00:00", "Z"),
        anchor,
    )


@router.post("", status_code=201)
def create_subscription(payload: SubscriptionCreate, principal: dict = Depends(get_current_principal)):
    now_iso = get_clock().now_iso()
    sub_id = str(uuid.uuid4())
    period_id = str(uuid.uuid4())

    with get_db(write=True) as db:
        pv = db.fetchone(
            """
            SELECT pv.*, p.name as plan_name, p.service_id, s.organization_id, s.name as service_name
            FROM plan_versions pv
            JOIN plans p ON p.id = pv.plan_id
            JOIN services s ON s.id = p.service_id
            WHERE pv.id = %s
            """,
            (payload.plan_version_id,),
        )
        if not pv:
            raise DomainError(404, "PLAN_VERSION_NOT_FOUND", "Plan version not found.")

        provider_org_id = pv["organization_id"]
        # Actor must either be authorized provider staff/admin/owner/billing OR buyer on their own account
        is_provider = db.fetchone(
            "SELECT id, role FROM memberships WHERE organization_id = %s AND user_id = %s AND status = 'active'",
            (provider_org_id, principal["user"]["id"]),
        )
        if not is_provider and payload.buyer_account_id != principal["personal_account_id"]:
            raise DomainError(403, "FORBIDDEN", "Not authorized to create subscription for this organization or buyer.")

        contact_name = "Subscriber"
        if payload.subscriber_contact_id:
            sc = db.fetchone(
                "SELECT id, organization_id, name FROM subscriber_contacts WHERE id = %s",
                (payload.subscriber_contact_id,),
            )
            if not sc or sc["organization_id"] != provider_org_id:
                raise DomainError(404, "SUBSCRIBER_NOT_FOUND", "Subscriber contact not found in organization.")
            contact_name = sc["name"]

        p_start, p_end, anchor = _compute_period_bounds(payload.starts_at, pv["billing_interval"])
        total_minor = pv["price_minor"] * payload.seats
        sub_status = "pending" if total_minor > 0 else "active"

        db.execute(
            """
            INSERT INTO subscriptions (
                id, buyer_account_id, provider_org_id, subscriber_contact_id, plan_version_id,
                quantity, status, anchor_day, period_start, period_end, auto_renew, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                sub_id,
                payload.buyer_account_id,
                provider_org_id,
                payload.subscriber_contact_id,
                pv["id"],
                payload.seats,
                sub_status,
                anchor,
                p_start,
                p_end,
                1 if payload.auto_renew else 0,
                now_iso,
            ),
        )
        db.execute(
            """
            INSERT INTO subscription_periods (id, subscription_id, period_start, period_end, status, created_at)
            VALUES (%s, %s, %s, %s, 'active', %s)
            """,
            (period_id, sub_id, p_start, p_end, now_iso),
        )

        # Initialize usage counters for plan metric limits
        limits = db.fetchall(
            "SELECT metric_id, limit_value FROM plan_metric_limits WHERE plan_version_id = %s",
            (pv["id"],),
        )
        for lim in limits:
            db.execute(
                """
                INSERT INTO usage_counters (id, subscription_period_id, metric_id, consumed, updated_at)
                VALUES (%s, %s, %s, 0, %s)
                """,
                (str(uuid.uuid4()), period_id, lim["metric_id"], now_iso),
            )

        invoice_id = None
        obligation_id = None
        doc_number = None
        if total_minor > 0:
            obligation_id = str(uuid.uuid4())
            invoice_id = str(uuid.uuid4())
            due_at = payload.due_at or p_start
            doc_number = f"INV-{now_iso[:4]}-{uuid.uuid4().hex[:6].upper()}"
            snapshot = {
                "document_number": doc_number,
                "service_name": pv["service_name"],
                "plan_name": pv["plan_name"],
                "plan_version": pv["version"],
                "subscriber_name": contact_name,
                "period_start": p_start,
                "period_end": p_end,
                "quantity": payload.seats,
                "unit_price_minor": pv["price_minor"],
                "total_minor": total_minor,
                "currency": pv["currency"],
            }
            db.execute(
                """
                INSERT INTO financial_obligations (
                    id, organization_id, subscription_id, subscriber_contact_id, direction,
                    party_ref, description, amount_minor, currency, due_at, status, created_at
                )
                VALUES (%s, %s, %s, %s, 'RECEIVABLE', %s, %s, %s, %s, %s, 'issued', %s)
                """,
                (
                    obligation_id,
                    provider_org_id,
                    sub_id,
                    payload.subscriber_contact_id,
                    payload.subscriber_contact_id or payload.buyer_account_id or sub_id,
                    f"{pv['service_name']} — {pv['plan_name']} (v{pv['version']})",
                    total_minor,
                    pv["currency"],
                    due_at,
                    now_iso,
                ),
            )
            db.execute(
                """
                INSERT INTO invoices (
                    id, organization_id, obligation_id, subscription_period_id, document_number,
                    invoice_type, total_minor, currency, snapshot_json, due_at, status, issued_at, created_at
                )
                VALUES (%s, %s, %s, %s, %s, 'standard', %s, %s, %s, %s, 'issued', %s, %s)
                """,
                (
                    invoice_id,
                    provider_org_id,
                    obligation_id,
                    period_id,
                    doc_number,
                    total_minor,
                    pv["currency"],
                    json.dumps(snapshot),
                    due_at,
                    now_iso,
                    now_iso,
                ),
            )
            db.execute(
                """
                INSERT INTO invoice_lines (id, invoice_id, description, quantity, unit_price_minor, total_minor)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (str(uuid.uuid4()), invoice_id, f"{pv['plan_name']} ({pv['billing_interval']})", payload.seats, pv["price_minor"], total_minor),
            )

        outbox_id = str(uuid.uuid4())
        db.execute(
            """
            INSERT INTO outbox_events (id, aggregate_type, aggregate_id, event_type, payload_json, created_at)
            VALUES (%s, 'subscription', %s, 'subscription.created', %s, %s)
            """,
            (outbox_id, sub_id, json.dumps({"subscription_id": sub_id, "status": sub_status}), now_iso),
        )
        record_audit_log(
            db,
            actor_user_id=principal["user"]["id"],
            tenant_id=provider_org_id,
            action="subscription.created",
            resource_type="subscription",
            resource_id=sub_id,
            metadata={"status": sub_status, "plan_version_id": pv["id"]},
        )

    return {
        "id": sub_id,
        "provider_org_id": provider_org_id,
        "plan_version_id": pv["id"],
        "plan_version_number": pv["version"],
        "price_minor": pv["price_minor"],
        "currency": pv["currency"],
        "quantity": payload.seats,
        "status": sub_status,
        "period_id": period_id,
        "period_start": p_start,
        "period_end": p_end,
        "invoice_id": invoice_id,
        "invoice_number": doc_number,
        "obligation_id": obligation_id,
    }


@router.get("", status_code=200)
def list_subscriptions(organization_id: str, principal: dict = Depends(get_current_principal)):
    with get_db(write=False) as db:
        require_org_role(db, principal, organization_id, {"owner", "admin", "billing", "staff", "developer"})
        rows = db.fetchall(
            """
            SELECT s.*, pv.price_minor, pv.currency, pv.billing_interval, pv.version as plan_version,
                   p.name as plan_name, sv.name as service_name, sc.name as subscriber_name
            FROM subscriptions s
            JOIN plan_versions pv ON pv.id = s.plan_version_id
            JOIN plans p ON p.id = pv.plan_id
            JOIN services sv ON sv.id = p.service_id
            LEFT JOIN subscriber_contacts sc ON sc.id = s.subscriber_contact_id
            WHERE s.provider_org_id = %s
            ORDER BY s.created_at DESC
            """,
            (organization_id,),
        )
    return {"subscriptions": rows}


@router.post("/{sub_id}/cancel", status_code=200)
def cancel_subscription(sub_id: str, payload: CancelRequest, principal: dict = Depends(get_current_principal)):
    now_iso = get_clock().now_iso()
    with get_db(write=True) as db:
        sub = db.fetchone("SELECT * FROM subscriptions WHERE id = %s", (sub_id,))
        if not sub:
            raise DomainError(404, "SUBSCRIPTION_NOT_FOUND", "Subscription not found.")
        require_org_role(db, principal, sub["provider_org_id"], {"owner", "admin", "billing"})
        if sub["status"] in ("canceled", "expired"):
            raise DomainError(409, "INVALID_TRANSITION", f"Cannot cancel subscription in state '{sub['status']}'.")
        new_status = "canceled" if payload.effective == "now" else sub["status"]
        db.execute(
            "UPDATE subscriptions SET status = %s, auto_renew = 0, canceled_at = %s WHERE id = %s",
            (new_status, now_iso, sub_id),
        )
        record_audit_log(
            db,
            actor_user_id=principal["user"]["id"],
            tenant_id=sub["provider_org_id"],
            action="subscription.canceled",
            resource_type="subscription",
            resource_id=sub_id,
            metadata={"effective": payload.effective, "reason": payload.reason},
        )
    return {"id": sub_id, "status": new_status, "auto_renew": False, "canceled_at": now_iso}


@router.post("/{sub_id}/seat-allocations", status_code=201)
def allocate_subscription_seat(sub_id: str, payload: SeatAllocationCreate, principal: dict = Depends(get_current_principal)):
    now_iso = get_clock().now_iso()
    alloc_id = str(uuid.uuid4())
    with get_db(write=True) as db:
        sub = db.fetchone(
            """
            SELECT s.*, pv.max_seats
            FROM subscriptions s
            JOIN plan_versions pv ON pv.id = s.plan_version_id
            WHERE s.id = %s
            """,
            (sub_id,),
        )
        if not sub:
            raise DomainError(404, "SUBSCRIPTION_NOT_FOUND", "Subscription not found.")
        require_org_role(db, principal, sub["provider_org_id"], {"owner", "admin", "billing"})
        mem = db.fetchone("SELECT id, organization_id FROM memberships WHERE id = %s", (payload.membership_id,))
        if not mem or mem["organization_id"] != sub["provider_org_id"]:
            raise DomainError(403, "WRONG_ORG_MEMBER", "Employee membership does not belong to this organization.")

        active_count_row = db.fetchone(
            "SELECT COUNT(*) as cnt FROM seat_allocations WHERE subscription_id = %s AND revoked_at IS NULL",
            (sub_id,),
        )
        capacity = max(sub["quantity"], sub["max_seats"])
        if active_count_row["cnt"] >= capacity:
            raise DomainError(409, "SEAT_LIMIT_EXCEEDED", "All purchased seats are currently allocated.", {"capacity": capacity})

        existing = db.fetchone(
            "SELECT id FROM seat_allocations WHERE subscription_id = %s AND membership_id = %s AND revoked_at IS NULL",
            (sub_id, payload.membership_id),
        )
        if existing:
            raise DomainError(409, "SEAT_ALREADY_ALLOCATED", "Member already holds an active seat.")

        db.execute(
            """
            INSERT INTO seat_allocations (id, subscription_id, external_subscription_id, organization_id, membership_id, assigned_at)
            VALUES (%s, %s, NULL, %s, %s, %s)
            """,
            (alloc_id, sub_id, sub["provider_org_id"], payload.membership_id, now_iso),
        )
    return {"id": alloc_id, "subscription_id": sub_id, "membership_id": payload.membership_id, "assigned_at": now_iso}


@router.delete("/{sub_id}/seat-allocations/{allocation_id}", status_code=200)
def revoke_subscription_seat(sub_id: str, allocation_id: str, principal: dict = Depends(get_current_principal)):
    now_iso = get_clock().now_iso()
    with get_db(write=True) as db:
        alloc = db.fetchone(
            "SELECT * FROM seat_allocations WHERE id = %s AND subscription_id = %s",
            (allocation_id, sub_id),
        )
        if not alloc:
            raise DomainError(404, "ALLOCATION_NOT_FOUND", "Seat allocation not found.")
        require_org_role(db, principal, alloc["organization_id"], {"owner", "admin", "billing"})
        db.execute("UPDATE seat_allocations SET revoked_at = %s WHERE id = %s", (now_iso, allocation_id))
    return {"id": allocation_id, "revoked_at": now_iso}

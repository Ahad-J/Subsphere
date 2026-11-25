"""Subscriber CRM contacts and verified claim-invitation workflow."""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, EmailStr, Field

from app.audit.service import record_audit_log
from app.auth.dependencies import get_current_principal, require_org_role
from app.core.clock import get_clock
from app.core.config import get_settings
from app.core.errors import DomainError
from app.core.security import generate_token, hash_token
from app.db.engine import get_db

router = APIRouter(prefix="/api/v1/subscribers", tags=["subscribers"])
provider_subscribers_router = APIRouter(prefix="/api/v1/provider", tags=["subscribers"])

VALID_SUBSCRIBER_STATUSES = {"active", "paused", "canceled"}


class SubscriberCreate(BaseModel):
    organization_id: str
    name: str = Field(..., min_length=1, max_length=120)
    email: EmailStr | None = None
    phone_e164: str | None = None
    notes: str = ""
    status: str = "active"
    plan_name: str = ""
    fee_minor: int | None = Field(default=None, ge=0)
    currency: str = "USD"
    next_due_date: str | None = None


class SubscriberPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    email: EmailStr | None = None
    phone_e164: str | None = None
    notes: str | None = None
    status: str | None = None
    plan_name: str | None = None
    fee_minor: int | None = Field(default=None, ge=0)
    currency: str | None = None
    next_due_date: str | None = None
    version: int | None = None


class SubscriberInviteRequest(BaseModel):
    email: EmailStr
    role: str = "subscriber"


@router.post("", status_code=201)
def create_subscriber_contact(payload: SubscriberCreate, principal: dict = Depends(get_current_principal)):
    if payload.status not in VALID_SUBSCRIBER_STATUSES:
        raise DomainError(422, "INVALID_STATUS", "Subscriber status must be active, paused, or canceled.")
    currency = payload.currency.strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise DomainError(422, "INVALID_CURRENCY", "Currency must be a 3-letter ISO code.")
    if payload.next_due_date is not None:
        try:
            date.fromisoformat(payload.next_due_date)
        except ValueError as exc:
            raise DomainError(422, "INVALID_DATE", "next_due_date must be YYYY-MM-DD.") from exc
    now_iso = get_clock().now_iso()
    sub_id = str(uuid.uuid4())
    with get_db(write=True) as db:
        require_org_role(db, principal, payload.organization_id, {"owner", "admin", "billing", "staff"})
        db.execute(
            """
            INSERT INTO subscriber_contacts
            (id, organization_id, name, email, phone_e164, notes, status, plan_name, fee_minor, currency, next_due_date, version, updated_at, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s)
            """,
            (
                sub_id,
                payload.organization_id,
                payload.name.strip(),
                payload.email.lower() if payload.email else None,
                payload.phone_e164,
                payload.notes,
                payload.status,
                payload.plan_name.strip(),
                payload.fee_minor if payload.fee_minor is not None else 0,
                currency,
                payload.next_due_date,
                now_iso,
                now_iso,
            ),
        )
        record_audit_log(
            db,
            actor_user_id=principal["user"]["id"],
            tenant_id=payload.organization_id,
            action="subscriber_contact.created",
            resource_type="subscriber_contact",
            resource_id=sub_id,
        )
    return {
        "id": sub_id,
        "organization_id": payload.organization_id,
        "name": payload.name.strip(),
        "email": payload.email.lower() if payload.email else None,
        "phone_e164": payload.phone_e164,
        "notes": payload.notes,
        "status": payload.status,
        "plan_name": payload.plan_name.strip(),
        "fee_minor": payload.fee_minor if payload.fee_minor is not None else 0,
        "currency": currency,
        "next_due_date": payload.next_due_date,
        "version": 1,
        "updated_at": now_iso,
        "linked_user_id": None,
        "created_at": now_iso,
    }


@router.get("", status_code=200)
def list_subscribers(organization_id: str, principal: dict = Depends(get_current_principal)):
    with get_db(write=False) as db:
        require_org_role(db, principal, organization_id, {"owner", "admin", "billing", "staff", "developer"})
        rows = db.fetchall(
            """
            SELECT sc.*, cl.user_id as linked_user_id, cl.linked_at
            FROM subscriber_contacts sc
            LEFT JOIN customer_links cl ON cl.subscriber_contact_id = sc.id
            WHERE sc.organization_id = %s
            ORDER BY sc.created_at DESC
            """,
            (organization_id,),
        )
    return {"subscribers": rows}


@router.get("/{subscriber_id}", status_code=200)
def get_subscriber(subscriber_id: str, principal: dict = Depends(get_current_principal)):
    with get_db(write=False) as db:
        row = db.fetchone(
            """
            SELECT sc.*, cl.user_id as linked_user_id, cl.linked_at
            FROM subscriber_contacts sc
            LEFT JOIN customer_links cl ON cl.subscriber_contact_id = sc.id
            WHERE sc.id = %s
            """,
            (subscriber_id,),
        )
        if not row:
            raise DomainError(404, "SUBSCRIBER_NOT_FOUND", "Subscriber contact not found.")
        require_org_role(db, principal, row["organization_id"], {"owner", "admin", "billing", "staff", "developer"})
        subs = db.fetchall(
            """
            SELECT s.*, pv.price_minor, pv.currency, pv.billing_interval, p.name as plan_name
            FROM subscriptions s
            JOIN plan_versions pv ON pv.id = s.plan_version_id
            JOIN plans p ON p.id = pv.plan_id
            WHERE s.subscriber_contact_id = %s
            """,
            (subscriber_id,),
        )
        invoices = db.fetchall(
            """
            SELECT i.*
            FROM invoices i
            JOIN financial_obligations fo ON fo.id = i.obligation_id
            WHERE fo.subscriber_contact_id = %s
            ORDER BY i.issued_at DESC
            """,
            (subscriber_id,),
        )
    return {**row, "subscriptions": subs, "invoices": invoices}


@router.patch("/{subscriber_id}", status_code=200)
def patch_subscriber(
    subscriber_id: str,
    payload: SubscriberPatch,
    principal: dict = Depends(get_current_principal),
):
    now_iso = get_clock().now_iso()
    with get_db(write=True) as db:
        existing = db.fetchone("SELECT * FROM subscriber_contacts WHERE id = %s", (subscriber_id,))
        if not existing:
            raise DomainError(404, "SUBSCRIBER_NOT_FOUND", "Subscriber contact not found.")
        require_org_role(db, principal, existing["organization_id"], {"owner", "admin", "billing", "staff"})

        cur_version = existing.get("version") or 1
        if payload.version is not None and payload.version != cur_version:
            raise DomainError(409, "VERSION_CONFLICT", "Subscriber contact was modified concurrently; reload and retry.")

        updates: dict[str, object] = {}
        if payload.name is not None:
            updates["name"] = payload.name.strip()
        if payload.email is not None:
            updates["email"] = payload.email.lower()
        if payload.phone_e164 is not None:
            updates["phone_e164"] = payload.phone_e164
        if payload.notes is not None:
            updates["notes"] = payload.notes
        if payload.status is not None:
            if payload.status not in VALID_SUBSCRIBER_STATUSES:
                raise DomainError(422, "INVALID_STATUS", "Subscriber status must be active, paused, or canceled.")
            updates["status"] = payload.status
        if payload.plan_name is not None:
            updates["plan_name"] = payload.plan_name.strip()
        if payload.fee_minor is not None:
            updates["fee_minor"] = payload.fee_minor
        if payload.currency is not None:
            cur = payload.currency.strip().upper()
            if len(cur) != 3 or not cur.isalpha():
                raise DomainError(422, "INVALID_CURRENCY", "Currency must be a 3-letter ISO code.")
            updates["currency"] = cur
        if payload.next_due_date is not None:
            try:
                date.fromisoformat(payload.next_due_date)
            except ValueError as exc:
                raise DomainError(422, "INVALID_DATE", "next_due_date must be YYYY-MM-DD.") from exc
            updates["next_due_date"] = payload.next_due_date

        before = {k: existing.get(k) for k in updates}
        if updates:
            set_clauses = [f"{col} = %s" for col in updates] + ["version = %s", "updated_at = %s"]
            params = list(updates.values()) + [cur_version + 1, now_iso, subscriber_id]
            db.execute(f"UPDATE subscriber_contacts SET {', '.join(set_clauses)} WHERE id = %s", tuple(params))
            record_audit_log(
                db,
                actor_user_id=principal["user"]["id"],
                tenant_id=existing["organization_id"],
                action="subscriber_contact.updated",
                resource_type="subscriber_contact",
                resource_id=subscriber_id,
                metadata={"before": before, "after": updates},
            )

        updated = db.fetchone(
            """
            SELECT sc.*, cl.user_id as linked_user_id, cl.linked_at
            FROM subscriber_contacts sc
            LEFT JOIN customer_links cl ON cl.subscriber_contact_id = sc.id
            WHERE sc.id = %s
            """,
            (subscriber_id,),
        )
        subs = db.fetchall(
            """
            SELECT s.*, pv.price_minor, pv.currency, pv.billing_interval, p.name as plan_name
            FROM subscriptions s
            JOIN plan_versions pv ON pv.id = s.plan_version_id
            JOIN plans p ON p.id = pv.plan_id
            WHERE s.subscriber_contact_id = %s
            """,
            (subscriber_id,),
        )
        invoices = db.fetchall(
            """
            SELECT i.*
            FROM invoices i
            JOIN financial_obligations fo ON fo.id = i.obligation_id
            WHERE fo.subscriber_contact_id = %s
            ORDER BY i.issued_at DESC
            """,
            (subscriber_id,),
        )
    return {**updated, "subscriptions": subs, "invoices": invoices}


@provider_subscribers_router.get("/subscribers", status_code=200)
def provider_list_subscribers(
    organization_id: str = Query(..., alias="organization_id"),
    principal: dict = Depends(get_current_principal),
):
    return list_subscribers(organization_id=organization_id, principal=principal)


@provider_subscribers_router.get("/subscribers/{subscriber_id}", status_code=200)
def provider_get_subscriber(subscriber_id: str, principal: dict = Depends(get_current_principal)):
    return get_subscriber(subscriber_id=subscriber_id, principal=principal)


@provider_subscribers_router.patch("/subscribers/{subscriber_id}", status_code=200)
def provider_patch_subscriber(
    subscriber_id: str,
    payload: SubscriberPatch,
    principal: dict = Depends(get_current_principal),
):
    return patch_subscriber(subscriber_id=subscriber_id, payload=payload, principal=principal)


@router.post("/{subscriber_id}/invite", status_code=201)
def invite_subscriber_to_claim(
    subscriber_id: str,
    payload: SubscriberInviteRequest | None = None,
    principal: dict = Depends(get_current_principal),
):
    now = get_clock().now()
    now_iso = get_clock().now_iso()
    expires_iso = (now + timedelta(days=7)).isoformat().replace("+00:00", "Z")
    raw_token = generate_token(24)
    invite_id = str(uuid.uuid4())

    with get_db(write=True) as db:
        sc = db.fetchone("SELECT id, organization_id, name, email FROM subscriber_contacts WHERE id = %s", (subscriber_id,))
        if not sc:
            raise DomainError(404, "SUBSCRIBER_NOT_FOUND", "Subscriber contact not found.")
        require_org_role(db, principal, sc["organization_id"], {"owner", "admin", "billing", "staff"})
        target_email = (payload.email if payload else (sc.get("email") or "subscriber@example.com")).lower()
        target_role = payload.role if payload else "subscriber"
        db.execute(
            """
            INSERT INTO invitations (id, organization_id, subscriber_contact_id, email, role, token_hash, expires_at, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                invite_id,
                sc["organization_id"],
                subscriber_id,
                target_email,
                target_role,
                hash_token(raw_token),
                expires_iso,
                now_iso,
            ),
        )
    resp = {
        "id": invite_id,
        "subscriber_contact_id": subscriber_id,
        "email": target_email,
        "status": "pending",
        "expires_at": expires_iso,
    }
    if get_settings().app_env != "production":
        resp["claim_token"] = raw_token
        resp["invite_token"] = raw_token
    return resp


@router.post("/invites/{token}/claim", status_code=200)
def claim_subscriber_invitation(token: str, principal: dict = Depends(get_current_principal)):
    t_hash = hash_token(token)
    now_iso = get_clock().now_iso()
    user_id = principal["user"]["id"]
    with get_db(write=True) as db:
        inv = db.fetchone(
            """
            SELECT id, organization_id, subscriber_contact_id, email, expires_at, accepted_at
            FROM invitations
            WHERE token_hash = %s AND subscriber_contact_id IS NOT NULL
            """,
            (t_hash,),
        )
        if not inv:
            raise DomainError(404, "INVITE_NOT_FOUND", "Claim invitation not found.")
        if inv["accepted_at"] is not None:
            raise DomainError(409, "INVITE_ALREADY_CLAIMED", "Claim invitation has already been used.")
        if inv["expires_at"] < now_iso:
            raise DomainError(400, "INVITE_EXPIRED", "Claim invitation has expired.")
        if not principal["user"]["is_verified"]:
            raise DomainError(403, "EMAIL_NOT_VERIFIED", "Verified identity is required to claim a subscriber contact.")

        existing_link = db.fetchone(
            "SELECT id FROM customer_links WHERE subscriber_contact_id = %s",
            (inv["subscriber_contact_id"],),
        )
        if existing_link:
            raise DomainError(409, "ALREADY_LINKED", "Subscriber contact is already linked to a user.")

        link_id = str(uuid.uuid4())
        db.execute(
            """
            INSERT INTO customer_links (id, subscriber_contact_id, user_id, linked_at)
            VALUES (%s, %s, %s, %s)
            """,
            (link_id, inv["subscriber_contact_id"], user_id, now_iso),
        )
        db.execute(
            "UPDATE invitations SET accepted_at = %s, accepted_by_user_id = %s WHERE id = %s",
            (now_iso, user_id, inv["id"]),
        )
        record_audit_log(
            db,
            actor_user_id=user_id,
            tenant_id=inv["organization_id"],
            action="subscriber_contact.claimed",
            resource_type="customer_link",
            resource_id=link_id,
            metadata={"subscriber_contact_id": inv["subscriber_contact_id"]},
        )
    return {
        "claimed": True,
        "link_id": link_id,
        "subscriber_contact_id": inv["subscriber_contact_id"],
        "user_id": user_id,
    }

"""Enterprise buying, seat pools, seat allocations, and employee identity mappings."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.audit.service import record_audit_log
from app.auth.dependencies import get_current_principal, require_org_role
from app.core.clock import get_clock
from app.core.errors import DomainError
from app.db.engine import get_db

router = APIRouter(prefix="/api/v1/enterprise", tags=["enterprise"])


class ExternalSubscriptionCreate(BaseModel):
    organization_id: str
    vendor_name: str = Field(..., min_length=1, max_length=120)
    service_name: str = Field(..., min_length=1, max_length=120)
    seats_purchased: int = Field(..., ge=1)
    unit_price_minor: int = Field(..., ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    billing_interval: str = "monthly"
    next_renewal_date: str


class EnterpriseSeatAssign(BaseModel):
    membership_id: str


class IdentityMappingCreate(BaseModel):
    organization_id: str
    membership_id: str
    provider_code: str
    external_user_ref: str


@router.post("/subscriptions", status_code=201)
def create_external_subscription(payload: ExternalSubscriptionCreate, principal: dict = Depends(get_current_principal)):
    now_iso = get_clock().now_iso()
    ext_id = str(uuid.uuid4())
    ob_id = str(uuid.uuid4())
    total_payable = payload.seats_purchased * payload.unit_price_minor

    with get_db(write=True) as db:
        require_org_role(db, principal, payload.organization_id, {"owner", "admin", "billing"})
        db.execute(
            """
            INSERT INTO external_subscriptions (
                id, organization_id, vendor_name, service_name, seats_purchased,
                unit_price_minor, currency, billing_interval, next_renewal_date, status, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', %s)
            """,
            (
                ext_id,
                payload.organization_id,
                payload.vendor_name.strip(),
                payload.service_name.strip(),
                payload.seats_purchased,
                payload.unit_price_minor,
                payload.currency.upper(),
                payload.billing_interval,
                payload.next_renewal_date,
                now_iso,
            ),
        )
        db.execute(
            """
            INSERT INTO financial_obligations (
                id, organization_id, external_subscription_id, direction, party_ref,
                description, amount_minor, currency, due_at, status, created_at
            )
            VALUES (%s, %s, %s, 'PAYABLE', %s, %s, %s, %s, %s, 'issued', %s)
            """,
            (
                ob_id,
                payload.organization_id,
                ext_id,
                payload.vendor_name.strip(),
                f"{payload.vendor_name} {payload.service_name} ({payload.seats_purchased} seats)",
                total_payable,
                payload.currency.upper(),
                payload.next_renewal_date,
                now_iso,
            ),
        )
        record_audit_log(
            db,
            actor_user_id=principal["user"]["id"],
            tenant_id=payload.organization_id,
            action="enterprise_subscription.created",
            resource_type="external_subscription",
            resource_id=ext_id,
            metadata={"seats_purchased": payload.seats_purchased, "total_payable_minor": total_payable},
        )
    return {
        "id": ext_id,
        "organization_id": payload.organization_id,
        "vendor_name": payload.vendor_name.strip(),
        "service_name": payload.service_name.strip(),
        "seats_purchased": payload.seats_purchased,
        "seats_assigned": 0,
        "seats_unassigned": payload.seats_purchased,
        "unit_price_minor": payload.unit_price_minor,
        "total_payable_minor": total_payable,
        "currency": payload.currency.upper(),
        "obligation_id": ob_id,
    }


@router.get("/subscriptions", status_code=200)
def list_external_subscriptions(organization_id: str, principal: dict = Depends(get_current_principal)):
    with get_db(write=False) as db:
        require_org_role(db, principal, organization_id, {"owner", "admin", "billing", "staff", "developer"})
        rows = db.fetchall(
            "SELECT * FROM external_subscriptions WHERE organization_id = %s ORDER BY created_at DESC",
            (organization_id,),
        )
        out = []
        for r in rows:
            allocs = db.fetchall(
                """
                SELECT sa.*, u.email, u.full_name, m.role
                FROM seat_allocations sa
                JOIN memberships m ON m.id = sa.membership_id
                JOIN users u ON u.id = m.user_id
                WHERE sa.external_subscription_id = %s
                ORDER BY sa.assigned_at
                """,
                (r["id"],),
            )
            active = [a for a in allocs if a["revoked_at"] is None]
            out.append(
                {
                    **r,
                    "seats_assigned": len(active),
                    "assigned_seats": len(active),
                    "seats_unassigned": r["seats_purchased"] - len(active),
                    "total_payable_minor": r["seats_purchased"] * r["unit_price_minor"],
                    "allocations": allocs,
                }
            )
    return {"external_subscriptions": out}


@router.post("/subscriptions/{ext_sub_id}/seat-allocations", status_code=201)
def assign_enterprise_seat(ext_sub_id: str, payload: EnterpriseSeatAssign, principal: dict = Depends(get_current_principal)):
    now_iso = get_clock().now_iso()
    alloc_id = str(uuid.uuid4())
    with get_db(write=True) as db:
        ext = db.fetchone("SELECT * FROM external_subscriptions WHERE id = %s", (ext_sub_id,))
        if not ext:
            raise DomainError(404, "EXTERNAL_SUB_NOT_FOUND", "External subscription not found.")
        require_org_role(db, principal, ext["organization_id"], {"owner", "admin", "billing"})

        mem = db.fetchone("SELECT id, organization_id FROM memberships WHERE id = %s AND status = 'active'", (payload.membership_id,))
        if not mem or mem["organization_id"] != ext["organization_id"]:
            raise DomainError(403, "WRONG_ORG_MEMBER", "Employee does not belong to this organization.")

        existing = db.fetchone(
            "SELECT id FROM seat_allocations WHERE external_subscription_id = %s AND membership_id = %s AND revoked_at IS NULL",
            (ext_sub_id, payload.membership_id),
        )
        if existing:
            raise DomainError(409, "SEAT_ALREADY_ALLOCATED", "Employee already has an active seat.")

        cnt = db.fetchone(
            "SELECT COUNT(*) as c FROM seat_allocations WHERE external_subscription_id = %s AND revoked_at IS NULL",
            (ext_sub_id,),
        )
        if cnt["c"] >= ext["seats_purchased"]:
            raise DomainError(
                409,
                "SEAT_LIMIT_EXCEEDED",
                "Cannot assign seat: all purchased seats are allocated.",
                {"purchased": ext["seats_purchased"], "assigned": cnt["c"]},
            )

        db.execute(
            """
            INSERT INTO seat_allocations (id, subscription_id, external_subscription_id, organization_id, membership_id, assigned_at)
            VALUES (%s, NULL, %s, %s, %s, %s)
            """,
            (alloc_id, ext_sub_id, ext["organization_id"], payload.membership_id, now_iso),
        )
        record_audit_log(
            db,
            actor_user_id=principal["user"]["id"],
            tenant_id=ext["organization_id"],
            action="enterprise_seat.assigned",
            resource_type="seat_allocation",
            resource_id=alloc_id,
            metadata={"membership_id": payload.membership_id, "external_subscription_id": ext_sub_id},
        )
    return {
        "id": alloc_id,
        "external_subscription_id": ext_sub_id,
        "membership_id": payload.membership_id,
        "assigned_at": now_iso,
        "seats_assigned": cnt["c"] + 1,
        "seats_unassigned": ext["seats_purchased"] - (cnt["c"] + 1),
    }


@router.delete("/subscriptions/{ext_sub_id}/seat-allocations/{alloc_id}", status_code=200)
def revoke_enterprise_seat(ext_sub_id: str, alloc_id: str, principal: dict = Depends(get_current_principal)):
    now_iso = get_clock().now_iso()
    with get_db(write=True) as db:
        alloc = db.fetchone(
            "SELECT * FROM seat_allocations WHERE id = %s AND external_subscription_id = %s",
            (alloc_id, ext_sub_id),
        )
        if not alloc:
            raise DomainError(404, "ALLOCATION_NOT_FOUND", "Seat allocation not found.")
        require_org_role(db, principal, alloc["organization_id"], {"owner", "admin", "billing"})
        db.execute("UPDATE seat_allocations SET revoked_at = %s WHERE id = %s", (now_iso, alloc_id))
        record_audit_log(
            db,
            actor_user_id=principal["user"]["id"],
            tenant_id=alloc["organization_id"],
            action="enterprise_seat.revoked",
            resource_type="seat_allocation",
            resource_id=alloc_id,
        )
    return {"id": alloc_id, "revoked_at": now_iso}


@router.post("/identity-mappings", status_code=201)
def create_identity_mapping(payload: IdentityMappingCreate, principal: dict = Depends(get_current_principal)):
    now_iso = get_clock().now_iso()
    map_id = str(uuid.uuid4())
    with get_db(write=True) as db:
        require_org_role(db, principal, payload.organization_id, {"owner", "admin"})
        mem = db.fetchone("SELECT id, organization_id FROM memberships WHERE id = %s", (payload.membership_id,))
        if not mem or mem["organization_id"] != payload.organization_id:
            raise DomainError(403, "WRONG_ORG_MEMBER", "Membership does not belong to this organization.")
        db.execute(
            """
            INSERT INTO employee_identity_mappings (id, organization_id, membership_id, provider_code, external_user_ref, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (map_id, payload.organization_id, payload.membership_id, payload.provider_code, payload.external_user_ref, now_iso),
        )
    return {
        "id": map_id,
        "organization_id": payload.organization_id,
        "membership_id": payload.membership_id,
        "provider_code": payload.provider_code,
        "external_user_ref": payload.external_user_ref,
    }

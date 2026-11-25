"""Organization workspace, membership, invitation, and context-switching endpoints."""
from __future__ import annotations

import re
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, Field

from app.audit.service import record_audit_log
from app.auth.dependencies import get_current_principal, require_org_role
from app.core.clock import get_clock
from app.core.config import get_settings
from app.core.errors import DomainError
from app.core.security import generate_token, hash_token
from app.db.engine import get_db

router = APIRouter(prefix="/api/v1/organizations", tags=["organizations"])

_ALLOWED_ORG_ROLES = {"owner", "admin", "billing", "developer", "staff", "member"}


class CreateOrgRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    slug: str = Field(..., min_length=2, max_length=80)
    timezone: str = "UTC"
    default_currency: str = Field(default="USD", min_length=3, max_length=3)
    business_type: str = "general"


class InviteMemberRequest(BaseModel):
    email: EmailStr
    role: str = "member"


@router.post("", status_code=201)
def create_organization(payload: CreateOrgRequest, principal: dict = Depends(get_current_principal)):
    clean_slug = re.sub(r"[^a-z0-9\-]", "-", payload.slug.strip().lower()).strip("-")
    if not clean_slug:
        raise DomainError(422, "INVALID_SLUG", "Organization slug is invalid.")
    user_id = principal["user"]["id"]
    now_iso = get_clock().now_iso()
    org_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())
    membership_id = str(uuid.uuid4())

    with get_db(write=True) as db:
        existing = db.fetchone("SELECT id FROM organizations WHERE slug = %s", (clean_slug,))
        if existing:
            raise DomainError(409, "SLUG_EXISTS", "An organization with this slug already exists.")
        db.execute(
            """
            INSERT INTO accounts (id, owner_user_id, kind, display_name, created_at)
            VALUES (%s, %s, 'organization', %s, %s)
            """,
            (account_id, user_id, payload.name.strip(), now_iso),
        )
        db.execute(
            """
            INSERT INTO organizations (id, account_id, name, slug, timezone, default_currency, business_type, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                org_id,
                account_id,
                payload.name.strip(),
                clean_slug,
                payload.timezone,
                payload.default_currency.upper(),
                payload.business_type,
                now_iso,
            ),
        )
        db.execute(
            """
            INSERT INTO memberships (id, organization_id, user_id, role, status, created_at)
            VALUES (%s, %s, %s, 'owner', 'active', %s)
            """,
            (membership_id, org_id, user_id, now_iso),
        )
        record_audit_log(
            db,
            actor_user_id=user_id,
            tenant_id=org_id,
            action="organization.created",
            resource_type="organization",
            resource_id=org_id,
            metadata={"slug": clean_slug},
        )
    return {
        "id": org_id,
        "account_id": account_id,
        "name": payload.name.strip(),
        "slug": clean_slug,
        "timezone": payload.timezone,
        "default_currency": payload.default_currency.upper(),
        "business_type": payload.business_type,
        "role": "owner",
        "membership_id": membership_id,
    }


@router.get("", status_code=200)
def list_organizations(principal: dict = Depends(get_current_principal)):
    return {"organizations": principal["memberships"]}


@router.get("/{org_id}/members", status_code=200)
def list_members(org_id: str, principal: dict = Depends(get_current_principal)):
    with get_db(write=False) as db:
        require_org_role(db, principal, org_id, _ALLOWED_ORG_ROLES)
        members = db.fetchall(
            """
            SELECT m.id, m.user_id, m.role, m.status, m.created_at, u.email, u.full_name
            FROM memberships m
            JOIN users u ON u.id = m.user_id
            WHERE m.organization_id = %s
            ORDER BY m.created_at
            """,
            (org_id,),
        )
    return {"members": members}


@router.post("/{org_id}/invites", status_code=201)
def invite_member(org_id: str, payload: InviteMemberRequest, principal: dict = Depends(get_current_principal)):
    if payload.role not in _ALLOWED_ORG_ROLES:
        raise DomainError(422, "INVALID_ROLE", f"Unsupported organization role: {payload.role}")
    now = get_clock().now()
    now_iso = get_clock().now_iso()
    expires_iso = (now + timedelta(days=7)).isoformat().replace("+00:00", "Z")
    raw_token = generate_token(24)
    invite_id = str(uuid.uuid4())

    with get_db(write=True) as db:
        require_org_role(db, principal, org_id, {"owner", "admin"})
        db.execute(
            """
            INSERT INTO invitations (id, organization_id, subscriber_contact_id, email, role, token_hash, expires_at, created_at)
            VALUES (%s, %s, NULL, %s, %s, %s, %s, %s)
            """,
            (invite_id, org_id, payload.email.lower(), payload.role, hash_token(raw_token), expires_iso, now_iso),
        )
        record_audit_log(
            db,
            actor_user_id=principal["user"]["id"],
            tenant_id=org_id,
            action="organization.member_invited",
            resource_type="invitation",
            resource_id=invite_id,
            metadata={"email": payload.email.lower(), "role": payload.role},
        )
    resp = {
        "id": invite_id,
        "organization_id": org_id,
        "email": payload.email.lower(),
        "role": payload.role,
        "status": "pending",
        "expires_at": expires_iso,
    }
    if get_settings().app_env != "production":
        resp["invite_token"] = raw_token
    return resp


@router.post("/invites/{token}/accept", status_code=200)
def accept_invite(token: str, principal: dict = Depends(get_current_principal)):
    t_hash = hash_token(token)
    now_iso = get_clock().now_iso()
    user_id = principal["user"]["id"]
    with get_db(write=True) as db:
        inv = db.fetchone(
            """
            SELECT id, organization_id, email, role, expires_at, accepted_at
            FROM invitations
            WHERE token_hash = %s
            """,
            (t_hash,),
        )
        if not inv:
            raise DomainError(404, "INVITE_NOT_FOUND", "Invitation not found.")
        if inv["accepted_at"] is not None:
            raise DomainError(409, "INVITE_ALREADY_ACCEPTED", "Invitation has already been accepted.")
        if inv["expires_at"] < now_iso:
            raise DomainError(400, "INVITE_EXPIRED", "Invitation has expired.")

        existing = db.fetchone(
            "SELECT id FROM memberships WHERE organization_id = %s AND user_id = %s",
            (inv["organization_id"], user_id),
        )
        if existing:
            raise DomainError(409, "ALREADY_MEMBER", "User is already a member of this organization.")

        membership_id = str(uuid.uuid4())
        db.execute(
            """
            INSERT INTO memberships (id, organization_id, user_id, role, status, created_at)
            VALUES (%s, %s, %s, %s, 'active', %s)
            """,
            (membership_id, inv["organization_id"], user_id, inv["role"], now_iso),
        )
        db.execute(
            "UPDATE invitations SET accepted_at = %s, accepted_by_user_id = %s WHERE id = %s",
            (now_iso, user_id, inv["id"]),
        )
        record_audit_log(
            db,
            actor_user_id=user_id,
            tenant_id=inv["organization_id"],
            action="organization.invite_accepted",
            resource_type="membership",
            resource_id=membership_id,
        )
    return {
        "accepted": True,
        "membership_id": membership_id,
        "organization_id": inv["organization_id"],
        "role": inv["role"],
    }


@router.post("/{org_id}/switch-context", status_code=200)
def switch_context(org_id: str, principal: dict = Depends(get_current_principal)):
    with get_db(write=True) as db:
        membership = require_org_role(db, principal, org_id, _ALLOWED_ORG_ROLES)
        db.execute(
            "UPDATE sessions SET active_org_id = %s WHERE id = %s",
            (org_id, principal["session_id"]),
        )
    return {"active_org_id": org_id, "role": membership["role"]}

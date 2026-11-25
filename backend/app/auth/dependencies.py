"""FastAPI authentication, RBAC, and service API key dependencies."""
from __future__ import annotations

from typing import Any

from fastapi import Depends, Request

from app.auth.service import get_session_principal
from app.core.errors import DomainError
from app.core.security import hash_token
from app.db.engine import DBConnection, get_db


def get_current_principal(request: Request) -> dict[str, Any]:
    token = request.cookies.get("subsphere_session")
    using_cookie = bool(token)
    if not token:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
    if not token:
        raise DomainError(401, "UNAUTHENTICATED", "Authentication is required.")

    with get_db(write=False) as db:
        principal = get_session_principal(db, token)

    # Enforce CSRF check on state-changing requests authenticated via cookies when header is provided or required
    if using_cookie and request.method in ("POST", "PUT", "PATCH", "DELETE"):
        csrf_hdr = request.headers.get("x-csrf-token")
        # If CSRF cookie is present and request explicitly omits CSRF header in browser mode, validate if header sent
        if csrf_hdr is not None and csrf_hdr != principal["csrf_token"]:
            raise DomainError(403, "CSRF_INVALID", "Invalid CSRF token.")

    return principal


def require_org_role(
    db: DBConnection,
    principal: dict[str, Any],
    organization_id: str,
    allowed_roles: set[str],
) -> dict[str, Any]:
    """Validate actor has an active membership in organization_id with one of allowed_roles."""
    user_id = principal["user"]["id"]
    membership = db.fetchone(
        """
        SELECT id, organization_id, user_id, role, status
        FROM memberships
        WHERE organization_id = %s AND user_id = %s AND status = 'active'
        """,
        (organization_id, user_id),
    )
    if not membership:
        raise DomainError(403, "FORBIDDEN", "You do not have access to this organization.")
    if membership["role"] not in allowed_roles:
        raise DomainError(
            403,
            "INSUFFICIENT_ROLE",
            f"Role '{membership['role']}' is not authorized for this action.",
            {"required_roles": sorted(allowed_roles)},
        )
    return membership


def get_service_api_key(request: Request) -> dict[str, Any]:
    raw_key = request.headers.get("x-api-key")
    if not raw_key:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            raw_key = auth[7:].strip()
    if not raw_key:
        raise DomainError(401, "API_KEY_REQUIRED", "Service API key is required.")

    key_hash = hash_token(raw_key)
    with get_db(write=False) as db:
        row = db.fetchone(
            """
            SELECT id, organization_id, service_id, name, scope, revoked_at
            FROM api_keys
            WHERE key_hash = %s
            """,
            (key_hash,),
        )
    if not row or row["revoked_at"] is not None:
        raise DomainError(401, "INVALID_API_KEY", "Service API key is invalid or revoked.")
    return row

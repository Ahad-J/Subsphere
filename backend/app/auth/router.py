"""Authentication and session endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, EmailStr, Field

from app.auth.dependencies import get_current_principal
from app.auth.service import (
    login_user,
    logout_session,
    refresh_session,
    register_user,
    reset_password_dev,
    verify_email_token,
)
from app.core.errors import DomainError
from app.db.engine import get_db

router = APIRouter(prefix="/api/v1", tags=["identity"])


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    full_name: str = ""


class VerifyEmailRequest(BaseModel):
    token: str = Field(..., min_length=8)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1)


class RefreshRequest(BaseModel):
    refresh_token: str | None = None


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    new_password: str = Field(..., min_length=8, max_length=128)


@router.post("/auth/register", status_code=201)
def register(payload: RegisterRequest):
    with get_db(write=True) as db:
        return register_user(db, email=payload.email, password=payload.password, full_name=payload.full_name)


@router.post("/auth/verify-email", status_code=200)
def verify_email(payload: VerifyEmailRequest):
    with get_db(write=True) as db:
        return verify_email_token(db, payload.token)


@router.post("/auth/reset-password", status_code=200)
def reset_password(payload: ResetPasswordRequest):
    with get_db(write=True) as db:
        return reset_password_dev(db, email=payload.email, new_password=payload.new_password)


@router.post("/auth/login", status_code=200)
def login(payload: LoginRequest, request: Request, response: Response):
    client_ip = request.client.host if request.client else "127.0.0.1"
    with get_db(write=True) as db:
        res = login_user(db, email=payload.email, password=payload.password, client_ip=client_ip)

    response.set_cookie(
        key="subsphere_session",
        value=res["access_token"],
        httponly=True,
        samesite="lax",
        max_age=7200,
    )
    response.set_cookie(
        key="subsphere_refresh",
        value=res["refresh_token"],
        httponly=True,
        samesite="lax",
        max_age=7 * 86400,
    )
    response.set_cookie(
        key="subsphere_csrf",
        value=res["csrf_token"],
        httponly=False,
        samesite="lax",
        max_age=7200,
    )
    return res


@router.post("/auth/refresh", status_code=200)
def refresh(request: Request, response: Response, payload: RefreshRequest | None = None):
    raw_refresh = (payload.refresh_token if payload else None) or request.cookies.get("subsphere_refresh")
    if not raw_refresh:
        raise DomainError(401, "REFRESH_TOKEN_REQUIRED", "Refresh token is required.")
    with get_db(write=True) as db:
        res = refresh_session(db, raw_refresh)
    response.set_cookie("subsphere_session", res["access_token"], httponly=True, samesite="lax", max_age=7200)
    response.set_cookie("subsphere_refresh", res["refresh_token"], httponly=True, samesite="lax", max_age=7 * 86400)
    response.set_cookie("subsphere_csrf", res["csrf_token"], httponly=False, samesite="lax", max_age=7200)
    return res


@router.post("/auth/logout", status_code=200)
def logout(response: Response, principal: dict = Depends(get_current_principal)):
    with get_db(write=True) as db:
        logout_session(db, principal["session_id"])
    response.delete_cookie("subsphere_session")
    response.delete_cookie("subsphere_refresh")
    response.delete_cookie("subsphere_csrf")
    return {"logged_out": True}


@router.get("/me", status_code=200)
@router.get("/auth/me", status_code=200)
def me(principal: dict = Depends(get_current_principal)):
    normalized_orgs = [
        {
            **m,
            "organization_name": m.get("organization_name") or m.get("org_name"),
            "slug": m.get("slug") or m.get("org_slug"),
        }
        for m in principal["memberships"]
    ]
    return {
        "user": principal["user"],
        "personal_account_id": principal["personal_account_id"],
        "active_org_id": principal["active_org_id"],
        "organizations": normalized_orgs,
        "memberships": normalized_orgs,
        "csrf_token": principal["csrf_token"],
    }


@router.get("/accounts", status_code=200)
def list_accounts(principal: dict = Depends(get_current_principal)):
    accounts = [
        {
            "id": principal["personal_account_id"],
            "kind": "personal",
            "display_name": principal["user"]["full_name"] or principal["user"]["email"],
            "role": "owner",
        }
    ]
    for m in principal["memberships"]:
        accounts.append(
            {
                "id": m["organization_id"],
                "kind": "organization",
                "display_name": m["org_name"],
                "slug": m["org_slug"],
                "role": m["role"],
                "default_currency": m["default_currency"],
                "business_type": m["business_type"],
            }
        )
    return {"accounts": accounts}

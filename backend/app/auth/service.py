"""Authentication, session management, rate limiting, and CSRF helpers."""
from __future__ import annotations

import time
import uuid
from collections import defaultdict
from datetime import timedelta
from typing import Any

from app.audit.service import record_audit_log
from app.core.clock import get_clock
from app.core.config import get_settings
from app.core.errors import DomainError
from app.core.security import (
    create_jwt,
    decode_jwt,
    derive_masked_relay_address,
    generate_token,
    hash_password,
    hash_token,
    mask_email_address,
    verify_password,
)
from app.db.engine import DBConnection

# In-memory sliding window for failed login rate limiting (per email/IP)
_FAILED_LOGINS: dict[str, list[float]] = defaultdict(list)
_MAX_LOGIN_ATTEMPTS = 5
_WINDOW_SECONDS = 300.0


def reset_rate_limits() -> None:
    _FAILED_LOGINS.clear()


def _check_login_rate_limit(key: str) -> None:
    now = time.monotonic()
    attempts = [t for t in _FAILED_LOGINS[key] if now - t < _WINDOW_SECONDS]
    _FAILED_LOGINS[key] = attempts
    if len(attempts) >= _MAX_LOGIN_ATTEMPTS:
        raise DomainError(
            429,
            "RATE_LIMITED",
            "Too many failed login attempts. Please try again later.",
            {"retry_after_seconds": int(_WINDOW_SECONDS)},
        )


def _record_failed_login(key: str) -> None:
    _FAILED_LOGINS[key].append(time.monotonic())


def _ensure_saved_email_mask(db: DBConnection, account_id: str | None, email: str, now_iso: str) -> None:
    if not account_id or not email:
        return
    existing = db.fetchone("SELECT id, primary_email FROM notification_preferences WHERE account_id = %s", (account_id,))
    if not existing:
        db.execute(
            """
            INSERT INTO notification_preferences (
                id, account_id, organization_id, email_enabled, primary_email,
                default_offsets_json, send_hour_local, timezone, monthly_report_opt_in, updated_at
            )
            VALUES (%s, %s, NULL, 1, %s, '[7,3,1]', 9, 'UTC', 0, %s)
            """,
            (str(uuid.uuid4()), account_id, email.lower(), now_iso),
        )
    elif not existing.get("primary_email"):
        db.execute(
            "UPDATE notification_preferences SET primary_email = %s, updated_at = %s WHERE id = %s",
            (email.lower(), now_iso, existing["id"]),
        )


def register_user(
    db: DBConnection,
    *,
    email: str,
    password: str,
    full_name: str = "",
) -> dict[str, Any]:
    norm_email = email.strip().lower()
    existing = db.fetchone("SELECT id FROM users WHERE email = %s", (norm_email,))
    if existing:
        raise DomainError(409, "EMAIL_EXISTS", "An account with this email already exists.")

    now = get_clock().now()
    now_iso = get_clock().now_iso()
    user_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())
    pw_hash = hash_password(password)

    db.execute(
        """
        INSERT INTO users (id, email, password_hash, full_name, is_verified, is_platform_admin, created_at)
        VALUES (%s, %s, %s, %s, 0, 0, %s)
        """,
        (user_id, norm_email, pw_hash, full_name.strip(), now_iso),
    )
    db.execute(
        """
        INSERT INTO accounts (id, owner_user_id, kind, display_name, created_at)
        VALUES (%s, %s, 'personal', %s, %s)
        """,
        (account_id, user_id, full_name.strip() or norm_email.split("@")[0], now_iso),
    )

    raw_verify_token = generate_token(24)
    verify_id = str(uuid.uuid4())
    expires_iso = (now + timedelta(hours=24)).isoformat().replace("+00:00", "Z")
    db.execute(
        """
        INSERT INTO email_verifications (id, user_id, token_hash, expires_at, created_at)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (verify_id, user_id, hash_token(raw_verify_token), expires_iso, now_iso),
    )

    # Queue welcome email notification job (free personal workspace)
    db.execute(
        """
        INSERT INTO notification_jobs (
            id, logical_key, account_id, organization_id, rule_id, channel, recipient,
            subject, body, status, attempts, created_at, updated_at
        )
        VALUES (%s, %s, %s, NULL, NULL, 'email', %s, %s, %s, 'queued', 0, %s, %s)
        """,
        (
            str(uuid.uuid4()),
            f"welcome:{user_id}",
            account_id,
            norm_email,
            "Welcome to SubSphere (Personal Free Workspace)",
            f"Hello {full_name or norm_email},\n\nYour free SubSphere personal workspace is ready. No payment card is required.\n",
            now_iso,
            now_iso,
        ),
    )

    _ensure_saved_email_mask(db, account_id, norm_email, now_iso)

    record_audit_log(
        db,
        actor_user_id=user_id,
        tenant_id=account_id,
        action="user.registered",
        resource_type="user",
        resource_id=user_id,
        metadata={"plan_tier": "personal_free"},
    )

    result: dict[str, Any] = {
        "user": {
            "id": user_id,
            "email": norm_email,
            "masked_email": mask_email_address(norm_email),
            "relay_mask_email": derive_masked_relay_address(norm_email),
            "full_name": full_name.strip(),
            "is_verified": False,
        },
        "personal_account": {
            "id": account_id,
            "kind": "personal",
            "tier": "personal_free",
            "payment_required": False,
        },
    }
    if get_settings().app_env != "production":
        result["verification_token"] = raw_verify_token
    return result


def verify_email_token(db: DBConnection, token: str) -> dict[str, Any]:
    tok_hash = hash_token(token)
    row = db.fetchone(
        "SELECT id, user_id, expires_at, verified_at FROM email_verifications WHERE token_hash = %s",
        (tok_hash,),
    )
    if not row:
        raise DomainError(400, "INVALID_TOKEN", "Email verification token is invalid.")
    if row["verified_at"] is not None:
        raise DomainError(409, "TOKEN_ALREADY_USED", "Email verification token has already been used.")
    now_iso = get_clock().now_iso()
    if row["expires_at"] < now_iso:
        raise DomainError(400, "TOKEN_EXPIRED", "Email verification token has expired.")

    db.execute("UPDATE email_verifications SET verified_at = %s WHERE id = %s", (now_iso, row["id"]))
    db.execute("UPDATE users SET is_verified = 1 WHERE id = %s", (row["user_id"],))
    return {"verified": True, "user_id": row["user_id"]}


def login_user(
    db: DBConnection,
    *,
    email: str,
    password: str,
    client_ip: str = "127.0.0.1",
) -> dict[str, Any]:
    norm_email = email.strip().lower()
    rate_key = f"{norm_email}:{client_ip}"
    _check_login_rate_limit(rate_key)

    user = db.fetchone(
        "SELECT id, email, password_hash, full_name, is_verified, is_platform_admin FROM users WHERE email = %s",
        (norm_email,),
    )
    if not user or not verify_password(password, user["password_hash"]):
        _record_failed_login(rate_key)
        raise DomainError(401, "INVALID_CREDENTIALS", "Invalid email or password.")

    _FAILED_LOGINS.pop(rate_key, None)

    personal = db.fetchone(
        "SELECT id, display_name FROM accounts WHERE owner_user_id = %s AND kind = 'personal'",
        (user["id"],),
    )

    now = get_clock().now()
    now_iso = get_clock().now_iso()
    session_id = str(uuid.uuid4())
    refresh_raw = generate_token(32)
    csrf_token = generate_token(24)
    expires_iso = (now + timedelta(days=7)).isoformat().replace("+00:00", "Z")

    db.execute(
        """
        INSERT INTO sessions (id, user_id, active_org_id, refresh_token_hash, csrf_token, expires_at, created_at)
        VALUES (%s, %s, NULL, %s, %s, %s, %s)
        """,
        (session_id, user["id"], hash_token(refresh_raw), csrf_token, expires_iso, now_iso),
    )

    secret = get_settings().app_secret
    access_token = create_jwt(
        {"sub": user["id"], "sid": session_id},
        secret=secret,
        expires_delta=timedelta(hours=2),
    )

    _ensure_saved_email_mask(db, personal["id"] if personal else None, user["email"], now_iso)

    record_audit_log(
        db,
        actor_user_id=user["id"],
        tenant_id=personal["id"] if personal else None,
        action="user.logged_in",
        resource_type="session",
        resource_id=session_id,
    )

    return {
        "access_token": access_token,
        "refresh_token": refresh_raw,
        "csrf_token": csrf_token,
        "session_id": session_id,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "masked_email": mask_email_address(user["email"]),
            "relay_mask_email": derive_masked_relay_address(user["email"]),
            "full_name": user["full_name"],
            "is_verified": bool(user["is_verified"]),
            "is_platform_admin": bool(user["is_platform_admin"]),
        },
        "personal_account_id": personal["id"] if personal else None,
    }


def refresh_session(db: DBConnection, refresh_token: str) -> dict[str, Any]:
    r_hash = hash_token(refresh_token)
    sess = db.fetchone(
        "SELECT id, user_id, active_org_id, csrf_token, expires_at, revoked_at FROM sessions WHERE refresh_token_hash = %s",
        (r_hash,),
    )
    if not sess or sess["revoked_at"] is not None:
        raise DomainError(401, "INVALID_SESSION", "Refresh token is invalid or revoked.")
    now = get_clock().now()
    now_iso = get_clock().now_iso()
    if sess["expires_at"] < now_iso:
        raise DomainError(401, "SESSION_EXPIRED", "Refresh token has expired.")

    new_refresh = generate_token(32)
    new_csrf = generate_token(24)
    new_exp = (now + timedelta(days=7)).isoformat().replace("+00:00", "Z")
    db.execute(
        """
        UPDATE sessions
        SET refresh_token_hash = %s, csrf_token = %s, expires_at = %s
        WHERE id = %s
        """,
        (hash_token(new_refresh), new_csrf, new_exp, sess["id"]),
    )
    secret = get_settings().app_secret
    access_token = create_jwt(
        {"sub": sess["user_id"], "sid": sess["id"]},
        secret=secret,
        expires_delta=timedelta(hours=2),
    )
    return {
        "access_token": access_token,
        "refresh_token": new_refresh,
        "csrf_token": new_csrf,
        "session_id": sess["id"],
    }


def logout_session(db: DBConnection, session_id: str) -> None:
    db.execute(
        "UPDATE sessions SET revoked_at = %s WHERE id = %s",
        (get_clock().now_iso(), session_id),
    )


def get_session_principal(db: DBConnection, token: str) -> dict[str, Any]:
    try:
        claims = decode_jwt(token, get_settings().app_secret)
    except Exception:
        raise DomainError(401, "UNAUTHENTICATED", "Invalid or expired authentication token.")
    sid = claims.get("sid")
    uid = claims.get("sub")
    if not sid or not uid:
        raise DomainError(401, "UNAUTHENTICATED", "Invalid session claims.")
    sess = db.fetchone(
        "SELECT id, user_id, active_org_id, csrf_token, expires_at, revoked_at FROM sessions WHERE id = %s",
        (sid,),
    )
    if not sess or sess["revoked_at"] is not None or sess["expires_at"] < get_clock().now_iso():
        raise DomainError(401, "UNAUTHENTICATED", "Session has been revoked or expired.")
    user = db.fetchone(
        "SELECT id, email, full_name, is_verified, is_platform_admin FROM users WHERE id = %s",
        (uid,),
    )
    if not user:
        raise DomainError(401, "UNAUTHENTICATED", "User not found.")
    personal = db.fetchone(
        "SELECT id, display_name FROM accounts WHERE owner_user_id = %s AND kind = 'personal'",
        (uid,),
    )
    memberships = db.fetchall(
        """
        SELECT m.id as membership_id, m.organization_id, m.role, m.status, o.name as org_name, o.slug as org_slug, o.default_currency, o.business_type
        FROM memberships m
        JOIN organizations o ON o.id = m.organization_id
        WHERE m.user_id = %s AND m.status = 'active'
        ORDER BY o.name
        """,
        (uid,),
    )
    return {
        "session_id": sid,
        "csrf_token": sess["csrf_token"],
        "active_org_id": sess["active_org_id"],
        "user": {
            "id": user["id"],
            "email": user["email"],
            "masked_email": mask_email_address(user["email"]),
            "relay_mask_email": derive_masked_relay_address(user["email"]),
            "full_name": user["full_name"],
            "is_verified": bool(user["is_verified"]),
            "is_platform_admin": bool(user["is_platform_admin"]),
        },
        "personal_account_id": personal["id"] if personal else None,
        "memberships": memberships,
    }


def reset_password_dev(
    db: DBConnection,
    *,
    email: str,
    new_password: str,
) -> dict[str, Any]:
    if get_settings().app_env == "production":
        raise DomainError(403, "FORBIDDEN", "Direct password reset is disabled in production.")
    norm_email = email.strip().lower()
    user = db.fetchone("SELECT id, email FROM users WHERE email = %s", (norm_email,))
    if not user:
        raise DomainError(404, "USER_NOT_FOUND", "No account found with that email.")
    pw_hash = hash_password(new_password)
    db.execute("UPDATE users SET password_hash = %s, is_verified = 1 WHERE id = %s", (pw_hash, user["id"]))
    for key in [k for k in list(_FAILED_LOGINS.keys()) if k.startswith(f"{norm_email}:")]:
        _FAILED_LOGINS.pop(key, None)
    return {"reset": True, "email": user["email"], "masked_email": mask_email_address(user["email"])}

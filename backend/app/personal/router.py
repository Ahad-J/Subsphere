"""Individual (Free) API routes: subscriptions, calendar, analytics, monitoring, privacy."""
from __future__ import annotations

import json
import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, Field

from app.audit.service import record_audit_log
from app.auth.dependencies import get_current_principal, require_org_role
from app.core.clock import get_clock
from app.core.errors import DomainError
from app.core.security import generate_token, hash_token
from app.db.engine import get_db
from app.personal.domain import (
    annual_equivalent_minor,
    monthly_equivalent_minor,
    project_occurrences_in_range,
)
from app.personal.service import (
    _FORBIDDEN_PRIVACY_KEYS,
    _VALID_TRACKING_STATUSES,
    compute_personal_dashboard,
    create_personal_subscription,
    reconcile_subscription_reminders,
    sanitize_domain_only,
    schedule_personal_renewal_reminders,
    serialize_personal_subscription,
    validate_timezone,
)

router = APIRouter(prefix="/api/v1/personal", tags=["personal"])
calendar_router = APIRouter(prefix="/api/v1", tags=["calendar"])

_VALID_INTERVALS = {"monthly", "quarterly", "annual", "yearly", "weekly", "one_time", "custom"}
_VALID_PRODUCT_TYPES = {"recurring_subscription", "one_time_purchase", "trial"}


class PersonalSubscriptionCreate(BaseModel):
    service_name: str = Field(..., min_length=1, max_length=120)
    service_url: str = ""
    category: str = "General"
    product_type: str = "recurring_subscription"
    plan_name: str = ""
    price_minor: int = Field(..., ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    billing_interval: str = "monthly"
    next_renewal_date: date
    start_date: date | None = None
    trial_end_date: date | None = None
    anchor_day: int | None = None
    auto_renew: bool = True
    timezone: str = "UTC"
    notes: str = ""
    recipient_email: str | None = None
    tracking_status: str = "tracking_active"
    reminder_offsets_days: list[int] | None = None
    monitoring_source: str = "not_connected"


class PersonalSubscriptionPatch(BaseModel):
    service_name: str | None = None
    service_url: str | None = None
    category: str | None = None
    product_type: str | None = None
    plan_name: str | None = None
    price_minor: int | None = Field(default=None, ge=0)
    currency: str | None = None
    billing_interval: str | None = None
    next_renewal_date: date | None = None
    start_date: date | None = None
    trial_end_date: date | None = None
    anchor_day: int | None = None
    auto_renew: bool | None = None
    timezone: str | None = None
    notes: str | None = None
    recipient_email: str | None = None
    tracking_status: str | None = None
    reminder_offsets_days: list[int] | None = None
    version: int | None = None


class MonitoringConnectRequest(BaseModel):
    source_type: str
    allowlist: list[str] = Field(default_factory=list)


class MonitoringRevokeRequest(BaseModel):
    source_type: str


@router.post("/subscriptions", status_code=201)
def add_subscription(payload: PersonalSubscriptionCreate, principal: dict = Depends(get_current_principal)):
    if payload.billing_interval not in _VALID_INTERVALS:
        raise DomainError(422, "INVALID_INTERVAL", f"Unsupported billing_interval: {payload.billing_interval}")
    if payload.product_type not in _VALID_PRODUCT_TYPES:
        raise DomainError(422, "INVALID_PRODUCT_TYPE", f"Unsupported product_type: {payload.product_type}")
    with get_db(write=True) as db:
        return create_personal_subscription(
            db,
            account_id=principal["personal_account_id"],
            user_id=principal["user"]["id"],
            data=payload.model_dump(),
        )


@router.get("/subscriptions", status_code=200)
def list_subscriptions(principal: dict = Depends(get_current_principal)):
    acc_id = principal["personal_account_id"]
    with get_db(write=False) as db:
        rows = db.fetchall(
            "SELECT * FROM personal_subscriptions WHERE account_id = %s ORDER BY next_renewal_date",
            (acc_id,),
        )
    return {"subscriptions": [serialize_personal_subscription(r) for r in rows]}


@router.get("/subscriptions/{sub_id}", status_code=200)
def get_subscription(sub_id: str, principal: dict = Depends(get_current_principal)):
    acc_id = principal["personal_account_id"]
    with get_db(write=False) as db:
        row = db.fetchone(
            "SELECT * FROM personal_subscriptions WHERE id = %s AND account_id = %s",
            (sub_id, acc_id),
        )
    if not row:
        raise DomainError(404, "NOT_FOUND", "Personal subscription not found.")
    return serialize_personal_subscription(row)


@router.patch("/subscriptions/{sub_id}", status_code=200)
def update_subscription(sub_id: str, payload: PersonalSubscriptionPatch, principal: dict = Depends(get_current_principal)):
    acc_id = principal["personal_account_id"]
    now_iso = get_clock().now_iso()
    with get_db(write=True) as db:
        row = db.fetchone(
            "SELECT * FROM personal_subscriptions WHERE id = %s AND account_id = %s",
            (sub_id, acc_id),
        )
        if not row:
            raise DomainError(404, "NOT_FOUND", "Personal subscription not found.")
        cur_version = int(row.get("version") or 1)
        if payload.version is not None and payload.version != cur_version:
            raise DomainError(
                409,
                "VERSION_CONFLICT",
                f"Subscription was modified concurrently (expected version {payload.version}, actual {cur_version}).",
                {"current_version": cur_version},
            )
        upd = payload.model_dump(exclude_none=True)
        if "billing_interval" in upd and upd["billing_interval"] not in _VALID_INTERVALS:
            raise DomainError(422, "INVALID_INTERVAL", "Invalid billing_interval.")
        if "product_type" in upd and upd["product_type"] not in _VALID_PRODUCT_TYPES:
            raise DomainError(422, "INVALID_PRODUCT_TYPE", "Invalid product_type.")
        new_tz = validate_timezone(upd.get("timezone", row.get("timezone") or "UTC"))
        new_tracking = upd.get("tracking_status", row.get("tracking_status") or "tracking_active")
        if new_tracking not in _VALID_TRACKING_STATUSES:
            raise DomainError(422, "INVALID_TRACKING_STATUS", f"Unsupported tracking_status: {new_tracking}")

        new_service = upd.get("service_name", row["service_name"])
        new_url = upd.get("service_url", row.get("service_url") or "")
        new_cat = upd.get("category", row["category"])
        new_ptype = upd.get("product_type", row["product_type"])
        new_plan = upd.get("plan_name", row["plan_name"])
        new_price = upd.get("price_minor", row["price_minor"])
        new_cur = upd.get("currency", row["currency"]).upper()
        new_int = upd.get("billing_interval", row["billing_interval"])
        new_next_date = date.fromisoformat(str(upd.get("next_renewal_date", row["next_renewal_date"])))
        new_next = new_next_date.isoformat()
        new_start = str(upd.get("start_date", row.get("start_date"))) if upd.get("start_date", row.get("start_date")) else None
        new_trial = str(upd.get("trial_end_date", row.get("trial_end_date"))) if upd.get("trial_end_date", row.get("trial_end_date")) else None
        new_anchor = int(upd.get("anchor_day") or (new_next_date.day if "next_renewal_date" in upd else row["anchor_day"]))
        new_auto = 1 if upd.get("auto_renew", bool(row["auto_renew"])) else 0
        new_notes = upd.get("notes", row.get("notes") or "")
        new_recipient = upd.get("recipient_email", row.get("recipient_email") or principal["user"]["email"])
        offsets_list = (
            [int(x) for x in upd["reminder_offsets_days"]]
            if "reminder_offsets_days" in upd
            else [int(x) for x in json.loads(row["reminder_offsets_json"] or "[7,3,1]")]
        )
        new_offsets = json.dumps(offsets_list)
        new_status = "cancelled_user_recorded" if new_tracking in ("stopped_tracking", "canceled_at_provider") else "active_user_recorded"
        next_version = cur_version + 1

        db.execute(
            """
            UPDATE personal_subscriptions
            SET service_name=%s, service_url=%s, category=%s, product_type=%s, plan_name=%s,
                price_minor=%s, currency=%s, billing_interval=%s, next_renewal_date=%s,
                start_date=%s, trial_end_date=%s, anchor_day=%s, auto_renew=%s, timezone=%s,
                notes=%s, recipient_email=%s, reminder_offsets_json=%s, tracking_status=%s,
                status=%s, version=%s, updated_at=%s
            WHERE id=%s AND account_id=%s
            """,
            (
                new_service,
                new_url,
                new_cat,
                new_ptype,
                new_plan,
                new_price,
                new_cur,
                new_int,
                new_next,
                new_start,
                new_trial,
                new_anchor,
                new_auto,
                new_tz,
                new_notes,
                new_recipient,
                new_offsets,
                new_tracking,
                new_status,
                next_version,
                now_iso,
                sub_id,
                acc_id,
            ),
        )
        reconcile_subscription_reminders(
            db,
            subscription_id=sub_id,
            account_id=acc_id,
            next_renewal_date=new_next,
            offsets=offsets_list,
            tracking_status=new_tracking,
            recipient=new_recipient,
            tz_name=new_tz,
        )
        record_audit_log(
            db,
            actor_user_id=principal["user"]["id"],
            tenant_id=acc_id,
            action="personal_subscription.updated",
            resource_type="personal_subscription",
            resource_id=sub_id,
            metadata={
                "previous": {
                    "price_minor": row["price_minor"],
                    "billing_interval": row["billing_interval"],
                    "next_renewal_date": row["next_renewal_date"],
                    "tracking_status": row.get("tracking_status") or "tracking_active",
                },
                "updated": {
                    "price_minor": new_price,
                    "billing_interval": new_int,
                    "next_renewal_date": new_next,
                    "tracking_status": new_tracking,
                },
                "version": next_version,
            },
        )
        updated = db.fetchone("SELECT * FROM personal_subscriptions WHERE id = %s", (sub_id,))
    return serialize_personal_subscription(updated)


@router.delete("/subscriptions/{sub_id}", status_code=204)
def delete_subscription(sub_id: str, principal: dict = Depends(get_current_principal)):
    acc_id = principal["personal_account_id"]
    with get_db(write=True) as db:
        row = db.fetchone("SELECT id FROM personal_subscriptions WHERE id = %s AND account_id = %s", (sub_id, acc_id))
        if not row:
            raise DomainError(404, "NOT_FOUND", "Personal subscription not found.")
        db.execute("DELETE FROM personal_subscriptions WHERE id = %s AND account_id = %s", (sub_id, acc_id))
    return None


@router.get("/calendar", status_code=200)
def get_calendar(month: str | None = None, principal: dict = Depends(get_current_principal)):
    acc_id = principal["personal_account_id"]
    target_month = month or get_clock().now().strftime("%Y-%m")
    with get_db(write=False) as db:
        rows = db.fetchall(
            "SELECT * FROM personal_subscriptions WHERE account_id = %s ORDER BY next_renewal_date",
            (acc_id,),
        )
    events = []
    for r in rows:
        if r["next_renewal_date"].startswith(target_month):
            item = {
                "id": r["id"],
                "subscription_id": r["id"],
                "service_name": r["service_name"],
                "plan_name": r["plan_name"],
                "date": r["next_renewal_date"],
                "price_minor": r["price_minor"],
                "currency": r["currency"],
                "billing_interval": r["billing_interval"],
                "provenance": "user_recorded",
                "ledger_status": "projection_only",
            }
            item["subscriptions"] = [{**item}]
            events.append(item)
    return {"month": target_month, "events": events}


@router.get("/analytics/spending", status_code=200)
@router.get("/dashboard", status_code=200)
def get_spending_dashboard(principal: dict = Depends(get_current_principal)):
    with get_db(write=False) as db:
        return compute_personal_dashboard(db, principal["personal_account_id"])


@router.post("/monitoring/connect", status_code=200)
def connect_monitoring(payload: MonitoringConnectRequest, principal: dict = Depends(get_current_principal)):
    if payload.source_type not in ("browser_extension", "desktop_helper", "litellm", "official_api"):
        raise DomainError(422, "INVALID_SOURCE", f"Unsupported source_type: {payload.source_type}")
    clean_allowlist = [sanitize_domain_only(item) for item in payload.allowlist]
    now_iso = get_clock().now_iso()
    acc_id = principal["personal_account_id"]
    with get_db(write=True) as db:
        existing = db.fetchone(
            "SELECT id FROM personal_monitoring_sources WHERE account_id = %s AND source_type = %s",
            (acc_id, payload.source_type),
        )
        if existing:
            db.execute(
                "UPDATE personal_monitoring_sources SET enabled = 1, allowlist_json = %s, updated_at = %s WHERE id = %s",
                (json.dumps(clean_allowlist), now_iso, existing["id"]),
            )
        else:
            db.execute(
                """
                INSERT INTO personal_monitoring_sources (id, account_id, source_type, enabled, allowlist_json, updated_at)
                VALUES (%s, %s, %s, 1, %s, %s)
                """,
                (str(uuid.uuid4()), acc_id, payload.source_type, json.dumps(clean_allowlist), now_iso),
            )
    return {"source_type": payload.source_type, "enabled": True, "allowlist": clean_allowlist}


@router.post("/monitoring/revoke", status_code=200)
def revoke_monitoring(payload: MonitoringRevokeRequest, principal: dict = Depends(get_current_principal)):
    now_iso = get_clock().now_iso()
    acc_id = principal["personal_account_id"]
    with get_db(write=True) as db:
        db.execute(
            "UPDATE personal_monitoring_sources SET enabled = 0, updated_at = %s WHERE account_id = %s AND source_type = %s",
            (now_iso, acc_id, payload.source_type),
        )
    return {"source_type": payload.source_type, "enabled": False}


@router.get("/monitoring", status_code=200)
def list_monitoring(principal: dict = Depends(get_current_principal)):
    acc_id = principal["personal_account_id"]
    with get_db(write=False) as db:
        rows = db.fetchall(
            "SELECT source_type, enabled, allowlist_json, updated_at FROM personal_monitoring_sources WHERE account_id = %s",
            (acc_id,),
        )
    return {
        "sources": [
            {
                "source_type": r["source_type"],
                "enabled": bool(r["enabled"]),
                "allowlist": json.loads(r["allowlist_json"] or "[]"),
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]
    }


@router.post("/activity/aggregate", status_code=201)
@router.post("/activity/import", status_code=201)
def record_activity(body: dict[str, Any], principal: dict = Depends(get_current_principal)):
    # Privacy check: reject prompts, cookies, raw URLs, keystrokes (T071)
    lower_keys = {str(k).lower() for k in body.keys()}
    forbidden_found = lower_keys & _FORBIDDEN_PRIVACY_KEYS
    if forbidden_found:
        raise DomainError(
            422,
            "PRIVACY_VIOLATION",
            f"Payload contains disallowed private fields: {sorted(forbidden_found)}",
            {"rejected_fields": sorted(forbidden_found)},
        )

    source = body.get("source", "ALLOWED_BROWSER_ACTIVITY")
    if source not in ("ALLOWED_BROWSER_ACTIVITY", "ALLOWED_DESKTOP_ACTIVITY", "MANUAL"):
        raise DomainError(422, "INVALID_ACTIVITY_SOURCE", f"Unsupported personal activity source: {source}")

    domain_or_app = sanitize_domain_only(str(body.get("domain") or body.get("domain_or_app") or body.get("app_name") or ""))
    if not domain_or_app:
        raise DomainError(422, "MISSING_TARGET", "domain_or_app is required.")

    active_seconds = int(body.get("observed_active_seconds") or body.get("active_seconds") or 0)
    if active_seconds < 0:
        raise DomainError(422, "INVALID_DURATION", "active_seconds must be non-negative.")

    acc_id = principal["personal_account_id"]
    now_iso = get_clock().now_iso()
    occurred_at = str(body.get("occurred_at") or now_iso)

    with get_db(write=True) as db:
        if source in ("ALLOWED_BROWSER_ACTIVITY", "ALLOWED_DESKTOP_ACTIVITY"):
            src_type = "browser_extension" if source == "ALLOWED_BROWSER_ACTIVITY" else "desktop_helper"
            cfg = db.fetchone(
                "SELECT enabled, allowlist_json FROM personal_monitoring_sources WHERE account_id = %s AND source_type = %s",
                (acc_id, src_type),
            )
            if cfg:
                if not cfg["enabled"]:
                    raise DomainError(403, "SOURCE_DISABLED", f"Monitoring source '{src_type}' is disabled.")
                allowlist = [a.lower() for a in json.loads(cfg["allowlist_json"] or "[]")]
                if allowlist and domain_or_app not in allowlist:
                    raise DomainError(
                        403,
                        "DOMAIN_NOT_ALLOWLISTED",
                        f"'{domain_or_app}' is not in your consented allowlist.",
                    )

        act_id = str(uuid.uuid4())
        db.execute(
            """
            INSERT INTO personal_activity (
                id, account_id, personal_subscription_id, source, domain_or_app,
                observed_active_seconds, visit_count, tokens, reliability, quality_note,
                occurred_at, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, 'OBSERVED', %s, %s, %s)
            """,
            (
                act_id,
                acc_id,
                body.get("personal_subscription_id"),
                source,
                domain_or_app,
                active_seconds,
                int(body.get("visit_count", 1)),
                "Observed foreground/tab activity only; token count unavailable.",
                occurred_at,
                now_iso,
            ),
        )
    return {
        "id": act_id,
        "account_id": acc_id,
        "source": source,
        "domain_or_app": domain_or_app,
        "observed_active_seconds": active_seconds,
        "tokens": None,
        "reliability": "OBSERVED",
        "quality_note": "Observed foreground/tab activity only; token count unavailable.",
    }


@router.get("/usage", status_code=200)
def get_personal_usage(account_id: str | None = None, provider: str | None = None, principal: dict = Depends(get_current_principal)):
    own_acc = principal["personal_account_id"]
    if account_id and account_id != own_acc:
        raise DomainError(403, "FORBIDDEN", "Cannot access another user's personal activity.")

    if provider and provider.lower() in ("unsupported_desktop", "cursor_native", "antigravity_desktop"):
        return {
            "account_id": own_acc,
            "provider": provider,
            "requests_count": None,
            "tokens": None,
            "token_count_quality": "unavailable",
            "quality_note": "Official API or routed gateway unavailable for this desktop application; tokens are unknown (null), not zero.",
            "records": [],
        }

    with get_db(write=False) as db:
        activities = db.fetchall(
            "SELECT * FROM personal_activity WHERE account_id = %s ORDER BY occurred_at DESC",
            (own_acc,),
        )
        ext_records = db.fetchall(
            "SELECT * FROM external_usage_records WHERE account_id = %s AND organization_id IS NULL ORDER BY occurred_at DESC",
            (own_acc,),
        )

    total_observed_sec = sum((a["observed_active_seconds"] or 0) for a in activities)
    has_tokens = any(r["total_tokens"] is not None for r in ext_records)
    total_tokens = sum((r["total_tokens"] or 0) for r in ext_records) if has_tokens else None

    return {
        "account_id": own_acc,
        "observed_active_seconds": total_observed_sec,
        "observed_active_minutes": total_observed_sec // 60,
        "tokens": total_tokens,
        "token_count_quality": "verified" if has_tokens else "unavailable",
        "activity_records": activities,
        "ai_records": ext_records,
    }


@router.delete("/activity/{act_id}", status_code=204)
def delete_activity(act_id: str, principal: dict = Depends(get_current_principal)):
    acc_id = principal["personal_account_id"]
    with get_db(write=True) as db:
        row = db.fetchone("SELECT id FROM personal_activity WHERE id = %s AND account_id = %s", (act_id, acc_id))
        if not row:
            raise DomainError(404, "NOT_FOUND", "Activity record not found.")
        db.execute("DELETE FROM personal_activity WHERE id = %s AND account_id = %s", (act_id, acc_id))
    return None


@router.delete("/history", status_code=200)
def delete_all_personal_history(principal: dict = Depends(get_current_principal)):
    acc_id = principal["personal_account_id"]
    with get_db(write=True) as db:
        db.execute("DELETE FROM personal_activity WHERE account_id = %s", (acc_id,))
        db.execute("DELETE FROM activity_daily WHERE account_id = %s", (acc_id,))
        db.execute("DELETE FROM external_usage_records WHERE account_id = %s AND organization_id IS NULL", (acc_id,))
        db.execute(
            "DELETE FROM reports WHERE account_id = %s AND organization_id IS NULL",
            (acc_id,),
        )
        record_audit_log(
            db,
            actor_user_id=principal["user"]["id"],
            tenant_id=acc_id,
            action="personal.history_deleted",
            resource_type="account",
            resource_id=acc_id,
        )
    return {"deleted": True, "account_id": acc_id}


@router.get("/export", status_code=200)
def export_personal_data(principal: dict = Depends(get_current_principal)):
    acc_id = principal["personal_account_id"]
    with get_db(write=False) as db:
        subs = db.fetchall("SELECT * FROM personal_subscriptions WHERE account_id = %s", (acc_id,))
        acts = db.fetchall("SELECT * FROM personal_activity WHERE account_id = %s", (acc_id,))
        ai_recs = db.fetchall("SELECT * FROM external_usage_records WHERE account_id = %s AND organization_id IS NULL", (acc_id,))
    return {
        "exported_at": get_clock().now_iso(),
        "account_id": acc_id,
        "subscriptions": subs,
        "activity": acts,
        "ai_usage": ai_recs,
    }


@router.post("/reminders/tick", status_code=200)
@router.post("/reminders/run", status_code=200)
def trigger_reminders(principal: dict = Depends(get_current_principal)):
    with get_db(write=True) as db:
        return schedule_personal_renewal_reminders(db, account_id=principal["personal_account_id"])


# ── R4: Chrome MV3 Extension Pairing, Scoped Token Auth, Daily Activity Sync ───────────


class ExtensionPairExchange(BaseModel):
    pair_code: str = Field(..., min_length=4)
    device_name: str = "chrome-desktop"
    allowlist: list[str] = Field(default_factory=lambda: ["claude.ai", "chatgpt.com"])
    permissions: list[str] = Field(default_factory=lambda: ["tabs", "storage", "alarms", "idle"])


class ExtensionActivityRecord(BaseModel):
    record_id: str = Field(..., min_length=1, max_length=160)
    day: str
    domain: str
    active_seconds: int | None = None
    visits: int = 1
    coverage: str = "live_focused"


class ExtensionActivitySyncRequest(BaseModel):
    source_device: str = "chrome-desktop"
    records: list[dict[str, Any]] = Field(default_factory=list)


def _resolve_extension_or_user_account(request: Request, db: Any) -> tuple[str, dict[str, Any] | None]:
    """Authenticate either scoped ext_live_ token or logged-in user JWT; reject revoked tokens."""
    ext_token = request.headers.get("x-subsphere-extension-token", "").strip()
    auth = request.headers.get("authorization", "")
    if not ext_token and auth.lower().startswith("bearer "):
        cand = auth[7:].strip()
        if cand.startswith("ext_live_"):
            ext_token = cand

    if ext_token:
        t_hash = hash_token(ext_token)
        conn = db.fetchone(
            "SELECT * FROM tracking_connections WHERE token_hash = %s AND source_type = 'browser_extension'",
            (t_hash,),
        )
        if not conn:
            raise DomainError(401, "EXTENSION_INVALID_TOKEN", "Extension token is invalid.")
        if conn.get("revoked_at") is not None or conn.get("status") == "revoked":
            raise DomainError(401, "EXTENSION_REVOKED", "Extension connection has been revoked.")
        if conn.get("status") == "paused":
            raise DomainError(403, "EXTENSION_PAUSED", "Extension tracking is currently paused.")
        return conn["account_id"], conn

    principal = get_current_principal(request)
    conn = db.fetchone(
        """
        SELECT * FROM tracking_connections
        WHERE account_id = %s AND source_type = 'browser_extension' AND revoked_at IS NULL
        ORDER BY created_at DESC LIMIT 1
        """,
        (principal["personal_account_id"],),
    )
    if conn and conn.get("status") == "revoked":
        raise DomainError(401, "EXTENSION_REVOKED", "Extension connection has been revoked.")
    return principal["personal_account_id"], conn


@router.post("/extension/pair-code", status_code=201)
def create_extension_pair_code(principal: dict = Depends(get_current_principal)):
    from datetime import timedelta

    now = get_clock().now()
    now_iso = get_clock().now_iso()
    expires_iso = (now + timedelta(minutes=15)).isoformat().replace("+00:00", "Z")
    raw_code = f"PAIR-{generate_token(6).upper()}"
    conn_id = str(uuid.uuid4())
    acc_id = principal["personal_account_id"]

    with get_db(write=True) as db:
        db.execute(
            """
            INSERT INTO tracking_connections (
                id, account_id, organization_id, source_type, status, device_name,
                pair_code_hash, pair_expires_at, permissions_json, allowlist_json, created_at
            )
            VALUES (%s, %s, NULL, 'browser_extension', 'pending_pair', 'chrome-desktop', %s, %s, '["tabs","storage","alarms","idle"]', '["claude.ai","chatgpt.com"]', %s)
            """,
            (conn_id, acc_id, hash_token(raw_code), expires_iso, now_iso),
        )
    return {
        "connection_id": conn_id,
        "pair_code": raw_code,
        "expires_at": expires_iso,
        "status": "pending_pair",
    }


@router.post("/extension/pair", status_code=200)
def pair_extension(payload: ExtensionPairExchange):
    now_iso = get_clock().now_iso()
    code_hash = hash_token(payload.pair_code.strip())
    clean_allowlist = [sanitize_domain_only(d) for d in payload.allowlist if d.strip()]
    ext_token = f"ext_live_{generate_token(24)}"
    tok_hash = hash_token(ext_token)

    with get_db(write=True) as db:
        conn = db.fetchone(
            "SELECT * FROM tracking_connections WHERE pair_code_hash = %s AND source_type = 'browser_extension'",
            (code_hash,),
        )
        if not conn:
            raise DomainError(404, "INVALID_PAIR_CODE", "Pairing code not found or invalid.")
        if conn.get("status") not in ("pending_pair",):
            raise DomainError(409, "PAIR_CODE_USED", "Pairing code has already been redeemed.")
        if conn.get("pair_expires_at") and conn["pair_expires_at"] < now_iso:
            raise DomainError(400, "PAIR_CODE_EXPIRED", "Pairing code has expired; generate a new code.")

        db.execute(
            """
            UPDATE tracking_connections
            SET status = 'connected', device_name = %s, token_hash = %s,
                permissions_json = %s, allowlist_json = %s, last_seen = %s
            WHERE id = %s
            """,
            (
                payload.device_name.strip() or "chrome-desktop",
                tok_hash,
                json.dumps(payload.permissions),
                json.dumps(clean_allowlist),
                now_iso,
                conn["id"],
            ),
        )
        # Keep personal_monitoring_sources synchronized
        existing_ms = db.fetchone(
            "SELECT id FROM personal_monitoring_sources WHERE account_id = %s AND source_type = 'browser_extension'",
            (conn["account_id"],),
        )
        if existing_ms:
            db.execute(
                "UPDATE personal_monitoring_sources SET enabled = 1, allowlist_json = %s, updated_at = %s WHERE id = %s",
                (json.dumps(clean_allowlist), now_iso, existing_ms["id"]),
            )
        else:
            db.execute(
                """
                INSERT INTO personal_monitoring_sources (id, account_id, source_type, enabled, allowlist_json, updated_at)
                VALUES (%s, %s, 'browser_extension', 1, %s, %s)
                """,
                (str(uuid.uuid4()), conn["account_id"], json.dumps(clean_allowlist), now_iso),
            )

    return {
        "connection_id": conn["id"],
        "status": "connected",
        "extension_token": ext_token,
        "allowlist": clean_allowlist,
        "permissions": payload.permissions,
        "paired_at": now_iso,
    }


@router.get("/extension/status", status_code=200)
def get_extension_status(principal: dict = Depends(get_current_principal)):
    acc_id = principal["personal_account_id"]
    with get_db(write=False) as db:
        conns = db.fetchall(
            """
            SELECT id, source_type, status, device_name, permissions_json, allowlist_json,
                   last_seen, last_synced, revoked_at, created_at
            FROM tracking_connections
            WHERE account_id = %s AND source_type = 'browser_extension'
            ORDER BY created_at DESC LIMIT 5
            """,
            (acc_id,),
        )
        daily_rows = db.fetchall(
            """
            SELECT day, domain_or_app, SUM(COALESCE(active_seconds, 0)) AS active_seconds,
                   SUM(visits) AS visits, MAX(coverage) AS coverage
            FROM activity_daily
            WHERE account_id = %s
            GROUP BY day, domain_or_app
            ORDER BY day DESC, domain_or_app ASC
            LIMIT 60
            """,
            (acc_id,),
        )
    active = next((c for c in conns if c["status"] == "connected" and c["revoked_at"] is None), conns[0] if conns else None)
    return {
        "connected": bool(active and active["status"] == "connected" and active["revoked_at"] is None),
        "status": active["status"] if active else "not_paired",
        "connection": {
            **active,
            "permissions": json.loads(active["permissions_json"] or "[]"),
            "allowlist": json.loads(active["allowlist_json"] or "[]"),
        }
        if active
        else None,
        "daily_aggregates": daily_rows,
    }


@router.post("/extension/revoke", status_code=200)
def revoke_extension(
    delete_history: bool = False,
    principal: dict = Depends(get_current_principal),
):
    acc_id = principal["personal_account_id"]
    now_iso = get_clock().now_iso()
    with get_db(write=True) as db:
        db.execute(
            """
            UPDATE tracking_connections
            SET status = 'revoked', revoked_at = %s
            WHERE account_id = %s AND source_type = 'browser_extension' AND revoked_at IS NULL
            """,
            (now_iso, acc_id),
        )
        db.execute(
            "UPDATE personal_monitoring_sources SET enabled = 0, updated_at = %s WHERE account_id = %s AND source_type = 'browser_extension'",
            (now_iso, acc_id),
        )
        if delete_history:
            db.execute("DELETE FROM personal_activity WHERE account_id = %s AND source = 'ALLOWED_BROWSER_ACTIVITY'", (acc_id,))
            db.execute("DELETE FROM activity_daily WHERE account_id = %s", (acc_id,))
        record_audit_log(
            db,
            actor_user_id=principal["user"]["id"],
            tenant_id=acc_id,
            action="extension.revoked",
            resource_type="tracking_connection",
            resource_id=acc_id,
            metadata={"deleted_history": delete_history},
        )
    return {"revoked": True, "deleted_history": delete_history, "revoked_at": now_iso}


@router.post("/activity/sync", status_code=200)
def sync_extension_activity(request: Request, body: dict[str, Any]):
    """Batch daily aggregate sync from Chromium MV3 extension with allowlist, privacy, and dedupe checks."""
    now_iso = get_clock().now_iso()
    source_device = str(body.get("source_device") or "chrome-desktop").strip()
    records = body.get("records")
    if not isinstance(records, list):
        raise DomainError(422, "INVALID_RECORDS", "records must be a list of daily aggregate objects.")

    with get_db(write=True) as db:
        acc_id, conn = _resolve_extension_or_user_account(request, db)
        allowlist: list[str] = []
        if conn and conn.get("allowlist_json"):
            allowlist = [a.lower() for a in json.loads(conn["allowlist_json"])]
        else:
            ms = db.fetchone(
                "SELECT enabled, allowlist_json FROM personal_monitoring_sources WHERE account_id = %s AND source_type = 'browser_extension'",
                (acc_id,),
            )
            if ms:
                if not ms["enabled"]:
                    raise DomainError(403, "SOURCE_DISABLED", "Browser extension monitoring is disabled.")
                allowlist = [a.lower() for a in json.loads(ms["allowlist_json"] or "[]")]

        accepted = 0
        duplicates_skipped = 0
        rejected_privacy = 0

        for item in records:
            if not isinstance(item, dict):
                continue
            item_keys = {str(k).lower() for k in item.keys()}
            if item_keys & (_FORBIDDEN_PRIVACY_KEYS | {"title", "page_title", "url", "query"}):
                raise DomainError(
                    422,
                    "PRIVACY_VIOLATION",
                    f"Extension payload contains disallowed private fields: {sorted(item_keys & (_FORBIDDEN_PRIVACY_KEYS | {'title', 'page_title', 'url', 'query'}))}",
                )
            domain = sanitize_domain_only(str(item.get("domain") or item.get("domain_or_app") or ""))
            if not domain:
                raise DomainError(422, "MISSING_DOMAIN", "Each record requires a domain name.")
            if allowlist and not any(domain == a or domain.endswith("." + a) for a in allowlist):
                raise DomainError(403, "DOMAIN_NOT_ALLOWLISTED", f"Domain '{domain}' is not in your consented allowlist.")

            record_id = str(item.get("record_id") or "").strip()
            day_str = str(item.get("day") or now_iso[:10])[:10]
            if not record_id:
                record_id = f"{day_str}:{domain}"

            existing = db.fetchone(
                """
                SELECT id FROM activity_daily
                WHERE account_id = %s AND source_device = %s AND record_id = %s
                """,
                (acc_id, source_device, record_id),
            )
            if existing:
                duplicates_skipped += 1
                continue

            active_sec_raw = item.get("active_seconds", item.get("observed_active_seconds"))
            active_seconds = int(active_sec_raw) if active_sec_raw is not None else None
            if active_seconds is not None and active_seconds < 0:
                raise DomainError(422, "INVALID_DURATION", "active_seconds must be non-negative.")
            visits = max(0, int(item.get("visits", item.get("visit_count", 1))))
            coverage = str(item.get("coverage") or "live_focused")

            db.execute(
                """
                INSERT INTO activity_daily (
                    id, account_id, personal_subscription_id, source_id, source_device,
                    record_id, domain_or_app, day, visits, active_seconds, coverage, created_at, updated_at
                )
                VALUES (%s, %s, NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(uuid.uuid4()),
                    acc_id,
                    conn["id"] if conn else None,
                    source_device,
                    record_id,
                    domain,
                    day_str,
                    visits,
                    active_seconds,
                    coverage,
                    now_iso,
                    now_iso,
                ),
            )
            q_note = (
                "History import (may include synced browser visits); active focused duration not reconstructed."
                if coverage.startswith("history_import")
                else "Observed allowlisted browser tab focus while window active and user not idle; token count unavailable."
            )
            db.execute(
                """
                INSERT INTO personal_activity (
                    id, account_id, personal_subscription_id, source, domain_or_app,
                    observed_active_seconds, visit_count, tokens, reliability, quality_note,
                    occurred_at, created_at
                )
                VALUES (%s, %s, NULL, 'ALLOWED_BROWSER_ACTIVITY', %s, %s, %s, NULL, 'OBSERVED', %s, %s, %s)
                """,
                (
                    str(uuid.uuid4()),
                    acc_id,
                    domain,
                    active_seconds,
                    visits,
                    q_note,
                    f"{day_str}T12:00:00Z",
                    now_iso,
                ),
            )
            accepted += 1

        if conn:
            db.execute(
                "UPDATE tracking_connections SET last_seen = %s, last_synced = %s WHERE id = %s",
                (now_iso, now_iso, conn["id"]),
            )

    return {
        "accepted": accepted,
        "duplicates_skipped": duplicates_skipped,
        "rejected_privacy": rejected_privacy,
        "synced_at": now_iso,
    }


# ── R1: Unified Month Calendar API (Individual & Provider isolated) ───────────────────


@calendar_router.get("/calendar/events", status_code=200)
def get_calendar_events(
    view: str = Query(default="individual"),
    from_date: str | None = Query(default=None, alias="from"),
    to_date: str | None = Query(default=None, alias="to"),
    organization_id: str | None = Query(default=None),
    category: str | None = Query(default=None),
    status: str | None = Query(default=None),
    principal: dict = Depends(get_current_principal),
):
    from datetime import timedelta

    now = get_clock().now()
    if from_date:
        start_d = date.fromisoformat(from_date[:10])
    else:
        start_d = date(now.year, now.month, 1)
    if to_date:
        end_d = date.fromisoformat(to_date[:10]) + timedelta(days=1)
    else:
        # Default to end of start_d's month + 1 day (half-open)
        if start_d.month == 12:
            end_d = date(start_d.year + 1, 1, 1)
        else:
            end_d = date(start_d.year, start_d.month + 1, 1)

    events: list[dict[str, Any]] = []

    if view == "individual":
        acc_id = principal["personal_account_id"]
        with get_db(write=False) as db:
            subs = db.fetchall(
                "SELECT * FROM personal_subscriptions WHERE account_id = %s ORDER BY next_renewal_date",
                (acc_id,),
            )
            overrides = db.fetchall(
                "SELECT * FROM calendar_event_overrides WHERE account_id = %s AND event_date >= %s AND event_date < %s",
                (acc_id, start_d.isoformat(), end_d.isoformat()),
            )
        for s in subs:
            if category and (s["category"] or "").lower() != category.lower():
                continue
            t_stat = s.get("tracking_status") or "tracking_active"
            if status and status != "all":
                if status == "active" and t_stat in ("stopped_tracking", "canceled_at_provider"):
                    continue
                if status == "trial" and s["product_type"] != "trial":
                    continue
                if status == "paused" and t_stat != "tracking_paused":
                    continue
            first_ren = date.fromisoformat(s["next_renewal_date"])
            if t_stat not in ("stopped_tracking", "canceled_at_provider") and s["status"] != "cancelled_user_recorded":
                occ_dates = project_occurrences_in_range(
                    first_ren,
                    s["billing_interval"],
                    int(s["anchor_day"] or first_ren.day),
                    start_d,
                    end_d,
                )
                for od in occ_dates:
                    ev_type = "trial_expires" if s["product_type"] == "trial" else "renewal_due"
                    events.append(
                        {
                            "id": f"cal_sub_{s['id']}_{od.isoformat()}",
                            "subscription_id": s["id"],
                            "view": "individual",
                            "title": f"{s['service_name']} ({s['plan_name'] or s['billing_interval']})",
                            "service_name": s["service_name"],
                            "plan_name": s["plan_name"],
                            "category": s["category"],
                            "date": od.isoformat(),
                            "amount_minor": s["price_minor"],
                            "currency": s["currency"],
                            "billing_interval": s["billing_interval"],
                            "product_type": s["product_type"],
                            "event_type": ev_type,
                            "color": "amber" if ev_type == "trial_expires" else "blue",
                            "status": s["status"],
                            "tracking_status": t_stat,
                            "timezone": s.get("timezone") or "UTC",
                            "reminder_offsets_days": json.loads(s["reminder_offsets_json"] or "[7,3,1]"),
                            "provenance": "user_recorded",
                        }
                    )
            if s.get("trial_end_date") and s["product_type"] != "trial":
                td = date.fromisoformat(s["trial_end_date"])
                if start_d <= td < end_d:
                    events.append(
                        {
                            "id": f"cal_trial_{s['id']}_{td.isoformat()}",
                            "subscription_id": s["id"],
                            "view": "individual",
                            "title": f"{s['service_name']} trial ends",
                            "service_name": s["service_name"],
                            "plan_name": s["plan_name"],
                            "category": s["category"],
                            "date": td.isoformat(),
                            "amount_minor": s["price_minor"],
                            "currency": s["currency"],
                            "billing_interval": s["billing_interval"],
                            "product_type": s["product_type"],
                            "event_type": "trial_expires",
                            "color": "amber",
                            "status": s["status"],
                            "tracking_status": t_stat,
                            "timezone": s.get("timezone") or "UTC",
                            "reminder_offsets_days": json.loads(s["reminder_offsets_json"] or "[7,3,1]"),
                            "provenance": "user_recorded",
                        }
                    )
            if s.get("start_date"):
                sd = date.fromisoformat(s["start_date"])
                if start_d <= sd < end_d:
                    events.append(
                        {
                            "id": f"cal_start_{s['id']}_{sd.isoformat()}",
                            "subscription_id": s["id"],
                            "view": "individual",
                            "title": f"{s['service_name']} activated",
                            "service_name": s["service_name"],
                            "plan_name": s["plan_name"],
                            "category": s["category"],
                            "date": sd.isoformat(),
                            "amount_minor": s["price_minor"],
                            "currency": s["currency"],
                            "billing_interval": s["billing_interval"],
                            "product_type": s["product_type"],
                            "event_type": "subscription_activated",
                            "color": "neutral",
                            "status": s["status"],
                            "tracking_status": t_stat,
                            "timezone": s.get("timezone") or "UTC",
                            "reminder_offsets_days": json.loads(s["reminder_offsets_json"] or "[7,3,1]"),
                            "provenance": "user_recorded",
                        }
                    )
        for ov in overrides:
            events.append(
                {
                    "id": f"cal_ov_{ov['id']}",
                    "subscription_id": None,
                    "view": "individual",
                    "title": ov["title"],
                    "service_name": ov["title"],
                    "plan_name": "",
                    "category": "Custom",
                    "date": ov["event_date"],
                    "amount_minor": ov["amount_minor"] or 0,
                    "currency": ov["currency"],
                    "billing_interval": "one_time",
                    "product_type": "one_time_purchase",
                    "event_type": ov["event_type"],
                    "color": "neutral",
                    "status": "scheduled",
                    "tracking_status": "tracking_active",
                    "timezone": "UTC",
                    "reminder_offsets_days": [],
                    "provenance": "manual_calendar_override",
                }
            )

    elif view == "provider":
        target_org = organization_id or principal.get("active_org_id")
        if not target_org:
            raise DomainError(422, "ORGANIZATION_REQUIRED", "organization_id is required for provider calendar view.")
        with get_db(write=False) as db:
            require_org_role(db, principal, target_org, {"owner", "admin", "billing", "staff", "developer"})
            invs = db.fetchall(
                """
                SELECT i.*, fo.subscriber_contact_id, sc.name AS subscriber_name
                FROM invoices i
                JOIN financial_obligations fo ON fo.id = i.obligation_id
                LEFT JOIN subscriber_contacts sc ON sc.id = fo.subscriber_contact_id
                WHERE i.organization_id = %s AND i.status != 'void'
                """,
                (target_org,),
            )
            pays = db.fetchall(
                """
                SELECT p.*, i.document_number
                FROM payments p
                JOIN invoices i ON i.id = p.invoice_id
                WHERE p.organization_id = %s AND p.status = 'confirmed'
                """,
                (target_org,),
            )
            ext_subs = db.fetchall(
                "SELECT * FROM external_subscriptions WHERE organization_id = %s AND status = 'active'",
                (target_org,),
            )
        seen_inv_ids: set[str] = set()
        for inv in invs:
            due_str = str(inv["due_at"])[:10]
            due_d = date.fromisoformat(due_str)
            if start_d <= due_d < end_d:
                seen_inv_ids.add(inv["id"])
                is_overdue = inv["status"] == "overdue"
                ev_type = "provider_membership_expiration" if is_overdue else "provider_receivable_due"
                events.append(
                    {
                        "id": f"cal_inv_{inv['id']}",
                        "invoice_id": inv["id"],
                        "subscriber_contact_id": inv.get("subscriber_contact_id"),
                        "view": "provider",
                        "title": f"{inv['subscriber_name'] or inv['document_number']} ({inv['status']})",
                        "service_name": inv["subscriber_name"] or "Subscriber Invoice",
                        "category": "Receivable",
                        "date": due_str,
                        "amount_minor": inv["total_minor"],
                        "currency": inv["currency"],
                        "billing_interval": "invoice",
                        "event_type": ev_type,
                        "color": "red" if is_overdue else ("green" if inv["status"] == "paid" else "amber"),
                        "status": inv["status"],
                        "provenance": "postgresql_ledger",
                    }
                )
        for p in pays:
            paid_str = str(p["paid_at"])[:10]
            paid_d = date.fromisoformat(paid_str)
            if start_d <= paid_d < end_d:
                events.append(
                    {
                        "id": f"cal_pay_{p['id']}",
                        "invoice_id": p["invoice_id"],
                        "view": "provider",
                        "title": f"Payment {p['document_number']}",
                        "service_name": p["document_number"],
                        "category": "Payment",
                        "date": paid_str,
                        "amount_minor": p["amount_minor"],
                        "currency": p["currency"],
                        "billing_interval": "payment",
                        "event_type": "provider_confirmed_payment",
                        "color": "green",
                        "status": "paid",
                        "provenance": "postgresql_ledger",
                    }
                )
        for es in ext_subs:
            r_str = str(es["next_renewal_date"])[:10]
            r_d = date.fromisoformat(r_str)
            if start_d <= r_d < end_d:
                events.append(
                    {
                        "id": f"cal_ext_{es['id']}_{r_str}",
                        "external_subscription_id": es["id"],
                        "view": "provider",
                        "title": f"{es['vendor_name']} {es['service_name']} ({es['seats_purchased']} seats)",
                        "service_name": es["service_name"],
                        "category": "Enterprise Payable",
                        "date": r_str,
                        "amount_minor": es["seats_purchased"] * es["unit_price_minor"],
                        "currency": es["currency"],
                        "billing_interval": es["billing_interval"],
                        "event_type": "provider_payable_due",
                        "color": "amber",
                        "status": es["status"],
                        "provenance": "postgresql_ledger",
                    }
                )
    else:
        raise DomainError(422, "INVALID_VIEW", "view must be individual or provider.")

    for ev in events:
        ev.setdefault("event_id", ev["id"])
        ev.setdefault("kind", ev.get("event_type", "renewal_due"))
        ev.setdefault("notes", "")

    events.sort(key=lambda e: (e["date"], e["title"]))
    return {
        "view": view,
        "from": start_d.isoformat(),
        "to": to_date[:10] if to_date else end_d.isoformat(),
        "events": events,
    }


@calendar_router.get("/calendar/events/{event_id}", status_code=200)
def get_calendar_event_detail(
    event_id: str,
    view: str = Query(default="individual"),
    from_date: str | None = Query(default=None, alias="from"),
    to_date: str | None = Query(default=None, alias="to"),
    organization_id: str | None = Query(default=None),
    principal: dict = Depends(get_current_principal),
):
    from datetime import timedelta

    if not from_date and not to_date:
        now_d = get_clock().now().date()
        from_date = (now_d - timedelta(days=365)).isoformat()
        to_date = (now_d + timedelta(days=730)).isoformat()
    res = get_calendar_events(
        view=view,
        from_date=from_date,
        to_date=to_date,
        organization_id=organization_id,
        category=None,
        status=None,
        principal=principal,
    )
    match = next((e for e in res["events"] if e["id"] == event_id or e.get("event_id") == event_id), None)
    if not match:
        raise DomainError(404, "EVENT_NOT_FOUND", "Calendar event not found in visible range.")
    return match

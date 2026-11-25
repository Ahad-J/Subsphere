"""Personal workspace services: subscriptions, privacy-safe activity, calendar, reminders."""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from app.audit.service import record_audit_log
from app.core.clock import get_clock
from app.core.errors import DomainError
from app.db.engine import DBConnection
from app.personal.domain import annual_equivalent_minor, monthly_equivalent_minor

_VALID_TRACKING_STATUSES = {"tracking_active", "tracking_paused", "stopped_tracking", "canceled_at_provider"}


def validate_timezone(tz_name: str | None) -> str:
    clean = (tz_name or "UTC").strip() or "UTC"
    try:
        ZoneInfo(clean)
    except Exception as exc:
        raise DomainError(422, "INVALID_TIMEZONE", f"Unsupported IANA timezone: {clean}") from exc
    return clean


def local_send_instant_utc(target_date: date, tz_name: str, send_hour: int = 9) -> str:
    tz = ZoneInfo(validate_timezone(tz_name))
    local_dt = datetime.combine(target_date, time(hour=max(0, min(23, send_hour)), minute=0), tzinfo=tz)
    return local_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def serialize_personal_subscription(row: dict[str, Any]) -> dict[str, Any]:
    offsets = json.loads(row.get("reminder_offsets_json") or "[7,3,1]")
    return {
        **row,
        "service_url": row.get("service_url") or "",
        "start_date": row.get("start_date"),
        "timezone": row.get("timezone") or "UTC",
        "notes": row.get("notes") or "",
        "recipient_email": row.get("recipient_email"),
        "tracking_status": row.get("tracking_status") or "tracking_active",
        "version": int(row.get("version") or 1),
        "auto_renew": bool(row["auto_renew"]),
        "reminder_offsets_days": offsets,
        "monthly_equivalent_minor": monthly_equivalent_minor(row["price_minor"], row["billing_interval"], row["product_type"]),
        "annual_equivalent_minor": annual_equivalent_minor(row["price_minor"], row["billing_interval"], row["product_type"]),
        "usage_status": row.get("monitoring_source") or "not_connected",
    }

_FORBIDDEN_PRIVACY_KEYS = {
    "prompt",
    "prompts",
    "response",
    "responses",
    "cookie",
    "cookies",
    "full_url",
    "raw_url",
    "keystroke",
    "keystrokes",
    "window_content",
    "screenshot",
    "authorization",
}


def sanitize_domain_only(value: str) -> str:
    raw = value.strip().lower()
    if "://" in raw:
        parsed = urlparse(raw)
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise DomainError(422, "PRIVACY_VIOLATION", "Full URLs with paths or query strings are rejected; submit domain hostname only.")
        return (parsed.hostname or "").lower()
    if "/" in raw or "?" in raw or "#" in raw:
        raise DomainError(422, "PRIVACY_VIOLATION", "Paths and query parameters are rejected; submit domain or app name only.")
    return raw


def reconcile_subscription_reminders(
    db: DBConnection,
    *,
    subscription_id: str,
    account_id: str,
    next_renewal_date: str,
    offsets: list[int],
    tracking_status: str,
    recipient: str,
    tz_name: str,
    send_hour_local: int = 9,
) -> dict[str, Any]:
    """Cancel obsolete queued/retrying jobs when schedule changes; keep already-sent jobs intact."""
    now_iso = get_clock().now_iso()
    valid_keys = {
        f"personal_renewal:{subscription_id}:{next_renewal_date}:{int(off)}"
        for off in offsets
    }
    is_active_tracking = tracking_status in ("tracking_active", "tracking_paused")

    pending_jobs = db.fetchall(
        """
        SELECT id, logical_key, status FROM notification_jobs
        WHERE logical_key LIKE %s AND status IN ('queued', 'retrying')
        """,
        (f"personal_renewal:{subscription_id}:%",),
    )
    suppressed_count = 0
    for j in pending_jobs:
        if (not is_active_tracking) or (j["logical_key"] not in valid_keys):
            db.execute(
                """
                UPDATE notification_jobs
                SET status = 'suppressed', last_error = 'Rescheduled or tracking stopped', updated_at = %s
                WHERE id = %s AND status IN ('queued', 'retrying')
                """,
                (now_iso, j["id"]),
            )
            suppressed_count += 1

    # Sync reminder_rules for this subscription
    db.execute("DELETE FROM reminder_rules WHERE subscription_id = %s", (subscription_id,))
    if is_active_tracking and recipient:
        for off in offsets:
            db.execute(
                """
                INSERT INTO reminder_rules (
                    id, account_id, organization_id, subscription_id, recipient, days_before,
                    enabled, channel, timezone, send_hour_local, created_at, updated_at
                )
                VALUES (%s, %s, NULL, %s, %s, %s, 1, 'email', %s, %s, %s, %s)
                ON CONFLICT (subscription_id, recipient, days_before, channel) DO UPDATE SET
                    enabled = 1, timezone = excluded.timezone, send_hour_local = excluded.send_hour_local, updated_at = excluded.updated_at
                """,
                (
                    str(uuid.uuid4()),
                    account_id,
                    subscription_id,
                    recipient.strip().lower(),
                    int(off),
                    tz_name,
                    send_hour_local,
                    now_iso,
                    now_iso,
                ),
            )
    return {"suppressed_jobs": suppressed_count, "active_offsets": offsets if is_active_tracking else []}


def create_personal_subscription(
    db: DBConnection,
    *,
    account_id: str,
    user_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    now_iso = get_clock().now_iso()
    sub_id = str(uuid.uuid4())
    renewal = date.fromisoformat(str(data["next_renewal_date"]))
    anchor_day = int(data.get("anchor_day") or renewal.day)
    if anchor_day < 1 or anchor_day > 31:
        anchor_day = renewal.day
    product_type = data.get("product_type", "recurring_subscription")
    billing_interval = data["billing_interval"]
    price_minor = int(data["price_minor"])
    currency = data.get("currency", "USD").upper()
    offsets = data.get("reminder_offsets_days")
    if offsets is None:
        offsets = [7, 3, 1]
    tz_name = validate_timezone(data.get("timezone"))
    tracking_status = data.get("tracking_status") or "tracking_active"
    if tracking_status not in _VALID_TRACKING_STATUSES:
        raise DomainError(422, "INVALID_TRACKING_STATUS", f"Unsupported tracking_status: {tracking_status}")

    user_row = db.fetchone("SELECT email FROM users WHERE id = %s", (user_id,))
    recipient_email = (data.get("recipient_email") or (user_row["email"] if user_row else "") or "").strip().lower()
    start_date_str = str(data["start_date"]) if data.get("start_date") else get_clock().now().date().isoformat()
    sub_status = "cancelled_user_recorded" if tracking_status in ("stopped_tracking", "canceled_at_provider") else "active_user_recorded"

    db.execute(
        """
        INSERT INTO personal_subscriptions (
            id, account_id, service_name, service_url, category, product_type, plan_name,
            price_minor, currency, billing_interval, next_renewal_date, start_date, trial_end_date,
            anchor_day, auto_renew, timezone, notes, recipient_email, reminder_offsets_json,
            monitoring_source, tracking_status, status, version, created_at, updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s)
        """,
        (
            sub_id,
            account_id,
            data["service_name"].strip(),
            str(data.get("service_url") or "").strip(),
            data.get("category", "General"),
            product_type,
            data.get("plan_name", ""),
            price_minor,
            currency,
            billing_interval,
            renewal.isoformat(),
            start_date_str,
            str(data["trial_end_date"]) if data.get("trial_end_date") else None,
            anchor_day,
            1 if data.get("auto_renew", True) else 0,
            tz_name,
            str(data.get("notes") or ""),
            recipient_email,
            json.dumps([int(x) for x in offsets]),
            data.get("monitoring_source", "not_connected"),
            tracking_status,
            sub_status,
            now_iso,
            now_iso,
        ),
    )

    reconcile_subscription_reminders(
        db,
        subscription_id=sub_id,
        account_id=account_id,
        next_renewal_date=renewal.isoformat(),
        offsets=[int(x) for x in offsets],
        tracking_status=tracking_status,
        recipient=recipient_email,
        tz_name=tz_name,
    )

    record_audit_log(
        db,
        actor_user_id=user_id,
        tenant_id=account_id,
        action="personal_subscription.created",
        resource_type="personal_subscription",
        resource_id=sub_id,
    )

    saved = db.fetchone("SELECT * FROM personal_subscriptions WHERE id = %s", (sub_id,))
    return serialize_personal_subscription(saved)


def compute_personal_dashboard(db: DBConnection, account_id: str) -> dict[str, Any]:
    subs = db.fetchall(
        "SELECT * FROM personal_subscriptions WHERE account_id = %s ORDER BY next_renewal_date",
        (account_id,),
    )
    now_date = get_clock().now().date()
    in_30_days = now_date + timedelta(days=30)

    by_currency: dict[str, dict[str, Any]] = {}
    category_breakdown: dict[str, dict[str, int]] = {}
    upcoming_renewals: list[dict[str, Any]] = []
    active_recurring = 0
    due_soon = 0
    for s in subs:
        t_stat = s.get("tracking_status") or "tracking_active"
        if t_stat in ("stopped_tracking", "canceled_at_provider") or s.get("status") == "cancelled_user_recorded":
            continue
        cur = s["currency"]
        if cur not in by_currency:
            by_currency[cur] = {
                "currency": cur,
                "monthly_equivalent_minor": 0,
                "annual_equivalent_minor": 0,
                "one_time_spend_minor": 0,
                "subscription_count": 0,
            }
        meq = monthly_equivalent_minor(s["price_minor"], s["billing_interval"], s["product_type"])
        aeq = annual_equivalent_minor(s["price_minor"], s["billing_interval"], s["product_type"])
        cat = s.get("category") or "other"
        if cur not in category_breakdown:
            category_breakdown[cur] = {}
        if s["product_type"] != "one_time_purchase" and s["billing_interval"] not in ("one_time", "custom"):
            active_recurring += 1
            by_currency[cur]["subscription_count"] += 1
            by_currency[cur]["monthly_equivalent_minor"] += meq
            by_currency[cur]["annual_equivalent_minor"] += aeq
            category_breakdown[cur][cat] = category_breakdown[cur].get(cat, 0) + meq
        else:
            by_currency[cur]["one_time_spend_minor"] += s["price_minor"]
            category_breakdown[cur][cat] = category_breakdown[cur].get(cat, 0) + s["price_minor"]

        r_date = date.fromisoformat(s["next_renewal_date"])
        if now_date <= r_date <= in_30_days:
            due_soon += 1
        upcoming_renewals.append(serialize_personal_subscription(s))

    acts = db.fetchall(
        "SELECT * FROM personal_activity WHERE account_id = %s ORDER BY occurred_at DESC",
        (account_id,),
    )
    observed_seconds = sum((a["observed_active_seconds"] or 0) for a in acts if a["reliability"] == "OBSERVED")

    ext_ai = db.fetchall(
        "SELECT * FROM external_usage_records WHERE account_id = %s AND organization_id IS NULL",
        (account_id,),
    )
    verified_tokens = sum((r["total_tokens"] or 0) for r in ext_ai if r["total_tokens"] is not None)
    has_tokens = any(r["total_tokens"] is not None for r in ext_ai)

    sources = db.fetchall(
        "SELECT source_type, enabled, allowlist_json, updated_at FROM personal_monitoring_sources WHERE account_id = %s",
        (account_id,),
    )
    source_map: dict[str, str] = {"liteLLM": "not_connected", "browser": "not_connected", "desktop": "not_connected"}
    for src in sources:
        if src["source_type"] == "litellm":
            source_map["liteLLM"] = "connected" if src["enabled"] else "disconnected"
        elif src["source_type"] == "browser_extension":
            source_map["browser"] = "observed_partial" if src["enabled"] else "disabled"
        elif src["source_type"] == "desktop_helper":
            source_map["desktop"] = "observed_partial" if src["enabled"] else "disabled"

    if any(a["source"] == "ALLOWED_DESKTOP_ACTIVITY" for a in acts) and source_map["desktop"] == "not_connected":
        source_map["desktop"] = "observed_partial"
    if any(a["source"] == "ALLOWED_BROWSER_ACTIVITY" for a in acts) and source_map["browser"] == "not_connected":
        source_map["browser"] = "observed_partial"

    return {
        "account_type": "individual",
        "currencies": list(by_currency.values()),
        "currency_totals": list(by_currency.values()),
        "upcoming_renewals": upcoming_renewals,
        "category_breakdown": category_breakdown,
        "active_recurring_subscriptions": active_recurring,
        "active_subscription_count": active_recurring,
        "due_in_next_30_days": due_soon,
        "usage_sources": source_map,
        "monitoring_sources": [
            {
                "source_type": s["source_type"],
                "enabled": bool(s["enabled"]),
                "allowlist": json.loads(s["allowlist_json"] or "[]"),
                "updated_at": s["updated_at"],
            }
            for s in sources
        ],
        "observed_active_minutes": observed_seconds // 60,
        "observed_active_seconds": observed_seconds,
        "token_count": verified_tokens if has_tokens else None,
        "token_count_quality": "verified" if has_tokens else "unavailable",
        "calculated_at": get_clock().now_iso(),
    }


def schedule_personal_renewal_reminders(db: DBConnection, account_id: str | None = None) -> dict[str, Any]:
    now = get_clock().now()
    today = now.date()
    now_iso = get_clock().now_iso()

    if account_id:
        subs = db.fetchall(
            """
            SELECT ps.*, u.email as user_email
            FROM personal_subscriptions ps
            JOIN accounts a ON a.id = ps.account_id
            JOIN users u ON u.id = a.owner_user_id
            WHERE ps.account_id = %s AND ps.product_type != 'one_time_purchase'
            """,
            (account_id,),
        )
    else:
        subs = db.fetchall(
            """
            SELECT ps.*, u.email as user_email
            FROM personal_subscriptions ps
            JOIN accounts a ON a.id = ps.account_id
            JOIN users u ON u.id = a.owner_user_id
            WHERE ps.product_type != 'one_time_purchase'
            """
        )

    created_jobs: list[str] = []
    skipped_duplicates = 0
    for s in subs:
        t_stat = s.get("tracking_status") or "tracking_active"
        if t_stat in ("stopped_tracking", "canceled_at_provider") or s.get("status") == "cancelled_user_recorded":
            continue
        # Respect user notification_preferences when present
        pref = db.fetchone(
            "SELECT email_enabled, primary_email, default_offsets_json, timezone FROM notification_preferences WHERE account_id = %s",
            (s["account_id"],),
        )
        if pref and pref.get("email_enabled") == 0:
            continue

        tz_name = (s.get("timezone") or (pref["timezone"] if pref and pref.get("timezone") else None) or "UTC")
        try:
            local_today = now.astimezone(ZoneInfo(tz_name)).date()
        except Exception:
            local_today = today
        r_date = date.fromisoformat(s["next_renewal_date"])
        days_until = (r_date - local_today).days
        raw_offsets = s.get("reminder_offsets_json") or (pref["default_offsets_json"] if pref and pref.get("default_offsets_json") else "[7,3,1]")
        offsets = [int(x) for x in json.loads(raw_offsets)]

        # Respect explicit reminder_rules overrides if any exist for this subscription
        rule_rows = db.fetchall("SELECT * FROM reminder_rules WHERE subscription_id = %s", (s["id"],))
        if rule_rows:
            offsets = [int(rr["days_before"]) for rr in rule_rows if rr["enabled"]]

        pref_email = pref["primary_email"] if pref and pref.get("primary_email") else None
        recipient = (s.get("recipient_email") or pref_email or s["user_email"] or "").strip().lower()
        if not recipient:
            continue

        if days_until in offsets:
            logical_key = f"personal_renewal:{s['id']}:{s['next_renewal_date']}:{days_until}"
            existing = db.fetchone("SELECT id FROM notification_jobs WHERE logical_key = %s", (logical_key,))
            if existing:
                skipped_duplicates += 1
                continue
            job_id = str(uuid.uuid4())
            price_fmt = f"{s['currency']} {s['price_minor'] / 100:.2f}"
            subject = f"Upcoming renewal — {s['service_name']} on {r_date.strftime('%B %d')}"
            body = (
                f"Hello,\n\n"
                f"Your tracked {s['service_name']} {s['plan_name']} subscription is due to renew on {r_date.strftime('%B %d, %Y')} ({tz_name}).\n"
                f"Your recorded price is {price_fmt} per {s['billing_interval']}.\n\n"
                f"This reminder is based on your saved subscription details.\n"
                f"SubSphere has not charged your account or confirmed payment.\n"
            )
            db.execute(
                """
                INSERT INTO notification_jobs (
                    id, logical_key, account_id, organization_id, rule_id, channel, recipient,
                    subject, body, status, attempts, created_at, updated_at
                )
                VALUES (%s, %s, %s, NULL, NULL, 'email', %s, %s, %s, 'queued', 0, %s, %s)
                """,
                (job_id, logical_key, s["account_id"], recipient, subject, body, now_iso, now_iso),
            )
            created_jobs.append(job_id)

    return {
        "queued_count": len(created_jobs),
        "scheduled_count": len(created_jobs),
        "job_ids": created_jobs,
        "skipped_duplicates": skipped_duplicates,
    }

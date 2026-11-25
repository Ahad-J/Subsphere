"""Notifications, SMTP dispatch, Sender Domains, Webhooks, Consents, and WhatsApp routes."""
from __future__ import annotations

import json
import socket
import time
import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, EmailStr, Field

from app.audit.service import record_audit_log
from app.auth.dependencies import get_current_principal, require_org_role
from app.core.clock import get_clock
from app.core.config import get_settings
from app.core.errors import DomainError
from app.core.security import (
    derive_masked_relay_address,
    generate_token,
    hash_token,
    is_safe_public_url,
    mask_email_address,
)
from app.db.engine import get_db
from app.notifications.dispatcher import dispatch_email_jobs
from app.notifications.whatsapp import send_whatsapp_template
from app.personal.service import reconcile_subscription_reminders, validate_timezone

router = APIRouter(prefix="/api/v1", tags=["notifications"])

_TEST_EMAIL_TIMESTAMPS: dict[str, list[float]] = {}


def reset_notification_rate_limits() -> None:
    _TEST_EMAIL_TIMESTAMPS.clear()


class NotificationPreferencesPatch(BaseModel):
    email_enabled: bool | None = None
    recipient_email: EmailStr | None = None
    primary_email: EmailStr | None = None
    default_offsets_days: list[int] | None = None
    send_hour_local: int | None = Field(default=None, ge=0, le=23)
    timezone: str | None = None
    monthly_report_opt_in: bool | None = None


class TestEmailRequest(BaseModel):
    recipient: EmailStr | None = None
    subject: str | None = None


class MaskedInboxReceiveRequest(BaseModel):
    sender: str = "billing@claude.ai"
    subject: str = "Subscription Renewal Notice (via SubSphere Mask)"
    body: str = ""


class ReminderRulesRequest(BaseModel):
    days_before: list[int] = Field(default_factory=lambda: [7, 3, 1])
    send_hour_local: int = Field(default=9, ge=0, le=23)
    timezone: str = "UTC"
    recipient_email: EmailStr | None = None
    enabled: bool = True
    channel: str = "email"


class DomainCreate(BaseModel):
    organization_id: str
    domain: str = Field(..., min_length=3, max_length=255)


class DomainVerifyInput(BaseModel):
    spf_ok: bool = True
    dkim_ok: bool = True
    dmarc_ok: bool = True


class ConsentUpdate(BaseModel):
    organization_id: str | None = None
    channel: str
    scope: str = "transactional_notices"
    status: str  # granted | revoked


class WebhookCreate(BaseModel):
    organization_id: str
    url: str
    events: list[str] = Field(default_factory=lambda: ["invoice.paid"])


class BrandedEmailSend(BaseModel):
    organization_id: str
    from_email: EmailStr
    recipient: EmailStr
    subject: str
    body: str


class WhatsAppSendRequest(BaseModel):
    organization_id: str
    subscriber_contact_id: str
    template_name: str = "receipt_notice"
    parameters: list[str] = Field(default_factory=list)
    variables: list[str] | None = None


class PostalWebhookEvent(BaseModel):
    provider_msg_id: str
    event: str  # delivered | bounced


@router.get("/notifications", status_code=200)
def list_notifications(organization_id: str | None = None, principal: dict = Depends(get_current_principal)):
    with get_db(write=False) as db:
        if organization_id:
            require_org_role(db, principal, organization_id, {"owner", "admin", "billing", "staff"})
            jobs = db.fetchall(
                "SELECT * FROM notification_jobs WHERE organization_id = %s ORDER BY created_at DESC LIMIT 100",
                (organization_id,),
            )
        else:
            jobs = db.fetchall(
                "SELECT * FROM notification_jobs WHERE account_id = %s ORDER BY created_at DESC LIMIT 100",
                (principal["personal_account_id"],),
            )
        for j in jobs:
            j["attempts_history"] = db.fetchall(
                "SELECT * FROM notification_attempts WHERE job_id = %s ORDER BY attempted_at DESC",
                (j["id"],),
            )
    return {"notifications": jobs}


@router.post("/notifications/dispatch", status_code=200)
def trigger_dispatch(organization_id: str | None = None, principal: dict = Depends(get_current_principal)):
    with get_db(write=True) as db:
        if organization_id:
            require_org_role(db, principal, organization_id, {"owner", "admin", "billing"})
        return dispatch_email_jobs(db, organization_id=organization_id)


@router.post("/notifications/send-branded", status_code=202)
def send_branded_email(payload: BrandedEmailSend, principal: dict = Depends(get_current_principal)):
    domain = payload.from_email.split("@")[-1].lower()
    now_iso = get_clock().now_iso()
    with get_db(write=True) as db:
        require_org_role(db, principal, payload.organization_id, {"owner", "admin", "billing"})
        dom = db.fetchone(
            "SELECT * FROM sender_domains WHERE organization_id = %s AND domain = %s",
            (payload.organization_id, domain),
        )
        if not dom or dom["status"] != "verified":
            raise DomainError(
                403,
                "UNVERIFIED_SENDER_DOMAIN",
                f"Custom sender domain '{domain}' is not DNS-verified; sending blocked.",
            )
        job_id = str(uuid.uuid4())
        db.execute(
            """
            INSERT INTO notification_jobs (
                id, logical_key, account_id, organization_id, rule_id, channel, recipient,
                subject, body, status, attempts, created_at, updated_at
            )
            VALUES (%s, %s, NULL, %s, NULL, 'email', %s, %s, %s, 'queued', 0, %s, %s)
            """,
            (
                job_id,
                f"branded:{payload.organization_id}:{uuid.uuid4().hex}",
                payload.organization_id,
                payload.recipient.lower(),
                payload.subject,
                payload.body,
                now_iso,
                now_iso,
            ),
        )
    return {"job_id": job_id, "status": "queued", "from_domain": domain}


@router.post("/domains", status_code=201)
def register_sender_domain(payload: DomainCreate, principal: dict = Depends(get_current_principal)):
    dom_name = payload.domain.strip().lower()
    now_iso = get_clock().now_iso()
    dom_id = str(uuid.uuid4())
    vtok = f"subsphere-verify={generate_token(12)}"
    with get_db(write=True) as db:
        require_org_role(db, principal, payload.organization_id, {"owner", "admin"})
        dup = db.fetchone(
            "SELECT id FROM sender_domains WHERE organization_id = %s AND domain = %s",
            (payload.organization_id, dom_name),
        )
        if dup:
            raise DomainError(409, "DOMAIN_EXISTS", "Domain already registered for this organization.")
        db.execute(
            """
            INSERT INTO sender_domains (id, organization_id, domain, spf_verified, dkim_verified, dmarc_verified, status, verification_token, created_at)
            VALUES (%s, %s, %s, 0, 0, 0, 'pending_dns', %s, %s)
            """,
            (dom_id, payload.organization_id, dom_name, vtok, now_iso),
        )
    return {
        "id": dom_id,
        "organization_id": payload.organization_id,
        "domain": dom_name,
        "status": "pending_dns",
        "verification_txt": vtok,
    }


@router.get("/domains", status_code=200)
def list_sender_domains(organization_id: str, principal: dict = Depends(get_current_principal)):
    with get_db(write=False) as db:
        require_org_role(db, principal, organization_id, {"owner", "admin", "billing"})
        rows = db.fetchall("SELECT * FROM sender_domains WHERE organization_id = %s", (organization_id,))
    return {"domains": rows}


@router.post("/domains/{domain_id}/verify", status_code=200)
def verify_sender_domain(domain_id: str, payload: DomainVerifyInput | None = None, principal: dict = Depends(get_current_principal)):
    now_iso = get_clock().now_iso()
    checks = payload or DomainVerifyInput()
    all_ok = checks.spf_ok and checks.dkim_ok and checks.dmarc_ok
    with get_db(write=True) as db:
        dom = db.fetchone("SELECT * FROM sender_domains WHERE id = %s", (domain_id,))
        if not dom:
            raise DomainError(404, "DOMAIN_NOT_FOUND", "Sender domain not found.")
        require_org_role(db, principal, dom["organization_id"], {"owner", "admin"})
        new_status = "verified" if all_ok else "pending_dns"
        db.execute(
            """
            UPDATE sender_domains
            SET spf_verified = %s, dkim_verified = %s, dmarc_verified = %s, status = %s, verified_at = %s
            WHERE id = %s
            """,
            (
                1 if checks.spf_ok else 0,
                1 if checks.dkim_ok else 0,
                1 if checks.dmarc_ok else 0,
                new_status,
                now_iso if all_ok else None,
                domain_id,
            ),
        )
    return {"id": domain_id, "domain": dom["domain"], "status": new_status}


@router.post("/contacts/{subscriber_id}/consents", status_code=200)
def update_contact_consent(subscriber_id: str, payload: ConsentUpdate, principal: dict = Depends(get_current_principal)):
    if payload.channel not in ("email", "whatsapp"):
        raise DomainError(422, "INVALID_CHANNEL", "channel must be email or whatsapp.")
    if payload.status not in ("granted", "revoked"):
        raise DomainError(422, "INVALID_STATUS", "status must be granted or revoked.")
    now_iso = get_clock().now_iso()
    with get_db(write=True) as db:
        sc = db.fetchone("SELECT id, organization_id FROM subscriber_contacts WHERE id = %s", (subscriber_id,))
        if not sc:
            raise DomainError(404, "SUBSCRIBER_NOT_FOUND", "Subscriber contact not found in organization.")
        org_id = payload.organization_id or sc["organization_id"]
        if sc["organization_id"] != org_id:
            raise DomainError(404, "SUBSCRIBER_NOT_FOUND", "Subscriber contact not found in organization.")
        require_org_role(db, principal, org_id, {"owner", "admin", "billing", "staff"})

        existing = db.fetchone(
            """
            SELECT id FROM consents
            WHERE organization_id = %s AND subscriber_contact_id = %s AND channel = %s AND scope = %s
            """,
            (org_id, subscriber_id, payload.channel, payload.scope),
        )
        if existing:
            db.execute(
                """
                UPDATE consents
                SET status = %s, granted_at = COALESCE(granted_at, %s), revoked_at = %s
                WHERE id = %s
                """,
                (
                    payload.status,
                    now_iso if payload.status == "granted" else None,
                    now_iso if payload.status == "revoked" else None,
                    existing["id"],
                ),
            )
            cid = existing["id"]
        else:
            cid = str(uuid.uuid4())
            db.execute(
                """
                INSERT INTO consents (id, organization_id, subscriber_contact_id, channel, scope, status, granted_at, revoked_at, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    cid,
                    org_id,
                    subscriber_id,
                    payload.channel,
                    payload.scope,
                    payload.status,
                    now_iso if payload.status == "granted" else None,
                    now_iso if payload.status == "revoked" else None,
                    now_iso,
                ),
            )
        record_audit_log(
            db,
            actor_user_id=principal["user"]["id"],
            tenant_id=org_id,
            action=f"consent.{payload.status}",
            resource_type="consent",
            resource_id=cid,
            metadata={"channel": payload.channel, "subscriber_contact_id": subscriber_id},
        )
    return {"id": cid, "subscriber_contact_id": subscriber_id, "channel": payload.channel, "status": payload.status}


@router.post("/notifications/whatsapp", status_code=200)
def send_whatsapp_message(payload: WhatsAppSendRequest, principal: dict = Depends(get_current_principal)):
    with get_db(write=True) as db:
        require_org_role(db, principal, payload.organization_id, {"owner", "admin", "billing"})
        return send_whatsapp_template(
            db,
            organization_id=payload.organization_id,
            subscriber_contact_id=payload.subscriber_contact_id,
            template_name=payload.template_name,
            parameters=payload.parameters or payload.variables or [],
            actor_user_id=principal["user"]["id"],
        )


@router.post("/webhooks", status_code=201)
def register_webhook(payload: WebhookCreate, principal: dict = Depends(get_current_principal)):
    safe, reason = is_safe_public_url(payload.url)
    if not safe:
        raise DomainError(422, "UNSAFE_WEBHOOK_URL", f"Webhook URL rejected by SSRF policy: {reason}")
    now_iso = get_clock().now_iso()
    wh_id = str(uuid.uuid4())
    raw_secret = f"whsec_{generate_token(24)}"
    with get_db(write=True) as db:
        require_org_role(db, principal, payload.organization_id, {"owner", "admin", "developer"})
        db.execute(
            """
            INSERT INTO webhook_endpoints (id, organization_id, url, secret_hash, events_json, active, created_at)
            VALUES (%s, %s, %s, %s, %s, 1, %s)
            """,
            (wh_id, payload.organization_id, payload.url.strip(), hash_token(raw_secret), json.dumps(payload.events), now_iso),
        )
    return {"id": wh_id, "organization_id": payload.organization_id, "url": payload.url.strip(), "events": payload.events, "signing_secret": raw_secret}


@router.post("/webhooks/postal", status_code=200)
def postal_delivery_webhook(payload: PostalWebhookEvent):
    now_iso = get_clock().now_iso()
    with get_db(write=True) as db:
        att = db.fetchone(
            "SELECT job_id FROM notification_attempts WHERE provider_msg_id = %s",
            (payload.provider_msg_id,),
        )
        if not att:
            raise DomainError(404, "MESSAGE_NOT_FOUND", "Provider message ID not found.")
        new_status = "delivered" if payload.event == "delivered" else "failed"
        db.execute(
            "UPDATE notification_jobs SET status = %s, updated_at = %s WHERE id = %s",
            (new_status, now_iso, att["job_id"]),
        )
    return {"job_id": att["job_id"], "status": new_status}


def _serialize_preferences(row: dict | None, user_email: str, acc_id: str) -> dict:
    if not row:
        return {
            "account_id": acc_id,
            "email_enabled": True,
            "primary_email": user_email,
            "recipient_email": user_email,
            "masked_email": mask_email_address(user_email),
            "relay_mask_email": derive_masked_relay_address(user_email),
            "default_offsets_days": [7, 3, 1],
            "send_hour_local": 9,
            "timezone": "UTC",
            "monthly_report_opt_in": False,
            "updated_at": None,
        }
    email = row["primary_email"] or user_email
    offsets = [int(x) for x in json.loads(row["default_offsets_json"] or "[7,3,1]")]
    return {
        "id": row["id"],
        "account_id": acc_id,
        "email_enabled": bool(row.get("email_enabled", 1)),
        "primary_email": email,
        "recipient_email": email,
        "masked_email": mask_email_address(email),
        "relay_mask_email": derive_masked_relay_address(email),
        "default_offsets_days": offsets,
        "send_hour_local": int(row["send_hour_local"]),
        "timezone": row["timezone"],
        "monthly_report_opt_in": bool(row["monthly_report_opt_in"]),
        "updated_at": row["updated_at"],
    }


@router.get("/notifications/preferences", status_code=200)
def get_notification_preferences(principal: dict = Depends(get_current_principal)):
    acc_id = principal["personal_account_id"]
    with get_db(write=False) as db:
        row = db.fetchone("SELECT * FROM notification_preferences WHERE account_id = %s", (acc_id,))
    return _serialize_preferences(row, principal["user"]["email"], acc_id)


@router.patch("/notifications/preferences", status_code=200)
def patch_notification_preferences(
    payload: NotificationPreferencesPatch,
    principal: dict = Depends(get_current_principal),
):
    acc_id = principal["personal_account_id"]
    now_iso = get_clock().now_iso()
    user_email = principal["user"]["email"].lower()
    with get_db(write=True) as db:
        row = db.fetchone("SELECT * FROM notification_preferences WHERE account_id = %s", (acc_id,))
        cur = _serialize_preferences(row, user_email, acc_id)

        new_enabled = payload.email_enabled if payload.email_enabled is not None else cur["email_enabled"]
        chosen_email = (payload.recipient_email or payload.primary_email or cur["primary_email"]).lower()
        new_offsets = (
            [int(x) for x in payload.default_offsets_days if int(x) >= 0]
            if payload.default_offsets_days is not None
            else cur["default_offsets_days"]
        )
        new_hour = payload.send_hour_local if payload.send_hour_local is not None else cur["send_hour_local"]
        new_tz = validate_timezone(payload.timezone) if payload.timezone is not None else cur["timezone"]
        new_opt_in = (
            payload.monthly_report_opt_in
            if payload.monthly_report_opt_in is not None
            else cur["monthly_report_opt_in"]
        )

        if row:
            db.execute(
                """
                UPDATE notification_preferences
                SET email_enabled = %s, primary_email = %s, default_offsets_json = %s,
                    send_hour_local = %s, timezone = %s, monthly_report_opt_in = %s, updated_at = %s
                WHERE account_id = %s
                """,
                (
                    1 if new_enabled else 0,
                    chosen_email,
                    json.dumps(new_offsets),
                    new_hour,
                    new_tz,
                    1 if new_opt_in else 0,
                    now_iso,
                    acc_id,
                ),
            )
        else:
            db.execute(
                """
                INSERT INTO notification_preferences (
                    id, account_id, organization_id, email_enabled, primary_email,
                    default_offsets_json, send_hour_local, timezone, monthly_report_opt_in, updated_at
                )
                VALUES (%s, %s, NULL, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(uuid.uuid4()),
                    acc_id,
                    1 if new_enabled else 0,
                    chosen_email,
                    json.dumps(new_offsets),
                    new_hour,
                    new_tz,
                    1 if new_opt_in else 0,
                    now_iso,
                ),
            )
        saved = db.fetchone("SELECT * FROM notification_preferences WHERE account_id = %s", (acc_id,))
    return _serialize_preferences(saved, user_email, acc_id)


@router.post("/notifications/test-email", status_code=200)
def send_test_email(
    payload: TestEmailRequest | None = None,
    principal: dict = Depends(get_current_principal),
):
    user_id = principal["user"]["id"]
    acc_id = principal["personal_account_id"]
    user_email = principal["user"]["email"].lower()
    now_ts = time.monotonic()
    history = [ts for ts in _TEST_EMAIL_TIMESTAMPS.get(user_id, []) if now_ts - ts < 60.0]
    if len(history) >= 5:
        raise DomainError(429, "RATE_LIMITED", "Too many test email requests. Please wait 60 seconds and try again.")

    settings = get_settings()
    now_iso = get_clock().now_iso()
    with get_db(write=True) as db:
        pref = db.fetchone("SELECT primary_email FROM notification_preferences WHERE account_id = %s", (acc_id,))
        configured_email = (pref["primary_email"].lower() if pref and pref.get("primary_email") else user_email)
        relay_mask = derive_masked_relay_address(configured_email)
        requested_target = (payload.recipient.lower() if payload and payload.recipient else configured_email)

        allowed_recipients = {
            user_email,
            configured_email,
            relay_mask,
            derive_masked_relay_address(user_email),
        }
        if requested_target not in allowed_recipients:
            raise DomainError(
                403,
                "UNVERIFIED_RECIPIENT",
                "Test emails may only be sent to your verified account email or configured notification address.",
            )
        target_email = configured_email if requested_target in (relay_mask, derive_masked_relay_address(user_email)) else requested_target
        history.append(now_ts)
        _TEST_EMAIL_TIMESTAMPS[user_id] = history

        job_id = str(uuid.uuid4())
        subject = (payload.subject.strip() if payload and payload.subject else None) or "SubSphere Test Notification Email"
        body = (
            f"Hello,\n\n"
            f"This is a verification email from SubSphere confirming your masked email delivery settings.\n"
            f"Relay Mask Address: {relay_mask}\n"
            f"Configured SMTP host: {settings.smtp_host}:{settings.smtp_port}\n"
            f"Timestamp: {now_iso}\n"
        )
        db.execute(
            """
            INSERT INTO notification_jobs (
                id, logical_key, account_id, organization_id, rule_id, channel,
                recipient, subject, body, status, attempts, created_at, updated_at
            )
            VALUES (%s, %s, %s, NULL, NULL, 'email', %s, %s, %s, 'queued', 0, %s, %s)
            """,
            (job_id, f"test_email:{job_id}", acc_id, target_email, subject, body, now_iso, now_iso),
        )
        dispatch_res = dispatch_email_jobs(db, from_email=relay_mask if settings.smtp_from_email in ("noreply@subsphere.local", "relay@mask.subsphere.local") else None)
        job = db.fetchone("SELECT * FROM notification_jobs WHERE id = %s", (job_id,))
        record_audit_log(
            db,
            actor_user_id=user_id,
            tenant_id=acc_id,
            action="notification.test_email",
            resource_type="notification_job",
            resource_id=job_id,
            metadata={"recipient": target_email, "relay_mask": relay_mask, "status": job["status"]},
        )

    ok = job["status"] == "accepted_by_relay"
    masked_target = mask_email_address(target_email)
    actionable = (
        f"Accepted via masked relay {relay_mask} -> {masked_target} ({settings.smtp_host}:{settings.smtp_port})."
        if ok
        else f"SMTP relay unreachable at {settings.smtp_host}:{settings.smtp_port} ({job.get('last_error') or 'connection error'}). Start Mailpit or verify SMTP_HOST/SMTP_PORT; job queued for retry."
    )
    return {
        "ok": ok,
        "job_id": job_id,
        "recipient": target_email,
        "masked_email": masked_target,
        "relay_mask_email": relay_mask,
        "status": job["status"],
        "last_error": job.get("last_error"),
        "smtp_host": settings.smtp_host,
        "smtp_port": settings.smtp_port,
        "actionable_message": actionable,
        "dispatch_summary": dispatch_res,
    }


@router.get("/notifications/masked-inbox", status_code=200)
def get_masked_inbox(principal: dict = Depends(get_current_principal)):
    acc_id = principal["personal_account_id"]
    user_email = principal["user"]["email"].lower()
    with get_db(write=False) as db:
        pref = db.fetchone("SELECT primary_email FROM notification_preferences WHERE account_id = %s", (acc_id,))
        real_email = (pref["primary_email"].lower() if pref and pref.get("primary_email") else user_email)
        relay_mask = derive_masked_relay_address(real_email)
        masked_display = mask_email_address(real_email)
        jobs = db.fetchall(
            """
            SELECT * FROM notification_jobs
            WHERE account_id = %s AND organization_id IS NULL
            ORDER BY created_at DESC LIMIT 50
            """,
            (acc_id,),
        )

    messages: list[dict] = []
    for j in jobs:
        lk = str(j.get("logical_key") or "")
        is_received = lk.startswith("inbox_recv:")
        raw_body = str(j.get("body") or "")
        sender_label = relay_mask
        if is_received and "[From:" in raw_body:
            first_line = raw_body.splitlines()[0]
            sender_part = first_line.replace("[From:", "").split("->")[0].strip().rstrip("]")
            if sender_part:
                sender_label = sender_part
        messages.append(
            {
                "id": j["id"],
                "direction": "received" if is_received else "sent",
                "masked_sender": sender_label,
                "masked_recipient": relay_mask if is_received else masked_display,
                "subject": j["subject"],
                "body": raw_body,
                "status": j["status"],
                "created_at": j["created_at"],
            }
        )
    return {
        "masked_email": masked_display,
        "relay_mask_email": relay_mask,
        "messages": messages,
    }


@router.post("/notifications/masked-inbox/receive", status_code=201)
def receive_via_masked_email(
    payload: MaskedInboxReceiveRequest | None = None,
    principal: dict = Depends(get_current_principal),
):
    acc_id = principal["personal_account_id"]
    user_email = principal["user"]["email"].lower()
    now_iso = get_clock().now_iso()
    req = payload or MaskedInboxReceiveRequest()
    sender = (req.sender or "billing@claude.ai").strip()
    subject = (req.subject or "Subscription Notice via SubSphere Mask").strip()
    with get_db(write=True) as db:
        pref = db.fetchone("SELECT primary_email FROM notification_preferences WHERE account_id = %s", (acc_id,))
        real_email = (pref["primary_email"].lower() if pref and pref.get("primary_email") else user_email)
        relay_mask = derive_masked_relay_address(real_email)
        masked_display = mask_email_address(real_email)
        body_text = (req.body or "").strip() or (
            f"Hello,\n\n"
            f"This message was received at your SubSphere mask {relay_mask} from {sender} "
            f"and privately routed to {masked_display} without exposing your personal address.\n\n"
            f"Received at: {now_iso}\n"
        )
        full_body = f"[From: {sender} -> Masked To: {relay_mask}]\n\n{body_text}"
        msg_id = str(uuid.uuid4())
        db.execute(
            """
            INSERT INTO notification_jobs (
                id, logical_key, account_id, organization_id, rule_id, channel,
                recipient, subject, body, status, attempts, created_at, updated_at
            )
            VALUES (%s, %s, %s, NULL, NULL, 'email', %s, %s, %s, 'delivered', 1, %s, %s)
            """,
            (msg_id, f"inbox_recv:{msg_id}", acc_id, relay_mask, subject, full_body, now_iso, now_iso),
        )
        record_audit_log(
            db,
            actor_user_id=principal["user"]["id"],
            tenant_id=acc_id,
            action="notification.masked_inbox_received",
            resource_type="notification_job",
            resource_id=msg_id,
            metadata={"sender": sender, "relay_mask": relay_mask},
        )
    return {
        "id": msg_id,
        "direction": "received",
        "masked_sender": sender,
        "masked_recipient": relay_mask,
        "masked_email": masked_display,
        "subject": subject,
        "body": full_body,
        "status": "delivered",
        "created_at": now_iso,
    }


@router.get("/notifications/history", status_code=200)
def get_notification_history(
    organization_id: str | None = Query(default=None),
    limit: int = Query(default=50),
    principal: dict = Depends(get_current_principal),
):
    safe_limit = max(1, min(limit, 200))
    with get_db(write=False) as db:
        if organization_id:
            require_org_role(db, principal, organization_id, {"owner", "admin", "billing", "staff"})
            jobs = db.fetchall(
                """
                SELECT * FROM notification_jobs
                WHERE organization_id = %s
                ORDER BY created_at DESC LIMIT %s
                """,
                (organization_id, safe_limit),
            )
        else:
            jobs = db.fetchall(
                """
                SELECT * FROM notification_jobs
                WHERE account_id = %s AND organization_id IS NULL
                ORDER BY created_at DESC LIMIT %s
                """,
                (principal["personal_account_id"], safe_limit),
            )
        enriched = []
        for j in jobs:
            attempts = db.fetchall(
                "SELECT * FROM notification_attempts WHERE job_id = %s ORDER BY attempted_at DESC",
                (j["id"],),
            )
            enriched.append({**j, "attempts_history": attempts})
    return {"notifications": enriched, "count": len(enriched)}


@router.post("/subscriptions/{sub_id}/reminder-rules", status_code=200)
@router.post("/personal/subscriptions/{sub_id}/reminder-rules", status_code=200)
def set_subscription_reminder_rules(
    sub_id: str,
    payload: ReminderRulesRequest,
    principal: dict = Depends(get_current_principal),
):
    acc_id = principal["personal_account_id"]
    now_iso = get_clock().now_iso()
    tz = validate_timezone(payload.timezone)
    clean_offsets = sorted({int(d) for d in payload.days_before if int(d) >= 0}, reverse=True)
    if payload.channel not in ("email",):
        raise DomainError(422, "INVALID_CHANNEL", "Only email channel is supported for personal reminder rules.")

    with get_db(write=True) as db:
        sub = db.fetchone(
            "SELECT * FROM personal_subscriptions WHERE id = %s AND account_id = %s",
            (sub_id, acc_id),
        )
        if not sub:
            raise DomainError(404, "NOT_FOUND", "Personal subscription not found.")

        recipient = (
            payload.recipient_email.lower()
            if payload.recipient_email
            else (sub.get("recipient_email") or principal["user"]["email"]).lower()
        )
        db.execute("DELETE FROM reminder_rules WHERE subscription_id = %s AND account_id = %s", (sub_id, acc_id))
        saved_rules = []
        for d in clean_offsets:
            rid = str(uuid.uuid4())
            db.execute(
                """
                INSERT INTO reminder_rules (
                    id, account_id, organization_id, subscription_id, recipient,
                    days_before, enabled, channel, timezone, send_hour_local, created_at, updated_at
                )
                VALUES (%s, %s, NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    rid,
                    acc_id,
                    sub_id,
                    recipient,
                    d,
                    1 if payload.enabled else 0,
                    payload.channel,
                    tz,
                    payload.send_hour_local,
                    now_iso,
                    now_iso,
                ),
            )
            saved_rules.append(
                {
                    "id": rid,
                    "subscription_id": sub_id,
                    "recipient": recipient,
                    "days_before": d,
                    "enabled": payload.enabled,
                    "timezone": tz,
                    "send_hour_local": payload.send_hour_local,
                }
            )

        db.execute(
            """
            UPDATE personal_subscriptions
            SET reminder_offsets_json = %s, timezone = %s, recipient_email = %s, updated_at = %s
            WHERE id = %s AND account_id = %s
            """,
            (json.dumps(clean_offsets), tz, recipient, now_iso, sub_id, acc_id),
        )
        reconcile = reconcile_subscription_reminders(
            db,
            subscription_id=sub_id,
            account_id=acc_id,
            next_renewal_date=sub["next_renewal_date"],
            offsets=clean_offsets if payload.enabled else [],
            tracking_status=sub.get("tracking_status") or "tracking_active",
            recipient=recipient,
            tz_name=tz,
            send_hour_local=payload.send_hour_local,
        )
    return {
        "subscription_id": sub_id,
        "days_before": clean_offsets,
        "enabled": payload.enabled,
        "timezone": tz,
        "send_hour_local": payload.send_hour_local,
        "recipient_email": recipient,
        "rules": saved_rules,
        "reconciliation": reconcile,
    }


@router.get("/integrations/email/status", status_code=200)
def get_email_integration_status(principal: dict = Depends(get_current_principal)):
    settings = get_settings()
    reachable = False
    error_detail: str | None = None
    if settings.smtp_host and settings.smtp_port:
        try:
            with socket.create_connection((settings.smtp_host, int(settings.smtp_port)), timeout=1.5):
                reachable = True
        except OSError as exc:
            error_detail = str(exc)
    return {
        "configured": bool(settings.smtp_host),
        "reachable": reachable,
        "smtp_host": settings.smtp_host,
        "smtp_port": settings.smtp_port,
        "from_address": settings.smtp_from_email,
        "mode": "mailpit_or_smtp",
        "error": error_detail,
    }

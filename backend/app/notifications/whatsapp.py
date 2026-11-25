"""Official WhatsApp Business Platform adapter with consent verification & click-to-chat fallback."""
from __future__ import annotations

import urllib.parse
import uuid
from typing import Any, Callable

import httpx

from app.audit.service import record_audit_log
from app.core.clock import get_clock
from app.core.config import get_settings
from app.core.errors import DomainError
from app.db.engine import DBConnection


def build_click_to_chat_url(phone_e164: str, message_text: str) -> str:
    digits = "".join(ch for ch in phone_e164 if ch.isdigit())
    encoded = urllib.parse.quote(message_text)
    return f"https://wa.me/{digits}?text={encoded}"


def send_whatsapp_template(
    db: DBConnection,
    *,
    organization_id: str,
    subscriber_contact_id: str,
    template_name: str,
    parameters: list[str],
    actor_user_id: str | None = None,
    http_poster: Callable[[str, dict[str, str], dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    now_iso = get_clock().now_iso()
    sc = db.fetchone(
        "SELECT id, organization_id, name, phone_e164 FROM subscriber_contacts WHERE id = %s",
        (subscriber_contact_id,),
    )
    if not sc or sc["organization_id"] != organization_id:
        raise DomainError(404, "SUBSCRIBER_NOT_FOUND", "Subscriber contact not found in organization.")
    if not sc["phone_e164"]:
        raise DomainError(422, "PHONE_MISSING", "Subscriber contact has no phone_e164 number.")

    consent = db.fetchone(
        """
        SELECT * FROM consents
        WHERE organization_id = %s AND subscriber_contact_id = %s AND channel = 'whatsapp' AND status = 'granted'
        """,
        (organization_id, subscriber_contact_id),
    )
    if not consent:
        record_audit_log(
            db,
            actor_user_id=actor_user_id,
            tenant_id=organization_id,
            action="whatsapp.blocked_no_consent",
            resource_type="subscriber_contact",
            resource_id=subscriber_contact_id,
        )
        raise DomainError(
            403,
            "WHATSAPP_CONSENT_REQUIRED",
            "Recipient has not granted WhatsApp opt-in consent (or has opted out).",
        )

    settings = get_settings()
    payload = {
        "messaging_product": "whatsapp",
        "to": sc["phone_e164"],
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": "en"},
            "components": [{"type": "body", "parameters": [{"type": "text", "text": p} for p in parameters]}],
        },
    }

    if not settings.whatsapp_api_url and http_poster is None:
        # Provider credentials not supplied: fail safely and return manual click-to-chat fallback
        fallback_msg = f"Hello {sc['name']}, " + " ".join(parameters)
        return {
            "sent": False,
            "status": "provider_disabled",
            "click_to_chat_url": build_click_to_chat_url(sc["phone_e164"], fallback_msg),
            "recipient": sc["phone_e164"],
        }

    def _default_post(url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            return resp.json()

    poster = http_poster or _default_post
    endpoint = f"{settings.whatsapp_api_url.rstrip('/')}/{settings.whatsapp_phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {settings.whatsapp_access_token}", "Content-Type": "application/json"}

    resp_data = poster(endpoint, headers, payload)
    msg_id = None
    if isinstance(resp_data, dict):
        msgs = resp_data.get("messages") or []
        if msgs and isinstance(msgs[0], dict):
            msg_id = msgs[0].get("id")

    job_id = str(uuid.uuid4())
    db.execute(
        """
        INSERT INTO notification_jobs (
            id, logical_key, account_id, organization_id, rule_id, channel, recipient,
            subject, body, status, attempts, created_at, updated_at
        )
        VALUES (%s, %s, NULL, %s, NULL, 'whatsapp', %s, %s, %s, 'accepted_by_relay', 1, %s, %s)
        """,
        (
            job_id,
            f"wa:{organization_id}:{subscriber_contact_id}:{uuid.uuid4().hex[:10]}",
            organization_id,
            sc["phone_e164"],
            template_name,
            str(parameters),
            now_iso,
            now_iso,
        ),
    )
    db.execute(
        """
        INSERT INTO notification_attempts (id, job_id, provider_msg_id, result_status, detail, attempted_at)
        VALUES (%s, %s, %s, 'accepted_by_relay', 'Submitted to WhatsApp Business Platform API', %s)
        """,
        (str(uuid.uuid4()), job_id, msg_id, now_iso),
    )
    return {
        "sent": True,
        "status": "accepted_by_relay",
        "job_id": job_id,
        "provider_message_id": msg_id,
        "recipient": sc["phone_e164"],
    }

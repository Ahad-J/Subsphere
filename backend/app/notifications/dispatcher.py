"""Durable SMTP notification dispatcher for Mailpit (dev) and Postal (prod)."""
from __future__ import annotations

import smtplib
import uuid
from datetime import timedelta
from email.message import EmailMessage
from typing import Any, Callable

from app.core.clock import get_clock
from app.core.config import get_settings
from app.core.security import derive_masked_relay_address
from app.db.engine import DBConnection


def default_smtp_sender(host: str, port: int, username: str, password: str, from_addr: str, to_addr: str, subject: str, body: str) -> str:
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["Reply-To"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["X-SubSphere-Relay"] = "masked-email-router"
    msg.set_content(body)
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=10) as client:
            if username and password:
                client.login(username, password)
            client.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=10) as client:
            client.ehlo()
            if username and password:
                client.starttls()
                client.ehlo()
                client.login(username, password)
            client.send_message(msg)
    return f"smtp-{uuid.uuid4().hex[:12]}"


def dispatch_email_jobs(
    db: DBConnection,
    *,
    organization_id: str | None = None,
    from_email: str | None = None,
    smtp_transport: Callable[[str, int, str, str, str, str, str, str], str] | None = None,
) -> dict[str, Any]:
    """Process queued/retrying email jobs honestly: SMTP 250 OK -> accepted_by_relay (NOT delivered)."""
    settings = get_settings()
    sender = smtp_transport or default_smtp_sender
    now = get_clock().now()
    now_iso = get_clock().now_iso()

    if organization_id:
        jobs = db.fetchall(
            """
            SELECT * FROM notification_jobs
            WHERE channel = 'email' AND organization_id = %s AND status IN ('queued', 'retrying')
            ORDER BY created_at
            """,
            (organization_id,),
        )
    else:
        jobs = db.fetchall(
            """
            SELECT * FROM notification_jobs
            WHERE channel = 'email' AND status IN ('queued', 'retrying')
            ORDER BY created_at
            """
        )

    accepted = 0
    retrying = 0
    results = []

    for job in jobs:
        if job["next_retry_at"] and job["next_retry_at"] > now_iso:
            continue

        chosen_from = from_email or settings.smtp_from_email
        if not from_email and job.get("account_id") and settings.smtp_from_email in ("noreply@subsphere.local", "relay@mask.subsphere.local"):
            owner = db.fetchone(
                "SELECT u.email FROM accounts a JOIN users u ON u.id = a.owner_user_id WHERE a.id = %s",
                (job["account_id"],),
            )
            if owner and owner.get("email"):
                chosen_from = derive_masked_relay_address(owner["email"])
        if job["organization_id"] and from_email:
            domain = from_email.split("@")[-1].lower()
            dom_row = db.fetchone(
                "SELECT status FROM sender_domains WHERE organization_id = %s AND domain = %s",
                (job["organization_id"], domain),
            )
            if not dom_row or dom_row["status"] != "verified":
                chosen_from = settings.smtp_from_email

        attempt_num = job["attempts"] + 1
        try:
            provider_msg_id = sender(
                settings.smtp_host,
                settings.smtp_port,
                settings.smtp_username,
                settings.smtp_password,
                chosen_from,
                job["recipient"],
                job["subject"],
                job["body"],
            )
            db.execute(
                """
                UPDATE notification_jobs
                SET status = 'accepted_by_relay', attempts = %s, last_error = NULL, next_retry_at = NULL, updated_at = %s
                WHERE id = %s
                """,
                (attempt_num, now_iso, job["id"]),
            )
            db.execute(
                """
                INSERT INTO notification_attempts (id, job_id, provider_msg_id, result_status, detail, attempted_at)
                VALUES (%s, %s, %s, 'accepted_by_relay', %s, %s)
                """,
                (str(uuid.uuid4()), job["id"], provider_msg_id, f"Accepted by SMTP relay {settings.smtp_host}:{settings.smtp_port}", now_iso),
            )
            accepted += 1
            results.append({"job_id": job["id"], "status": "accepted_by_relay", "provider_msg_id": provider_msg_id})
        except Exception as exc:
            backoff_sec = min(3600, 30 * (2 ** min(6, attempt_num - 1)))
            next_retry = (now + timedelta(seconds=backoff_sec)).isoformat().replace("+00:00", "Z")
            err_text = str(exc)[:400]
            db.execute(
                """
                UPDATE notification_jobs
                SET status = 'retrying', attempts = %s, last_error = %s, next_retry_at = %s, updated_at = %s
                WHERE id = %s
                """,
                (attempt_num, err_text, next_retry, now_iso, job["id"]),
            )
            db.execute(
                """
                INSERT INTO notification_attempts (id, job_id, provider_msg_id, result_status, detail, attempted_at)
                VALUES (%s, %s, NULL, 'retry_scheduled', %s, %s)
                """,
                (str(uuid.uuid4()), job["id"], err_text, now_iso),
            )
            retrying += 1
            results.append({"job_id": job["id"], "status": "retrying", "error": err_text, "next_retry_at": next_retry})

    return {"accepted_by_relay": accepted, "retrying": retrying, "results": results}

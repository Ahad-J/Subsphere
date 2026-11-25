"""Service API keys, atomic usage consumption, hard-limit quota enforcement, and advisory entitlements."""
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, Field

from app.audit.service import record_audit_log
from app.auth.dependencies import get_current_principal, get_service_api_key, require_org_role
from app.core.clock import get_clock
from app.core.errors import DomainError
from app.core.security import generate_token, hash_token
from app.db.engine import get_db
from app.personal.domain import is_in_half_open_period

router = APIRouter(prefix="/api/v1", tags=["metering"])


class ApiKeyCreate(BaseModel):
    organization_id: str
    service_id: str
    name: str = Field(..., min_length=1, max_length=100)
    scope: str = "metering:write"


class UsageConsumeRequest(BaseModel):
    external_event_id: str | None = None
    subscription_id: str
    metric_key: str
    quantity: int = Field(..., gt=0)
    occurred_at: str | None = None
    seat_allocation_id: str | None = None
    source: str = "PROVIDER_SDK"


@router.post("/api-keys", status_code=201)
def create_service_api_key(payload: ApiKeyCreate, principal: dict = Depends(get_current_principal)):
    now_iso = get_clock().now_iso()
    key_id = str(uuid.uuid4())
    raw_key = f"ss_live_{generate_token(24)}"
    prefix = raw_key[:14]

    with get_db(write=True) as db:
        require_org_role(db, principal, payload.organization_id, {"owner", "admin", "developer"})
        svc = db.fetchone("SELECT id, organization_id FROM services WHERE id = %s", (payload.service_id,))
        if not svc or svc["organization_id"] != payload.organization_id:
            raise DomainError(404, "SERVICE_NOT_FOUND", "Service not found in organization.")
        db.execute(
            """
            INSERT INTO api_keys (id, organization_id, service_id, name, key_prefix, key_hash, scope, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (key_id, payload.organization_id, payload.service_id, payload.name.strip(), prefix, hash_token(raw_key), payload.scope, now_iso),
        )
        record_audit_log(
            db,
            actor_user_id=principal["user"]["id"],
            tenant_id=payload.organization_id,
            action="api_key.created",
            resource_type="api_key",
            resource_id=key_id,
            metadata={"service_id": payload.service_id, "prefix": prefix},
        )
    return {
        "id": key_id,
        "organization_id": payload.organization_id,
        "service_id": payload.service_id,
        "name": payload.name.strip(),
        "key_prefix": prefix,
        "api_key": raw_key,
        "scope": payload.scope,
    }


@router.get("/api-keys", status_code=200)
def list_api_keys(organization_id: str, principal: dict = Depends(get_current_principal)):
    with get_db(write=False) as db:
        require_org_role(db, principal, organization_id, {"owner", "admin", "developer"})
        rows = db.fetchall(
            "SELECT id, organization_id, service_id, name, key_prefix, scope, revoked_at, created_at FROM api_keys WHERE organization_id = %s ORDER BY created_at DESC",
            (organization_id,),
        )
    return {"api_keys": rows}


@router.post("/usage/consume", status_code=201)
def consume_usage(
    payload: UsageConsumeRequest,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    api_key: dict = Depends(get_service_api_key),
):
    ext_evt_id = (payload.external_event_id or idempotency_key or "").strip()
    if not ext_evt_id:
        raise DomainError(422, "EVENT_ID_REQUIRED", "external_event_id or Idempotency-Key header is required.")
    now_iso = get_clock().now_iso()
    occurred_at = payload.occurred_at or now_iso
    metric_key = payload.metric_key.strip().lower()

    with get_db(write=True) as db:
        sub = db.fetchone(
            """
            SELECT s.*, pv.plan_id, p.service_id
            FROM subscriptions s
            JOIN plan_versions pv ON pv.id = s.plan_version_id
            JOIN plans p ON p.id = pv.plan_id
            WHERE s.id = %s
            """,
            (payload.subscription_id,),
        )
        if not sub:
            raise DomainError(404, "SUBSCRIPTION_NOT_FOUND", "Subscription not found.")

        # Cross-service API key isolation check (T046, T074)
        if sub["service_id"] != api_key["service_id"] or sub["provider_org_id"] != api_key["organization_id"]:
            raise DomainError(403, "FORBIDDEN_SERVICE_KEY", "API key is not authorized for this subscription's service.")

        if sub["status"] not in ("active", "trialing", "past_due"):
            raise DomainError(409, "SUBSCRIPTION_INACTIVE", f"Subscription is in '{sub['status']}' state.")

        metric = db.fetchone(
            "SELECT * FROM metrics WHERE service_id = %s AND key = %s",
            (sub["service_id"], metric_key),
        )
        if not metric:
            raise DomainError(404, "METRIC_NOT_FOUND", f"Metric '{metric_key}' is not defined for this service.")

        # Find active period containing occurred_at [start, end)
        periods = db.fetchall(
            "SELECT * FROM subscription_periods WHERE subscription_id = %s ORDER BY period_start DESC",
            (sub["id"],),
        )
        target_period = None
        for per in periods:
            if is_in_half_open_period(occurred_at, per["period_start"], per["period_end"]):
                target_period = per
                break
        if not target_period:
            raise DomainError(409, "NO_ACTIVE_PERIOD", "No subscription period covers occurred_at timestamp.")

        limit_row = db.fetchone(
            "SELECT limit_value FROM plan_metric_limits WHERE plan_version_id = %s AND metric_id = %s",
            (sub["plan_version_id"], metric["id"]),
        )
        limit_val = limit_row["limit_value"] if limit_row else None

        # Idempotency check on (service_id, external_event_id) (T042, T043)
        existing_evt = db.fetchone(
            "SELECT * FROM usage_events WHERE service_id = %s AND external_event_id = %s",
            (sub["service_id"], ext_evt_id),
        )
        if existing_evt:
            if (
                existing_evt["quantity"] != payload.quantity
                or existing_evt["metric_id"] != metric["id"]
                or existing_evt["subscription_id"] != sub["id"]
            ):
                raise DomainError(
                    409,
                    "EVENT_ID_CONFLICT",
                    "Event ID was previously committed with a different quantity, metric, or subscription.",
                )
            counter = db.fetchone(
                "SELECT consumed FROM usage_counters WHERE subscription_period_id = %s AND metric_id = %s",
                (existing_evt["subscription_period_id"], metric["id"]),
            )
            used = counter["consumed"] if counter else 0
            remaining = max(0, limit_val - used) if limit_val is not None else None
            response.status_code = 200
            return {
                "accepted": True,
                "duplicate": True,
                "metric_key": metric_key,
                "used": used,
                "limit": limit_val,
                "remaining": remaining,
                "event_id": existing_evt["id"],
                "source": existing_evt["source"],
            }

        # Atomic counter lookup/initialize
        counter = db.fetchone(
            "SELECT id, consumed FROM usage_counters WHERE subscription_period_id = %s AND metric_id = %s",
            (target_period["id"], metric["id"]),
        )
        if not counter:
            counter_id = str(uuid.uuid4())
            db.execute(
                """
                INSERT INTO usage_counters (id, subscription_period_id, metric_id, consumed, updated_at)
                VALUES (%s, %s, %s, 0, %s)
                """,
                (counter_id, target_period["id"], metric["id"], now_iso),
            )
            current_used = 0
        else:
            counter_id = counter["id"]
            current_used = counter["consumed"]

        new_used = current_used + payload.quantity
        if limit_val is not None and new_used > limit_val:
            if metric["enforcement"] == "hard_limit":
                allowed_rem = max(0, limit_val - current_used)
                raise DomainError(
                    409,
                    "QUOTA_EXCEEDED",
                    "Requested usage exceeds the current subscription allowance.",
                    {"metric": metric_key, "allowed_remaining": allowed_rem, "used": current_used, "limit": limit_val},
                )
            else:
                # Monitor-only overuse: queue alert, accept event, do NOT auto-invoice (T045)
                lkey = f"quota_alert:{target_period['id']}:{metric['id']}"
                if not db.fetchone("SELECT id FROM notification_jobs WHERE logical_key = %s", (lkey,)):
                    db.execute(
                        """
                        INSERT INTO notification_jobs (
                            id, logical_key, account_id, organization_id, rule_id, channel, recipient,
                            subject, body, status, attempts, created_at, updated_at
                        )
                        VALUES (%s, %s, NULL, %s, NULL, 'email', 'ops@provider.local', %s, %s, 'queued', 0, %s, %s)
                        """,
                        (
                            str(uuid.uuid4()),
                            lkey,
                            sub["provider_org_id"],
                            f"Monitor-only quota threshold exceeded for {metric_key}",
                            f"Usage for {metric_key} reached {new_used} (threshold {limit_val}).",
                            now_iso,
                            now_iso,
                        ),
                    )

        event_uuid = str(uuid.uuid4())
        db.execute(
            """
            INSERT INTO usage_events (
                id, service_id, external_event_id, subscription_id, subscription_period_id,
                metric_id, seat_allocation_id, quantity, source, occurred_at, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                event_uuid,
                sub["service_id"],
                ext_evt_id,
                sub["id"],
                target_period["id"],
                metric["id"],
                payload.seat_allocation_id,
                payload.quantity,
                payload.source,
                occurred_at,
                now_iso,
            ),
        )
        db.execute(
            "UPDATE usage_counters SET consumed = %s, updated_at = %s WHERE id = %s",
            (new_used, now_iso, counter_id),
        )

        # Write OutboxEvent + independent Kafka and OpenMeter delivery rows in same SQL TX
        outbox_id = str(uuid.uuid4())
        evt_payload = {
            "event_id": event_uuid,
            "external_event_id": ext_evt_id,
            "organization_id": sub["provider_org_id"],
            "service_id": sub["service_id"],
            "subscription_id": sub["id"],
            "subscription_period_id": target_period["id"],
            "subscriber_ref": sub["subscriber_contact_id"] or sub["buyer_account_id"] or sub["id"],
            "metric_key": metric_key,
            "quantity": payload.quantity,
            "source": payload.source,
            "occurred_at": occurred_at,
        }
        db.execute(
            """
            INSERT INTO outbox_events (id, aggregate_type, aggregate_id, event_type, payload_json, created_at)
            VALUES (%s, 'usage_event', %s, 'usage.accepted', %s, %s)
            """,
            (outbox_id, event_uuid, json.dumps(evt_payload), now_iso),
        )
        for dest in ("kafka", "openmeter"):
            db.execute(
                """
                INSERT INTO outbox_deliveries (id, event_id, destination, status, attempts)
                VALUES (%s, %s, %s, 'pending', 0)
                """,
                (str(uuid.uuid4()), outbox_id, dest),
            )

    remaining = max(0, limit_val - new_used) if limit_val is not None else None
    return {
        "accepted": True,
        "duplicate": False,
        "metric_key": metric_key,
        "used": new_used,
        "limit": limit_val,
        "remaining": remaining,
        "event_id": event_uuid,
        "source": payload.source,
    }


@router.get("/entitlements/check", status_code=200)
def check_entitlement_advisory(subscription_id: str, metric_key: str, principal: dict = Depends(get_current_principal)):
    """Advisory read-only entitlement check; never reserves or mutates quota."""
    with get_db(write=False) as db:
        sub = db.fetchone(
            """
            SELECT s.*, p.service_id
            FROM subscriptions s
            JOIN plan_versions pv ON pv.id = s.plan_version_id
            JOIN plans p ON p.id = pv.plan_id
            WHERE s.id = %s
            """,
            (subscription_id,),
        )
        if not sub:
            raise DomainError(404, "SUBSCRIPTION_NOT_FOUND", "Subscription not found.")
        require_org_role(db, principal, sub["provider_org_id"], {"owner", "admin", "billing", "developer", "staff"})
        metric = db.fetchone("SELECT * FROM metrics WHERE service_id = %s AND key = %s", (sub["service_id"], metric_key))
        if not metric:
            raise DomainError(404, "METRIC_NOT_FOUND", "Metric not found.")
        limit_row = db.fetchone(
            "SELECT limit_value FROM plan_metric_limits WHERE plan_version_id = %s AND metric_id = %s",
            (sub["plan_version_id"], metric["id"]),
        )
        per = db.fetchone(
            "SELECT id FROM subscription_periods WHERE subscription_id = %s ORDER BY period_start DESC LIMIT 1",
            (subscription_id,),
        )
        counter = db.fetchone(
            "SELECT consumed FROM usage_counters WHERE subscription_period_id = %s AND metric_id = %s",
            (per["id"], metric["id"]),
        ) if per else None

    used = counter["consumed"] if counter else 0
    lim = limit_row["limit_value"] if limit_row else None
    return {
        "advisory": True,
        "subscription_id": subscription_id,
        "metric_key": metric_key,
        "enforcement": metric["enforcement"],
        "used": used,
        "limit": lim,
        "remaining": max(0, lim - used) if lim is not None else None,
        "allowed": (used < lim) if (lim is not None and metric["enforcement"] == "hard_limit") else True,
    }


@router.get("/usage", status_code=200)
def list_provider_usage(organization_id: str, subscription_id: str | None = None, principal: dict = Depends(get_current_principal)):
    with get_db(write=False) as db:
        require_org_role(db, principal, organization_id, {"owner", "admin", "billing", "developer", "staff"})
        if subscription_id:
            events = db.fetchall(
                """
                SELECT ue.*, m.key as metric_key
                FROM usage_events ue
                JOIN metrics m ON m.id = ue.metric_id
                WHERE ue.subscription_id = %s
                ORDER BY ue.occurred_at DESC
                LIMIT 200
                """,
                (subscription_id,),
            )
            counters = db.fetchall(
                """
                SELECT uc.*, m.key as metric_key, m.enforcement, pml.limit_value
                FROM usage_counters uc
                JOIN subscription_periods sp ON sp.id = uc.subscription_period_id
                JOIN subscriptions s ON s.id = sp.subscription_id
                JOIN metrics m ON m.id = uc.metric_id
                LEFT JOIN plan_metric_limits pml ON pml.plan_version_id = s.plan_version_id AND pml.metric_id = m.id
                WHERE sp.subscription_id = %s
                """,
                (subscription_id,),
            )
        else:
            events = db.fetchall(
                """
                SELECT ue.*, m.key as metric_key
                FROM usage_events ue
                JOIN metrics m ON m.id = ue.metric_id
                JOIN services sv ON sv.id = ue.service_id
                WHERE sv.organization_id = %s
                ORDER BY ue.occurred_at DESC
                LIMIT 200
                """,
                (organization_id,),
            )
            counters = db.fetchall(
                """
                SELECT uc.*, m.key as metric_key, m.enforcement, pml.limit_value, s.id as subscription_id
                FROM usage_counters uc
                JOIN subscription_periods sp ON sp.id = uc.subscription_period_id
                JOIN subscriptions s ON s.id = sp.subscription_id
                JOIN metrics m ON m.id = uc.metric_id
                LEFT JOIN plan_metric_limits pml ON pml.plan_version_id = s.plan_version_id AND pml.metric_id = m.id
                WHERE s.provider_org_id = %s
                """,
                (organization_id,),
            )
    return {"events": events, "counters": counters}

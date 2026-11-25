"""Analytics, outbox dispatch, Medallion materialization, freshness, and reconciliation routes."""
from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.analytics.medallion import (
    MedallionPipeline,
    generate_reconciliation_report,
    get_freshness_status,
)
from app.analytics.outbox import OutboxDispatcher, build_openmeter_cloudevent
from app.auth.dependencies import get_current_principal, require_org_role
from app.db.engine import get_db

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


class OutboxDispatchRequest(BaseModel):
    destinations: Optional[list[str]] = None
    simulate_failures: list[str] = Field(default_factory=list)
    limit: int = Field(default=500, ge=1, le=5000)


class MedallionRunRequest(BaseModel):
    organization_id: Optional[str] = None
    replay_from_bronze: bool = False
    additional_raw_events: Optional[list[dict[str, Any]]] = None


@router.post("/outbox/dispatch")
async def dispatch_outbox(
    body: OutboxDispatchRequest,
    principal: dict = Depends(get_current_principal),
) -> dict[str, Any]:
    dispatcher = OutboxDispatcher()
    with get_db(write=True) as db:
        if principal.get("active_org_id"):
            require_org_role(
                db,
                principal,
                principal["active_org_id"],
                {"owner", "admin", "developer", "billing"},
            )
        return dispatcher.dispatch_pending(
            db,
            destinations=body.destinations,
            simulate_failures=set(body.simulate_failures),
            limit=body.limit,
        )


@router.post("/medallion/run")
async def run_medallion_pipeline(
    body: MedallionRunRequest,
    principal: dict = Depends(get_current_principal),
) -> dict[str, Any]:
    pipeline = MedallionPipeline()
    with get_db(write=True) as db:
        target_org = body.organization_id or principal.get("active_org_id")
        if target_org:
            require_org_role(db, principal, target_org, {"owner", "admin", "developer", "billing"})
        return pipeline.run_silver_and_gold(
            db,
            replay_from_bronze=body.replay_from_bronze,
            additional_raw_events=body.additional_raw_events,
        )


@router.get("/freshness")
async def analytics_freshness(
    stale_threshold_seconds: int = Query(default=900),
    principal: dict = Depends(get_current_principal),
) -> dict[str, Any]:
    threshold = max(1, int(stale_threshold_seconds))
    with get_db() as db:
        return get_freshness_status(db, stale_threshold_seconds=threshold)


@router.get("/reconciliation")
async def analytics_reconciliation(
    organization_id: Optional[str] = Query(default=None),
    principal: dict = Depends(get_current_principal),
) -> dict[str, Any]:
    with get_db() as db:
        if organization_id:
            require_org_role(
                db,
                principal,
                organization_id,
                {"owner", "admin", "billing", "developer", "staff"},
            )
        return generate_reconciliation_report(db, organization_id=organization_id)


@router.get("/gold/usage")
async def list_gold_daily_usage(
    organization_id: str = Query(...),
    principal: dict = Depends(get_current_principal),
) -> dict[str, Any]:
    with get_db() as db:
        require_org_role(
            db,
            principal,
            organization_id,
            {"owner", "admin", "billing", "developer", "staff"},
        )
        rows = db.fetchall(
            """
            SELECT * FROM daily_usage_aggregates
            WHERE organization_id = %s
            ORDER BY day DESC, service_id, metric_key
            """,
            (organization_id,),
        )
        finance = db.fetchall(
            """
            SELECT * FROM daily_finance_aggregates
            WHERE organization_id = %s
            ORDER BY day DESC, currency
            """,
            (organization_id,),
        )
    return {"daily_usage": rows, "daily_finance": finance}


@router.get("/cloudevents")
async def preview_cloudevents(
    limit: int = Query(default=20),
    principal: dict = Depends(get_current_principal),
) -> dict[str, Any]:
    bounded_limit = max(1, min(200, int(limit)))
    with get_db() as db:
        rows = db.fetchall(
            """
            SELECT e.*, d.status AS openmeter_status
            FROM outbox_events e
            LEFT JOIN outbox_deliveries d
              ON d.event_id = e.id AND d.destination = 'openmeter'
            WHERE e.event_type = 'usage.accepted'
            ORDER BY e.created_at DESC
            LIMIT %s
            """,
            (bounded_limit,),
        )
    events = []
    for r in rows:
        ce = build_openmeter_cloudevent(r, json.loads(r["payload_json"] or "{}"))
        ce["delivery_status"] = r.get("openmeter_status")
        events.append(ce)
    return {"cloudevents": events}

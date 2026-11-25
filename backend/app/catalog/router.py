"""Provider service catalog, immutable plan versions, and metric definitions."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.audit.service import record_audit_log
from app.auth.dependencies import get_current_principal, require_org_role
from app.core.clock import get_clock
from app.core.errors import DomainError
from app.db.engine import get_db

router = APIRouter(prefix="/api/v1", tags=["catalog"])


class ServiceCreate(BaseModel):
    organization_id: str
    name: str = Field(..., min_length=1, max_length=120)
    slug: str = Field(..., min_length=1, max_length=80)
    service_type: str = "saas"
    category: str = "general"
    currency: str = Field(default="USD", min_length=3, max_length=3)


class PlanCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str = ""


class MetricLimitInput(BaseModel):
    metric_key: str
    limit_value: int = Field(..., ge=0)


class PlanVersionCreate(BaseModel):
    price_minor: int = Field(..., ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    billing_interval: str = "monthly"
    pricing_model: str = "flat"
    included_seats: int = Field(default=1, ge=1)
    max_seats: int = Field(default=1, ge=1)
    metrics: list[MetricLimitInput] = Field(default_factory=list)


class PlanVersionPatch(BaseModel):
    price_minor: int = Field(..., ge=0)
    billing_interval: str | None = None
    included_seats: int | None = None
    max_seats: int | None = None


class MetricCreate(BaseModel):
    key: str = Field(..., min_length=1, max_length=80)
    name: str = Field(..., min_length=1, max_length=120)
    unit: str = "units"
    enforcement: str = "hard_limit"


@router.post("/services", status_code=201)
def create_service(payload: ServiceCreate, principal: dict = Depends(get_current_principal)):
    now_iso = get_clock().now_iso()
    service_id = str(uuid.uuid4())
    slug = payload.slug.strip().lower()
    with get_db(write=True) as db:
        require_org_role(db, principal, payload.organization_id, {"owner", "admin"})
        dup = db.fetchone(
            "SELECT id FROM services WHERE organization_id = %s AND slug = %s",
            (payload.organization_id, slug),
        )
        if dup:
            raise DomainError(409, "SERVICE_SLUG_EXISTS", "A service with this slug already exists in the organization.")
        db.execute(
            """
            INSERT INTO services (id, organization_id, name, slug, service_type, category, currency, status, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'active', %s)
            """,
            (
                service_id,
                payload.organization_id,
                payload.name.strip(),
                slug,
                payload.service_type,
                payload.category,
                payload.currency.upper(),
                now_iso,
            ),
        )
        record_audit_log(
            db,
            actor_user_id=principal["user"]["id"],
            tenant_id=payload.organization_id,
            action="service.created",
            resource_type="service",
            resource_id=service_id,
        )
    return {
        "id": service_id,
        "organization_id": payload.organization_id,
        "name": payload.name.strip(),
        "slug": slug,
        "service_type": payload.service_type,
        "category": payload.category,
        "currency": payload.currency.upper(),
        "status": "active",
    }


@router.get("/services", status_code=200)
def list_services(organization_id: str | None = None, principal: dict = Depends(get_current_principal)):
    with get_db(write=False) as db:
        if organization_id:
            require_org_role(db, principal, organization_id, {"owner", "admin", "billing", "developer", "staff", "member"})
            rows = db.fetchall("SELECT * FROM services WHERE organization_id = %s ORDER BY created_at", (organization_id,))
        else:
            rows = db.fetchall("SELECT * FROM services WHERE status = 'active' ORDER BY created_at")
    return {"services": rows}


@router.post("/services/{service_id}/metrics", status_code=201)
def create_metric(service_id: str, payload: MetricCreate, principal: dict = Depends(get_current_principal)):
    if payload.enforcement not in ("hard_limit", "monitor_only"):
        raise DomainError(422, "INVALID_ENFORCEMENT", "enforcement must be hard_limit or monitor_only")
    now_iso = get_clock().now_iso()
    metric_id = str(uuid.uuid4())
    key = payload.key.strip().lower()
    with get_db(write=True) as db:
        svc = db.fetchone("SELECT id, organization_id FROM services WHERE id = %s", (service_id,))
        if not svc:
            raise DomainError(404, "SERVICE_NOT_FOUND", "Service not found.")
        require_org_role(db, principal, svc["organization_id"], {"owner", "admin", "developer"})
        dup = db.fetchone("SELECT id FROM metrics WHERE service_id = %s AND key = %s", (service_id, key))
        if dup:
            raise DomainError(409, "METRIC_EXISTS", "Metric key already exists for this service.")
        db.execute(
            """
            INSERT INTO metrics (id, service_id, key, name, unit, enforcement, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (metric_id, service_id, key, payload.name.strip(), payload.unit, payload.enforcement, now_iso),
        )
    return {
        "id": metric_id,
        "service_id": service_id,
        "key": key,
        "name": payload.name.strip(),
        "unit": payload.unit,
        "enforcement": payload.enforcement,
    }


@router.get("/services/{service_id}/metrics", status_code=200)
def list_metrics(service_id: str, principal: dict = Depends(get_current_principal)):
    with get_db(write=False) as db:
        svc = db.fetchone("SELECT id, organization_id FROM services WHERE id = %s", (service_id,))
        if not svc:
            raise DomainError(404, "SERVICE_NOT_FOUND", "Service not found.")
        require_org_role(db, principal, svc["organization_id"], {"owner", "admin", "billing", "developer", "staff", "member"})
        rows = db.fetchall("SELECT * FROM metrics WHERE service_id = %s ORDER BY key", (service_id,))
    return {"metrics": rows}


@router.post("/services/{service_id}/plans", status_code=201)
def create_plan(service_id: str, payload: PlanCreate, principal: dict = Depends(get_current_principal)):
    now_iso = get_clock().now_iso()
    plan_id = str(uuid.uuid4())
    with get_db(write=True) as db:
        svc = db.fetchone("SELECT id, organization_id, status FROM services WHERE id = %s", (service_id,))
        if not svc:
            raise DomainError(404, "SERVICE_NOT_FOUND", "Service not found.")
        if svc["status"] != "active":
            raise DomainError(409, "SERVICE_INACTIVE", "Cannot add plans to an inactive service.")
        require_org_role(db, principal, svc["organization_id"], {"owner", "admin"})
        db.execute(
            """
            INSERT INTO plans (id, service_id, name, description, status, created_at)
            VALUES (%s, %s, %s, %s, 'active', %s)
            """,
            (plan_id, service_id, payload.name.strip(), payload.description, now_iso),
        )
    return {"id": plan_id, "service_id": service_id, "name": payload.name.strip(), "description": payload.description, "status": "active"}


@router.get("/services/{service_id}/plans", status_code=200)
def list_plans(service_id: str):
    with get_db(write=False) as db:
        svc = db.fetchone("SELECT id FROM services WHERE id = %s", (service_id,))
        if not svc:
            raise DomainError(404, "SERVICE_NOT_FOUND", "Service not found.")
        plans = db.fetchall("SELECT * FROM plans WHERE service_id = %s ORDER BY created_at", (service_id,))
        for p in plans:
            p["versions"] = db.fetchall(
                "SELECT * FROM plan_versions WHERE plan_id = %s ORDER BY version DESC",
                (p["id"],),
            )
    return {"plans": plans}


@router.post("/plans/{plan_id}/versions", status_code=201)
def create_plan_version(plan_id: str, payload: PlanVersionCreate, principal: dict = Depends(get_current_principal)):
    if payload.billing_interval not in ("monthly", "annual", "weekly", "one_time"):
        raise DomainError(422, "INVALID_INTERVAL", "Invalid billing_interval.")
    if payload.max_seats < payload.included_seats:
        raise DomainError(422, "INVALID_SEATS", "max_seats must be >= included_seats.")

    now_iso = get_clock().now_iso()
    pv_id = str(uuid.uuid4())
    with get_db(write=True) as db:
        plan = db.fetchone(
            """
            SELECT p.id, p.service_id, s.organization_id
            FROM plans p JOIN services s ON s.id = p.service_id
            WHERE p.id = %s
            """,
            (plan_id,),
        )
        if not plan:
            raise DomainError(404, "PLAN_NOT_FOUND", "Plan not found.")
        require_org_role(db, principal, plan["organization_id"], {"owner", "admin"})

        last = db.fetchone("SELECT MAX(version) as max_v FROM plan_versions WHERE plan_id = %s", (plan_id,))
        next_version = (last["max_v"] or 0) + 1
        db.execute(
            """
            INSERT INTO plan_versions (
                id, plan_id, version, price_minor, currency, billing_interval,
                pricing_model, included_seats, max_seats, status, published_at, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'published', %s, %s)
            """,
            (
                pv_id,
                plan_id,
                next_version,
                payload.price_minor,
                payload.currency.upper(),
                payload.billing_interval,
                payload.pricing_model,
                payload.included_seats,
                payload.max_seats,
                now_iso,
                now_iso,
            ),
        )
        metric_limits = []
        for ml in payload.metrics:
            m = db.fetchone(
                "SELECT id, key, enforcement FROM metrics WHERE service_id = %s AND key = %s",
                (plan["service_id"], ml.metric_key.strip().lower()),
            )
            if not m:
                raise DomainError(404, "METRIC_NOT_FOUND", f"Metric '{ml.metric_key}' not found on service.")
            ml_id = str(uuid.uuid4())
            db.execute(
                """
                INSERT INTO plan_metric_limits (id, plan_version_id, metric_id, limit_value, created_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (ml_id, pv_id, m["id"], ml.limit_value, now_iso),
            )
            metric_limits.append({"metric_id": m["id"], "metric_key": m["key"], "limit_value": ml.limit_value, "enforcement": m["enforcement"]})

    return {
        "id": pv_id,
        "plan_id": plan_id,
        "version": next_version,
        "price_minor": payload.price_minor,
        "currency": payload.currency.upper(),
        "billing_interval": payload.billing_interval,
        "pricing_model": payload.pricing_model,
        "included_seats": payload.included_seats,
        "max_seats": payload.max_seats,
        "status": "published",
        "metric_limits": metric_limits,
    }


@router.get("/plans/{plan_id}/versions", status_code=200)
def list_plan_versions(plan_id: str):
    with get_db(write=False) as db:
        versions = db.fetchall(
            "SELECT * FROM plan_versions WHERE plan_id = %s ORDER BY version ASC",
            (plan_id,),
        )
    return {"versions": versions}


@router.patch("/plans/{plan_id}/versions/{version_id}", status_code=201)
def revise_published_plan_price(
    plan_id: str,
    version_id: str,
    payload: PlanVersionPatch,
    principal: dict = Depends(get_current_principal),
):
    """Published plan versions are immutable; price edits create a new version (v+1) preserving v1 contracts (T022)."""
    now_iso = get_clock().now_iso()
    new_pv_id = str(uuid.uuid4())
    with get_db(write=True) as db:
        plan = db.fetchone(
            """
            SELECT p.id, p.service_id, s.organization_id
            FROM plans p JOIN services s ON s.id = p.service_id
            WHERE p.id = %s
            """,
            (plan_id,),
        )
        if not plan:
            raise DomainError(404, "PLAN_NOT_FOUND", "Plan not found.")
        require_org_role(db, principal, plan["organization_id"], {"owner", "admin"})

        old_v = db.fetchone("SELECT * FROM plan_versions WHERE id = %s AND plan_id = %s", (version_id, plan_id))
        if not old_v:
            raise DomainError(404, "PLAN_VERSION_NOT_FOUND", "Plan version not found.")

        last = db.fetchone("SELECT MAX(version) as max_v FROM plan_versions WHERE plan_id = %s", (plan_id,))
        next_version = (last["max_v"] or 0) + 1

        db.execute(
            """
            INSERT INTO plan_versions (
                id, plan_id, version, price_minor, currency, billing_interval,
                pricing_model, included_seats, max_seats, status, published_at, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'published', %s, %s)
            """,
            (
                new_pv_id,
                plan_id,
                next_version,
                payload.price_minor,
                old_v["currency"],
                payload.billing_interval or old_v["billing_interval"],
                old_v["pricing_model"],
                payload.included_seats or old_v["included_seats"],
                payload.max_seats or old_v["max_seats"],
                now_iso,
                now_iso,
            ),
        )
        # Copy metric limits to new version
        old_limits = db.fetchall("SELECT metric_id, limit_value FROM plan_metric_limits WHERE plan_version_id = %s", (version_id,))
        for ol in old_limits:
            db.execute(
                """
                INSERT INTO plan_metric_limits (id, plan_version_id, metric_id, limit_value, created_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (str(uuid.uuid4()), new_pv_id, ol["metric_id"], ol["limit_value"], now_iso),
            )
        record_audit_log(
            db,
            actor_user_id=principal["user"]["id"],
            tenant_id=plan["organization_id"],
            action="plan_version.revised",
            resource_type="plan_version",
            resource_id=new_pv_id,
            metadata={"previous_version_id": version_id, "new_version": next_version, "price_minor": payload.price_minor},
        )
    return {
        "id": new_pv_id,
        "plan_id": plan_id,
        "version": next_version,
        "price_minor": payload.price_minor,
        "currency": old_v["currency"],
        "billing_interval": payload.billing_interval or old_v["billing_interval"],
        "status": "published",
        "previous_version_preserved": True,
    }

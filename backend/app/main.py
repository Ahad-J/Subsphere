"""SubSphere 4.0 Operational Core API."""
from __future__ import annotations

import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.analytics.router import router as analytics_router
from app.auth.router import router as auth_router
from app.catalog.router import router as catalog_router
from app.db.engine import init_schema, ping_database
from app.finance.router import router as finance_router
from app.integrations.router import otel_public_router, router as integrations_router
from app.metering.router import router as metering_router
from app.notifications.router import router as notifications_router
from app.organizations.router import router as organizations_router
from app.personal.router import router as personal_router
from app.providers.enterprise_router import router as enterprise_router
from app.personal.router import calendar_router
from app.reports.router import history_router, router as reports_router
from app.subscribers.router import provider_subscribers_router, router as subscribers_router
from app.subscriptions.router import router as subscriptions_router

app = FastAPI(
    title="SubSphere 4.0 API",
    version="0.1.0",
    description="Subscription, usage, and financial intelligence platform.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8080", "http://127.0.0.1:8080", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health/live")
@app.get("/api/v1/health/live")
async def live():
    return {"status": "alive"}


@app.get("/health/ready")
@app.get("/api/v1/health/ready")
def ready():
    """Return readiness only if the configured database query succeeds."""
    url = os.getenv("DATABASE_URL")
    if not url:
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured")
    if not ping_database(url):
        raise HTTPException(status_code=503, detail="Database unavailable")
    return {"status": "ready", "database": "reachable"}


@app.get("/api/v1/meta")
async def metadata():
    return {
        "product": "SubSphere",
        "version": "4.0.0",
        "stage": "m0-foundation",
        "implemented": [
            "health/live",
            "health/ready",
            "api/v1/meta",
            "api/v1/auth",
            "api/v1/organizations",
            "api/v1/personal",
            "api/v1/catalog",
            "api/v1/subscribers",
            "api/v1/subscriptions",
            "api/v1/finance",
            "api/v1/enterprise",
            "api/v1/metering",
            "api/v1/reports",
            "api/v1/notifications",
            "api/v1/integrations",
            "api/v1/analytics",
        ],
    }


app.include_router(auth_router)
app.include_router(organizations_router)
app.include_router(personal_router)
app.include_router(calendar_router)
app.include_router(catalog_router)
app.include_router(subscribers_router)
app.include_router(provider_subscribers_router)
app.include_router(subscriptions_router)
app.include_router(finance_router)
app.include_router(enterprise_router)
app.include_router(metering_router)
app.include_router(reports_router)
app.include_router(history_router)
app.include_router(notifications_router)
app.include_router(integrations_router)
app.include_router(otel_public_router)
app.include_router(analytics_router)

if os.getenv("DATABASE_URL", "").strip().startswith(("postgres://", "postgresql://")):
    try:
        init_schema()
    except Exception:
        pass

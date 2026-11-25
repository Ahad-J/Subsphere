"""AI gateway connections, token pricing versions, and source-qualified AI usage routes."""
from __future__ import annotations

import json
import urllib.request
import uuid
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from app.audit.service import record_audit_log
from app.auth.dependencies import get_current_principal, require_org_role
from app.auth.service import get_session_principal
from app.core.clock import get_clock
from app.core.config import get_settings
from app.core.errors import DomainError
from app.core.security import generate_token, hash_token, is_safe_public_url
from app.db.engine import get_db
from app.integrations.litellm_adapter import (
    PROVIDER_CAPABILITIES,
    compute_estimated_token_value_minor,
    get_provider_capabilities,
    mask_credential,
    parse_decimal,
)
from app.integrations.mcp_script import render_mcp_server_script
from app.integrations.otel_adapter import ClaudeCodeOtelIngestAdapter
from app.integrations.universal_connector import (
    APPLICATION_CONNECTOR_MATRIX,
    FIVE_MEASUREMENTS_SPEC,
    TRACKING_METHODS_CATALOG,
    build_usage_intelligence,
    ensure_connector_connection,
    ingest_universal_events,
    render_claude_code_helper_script,
    select_best_connector,
)
from app.personal.service import create_personal_subscription, serialize_personal_subscription

router = APIRouter(prefix="/api/v1", tags=["integrations", "ai"])
otel_public_router = APIRouter(tags=["integrations", "otel"])


class ConnectionCreateRequest(BaseModel):
    provider_code: str = Field(min_length=2, max_length=64)
    display_name: str = Field(min_length=1, max_length=160)
    organization_id: Optional[str] = None
    credential: str = ""
    privacy_scope: str = "metadata_only"
    endpoint_url: Optional[str] = None
    config: dict[str, Any] = Field(default_factory=dict)


class UsageRecordIn(BaseModel):
    external_record_id: str = Field(min_length=1, max_length=200)
    model_name: Optional[str] = None
    external_user_ref: Optional[str] = None
    requests_count: Optional[int] = Field(default=None, ge=0)
    input_tokens: Optional[int] = Field(default=None, ge=0)
    output_tokens: Optional[int] = Field(default=None, ge=0)
    total_tokens: Optional[int] = Field(default=None, ge=0)
    reported_cost_minor: Optional[int] = Field(default=None, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    source: Optional[str] = None
    reliability: Optional[str] = None
    quality_note: str = ""
    occurred_at: Optional[str] = None


class SyncUsageRequest(BaseModel):
    connection_id: Optional[str] = None
    records: list[UsageRecordIn] = Field(default_factory=list)


class PricingVersionCreateRequest(BaseModel):
    provider_code: str = Field(min_length=2, max_length=64)
    model_name: str = Field(min_length=1, max_length=120)
    input_per_million_usd: str
    output_per_million_usd: str
    effective_from: Optional[str] = None


def _serialize_connection(row: dict[str, Any]) -> dict[str, Any]:
    caps = get_provider_capabilities(row["provider_code"])
    cfg = json.loads(row.get("config_json") or "{}")
    return {
        "id": row["id"],
        "account_id": row["account_id"],
        "organization_id": row.get("organization_id"),
        "provider_code": row["provider_code"],
        "display_name": row["display_name"],
        "credential_masked": row.get("credential_ref") or "",
        "privacy_scope": row["privacy_scope"],
        "status": row["status"],
        "config": cfg,
        "last_sync_success": row.get("last_sync_success"),
        "coverage_start": row.get("coverage_start"),
        "coverage_end": row.get("coverage_end"),
        "error_code": row.get("error_code"),
        "created_at": row["created_at"],
        "capabilities": caps,
    }


def _resolve_pricing(
    db: Any,
    *,
    provider_code: str,
    model_name: Optional[str],
    occurred_at: str,
) -> dict[str, Any] | None:
    if not model_name:
        return None
    row = db.fetchone(
        """
        SELECT * FROM pricing_versions
        WHERE model_name = %s AND effective_from <= %s
          AND (provider_code = %s OR provider_code = '*')
        ORDER BY CASE WHEN provider_code = %s THEN 0 ELSE 1 END ASC,
                 effective_from DESC, created_at DESC
        LIMIT 1
        """,
        (model_name, occurred_at, provider_code, provider_code),
    )
    if row is not None:
        return row
    return db.fetchone(
        """
        SELECT * FROM pricing_versions
        WHERE model_name = %s AND effective_from <= %s
        ORDER BY effective_from DESC, created_at DESC
        LIMIT 1
        """,
        (model_name, occurred_at),
    )


def _perform_sync(
    db: Any,
    *,
    connection: dict[str, Any],
    records: list[UsageRecordIn],
    actor_user_id: str,
    now_iso: str,
) -> dict[str, Any]:
    caps = get_provider_capabilities(connection["provider_code"])
    inserted = 0
    duplicates_skipped = 0
    created_ids: list[str] = []

    cov_start = connection.get("coverage_start")
    cov_end = connection.get("coverage_end")

    for rec in records:
        occurred_at = rec.occurred_at or now_iso
        existing = db.fetchone(
            """
            SELECT id FROM external_usage_records
            WHERE connection_id = %s AND external_record_id = %s
            """,
            (connection["id"], rec.external_record_id),
        )
        if existing is not None:
            duplicates_skipped += 1
            continue

        membership_id = None
        if rec.external_user_ref and connection.get("organization_id"):
            mapping = db.fetchone(
                """
                SELECT membership_id FROM employee_identity_mappings
                WHERE organization_id = %s AND external_user_ref = %s
                  AND (provider_code = %s OR provider_code = '*')
                ORDER BY CASE WHEN provider_code = %s THEN 0 ELSE 1 END ASC
                LIMIT 1
                """,
                (
                    connection["organization_id"],
                    rec.external_user_ref,
                    connection["provider_code"],
                    connection["provider_code"],
                ),
            )
            if mapping is not None:
                membership_id = mapping["membership_id"]

        in_tok = rec.input_tokens
        out_tok = rec.output_tokens
        tot_tok = rec.total_tokens
        if tot_tok is None and in_tok is not None and out_tok is not None:
            tot_tok = in_tok + out_tok

        estimated_value_minor: int | None = None
        quality_notes: list[str] = []
        if rec.quality_note:
            quality_notes.append(rec.quality_note)

        pricing = _resolve_pricing(
            db,
            provider_code=connection["provider_code"],
            model_name=rec.model_name,
            occurred_at=occurred_at,
        )
        if pricing is not None:
            est_minor, pricing_note = compute_estimated_token_value_minor(
                input_tokens=in_tok,
                output_tokens=out_tok,
                input_per_million_usd=pricing["input_per_million_usd"],
                output_per_million_usd=pricing["output_per_million_usd"],
            )
            estimated_value_minor = est_minor
            if pricing_note:
                quality_notes.append(pricing_note)
        elif in_tok is None and out_tok is None:
            quality_notes.append("Unavailable: source did not provide token metrics.")
        elif in_tok is None or out_tok is None:
            missing_tok = "input_tokens" if in_tok is None else "output_tokens"
            quality_notes.append(f"Unavailable token category: {missing_tok}.")

        source = rec.source or caps["default_source"]
        reliability = rec.reliability or caps["default_reliability"]
        row_id = str(uuid.uuid4())
        db.execute(
            """
            INSERT INTO external_usage_records (
                id, connection_id, external_record_id, account_id, organization_id,
                membership_id, model_name, requests_count, input_tokens, output_tokens,
                total_tokens, reported_cost_minor, estimated_value_minor, currency,
                source, reliability, quality_note, occurred_at, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (connection_id, external_record_id) DO NOTHING
            """,
            (
                row_id,
                connection["id"],
                rec.external_record_id,
                connection["account_id"],
                connection.get("organization_id"),
                membership_id,
                rec.model_name,
                rec.requests_count,
                in_tok,
                out_tok,
                tot_tok,
                rec.reported_cost_minor,
                estimated_value_minor,
                rec.currency.upper(),
                source,
                reliability,
                " ".join(quality_notes).strip(),
                occurred_at,
                now_iso,
            ),
        )
        inserted += 1
        created_ids.append(row_id)
        if cov_start is None or occurred_at < cov_start:
            cov_start = occurred_at
        if cov_end is None or occurred_at > cov_end:
            cov_end = occurred_at

    db.execute(
        """
        UPDATE integration_connections
        SET last_sync_success = %s, coverage_start = %s, coverage_end = %s, error_code = NULL, status = 'connected'
        WHERE id = %s
        """,
        (now_iso, cov_start, cov_end, connection["id"]),
    )
    record_audit_log(
        db,
        actor_user_id=actor_user_id,
        tenant_id=connection.get("organization_id") or connection["account_id"],
        action="integration.usage_synced",
        resource_type="integration_connection",
        resource_id=connection["id"],
        metadata={
            "provider_code": connection["provider_code"],
            "inserted": inserted,
            "duplicates_skipped": duplicates_skipped,
        },
    )
    return {
        "connection_id": connection["id"],
        "provider_code": connection["provider_code"],
        "inserted_count": inserted,
        "duplicates_skipped": duplicates_skipped,
        "record_ids": created_ids,
        "synced_at": now_iso,
    }


def _authorize_connection(db: Any, principal: dict[str, Any], connection_id: str) -> dict[str, Any]:
    row = db.fetchone("SELECT * FROM integration_connections WHERE id = %s", (connection_id,))
    if row is None:
        raise DomainError(404, "CONNECTION_NOT_FOUND", "Integration connection not found")
    if row.get("organization_id"):
        require_org_role(db, principal, row["organization_id"], {"owner", "admin", "developer", "billing"})
    elif row["account_id"] != principal["personal_account_id"]:
        raise DomainError(403, "FORBIDDEN", "Cannot access another account's integration connection")
    return row


@router.get("/connections/capabilities")
async def list_provider_capabilities() -> dict[str, Any]:
    return {"providers": list(PROVIDER_CAPABILITIES.values())}


@router.post("/connections")
async def create_connection(
    body: ConnectionCreateRequest,
    principal: dict = Depends(get_current_principal),
) -> dict[str, Any]:
    provider_code = body.provider_code.strip().lower()
    if provider_code not in PROVIDER_CAPABILITIES:
        raise DomainError(
            422,
            "UNSUPPORTED_PROVIDER",
            f"Unsupported integration provider: {provider_code}",
            {"supported": sorted(PROVIDER_CAPABILITIES.keys())},
        )
    if body.endpoint_url:
        safe, reason = is_safe_public_url(body.endpoint_url)
        if not safe:
            raise DomainError(422, "UNSAFE_ENDPOINT_URL", reason)

    clock = get_clock()
    now_iso = clock.now_iso()
    conn_id = str(uuid.uuid4())
    cfg = dict(body.config)
    if body.endpoint_url:
        cfg["endpoint_url"] = body.endpoint_url

    with get_db(write=True) as db:
        account_id = principal["personal_account_id"]
        if body.organization_id:
            require_org_role(db, principal, body.organization_id, {"owner", "admin", "developer"})
            org_acc = db.fetchone(
                "SELECT account_id AS id FROM organizations WHERE id = %s",
                (body.organization_id,),
            )
            if org_acc is None:
                raise DomainError(404, "ORGANIZATION_NOT_FOUND", "Organization account not found")
            account_id = org_acc["id"]

        masked = mask_credential(body.credential)
        db.execute(
            """
            INSERT INTO integration_connections (
                id, account_id, organization_id, provider_code, display_name,
                credential_ref, privacy_scope, status, config_json, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'connected', %s, %s)
            """,
            (
                conn_id,
                account_id,
                body.organization_id,
                provider_code,
                body.display_name.strip(),
                masked,
                body.privacy_scope,
                json.dumps(cfg),
                now_iso,
            ),
        )
        row = db.fetchone("SELECT * FROM integration_connections WHERE id = %s", (conn_id,))
        record_audit_log(
            db,
            actor_user_id=principal["user"]["id"],
            tenant_id=body.organization_id or account_id,
            action="integration.connection_created",
            resource_type="integration_connection",
            resource_id=conn_id,
            metadata={"provider_code": provider_code},
        )
    return _serialize_connection(row)


@router.get("/connections")
async def list_connections(
    organization_id: Optional[str] = Query(default=None),
    principal: dict = Depends(get_current_principal),
) -> dict[str, Any]:
    with get_db() as db:
        if organization_id:
            require_org_role(db, principal, organization_id, {"owner", "admin", "developer", "billing", "staff"})
            rows = db.fetchall(
                "SELECT * FROM integration_connections WHERE organization_id = %s ORDER BY created_at DESC",
                (organization_id,),
            )
        else:
            rows = db.fetchall(
                """
                SELECT * FROM integration_connections
                WHERE account_id = %s AND organization_id IS NULL
                ORDER BY created_at DESC
                """,
                (principal["personal_account_id"],),
            )
    return {"connections": [_serialize_connection(r) for r in rows]}


@router.get("/connections/{connection_id}/status")
async def get_connection_status(
    connection_id: str,
    principal: dict = Depends(get_current_principal),
) -> dict[str, Any]:
    with get_db() as db:
        row = _authorize_connection(db, principal, connection_id)
        cnt_row = db.fetchone(
            "SELECT COUNT(*) AS cnt FROM external_usage_records WHERE connection_id = %s",
            (connection_id,),
        )
    payload = _serialize_connection(row)
    payload["record_count"] = int(cnt_row["cnt"]) if cnt_row else 0
    return payload


@router.post("/connections/{connection_id}/sync")
async def sync_connection_usage(
    connection_id: str,
    body: SyncUsageRequest,
    principal: dict = Depends(get_current_principal),
) -> dict[str, Any]:
    now_iso = get_clock().now_iso()
    with get_db(write=True) as db:
        conn = _authorize_connection(db, principal, connection_id)
        return _perform_sync(
            db,
            connection=conn,
            records=body.records,
            actor_user_id=principal["user"]["id"],
            now_iso=now_iso,
        )


@router.post("/integrations/litellm/sync")
async def sync_litellm_endpoint(
    body: SyncUsageRequest,
    principal: dict = Depends(get_current_principal),
) -> dict[str, Any]:
    now_iso = get_clock().now_iso()
    with get_db(write=True) as db:
        if body.connection_id:
            conn = _authorize_connection(db, principal, body.connection_id)
        else:
            org_id = principal.get("active_org_id")
            if org_id:
                conn = db.fetchone(
                    """
                    SELECT * FROM integration_connections
                    WHERE organization_id = %s AND provider_code = 'litellm'
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (org_id,),
                )
            else:
                conn = db.fetchone(
                    """
                    SELECT * FROM integration_connections
                    WHERE account_id = %s AND organization_id IS NULL AND provider_code = 'litellm'
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (principal["personal_account_id"],),
                )
            if conn is None:
                raise DomainError(404, "LITELLM_NOT_CONNECTED", "No LiteLLM connection configured")
        return _perform_sync(
            db,
            connection=conn,
            records=body.records,
            actor_user_id=principal["user"]["id"],
            now_iso=now_iso,
        )


@router.post("/integrations/gateway/sync")
async def ingest_gateway_webhook(
    body: SyncUsageRequest,
    x_subsphere_api_key: Optional[str] = Header(default=None, alias="X-SubSphere-API-Key"),
) -> dict[str, Any]:
    """Service-key-authenticated ingestion endpoint for LiteLLM Proxy callback/sync."""
    if not x_subsphere_api_key:
        raise DomainError(401, "API_KEY_REQUIRED", "Missing X-SubSphere-API-Key")
    if not body.connection_id:
        raise DomainError(422, "CONNECTION_ID_REQUIRED", "connection_id is required")

    from app.core.security import hash_token

    key_hash = hash_token(x_subsphere_api_key)
    now_iso = get_clock().now_iso()
    with get_db(write=True) as db:
        api_key_row = db.fetchone(
            "SELECT * FROM api_keys WHERE key_hash = %s AND revoked_at IS NULL",
            (key_hash,),
        )
        if api_key_row is None:
            raise DomainError(401, "INVALID_API_KEY", "API key invalid or revoked")
        conn = db.fetchone("SELECT * FROM integration_connections WHERE id = %s", (body.connection_id,))
        if conn is None:
            raise DomainError(404, "CONNECTION_NOT_FOUND", "Connection not found")
        if conn.get("organization_id") != api_key_row["organization_id"]:
            raise DomainError(403, "WRONG_ORGANIZATION", "API key cannot sync another organization's connection")
        return _perform_sync(
            db,
            connection=conn,
            records=body.records,
            actor_user_id="system:gateway",
            now_iso=now_iso,
        )


@router.post("/pricing/versions")
async def create_pricing_version(
    body: PricingVersionCreateRequest,
    principal: dict = Depends(get_current_principal),
) -> dict[str, Any]:
    try:
        in_dec = parse_decimal(body.input_per_million_usd, "input_per_million_usd")
        out_dec = parse_decimal(body.output_per_million_usd, "output_per_million_usd")
    except ValueError as exc:
        raise DomainError(422, "INVALID_PRICING", str(exc)) from exc

    clock = get_clock()
    now_iso = clock.now_iso()
    eff_from = body.effective_from or now_iso
    pv_id = str(uuid.uuid4())
    with get_db(write=True) as db:
        if principal.get("active_org_id"):
            require_org_role(db, principal, principal["active_org_id"], {"owner", "admin", "developer", "billing"})
        db.execute(
            """
            INSERT INTO pricing_versions (
                id, provider_code, model_name, input_per_million_usd,
                output_per_million_usd, effective_from, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                pv_id,
                body.provider_code.strip().lower(),
                body.model_name.strip(),
                str(in_dec),
                str(out_dec),
                eff_from,
                now_iso,
            ),
        )
        row = db.fetchone("SELECT * FROM pricing_versions WHERE id = %s", (pv_id,))
    return row


@router.get("/pricing/versions")
async def list_pricing_versions(
    model_name: Optional[str] = Query(default=None),
    principal: dict = Depends(get_current_principal),
) -> dict[str, Any]:
    with get_db() as db:
        if model_name:
            rows = db.fetchall(
                "SELECT * FROM pricing_versions WHERE model_name = %s ORDER BY effective_from DESC, created_at DESC",
                (model_name,),
            )
        else:
            rows = db.fetchall(
                "SELECT * FROM pricing_versions ORDER BY effective_from DESC, created_at DESC"
            )
    return {"pricing_versions": rows}


@router.get("/ai/usage")
async def get_ai_usage_summary(
    organization_id: Optional[str] = Query(default=None),
    service_name: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    currency: str = Query(default="USD"),
    principal: dict = Depends(get_current_principal),
) -> dict[str, Any]:
    target_currency = currency.upper()
    with get_db() as db:
        if organization_id:
            require_org_role(
                db,
                principal,
                organization_id,
                {"owner", "admin", "billing", "developer", "staff"},
            )
            sql = """
                SELECT r.*, c.provider_code, c.display_name AS connection_name
                FROM external_usage_records r
                JOIN integration_connections c ON c.id = r.connection_id
                WHERE r.organization_id = %s
            """
            params: list[Any] = [organization_id]
        else:
            sql = """
                SELECT r.*, c.provider_code, c.display_name AS connection_name
                FROM external_usage_records r
                JOIN integration_connections c ON c.id = r.connection_id
                WHERE r.account_id = %s AND r.organization_id IS NULL
            """
            params = [principal["personal_account_id"]]

        if source:
            sql += " AND r.source = %s"
            params.append(source)
        sql += " ORDER BY r.occurred_at DESC, r.created_at DESC"
        raw_records = db.fetchall(sql, tuple(params))

        # Deduplicate overlapping OTel + LiteLLM records sharing request_ref without double-counting tokens
        seen_refs: dict[str, dict[str, Any]] = {}
        passthrough: list[dict[str, Any]] = []
        for r in raw_records:
            req_ref = (r.get("request_ref") or "").strip()
            if not req_ref:
                passthrough.append(r)
                continue
            if req_ref not in seen_refs:
                seen_refs[req_ref] = r
            else:
                existing = seen_refs[req_ref]
                if existing.get("reported_cost_minor") is None and r.get("reported_cost_minor") is not None:
                    seen_refs[req_ref] = r
        records = list(seen_refs.values()) + passthrough
        records.sort(key=lambda x: (str(x.get("occurred_at") or ""), str(x.get("created_at") or "")), reverse=True)
        deduplicated_count = len(raw_records) - len(records)

        # Subscription fee strictly separated (for Individual view, active personal AI/specified subs in currency)
        subscription_fee_minor: int | None = None
        if not organization_id:
            if service_name:
                sub_rows = db.fetchall(
                    """
                    SELECT price_minor FROM personal_subscriptions
                    WHERE account_id = %s AND LOWER(service_name) LIKE %s
                      AND currency = %s AND status != 'cancelled_user_recorded'
                    """,
                    (principal["personal_account_id"], f"%{service_name.lower()}%", target_currency),
                )
            else:
                sub_rows = db.fetchall(
                    """
                    SELECT price_minor FROM personal_subscriptions
                    WHERE account_id = %s AND LOWER(category) = 'ai'
                      AND currency = %s AND status != 'cancelled_user_recorded'
                    """,
                    (principal["personal_account_id"], target_currency),
                )
            if sub_rows:
                subscription_fee_minor = sum(int(r["price_minor"]) for r in sub_rows)
        else:
            # Organization external AI/SaaS subscription fees in currency (payable)
            ext_rows = db.fetchall(
                """
                SELECT seats_purchased, unit_price_minor
                FROM external_subscriptions
                WHERE organization_id = %s AND currency = %s
                  AND status IN ('active', 'trial')
                """,
                (organization_id, target_currency),
            )
            sub_rows_org = db.fetchall(
                """
                SELECT s.id, pv.price_minor, s.quantity
                FROM subscriptions s
                JOIN organizations o ON o.account_id = s.buyer_account_id
                JOIN plan_versions pv ON pv.id = s.plan_version_id
                WHERE o.id = %s AND pv.currency = %s
                  AND s.status IN ('active', 'trialing')
                """,
                (organization_id, target_currency),
            )
            total_org_fee = sum(int(r["seats_purchased"]) * int(r["unit_price_minor"]) for r in ext_rows) + sum(
                int(r["price_minor"]) * int(r["quantity"]) for r in sub_rows_org
            )
            if ext_rows or sub_rows_org:
                subscription_fee_minor = total_org_fee

        # Also pull personal activity (browser/desktop) so callers see full source-qualified view
        activity_rows: list[dict[str, Any]] = []
        is_authorized = False
        last_ping_at: str | None = None
        if not organization_id:
            activity_rows = db.fetchall(
                """
                SELECT id, domain_or_app, source, observed_active_seconds, visit_count,
                       tokens, reliability, quality_note, occurred_at
                FROM personal_activity
                WHERE account_id = %s
                ORDER BY occurred_at DESC
                """,
                (principal["personal_account_id"],),
            )
            auth_conns = db.fetchall(
                """
                SELECT status, last_sync_success, config_json FROM integration_connections
                WHERE account_id = %s AND organization_id IS NULL AND status = 'connected'
                """,
                (principal["personal_account_id"],),
            )
            mon_rows = db.fetchall(
                """
                SELECT enabled FROM personal_monitoring_sources
                WHERE account_id = %s AND enabled = 1
                """,
                (principal["personal_account_id"],),
            )
            is_authorized = bool(auth_conns or mon_rows)
            sync_times = [str(c["last_sync_success"]) for c in auth_conns if c.get("last_sync_success")]
            if sync_times:
                last_ping_at = max(sync_times)

        intelligence = build_usage_intelligence(
            db,
            account_id=None if organization_id else principal["personal_account_id"],
            organization_id=organization_id,
            currency=target_currency,
        )

    # Aggregate without ever coercing unavailable/missing categories to 0
    input_toks = [int(r["input_tokens"]) for r in records if r["input_tokens"] is not None]
    output_toks = [int(r["output_tokens"]) for r in records if r["output_tokens"] is not None]
    total_toks = [int(r["total_tokens"]) for r in records if r["total_tokens"] is not None]
    requests_list = [int(r["requests_count"]) for r in records if r["requests_count"] is not None]
    reported_costs = [
        int(r["reported_cost_minor"])
        for r in records
        if r["reported_cost_minor"] is not None and r["currency"] == target_currency
    ]
    estimated_vals = [
        int(r["estimated_value_minor"])
        for r in records
        if r["estimated_value_minor"] is not None and r["currency"] == target_currency
    ]

    # Recompute exact Decimal estimate total for records that have both tokens and estimated_value_minor
    input_total: int | None = sum(input_toks) if input_toks else None
    output_total: int | None = sum(output_toks) if output_toks else None
    tokens_total: int | None = (
        sum(total_toks)
        if total_toks and (input_total is not None and output_total is not None)
        else (sum(total_toks) if total_toks else None)
    )
    requests_total: int | None = sum(requests_list) if requests_list else None
    reported_cost_minor: int | None = sum(reported_costs) if reported_costs else None
    estimated_value_minor: int | None = sum(estimated_vals) if estimated_vals else None
    used_credits_minor = (reported_cost_minor or 0) + (estimated_value_minor or 0)
    monthly_limit_minor = (
        subscription_fee_minor
        if (subscription_fee_minor is not None and subscription_fee_minor > 0)
        else 2000
    )

    missing_categories: list[str] = []
    if input_total is None:
        missing_categories.append("input_tokens")
    if output_total is None:
        missing_categories.append("output_tokens")

    by_source: dict[str, dict[str, Any]] = {}
    for r in records:
        src = r["source"]
        if src not in by_source:
            by_source[src] = {"source": src, "records": 0, "tokens": 0, "reported_cost_minor": 0}
        by_source[src]["records"] += 1
        by_source[src]["tokens"] += int(r.get("total_tokens") or 0)
        by_source[src]["reported_cost_minor"] += int(r.get("reported_cost_minor") or 0)

    return {
        "currency": target_currency,
        "authorized": is_authorized,
        "credits": {
            "monthly_limit_minor": monthly_limit_minor,
            "used_credits_minor": used_credits_minor,
            "remaining_credits_minor": max(0, monthly_limit_minor - used_credits_minor),
            "currency": target_currency,
            "last_ping_at": last_ping_at,
        },
        "cost_separation": {
            "subscription_fee_minor": subscription_fee_minor,
            "reported_extra_usage_minor": reported_cost_minor,
            "estimated_token_value_minor": estimated_value_minor,
            "note": (
                "Subscription fee, reported API cost, and estimated token value are separated strictly; "
                "not additive."
            ),
        },
        "tokens": {
            "input_tokens": input_total,
            "output_tokens": output_total,
            "total_tokens": tokens_total,
            "requests_count": requests_total,
            "unavailable_fields": missing_categories,
        },
        "records": records,
        "observed_activity": activity_rows,
        "by_source": list(by_source.values()),
        "deduplicated_count": deduplicated_count,
        "record_count": len(records),
        "intelligence": intelligence,
    }


# ── R5 & R6 & R7: Claude Code OTel, LiteLLM Gateway Wizard & Provider AI Usage ─────────


class OtelPairTokenRequest(BaseModel):
    organization_id: Optional[str] = None
    device_name: str = "claude-code-cli"


class LiteLLMVirtualKeyRequest(BaseModel):
    organization_id: Optional[str] = None
    key_alias: str = "subsphere-workspace"
    billing_lane_default: str = "api_key"


class LiteLLMTestRequest(BaseModel):
    organization_id: Optional[str] = None
    model_name: str = "gpt-4o-mini"
    billing_lane: str = "api_key"  # api_key | claude_subscription
    input_tokens: int = Field(default=120, ge=1)
    output_tokens: int = Field(default=48, ge=0)
    request_ref: Optional[str] = None


def _ensure_otel_connection(
    db: Any, *, account_id: str, organization_id: Optional[str], display_name: str = "Claude Code OpenTelemetry"
) -> str:
    row = db.fetchone(
        """
        SELECT id FROM integration_connections
        WHERE account_id = %s AND provider_code = 'claude_code_otel'
          AND ((%s IS NULL AND organization_id IS NULL) OR organization_id = %s)
        ORDER BY created_at DESC LIMIT 1
        """,
        (account_id, organization_id, organization_id),
    )
    if row:
        return row["id"]
    conn_id = str(uuid.uuid4())
    now_iso = get_clock().now_iso()
    db.execute(
        """
        INSERT INTO integration_connections (
            id, account_id, organization_id, provider_code, display_name,
            credential_ref, privacy_scope, status, config_json, created_at
        )
        VALUES (%s, %s, %s, 'claude_code_otel', %s, 'otel_paired', 'metadata_only', 'connected', '{}', %s)
        """,
        (conn_id, account_id, organization_id, display_name, now_iso),
    )
    return conn_id


def _resolve_otel_target(request: Request, db: Any) -> tuple[str, Optional[str], str, Optional[str]]:
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else (request.cookies.get("subsphere_session") or "")
    if not token:
        raise DomainError(401, "UNAUTHENTICATED", "Valid OTel pair token or session authentication required.")

    if token.startswith("otel_live_"):
        t_hash = hash_token(token)
        tc = db.fetchone(
            """
            SELECT * FROM tracking_connections
            WHERE token_hash = %s AND source_type = 'claude_code_otel'
            """,
            (t_hash,),
        )
        if not tc:
            raise DomainError(401, "INVALID_OTEL_TOKEN", "Invalid Claude Code OpenTelemetry token.")
        if tc.get("revoked_at") is not None or tc.get("status") == "revoked":
            raise DomainError(401, "OTEL_TOKEN_REVOKED", "This OpenTelemetry token has been revoked.")
        now_iso = get_clock().now_iso()
        db.execute("UPDATE tracking_connections SET last_seen = %s, last_synced = %s WHERE id = %s", (now_iso, now_iso, tc["id"]))
        org_id = tc.get("organization_id")
        ic_id = _ensure_otel_connection(db, account_id=tc["account_id"], organization_id=org_id)
        return tc["account_id"], org_id, ic_id, None

    principal = get_session_principal(db, token)
    org_id = request.query_params.get("organization_id") if hasattr(request, "query_params") else None
    if org_id:
        require_org_role(db, principal, org_id, {"owner", "admin", "billing", "developer"})
        org = db.fetchone("SELECT account_id FROM organizations WHERE id = %s", (org_id,))
        acc_id = org["account_id"] if org else principal["personal_account_id"]
    else:
        acc_id = principal["personal_account_id"]
    ic_id = _ensure_otel_connection(db, account_id=acc_id, organization_id=org_id)
    return acc_id, org_id, ic_id, principal["user"]["id"]


@router.post("/integrations/otel/pair-token", status_code=201)
def create_otel_pair_token(
    payload: OtelPairTokenRequest | None = None,
    principal: dict = Depends(get_current_principal),
):
    now_iso = get_clock().now_iso()
    raw_tok = f"otel_live_{generate_token(20)}"
    tok_hash = hash_token(raw_tok)
    tc_id = str(uuid.uuid4())
    org_id = payload.organization_id if payload else None
    device = (payload.device_name.strip() if payload and payload.device_name else "claude-code-cli")

    with get_db(write=True) as db:
        if org_id:
            require_org_role(db, principal, org_id, {"owner", "admin", "billing", "developer"})
            org = db.fetchone("SELECT account_id FROM organizations WHERE id = %s", (org_id,))
            if not org:
                raise DomainError(404, "ORG_NOT_FOUND", "Organization not found.")
            acc_id = org["account_id"]
        else:
            acc_id = principal["personal_account_id"]

        ic_id = _ensure_otel_connection(db, account_id=acc_id, organization_id=org_id)
        db.execute(
            """
            INSERT INTO tracking_connections (
                id, account_id, organization_id, source_type, status, device_name,
                token_hash, permissions_json, allowlist_json, last_seen, created_at
            )
            VALUES (%s, %s, %s, 'claude_code_otel', 'connected', %s, %s, '["metrics_only"]', '[]', %s, %s)
            """,
            (tc_id, acc_id, org_id, device, tok_hash, now_iso, now_iso),
        )
        record_audit_log(
            db,
            actor_user_id=principal["user"]["id"],
            tenant_id=org_id or acc_id,
            action="otel.pair_token_created",
            resource_type="tracking_connection",
            resource_id=tc_id,
        )

    export_env = "\n".join(
        [
            "export CLAUDE_CODE_ENABLE_TELEMETRY=1",
            "export OTEL_METRICS_EXPORTER=otlp",
            "export OTEL_EXPORTER_OTLP_PROTOCOL=http/json",
            "export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4317",
            f'export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Bearer {raw_tok}"',
        ]
    )
    return {
        "connection_id": tc_id,
        "integration_connection_id": ic_id,
        "otel_token": raw_tok,
        "organization_id": org_id,
        "collector_endpoint": "http://127.0.0.1:4317/v1/metrics",
        "backend_metrics_endpoint": "http://127.0.0.1:8000/v1/metrics",
        "export_env_block": export_env,
        "privacy_guarantee": "Rejects prompts, message content, and raw tool parameters; aggregates metrics only.",
    }


@router.post("/integrations/otel/claude-code", status_code=200)
@otel_public_router.post("/v1/metrics", status_code=200)
def ingest_claude_code_otel(request: Request, body: dict[str, Any]):
    with get_db(write=True) as db:
        acc_id, org_id, ic_id, _actor = _resolve_otel_target(request, db)
        result = ClaudeCodeOtelIngestAdapter.ingest(
            db,
            account_id=acc_id,
            organization_id=org_id,
            connection_id=ic_id,
            payload=body,
        )
    return {"accepted": True, **result}


@router.get("/integrations/otel/status", status_code=200)
def get_otel_status(
    organization_id: Optional[str] = Query(default=None),
    principal: dict = Depends(get_current_principal),
):
    with get_db(write=False) as db:
        if organization_id:
            require_org_role(db, principal, organization_id, {"owner", "admin", "billing", "developer"})
            conns = db.fetchall(
                """
                SELECT id, status, device_name, last_seen, last_synced, revoked_at, created_at
                FROM tracking_connections
                WHERE organization_id = %s AND source_type = 'claude_code_otel'
                ORDER BY created_at DESC LIMIT 5
                """,
                (organization_id,),
            )
            recs = db.fetchall(
                "SELECT total_tokens, cache_read_tokens, cache_creation_tokens, session_id FROM external_usage_records WHERE organization_id = %s AND source = 'CLAUDE_CODE_OTEL'",
                (organization_id,),
            )
        else:
            acc_id = principal["personal_account_id"]
            conns = db.fetchall(
                """
                SELECT id, status, device_name, last_seen, last_synced, revoked_at, created_at
                FROM tracking_connections
                WHERE account_id = %s AND organization_id IS NULL AND source_type = 'claude_code_otel'
                ORDER BY created_at DESC LIMIT 5
                """,
                (acc_id,),
            )
            recs = db.fetchall(
                "SELECT total_tokens, cache_read_tokens, cache_creation_tokens, session_id FROM external_usage_records WHERE account_id = %s AND organization_id IS NULL AND source = 'CLAUDE_CODE_OTEL'",
                (acc_id,),
            )
    active = next((c for c in conns if c["status"] == "connected" and c["revoked_at"] is None), None)
    return {
        "connected": bool(active),
        "connection": active,
        "total_tokens": sum(int(r.get("total_tokens") or 0) for r in recs),
        "cache_read_tokens": sum(int(r.get("cache_read_tokens") or 0) for r in recs),
        "cache_creation_tokens": sum(int(r.get("cache_creation_tokens") or 0) for r in recs),
        "session_count": len({r["session_id"] for r in recs if r.get("session_id")}),
        "record_count": len(recs),
    }


@router.get("/integrations/mcp/tools", status_code=200)
def list_mcp_tools(principal: dict = Depends(get_current_principal)):
    return {
        "telemetry_capability": False,
        "disclosure": "MCP tools provide SubSphere convenience actions only; they do not meter Claude Code or AI token usage. Use Claude Code OpenTelemetry or LiteLLM Proxy for verified token counts.",
        "tools": [
            {"name": "subsphere.list_subscriptions", "purpose": "Read tracked subscriptions"},
            {"name": "subsphere.create_reminder_rule", "purpose": "Configure renewal reminders"},
            {"name": "subsphere.export_history", "purpose": "Generate report snapshot"},
        ],
    }


@router.get("/integrations/litellm/health", status_code=200)
def get_litellm_health(principal: dict = Depends(get_current_principal)):
    settings = get_settings()
    base_url = (settings.litellm_url or "").rstrip("/")
    if not base_url:
        return {
            "configured": False,
            "reachable": False,
            "url": "",
            "models": [],
            "actionable_error": "LITELLM_URL is not configured. Start the AI profile (make up-ai) or set LITELLM_URL.",
        }
    try:
        with urllib.request.urlopen(f"{base_url}/health", timeout=1.5) as resp:
            healthy = resp.status == 200
        models: list[str] = []
        try:
            with urllib.request.urlopen(f"{base_url}/v1/models", timeout=1.5) as mresp:
                mdata = json.loads(mresp.read().decode("utf-8"))
                models = [m["id"] for m in mdata.get("data", []) if "id" in m]
        except Exception:
            pass
        return {
            "configured": True,
            "reachable": healthy,
            "url": base_url,
            "models": models,
            "actionable_error": None if healthy else "LiteLLM responded with non-200 health status.",
        }
    except Exception as exc:
        return {
            "configured": True,
            "reachable": False,
            "url": base_url,
            "models": [],
            "actionable_error": f"LiteLLM Gateway unreachable at {base_url} ({exc}). Start the AI Compose profile (`make up-ai`) and verify LITELLM_MASTER_KEY.",
        }


@router.post("/integrations/litellm/virtual-key", status_code=201)
def create_litellm_virtual_key(
    payload: LiteLLMVirtualKeyRequest | None = None,
    principal: dict = Depends(get_current_principal),
):
    settings = get_settings()
    now_iso = get_clock().now_iso()
    req_data = payload or LiteLLMVirtualKeyRequest()
    org_id = req_data.organization_id

    with get_db(write=True) as db:
        if org_id:
            require_org_role(db, principal, org_id, {"owner", "admin", "billing", "developer"})
            org = db.fetchone("SELECT account_id FROM organizations WHERE id = %s", (org_id,))
            if not org:
                raise DomainError(404, "ORG_NOT_FOUND", "Organization not found.")
            acc_id = org["account_id"]
        else:
            acc_id = principal["personal_account_id"]

        mode = "local_scoped_key"
        virtual_key = f"sk-subsphere-vk-{generate_token(12)}"
        base_url = (settings.litellm_url or "").rstrip("/")
        if base_url:
            try:
                req = urllib.request.Request(
                    f"{base_url}/key/generate",
                    data=json.dumps({"user_id": principal["user"]["id"], "key_alias": req_data.key_alias}).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {settings.litellm_master_key or 'local'}",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=1.5) as resp:
                    if resp.status == 200:
                        body = json.loads(resp.read().decode("utf-8"))
                        if body.get("key"):
                            virtual_key = str(body["key"])
                            mode = "live_gateway"
            except Exception:
                pass

        conn_id = str(uuid.uuid4())
        masked = mask_credential(virtual_key)
        cfg = {
            "key_alias": req_data.key_alias,
            "billing_lane_default": req_data.billing_lane_default,
            "virtual_key_hash": hash_token(virtual_key),
            "mode": mode,
        }
        db.execute(
            """
            INSERT INTO integration_connections (
                id, account_id, organization_id, provider_code, display_name,
                credential_ref, privacy_scope, status, config_json, created_at
            )
            VALUES (%s, %s, %s, 'litellm', %s, %s, 'metadata_only', 'connected', %s, %s)
            """,
            (conn_id, acc_id, org_id, f"LiteLLM Virtual Key ({req_data.key_alias})", masked, json.dumps(cfg), now_iso),
        )
        record_audit_log(
            db,
            actor_user_id=principal["user"]["id"],
            tenant_id=org_id or acc_id,
            action="litellm.virtual_key_created",
            resource_type="integration_connection",
            resource_id=conn_id,
        )

    return {
        "connection_id": conn_id,
        "provider_code": "litellm",
        "virtual_key": virtual_key,
        "credential_masked": masked,
        "mode": mode,
        "gateway_url": base_url or "http://127.0.0.1:4000",
        "billing_lane_default": req_data.billing_lane_default,
    }


@router.post("/integrations/litellm/test-request", status_code=200)
def run_litellm_test_request(
    payload: LiteLLMTestRequest | None = None,
    principal: dict = Depends(get_current_principal),
):
    req_data = payload or LiteLLMTestRequest()
    if req_data.billing_lane not in ("api_key", "claude_subscription"):
        raise DomainError(422, "INVALID_BILLING_LANE", "billing_lane must be 'api_key' or 'claude_subscription'.")
    settings = get_settings()
    now_iso = get_clock().now_iso()
    org_id = req_data.organization_id
    req_ref = req_data.request_ref or f"lit-req-{uuid.uuid4().hex[:10]}"

    with get_db(write=True) as db:
        if org_id:
            require_org_role(db, principal, org_id, {"owner", "admin", "billing", "developer"})
            org = db.fetchone("SELECT account_id FROM organizations WHERE id = %s", (org_id,))
            if not org:
                raise DomainError(404, "ORG_NOT_FOUND", "Organization not found.")
            acc_id = org["account_id"]
        else:
            acc_id = principal["personal_account_id"]

        conn = db.fetchone(
            """
            SELECT id FROM integration_connections
            WHERE account_id = %s AND provider_code = 'litellm'
              AND ((%s IS NULL AND organization_id IS NULL) OR organization_id = %s)
            ORDER BY created_at DESC LIMIT 1
            """,
            (acc_id, org_id, org_id),
        )
        if not conn:
            conn_id = str(uuid.uuid4())
            db.execute(
                """
                INSERT INTO integration_connections (
                    id, account_id, organization_id, provider_code, display_name,
                    credential_ref, privacy_scope, status, config_json, created_at
                )
                VALUES (%s, %s, %s, 'litellm', 'LiteLLM Proxy Gateway', 'sk-sub***demo', 'metadata_only', 'connected', '{}', %s)
                """,
                (conn_id, acc_id, org_id, now_iso),
            )
        else:
            conn_id = conn["id"]

        in_tok = req_data.input_tokens
        out_tok = req_data.output_tokens
        execution_mode = "contract_fixture"
        base_url = (settings.litellm_url or "").rstrip("/")
        if base_url:
            try:
                gw_req = urllib.request.Request(
                    f"{base_url}/v1/chat/completions",
                    data=json.dumps(
                        {
                            "model": req_data.model_name,
                            "billing_lane": req_data.billing_lane,
                            "request_ref": req_ref,
                            "input_tokens": in_tok,
                            "output_tokens": out_tok,
                            "messages": [{"role": "user", "content": "health probe"}],
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(gw_req, timeout=1.5) as gw_resp:
                    if gw_resp.status == 200:
                        gbody = json.loads(gw_resp.read().decode("utf-8"))
                        usage = gbody.get("usage") or {}
                        in_tok = int(usage.get("prompt_tokens", in_tok))
                        out_tok = int(usage.get("completion_tokens", out_tok))
                        execution_mode = "live_gateway"
            except Exception:
                pass

        pricing = _resolve_pricing(db, provider_code="litellm", model_name=req_data.model_name, occurred_at=now_iso)
        in_rate = parse_decimal(pricing["input_per_million_usd"]) if pricing else Decimal("1.00")
        out_rate = parse_decimal(pricing["output_per_million_usd"]) if pricing else Decimal("4.00")
        est_minor, _ = compute_estimated_token_value_minor(
            input_tokens=in_tok,
            output_tokens=out_tok,
            input_per_million_usd=str(in_rate),
            output_per_million_usd=str(out_rate),
        )
        rep_cost = max(1, est_minor or 0) if req_data.billing_lane == "api_key" else None
        quality_note = (
            f"LiteLLM API-key billed request ({execution_mode})."
            if req_data.billing_lane == "api_key"
            else f"Covered by recurring Claude subscription ({execution_mode}); no marginal API charge added."
        )

        rec_id = str(uuid.uuid4())
        db.execute(
            """
            INSERT INTO external_usage_records (
                id, connection_id, external_record_id, account_id, organization_id, membership_id,
                model_name, requests_count, input_tokens, output_tokens, total_tokens,
                reported_cost_minor, estimated_value_minor, currency, source, reliability,
                billing_lane, request_ref, quality_note, occurred_at, created_at
            )
            VALUES (%s, %s, %s, %s, %s, NULL, %s, 1, %s, %s, %s, %s, %s, 'USD', 'GATEWAY_ROUTED', 'EXACT_IF_ROUTED', %s, %s, %s, %s, %s)
            """,
            (
                rec_id,
                conn_id,
                req_ref,
                acc_id,
                org_id,
                req_data.model_name,
                in_tok,
                out_tok,
                in_tok + out_tok,
                rep_cost,
                est_minor,
                req_data.billing_lane,
                req_ref,
                quality_note,
                now_iso,
                now_iso,
            ),
        )
        db.execute("UPDATE integration_connections SET last_sync_success = %s WHERE id = %s", (now_iso, conn_id))

    return {
        "record_id": rec_id,
        "request_ref": req_ref,
        "execution_mode": execution_mode,
        "billing_lane": req_data.billing_lane,
        "model_name": req_data.model_name,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "total_tokens": in_tok + out_tok,
        "reported_cost_minor": rep_cost,
        "estimated_value_minor": est_minor,
        "quality_note": quality_note,
    }


@router.get("/provider/ai/usage", status_code=200)
def get_provider_employee_ai_usage(
    organization_id: str = Query(...),
    principal: dict = Depends(get_current_principal),
):
    with get_db(write=False) as db:
        require_org_role(db, principal, organization_id, {"owner", "admin", "billing", "developer"})
        org = db.fetchone("SELECT id, name, default_currency FROM organizations WHERE id = %s", (organization_id,))
        if not org:
            raise DomainError(404, "ORG_NOT_FOUND", "Organization not found.")

        members = db.fetchall(
            """
            SELECT m.id as membership_id, m.user_id, m.role, u.email, u.full_name
            FROM memberships m
            JOIN users u ON u.id = m.user_id
            WHERE m.organization_id = %s AND m.status = 'active'
            ORDER BY u.email
            """,
            (organization_id,),
        )
        raw_records = db.fetchall(
            """
            SELECT * FROM external_usage_records
            WHERE organization_id = %s
            ORDER BY occurred_at DESC
            """,
            (organization_id,),
        )

    # Deduplicate request_ref
    seen: dict[str, dict[str, Any]] = {}
    records: list[dict[str, Any]] = []
    for r in raw_records:
        rr = (r.get("request_ref") or "").strip()
        if rr:
            if rr not in seen:
                seen[rr] = r
        else:
            records.append(r)
    records = list(seen.values()) + records

    by_member: dict[str, dict[str, Any]] = {}
    for m in members:
        by_member[m["membership_id"]] = {
            "membership_id": m["membership_id"],
            "email": m["email"],
            "full_name": m["full_name"],
            "role": m["role"],
            "requests_count": 0,
            "total_tokens": 0,
            "reported_cost_minor": 0,
            "estimated_value_minor": 0,
        }
    unassigned = {"requests_count": 0, "total_tokens": 0, "reported_cost_minor": 0, "estimated_value_minor": 0}
    for r in records:
        mid = r.get("membership_id")
        bucket = by_member.get(mid) if mid and mid in by_member else unassigned
        bucket["requests_count"] += int(r.get("requests_count") or 1)
        bucket["total_tokens"] += int(r.get("total_tokens") or 0)
        bucket["reported_cost_minor"] += int(r.get("reported_cost_minor") or 0)
        bucket["estimated_value_minor"] += int(r.get("estimated_value_minor") or 0)

    return {
        "organization_id": organization_id,
        "org_name": org["name"],
        "currency": org["default_currency"],
        "employees": list(by_member.values()),
        "org_shared_unattributed": unassigned,
        "total_tokens": sum(int(r.get("total_tokens") or 0) for r in records),
        "total_reported_cost_minor": sum(int(r.get("reported_cost_minor") or 0) for r in records),
        "record_count": len(records),
        "privacy_boundary": "Personal account browser/desktop activity is strictly excluded; only organization-authorized telemetry is shown.",
    }


class OneClickAuthorizeRequest(BaseModel):
    allowlist: list[str] = Field(
        default_factory=lambda: [
            "claude.ai",
            "chatgpt.com",
            "gemini.google.com",
            "perplexity.ai",
            "cursor.com",
            "github.com",
        ]
    )
    billing_lane: str = "claude_subscription"
    model_name: str = "claude-3-5-sonnet"


class PingCreditsRequest(BaseModel):
    auto_authorize: bool = False
    model_name: str = "claude-3-5-sonnet"
    billing_lane: str = "claude_subscription"


def _upsert_monitoring_source(db: Any, *, acc_id: str, source_type: str, enabled: bool, allowlist: list[str], now_iso: str) -> None:
    existing = db.fetchone(
        "SELECT id FROM personal_monitoring_sources WHERE account_id = %s AND source_type = %s",
        (acc_id, source_type),
    )
    if existing:
        db.execute(
            "UPDATE personal_monitoring_sources SET enabled = %s, allowlist_json = %s, updated_at = %s WHERE id = %s",
            (1 if enabled else 0, json.dumps(allowlist), now_iso, existing["id"]),
        )
    else:
        db.execute(
            """
            INSERT INTO personal_monitoring_sources (id, account_id, source_type, enabled, allowlist_json, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (str(uuid.uuid4()), acc_id, source_type, 1 if enabled else 0, json.dumps(allowlist), now_iso),
        )


def _ensure_one_click_integration(
    db: Any,
    *,
    acc_id: str,
    provider_code: str,
    display_name: str,
    credential_ref: str,
    now_iso: str,
) -> str:
    existing = db.fetchone(
        """
        SELECT id, config_json FROM integration_connections
        WHERE account_id = %s AND organization_id IS NULL AND provider_code = %s
        ORDER BY created_at DESC LIMIT 1
        """,
        (acc_id, provider_code),
    )
    if existing:
        try:
            cfg = json.loads(existing.get("config_json") or "{}")
        except Exception:
            cfg = {}
        cfg["authorized_one_click"] = True
        db.execute(
            "UPDATE integration_connections SET status = 'connected', config_json = %s WHERE id = %s",
            (json.dumps(cfg), existing["id"]),
        )
        return existing["id"]

    conn_id = str(uuid.uuid4())
    db.execute(
        """
        INSERT INTO integration_connections (
            id, account_id, organization_id, provider_code, display_name,
            credential_ref, privacy_scope, status, config_json, created_at
        )
        VALUES (%s, %s, NULL, %s, %s, %s, 'metadata_only', 'connected', %s, %s)
        """,
        (conn_id, acc_id, provider_code, display_name, credential_ref, json.dumps({"authorized_one_click": True}), now_iso),
    )
    return conn_id


def _ping_credits_internal(
    db: Any,
    *,
    acc_id: str,
    actor_user_id: str,
    now_iso: str,
    model_name: str = "claude-3-5-sonnet",
    billing_lane: str = "claude_subscription",
) -> dict[str, Any]:
    """Perform a read-only sync against LiteLLM /v1/credits and /spend/logs (never fabricates usage)."""
    settings = get_settings()
    conn_id = _ensure_one_click_integration(
        db,
        acc_id=acc_id,
        provider_code="litellm",
        display_name="LiteLLM AI Token & Credits Meter",
        credential_ref="sk-sub***live",
        now_iso=now_iso,
    )

    execution_mode = "no_gateway_configured"
    gateway_credits: dict[str, Any] | None = None
    spend_logs: list[dict[str, Any]] = []
    new_records_synced = 0

    base_url = (settings.litellm_url or "").rstrip("/")
    if base_url:
        gw_headers = {"Authorization": f"Bearer {settings.litellm_master_key}"} if settings.litellm_master_key else {}
        execution_mode = "gateway_unreachable"
        try:
            c_req = urllib.request.Request(f"{base_url}/v1/credits", headers=gw_headers, method="GET")
            with urllib.request.urlopen(c_req, timeout=1.5) as c_resp:
                if c_resp.status == 200:
                    gateway_credits = json.loads(c_resp.read().decode("utf-8"))
                    execution_mode = "live_gateway"
        except Exception:
            pass

        try:
            s_req = urllib.request.Request(f"{base_url}/spend/logs", headers=gw_headers, method="GET")
            with urllib.request.urlopen(s_req, timeout=1.5) as s_resp:
                if s_resp.status == 200:
                    sdata = json.loads(s_resp.read().decode("utf-8"))
                    if isinstance(sdata, list):
                        spend_logs = sdata
                    elif isinstance(sdata, dict):
                        spend_logs = list(sdata.get("logs") or sdata.get("data") or [])
                    execution_mode = "live_gateway"
        except Exception:
            pass

    for entry in spend_logs:
        if not isinstance(entry, dict):
            continue
        ext_id = str(entry.get("request_id") or entry.get("id") or entry.get("call_id") or entry.get("request_ref") or "").strip()
        if not ext_id:
            continue
        req_ref_val = str(entry.get("request_ref") or ext_id).strip() or None

        existing = db.fetchone(
            """
            SELECT id FROM external_usage_records
            WHERE account_id = %s AND organization_id IS NULL
              AND (
                external_record_id = %s
                OR (%s IS NOT NULL AND (external_record_id = %s OR request_ref = %s))
              )
            LIMIT 1
            """,
            (acc_id, ext_id, req_ref_val, req_ref_val, req_ref_val),
        )
        if existing is not None:
            continue

        in_tok_raw = entry.get("input_tokens") if entry.get("input_tokens") is not None else entry.get("prompt_tokens")
        out_tok_raw = entry.get("output_tokens") if entry.get("output_tokens") is not None else entry.get("completion_tokens")
        in_tok = int(in_tok_raw) if in_tok_raw is not None else None
        out_tok = int(out_tok_raw) if out_tok_raw is not None else None
        tot_tok_raw = entry.get("total_tokens")
        if tot_tok_raw is not None:
            tot_tok = int(tot_tok_raw)
        elif in_tok is not None and out_tok is not None:
            tot_tok = in_tok + out_tok
        else:
            tot_tok = None

        entry_model = str(entry.get("model") or entry.get("model_name") or model_name)
        occurred_at = str(entry.get("occurred_at") or entry.get("startTime") or now_iso)
        entry_lane = str(
            entry.get("billing_lane")
            or ("api_key" if entry.get("reported_cost_minor") is not None else billing_lane)
        )

        pricing = _resolve_pricing(db, provider_code="litellm", model_name=entry_model, occurred_at=occurred_at)
        est_minor: int | None = None
        if in_tok is not None or out_tok is not None:
            in_rate = parse_decimal(pricing["input_per_million_usd"]) if pricing else Decimal("1.00")
            out_rate = parse_decimal(pricing["output_per_million_usd"]) if pricing else Decimal("4.00")
            est_minor, _ = compute_estimated_token_value_minor(
                input_tokens=in_tok,
                output_tokens=out_tok,
                input_per_million_usd=str(in_rate),
                output_per_million_usd=str(out_rate),
            )

        rep_cost_raw = entry.get("reported_cost_minor")
        if rep_cost_raw is not None:
            rep_cost: int | None = int(rep_cost_raw)
        elif entry.get("spend") is not None and entry_lane == "api_key":
            rep_cost = int((parse_decimal(str(entry["spend"])) * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        else:
            rep_cost = None

        quality_note = (
            f"Synced from LiteLLM gateway spend log ({execution_mode})."
            if entry_lane == "api_key"
            else f"Synced from LiteLLM gateway spend log via subscription lane ({execution_mode})."
        )
        rec_id = str(uuid.uuid4())
        db.execute(
            """
            INSERT INTO external_usage_records (
                id, connection_id, external_record_id, account_id, organization_id, membership_id,
                model_name, requests_count, input_tokens, output_tokens, total_tokens,
                reported_cost_minor, estimated_value_minor, currency, source, reliability,
                billing_lane, request_ref, quality_note, occurred_at, created_at
            )
            VALUES (%s, %s, %s, %s, NULL, NULL, %s, 1, %s, %s, %s, %s, %s, 'USD', 'GATEWAY_ROUTED', 'EXACT_IF_ROUTED', %s, %s, %s, %s, %s)
            ON CONFLICT (connection_id, external_record_id) DO NOTHING
            """,
            (
                rec_id,
                conn_id,
                ext_id,
                acc_id,
                entry_model,
                in_tok,
                out_tok,
                tot_tok,
                rep_cost,
                est_minor,
                entry_lane,
                req_ref_val,
                quality_note,
                occurred_at,
                now_iso,
            ),
        )
        new_records_synced += 1

    db.execute(
        "UPDATE integration_connections SET last_sync_success = %s, status = 'connected' WHERE id = %s",
        (now_iso, conn_id),
    )
    record_audit_log(
        db,
        actor_user_id=actor_user_id,
        tenant_id=acc_id,
        action="ai.credits_pinged",
        resource_type="integration_connection",
        resource_id=conn_id,
        metadata={"new_records_synced": new_records_synced, "execution_mode": execution_mode},
    )
    return {
        "new_records_synced": new_records_synced,
        "execution_mode": execution_mode,
        "last_ping_at": now_iso,
        "gateway_credits": gateway_credits,
    }


@router.post("/ai/authorize", status_code=200)
async def authorize_ai_tracking(
    payload: OneClickAuthorizeRequest | None = None,
    principal: dict = Depends(get_current_principal),
) -> dict[str, Any]:
    """1-click persistent authorization for app tracking + real-time AI token/credits metering."""
    from datetime import timedelta

    req_data = payload or OneClickAuthorizeRequest()
    domains = sorted({d.strip().lower() for d in req_data.allowlist if d.strip()}) or ["claude.ai", "chatgpt.com"]
    acc_id = principal["personal_account_id"]
    now = get_clock().now()
    now_iso = get_clock().now_iso()
    raw_code = f"PAIR-{generate_token(6).upper()}"
    expires_iso = (now + timedelta(minutes=15)).isoformat().replace("+00:00", "Z")

    with get_db(write=True) as db:
        _upsert_monitoring_source(db, acc_id=acc_id, source_type="browser_extension", enabled=True, allowlist=domains, now_iso=now_iso)
        _upsert_monitoring_source(db, acc_id=acc_id, source_type="desktop_helper", enabled=True, allowlist=["cursor", "code", "claude"], now_iso=now_iso)
        _upsert_monitoring_source(db, acc_id=acc_id, source_type="litellm", enabled=True, allowlist=domains, now_iso=now_iso)
        _ensure_one_click_integration(
            db,
            acc_id=acc_id,
            provider_code="claude_code_otel",
            display_name="Claude Code OTel Telemetry",
            credential_ref="otel_authorized",
            now_iso=now_iso,
        )

        # Ensure OTel tracking_connection exists
        existing_otel = db.fetchone(
            "SELECT id FROM tracking_connections WHERE account_id = %s AND source_type = 'claude_code_otel' AND revoked_at IS NULL LIMIT 1",
            (acc_id,),
        )
        if not existing_otel:
            db.execute(
                """
                INSERT INTO tracking_connections (
                    id, account_id, organization_id, source_type, status, device_name,
                    token_hash, permissions_json, allowlist_json, last_seen, created_at
                )
                VALUES (%s, %s, NULL, 'claude_code_otel', 'connected', 'one-click-local', %s, '["metrics_only"]', %s, %s, %s)
                """,
                (str(uuid.uuid4()), acc_id, hash_token(f"otel_live_{generate_token(16)}"), json.dumps(domains), now_iso, now_iso),
            )

        # Create pending pair code for Chromium MV3 auto-pairing too
        db.execute(
            """
            INSERT INTO tracking_connections (
                id, account_id, organization_id, source_type, status, device_name,
                pair_code_hash, pair_expires_at, permissions_json, allowlist_json, created_at
            )
            VALUES (%s, %s, NULL, 'browser_extension', 'pending_pair', 'chrome-desktop', %s, %s, '["tabs","storage","alarms","idle"]', %s, %s)
            """,
            (str(uuid.uuid4()), acc_id, hash_token(raw_code), expires_iso, json.dumps(domains), now_iso),
        )

        ping_meta = _ping_credits_internal(
            db,
            acc_id=acc_id,
            actor_user_id=principal["user"]["id"],
            now_iso=now_iso,
            model_name=req_data.model_name,
            billing_lane=req_data.billing_lane,
        )

    usage = await get_ai_usage_summary(
        organization_id=None,
        service_name=None,
        source=None,
        currency="USD",
        principal=principal,
    )
    return {
        "authorized": True,
        "allowlist": domains,
        "pair_code": raw_code,
        "expires_at": expires_iso,
        "ping": ping_meta,
        "usage": usage,
    }


@router.post("/ai/ping-credits", status_code=200)
async def ping_credits_and_tokens(
    payload: PingCreditsRequest | None = None,
    principal: dict = Depends(get_current_principal),
) -> dict[str, Any]:
    """Ping LiteLLM / credits meter and update token readings in real time."""
    req_data = payload or PingCreditsRequest()
    acc_id = principal["personal_account_id"]
    now_iso = get_clock().now_iso()

    with get_db(write=True) as db:
        auth_conn = db.fetchone(
            "SELECT id FROM integration_connections WHERE account_id = %s AND organization_id IS NULL AND status = 'connected' LIMIT 1",
            (acc_id,),
        )
        mon_conn = db.fetchone(
            "SELECT id FROM personal_monitoring_sources WHERE account_id = %s AND enabled = 1 LIMIT 1",
            (acc_id,),
        )
        if not auth_conn and not mon_conn:
            if not req_data.auto_authorize:
                raise DomainError(
                    403,
                    "AUTHORIZATION_REQUIRED",
                    "Grant 1-click AI tracking permission before pinging live credits.",
                )
            _upsert_monitoring_source(db, acc_id=acc_id, source_type="litellm", enabled=True, allowlist=["claude.ai", "chatgpt.com"], now_iso=now_iso)

        ping_meta = _ping_credits_internal(
            db,
            acc_id=acc_id,
            actor_user_id=principal["user"]["id"],
            now_iso=now_iso,
            model_name=req_data.model_name,
            billing_lane=req_data.billing_lane,
        )

    usage = await get_ai_usage_summary(
        organization_id=None,
        service_name=None,
        source=None,
        currency="USD",
        principal=principal,
    )
    return {
        "ping": ping_meta,
        **usage,
    }


@router.get("/ai/credits", status_code=200)
async def get_ai_credits(principal: dict = Depends(get_current_principal)) -> dict[str, Any]:
    usage = await get_ai_usage_summary(
        organization_id=None,
        service_name=None,
        source=None,
        currency="USD",
        principal=principal,
    )
    return {
        "authorized": usage["authorized"],
        "credits": usage["credits"],
        "tokens": usage["tokens"],
    }


@router.post("/ai/revoke", status_code=200)
def revoke_ai_authorization(principal: dict = Depends(get_current_principal)) -> dict[str, Any]:
    acc_id = principal["personal_account_id"]
    now_iso = get_clock().now_iso()
    with get_db(write=True) as db:
        db.execute(
            "UPDATE personal_monitoring_sources SET enabled = 0, updated_at = %s WHERE account_id = %s",
            (now_iso, acc_id),
        )
        db.execute(
            "UPDATE tracking_connections SET status = 'revoked', revoked_at = %s WHERE account_id = %s AND revoked_at IS NULL",
            (now_iso, acc_id),
        )
        db.execute(
            "UPDATE integration_connections SET status = 'revoked' WHERE account_id = %s AND organization_id IS NULL",
            (acc_id,),
        )
        record_audit_log(
            db,
            actor_user_id=principal["user"]["id"],
            tenant_id=acc_id,
            action="ai.authorization_revoked",
            resource_type="account",
            resource_id=acc_id,
        )
    return {"authorized": False, "revoked_at": now_iso}


# ── SubSphere MCP Server (Agent & Web Model Context Protocol Connector) ────────────────


class McpTokenRequest(BaseModel):
    client_name: str = "ai-agent"
    organization_id: Optional[str] = None


class McpReportUsageRequest(BaseModel):
    model_name: str = "claude-3-5-sonnet"
    agent_name: str = "ai-agent"
    input_tokens: Optional[int] = Field(default=None, ge=0)
    output_tokens: Optional[int] = Field(default=None, ge=0)
    total_tokens: Optional[int] = Field(default=None, ge=0)
    requests_count: int = Field(default=1, ge=1)
    billing_lane: str = "claude_subscription"
    reported_cost_minor: Optional[int] = Field(default=None, ge=0)
    session_id: Optional[str] = None
    request_ref: Optional[str] = None


_MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "subsphere_report_usage",
        "description": "Report AI token, request, model, and credit usage from an AI agent or web chat into SubSphere.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "model_name": {"type": "string", "description": "AI model identifier (e.g. claude-3-5-sonnet, gpt-4o)."},
                "agent_name": {"type": "string", "description": "Client or agent reporting the usage (e.g. claude-desktop, cursor, claude-code)."},
                "input_tokens": {"type": "integer", "minimum": 0, "description": "Prompt/input tokens used."},
                "output_tokens": {"type": "integer", "minimum": 0, "description": "Completion/output tokens used."},
                "total_tokens": {"type": "integer", "minimum": 0, "description": "Optional explicit total tokens."},
                "requests_count": {"type": "integer", "minimum": 1, "default": 1},
                "billing_lane": {"type": "string", "enum": ["claude_subscription", "api_key"], "default": "claude_subscription"},
                "reported_cost_minor": {"type": "integer", "minimum": 0, "description": "Direct billed cost in minor units (cents), if metered via API key."},
                "session_id": {"type": "string"},
                "request_ref": {"type": "string"},
            },
            "required": ["model_name"],
        },
    },
    {
        "name": "subsphere_get_usage_summary",
        "description": "Read the current user's SubSphere AI token totals, remaining monthly budget credits, and active subscription count.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "subsphere_list_subscriptions",
        "description": "List all active recurring subscriptions tracked in the user's SubSphere personal workspace.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "subsphere_add_subscription",
        "description": "Add a recurring AI or software subscription to the user's SubSphere workspace.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "service_name": {"type": "string"},
                "category": {"type": "string", "default": "AI"},
                "plan_name": {"type": "string", "default": "Pro"},
                "price_minor": {"type": "integer", "minimum": 0, "description": "Price in minor units (e.g. 2000 for $20.00)."},
                "currency": {"type": "string", "default": "USD"},
                "billing_interval": {"type": "string", "enum": ["monthly", "quarterly", "annual", "yearly", "weekly", "one_time"], "default": "monthly"},
                "next_renewal_date": {"type": "string", "description": "YYYY-MM-DD renewal date."},
                "service_url": {"type": "string", "default": ""},
            },
            "required": ["service_name", "price_minor", "next_renewal_date"],
        },
    },
    {
        "name": "subsphere_record_connector_event",
        "description": "Record a normalized Universal Usage Connector metadata event (document_read, source_code_read, document_generated, image_generated, image_uploaded, tool_call) without sending file contents.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "event_type": {
                    "type": "string",
                    "enum": [
                        "document_read",
                        "source_code_read",
                        "document_generated",
                        "image_generated",
                        "image_uploaded",
                        "tool_call",
                    ],
                },
                "application": {"type": "string", "default": "claude_code"},
                "file_type": {"type": "string"},
                "media_type": {"type": "string"},
                "quantity": {"type": "integer", "minimum": 1, "default": 1},
            },
            "required": ["event_type"],
        },
    },
]


def _resolve_public_base_url(request: Request) -> str:
    host = (request.headers.get("host") or "").strip()
    if not host or "testserver" in host or host == "backend:8000":
        return "http://127.0.0.1:8080"
    proto = (request.headers.get("x-forwarded-proto") or "http").split(",")[0].strip() or "http"
    return f"{proto}://{host}"


def _ensure_mcp_connection(
    db: Any,
    *,
    account_id: str,
    organization_id: Optional[str] = None,
    display_name: str = "SubSphere MCP Server",
) -> str:
    row = db.fetchone(
        """
        SELECT id FROM integration_connections
        WHERE account_id = %s AND provider_code = 'mcp_server'
          AND ((%s IS NULL AND organization_id IS NULL) OR organization_id = %s)
        ORDER BY created_at DESC LIMIT 1
        """,
        (account_id, organization_id, organization_id),
    )
    now_iso = get_clock().now_iso()
    if row:
        db.execute("UPDATE integration_connections SET status = 'connected' WHERE id = %s", (row["id"],))
        return row["id"]
    conn_id = str(uuid.uuid4())
    db.execute(
        """
        INSERT INTO integration_connections (
            id, account_id, organization_id, provider_code, display_name,
            credential_ref, privacy_scope, status, config_json, created_at
        )
        VALUES (%s, %s, %s, 'mcp_server', %s, 'mcp_installed', 'metadata_only', 'connected', '{"protocol":"mcp-2024-11-05"}', %s)
        """,
        (conn_id, account_id, organization_id, display_name, now_iso),
    )
    return conn_id


def _activate_mcp_connection_row(db: Any, tc: dict[str, Any]) -> tuple[str, Optional[str], str, Optional[str], str]:
    now_iso = get_clock().now_iso()
    db.execute("UPDATE tracking_connections SET last_seen = %s WHERE id = %s", (now_iso, tc["id"]))
    org_id = tc.get("organization_id")
    ic_id = _ensure_mcp_connection(db, account_id=tc["account_id"], organization_id=org_id)
    acc_row = db.fetchone("SELECT owner_user_id FROM accounts WHERE id = %s", (tc["account_id"],))
    actor_user_id = acc_row["owner_user_id"] if acc_row else None
    return tc["account_id"], org_id, ic_id, actor_user_id, tc.get("device_name") or "ai-agent"


def _resolve_mcp_target(request: Request, db: Any) -> tuple[str, Optional[str], str, Optional[str], str]:
    auth = request.headers.get("authorization", "")
    token = (
        auth[7:].strip()
        if auth.lower().startswith("bearer ")
        else (
            request.headers.get("x-subsphere-mcp-token", "").strip()
            or (request.query_params.get("token", "").strip() if hasattr(request, "query_params") else "")
            or (request.cookies.get("subsphere_session") or "").strip()
        )
    )
    if not token:
        active_tc = db.fetchone(
            """
            SELECT * FROM tracking_connections
            WHERE source_type = 'mcp_server' AND revoked_at IS NULL AND status != 'revoked'
            ORDER BY created_at DESC LIMIT 1
            """
        )
        if not active_tc:
            raise DomainError(401, "UNAUTHENTICATED", "Valid SubSphere MCP token or user session required.")
        return _activate_mcp_connection_row(db, active_tc)

    if token.startswith("mcp_live_"):
        t_hash = hash_token(token)
        tc = db.fetchone(
            "SELECT * FROM tracking_connections WHERE token_hash = %s AND source_type = 'mcp_server'",
            (t_hash,),
        )
        if not tc:
            raise DomainError(401, "INVALID_MCP_TOKEN", "Invalid SubSphere MCP token.")
        if tc.get("revoked_at") is not None or tc.get("status") == "revoked":
            raise DomainError(401, "MCP_TOKEN_REVOKED", "This SubSphere MCP token has been revoked.")
        return _activate_mcp_connection_row(db, tc)

    principal = get_session_principal(db, token)
    org_id = request.query_params.get("organization_id") if hasattr(request, "query_params") else None
    if org_id:
        require_org_role(db, principal, org_id, {"owner", "admin", "billing", "developer"})
        org = db.fetchone("SELECT account_id FROM organizations WHERE id = %s", (org_id,))
        acc_id = org["account_id"] if org else principal["personal_account_id"]
    else:
        acc_id = principal["personal_account_id"]
    ic_id = _ensure_mcp_connection(db, account_id=acc_id, organization_id=org_id)
    return acc_id, org_id, ic_id, principal["user"]["id"], "web-session"


def _ingest_mcp_usage(
    db: Any,
    *,
    account_id: str,
    organization_id: Optional[str],
    connection_id: str,
    actor_user_id: Optional[str],
    agent_name: str,
    model_name: str,
    input_tokens: Optional[int],
    output_tokens: Optional[int],
    total_tokens: Optional[int],
    requests_count: int = 1,
    billing_lane: str = "claude_subscription",
    reported_cost_minor: Optional[int] = None,
    session_id: Optional[str] = None,
    request_ref: Optional[str] = None,
) -> dict[str, Any]:
    now_iso = get_clock().now_iso()
    in_tok = int(input_tokens) if input_tokens is not None else None
    out_tok = int(output_tokens) if output_tokens is not None else None
    if total_tokens is not None:
        tot_tok: Optional[int] = int(total_tokens)
    elif in_tok is not None and out_tok is not None:
        tot_tok = in_tok + out_tok
    elif in_tok is not None or out_tok is not None:
        tot_tok = (in_tok or 0) + (out_tok or 0)
    else:
        tot_tok = None

    lane = billing_lane if billing_lane in ("api_key", "claude_subscription") else "claude_subscription"
    pricing = _resolve_pricing(db, provider_code="mcp_server", model_name=model_name, occurred_at=now_iso)
    est_minor: Optional[int] = None
    if in_tok is not None or out_tok is not None:
        in_rate = parse_decimal(pricing["input_per_million_usd"]) if pricing else Decimal("1.00")
        out_rate = parse_decimal(pricing["output_per_million_usd"]) if pricing else Decimal("4.00")
        est_minor, _ = compute_estimated_token_value_minor(
            input_tokens=in_tok,
            output_tokens=out_tok,
            input_per_million_usd=str(in_rate),
            output_per_million_usd=str(out_rate),
        )

    rep_cost = (
        int(reported_cost_minor)
        if reported_cost_minor is not None
        else (max(1, est_minor or 0) if lane == "api_key" and est_minor is not None else None)
    )
    req_ref_val = (request_ref or f"mcp-{uuid.uuid4().hex[:12]}").strip()
    ext_id = req_ref_val
    rec_id = str(uuid.uuid4())
    quality_note = f"Reported via SubSphere MCP ({agent_name}, lane={lane})."

    existing = db.fetchone(
        """
        SELECT id FROM external_usage_records
        WHERE connection_id = %s AND external_record_id = %s
        LIMIT 1
        """,
        (connection_id, ext_id),
    )
    inserted = existing is None
    if inserted:
        db.execute(
            """
            INSERT INTO external_usage_records (
                id, connection_id, external_record_id, account_id, organization_id, membership_id,
                model_name, requests_count, input_tokens, output_tokens, total_tokens,
                reported_cost_minor, estimated_value_minor, currency, source, reliability,
                billing_lane, request_ref, session_id, quality_note, occurred_at, created_at
            )
            VALUES (%s, %s, %s, %s, %s, NULL, %s, %s, %s, %s, %s, %s, %s, 'USD', 'MCP_SERVER', 'EXACT_AGENT_MCP', %s, %s, %s, %s, %s, %s)
            ON CONFLICT (connection_id, external_record_id) DO NOTHING
            """,
            (
                rec_id,
                connection_id,
                ext_id,
                account_id,
                organization_id,
                model_name,
                max(1, int(requests_count)),
                in_tok,
                out_tok,
                tot_tok,
                rep_cost,
                est_minor,
                lane,
                req_ref_val,
                session_id,
                quality_note,
                now_iso,
                now_iso,
            ),
        )

    db.execute(
        "UPDATE integration_connections SET last_sync_success = %s, status = 'connected' WHERE id = %s",
        (now_iso, connection_id),
    )
    db.execute(
        "UPDATE tracking_connections SET last_synced = %s WHERE account_id = %s AND source_type = 'mcp_server' AND revoked_at IS NULL",
        (now_iso, account_id),
    )
    if actor_user_id:
        record_audit_log(
            db,
            actor_user_id=actor_user_id,
            tenant_id=organization_id or account_id,
            action="mcp.usage_reported",
            resource_type="external_usage_record",
            resource_id=rec_id if inserted else existing["id"],
            metadata={
                "agent_name": agent_name,
                "model_name": model_name,
                "total_tokens": tot_tok,
                "inserted": inserted,
            },
        )
    return {
        "record_id": rec_id if inserted else existing["id"],
        "inserted": inserted,
        "source": "MCP_SERVER",
        "reliability": "EXACT_AGENT_MCP",
        "agent_name": agent_name,
        "model_name": model_name,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "total_tokens": tot_tok,
        "requests_count": max(1, int(requests_count)),
        "billing_lane": lane,
        "reported_cost_minor": rep_cost,
        "estimated_value_minor": est_minor,
        "occurred_at": now_iso,
    }


def _mcp_summary_payload(db: Any, *, account_id: str, organization_id: Optional[str]) -> dict[str, Any]:
    if organization_id:
        recs = db.fetchall(
            "SELECT * FROM external_usage_records WHERE organization_id = %s ORDER BY occurred_at DESC",
            (organization_id,),
        )
        sub_count_row = db.fetchone(
            "SELECT COUNT(*) AS cnt FROM external_subscriptions WHERE organization_id = %s AND status IN ('active', 'trial')",
            (organization_id,),
        )
        sub_fee_row = db.fetchone(
            "SELECT COALESCE(SUM(seats_purchased * unit_price_minor), 0) AS total FROM external_subscriptions WHERE organization_id = %s AND status IN ('active', 'trial')",
            (organization_id,),
        )
    else:
        recs = db.fetchall(
            "SELECT * FROM external_usage_records WHERE account_id = %s AND organization_id IS NULL ORDER BY occurred_at DESC",
            (account_id,),
        )
        sub_count_row = db.fetchone(
            "SELECT COUNT(*) AS cnt FROM personal_subscriptions WHERE account_id = %s AND status != 'cancelled_user_recorded'",
            (account_id,),
        )
        sub_fee_row = db.fetchone(
            "SELECT COALESCE(SUM(price_minor), 0) AS total FROM personal_subscriptions WHERE account_id = %s AND LOWER(category) = 'ai' AND status != 'cancelled_user_recorded'",
            (account_id,),
        )

    in_vals = [int(r["input_tokens"]) for r in recs if r.get("input_tokens") is not None]
    out_vals = [int(r["output_tokens"]) for r in recs if r.get("output_tokens") is not None]
    tot_vals = [int(r["total_tokens"]) for r in recs if r.get("total_tokens") is not None]
    rep_vals = [int(r["reported_cost_minor"]) for r in recs if r.get("reported_cost_minor") is not None]
    est_vals = [int(r["estimated_value_minor"]) for r in recs if r.get("estimated_value_minor") is not None]
    ai_fee = int(sub_fee_row["total"]) if sub_fee_row else 0
    limit_minor = ai_fee if ai_fee > 0 else 2000
    used_minor = sum(rep_vals) + sum(est_vals)
    return {
        "active_subscription_count": int(sub_count_row["cnt"]) if sub_count_row else 0,
        "ai_subscription_fee_minor": ai_fee,
        "tokens": {
            "input_tokens": sum(in_vals) if in_vals else None,
            "output_tokens": sum(out_vals) if out_vals else None,
            "total_tokens": sum(tot_vals) if tot_vals else None,
            "requests_count": sum(int(r["requests_count"] or 1) for r in recs) if recs else None,
        },
        "credits": {
            "monthly_limit_minor": limit_minor,
            "used_credits_minor": used_minor,
            "remaining_credits_minor": max(0, limit_minor - used_minor),
            "currency": "USD",
        },
        "record_count": len(recs),
    }


@router.post("/mcp/token", status_code=201)
def create_mcp_token(
    request: Request,
    payload: McpTokenRequest | None = None,
    principal: dict = Depends(get_current_principal),
) -> dict[str, Any]:
    """Generate a persistent SubSphere MCP Server token & install snippets for AI agents and web clients."""
    req_data = payload or McpTokenRequest()
    org_id = req_data.organization_id
    with get_db(write=True) as db:
        if org_id:
            require_org_role(db, principal, org_id, {"owner", "admin", "billing", "developer"})
            org = db.fetchone("SELECT account_id FROM organizations WHERE id = %s", (org_id,))
            if not org:
                raise DomainError(404, "ORG_NOT_FOUND", "Organization not found.")
            acc_id = org["account_id"]
        else:
            acc_id = principal["personal_account_id"]

        now_iso = get_clock().now_iso()
        raw_tok = f"mcp_live_{generate_token(24)}"
        tok_hash = hash_token(raw_tok)
        tc_id = str(uuid.uuid4())
        client_name = (req_data.client_name or "ai-agent").strip() or "ai-agent"

        ic_id = _ensure_mcp_connection(db, account_id=acc_id, organization_id=org_id)
        db.execute(
            """
            INSERT INTO tracking_connections (
                id, account_id, organization_id, source_type, status, device_name,
                token_hash, permissions_json, allowlist_json, last_seen, created_at
            )
            VALUES (%s, %s, %s, 'mcp_server', 'connected', %s, %s, '["mcp_tools","mcp_usage_report"]', '[]', %s, %s)
            """,
            (tc_id, acc_id, org_id, client_name, tok_hash, now_iso, now_iso),
        )
        record_audit_log(
            db,
            actor_user_id=principal["user"]["id"],
            tenant_id=org_id or acc_id,
            action="mcp.token_created",
            resource_type="tracking_connection",
            resource_id=tc_id,
            metadata={"client_name": client_name},
        )

    base_url = _resolve_public_base_url(request)
    http_mcp_url = f"{base_url}/api/v1/mcp?token={raw_tok}"
    script_download_url = f"/api/v1/mcp/install.py?token={raw_tok}"
    claude_desktop_config = {
        "mcpServers": {
            "subsphere": {
                "command": "python3",
                "args": ["subsphere_mcp_server.py"],
                "env": {
                    "SUBSPHERE_API_URL": base_url,
                    "SUBSPHERE_MCP_TOKEN": raw_tok,
                },
            }
        }
    }
    claude_code_command = (
        f'claude mcp add --transport http subsphere "{base_url}/api/v1/mcp" '
        f'--header "Authorization: Bearer {raw_tok}"'
    )
    return {
        "connection_id": tc_id,
        "integration_connection_id": ic_id,
        "client_name": client_name,
        "mcp_token": raw_tok,
        "http_mcp_url": http_mcp_url,
        "script_download_url": script_download_url,
        "claude_desktop_config": claude_desktop_config,
        "claude_code_command": claude_code_command,
        "tools": [t["name"] for t in _MCP_TOOLS],
    }


@router.get("/mcp/status", status_code=200)
def get_mcp_status(
    request: Request,
    organization_id: Optional[str] = Query(default=None),
    principal: dict = Depends(get_current_principal),
) -> dict[str, Any]:
    with get_db(write=False) as db:
        if organization_id:
            require_org_role(db, principal, organization_id, {"owner", "admin", "billing", "developer"})
            conns = db.fetchall(
                """
                SELECT id, status, device_name, last_seen, last_synced, revoked_at, created_at
                FROM tracking_connections
                WHERE organization_id = %s AND source_type = 'mcp_server'
                ORDER BY created_at DESC LIMIT 10
                """,
                (organization_id,),
            )
            recs = db.fetchall(
                """
                SELECT input_tokens, output_tokens, total_tokens, requests_count
                FROM external_usage_records
                WHERE organization_id = %s AND source = 'MCP_SERVER'
                """,
                (organization_id,),
            )
        else:
            acc_id = principal["personal_account_id"]
            conns = db.fetchall(
                """
                SELECT id, status, device_name, last_seen, last_synced, revoked_at, created_at
                FROM tracking_connections
                WHERE account_id = %s AND organization_id IS NULL AND source_type = 'mcp_server'
                ORDER BY created_at DESC LIMIT 10
                """,
                (acc_id,),
            )
            recs = db.fetchall(
                """
                SELECT input_tokens, output_tokens, total_tokens, requests_count
                FROM external_usage_records
                WHERE account_id = %s AND organization_id IS NULL AND source = 'MCP_SERVER'
                """,
                (acc_id,),
            )

    active = [c for c in conns if c["status"] == "connected" and c.get("revoked_at") is None]
    base_url = _resolve_public_base_url(request)
    in_vals = [int(r["input_tokens"]) for r in recs if r.get("input_tokens") is not None]
    out_vals = [int(r["output_tokens"]) for r in recs if r.get("output_tokens") is not None]
    tot_vals = [int(r["total_tokens"]) for r in recs if r.get("total_tokens") is not None]
    req_vals = [int(r["requests_count"]) for r in recs if r.get("requests_count") is not None]
    return {
        "connected": bool(active),
        "active_connection": active[0] if active else None,
        "connections_count": len(active),
        "total_tokens": sum(tot_vals) if tot_vals else 0,
        "input_tokens": sum(in_vals) if in_vals else 0,
        "output_tokens": sum(out_vals) if out_vals else 0,
        "requests_count": sum(req_vals) if req_vals else 0,
        "record_count": len(recs),
        "http_mcp_url": f"{base_url}/api/v1/mcp",
        "tools": [t["name"] for t in _MCP_TOOLS],
    }


@router.post("/mcp/revoke", status_code=200)
def revoke_mcp_connections(
    organization_id: Optional[str] = Query(default=None),
    principal: dict = Depends(get_current_principal),
) -> dict[str, Any]:
    now_iso = get_clock().now_iso()
    with get_db(write=True) as db:
        if organization_id:
            require_org_role(db, principal, organization_id, {"owner", "admin", "developer"})
            db.execute(
                "UPDATE tracking_connections SET status = 'revoked', revoked_at = %s WHERE organization_id = %s AND source_type = 'mcp_server' AND revoked_at IS NULL",
                (now_iso, organization_id),
            )
            db.execute(
                "UPDATE integration_connections SET status = 'revoked' WHERE organization_id = %s AND provider_code = 'mcp_server'",
                (organization_id,),
            )
            tenant = organization_id
        else:
            acc_id = principal["personal_account_id"]
            db.execute(
                "UPDATE tracking_connections SET status = 'revoked', revoked_at = %s WHERE account_id = %s AND organization_id IS NULL AND source_type = 'mcp_server' AND revoked_at IS NULL",
                (now_iso, acc_id),
            )
            db.execute(
                "UPDATE integration_connections SET status = 'revoked' WHERE account_id = %s AND organization_id IS NULL AND provider_code = 'mcp_server'",
                (acc_id,),
            )
            tenant = acc_id
        record_audit_log(
            db,
            actor_user_id=principal["user"]["id"],
            tenant_id=tenant,
            action="mcp.tokens_revoked",
            resource_type="tracking_connection",
            resource_id=tenant,
        )
    return {"connected": False, "revoked_at": now_iso}


@router.get("/mcp/install.py", status_code=200)
def download_mcp_install_script(
    request: Request,
    token: str = Query(default=""),
) -> Response:
    base_url = _resolve_public_base_url(request)
    script_text = render_mcp_server_script(token=token.strip(), api_url=base_url)
    return Response(
        content=script_text.encode("utf-8"),
        status_code=200,
        headers={"Content-Disposition": 'attachment; filename="subsphere_mcp_server.py"'},
        media_type="text/x-python",
    )


@router.post("/mcp/report-usage", status_code=200)
async def report_mcp_usage_http(
    request: Request,
    payload: McpReportUsageRequest,
) -> dict[str, Any]:
    with get_db(write=True) as db:
        acc_id, org_id, ic_id, actor_user_id, device_name = _resolve_mcp_target(request, db)
        rec = _ingest_mcp_usage(
            db,
            account_id=acc_id,
            organization_id=org_id,
            connection_id=ic_id,
            actor_user_id=actor_user_id,
            agent_name=payload.agent_name or device_name,
            model_name=payload.model_name,
            input_tokens=payload.input_tokens,
            output_tokens=payload.output_tokens,
            total_tokens=payload.total_tokens,
            requests_count=payload.requests_count,
            billing_lane=payload.billing_lane,
            reported_cost_minor=payload.reported_cost_minor,
            session_id=payload.session_id,
            request_ref=payload.request_ref,
        )
        summary = _mcp_summary_payload(db, account_id=acc_id, organization_id=org_id)
    return {"accepted": True, "record": rec, "summary": summary}


@router.get("/mcp", status_code=200)
def discover_mcp_server(request: Request) -> dict[str, Any]:
    """Model Context Protocol (MCP) Server discovery metadata."""
    base_url = _resolve_public_base_url(request)
    return {
        "name": "subsphere-mcp",
        "version": "4.1.0",
        "protocolVersion": "2024-11-05",
        "status": "ready",
        "transport": ["streamable-http", "stdio-bridge"],
        "endpoint": f"{base_url}/api/v1/mcp",
        "tools": _MCP_TOOLS,
    }


def _mcp_tool_call(
    db: Any,
    *,
    name: str,
    arguments: dict[str, Any],
    account_id: str,
    organization_id: Optional[str],
    connection_id: str,
    actor_user_id: Optional[str],
    device_name: str,
) -> tuple[dict[str, Any], bool]:
    if name == "subsphere_report_usage":
        model_name = str(arguments.get("model_name") or arguments.get("model") or "claude-3-5-sonnet")
        agent_name = str(arguments.get("agent_name") or arguments.get("agent") or device_name or "ai-agent")
        in_tok = arguments.get("input_tokens") if arguments.get("input_tokens") is not None else arguments.get("prompt_tokens")
        out_tok = arguments.get("output_tokens") if arguments.get("output_tokens") is not None else arguments.get("completion_tokens")
        tot_tok = arguments.get("total_tokens")
        rep_cost = arguments.get("reported_cost_minor") if arguments.get("reported_cost_minor") is not None else arguments.get("cost_minor")
        rec = _ingest_mcp_usage(
            db,
            account_id=account_id,
            organization_id=organization_id,
            connection_id=connection_id,
            actor_user_id=actor_user_id,
            agent_name=agent_name,
            model_name=model_name,
            input_tokens=int(in_tok) if in_tok is not None else None,
            output_tokens=int(out_tok) if out_tok is not None else None,
            total_tokens=int(tot_tok) if tot_tok is not None else None,
            requests_count=int(arguments.get("requests_count") or 1),
            billing_lane=str(arguments.get("billing_lane") or "claude_subscription"),
            reported_cost_minor=int(rep_cost) if rep_cost is not None else None,
            session_id=arguments.get("session_id"),
            request_ref=arguments.get("request_ref"),
        )
        summary = _mcp_summary_payload(db, account_id=account_id, organization_id=organization_id)
        return {"recorded": rec, "usage_summary": summary}, False

    if name == "subsphere_get_usage_summary":
        return _mcp_summary_payload(db, account_id=account_id, organization_id=organization_id), False

    if name == "subsphere_list_subscriptions":
        rows = db.fetchall(
            "SELECT * FROM personal_subscriptions WHERE account_id = %s ORDER BY next_renewal_date ASC",
            (account_id,),
        )
        return {"subscriptions": [serialize_personal_subscription(r) for r in rows]}, False

    if name == "subsphere_add_subscription":
        svc_name = str(arguments.get("service_name") or "").strip()
        next_due = str(arguments.get("next_renewal_date") or "").strip()
        if not svc_name or not next_due or arguments.get("price_minor") is None:
            return {"error": "service_name, price_minor, and next_renewal_date are required."}, True
        created = create_personal_subscription(
            db,
            account_id=account_id,
            user_id=actor_user_id or "",
            data={
                "service_name": svc_name,
                "service_url": str(arguments.get("service_url") or ""),
                "category": str(arguments.get("category") or "AI"),
                "plan_name": str(arguments.get("plan_name") or "Pro"),
                "price_minor": int(arguments["price_minor"]),
                "currency": str(arguments.get("currency") or "USD").upper(),
                "billing_interval": str(arguments.get("billing_interval") or "monthly"),
                "next_renewal_date": next_due,
                "monitoring_source": "mcp_server",
            },
        )
        return {"subscription": created}, False

    if name == "subsphere_record_connector_event":
        ev_arg = dict(arguments)
        if not ev_arg.get("source"):
            ev_arg["source"] = "agent_tool_hook"
        if not ev_arg.get("application"):
            ev_arg["application"] = device_name or "claude_code"
        stored = ingest_universal_events(
            db,
            account_id=account_id,
            organization_id=organization_id,
            actor_user_id=actor_user_id,
            events=[ev_arg],
        )
        intelligence = build_usage_intelligence(
            db,
            account_id=account_id,
            organization_id=organization_id,
        )
        return {"recorded_events": stored, "intelligence": intelligence}, False

    return {"error": f"Unknown MCP tool: {name}"}, True


@router.post("/mcp", status_code=200)
async def handle_mcp_jsonrpc(request: Request) -> Response:
    """JSON-RPC 2.0 Model Context Protocol endpoint for AI agents and web clients."""
    with get_db(write=True) as db:
        acc_id, org_id, ic_id, actor_user_id, device_name = _resolve_mcp_target(request, db)
        raw_body = await request.body()
        if not raw_body:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Empty JSON-RPC payload"}},
                status_code=400,
            )
        try:
            rpc = json.loads(raw_body.decode("utf-8"))
        except Exception as exc:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"Invalid JSON: {exc}"}},
                status_code=400,
            )
        if not isinstance(rpc, dict):
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid JSON-RPC request object"}},
                status_code=400,
            )

        rpc_id = rpc.get("id")
        is_notification = "id" not in rpc or rpc_id is None
        method = str(rpc.get("method") or "")
        params = rpc.get("params") if isinstance(rpc.get("params"), dict) else {}

        if method == "initialize":
            result: Any = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}, "resources": {"listChanged": False}},
                "serverInfo": {"name": "subsphere-mcp", "version": "4.1.0"},
            }
        elif method in ("notifications/initialized", "ping"):
            if is_notification and method == "notifications/initialized":
                return Response(content=b"", status_code=202)
            result = {}
        elif method == "tools/list":
            result = {"tools": _MCP_TOOLS}
        elif method == "tools/call":
            tool_name = str(params.get("name") or "")
            tool_args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            payload_out, is_error = _mcp_tool_call(
                db,
                name=tool_name,
                arguments=tool_args,
                account_id=acc_id,
                organization_id=org_id,
                connection_id=ic_id,
                actor_user_id=actor_user_id,
                device_name=device_name,
            )
            result = {
                "content": [{"type": "text", "text": json.dumps(payload_out, default=str)}],
                "isError": is_error,
            }
        elif method == "resources/list":
            result = {
                "resources": [
                    {
                        "uri": "subsphere://usage/summary",
                        "name": "SubSphere AI Token & Credit Summary",
                        "mimeType": "application/json",
                    },
                    {
                        "uri": "subsphere://subscriptions/list",
                        "name": "SubSphere Tracked Subscriptions",
                        "mimeType": "application/json",
                    },
                ]
            }
        elif method == "resources/read":
            uri = str(params.get("uri") or "")
            if uri == "subsphere://usage/summary":
                res_data = _mcp_summary_payload(db, account_id=acc_id, organization_id=org_id)
            elif uri == "subsphere://subscriptions/list":
                rows = db.fetchall(
                    "SELECT * FROM personal_subscriptions WHERE account_id = %s ORDER BY next_renewal_date ASC",
                    (acc_id,),
                )
                res_data = {"subscriptions": [serialize_personal_subscription(r) for r in rows]}
            else:
                return JSONResponse(
                    {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32602, "message": f"Unknown resource URI: {uri}"}},
                    status_code=200,
                )
            result = {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(res_data, default=str)}]}
        else:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32601, "message": f"Method not found: {method}"}},
                status_code=200,
            )

    mcp_headers = {"Mcp-Session-Id": "subsphere-mcp-session"}
    if is_notification:
        return Response(content=b"", status_code=202, headers=mcp_headers)
    return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": result}, status_code=200, headers=mcp_headers)


# ── SubSphere Universal Usage Connector & AI Usage Intelligence ────────────────────────


class ConnectorConnectRequest(BaseModel):
    application: str = Field(min_length=2, max_length=64)
    plan_tier: str = "personal"
    api_key: Optional[str] = None
    allowlist: Optional[list[str]] = None
    organization_id: Optional[str] = None


def _resolve_connector_actor(request: Request, db: Any) -> tuple[str, Optional[str], Optional[str]]:
    auth = request.headers.get("authorization", "")
    token = (
        auth[7:].strip()
        if auth.lower().startswith("bearer ")
        else (
            request.headers.get("x-subsphere-mcp-token", "").strip()
            or (request.cookies.get("subsphere_session") or "").strip()
        )
    )
    if not token:
        raise DomainError(401, "UNAUTHENTICATED", "Authentication or connector token required.")

    if token.startswith(("mcp_live_", "otel_live_")):
        t_hash = hash_token(token)
        tc = db.fetchone(
            "SELECT * FROM tracking_connections WHERE token_hash = %s",
            (t_hash,),
        )
        if not tc:
            raise DomainError(401, "INVALID_CONNECTOR_TOKEN", "Invalid SubSphere connector token.")
        if tc.get("revoked_at") is not None or tc.get("status") == "revoked":
            raise DomainError(401, "CONNECTOR_TOKEN_REVOKED", "This SubSphere connector token has been revoked.")
        acc_row = db.fetchone("SELECT owner_user_id FROM accounts WHERE id = %s", (tc["account_id"],))
        actor_id = acc_row["owner_user_id"] if acc_row else None
        return tc["account_id"], tc.get("organization_id"), actor_id

    principal = get_session_principal(db, token)
    return principal["personal_account_id"], None, principal["user"]["id"]


@router.get("/connectors/catalog", status_code=200)
def get_universal_connector_catalog() -> dict[str, Any]:
    return {
        "tracking_methods": TRACKING_METHODS_CATALOG,
        "applications": list(APPLICATION_CONNECTOR_MATRIX.values()),
        "five_measurements_spec": FIVE_MEASUREMENTS_SPEC,
    }


@router.post("/connectors/connect", status_code=201)
def connect_universal_application(
    request: Request,
    payload: ConnectorConnectRequest,
    principal: dict = Depends(get_current_principal),
) -> dict[str, Any]:
    now_iso = get_clock().now_iso()
    base_url = _resolve_public_base_url(request)
    sel = select_best_connector(
        payload.application,
        plan_tier=payload.plan_tier,
        has_admin_api_key=bool(payload.api_key and payload.api_key.strip()),
    )
    app_slug = sel["application"]
    method = sel["selected_method"]

    with get_db(write=True) as db:
        if payload.organization_id:
            require_org_role(db, principal, payload.organization_id, {"owner", "admin", "billing", "developer"})
            org = db.fetchone("SELECT account_id FROM organizations WHERE id = %s", (payload.organization_id,))
            if not org:
                raise DomainError(404, "ORG_NOT_FOUND", "Organization not found.")
            acc_id = org["account_id"]
            org_id: Optional[str] = payload.organization_id
        else:
            acc_id = principal["personal_account_id"]
            org_id = None

        conn_id = ensure_connector_connection(
            db,
            account_id=acc_id,
            organization_id=org_id,
            provider_code=sel["provider_code"],
            display_name=sel["display_name"],
            credential_secret=payload.api_key,
            config_obj={
                "application": app_slug,
                "selected_method": method,
                "plan_tier": payload.plan_tier,
                "coverage": sel["coverage"],
            },
        )

        config_out: dict[str, Any] = {
            "application": app_slug,
            "ingest_events_url": f"{base_url}/api/v1/connectors/events",
            "protocol": method,
        }

        if method == "opentelemetry":
            raw_tok = f"otel_live_{generate_token(20)}"
            tc_id = str(uuid.uuid4())
            db.execute(
                """
                INSERT INTO tracking_connections (
                    id, account_id, organization_id, source_type, status, device_name,
                    token_hash, permissions_json, allowlist_json, last_seen, created_at
                )
                VALUES (%s, %s, %s, 'claude_code_otel', 'connected', %s, %s, '["metrics_only","tool_hooks"]', '[]', %s, %s)
                """,
                (tc_id, acc_id, org_id, app_slug, hash_token(raw_tok), now_iso, now_iso),
            )
            export_env = "\n".join(
                [
                    "export CLAUDE_CODE_ENABLE_TELEMETRY=1",
                    "export OTEL_METRICS_EXPORTER=otlp",
                    "export OTEL_EXPORTER_OTLP_PROTOCOL=http/json",
                    f"export OTEL_EXPORTER_OTLP_ENDPOINT={base_url.replace(':8080', ':4317')}",
                    f'export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Bearer {raw_tok}"',
                ]
            )
            config_out.update(
                {
                    "otel_token": raw_tok,
                    "collector_endpoint": f"{base_url.replace(':8080', ':4317')}/v1/metrics",
                    "helper_script_url": f"/api/v1/connectors/claude-code-helper.sh?token={raw_tok}",
                    "export_env_block": export_env,
                    "litellm_required": False,
                }
            )
        elif method == "browser_extension":
            default_domains = (
                payload.allowlist
                if payload.allowlist
                else (
                    ["huggingface.co"]
                    if app_slug == "huggingchat"
                    else (["chatgpt.com"] if app_slug == "chatgpt_web" else ["claude.ai"])
                )
            )
            allow_json = json.dumps(default_domains)
            pair_code = generate_token(8)[:6].upper()
            if not org_id:
                existing_mon = db.fetchone(
                    "SELECT id FROM personal_monitoring_sources WHERE account_id = %s AND source_type = 'browser_extension'",
                    (acc_id,),
                )
                if existing_mon:
                    db.execute(
                        "UPDATE personal_monitoring_sources SET enabled = 1, allowlist_json = %s, updated_at = %s WHERE id = %s",
                        (allow_json, now_iso, existing_mon["id"]),
                    )
                else:
                    db.execute(
                        """
                        INSERT INTO personal_monitoring_sources (id, account_id, source_type, enabled, allowlist_json, updated_at)
                        VALUES (%s, %s, 'browser_extension', 1, %s, %s)
                        """,
                        (str(uuid.uuid4()), acc_id, allow_json, now_iso),
                    )
            db.execute(
                """
                INSERT INTO tracking_connections (
                    id, account_id, organization_id, source_type, status, device_name,
                    pair_code_hash, pair_expires_at, permissions_json, allowlist_json, last_seen, created_at
                )
                VALUES (%s, %s, %s, 'browser_extension', 'pending_pair', %s, %s, '2099-12-31T23:59:59Z', '["observed_interactions","active_time"]', %s, %s, %s)
                """,
                (str(uuid.uuid4()), acc_id, org_id, app_slug, hash_token(pair_code), allow_json, now_iso, now_iso),
            )
            config_out.update(
                {
                    "allowlist": default_domains,
                    "pair_code": pair_code,
                    "extension_dir": "/home/abdul-ahad/Documents/SubSphere_Complete_Project_Package/SubSphere_Project/services/browser-extension",
                    "tokens_status": "Unavailable (browser cannot determine server-side token usage)",
                }
            )
        else:
            raw_tok = f"mcp_live_{generate_token(20)}"
            tc_id = str(uuid.uuid4())
            db.execute(
                """
                INSERT INTO tracking_connections (
                    id, account_id, organization_id, source_type, status, device_name,
                    token_hash, permissions_json, allowlist_json, last_seen, created_at
                )
                VALUES (%s, %s, %s, %s, 'connected', %s, %s, '["connector_events"]', '[]', %s, %s)
                """,
                (tc_id, acc_id, org_id, sel["provider_code"], app_slug, hash_token(raw_tok), now_iso, now_iso),
            )
            config_out.update(
                {
                    "connector_token": raw_tok,
                    "masked_credential": mask_credential(payload.api_key or "") if payload.api_key else None,
                }
            )

        record_audit_log(
            db,
            actor_user_id=principal["user"]["id"],
            tenant_id=org_id or acc_id,
            action="universal_connector.connected",
            resource_type="integration_connection",
            resource_id=conn_id,
            metadata={"application": app_slug, "selected_method": method, "plan_tier": payload.plan_tier},
        )

    return {
        "application": app_slug,
        "display_name": sel["display_name"],
        "selected_method": method,
        "source": sel["source"],
        "source_badge": sel["source_badge"],
        "fallback_reason": sel["fallback_reason"],
        "connection_id": conn_id,
        "status": "connected",
        "config": config_out,
        "coverage": sel["coverage"],
    }


@router.get("/connectors/claude-code-helper.sh", status_code=200)
def download_claude_code_helper_script(
    request: Request,
    token: str = Query(default=""),
) -> Response:
    base_url = _resolve_public_base_url(request)
    script_text = render_claude_code_helper_script(otel_token=token.strip(), api_url=base_url)
    return Response(
        content=script_text.encode("utf-8"),
        status_code=200,
        headers={"Content-Disposition": 'attachment; filename="claude_code_connect.sh"'},
        media_type="text/x-shellscript",
    )


@router.post("/connectors/events", status_code=201)
async def ingest_universal_connector_events(request: Request) -> dict[str, Any]:
    raw_body = await request.body()
    if not raw_body:
        raise DomainError(400, "EMPTY_BODY", "Connector event JSON body is required.")
    try:
        parsed = json.loads(raw_body.decode("utf-8"))
    except Exception as exc:
        raise DomainError(400, "INVALID_JSON", f"Invalid JSON payload: {exc}") from exc

    if isinstance(parsed, dict) and isinstance(parsed.get("events"), list):
        events_list = [e for e in parsed["events"] if isinstance(e, dict)]
        requested_org_id = parsed.get("organization_id")
    elif isinstance(parsed, dict):
        events_list = [parsed]
        requested_org_id = parsed.get("organization_id")
    elif isinstance(parsed, list):
        events_list = [e for e in parsed if isinstance(e, dict)]
        requested_org_id = None
    else:
        raise DomainError(400, "INVALID_PAYLOAD", "Expected event object or {'events': [...]}.")

    if not events_list:
        raise DomainError(400, "NO_EVENTS", "At least one event is required.")

    with get_db(write=True) as db:
        acc_id, org_id, actor_user_id = _resolve_connector_actor(request, db)
        if requested_org_id and requested_org_id != org_id:
            if not actor_user_id:
                raise DomainError(403, "FORBIDDEN", "Token is scoped to a different tenant.")
            auth = request.headers.get("authorization", "")
            tok = auth[7:].strip() if auth.lower().startswith("bearer ") else (request.cookies.get("subsphere_session") or "").strip()
            principal = get_session_principal(db, tok)
            require_org_role(db, principal, str(requested_org_id), {"owner", "admin", "billing", "developer"})
            org_row = db.fetchone("SELECT account_id FROM organizations WHERE id = %s", (str(requested_org_id),))
            if not org_row:
                raise DomainError(404, "ORG_NOT_FOUND", "Organization not found.")
            acc_id = org_row["account_id"]
            org_id = str(requested_org_id)

        stored = ingest_universal_events(
            db,
            account_id=acc_id,
            organization_id=org_id,
            actor_user_id=actor_user_id,
            events=events_list,
        )
        intelligence = build_usage_intelligence(
            db,
            account_id=None if org_id else acc_id,
            organization_id=org_id,
        )

    return {
        "accepted_count": len(stored),
        "events": stored,
        "intelligence": intelligence,
    }


@router.get("/connectors/intelligence", status_code=200)
def get_universal_usage_intelligence(
    organization_id: Optional[str] = Query(default=None),
    currency: str = Query(default="USD"),
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
            acc_id = None
        else:
            acc_id = principal["personal_account_id"]
        return build_usage_intelligence(
            db,
            account_id=acc_id,
            organization_id=organization_id,
            currency=currency.upper(),
        )

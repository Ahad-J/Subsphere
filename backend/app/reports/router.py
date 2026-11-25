"""Report generation, immutable snapshots, private download tokens, and delivery."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel

from app.audit.service import record_audit_log
from app.auth.dependencies import get_current_principal, require_org_role
from app.auth.service import get_session_principal
from app.core.clock import get_clock
from app.core.errors import DomainError
from app.core.security import generate_token, hash_token
from app.db.engine import DBConnection, get_db
from app.notifications.dispatcher import dispatch_email_jobs
from app.personal.domain import project_occurrences_in_range
from app.personal.service import compute_personal_dashboard
from app.reports.renderer import render_report_html, render_report_pdf
from app.reports.storage import PrivateObjectStore

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])
history_router = APIRouter(prefix="/api/v1", tags=["reports"])

_BAR_COLORS = ["#2563eb", "#0891b2", "#7c3aed", "#059669", "#d97706", "#dc2626"]


class ReportCreate(BaseModel):
    report_type: str = "statement"
    kind: str | None = None
    view: str = "individual"
    organization_id: str | None = None
    invoice_id: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    from_date: str | None = None
    to_date: str | None = None
    filters: dict[str, Any] | None = None
    custom_html_test: str | None = None


class ShareLinkCreate(BaseModel):
    expires_in_seconds: int = 900


def _resolve_period(from_str: str | None, to_str: str | None) -> tuple[str, str]:
    today = get_clock().now().date()
    try:
        end_d = date.fromisoformat(to_str) if to_str else today
        start_d = date.fromisoformat(from_str) if from_str else (end_d - timedelta(days=30))
    except ValueError as exc:
        raise DomainError(422, "INVALID_DATE", "Period dates must be ISO YYYY-MM-DD format.") from exc
    if end_d < start_d:
        raise DomainError(422, "INVALID_DATE_RANGE", "End date must be on or after start date.")
    return start_d.isoformat(), end_d.isoformat()


def _deduplicate_ai_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate overlapping OTel + LiteLLM records sharing request_ref."""
    seen_refs: dict[str, dict[str, Any]] = {}
    passthrough: list[dict[str, Any]] = []
    for r in rows:
        req_ref = (r.get("request_ref") or "").strip()
        if not req_ref:
            passthrough.append(r)
            continue
        if req_ref not in seen_refs:
            seen_refs[req_ref] = r
        else:
            # Prefer gateway if it has reported cost, or retain first telemetry
            existing = seen_refs[req_ref]
            if existing.get("reported_cost_minor") is None and r.get("reported_cost_minor") is not None:
                seen_refs[req_ref] = r
    merged = list(seen_refs.values()) + passthrough
    merged.sort(key=lambda x: str(x.get("occurred_at") or ""), reverse=True)
    return merged


def build_history_dataset(
    db: DBConnection,
    *,
    principal: dict[str, Any],
    view: str = "individual",
    period_start: str | None = None,
    period_end: str | None = None,
    organization_id: str | None = None,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if view not in ("individual", "provider"):
        raise DomainError(422, "INVALID_VIEW", "view must be 'individual' or 'provider'.")
    p_start, p_end = _resolve_period(period_start, period_end)
    flt = filters or {}
    now_iso = get_clock().now_iso()

    if view == "provider":
        if not organization_id:
            raise DomainError(422, "ORGANIZATION_REQUIRED", "organization_id is required for provider history view.")
        require_org_role(db, principal, organization_id, {"owner", "admin", "billing", "staff"})
        org = db.fetchone("SELECT id, name, default_currency FROM organizations WHERE id = %s", (organization_id,))
        if not org:
            raise DomainError(404, "ORG_NOT_FOUND", "Organization not found.")

        invs = db.fetchall(
            """
            SELECT * FROM invoices
            WHERE organization_id = %s AND substr(issued_at, 1, 10) >= %s AND substr(issued_at, 1, 10) <= %s
            ORDER BY issued_at DESC
            """,
            (organization_id, p_start, p_end),
        )
        pays = db.fetchall(
            """
            SELECT * FROM payments
            WHERE organization_id = %s AND substr(paid_at, 1, 10) >= %s AND substr(paid_at, 1, 10) <= %s
            ORDER BY paid_at DESC
            """,
            (organization_id, p_start, p_end),
        )
        subs_org = db.fetchall(
            """
            SELECT sc.name as service_name, sc.plan_name, 'monthly' as billing_interval,
                   COALESCE(sc.fee_minor, 0) as price_minor, sc.currency,
                   COALESCE(sc.next_due_date, '') as next_renewal_date, sc.status as tracking_status,
                   'Subscriber' as category
            FROM subscriber_contacts sc
            WHERE sc.organization_id = %s
            ORDER BY sc.created_at DESC
            """,
            (organization_id,),
        )
        ext_ai = _deduplicate_ai_records(
            db.fetchall(
                """
                SELECT * FROM external_usage_records
                WHERE organization_id = %s AND substr(occurred_at, 1, 10) >= %s AND substr(occurred_at, 1, 10) <= %s
                ORDER BY occurred_at DESC
                """,
                (organization_id, p_start, p_end),
            )
        )
        notif_rows = db.fetchall(
            """
            SELECT recipient, subject, status, updated_at FROM notification_jobs
            WHERE organization_id = %s AND substr(created_at, 1, 10) >= %s AND substr(created_at, 1, 10) <= %s
            ORDER BY created_at DESC LIMIT 50
            """,
            (organization_id, p_start, p_end),
        )
        collected_minor = sum((p["amount_minor"] or 0) for p in pays if p["status"] == "confirmed")
        cur_totals = [
            {
                "currency": org["default_currency"],
                "monthly_equivalent_minor": collected_minor,
                "annual_equivalent_minor": collected_minor * 12,
                "one_time_spend_minor": 0,
                "subscription_count": len(subs_org),
            }
        ]
        total_tok = sum((r["total_tokens"] or 0) for r in ext_ai if r["total_tokens"] is not None)
        has_tok = any(r["total_tokens"] is not None for r in ext_ai)
        reported_cost = sum((r["reported_cost_minor"] or 0) for r in ext_ai if r["reported_cost_minor"] is not None)
        ai_display = [
            {
                "source": r["source"],
                "model_name": r.get("model_name") or "-",
                "billing_lane": r.get("billing_lane") or "standard",
                "total_tokens": r.get("total_tokens"),
                "reported_cost_display": f"{r['currency']} {r['reported_cost_minor']/100:,.2f}"
                if r.get("reported_cost_minor") is not None
                else "Not billed per token",
                "estimated_value_display": f"{r['currency']} {r['estimated_value_minor']/100:,.2f}"
                if r.get("estimated_value_minor") is not None
                else "-",
            }
            for r in ext_ai
        ]
        return {
            "kind": "history_report",
            "view": "provider",
            "organization_id": organization_id,
            "org_name": org["name"],
            "period_start": p_start,
            "period_end": p_end,
            "period": {"from": p_start, "to": p_end},
            "issued_at": now_iso,
            "currency_totals": cur_totals,
            "category_bars": [],
            "subscriptions": subs_org,
            "invoices": invs,
            "payments": pays,
            "observed_activity_summary": {"total_active_seconds": 0, "total_active_minutes": 0, "by_domain": [], "daily_rows": []},
            "ai_usage_summary": {
                "total_tokens": total_tok if has_tok else None,
                "reported_api_cost_minor": reported_cost,
                "records_display": ai_display,
                "records": ext_ai,
            },
            "notifications_sent": notif_rows,
            "provenance_notes": [
                "Provider invoices and confirmed payments come strictly from the authoritative organization ledger.",
                "Personal user subscriptions and private browser/desktop activity are excluded from organization reports.",
                "Employee AI telemetry only reflects organization-authorized connections.",
            ],
        }

    # Individual view history
    acc_id = principal["personal_account_id"]
    dash = compute_personal_dashboard(db, acc_id)
    subs = db.fetchall("SELECT * FROM personal_subscriptions WHERE account_id = %s ORDER BY next_renewal_date", (acc_id,))
    cat_filter = (flt.get("category") or "").strip().lower()
    if cat_filter:
        subs = [s for s in subs if (s.get("category") or "").lower() == cat_filter]

    start_d = date.fromisoformat(p_start)
    end_d = date.fromisoformat(p_end) + timedelta(days=1)
    renewal_events = []
    for s in subs:
        if s.get("product_type") == "one_time_purchase":
            continue
        first_ren = date.fromisoformat(s["next_renewal_date"])
        occs = project_occurrences_in_range(
            first_ren,
            s["billing_interval"],
            int(s.get("anchor_day") or first_ren.day),
            start_d,
            end_d,
        )
        for d in occs:
            renewal_events.append(
                {
                    "date": d.isoformat(),
                    "subscription_id": s["id"],
                    "service_name": s["service_name"],
                    "price_minor": s["price_minor"],
                    "currency": s["currency"],
                    "tracking_status": s.get("tracking_status") or "tracking_active",
                }
            )

    daily_rows = db.fetchall(
        """
        SELECT * FROM activity_daily
        WHERE account_id = %s AND day >= %s AND day <= %s
        ORDER BY day DESC, domain_or_app
        """,
        (acc_id, p_start, p_end),
    )
    pa_rows = db.fetchall(
        """
        SELECT * FROM personal_activity
        WHERE account_id = %s AND substr(occurred_at, 1, 10) >= %s AND substr(occurred_at, 1, 10) <= %s
        ORDER BY occurred_at DESC
        """,
        (acc_id, p_start, p_end),
    )
    domain_agg: dict[str, dict[str, Any]] = {}
    if daily_rows:
        for dr in daily_rows:
            k = f"{dr['domain_or_app']}|ALLOWED_BROWSER_ACTIVITY"
            if k not in domain_agg:
                domain_agg[k] = {
                    "domain_or_app": dr["domain_or_app"],
                    "source": "ALLOWED_BROWSER_ACTIVITY",
                    "active_seconds": 0,
                    "visits": 0,
                    "reliability": "OBSERVED",
                }
            domain_agg[k]["active_seconds"] += int(dr.get("active_seconds") or 0)
            domain_agg[k]["visits"] += int(dr.get("visits") or 0)
    else:
        for pr in pa_rows:
            k = f"{pr['domain_or_app']}|{pr['source']}"
            if k not in domain_agg:
                domain_agg[k] = {
                    "domain_or_app": pr["domain_or_app"],
                    "source": pr["source"],
                    "active_seconds": 0,
                    "visits": 0,
                    "reliability": pr.get("reliability") or "OBSERVED",
                }
            domain_agg[k]["active_seconds"] += int(pr.get("observed_active_seconds") or 0)
            domain_agg[k]["visits"] += int(pr.get("visit_count") or 1)

    by_domain = []
    total_obs_sec = 0
    for item in domain_agg.values():
        total_obs_sec += item["active_seconds"]
        by_domain.append({**item, "active_minutes": item["active_seconds"] // 60})
    by_domain.sort(key=lambda x: x["active_seconds"], reverse=True)

    ext_ai = _deduplicate_ai_records(
        db.fetchall(
            """
            SELECT * FROM external_usage_records
            WHERE account_id = %s AND organization_id IS NULL
              AND substr(occurred_at, 1, 10) >= %s AND substr(occurred_at, 1, 10) <= %s
            ORDER BY occurred_at DESC
            """,
            (acc_id, p_start, p_end),
        )
    )
    src_filter = (flt.get("source") or "").strip().upper()
    if src_filter:
        ext_ai = [r for r in ext_ai if (r.get("source") or "").upper() == src_filter]

    has_tok = any(r["total_tokens"] is not None for r in ext_ai)
    total_tok = sum((r["total_tokens"] or 0) for r in ext_ai if r["total_tokens"] is not None)
    reported_cost = sum((r["reported_cost_minor"] or 0) for r in ext_ai if r["reported_cost_minor"] is not None)
    est_cost = sum((r["estimated_value_minor"] or 0) for r in ext_ai if r["estimated_value_minor"] is not None)
    by_source: dict[str, dict[str, Any]] = {}
    ai_display = []
    for r in ext_ai:
        src = r["source"]
        if src not in by_source:
            by_source[src] = {"source": src, "records": 0, "total_tokens": 0, "reported_cost_minor": 0}
        by_source[src]["records"] += 1
        by_source[src]["total_tokens"] += int(r.get("total_tokens") or 0)
        by_source[src]["reported_cost_minor"] += int(r.get("reported_cost_minor") or 0)
        ai_display.append(
            {
                "source": src,
                "model_name": r.get("model_name") or "-",
                "billing_lane": r.get("billing_lane") or "standard",
                "total_tokens": r.get("total_tokens"),
                "reported_cost_display": f"{r['currency']} {r['reported_cost_minor']/100:,.2f}"
                if r.get("reported_cost_minor") is not None
                else "Not billed per token",
                "estimated_value_display": f"{r['currency']} {r['estimated_value_minor']/100:,.2f}"
                if r.get("estimated_value_minor") is not None
                else "-",
            }
        )

    notif_rows = db.fetchall(
        """
        SELECT recipient, subject, status, updated_at FROM notification_jobs
        WHERE account_id = %s AND organization_id IS NULL
          AND substr(created_at, 1, 10) >= %s AND substr(created_at, 1, 10) <= %s
        ORDER BY created_at DESC LIMIT 50
        """,
        (acc_id, p_start, p_end),
    )

    # Category bar chart computation
    raw_cat_bars: list[dict[str, Any]] = []
    for cur, cat_map in dash.get("category_breakdown", {}).items():
        for cat_name, amt_minor in cat_map.items():
            raw_cat_bars.append({"category": cat_name, "currency": cur, "monthly_minor": amt_minor})
    max_amt = max((b["monthly_minor"] for b in raw_cat_bars), default=1) or 1
    category_bars = []
    for idx, b in enumerate(sorted(raw_cat_bars, key=lambda x: x["monthly_minor"], reverse=True)):
        width_px = max(14, int(round((b["monthly_minor"] / max_amt) * 280)))
        pct = round((b["monthly_minor"] / max_amt) * 100.0, 1)
        category_bars.append(
            {
                **b,
                "bar_width": width_px,
                "width_pct": pct,
                "color": _BAR_COLORS[idx % len(_BAR_COLORS)],
                "y_pos": idx * 28 + 8,
                "amount_display": f"{b['currency']} {b['monthly_minor']/100:,.2f}/mo",
            }
        )

    return {
        "kind": "history_report",
        "view": "individual",
        "account_id": acc_id,
        "account_email": principal["user"]["email"],
        "period_start": p_start,
        "period_end": p_end,
        "period": {"from": p_start, "to": p_end},
        "issued_at": now_iso,
        "currency_totals": dash.get("currency_totals", []),
        "category_breakdown": dash.get("category_breakdown", {}),
        "category_bars": category_bars,
        "subscriptions": subs,
        "items": subs,
        "renewal_events": renewal_events,
        "observed_activity_summary": {
            "total_active_seconds": total_obs_sec,
            "total_active_minutes": total_obs_sec // 60,
            "by_domain": by_domain,
            "daily_rows": daily_rows,
        },
        "ai_usage_summary": {
            "total_tokens": total_tok if has_tok else None,
            "token_count_quality": "verified" if has_tok else "unavailable",
            "reported_api_cost_minor": reported_cost,
            "estimated_equivalent_minor": est_cost,
            "by_source": list(by_source.values()),
            "records_display": ai_display,
            "records": ext_ai,
        },
        "notifications_sent": notif_rows,
        "provenance_notes": [
            "Recurring subscription fees are tracked separately from metered API spend and estimated token value.",
            "Observed browser/desktop activity measures foreground tab or application time only; token counts are marked Unavailable unless routed via LiteLLM or Claude Code OTel.",
            "Recorded personal obligations and calendar renewals do not represent verified bank charges unless backed by a provider payment receipt.",
        ],
    }


@history_router.get("/history", status_code=200)
def get_history_dataset(
    from_date: str | None = Query(default=None, alias="from"),
    to_date: str | None = Query(default=None, alias="to"),
    view: str = Query(default="individual"),
    organization_id: str | None = Query(default=None),
    category: str | None = Query(default=None),
    source: str | None = Query(default=None),
    principal: dict = Depends(get_current_principal),
):
    with get_db(write=False) as db:
        return build_history_dataset(
            db,
            principal=principal,
            view=view,
            period_start=from_date,
            period_end=to_date,
            organization_id=organization_id,
            filters={"category": category, "source": source},
        )


def render_report_job(db: DBConnection, report_id: str, raw_html_override: str | None = None) -> dict:
    rep = db.fetchone("SELECT * FROM reports WHERE id = %s", (report_id,))
    if not rep:
        raise DomainError(404, "REPORT_NOT_FOUND", "Report not found.")
    snapshot = json.loads(rep["snapshot_json"])
    pdf_bytes = render_report_pdf(rep["report_type"], snapshot, rep["snapshot_hash"], raw_html_override=raw_html_override)
    store = PrivateObjectStore()
    tenant_folder = rep["organization_id"] or rep["account_id"] or "global"
    obj_key = f"reports/{tenant_folder}/{rep['id']}.pdf"
    store.put_bytes(obj_key, pdf_bytes, "application/pdf")
    db.execute("UPDATE reports SET object_key = %s, status = 'ready' WHERE id = %s", (obj_key, report_id))
    return {"id": report_id, "status": "ready", "object_key": obj_key, "size_bytes": len(pdf_bytes)}


@router.post("", status_code=202)
def create_report(payload: ReportCreate, principal: dict = Depends(get_current_principal)):
    is_history = payload.kind == "history_report" or payload.report_type == "history_report"
    db_report_type = "statement" if is_history else payload.report_type
    if db_report_type not in ("payment_receipt", "invoice", "statement"):
        raise DomainError(
            422,
            "INVALID_REPORT_TYPE",
            "report_type must be payment_receipt, invoice, statement, or history_report.",
        )
    now_iso = get_clock().now_iso()
    report_id = str(uuid.uuid4())
    stored_kind = "history_report" if is_history else db_report_type
    p_start = payload.period_start or payload.from_date
    p_end = payload.period_end or payload.to_date

    with get_db(write=True) as db:
        if db_report_type in ("payment_receipt", "invoice"):
            if not payload.invoice_id:
                raise DomainError(422, "INVOICE_ID_REQUIRED", "invoice_id is required for invoice/receipt reports.")
            inv = db.fetchone(
                """
                SELECT i.*, o.name as org_name
                FROM invoices i JOIN organizations o ON o.id = i.organization_id
                WHERE i.id = %s
                """,
                (payload.invoice_id,),
            )
            if not inv:
                raise DomainError(404, "INVOICE_NOT_FOUND", "Invoice not found.")
            require_org_role(db, principal, inv["organization_id"], {"owner", "admin", "billing", "staff"})

            if db_report_type == "payment_receipt" and inv["status"] != "paid":
                raise DomainError(
                    409,
                    "UNPAID_INVOICE_FOR_RECEIPT",
                    "Payment receipt cannot be generated for an unpaid invoice; generate an invoice or statement instead.",
                )

            inv_snap = json.loads(inv["snapshot_json"])
            pay = db.fetchone(
                "SELECT * FROM payments WHERE invoice_id = %s AND status = 'confirmed' ORDER BY paid_at DESC LIMIT 1",
                (inv["id"],),
            )
            doc_num = f"R-{now_iso[:4]}-{uuid.uuid4().hex[:6].upper()}" if db_report_type == "payment_receipt" else inv["document_number"]
            paid_minor = pay["amount_minor"] if pay else 0
            snapshot = {
                "receipt_number": doc_num if db_report_type == "payment_receipt" else None,
                "document_number": inv["document_number"],
                "org_name": inv["org_name"],
                "billed_to": inv_snap.get("subscriber_name") or inv_snap.get("party_ref") or "Subscriber",
                "plan_name": inv_snap.get("plan_name") or inv_snap.get("description") or "Service",
                "period_start": inv_snap.get("period_start", ""),
                "period_end": inv_snap.get("period_end", ""),
                "total_minor": inv["total_minor"],
                "amount_paid_minor": paid_minor,
                "balance_due_minor": max(0, inv["total_minor"] - paid_minor),
                "currency": inv["currency"],
                "payment_method": pay["method"] if pay else "unpaid",
                "payment_reference": pay["external_ref"] if pay else "-",
                "issued_at": now_iso,
                "status": "CONFIRMED PAYMENT" if db_report_type == "payment_receipt" else inv["status"].upper(),
            }
            org_id = inv["organization_id"]
            acc_id = None
        elif is_history or p_start or p_end or payload.filters:
            doc_num = f"HIST-{now_iso[:4]}-{uuid.uuid4().hex[:6].upper()}"
            stored_kind = "history_report"
            effective_view = "provider" if (payload.view == "provider" or payload.organization_id) else "individual"
            snapshot = build_history_dataset(
                db,
                principal=principal,
                view=effective_view,
                period_start=p_start,
                period_end=p_end,
                organization_id=payload.organization_id,
                filters=payload.filters,
            )
            snapshot["document_number"] = doc_num
            p_start = snapshot["period_start"]
            p_end = snapshot["period_end"]
            org_id = payload.organization_id if effective_view == "provider" else None
            acc_id = principal["personal_account_id"] if effective_view == "individual" else None
        else:
            # Legacy v4.0 Personal or Org statement compatibility
            if payload.organization_id:
                require_org_role(db, principal, payload.organization_id, {"owner", "admin", "billing"})
                org = db.fetchone("SELECT name, default_currency FROM organizations WHERE id = %s", (payload.organization_id,))
                snapshot = {"org_name": org["name"], "issued_at": now_iso, "items": []}
                org_id = payload.organization_id
                acc_id = None
            else:
                acc_id = principal["personal_account_id"]
                items = db.fetchall(
                    "SELECT service_name, billing_interval, price_minor, currency, next_renewal_date FROM personal_subscriptions WHERE account_id = %s",
                    (acc_id,),
                )
                snapshot = {"account_email": principal["user"]["email"], "issued_at": now_iso, "items": items}
                org_id = None
            doc_num = f"ST-{now_iso[:4]}-{uuid.uuid4().hex[:6].upper()}"

        snap_str = json.dumps(snapshot, sort_keys=True)
        snap_hash = hashlib.sha256(snap_str.encode("utf-8")).hexdigest()
        db.execute(
            """
            INSERT INTO reports (
                id, organization_id, account_id, invoice_id, report_type, kind,
                period_start, period_end, filters_json, document_number,
                snapshot_json, snapshot_hash, object_key, status, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '', 'queued', %s)
            """,
            (
                report_id,
                org_id,
                acc_id,
                payload.invoice_id,
                db_report_type,
                stored_kind,
                p_start,
                p_end,
                json.dumps(payload.filters or {}),
                doc_num,
                snap_str,
                snap_hash,
                now_iso,
            ),
        )
        # Synchronously execute worker render unless test validates unsafe HTML or queued state
        render_res = render_report_job(db, report_id, raw_html_override=payload.custom_html_test)

    return {
        "id": report_id,
        "report_type": db_report_type,
        "kind": stored_kind,
        "document_number": doc_num,
        "period_start": p_start,
        "period_end": p_end,
        "snapshot_hash": snap_hash,
        "status": render_res["status"],
        "object_key": render_res["object_key"],
        "download_url": f"/api/v1/reports/{report_id}/download",
        "preview_url": f"/api/v1/reports/{report_id}/preview",
    }


@router.get("", status_code=200)
def list_reports(organization_id: str | None = None, principal: dict = Depends(get_current_principal)):
    with get_db(write=False) as db:
        if organization_id:
            require_org_role(db, principal, organization_id, {"owner", "admin", "billing", "staff"})
            rows = db.fetchall("SELECT * FROM reports WHERE organization_id = %s ORDER BY created_at DESC", (organization_id,))
        else:
            rows = db.fetchall(
                "SELECT * FROM reports WHERE account_id = %s ORDER BY created_at DESC",
                (principal["personal_account_id"],),
            )
    return {"reports": rows}


@router.get("/{report_id}", status_code=200)
def get_report(report_id: str, principal: dict = Depends(get_current_principal)):
    with get_db(write=False) as db:
        rep = db.fetchone("SELECT * FROM reports WHERE id = %s", (report_id,))
        if not rep:
            raise DomainError(404, "REPORT_NOT_FOUND", "Report not found.")
        if rep["organization_id"]:
            require_org_role(db, principal, rep["organization_id"], {"owner", "admin", "billing", "staff"})
        elif rep["account_id"] != principal["personal_account_id"]:
            raise DomainError(403, "FORBIDDEN", "Access denied to this report.")
    return rep


@router.post("/{report_id}/render", status_code=200)
def trigger_render(report_id: str, principal: dict = Depends(get_current_principal)):
    with get_db(write=True) as db:
        rep = db.fetchone("SELECT * FROM reports WHERE id = %s", (report_id,))
        if not rep:
            raise DomainError(404, "REPORT_NOT_FOUND", "Report not found.")
        if rep["organization_id"]:
            require_org_role(db, principal, rep["organization_id"], {"owner", "admin", "billing", "staff"})
        elif rep["account_id"] != principal["personal_account_id"]:
            raise DomainError(403, "FORBIDDEN", "Access denied.")
        res = render_report_job(db, report_id)
    return res


@router.post("/{report_id}/share-link", status_code=201)
def create_expiring_download_token(
    report_id: str,
    payload: ShareLinkCreate | None = None,
    principal: dict = Depends(get_current_principal),
):
    ttl = payload.expires_in_seconds if payload else 900
    now = get_clock().now()
    now_iso = get_clock().now_iso()
    expires_iso = (now + timedelta(seconds=ttl)).isoformat().replace("+00:00", "Z")
    raw_token = generate_token(24)
    tok_id = str(uuid.uuid4())
    with get_db(write=True) as db:
        rep = db.fetchone("SELECT * FROM reports WHERE id = %s", (report_id,))
        if not rep:
            raise DomainError(404, "REPORT_NOT_FOUND", "Report not found.")
        if rep["organization_id"]:
            require_org_role(db, principal, rep["organization_id"], {"owner", "admin", "billing"})
        elif rep["account_id"] != principal["personal_account_id"]:
            raise DomainError(403, "FORBIDDEN", "Access denied.")
        if rep["status"] != "ready":
            render_report_job(db, report_id)
        db.execute(
            """
            INSERT INTO report_download_tokens (id, report_id, token_hash, expires_at, created_at)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (tok_id, report_id, hash_token(raw_token), expires_iso, now_iso),
        )
    share_url = f"/api/v1/reports/{report_id}/download?token={raw_token}"
    return {
        "report_id": report_id,
        "download_token": raw_token,
        "download_url": share_url,
        "share_url": share_url,
        "expires_at": expires_iso,
        "single_use": True,
    }


@router.get("/{report_id}/download", status_code=200)
def download_report_pdf(report_id: str, request: Request, token: str | None = None):
    now_iso = get_clock().now_iso()
    with get_db(write=True) as db:
        rep = db.fetchone("SELECT * FROM reports WHERE id = %s", (report_id,))
        if not rep:
            raise DomainError(404, "REPORT_NOT_FOUND", "Report not found.")

        if token:
            t_hash = hash_token(token)
            tok = db.fetchone(
                "SELECT * FROM report_download_tokens WHERE report_id = %s AND token_hash = %s",
                (report_id, t_hash),
            )
            if not tok:
                raise DomainError(403, "INVALID_DOWNLOAD_TOKEN", "Invalid report download token.")
            if tok["used_at"] is not None or tok["expires_at"] < now_iso:
                raise DomainError(410, "DOWNLOAD_TOKEN_EXPIRED", "Single-use download token has expired or already been used.")
            db.execute("UPDATE report_download_tokens SET used_at = %s WHERE id = %s", (now_iso, tok["id"]))
        else:
            # Must be authenticated session with tenant ownership (T055)
            sess_tok = request.cookies.get("subsphere_session")
            if not sess_tok:
                auth = request.headers.get("authorization", "")
                if auth.lower().startswith("bearer "):
                    sess_tok = auth[7:].strip()
            if not sess_tok:
                raise DomainError(401, "UNAUTHENTICATED", "Authentication or valid single-use download token is required.")
            principal = get_session_principal(db, sess_tok)
            if rep["organization_id"]:
                require_org_role(db, principal, rep["organization_id"], {"owner", "admin", "billing", "staff"})
            elif rep["account_id"] != principal["personal_account_id"]:
                raise DomainError(403, "FORBIDDEN", "Access denied to another tenant's report.")

        if rep["status"] != "ready" or not rep["object_key"]:
            render_report_job(db, report_id)
            rep = db.fetchone("SELECT * FROM reports WHERE id = %s", (report_id,))

        store = PrivateObjectStore()
        pdf_bytes = store.get_bytes(rep["object_key"])
        if not pdf_bytes:
            # Regenerate from immutable snapshot if object store cache was cleared
            render_report_job(db, report_id)
            rep = db.fetchone("SELECT * FROM reports WHERE id = %s", (report_id,))
            pdf_bytes = store.get_bytes(rep["object_key"])

    return Response(
        content=pdf_bytes or b"",
        status_code=200,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{rep["document_number"]}.pdf"'},
    )


@router.get("/{report_id}/preview", status_code=200)
def preview_report(
    report_id: str,
    format: str = Query(default="html"),
    principal: dict = Depends(get_current_principal),
):
    with get_db(write=False) as db:
        rep = db.fetchone("SELECT * FROM reports WHERE id = %s", (report_id,))
        if not rep:
            raise DomainError(404, "REPORT_NOT_FOUND", "Report not found.")
        if rep["organization_id"]:
            require_org_role(db, principal, rep["organization_id"], {"owner", "admin", "billing", "staff"})
        elif rep["account_id"] != principal["personal_account_id"]:
            raise DomainError(403, "FORBIDDEN", "Access denied to this report.")
        snapshot = json.loads(rep["snapshot_json"])
        html = render_report_html(rep["report_type"], snapshot, rep["snapshot_hash"])
    if format == "json":
        return {
            "id": report_id,
            "kind": rep.get("kind") or rep["report_type"],
            "document_number": rep["document_number"],
            "html": html,
            "snapshot": snapshot,
        }
    return Response(content=html, status_code=200, media_type="text/html")


@router.post("/{report_id}/send-email", status_code=200)
def send_report_email(report_id: str, principal: dict = Depends(get_current_principal)):
    now_iso = get_clock().now_iso()
    user_email = principal["user"]["email"].lower()
    with get_db(write=True) as db:
        rep = db.fetchone("SELECT * FROM reports WHERE id = %s", (report_id,))
        if not rep:
            raise DomainError(404, "REPORT_NOT_FOUND", "Report not found.")
        if rep["organization_id"]:
            require_org_role(db, principal, rep["organization_id"], {"owner", "admin", "billing", "staff"})
        elif rep["account_id"] != principal["personal_account_id"]:
            raise DomainError(403, "FORBIDDEN", "Access denied to this report.")

        pref = None
        if rep["account_id"]:
            pref = db.fetchone("SELECT primary_email FROM notification_preferences WHERE account_id = %s", (rep["account_id"],))
        recipient = (pref["primary_email"].lower() if pref and pref.get("primary_email") else user_email)
        snap = json.loads(rep["snapshot_json"])
        job_id = str(uuid.uuid4())
        subject = f"SubSphere Report {rep['document_number']} ({rep.get('kind') or rep['report_type']})"
        body = (
            f"Hello,\n\n"
            f"Your SubSphere report {rep['document_number']} is ready.\n"
            f"Period: {snap.get('period_start', '-')} to {snap.get('period_end', '-')}\n"
            f"Download path: /api/v1/reports/{report_id}/download\n"
            f"Checksum: {rep['snapshot_hash'][:16]}\n"
        )
        db.execute(
            """
            INSERT INTO notification_jobs (
                id, logical_key, account_id, organization_id, rule_id, channel,
                recipient, subject, body, status, attempts, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, NULL, 'email', %s, %s, %s, 'queued', 0, %s, %s)
            """,
            (
                job_id,
                f"report_email:{report_id}:{job_id}",
                rep["account_id"],
                rep["organization_id"],
                recipient,
                subject,
                body,
                now_iso,
                now_iso,
            ),
        )
        dispatch_res = dispatch_email_jobs(db)
        job = db.fetchone("SELECT status, last_error FROM notification_jobs WHERE id = %s", (job_id,))
    return {
        "report_id": report_id,
        "job_id": job_id,
        "recipient": recipient,
        "status": job["status"],
        "last_error": job["last_error"],
        "dispatch_summary": dispatch_res,
    }

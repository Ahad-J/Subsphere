"""Claude Code OpenTelemetry metric ingestion adapter with privacy guard and cumulative/delta deduplication."""
from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

from app.core.clock import get_clock
from app.core.errors import DomainError
from app.db.engine import DBConnection
from app.integrations.litellm_adapter import compute_estimated_token_value_minor

FORBIDDEN_OTEL_ATTRIBUTE_KEYS = {
    "prompt",
    "user_prompt",
    "raw_prompt",
    "prompt_text",
    "completion_text",
    "message_content",
    "tool_parameters",
    "tool_args",
    "tool_input",
    "tool_output",
}


def _extract_attr_value(val_obj: Any) -> Any:
    if not isinstance(val_obj, dict):
        return val_obj
    for key in ("stringValue", "string_value", "intValue", "int_value", "doubleValue", "double_value", "boolValue", "bool_value"):
        if key in val_obj:
            return val_obj[key]
    return val_obj


def _parse_attributes(attrs: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(attrs, dict):
        for k, v in attrs.items():
            out[str(k)] = _extract_attr_value(v)
    elif isinstance(attrs, list):
        for item in attrs:
            if isinstance(item, dict) and "key" in item:
                out[str(item["key"])] = _extract_attr_value(item.get("value"))
    return out


def validate_no_forbidden_otel_content(obj: Any) -> None:
    """Recursively reject any prompt, message content, or raw tool parameter keys in OTel payloads."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).strip().lower()
            if kl in FORBIDDEN_OTEL_ATTRIBUTE_KEYS:
                raise DomainError(
                    422,
                    "PRIVACY_VIOLATION_PROMPT_CONTENT",
                    f"Telemetry payload contains prohibited private attribute '{k}'. Only aggregate metric counters are accepted.",
                )
            if k == "attributes":
                parsed = _parse_attributes(v)
                for ak in parsed:
                    if ak.strip().lower() in FORBIDDEN_OTEL_ATTRIBUTE_KEYS:
                        raise DomainError(
                            422,
                            "PRIVACY_VIOLATION_PROMPT_CONTENT",
                            f"Telemetry attribute '{ak}' is prohibited; prompt text and tool parameters are never stored.",
                        )
            validate_no_forbidden_otel_content(v)
    elif isinstance(obj, list):
        for item in obj:
            validate_no_forbidden_otel_content(item)


def _dp_numeric_value(dp: dict[str, Any]) -> float:
    for key in ("asInt", "as_int", "asDouble", "as_double", "value", "sum"):
        if key in dp and dp[key] is not None:
            return float(dp[key])
    return 0.0


def _dp_timestamp(dp: dict[str, Any], default_iso: str) -> str:
    nano = dp.get("timeUnixNano") or dp.get("time_unix_nano") or dp.get("startTimeUnixNano")
    if nano:
        return str(nano)
    return str(dp.get("timestamp") or default_iso)


def _normalize_metrics(payload: dict[str, Any], default_iso: str) -> list[dict[str, Any]]:
    """Convert either standard OTLP JSON or SubSphere simplified metric JSON into normalized data points."""
    points: list[dict[str, Any]] = []

    # 1. Standard OTLP JSON: resourceMetrics -> scopeMetrics -> metrics
    for rm in payload.get("resourceMetrics") or payload.get("resource_metrics") or []:
        res_attrs = _parse_attributes((rm.get("resource") or {}).get("attributes"))
        for sm in rm.get("scopeMetrics") or rm.get("scope_metrics") or []:
            for metric in sm.get("metrics") or []:
                m_name = str(metric.get("name") or "")
                container = metric.get("sum") or metric.get("gauge") or metric.get("histogram") or {}
                temp_raw = container.get("aggregationTemporality") or container.get("aggregation_temporality") or payload.get("temporality") or "delta"
                if temp_raw in (2, "2", "AGGREGATION_TEMPORALITY_CUMULATIVE", "cumulative"):
                    temporality = "cumulative"
                else:
                    temporality = "delta"
                for dp in container.get("dataPoints") or container.get("data_points") or []:
                    dp_attrs = {**res_attrs, **_parse_attributes(dp.get("attributes"))}
                    points.append(
                        {
                            "name": m_name,
                            "value": _dp_numeric_value(dp),
                            "temporality": temporality,
                            "timestamp": _dp_timestamp(dp, default_iso),
                            "attrs": dp_attrs,
                        }
                    )

    # 2. Simplified SubSphere flat telemetry payload: {"session_id": ..., "temporality": ..., "metrics": [...]}
    base_session = payload.get("session_id") or payload.get("session.id") or "default-session"
    base_temp = str(payload.get("temporality") or "delta").lower()
    for m in payload.get("metrics") or []:
        if not isinstance(m, dict):
            continue
        attrs = {**_parse_attributes(m.get("attributes"))}
        for k in ("type", "model", "session_id", "session.id", "request_ref", "request.id", "billing_lane"):
            if k in m and k not in attrs:
                attrs[k] = m[k]
        attrs.setdefault("session.id", attrs.get("session_id") or base_session)
        points.append(
            {
                "name": str(m.get("name") or ""),
                "value": float(m.get("value") or 0),
                "temporality": str(m.get("temporality") or base_temp).lower(),
                "timestamp": str(m.get("timestamp") or default_iso),
                "attrs": attrs,
            }
        )

    return points


class ClaudeCodeOtelIngestAdapter:
    """Ingests Claude Code OpenTelemetry metrics with strict privacy filtering and cumulative/delta deduplication."""

    @staticmethod
    def ingest(
        db: DBConnection,
        *,
        account_id: str,
        organization_id: str | None,
        connection_id: str,
        payload: dict[str, Any],
        membership_id: str | None = None,
    ) -> dict[str, Any]:
        validate_no_forbidden_otel_content(payload)
        now_iso = get_clock().now_iso()
        points = _normalize_metrics(payload, now_iso)
        if not points:
            raise DomainError(422, "EMPTY_TELEMETRY", "No recognized OpenTelemetry metric points in payload.")

        # Apply cumulative/delta checkpointing per (session_id, metric_name, model_name, token_type)
        grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        duplicates_skipped = 0

        for pt in points:
            m_name = pt["name"]
            attrs = pt["attrs"]
            session_id = str(attrs.get("session.id") or attrs.get("session_id") or "default-session")
            model_name = str(attrs.get("model") or attrs.get("gen_ai.request.model") or "claude-3-5-sonnet")
            token_type = str(attrs.get("type") or attrs.get("token_type") or "")
            request_ref = str(attrs.get("request_ref") or attrs.get("request.id") or "")
            billing_lane = str(attrs.get("billing_lane") or "claude_subscription")
            ts_str = pt["timestamp"]
            temporality = pt["temporality"]
            raw_val = pt["value"]

            # Scale cost USD to integer minor cents for checkpoint math
            scaled_val = int(round(raw_val * 100)) if "cost" in m_name else int(round(raw_val))

            cp = db.fetchone(
                """
                SELECT * FROM otel_metric_checkpoints
                WHERE account_id = %s AND session_id = %s AND metric_name = %s
                  AND model_name = %s AND token_type = %s
                """,
                (account_id, session_id, m_name, model_name, token_type),
            )

            delta_val = scaled_val
            if temporality == "cumulative":
                prev_val = int(cp["last_cumulative_value"]) if cp else 0
                if scaled_val <= prev_val:
                    duplicates_skipped += 1
                    continue
                delta_val = scaled_val - prev_val
                new_checkpoint_val = scaled_val
            else:
                # Delta temporality: skip exact timestamp replay when same checkpoint timestamp was already ingested
                if cp and cp["last_timestamp"] == ts_str and cp["last_cumulative_value"] == scaled_val:
                    duplicates_skipped += 1
                    continue
                new_checkpoint_val = scaled_val

            if cp:
                db.execute(
                    """
                    UPDATE otel_metric_checkpoints
                    SET temporality = %s, last_cumulative_value = %s, last_timestamp = %s, updated_at = %s
                    WHERE id = %s
                    """,
                    (temporality, new_checkpoint_val, ts_str, now_iso, cp["id"]),
                )
            else:
                db.execute(
                    """
                    INSERT INTO otel_metric_checkpoints (
                        id, account_id, organization_id, session_id, metric_name,
                        model_name, token_type, temporality, last_cumulative_value, last_timestamp, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(uuid.uuid4()),
                        account_id,
                        organization_id,
                        session_id,
                        m_name,
                        model_name,
                        token_type,
                        temporality,
                        new_checkpoint_val,
                        ts_str,
                        now_iso,
                    ),
                )

            bucket_key = (session_id, model_name, request_ref or ts_str, billing_lane)
            if bucket_key not in grouped:
                grouped[bucket_key] = {
                    "session_id": session_id,
                    "model_name": model_name,
                    "request_ref": request_ref or None,
                    "billing_lane": billing_lane,
                    "ts": ts_str,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_read_tokens": 0,
                    "cache_creation_tokens": 0,
                    "requests_count": 0,
                    "cost_minor": None,
                    "has_tokens": False,
                }
            b = grouped[bucket_key]
            if m_name in ("claude_code.token.usage", "gen_ai.client.token.usage"):
                b["has_tokens"] = True
                tl = token_type.lower()
                if tl == "input":
                    b["input_tokens"] += delta_val
                elif tl == "output":
                    b["output_tokens"] += delta_val
                elif tl in ("cacheread", "cache_read"):
                    b["cache_read_tokens"] += delta_val
                elif tl in ("cachecreation", "cache_creation"):
                    b["cache_creation_tokens"] += delta_val
                else:
                    b["input_tokens"] += delta_val
            elif m_name in ("claude_code.cost.usage",):
                b["cost_minor"] = (b["cost_minor"] or 0) + delta_val
            elif m_name in ("claude_code.api_request", "claude_code.session.count"):
                b["requests_count"] += max(1, delta_val)

        ingested = 0
        total_in = 0
        total_out = 0
        total_cr = 0
        total_cc = 0
        record_ids: list[str] = []

        for _, b in grouped.items():
            tot_tokens = b["input_tokens"] + b["output_tokens"] + b["cache_read_tokens"] + b["cache_creation_tokens"]
            if not b["has_tokens"] and b["requests_count"] == 0 and b["cost_minor"] is None:
                continue
            ext_rec_id = f"otel:{b['session_id']}:{b['model_name']}:{b['request_ref'] or uuid.uuid4().hex[:10]}"
            existing_rec = db.fetchone(
                "SELECT id FROM external_usage_records WHERE connection_id = %s AND external_record_id = %s",
                (connection_id, ext_rec_id),
            )
            if existing_rec:
                duplicates_skipped += 1
                continue

            # Resolve pricing for estimated equivalent token value
            pricing_row = db.fetchone(
                """
                SELECT input_per_million_usd, output_per_million_usd FROM pricing_versions
                WHERE model_name = %s ORDER BY effective_from DESC LIMIT 1
                """,
                (b["model_name"],),
            )
            in_rate = Decimal(str(pricing_row["input_per_million_usd"])) if pricing_row else Decimal("3.00")
            out_rate = Decimal(str(pricing_row["output_per_million_usd"])) if pricing_row else Decimal("15.00")
            est_minor, _ = compute_estimated_token_value_minor(
                input_tokens=b["input_tokens"] + b["cache_read_tokens"],
                output_tokens=b["output_tokens"] + b["cache_creation_tokens"],
                input_per_million_usd=str(in_rate),
                output_per_million_usd=str(out_rate),
            )

            # Subscription billing lane preserves cost separation (API billed cost = None unless API lane explicitly pushed > 0)
            rep_cost = b["cost_minor"] if b["billing_lane"] == "api_key" else None
            q_note = (
                "Claude Code OTel telemetry (Claude subscription lane — covered by subscription, not double-billed)"
                if b["billing_lane"] == "claude_subscription"
                else "Claude Code OTel telemetry (API key usage lane)"
            )

            rec_id = str(uuid.uuid4())
            db.execute(
                """
                INSERT INTO external_usage_records (
                    id, connection_id, external_record_id, account_id, organization_id, membership_id,
                    model_name, requests_count, input_tokens, output_tokens, total_tokens,
                    cache_read_tokens, cache_creation_tokens, reported_cost_minor, estimated_value_minor,
                    currency, source, reliability, billing_lane, request_ref, session_id, quality_note,
                    occurred_at, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'USD', 'CLAUDE_CODE_OTEL', 'VERIFIED_TELEMETRY', %s, %s, %s, %s, %s, %s)
                """,
                (
                    rec_id,
                    connection_id,
                    ext_rec_id,
                    account_id,
                    organization_id,
                    membership_id,
                    b["model_name"],
                    b["requests_count"] or 1,
                    b["input_tokens"],
                    b["output_tokens"],
                    tot_tokens,
                    b["cache_read_tokens"],
                    b["cache_creation_tokens"],
                    rep_cost,
                    est_minor,
                    b["billing_lane"],
                    b["request_ref"],
                    b["session_id"],
                    q_note,
                    now_iso,
                    now_iso,
                ),
            )
            ingested += 1
            total_in += b["input_tokens"]
            total_out += b["output_tokens"]
            total_cr += b["cache_read_tokens"]
            total_cc += b["cache_creation_tokens"]
            record_ids.append(rec_id)

        db.execute(
            "UPDATE integration_connections SET last_sync_success = %s, error_code = NULL WHERE id = %s",
            (now_iso, connection_id),
        )
        return {
            "ingested_records": ingested,
            "duplicates_skipped": duplicates_skipped,
            "record_ids": record_ids,
            "totals": {
                "input_tokens": total_in,
                "output_tokens": total_out,
                "cache_read_tokens": total_cr,
                "cache_creation_tokens": total_cc,
                "total_tokens": total_in + total_out + total_cr + total_cc,
            },
        }

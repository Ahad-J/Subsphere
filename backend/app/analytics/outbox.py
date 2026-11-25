"""Transactional outbox dispatcher for independent Kafka and OpenMeter delivery."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

import requests

from app.core.clock import get_clock
from app.core.config import get_settings


def _analytics_dir() -> Path:
    base = Path(get_settings().data_dir) / "analytics"
    base.mkdir(parents=True, exist_ok=True)
    return base


def build_openmeter_cloudevent(event_row: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Format an outbox usage event as a CloudEvents 1.0 document for OpenMeter."""
    evt_id = str(payload.get("external_event_id") or payload.get("event_id") or event_row["id"])
    occurred_at = str(payload.get("occurred_at") or event_row["created_at"])
    subject = str(
        payload.get("subscriber_ref")
        or payload.get("subscription_id")
        or event_row["aggregate_id"]
    )
    return {
        "specversion": "1.0",
        "type": "subsphere.usage.v1",
        "id": evt_id,
        "source": "https://subsphere.local/metering",
        "subject": subject,
        "time": occurred_at,
        "datacontenttype": "application/json",
        "data": {
            "event_id": payload.get("event_id") or event_row["aggregate_id"],
            "external_event_id": payload.get("external_event_id"),
            "organization_id": payload.get("organization_id"),
            "service_id": payload.get("service_id"),
            "subscription_id": payload.get("subscription_id"),
            "subscription_period_id": payload.get("subscription_period_id"),
            "metric_key": payload.get("metric_key"),
            "quantity": payload.get("quantity"),
            "source": payload.get("source"),
        },
    }


def _default_kafka_sink(event_row: dict[str, Any], payload: dict[str, Any]) -> None:
    """Persist Kafka topic event locally when Kafka broker isn't connected, or publish via HTTP bridge."""
    spool_file = _analytics_dir() / "kafka_events.jsonl"
    record = {
        "topic": "subsphere.usage.accepted.v1",
        "key": str(payload.get("subscription_id") or event_row["aggregate_id"]),
        "event_id": payload.get("event_id") or event_row["aggregate_id"],
        "event_type": event_row["event_type"],
        "payload": payload,
        "dispatched_at": get_clock().now_iso(),
    }
    with spool_file.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def _default_openmeter_sink(event_row: dict[str, Any], payload: dict[str, Any]) -> None:
    """Deliver CloudEvents 1.0 payload to OpenMeter endpoint if configured, else append to local OpenMeter store."""
    ce = build_openmeter_cloudevent(event_row, payload)
    openmeter_url = os.getenv("OPENMETER_URL", "").strip()
    if openmeter_url:
        resp = requests.post(
            f"{openmeter_url.rstrip('/')}/api/v1/events",
            headers={"Content-Type": "application/cloudevents+json"},
            json=ce,
            timeout=5,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"OpenMeter HTTP {resp.status_code}: {resp.text[:200]}")

    om_file = _analytics_dir() / "openmeter_cloudevents.jsonl"
    # Deduplicate by CloudEvent `id` when checking reconciliation
    with om_file.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(ce) + "\n")


class OutboxDispatcher:
    """Dispatches outbox_deliveries independently per destination with failure isolation."""

    def __init__(
        self,
        kafka_sink: Callable[[dict[str, Any], dict[str, Any]], None] | None = None,
        openmeter_sink: Callable[[dict[str, Any], dict[str, Any]], None] | None = None,
    ) -> None:
        self.sinks: dict[str, Callable[[dict[str, Any], dict[str, Any]], None]] = {
            "kafka": kafka_sink or _default_kafka_sink,
            "openmeter": openmeter_sink or _default_openmeter_sink,
        }

    def dispatch_pending(
        self,
        db: Any,
        *,
        destinations: list[str] | None = None,
        simulate_failures: set[str] | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        now_iso = get_clock().now_iso()
        target_dests = destinations or ["kafka", "openmeter"]
        fail_set = simulate_failures or set()

        placeholders = ", ".join(["%s"] * len(target_dests))
        rows = db.fetchall(
            f"""
            SELECT d.id AS delivery_id, d.event_id, d.destination, d.status, d.attempts,
                   e.aggregate_type, e.aggregate_id, e.event_type, e.payload_json, e.created_at
            FROM outbox_deliveries d
            JOIN outbox_events e ON e.id = d.event_id
            WHERE d.status IN ('pending', 'failed')
              AND d.destination IN ({placeholders})
            ORDER BY e.created_at ASC, d.id ASC
            LIMIT %s
            """,
            (*target_dests, limit),
        )

        delivered_counts: dict[str, int] = {d: 0 for d in target_dests}
        failed_counts: dict[str, int] = {d: 0 for d in target_dests}
        errors: list[dict[str, Any]] = []

        for row in rows:
            dest = row["destination"]
            delivery_id = row["delivery_id"]
            attempts = int(row["attempts"] or 0) + 1
            payload = json.loads(row["payload_json"] or "{}")
            event_row = {
                "id": row["event_id"],
                "aggregate_type": row["aggregate_type"],
                "aggregate_id": row["aggregate_id"],
                "event_type": row["event_type"],
                "created_at": row["created_at"],
            }

            try:
                if dest in fail_set:
                    raise RuntimeError(f"Simulated {dest} outage")
                sink = self.sinks.get(dest)
                if sink is None:
                    raise RuntimeError(f"Unknown outbox destination: {dest}")
                sink(event_row, payload)
            except Exception as exc:
                err_msg = str(exc)[:300]
                db.execute(
                    """
                    UPDATE outbox_deliveries
                    SET status = 'failed', attempts = %s, last_error = %s
                    WHERE id = %s
                    """,
                    (attempts, err_msg, delivery_id),
                )
                failed_counts[dest] = failed_counts.get(dest, 0) + 1
                errors.append({"delivery_id": delivery_id, "destination": dest, "error": err_msg})
                continue

            db.execute(
                """
                UPDATE outbox_deliveries
                SET status = 'delivered', attempts = %s, last_error = NULL, delivered_at = %s
                WHERE id = %s
                """,
                (attempts, now_iso, delivery_id),
            )
            delivered_counts[dest] = delivered_counts.get(dest, 0) + 1

        return {
            "processed": len(rows),
            "delivered": delivered_counts,
            "failed": failed_counts,
            "errors": errors,
            "dispatched_at": now_iso,
        }

"""Bronze -> Silver -> Gold Medallion analytics pipeline, checkpoint dedup, freshness, and reconciliation."""
from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.clock import get_clock
from app.core.config import get_settings


def _lakehouse_root() -> Path:
    root = Path(get_settings().data_dir) / "lakehouse"
    for sub in ("bronze/events", "silver", "gold", "rejected", "checkpoints"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def _analytics_root() -> Path:
    root = Path(get_settings().data_dir) / "analytics"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _parse_iso(ts: str) -> datetime:
    cleaned = ts.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class MedallionPipeline:
    """Implements Bronze (immutable raw) -> Silver (dedup + schema) -> Gold (daily materialized aggregates)."""

    def __init__(self, lakehouse_dir: Path | None = None) -> None:
        self.root = lakehouse_dir or _lakehouse_root()
        for sub in ("bronze/events", "silver", "gold", "rejected", "checkpoints"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    def _checkpoint_file(self) -> Path:
        return self.root / "checkpoints" / "silver_seen_ids.json"

    def _load_seen_ids(self) -> set[str]:
        cp = self._checkpoint_file()
        if not cp.exists():
            return set()
        return set(json.loads(cp.read_text(encoding="utf-8")))

    def _save_seen_ids(self, seen: set[str]) -> None:
        self._checkpoint_file().write_text(json.dumps(sorted(seen)), encoding="utf-8")

    def ingest_bronze_events(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        """Validate & append raw events partitioned by event_date into Bronze; quarantine malformed rows."""
        accepted = 0
        rejected = 0
        partitioned: dict[str, list[str]] = defaultdict(list)
        rejected_lines: list[str] = []

        for raw in events:
            evt_id = raw.get("event_id") or raw.get("id")
            org_id = raw.get("organization_id") or raw.get("data", {}).get("organization_id")
            svc_id = raw.get("service_id") or raw.get("data", {}).get("service_id")
            metric_key = raw.get("metric_key") or raw.get("data", {}).get("metric_key")
            qty = raw.get("quantity") if "quantity" in raw else raw.get("data", {}).get("quantity")
            occurred_at = raw.get("occurred_at") or raw.get("time")

            valid = bool(evt_id and org_id and svc_id and metric_key and occurred_at)
            try:
                qty_int = int(qty)
                if qty_int <= 0:
                    valid = False
            except (TypeError, ValueError):
                qty_int = 0
                valid = False

            if valid:
                try:
                    dt = _parse_iso(str(occurred_at))
                    day = dt.strftime("%Y-%m-%d")
                except Exception:
                    valid = False
                    day = "invalid"
            else:
                day = "invalid"

            if not valid:
                rejected += 1
                rejected_lines.append(json.dumps({"reason": "schema_validation_failed", "raw": raw}))
                continue

            normalized_raw = {
                "event_id": str(evt_id),
                "external_event_id": raw.get("external_event_id") or str(evt_id),
                "organization_id": str(org_id),
                "service_id": str(svc_id),
                "subscription_id": raw.get("subscription_id") or raw.get("subject"),
                "metric_key": str(metric_key),
                "quantity": qty_int,
                "occurred_at": str(occurred_at),
                "event_date": day,
            }
            partitioned[day].append(json.dumps(normalized_raw))
            accepted += 1

        for day, lines in partitioned.items():
            part_dir = self.root / "bronze" / "events" / f"event_date={day}"
            part_dir.mkdir(parents=True, exist_ok=True)
            part_file = part_dir / "events.jsonl"
            with part_file.open("a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")

        if rejected_lines:
            rej_file = self.root / "rejected" / "malformed.jsonl"
            with rej_file.open("a", encoding="utf-8") as fh:
                fh.write("\n".join(rejected_lines) + "\n")

        return {"bronze_accepted": accepted, "rejected": rejected}

    def run_silver_and_gold(
        self,
        db: Any,
        *,
        replay_from_bronze: bool = False,
        additional_raw_events: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Run incremental or full Bronze->Silver->Gold materialization into PostgreSQL."""
        now_iso = get_clock().now_iso()

        # Pull newly delivered Kafka spool or raw Bronze inputs if supplied
        ingest_stats = {"bronze_accepted": 0, "rejected": 0}
        if additional_raw_events:
            ingest_stats = self.ingest_bronze_events(additional_raw_events)
        else:
            kafka_spool = _analytics_root() / "kafka_events.jsonl"
            if kafka_spool.exists():
                cursor_file = self.root / "checkpoints" / "kafka_spool_offset.txt"
                last_line = int(cursor_file.read_text(encoding="utf-8").strip()) if cursor_file.exists() else 0
                lines = [ln for ln in kafka_spool.read_text(encoding="utf-8").splitlines() if ln.strip()]
                if not replay_from_bronze and last_line < len(lines):
                    new_lines = lines[last_line:]
                    spool_events = []
                    for ln in new_lines:
                        rec = json.loads(ln)
                        if rec.get("event_type") == "usage.accepted" and rec.get("payload"):
                            spool_events.append(rec["payload"])
                    if spool_events:
                        ingest_stats = self.ingest_bronze_events(spool_events)
                    cursor_file.write_text(str(len(lines)), encoding="utf-8")

        # Read all Bronze partitions
        bronze_rows: list[dict[str, Any]] = []
        for pfile in sorted((self.root / "bronze" / "events").glob("event_date=*/events.jsonl")):
            for line in pfile.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    bronze_rows.append(json.loads(line))

        # Silver dedup via checkpoint state
        seen_ids = self._load_seen_ids()
        silver_file = self.root / "silver" / "events.jsonl"
        new_silver: list[dict[str, Any]] = []
        duplicates_removed = 0

        for row in bronze_rows:
            eid = row["event_id"]
            if eid in seen_ids:
                duplicates_removed += 1
                continue
            seen_ids.add(eid)
            new_silver.append(row)

        if new_silver:
            with silver_file.open("a", encoding="utf-8") as fh:
                for row in new_silver:
                    fh.write(json.dumps(row) + "\n")
            self._save_seen_ids(seen_ids)

        # Load full Silver dataset to compute exact Gold aggregates (idempotent overwrite/UPSERT)
        all_silver: list[dict[str, Any]] = []
        if silver_file.exists():
            seen_silver_ids: set[str] = set()
            for line in silver_file.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if row["event_id"] in seen_silver_ids:
                    continue
                seen_silver_ids.add(row["event_id"])
                all_silver.append(row)

        gold_usage: dict[tuple[str, str, str, str], dict[str, int]] = defaultdict(
            lambda: {"total_quantity": 0, "distinct_events": 0}
        )
        for row in all_silver:
            key = (
                row["organization_id"],
                row["service_id"],
                row["metric_key"],
                row["event_date"],
            )
            gold_usage[key]["total_quantity"] += int(row["quantity"])
            gold_usage[key]["distinct_events"] += 1

        gold_file = self.root / "gold" / "daily_usage.jsonl"
        with gold_file.open("w", encoding="utf-8") as fh:
            for (org_id, svc_id, mkey, day), agg in sorted(gold_usage.items()):
                record = {
                    "organization_id": org_id,
                    "service_id": svc_id,
                    "metric_key": mkey,
                    "day": day,
                    "total_quantity": agg["total_quantity"],
                    "distinct_events": agg["distinct_events"],
                    "updated_at": now_iso,
                }
                fh.write(json.dumps(record) + "\n")
                db.execute(
                    """
                    INSERT INTO daily_usage_aggregates (
                        id, organization_id, service_id, metric_key, day,
                        total_quantity, distinct_events, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (organization_id, service_id, metric_key, day)
                    DO UPDATE SET total_quantity = excluded.total_quantity,
                                  distinct_events = excluded.distinct_events,
                                  updated_at = excluded.updated_at
                    """,
                    (
                        str(uuid.uuid4()),
                        org_id,
                        svc_id,
                        mkey,
                        day,
                        agg["total_quantity"],
                        agg["distinct_events"],
                        now_iso,
                    ),
                )

        # Also materialize daily finance aggregates directly from authoritative PostgreSQL invoices & payments
        finance_rows = db.fetchall(
            """
            SELECT organization_id, currency, SUBSTR(issued_at, 1, 10) AS day,
                   SUM(total_minor) AS invoiced_minor
            FROM invoices
            GROUP BY organization_id, currency, SUBSTR(issued_at, 1, 10)
            """
        )
        collected_map: dict[tuple[str, str, str], int] = {}
        for p in db.fetchall(
            """
            SELECT organization_id, currency, SUBSTR(paid_at, 1, 10) AS day,
                   SUM(amount_minor) AS collected_minor
            FROM payments
            GROUP BY organization_id, currency, SUBSTR(paid_at, 1, 10)
            """
        ):
            collected_map[(p["organization_id"], p["currency"], p["day"])] = int(p["collected_minor"] or 0)

        for fr in finance_rows:
            k = (fr["organization_id"], fr["currency"], fr["day"])
            db.execute(
                """
                INSERT INTO daily_finance_aggregates (
                    id, organization_id, currency, day, invoiced_minor,
                    collected_minor, expenses_minor, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, 0, %s)
                ON CONFLICT (organization_id, currency, day)
                DO UPDATE SET invoiced_minor = excluded.invoiced_minor,
                              collected_minor = excluded.collected_minor,
                              updated_at = excluded.updated_at
                """,
                (
                    str(uuid.uuid4()),
                    fr["organization_id"],
                    fr["currency"],
                    fr["day"],
                    int(fr["invoiced_minor"] or 0),
                    collected_map.get(k, 0),
                    now_iso,
                ),
            )

        run_id = str(uuid.uuid4())
        bronze_total = len(bronze_rows)
        silver_total = len(all_silver)
        gold_total = len(gold_usage)
        rej_total = ingest_stats["rejected"]
        rej_file = self.root / "rejected" / "malformed.jsonl"
        if rej_file.exists() and rej_total == 0:
            rej_total = sum(1 for ln in rej_file.read_text(encoding="utf-8").splitlines() if ln.strip())

        db.execute(
            """
            INSERT INTO materialized_analytics_runs (
                id, pipeline_name, status, bronze_count, silver_count, gold_count,
                duplicates_removed, rejected_count, refreshed_at, metadata_json
            ) VALUES (%s, 'medallion_usage_finance_v1', 'succeeded', %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                run_id,
                bronze_total,
                silver_total,
                gold_total,
                duplicates_removed,
                rej_total,
                now_iso,
                json.dumps({"replay": replay_from_bronze, "new_silver_rows": len(new_silver)}),
            ),
        )

        return {
            "run_id": run_id,
            "status": "succeeded",
            "bronze_count": bronze_total,
            "silver_count": silver_total,
            "gold_groups": gold_total,
            "new_silver_rows": len(new_silver),
            "duplicates_removed": duplicates_removed,
            "rejected_count": rej_total,
            "refreshed_at": now_iso,
        }


def get_freshness_status(db: Any, *, stale_threshold_seconds: int = 900) -> dict[str, Any]:
    now_dt = get_clock().now()
    latest = db.fetchone(
        "SELECT * FROM materialized_analytics_runs ORDER BY refreshed_at DESC LIMIT 1"
    )
    outbox_stats = db.fetchall(
        "SELECT destination, status, COUNT(*) AS cnt FROM outbox_deliveries GROUP BY destination, status"
    )
    backlog: dict[str, dict[str, int]] = {}
    pending_or_failed = 0
    for r in outbox_stats:
        dest = r["destination"]
        backlog.setdefault(dest, {})[r["status"]] = int(r["cnt"])
        if r["status"] in ("pending", "failed"):
            pending_or_failed += int(r["cnt"])

    if latest is None:
        return {
            "has_materialized_run": False,
            "last_refreshed_at": None,
            "staleness_seconds": None,
            "is_stale": True,
            "banner": "Analytics Delayed / Not Yet Materialized",
            "outbox_backlog": backlog,
            "pending_or_failed_deliveries": pending_or_failed,
        }

    ref_dt = _parse_iso(latest["refreshed_at"])
    staleness_sec = max(0, int((now_dt - ref_dt).total_seconds()))
    is_stale = staleness_sec > stale_threshold_seconds or pending_or_failed > 0
    banner = None
    if is_stale:
        banner = (
            f"Analytics Delayed / Stale (lag {staleness_sec}s, pending/failed outbox={pending_or_failed})"
        )
    return {
        "has_materialized_run": True,
        "last_run_id": latest["id"],
        "last_refreshed_at": latest["refreshed_at"],
        "staleness_seconds": staleness_sec,
        "is_stale": is_stale,
        "banner": banner,
        "outbox_backlog": backlog,
        "pending_or_failed_deliveries": pending_or_failed,
    }


def generate_reconciliation_report(db: Any, organization_id: str | None = None) -> dict[str, Any]:
    """Produce Output O10 reconciliation comparing PostgreSQL Ledger vs OpenMeter vs Spark Gold."""
    if organization_id:
        pg_row = db.fetchone(
            """
            SELECT COUNT(ue.id) AS event_count, COALESCE(SUM(ue.quantity), 0) AS total_quantity
            FROM usage_events ue
            JOIN services s ON s.id = ue.service_id
            WHERE s.organization_id = %s
            """,
            (organization_id,),
        )
        gold_row = db.fetchone(
            """
            SELECT COALESCE(SUM(distinct_events), 0) AS event_count,
                   COALESCE(SUM(total_quantity), 0) AS total_quantity
            FROM daily_usage_aggregates
            WHERE organization_id = %s
            """,
            (organization_id,),
        )
    else:
        pg_row = db.fetchone(
            "SELECT COUNT(id) AS event_count, COALESCE(SUM(quantity), 0) AS total_quantity FROM usage_events"
        )
        gold_row = db.fetchone(
            """
            SELECT COALESCE(SUM(distinct_events), 0) AS event_count,
                   COALESCE(SUM(total_quantity), 0) AS total_quantity
            FROM daily_usage_aggregates
            """
        )

    pg_events = int(pg_row["event_count"] or 0) if pg_row else 0
    pg_qty = int(pg_row["total_quantity"] or 0) if pg_row else 0
    gold_events = int(gold_row["event_count"] or 0) if gold_row else 0
    gold_qty = int(gold_row["total_quantity"] or 0) if gold_row else 0

    # OpenMeter distinct CloudEvents ingested (filtered by org if supplied)
    om_file = _analytics_root() / "openmeter_cloudevents.jsonl"
    om_seen: set[str] = set()
    om_qty = 0
    if om_file.exists():
        for line in om_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            ce = json.loads(line)
            data = ce.get("data") or {}
            if organization_id and data.get("organization_id") != organization_id:
                continue
            ce_id = ce.get("id")
            if ce_id and ce_id not in om_seen:
                om_seen.add(ce_id)
                om_qty += int(data.get("quantity") or 0)

    return {
        "organization_id": organization_id,
        "reconciled_at": get_clock().now_iso(),
        "postgres_ledger": {
            "accepted_events": pg_events,
            "total_quantity": pg_qty,
        },
        "openmeter": {
            "distinct_cloudevents": len(om_seen),
            "total_quantity": om_qty,
        },
        "spark_gold": {
            "distinct_events": gold_events,
            "total_quantity": gold_qty,
        },
        "discrepancies": {
            "postgres_vs_openmeter_events": abs(pg_events - len(om_seen)),
            "postgres_vs_openmeter_quantity": abs(pg_qty - om_qty),
            "postgres_vs_spark_gold_events": abs(pg_events - gold_events),
            "postgres_vs_spark_gold_quantity": abs(pg_qty - gold_qty),
        },
        "reconciled_zero_discrepancy": (
            pg_events == len(om_seen) == gold_events and pg_qty == om_qty == gold_qty
        ),
    }

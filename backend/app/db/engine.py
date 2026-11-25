"""Dual-mode relational database engine (PostgreSQL 16 + SQLite WAL/BEGIN IMMEDIATE).

All authoritative business mutations use parameterized SQL inside explicit transactions.
SQLite mode enforces PRAGMA foreign_keys=ON and uses a process-wide RLock + BEGIN IMMEDIATE
so concurrent quota/seat allocation tests execute with true serializable isolation.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
_SQLITE_WRITE_LOCK = threading.RLock()


class DBConnection:
    """Thin wrapper unifying psycopg and sqlite3 cursor/transaction operations."""

    def __init__(self, raw_conn: Any, driver: str, lock: threading.RLock | None = None):
        self._conn = raw_conn
        self.driver = driver
        self._lock = lock

    def _adapt_sql(self, sql: str) -> str:
        if self.driver == "sqlite":
            return sql.replace("%s", "?")
        return sql.replace("?", "%s")

    def execute(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> Any:
        adapted = self._adapt_sql(sql)
        if self.driver == "sqlite":
            return self._conn.execute(adapted, tuple(params))
        cur = self._conn.cursor()
        cur.execute(adapted, tuple(params))
        return cur

    def fetchone(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> dict[str, Any] | None:
        cur = self.execute(sql, params)
        row = cur.fetchone()
        if row is None:
            return None
        if self.driver == "sqlite":
            return dict(row)
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    def fetchall(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> list[dict[str, Any]]:
        cur = self.execute(sql, params)
        rows = cur.fetchall()
        if self.driver == "sqlite":
            return [dict(r) for r in rows]
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in rows]

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()


def _get_database_url() -> str:
    return os.getenv("DATABASE_URL", "").strip()


def ping_database(url: str | None = None) -> bool:
    db_url = (url if url is not None else _get_database_url()).strip()
    if not db_url:
        return False
    if db_url.startswith(("postgres://", "postgresql://")):
        try:
            import psycopg
            with psycopg.connect(db_url, connect_timeout=2) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    return cur.fetchone() == (1,)
        except Exception:
            return False
    if db_url.startswith("sqlite:///"):
        path = db_url[len("sqlite:///"):]
        try:
            conn = sqlite3.connect(path, timeout=5.0)
            try:
                res = conn.execute("SELECT 1").fetchone()
                return res == (1,)
            finally:
                conn.close()
        except Exception:
            return False
    return False


_ADDITIVE_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "subscriber_contacts": [
        ("status", "TEXT NOT NULL DEFAULT 'active'"),
        ("plan_name", "TEXT NOT NULL DEFAULT ''"),
        ("fee_minor", "BIGINT NOT NULL DEFAULT 0"),
        ("currency", "TEXT NOT NULL DEFAULT 'USD'"),
        ("next_due_date", "TEXT"),
        ("version", "INTEGER NOT NULL DEFAULT 1"),
        ("updated_at", "TEXT"),
    ],
    "external_usage_records": [
        ("cache_read_tokens", "BIGINT"),
        ("cache_creation_tokens", "BIGINT"),
        ("billing_lane", "TEXT NOT NULL DEFAULT ''"),
        ("application", "TEXT NOT NULL DEFAULT ''"),
        ("images_generated", "BIGINT"),
        ("images_uploaded", "BIGINT"),
        ("document_reads", "BIGINT"),
        ("documents_generated", "BIGINT"),
        ("source_code_reads", "BIGINT"),
        ("request_ref", "TEXT"),
        ("session_id", "TEXT"),
    ],
    "personal_subscriptions": [
        ("service_url", "TEXT NOT NULL DEFAULT ''"),
        ("start_date", "TEXT"),
        ("timezone", "TEXT NOT NULL DEFAULT 'UTC'"),
        ("notes", "TEXT NOT NULL DEFAULT ''"),
        ("recipient_email", "TEXT"),
        ("tracking_status", "TEXT NOT NULL DEFAULT 'tracking_active'"),
        ("version", "INTEGER NOT NULL DEFAULT 1"),
    ],
    "reports": [
        ("kind", "TEXT NOT NULL DEFAULT ''"),
        ("period_start", "TEXT"),
        ("period_end", "TEXT"),
        ("filters_json", "TEXT NOT NULL DEFAULT '{}'"),
    ],
    "notification_preferences": [
        ("email_enabled", "INTEGER NOT NULL DEFAULT 1"),
    ],
}


def _apply_additive_migrations(executor: Any, driver: str) -> None:
    """Safely add v4.1 columns to pre-existing v4.0 databases without losing data."""
    for table, cols in _ADDITIVE_COLUMNS.items():
        if driver == "sqlite":
            existing = {r[1] for r in executor.execute(f"PRAGMA table_info({table})").fetchall()}
        else:
            executor.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = %s
                """,
                (table,),
            )
            existing = {r[0] for r in executor.fetchall()}
        for col_name, col_def in cols:
            if col_name not in existing:
                executor.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}")
    if driver != "sqlite":
        executor.execute(
            "ALTER TABLE personal_subscriptions DROP CONSTRAINT IF EXISTS personal_subscriptions_billing_interval_check"
        )


def init_schema(url: str | None = None) -> None:
    db_url = (url or _get_database_url()).strip()
    if not db_url:
        raise RuntimeError("DATABASE_URL is not configured")
    sql_text = _SCHEMA_PATH.read_text(encoding="utf-8")
    if db_url.startswith("sqlite:///"):
        path = db_url[len("sqlite:///"):]
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        with _SQLITE_WRITE_LOCK:
            conn = sqlite3.connect(path, timeout=30.0)
            try:
                conn.execute("PRAGMA foreign_keys = ON")
                conn.execute("PRAGMA journal_mode = WAL")
                conn.executescript(sql_text)
                _apply_additive_migrations(conn, "sqlite")
                conn.commit()
            finally:
                conn.close()
    elif db_url.startswith(("postgres://", "postgresql://")):
        import psycopg
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(987654321)")
                for stmt in sql_text.split(";"):
                    s = stmt.strip()
                    if s:
                        cur.execute(s)
                _apply_additive_migrations(cur, "postgres")
            conn.commit()
    else:
        raise RuntimeError(f"Unsupported DATABASE_URL scheme: {db_url}")


def drop_all_tables(url: str | None = None) -> None:
    db_url = (url or _get_database_url()).strip()
    tables = [
        "universal_connector_events",
        "calendar_event_overrides",
        "otel_metric_checkpoints",
        "activity_daily",
        "tracking_connections",
        "reminder_rules",
        "notification_preferences",
        "daily_finance_aggregates",
        "daily_usage_aggregates",
        "materialized_analytics_runs",
        "audit_logs",
        "outbox_deliveries",
        "outbox_events",
        "report_download_tokens",
        "reports",
        "webhook_endpoints",
        "sender_domains",
        "consents",
        "notification_attempts",
        "notification_jobs",
        "notification_rules",
        "pricing_versions",
        "personal_activity",
        "personal_monitoring_sources",
        "personal_subscriptions",
        "external_usage_records",
        "integration_connections",
        "usage_counters",
        "usage_events",
        "financial_adjustments",
        "expenses",
        "payments",
        "invoice_lines",
        "invoices",
        "financial_obligations",
        "employee_identity_mappings",
        "seat_allocations",
        "external_subscriptions",
        "subscription_periods",
        "subscriptions",
        "customer_links",
        "subscriber_contacts",
        "plan_metric_limits",
        "metrics",
        "plan_versions",
        "plans",
        "services",
        "api_keys",
        "sessions",
        "email_verifications",
        "invitations",
        "memberships",
        "organizations",
        "accounts",
        "users",
    ]
    if db_url.startswith("sqlite:///"):
        path = db_url[len("sqlite:///"):]
        with _SQLITE_WRITE_LOCK:
            conn = sqlite3.connect(path, timeout=30.0)
            try:
                conn.execute("PRAGMA foreign_keys = OFF")
                for t in tables:
                    conn.execute(f"DROP TABLE IF EXISTS {t}")
                conn.commit()
            finally:
                conn.close()
    elif db_url.startswith(("postgres://", "postgresql://")):
        import psycopg
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                for t in tables:
                    cur.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
            conn.commit()


@contextmanager
def get_db(write: bool = False) -> Iterator[DBConnection]:
    db_url = _get_database_url()
    if not db_url:
        raise RuntimeError("DATABASE_URL is not configured")
    if db_url.startswith("sqlite:///"):
        path = db_url[len("sqlite:///"):]
        if write:
            _SQLITE_WRITE_LOCK.acquire()
        conn = sqlite3.connect(path, timeout=30.0, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        db = DBConnection(conn, "sqlite")
        try:
            if write:
                conn.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                conn.execute("COMMIT")
        except Exception:
            if write:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
            raise
        finally:
            conn.close()
            if write:
                _SQLITE_WRITE_LOCK.release()
    elif db_url.startswith(("postgres://", "postgresql://")):
        import psycopg
        conn = psycopg.connect(db_url)
        db = DBConnection(conn, "postgres")
        try:
            yield db
            if write:
                conn.commit()
        except Exception:
            if write:
                conn.rollback()
            raise
        finally:
            conn.close()
    else:
        raise RuntimeError(f"Unsupported DATABASE_URL: {db_url}")

"""Sandboxed WeasyPrint/PyCairo/Pure-Python PDF Report Worker."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.db.engine import DBConnection, get_db, init_schema  # noqa: E402
from app.reports.router import render_report_job  # noqa: E402


def process_pending_reports(db: DBConnection) -> int:
    pending = db.fetchall("SELECT id FROM reports WHERE status = 'queued' ORDER BY created_at")
    processed = 0
    for row in pending:
        render_report_job(db, row["id"])
        processed += 1
    return processed


def main() -> None:
    init_schema()
    interval = int(os.getenv("REPORT_WORKER_POLL_SECONDS", "5"))
    once = os.getenv("REPORT_WORKER_RUN_ONCE", "false").lower() == "true"
    while True:
        with get_db(write=True) as db:
            count = process_pending_reports(db)
        print(f"[report-worker] processed={count}")
        if once:
            break
        time.sleep(interval)


if __name__ == "__main__":
    main()

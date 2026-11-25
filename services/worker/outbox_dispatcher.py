"""SubSphere Outbox Dispatcher Worker (Kafka + OpenMeter CloudEvents + periodic notifications)."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Allow standalone execution with backend modules on sys.path
_BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.analytics.medallion import MedallionPipeline  # noqa: E402
from app.analytics.outbox import OutboxDispatcher  # noqa: E402
from app.db.engine import get_db, init_schema  # noqa: E402
from app.notifications.dispatcher import dispatch_email_jobs  # noqa: E402
from app.personal.service import schedule_personal_renewal_reminders  # noqa: E402


def run_once(materialize_medallion: bool = True) -> dict[str, object]:
    dispatcher = OutboxDispatcher()
    with get_db(write=True) as db:
        reminders_res = schedule_personal_renewal_reminders(db)
        email_res = dispatch_email_jobs(db)
        outbox_res = dispatcher.dispatch_pending(db)
        medallion_res = None
        if materialize_medallion:
            medallion_res = MedallionPipeline().run_silver_and_gold(db)
    return {
        "outbox": outbox_res,
        "medallion": medallion_res,
        "reminders": reminders_res,
        "email_dispatch": email_res,
    }


def main() -> None:
    init_schema()
    interval = int(os.getenv("WORKER_POLL_SECONDS", "5"))
    once = os.getenv("WORKER_RUN_ONCE", "false").lower() == "true"
    while True:
        res = run_once(materialize_medallion=True)
        print(
            f"[worker] dispatched={res['outbox']['processed']} "
            f"silver={res['medallion']['silver_count'] if res['medallion'] else 0}"
        )
        if once:
            break
        time.sleep(interval)


if __name__ == "__main__":
    main()

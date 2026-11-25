"""Audit logging helper for SubSphere 4.0."""
from __future__ import annotations

import json
import uuid
from typing import Any

from app.core.clock import get_clock
from app.db.engine import DBConnection


def record_audit_log(
    db: DBConnection,
    *,
    actor_user_id: str | None,
    tenant_id: str | None,
    action: str,
    resource_type: str,
    resource_id: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    audit_id = str(uuid.uuid4())
    db.execute(
        """
        INSERT INTO audit_logs (id, actor_user_id, tenant_id, action, resource_type, resource_id, metadata_json, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            audit_id,
            actor_user_id,
            tenant_id,
            action,
            resource_type,
            resource_id,
            json.dumps(metadata or {}, default=str),
            get_clock().now_iso(),
        ),
    )
    return audit_id

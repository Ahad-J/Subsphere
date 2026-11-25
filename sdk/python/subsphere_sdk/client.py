"""SubSphere 4.0 Python Developer SDK for metering and advisory entitlement checks."""
from __future__ import annotations

import uuid
from typing import Any


class SubSphereQuotaError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.details = details or {}


class SubSphereClient:
    def __init__(self, api_key: str, base_url: str = "http://localhost:8000", http_client: Any = None):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        if http_client is not None:
            self._http = http_client
        else:
            import httpx
            self._http = httpx.Client(base_url=self.base_url)

    def consume_usage(
        self,
        *,
        subscription_id: str,
        metric_key: str,
        quantity: int = 1,
        external_event_id: str | None = None,
        occurred_at: str | None = None,
        seat_allocation_id: str | None = None,
    ) -> dict[str, Any]:
        evt_id = external_event_id or f"sdk_{uuid.uuid4().hex}"
        payload: dict[str, Any] = {
            "external_event_id": evt_id,
            "subscription_id": subscription_id,
            "metric_key": metric_key,
            "quantity": quantity,
            "source": "PROVIDER_SDK",
        }
        if occurred_at:
            payload["occurred_at"] = occurred_at
        if seat_allocation_id:
            payload["seat_allocation_id"] = seat_allocation_id

        res = self._http.post(
            "/api/v1/usage/consume",
            headers={"X-API-Key": self.api_key, "Idempotency-Key": evt_id},
            json=payload,
        )
        data = res.json()
        if res.status_code >= 400:
            raise SubSphereQuotaError(
                res.status_code,
                data.get("code", "API_ERROR"),
                data.get("message", "Usage consume failed"),
                data.get("details"),
            )
        return data

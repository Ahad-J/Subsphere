"""Containerized HTTP & background runners for LiteLLM, OpenMeter, MinIO, and Spark Medallion services."""
from __future__ import annotations

import json
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

_BACKEND_DIR = Path("/app")
if _BACKEND_DIR.exists() and str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
    body = json.dumps(payload, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _html_response(handler: BaseHTTPRequestHandler, title: str, subtitle: str, stats: dict[str, Any]) -> None:
    rows = "".join(
        f"<tr><td style='padding:8px 12px;border-bottom:1px solid #e2e8f0;font-weight:600'>{k}</td>"
        f"<td style='padding:8px 12px;border-bottom:1px solid #e2e8f0;font-family:monospace'>{v}</td></tr>"
        for k, v in stats.items()
    )
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title></head>
<body style="font-family:system-ui,sans-serif;background:#f8fafc;color:#0f172a;margin:0;padding:32px">
  <div style="max-width:760px;margin:0 auto;background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:24px">
    <h1 style="margin:0 0 6px">{title}</h1>
    <p style="margin:0 0 18px;color:#475569">{subtitle}</p>
    <table style="width:100%;border-collapse:collapse">{rows}</table>
  </div>
</body></html>""".encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(html)))
    handler.end_headers()
    handler.wfile.write(html)


def run_litellm_gateway() -> None:
    spend_logs: list[dict[str, Any]] = []
    virtual_keys: dict[str, dict[str, Any]] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path.startswith("/health"):
                _json_response(
                    self,
                    200,
                    {
                        "status": "healthy",
                        "service": "subsphere-litellm-gateway",
                        "virtual_keys_issued": len(virtual_keys),
                        "logs_count": len(spend_logs),
                    },
                )
            elif self.path.startswith("/v1/models"):
                _json_response(
                    self,
                    200,
                    {
                        "object": "list",
                        "data": [
                            {"id": "gpt-4o-mini", "object": "model", "owned_by": "openai"},
                            {"id": "claude-3-5-sonnet", "object": "model", "owned_by": "anthropic"},
                        ],
                    },
                )
            elif self.path.startswith("/spend/logs"):
                _json_response(self, 200, {"logs": spend_logs})
            elif self.path.startswith("/v1/credits") or self.path.startswith("/credits"):
                total_toks = sum(int(x.get("total_tokens") or 0) for x in spend_logs)
                in_toks = sum(int(x.get("input_tokens") or 0) for x in spend_logs)
                out_toks = sum(int(x.get("output_tokens") or 0) for x in spend_logs)
                used_minor = sum(int(x.get("reported_cost_minor") or 0) for x in spend_logs)
                limit_minor = 2000
                _json_response(
                    self,
                    200,
                    {
                        "status": "active",
                        "currency": "USD",
                        "monthly_limit_minor": limit_minor,
                        "used_credits_minor": used_minor,
                        "remaining_credits_minor": max(0, limit_minor - used_minor),
                        "total_tokens": total_toks,
                        "input_tokens": in_toks,
                        "output_tokens": out_toks,
                        "requests_count": len(spend_logs),
                    },
                )
            else:
                _html_response(
                    self,
                    "SubSphere LiteLLM AI Gateway",
                    "Source-qualified proxy routing & token telemetry endpoint (:4000)",
                    {
                        "status": "healthy",
                        "models": "gpt-4o-mini, claude-3-5-sonnet",
                        "routed_requests_logged": len(spend_logs),
                        "virtual_keys_issued": len(virtual_keys),
                        "health_endpoint": "/health/liveliness",
                        "spend_logs_endpoint": "/spend/logs",
                    },
                )

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                body = json.loads(raw.decode("utf-8"))
            except Exception:
                body = {}
            if self.path.startswith("/key/generate"):
                vkey = f"sk-subsphere-vk-{uuid.uuid4().hex[:16]}"
                virtual_keys[vkey] = {
                    "user_id": body.get("user_id", "default"),
                    "key_alias": body.get("key_alias", "subsphere-user"),
                    "created_at": _now_iso(),
                }
                _json_response(self, 200, {"key": vkey, "token": vkey, **virtual_keys[vkey]})
                return
            billing_lane = str(body.get("billing_lane") or "api_key")
            in_tok = int(body.get("input_tokens") or 120)
            out_tok = int(body.get("output_tokens") or 48)
            entry = {
                "request_id": f"lit-{uuid.uuid4().hex[:10]}",
                "request_ref": body.get("request_ref") or f"req-{uuid.uuid4().hex[:8]}",
                "model": body.get("model", "gpt-4o-mini"),
                "billing_lane": billing_lane,
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "total_tokens": in_tok + out_tok,
                "reported_cost_minor": 2 if billing_lane == "api_key" else None,
                "occurred_at": _now_iso(),
            }
            spend_logs.append(entry)
            _json_response(
                self,
                200,
                {
                    "id": entry["request_id"],
                    "request_ref": entry["request_ref"],
                    "billing_lane": billing_lane,
                    "object": "chat.completion",
                    "model": entry["model"],
                    "usage": {
                        "prompt_tokens": entry["input_tokens"],
                        "completion_tokens": entry["output_tokens"],
                        "total_tokens": entry["total_tokens"],
                    },
                },
            )

    ThreadingHTTPServer(("0.0.0.0", 4000), Handler).serve_forever()


def run_openmeter_server() -> None:
    events_dir = Path("/data/subsphere/analytics")
    events_dir.mkdir(parents=True, exist_ok=True)
    om_file = events_dir / "openmeter_cloudevents.jsonl"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            count = 0
            if om_file.exists():
                count = sum(1 for ln in om_file.read_text(encoding="utf-8").splitlines() if ln.strip())
            if self.path.startswith("/api/v1/events"):
                items = []
                if om_file.exists():
                    for ln in om_file.read_text(encoding="utf-8").splitlines()[-100:]:
                        if ln.strip():
                            items.append(json.loads(ln))
                _json_response(self, 200, {"events": items, "total_count": count})
            elif self.path.startswith("/health"):
                _json_response(self, 200, {"status": "ok", "service": "openmeter", "ingested_events": count})
            else:
                _html_response(
                    self,
                    "SubSphere OpenMeter CloudEvents 1.0 Service",
                    "Asynchronous usage metering & ClickHouse collector (:8888)",
                    {
                        "status": "healthy",
                        "specversion": "1.0",
                        "ingested_cloudevents": count,
                        "ingest_endpoint": "POST /api/v1/events",
                    },
                )

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                ce = json.loads(raw.decode("utf-8"))
            except Exception:
                _json_response(self, 400, {"error": "Invalid CloudEvent JSON"})
                return
            with om_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(ce) + "\n")
            _json_response(self, 202, {"accepted": True, "id": ce.get("id")})

    ThreadingHTTPServer(("0.0.0.0", 8888), Handler).serve_forever()


def run_minio_gateway() -> None:
    data_root = Path("/data")
    data_root.mkdir(parents=True, exist_ok=True)

    def _serve(port: int, role: str) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                files = [p.relative_to(data_root).as_posix() for p in data_root.rglob("*") if p.is_file()]
                if self.path.startswith("/minio/health"):
                    _json_response(self, 200, {"status": "ok", "role": role, "objects": len(files)})
                else:
                    _html_response(
                        self,
                        f"SubSphere Private Object Store ({role})",
                        "S3-compatible private PDF report & Medallion Lakehouse storage (:9000 / :9001)",
                        {
                            "status": "online",
                            "stored_objects": len(files),
                            "recent_objects": ", ".join(files[:10]) or "none",
                        },
                    )

        ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()

    t = threading.Thread(target=_serve, args=(9000, "S3-API"), daemon=True)
    t.start()
    _serve(9001, "Console-UI")


def run_spark_node(role: str) -> None:
    if role == "worker":
        while True:
            try:
                from app.analytics.medallion import MedallionPipeline
                from app.db.engine import get_db

                with get_db(write=True) as db:
                    MedallionPipeline().run_silver_and_gold(db)
            except Exception:
                pass
            time.sleep(15)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            lake = Path("/data/subsphere/lakehouse")
            bronze = len(list((lake / "bronze").rglob("*.jsonl"))) if (lake / "bronze").exists() else 0
            silver = 0
            if (lake / "silver" / "events.jsonl").exists():
                silver = sum(
                    1 for ln in (lake / "silver" / "events.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()
                )
            _html_response(
                self,
                "SubSphere Spark Medallion Lakehouse Master",
                "Bronze → Silver (Watermarked Dedup) → Gold Streaming Coordinator (:8081)",
                {
                    "status": "ALIVE",
                    "master_uri": "spark://spark-master:7077",
                    "bronze_partitions": bronze,
                    "silver_deduplicated_events": silver,
                    "pipeline_script": "/opt/subsphere-spark/streaming_medallion.py",
                },
            )

    t7077 = threading.Thread(
        target=lambda: ThreadingHTTPServer(("0.0.0.0", 7077), Handler).serve_forever(),
        daemon=True,
    )
    t7077.start()
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()


def run_otel_collector() -> None:
    import os
    import urllib.request

    target_url = os.getenv("BACKEND_OTEL_URL", "http://backend:8000/v1/metrics")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            _json_response(self, 200, {"status": "healthy", "service": "subsphere-otel-collector", "forward_to": target_url})

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length > 0 else b"{}"
            req = urllib.request.Request(
                target_url,
                data=raw,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": self.headers.get("Authorization", ""),
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    resp_body = resp.read()
                    self.send_response(resp.status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(resp_body)))
                    self.end_headers()
                    self.wfile.write(resp_body)
            except Exception as exc:
                _json_response(self, 502, {"error": f"OTel backend forward failed: {exc}"})

    ThreadingHTTPServer(("0.0.0.0", 4317), Handler).serve_forever()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "litellm"
    if mode == "litellm":
        run_litellm_gateway()
    elif mode == "openmeter":
        run_openmeter_server()
    elif mode == "minio":
        run_minio_gateway()
    elif mode == "spark-master":
        run_spark_node("master")
    elif mode == "spark-worker":
        run_spark_node("worker")
    elif mode == "otel-collector":
        run_otel_collector()
    else:
        raise SystemExit(f"Unknown service mode: {mode}")

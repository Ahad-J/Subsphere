"""Template renderer for the downloadable SubSphere MCP Server Python bridge."""
from __future__ import annotations

import json
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parents[3] / "services" / "mcp-server" / "subsphere_mcp_server.py"

_FALLBACK_TEMPLATE = '''#!/usr/bin/env python3
"""SubSphere Model Context Protocol (MCP) Server (stdio JSON-RPC 2.0 + CLI reporter)."""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

DEFAULT_API_URL = "http://127.0.0.1:8080"
DEFAULT_TOKEN = ""


def _post_mcp(api_url: str, token: str, payload: dict[str, Any]) -> tuple[int, str]:
    endpoint = f"{api_url.rstrip('/')}/api/v1/mcp"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def _run_stdio_loop(api_url: str, token: str) -> int:
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            sys.stdout.write(
                json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"Parse error: {exc}"}}) + "\\n"
            )
            sys.stdout.flush()
            continue

        rpc_id = msg.get("id") if isinstance(msg, dict) else None
        is_request = isinstance(msg, dict) and ("id" in msg and rpc_id is not None)
        try:
            status, resp_text = _post_mcp(api_url, token, msg)
        except Exception as exc:
            if is_request:
                sys.stdout.write(
                    json.dumps({"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32000, "message": f"SubSphere MCP error: {exc}"}}) + "\\n"
                )
                sys.stdout.flush()
            continue

        if is_request:
            if status >= 400:
                try:
                    err_data = json.loads(resp_text)
                except Exception:
                    err_data = {"message": resp_text}
                out = err_data if "jsonrpc" in err_data else {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "error": {"code": -32001, "message": err_data.get("message") or f"HTTP {status}", "data": err_data},
                }
                sys.stdout.write(json.dumps(out) + "\\n")
            elif resp_text.strip():
                sys.stdout.write(resp_text.strip() + "\\n")
            else:
                sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rpc_id, "result": {}}) + "\\n")
            sys.stdout.flush()
    return 0


def _run_report_usage(args: argparse.Namespace, api_url: str, token: str) -> int:
    rpc_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "subsphere_report_usage",
            "arguments": {
                "model_name": args.model,
                "agent_name": args.agent,
                "input_tokens": args.input_tokens,
                "output_tokens": args.output_tokens,
                "requests_count": args.requests,
                "billing_lane": args.lane,
                "reported_cost_minor": args.cost_minor,
                "session_id": args.session_id,
                "request_ref": args.request_ref,
            },
        },
    }
    status, resp_text = _post_mcp(api_url, token, rpc_payload)
    sys.stdout.write(resp_text + ("\\n" if not resp_text.endswith("\\n") else ""))
    return 0 if status < 400 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="SubSphere MCP Server (stdio bridge & CLI usage reporter)")
    parser.add_argument("--api-url", default=os.environ.get("SUBSPHERE_API_URL", DEFAULT_API_URL))
    parser.add_argument("--token", default=os.environ.get("SUBSPHERE_MCP_TOKEN", DEFAULT_TOKEN))
    parser.add_argument("--report-usage", action="store_true", help="Report one AI usage event and exit")
    parser.add_argument("--model", default="claude-3-5-sonnet")
    parser.add_argument("--agent", default="cli-agent")
    parser.add_argument("--input-tokens", type=int, default=0)
    parser.add_argument("--output-tokens", type=int, default=0)
    parser.add_argument("--requests", type=int, default=1)
    parser.add_argument("--lane", default="claude_subscription", choices=["claude_subscription", "api_key"])
    parser.add_argument("--cost-minor", type=int, default=None)
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--request-ref", default=None)
    args = parser.parse_args()

    if args.report_usage:
        return _run_report_usage(args, args.api_url, args.token)
    return _run_stdio_loop(args.api_url, args.token)


if __name__ == "__main__":
    raise SystemExit(main())
'''


def render_mcp_server_script(token: str = "", api_url: str = "http://127.0.0.1:8080") -> str:
    if _SCRIPT_PATH.exists():
        content = _SCRIPT_PATH.read_text(encoding="utf-8")
    else:
        content = _FALLBACK_TEMPLATE
    content = content.replace('DEFAULT_API_URL = "http://127.0.0.1:8080"', f"DEFAULT_API_URL = {json.dumps(api_url)}")
    if token:
        content = content.replace('DEFAULT_TOKEN = ""', f"DEFAULT_TOKEN = {json.dumps(token)}")
    return content

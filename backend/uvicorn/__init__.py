"""Self-contained HTTP server runner compatible with `uvicorn app.main:app`."""
from __future__ import annotations

import importlib
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


def run(app: str | Any, host: str = "127.0.0.1", port: int = 8000, **kwargs: Any) -> None:
    if isinstance(app, str):
        mod_name, attr_name = app.split(":", 1)
        mod = importlib.import_module(mod_name)
        asgi_app = getattr(mod, attr_name)
    else:
        asgi_app = app

    class Handler(BaseHTTPRequestHandler):
        def _dispatch(self, method: str) -> None:
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(length) if length > 0 else b""
            cookies: dict[str, str] = {}
            cookie_hdr = self.headers.get("Cookie", "")
            for part in cookie_hdr.split(";"):
                if "=" in part:
                    k, v = part.strip().split("=", 1)
                    cookies[k] = v
            res = asgi_app.handle_request(
                method=method,
                raw_url=self.path,
                headers=dict(self.headers),
                cookies=cookies,
                body=body,
                client_host=self.client_address[0],
            )
            self.send_response(res.status_code)
            existing_lower = {k.lower() for k in res.headers}
            for k, v in res.headers.items():
                self.send_header(k, v)
            if "access-control-allow-origin" not in existing_lower:
                self.send_header("Access-Control-Allow-Origin", "*")
            if "access-control-expose-headers" not in existing_lower:
                self.send_header("Access-Control-Expose-Headers", "Mcp-Session-Id")
            if "access-control-allow-private-network" not in existing_lower:
                self.send_header("Access-Control-Allow-Private-Network", "true")
            for ck, meta in getattr(res, "_cookies", {}).items():
                cookie_str = f"{ck}={meta.get('value', '')}; Path={meta.get('path', '/')}"
                if meta.get("httponly"):
                    cookie_str += "; HttpOnly"
                if meta.get("samesite"):
                    cookie_str += f"; SameSite={meta['samesite']}"
                self.send_header("Set-Cookie", cookie_str)
            self.end_headers()
            self.wfile.write(res.body)

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_PUT(self):
            self._dispatch("PUT")

        def do_PATCH(self):
            self._dispatch("PATCH")

        def do_DELETE(self):
            self._dispatch("DELETE")

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
            self.send_header(
                "Access-Control-Allow-Headers",
                "Content-Type, Authorization, X-CSRF-Token, Idempotency-Key, X-API-Key, X-SubSphere-Mcp-Token, Mcp-Session-Id, Accept",
            )
            self.send_header("Access-Control-Expose-Headers", "Mcp-Session-Id")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.end_headers()

    server = ThreadingHTTPServer((host, port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "app.main:app"
    host = "0.0.0.0"
    port = 8000
    for i, arg in enumerate(sys.argv):
        if arg == "--host" and i + 1 < len(sys.argv):
            host = sys.argv[i + 1]
        elif arg == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])
    run(target, host=host, port=port)

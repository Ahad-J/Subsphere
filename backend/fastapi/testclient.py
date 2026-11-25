"""FastAPI TestClient with cookie persistence."""
from __future__ import annotations

import json as _json
from typing import Any
from urllib.parse import urlencode

from httpx import Response as HttpxResponse


class TestClient:
    def __init__(self, app: Any, base_url: str = "http://testserver"):
        self.app = app
        self.base_url = base_url
        self.cookies: dict[str, str] = {}

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        json: Any = None,
        data: Any = None,
    ) -> HttpxResponse:
        target = url
        if params:
            sep = "&" if "?" in target else "?"
            target = f"{target}{sep}{urlencode(params, doseq=True)}"
        req_headers = dict(headers or {})
        body = b""
        if json is not None:
            body = _json.dumps(json, default=str).encode("utf-8")
            req_headers.setdefault("Content-Type", "application/json")
        elif data is not None:
            body = data.encode("utf-8") if isinstance(data, str) else data

        merged_cookies = dict(self.cookies)
        if cookies:
            merged_cookies.update(cookies)

        res = self.app.handle_request(
            method=method,
            raw_url=target,
            headers=req_headers,
            cookies=merged_cookies,
            body=body,
        )
        for ck, meta in getattr(res, "_cookies", {}).items():
            if meta.get("max_age") == 0:
                self.cookies.pop(ck, None)
            else:
                self.cookies[ck] = meta.get("value", "")

        return HttpxResponse(
            status_code=res.status_code,
            content=res.body,
            headers=res.headers,
            cookies=dict(self.cookies),
        )

    def get(self, url: str, **kwargs: Any) -> HttpxResponse:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> HttpxResponse:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> HttpxResponse:
        return self.request("PUT", url, **kwargs)

    def patch(self, url: str, **kwargs: Any) -> HttpxResponse:
        return self.request("PATCH", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> HttpxResponse:
        return self.request("DELETE", url, **kwargs)

    def __enter__(self) -> TestClient:
        return self

    def __exit__(self, *args: Any) -> None:
        return None

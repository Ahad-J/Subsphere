"""Self-contained httpx-compatible client primitives for TestClient and adapters."""
from __future__ import annotations

import json as _json
import urllib.error
import urllib.request
from typing import Any


class Headers(dict):
    def get(self, key: str, default: Any = None) -> Any:
        kl = key.lower()
        for k, v in self.items():
            if k.lower() == kl:
                return v
        return default

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        kl = key.lower()
        return any(k.lower() == kl for k in self.keys())


class Response:
    def __init__(
        self,
        status_code: int = 200,
        content: bytes = b"",
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
    ):
        self.status_code = status_code
        self.content = content
        self.headers = Headers(headers or {})
        self.cookies = cookies or {}

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return _json.loads(self.text)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise HTTPStatusError(f"HTTP {self.status_code}", response=self)


class HTTPStatusError(Exception):
    def __init__(self, message: str, response: Response):
        super().__init__(message)
        self.response = response


class Client:
    def __init__(self, base_url: str = "", timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request(self, method: str, url: str, **kwargs: Any) -> Response:
        full_url = url if url.startswith("http") else f"{self.base_url}/{url.lstrip('/')}"
        headers = dict(kwargs.get("headers") or {})
        body: bytes | None = None
        if "json" in kwargs and kwargs["json"] is not None:
            body = _json.dumps(kwargs["json"]).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        elif "data" in kwargs and kwargs["data"] is not None:
            data = kwargs["data"]
            body = data.encode("utf-8") if isinstance(data, str) else data
        req = urllib.request.Request(full_url, data=body, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return Response(
                    status_code=resp.status,
                    content=resp.read(),
                    headers=dict(resp.headers),
                )
        except urllib.error.HTTPError as exc:
            return Response(
                status_code=exc.code,
                content=exc.read(),
                headers=dict(exc.headers or {}),
            )

    def get(self, url: str, **kwargs: Any) -> Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> Response:
        return self.request("POST", url, **kwargs)

    def patch(self, url: str, **kwargs: Any) -> Response:
        return self.request("PATCH", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> Response:
        return self.request("PUT", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> Response:
        return self.request("DELETE", url, **kwargs)

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *args: Any) -> None:
        return None

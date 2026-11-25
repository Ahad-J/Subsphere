"""FastAPI-compatible HTTP responses."""
from __future__ import annotations

import json
from typing import Any


class Response:
    def __init__(
        self,
        content: bytes | str = b"",
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        media_type: str | None = None,
    ):
        self.body = content.encode("utf-8") if isinstance(content, str) else content
        self.status_code = status_code
        self.headers = dict(headers or {})
        self.media_type = media_type
        if media_type:
            self.headers.setdefault("content-type", media_type)
        self._cookies: dict[str, dict[str, Any]] = {}

    def set_cookie(
        self,
        key: str,
        value: str = "",
        max_age: int | None = None,
        httponly: bool = False,
        secure: bool = False,
        samesite: str | None = "lax",
        path: str = "/",
    ) -> None:
        self._cookies[key] = {
            "value": value,
            "max_age": max_age,
            "httponly": httponly,
            "secure": secure,
            "samesite": samesite,
            "path": path,
        }

    def delete_cookie(self, key: str, path: str = "/") -> None:
        self.set_cookie(key, "", max_age=0, path=path)


class JSONResponse(Response):
    def __init__(
        self,
        content: Any,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ):
        raw = json.dumps(content, default=str).encode("utf-8")
        super().__init__(content=raw, status_code=status_code, headers=headers, media_type="application/json")


class PlainTextResponse(Response):
    def __init__(self, content: str, status_code: int = 200, headers: dict[str, str] | None = None):
        super().__init__(content=content, status_code=status_code, headers=headers, media_type="text/plain; charset=utf-8")

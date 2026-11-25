"""Self-contained FastAPI-compatible ASGI/WSGI web framework for SubSphere."""
from __future__ import annotations

import asyncio
import inspect
import json
import re
import types
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Union, get_args, get_origin, get_type_hints
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel, ValidationError
from fastapi.responses import JSONResponse, Response


class HTTPException(Exception):
    def __init__(
        self,
        status_code: int,
        detail: Any = None,
        headers: dict[str, str] | None = None,
    ):
        self.status_code = status_code
        self.detail = detail
        self.headers = headers or {}
        super().__init__(str(detail))


@dataclass
class _Depends:
    dependency: Callable[..., Any] | None = None


def Depends(dependency: Callable[..., Any] | None = None) -> Any:
    return _Depends(dependency=dependency)


@dataclass
class _ParamMarker:
    kind: str
    default: Any = ...
    alias: str | None = None


def Header(default: Any = ..., *, alias: str | None = None) -> Any:
    return _ParamMarker("header", default=default, alias=alias)


def Query(default: Any = ..., *, alias: str | None = None) -> Any:
    return _ParamMarker("query", default=default, alias=alias)


def Cookie(default: Any = ..., *, alias: str | None = None) -> Any:
    return _ParamMarker("cookie", default=default, alias=alias)


def Body(default: Any = ...) -> Any:
    return _ParamMarker("body", default=default)


class _Undefined:
    pass


_UNDEFINED = _Undefined()


class _Headers(dict):
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


class _State:
    pass


class Request:
    def __init__(
        self,
        method: str,
        path: str,
        query_params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        body: bytes = b"",
        client_host: str = "127.0.0.1",
    ):
        self.method = method.upper()
        self.url = urlparse(path)
        self.query_params = query_params or {}
        self.headers = _Headers({k.lower(): v for k, v in (headers or {}).items()})
        self.cookies = dict(cookies or {})
        self._body = body
        self.client = type("ClientAddr", (), {"host": client_host})()
        self.state = _State()

    async def body(self) -> bytes:
        return self._body

    async def json(self) -> Any:
        if not self._body:
            return None
        return json.loads(self._body.decode("utf-8"))


@dataclass
class Route:
    method: str
    path: str
    endpoint: Callable[..., Any]
    status_code: int | None
    tags: list[str]
    summary: str | None
    pattern: re.Pattern[str]
    param_names: list[str]


def _compile_path(path: str) -> tuple[re.Pattern[str], list[str]]:
    param_names: list[str] = []
    parts = []
    pos = 0
    for m in re.finditer(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", path):
        parts.append(re.escape(path[pos : m.start()]))
        name = m.group(1)
        param_names.append(name)
        parts.append(f"(?P<{name}>[^/]+)")
        pos = m.end()
    parts.append(re.escape(path[pos:]))
    regex = "^" + "".join(parts) + "$"
    return re.compile(regex), param_names


class APIRouter:
    def __init__(self, prefix: str = "", tags: list[str] | None = None):
        self.prefix = prefix.rstrip("/")
        self.tags = tags or []
        self.routes: list[Route] = []

    def add_api_route(
        self,
        path: str,
        endpoint: Callable[..., Any],
        methods: list[str],
        status_code: int | None = None,
        tags: list[str] | None = None,
        summary: str | None = None,
    ) -> None:
        if not path:
            full_path = self.prefix or "/"
        elif path.startswith("/"):
            full_path = f"{self.prefix}{path}"
        else:
            full_path = f"{self.prefix}/{path}"
        pattern, param_names = _compile_path(full_path)
        for method in methods:
            self.routes.append(
                Route(
                    method=method.upper(),
                    path=full_path,
                    endpoint=endpoint,
                    status_code=status_code,
                    tags=tags or list(self.tags),
                    summary=summary,
                    pattern=pattern,
                    param_names=param_names,
                )
            )

    def get(self, path: str, **kwargs: Any):
        def deco(fn: Callable[..., Any]):
            self.add_api_route(path, fn, ["GET"], **kwargs)
            return fn
        return deco

    def post(self, path: str, **kwargs: Any):
        def deco(fn: Callable[..., Any]):
            self.add_api_route(path, fn, ["POST"], **kwargs)
            return fn
        return deco

    def put(self, path: str, **kwargs: Any):
        def deco(fn: Callable[..., Any]):
            self.add_api_route(path, fn, ["PUT"], **kwargs)
            return fn
        return deco

    def patch(self, path: str, **kwargs: Any):
        def deco(fn: Callable[..., Any]):
            self.add_api_route(path, fn, ["PATCH"], **kwargs)
            return fn
        return deco

    def delete(self, path: str, **kwargs: Any):
        def deco(fn: Callable[..., Any]):
            self.add_api_route(path, fn, ["DELETE"], **kwargs)
            return fn
        return deco


class FastAPI:
    def __init__(
        self,
        title: str = "FastAPI",
        version: str = "0.1.0",
        description: str = "",
        docs_url: str | None = "/docs",
        openapi_url: str | None = "/openapi.json",
    ):
        self.title = title
        self.version = version
        self.description = description
        self.docs_url = docs_url
        self.openapi_url = openapi_url
        self.routes: list[Route] = []
        self.dependency_overrides: dict[Callable[..., Any], Callable[..., Any]] = {}
        self.exception_handlers: dict[Any, Callable[..., Any]] = {}
        self.middlewares: list[tuple[Any, dict[str, Any]]] = []
        self._startup_handlers: list[Callable[..., Any]] = []

    def add_middleware(self, middleware_cls: Any, **options: Any) -> None:
        self.middlewares.append((middleware_cls, options))

    def on_event(self, event_type: str):
        def deco(fn: Callable[..., Any]):
            if event_type == "startup":
                self._startup_handlers.append(fn)
            return fn
        return deco

    def exception_handler(self, exc_class: Any):
        def deco(fn: Callable[..., Any]):
            self.exception_handlers[exc_class] = fn
            return fn
        return deco

    def include_router(self, router: APIRouter, prefix: str = "", tags: list[str] | None = None) -> None:
        base_prefix = prefix.rstrip("/")
        for r in router.routes:
            full_path = f"{base_prefix}{r.path}" if base_prefix else r.path
            pattern, param_names = _compile_path(full_path)
            self.routes.append(
                Route(
                    method=r.method,
                    path=full_path,
                    endpoint=r.endpoint,
                    status_code=r.status_code,
                    tags=tags or r.tags,
                    summary=r.summary,
                    pattern=pattern,
                    param_names=param_names,
                )
            )

    def _add_route(self, method: str, path: str, **kwargs: Any):
        def deco(fn: Callable[..., Any]):
            pattern, param_names = _compile_path(path)
            self.routes.append(
                Route(
                    method=method,
                    path=path,
                    endpoint=fn,
                    status_code=kwargs.get("status_code"),
                    tags=kwargs.get("tags", []),
                    summary=kwargs.get("summary"),
                    pattern=pattern,
                    param_names=param_names,
                )
            )
            return fn
        return deco

    def get(self, path: str, **kwargs: Any):
        return self._add_route("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any):
        return self._add_route("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any):
        return self._add_route("PUT", path, **kwargs)

    def patch(self, path: str, **kwargs: Any):
        return self._add_route("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs: Any):
        return self._add_route("DELETE", path, **kwargs)

    def openapi(self) -> dict[str, Any]:
        paths: dict[str, dict[str, Any]] = {}
        for r in self.routes:
            p = paths.setdefault(r.path, {})
            p[r.method.lower()] = {
                "summary": r.summary or r.endpoint.__name__,
                "tags": r.tags,
                "responses": {"200": {"description": "Successful Response"}},
            }
        return {
            "openapi": "3.1.0",
            "info": {"title": self.title, "version": self.version, "description": self.description},
            "paths": paths,
        }

    def _match_route(self, method: str, path: str) -> tuple[Route | None, dict[str, str], bool]:
        path_matched = False
        for r in self.routes:
            m = r.pattern.match(path)
            if m:
                path_matched = True
                if r.method == method.upper():
                    return r, m.groupdict(), True
        return None, {}, path_matched

    def _call_callable(
        self,
        fn: Callable[..., Any],
        request: Request,
        response: Response,
        path_params: dict[str, str],
        body_data: Any,
        dep_cache: dict[Callable[..., Any], Any],
    ) -> Any:
        actual_fn = self.dependency_overrides.get(fn, fn)
        sig = inspect.signature(actual_fn)
        try:
            hints = get_type_hints(actual_fn)
        except Exception:
            hints = getattr(actual_fn, "__annotations__", {})

        kwargs: dict[str, Any] = {}
        for pname, param in sig.parameters.items():
            ann = hints.get(pname, param.annotation)
            default = param.default

            if isinstance(default, _Depends):
                dep_fn = default.dependency
                if dep_fn is None:
                    raise RuntimeError(f"Missing dependency for {pname}")
                if dep_fn not in dep_cache:
                    dep_cache[dep_fn] = self._call_callable(
                        dep_fn, request, response, path_params, body_data, dep_cache
                    )
                kwargs[pname] = dep_cache[dep_fn]
                continue

            if ann is Request or pname == "request":
                kwargs[pname] = request
                continue

            if ann is Response or pname == "response":
                kwargs[pname] = response
                continue

            if pname in path_params:
                raw = path_params[pname]
                if ann is int:
                    kwargs[pname] = int(raw)
                else:
                    kwargs[pname] = raw
                continue

            if isinstance(default, _ParamMarker):
                if default.kind == "header":
                    hname = (default.alias or pname.replace("_", "-")).lower()
                    val = request.headers.get(hname, _UNDEFINED)
                    if val is _UNDEFINED:
                        if default.default is ...:
                            raise HTTPException(status_code=422, detail=f"Missing required header: {hname}")
                        val = default.default
                    kwargs[pname] = val
                elif default.kind == "query":
                    qname = default.alias or pname
                    val = request.query_params.get(qname, _UNDEFINED)
                    if val is _UNDEFINED:
                        if default.default is ...:
                            raise HTTPException(status_code=422, detail=f"Missing required query parameter: {qname}")
                        val = default.default
                    elif ann is int:
                        val = int(val)
                    elif ann is bool:
                        val = str(val).lower() in ("true", "1", "yes")
                    kwargs[pname] = val
                elif default.kind == "cookie":
                    cname = default.alias or pname
                    val = request.cookies.get(cname, _UNDEFINED)
                    if val is _UNDEFINED:
                        if default.default is ...:
                            raise HTTPException(status_code=422, detail=f"Missing required cookie: {cname}")
                        val = default.default
                    kwargs[pname] = val
                elif default.kind == "body":
                    kwargs[pname] = body_data
                continue

            model_cls = None
            if isinstance(ann, type) and issubclass(ann, BaseModel):
                model_cls = ann
            elif get_origin(ann) in (Union, types.UnionType):
                for arg in get_args(ann):
                    if isinstance(arg, type) and issubclass(arg, BaseModel):
                        model_cls = arg
                        break

            if model_cls is not None:
                if body_data is None:
                    if default is inspect.Parameter.empty:
                        raise ValidationError([{"loc": ["body"], "msg": "Field required", "type": "missing"}])
                    kwargs[pname] = default
                else:
                    kwargs[pname] = model_cls.model_validate(body_data)
                continue

            if ann is dict or getattr(ann, "__origin__", None) is dict:
                if body_data is not None and pname in ("payload", "body", "data"):
                    kwargs[pname] = body_data
                    continue

            # Query param fallback
            if pname in request.query_params:
                raw_q = request.query_params[pname]
                if ann is int:
                    kwargs[pname] = int(raw_q)
                elif ann is bool:
                    kwargs[pname] = raw_q.lower() in ("true", "1", "yes")
                else:
                    kwargs[pname] = raw_q
                continue

            if default is not inspect.Parameter.empty:
                kwargs[pname] = default
                continue

            # Optional None check
            origin = getattr(ann, "__origin__", None)
            if origin is not None and type(None) in getattr(ann, "__args__", ()):
                kwargs[pname] = None
                continue

            kwargs[pname] = None

        res = actual_fn(**kwargs)
        if inspect.iscoroutine(res):
            loop = asyncio.new_event_loop()
            try:
                res = loop.run_until_complete(res)
            finally:
                loop.close()
        return res

    @staticmethod
    def _attach_cors_headers(resp: Response) -> Response:
        existing_lower = {k.lower() for k in resp.headers}
        if "access-control-allow-origin" not in existing_lower:
            resp.headers["Access-Control-Allow-Origin"] = "*"
        if "access-control-expose-headers" not in existing_lower:
            resp.headers["Access-Control-Expose-Headers"] = "Mcp-Session-Id"
        if "access-control-allow-private-network" not in existing_lower:
            resp.headers["Access-Control-Allow-Private-Network"] = "true"
        return resp

    def handle_request(
        self,
        method: str,
        raw_url: str,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        body: bytes = b"",
        client_host: str = "127.0.0.1",
    ) -> Response:
        if method.upper() == "OPTIONS":
            return Response(
                content=b"",
                status_code=204,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
                    "Access-Control-Allow-Headers": "Content-Type, Authorization, X-CSRF-Token, Idempotency-Key, X-API-Key, X-SubSphere-Mcp-Token, Mcp-Session-Id, Accept",
                    "Access-Control-Expose-Headers": "Mcp-Session-Id",
                    "Access-Control-Allow-Private-Network": "true",
                },
            )
        return self._attach_cors_headers(
            self._dispatch_request(
                method=method,
                raw_url=raw_url,
                headers=headers,
                cookies=cookies,
                body=body,
                client_host=client_host,
            )
        )

    def _dispatch_request(
        self,
        *,
        method: str,
        raw_url: str,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        body: bytes = b"",
        client_host: str = "127.0.0.1",
    ) -> Response:
        parsed = urlparse(raw_url)
        path = parsed.path or "/"
        qs = {k: v[-1] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
        req_id = f"req_{uuid.uuid4().hex[:12]}"
        request = Request(
            method=method,
            path=path,
            query_params=qs,
            headers=headers,
            cookies=cookies,
            body=body,
            client_host=client_host,
        )
        request.state.request_id = req_id

        if self.openapi_url and path == self.openapi_url and method.upper() == "GET":
            return JSONResponse(self.openapi(), status_code=200)

        route, path_params, path_matched = self._match_route(method, path)
        if route is None:
            status = 405 if path_matched else 404
            msg = "Method Not Allowed" if path_matched else "Not Found"
            return JSONResponse(
                {"code": "METHOD_NOT_ALLOWED" if path_matched else "NOT_FOUND", "message": msg, "details": {}, "request_id": req_id},
                status_code=status,
            )

        body_data: Any = None
        if body:
            ctype = request.headers.get("content-type", "")
            if "json" in ctype or body.lstrip().startswith((b"{", b"[")):
                try:
                    body_data = json.loads(body.decode("utf-8"))
                except Exception:
                    return JSONResponse(
                        {"code": "MALFORMED_JSON", "message": "Invalid JSON body", "details": {}, "request_id": req_id},
                        status_code=400,
                    )

        response = Response(status_code=route.status_code or 200)
        try:
            result = self._call_callable(
                route.endpoint, request, response, path_params, body_data, {}
            )
            if isinstance(result, Response):
                for ck, cv in response._cookies.items():
                    result._cookies.setdefault(ck, cv)
                return result
            if route.status_code == 204 or result is None and route.status_code == 204:
                response.status_code = 204
                response.body = b""
                return response
            if isinstance(result, BaseModel):
                payload = result.model_dump()
            else:
                payload = result
            out = JSONResponse(payload, status_code=response.status_code, headers=response.headers)
            out._cookies.update(response._cookies)
            return out
        except ValidationError as ve:
            return JSONResponse(
                {
                    "code": "VALIDATION_ERROR",
                    "message": "Request validation failed",
                    "details": {"errors": ve.errors()},
                    "request_id": req_id,
                },
                status_code=422,
            )
        except HTTPException as he:
            if HTTPException in self.exception_handlers:
                res = self.exception_handlers[HTTPException](request, he)
                if inspect.iscoroutine(res):
                    loop = asyncio.new_event_loop()
                    try:
                        res = loop.run_until_complete(res)
                    finally:
                        loop.close()
                return res
            if isinstance(he.detail, dict) and "code" in he.detail:
                body_err = dict(he.detail)
                body_err.setdefault("request_id", req_id)
            else:
                body_err = {
                    "code": f"HTTP_{he.status_code}",
                    "message": str(he.detail or "Request failed"),
                    "details": {},
                    "detail": he.detail,
                    "request_id": req_id,
                }
            return JSONResponse(body_err, status_code=he.status_code, headers=he.headers)
        except Exception as exc:
            for exc_cls, handler in self.exception_handlers.items():
                if isinstance(exc, exc_cls):
                    res = handler(request, exc)
                    if inspect.iscoroutine(res):
                        loop = asyncio.new_event_loop()
                        try:
                            res = loop.run_until_complete(res)
                        finally:
                            loop.close()
                    return res
            return JSONResponse(
                {"code": "INTERNAL_ERROR", "message": str(exc), "details": {}, "request_id": req_id},
                status_code=500,
            )


__all__ = [
    "APIRouter",
    "Body",
    "Cookie",
    "Depends",
    "FastAPI",
    "HTTPException",
    "Header",
    "Query",
    "Request",
    "Response",
]

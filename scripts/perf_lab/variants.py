"""Middleware variants of the Iris app, for the performance lab only.

Used to test where the event loop's time goes before touching security code:
the same scenario runs against the real middleware stack and against stacks
with one piece removed or replaced. Select with PERF_LAB_VARIANT and launch
``scripts.perf_lab.variants:app`` instead of ``server:app``.

- normal                     the real stack
- no_gzip                    without response compression
- trivial_auth_http          authentication replaced by a cheap check (token
                             signature + identity cached in memory), still a
                             BaseHTTPMiddleware like the real one
- trivial_auth_asgi          the same cheap check as a pure ASGI middleware
- trivial_auth_and_log_asgi  as above, and request logging as pure ASGI too
- real_auth_inline           the real bearer checks (signature, device not
                             revoked and matching, token and session versions)
                             with today's two reads, run on the event loop
- real_auth_join_inline      the same checks with one JOIN on a connection kept
                             open, run on the event loop
- ingest_files_only          batch ingest admits, writes and syncs the files, then
                             answers "ready" without its commit or catalog step:
                             how fast the disk takes the photos alone
- ingest_no_catalog          batch ingest commits but skips the catalog step

real_auth_* time each identity resolution and, when PERF_LAB_AUTH_STATS names
a file, write p50/p95/p99/p99.9/max there on exit: work kept on the event loop
must be small in its worst case, not only on average.

The difference between trivial_auth_http and trivial_auth_asgi is the cost of
the BaseHTTPMiddleware wrapper itself. Nothing here is imported by the server:
these stacks exist only in a disposable lab process.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import uuid
from pathlib import Path

from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import JSONResponse

import server
from core.device_tokens import read_access_token
from core.users_db import _from_row, get_device, get_user_by_id

VARIANTS = (
    "normal",
    "no_gzip",
    "trivial_auth_http",
    "trivial_auth_asgi",
    "trivial_auth_and_log_asgi",
    "real_auth_inline",
    "real_auth_join_inline",
    "ingest_files_only",
    "ingest_no_catalog",
)
_PUBLIC = {"/healthz", "/api/auth/devices/login", "/api/auth/devices/refresh"}
_identities: dict[tuple[int, str], object] = {}
_logger = logging.getLogger("iris")


def _identify(authorization: str):
    """(user, device id) for a validly signed bearer token, cached; None otherwise."""
    if not authorization.lower().startswith("bearer "):
        return None
    payload = read_access_token(server.app.state.auth_secret, authorization[7:].strip())
    if not payload:
        return None
    key = (payload.get("user_id"), str(payload.get("device_id")))
    if key not in _identities:
        _identities[key] = get_user_by_id(server.app.state.users_db_path, int(key[0]))
    user = _identities[key]
    return (user, key[1]) if user is not None else None


async def trivial_auth_dispatch(request, call_next):
    if request.url.path in _PUBLIC:
        return await call_next(request)
    found = _identify(request.headers.get("authorization", ""))
    if found is None:
        return JSONResponse({"detail": "Autenticação necessária"}, status_code=401)
    request.state.iris_user, request.state.iris_device_id = found
    return await call_next(request)


class TrivialAuthASGI:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope["path"] in _PUBLIC:
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        found = _identify(headers.get(b"authorization", b"").decode("latin-1"))
        if found is None:
            await JSONResponse({"detail": "Autenticação necessária"}, status_code=401)(
                scope, receive, send
            )
            return
        state = scope.setdefault("state", {})
        state["iris_user"], state["iris_device_id"] = found
        await self.app(scope, receive, send)


class LogASGI:
    """One access line per request, like server.log_request, without the wrapper."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id, started, status = uuid.uuid4().hex[:16], time.perf_counter(), {"code": 0}
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_status(message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_with_status)
        finally:
            _logger.info(
                "http_request_completed request_id=%s method=%s path=%s status=%d duration_ms=%.1f",
                request_id,
                scope["method"],
                scope["path"],
                status["code"],
                (time.perf_counter() - started) * 1000,
            )


_auth_seconds: list[float] = []
_shared: sqlite3.Connection | None = None
_JOIN = (
    "SELECT d.revoked_at AS device_revoked_at, d.user_id AS device_user_id, "
    "d.token_version AS device_token_version, u.* "
    "FROM devices d JOIN users u ON u.id = d.user_id WHERE d.id = ?"
)


def _resolve_two_reads(payload: dict):
    db = server.app.state.users_db_path
    device = get_device(db, str(payload.get("device_id")))
    if (
        device is None
        or device.revoked_at
        or device.user_id != payload.get("user_id")
        or device.token_version != payload.get("token_version")
    ):
        return None
    return get_user_by_id(db, int(payload["user_id"]))


def _resolve_join(payload: dict):
    global _shared
    if _shared is None:  # only ever used on the event loop thread
        _shared = sqlite3.connect(server.app.state.users_db_path)
        _shared.row_factory = sqlite3.Row
    row = _shared.execute(_JOIN, (str(payload.get("device_id")),)).fetchone()
    if (
        row is None
        or row["device_revoked_at"]
        or row["device_user_id"] != payload.get("user_id")
        or row["device_token_version"] != payload.get("token_version")
    ):
        return None
    return _from_row(row)


def _real_auth(resolve):
    async def dispatch(request, call_next):
        if request.url.path in _PUBLIC:
            return await call_next(request)
        started = time.perf_counter()
        authorization = request.headers.get("authorization", "")
        payload = (
            read_access_token(server.app.state.auth_secret, authorization[7:].strip())
            if authorization.lower().startswith("bearer ")
            else None
        )
        user = resolve(payload) if payload else None
        if user is not None and user.session_version != payload.get("session_version"):
            user = None
        _auth_seconds.append(time.perf_counter() - started)
        # uvicorn re-raises SIGTERM after shutting down, so atexit never runs:
        # keep the file current instead.
        if len(_auth_seconds) % 100 == 0:
            _write_auth_stats()
        if user is None:
            return JSONResponse({"detail": "Autenticação necessária"}, status_code=401)
        request.state.iris_user, request.state.iris_device_id = user, str(payload.get("device_id"))
        return await call_next(request)

    return dispatch


def _write_auth_stats() -> None:
    # Default: next to the lab's data (ROOT/lab/data -> ROOT/auth-stats.json).
    target = os.environ.get("PERF_LAB_AUTH_STATS") or str(
        Path(os.environ.get("IRIS_DATA_DIR", ".")).resolve().parent.parent / "auth-stats.json"
    )
    if not target or not _auth_seconds:
        return
    ordered = sorted(_auth_seconds)

    def at(q: float) -> float:
        return round(ordered[min(len(ordered) - 1, int(q * len(ordered)))] * 1e6, 1)

    Path(target).write_text(
        json.dumps(
            {
                "requests": len(ordered),
                "p50_us": at(0.50),
                "p95_us": at(0.95),
                "p99_us": at(0.99),
                "p999_us": at(0.999),
                "max_us": round(ordered[-1] * 1e6, 1),
            }
        )
    )


def _dispatch_of(middleware: Middleware):
    return middleware.kwargs.get("dispatch") if middleware.cls is BaseHTTPMiddleware else None


def _skip_ingest_steps(*, commit: bool) -> None:
    """Replace batch ingest's later steps with answers, for disk-only measurements."""
    from core.sync_ingest import SyncIngestPipeline

    def answer_ready(self, user, stored, *args):
        return {item.upload_id: {"upload_id": item.upload_id, "state": "ready"} for item in stored}

    SyncIngestPipeline._catalog = answer_ready
    if not commit:
        SyncIngestPipeline._commit = lambda self, *args, **kwargs: None


def apply(variant: str, app=server.app) -> None:
    if variant not in VARIANTS:
        raise SystemExit(f"PERF_LAB_VARIANT must be one of {VARIANTS}, got {variant!r}")
    stack = list(app.user_middleware)
    if variant == "no_gzip":
        stack = [m for m in stack if m.cls is not GZipMiddleware]
    if variant.startswith("trivial_auth"):
        replacement = (
            Middleware(BaseHTTPMiddleware, dispatch=trivial_auth_dispatch)
            if variant == "trivial_auth_http"
            else Middleware(TrivialAuthASGI)
        )
        stack = [
            replacement if _dispatch_of(m) is server.authenticate_library_request else m
            for m in stack
        ]
    if variant.startswith("real_auth"):
        dispatch = _real_auth(
            _resolve_two_reads if variant == "real_auth_inline" else _resolve_join
        )
        stack = [
            Middleware(BaseHTTPMiddleware, dispatch=dispatch)
            if _dispatch_of(m) is server.authenticate_library_request
            else m
            for m in stack
        ]
    if variant in {"ingest_files_only", "ingest_no_catalog"}:
        _skip_ingest_steps(commit=variant == "ingest_no_catalog")
    if variant == "trivial_auth_and_log_asgi":
        stack = [Middleware(LogASGI) if _dispatch_of(m) is server.log_request else m for m in stack]
    app.user_middleware = stack
    app.middleware_stack = None  # rebuilt from user_middleware on the first request


apply(os.environ.get("PERF_LAB_VARIANT", "normal"))
app = server.app

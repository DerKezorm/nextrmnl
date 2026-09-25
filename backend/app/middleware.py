"""Request id, timing and security headers for every request."""

from __future__ import annotations

import logging
import secrets
import time
from collections.abc import Callable
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from .services import logs

logger = logging.getLogger("nextrmnl.api")

#: Paths whose calls explain nothing but fill the log.
QUIET_PATHS = ("/api/health", "/api/logs", "/api/auth/me", "/api/connections/reach", "/api/sessions/")
SLOW_MS = 3000
#: What takes long by nature: backups grow with the database, an SSH connect waits for the network.
SLOW_EXPECTED = ("/api/backups", "/api/sessions", "/api/vault/file", "/api/oidc/authentik")

#: xterm.js sets inline styles, hence 'unsafe-inline' for styles only. Scripts stay strict.
CSP = (
    b"default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; font-src 'self' data:; "
    b"connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'"
)
SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"content-security-policy", CSP),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"same-origin"),
    (b"x-frame-options", b"DENY"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
)


class RequestContextMiddleware:
    """Pure ASGI, so streaming responses and WebSockets are not buffered."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        request_id = secrets.token_hex(3)
        scope.setdefault("state", {})["request_id"] = request_id
        token = logs.bind_request(request_id)
        start = time.perf_counter()
        status = 0
        path = scope.get("path", "?")

        async def send_wrapper(message: dict) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = message.setdefault("headers", [])
                headers.append((b"x-request-id", request_id.encode("ascii")))
                headers.extend(SECURITY_HEADERS)
            await send(message)

        method = scope.get("method", "WS")
        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            duration = (time.perf_counter() - start) * 1000
            logger.exception("Unhandled error on %s %s after %dms", method, path, duration)
            raise
        else:
            if scope["type"] != "http":
                return
            duration = (time.perf_counter() - start) * 1000
            if status >= 500:
                logger.error("%s %s -> %s in %dms", method, path, status, duration)
            elif duration >= SLOW_MS and not path.startswith(QUIET_PATHS + SLOW_EXPECTED):
                logger.warning("Slow request: %s %s -> %s in %dms", method, path, status, duration)
            elif not path.startswith(QUIET_PATHS):
                logger.debug("%s %s -> %s in %dms", method, path, status, duration)
        finally:
            logs.unbind_request(token)


async def unhandled_error(request: Request, _exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None) or "-"
    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "code": "internal_error",
                "message": f"Something went wrong on the server. Request id: {request_id}",
                "request_id": request_id,
            }
        },
        headers={"X-Request-Id": request_id},
    )

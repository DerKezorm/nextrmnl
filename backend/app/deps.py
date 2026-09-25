"""Who may do what.

Every request under ``/api`` (except setup, sign-in, invites and health) needs the session cookie. Changing
requests need the header ``X-Requested-By: nextrmnl`` on top. WebSockets check the Origin against the Host,
because a browser sends cookies with cross-site WebSocket handshakes without asking.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request, WebSocket
from sqlalchemy.orm import Session

from .db import get_db
from .meldungen import fehler
from .models import OPERATOR, Account
from .security import CSRF_HEADER, CSRF_VALUE, SESSION_COOKIE, session_account
from .services import logs, settings_service

DbSession = Annotated[Session, Depends(get_db)]
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def client_ip(request: Request | WebSocket) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "-")[:64]


def check_csrf(request: Request) -> None:
    if request.method in UNSAFE and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise fehler("missing_header", f"This request needs the header X-Requested-By: {CSRF_VALUE}.", 403)


def current_account(request: Request, db: DbSession) -> Account:
    check_csrf(request)
    account = session_account(db, request.cookies.get(SESSION_COOKIE))
    if account is None:
        raise fehler("not_signed_in", "Not signed in.", 401)
    logs.set_actor(account.name)
    return account


def operator_account(account: Annotated[Account, Depends(current_account)]) -> Account:
    if account.role != OPERATOR:
        raise fehler("operator_only", "Only the operator may do this.", 403)
    return account


CurrentAccount = Annotated[Account, Depends(current_account)]
OperatorAccount = Annotated[Account, Depends(operator_account)]


def same_origin(websocket: WebSocket, db: Session) -> bool:
    """The Origin of the handshake must be the host the browser talked to.

    Behind a reverse proxy the Host header often names the proxy's upstream, so the original host is also
    accepted from ``X-Forwarded-Host`` and from the public address (the setting, else ``NEXTRMNL_PUBLIC_URL``).
    A browser cannot set any of these headers itself, which is what makes the check worth anything.
    """
    origin = websocket.headers.get("origin", "")
    if not origin:
        return False
    origin_host = origin.split("://", 1)[-1].split("/", 1)[0].lower()
    candidates = {websocket.headers.get("host", "").lower()}
    forwarded = websocket.headers.get("x-forwarded-host", "")
    if forwarded:
        candidates.add(forwarded.split(",")[0].strip().lower())
    public = settings_service.public_url(db)
    if public:
        candidates.add(public.split("://", 1)[-1].split("/", 1)[0].lower())
    candidates.discard("")
    return origin_host in candidates


def websocket_account(websocket: WebSocket, db: Session) -> Account | None:
    """The signed-in account behind a WebSocket, or None. Same-origin is required."""
    if not same_origin(websocket, db):
        return None
    account = session_account(db, websocket.cookies.get(SESSION_COOKIE))
    if account is not None:
        logs.set_actor(account.name)
    return account

"""Who may do what.

Every request under ``/api`` (except setup, sign-in, invites and health) needs the session cookie. Changing
requests need the header ``X-Requested-By: nextrmnl`` on top. WebSockets check the Origin against the Host,
because a browser sends cookies with cross-site WebSocket handshakes without asking.
"""

from __future__ import annotations

import ipaddress
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException, Request, WebSocket
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .meldungen import fehler, meldung
from .models import OPERATOR, Account
from .security import CSRF_HEADER, CSRF_VALUE, SESSION_COOKIE, brake, session_account
from .services import accounts, logs, settings_service, totp

DbSession = Annotated[Session, Depends(get_db)]
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
#: What an account may still call while the operator requires a second factor it has not set up: seeing itself,
#: enrolling, leaving.
SETUP_ONLY_PATHS = {"/api/auth/me", "/api/auth/logout", "/api/auth/totp/begin", "/api/auth/totp/confirm"}


@lru_cache(maxsize=4)
def _trusted_networks(spec: str) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    networks = []
    for entry in spec.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            continue
    return tuple(networks)


def _is_trusted_proxy(address: str) -> bool:
    networks = _trusted_networks(get_settings().trusted_proxies)
    if not networks:
        return False
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(parsed in network for network in networks)


def client_ip(request: Request | WebSocket) -> str:
    """The sender's address, for the brake and the history.

    ``X-Forwarded-For`` is believed only when the peer is a configured trusted proxy, and then the rightmost
    hop that is not itself a trusted proxy counts: everything left of it was written by whoever sent the request
    and would let a guesser pick a fresh address for every try.
    """
    peer = (request.client.host if request.client else "-")[:64]
    forwarded = request.headers.get("x-forwarded-for", "")
    if not forwarded or not _is_trusted_proxy(peer):
        return peer
    hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
    for hop in reversed(hops):
        if not _is_trusted_proxy(hop):
            return hop[:64]
    return (hops[0] if hops else peer)[:64]


# --- Proving the password once more while signed in ------------------------------------------------------------ #
#
# Turning the second factor off, exporting the vault, opening it, changing the password: each asks for the
# password again, and each counts a wrong answer the way the sign-in does. Otherwise a stolen cookie or an
# unattended browser would be a place to guess the password without limit.


def _reauth_key(request: Request) -> str:
    return "reauth:" + client_ip(request)


def reauth_guard(request: Request, account: Account) -> None:
    """Refuses before any password is checked when the account is locked or the sender is braked."""
    if accounts.is_locked(account):
        raise fehler("account_locked", "Too many failed attempts. Try again later.", 429)
    wait = brake.wait_seconds(_reauth_key(request))
    if wait:
        raise HTTPException(
            status_code=429,
            detail=meldung("too_many_attempts", "Too many attempts. Try again later.", retry_after=wait),
            headers={"Retry-After": str(wait)},
        )


def reauth_failed(request: Request, db: Session, account: Account) -> None:
    brake.failed(_reauth_key(request))
    accounts.note_failure(db, account)


def reauth_succeeded(request: Request, db: Session, account: Account) -> None:
    brake.succeeded(_reauth_key(request))
    accounts.note_success(db, account)


def check_csrf(request: Request) -> None:
    if request.method in UNSAFE and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise fehler("missing_header", f"This request needs the header X-Requested-By: {CSRF_VALUE}.", 403)


def current_account(request: Request, db: DbSession) -> Account:
    check_csrf(request)
    account = session_account(db, request.cookies.get(SESSION_COOKIE))
    if account is None:
        raise fehler("not_signed_in", "Not signed in.", 401)
    logs.set_actor(account.name)
    if request.url.path not in SETUP_ONLY_PATHS and totp.setup_required(db, account):
        raise fehler("second_factor_setup_required", "Set up your second factor first.", 403)
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
    if account is not None and totp.setup_required(db, account):
        return None
    if account is not None:
        logs.set_actor(account.name)
    return account

"""Connections: own ones and those shared with the account. Sharing passes name, address and settings, never access."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..deps import CurrentAccount, DbSession
from ..meldungen import fehler
from ..models import AUTH_METHODS, Account, Connection, ConnectionShare, HostKey, VaultKey, utcnow
from ..services import settings_service, targets

logger = logging.getLogger("nextrmnl.connections")

router = APIRouter(prefix="/api/connections", tags=["connections"])


class ConnectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    group: str = Field(default="", max_length=64)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=22, ge=1, le=65535)
    user: str = Field(default="root", min_length=1, max_length=64)
    auth: str = Field(default="key")
    key_id: int | None = None
    jump_id: int | None = None
    keepalive: bool = True
    start_command: str = Field(default="", max_length=255)


class ShareIn(BaseModel):
    account_ids: list[int]


def visible(db: DbSession, account: Account) -> list[Connection]:
    shared_ids = select(ConnectionShare.connection_id).where(ConnectionShare.account_id == account.id)
    return list(
        db.scalars(
            select(Connection)
            .where((Connection.owner_id == account.id) | Connection.id.in_(shared_ids))
            .order_by(Connection.group, Connection.name)
        )
    )


def own(db: DbSession, account: Account, connection_id: int) -> Connection:
    row = db.get(Connection, connection_id)
    if row is None or row.owner_id != account.id:
        raise fehler("not_found", "Connection not found.", 404)
    return row


def accessible(db: DbSession, account: Account, connection_id: int) -> Connection:
    row = db.get(Connection, connection_id)
    if row is None:
        raise fehler("not_found", "Connection not found.", 404)
    if row.owner_id == account.id:
        return row
    share = db.get(ConnectionShare, {"connection_id": connection_id, "account_id": account.id})
    if share is None:
        raise fehler("not_found", "Connection not found.", 404)
    return row


def view(
    db: DbSession, account: Account, row: Connection, names: dict[int, str], host_keys: dict[tuple[str, int], HostKey]
) -> dict[str, Any]:
    shared_with = [
        int(account_id)
        for account_id in db.scalars(select(ConnectionShare.account_id).where(ConnectionShare.connection_id == row.id))
    ]
    host_key = host_keys.get((row.host.lower(), row.port))
    # A member sees the connection with their own sign-in: their user name, method and key.
    user, auth, key_id = row.user, row.auth, row.key_id
    if row.owner_id != account.id:
        share = db.get(ConnectionShare, {"connection_id": row.id, "account_id": account.id})
        user = share.user if share is not None and share.user else row.user
        auth = share.auth if share is not None else "ask"
        key_id = share.key_id if share is not None and auth == "key" else None
    return {
        "id": row.id,
        "name": row.name,
        "group": row.group,
        "host": row.host,
        "port": row.port,
        "user": user,
        "owner_user": row.user,
        "auth": auth,
        "key_id": key_id,
        "jump_id": row.jump_id,
        "keepalive": row.keepalive,
        "start_command": row.start_command,
        "owner_id": row.owner_id,
        "shared_by": names.get(row.owner_id) if row.owner_id != account.id else None,
        "shared_with": shared_with if row.owner_id == account.id else [],
        "host_key": {"state": "known", "fingerprint": host_key.fingerprint, "key_type": host_key.key_type}
        if host_key
        else {"state": "new", "fingerprint": "", "key_type": ""},
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
        "created_at": row.created_at.isoformat(),
    }


def _names(db: DbSession) -> dict[int, str]:
    return {row.id: row.name for row in db.scalars(select(Account))}


def _host_keys(db: DbSession) -> dict[tuple[str, int], HostKey]:
    return {(row.host.lower(), row.port): row for row in db.scalars(select(HostKey))}


@router.get("", summary="Own and shared connections")
def list_connections(account: CurrentAccount, db: DbSession) -> list[dict[str, Any]]:
    names, host_keys = _names(db), _host_keys(db)
    return [view(db, account, row, names, host_keys) for row in visible(db, account)]


def _validate(db: DbSession, account: Account, payload: ConnectionIn, own_id: int | None = None) -> None:
    if payload.auth not in AUTH_METHODS:
        raise fehler("invalid_auth", "Unknown sign-in method.", 422)
    if payload.auth == "key":
        if payload.key_id is None:
            raise fehler("key_required", "Choose a key from the vault.", 422)
        key = db.get(VaultKey, payload.key_id)
        if key is None or key.account_id != account.id:
            raise fehler("key_not_found", "This key is not in your vault.", 422)
    if payload.jump_id is not None:
        if payload.jump_id == own_id:
            raise fehler("jump_self", "A connection cannot jump through itself.", 422)
        accessible(db, account, payload.jump_id)
    if " " in payload.host.strip() or "/" in payload.host:
        raise fehler("invalid_host", "That is not a host name or address.", 422)


@router.post("", status_code=201, summary="Create a connection")
def create(payload: ConnectionIn, account: CurrentAccount, db: DbSession) -> dict[str, Any]:
    _validate(db, account, payload)
    row = Connection(
        owner_id=account.id,
        name=payload.name.strip(),
        group=payload.group.strip(),
        host=payload.host.strip(),
        port=payload.port,
        user=payload.user.strip(),
        auth=payload.auth,
        key_id=payload.key_id if payload.auth == "key" else None,
        jump_id=payload.jump_id,
        keepalive=payload.keepalive,
        start_command=payload.start_command.strip(),
    )
    db.add(row)
    db.commit()
    logger.info("Connection created name=%s target=%s@%s:%s", row.name, row.user, row.host, row.port)
    return view(db, account, row, _names(db), _host_keys(db))


@router.put("/{connection_id}", summary="Change an own connection")
def update(connection_id: int, payload: ConnectionIn, account: CurrentAccount, db: DbSession) -> dict[str, Any]:
    row = own(db, account, connection_id)
    _validate(db, account, payload, own_id=row.id)
    row.name = payload.name.strip()
    row.group = payload.group.strip()
    row.host = payload.host.strip()
    row.port = payload.port
    row.user = payload.user.strip()
    row.auth = payload.auth
    row.key_id = payload.key_id if payload.auth == "key" else None
    row.jump_id = payload.jump_id
    row.keepalive = payload.keepalive
    row.start_command = payload.start_command.strip()
    db.commit()
    logger.info("Connection changed id=%s name=%s", row.id, row.name)
    return view(db, account, row, _names(db), _host_keys(db))


@router.delete("/{connection_id}", status_code=204, summary="Delete an own connection")
def delete(connection_id: int, account: CurrentAccount, db: DbSession) -> None:
    row = own(db, account, connection_id)
    db.delete(row)
    db.commit()
    logger.info("Connection deleted id=%s name=%s", connection_id, row.name)


@router.put("/{connection_id}/share", summary="Which accounts see this connection")
def share(connection_id: int, payload: ShareIn, account: CurrentAccount, db: DbSession) -> dict[str, Any]:
    row = own(db, account, connection_id)
    wanted = {account_id for account_id in payload.account_ids if account_id != account.id}
    existing = {int(a) for a in db.scalars(select(Account.id))}
    if not wanted <= existing:
        raise fehler("unknown_account", "One of the accounts does not exist.", 422)
    # Only the difference changes: a member's own sign-in on a share they keep must survive a re-share.
    current = {
        int(a) for a in db.scalars(select(ConnectionShare.account_id).where(ConnectionShare.connection_id == row.id))
    }
    if current - wanted:
        db.execute(
            ConnectionShare.__table__.delete().where(
                ConnectionShare.connection_id == row.id, ConnectionShare.account_id.in_(current - wanted)
            )
        )
    for account_id in sorted(wanted - current):
        db.add(ConnectionShare(connection_id=row.id, account_id=account_id))
    db.commit()
    names = _names(db)
    logger.info("Connection %r shared with accounts: %s", row.name, ", ".join(names[a] for a in sorted(wanted)) or "-")
    return view(db, account, row, names, _host_keys(db))


class AccessIn(BaseModel):
    """A member's own sign-in on a shared connection. Empty user means the owner's user name."""

    user: str = Field(default="", max_length=64)
    auth: str = Field(default="ask")
    key_id: int | None = None
    #: Runs in the member's own shell after sign-in. The owner's command never does.
    start_command: str = Field(default="", max_length=255)


@router.put("/{connection_id}/my-access", summary="Own user name, method and key on a connection shared with me")
def set_my_access(connection_id: int, payload: AccessIn, account: CurrentAccount, db: DbSession) -> dict[str, Any]:
    row = db.get(Connection, connection_id)
    share = db.get(ConnectionShare, {"connection_id": connection_id, "account_id": account.id})
    if row is None or share is None:
        raise fehler("not_found", "Connection not found.", 404)
    if payload.auth not in AUTH_METHODS:
        raise fehler("invalid_auth", "Unknown sign-in method.", 422)
    if payload.auth == "key":
        if payload.key_id is None:
            raise fehler("key_required", "Choose a key from the vault.", 422)
        key = db.get(VaultKey, payload.key_id)
        if key is None or key.account_id != account.id:
            raise fehler("key_not_found", "This key is not in your vault.", 422)
    share.user = payload.user.strip()
    share.auth = payload.auth
    share.key_id = payload.key_id if payload.auth == "key" else None
    share.start_command = payload.start_command.strip()
    db.commit()
    logger.info("Own sign-in set on shared connection id=%s account=%s auth=%s", row.id, account.name, payload.auth)
    return view(db, account, row, _names(db), _host_keys(db))


@router.post("/{connection_id}/host-key/forget", status_code=204, summary="Forget the stored host key of this target")
def forget_host_key(connection_id: int, account: CurrentAccount, db: DbSession) -> None:
    row = accessible(db, account, connection_id)
    if row.owner_id != account.id and account.role != "operator":
        # The store is shared by everyone; a member of a share must not be able to make the next contact of
        # every other account a fresh "unknown host" that hides a changed key.
        raise fehler("operator_only", "Only the owner of the connection or the operator may forget a host key.", 403)
    key = db.scalar(select(HostKey).where(HostKey.host == row.host.lower(), HostKey.port == row.port))
    if key is not None:
        db.delete(key)
        db.commit()
        logger.info("Host key forgotten for %s:%s by %s", row.host, row.port, account.name)


async def _probe(host: str, port: int) -> tuple[str, int | None]:
    start = time.perf_counter()
    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=2.5)
    except (OSError, TimeoutError):
        return "down", None
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return "up", int((time.perf_counter() - start) * 1000)


@router.get("/reach", summary="Is the TCP port of each connection reachable? Only for allowed targets")
async def reach(account: CurrentAccount, db: DbSession) -> dict[str, dict[str, Any]]:
    mode = settings_service.get(db, "targets_mode")
    entries = targets.parse_list(settings_service.get(db, "targets_list"))
    rows = visible(db, account)

    async def one(row: Connection) -> tuple[int, dict[str, Any]]:
        if row.jump_id is not None:
            return row.id, {"reach": "unknown", "latency_ms": None}
        verdict = await targets.check(row.host, row.port, mode, entries)
        if not verdict.allowed:
            return row.id, {"reach": "unknown", "latency_ms": None, "blocked": verdict.reason}
        state, latency = await _probe(row.host, row.port)
        return row.id, {"reach": state, "latency_ms": latency}

    results = await asyncio.gather(*(one(row) for row in rows))
    return {str(connection_id): data for connection_id, data in results}


def touch(db: DbSession, connection_id: int | None) -> None:
    if connection_id is None:
        return
    row = db.get(Connection, connection_id)
    if row is not None:
        row.last_used_at = utcnow()
        db.commit()

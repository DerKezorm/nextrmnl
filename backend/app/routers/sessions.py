"""Live sessions: the terminal WebSocket, the list of running sessions, the history, and SFTP on a session.

The WebSocket is authenticated by hand (``deps.websocket_account``): a browser sends the cookie with a cross-site
WebSocket handshake without asking, so the Origin must match the Host, and an unauthenticated handshake is
closed with 4401 before anything else happens. The HTTP routes use the usual dependencies.

Uploads are streamed part by part from the multipart body straight into the SFTP file; nothing is spooled to disk
or held in memory, so a 4 GB file costs 4 GB of network and nothing else.
"""

from __future__ import annotations

import logging
import posixpath
from collections import deque
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any
from urllib.parse import quote

import asyncssh
from fastapi import APIRouter, HTTPException, Query, Request, WebSocket
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from python_multipart.multipart import MultipartParser, parse_options_header
from sqlalchemy import select

from ..db import SessionLocal
from ..deps import CurrentAccount, DbSession, client_ip, websocket_account
from ..meldungen import fehler
from ..models import OPERATOR, Account, SessionRecord
from ..security import SESSION_COOKIE
from ..services import ssh

logger = logging.getLogger("nextrmnl.ssh")

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

#: Close codes of the handshake, before the socket is accepted.
WS_UNAUTHORIZED = 4401
WS_BAD_REQUEST = 4400
WS_NOT_FOUND = 4404
WS_TOO_MANY = 4429
UPLOAD_LIMIT = 4 * 1024 * 1024 * 1024
MAX_COLS = 1000
MAX_ROWS = 1000


def _clamp(value: int, upper: int, default: int) -> int:
    return value if 1 <= value <= upper else default


def _quick_target(host: str | None, port: int, user: str | None) -> ssh.Target | None:
    if not host or not user:
        return None
    host, user = host.strip(), user.strip()
    if not host or " " in host or "/" in host or len(host) > 255:
        return None
    if not user or len(user) > 64 or not (1 <= port <= 65535):
        return None
    return ssh.quick_target(host, port, user)


@router.websocket("/ws")
async def terminal(
    websocket: WebSocket,
    connection_id: int | None = None,
    host: str | None = None,
    port: int = 22,
    user: str | None = None,
    cols: int = 80,
    rows: int = 24,
) -> None:
    """One SSH session: ``connection_id`` for a saved connection, or ``host``, ``port`` and ``user`` for quick connect.

    Refused handshakes close with 4401 (no cookie or foreign Origin), 4404 (connection not visible) or 4400.
    """
    with SessionLocal() as db:
        account = websocket_account(websocket, db)
        if account is None:
            await websocket.close(code=WS_UNAUTHORIZED)
            return
        if connection_id is not None:
            row = ssh._accessible(db, account, connection_id)
            if row is None:
                await websocket.close(code=WS_NOT_FOUND)
                return
            try:
                target = ssh.target_from_connection(db, account, row)
            except (LookupError, ValueError):
                await websocket.close(code=WS_NOT_FOUND)
                return
        else:
            quick = _quick_target(host, port, user)
            if quick is None:
                await websocket.close(code=WS_BAD_REQUEST)
                return
            target = quick
        # The ORM row is not used after this point; copy what the session needs before the DB session ends.
        holder = Account(id=account.id, name=account.name, role=account.role)
    if ssh.count_for(holder.id) >= ssh.MAX_SESSIONS_PER_ACCOUNT:
        await websocket.close(code=WS_TOO_MANY)
        return
    await websocket.accept()
    size = (_clamp(cols, MAX_COLS, 80), _clamp(rows, MAX_ROWS, 24))
    session = ssh.SshSession(holder, target, client_ip(websocket), *size)
    # The terminal ends with the sign-in behind it: the session watches its own cookie.
    session.session_token = websocket.cookies.get(SESSION_COOKIE)
    try:
        await session.run(websocket)
    except Exception:
        # run() handles its own failures; this is the last net so nothing reaches the middleware's error log.
        logger.exception("Session handler failed account=%s", holder.name)
    # A best-effort close: the browser is usually gone already, and Starlette raises for that.
    with suppress(Exception):
        await websocket.close()


# ----------------------------------------------------------------------
# Running sessions and history
# ----------------------------------------------------------------------


def _names(db: DbSession) -> dict[int, str]:
    return {row.id: row.name for row in db.scalars(select(Account))}


def _running_view(session: ssh.SshSession) -> dict[str, Any]:
    return {
        "id": session.id,
        "account": session.account_name,
        "connection_id": session.target.connection_id,
        "name": session.target.name,
        "target": session.target.label,
        "started_at": session.started_at.isoformat(),
        "from_ip": session.from_ip,
        "state": "open" if session.opened_at else "connecting",
    }


@router.get("/running", summary="Live sessions: all for the operator, own ones for a member")
def running(account: CurrentAccount) -> list[dict[str, Any]]:
    return [_running_view(session) for session in ssh.running(account)]


@router.post("/{session_id}/disconnect", status_code=204, summary="End a live session")
async def disconnect(session_id: str, account: CurrentAccount) -> None:
    session = ssh.get(session_id)
    if session is None or (account.role != OPERATOR and session.account_id != account.id):
        raise fehler("session_not_found", "Session not found.", 404)
    await ssh.disconnect(session_id, account.name)


def _record_view(record: SessionRecord, names: dict[int, str]) -> dict[str, Any]:
    return {
        "id": record.id,
        "account": names.get(record.account_id, "?"),
        "connection_id": record.connection_id,
        "name": record.name,
        "target": record.target,
        "from_ip": record.from_ip,
        "started_at": record.started_at.isoformat(),
        "ended_at": record.ended_at.isoformat() if record.ended_at else None,
        "end": record.end,
        "detail": record.detail,
    }


@router.get("/history", summary="Past sessions, newest first")
def history(
    account: CurrentAccount, db: DbSession, limit: int = Query(default=200, ge=1, le=1000)
) -> list[dict[str, Any]]:
    ssh.purge_history(db)
    names = _names(db)
    return [_record_view(record, names) for record in ssh.history(db, account, limit)]


# ----------------------------------------------------------------------
# SFTP
# ----------------------------------------------------------------------


class PathIn(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


class RenameIn(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    to: str = Field(min_length=1, max_length=4096)


def _own_session(account: Account, session_id: str) -> ssh.SshSession:
    """Only the account that opened the session may use its connection, the operator included."""
    session = ssh.get(session_id)
    if session is None or session.account_id != account.id:
        raise fehler("session_not_found", "Session not found.", 404)
    return session


def _sftp_error(error: Exception) -> HTTPException:
    if isinstance(error, ssh.SessionNotOpen):
        return fehler("session_not_open", "The session is not connected yet.", 409)
    if isinstance(error, asyncssh.SFTPNoSuchFile):
        return fehler("not_found", "No such file or directory on the server.", 404)
    if isinstance(error, asyncssh.SFTPPermissionDenied):
        return fehler("permission_denied", "The server refused this: permission denied.", 403)
    if isinstance(error, asyncssh.SFTPError):
        reason = error.reason[:200]
    elif isinstance(error, asyncssh.Error | OSError):
        # Typically the SFTP subsystem is off on the server (a Synology with SSH on but SFTP off says
        # "subsystem request failed"). The server's own words are the only clue, so they go to the browser.
        reason = (str(error) or type(error).__name__)[:200]
    else:
        raise error
    # Paths never appear here; the reason is the server's message about the operation, not the data.
    logging.getLogger("nextrmnl.sftp").warning("SFTP operation failed: %s: %s", type(error).__name__, reason)
    return fehler("sftp_failed", "The file operation failed on the server.", 400, reason=reason)


@router.get("/{session_id}/files", summary="List a directory over the session's connection")
async def list_files(session_id: str, account: CurrentAccount, path: str = "") -> dict[str, Any]:
    session = _own_session(account, session_id)
    try:
        return await session.listdir(path)
    except Exception as error:
        raise _sftp_error(error) from error


@router.get("/{session_id}/files/stat", summary="Attributes of one entry")
async def stat_file(session_id: str, account: CurrentAccount, path: str) -> dict[str, Any]:
    session = _own_session(account, session_id)
    try:
        return await session.stat(path)
    except Exception as error:
        raise _sftp_error(error) from error


def _disposition(name: str) -> str:
    # A remote file name is foreign text: control characters would split the header, so they go first.
    clean = "".join(char for char in name if ord(char) >= 0x20 and char != "\x7f") or "download"
    ascii_name = clean.encode("ascii", "replace").decode("ascii").replace('"', "_").replace("\\", "_")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(clean)}"


@router.get("/{session_id}/files/download", summary="Download a file, streamed")
async def download(session_id: str, account: CurrentAccount, path: str) -> StreamingResponse:
    session = _own_session(account, session_id)
    try:
        name, size, chunks = await session.download(path)
    except Exception as error:
        raise _sftp_error(error) from error
    headers = {"Content-Disposition": _disposition(name), "Cache-Control": "no-store"}
    if size is not None:
        headers["Content-Length"] = str(size)
    return StreamingResponse(chunks, media_type="application/octet-stream", headers=headers)


class _MultipartFile:
    """Streams the ``file`` field of a multipart body: headers first, then the data as it arrives."""

    def __init__(self, request: Request, boundary: bytes, limit: int) -> None:
        self._stream = request.stream().__aiter__()
        self._limit = limit
        self._pending: deque[bytes] = deque()
        self._headers: dict[bytes, bytes] = {}
        self._field = b""
        self._value = b""
        self._in_file = False
        self._file_done = False
        self._total = 0
        self.filename: str | None = None
        self._parser = MultipartParser(
            boundary,
            {
                "on_part_begin": self._part_begin,
                "on_header_field": self._header_field,
                "on_header_value": self._header_value,
                "on_header_end": self._header_end,
                "on_headers_finished": self._headers_finished,
                "on_part_data": self._part_data,
                "on_part_end": self._part_end,
            },
        )

    def _part_begin(self) -> None:
        self._headers, self._field, self._value = {}, b"", b""

    def _header_field(self, data: bytes, start: int, end: int) -> None:
        self._field += data[start:end]

    def _header_value(self, data: bytes, start: int, end: int) -> None:
        self._value += data[start:end]

    def _header_end(self) -> None:
        self._headers[self._field.lower()] = self._value
        self._field, self._value = b"", b""

    def _headers_finished(self) -> None:
        _kind, params = parse_options_header(self._headers.get(b"content-disposition", b""))
        if params.get(b"name") == b"file" and self.filename is None:
            self._in_file = True
            raw = params.get(b"filename", b"")
            self.filename = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)

    def _part_data(self, data: bytes, start: int, end: int) -> None:
        if not self._in_file:
            return
        self._total += end - start
        if self._total > self._limit:
            raise fehler("too_large", "The file is larger than 4 GB.", 413)
        self._pending.append(bytes(data[start:end]))

    def _part_end(self) -> None:
        if self._in_file:
            self._in_file, self._file_done = False, True

    async def _feed(self) -> bool:
        try:
            chunk = await self._stream.__anext__()
        except StopAsyncIteration:
            self._parser.finalize()
            return False
        if chunk:
            self._parser.write(chunk)
        return True

    async def find_file(self) -> str | None:
        while self.filename is None:
            if not await self._feed():
                return None
        return self.filename

    async def chunks(self) -> AsyncIterator[bytes]:
        while True:
            while self._pending:
                yield self._pending.popleft()
            if self._file_done or not await self._feed():
                while self._pending:
                    yield self._pending.popleft()
                return


@router.post("/{session_id}/files/upload", status_code=201, summary="Upload one file into a directory, streamed")
async def upload(session_id: str, request: Request, account: CurrentAccount, path: str = "") -> dict[str, Any]:
    session = _own_session(account, session_id)
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > UPLOAD_LIMIT + 1024 * 1024:
        raise fehler("too_large", "The file is larger than 4 GB.", 413)
    content_type, params = parse_options_header(request.headers.get("content-type", ""))
    boundary = params.get(b"boundary")
    if content_type != b"multipart/form-data" or not boundary:
        raise fehler("invalid_input", "Send the file as multipart/form-data in the field 'file'.", 422)
    body = _MultipartFile(request, boundary, UPLOAD_LIMIT)
    filename = await body.find_file()
    name = posixpath.basename((filename or "").replace("\\", "/")).strip()
    if not name or name in (".", ".."):
        raise fehler("invalid_input", "The upload needs a file name.", 422)
    try:
        remote, size = await session.upload(path, name, body.chunks())
    except HTTPException:
        raise
    except Exception as error:
        raise _sftp_error(error) from error
    return {"path": remote, "name": name, "size": size}


@router.post("/{session_id}/files/mkdir", status_code=201, summary="Create a directory")
async def mkdir(session_id: str, payload: PathIn, account: CurrentAccount) -> dict[str, Any]:
    session = _own_session(account, session_id)
    try:
        return {"path": await session.mkdir(payload.path)}
    except Exception as error:
        raise _sftp_error(error) from error


@router.post("/{session_id}/files/rename", summary="Rename or move an entry")
async def rename(session_id: str, payload: RenameIn, account: CurrentAccount) -> dict[str, Any]:
    session = _own_session(account, session_id)
    try:
        return {"path": await session.rename(payload.path, payload.to)}
    except Exception as error:
        raise _sftp_error(error) from error


@router.delete("/{session_id}/files", status_code=204, summary="Delete a file or a directory")
async def remove(session_id: str, account: CurrentAccount, path: str, recursive: bool = False) -> None:
    """A directory with contents needs ``recursive=true``; the browser asks for that separately."""
    session = _own_session(account, session_id)
    try:
        await session.remove(path, recursive=recursive)
    except Exception as error:
        raise _sftp_error(error) from error

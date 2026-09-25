"""Live SSH sessions, driven by one WebSocket each, with SFTP on the same connection.

How a session runs
------------------
The browser opens a WebSocket, the router checks the cookie and the Origin and builds a ``Target`` (from a saved
connection or from quick-connect parameters). ``SshSession.run`` then walks the lifecycle: targets check, jump
hosts, host key, authentication, shell, pump, end. Questions to the person (host key, password, passphrase) go
out as JSON on the same WebSocket and the answer comes back on it; terminal data travels as binary frames.

Why the host key is checked the way it is
-----------------------------------------
asyncssh gets an *empty* known_hosts list, never ``None``: with ``None`` it would skip validation entirely. With an
empty list every server key ends up in ``validate_host_public_key`` of our client, which compares it with the
store. A known key passes; an unknown or changed one is remembered and refused, the handshake fails, the person
sees the fingerprint and decides, and only then is the key stored and the connection tried again. Nothing in
here accepts a key on its own.

Why credentials are settled before the connection is opened
-----------------------------------------------------------
asyncssh's ``connect_timeout`` covers TCP connect, handshake *and* authentication, and a server drops an
unauthenticated connection after its own grace time. A person typing a password must count toward neither, so
nothing is asked while a connection is open: for a server without a stored key a handshake without credentials
settles the host key first (the person sees the fingerprint before typing anything, as with ``ssh``), then the
password or passphrase is asked for, then the connection is opened with ``password=`` or ``client_keys=``. A
refused password means a new question and a new connection; three tries, then ``auth_failed``.

Nothing from the operator's machine leaks in: no ``~/.ssh/config``, no default keys, no agent, no GSSAPI.

Secrets
-------
Passwords, passphrases and private keys live in local variables and in the attempt object for the length of one
connection attempt, then they are dropped. They never reach a log line or the session record. Session ids are
not logged either; account and target are.
"""

from __future__ import annotations

import asyncio
import json
import logging
import posixpath
import secrets
import stat
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
from typing import Any

import asyncssh
from asyncssh.public_key import get_default_public_key_algs, get_public_key_algs
from fastapi import WebSocket
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect

from ..db import SessionLocal
from ..models import (
    AUTH_ASK,
    AUTH_KEY,
    AUTH_PASSWORD,
    END_CUT,
    END_FAILED,
    END_HOSTKEY,
    END_NORMAL,
    END_RUNNING,
    OPERATOR,
    Account,
    Connection,
    ConnectionShare,
    SessionRecord,
    utcnow,
)
from ..security import brake, session_account
from . import hostkeys, settings_service, targets, vault

logger = logging.getLogger("nextrmnl.ssh")
sftp_logger = logging.getLogger("nextrmnl.sftp")

#: TCP connect, handshake and authentication together; no person is waited for inside it.
CONNECT_TIMEOUT = 10
#: A question to the person waits this long. No server connection is open while it waits.
ANSWER_TIMEOUT = 300
AUTH_ATTEMPTS = 3
KEEPALIVE_SECONDS = 30
CHUNK = 64 * 1024
MAX_JUMPS = 5
TERM_TYPE = "xterm-256color"

# Details, all short English codes the frontend turns into sentences.
DETAIL_TARGET = "target_not_allowed"
DETAIL_VAULT = "vault_locked"
DETAIL_HOSTKEY = "hostkey"
DETAIL_TIMEOUT = "timeout"
DETAIL_UNREACHABLE = "unreachable"
DETAIL_AUTH = "auth_failed"
DETAIL_KEY_NOT_FOUND = "key_not_found"
DETAIL_KEY_UNREADABLE = "key_unreadable"
DETAIL_NO_ANSWER = "no_answer"
DETAIL_BROWSER = "browser_closed"
DETAIL_LOST = "connection_lost"
DETAIL_OPERATOR = "disconnected_by_operator"
DETAIL_SHUTDOWN = "server_shutdown"
DETAIL_EXIT = "exit"
DETAIL_INTERNAL = "internal_error"
#: The browser session behind the terminal ended: sign-out, deletion, a reset, or the operator ended it.
DETAIL_SIGNED_OUT = "signed_out"
#: Too many refused sign-ins at targets in a row; the account waits before the next connect.
DETAIL_TOO_MANY = "too_many_attempts"
#: Live sessions one account may hold; more is a script, not a person.
MAX_SESSIONS_PER_ACCOUNT = 25
#: How often a live session checks that its browser session still exists.
AUTH_WATCH_SECONDS = 30


class SessionEnded(Exception):
    """Stops the lifecycle with an end and a detail for the record."""

    def __init__(self, end: str, detail: str, message: str = "") -> None:
        super().__init__(detail)
        self.end, self.detail, self.message = end, detail, message


class BrowserGone(Exception):
    pass


class SessionNotOpen(Exception):
    pass


@dataclass
class Target:
    """Where to connect and how. Built from a saved connection or from quick-connect parameters."""

    host: str
    port: int
    user: str
    auth: str
    key_id: int | None = None
    connection_id: int | None = None
    name: str = ""
    keepalive: bool = True
    start_command: str = ""
    jump: Target | None = None
    #: The address the allowed-targets check resolved and judged; the connection goes there, not to a second
    #: resolution of the name that could answer differently.
    address: str = ""

    @property
    def label(self) -> str:
        return f"{self.user}@{self.host}:{self.port}"

    @property
    def endpoint(self) -> str:
        return self.address or self.host


def _accessible(db: Session, account: Account, connection_id: int) -> Connection | None:
    row = db.get(Connection, connection_id)
    if row is None:
        return None
    if row.owner_id == account.id:
        return row
    share = db.get(ConnectionShare, {"connection_id": connection_id, "account_id": account.id})
    return row if share is not None else None


def target_from_connection(db: Session, account: Account, row: Connection, depth: int = 0) -> Target:
    """A target with its chain of jump hosts. Every jump must be visible to the account too."""
    auth, key_id, user, start_command = row.auth, row.key_id, row.user, row.start_command
    if row.owner_id != account.id:
        # A shared connection carries the owner's key, which nobody else can read. The member signs in with
        # their own user name and their own key or password from their own vault (``ConnectionShare``), and
        # only their own command runs in their shell: the owner's would run with the member's rights.
        share = db.get(ConnectionShare, {"connection_id": row.id, "account_id": account.id})
        auth = share.auth if share is not None and share.auth in (AUTH_KEY, AUTH_PASSWORD, AUTH_ASK) else AUTH_ASK
        key_id = share.key_id if share is not None and auth == AUTH_KEY else None
        user = share.user if share is not None and share.user else row.user
        start_command = share.start_command if share is not None else ""
        if auth == AUTH_KEY and key_id is None:
            auth = AUTH_ASK
    jump = None
    if row.jump_id is not None:
        if depth >= MAX_JUMPS:
            raise ValueError("jump chain too long")
        jump_row = _accessible(db, account, row.jump_id)
        if jump_row is None:
            raise LookupError("jump host not visible")
        jump = target_from_connection(db, account, jump_row, depth + 1)
    return Target(
        host=row.host,
        port=row.port,
        user=user,
        auth=auth,
        key_id=key_id,
        connection_id=row.id,
        name=row.name,
        keepalive=row.keepalive,
        start_command=start_command,
        jump=jump,
    )


def quick_target(host: str, port: int, user: str) -> Target:
    return Target(host=host.strip(), port=port, user=user.strip(), auth=AUTH_ASK, name=f"{user.strip()}@{host.strip()}")


@dataclass
class _Attempt:
    """One handshake with one target: what it offered as host key and when the TCP connection stood."""

    offered: tuple[str, asyncssh.SSHKey, dict[str, Any] | None] | None = None
    connected_at: float | None = None
    started_at: float = 0.0
    via: str = ""


@dataclass
class _Credentials:
    """What signs in, settled *before* the connection is opened.

    asyncssh's ``connect_timeout`` covers the whole handshake including authentication, so a person typing a
    password inside an asyncssh callback would count toward it. Everything a person answers is therefore asked
    first and handed to ``connect`` as ``password=`` or ``client_keys=``. Dropped once the attempt is over.
    """

    password: str | None = None
    keys: list[asyncssh.SSHKey] | None = None
    via: str = ""
    typed: bool = False
    store: bool = False


class _Client(asyncssh.SSHClient):
    """asyncssh's view of a session: the host key check against the store. Credentials are not its business."""

    def __init__(self, target: Target, attempt: _Attempt) -> None:
        self._target, self._attempt = target, attempt

    def connection_made(self, conn: asyncssh.SSHClientConnection) -> None:
        self._attempt.connected_at = time.perf_counter()

    def validate_host_public_key(self, host: str, addr: str, port: int, key: asyncssh.SSHKey) -> bool:
        with SessionLocal() as db:
            state, row = hostkeys.compare(db, self._target.host, self._target.port, key)
            old = None
            if row is not None:
                old = {
                    "old_key_type": row.key_type,
                    "old_fingerprint": row.fingerprint,
                    "trusted_by": row.trusted_by,
                    "trusted_at": row.trusted_at.isoformat(),
                }
        if state == hostkeys.KNOWN:
            return True
        self._attempt.offered = (state, key, old)
        return False


def _host_key_algs(target: Target) -> list[str]:
    """Plain host keys only, the stored key's algorithms first so a server with several keys shows the known one."""
    defaults = [alg.decode("ascii") for alg in get_default_public_key_algs()]
    supported = {alg.decode("ascii") for alg in get_public_key_algs()}
    with SessionLocal() as db:
        row = hostkeys.stored(db, target.host, target.port)
        key = hostkeys.stored_key(row) if row is not None else None
    if key is None:
        return defaults
    preferred = [alg.decode("ascii") for alg in key.sig_algorithms if alg.decode("ascii") in supported]
    return preferred + [alg for alg in defaults if alg not in preferred]


def _classify(error: BaseException) -> SessionEnded:
    if isinstance(error, TimeoutError):
        return SessionEnded(END_FAILED, DETAIL_TIMEOUT)
    if isinstance(error, asyncssh.PermissionDenied):
        return SessionEnded(END_FAILED, DETAIL_AUTH, str(error))
    if isinstance(error, asyncssh.HostKeyNotVerifiable):
        return SessionEnded(END_HOSTKEY, DETAIL_HOSTKEY, str(error))
    if isinstance(error, asyncssh.ConnectionLost):
        return SessionEnded(END_FAILED, DETAIL_LOST, str(error))
    if isinstance(error, asyncssh.Error):
        return SessionEnded(END_FAILED, DETAIL_UNREACHABLE, str(error))
    if isinstance(error, OSError):
        # The text of an OSError is in the language of the operating system; the detail says enough.
        return SessionEnded(END_FAILED, DETAIL_UNREACHABLE)
    logger.exception("Unexpected error while connecting")
    return SessionEnded(END_FAILED, DETAIL_INTERNAL)


_sessions: dict[str, SshSession] = {}


def _sign_in_alive(token: str) -> bool:
    with SessionLocal() as db:
        return session_account(db, token) is not None


class SshSession:
    def __init__(self, account: Account, target: Target, from_ip: str, cols: int, rows: int) -> None:
        self.id = secrets.token_urlsafe(12)
        self.account_id = account.id
        self.account_name = account.name
        self.account_role = account.role
        #: The browser session's token; the terminal ends when that session does.
        self.session_token: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.target = target
        self.from_ip = from_ip
        self.cols, self.rows = cols, rows
        self.started_at = utcnow()
        self.record_id: int | None = None
        self.end = END_RUNNING
        self.detail = ""
        self.via = ""
        self.exit_status: int | None = None
        self.opened_at: float | None = None
        self.latency_ms: int | None = None
        self.conn: asyncssh.SSHClientConnection | None = None
        self.jump_conns: list[asyncssh.SSHClientConnection] = []
        self.process: asyncssh.SSHClientProcess | None = None
        self._sftp: asyncssh.SFTPClient | None = None
        self._home: str | None = None
        self._ws: WebSocket | None = None
        # Bounded: answers arrive one at a time; a browser sending thousands must not grow the server.
        self._answers: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=32)
        self._send_lock = asyncio.Lock()
        self._browser_gone = asyncio.Event()
        self._closed_sent = False
        self.done = asyncio.Event()

    # ------------------------------------------------------------------
    # Talking to the browser
    # ------------------------------------------------------------------

    async def _send(self, message: dict[str, Any]) -> None:
        if self._ws is None or self._browser_gone.is_set():
            return
        try:
            async with self._send_lock:
                await self._ws.send_text(json.dumps(message))
        except (WebSocketDisconnect, RuntimeError, OSError):
            self._browser_gone.set()

    async def _send_bytes(self, data: bytes) -> None:
        if self._ws is None or self._browser_gone.is_set():
            raise BrowserGone()
        try:
            async with self._send_lock:
                await self._ws.send_bytes(data)
        except (WebSocketDisconnect, RuntimeError, OSError) as error:
            self._browser_gone.set()
            raise BrowserGone() from error

    async def _read_browser(self) -> None:
        """Binary frames go to the shell, JSON frames are control messages or answers to our questions."""
        assert self._ws is not None
        try:
            while True:
                message = await self._ws.receive()
                if message["type"] == "websocket.disconnect":
                    break
                data = message.get("bytes")
                if data is not None:
                    if self.process is not None and not self.process.stdin.is_closing():
                        self.process.stdin.write(data)
                        try:
                            # Back-pressure: a browser pasting megabytes waits for the shell instead of filling
                            # the server's memory.
                            await self.process.stdin.drain()
                        except (asyncssh.Error, OSError, BrokenPipeError):
                            break
                    continue
                text = message.get("text")
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                except ValueError:
                    continue
                if not isinstance(payload, dict):
                    continue
                kind = payload.get("type")
                if kind == "resize":
                    self._resize(payload)
                elif kind in ("hostkey", "password", "passphrase"):
                    self._answers.put_nowait(payload)
        except (WebSocketDisconnect, RuntimeError, OSError):
            pass
        finally:
            self._browser_gone.set()
            self._answers.put_nowait(None)

    def _resize(self, payload: dict[str, Any]) -> None:
        try:
            cols, rows = int(payload.get("cols", 0)), int(payload.get("rows", 0))
        except (TypeError, ValueError):
            return
        if not (1 <= cols <= 1000 and 1 <= rows <= 1000):
            return
        self.cols, self.rows = cols, rows
        if self.process is not None and not self.process.is_closing():
            self.process.change_terminal_size(cols, rows)

    async def _ask(self, question: dict[str, Any], expect: str) -> dict[str, Any]:
        """Sends a question and waits for the answer of the expected type. Stale answers are dropped."""
        await self._send(question)
        while True:
            try:
                answer = await asyncio.wait_for(self._answers.get(), ANSWER_TIMEOUT)
            except TimeoutError as error:
                raise SessionEnded(END_FAILED, DETAIL_NO_ANSWER) from error
            if answer is None:
                raise BrowserGone()
            if answer.get("type") == "_cut":
                raise SessionEnded(self.end, self.detail)
            if answer.get("type") == expect:
                return answer

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def run(self, websocket: WebSocket) -> None:
        self._ws = websocket
        self._loop = asyncio.get_running_loop()
        _sessions[self.id] = self
        self._open_record()
        reader = asyncio.create_task(self._read_browser())
        try:
            await self._send({"type": "status", "state": "connecting", "session_id": self.id})
            if brake.wait_seconds(self._brake_key()):
                # Refused sign-ins at targets count per account: one member must not hammer a machine into
                # its fail2ban for everybody, and the server does not connect on a script's behalf all night.
                raise SessionEnded(END_FAILED, DETAIL_TOO_MANY)
            await self._check_targets()
            await self._connect_chain()
            if self.end != END_RUNNING:
                # The operator cut the session while it was still connecting.
                raise SessionEnded(self.end, self.detail)
            await self._open_shell()
            await self._announce_open()
            await self._serve_shell()
        except SessionEnded as ended:
            self._set_end(ended.end, ended.detail)
            await self._send(
                {"type": "status", "state": "failed", "detail": ended.detail, "message": ended.message, "end": self.end}
            )
        except BrowserGone:
            self._set_end(END_NORMAL if self.opened_at else END_FAILED, DETAIL_BROWSER)
        except asyncio.CancelledError:
            self._set_end(END_CUT, DETAIL_SHUTDOWN)
            raise
        except Exception:
            logger.exception("Session failed unexpectedly account=%s target=%s", self.account_name, self.target.label)
            self._set_end(END_FAILED, DETAIL_INTERNAL)
            await self._send({"type": "status", "state": "failed", "detail": DETAIL_INTERNAL, "end": END_FAILED})
        finally:
            reader.cancel()
            try:
                self._teardown()
            except Exception:
                # Nothing may escape to the router: the socket is about to be closed either way.
                logger.exception("Session teardown failed account=%s", self.account_name)

    async def _check_targets(self) -> None:
        with SessionLocal() as db:
            mode = settings_service.get(db, "targets_mode")
            entries = targets.parse_list(settings_service.get(db, "targets_list"))
        target: Target | None = self.target
        while target is not None:
            verdict = await targets.check(target.host, target.port, mode, entries)
            if not verdict.allowed:
                logger.warning(
                    "Target refused account=%s target=%s reason=%s mode=%s",
                    self.account_name,
                    target.label,
                    verdict.reason,
                    mode,
                )
                raise SessionEnded(END_FAILED, DETAIL_TARGET, verdict.reason)
            if verdict.addresses:
                # Connect to what was judged. A name whose owner answers differently the second time (a short
                # TTL, a rebinding trick) would otherwise walk the policy around.
                target.address = verdict.addresses[0]
            target = target.jump

    def _brake_key(self) -> str:
        return f"connect:{self.account_id}"

    async def _connect_chain(self) -> None:
        """Jump hosts first, innermost last; each one becomes the tunnel of the next."""
        chain: list[Target] = []
        target: Target | None = self.target
        while target is not None:
            chain.append(target)
            target = target.jump
        tunnel: asyncssh.SSHClientConnection | None = None
        for hop in reversed(chain):
            conn, attempt = await self._connect_one(hop, tunnel)
            if hop is self.target:
                self.conn = conn
                self.via = attempt.via
                if attempt.connected_at is not None:
                    self.latency_ms = int((attempt.connected_at - attempt.started_at) * 1000)
            else:
                self.jump_conns.append(conn)
            tunnel = conn

    async def _connect_one(
        self, target: Target, tunnel: asyncssh.SSHClientConnection | None
    ) -> tuple[asyncssh.SSHClientConnection, _Attempt]:
        """Host key first, then credentials, then the connection; a refused password starts over with a new one."""
        if target.auth in (AUTH_KEY, AUTH_PASSWORD) and not vault.is_open(self.account_id):
            raise SessionEnded(END_FAILED, DETAIL_VAULT)
        with SessionLocal() as db:
            known = hostkeys.stored(db, target.host, target.port) is not None
        if not known:
            await self._settle_host_key(target, tunnel)
        keys, key_via = (await self._load_key(target)) if target.auth == AUTH_KEY else (None, "")
        tries = 1 if keys is not None else AUTH_ATTEMPTS
        refused = ""
        for number in range(tries):
            if keys is not None:
                credentials = _Credentials(keys=keys, via=key_via)
            else:
                credentials = await self._password_credentials(target, number, refused)
            try:
                conn, attempt = await self._open(target, tunnel, credentials)
            except asyncssh.PermissionDenied:
                credentials.password = None
                refused = "stored" if credentials.via == "stored password" else "typed"
                brake.failed(self._brake_key())
                logger.info(
                    "Sign-in refused account=%s target=%s via=%s try=%s", self.account_name, target.label,
                    credentials.via, number + 1,
                )
                continue
            brake.succeeded(self._brake_key())
            self._remember_password(target, credentials)
            return conn, attempt
        raise SessionEnded(END_FAILED, DETAIL_AUTH)

    def _connect_arguments(self, target: Target, tunnel: asyncssh.SSHClientConnection | None) -> dict[str, Any]:
        """The same for every handshake: nothing from the operator's machine, our host key check, our timeouts."""
        return {
            "username": target.user,
            "tunnel": tunnel,
            "known_hosts": ([], [], []),
            "server_host_key_algs": _host_key_algs(target),
            "agent_path": None,
            "config": None,
            "gss_host": None,
            "connect_timeout": CONNECT_TIMEOUT,
            "keepalive_interval": KEEPALIVE_SECONDS if target.keepalive else 0,
            "keepalive_count_max": 3,
        }

    async def _settle_host_key(self, target: Target, tunnel: asyncssh.SSHClientConnection | None) -> None:
        """First contact with a server: a handshake without credentials that stops at the host key.

        The person sees the fingerprint before typing anything, as with ``ssh``. Once the key is trusted the real
        connection finds it in the store; if the key turned out to be known meanwhile, this handshake ends in a
        harmless refusal because no credentials were offered.
        """
        attempt = _Attempt(started_at=time.perf_counter())
        logger.debug("Probing host key account=%s target=%s", self.account_name, target.label)
        try:
            conn = await asyncssh.connect(
                target.endpoint,
                target.port,
                client_factory=partial(_Client, target, attempt),
                client_keys=None,
                preferred_auth=["publickey"],
                **self._connect_arguments(target, tunnel),
            )
        except asyncssh.PermissionDenied:
            return
        except Exception as error:
            if attempt.offered is not None:
                state, key, old = attempt.offered
                await self._hostkey_dialog(target, state, key, old)
                return
            raise self._failed(target, error) from error
        conn.close()

    async def _open(
        self, target: Target, tunnel: asyncssh.SSHClientConnection | None, credentials: _Credentials
    ) -> tuple[asyncssh.SSHClientConnection, _Attempt]:
        """One connection with settled credentials. A changed host key is put to the person once, then retried."""
        preferred = ["publickey"] if credentials.keys else ["keyboard-interactive", "password"]
        for _round in (1, 2):
            attempt = _Attempt(started_at=time.perf_counter(), via=credentials.via)
            logger.debug("Connecting account=%s target=%s via=%s jump=%s", self.account_name, target.label,
                         credentials.via, tunnel is not None)
            try:
                conn = await asyncssh.connect(
                    target.endpoint,
                    target.port,
                    client_factory=partial(_Client, target, attempt),
                    client_keys=credentials.keys or None,
                    password=credentials.password,
                    preferred_auth=preferred,
                    **self._connect_arguments(target, tunnel),
                )
            except asyncssh.PermissionDenied:
                raise
            except Exception as error:
                if attempt.offered is not None:
                    state, key, old = attempt.offered
                    await self._hostkey_dialog(target, state, key, old)
                    continue
                raise self._failed(target, error) from error
            return conn, attempt
        raise SessionEnded(END_HOSTKEY, DETAIL_HOSTKEY)

    def _failed(self, target: Target, error: BaseException) -> SessionEnded:
        ended = _classify(error)
        logger.info("Connect failed account=%s target=%s detail=%s", self.account_name, target.label, ended.detail)
        return ended

    async def _hostkey_dialog(
        self, target: Target, state: str, key: asyncssh.SSHKey, old: dict[str, Any] | None
    ) -> None:
        question: dict[str, Any] = {
            "type": "hostkey",
            "state": state,
            "host": target.host,
            "port": target.port,
            "for": target.name,
            "key_type": key.get_algorithm(),
            "fingerprint": key.get_fingerprint(),
        }
        if old:
            question.update(old)
            logger.warning(
                "Host key changed for %s:%s stored=%s offered=%s account=%s",
                target.host,
                target.port,
                old["old_fingerprint"],
                key.get_fingerprint(),
                self.account_name,
            )
            if self.account_role != OPERATOR:
                # The store is shared by every account. A member replacing a changed key would make the
                # impostor's key the known one for everybody else, without anybody else ever seeing a warning.
                raise SessionEnded(
                    END_HOSTKEY,
                    DETAIL_HOSTKEY,
                    "The host key of this machine has changed. Only the operator can accept the new key.",
                )
        answer = await self._ask(question, "hostkey")
        if answer.get("accept") is not True:
            logger.warning(
                "Host key %s refused for %s:%s fingerprint=%s by=%s",
                state,
                target.host,
                target.port,
                key.get_fingerprint(),
                self.account_name,
            )
            raise SessionEnded(END_HOSTKEY, DETAIL_HOSTKEY)
        with SessionLocal() as db:
            hostkeys.trust(db, target.host, target.port, key, self.account_name)

    async def _password_credentials(self, target: Target, number: int, refused: str) -> _Credentials:
        """The stored password on the first try if there is one, otherwise the person is asked."""
        if target.auth == AUTH_PASSWORD and number == 0 and target.connection_id is not None:
            with SessionLocal() as db:
                try:
                    stored = vault.get_password(
                        db, self.account_id, target.connection_id, host=target.host, port=target.port, user=target.user
                    )
                except vault.VaultLocked as error:
                    raise SessionEnded(END_FAILED, DETAIL_VAULT) from error
            if stored is not None:
                return _Credentials(password=stored, via="stored password")
        question = {
            "type": "need",
            "what": "password",
            "for": target.name,
            "user": target.user,
            "host": target.host,
            "retry": refused != "",
            "stored_failed": refused == "stored",
            "can_store": target.connection_id is not None and vault.is_open(self.account_id),
        }
        answer = await self._ask(question, "password")
        value = answer.get("value")
        return _Credentials(
            password=value if isinstance(value, str) else "",
            via="password",
            typed=True,
            store=bool(answer.get("store")) and target.connection_id is not None,
        )

    async def _load_key(self, target: Target) -> tuple[list[asyncssh.SSHKey], str]:
        """The private key from the vault, asking for its passphrase if it has one. Three tries."""
        if target.key_id is None:
            raise SessionEnded(END_FAILED, DETAIL_KEY_NOT_FOUND)
        with SessionLocal() as db:
            try:
                row, text = vault.private_key_text(db, self.account_id, target.key_id)
            except vault.VaultLocked as error:
                raise SessionEnded(END_FAILED, DETAIL_VAULT) from error
            except LookupError as error:
                raise SessionEnded(END_FAILED, DETAIL_KEY_NOT_FOUND) from error
            name, protected = row.name, row.has_passphrase
        tries = AUTH_ATTEMPTS if protected else 1
        for number in range(tries):
            passphrase = None
            if protected:
                answer = await self._ask(
                    {"type": "need", "what": "passphrase", "key": name, "for": target.name, "retry": number > 0},
                    "passphrase",
                )
                value = answer.get("value")
                passphrase = value if isinstance(value, str) and value else None
            try:
                key = asyncssh.import_private_key(text, passphrase)
            except (asyncssh.KeyImportError, asyncssh.KeyEncryptionError):
                if not protected:
                    raise SessionEnded(END_FAILED, DETAIL_KEY_UNREADABLE) from None
                continue
            return [key], f"key {name}"
        raise SessionEnded(END_FAILED, DETAIL_AUTH, "The passphrase was wrong three times.")

    def _remember_password(self, target: Target, credentials: _Credentials) -> None:
        """Stores a typed password if the person asked for it, then forgets it either way."""
        password, credentials.password = credentials.password, None
        if not (credentials.typed and credentials.store and password and target.connection_id is not None):
            return
        with SessionLocal() as db:
            account = db.get(Account, self.account_id)
            if account is None:
                return
            try:
                vault.set_password(
                    db, account, target.connection_id, password, host=target.host, port=target.port, user=target.user
                )
            except vault.VaultLocked:
                logger.info("Password not stored, vault locked account=%s connection_id=%s", self.account_name,
                            target.connection_id)

    async def _open_shell(self) -> None:
        assert self.conn is not None
        self.process = await self.conn.create_process(
            term_type=TERM_TYPE, term_size=(self.cols, self.rows), encoding=None, stderr=asyncssh.STDOUT
        )
        if self.target.start_command:
            self.process.stdin.write(self.target.start_command.encode("utf-8") + b"\n")

    async def _announce_open(self) -> None:
        self.opened_at = time.perf_counter()
        with SessionLocal() as db:
            from ..routers.connections import touch

            touch(db, self.target.connection_id)
        jump = self.target.jump.name if self.target.jump else None
        logger.info(
            "Session opened account=%s target=%s via=%s jump=%s from=%s",
            self.account_name,
            self.target.label,
            self.via,
            jump or "-",
            self.from_ip,
        )
        await self._send(
            {
                "type": "status",
                "state": "open",
                "session_id": self.id,
                "via": self.via,
                "jump": jump,
                "latency_ms": self.latency_ms,
            }
        )

    async def _serve_shell(self) -> None:
        """Pumps shell output to the browser until the shell exits, the browser leaves, or the sign-in ends."""
        assert self.process is not None
        pump = asyncio.create_task(self._pump())
        gone = asyncio.create_task(self._browser_gone.wait())
        watch = asyncio.create_task(self._watch_auth())
        done, _pending = await asyncio.wait({pump, gone, watch}, return_when=asyncio.FIRST_COMPLETED)
        watch.cancel()
        if pump not in done:
            pump.cancel()
            gone.cancel()
            if watch in done:
                self._set_end(END_CUT, DETAIL_SIGNED_OUT)
                await self._send_closed()
                self._close_connections()
                return
            raise BrowserGone()
        gone.cancel()
        lost = pump.result()
        try:
            await asyncio.wait_for(self.process.wait_closed(), 5)
        except (TimeoutError, asyncssh.Error, OSError):
            pass
        self.exit_status = self.process.exit_status
        if lost:
            self._set_end(END_CUT, DETAIL_LOST)
        else:
            self._set_end(END_NORMAL, DETAIL_EXIT)
        await self._send_closed()

    async def _watch_auth(self) -> None:
        """Returns once the browser session behind this terminal is gone: signed out elsewhere, deleted, expired.
        A cookie is checked at the handshake only; without this a shell would outlive the sign-in it came with."""
        while True:
            await asyncio.sleep(AUTH_WATCH_SECONDS)
            token = self.session_token
            if token is None:
                continue
            if not await asyncio.to_thread(_sign_in_alive, token):
                logger.info("Session cut, sign-in ended account=%s target=%s", self.account_name, self.target.label)
                return

    async def _send_closed(self) -> None:
        """Tells the browser once how the session ended, whoever ended it."""
        if self._closed_sent:
            return
        self._closed_sent = True
        await self._send(
            {
                "type": "status",
                "state": "closed",
                "end": self.end,
                "detail": self.detail,
                "exit_status": self.exit_status,
            }
        )

    async def _pump(self) -> bool:
        """Returns True if the connection was lost rather than closed."""
        assert self.process is not None
        stdout = self.process.stdout
        while True:
            try:
                data = await stdout.read(CHUNK)
            except (asyncssh.Error, OSError):
                return True
            if not data:
                return False
            await self._send_bytes(data)

    # ------------------------------------------------------------------
    # Ending
    # ------------------------------------------------------------------

    def _set_end(self, end: str, detail: str) -> None:
        """The first end wins: an operator's cut is not overwritten by the exit it causes."""
        if self.end == END_RUNNING:
            self.end, self.detail = end, detail

    def cut(self, end: str, detail: str) -> None:
        """Ends the session from outside (operator, shutdown). The lifecycle notices and cleans up."""
        self._set_end(end, detail)
        with suppress(asyncio.QueueFull):
            self._answers.put_nowait({"type": "_cut"})
        self._close_connections()

    def _close_connections(self) -> None:
        if self._sftp is not None:
            self._sftp.exit()
            self._sftp = None
        if self.process is not None and not self.process.is_closing():
            self.process.close()
        for conn in [self.conn, *reversed(self.jump_conns)]:
            if conn is not None:
                conn.close()

    def _teardown(self) -> None:
        """Synchronous on purpose: it must finish even while the task is being cancelled."""
        if self.done.is_set():
            return
        self._set_end(END_FAILED, DETAIL_INTERNAL)
        self._close_connections()
        _sessions.pop(self.id, None)
        try:
            self._close_record()
        except Exception:
            logger.exception("Session record could not be closed account=%s", self.account_name)
        duration = int(time.perf_counter() - self.opened_at) if self.opened_at else 0
        if self.opened_at:
            logger.info(
                "Session closed account=%s target=%s duration=%ss end=%s detail=%s exit_status=%s",
                self.account_name,
                self.target.label,
                duration,
                self.end,
                self.detail,
                self.exit_status if self.exit_status is not None else "-",
            )
        else:
            logger.info(
                "Session not opened account=%s target=%s end=%s detail=%s",
                self.account_name,
                self.target.label,
                self.end,
                self.detail,
            )
        self.done.set()

    def _open_record(self) -> None:
        with SessionLocal() as db:
            record = SessionRecord(
                account_id=self.account_id,
                connection_id=self.target.connection_id,
                name=self.target.name[:64],
                target=self.target.label[:320],
                from_ip=self.from_ip[:64],
                started_at=self.started_at,
                end=END_RUNNING,
            )
            db.add(record)
            db.commit()
            self.record_id = record.id

    def _close_record(self) -> None:
        if self.record_id is None:
            return
        with SessionLocal() as db:
            record = db.get(SessionRecord, self.record_id)
            if record is None:
                return
            record.ended_at = utcnow()
            record.end = self.end
            record.detail = self.detail[:255]
            db.commit()

    # ------------------------------------------------------------------
    # SFTP on the same connection
    # ------------------------------------------------------------------

    async def sftp(self) -> asyncssh.SFTPClient:
        if self.conn is None or self.opened_at is None or self.end != END_RUNNING:
            raise SessionNotOpen()
        if self._sftp is None:
            self._sftp = await self.conn.start_sftp_client()
            home = await self._sftp.realpath(".")
            self._home = home.decode("utf-8", "replace") if isinstance(home, bytes) else str(home)
            sftp_logger.debug("SFTP started account=%s target=%s", self.account_name, self.target.label)
        return self._sftp

    @property
    def home(self) -> str:
        return self._home or "/"

    def _remote(self, path: str) -> str:
        """Paths come from the browser as given; only ``~`` is ours to resolve."""
        path = (path or "").strip() or "~"
        if path == "~":
            return self.home
        if path.startswith("~/"):
            return posixpath.join(self.home, path[2:])
        return path

    async def listdir(self, path: str) -> dict[str, Any]:
        sftp = await self.sftp()
        resolved = _text(await sftp.realpath(self._remote(path)))
        entries = []
        for name in await sftp.readdir(resolved):
            filename = _text(name.filename)
            if filename in (".", ".."):
                continue
            attrs = name.attrs
            entries.append(
                {
                    "name": filename,
                    "type": _entry_type(attrs),
                    "size": attrs.size or 0,
                    "mtime": attrs.mtime,
                    "mode": (attrs.permissions & 0o7777) if attrs.permissions is not None else None,
                }
            )
        entries.sort(key=lambda entry: (entry["type"] != "dir", entry["name"].lower()))
        sftp_logger.debug("SFTP list account=%s path=%s entries=%s", self.account_name, resolved, len(entries))
        return {"path": resolved, "home": self.home, "entries": entries}

    async def stat(self, path: str) -> dict[str, Any]:
        sftp = await self.sftp()
        remote = self._remote(path)
        attrs = await sftp.stat(remote)
        return {
            "path": remote,
            "name": posixpath.basename(remote.rstrip("/")) or remote,
            "type": _entry_type(attrs),
            "size": attrs.size or 0,
            "mtime": attrs.mtime,
            "mode": (attrs.permissions & 0o7777) if attrs.permissions is not None else None,
        }

    async def download(self, path: str) -> tuple[str, int | None, AsyncIterator[bytes]]:
        """Name, size if known and the chunks of a remote file."""
        sftp = await self.sftp()
        remote = self._remote(path)
        attrs = await sftp.stat(remote)
        if _entry_type(attrs) == "dir":
            raise asyncssh.SFTPFailure("Is a directory")
        size = attrs.size
        sftp_logger.debug("SFTP download account=%s path=%s size=%s", self.account_name, remote, size)

        async def chunks() -> AsyncIterator[bytes]:
            async with sftp.open(remote, "rb") as handle:
                while True:
                    chunk = await handle.read(CHUNK)
                    if not chunk:
                        break
                    yield chunk

        return posixpath.basename(remote.rstrip("/")) or "download", size, chunks()

    async def upload(self, directory: str, name: str, chunks: AsyncIterator[bytes]) -> tuple[str, int]:
        sftp = await self.sftp()
        remote = posixpath.join(self._remote(directory), name)
        total = 0
        # Opened outside the guard: if the file cannot be opened at all (read-only, no permission), nothing was
        # written and the existing file is not ours to remove.
        handle = await sftp.open(remote, "wb")
        try:
            async with handle:
                async for chunk in chunks:
                    await handle.write(chunk)
                    total += len(chunk)
        except BaseException:
            # A half file is worse than none; the browser shows the error and the person tries again.
            try:
                await sftp.remove(remote)
            except (asyncssh.Error, OSError):
                pass
            raise
        sftp_logger.debug("SFTP upload account=%s path=%s size=%s", self.account_name, remote, total)
        return remote, total

    async def mkdir(self, path: str) -> str:
        sftp = await self.sftp()
        remote = self._remote(path)
        await sftp.mkdir(remote)
        sftp_logger.debug("SFTP mkdir account=%s path=%s", self.account_name, remote)
        return remote

    async def rename(self, path: str, to: str) -> str:
        sftp = await self.sftp()
        source, destination = self._remote(path), self._remote(to)
        if not destination.startswith("/"):
            destination = posixpath.join(posixpath.dirname(source), destination)
        await sftp.rename(source, destination)
        sftp_logger.debug("SFTP rename account=%s path=%s to=%s", self.account_name, source, destination)
        return destination

    async def remove(self, path: str, recursive: bool = False) -> str:
        """Files and empty directories; with ``recursive`` a directory with everything in it. A link to a
        directory is a link and goes as a file, its target stays."""
        sftp = await self.sftp()
        remote = self._remote(path)
        attrs = await sftp.lstat(remote)
        if _entry_type(attrs) == "dir":
            if recursive:
                await sftp.rmtree(remote)
            else:
                await sftp.rmdir(remote)
        else:
            await sftp.remove(remote)
        sftp_logger.debug("SFTP remove account=%s path=%s recursive=%s", self.account_name, remote, recursive)
        return remote


def _text(value: bytes | str) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


def _entry_type(attrs: asyncssh.SFTPAttrs) -> str:
    if attrs.type == asyncssh.FILEXFER_TYPE_DIRECTORY:
        return "dir"
    if attrs.type == asyncssh.FILEXFER_TYPE_SYMLINK:
        return "link"
    if attrs.type == asyncssh.FILEXFER_TYPE_REGULAR:
        return "file"
    mode = attrs.permissions or 0
    if stat.S_ISDIR(mode):
        return "dir"
    if stat.S_ISLNK(mode):
        return "link"
    return "file"


# ----------------------------------------------------------------------
# Registry
# ----------------------------------------------------------------------


def get(session_id: str) -> SshSession | None:
    return _sessions.get(session_id)


def count_for(account_id: int) -> int:
    return sum(1 for session in _sessions.values() if session.account_id == account_id)


def close_account_sessions(account_id: int, detail: str) -> int:
    """Cuts every live session of an account, safe to call from any thread: sign-out, deletion, a reset by
    the operator. The sessions notice on their own loop and tell their browsers why."""
    sessions = [session for session in _sessions.values() if session.account_id == account_id]
    for session in sessions:
        loop = session._loop
        if loop is None or loop.is_closed():
            continue
        loop.call_soon_threadsafe(session.cut, END_CUT, detail)
    if sessions:
        logger.info("Cutting %s live sessions of account_id=%s: %s", len(sessions), account_id, detail)
    return len(sessions)


def running(account: Account) -> list[SshSession]:
    """The operator sees every live session, a member only their own."""
    sessions = list(_sessions.values())
    if account.role != OPERATOR:
        sessions = [session for session in sessions if session.account_id == account.id]
    return sorted(sessions, key=lambda session: session.started_at)


async def disconnect(session_id: str, by_name: str) -> bool:
    session = _sessions.get(session_id)
    if session is None:
        return False
    logger.info("Session disconnected by operator=%s account=%s target=%s", by_name, session.account_name,
                session.target.label)
    session._set_end(END_CUT, DETAIL_OPERATOR)
    await session._send_closed()
    session.cut(END_CUT, DETAIL_OPERATOR)
    return True


async def close_all(reason: str) -> None:
    sessions = list(_sessions.values())
    if not sessions:
        return
    logger.info("Closing %s live sessions: %s", len(sessions), reason)
    for session in sessions:
        session._set_end(END_CUT, DETAIL_SHUTDOWN)
        await session._send_closed()
        session.cut(END_CUT, DETAIL_SHUTDOWN)
    waits = [asyncio.wait_for(session.done.wait(), 3) for session in sessions]
    await asyncio.gather(*waits, return_exceptions=True)
    for session in sessions:
        if session.id in _sessions:
            # The WebSocket handler did not get to its own cleanup; the record must not stay "running".
            session._teardown()


def history(db: Session, account: Account, limit: int) -> list[SessionRecord]:
    statement = select(SessionRecord).order_by(SessionRecord.started_at.desc(), SessionRecord.id.desc()).limit(limit)
    if account.role != OPERATOR:
        statement = statement.where(SessionRecord.account_id == account.id)
    return list(db.scalars(statement))


def close_stale_records(db: Session) -> int:
    """Records still ``running`` without a live session behind them: left over from a crash or a hard stop."""
    live = {session.record_id for session in _sessions.values() if session.record_id is not None}
    running = db.scalars(select(SessionRecord).where(SessionRecord.end == END_RUNNING))
    stale = [row for row in running if row.id not in live]
    for row in stale:
        row.end, row.detail, row.ended_at = END_CUT, DETAIL_SHUTDOWN, utcnow()
    if stale:
        db.commit()
        logger.info("Closed %s session records left running by an earlier process", len(stale))
    return len(stale)


def purge_history(db: Session) -> int:
    """Deletes records older than ``history_days``. Live sessions stay whatever their age."""
    close_stale_records(db)
    days = int(settings_service.get(db, "history_days") or 90)
    limit = utcnow() - timedelta(days=max(1, days))
    result = db.execute(
        delete(SessionRecord).where(SessionRecord.started_at < limit, SessionRecord.end != END_RUNNING)
    )
    db.commit()
    removed = result.rowcount or 0
    if removed:
        logger.info("Session history purged removed=%s older_than_days=%s", removed, days)
    return removed

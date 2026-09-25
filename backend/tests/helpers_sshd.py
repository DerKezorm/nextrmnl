"""An SSH server for the tests: asyncssh, in-process, in its own thread with its own event loop.

The app under test runs in the TestClient's portal loop; the server must not share that loop, otherwise a
blocking ``receive_json`` in the test thread would starve it. So the server lives in a daemon thread and the
tests talk to it only through the app. A fresh host key per server, so nothing is trusted by accident.

The shell is a tiny echo loop: every line comes back with ``echo: `` in front, ``size`` reports the terminal
size, ``exit`` (optionally with a code) ends the process. SFTP is rooted in a temporary directory.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import anyio
import asyncssh
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

ORIGIN = {"origin": "http://testserver"}
USER = "tester"
PASSWORD = "correct-horse-battery"
WAIT = 15


class Sshd:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.host_key = asyncssh.generate_private_key("ssh-ed25519")
        self.authorized: list[asyncssh.SSHKey] = []
        self.port = 0
        self.resizes: list[tuple[int, int]] = []
        self.password_tries = 0
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="test-sshd", daemon=True)
        self._acceptor: asyncssh.SSHAcceptor | None = None

    @property
    def fingerprint(self) -> str:
        return self.host_key.get_fingerprint()

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()
        self._acceptor = asyncio.run_coroutine_threadsafe(self._serve(self.port), self._loop).result(WAIT)
        self.port = self._acceptor.get_port()

    def stop(self) -> None:
        if self._acceptor is not None:
            asyncio.run_coroutine_threadsafe(self._close(), self._loop).result(WAIT)
            self._acceptor = None

    def shutdown(self) -> None:
        self.stop()
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(WAIT)

    def rotate_host_key(self) -> None:
        """Same port, new host key: what a reinstalled or impersonated server looks like."""
        self.stop()
        self.host_key = asyncssh.generate_private_key("ssh-ed25519")
        self.start()

    def authorize(self, public_key_text: str) -> None:
        self.authorized.append(asyncssh.import_public_key(public_key_text))

    async def _close(self) -> None:
        assert self._acceptor is not None
        self._acceptor.close()
        await self._acceptor.wait_closed()

    async def _serve(self, port: int) -> asyncssh.SSHAcceptor:
        sshd = self

        class Server(asyncssh.SSHServer):
            def begin_auth(self, username: str) -> bool:
                return True

            def password_auth_supported(self) -> bool:
                return True

            def validate_password(self, username: str, password: str) -> bool:
                sshd.password_tries += 1
                return username == USER and password == PASSWORD

            def public_key_auth_supported(self) -> bool:
                return True

            def validate_public_key(self, username: str, key: asyncssh.SSHKey) -> bool:
                return username == USER and key in sshd.authorized

            def connection_requested(self, dest_host: str, dest_port: int, orig_host: str, orig_port: int) -> bool:
                # Port forwarding, so the server can be its own jump host in the tests.
                return dest_host in ("127.0.0.1", "localhost")

        class Sftp(asyncssh.SFTPServer):
            def __init__(self, chan: asyncssh.SSHServerChannel) -> None:
                super().__init__(chan, chroot=str(sshd.root).encode())

        return await asyncssh.create_server(
            Server,
            "127.0.0.1",
            port,
            server_host_keys=[self.host_key],
            process_factory=self._shell,
            sftp_factory=Sftp,
            allow_scp=False,
        )

    async def _shell(self, process: asyncssh.SSHServerProcess) -> None:
        process.stdout.write("welcome\n")
        while True:
            try:
                line = await process.stdin.readline()
            except asyncssh.TerminalSizeChanged as change:
                self.resizes.append((change.width, change.height))
                continue
            except asyncssh.BreakReceived:
                continue
            if not line:
                break
            line = line.rstrip("\r\n")
            if line.startswith("exit"):
                parts = line.split()
                process.exit(int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0)
                return
            if line == "size":
                width, height, _, _ = process.get_terminal_size()
                process.stdout.write(f"size: {width}x{height}\n")
                continue
            process.stdout.write(f"echo: {line}\n")
        process.exit(0)


@contextmanager
def running_sshd(root: Path) -> Iterator[Sshd]:
    """A started server rooted in ``root``; the test modules wrap this in a fixture named ``sshd``."""
    root.mkdir(parents=True, exist_ok=True)
    server = Sshd(root)
    server.start()
    try:
        yield server
    finally:
        server.shutdown()


# ----------------------------------------------------------------------
# Talking to the app's WebSocket from the test thread
# ----------------------------------------------------------------------


async def _receive_within(stream: Any, seconds: float) -> Any:
    with anyio.fail_after(seconds):
        return await stream.receive()


def receive(ws: WebSocketTestSession, timeout: float = WAIT) -> dict[str, Any]:
    """One raw ASGI message from the app, or a failure after ``timeout`` instead of hanging forever."""
    try:
        message = ws.portal.call(_receive_within, ws._send_rx, timeout)
    except TimeoutError:
        raise AssertionError(f"no message from the app within {timeout}s") from None
    if message["type"] == "websocket.close":
        raise WebSocketDisconnect(code=message.get("code", 1000), reason=message.get("reason", ""))
    return message


def receive_json(ws: WebSocketTestSession, timeout: float = WAIT) -> dict[str, Any]:
    """The next JSON frame; binary frames in between are dropped."""
    while True:
        message = receive(ws, timeout)
        if message.get("text") is not None:
            return json.loads(message["text"])


def receive_until(ws: WebSocketTestSession, needle: bytes, timeout: float = WAIT) -> bytes:
    """Collects binary frames until ``needle`` appears; JSON frames in between fail the test."""
    data = b""
    deadline = time.monotonic() + timeout
    while needle not in data:
        remaining = max(0.1, deadline - time.monotonic())
        message = receive(ws, remaining)
        if message.get("text") is not None:
            raise AssertionError(f"expected terminal data, got {message['text']}")
        data += message["bytes"]
    return data


def wait_for(predicate: Callable[[], bool], timeout: float = WAIT) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        time.sleep(0.05)


def ws_url(connection_id: int | None = None, **quick: Any) -> str:
    params = {"cols": 80, "rows": 24}
    if connection_id is not None:
        params["connection_id"] = connection_id
    params.update(quick)
    return "/api/sessions/ws?" + "&".join(f"{key}={value}" for key, value in params.items())


def connect_ws(client: TestClient, connection_id: int | None = None, **quick: Any) -> WebSocketTestSession:
    return client.websocket_connect(ws_url(connection_id, **quick), headers=ORIGIN)


def open_session(ws: WebSocketTestSession, accept_key: bool = True) -> dict[str, Any]:
    """Walks through connecting and a new host key; returns the ``open`` status."""
    connecting = receive_json(ws)
    assert connecting["state"] == "connecting", connecting
    message = receive_json(ws)
    if message["type"] == "hostkey":
        ws.send_json({"type": "hostkey", "accept": accept_key})
        message = receive_json(ws)
    return message

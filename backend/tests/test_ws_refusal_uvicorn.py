"""A refused terminal handshake under a real uvicorn, the way a browser sees it.

The TestClient shows the close code of a socket closed before ``accept``; uvicorn answers that with a bare HTTP
403, and a browser reports 1006 without any reason. So every refusal looked like a lost connection.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator

import pytest
import uvicorn
from fastapi.testclient import TestClient
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from app.main import app
from tests.conftest import session_token


@pytest.fixture(name="server")
def _server() -> Iterator[str]:
    """``host:port`` of nextrmnl under uvicorn, in a thread; the test client's database is the same."""
    config = uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="off", log_level="warning", proxy_headers=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "uvicorn did not start"
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def _close_code(server: str, query: str, origin: str, cookie: str | None = None) -> int:
    headers = {"Cookie": f"nextrmnl_session={cookie}"} if cookie else None
    url = f"ws://{server}/api/sessions/ws?{query}"
    with (
        connect(url, origin=origin, additional_headers=headers, open_timeout=5) as ws,  # type: ignore[arg-type]
        pytest.raises(ConnectionClosed) as closed,
    ):
        ws.recv(timeout=5)
    assert closed.value.rcvd is not None, "closed without a close frame"
    return closed.value.rcvd.code


def test_the_browser_learns_why_the_terminal_was_refused(server: str, client: TestClient, operator: dict) -> None:
    own = f"http://{server}"
    quick = "host=192.0.2.1&port=22&user=root"
    assert _close_code(server, quick, "http://evil.example.com") == 4403
    assert _close_code(server, quick, own) == 4401
    token = session_token(client)
    assert _close_code(server, "connection_id=999999", own, token) == 4404
    assert _close_code(server, "host=&user=", own, token) == 4400
    assert _close_code(server, "attach=no-such-session", own, token) == 4404

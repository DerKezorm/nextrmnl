"""A shell outlives its browser for a while: reload, closed tab, dropped network. Who may take it back, what the
returning browser sees, and what still ends it."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from app.models import MEMBER, Account
from app.services import ssh
from tests.conftest import UI, invite_member
from tests.helpers_sshd import (
    ORIGIN,
    USER,
    Sshd,
    connect_ws,
    open_session,
    receive,
    receive_json,
    receive_until,
    running_sshd,
    wait_for,
)
from tests.test_ssh import make_connection, store_password


@pytest.fixture(name="sshd")
def _sshd(tmp_path: Path, client: TestClient) -> Iterator[Sshd]:
    with running_sshd(tmp_path / "sftp-root") as server:
        yield server
        # Waiting shells hold their connection; the server would wait for them on shutdown.
        for account_id in {session.account_id for session in list(ssh._sessions.values())}:
            ssh.close_account_sessions(account_id, "test over")
        wait_for(lambda: not ssh._sessions)


def attach_ws(client: TestClient, session_id: str) -> WebSocketTestSession:
    return client.websocket_connect(f"/api/sessions/ws?attach={session_id}", headers=ORIGIN)


def running(client: TestClient) -> list[dict]:
    return client.get("/api/sessions/running").json()


def leave_open_shell(client: TestClient, sshd: Sshd) -> str:
    """Opens a shell, types a line, and drops the browser without saying ``close``. Returns the session id."""
    cid = make_connection(client, sshd, "password")
    store_password(client, cid)
    with connect_ws(client, cid) as ws:
        opened = open_session(ws)
        assert opened["state"] == "open"
        receive_until(ws, b"welcome")
        ws.send_bytes(b"before the reload\n")
        receive_until(ws, b"echo: before the reload")
        ws.close(1000)
    wait_for(lambda: [s["detached"] for s in running(client)] == [True])
    return opened["session_id"]


def test_a_dropped_browser_leaves_the_shell_waiting(client: TestClient, operator: dict, sshd: Sshd) -> None:
    session_id = leave_open_shell(client, sshd)
    entry = running(client)[0]
    assert entry["id"] == session_id and entry["mine"] is True and entry["state"] == "open"
    history = client.get("/api/sessions/history").json()
    assert history[0]["end"] == "running" and history[0]["ended_at"] is None


def test_the_returning_browser_sees_the_screen_and_types_on(client: TestClient, operator: dict, sshd: Sshd) -> None:
    session_id = leave_open_shell(client, sshd)
    with attach_ws(client, session_id) as ws:
        status = receive_json(ws)
        assert status["state"] == "open" and status["resumed"] is True and status["session_id"] == session_id
        assert status["via"] == "stored password"
        replay = receive_until(ws, b"echo: before the reload")
        assert b"welcome" in replay
        assert running(client)[0]["detached"] is False
        ws.send_bytes(b"after the reload\n")
        receive_until(ws, b"echo: after the reload")
        ws.send_bytes(b"exit 3\n")
        closed = receive_json(ws)
        assert closed["state"] == "closed" and closed["detail"] == "exit" and closed["exit_status"] == 3
    wait_for(lambda: running(client) == [])
    # Still one session in the history, not two.
    records = client.get("/api/sessions/history").json()
    assert [(r["end"], r["detail"]) for r in records] == [("normal", "exit")]


def test_output_while_nobody_watches_is_replayed(client: TestClient, operator: dict, sshd: Sshd) -> None:
    cid = make_connection(client, sshd, "password")
    store_password(client, cid)
    with connect_ws(client, cid) as ws:
        session_id = open_session(ws)["session_id"]
        receive_until(ws, b"welcome")
        # The line goes out, the browser leaves before the answer is read.
        ws.send_bytes(b"typed and left\n")
        ws.close(1000)
    wait_for(lambda: [s["detached"] for s in running(client)] == [True])
    with attach_ws(client, session_id) as ws:
        assert receive_json(ws)["resumed"] is True
        receive_until(ws, b"echo: typed and left")
        ws.send_json({"type": "close"})


def test_closing_on_purpose_ends_the_shell_at_once(client: TestClient, operator: dict, sshd: Sshd) -> None:
    cid = make_connection(client, sshd, "password")
    store_password(client, cid)
    with connect_ws(client, cid) as ws:
        assert open_session(ws)["state"] == "open"
        ws.send_json({"type": "close"})
        ws.close(1000)
    wait_for(lambda: running(client) == [])
    record = client.get("/api/sessions/history").json()[0]
    assert (record["end"], record["detail"]) == ("normal", "browser_closed")


def test_waiting_can_be_turned_off(client: TestClient, operator: dict, sshd: Sshd) -> None:
    assert client.put("/api/settings", json={"detach_minutes": 0}, headers=UI).json()["detach_minutes"] == 0
    cid = make_connection(client, sshd, "password")
    store_password(client, cid)
    with connect_ws(client, cid) as ws:
        assert open_session(ws)["state"] == "open"
        ws.close(1000)
    wait_for(lambda: running(client) == [])
    assert client.get("/api/sessions/history").json()[0]["detail"] == "browser_closed"


def test_detach_minutes_are_bounded(client: TestClient, operator: dict) -> None:
    assert client.get("/api/settings").json()["detach_minutes"] == 5
    assert client.put("/api/settings", json={"detach_minutes": -1}, headers=UI).status_code == 422
    assert client.put("/api/settings", json={"detach_minutes": 121}, headers=UI).status_code == 422


def test_a_detached_shell_ends_when_the_time_runs_out(
    client: TestClient, operator: dict, sshd: Sshd, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ssh, "MINUTE", 0.3)
    leave_open_shell(client, sshd)
    wait_for(lambda: running(client) == [], timeout=10)
    record = client.get("/api/sessions/history").json()[0]
    assert (record["end"], record["detail"]) == ("normal", "browser_closed")


def test_signing_out_ends_a_detached_shell(client: TestClient, operator: dict, sshd: Sshd) -> None:
    leave_open_shell(client, sshd)
    assert client.post("/api/auth/logout", headers=UI).status_code == 204
    wait_for(lambda: not ssh._sessions, timeout=10)


def test_nobody_else_takes_a_session(client: TestClient, operator: dict, sshd: Sshd) -> None:
    member = invite_member(client, "alex")
    session_id = leave_open_shell(client, sshd)
    # Another account: not found, as if there were no such session.
    with pytest.raises(WebSocketDisconnect) as refused, attach_ws(member, session_id) as ws:
        receive(ws)
    assert refused.value.code == 4404
    with pytest.raises(WebSocketDisconnect) as unknown, attach_ws(client, "no-such-session") as ws:
        receive(ws)
    assert unknown.value.code == 4404
    # The member's own waiting session: the operator sees it running but may not type into it. Registered by
    # hand, because a second TestClient's event loop ends with its WebSocket and would take the session along.
    member_id = member.get("/api/auth/me").json()["id"]
    theirs = ssh.SshSession(
        Account(id=member_id, name="alex", role=MEMBER), ssh.quick_target("192.0.2.10", 22, "alex"), "testclient", 80, 24
    )
    theirs.opened_at = 1.0
    theirs.browser_gone.set()
    ssh._sessions[theirs.id] = theirs
    try:
        view = next(s for s in running(client) if s["id"] == theirs.id)
        assert view["mine"] is False and view["account"] == "alex" and view["detached"] is True
        with pytest.raises(WebSocketDisconnect) as foreign, attach_ws(client, theirs.id) as ws:
            receive(ws)
        assert foreign.value.code == 4404
        assert [s["id"] for s in running(member)] == [theirs.id]
    finally:
        ssh._sessions.pop(theirs.id, None)
    # Without a sign-in nothing at all.
    stranger = TestClient(client.app, base_url="http://testserver")
    with pytest.raises(WebSocketDisconnect) as anonymous, attach_ws(stranger, session_id) as ws:
        receive(ws)
    assert anonymous.value.code == 4401


def test_a_second_browser_takes_over_and_the_first_is_let_go(client: TestClient, operator: dict, sshd: Sshd) -> None:
    cid = make_connection(client, sshd, "password")
    store_password(client, cid)
    with connect_ws(client, cid) as first:
        session_id = open_session(first)["session_id"]
        receive_until(first, b"welcome")
        with attach_ws(client, session_id) as second:
            assert receive_json(second)["resumed"] is True
            told = receive_json(first)
            assert told["state"] == "closed" and told["detail"] == "taken_over"
            with pytest.raises(WebSocketDisconnect) as let_go:
                receive(first)
            assert let_go.value.code == ssh.WS_TAKEN_OVER
            # The first browser has no say any more, whether its frame still arrives or not.
            with suppress(Exception):
                first.send_bytes(b"from the old window\n")
            second.send_bytes(b"from the new window\n")
            data = receive_until(second, b"echo: from the new window")
            assert b"old window" not in data
            assert running(client)[0]["detached"] is False
            second.send_json({"type": "close"})
    wait_for(lambda: running(client) == [])


def test_coming_back_redraws_at_the_same_size(client: TestClient, operator: dict, sshd: Sshd) -> None:
    session_id = leave_open_shell(client, sshd)
    before = len(sshd.resizes)
    with attach_ws(client, session_id) as ws:
        receive_json(ws)
        ws.send_json({"type": "resize", "cols": 80, "rows": 24})
        wait_for(lambda: len(sshd.resizes) >= before + 2)
        # A short step to another size and back: full-screen programs paint themselves again.
        assert sshd.resizes[before:before + 2] == [(80, 25), (80, 24)]
        # Only once: the next resize at the same size changes nothing.
        ws.send_json({"type": "resize", "cols": 80, "rows": 24})
        ws.send_bytes(b"size\n")
        receive_until(ws, b"size: 80x24")
        assert len(sshd.resizes) == before + 2
        ws.send_json({"type": "close"})


def test_the_replay_buffer_is_bounded() -> None:
    session = ssh.SshSession.__new__(ssh.SshSession)
    session._output, session._output_size, session._output_total = ssh.deque(), 0, 0
    for _ in range(10):
        session._remember_output(b"x" * (ssh.REPLAY_BYTES // 4))
    assert session._output_size <= ssh.REPLAY_BYTES
    assert sum(len(chunk) for chunk in session._output) == session._output_size
    session._remember_output(b"a" * ssh.REPLAY_BYTES + b"tail")
    assert session._output_size == ssh.REPLAY_BYTES
    assert b"".join(session._output).endswith(b"tail")


def test_a_browser_that_says_what_it_has_gets_only_the_rest(client: TestClient, operator: dict, sshd: Sshd) -> None:
    cid = make_connection(client, sshd, "password")
    store_password(client, cid)
    with connect_ws(client, cid) as ws:
        session_id = open_session(ws)["session_id"]
        seen = receive_until(ws, b"welcome\r\n")
        ws.close(1000)
    wait_for(lambda: [s["detached"] for s in running(client)] == [True])
    with client.websocket_connect(f"/api/sessions/ws?attach={session_id}&have={len(seen)}", headers=ORIGIN) as ws:
        status = receive_json(ws)
        assert status["replay"] == "tail" and status["offset"] == len(seen)
        ws.send_bytes(b"next\n")
        data = receive_until(ws, b"echo: next")
        assert b"welcome" not in data
        ws.close(1000)
    wait_for(lambda: [s["detached"] for s in running(client)] == [True])
    # More than was ever sent, or nothing said: the whole buffer for an empty screen.
    for have in ("&have=999999999", ""):
        with client.websocket_connect(f"/api/sessions/ws?attach={session_id}{have}", headers=ORIGIN) as ws:
            full = receive_json(ws)
            assert full["replay"] == "full" and full["offset"] == 0
            receive_until(ws, b"welcome")
            ws.close(1000)
        wait_for(lambda: [s["detached"] for s in running(client)] == [True])


def test_quick_connect_waits_too(client: TestClient, operator: dict, sshd: Sshd) -> None:
    with connect_ws(client, host="127.0.0.1", port=sshd.port, user=USER) as ws:
        assert receive_json(ws)["state"] == "connecting"
        assert receive_json(ws)["type"] == "hostkey"
        ws.send_json({"type": "hostkey", "accept": True})
        question = receive_json(ws)
        assert question["what"] == "password"
        ws.send_json({"type": "password", "value": "correct-horse-battery"})
        session_id = receive_json(ws)["session_id"]
        ws.close(1000)
    wait_for(lambda: [s["detached"] for s in running(client)] == [True])
    with attach_ws(client, session_id) as ws:
        assert receive_json(ws)["resumed"] is True
        receive_until(ws, b"welcome")
        ws.send_json({"type": "close"})
    wait_for(lambda: running(client) == [])

"""Running sessions, the operator's disconnect, the history, who sees what, and the handshake guards."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from app.db import SessionLocal
from app.main import app
from app.models import END_NORMAL, END_RUNNING, SessionRecord, utcnow
from app.services import ssh
from tests.conftest import UI, invite_member
from tests.helpers_sshd import (
    ORIGIN,
    PASSWORD,
    USER,
    Sshd,
    connect_ws,
    open_session,
    receive_json,
    receive_until,
    running_sshd,
    wait_for,
    ws_url,
)
from tests.test_ssh import make_connection, store_password


@pytest.fixture(name="sshd")
def _sshd(tmp_path: Path) -> Iterator[Sshd]:
    with running_sshd(tmp_path / "sftp-root") as server:
        yield server


def test_running_list_and_operator_disconnect(client: TestClient, operator: dict, sshd: Sshd) -> None:
    cid = make_connection(client, sshd, "password")
    store_password(client, cid)
    assert client.get("/api/sessions/running").json() == []
    with connect_ws(client, cid) as ws:
        opened = open_session(ws)
        assert opened["state"] == "open"
        receive_until(ws, b"welcome")
        running = client.get("/api/sessions/running").json()
        assert len(running) == 1
        entry = running[0]
        assert entry["id"] == opened["session_id"] and entry["account"] == "admin"
        assert entry["connection_id"] == cid and entry["name"] == "box"
        assert entry["target"] == f"{USER}@127.0.0.1:{sshd.port}" and entry["state"] == "open"
        assert entry["from_ip"] and entry["started_at"]

        cut = client.post(f"/api/sessions/{entry['id']}/disconnect", headers=UI)
        assert cut.status_code == 204, cut.text
        closed = receive_json(ws)
        assert closed["state"] == "closed" and closed["detail"] == "disconnected_by_operator" and closed["end"] == "cut"
        # The browser gets told once, even though the shell also ends because of the cut.
        with pytest.raises(WebSocketDisconnect):
            receive_json(ws, timeout=5)
    wait_for(lambda: client.get("/api/sessions/running").json() == [])
    record = client.get("/api/sessions/history").json()[0]
    assert record["end"] == "cut" and record["detail"] == "disconnected_by_operator" and record["ended_at"]
    assert client.post(f"/api/sessions/{entry['id']}/disconnect", headers=UI).status_code == 404
    assert client.post("/api/sessions/nope/disconnect", headers=UI).status_code == 404


def test_history_shows_every_kind_of_end(client: TestClient, operator: dict, sshd: Sshd) -> None:
    cid = make_connection(client, sshd, "password")
    store_password(client, cid)
    # 1. refused host key
    with connect_ws(client, cid) as ws:
        assert open_session(ws, accept_key=False)["detail"] == "hostkey"
    # 2. clean exit
    with connect_ws(client, cid) as ws:
        assert open_session(ws)["state"] == "open"
        ws.send_bytes(b"exit\n")
        assert receive_json(ws)["end"] == "normal"
    # 3. refused target
    with connect_ws(client, host="203.0.113.7", port=22, user=USER) as ws:
        receive_json(ws)
        assert receive_json(ws)["detail"] == "target_not_allowed"
    # 4. browser leaves while the shell runs
    with connect_ws(client, cid) as ws:
        assert receive_json(ws)["state"] == "connecting"
        assert receive_json(ws)["state"] == "open"
        ws.close(1000)
        wait_for(lambda: client.get("/api/sessions/running").json() == [])
    # 5. operator cuts
    with connect_ws(client, cid) as ws:
        assert receive_json(ws)["state"] == "connecting"
        opened = receive_json(ws)
        client.post(f"/api/sessions/{opened['session_id']}/disconnect", headers=UI)
        receive_json(ws)
    wait_for(lambda: all(r["ended_at"] for r in client.get("/api/sessions/history").json()))

    records = client.get("/api/sessions/history").json()
    assert [(r["end"], r["detail"]) for r in records] == [
        ("cut", "disconnected_by_operator"),
        ("normal", "browser_closed"),
        ("failed", "target_not_allowed"),
        ("normal", "exit"),
        ("hostkey", "hostkey"),
    ]
    assert all(r["account"] == "admin" and r["started_at"] and r["from_ip"] for r in records)
    assert records[0]["name"] == "box" and records[0]["connection_id"] == cid
    assert client.get("/api/sessions/history", params={"limit": 2}).json() == records[:2]
    assert client.get("/api/sessions/history", params={"limit": 0}).status_code == 422


def test_members_see_only_their_own_sessions(client: TestClient, operator: dict, sshd: Sshd) -> None:
    member = invite_member(client, "alex")
    cid = make_connection(client, sshd, "password")
    store_password(client, cid)
    with connect_ws(client, cid) as ws:
        opened = open_session(ws)
        assert opened["state"] == "open"
        assert member.get("/api/sessions/running").json() == []
        assert len(client.get("/api/sessions/running").json()) == 1
        refused = member.post(f"/api/sessions/{opened['session_id']}/disconnect", headers=UI)
        assert refused.status_code == 404
        # Still running: the member's attempt changed nothing.
        assert len(client.get("/api/sessions/running").json()) == 1
        assert member.get(f"/api/sessions/{opened['session_id']}/files").status_code == 404
        ws.send_bytes(b"exit\n")
        receive_json(ws)
    wait_for(lambda: client.get("/api/sessions/history").json()[0]["ended_at"] is not None)
    assert member.get("/api/sessions/history").json() == []
    assert len(client.get("/api/sessions/history").json()) == 1

    # The member's own failed attempt shows up for the member and for the operator.
    with connect_ws(member, host="203.0.113.9", port=22, user=USER) as ws:
        receive_json(ws)
        assert receive_json(ws)["detail"] == "target_not_allowed"
    own = member.get("/api/sessions/history").json()
    assert len(own) == 1 and own[0]["account"] == "alex"
    assert [r["account"] for r in client.get("/api/sessions/history").json()] == ["alex", "admin"]


def test_websocket_without_cookie_is_closed_with_4401(client: TestClient, operator: dict, sshd: Sshd) -> None:
    cid = make_connection(client, sshd, "ask")
    anonymous = TestClient(app, base_url="http://testserver")
    with pytest.raises(WebSocketDisconnect) as refused, anonymous.websocket_connect(ws_url(cid), headers=ORIGIN):
        pass
    assert refused.value.code == 4401
    assert client.get("/api/sessions/history").json() == []


def test_websocket_with_foreign_origin_is_refused(client: TestClient, operator: dict, sshd: Sshd) -> None:
    cid = make_connection(client, sshd, "ask")
    for headers in ({"origin": "http://evil.example.com"}, {}):
        with pytest.raises(WebSocketDisconnect) as refused, client.websocket_connect(ws_url(cid), headers=headers):
            pass
        assert refused.value.code == 4401
    assert client.get("/api/sessions/history").json() == []
    assert sshd.password_tries == 0


def test_websocket_refuses_invisible_connection_and_bad_quick_params(
    client: TestClient, operator: dict, sshd: Sshd
) -> None:
    member = invite_member(client, "alex")
    cid = make_connection(client, sshd, "ask")
    with pytest.raises(WebSocketDisconnect) as refused, connect_ws(member, cid):
        pass
    assert refused.value.code == 4404
    with pytest.raises(WebSocketDisconnect) as refused, connect_ws(client, host="127.0.0.1", port=sshd.port):
        pass
    assert refused.value.code == 4400
    with pytest.raises(WebSocketDisconnect) as refused, client.websocket_connect("/api/sessions/ws", headers=ORIGIN):
        pass
    assert refused.value.code == 4400


def test_purge_history_keeps_recent_and_running(client: TestClient, operator: dict) -> None:
    with SessionLocal() as db:
        old = utcnow() - timedelta(days=120)
        db.add(SessionRecord(account_id=operator["id"], target="a", started_at=old, ended_at=old, end=END_NORMAL))
        db.add(SessionRecord(account_id=operator["id"], target="b", started_at=old, end=END_RUNNING))
        db.add(SessionRecord(account_id=operator["id"], target="c", end=END_NORMAL, ended_at=utcnow()))
        # Left "running" by an earlier process: no live session carries this record.
        db.add(SessionRecord(account_id=operator["id"], target="e", end=END_RUNNING))
        db.commit()
        # "a" is old, "b" is old and stale (closed first, then purged), "c" is recent, "e" is recent and stale.
        assert ssh.purge_history(db) == 2
        rows = {r.target: r for r in db.scalars(select(SessionRecord))}
        assert sorted(rows) == ["c", "e"]
        assert rows["e"].end == "cut" and rows["e"].detail == "server_shutdown" and rows["e"].ended_at is not None
    client.put("/api/settings", json={"history_days": 1}, headers=UI)
    with SessionLocal() as db:
        db.add(SessionRecord(account_id=operator["id"], target="d", started_at=utcnow() - timedelta(days=2),
                             ended_at=utcnow(), end=END_NORMAL))
        db.commit()
    # The history endpoint purges as it goes.
    assert sorted(r["target"] for r in client.get("/api/sessions/history").json()) == ["c", "e"]


def test_password_change_does_not_leak_into_records(client: TestClient, operator: dict, sshd: Sshd) -> None:
    """Whatever the person typed never lands in the record or the history answer."""
    cid = make_connection(client, sshd, "ask")
    with connect_ws(client, cid) as ws:
        open_session(ws)
        ws.send_json({"type": "password", "value": "typed-secret-xyz", "store": False})
        message = receive_json(ws)
        assert message["type"] == "need"
        ws.send_json({"type": "password", "value": PASSWORD, "store": False})
        assert receive_json(ws)["state"] == "open"
        ws.send_bytes(b"exit\n")
        receive_json(ws)
    dump = client.get("/api/sessions/history").text
    assert "typed-secret-xyz" not in dump and PASSWORD not in dump
    with SessionLocal() as db:
        for record in db.scalars(select(SessionRecord)):
            assert "typed-secret-xyz" not in f"{record.target}{record.detail}{record.name}"


def test_server_shutdown_cuts_live_sessions(client: TestClient, operator: dict, sshd: Sshd) -> None:
    """``close_all`` is what the lifespan runs at shutdown: a live session is told, cut and recorded as such."""
    cid = make_connection(client, sshd, "password")
    store_password(client, cid)
    with connect_ws(client, cid) as ws:
        opened = open_session(ws)
        assert opened["state"] == "open"
        receive_until(ws, b"welcome")
        # Run it in the app's loop, exactly as the lifespan does.
        client.portal.call(ssh.close_all, "server shutting down")
        closed = receive_json(ws)
        assert closed == {"type": "status", "state": "closed", "end": "cut", "detail": "server_shutdown", "exit_status": None}
        wait_for(lambda: ssh.get(opened["session_id"]) is None)
    assert client.get("/api/sessions/running").json() == []
    with SessionLocal() as db:
        records = list(db.scalars(select(SessionRecord)))
        assert len(records) == 1
        assert records[0].end == "cut" and records[0].detail == "server_shutdown" and records[0].ended_at is not None


def test_client_dropping_early_leaves_no_error_line(
    client: TestClient, operator: dict, sshd: Sshd, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Under uvicorn a close after the browser left raises out of Starlette's send; that is nothing to log."""
    from starlette.websockets import WebSocket, WebSocketState

    original_close = WebSocket.close

    async def close_like_uvicorn(self: WebSocket, code: int = 1000, reason: str | None = None) -> None:
        if self.client_state == WebSocketState.DISCONNECTED:
            raise WebSocketDisconnect(code=1006)
        await original_close(self, code, reason)

    monkeypatch.setattr(WebSocket, "close", close_like_uvicorn)
    cid = make_connection(client, sshd, "ask")
    caplog.set_level(logging.INFO)
    with connect_ws(client, cid) as ws:
        connecting = receive_json(ws)
        assert connecting["state"] == "connecting"
        ws.close(1000)
        wait_for(lambda: ssh.get(connecting["session_id"]) is None)
        time.sleep(0.5)
    errors = [record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR]
    assert errors == []
    record = client.get("/api/sessions/history").json()[0]
    assert record["end"] == "failed" and record["detail"] == "browser_closed"

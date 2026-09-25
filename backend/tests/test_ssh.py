"""The terminal WebSocket against an in-process SSH server: host keys, the three ways to sign in, the shell."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import asyncssh
import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.services import hostkeys
from tests.conftest import UI
from tests.helpers_sshd import (
    PASSWORD,
    USER,
    Sshd,
    connect_ws,
    open_session,
    receive_json,
    receive_until,
    running_sshd,
    wait_for,
)


@pytest.fixture(name="sshd")
def _sshd(tmp_path: Path) -> Iterator[Sshd]:
    with running_sshd(tmp_path / "sftp-root") as server:
        yield server


def make_connection(client: TestClient, server: Sshd, auth: str = "ask", name: str = "box", **extra: object) -> int:
    payload = {"name": name, "host": "127.0.0.1", "port": server.port, "user": USER, "auth": auth, **extra}
    response = client.post("/api/connections", json=payload, headers=UI)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def store_password(client: TestClient, connection_id: int, password: str = PASSWORD) -> None:
    stored = client.put(f"/api/vault/passwords/{connection_id}", json={"password": password}, headers=UI)
    assert stored.status_code == 200, stored.text


def host_key_state(client: TestClient, connection_id: int) -> dict:
    for row in client.get("/api/connections").json():
        if row["id"] == connection_id:
            return row["host_key"]
    raise AssertionError("connection vanished")


def test_new_host_key_is_asked_once_then_silent(client: TestClient, operator: dict, sshd: Sshd) -> None:
    cid = make_connection(client, sshd, "password")
    store_password(client, cid)
    with connect_ws(client, cid) as ws:
        connecting = receive_json(ws)
        assert connecting["state"] == "connecting" and connecting["session_id"]
        question = receive_json(ws)
        assert question["type"] == "hostkey" and question["state"] == "new"
        assert question["host"] == "127.0.0.1" and question["port"] == sshd.port and question["for"] == "box"
        assert question["key_type"] == "ssh-ed25519" and question["fingerprint"] == sshd.fingerprint
        assert "old_fingerprint" not in question
        ws.send_json({"type": "hostkey", "accept": True})
        opened = receive_json(ws)
        assert opened["state"] == "open" and opened["via"] == "stored password" and opened["jump"] is None
        assert opened["session_id"] == connecting["session_id"]
        assert isinstance(opened["latency_ms"], int)
        receive_until(ws, b"welcome")
        ws.send_bytes(b"hello\n")
        receive_until(ws, b"echo: hello")
        ws.send_bytes(b"exit 7\n")
        closed = receive_json(ws)
        assert closed == {"type": "status", "state": "closed", "end": "normal", "detail": "exit", "exit_status": 7}

    state = host_key_state(client, cid)
    assert state["state"] == "known" and state["fingerprint"] == sshd.fingerprint
    with SessionLocal() as db:
        row = hostkeys.stored(db, "127.0.0.1", sshd.port)
        assert row is not None and row.trusted_by == "admin"

    # The second connection knows the key and asks nothing.
    with connect_ws(client, cid) as ws:
        assert receive_json(ws)["state"] == "connecting"
        assert receive_json(ws)["state"] == "open"
        ws.send_bytes(b"exit\n")
        assert receive_json(ws)["state"] == "closed"


def test_changed_host_key_is_reported_and_refused(client: TestClient, operator: dict, sshd: Sshd) -> None:
    cid = make_connection(client, sshd, "password")
    store_password(client, cid)
    with connect_ws(client, cid) as ws:
        assert open_session(ws)["state"] == "open"
        ws.send_bytes(b"exit\n")
        receive_json(ws)
    old_fingerprint = sshd.fingerprint

    sshd.rotate_host_key()
    assert sshd.fingerprint != old_fingerprint
    with connect_ws(client, cid) as ws:
        assert receive_json(ws)["state"] == "connecting"
        warning = receive_json(ws)
        assert warning["type"] == "hostkey" and warning["state"] == "changed"
        assert warning["fingerprint"] == sshd.fingerprint and warning["old_fingerprint"] == old_fingerprint
        assert warning["trusted_by"] == "admin" and warning["old_key_type"] == "ssh-ed25519"
        ws.send_json({"type": "hostkey", "accept": False})
        failed = receive_json(ws)
        assert failed["state"] == "failed" and failed["detail"] == "hostkey" and failed["end"] == "hostkey"
    # The store keeps the old key; nothing was replaced behind the person's back.
    assert host_key_state(client, cid)["fingerprint"] == old_fingerprint
    assert sshd.password_tries == 1

    # Accepting the changed key replaces it and the connection goes through.
    with connect_ws(client, cid) as ws:
        assert open_session(ws)["state"] == "open"
        ws.send_bytes(b"exit\n")
        receive_json(ws)
    assert host_key_state(client, cid)["fingerprint"] == sshd.fingerprint


def test_refused_new_key_ends_with_hostkey(client: TestClient, operator: dict, sshd: Sshd) -> None:
    cid = make_connection(client, sshd, "ask")
    with connect_ws(client, cid) as ws:
        failed = open_session(ws, accept_key=False)
        assert failed["state"] == "failed" and failed["detail"] == "hostkey"
    assert host_key_state(client, cid)["state"] == "new"
    assert sshd.password_tries == 0
    records = client.get("/api/sessions/history").json()
    assert records[0]["end"] == "hostkey" and records[0]["detail"] == "hostkey"


def test_ask_password_and_store_it(client: TestClient, operator: dict, sshd: Sshd) -> None:
    cid = make_connection(client, sshd, "ask")
    with connect_ws(client, cid) as ws:
        need = open_session(ws)
        assert need == {
            "type": "need",
            "what": "password",
            "for": "box",
            "user": USER,
            "host": "127.0.0.1",
            "retry": False,
            "stored_failed": False,
            "can_store": True,
        }
        ws.send_json({"type": "password", "value": "wrong-one", "store": True})
        again = receive_json(ws)
        assert again["type"] == "need" and again["retry"] is True
        ws.send_json({"type": "password", "value": PASSWORD, "store": True})
        opened = receive_json(ws)
        assert opened["state"] == "open" and opened["via"] == "password"
        ws.send_bytes(b"exit\n")
        receive_json(ws)
    # Only the password that worked was stored.
    assert client.get("/api/vault/passwords").json()[0]["connection_id"] == cid
    with SessionLocal() as db:
        from app.services import vault

        assert vault.get_password(db, operator["id"], cid) == PASSWORD


def test_wrong_password_three_times_fails(client: TestClient, operator: dict, sshd: Sshd) -> None:
    cid = make_connection(client, sshd, "ask")
    with connect_ws(client, cid) as ws:
        message = open_session(ws)
        for _ in range(3):
            assert message["type"] == "need" and message["what"] == "password"
            ws.send_json({"type": "password", "value": "nope", "store": False})
            message = receive_json(ws)
        assert message["state"] == "failed" and message["detail"] == "auth_failed"
    assert sshd.password_tries == 3
    assert client.get("/api/vault/passwords").json() == []


def test_stored_password_refused_asks_the_person(client: TestClient, operator: dict, sshd: Sshd) -> None:
    cid = make_connection(client, sshd, "password")
    store_password(client, cid, "outdated")
    with connect_ws(client, cid) as ws:
        need = open_session(ws)
        assert need["type"] == "need" and need["stored_failed"] is True and need["retry"] is True
        ws.send_json({"type": "password", "value": PASSWORD, "store": False})
        assert receive_json(ws)["state"] == "open"
        ws.send_bytes(b"exit\n")
        receive_json(ws)


def test_key_auth_with_a_vault_key(client: TestClient, operator: dict, sshd: Sshd) -> None:
    key = client.post("/api/vault/keys/generate", json={"name": "homelab", "key_type": "ed25519"}, headers=UI).json()
    sshd.authorize(key["public_key"])
    cid = make_connection(client, sshd, "key", key_id=key["id"])
    with connect_ws(client, cid) as ws:
        opened = open_session(ws)
        assert opened["state"] == "open" and opened["via"] == "key homelab"
        ws.send_bytes(b"exit\n")
        receive_json(ws)
    assert sshd.password_tries == 0

    # A key the server does not know is refused without any password prompt.
    other = client.post("/api/vault/keys/generate", json={"name": "other"}, headers=UI).json()
    cid2 = make_connection(client, sshd, "key", name="box2", key_id=other["id"])
    with connect_ws(client, cid2) as ws:
        failed = open_session(ws)
        assert failed["state"] == "failed" and failed["detail"] == "auth_failed"


def test_key_with_passphrase_asks_for_it(client: TestClient, operator: dict, sshd: Sshd) -> None:
    key = client.post(
        "/api/vault/keys/generate", json={"name": "guarded", "passphrase": "open-sesame-12"}, headers=UI
    ).json()
    assert key["has_passphrase"] is True
    sshd.authorize(key["public_key"])
    cid = make_connection(client, sshd, "key", key_id=key["id"])
    with connect_ws(client, cid) as ws:
        need = open_session(ws)
        assert need == {"type": "need", "what": "passphrase", "key": "guarded", "for": "box", "retry": False}
        ws.send_json({"type": "passphrase", "value": "wrong"})
        again = receive_json(ws)
        assert again["what"] == "passphrase" and again["retry"] is True
        ws.send_json({"type": "passphrase", "value": "open-sesame-12"})
        opened = receive_json(ws)
        assert opened["state"] == "open" and opened["via"] == "key guarded"
        ws.send_bytes(b"exit\n")
        receive_json(ws)


def test_locked_vault_fails_before_connecting(client: TestClient, operator: dict, sshd: Sshd) -> None:
    cid = make_connection(client, sshd, "password")
    store_password(client, cid)
    client.post("/api/vault/lock", headers=UI)
    with connect_ws(client, cid) as ws:
        assert receive_json(ws)["state"] == "connecting"
        failed = receive_json(ws)
        assert failed["state"] == "failed" and failed["detail"] == "vault_locked"
    assert sshd.password_tries == 0
    records = client.get("/api/sessions/history").json()
    assert records[0]["end"] == "failed" and records[0]["detail"] == "vault_locked"


def test_target_outside_private_ranges_is_refused(client: TestClient, operator: dict, sshd: Sshd) -> None:
    with connect_ws(client, host="203.0.113.1", port=22, user=USER) as ws:
        assert receive_json(ws)["state"] == "connecting"
        failed = receive_json(ws, timeout=5)
        assert failed["state"] == "failed" and failed["detail"] == "target_not_allowed" and failed["end"] == "failed"
    records = client.get("/api/sessions/history").json()
    assert records[0]["end"] == "failed" and records[0]["detail"] == "target_not_allowed"
    assert records[0]["target"] == f"{USER}@203.0.113.1:22" and records[0]["connection_id"] is None

    # The jump host is checked the same way, before anything is connected.
    bastion = client.post(
        "/api/connections", json={"name": "far", "host": "203.0.113.1", "user": USER, "auth": "ask"}, headers=UI
    ).json()["id"]
    cid = make_connection(client, sshd, "ask", jump_id=bastion)
    with connect_ws(client, cid) as ws:
        assert receive_json(ws)["state"] == "connecting"
        assert receive_json(ws, timeout=5)["detail"] == "target_not_allowed"
    assert sshd.password_tries == 0


def test_quick_connect(client: TestClient, operator: dict, sshd: Sshd) -> None:
    with connect_ws(client, host="127.0.0.1", port=sshd.port, user=USER) as ws:
        need = open_session(ws)
        assert need["type"] == "need" and need["what"] == "password" and need["for"] == f"{USER}@127.0.0.1"
        assert need["can_store"] is False
        ws.send_json({"type": "password", "value": PASSWORD, "store": True})
        opened = receive_json(ws)
        assert opened["state"] == "open" and opened["via"] == "password"
        ws.send_bytes(b"exit\n")
        receive_json(ws)
    # Nothing to store a password against: the vault stays empty.
    assert client.get("/api/vault/passwords").json() == []
    record = client.get("/api/sessions/history").json()[0]
    assert record["connection_id"] is None and record["name"] == f"{USER}@127.0.0.1" and record["end"] == "normal"


def test_start_command_and_resize(client: TestClient, operator: dict, sshd: Sshd) -> None:
    cid = make_connection(client, sshd, "password", start_command="size")
    store_password(client, cid)
    with connect_ws(client, cid) as ws:
        assert open_session(ws)["state"] == "open"
        receive_until(ws, b"size: 80x24")
        ws.send_json({"type": "resize", "cols": 132, "rows": 43})
        wait_for(lambda: (132, 43) in sshd.resizes)
        ws.send_bytes(b"size\n")
        receive_until(ws, b"size: 132x43")
        ws.send_bytes(b"exit\n")
        receive_json(ws)


def test_jump_host(client: TestClient, operator: dict, sshd: Sshd) -> None:
    bastion = make_connection(client, sshd, "password", name="bastion")
    store_password(client, bastion)
    inner = make_connection(client, sshd, "ask", name="inner", jump_id=bastion)
    with connect_ws(client, inner) as ws:
        # One host key question: the bastion and the inner host are the same server here.
        need = open_session(ws)
        assert need["type"] == "need" and need["for"] == "inner"
        ws.send_json({"type": "password", "value": PASSWORD, "store": False})
        opened = receive_json(ws)
        assert opened["state"] == "open" and opened["jump"] == "bastion" and opened["via"] == "password"
        ws.send_bytes(b"ping\n")
        receive_until(ws, b"echo: ping")
        ws.send_bytes(b"exit\n")
        receive_json(ws)
    assert sshd.password_tries == 2


def test_shared_connection_uses_the_members_own_password(client: TestClient, operator: dict, sshd: Sshd) -> None:
    from tests.conftest import invite_member

    key = client.post("/api/vault/keys/generate", json={"name": "homelab"}, headers=UI).json()
    sshd.authorize(key["public_key"])
    cid = make_connection(client, sshd, "key", key_id=key["id"])
    member = invite_member(client, "alex")
    alex_id = member.get("/api/auth/me").json()["id"]
    client.put(f"/api/connections/{cid}/share", json={"account_ids": [alex_id]}, headers=UI)
    # The member has no access to the operator's key, so the member is asked for a password instead.
    with connect_ws(member, cid) as ws:
        need = open_session(ws)
        assert need["type"] == "need" and need["what"] == "password"
        ws.send_json({"type": "password", "value": PASSWORD, "store": False})
        assert receive_json(ws)["state"] == "open"
        ws.send_bytes(b"exit\n")
        receive_json(ws)
    assert sshd.password_tries == 1


def test_shared_connection_uses_the_members_own_key_and_user(client: TestClient, operator: dict, sshd: Sshd) -> None:
    from tests.conftest import invite_member

    owner_key = client.post("/api/vault/keys/generate", json={"name": "homelab"}, headers=UI).json()
    sshd.authorize(owner_key["public_key"])
    cid = make_connection(client, sshd, "key", key_id=owner_key["id"], user="somebody-else")
    member = invite_member(client, "alex")
    alex_id = member.get("/api/auth/me").json()["id"]
    client.put(f"/api/connections/{cid}/share", json={"account_ids": [alex_id]}, headers=UI)
    # The member sets their own user name and their own key; the owner's key never leaves the owner's vault.
    own_key = member.post("/api/vault/keys/generate", json={"name": "mine"}, headers=UI).json()
    sshd.authorize(own_key["public_key"])
    foreign = member.put(f"/api/connections/{cid}/my-access", json={"user": USER, "auth": "key", "key_id": owner_key["id"]}, headers=UI)
    assert foreign.status_code == 422 and foreign.json()["detail"]["code"] == "key_not_found"
    access = member.put(f"/api/connections/{cid}/my-access", json={"user": USER, "auth": "key", "key_id": own_key["id"]}, headers=UI)
    assert access.status_code == 200, access.text
    assert access.json()["user"] == USER and access.json()["auth"] == "key" and access.json()["key_id"] == own_key["id"]
    assert access.json()["owner_user"] == "somebody-else"
    # The owner still sees the connection with the owner's own sign-in.
    mine = next(row for row in client.get("/api/connections").json() if row["id"] == cid)
    assert mine["user"] == "somebody-else" and mine["key_id"] == owner_key["id"]
    with connect_ws(member, cid) as ws:
        opened = open_session(ws)
        assert opened["state"] == "open", opened
        assert opened["via"] == "key mine"
        ws.send_bytes(b"exit\n")
        receive_json(ws)
    assert sshd.password_tries == 0
    # Re-sharing with the same member keeps the member's sign-in; the owner cannot set it.
    client.put(f"/api/connections/{cid}/share", json={"account_ids": [alex_id]}, headers=UI)
    again = next(row for row in member.get("/api/connections").json() if row["id"] == cid)
    assert again["auth"] == "key" and again["key_id"] == own_key["id"]
    assert client.put(f"/api/connections/{cid}/my-access", json={"user": "x", "auth": "ask"}, headers=UI).status_code == 404


def test_host_key_store_compares_keys_not_names() -> None:
    key = asyncssh.generate_private_key("ssh-ed25519")
    other = asyncssh.generate_private_key("ssh-ed25519")
    with SessionLocal() as db:
        assert hostkeys.compare(db, "NAS.example.com", 22, key) == (hostkeys.NEW, None)
        row = hostkeys.trust(db, "NAS.example.com", 22, key, "admin")
        assert row.host == "nas.example.com" and row.fingerprint == key.get_fingerprint()
        state, found = hostkeys.compare(db, "nas.example.com.", 22, key)
        assert state == hostkeys.KNOWN and found is not None and found.id == row.id
        assert hostkeys.compare(db, "nas.example.com", 22, other)[0] == hostkeys.CHANGED
        # A different port is a different server.
        assert hostkeys.compare(db, "nas.example.com", 2222, key)[0] == hostkeys.NEW
        replaced = hostkeys.trust(db, "nas.example.com", 22, other, "alex")
        assert replaced.id == row.id and replaced.trusted_by == "alex"
        assert hostkeys.compare(db, "nas.example.com", 22, key)[0] == hostkeys.CHANGED
        assert hostkeys.forget(db, "nas.example.com", 22) is True
        assert hostkeys.forget(db, "nas.example.com", 22) is False
        assert hostkeys.stored(db, "nas.example.com", 22) is None


def test_slow_answers_do_not_count_toward_the_connect_timeout(
    client: TestClient, operator: dict, sshd: Sshd, monkeypatch: pytest.MonkeyPatch
) -> None:
    """asyncssh's connect timeout covers authentication; a person typing must never fall into it."""
    from app.services import ssh

    monkeypatch.setattr(ssh, "CONNECT_TIMEOUT", 1)
    cid = make_connection(client, sshd, "ask")
    with connect_ws(client, cid) as ws:
        assert receive_json(ws)["state"] == "connecting"
        assert receive_json(ws)["type"] == "hostkey"
        time.sleep(2)
        ws.send_json({"type": "hostkey", "accept": True})
        need = receive_json(ws)
        assert need["type"] == "need" and need["what"] == "password"
        time.sleep(2)
        ws.send_json({"type": "password", "value": "wrong", "store": False})
        again = receive_json(ws)
        assert again["type"] == "need" and again["retry"] is True
        time.sleep(2)
        ws.send_json({"type": "password", "value": PASSWORD, "store": False})
        opened = receive_json(ws)
        assert opened["state"] == "open", opened
        ws.send_bytes(b"exit\n")
        receive_json(ws)
    assert sshd.password_tries == 2

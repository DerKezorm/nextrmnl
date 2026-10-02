"""Guest accounts and shares with an end: who gets in until when, and what ends when the time is up."""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.db import SessionLocal
from app.main import app
from app.models import Account
from app.services import ssh
from tests.conftest import PASSWORD, UI, invite_member
from tests.helpers_sshd import Sshd, connect_ws, open_session, receive, receive_json, receive_until, running_sshd
from tests.test_oidc import FakeProvider, configure, fresh_browser, provider, sign_in_via_oidc  # noqa: F401
from tests.test_ssh import make_connection, store_password


def later(days: float = 7) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


def earlier(minutes: float = 1) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()


def account_id(name: str) -> int:
    with SessionLocal() as db:
        return db.query(Account).filter(Account.name == name).one().id


def login(client: TestClient, name: str, password: str = PASSWORD):
    return client.post("/api/auth/login", json={"name": name, "password": password}, headers=UI)


@pytest.fixture(name="sshd")
def _sshd(tmp_path: Path) -> Iterator[Sshd]:
    with running_sshd(tmp_path / "sftp-root") as server:
        yield server


def test_an_invitation_can_make_a_guest(client: TestClient, operator: dict) -> None:
    end = later(3)
    made = client.post("/api/accounts/invites", json={"name": "guest", "role": "member", "account_expires_at": end}, headers=UI)
    assert made.status_code == 201, made.text
    assert made.json()["account_expires_at"] is not None
    assert client.get("/api/accounts/invites").json()[0]["account_expires_at"] is not None
    token = made.json()["link"].rsplit("/", 1)[-1]
    guest = TestClient(app, base_url="http://testserver")
    accepted = guest.post(f"/api/invites/{token}", json={"name": "guest", "password": PASSWORD}, headers=UI)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["expires_at"] is not None
    assert guest.get("/api/auth/me").status_code == 200


def test_invitations_refuse_odd_ends(client: TestClient, operator: dict) -> None:
    past = client.post("/api/accounts/invites", json={"name": "late", "account_expires_at": earlier()}, headers=UI)
    assert past.status_code == 422 and past.json()["detail"]["code"] == "expiry_in_past"
    boss = client.post("/api/accounts/invites", json={"name": "boss", "role": "operator", "account_expires_at": later()}, headers=UI)
    assert boss.status_code == 422 and boss.json()["detail"]["code"] == "operator_cannot_expire"
    naive = client.post("/api/accounts/invites", json={"name": "naive", "account_expires_at": "2099-01-01T00:00:00"}, headers=UI)
    assert naive.status_code == 422


def test_an_expired_account_is_out_everywhere_and_back_when_extended(client: TestClient, operator: dict) -> None:
    member = invite_member(client, "alex")
    alex = account_id("alex")
    ended = client.put(f"/api/accounts/{alex}/expiry", json={"expires_at": earlier()}, headers=UI)
    assert ended.status_code == 200 and ended.json()["expires_at"] is not None
    # The open browser is out at once.
    assert member.get("/api/auth/me").status_code == 401
    # The right password gets the reason; a wrong one gets the same answer as always, names stay secret.
    refused = login(TestClient(app, base_url="http://testserver"), "alex")
    assert refused.status_code == 403 and refused.json()["detail"]["code"] == "account_expired"
    wrong = login(TestClient(app, base_url="http://testserver"), "alex", "not-the-password-at-all")
    assert wrong.status_code == 401 and wrong.json()["detail"]["code"] == "wrong_credentials"

    back = client.put(f"/api/accounts/{alex}/expiry", json={"expires_at": None}, headers=UI)
    assert back.status_code == 200 and back.json()["expires_at"] is None
    assert login(TestClient(app, base_url="http://testserver"), "alex").status_code == 200


def test_an_end_that_simply_passes_signs_the_browser_out(client: TestClient, operator: dict) -> None:
    member = invite_member(client, "alex")
    assert member.get("/api/auth/me").status_code == 200
    # Nobody touches the account; its day just comes.
    with SessionLocal() as db:
        row = db.query(Account).filter(Account.name == "alex").one()
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert member.get("/api/auth/me").status_code == 401
    assert member.get("/api/connections").status_code == 401


def test_a_future_end_changes_nothing_yet(client: TestClient, operator: dict) -> None:
    member = invite_member(client, "alex")
    assert client.put(f"/api/accounts/{account_id('alex')}/expiry", json={"expires_at": later()}, headers=UI).status_code == 200
    assert member.get("/api/auth/me").status_code == 200
    assert member.get("/api/auth/me").json()["expires_at"] is not None


def test_the_operator_never_expires(client: TestClient, operator: dict) -> None:
    me = account_id("admin")
    refused = client.put(f"/api/accounts/{me}/expiry", json={"expires_at": earlier()}, headers=UI)
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == "operator_cannot_expire"
    invite_member(client, "alex")
    alex = account_id("alex")
    client.put(f"/api/accounts/{alex}/expiry", json={"expires_at": later()}, headers=UI)
    promoted = client.put(f"/api/accounts/{alex}/role", json={"role": "operator"}, headers=UI)
    assert promoted.json()["expires_at"] is None
    assert client.put("/api/accounts/9999/expiry", json={"expires_at": None}, headers=UI).status_code == 404


def test_only_the_operator_sets_ends(client: TestClient, operator: dict) -> None:
    member = invite_member(client, "alex")
    assert member.put(f"/api/accounts/{account_id('alex')}/expiry", json={"expires_at": None}, headers=UI).status_code == 403


def test_an_expired_account_cannot_come_in_through_the_provider(
    client: TestClient, operator: dict, provider: FakeProvider  # noqa: F811
) -> None:
    configure(client)
    first = sign_in_via_oidc(fresh_browser(client), provider)
    assert first.headers["location"] == "/"
    client.put(f"/api/accounts/{account_id('alex')}/expiry", json={"expires_at": earlier()}, headers=UI)
    again = fresh_browser(client)
    refused = sign_in_via_oidc(again, provider)
    assert refused.status_code == 303 and "account_expired" in refused.headers["location"]
    assert not again.cookies.get("nextrmnl_session")


def share(client: TestClient, connection_id: int, member_id: int, end: str | None) -> dict:
    response = client.put(
        f"/api/connections/{connection_id}/share",
        json={"shares": [{"account_id": member_id, "expires_at": end}]},
        headers=UI,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_a_share_ends_on_its_date(client: TestClient, operator: dict) -> None:
    member = invite_member(client, "alex")
    alex = account_id("alex")
    made = client.post("/api/connections", json={"name": "nas", "host": "192.0.2.5", "user": "root", "auth": "ask"}, headers=UI)
    cid = made.json()["id"]
    view = share(client, cid, alex, later(1))
    assert view["shared_with"] == [alex] and view["share_ends"][str(alex)] is not None
    listed = member.get("/api/connections").json()
    assert [c["id"] for c in listed] == [cid] and listed[0]["share_expires_at"] is not None

    view = share(client, cid, alex, earlier())
    # The owner still sees the run-out share and its date, to extend it.
    assert view["shared_with"] == [alex] and view["share_ends"][str(alex)] is not None
    assert member.get("/api/connections").json() == []
    assert member.put(f"/api/connections/{cid}/my-access", json={"user": "x", "auth": "ask"}, headers=UI).status_code == 404
    assert member.put(f"/api/vault/passwords/{cid}", json={"password": "a-password-long-enough"}, headers=UI).status_code == 404
    with pytest.raises(WebSocketDisconnect) as refused, connect_ws(member, cid) as ws:
        receive(ws)
    assert refused.value.code == 4404

    # Extended: back again. The older form without ends still works and means no end.
    share(client, cid, alex, None)
    assert [c["id"] for c in member.get("/api/connections").json()] == [cid]
    old_form = client.put(f"/api/connections/{cid}/share", json={"account_ids": [alex]}, headers=UI)
    assert old_form.status_code == 200 and old_form.json()["share_ends"][str(alex)] is None


def test_share_ends_need_a_time_zone(client: TestClient, operator: dict) -> None:
    invite_member(client, "alex")
    made = client.post("/api/connections", json={"name": "nas", "host": "192.0.2.5", "user": "root", "auth": "ask"}, headers=UI)
    response = client.put(
        f"/api/connections/{made.json()['id']}/share",
        json={"shares": [{"account_id": account_id("alex"), "expires_at": "2099-01-01T00:00:00"}]},
        headers=UI,
    )
    assert response.status_code == 422


@pytest.mark.parametrize("how", ["runs out", "taken back"])
def test_a_running_shell_on_a_share_closes_when_the_share_ends(
    client: TestClient, operator: dict, sshd: Sshd, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    monkeypatch.setattr(ssh, "AUTH_WATCH_SECONDS", 0.2)
    member = invite_member(client, "alex")
    alex = account_id("alex")
    cid = make_connection(client, sshd, "password")
    store_password(client, cid)
    share(client, cid, alex, later())
    member.put(f"/api/connections/{cid}/my-access", json={"user": "tester", "auth": "ask"}, headers=UI)
    with connect_ws(member, cid) as ws:
        first = open_session(ws)
        if first.get("type") == "need":
            ws.send_json({"type": "password", "value": PASSWORD})
            first = receive_json(ws)
        assert first["state"] == "open", first
        if how == "runs out":
            share(client, cid, alex, earlier())
        else:
            client.put(f"/api/connections/{cid}/share", json={"shares": []}, headers=UI)
        closed = receive_json(ws)
        assert closed["state"] == "closed" and closed["detail"] == "access_ended" and closed["end"] == "cut"


def test_the_owner_keeps_the_shell(client: TestClient, operator: dict, sshd: Sshd, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssh, "AUTH_WATCH_SECONDS", 0.2)
    cid = make_connection(client, sshd, "password")
    store_password(client, cid)
    with connect_ws(client, cid) as ws:
        assert open_session(ws)["state"] == "open"
        ws.send_bytes(b"still here\n")
        # Several watch rounds pass; the owner's shell stays.
        receive_until(ws, b"echo: still here")
        time.sleep(1)
        ws.send_bytes(b"exit\n")
        assert receive_json(ws)["detail"] == "exit"

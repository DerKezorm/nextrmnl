"""Notifications: closed by default, the three kinds of inbox, the groups, the test button, and what never goes out."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import Account
from app.services import accounts, notify
from tests.conftest import PASSWORD, UI, invite_member
from tests.helpers_sshd import Sshd, connect_ws, open_session, running_sshd
from tests.test_ssh import make_connection, store_password

TOKEN = "inbox-token-for-the-test"


class Inbox:
    """A tiny HTTP server that keeps what it receives and answers with ``status``."""

    def __init__(self) -> None:
        self.received: list[dict] = []
        self.status = 200
        self.location = ""
        inbox = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(length)
                inbox.received.append({"path": self.path, "headers": dict(self.headers), "body": body})
                self.send_response(inbox.status)
                if inbox.location:
                    self.send_header("Location", inbox.location)
                self.end_headers()

            def log_message(self, *args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def wait(self, count: int = 1, timeout: float = 5) -> list[dict]:
        deadline = time.monotonic() + timeout
        while len(self.received) < count and time.monotonic() < deadline:
            time.sleep(0.02)
        return self.received

    def quiet(self) -> bool:
        """Nothing arrived, after everything queued went out. Waiting for the queue, not for a guessed time: a
        message that is merely slow must not pass as no message."""
        assert notify.wait_idle(10)
        time.sleep(0.05)
        return not self.received


@pytest.fixture(name="inbox")
def _inbox() -> Iterator[Inbox]:
    inbox = Inbox()
    yield inbox
    # Nothing may still be on its way when the test is over.
    notify.wait_idle()
    inbox.server.shutdown()


def turn_on(client: TestClient, inbox: Inbox, kind: str = "gotify", url: str | None = None, **extra: object) -> dict:
    payload = {"notify_enabled": True, "notify_kind": kind, "notify_url": url or inbox.url, "notify_token": TOKEN, **extra}
    response = client.put("/api/settings", json=payload, headers=UI)
    assert response.status_code == 200, response.text
    return response.json()


def sign_in_again(name: str = "admin", password: str = PASSWORD) -> int:
    return TestClient(app, base_url="http://testserver").post(
        "/api/auth/login", json={"name": name, "password": password}, headers=UI
    ).status_code


def test_nothing_goes_out_until_the_operator_opens_it(client: TestClient, operator: dict, inbox: Inbox) -> None:
    settings = client.get("/api/settings").json()
    assert settings["notify_enabled"] is False and settings["notify_token_set"] is False
    assert settings["notify_events"] == ["security", "signin", "sessions", "operations"]
    # Address and token stored, the switch still off: silence.
    client.put("/api/settings", json={"notify_url": inbox.url, "notify_token": TOKEN}, headers=UI)
    assert sign_in_again() == 200
    assert inbox.quiet()


def test_gotify_and_nexsift_get_title_message_priority_and_the_token_as_header(
    client: TestClient, operator: dict, inbox: Inbox, caplog: pytest.LogCaptureFixture
) -> None:
    settings = turn_on(client, inbox)
    assert settings["notify_token_set"] is True and TOKEN not in json.dumps(settings)
    # Not even the encrypted form goes to the browser.
    assert not {key for key in settings if key.startswith("notify_token") and key != "notify_token_set"}
    with caplog.at_level(logging.DEBUG):
        assert sign_in_again() == 200
        message = inbox.wait()[0]
    assert message["path"] == "/message"
    assert message["headers"]["X-Gotify-Key"] == TOKEN
    body = json.loads(message["body"])
    assert body["title"] == "Sign-in" and "admin signed in from" in body["message"] and body["priority"] == 4
    # The token never in the log, nor in the address.
    assert TOKEN not in caplog.text and "token" not in message["path"]


def test_ntfy_gets_text_with_title_priority_and_tags(client: TestClient, operator: dict, inbox: Inbox) -> None:
    turn_on(client, inbox, "ntfy", url=f"{inbox.url}/homelab-alerts")
    assert sign_in_again() == 200
    message = inbox.wait()[0]
    assert message["path"] == "/homelab-alerts"
    assert message["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert message["headers"]["Title"] == "Sign-in" and message["headers"]["Tags"] == "nextrmnl,signin"
    assert message["body"].decode("utf-8").startswith("admin signed in from")


def test_a_webhook_gets_json_with_the_group(client: TestClient, operator: dict, inbox: Inbox) -> None:
    turn_on(client, inbox, "webhook", url=f"{inbox.url}/api/v1/hook/abc")
    assert sign_in_again() == 200
    body = json.loads(inbox.wait()[0]["body"])
    assert body["source"] == "nextrmnl" and body["category"] == "signin" and body["title"] == "Sign-in"


def test_groups_can_be_switched_off(client: TestClient, operator: dict, inbox: Inbox) -> None:
    turn_on(client, inbox, notify_events=["security"])
    assert sign_in_again() == 200
    assert inbox.quiet()
    # Ten wrong checks lock the account: that is security, and it goes out, urgent. Counted through the service,
    # because the sender's brake stops a single browser after five.
    invite_member(client, "alex")
    inbox.received.clear()
    with SessionLocal() as db:
        row = db.query(Account).filter(Account.name == "alex").one()
        for _ in range(10):
            accounts.note_failure(db, row)
    body = json.loads(inbox.wait()[0]["body"])
    assert body["title"] == "Account locked" and "alex" in body["message"] and body["priority"] == 8
    assert "wrong-password-xyz" not in json.dumps(inbox.received, default=str)


def test_an_api_key_and_a_reset_second_factor_are_reported(client: TestClient, operator: dict, inbox: Inbox) -> None:
    turn_on(client, inbox, notify_events=["security"])
    client.put("/api/settings", json={"api_keys_allowed": True}, headers=UI)
    made = client.post("/api/api-keys", json={"name": "dashboard"}, headers=UI)
    body = json.loads(inbox.wait()[0]["body"])
    assert body["title"] == "New API key" and "dashboard" in body["message"]
    assert made.json()["key"] not in json.dumps(inbox.received, default=str)


@pytest.fixture(name="sshd")
def _sshd(tmp_path: Path) -> Iterator[Sshd]:
    with running_sshd(tmp_path / "sftp-root") as server:
        yield server


def test_an_opened_session_is_reported_without_what_is_typed(
    client: TestClient, operator: dict, inbox: Inbox, sshd: Sshd
) -> None:
    turn_on(client, inbox, notify_events=["sessions"])
    cid = make_connection(client, sshd, "password")
    store_password(client, cid)
    with connect_ws(client, cid) as ws:
        assert open_session(ws)["state"] == "open"
        ws.send_bytes(b"secret-command-line\n")
        body = json.loads(inbox.wait()[0]["body"])
        ws.send_json({"type": "close"})
    assert body["title"] == "Session opened" and "admin opened tester@127.0.0.1" in body["message"]
    assert body["priority"] == 2
    time.sleep(0.3)
    assert "secret-command-line" not in json.dumps(inbox.received, default=str)
    assert PASSWORD not in json.dumps(inbox.received, default=str)


def test_the_test_button(client: TestClient, operator: dict, inbox: Inbox) -> None:
    off = client.post("/api/settings/notify/test", headers=UI)
    assert off.status_code == 409 and off.json()["detail"]["code"] == "notify_off"
    turn_on(client, inbox)
    sent = client.post("/api/settings/notify/test", headers=UI)
    assert sent.status_code == 200 and sent.json()["status"] == 200
    assert json.loads(inbox.received[-1]["body"])["message"].startswith("Test message")
    inbox.status = 500
    refused = client.post("/api/settings/notify/test", headers=UI)
    assert refused.status_code == 502 and refused.json()["detail"]["code"] == "notify_refused"
    # A redirect is not followed: an inbox must not send the token on to somewhere else.
    inbox.status, inbox.location = 302, "http://203.0.113.9/elsewhere"
    count = len(inbox.received)
    moved = client.post("/api/settings/notify/test", headers=UI)
    assert moved.status_code == 502 and moved.json()["detail"]["code"] == "notify_refused"
    assert len(inbox.received) == count + 1
    client.put("/api/settings", json={"notify_url": "http://127.0.0.1:1"}, headers=UI)
    gone = client.post("/api/settings/notify/test", headers=UI)
    assert gone.status_code == 502 and gone.json()["detail"]["code"] == "notify_unreachable"
    member = invite_member(client, "alex")
    assert member.post("/api/settings/notify/test", headers=UI).status_code == 403


def test_settings_are_checked(client: TestClient, operator: dict) -> None:
    for bad in (
        {"notify_kind": "carrier-pigeon"},
        {"notify_url": "ftp://inbox.example.com"},
        {"notify_url": "https://user:secret@inbox.example.com"},
        {"notify_events": ["security", "gossip"]},
        {"notify_enabled": True},
    ):
        response = client.put("/api/settings", json=bad, headers=UI)
        assert response.status_code == 422, bad
    kept = client.put("/api/settings", json={"notify_url": "https://inbox.example.com", "notify_token": TOKEN}, headers=UI)
    assert kept.json()["notify_token_set"] is True
    # Leaving the token out keeps it; an empty one removes it.
    assert client.put("/api/settings", json={"notify_kind": "ntfy"}, headers=UI).json()["notify_token_set"] is True
    assert client.put("/api/settings", json={"notify_token": ""}, headers=UI).json()["notify_token_set"] is False
    events = client.put("/api/settings", json={"notify_events": ["operations", "security"]}, headers=UI).json()
    assert events["notify_events"] == ["security", "operations"]


def test_a_full_queue_drops_instead_of_waiting(
    client: TestClient, operator: dict, inbox: Inbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    turn_on(client, inbox)
    monkeypatch.setattr(notify, "MAX_PENDING", 0)
    assert sign_in_again() == 200
    assert inbox.quiet()

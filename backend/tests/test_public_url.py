"""The public address: the setting first, the environment second, the request third. It drives invitation
links, the OIDC redirect and the WebSocket origin check."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import get_settings
from tests.conftest import UI
from tests.helpers_sshd import Sshd, receive_json, running_sshd, ws_url
from tests.test_ssh import make_connection

PUBLIC = "https://nextrmnl.example.com"


@pytest.fixture(name="sshd")
def _sshd(tmp_path: Path) -> Iterator[Sshd]:
    with running_sshd(tmp_path / "sftp-root") as server:
        yield server


def set_public_url(client: TestClient, value: str) -> httpx.Response:
    return client.put("/api/settings", json={"public_url": value}, headers=UI)


def test_the_public_address_drives_invitation_links_and_the_oidc_redirect(client: TestClient, operator: dict) -> None:
    # Nothing set: the request's own address.
    assert client.get("/api/settings").json()["public_url"] == ""
    assert client.get("/api/oidc/config").json()["redirect_uri"] == "http://testserver/api/oidc/callback"

    saved = set_public_url(client, PUBLIC + "/")
    assert saved.status_code == 200, saved.text
    # Stored without the trailing slash, so that paths can simply be appended.
    assert saved.json()["public_url"] == PUBLIC
    assert client.get("/api/oidc/config").json()["redirect_uri"] == PUBLIC + "/api/oidc/callback"
    invite = client.post("/api/accounts/invites", json={"name": "sam", "role": "member"}, headers=UI)
    assert invite.status_code == 201, invite.text
    assert invite.json()["link"].startswith(PUBLIC + "/invite/")

    # Empty again: back to the request.
    assert set_public_url(client, "").json()["public_url"] == ""
    assert client.get("/api/oidc/config").json()["redirect_uri"] == "http://testserver/api/oidc/callback"


@pytest.mark.parametrize(
    "value",
    [
        "nextrmnl.example.com",
        "ftp://nextrmnl.example.com",
        "https://nextrmnl.example.com/nextrmnl",
        "https://alex:pw@nextrmnl.example.com",
        "https://nextrmnl.example.com/?x=1",
        "https://nextrmnl.example.com:port",
        "javascript:alert(1)",
    ],
)
def test_addresses_that_are_not_a_plain_origin_are_refused(client: TestClient, operator: dict, value: str) -> None:
    refused = set_public_url(client, value)
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["code"] == "public_url_invalid"
    assert client.get("/api/settings").json()["public_url"] == ""


def test_the_environment_is_the_fallback_when_the_setting_is_empty(
    client: TestClient, operator: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "public_url", "http://env.example.com/")
    assert client.get("/api/oidc/config").json()["redirect_uri"] == "http://env.example.com/api/oidc/callback"
    # The setting wins over the environment.
    assert set_public_url(client, PUBLIC).status_code == 200
    assert client.get("/api/oidc/config").json()["redirect_uri"] == PUBLIC + "/api/oidc/callback"


def test_a_websocket_from_the_public_address_passes_the_origin_check(
    client: TestClient, operator: dict, sshd: Sshd
) -> None:
    cid = make_connection(client, sshd, "ask")
    headers = {"origin": PUBLIC}
    # Not the public address yet: a foreign origin like any other.
    with pytest.raises(WebSocketDisconnect) as refused, client.websocket_connect(ws_url(cid), headers=headers):
        pass
    assert refused.value.code == 4401

    assert set_public_url(client, PUBLIC).status_code == 200
    with client.websocket_connect(ws_url(cid), headers=headers) as websocket:
        first = receive_json(websocket)
    assert isinstance(first, dict) and first.get("type")

"""Behind a reverse proxy: the scheme (https) and the sender's address come from a proxy in
NEXTRMNL_TRUSTED_PROXIES and from nobody else, as the rule of the shared sign-in module says ("Hinter einem
Proxy"): rightmost value of the last header line, any case, only http and https, nobody trusted from the start.
What depends on it: the Secure flag of the cookies, the passkey party without a public address, and the brake."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import deps
from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.routers import auth
from app.services import passkeys
from tests.conftest import PASSWORD, UI

PROXY = "203.0.113.5"
STRANGER = "198.51.100.7"
HOST = "rmnl.example.com"


@pytest.fixture
def trusted(monkeypatch: pytest.MonkeyPatch) -> None:
    """One proxy network is trusted; the cookie flag goes by the request (the tests run with ``off`` otherwise)."""
    settings = get_settings()
    monkeypatch.setattr(settings, "trusted_proxies", "203.0.113.0/24")
    monkeypatch.setattr(settings, "cookie_secure", "auto")


def request_from(peer: str, *lines: str, host: str = HOST, extra: list[tuple[bytes, bytes]] | None = None) -> Request:
    """A plain http request from ``peer`` with one ``X-Forwarded-Proto`` line per entry of ``lines``."""
    headers = [(b"host", host.encode("ascii"))]
    headers += [(b"x-forwarded-proto", line.encode("ascii")) for line in lines]
    headers += extra or []
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": headers,
            "client": (peer, 1234),
            "query_string": b"",
            "scheme": "http",
            "server": (host, 80),
        }
    )


# --- The one reading -------------------------------------------------------------------------------------------- #


def test_the_proxy_scheme_is_the_rightmost_value_of_the_last_line_of_a_trusted_proxy(trusted: None) -> None:
    assert deps.forwarded_proto(request_from(PROXY, "https")) == "https"
    assert deps.forwarded_proto(request_from(PROXY, "HTTPS")) == "https"
    assert deps.forwarded_proto(request_from(PROXY, " https ")) == "https"
    # Of a list the nearest proxy wrote the last value; what stands left of it may be the sender's.
    assert deps.forwarded_proto(request_from(PROXY, "https, http")) == "http"
    assert deps.forwarded_proto(request_from(PROXY, "http, https")) == "https"
    # Of several lines the last one counts; a line above may be the sender's.
    assert deps.forwarded_proto(request_from(PROXY, "https", "http")) == "http"
    assert deps.forwarded_proto(request_from(PROXY, "http", "https")) == "https"
    # Only http and https: anything else is no value, and an empty last value too.
    assert deps.forwarded_proto(request_from(PROXY, "wss")) == ""
    assert deps.forwarded_proto(request_from(PROXY, "javascript")) == ""
    assert deps.forwarded_proto(request_from(PROXY, "https,")) == ""
    assert deps.forwarded_proto(request_from(PROXY)) == ""


def test_the_proxy_scheme_is_nobodys_but_a_trusted_proxys(trusted: None, monkeypatch: pytest.MonkeyPatch) -> None:
    assert deps.forwarded_proto(request_from(STRANGER, "https")) == ""
    assert deps.request_base(request_from(STRANGER, "https")) == f"http://{HOST}/"
    # From the start nobody is trusted, the own machine included.
    monkeypatch.setattr(get_settings(), "trusted_proxies", "")
    assert deps.forwarded_proto(request_from("127.0.0.1", "https")) == ""
    assert deps.forwarded_proto(request_from(PROXY, "https")) == ""


# --- The Secure flag --------------------------------------------------------------------------------------------- #


def test_the_secure_flag_follows_a_trusted_proxy_only(trusted: None, monkeypatch: pytest.MonkeyPatch) -> None:
    assert auth._secure(request_from(PROXY, "https")) is True
    assert auth._secure(request_from(PROXY, "https, http")) is False
    assert auth._secure(request_from(PROXY, "https", "http")) is False
    assert auth._secure(request_from(PROXY, "http", "https")) is True
    assert auth._secure(request_from(STRANGER, "https")) is False
    assert auth._secure(request_from(STRANGER)) is False
    monkeypatch.setattr(get_settings(), "trusted_proxies", "")
    assert auth._secure(request_from(PROXY, "https")) is False
    # The operator's word wins either way.
    monkeypatch.setattr(get_settings(), "cookie_secure", "on")
    assert auth._secure(request_from(STRANGER)) is True
    monkeypatch.setattr(get_settings(), "cookie_secure", "off")
    assert auth._secure(request_from(PROXY, "https")) is False


def _session_cookie(peer: str, headers: list[tuple[str, str]]) -> str:
    browser = TestClient(app, base_url=f"http://{HOST}", client=(peer, 4000))
    answer = browser.post(
        "/api/auth/login", json={"name": "admin", "password": PASSWORD}, headers=[*UI.items(), *headers]
    )
    assert answer.status_code == 200, answer.text
    cookies = [line for line in answer.headers.get_list("set-cookie") if line.startswith("nextrmnl_session=")]
    assert len(cookies) == 1
    return cookies[0]


def test_the_session_cookie_is_secure_only_when_a_trusted_proxy_says_https(operator: dict, trusted: None) -> None:
    assert "secure" in _session_cookie(PROXY, [("X-Forwarded-Proto", "https")]).lower()
    assert "secure" not in _session_cookie(STRANGER, [("X-Forwarded-Proto", "https")]).lower()
    assert "secure" not in _session_cookie(PROXY, [("X-Forwarded-Proto", "https, http")]).lower()
    lines = [("X-Forwarded-Proto", "https"), ("X-Forwarded-Proto", "http")]
    assert "secure" not in _session_cookie(PROXY, lines).lower()


# --- The passkey party without a public address ----------------------------------------------------------------- #


def test_the_passkey_party_takes_https_from_a_trusted_proxy_and_from_nobody_else(trusted: None) -> None:
    with SessionLocal() as db:
        party = passkeys.party(db, request_from(PROXY, "https"))
        assert party == passkeys.Party(rp_id=HOST, origin=f"https://{HOST}")
        # Anyone else saying https: the party is plain http, and there are no passkeys there.
        for refused in (request_from(STRANGER, "https"), request_from(PROXY, "https, http"), request_from(PROXY, "https", "http")):
            with pytest.raises(passkeys.PasskeyError) as caught:
                passkeys.party(db, refused)
            assert caught.value.code == "passkeys_need_https"
        # The host is the request's own; X-Forwarded-Host is believed from nobody.
        bent = request_from(PROXY, "https", extra=[(b"x-forwarded-host", b"evil.example.net")])
        assert passkeys.party(db, bent) == passkeys.Party(rp_id=HOST, origin=f"https://{HOST}")
        bent = request_from(STRANGER, "https", host="localhost:8460", extra=[(b"x-forwarded-host", b"evil.example.net")])
        assert passkeys.party(db, bent) == passkeys.Party(rp_id="localhost", origin="http://localhost:8460")


def test_a_passkey_begins_behind_a_trusted_proxy_without_a_public_address(operator: dict, trusted: None) -> None:
    def begin(peer: str) -> int:
        browser = TestClient(app, base_url=f"http://{HOST}", client=(peer, 4000))
        assert browser.post("/api/auth/login", json={"name": "admin", "password": PASSWORD}, headers=UI).status_code == 200
        return browser.post("/api/auth/passkeys/begin", headers={**UI, "X-Forwarded-Proto": "https"}).status_code

    assert begin(PROXY) == 200
    assert begin(STRANGER) == 409


# --- The sender's address ---------------------------------------------------------------------------------------- #


def test_the_sender_is_read_from_every_forwarded_for_line(trusted: None) -> None:
    # A proxy that adds its own line below the sender's (HAProxy's option forwardfor): the sender's own line
    # alone would let it pick an address for every try.
    two_lines = [(b"x-forwarded-for", b"192.0.2.99"), (b"x-forwarded-for", b"198.51.100.20")]
    assert deps.client_ip(request_from(PROXY, extra=two_lines)) == "198.51.100.20"
    assert deps.client_ip(request_from(PROXY, extra=[(b"x-forwarded-for", b"192.0.2.99, 198.51.100.20")])) == "198.51.100.20"
    assert deps.client_ip(request_from(STRANGER, extra=two_lines)) == STRANGER


def test_the_image_leaves_proxy_headers_to_the_app() -> None:
    """uvicorn on its own believes 127.0.0.1; in the image only NEXTRMNL_TRUSTED_PROXIES decides."""
    dockerfile = (Path(__file__).resolve().parents[2] / "Dockerfile").read_text(encoding="utf-8")
    command = [line for line in dockerfile.splitlines() if line.startswith("CMD ")]
    assert len(command) == 1
    assert '"--no-proxy-headers"' in command[0] and "forwarded-allow-ips" not in command[0]

"""OIDC sign-in against a fake provider: discovery, JWKS, token endpoint and userinfo answered by a MockTransport.

The provider signs with an RSA key generated here. The token endpoint checks code, PKCE verifier and client
secret like a real one would; whatever passes against it and is refused by the tampered variants is the
standard, not luck. No test touches the network.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import time
from collections.abc import Iterator
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Account
from app.services import oidc
from tests.conftest import PASSWORD, UI, invite_member

ISSUER = "https://sso.example.com"
CLIENT_ID = "nextrmnl-client"
CLIENT_SECRET = "a-client-secret-nobody-guesses"
KID = "key-1"
CODE = "one-time-code"

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_KEY_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
)
# A key the JWKS does not know, for the forged signature.
_FOREIGN = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_FOREIGN_PEM = _FOREIGN.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
)


def _jwk() -> dict:
    entry = jwt.algorithms.RSAAlgorithm.to_jwk(_KEY.public_key(), as_dict=True)
    entry.update({"kid": KID, "use": "sig", "alg": "RS256"})
    return entry


def _description(issuer: str = ISSUER, *, userinfo: bool = True) -> dict:
    data = {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/auth",
        "token_endpoint": f"{issuer}/token",
        "jwks_uri": f"{issuer}/jwks",
    }
    if userinfo:
        data["userinfo_endpoint"] = f"{issuer}/userinfo"
    return data


def _id_token(*, key: bytes = _KEY_PEM, kid: str | None = KID, algorithm: str = "RS256", **overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "sub": "person-1",
        "aud": CLIENT_ID,
        "exp": now + 300,
        "iat": now,
        "nonce": "nonce-1",
        "email": "alex@example.com",
        "email_verified": True,
        "preferred_username": "alex",
    }
    claims.update(overrides)
    # None means "leave the claim out".
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm=algorithm, headers={"kid": kid} if kid else None)


class FakeProvider:
    """The provider as four answers. ``claims`` overrides the id token, ``token_calls`` counts exchanges."""

    def __init__(self) -> None:
        self.claims: dict = {}
        self.token_calls = 0
        self.challenge = ""
        self.userinfo: dict | None = None
        self.reported_issuer = ISSUER
        self.with_userinfo = True
        self.raw_token: str | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if f"{request.url.scheme}://{request.url.host}" != ISSUER:
            raise httpx.ConnectError("no such host")
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(200, json=_description(self.reported_issuer, userinfo=self.with_userinfo))
        if path == "/jwks":
            return httpx.Response(200, json={"keys": [_jwk()]})
        if path == "/token":
            self.token_calls += 1
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            expected_auth = "Basic " + base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
            if request.headers.get("authorization") != expected_auth:
                return httpx.Response(401, json={"error": "invalid_client"})
            if form.get("code") != CODE or form.get("grant_type") != "authorization_code":
                return httpx.Response(400, json={"error": "invalid_grant"})
            digest = hashlib.sha256(form.get("code_verifier", "").encode()).digest()
            if base64.urlsafe_b64encode(digest).rstrip(b"=").decode() != self.challenge:
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "pkce"})
            token = self.raw_token if self.raw_token is not None else _id_token(**self.claims)
            return httpx.Response(200, json={"id_token": token, "access_token": "access-1", "token_type": "Bearer"})
        if path == "/userinfo":
            if self.userinfo is None:
                return httpx.Response(404)
            return httpx.Response(200, json=self.userinfo)
        return httpx.Response(404)


@pytest.fixture
def provider() -> Iterator[FakeProvider]:
    fake = FakeProvider()
    oidc.transport_for_tests = httpx.MockTransport(fake)
    oidc.clear_cache()
    oidc.forget_used_states()
    yield fake
    oidc.transport_for_tests = None
    oidc.clear_cache()
    oidc.forget_used_states()


def configure(client: TestClient, auto_create: bool = True, **overrides) -> dict:
    payload = {"issuer": ISSUER, "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "provider_name": "Test SSO"}
    payload.update(overrides)
    response = client.put("/api/oidc/config", json=payload, headers=UI)
    assert response.status_code == 200, response.text
    # Most tests want the provider to create accounts; the default is off and has its own test.
    allowed = client.put("/api/settings", json={"oidc_auto_create": auto_create}, headers=UI)
    assert allowed.status_code == 200, allowed.text
    return response.json()


def fresh_browser(client: TestClient) -> TestClient:
    return TestClient(client.app, base_url="http://testserver")


def start(browser: TestClient, provider: FakeProvider) -> dict[str, str]:
    """Click the button; returns the parameters of the redirect and tells the provider the PKCE challenge."""
    response = browser.get("/api/oidc/start", follow_redirects=False)
    assert response.status_code == 302, response.text
    target = urlsplit(response.headers["location"])
    assert f"{target.scheme}://{target.netloc}{target.path}" == f"{ISSUER}/auth"
    values = {k: v[0] for k, v in parse_qs(target.query).items()}
    provider.challenge = values["code_challenge"]
    return values


def come_back(browser: TestClient, state: str, code: str = CODE) -> httpx.Response:
    return browser.get(f"/api/oidc/callback?code={code}&state={state}", follow_redirects=False)


def sign_in_via_oidc(browser: TestClient, provider: FakeProvider, **claims) -> httpx.Response:
    values = start(browser, provider)
    provider.claims = {"nonce": values["nonce"], **claims}
    return come_back(browser, values["state"])


def account_count() -> int:
    with SessionLocal() as db:
        return db.query(Account).count()


def test_unknown_identity_is_refused_unless_the_operator_allows_new_accounts(
    client: TestClient, operator: dict, provider: FakeProvider
) -> None:
    configure(client, auto_create=False)
    assert client.get("/api/settings").json()["oidc_auto_create"] is False
    browser = fresh_browser(client)
    response = sign_in_via_oidc(browser, provider)
    assert response.headers["location"] == "/login?error=oidc_no_account"
    assert not browser.cookies.get("nextrmnl_session")
    assert account_count() == 1
    # An account that exists already still comes in by its verified address.
    with SessionLocal() as db:
        db.query(Account).filter(Account.name == "admin").update({"email": "alex@example.com"})
        db.commit()
    again = sign_in_via_oidc(fresh_browser(client), provider)
    assert again.headers["location"] == "/"


def test_a_signed_in_account_links_itself_to_the_provider(
    client: TestClient, operator: dict, provider: FakeProvider
) -> None:
    configure(client, auto_create=False)
    member = invite_member(client, "alex")
    assert member.get("/api/auth/me").json()["oidc_linked"] is False
    # Linking hands the account to whoever the browser is at the provider: it takes a signed-in browser, the
    # CSRF header and the password. A stranger has none of it, a wrong password is refused, and the old GET
    # with ``link=1`` starts a plain sign-in and links nothing.
    from tests.conftest import PASSWORD

    stranger = fresh_browser(client)
    assert stranger.post("/api/oidc/link/start", json={"password": PASSWORD}, headers=UI).status_code == 401
    wrong = member.post("/api/oidc/link/start", json={"password": "not-the-password"}, headers=UI)
    assert wrong.status_code == 401 and wrong.json()["detail"]["code"] == "wrong_password"
    assert member.get("/api/oidc/start?link=1", follow_redirects=False).status_code == 302
    assert member.get("/api/auth/me").json()["oidc_linked"] is False

    response = member.post("/api/oidc/link/start", json={"password": PASSWORD}, headers=UI)
    assert response.status_code == 200, response.text
    values = {k: v[0] for k, v in parse_qs(urlsplit(response.json()["url"]).query).items()}
    provider.challenge = values["code_challenge"]
    provider.claims = {"nonce": values["nonce"], "sub": "person-7"}
    back = come_back(member, values["state"])
    assert back.status_code == 303 and back.headers["location"] == "/settings?tab=account&linked=1"
    me = member.get("/api/auth/me").json()
    assert me["oidc_linked"] is True and me["sign_in"] == "password"
    with SessionLocal() as db:
        row = db.query(Account).filter(Account.name == "alex").one()
        assert row.oidc_subject == "person-7" and row.email == "alex@example.com"
    # From now on the provider identity signs into exactly this account, without any new account.
    fresh = fresh_browser(client)
    assert sign_in_via_oidc(fresh, provider, sub="person-7").headers["location"] == "/"
    assert fresh.get("/api/auth/me").json()["name"] == "alex"
    assert account_count() == 2
    # The same identity cannot be hooked to a second account.
    other = invite_member(client, "sam")
    response = other.post("/api/oidc/link/start", json={"password": PASSWORD}, headers=UI)
    values = {k: v[0] for k, v in parse_qs(urlsplit(response.json()["url"]).query).items()}
    provider.challenge = values["code_challenge"]
    provider.claims = {"nonce": values["nonce"], "sub": "person-7"}
    taken = come_back(other, values["state"])
    assert taken.headers["location"] == "/settings?tab=account&error=oidc_subject_taken"
    with SessionLocal() as db:
        assert db.query(Account).filter(Account.name == "sam").one().oidc_subject == ""
    # Unlinking: the password account may, an OIDC-only account may not.
    assert member.delete("/api/oidc/link", headers=UI).status_code == 204
    assert member.get("/api/auth/me").json()["oidc_linked"] is False
    configure(client, auto_create=True)
    created = fresh_browser(client)
    sign_in_via_oidc(created, provider, sub="person-9", email="nine@example.com", preferred_username="nine")
    refused = created.delete("/api/oidc/link", headers=UI)
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "oidc_only_account"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_config_is_validated_by_discovery_and_never_shows_the_secret(
    client: TestClient, operator: dict, provider: FakeProvider
) -> None:
    assert client.get("/api/oidc/state").json() == {"enabled": False, "provider_name": "OpenID Connect"}

    unreachable = client.put(
        "/api/oidc/config",
        json={"issuer": "https://nothing.example.com", "client_id": "x", "client_secret": "y"},
        headers=UI,
    )
    assert unreachable.status_code == 422
    assert unreachable.json()["detail"]["code"] == "issuer_unreachable"

    provider.reported_issuer = "https://other.example.com"
    wrong = client.put(
        "/api/oidc/config", json={"issuer": ISSUER, "client_id": "x", "client_secret": "y"}, headers=UI
    )
    assert wrong.status_code == 422
    assert wrong.json()["detail"]["code"] == "issuer_invalid"
    provider.reported_issuer = ISSUER

    body = configure(client)
    assert body["configured"] is True
    assert body["client_id"] == CLIENT_ID
    assert body["redirect_uri"] == "http://testserver/api/oidc/callback"
    assert CLIENT_SECRET not in str(body)
    assert "secret" not in " ".join(body)
    assert client.get("/api/oidc/config").json()["provider_name"] == "Test SSO"

    # The sign-in page sees the button without a session.
    outsider = fresh_browser(client)
    assert outsider.get("/api/oidc/state").json() == {"enabled": True, "provider_name": "Test SSO"}

    # An empty secret keeps the stored one; then the configuration is removed.
    configure(client, client_secret="", provider_name="Renamed")
    assert client.get("/api/oidc/state").json()["provider_name"] == "Renamed"
    assert client.delete("/api/oidc/config", headers=UI).status_code == 204
    assert client.get("/api/oidc/state").json()["enabled"] is False
    assert outsider.get("/api/oidc/start", follow_redirects=False).headers["location"] == "/login?error=oidc_not_configured"


def test_config_is_operator_only(client: TestClient, operator: dict, provider: FakeProvider) -> None:
    member = invite_member(client)
    assert member.get("/api/oidc/config").status_code == 403
    payload = {"issuer": ISSUER, "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}
    assert member.put("/api/oidc/config", json=payload, headers=UI).status_code == 403
    assert member.delete("/api/oidc/config", headers=UI).status_code == 403
    assert member.get("/api/oidc/authentik/blueprint").status_code == 403


# ---------------------------------------------------------------------------
# Outbound
# ---------------------------------------------------------------------------


def test_start_sets_the_attempt_cookie_and_redirects_with_pkce(
    client: TestClient, operator: dict, provider: FakeProvider
) -> None:
    configure(client)
    browser = fresh_browser(client)
    values = start(browser, provider)
    assert values["response_type"] == "code"
    assert values["client_id"] == CLIENT_ID
    assert values["redirect_uri"] == "http://testserver/api/oidc/callback"
    assert values["code_challenge_method"] == "S256"
    assert "openid" in values["scope"].split()
    for required in ("state", "nonce", "code_challenge"):
        assert values.get(required), f"{required} missing in the redirect"

    cookie = browser.cookies.get(oidc.COOKIE_NAME)
    assert cookie
    attempt = oidc.read_attempt(cookie)
    assert attempt is not None
    assert attempt["state"] == values["state"] and attempt["nonce"] == values["nonce"]
    digest = hashlib.sha256(attempt["verifier"].encode()).digest()
    assert base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == values["code_challenge"]
    # A tampered cookie is worthless.
    assert oidc.read_attempt(cookie[:-3] + "xyz") is None


# ---------------------------------------------------------------------------
# Return: refusals
# ---------------------------------------------------------------------------


def test_callback_with_a_wrong_state_is_refused(client: TestClient, operator: dict, provider: FakeProvider) -> None:
    configure(client)
    browser = fresh_browser(client)
    values = start(browser, provider)
    provider.claims = {"nonce": values["nonce"]}
    response = come_back(browser, "a-foreign-state")
    assert response.status_code == 303
    assert response.headers["location"] == "/login?error=oidc_state_mismatch"
    assert not browser.cookies.get("nextrmnl_session")
    assert provider.token_calls == 0
    assert account_count() == 1


def test_callback_without_the_cookie_is_refused(client: TestClient, operator: dict, provider: FakeProvider) -> None:
    configure(client)
    browser = fresh_browser(client)
    values = start(browser, provider)
    stranger = fresh_browser(client)
    response = come_back(stranger, values["state"])
    assert response.headers["location"] == "/login?error=oidc_state_mismatch"
    assert provider.token_calls == 0


def test_provider_error_is_reported_before_the_state_check(
    client: TestClient, operator: dict, provider: FakeProvider, caplog: pytest.LogCaptureFixture
) -> None:
    configure(client)
    browser = fresh_browser(client)
    start(browser, provider)
    # Foreign text from a public address goes into the log at DEBUG only: at the default level a stranger who
    # knows the callback address could otherwise roll the audit lines out of the ring.
    with caplog.at_level(logging.DEBUG, logger="nextrmnl.oidc"):
        response = browser.get(
            "/api/oidc/callback?error=access_denied&error_description=User%20cancelled%0Aline2", follow_redirects=False
        )
    assert response.headers["location"] == "/login?error=oidc_denied"
    assert "access_denied" in caplog.text
    assert all(record.levelno == logging.DEBUG for record in caplog.records if "callback refused" in record.message)
    # The foreign text stays on one log line.
    lines = [line for line in caplog.messages if "callback refused" in line]
    assert len(lines) == 1 and "\n" not in lines[0]


def test_callback_with_a_tampered_signature_is_refused(
    client: TestClient, operator: dict, provider: FakeProvider
) -> None:
    configure(client)
    browser = fresh_browser(client)
    values = start(browser, provider)
    provider.raw_token = _id_token(key=_FOREIGN_PEM, nonce=values["nonce"])
    response = come_back(browser, values["state"])
    assert response.headers["location"] == "/login?error=oidc_token_invalid"
    assert not browser.cookies.get("nextrmnl_session")
    assert account_count() == 1


def test_hs256_token_is_refused(client: TestClient, operator: dict, provider: FakeProvider) -> None:
    """The classic attack: bend ``alg`` to HS256 and sign with a guessable secret."""
    configure(client)
    browser = fresh_browser(client)
    values = start(browser, provider)
    provider.raw_token = _id_token(key=b"some-secret-long-enough-for-hs256-signing", algorithm="HS256", nonce=values["nonce"])
    response = come_back(browser, values["state"])
    assert response.headers["location"] == "/login?error=oidc_token_invalid"
    assert account_count() == 1


def test_tampered_nonce_is_refused(client: TestClient, operator: dict, provider: FakeProvider) -> None:
    """A correctly signed token from another run must not sign anybody in."""
    configure(client)
    browser = fresh_browser(client)
    values = start(browser, provider)
    provider.claims = {"nonce": "a-different-run"}
    response = come_back(browser, values["state"])
    assert response.headers["location"] == "/login?error=oidc_token_invalid"
    assert not browser.cookies.get("nextrmnl_session")
    assert account_count() == 1


@pytest.mark.parametrize(
    "broken",
    [
        {"iss": "https://evil.example.com"},
        {"aud": "another-app"},
        {"exp": int(time.time()) - 600},
        {"sub": None},
        {"azp": "another-app"},
    ],
)
def test_broken_tokens_are_refused(client: TestClient, operator: dict, provider: FakeProvider, broken: dict) -> None:
    configure(client)
    browser = fresh_browser(client)
    response = sign_in_via_oidc(browser, provider, **broken)
    assert response.headers["location"] == "/login?error=oidc_token_invalid"
    assert account_count() == 1


def test_unverified_email_is_refused(client: TestClient, operator: dict, provider: FakeProvider) -> None:
    """``email_verified: false`` means the provider does not vouch for the address; no account, no bridge."""
    configure(client)
    browser = fresh_browser(client)
    response = sign_in_via_oidc(browser, provider, email_verified=False)
    assert response.headers["location"] == "/login?error=oidc_email_unverified"
    assert not browser.cookies.get("nextrmnl_session")
    assert account_count() == 1
    # A string "false" is not a confirmation either, and neither is a missing address.
    response = sign_in_via_oidc(fresh_browser(client), provider, email_verified="false")
    assert response.headers["location"] == "/login?error=oidc_email_unverified"
    response = sign_in_via_oidc(fresh_browser(client), provider, email=None, email_verified=None)
    assert response.headers["location"] == "/login?error=oidc_email_unverified"
    assert account_count() == 1


# ---------------------------------------------------------------------------
# Return: accounts
# ---------------------------------------------------------------------------


def test_new_user_gets_a_member_account_and_a_session(
    client: TestClient, operator: dict, provider: FakeProvider
) -> None:
    configure(client)
    browser = fresh_browser(client)
    response = sign_in_via_oidc(browser, provider)
    assert response.status_code == 303, response.text
    assert response.headers["location"] == "/"
    assert browser.cookies.get("nextrmnl_session")
    assert not browser.cookies.get(oidc.COOKIE_NAME)
    assert provider.token_calls == 1

    me = browser.get("/api/auth/me")
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["name"] == "alex" and body["role"] == "member" and body["sign_in"] == "oidc"
    assert body["email"] == "alex@example.com"
    assert body["vault"] == "unset"
    with SessionLocal() as db:
        row = db.query(Account).filter(Account.name == "alex").one()
        assert row.oidc_subject == "person-1" and row.password_hash == ""
    # The new member is an ordinary member: no operator pages.
    assert browser.get("/api/settings").status_code == 403


def test_second_callback_for_the_same_subject_signs_into_the_same_account(
    client: TestClient, operator: dict, provider: FakeProvider
) -> None:
    configure(client)
    first = sign_in_via_oidc(fresh_browser(client), provider)
    assert first.headers["location"] == "/"
    # Name and address may have changed at the provider; the subject decides.
    again = fresh_browser(client)
    response = sign_in_via_oidc(again, provider, preferred_username="alexander", email="alex.new@example.com")
    assert response.headers["location"] == "/"
    assert again.get("/api/auth/me").json()["name"] == "alex"
    assert account_count() == 2


def test_existing_password_account_with_the_same_verified_email_is_linked_once(
    client: TestClient, operator: dict, provider: FakeProvider
) -> None:
    configure(client)
    with SessionLocal() as db:
        db.query(Account).filter(Account.name == "admin").update({"email": "Alex@example.com"})
        db.commit()

    browser = fresh_browser(client)
    response = sign_in_via_oidc(browser, provider)
    assert response.headers["location"] == "/"
    me = browser.get("/api/auth/me").json()
    assert me["name"] == "admin" and me["role"] == "operator" and me["sign_in"] == "password"
    assert account_count() == 1
    with SessionLocal() as db:
        assert db.query(Account).filter(Account.name == "admin").one().oidc_subject == "person-1"

    # The password still works: the account keeps its vault.
    other = fresh_browser(client)
    assert other.post("/api/auth/login", json={"name": "admin", "password": PASSWORD}, headers=UI).status_code == 200

    # A different subject with the same verified address does not take the account over.
    intruder = fresh_browser(client)
    response = sign_in_via_oidc(intruder, provider, sub="person-2")
    assert response.headers["location"] == "/login?error=oidc_email_taken"
    assert not intruder.cookies.get("nextrmnl_session")
    assert account_count() == 1


def test_state_is_single_use(
    client: TestClient, operator: dict, provider: FakeProvider, caplog: pytest.LogCaptureFixture
) -> None:
    configure(client)
    browser = fresh_browser(client)
    values = start(browser, provider)
    attempt_cookie = browser.cookies.get(oidc.COOKIE_NAME)
    provider.claims = {"nonce": values["nonce"]}
    assert come_back(browser, values["state"]).headers["location"] == "/"
    assert provider.token_calls == 1

    # The same callback again, with the original cookie sent along by hand: refused before the provider is
    # asked, and refused because the state was used, not because the cookie was missing.
    replay = fresh_browser(client)
    with caplog.at_level(logging.DEBUG, logger="nextrmnl.oidc"):
        response = replay.get(
            f"/api/oidc/callback?code={CODE}&state={values['state']}",
            headers={"Cookie": f"{oidc.COOKIE_NAME}={attempt_cookie}"},
            follow_redirects=False,
        )
    assert response.headers["location"] == "/login?error=oidc_state_mismatch"
    assert "state was already used" in caplog.text
    assert not replay.cookies.get("nextrmnl_session")
    assert provider.token_calls == 1


def test_address_may_come_from_userinfo(client: TestClient, operator: dict, provider: FakeProvider) -> None:
    """Authelia and Zitadel keep the address at userinfo; a foreign subject there is discarded."""
    configure(client)
    provider.userinfo = {"sub": "person-1", "email": "alex@example.com", "email_verified": True}
    response = sign_in_via_oidc(fresh_browser(client), provider, email=None, email_verified=None)
    assert response.headers["location"] == "/"
    assert account_count() == 2

    provider.userinfo = {"sub": "somebody-else", "email": "victim@example.com", "email_verified": True}
    response = sign_in_via_oidc(fresh_browser(client), provider, sub="person-3", email=None, email_verified=None)
    assert response.headers["location"] == "/login?error=oidc_email_unverified"
    assert account_count() == 2


def test_no_secret_leaves_no_trace_in_the_log(
    client: TestClient, operator: dict, provider: FakeProvider, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        configure(client)
        browser = fresh_browser(client)
        values = start(browser, provider)
        provider.claims = {"nonce": values["nonce"]}
        come_back(browser, values["state"])
    for secret in (CLIENT_SECRET, values["state"], values["nonce"], browser.cookies.get("nextrmnl_session") or "?"):
        assert secret not in caplog.text

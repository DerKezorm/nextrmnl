"""The sign-in module 1.0.11 and the review after it: the way back through an issuer change and a deleted account,
the sessions that rest on a provider's second factor, what the sign-in answers say about the requirement, the public
address and the scheme a proxy tells.
"""

from __future__ import annotations

import logging
import time

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import deps
from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.models import SIGN_IN_OIDC
from app.services import accounts, backups, oidc_store, settings_service, totp
from tests.conftest import PASSWORD, UI, sign_in
from tests.oidc_helpers import FakeProvider, add_provider, fresh_browser, link, location, sign_in_via_oidc
from tests.test_oidc_app import (
    _entries,
    _forced_to_set_up,
    _legacy,
    _old,
    _require_second_factor,
    browser_of,
    make_account,
    row_of,
)
from tests.test_passkeys import RP_ID, SoftKey, add_key


def _body_of(view: dict) -> dict:
    return {key: view[key] for key in ("label", "issuer", "client_id", "scopes", "enabled", "auto_create", "trusts_second_factor")}


def _save(client: TestClient, entry_id: int, **change: object):
    view = next(item for item in client.get("/api/oidc/admin/providers").json() if item["id"] == entry_id)
    return client.put(f"/api/oidc/admin/providers/{entry_id}", json={**_body_of(view), **change}, headers=UI)


# --- Issuer change and deleting an account: the way back stays what it was -------------------------------------------


def test_changing_the_issuer_of_the_entry_oidc_keeps_it_through_the_next_start(
    client: TestClient, operator: dict, provider: FakeProvider, caplog: pytest.LogCaptureFixture
) -> None:
    """The old settings are emptied for good (the older version finds no provider), but the entry stays, and the
    next start sees nothing the older version did: no entry removed, no line about taking over, no backup."""
    from tests.oidc_helpers import fakes

    make_account("anna")
    _legacy(provider, label="Company", subjects={"anna": "anna-1"})
    entry = oidc_store.migrate_settings(backup=False)
    assert entry is not None
    moved = provider.network.add(  # type: ignore[attr-defined]
        fakes.FakeProvider("https://moved.example.com", client_id=provider.client_id, client_secret=provider.client_secret)
    )
    assert _save(client, entry.id, issuer=moved.issuer).status_code == 200
    assert _old()["issuer"] == "" and _old()["subjects"]["anna"] == ""
    before = len(backups.entries())
    with caplog.at_level(logging.INFO):
        assert oidc_store.migrate_settings() is None
    [kept] = _entries()
    assert kept.slug == "oidc" and kept.issuer == moved.issuer
    assert len(backups.entries()) == before
    assert "taken over" not in caplog.text and "removed" not in caplog.text
    assert _old()["issuer"] == ""


def test_deleting_an_account_leaves_the_next_start_nothing_to_take_over(
    client: TestClient, operator: dict, provider: FakeProvider, caplog: pytest.LogCaptureFixture
) -> None:
    """The operator's delete route removes the account and its links; the way back and its fingerprint follow in
    the same commit, or the next start takes the change for the older version's and makes a backup for nothing."""
    make_account("anna")
    make_account("ben")
    _legacy(provider, label="Company", subjects={"anna": "anna-1", "ben": "ben-1"})
    assert oidc_store.migrate_settings(backup=False) is not None
    before = len(backups.entries())
    assert client.delete(f"/api/accounts/{row_of('ben').id}", headers=UI).status_code == 204
    assert _old()["subjects"] == {"admin": "", "anna": "anna-1"}
    with caplog.at_level(logging.INFO):
        assert oidc_store.migrate_settings() is None
    assert len(backups.entries()) == before
    assert "taken over" not in caplog.text
    with SessionLocal() as db:
        assert len(oidc_store.SqlStore(db).links_of_provider(_entries()[0].id)) == 1


def test_the_way_back_seals_the_secret_only_when_it_changed(
    client: TestClient, operator: dict, provider: FakeProvider
) -> None:
    """Sealed afresh every time the text would differ at every commit; the fingerprint reads the opened secret, so
    only a write for nothing would show: the stored text stays as it is."""
    make_account("anna")
    _legacy(provider, label="Company", subjects={"anna": "anna-1"})
    assert oidc_store.migrate_settings(backup=False) is not None

    def sealed() -> str:
        with SessionLocal() as db:
            return str(settings_service.get(db, "oidc_client_secret_enc"))

    first = sealed()
    assert first and _old()["secret"] == provider.client_secret
    assert oidc_store.migrate_settings() is None
    assert client.put(f"/api/oidc/admin/providers/{_entries()[0].id}", json=_body_of(_view(client)), headers=UI).status_code == 200
    assert sealed() == first


def _view(client: TestClient) -> dict:
    return client.get("/api/oidc/admin/providers").json()[0]


# --- Sessions that rest on a provider's second factor ----------------------------------------------------------------


def _free_session(client: TestClient, provider: FakeProvider, name: str = "anna", sub: str = "anna-1"):
    """``name`` linked to the provider ``sso`` (trusted), the requirement on, and a session that came in through the
    provider: held to nothing. Returns that browser."""
    make_account(name)
    assert location(link(browser_of(name), provider, PASSWORD, sub=sub)) == "/account?linked=sso"
    free = fresh_browser()
    assert location(sign_in_via_oidc(free, provider, sub=sub)) == "/"
    return free


def _trusted_entry(client: TestClient, provider: FakeProvider) -> int:
    """The entry ``sso``, trusted, and a second factor for the operator: the requirement that follows holds every
    password session without one, the operator's own included, and the operator still has to administer."""
    begun = client.post("/api/auth/totp/begin", headers=UI)
    assert begun.status_code == 200, begun.text
    code = totp.code_at(begun.json()["secret"], time.time())
    assert client.post("/api/auth/totp/confirm", json={"code": code, "password": PASSWORD}, headers=UI).status_code == 200
    return int(add_provider(client, provider)["id"])


def test_a_session_through_a_provider_is_held_to_the_requirement_again_when_the_entry_stops_being_trusted(
    client: TestClient, operator: dict, provider: FakeProvider
) -> None:
    entry_id = _trusted_entry(client, provider)
    free = _free_session(client, provider)
    _require_second_factor(client)
    assert _forced_to_set_up(free) is False
    assert _save(client, entry_id, trusts_second_factor=False).status_code == 200
    assert _forced_to_set_up(free) is True


def test_a_session_through_a_provider_is_held_again_when_the_entry_goes_off_or_away(
    client: TestClient, operator: dict, provider: FakeProvider
) -> None:
    entry_id = _trusted_entry(client, provider)
    free = _free_session(client, provider)
    _require_second_factor(client)
    assert _save(client, entry_id, enabled=False).status_code == 200
    assert _forced_to_set_up(free) is True
    # On again does not give back what was taken from the session; a new sign-in through the provider does.
    assert _save(client, entry_id, enabled=True).status_code == 200
    assert _forced_to_set_up(free) is True
    again = fresh_browser()
    assert location(sign_in_via_oidc(again, provider, sub="anna-1")) == "/"
    assert _forced_to_set_up(again) is False
    assert client.delete(f"/api/oidc/admin/providers/{entry_id}", headers=UI).status_code == 200
    assert _forced_to_set_up(again) is True


def test_a_session_through_a_provider_is_held_again_when_the_account_lets_go_of_the_link(
    client: TestClient, operator: dict, provider: FakeProvider
) -> None:
    _trusted_entry(client, provider)
    free = _free_session(client, provider)
    _require_second_factor(client)
    assert _forced_to_set_up(free) is False
    assert free.delete("/api/oidc/sso/link", headers=UI).status_code == 204
    assert _forced_to_set_up(free) is True


def test_the_operator_taking_the_link_ends_the_session_altogether(
    client: TestClient, operator: dict, provider: FakeProvider
) -> None:
    entry_id = _trusted_entry(client, provider)
    free = _free_session(client, provider)
    _require_second_factor(client)
    gone = client.request(
        "DELETE", f"/api/oidc/admin/accounts/{row_of('anna').id}/links/{entry_id}", json={"password": PASSWORD}, headers=UI
    )
    assert gone.status_code == 204
    assert free.get("/api/auth/me").status_code == 401


def test_changing_the_issuer_takes_the_exemption_with_the_links(
    client: TestClient, operator: dict, provider: FakeProvider
) -> None:
    from tests.oidc_helpers import fakes

    entry_id = _trusted_entry(client, provider)
    free = _free_session(client, provider)
    _require_second_factor(client)
    moved = provider.network.add(  # type: ignore[attr-defined]
        fakes.FakeProvider("https://moved.example.com", client_id=provider.client_id, client_secret=provider.client_secret)
    )
    assert _save(client, entry_id, issuer=moved.issuer).status_code == 200
    assert _forced_to_set_up(free) is True


def test_a_start_that_takes_over_a_link_the_older_version_dropped_holds_the_session_again(
    client: TestClient, operator: dict, provider: FakeProvider
) -> None:
    _legacy(provider, label="Company", subjects={})
    make_account("anna")
    with SessionLocal() as db:
        accounts.by_name(db, "anna").oidc_subject = "anna-1"  # type: ignore[union-attr]
        db.commit()
    assert oidc_store.migrate_settings(backup=False) is not None
    free = fresh_browser()
    assert location(sign_in_via_oidc(free, provider, slug="oidc", sub="anna-1")) == "/"
    _require_second_factor(client)
    assert _forced_to_set_up(free) is False
    with SessionLocal() as db:
        accounts.by_name(db, "anna").oidc_subject = ""  # type: ignore[union-attr]
        db.commit()
    assert oidc_store.migrate_settings(backup=False) is None
    assert _forced_to_set_up(free) is True


def test_only_the_accounts_of_the_entry_lose_the_exemption(
    client: TestClient, operator: dict, provider: FakeProvider
) -> None:
    from tests.oidc_helpers import fakes

    entry_id = _trusted_entry(client, provider)
    other = provider.network.add(  # type: ignore[attr-defined]
        fakes.FakeProvider("https://other.example.com", client_id="other-client", client_secret="other-secret")
    )
    add_provider(client, other, slug="work", label="Work")
    anna = _free_session(client, provider)
    make_account("ben")
    assert location(link(browser_of("ben"), other, PASSWORD, slug="work", sub="ben-1")) == "/account?linked=work"
    ben = fresh_browser()
    assert location(sign_in_via_oidc(ben, other, slug="work", sub="ben-1")) == "/"
    _require_second_factor(client)
    assert (_forced_to_set_up(anna), _forced_to_set_up(ben)) == (False, False)
    assert _save(client, entry_id, trusts_second_factor=False).status_code == 200
    assert (_forced_to_set_up(anna), _forced_to_set_up(ben)) == (True, False)


# --- What the answers say about the requirement ----------------------------------------------------------------------


def test_the_password_sign_in_answer_carries_the_requirement(client: TestClient, operator: dict) -> None:
    make_account("anna")
    _require_second_factor(client)
    answer = fresh_browser().post("/api/auth/login", json={"name": "anna", "password": PASSWORD})
    assert answer.status_code == 200 and answer.json()["second_factor_setup_required"] is True


def test_an_account_through_providers_alone_is_not_forced_whatever_the_provider_is_trusted_with(
    client: TestClient, operator: dict, provider: FakeProvider
) -> None:
    add_provider(client, provider, auto_create=True, trusts_second_factor=False)
    _require_second_factor(client)
    browser = fresh_browser()
    assert location(sign_in_via_oidc(browser, provider, sub="clara-1", preferred_username="clara")) == "/"
    assert row_of("clara").sign_in == SIGN_IN_OIDC
    assert _forced_to_set_up(browser) is False


def test_turning_the_second_factor_off_answers_for_the_session_that_asks(
    client: TestClient, operator: dict, provider: FakeProvider
) -> None:
    """A session that came in through the provider is not held to the requirement, also in the answer to the call that
    takes its account's second factor away; the page behind and the answer agree."""
    _trusted_entry(client, provider)
    free = _free_session(client, provider)
    begun = free.post("/api/auth/totp/begin", headers=UI)
    assert begun.status_code == 200
    code = totp.code_at(begun.json()["secret"], time.time())
    assert free.post("/api/auth/totp/confirm", json={"code": code, "password": PASSWORD}, headers=UI).status_code == 200
    _require_second_factor(client)
    off = free.post("/api/auth/totp/disable", json={"password": PASSWORD}, headers=UI)
    assert off.status_code == 200, off.text
    assert off.json()["two_factor"] is False and off.json()["second_factor_setup_required"] is False
    assert _forced_to_set_up(free) is False


def test_removing_the_last_passkey_answers_for_the_session_that_asks(
    client: TestClient, operator: dict, provider: FakeProvider
) -> None:
    _trusted_entry(client, provider)
    free = _free_session(client, provider)
    # Passkeys need the https address; set after the sign-in, whose attempt cookie is Secure on https.
    with SessionLocal() as db:
        settings_service.save(db, {"public_url": f"https://{RP_ID}"})
    _require_second_factor(client)
    added = add_key(free, SoftKey())
    assert added.status_code == 201, added.text
    assert added.json()["account"]["second_factor_setup_required"] is False
    [key] = free.get("/api/auth/passkeys").json()
    removed = free.post(f"/api/auth/passkeys/{key['id']}/remove", json={"password": PASSWORD}, headers=UI)
    assert removed.status_code == 200, removed.text
    assert removed.json()["two_factor"] is False and removed.json()["second_factor_setup_required"] is False
    assert _forced_to_set_up(free) is False


def test_a_read_key_works_whatever_the_requirement_says(client: TestClient, operator: dict) -> None:
    """Read keys for dashboards (``/api/v1``) never wait for a second factor: the operator makes one first, then
    requires the factor for everybody, and the key keeps working. So no key has anything to say about the factor."""
    assert client.put("/api/settings", json={"api_keys_allowed": True}, headers=UI).status_code == 200
    made = client.post("/api/api-keys", json={"name": "nexdeck"}, headers=UI)
    assert made.status_code == 201, made.text
    _require_second_factor(client)
    assert client.get("/api/connections").status_code == 403  # the operator's own session is held now
    outsider = TestClient(app, base_url="http://testserver")
    answer = outsider.get("/api/v1/status", headers={"Authorization": f"Bearer {made.json()['key']}"})
    assert answer.status_code == 200, answer.text


# --- The public address ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "https://nextrmnl.example.com:0",
        "https://nextrmnl.example.com:99999",
        "https://-bad.example.com",
        "https://" + "a" * 190 + ".example.com",
    ],
)
def test_the_settings_page_takes_nothing_the_sign_in_module_turns_down(
    client: TestClient, operator: dict, address: str
) -> None:
    refused = client.put("/api/settings", json={"public_url": address}, headers=UI)
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == "public_url_invalid"
    assert client.get("/api/settings").json()["public_url"] == ""


@pytest.mark.parametrize(
    "address",
    ["https://nextrmnl.example.com:0", "https://-bad.example.com", "https://" + "a" * 190 + ".example.com", "https://[::1"],
)
def test_the_check_of_the_address_itself_is_as_strict_as_the_module(address: str) -> None:
    """Not only the guard in front of it: the check every way of saving the address goes through says no as well."""
    with pytest.raises(ValueError):
        settings_service.normalize_public_url(address)


def test_the_authentik_card_saves_an_address_with_a_path_nowhere(client: TestClient, operator: dict) -> None:
    """nextrmnl lives at the root of its host (passkeys and the WebSocket check take the origin): an address with a
    path is no address here, and the module's route says so without saving anything."""
    refused = client.post("/api/oidc/authentik/address", json={"public_url": "https://rmnl.example.com/sub"}, headers=UI)
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "public_url_needed"
    assert client.get("/api/settings").json()["public_url"] == ""
    saved = client.post("/api/oidc/authentik/address", json={"public_url": "https://rmnl.example.com"}, headers=UI)
    assert saved.status_code == 200 and saved.json()["public_url"] == "https://rmnl.example.com"
    assert client.get("/api/settings").json()["public_url"] == "https://rmnl.example.com"


# --- Behind a proxy that ends TLS ------------------------------------------------------------------------------------


def _request(peer: str, proto: str | None) -> Request:
    headers = [(b"x-forwarded-proto", proto.encode("ascii"))] if proto is not None else []
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"host", b"rmnl.example.com"), *headers],
        "client": (peer, 1234),
        "query_string": b"",
        "scheme": "http",
        "server": ("rmnl.example.com", 80),
    }
    return Request(scope)


def test_the_scheme_comes_from_a_proxy_nextrmnl_trusts_and_from_nobody_else(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "trusted_proxies", "203.0.113.0/24")
    # The trusted proxy says https: that is the address of the app.
    assert deps.request_base(_request("203.0.113.5", "https")) == "https://rmnl.example.com/"
    # Several hops: the value the nearest proxy wrote counts, the one the client may have made up does not.
    assert deps.request_base(_request("203.0.113.5", "https, http")) == "http://rmnl.example.com/"
    assert deps.request_base(_request("203.0.113.5", "http, https")) == "https://rmnl.example.com/"
    # Nothing it can use: the request's own scheme.
    assert deps.request_base(_request("203.0.113.5", None)) == "http://rmnl.example.com/"
    assert deps.request_base(_request("203.0.113.5", "javascript")) == "http://rmnl.example.com/"
    # Any other sender: ignored, however it asks.
    assert deps.request_base(_request("198.51.100.7", "https")) == "http://rmnl.example.com/"
    monkeypatch.setattr(settings, "trusted_proxies", "")
    assert deps.request_base(_request("203.0.113.5", "https")) == "http://rmnl.example.com/"


def test_the_authentik_card_sees_the_scheme_a_trusted_proxy_tells(
    client: TestClient, operator: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "trusted_proxies", "203.0.113.0/24")

    def seen_through(peer: str) -> dict:
        browser = TestClient(app, base_url="http://rmnl.example.com", client=(peer, 50000), headers=UI)
        sign_in(browser, "admin")
        answer = browser.get("/api/oidc/authentik/address", headers={"X-Forwarded-Proto": "https"})
        assert answer.status_code == 200, answer.text
        return answer.json()

    trusted = seen_through("203.0.113.5")
    assert trusted["seen"] == "https://rmnl.example.com" and trusted["proxy_ignored"] is False
    stranger = seen_through("198.51.100.7")
    assert stranger["seen"] == "http://rmnl.example.com" and stranger["proxy_ignored"] is True


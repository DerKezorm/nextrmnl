"""nextrmnl's own side of sign-in through providers, around the shared contract tests (``tests/nexoidc``): the
migration from the single provider of 0.4, the way back to it, the buttons of the sign-in page, the operator's
account list, the code step after a provider that is not trusted with the second factor, invitations with role and
end, the address, the direct link of 0.4.1, and the log.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import crypto
from app.db import SessionLocal
from app.models import MEMBER, OPERATOR, SIGN_IN_OIDC, Account, Invite, OidcLink, OidcProvider, utcnow
from app.routers.auth import PENDING_COOKIE
from app.routers.oidc import NEXT_COOKIE
from app.security import hash_password, hash_token
from app.services import accounts, backups, oidc_store, settings_service, totp
from tests.conftest import PASSWORD, sign_in
from tests.oidc_helpers import (
    UI,
    FakeProvider,
    add_provider,
    error_in,
    fresh_browser,
    link,
    location,
    sign_in_via_oidc,
)

AUTHENTIK_ISSUER = "https://sso.example.com/application/o/nextrmnl/"


def make_account(name: str, role: str = MEMBER, *, password: bool = True) -> int:
    with SessionLocal() as db:
        row = Account(
            name=name,
            role=role,
            sign_in="password" if password else SIGN_IN_OIDC,
            password_hash=hash_password(PASSWORD) if password else "",
        )
        db.add(row)
        db.commit()
        return row.id


def row_of(name: str) -> Account:
    with SessionLocal() as db:
        row = accounts.by_name(db, name)
        assert row is not None
        db.expunge(row)
        return row


def browser_of(name: str) -> TestClient:
    browser = fresh_browser()
    sign_in(browser, name)
    return browser


def _legacy(
    fake: FakeProvider,
    *,
    label: str,
    issuer: str | None = None,
    auto_create: bool = False,
    subjects: dict[str, str] | None = None,
) -> None:
    """nextrmnl 0.4.2 as it kept its one provider: in the settings, the subject at the account."""
    with SessionLocal() as db:
        settings_service.save(
            db,
            {
                "oidc_issuer": issuer or fake.issuer,
                "oidc_client_id": fake.client_id,
                "oidc_client_secret_enc": crypto.encrypt_secret(fake.client_secret),
                "oidc_provider_name": label,
                "oidc_auto_create": auto_create,
                oidc_store.MIGRATED: False,
            },
        )
        for name, subject in (subjects or {}).items():
            row = accounts.by_name(db, name)
            assert row is not None
            row.oidc_subject = subject
        db.commit()


def _entries() -> list[OidcProvider]:
    with SessionLocal() as db:
        rows = list(db.scalars(select(OidcProvider).order_by(OidcProvider.id)))
        for row in rows:
            db.expunge(row)
        return rows


def test_a_client_secret_opens_only_for_its_own_provider() -> None:
    """Bauplan 01: sealed under ``oidc-provider:<id>``; another provider's ciphertext does not open here, and the
    secrets sealed without a context before (TOTP seeds, the old setting) still open as they did."""
    sealed = crypto.encrypt_secret("client-secret-value", "oidc-provider:1")
    assert crypto.decrypt_secret(sealed, "oidc-provider:1") == "client-secret-value"
    assert crypto.decrypt_secret(sealed, "oidc-provider:2") == ""
    assert crypto.decrypt_secret(sealed) == ""
    plain = crypto.encrypt_secret("old-value")
    assert crypto.decrypt_secret(plain) == "old-value" and crypto.decrypt_secret(plain, "oidc-provider:1") == ""


# --- The migration --------------------------------------------------------------------------------------------------


def test_the_migration_makes_a_backup_first_and_keeps_name_auto_create_and_every_link(
    client: TestClient, operator: dict, provider: FakeProvider
) -> None:
    make_account("anna")
    make_account("ben")
    _legacy(provider, label="Company", auto_create=True, subjects={"anna": "anna-1", "ben": "ben-1"})
    before = len(backups.entries())
    entry = oidc_store.migrate_settings()
    assert entry is not None and len(backups.entries()) == before + 1
    assert backups.entries()[0].kind == backups.UPDATE
    [row] = _entries()
    assert (row.slug, row.label, row.auto_create, row.trusts_second_factor, row.managed, row.enabled) == (
        "oidc",
        "Company",
        True,
        True,
        "",
        True,
    )
    with SessionLocal() as db:
        links = sorted((item.subject, item.issuer) for item in db.scalars(select(OidcLink)))
        assert oidc_store.SqlStore(db).provider_by_slug("oidc").client_secret == provider.client_secret
    assert links == [("anna-1", provider.issuer.rstrip("/")), ("ben-1", provider.issuer.rstrip("/"))]
    # A second start does nothing, no second backup either.
    assert oidc_store.migrate_settings() is None and len(backups.entries()) == before + 1
    # The old address still signs anna in.
    provider.person = {"sub": "anna-1", "preferred_username": "anna"}
    browser = fresh_browser()
    target = location(browser.get("/api/oidc/oidc/start"))
    assert "%2Fapi%2Foidc%2Fcallback" in target
    assert location(browser.get("/api/oidc/callback", params=provider.authorize(target))) == "/"
    assert browser.get("/api/auth/me").json()["name"] == "anna"


def test_removing_every_entry_after_the_migration_brings_the_old_provider_back_never(
    client: TestClient, operator: dict, provider: FakeProvider
) -> None:
    _legacy(provider, label="Company")
    entry = oidc_store.migrate_settings(backup=False)
    assert entry is not None
    assert client.delete(f"/api/oidc/admin/providers/{entry.id}", headers=UI).status_code == 200
    assert oidc_store.migrate_settings(backup=False) is None and _entries() == []
    # Even with the old settings still there (the entry gone past the way back): the migration ran once, that is all.
    _legacy(provider, label="Company")
    assert oidc_store.migrate_settings(backup=False) is not None
    with SessionLocal() as db:
        db.query(OidcProvider).delete()
        db.commit()
    assert oidc_store.migrate_settings(backup=False) is None and _entries() == []


def test_the_button_of_0_4_becomes_an_entry_the_button_looks_after_and_keeps_its_address(
    client: TestClient, operator: dict, provider: FakeProvider
) -> None:
    _legacy(provider, label="authentik", issuer=AUTHENTIK_ISSUER)
    oidc_store.migrate_settings(backup=False)
    [row] = _entries()
    assert (row.slug, row.managed, row.issuer, row.redirect_path) == (
        "oidc",
        "authentik",
        AUTHENTIK_ISSUER.rstrip("/"),
        "",
    )
    view = client.get("/api/oidc/admin/providers").json()[0]
    assert view["redirect_uri"] == "http://testserver/api/oidc/callback"
    # A provider named so by hand, with another issuer, is the operator's own; so is authentik's issuer form under
    # another name.
    for label, issuer in (("authentik", None), ("Company", AUTHENTIK_ISSUER)):
        with SessionLocal() as db:
            db.query(OidcProvider).delete()
            db.commit()
        _legacy(provider, label=label, issuer=issuer)
        oidc_store.migrate_settings(backup=False)
        assert _entries()[0].managed == ""


def test_without_a_provider_in_the_settings_nothing_is_made(client: TestClient, operator: dict) -> None:
    assert oidc_store.migrate_settings(backup=False) is None and _entries() == []
    with SessionLocal() as db:
        assert settings_service.get(db, oidc_store.MIGRATED) is True


def test_the_start_moves_the_old_provider_before_anybody_asks(provider: FakeProvider) -> None:
    """The lifespan runs the migration: an installation of 0.4 has its entry ``oidc`` after the first start."""
    make_account("anna")
    _legacy(provider, label="Company", subjects={"anna": "anna-1"})
    from app.main import app

    with TestClient(app):
        pass
    assert [row.slug for row in _entries()] == ["oidc"]


def test_the_start_reads_the_tls_context_of_the_sign_in_module_ahead() -> None:
    """``nexoidc.warm()`` in the lifespan: the first sign-in after a start does not wait for the certificates."""
    from app.main import app
    from app.vendor.nexoidc import tls

    tls.forget()
    assert tls._context is None
    with TestClient(app):
        assert tls._context is not None


# --- The buttons and the operator's list ---------------------------------------------------------------------------


def test_the_sign_in_page_learns_the_active_providers_in_their_order_and_nothing_else(
    client: TestClient, operator: dict, provider: FakeProvider
) -> None:
    first = add_provider(client, provider, slug="sso", label="SSO")
    second = add_provider(client, provider, slug="work", label="Work")
    assert fresh_browser().get("/api/auth/methods").json() == {
        "password": True,
        "providers": [{"slug": "sso", "label": "SSO"}, {"slug": "work", "label": "Work"}],
    }
    client.put("/api/oidc/admin/providers/order", json={"ids": [second["id"], first["id"]]}, headers=UI)
    body = {key: first[key] for key in ("label", "issuer", "client_id", "scopes", "auto_create", "trusts_second_factor")}
    saved = client.put(f"/api/oidc/admin/providers/{first['id']}", json={**body, "enabled": False}, headers=UI)
    assert saved.status_code == 200
    assert fresh_browser().get("/api/oidc/providers").json() == [{"slug": "work", "label": "Work"}]
    assert provider.client_secret not in str(client.get("/api/oidc/admin/providers").json())


def test_password_sign_in_can_go_off_only_with_an_active_provider_and_the_operator_keeps_it(
    client: TestClient, operator: dict, provider: FakeProvider
) -> None:
    refused = client.put("/api/settings", json={"password_login": False}, headers=UI)
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "provider_first"
    entry = add_provider(client, provider)
    # An entry that is off does not count either.
    body = {key: entry[key] for key in ("label", "issuer", "client_id", "scopes", "auto_create", "trusts_second_factor")}
    client.put(f"/api/oidc/admin/providers/{entry['id']}", json={**body, "enabled": False}, headers=UI)
    assert client.put("/api/settings", json={"password_login": False}, headers=UI).status_code == 409
    client.put(f"/api/oidc/admin/providers/{entry['id']}", json={**body, "enabled": True}, headers=UI)
    assert client.put("/api/settings", json={"password_login": False}, headers=UI).status_code == 200
    assert fresh_browser().get("/api/auth/methods").json()["password"] is False
    make_account("anna")
    refused = fresh_browser().post("/api/auth/login", json={"name": "anna", "password": PASSWORD})
    assert refused.status_code == 403 and refused.json()["detail"]["code"] == "password_login_off"
    assert fresh_browser().post("/api/auth/login", json={"name": "admin", "password": PASSWORD}).status_code == 200


def test_the_account_list_shows_each_accounts_providers_and_the_operator_unlinks_with_the_own_password(
    client: TestClient, operator: dict, provider: FakeProvider
) -> None:
    entry = add_provider(client, provider, label="SSO")
    make_account("anna")
    member = browser_of("anna")
    assert location(link(member, provider, PASSWORD, sub="anna-1")) == "/account?linked=sso"
    listed = {row["name"]: row for row in client.get("/api/accounts").json()}
    assert listed["anna"]["providers"] == [{"id": entry["id"], "slug": "sso", "label": "SSO"}]
    assert listed["anna"]["oidc_links"] == 1 and listed["admin"]["providers"] == []
    address = f"/api/oidc/admin/accounts/{listed['anna']['id']}/links/{entry['id']}"
    wrong = client.request("DELETE", address, json={"password": "not-it-at-all"}, headers=UI)
    assert wrong.status_code == 401 and wrong.json()["detail"]["code"] == "wrong_password"
    assert client.request("DELETE", address, json={"password": PASSWORD}, headers=UI).status_code == 204
    assert {row["name"]: row for row in client.get("/api/accounts").json()}["anna"]["providers"] == []
    # Never unseen: anna's own page says so with the provider's name, until she says she saw it.
    assert member.get("/api/auth/me").json()["oidc_unlinked"] == ["SSO"]
    assert member.delete("/api/auth/oidc-notice", headers=UI).status_code == 204
    assert member.get("/api/auth/me").json()["oidc_unlinked"] == []
    gone = client.request("DELETE", address, json={"password": PASSWORD}, headers=UI)
    assert gone.status_code == 404


def test_a_member_cannot_unlink_others(client: TestClient, operator: dict, provider: FakeProvider) -> None:
    entry = add_provider(client, provider)
    make_account("anna")
    anna_id = make_account("ben")
    member = browser_of("anna")
    refused = member.request(
        "DELETE", f"/api/oidc/admin/accounts/{anna_id}/links/{entry['id']}", json={"password": PASSWORD}, headers=UI
    )
    assert refused.status_code == 403


def test_the_operator_cannot_take_the_last_link_of_an_account_without_a_password(
    client: TestClient, operator: dict, provider: FakeProvider
) -> None:
    entry = add_provider(client, provider, auto_create=True)
    sign_in_via_oidc(fresh_browser(), provider, sub="clara-1", preferred_username="clara")
    clara = row_of("clara")
    assert clara.sign_in == SIGN_IN_OIDC
    refused = client.request(
        "DELETE", f"/api/oidc/admin/accounts/{clara.id}/links/{entry['id']}", json={"password": PASSWORD}, headers=UI
    )
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "oidc_only_account"


def test_an_account_without_a_password_cannot_start_a_link_and_has_no_vault_yet(
    client: TestClient, operator: dict, provider: FakeProvider
) -> None:
    add_provider(client, provider, auto_create=True)
    browser = fresh_browser()
    sign_in_via_oidc(browser, provider, sub="clara-1", preferred_username="clara")
    refused = browser.post("/api/oidc/sso/link", json={"password": "anything at all"}, headers=UI)
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "oidc_only_account"
    me = browser.get("/api/auth/me").json()
    assert (me["sign_in"], me["role"], me["vault"]) == ("oidc", "member", "unset")


def test_my_providers_say_which_are_linked(client: TestClient, operator: dict, provider: FakeProvider) -> None:
    add_provider(client, provider, slug="sso", label="SSO")
    add_provider(client, provider, slug="work", label="Work")
    make_account("anna")
    member = browser_of("anna")
    link(member, provider, PASSWORD, sub="anna-1", slug="work")
    assert member.get("/api/oidc/me").json() == [
        {"slug": "sso", "label": "SSO", "linked": False},
        {"slug": "work", "label": "Work", "linked": True},
    ]


# --- The second factor after a provider that is not trusted with it ------------------------------------------------


def test_the_code_step_of_the_sign_in_page_finishes_a_sign_in_through_an_untrusted_provider(
    client: TestClient, operator: dict, provider: FakeProvider
) -> None:
    add_provider(client, provider, trusts_second_factor=False)
    seed = totp.generate_seed()
    with SessionLocal() as db:
        db.add(
            Account(name="anna", role="member", password_hash=hash_password(PASSWORD), totp_secret_enc=totp.seal_seed(seed))
        )
        db.commit()
    member = fresh_browser()
    member.post("/api/auth/login", json={"name": "anna", "password": PASSWORD})
    member.post("/api/auth/login/totp", json={"code": totp.code_at(seed, time.time())})
    assert location(link(member, provider, PASSWORD, sub="anna-1")) == "/account?linked=sso"
    browser = fresh_browser()
    assert browser.get("/api/auth/login/pending").status_code == 401
    back = sign_in_via_oidc(browser, provider, sub="anna-1", next_page="/vault")
    assert location(back) == "/login?step=code" and browser.get("/api/auth/me").status_code == 401
    assert browser.cookies.get(PENDING_COOKIE)
    assert browser.get("/api/auth/login/pending").json() == {"name": "anna", "methods": ["totp"]}
    assert browser.post("/api/auth/login/totp", json={"code": "000000"}).status_code == 401
    done = browser.post("/api/auth/login/totp", json={"code": totp.code_at(seed, time.time() + 30)})
    assert done.status_code == 200 and browser.get("/api/auth/me").json()["name"] == "anna"
    # The vault does not open: nothing on this way knows the password.
    assert browser.get("/api/auth/me").json()["vault"] != "open"


def test_a_trusted_provider_lets_an_account_with_a_second_factor_in_without_the_code(
    client: TestClient, operator: dict, provider: FakeProvider
) -> None:
    add_provider(client, provider)
    seed = totp.generate_seed()
    with SessionLocal() as db:
        db.add(
            Account(name="anna", role="member", password_hash=hash_password(PASSWORD), totp_secret_enc=totp.seal_seed(seed))
        )
        db.commit()
    member = fresh_browser()
    member.post("/api/auth/login", json={"name": "anna", "password": PASSWORD})
    member.post("/api/auth/login/totp", json={"code": totp.code_at(seed, time.time())})
    link(member, provider, PASSWORD, sub="anna-1")
    browser = fresh_browser()
    assert location(sign_in_via_oidc(browser, provider, sub="anna-1")) == "/"
    assert browser.get("/api/auth/me").json()["name"] == "anna"


# --- Invitations --------------------------------------------------------------------------------------------------


def _invite(role: str, *, by: str = "admin", ends: bool = False) -> str:
    token = f"inv{time.time_ns()}"
    with SessionLocal() as db:
        inviter = accounts.by_name(db, by)
        assert inviter is not None
        db.add(
            Invite(
                token_hash=hash_token(token),
                role=role,
                created_by=inviter.id,
                expires_at=utcnow() + timedelta(days=7),
                account_expires_at=utcnow() + timedelta(days=3) if ends else None,
            )
        )
        db.commit()
    return token


def test_an_invitation_through_the_provider_brings_its_role_and_its_end(
    client: TestClient, operator: dict, provider: FakeProvider
) -> None:
    add_provider(client, provider)
    token = _invite(MEMBER, ends=True)
    browser = fresh_browser()
    back = sign_in_via_oidc(browser, provider, invite=token, sub="dora-1", preferred_username="dora@example.com")
    assert location(back) == "/" and browser.get("/api/auth/me").json()["name"] == "dora"
    dora = row_of("dora")
    assert dora.role == MEMBER and dora.expires_at is not None and dora.sign_in == SIGN_IN_OIDC
    # Used up: the next one with the same link does not even get to the provider, nor the password way.
    assert error_in(fresh_browser().get("/api/oidc/sso/start", params={"invite": token})) == "invite_invalid"
    assert fresh_browser().get(f"/api/invites/{token}").status_code == 404
    operator_token = _invite(OPERATOR, ends=True)
    sign_in_via_oidc(fresh_browser(), provider, invite=operator_token, sub="olga-1", preferred_username="olga")
    assert (row_of("olga").role, row_of("olga").expires_at) == (OPERATOR, None)


def test_an_invitation_with_a_known_identity_adds_and_never_takes_away(
    client: TestClient, operator: dict, provider: FakeProvider
) -> None:
    add_provider(client, provider)
    make_account("anna")
    link(browser_of("anna"), provider, PASSWORD, sub="anna-1")
    # A guest invitation for an account without an end: it stays without one and a member.
    token = _invite(MEMBER, ends=True)
    assert location(sign_in_via_oidc(fresh_browser(), provider, invite=token, sub="anna-1")) == "/"
    assert (row_of("anna").role, row_of("anna").expires_at) == (MEMBER, None)
    assert fresh_browser().get(f"/api/invites/{token}").status_code == 404
    # A guest whose end is near gets the later one, an operator invitation makes an operator without end.
    with SessionLocal() as db:
        row = accounts.by_name(db, "anna")
        assert row is not None
        row.expires_at = utcnow() + timedelta(hours=1)
        db.commit()
    sign_in_via_oidc(fresh_browser(), provider, invite=_invite(MEMBER, ends=True), sub="anna-1")
    assert row_of("anna").expires_at > utcnow() + timedelta(days=2)
    # An end later than the invitation's stays.
    with SessionLocal() as db:
        row = accounts.by_name(db, "anna")
        assert row is not None
        row.expires_at = utcnow() + timedelta(days=10)
        db.commit()
    sign_in_via_oidc(fresh_browser(), provider, invite=_invite(MEMBER, ends=True), sub="anna-1")
    assert row_of("anna").expires_at > utcnow() + timedelta(days=9)
    sign_in_via_oidc(fresh_browser(), provider, invite=_invite(OPERATOR), sub="anna-1")
    assert (row_of("anna").role, row_of("anna").expires_at) == (OPERATOR, None)


def test_an_invitation_of_somebody_who_is_no_operator_any_more_invites_nobody(
    client: TestClient, operator: dict, provider: FakeProvider
) -> None:
    add_provider(client, provider)
    make_account("helper", OPERATOR)
    token = _invite(MEMBER, by="helper")
    with SessionLocal() as db:
        row = accounts.by_name(db, "helper")
        assert row is not None
        row.role = MEMBER
        db.commit()
    assert error_in(fresh_browser().get("/api/oidc/sso/start", params={"invite": token})) == "invite_invalid"
    answer = fresh_browser().post(f"/api/invites/{token}", json={"name": "eve", "password": PASSWORD})
    assert answer.status_code == 404 and answer.json()["detail"]["code"] == "invite_invalid"


def test_the_same_invitation_with_a_password_is_used_up_once(client: TestClient, operator: dict) -> None:
    token = _invite(MEMBER)
    first = fresh_browser().post(f"/api/invites/{token}", json={"name": "eve", "password": PASSWORD}, headers=UI)
    assert first.status_code == 200
    again = fresh_browser().post(f"/api/invites/{token}", json={"name": "eva", "password": PASSWORD}, headers=UI)
    assert again.status_code == 404 and again.json()["detail"]["code"] == "invite_invalid"


def test_a_new_account_through_a_provider_gets_a_clean_unique_name(
    client: TestClient, operator: dict, provider: FakeProvider
) -> None:
    add_provider(client, provider, auto_create=True)
    make_account("max", OPERATOR)
    sign_in_via_oidc(fresh_browser(), provider, sub="m-1", preferred_username="Max@example.com")
    sign_in_via_oidc(fresh_browser(), provider, sub="m-2", preferred_username="", name="", email="")
    with SessionLocal() as db:
        names = set(db.scalars(select(Account.name)))
    assert {"max-2", "user"} <= names


@pytest.mark.parametrize("raw", ["a" * 80 + "-2", "Ä Ö", "x", "--", "max.mustermann"])
def test_names_from_a_provider_follow_the_rules_of_nextrmnl(raw: str) -> None:
    cleaned = oidc_store.clean_name(raw)
    assert cleaned == "" or accounts.NAME_PATTERN.match(cleaned)
    assert len(cleaned) <= 64
    if raw.endswith("-2"):
        assert cleaned.endswith("-2")


# --- The address ---------------------------------------------------------------------------------------------------


def test_only_an_account_through_providers_alone_follows_their_address(
    client: TestClient, operator: dict, provider: FakeProvider
) -> None:
    add_provider(client, provider, auto_create=True)
    sign_in_via_oidc(fresh_browser(), provider, sub="clara-1", preferred_username="clara", email="c@example.com")
    assert row_of("clara").email == "c@example.com"
    sign_in_via_oidc(fresh_browser(), provider, sub="clara-1", email="c2@example.com")
    assert row_of("clara").email == "c2@example.com"
    # An address another account has is never taken.
    sign_in_via_oidc(fresh_browser(), provider, sub="dan-1", preferred_username="dan", email="C2@example.com")
    assert row_of("dan").email == ""
    # An account with a password is not given one.
    make_account("anna")
    link(browser_of("anna"), provider, PASSWORD, sub="anna-1", email="anna@example.com")
    assert row_of("anna").email == ""


def test_the_last_link_takes_the_address_along_another_one_keeps_it(
    client: TestClient, operator: dict, provider: FakeProvider
) -> None:
    add_provider(client, provider, slug="sso")
    add_provider(client, provider, slug="work", label="Work")
    make_account("anna")
    member = browser_of("anna")
    link(member, provider, PASSWORD, sub="anna-1", slug="sso")
    link(member, provider, PASSWORD, sub="anna-1", slug="work")
    with SessionLocal() as db:
        row = accounts.by_name(db, "anna")
        assert row is not None
        # As 0.4 left it: an address from the provider on an account with a password.
        row.email = "anna@example.com"
        db.commit()
    assert member.delete("/api/oidc/sso/link", headers=UI).status_code == 204
    assert row_of("anna").email == "anna@example.com"
    assert member.delete("/api/oidc/work/link", headers=UI).status_code == 204
    assert row_of("anna").email == ""


# --- The direct link of 0.4.1 ---------------------------------------------------------------------------------------


@pytest.fixture
def anna_linked(client: TestClient, operator: dict, provider: FakeProvider) -> FakeProvider:
    add_provider(client, provider)
    make_account("anna")
    link(browser_of("anna"), provider, PASSWORD, sub="anna-1")
    provider.person = {"sub": "anna-1"}
    return provider


@pytest.mark.parametrize("own", ["/", "/sessions", "/vault", "/settings", "/about", "/connect/12", "/connect/9999999999"])
def test_every_own_page_is_a_target(anna_linked: FakeProvider, own: str) -> None:
    browser = fresh_browser()
    assert location(sign_in_via_oidc(browser, anna_linked, next_page=own)) == own
    assert browser.get("/api/auth/me").json()["name"] == "anna"


@pytest.mark.parametrize(
    "odd",
    [
        "//evil.example.com",
        "https://evil.example.com/",
        "/connect/0",
        "/connect/12345678901",
        "/connect/12/../../x",
        "/settings?tab=account",
        "/settings/",
        "\\\\evil.example.com",
        "/%2F%2Fevil.example.com",
        "",
    ],
)
def test_a_foreign_or_odd_target_ends_on_the_start_page(anna_linked: FakeProvider, odd: str) -> None:
    assert location(sign_in_via_oidc(fresh_browser(), anna_linked, next_page=odd)) == "/"


def test_the_target_is_checked_again_on_the_way_back(anna_linked: FakeProvider) -> None:
    from app.routers import oidc as oidc_router

    browser = fresh_browser()
    target = location(browser.get("/api/oidc/sso/start", params={"next": "/vault"}))
    # A forged cookie: a page with a signature that is not this server's, or signed but with a foreign page in it.
    body = oidc_router._pack_next("/vault").rsplit(".", 1)[0]
    browser.cookies.set(NEXT_COOKIE, body + "." + "0" * 40, path="/api/oidc")
    from tests.oidc_helpers import come_back

    assert location(come_back(browser, anna_linked, target)) == "/"
    browser = fresh_browser()
    target = location(browser.get("/api/oidc/sso/start", params={"next": "/vault"}))
    original = oidc_router.safe_next
    try:
        oidc_router.safe_next = lambda value: value  # type: ignore[assignment]
        forged = oidc_router._pack_next("//evil.example.com")
    finally:
        oidc_router.safe_next = original  # type: ignore[assignment]
    browser.cookies.set(NEXT_COOKIE, forged, path="/api/oidc")
    assert location(come_back(browser, anna_linked, target)) == "/"


def test_a_failed_sign_in_with_a_target_still_ends_on_the_sign_in_page(
    client: TestClient, operator: dict, provider: FakeProvider
) -> None:
    add_provider(client, provider)
    back = sign_in_via_oidc(fresh_browser(), provider, next_page="/connect/12", sub="nobody-1")
    assert location(back).startswith("/login?") and error_in(back) == "oidc_no_account"


def test_a_target_runs_out_with_the_attempt(anna_linked: FakeProvider, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.routers import oidc as oidc_router
    from tests.oidc_helpers import come_back

    monkeypatch.setattr(oidc_router, "NEXT_MINUTES", -1)
    browser = fresh_browser()
    target = location(browser.get("/api/oidc/sso/start", params={"next": "/vault"}))
    browser.cookies.set(NEXT_COOKIE, oidc_router._pack_next("/vault"), path="/api/oidc")
    assert location(come_back(browser, anna_linked, target)) == "/"


# --- The log --------------------------------------------------------------------------------------------------------


def test_no_secret_and_no_invitation_leaves_a_trace_in_the_log(
    client: TestClient, operator: dict, provider: FakeProvider, caplog: pytest.LogCaptureFixture
) -> None:
    token = _invite(MEMBER)
    with caplog.at_level(logging.DEBUG):
        add_provider(client, provider, auto_create=True)
        browser = fresh_browser()
        target = location(browser.get("/api/oidc/sso/start", params={"invite": token}))
        params = provider.authorize(target)
        browser.get("/api/oidc/sso/callback", params=params)
    session = browser.cookies.get("nextrmnl_session")
    assert session
    for secret in (provider.client_secret, params["state"], params["code"], session, token):
        assert secret not in caplog.text
    assert "alex@example.com" not in caplog.text


# --- The button after the migration ---------------------------------------------------------------------------------


def test_the_button_pressed_again_after_the_migration_keeps_the_old_address_the_entry_and_its_links(
    client: TestClient, operator: dict
) -> None:
    """Blueprint 01 "Umstieg" 3: the entry ``oidc`` keeps ``/api/oidc/callback`` when the operator presses the
    authentik button again; the provider is its own (found by its client id), so no link is dropped."""
    from app.vendor import nexoidc as module
    from tests.oidc_helpers import fakes

    network = fakes.Network()
    ak = network.add(fakes.FakeAuthentik())
    made = ak.add_provider("nextrmnl", "http://testserver/api/oidc/callback", slug="nextrmnl")
    issuer = f"{ak.base}/application/o/nextrmnl/"
    signer = ak.sign_in_with(issuer, made["client_id"], made["client_secret"])
    module.use_transport(network.transport())
    try:
        make_account("anna")
        _legacy(signer, label="authentik", issuer=issuer, subjects={"anna": "anna-1"})
        oidc_store.migrate_settings(backup=False)
        result = client.post("/api/oidc/authentik/setup", json={"url": ak.base, "token": ak.token}, headers=UI).json()
        assert result["ok"] and result["links_dropped"] == 0, result
        [row] = _entries()
        assert (row.slug, row.managed, row.redirect_path) == ("oidc", "authentik", "")
        assert len(ak.providers) == 1
        assert ak.providers[0]["redirect_uris"][0]["url"] == "http://testserver/api/oidc/callback"
        signer.person = {"sub": "anna-1"}
        browser = fresh_browser()
        target = location(browser.get("/api/oidc/oidc/start"))
        back = browser.get("/api/oidc/callback", params=signer.authorize(target))
        assert location(back) == "/" and browser.get("/api/auth/me").json()["name"] == "anna"
    finally:
        module.use_transport(None)


# --- The way back to 0.4 (blueprint 01 "Umstieg" 4) ----------------------------------------------------------------


def _old() -> dict[str, object]:
    with SessionLocal() as db:
        values = settings_service.get_all(db)
        subjects = {row.name: row.oidc_subject for row in db.scalars(select(Account))}
        secret = crypto.decrypt_secret(str(values["oidc_client_secret_enc"] or ""))
    return {
        "issuer": values["oidc_issuer"],
        "client": values["oidc_client_id"],
        "secret": secret,
        "name": values["oidc_provider_name"],
        "auto": values["oidc_auto_create"],
        "subjects": subjects,
    }


def test_the_old_settings_and_subjects_always_describe_the_entry_oidc(
    client: TestClient, operator: dict, provider: FakeProvider
) -> None:
    make_account("anna")
    make_account("ben")
    _legacy(provider, label="Company", subjects={"anna": "anna-1", "ben": "ben-1"})
    entry = oidc_store.migrate_settings(backup=False)
    assert entry is not None
    old = _old()
    assert (old["client"], old["secret"], old["name"], old["auto"]) == (
        provider.client_id,
        provider.client_secret,
        "Company",
        False,
    )
    assert old["subjects"] == {"admin": "", "anna": "anna-1", "ben": "ben-1"}
    # Unlinked by itself, by the operator, linked anew: the old column says the same as the links.
    anna = browser_of("anna")
    assert anna.delete("/api/oidc/oidc/link", headers=UI).status_code == 204
    ben_id = row_of("ben").id
    unlinked = client.request(
        "DELETE", f"/api/oidc/admin/accounts/{ben_id}/links/{entry.id}", json={"password": PASSWORD}, headers=UI
    )
    assert unlinked.status_code == 204
    assert _old()["subjects"] == {"admin": "", "anna": "", "ben": ""}
    assert location(link(anna, provider, PASSWORD, slug="oidc", sub="anna-9")) == "/account?linked=oidc"
    assert _old()["subjects"]["anna"] == "anna-9"
    # Switched off, the old version finds no client; on with new people allowed, it lets them in as before.
    view = client.get("/api/oidc/admin/providers").json()[0]
    body = {key: view[key] for key in ("label", "issuer", "client_id", "scopes", "trusts_second_factor")}
    off = client.put(f"/api/oidc/admin/providers/{entry.id}", json={**body, "enabled": False, "auto_create": True}, headers=UI)
    assert off.status_code == 200
    old = _old()
    assert (old["client"], old["secret"], old["auto"]) == ("", "", False)
    on = client.put(f"/api/oidc/admin/providers/{entry.id}", json={**body, "enabled": True, "auto_create": True}, headers=UI)
    assert on.status_code == 200
    old = _old()
    assert (old["client"], old["secret"], old["auto"]) == (provider.client_id, provider.client_secret, True)
    assert old["subjects"]["anna"] == "anna-9"


def test_another_issuer_or_removing_the_entry_oidc_empties_the_old_settings_for_good(
    client: TestClient, operator: dict, provider: FakeProvider
) -> None:
    from tests.oidc_helpers import fakes

    make_account("anna")
    _legacy(provider, label="Company", subjects={"anna": "anna-1"})
    entry = oidc_store.migrate_settings(backup=False)
    assert entry is not None
    moved = provider.network.add(  # type: ignore[attr-defined]
        fakes.FakeProvider("https://moved.example.com", client_id=provider.client_id, client_secret=provider.client_secret)
    )
    view = client.get("/api/oidc/admin/providers").json()[0]
    body = {key: view[key] for key in ("label", "client_id", "scopes", "enabled", "auto_create", "trusts_second_factor")}
    assert client.put(f"/api/oidc/admin/providers/{entry.id}", json={**body, "issuer": moved.issuer}, headers=UI).status_code == 200
    old = _old()
    assert (old["issuer"], old["client"], old["secret"], old["subjects"]["anna"]) == ("", "", "", "")
    # Back to the first issuer: the old settings stay empty, a provider gone once does not come back that way.
    assert client.put(f"/api/oidc/admin/providers/{entry.id}", json={**body, "issuer": provider.issuer}, headers=UI).status_code == 200
    assert _old()["issuer"] == ""
    make_account("ben")
    _legacy(provider, label="Company", subjects={"ben": "ben-1"})
    with SessionLocal() as db:
        db.query(OidcProvider).delete()
        db.commit()
    entry = oidc_store.migrate_settings(backup=False)
    assert entry is not None and _old()["subjects"]["ben"] == "ben-1"
    assert client.delete(f"/api/oidc/admin/providers/{entry.id}", headers=UI).status_code == 200
    old = _old()
    assert (old["issuer"], old["client"], old["subjects"]["ben"]) == ("", "", "")

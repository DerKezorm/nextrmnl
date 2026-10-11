"""The vault is open per browser session, not per account.

A session that gave the password has the vault open; another browser of the same account, above all one that came
through a sign-in provider and never saw the password, has it locked until it gives the password itself.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import SIGN_IN_OIDC, Account, AuthSession, utcnow
from app.security import end_session, hash_token, purge_sessions, start_session
from app.services import vault
from tests.conftest import PASSWORD, UI, invite_member, session_token, sign_in
from tests.helpers_sshd import Sshd, connect_ws, open_session, receive_json, running_sshd
from tests.oidc_helpers import FakeProvider, add_provider, fresh_browser, link, sign_in_via_oidc


def open_slots(account_id: int) -> int:
    """How many browser sessions hold the account's vault key in memory."""
    with vault._lock:
        return sum(1 for opened in vault._open.values() if opened.account_id == account_id)


def vault_state(browser: TestClient) -> str:
    return browser.get("/api/vault").json()["state"]


def second_browser(name: str = "admin") -> TestClient:
    browser = TestClient(app, base_url="http://testserver")
    sign_in(browser, name)
    return browser


def through_the_provider(client: TestClient, provider: FakeProvider) -> TestClient:
    """The operator links its account to the provider, then signs in a fresh browser there: no password on the way."""
    add_provider(client, provider)
    link(client, provider, PASSWORD, sub="admin-1")
    browser = fresh_browser()
    sign_in_via_oidc(browser, provider, sub="admin-1")
    assert browser.get("/api/auth/me").json()["name"] == "admin"
    return browser


def test_a_session_through_a_provider_does_not_get_the_vault_another_session_opened(
    client: TestClient, operator: dict, provider: FakeProvider
) -> None:
    connection = client.post(
        "/api/connections", json={"name": "switch", "host": "192.0.2.2", "user": "admin", "auth": "password"}, headers=UI
    ).json()["id"]
    assert vault_state(client) == "open"
    browser = through_the_provider(client, provider)

    assert vault_state(client) == "open"
    assert vault_state(browser) == "locked"
    assert browser.get("/api/auth/me").json()["vault"] == "locked"
    for answer in (
        browser.post("/api/vault/keys/generate", json={"name": "sneaky"}, headers=UI),
        browser.put(f"/api/vault/passwords/{connection}", json={"password": "pw"}, headers=UI),
        browser.post("/api/vault/file/export", json={"password": PASSWORD}, headers=UI),
        browser.post(
            "/api/vault/file/import",
            files={"file": ("tresor.nextrmnl-vault", b"{}")},
            data={"password": PASSWORD},
            headers=UI,
        ),
    ):
        assert answer.status_code == 423 and answer.json()["detail"]["code"] == "vault_locked", answer.text

    # With the password it opens there too, and each session locks on its own.
    assert browser.post("/api/vault/unlock", json={"password": PASSWORD}, headers=UI).json() == {"state": "open"}
    assert open_slots(operator["id"]) == 2
    assert client.post("/api/vault/lock", headers=UI).json() == {"state": "locked"}
    assert (vault_state(client), vault_state(browser)) == ("locked", "open")


def test_two_password_sessions_open_and_close_the_vault_each_for_itself(client: TestClient, operator: dict) -> None:
    other = second_browser()
    assert (vault_state(client), vault_state(other)) == ("open", "open")
    assert other.post("/api/vault/lock", headers=UI).status_code == 200
    assert (vault_state(client), vault_state(other)) == ("open", "locked")
    # Signing out in one browser locks only that browser's vault.
    assert other.post("/api/vault/unlock", json={"password": PASSWORD}, headers=UI).status_code == 200
    assert client.post("/api/auth/logout", headers=UI).status_code == 204
    assert vault_state(other) == "open"
    assert open_slots(operator["id"]) == 1


def test_a_wrong_password_in_one_session_leaves_the_others_alone(client: TestClient, operator: dict) -> None:
    other = second_browser()
    other.post("/api/vault/lock", headers=UI)
    wrong = other.post("/api/vault/unlock", json={"password": "not-the-password"}, headers=UI)
    assert wrong.status_code == 401
    assert (vault_state(client), vault_state(other)) == ("open", "locked")


def test_a_new_password_keeps_the_vault_open_only_where_it_was_given(client: TestClient, operator: dict) -> None:
    other = second_browser()
    assert open_slots(operator["id"]) == 2
    changed = client.put("/api/auth/password", json={"current": PASSWORD, "new": PASSWORD + "-new"}, headers=UI)
    assert changed.status_code == 204
    assert vault_state(client) == "open"
    assert other.get("/api/auth/me").status_code == 401
    assert open_slots(operator["id"]) == 1


def test_an_ended_session_takes_its_key_out_of_memory(client: TestClient, operator: dict) -> None:
    # Signed out by the account itself from elsewhere.
    other = second_browser()
    assert client.post("/api/auth/logout-all", headers=UI).status_code == 204
    assert other.get("/api/auth/me").status_code == 401
    assert open_slots(operator["id"]) == 1

    # Run out, noticed on the next request.
    other = second_browser()
    _expire(session_token(other))
    assert other.get("/api/auth/me").status_code == 401
    assert open_slots(operator["id"]) == 1

    # Run out, cleared by the housekeeping without any request.
    other = second_browser()
    _expire(session_token(other))
    with SessionLocal() as db:
        assert purge_sessions(db) == 1
    assert open_slots(operator["id"]) == 1
    assert vault_state(client) == "open"


def _expire(token: str) -> None:
    with SessionLocal() as db:
        row = db.query(AuthSession).filter(AuthSession.token_hash == hash_token(token)).one()
        row.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()


def test_the_operator_signing_an_account_out_locks_every_session_of_it(client: TestClient, operator: dict) -> None:
    created = client.post("/api/accounts/invites", json={"name": "alex", "role": "member"}, headers=UI).json()
    token = created["link"].rsplit("/", 1)[-1]
    first = TestClient(app, base_url="http://testserver")
    member = first.post(f"/api/invites/{token}", json={"name": "alex", "password": PASSWORD}, headers=UI).json()
    second = second_browser("alex")
    assert (vault_state(first), vault_state(second)) == ("open", "open")
    assert client.post(f"/api/accounts/{member['id']}/sign-out", headers=UI).status_code == 204
    assert open_slots(member["id"]) == 0
    assert vault_state(client) == "open"


def test_a_key_is_never_handed_to_another_account_holding_the_same_slot(client: TestClient, operator: dict) -> None:
    # The slot is the session's; a session of a different account must not read it even if it were handed the
    # token hash some day.
    with pytest.raises(vault.VaultLocked):
        vault.key_for(operator["id"] + 1, session_token(client))
    assert vault.key_for(operator["id"], session_token(client))
    with pytest.raises(vault.VaultLocked):
        vault.key_for(operator["id"], None)


@pytest.fixture(name="sshd")
def _sshd(tmp_path: Path) -> Any:
    with running_sshd(tmp_path / "sftp-root") as server:
        yield server


def test_a_terminal_reads_the_vault_of_the_session_that_starts_it(
    client: TestClient, operator: dict, provider: FakeProvider, sshd: Sshd
) -> None:
    key = client.post("/api/vault/keys/generate", json={"name": "homelab", "key_type": "ed25519"}, headers=UI).json()
    sshd.authorize(key["public_key"])
    payload = {"name": "box", "host": "127.0.0.1", "port": sshd.port, "user": "tester", "auth": "key"}
    cid = client.post("/api/connections", json={**payload, "key_id": key["id"]}, headers=UI).json()["id"]
    browser = through_the_provider(client, provider)

    with connect_ws(browser, cid) as ws:
        assert receive_json(ws)["state"] == "connecting"
        failed = receive_json(ws)
        assert failed["state"] == "failed" and failed["detail"] == "vault_locked"
    assert sshd.password_tries == 0

    # The session that gave the password connects with the same key.
    with connect_ws(client, cid) as ws:
        opened = open_session(ws)
        assert opened["state"] == "open" and opened["via"] == "key homelab", opened
        ws.send_bytes(b"exit\n")
        receive_json(ws)


def test_an_unlock_that_outlives_its_session_leaves_no_key_behind(client: TestClient, operator: dict) -> None:
    # The unwrapping takes a while; meanwhile the session is signed out elsewhere and its end finds nothing to lock.
    token = session_token(client)
    client.post("/api/vault/lock", headers=UI)
    with SessionLocal() as db:
        account = db.get(Account, operator["id"])
        key = vault.unwrap_key(account, PASSWORD)
        end_session(db, token)
        vault.open_with_key(account, token, key, 30)
    assert open_slots(operator["id"]) == 0


def test_a_guest_account_running_out_locks_the_vault_in_every_browser(client: TestClient, operator: dict) -> None:
    member = invite_member(client, "alex")
    member_id = member.get("/api/auth/me").json()["id"]
    second = second_browser("alex")
    assert open_slots(member_id) == 2
    with SessionLocal() as db:
        db.get(Account, member_id).expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert second.get("/api/auth/me").status_code == 401
    assert open_slots(member_id) == 0


def provider_account_browsers(count: int) -> tuple[int, list[TestClient]]:
    """An account that signs in through a provider only, signed in in ``count`` browsers."""
    browsers = []
    with SessionLocal() as db:
        account = Account(name="dana", role="member", sign_in=SIGN_IN_OIDC)
        db.add(account)
        db.commit()
        for _ in range(count):
            browser = TestClient(app, base_url="http://testserver")
            browser.cookies.set("nextrmnl_session", start_session(db, account, "192.0.2.5", "test"))
            browsers.append(browser)
        return account.id, browsers


def test_a_provider_account_changing_its_vault_password_locks_its_other_browsers(client: TestClient) -> None:
    account_id, (first, second) = provider_account_browsers(2)
    assert first.post("/api/vault/setup", json={"password": PASSWORD}, headers=UI).json() == {"state": "open"}
    assert vault_state(second) == "locked"
    assert second.post("/api/vault/unlock", json={"password": PASSWORD}, headers=UI).status_code == 200
    changed = first.put("/api/vault/password", json={"current": PASSWORD, "new": PASSWORD + "-new"}, headers=UI)
    assert changed.status_code == 204
    assert (vault_state(first), vault_state(second)) == ("open", "locked")
    assert open_slots(account_id) == 1


def test_a_vault_reset_locks_the_old_key_out_of_every_browser(client: TestClient) -> None:
    account_id, (first, second) = provider_account_browsers(2)
    first.post("/api/vault/setup", json={"password": PASSWORD}, headers=UI)
    second.post("/api/vault/unlock", json={"password": PASSWORD}, headers=UI)
    old_key = vault.key_for(account_id, session_token(second))
    assert first.post("/api/vault/reset", json={"password": PASSWORD + "-fresh"}, headers=UI).json() == {"state": "open"}
    assert (vault_state(first), vault_state(second)) == ("open", "locked")
    # The other browser cannot go on writing with the old key; it opens the fresh vault with the fresh password.
    assert second.post("/api/vault/keys/generate", json={"name": "late"}, headers=UI).status_code == 423
    assert second.post("/api/vault/unlock", json={"password": PASSWORD + "-fresh"}, headers=UI).status_code == 200
    assert vault.key_for(account_id, session_token(second)) != old_key

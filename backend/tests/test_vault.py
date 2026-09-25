import asyncssh
from fastapi.testclient import TestClient

from app import crypto
from app.db import SessionLocal
from app.services import vault
from tests.conftest import UI, invite_member


def test_crypto_wrap_and_entries() -> None:
    salt, wrapped, key = crypto.new_vault("pw-one-two-three")
    assert crypto.unwrap("pw-one-two-three", salt, wrapped) == key
    try:
        crypto.unwrap("pw-one-two-four", salt, wrapped)
    except crypto.WrongPassword:
        pass
    else:
        raise AssertionError("wrong password accepted")
    sealed = crypto.encrypt_entry(key, b"secret")
    assert sealed != b"secret"
    assert crypto.decrypt_entry(key, sealed) == b"secret"
    # Two encryptions of the same text differ (fresh nonce).
    assert crypto.encrypt_entry(key, b"secret") != sealed


def test_server_secret_round_trip() -> None:
    stored = crypto.encrypt_secret("client-secret")
    assert stored and stored != "client-secret"
    assert crypto.decrypt_secret(stored) == "client-secret"
    assert crypto.decrypt_secret("") == ""
    assert crypto.decrypt_secret("not-hex") == ""


def test_generate_import_and_delete_keys(client: TestClient, operator: dict) -> None:
    generated = client.post("/api/vault/keys/generate", json={"name": "homelab", "key_type": "ed25519"}, headers=UI)
    assert generated.status_code == 201, generated.text
    body = generated.json()
    assert body["key_type"] == "ed25519" and body["public_key"].startswith("ssh-ed25519 ")
    assert body["fingerprint"].startswith("SHA256:")
    assert "private" not in " ".join(body.keys())
    # Same name twice is refused.
    assert client.post("/api/vault/keys/generate", json={"name": "homelab"}, headers=UI).status_code == 409

    private = asyncssh.generate_private_key("ssh-ed25519").export_private_key("openssh").decode()
    imported = client.post("/api/vault/keys/import", json={"name": "laptop", "private_key": private}, headers=UI)
    assert imported.status_code == 201, imported.text
    assert imported.json()["has_passphrase"] is False

    protected = asyncssh.generate_private_key("ssh-ed25519").export_private_key("openssh", "pass").decode()
    without = client.post("/api/vault/keys/import", json={"name": "prot", "private_key": protected}, headers=UI)
    assert without.status_code == 422 and without.json()["detail"]["code"] == "passphrase_needed"
    with_pass = client.post(
        "/api/vault/keys/import", json={"name": "prot", "private_key": protected, "passphrase": "pass"}, headers=UI
    )
    assert with_pass.status_code == 201 and with_pass.json()["has_passphrase"] is True

    garbage = client.post("/api/vault/keys/import", json={"name": "bad", "private_key": "hello"}, headers=UI)
    assert garbage.status_code == 422

    names = [key["name"] for key in client.get("/api/vault/keys").json()]
    assert names == ["homelab", "laptop", "prot"]
    assert client.delete(f"/api/vault/keys/{body['id']}", headers=UI).status_code == 204
    assert [key["name"] for key in client.get("/api/vault/keys").json()] == ["laptop", "prot"]


def test_locked_vault_refuses_writes_but_lists(client: TestClient, operator: dict) -> None:
    client.post("/api/vault/keys/generate", json={"name": "homelab"}, headers=UI)
    client.post("/api/vault/lock", headers=UI)
    assert client.get("/api/vault/keys").status_code == 200
    refused = client.post("/api/vault/keys/generate", json={"name": "two"}, headers=UI)
    assert refused.status_code == 423 and refused.json()["detail"]["code"] == "vault_locked"


def test_vault_is_per_account(client: TestClient, operator: dict) -> None:
    client.post("/api/vault/keys/generate", json={"name": "homelab"}, headers=UI)
    member = invite_member(client, "alex")
    assert member.get("/api/vault/keys").json() == []
    key_id = client.get("/api/vault/keys").json()[0]["id"]
    assert member.delete(f"/api/vault/keys/{key_id}", headers=UI).status_code == 404
    with SessionLocal() as db:
        try:
            vault.private_key_text(db, member.get("/api/auth/me").json()["id"], key_id)
        except LookupError:
            pass
        else:
            raise AssertionError("member could read the operator's key")


def test_stored_passwords(client: TestClient, operator: dict) -> None:
    connection = client.post(
        "/api/connections", json={"name": "switch", "host": "192.0.2.2", "user": "admin", "auth": "password"}, headers=UI
    )
    assert connection.status_code == 201, connection.text
    cid = connection.json()["id"]
    stored = client.put(f"/api/vault/passwords/{cid}", json={"password": "stored-pw-1"}, headers=UI)
    assert stored.status_code == 200 and "password" not in stored.json()
    assert client.get("/api/vault/passwords").json()[0]["connection_id"] == cid
    with SessionLocal() as db:
        assert vault.get_password(db, operator["id"], cid) == "stored-pw-1"
    assert client.delete(f"/api/vault/passwords/{cid}", headers=UI).status_code == 204
    assert client.get("/api/vault/passwords").json() == []


def test_idle_vault_locks_itself(client: TestClient, operator: dict) -> None:
    from datetime import timedelta

    from app.models import utcnow

    with vault._lock:
        opened = vault._open[operator["id"]]
        opened.until = utcnow() - timedelta(seconds=1)
    assert vault.sweep() == 1
    assert client.get("/api/vault").json()["state"] == "locked"

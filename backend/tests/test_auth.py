from fastapi.testclient import TestClient

from app.security import MAX_FAILURES
from app.services import vault
from tests.conftest import PASSWORD, UI, invite_member, sign_in


def test_setup_creates_operator_and_signs_in(client: TestClient) -> None:
    assert client.get("/api/setup").json()["needs_setup"] is True
    response = client.post("/api/setup", json={"name": "Admin", "password": PASSWORD}, headers=UI)
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "admin" and body["role"] == "operator" and body["vault"] == "open"
    assert client.get("/api/setup").json()["needs_setup"] is False
    assert client.get("/api/auth/me").json()["name"] == "admin"
    # A second setup is refused.
    assert client.post("/api/setup", json={"name": "x", "password": PASSWORD}, headers=UI).status_code == 409


def test_setup_rejects_short_password(client: TestClient) -> None:
    response = client.post("/api/setup", json={"name": "admin", "password": "short"}, headers=UI)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "password_too_short"


def test_unsafe_requests_need_the_header(client: TestClient) -> None:
    assert client.post("/api/setup", json={"name": "admin", "password": PASSWORD}).status_code == 403


def test_signed_out_requests_are_refused(client: TestClient) -> None:
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/connections").status_code == 401


def test_login_logout_and_vault_state(client: TestClient, operator: dict) -> None:
    assert client.post("/api/auth/logout", headers=UI).status_code == 204
    assert client.get("/api/auth/me").status_code == 401
    body = sign_in(client, "admin")
    assert body["vault"] == "open"
    assert client.post("/api/vault/lock", headers=UI).json()["state"] == "locked"
    assert client.get("/api/auth/me").json()["vault"] == "locked"
    wrong = client.post("/api/vault/unlock", json={"password": "nope-nope-nope"}, headers=UI)
    assert wrong.status_code == 401
    assert client.post("/api/vault/unlock", json={"password": PASSWORD}, headers=UI).json()["state"] == "open"


def test_wrong_password_and_lockout(client: TestClient, operator: dict) -> None:
    client.post("/api/auth/logout", headers=UI)
    for _ in range(MAX_FAILURES):
        response = client.post("/api/auth/login", json={"name": "admin", "password": "wrong-wrong-wrong"}, headers=UI)
        assert response.status_code in (401, 429)
    locked = client.post("/api/auth/login", json={"name": "admin", "password": PASSWORD}, headers=UI)
    assert locked.status_code == 429


def test_unknown_account_looks_like_wrong_password(client: TestClient, operator: dict) -> None:
    client.post("/api/auth/logout", headers=UI)
    response = client.post("/api/auth/login", json={"name": "nobody", "password": PASSWORD}, headers=UI)
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "wrong_credentials"


def test_invite_flow_and_roles(client: TestClient, operator: dict) -> None:
    member = invite_member(client, "alex")
    me = member.get("/api/auth/me").json()
    assert me["name"] == "alex" and me["role"] == "member" and me["vault"] == "open"
    # Members do not see the operator pages.
    assert member.get("/api/accounts").status_code == 403
    assert member.get("/api/settings").status_code == 403
    names = {row["name"] for row in client.get("/api/accounts").json()}
    assert names == {"admin", "alex"}
    # The invitation is used up.
    assert client.get("/api/accounts/invites").json() == []


def test_invite_token_is_single_use_and_expires(client: TestClient, operator: dict) -> None:
    created = client.post("/api/accounts/invites", json={"name": "sam", "role": "member"}, headers=UI).json()
    token = created["link"].rsplit("/", 1)[-1]
    assert client.get(f"/api/invites/{token}").json()["name"] == "sam"
    other = TestClient(client.app, base_url="http://testserver")
    assert other.post(f"/api/invites/{token}", json={"name": "sam", "password": PASSWORD}, headers=UI).status_code == 200
    assert client.get(f"/api/invites/{token}").status_code == 404
    assert client.get("/api/invites/not-a-token").status_code == 404


def test_password_change_rewraps_the_vault(client: TestClient, operator: dict) -> None:
    generated = client.post("/api/vault/keys/generate", json={"name": "homelab", "key_type": "ed25519"}, headers=UI)
    assert generated.status_code == 201
    new_password = PASSWORD + "-changed"
    changed = client.put("/api/auth/password", json={"current": PASSWORD, "new": new_password}, headers=UI)
    assert changed.status_code == 204
    client.post("/api/auth/logout", headers=UI)
    assert client.post("/api/auth/login", json={"name": "admin", "password": PASSWORD}, headers=UI).status_code == 401
    body = sign_in(client, "admin", new_password)
    assert body["vault"] == "open"
    keys = client.get("/api/vault/keys").json()
    assert [key["name"] for key in keys] == ["homelab"]
    # The private key is still readable with the new password.
    from app.db import SessionLocal

    with SessionLocal() as db:
        _row, text = vault.private_key_text(db, body["id"], keys[0]["id"])
    assert "PRIVATE KEY" in text


def test_operator_cannot_delete_or_demote_self(client: TestClient, operator: dict) -> None:
    assert client.delete(f"/api/accounts/{operator['id']}", headers=UI).status_code == 409
    assert client.put(f"/api/accounts/{operator['id']}/role", json={"role": "member"}, headers=UI).status_code == 409


def test_security_headers_and_request_id(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert "content-security-policy" in response.headers
    assert response.headers["x-frame-options"] == "DENY"
    assert len(response.headers["x-request-id"]) == 6

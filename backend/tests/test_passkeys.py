"""Passkeys as the second factor, against a software authenticator that signs for real (ES256): adding needs the
password and HTTPS, the sign-in step takes only the account's own key, a replay or a cloned key is refused, and
recovery codes and the operator's reset cover the lost key."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import struct

import cbor2
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import Account
from app.services import passkeys
from tests.conftest import PASSWORD, UI, invite_member

ORIGIN = "https://ssh.example.com"
#: Built at run time, so the secret scanner does not take a test value for a real password.
WRONG = PASSWORD[::-1]
RP_ID = "ssh.example.com"


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class SoftKey:
    """A security key in software: one ES256 key pair, a counter, and the two ceremonies of WebAuthn."""

    def __init__(self, origin: str = ORIGIN, rp_id: str = RP_ID) -> None:
        self.private = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = secrets.token_bytes(32)
        self.counter = 0
        self.origin, self.rp_id = origin, rp_id

    def _cose(self) -> bytes:
        numbers = self.private.public_key().public_numbers()
        return cbor2.dumps({1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, "big"), -3: numbers.y.to_bytes(32, "big")})

    def _auth_data(self, attested: bool) -> bytes:
        flags = 0x01 | 0x04 | (0x40 if attested else 0)
        data = hashlib.sha256(self.rp_id.encode()).digest() + bytes([flags]) + struct.pack(">I", self.counter)
        if attested:
            data += bytes(16) + struct.pack(">H", len(self.credential_id)) + self.credential_id + self._cose()
        return data

    def _client_data(self, kind: str, challenge: str) -> bytes:
        return json.dumps({"type": kind, "challenge": challenge, "origin": self.origin, "crossOrigin": False}).encode()

    def create(self, options: dict) -> dict:
        client_data = self._client_data("webauthn.create", options["challenge"])
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": self._auth_data(True)})
        return {
            "id": b64(self.credential_id),
            "rawId": b64(self.credential_id),
            "type": "public-key",
            "response": {"clientDataJSON": b64(client_data), "attestationObject": b64(attestation), "transports": ["usb"]},
            "clientExtensionResults": {},
        }

    def get(self, options: dict, counter: int | None = None) -> dict:
        self.counter = self.counter + 1 if counter is None else counter
        client_data = self._client_data("webauthn.get", options["challenge"])
        auth_data = self._auth_data(False)
        signature = self.private.sign(auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))
        return {
            "id": b64(self.credential_id),
            "rawId": b64(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64(client_data),
                "authenticatorData": b64(auth_data),
                "signature": b64(signature),
            },
            "clientExtensionResults": {},
        }


@pytest.fixture(autouse=True)
def _https(client: TestClient, operator: dict) -> None:
    """Passkeys need HTTPS; the public address says nextrmnl runs there."""
    assert client.put("/api/settings", json={"public_url": ORIGIN}, headers=UI).status_code == 200


def add_key(client: TestClient, key: SoftKey, name: str = "yubikey", password: str = PASSWORD):
    begun = client.post("/api/auth/passkeys/begin", headers=UI)
    assert begun.status_code == 200, begun.text
    options = json.loads(begun.json()["options"])
    return client.post(
        "/api/auth/passkeys/finish", json={"name": name, "password": password, "credential": key.create(options)}, headers=UI
    )


def password_step(name: str = "admin") -> TestClient:
    browser = TestClient(app, base_url="http://testserver")
    first = browser.post("/api/auth/login", json={"name": name, "password": PASSWORD}, headers=UI)
    assert first.status_code == 200 and first.json()["second_factor"] is True, first.text
    browser.methods = first.json()["methods"]  # type: ignore[attr-defined]
    return browser


def passkey_step(browser: TestClient, key: SoftKey, **kwargs):
    begun = browser.post("/api/auth/login/passkey/begin", headers=UI)
    assert begun.status_code == 200, begun.text
    options = json.loads(begun.json()["options"])
    return browser.post("/api/auth/login/passkey", json={"credential": key.get(options, **kwargs)}, headers=UI), options


def test_adding_a_passkey_makes_it_a_second_factor_with_recovery_codes(client: TestClient) -> None:
    begun = client.post("/api/auth/passkeys/begin", headers=UI)
    options = json.loads(begun.json()["options"])
    assert options["rp"]["id"] == RP_ID and options["user"]["name"] == "admin"
    assert options["authenticatorSelection"]["residentKey"] == "discouraged"
    key = SoftKey()
    added = client.post(
        "/api/auth/passkeys/finish", json={"name": "yubikey", "password": PASSWORD, "credential": key.create(options)}, headers=UI
    )
    assert added.status_code == 201, added.text
    body = added.json()
    assert body["passkey"]["name"] == "yubikey" and len(body["recovery_codes"]) == 8
    assert body["account"]["two_factor"] is True and body["account"]["passkeys"] == 1 and body["account"]["totp"] is False
    listed = client.get("/api/auth/passkeys").json()
    assert [k["name"] for k in listed] == ["yubikey"] and "public_key" not in json.dumps(listed)
    # The same key twice is refused; the browser would not offer it anyway (excludeCredentials).
    again = client.post("/api/auth/passkeys/begin", headers=UI)
    assert b64(key.credential_id) in again.json()["options"]
    twice = client.post(
        "/api/auth/passkeys/finish",
        json={"name": "again", "password": PASSWORD, "credential": key.create(json.loads(again.json()["options"]))},
        headers=UI,
    )
    assert twice.status_code == 409 and twice.json()["detail"]["code"] == "passkey_exists"


def test_adding_needs_the_password_a_fresh_challenge_and_the_right_origin(client: TestClient) -> None:
    wrong = add_key(client, SoftKey(), password=WRONG)
    assert wrong.status_code == 401 and client.get("/api/auth/passkeys").json() == []
    # The challenge was not used up by the wrong password; but a stale or foreign one is refused.
    phishing = add_key(client, SoftKey(origin="https://ssh.example.com.evil.test"))
    assert phishing.status_code == 422 and phishing.json()["detail"]["code"] == "passkey_invalid"
    other_rp = add_key(client, SoftKey(rp_id="evil.test"))
    assert other_rp.status_code == 422
    no_challenge = client.post(
        "/api/auth/passkeys/finish",
        json={"name": "x", "password": PASSWORD, "credential": SoftKey().create({"challenge": b64(b"made up")})},
        headers=UI,
    )
    assert no_challenge.status_code == 410 and no_challenge.json()["detail"]["code"] == "passkey_expired"


def test_plain_http_has_no_passkeys(client: TestClient) -> None:
    client.put("/api/settings", json={"public_url": "http://192.0.2.10:8460"}, headers=UI)
    refused = client.post("/api/auth/passkeys/begin", headers=UI)
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "passkeys_need_https"
    # localhost counts as secure in every browser.
    client.put("/api/settings", json={"public_url": "http://localhost:8460"}, headers=UI)
    assert client.post("/api/auth/passkeys/begin", headers=UI).status_code == 200


def test_signing_in_with_the_passkey_opens_session_and_vault(client: TestClient) -> None:
    key = SoftKey()
    assert add_key(client, key).status_code == 201
    browser = password_step()
    assert browser.methods == ["passkey", "recovery"]  # type: ignore[attr-defined]
    # The password alone opens nothing.
    assert browser.get("/api/auth/me").status_code == 401
    done, options = passkey_step(browser, key)
    assert done.status_code == 200, done.text
    assert options["allowCredentials"][0]["id"] == b64(key.credential_id)
    me = browser.get("/api/auth/me").json()
    assert me["name"] == "admin" and me["vault"] == "open"
    assert client.get("/api/auth/passkeys").json()[0]["last_used_at"] is not None


def test_a_foreign_key_a_replay_and_a_clone_are_refused(client: TestClient) -> None:
    key = SoftKey()
    assert add_key(client, key).status_code == 201
    browser = password_step()
    stranger, _ = passkey_step(browser, SoftKey())
    assert stranger.status_code == 401 and stranger.json()["detail"]["code"] == "passkey_refused"
    # A captured answer for a used challenge does not count twice.
    good, options = passkey_step(browser, key)
    assert good.status_code == 200
    second = password_step()
    second.post("/api/auth/login/passkey/begin", headers=UI)
    replay = second.post("/api/auth/login/passkey", json={"credential": key.get(options)}, headers=UI)
    assert replay.status_code == 401
    # A counter that goes backwards points to a copied key.
    clone, _ = passkey_step(second, key, counter=1)
    assert clone.status_code == 401


def test_another_accounts_registered_key_does_not_sign_me_in(client: TestClient) -> None:
    mine = SoftKey()
    assert add_key(client, mine).status_code == 201
    member = invite_member(client, "alex")
    theirs = SoftKey()
    assert add_key(member, theirs).status_code == 201
    browser = password_step("admin")
    refused, _ = passkey_step(browser, theirs)
    assert refused.status_code == 401 and refused.json()["detail"]["code"] == "passkey_refused"
    assert browser.get("/api/auth/me").status_code == 401


def test_wrong_answers_end_the_parked_sign_in(client: TestClient) -> None:
    assert add_key(client, SoftKey()).status_code == 201
    browser = password_step()
    codes = []
    for _ in range(5):
        response, _ = passkey_step(browser, SoftKey())
        codes.append(response.json()["detail"]["code"])
    assert codes[-1] == "second_factor_expired" and codes[0] == "passkey_refused"
    assert browser.post("/api/auth/login/passkey/begin", headers=UI).status_code == 401


def test_recovery_codes_work_for_a_passkey_only_account_and_digits_do_not(client: TestClient) -> None:
    codes = add_key(client, SoftKey()).json()["recovery_codes"]
    browser = password_step()
    digits = browser.post("/api/auth/login/totp", json={"code": "123456"}, headers=UI)
    assert digits.status_code == 401
    used = browser.post("/api/auth/login/totp", json={"code": codes[0]}, headers=UI)
    assert used.status_code == 200, used.text
    assert browser.get("/api/auth/me").json()["two_factor_recovery_left"] == 7


def test_removing_the_last_passkey_takes_the_recovery_codes_along(client: TestClient) -> None:
    add_key(client, SoftKey())
    key_id = client.get("/api/auth/passkeys").json()[0]["id"]
    wrong = client.post(f"/api/auth/passkeys/{key_id}/remove", json={"password": WRONG}, headers=UI)
    assert wrong.status_code == 401
    member = invite_member(client, "alex")
    assert member.post(f"/api/auth/passkeys/{key_id}/remove", json={"password": PASSWORD}, headers=UI).status_code == 404
    gone = client.post(f"/api/auth/passkeys/{key_id}/remove", json={"password": PASSWORD}, headers=UI)
    assert gone.status_code == 200
    assert gone.json()["two_factor"] is False and gone.json()["two_factor_recovery_left"] == 0
    # Not only hidden: the hashes themselves are gone.
    with SessionLocal() as db:
        assert db.query(Account).filter(Account.name == "admin").one().totp_recovery == ""
    login = TestClient(app, base_url="http://testserver").post(
        "/api/auth/login", json={"name": "admin", "password": PASSWORD}, headers=UI
    )
    assert "second_factor" not in login.json()


def test_a_passkey_satisfies_the_required_mode_and_the_operator_resets_it(client: TestClient) -> None:
    member = invite_member(client, "alex")
    alex = next(a for a in client.get("/api/accounts").json() if a["name"] == "alex")
    # The operator has one, so the required mode does not hold the operator back.
    assert add_key(client, SoftKey(), name="operator key").status_code == 201
    client.put("/api/settings", json={"two_factor_required": True}, headers=UI)
    assert member.get("/api/auth/me").json()["second_factor_setup_required"] is True
    # In the required mode the way to a passkey is open, everything else is not.
    assert member.get("/api/connections").status_code == 403
    assert add_key(member, SoftKey()).status_code == 201
    assert member.get("/api/auth/me").json()["second_factor_setup_required"] is False
    assert member.get("/api/connections").status_code == 200
    reset = client.post(f"/api/accounts/{alex['id']}/totp/reset", headers=UI)
    assert reset.status_code == 200 and reset.json()["passkeys"] == 0 and reset.json()["two_factor"] is False
    with SessionLocal() as db:
        assert passkeys.count(db, alex["id"]) == 0


def test_the_vault_still_needs_the_password(client: TestClient) -> None:
    """A passkey is a second factor, not a way around the password: the password step stays first."""
    add_key(client, SoftKey())
    browser = TestClient(app, base_url="http://testserver")
    assert browser.post("/api/auth/login/passkey/begin", headers=UI).status_code == 401

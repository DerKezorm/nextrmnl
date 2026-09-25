"""The second factor: RFC vectors, enrolment, the two-step sign-in, replay, recovery codes, limits, the
operator's reset, the required mode. Nothing secret reaches the log."""

from __future__ import annotations

import logging
import time

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import Account
from app.security import brake
from app.services import totp
from tests.conftest import PASSWORD, UI, invite_member, sign_in

#: RFC 6238, appendix B: the ASCII seed "12345678901234567890", HMAC-SHA1, eight digits; we take the last six.
RFC_SEED = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
RFC_VECTORS = {59: "287082", 1111111109: "081804", 1234567890: "005924", 2000000000: "279037"}


class Clock:
    """The clock the second factor sees. Advancing it is how a test reaches the next code; the pending store
    keeps its own monotonic clock and is not touched."""

    def __init__(self) -> None:
        self.offset = 0.0

    def time(self) -> float:
        return time.time() + self.offset

    def monotonic(self) -> float:
        return time.monotonic()

    def advance(self, seconds: float) -> None:
        self.offset += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    fake = Clock()
    monkeypatch.setattr(totp, "time", fake)
    return fake


def current_code(secret: str) -> str:
    return totp.code_at(secret, totp.time.time())


def fresh_code(secret: str, clock: Clock) -> str:
    """The code of the next time step: the one used before counts as spent."""
    clock.advance(totp.STEP_SECONDS)
    return current_code(secret)


def enrol(client: TestClient) -> tuple[str, list[str]]:
    """Enrols the signed-in account; returns the seed and the recovery codes."""
    begun = client.post("/api/auth/totp/begin", headers=UI)
    assert begun.status_code == 200, begun.text
    secret = begun.json()["secret"]
    assert begun.json()["uri"].startswith("otpauth://totp/nextrmnl:")
    assert "<svg" in begun.json()["qr_svg"]
    confirmed = client.post("/api/auth/totp/confirm", json={"code": current_code(secret), "password": PASSWORD}, headers=UI)
    assert confirmed.status_code == 200, confirmed.text
    codes = confirmed.json()["recovery_codes"]
    assert len(codes) == totp.RECOVERY_CODES and confirmed.json()["account"]["two_factor"] is True
    return secret, codes


def password_step(client: TestClient, name: str = "admin") -> None:
    response = client.post("/api/auth/login", json={"name": name, "password": PASSWORD}, headers=UI)
    assert response.status_code == 200, response.text
    assert response.json() == {"second_factor": True}
    cookie = response.headers.get("set-cookie", "")
    assert "nextrmnl_2fa=" in cookie and "HttpOnly" in cookie and "Path=/api/auth" in cookie


def code_step(client: TestClient, code: str):
    return client.post("/api/auth/login/totp", json={"code": code}, headers=UI)


# --- The algorithm ---------------------------------------------------------------------------------------------- #


def test_codes_match_the_rfc_vectors() -> None:
    for moment, expected in RFC_VECTORS.items():
        assert totp.code_at(RFC_SEED, moment) == expected


def test_a_code_is_accepted_in_the_window_and_only_after_the_last_used_step() -> None:
    now = 1234567890
    step = now // totp.STEP_SECONDS
    assert totp.verify_code(RFC_SEED, "005924", now=now) == step
    # A step earlier and a step later still pass; two steps do not.
    assert totp.verify_code(RFC_SEED, totp.code_at(RFC_SEED, now - 30), now=now) == step - 1
    assert totp.verify_code(RFC_SEED, totp.code_at(RFC_SEED, now + 30), now=now) == step + 1
    assert totp.verify_code(RFC_SEED, totp.code_at(RFC_SEED, now + 60), now=now) is None
    # Replay: at or before the last accepted step is refused.
    assert totp.verify_code(RFC_SEED, "005924", after_step=step, now=now) is None
    assert totp.verify_code(RFC_SEED, "005924", after_step=step - 1, now=now) == step
    assert totp.verify_code(RFC_SEED, "00592", now=now) is None
    assert totp.verify_code(RFC_SEED, "abcdef", now=now) is None


def test_recovery_codes_are_typed_from_paper() -> None:
    codes = totp.generate_recovery_codes()
    assert len(codes) == 8 and len(set(codes)) == 8
    for code in codes:
        head, tail = code.split("-")
        assert len(head) == 5 and len(tail) == 5
        assert not set(code.replace("-", "")) & set("0o1liO")
    stored = totp.recovery_hashes(codes)
    assert codes[0] not in stored
    remaining = totp.use_recovery(stored, codes[2].upper().replace("-", " "))
    assert remaining is not None and len(totp.load_recovery(remaining)) == 7
    assert totp.use_recovery(remaining, codes[2]) is None


# --- Enrolment ------------------------------------------------------------------------------------------------- #


def test_enrolment_needs_the_password_and_a_matching_code(client: TestClient, operator: dict, clock: Clock) -> None:
    begun = client.post("/api/auth/totp/begin", headers=UI).json()
    secret = begun["secret"]
    wrong_password = client.post("/api/auth/totp/confirm", json={"code": current_code(secret), "password": "not-it"}, headers=UI)
    assert wrong_password.status_code == 401 and wrong_password.json()["detail"]["code"] == "wrong_password"
    wrong_code = client.post("/api/auth/totp/confirm", json={"code": "000000", "password": PASSWORD}, headers=UI)
    assert wrong_code.status_code == 422 and wrong_code.json()["detail"]["code"] == "totp_code_wrong"
    assert client.get("/api/auth/me").json()["two_factor"] is False

    confirmed = client.post("/api/auth/totp/confirm", json={"code": current_code(secret), "password": PASSWORD}, headers=UI)
    assert confirmed.status_code == 200, confirmed.text
    me = client.get("/api/auth/me").json()
    assert me["two_factor"] is True and me["two_factor_recovery_left"] == 8
    # Already on: a second enrolment is refused until it is turned off.
    assert client.post("/api/auth/totp/begin", headers=UI).status_code == 409
    # The seed is stored sealed, not in the clear.
    with SessionLocal() as db:
        row = db.get(Account, operator["id"])
        assert row is not None and row.totp_secret_enc and secret not in row.totp_secret_enc
        assert totp.open_seed(row.totp_secret_enc) == secret


def test_nothing_secret_reaches_the_log(client: TestClient, operator: dict, clock: Clock, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        secret, codes = enrol(client)
        client.post("/api/auth/logout", headers=UI)
        password_step(client)
        assert code_step(client, fresh_code(secret, clock)).status_code == 200
    assert secret not in caplog.text
    for code in codes:
        assert code not in caplog.text and code.replace("-", "") not in caplog.text
    assert "Second factor enabled account=admin" in caplog.text


# --- Signing in ------------------------------------------------------------------------------------------------- #


def test_sign_in_takes_two_steps_and_opens_the_vault_only_at_the_end(client: TestClient, operator: dict, clock: Clock) -> None:
    secret, _codes = enrol(client)
    client.post("/api/auth/logout", headers=UI)
    password_step(client)
    # The password alone signs nobody in.
    assert client.get("/api/auth/me").status_code == 401
    wrong = code_step(client, "000000")
    assert wrong.status_code == 401 and wrong.json()["detail"]["code"] == "totp_code_wrong"
    code = fresh_code(secret, clock)
    right = code_step(client, code)
    assert right.status_code == 200, right.text
    assert right.json()["name"] == "admin" and right.json()["vault"] == "open"
    assert client.get("/api/auth/me").status_code == 200
    # The pending cookie is gone; the code step cannot be repeated.
    assert code_step(client, code).status_code == 401


def test_the_enrolment_code_is_spent_and_a_code_works_once(client: TestClient, operator: dict, clock: Clock) -> None:
    secret, _codes = enrol(client)
    client.post("/api/auth/logout", headers=UI)
    password_step(client)
    # The code that confirmed the enrolment belongs to a step already used.
    spent = code_step(client, current_code(secret))
    assert spent.status_code == 401 and spent.json()["detail"]["code"] == "totp_code_wrong"
    code = fresh_code(secret, clock)
    assert code_step(client, code).status_code == 200
    client.post("/api/auth/logout", headers=UI)
    password_step(client)
    replayed = code_step(client, code)
    assert replayed.status_code == 401 and replayed.json()["detail"]["code"] == "totp_code_wrong"
    assert code_step(client, fresh_code(secret, clock)).status_code == 200


def test_five_wrong_codes_end_the_pending_sign_in(client: TestClient, operator: dict, clock: Clock) -> None:
    secret, _codes = enrol(client)
    client.post("/api/auth/logout", headers=UI)
    password_step(client)
    for _ in range(totp.MAX_ATTEMPTS - 1):
        assert code_step(client, "000000").json()["detail"]["code"] == "totp_code_wrong"
    last = code_step(client, "000000")
    assert last.status_code == 401 and last.json()["detail"]["code"] == "second_factor_expired"
    # Even the right code is refused now: the password has to be given again.
    code = fresh_code(secret, clock)
    again = code_step(client, code)
    assert again.status_code == 401 and again.json()["detail"]["code"] == "second_factor_expired"
    # The sender's brake is a guard of its own (five free tries, then waiting); lifted here to see the rest.
    brake._fails.clear()
    password_step(client)
    assert code_step(client, code).status_code == 200


def test_the_brake_slows_a_guessing_sender(client: TestClient, operator: dict, clock: Clock) -> None:
    enrol(client)
    client.post("/api/auth/logout", headers=UI)
    password_step(client)
    for _ in range(totp.MAX_ATTEMPTS):
        code_step(client, "000000")
    password_step(client)
    braked = code_step(client, "000000")
    assert braked.status_code == 429 and braked.json()["detail"]["code"] == "too_many_attempts"
    assert "retry-after" in braked.headers


def test_a_recovery_code_signs_in_once(client: TestClient, operator: dict, clock: Clock) -> None:
    _secret, codes = enrol(client)
    client.post("/api/auth/logout", headers=UI)
    password_step(client)
    used = code_step(client, codes[0])
    assert used.status_code == 200, used.text
    assert used.json()["two_factor_recovery_left"] == 7
    client.post("/api/auth/logout", headers=UI)
    password_step(client)
    assert code_step(client, codes[0]).status_code == 401
    assert code_step(client, codes[1]).status_code == 200


def test_new_recovery_codes_replace_the_old_ones(client: TestClient, operator: dict, clock: Clock) -> None:
    _secret, old = enrol(client)
    refused = client.post("/api/auth/totp/recovery", json={"password": "wrong-one"}, headers=UI)
    assert refused.status_code == 401
    renewed = client.post("/api/auth/totp/recovery", json={"password": PASSWORD}, headers=UI)
    assert renewed.status_code == 200
    new = renewed.json()["recovery_codes"]
    assert len(new) == 8 and not set(new) & set(old)
    client.post("/api/auth/logout", headers=UI)
    password_step(client)
    assert code_step(client, old[0]).status_code == 401
    assert code_step(client, new[0]).status_code == 200


def test_turning_it_off_needs_the_password(client: TestClient, operator: dict, clock: Clock) -> None:
    enrol(client)
    assert client.post("/api/auth/totp/disable", json={"password": "wrong-one"}, headers=UI).status_code == 401
    off = client.post("/api/auth/totp/disable", json={"password": PASSWORD}, headers=UI)
    assert off.status_code == 200 and off.json()["two_factor"] is False
    client.post("/api/auth/logout", headers=UI)
    # One step again.
    assert sign_in(client, "admin")["vault"] == "open"


def test_the_operator_resets_a_member_but_not_the_other_way_round(client: TestClient, operator: dict, clock: Clock) -> None:
    member = invite_member(client, "alex")
    enrol(member)
    member_id = member.get("/api/auth/me").json()["id"]
    # A member cannot reset the operator.
    assert member.post(f"/api/accounts/{operator['id']}/totp/reset", headers=UI).status_code == 403
    # The operator turns their own off under the account, not here.
    assert client.post(f"/api/accounts/{operator['id']}/totp/reset", headers=UI).status_code == 409
    reset = client.post(f"/api/accounts/{member_id}/totp/reset", headers=UI)
    assert reset.status_code == 200 and reset.json()["two_factor"] is False
    assert client.post(f"/api/accounts/{member_id}/totp/reset", headers=UI).status_code == 409
    member.post("/api/auth/logout", headers=UI)
    assert sign_in(member, "alex")["two_factor"] is False


# --- Required by the operator ---------------------------------------------------------------------------------- #


def test_required_mode_lets_an_account_without_a_second_factor_only_enrol(client: TestClient, operator: dict, clock: Clock) -> None:
    member = invite_member(client, "alex")
    assert member.get("/api/connections").status_code == 200
    assert client.put("/api/settings", json={"two_factor_required": True}, headers=UI).status_code == 200
    me = member.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["second_factor_setup_required"] is True
    blocked = member.get("/api/connections")
    assert blocked.status_code == 403 and blocked.json()["detail"]["code"] == "second_factor_setup_required"
    enrol(member)
    assert member.get("/api/auth/me").json()["second_factor_setup_required"] is False
    assert member.get("/api/connections").status_code == 200


def test_required_mode_applies_to_the_operator_too(client: TestClient, operator: dict, clock: Clock) -> None:
    assert client.put("/api/settings", json={"two_factor_required": True}, headers=UI).status_code == 200
    blocked = client.get("/api/settings")
    assert blocked.status_code == 403 and blocked.json()["detail"]["code"] == "second_factor_setup_required"
    # Seeing oneself, enrolling and leaving stay possible.
    assert client.get("/api/auth/me").status_code == 200
    enrol(client)
    assert client.get("/api/settings").status_code == 200


def test_oidc_only_accounts_have_no_second_factor_here(client: TestClient, operator: dict) -> None:
    """An account without a password cannot enrol: its provider is in charge of the second factor."""
    from app.security import start_session

    with SessionLocal() as db:
        account = Account(name="idp-user", role="member", sign_in="oidc", oidc_subject="sub-1", email="u@example.com")
        db.add(account)
        db.commit()
        token = start_session(db, account, "127.0.0.1", "tests")
    other = TestClient(app, base_url="http://testserver")
    other.cookies.set("nextrmnl_session", token)
    refused = other.post("/api/auth/totp/begin", headers=UI)
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "oidc_account"
    # Required mode does not lock such an account out either.
    assert client.put("/api/settings", json={"two_factor_required": True}, headers=UI).status_code == 200
    assert other.get("/api/connections").status_code == 200

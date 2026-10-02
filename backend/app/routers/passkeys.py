"""Passkeys as the second factor: list, add, remove, and the passkey step of a sign-in.

Adding and removing need the password, like turning the app on and off. The sign-in step redeems the same parked
password step as the code does (``routers/totp.py``): a wrong or foreign key counts like a wrong code.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..deps import CurrentAccount, DbSession, check_csrf, client_ip
from ..meldungen import fehler, meldung
from ..models import SIGN_IN_PASSWORD, Account
from ..security import brake
from ..services import accounts, passkeys, settings_service, totp, vault
from .auth import PENDING_COOKIE, account_view, sign_in_response
from .totp import _check_password, _clear_pending_cookie

logger = logging.getLogger("nextrmnl.auth")

router = APIRouter(prefix="/api", tags=["second factor"])


class FinishIn(BaseModel):
    name: str = Field(default="", max_length=64)
    password: str = Field(max_length=200)
    #: The browser's ``PublicKeyCredential`` as JSON; checked by py_webauthn, not by us.
    credential: dict[str, Any]


class PasswordIn(BaseModel):
    password: str = Field(max_length=200)


class AssertionIn(BaseModel):
    credential: dict[str, Any]


def _raise(error: passkeys.PasskeyError) -> HTTPException:
    return fehler(error.code, error.message, error.status)


@router.get("/auth/passkeys", summary="The own passkeys")
def listing(account: CurrentAccount, db: DbSession) -> list[dict[str, Any]]:
    return [passkeys.view(row) for row in passkeys.listing(db, account)]


@router.post("/auth/passkeys/begin", summary="Start adding a passkey; the browser gets the options")
def begin(request: Request, account: CurrentAccount, db: DbSession) -> dict[str, Any]:
    if account.sign_in != SIGN_IN_PASSWORD:
        raise fehler("oidc_account", "This account signs in through OIDC.", 409)
    try:
        return {"options": passkeys.begin_registration(db, request, account)}
    except passkeys.PasskeyError as error:
        raise _raise(error) from error


@router.post(
    "/auth/passkeys/finish", status_code=201, summary="Finish adding a passkey: the browser's answer and the password"
)
def finish(payload: FinishIn, request: Request, account: CurrentAccount, db: DbSession) -> dict[str, Any]:
    if account.sign_in != SIGN_IN_PASSWORD:
        raise fehler("oidc_account", "This account signs in through OIDC.", 409)
    _check_password(request, db, account, payload.password)
    first = not totp.has_second_factor(db, account)
    try:
        row = passkeys.finish_registration(db, request, account, payload.credential, payload.name)
    except passkeys.PasskeyError as error:
        raise _raise(error) from error
    codes = None
    if first or not totp.load_recovery(account.totp_recovery):
        # The first second factor brings recovery codes, as the app does: for the day the key is gone.
        codes = totp.generate_recovery_codes()
        account.totp_recovery = totp.recovery_hashes(codes)
        db.commit()
    return {"passkey": passkeys.view(row), "recovery_codes": codes, "account": account_view(db, account)}


@router.post("/auth/passkeys/{passkey_id}/remove", summary="Remove a passkey; needs the password")
def remove(
    passkey_id: int, payload: PasswordIn, request: Request, account: CurrentAccount, db: DbSession
) -> dict[str, Any]:
    _check_password(request, db, account, payload.password)
    if not passkeys.remove(db, account, passkey_id):
        raise fehler("not_found", "Passkey not found.", 404)
    if not totp.has_second_factor(db, account):
        # The last factor went; recovery codes for nothing would only be a way around the password.
        account.totp_recovery = ""
        db.commit()
    return account_view(db, account)


# --- The passkey step of a sign-in ----------------------------------------------------------------------------- #


def _pending_account(request: Request, response: Response, db: DbSession) -> tuple[str, Any, Account]:
    token = request.cookies.get(PENDING_COOKIE)
    pending = totp.get_pending(token)
    if token is None or pending is None:
        _clear_pending_cookie(response)
        raise fehler("second_factor_expired", "Start again with your password.", 401)
    account = db.get(Account, pending.account_id)
    if account is None or passkeys.count(db, account.id) == 0:
        totp.finish_pending(token)
        _clear_pending_cookie(response)
        raise fehler("second_factor_expired", "Start again with your password.", 401)
    if accounts.is_locked(account):
        totp.finish_pending(token)
        _clear_pending_cookie(response)
        raise fehler("account_locked", "Too many failed sign-ins. Try again later.", 429)
    return token, pending, account


@router.post("/auth/login/passkey/begin", summary="Second step with a passkey: the browser gets the challenge")
def login_begin(request: Request, response: Response, db: DbSession) -> dict[str, Any]:
    check_csrf(request)
    token, _pending, account = _pending_account(request, response, db)
    try:
        return {"options": passkeys.begin_sign_in(db, request, account, token)}
    except passkeys.PasskeyError as error:
        raise _raise(error) from error


@router.post("/auth/login/passkey", summary="Second step with a passkey: the browser's signed answer")
def login_finish(payload: AssertionIn, request: Request, response: Response, db: DbSession) -> dict[str, Any]:
    check_csrf(request)
    key = "totp:" + client_ip(request)
    wait = brake.wait_seconds(key)
    if wait:
        raise HTTPException(
            status_code=429,
            detail=meldung("too_many_attempts", "Too many attempts. Try again later.", retry_after=wait),
            headers={"Retry-After": str(wait)},
        )
    token, pending, account = _pending_account(request, response, db)
    try:
        accepted = passkeys.finish_sign_in(db, request, account, token, payload.credential)
    except passkeys.PasskeyError as error:
        raise _raise(error) from error
    if not accepted:
        brake.failed(key)
        accounts.note_failure(db, account)
        still_pending = totp.fail_pending(token) and not accounts.is_locked(account)
        logger.warning("Passkey step failed account=%s", account.name)
        if not still_pending:
            totp.finish_pending(token)
            _clear_pending_cookie(response)
            raise fehler("second_factor_expired", "Too many failed tries. Start again with your password.", 401)
        raise fehler("passkey_refused", "The passkey was not accepted. Try again or use another way.", 401)
    brake.succeeded(key)
    accounts.note_success(db, account)
    totp.finish_pending(token)
    _clear_pending_cookie(response)
    if pending.vault_key is not None and account.vault_ready:
        vault.open_with_key(account, pending.vault_key, int(settings_service.get(db, "vault_lock_minutes")))
    logger.info("Second factor passed with a passkey account=%s", account.name)
    return sign_in_response(db, request, response, account)

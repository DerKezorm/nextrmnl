"""The second factor: enrol, confirm, disable, recovery codes, the code step of a sign-in, the operator's reset.

The password step of a sign-in lives in ``routers/auth.py``; when the account has a second factor it answers
``{"second_factor": true}`` and leaves a short-lived cookie. The code step here turns that into the session.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..deps import (
    CurrentAccount,
    DbSession,
    OperatorAccount,
    check_csrf,
    client_ip,
    reauth_failed,
    reauth_guard,
    reauth_succeeded,
)
from ..meldungen import fehler, meldung
from ..models import SIGN_IN_PASSWORD, Account
from ..security import brake, end_all_sessions
from ..services import accounts, settings_service, totp, vault
from .auth import PENDING_COOKIE, account_view, end_ssh_sessions, sign_in_response

logger = logging.getLogger("nextrmnl.auth")

router = APIRouter(prefix="/api", tags=["second factor"])


class PasswordIn(BaseModel):
    password: str = Field(max_length=200)


class ConfirmIn(BaseModel):
    code: str = Field(max_length=32)
    password: str = Field(max_length=200)


class CodeIn(BaseModel):
    code: str = Field(max_length=32)


def _check_password(request: Request, db: DbSession, account: Account, password: str) -> None:
    """The password once more, counted like a sign-in: a stolen cookie must not be a place to guess it."""
    if account.sign_in != SIGN_IN_PASSWORD:
        raise fehler("oidc_account", "This account signs in through OIDC.", 409)
    reauth_guard(request, account)
    if not accounts.check_password(account, password):
        reauth_failed(request, db, account)
        raise fehler("wrong_password", "The current password is wrong.", 401)
    reauth_succeeded(request, db, account)


def _clear_pending_cookie(response: Response) -> None:
    response.delete_cookie(PENDING_COOKIE, path="/api/auth")


# --- Enrolment ------------------------------------------------------------------------------------------------- #


@router.post("/auth/totp/begin", summary="Start enrolling an authenticator app; the seed is shown once")
def begin(account: CurrentAccount) -> dict[str, Any]:
    if account.sign_in != SIGN_IN_PASSWORD:
        raise fehler("oidc_account", "This account signs in through OIDC.", 409)
    if account.totp_secret_enc:
        raise fehler("totp_enabled", "The second factor is already on. Turn it off first.", 409)
    seed = totp.begin_enrolment(account.id)
    uri = totp.provisioning_uri(seed, account.name)
    return {"secret": seed, "uri": uri, "qr_svg": totp.qr_svg(uri)}


@router.post("/auth/totp/confirm", summary="Finish enrolling: a code from the app and the password")
def confirm(payload: ConfirmIn, request: Request, account: CurrentAccount, db: DbSession) -> dict[str, Any]:
    if account.totp_secret_enc:
        raise fehler("totp_enabled", "The second factor is already on. Turn it off first.", 409)
    seed = totp.pending_seed(account.id)
    if seed is None:
        raise fehler("totp_enrolment_expired", "The enrolment timed out. Start again.", 410)
    _check_password(request, db, account, payload.password)
    step = totp.verify_code(seed, payload.code)
    if step is None:
        raise fehler("totp_code_wrong", "The code is not right. Check the time on your phone and try again.", 422)
    codes = totp.generate_recovery_codes()
    account.totp_secret_enc = totp.seal_seed(seed)
    account.totp_recovery = totp.recovery_hashes(codes)
    account.totp_last_step = step
    db.commit()
    totp.drop_enrolment(account.id)
    logger.info("Second factor enabled account=%s", account.name)
    return {"recovery_codes": codes, "account": account_view(db, account)}


@router.post("/auth/totp/disable", summary="Turn the second factor off; needs the password")
def disable(payload: PasswordIn, request: Request, account: CurrentAccount, db: DbSession) -> dict[str, Any]:
    if not account.totp_secret_enc:
        raise fehler("totp_not_enabled", "The second factor is not on.", 409)
    _check_password(request, db, account, payload.password)
    _reset(db, account)
    logger.info("Second factor disabled account=%s", account.name)
    return account_view(db, account)


@router.post("/auth/totp/recovery", summary="New recovery codes; the old ones stop working")
def new_recovery_codes(payload: PasswordIn, request: Request, account: CurrentAccount, db: DbSession) -> dict[str, Any]:
    if not account.totp_secret_enc:
        raise fehler("totp_not_enabled", "The second factor is not on.", 409)
    _check_password(request, db, account, payload.password)
    codes = totp.generate_recovery_codes()
    account.totp_recovery = totp.recovery_hashes(codes)
    db.commit()
    logger.info("Recovery codes renewed account=%s", account.name)
    return {"recovery_codes": codes, "account": account_view(db, account)}


def _reset(db: DbSession, account: Account) -> None:
    account.totp_secret_enc = ""
    account.totp_recovery = ""
    account.totp_last_step = 0
    db.commit()
    totp.forget_account(account.id)


@router.post("/accounts/{account_id}/totp/reset", summary="Operator: remove the second factor of a locked-out account")
def operator_reset(account_id: int, operator: OperatorAccount, db: DbSession) -> dict[str, Any]:
    if account_id == operator.id:
        raise fehler("use_disable", "Turn your own second factor off under your account, with your password.", 409)
    row = db.get(Account, account_id)
    if row is None:
        raise fehler("not_found", "Account not found.", 404)
    if not row.totp_secret_enc:
        raise fehler("totp_not_enabled", "The second factor is not on.", 409)
    _reset(db, row)
    # A reset is what happens after a lost phone or a suspected intruder: whoever holds a session of that
    # account is thrown out with it, terminals included, and signs in afresh with the password alone.
    end_all_sessions(db, row.id)
    vault.lock(row.id)
    end_ssh_sessions(row.id, "signed_out")
    logger.warning("Second factor reset by operator account=%s by=%s, all sessions ended", row.name, operator.name)
    return account_view(db, row)


# --- The code step of a sign-in -------------------------------------------------------------------------------- #


@router.post("/auth/login/totp", summary="Second step of the sign-in: the code from the app or a recovery code")
def login_code(payload: CodeIn, request: Request, response: Response, db: DbSession) -> dict[str, Any]:
    check_csrf(request)
    token = request.cookies.get(PENDING_COOKIE)
    pending = totp.get_pending(token)
    if token is None or pending is None:
        _clear_pending_cookie(response)
        raise fehler("second_factor_expired", "Start again with your password.", 401)
    key = "totp:" + client_ip(request)
    wait = brake.wait_seconds(key)
    if wait:
        raise HTTPException(
            status_code=429,
            detail=meldung("too_many_attempts", "Too many attempts. Try again later.", retry_after=wait),
            headers={"Retry-After": str(wait)},
        )
    account = db.get(Account, pending.account_id)
    if account is None or not account.totp_secret_enc:
        totp.finish_pending(token)
        _clear_pending_cookie(response)
        raise fehler("second_factor_expired", "Start again with your password.", 401)
    if accounts.is_locked(account):
        # Wrong codes count against the account like wrong passwords, whatever address they come from.
        totp.finish_pending(token)
        _clear_pending_cookie(response)
        raise fehler("account_locked", "Too many failed sign-ins. Try again later.", 429)

    typed = totp.normalize_code(payload.code)
    used_recovery = False
    if len(typed) == totp.DIGITS and typed.isdigit():
        try:
            seed = totp.open_seed(account.totp_secret_enc)
        except totp.SeedUnreadable:
            # A different secret.key than the one that sealed the seed. Failing closed is the only safe answer;
            # the operator resets the second factor and the person enrols again.
            logger.error("Second factor seed unreadable account=%s (secret.key changed?)", account.name)
            totp.finish_pending(token)
            _clear_pending_cookie(response)
            raise fehler(
                "second_factor_unavailable",
                "The second factor cannot be checked on this installation. Ask the operator to reset it.",
                401,
            ) from None
        step = totp.verify_code(seed, typed, after_step=account.totp_last_step)
        accepted = step is not None
        if accepted:
            account.totp_last_step = step
    else:
        remaining = totp.use_recovery(account.totp_recovery, typed)
        accepted = remaining is not None
        if accepted:
            account.totp_recovery = remaining or "[]"
            used_recovery = True
    if not accepted:
        brake.failed(key)
        accounts.note_failure(db, account)
        still_pending = totp.fail_pending(token) and not accounts.is_locked(account)
        logger.warning("Second factor failed account=%s", account.name)
        if not still_pending:
            totp.finish_pending(token)
            _clear_pending_cookie(response)
            raise fehler("second_factor_expired", "Too many wrong codes. Start again with your password.", 401)
        raise fehler("totp_code_wrong", "The code is not right.", 401)

    brake.succeeded(key)
    db.commit()
    accounts.note_success(db, account)
    totp.finish_pending(token)
    _clear_pending_cookie(response)
    if pending.vault_key is not None and account.vault_ready:
        vault.open_with_key(account, pending.vault_key, int(settings_service.get(db, "vault_lock_minutes")))
    if used_recovery:
        left = len(totp.load_recovery(account.totp_recovery))
        logger.warning("Recovery code used account=%s remaining=%s", account.name, left)
    else:
        logger.info("Second factor passed account=%s", account.name)
    return sign_in_response(db, request, response, account)


@router.post("/auth/login/totp/cancel", status_code=204, summary="Give up the second step and start over")
def cancel_code(request: Request, response: Response) -> None:
    check_csrf(request)
    token = request.cookies.get(PENDING_COOKIE)
    if token:
        totp.finish_pending(token)
    _clear_pending_cookie(response)

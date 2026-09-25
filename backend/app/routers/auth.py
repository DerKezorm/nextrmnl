"""Setup of the first account, sign-in, sign-out, invitations, the own account."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import __version__
from ..config import get_settings
from ..deps import CurrentAccount, DbSession, OperatorAccount, check_csrf, client_ip
from ..meldungen import fehler, meldung
from ..models import ROLES, SIGN_IN_PASSWORD, Account
from ..security import MIN_PASSWORD, SESSION_COOKIE, brake, end_all_sessions, end_session, start_session
from ..services import accounts, settings_service, totp, vault
from ..services.accounts import AccountError

logger = logging.getLogger("nextrmnl.auth")

router = APIRouter(prefix="/api", tags=["auth"])

#: The password step of a sign-in with a second factor leaves this cookie; the code step redeems it.
PENDING_COOKIE = "nextrmnl_2fa"


class SetupIn(BaseModel):
    name: str = Field(max_length=64)
    password: str = Field(max_length=200)


class LoginIn(BaseModel):
    name: str = Field(max_length=64)
    password: str = Field(max_length=200)


class PasswordChangeIn(BaseModel):
    current: str = Field(max_length=200)
    new: str = Field(max_length=200)


class PrefsIn(BaseModel):
    prefs: dict[str, Any]


class InviteIn(BaseModel):
    name: str = Field(default="", max_length=64)
    role: str = Field(default="member")


class InviteAcceptIn(BaseModel):
    name: str = Field(max_length=64)
    password: str = Field(max_length=200)


def _secure(request: Request) -> bool:
    mode = get_settings().cookie_secure.lower()
    if mode == "on":
        return True
    if mode == "off":
        return False
    forwarded = request.headers.get("x-forwarded-proto", "")
    return request.url.scheme == "https" or forwarded.split(",")[0].strip() == "https"


def _set_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=get_settings().session_days * 86400,
        httponly=True,
        samesite="lax",
        secure=_secure(request),
        path="/",
    )


def _raise(error: AccountError) -> HTTPException:
    return HTTPException(status_code=error.status, detail=meldung(error.code, error.message))


def _check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise fehler("password_too_short", f"Use at least {MIN_PASSWORD} characters.", 422, minimum=MIN_PASSWORD)


def _set_pending_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        PENDING_COOKIE,
        token,
        max_age=totp.PENDING_SECONDS,
        httponly=True,
        samesite="lax",
        secure=_secure(request),
        path="/api/auth",
    )


def account_view(db: DbSession, account: Account) -> dict[str, Any]:
    return {
        "id": account.id,
        "name": account.name,
        "role": account.role,
        "sign_in": account.sign_in,
        "email": account.email,
        "two_factor": bool(account.totp_secret_enc),
        "two_factor_recovery_left": len(totp.load_recovery(account.totp_recovery)) if account.totp_secret_enc else 0,
        "second_factor_setup_required": totp.setup_required(db, account),
        "oidc_linked": bool(account.oidc_subject),
        "vault": "unset" if not account.vault_ready else ("open" if vault.is_open(account.id) else "locked"),
        "prefs": account.prefs or {},
        "created_at": account.created_at.isoformat(),
        "last_seen_at": account.last_seen_at.isoformat() if account.last_seen_at else None,
    }


def sign_in_response(db: DbSession, request: Request, response: Response, account: Account) -> dict[str, Any]:
    token = start_session(db, account, client_ip(request), request.headers.get("user-agent", ""))
    _set_cookie(response, request, token)
    return account_view(db, account)


@router.get("/setup", summary="Does nextrmnl still need its first account?")
def setup_state(db: DbSession) -> dict[str, Any]:
    return {"needs_setup": accounts.count(db) == 0, "version": __version__, "min_password": MIN_PASSWORD}


@router.post("/setup", summary="Create the operator account")
def setup(payload: SetupIn, request: Request, response: Response, db: DbSession) -> dict[str, Any]:
    check_csrf(request)
    _check_password(payload.password)
    try:
        account = accounts.create_operator(db, payload.name, payload.password)
    except AccountError as error:
        raise _raise(error) from error
    vault.unlock(account, payload.password, int(settings_service.get(db, "vault_lock_minutes")))
    return sign_in_response(db, request, response, account)


@router.post("/auth/login", summary="Sign in with name and password")
def login(payload: LoginIn, request: Request, response: Response, db: DbSession) -> dict[str, Any]:
    check_csrf(request)
    if not settings_service.get(db, "password_login"):
        operator = accounts.by_name(db, payload.name)
        # The operator keeps the password as the emergency exit even when password sign-in is off.
        if operator is None or operator.role != "operator":
            raise fehler("password_login_off", "Sign-in with a password is turned off.", 403)
    key = "login:" + client_ip(request)
    wait = brake.wait_seconds(key)
    if wait:
        raise HTTPException(
            status_code=429,
            detail=meldung("too_many_attempts", "Too many attempts. Try again later.", retry_after=wait),
            headers={"Retry-After": str(wait)},
        )
    try:
        account = accounts.authenticate(db, payload.name, payload.password)
    except AccountError as error:
        brake.failed(key)
        raise _raise(error) from error
    brake.succeeded(key)
    if account.totp_secret_enc:
        # Nothing opens yet. The vault key is unwrapped now, while the password is at hand, and parked until the
        # code step; the browser gets a short-lived cookie that names the parked sign-in and nothing else.
        vault_key: bytes | None = None
        if account.vault_ready:
            try:
                vault_key = vault.unwrap_key(account, payload.password)
            except Exception:  # noqa: BLE001
                vault_key = None
        _set_pending_cookie(response, request, totp.start_pending(account.id, vault_key))
        logger.info("Password accepted, second factor pending account=%s", account.name)
        return {"second_factor": True}
    # The password was just given, so the vault opens along with the session.
    if account.vault_ready:
        try:
            vault.unlock(account, payload.password, int(settings_service.get(db, "vault_lock_minutes")))
        except Exception:  # noqa: BLE001
            vault.lock(account.id)
    return sign_in_response(db, request, response, account)


@router.post("/auth/logout", status_code=204, summary="Sign out in this browser")
def logout(request: Request, response: Response, db: DbSession) -> None:
    check_csrf(request)
    token = request.cookies.get(SESSION_COOKIE)
    account = None
    if token:
        from ..security import session_account

        account = session_account(db, token)
    end_session(db, token)
    if account is not None:
        vault.lock(account.id)
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/auth/me", summary="The signed-in account")
def me(account: CurrentAccount, db: DbSession) -> dict[str, Any]:
    return account_view(db, account)


@router.put("/auth/password", status_code=204, summary="Change the own password")
def change_password(payload: PasswordChangeIn, request: Request, account: CurrentAccount, db: DbSession) -> None:
    if account.sign_in != SIGN_IN_PASSWORD:
        raise fehler("oidc_account", "This account signs in through OIDC.", 409)
    _check_password(payload.new)
    try:
        accounts.change_password(db, account, payload.current, payload.new)
    except AccountError as error:
        raise _raise(error) from error
    # Other browsers must sign in again; this one stays.
    end_all_sessions(db, account.id, except_token=request.cookies.get(SESSION_COOKIE))
    vault.lock(account.id)
    vault.unlock(account, payload.new, int(settings_service.get(db, "vault_lock_minutes")))


@router.put("/me/prefs", summary="Terminal and clipboard preferences of the own account")
def save_prefs(payload: PrefsIn, account: CurrentAccount, db: DbSession) -> dict[str, Any]:
    if len(str(payload.prefs)) > 4000:
        raise fehler("prefs_too_large", "The preferences are too large.", 422)
    account.prefs = payload.prefs
    db.commit()
    return account.prefs


# ---------------------------------------------------------------------------
# Accounts and invitations (operator)
# ---------------------------------------------------------------------------


@router.get("/accounts", summary="All accounts")
def list_accounts(operator: OperatorAccount, db: DbSession) -> list[dict[str, Any]]:
    from sqlalchemy import select

    return [account_view(db, row) for row in db.scalars(select(Account).order_by(Account.created_at))]


@router.get("/accounts/names", summary="Names of all accounts, for sharing a connection")
def account_names(account: CurrentAccount, db: DbSession) -> list[dict[str, Any]]:
    from sqlalchemy import select

    return [{"id": row.id, "name": row.name} for row in db.scalars(select(Account).order_by(Account.name))]


@router.delete("/accounts/{account_id}", status_code=204, summary="Delete an account with everything it owns")
def delete_account(account_id: int, operator: OperatorAccount, db: DbSession) -> None:
    if account_id == operator.id:
        raise fehler("cannot_delete_self", "You cannot delete your own account.", 409)
    row = db.get(Account, account_id)
    if row is None:
        raise fehler("not_found", "Account not found.", 404)
    vault.lock(row.id)
    db.delete(row)
    db.commit()


class RoleIn(BaseModel):
    role: str


@router.put("/accounts/{account_id}/role", summary="Make an account operator or member")
def set_role(account_id: int, payload: RoleIn, operator: OperatorAccount, db: DbSession) -> dict[str, Any]:
    if payload.role not in ROLES:
        raise fehler("invalid_role", "Unknown role.", 422)
    row = db.get(Account, account_id)
    if row is None:
        raise fehler("not_found", "Account not found.", 404)
    if row.id == operator.id and payload.role != "operator":
        raise fehler("cannot_demote_self", "You cannot take the operator role from yourself.", 409)
    row.role = payload.role
    db.commit()
    return account_view(db, row)


@router.get("/accounts/invites", summary="Open invitations")
def list_invites(operator: OperatorAccount, db: DbSession) -> list[dict[str, Any]]:
    return [
        {"id": row.id, "name": row.name, "role": row.role, "expires_at": row.expires_at.isoformat()}
        for row in accounts.list_invites(db)
    ]


@router.post("/accounts/invites", status_code=201, summary="Invite an account; the link is shown once")
def create_invite(payload: InviteIn, request: Request, operator: OperatorAccount, db: DbSession) -> dict[str, Any]:
    try:
        invite, token = accounts.create_invite(db, operator, payload.name, payload.role)
    except AccountError as error:
        raise _raise(error) from error
    base = settings_service.public_url(db) or str(request.base_url).rstrip("/")
    return {
        "id": invite.id,
        "name": invite.name,
        "role": invite.role,
        "expires_at": invite.expires_at.isoformat(),
        "link": f"{base}/invite/{token}",
    }


@router.delete("/accounts/invites/{invite_id}", status_code=204, summary="Withdraw an invitation")
def delete_invite(invite_id: int, operator: OperatorAccount, db: DbSession) -> None:
    if not accounts.delete_invite(db, invite_id):
        raise fehler("not_found", "Invitation not found.", 404)


@router.get("/invites/{token}", summary="Is this invitation valid? (no sign-in needed)")
def invite_state(token: str, db: DbSession) -> dict[str, Any]:
    invite = accounts.find_invite(db, token)
    if invite is None:
        raise fehler("invite_invalid", "This invitation is not valid any more.", 404)
    return {"name": invite.name, "role": invite.role, "min_password": MIN_PASSWORD}


@router.post("/invites/{token}", summary="Accept an invitation and create the account")
def accept_invite(
    token: str, payload: InviteAcceptIn, request: Request, response: Response, db: DbSession
) -> dict[str, Any]:
    check_csrf(request)
    _check_password(payload.password)
    try:
        account = accounts.accept_invite(db, token, payload.name, payload.password)
    except AccountError as error:
        raise _raise(error) from error
    vault.unlock(account, payload.password, int(settings_service.get(db, "vault_lock_minutes")))
    return sign_in_response(db, request, response, account)

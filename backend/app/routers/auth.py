"""Setup of the first account, sign-in, sign-out, invitations, the own account."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import __version__
from ..config import get_settings
from ..deps import (
    CurrentAccount,
    DbSession,
    OperatorAccount,
    check_csrf,
    client_ip,
    reauth_failed,
    reauth_guard,
    reauth_succeeded,
    request_base,
    request_is_https,
)
from ..meldungen import fehler, meldung
from ..models import ROLES, SIGN_IN_PASSWORD, Account
from ..security import MIN_PASSWORD, SESSION_COOKIE, brake, end_all_sessions, end_session, start_session
from ..services import accounts, oidc_store, passkeys, settings_service, totp, vault
from ..services.accounts import AccountError
from ..vendor.nexoidc import providers

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
    #: The account made from this invitation is a guest until then; empty for no end.
    account_expires_at: datetime | None = None


class ExpiryIn(BaseModel):
    #: When the account ends; null takes the end away.
    expires_at: datetime | None = None


class InviteAcceptIn(BaseModel):
    name: str = Field(max_length=64)
    password: str = Field(max_length=200)


def _secure(request: Request) -> bool:
    mode = get_settings().cookie_secure.lower()
    if mode == "on":
        return True
    if mode == "off":
        return False
    # The connection's own scheme, or X-Forwarded-Proto from a proxy in NEXTRMNL_TRUSTED_PROXIES (rightmost value
    # of the last line), never the header of just any sender. Behind a proxy nextrmnl was not told about: "on".
    return request_is_https(request)


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


def account_view(db: DbSession, account: Account, token: str | None = None) -> dict[str, Any]:
    """The account as the interface sees it. ``token`` is the session cookie of the account itself when it asks
    about itself: whether it must set up a second factor depends on how that session began."""
    return {
        "id": account.id,
        "name": account.name,
        "role": account.role,
        "sign_in": account.sign_in,
        "email": account.email,
        "two_factor": totp.has_second_factor(db, account),
        #: Which kinds: the app and the number of passkeys.
        "totp": bool(account.totp_secret_enc),
        "passkeys": passkeys.count(db, account.id),
        "two_factor_recovery_left": len(totp.load_recovery(account.totp_recovery))
        if totp.has_second_factor(db, account)
        else 0,
        "second_factor_setup_required": totp.setup_required(db, account, token),
        #: How many providers the account is linked to; which ones the account page asks ``/api/oidc/me``.
        "oidc_links": oidc_store.link_count(db, account.id),
        #: Providers the operator unlinked it from, until the account says it saw it.
        "oidc_unlinked": oidc_store.unlinked_notices(account),
        "vault": "unset" if not account.vault_ready else ("open" if vault.is_open(account.id, token) else "locked"),
        "prefs": account.prefs or {},
        "created_at": account.created_at.isoformat(),
        "last_seen_at": account.last_seen_at.isoformat() if account.last_seen_at else None,
        "expires_at": account.expires_at.isoformat() if account.expires_at else None,
    }


def sign_in_response(
    db: DbSession, request: Request, response: Response, account: Account, *, vault_key: bytes | None = None
) -> dict[str, Any]:
    """Starts the browser session. ``vault_key``: unwrapped from the password given for this sign-in; the vault
    opens in this session and in no other."""
    token = start_session(db, account, client_ip(request), request.headers.get("user-agent", ""))
    if vault_key is not None and account.vault_ready:
        vault.open_with_key(account, token, vault_key, int(settings_service.get(db, "vault_lock_minutes")))
    _set_cookie(response, request, token)
    return account_view(db, account, token)


def password_vault_key(account: Account, password: str) -> bytes | None:
    """The vault key while the password is at hand, or None: no vault yet, or it does not open with this password
    (then the vault stays locked and asks later)."""
    if not account.vault_ready:
        return None
    try:
        return vault.unwrap_key(account, password)
    except Exception:  # noqa: BLE001
        return None


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
    return sign_in_response(db, request, response, account, vault_key=password_vault_key(account, payload.password))


@router.post("/auth/login", summary="Sign in with name and password")
def login(payload: LoginIn, request: Request, response: Response, db: DbSession) -> dict[str, Any]:
    check_csrf(request)
    # The brake first, then the password, then the rules: a name is verified (or, for an unknown one, a dummy
    # hash takes the same time) before any answer differs, so neither the reply nor its timing tells which
    # names exist.
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
    if not settings_service.get(db, "password_login") and account.role != "operator":
        # The operator keeps the password as the emergency exit even when password sign-in is off.
        raise fehler("password_login_off", "Sign-in with a password is turned off.", 403)
    if totp.has_second_factor(db, account):
        # Nothing opens yet. The vault key is unwrapped now, while the password is at hand, and parked until the
        # code step; the browser gets a short-lived cookie that names the parked sign-in and nothing else.
        _set_pending_cookie(
            response, request, totp.start_pending(account.id, password_vault_key(account, payload.password))
        )
        logger.info("Password accepted, second factor pending account=%s", account.name)
        return {"second_factor": True, "methods": totp.methods(db, account)}
    accounts.note_success(db, account)
    # The password was just given, so the vault opens along with the session, in this browser only.
    return sign_in_response(db, request, response, account, vault_key=password_vault_key(account, payload.password))


def end_ssh_sessions(account_id: int, detail: str) -> None:
    """Cuts the live terminals of an account when its sign-in ends. The SSH part is optional at import time."""
    try:
        from ..services import ssh
    except ImportError:  # pragma: no cover
        return
    ssh.close_account_sessions(account_id, detail)


@router.post("/auth/logout", status_code=204, summary="Sign out in this browser")
def logout(request: Request, response: Response, db: DbSession) -> None:
    check_csrf(request)
    token = request.cookies.get(SESSION_COOKIE)
    account = None
    if token:
        from ..security import session_account

        account = session_account(db, token)
    # Ending the session locks its vault; other browsers of the account keep theirs.
    end_session(db, token)
    if account is not None:
        end_ssh_sessions(account.id, "signed_out")
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.post("/auth/logout-all", status_code=204, summary="Sign out every other browser of the own account")
def logout_everywhere(request: Request, account: CurrentAccount, db: DbSession) -> None:
    end_all_sessions(db, account.id, except_token=request.cookies.get(SESSION_COOKIE))
    end_ssh_sessions(account.id, "signed_out")
    logger.info("All other sessions ended by their owner account=%s", account.name)


@router.get("/auth/methods", summary="How one signs in here: password, and which providers (no sign-in needed)")
def sign_in_methods(db: DbSession) -> dict[str, Any]:
    # Public: the sign-in page asks before anybody is signed in. Only the buttons and whether the form is there.
    listed = providers.public_list(oidc_store.SqlStore(db))
    return {"password": bool(settings_service.get(db, "password_login")), "providers": listed}


@router.get("/auth/login/pending", summary="The second step waiting in this browser, if any")
def pending_sign_in(request: Request, db: DbSession) -> dict[str, Any]:
    """After a provider that is not trusted with the second factor the browser lands on ``/login?step=code``; the
    page asks here what the step offers. Only this browser's own waiting sign-in, by its cookie."""
    waiting = totp.get_pending(request.cookies.get(PENDING_COOKIE))
    account = db.get(Account, waiting.account_id) if waiting is not None else None
    if account is None:
        raise fehler("second_factor_expired", "Start again with your password.", 401)
    return {"name": account.name, "methods": totp.methods(db, account)}


@router.delete("/auth/oidc-notice", status_code=204, summary="The account saw that the operator unlinked it")
def seen_unlinked(account: CurrentAccount, db: DbSession) -> None:
    account.oidc_unlinked = ""
    db.commit()


@router.get("/auth/me", summary="The signed-in account")
def me(request: Request, account: CurrentAccount, db: DbSession) -> dict[str, Any]:
    return account_view(db, account, request.cookies.get(SESSION_COOKIE))


@router.put("/auth/password", status_code=204, summary="Change the own password")
def change_password(payload: PasswordChangeIn, request: Request, account: CurrentAccount, db: DbSession) -> None:
    if account.sign_in != SIGN_IN_PASSWORD:
        raise fehler("oidc_account", "This account signs in through OIDC.", 409)
    _check_password(payload.new)
    reauth_guard(request, account)
    try:
        accounts.change_password(db, account, payload.current, payload.new)
    except AccountError as error:
        if error.code == "wrong_password":
            reauth_failed(request, db, account)
        raise _raise(error) from error
    reauth_succeeded(request, db, account)
    # Other browsers must sign in again; this one stays. Their terminals and open vaults end with them.
    token = request.cookies.get(SESSION_COOKIE)
    end_all_sessions(db, account.id, except_token=token)
    end_ssh_sessions(account.id, "signed_out")
    vault.unlock(account, token, payload.new, int(settings_service.get(db, "vault_lock_minutes")))


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

    linked = oidc_store.linked_providers(db)
    return [
        {**account_view(db, row), "providers": linked.get(row.id, [])}
        for row in db.scalars(select(Account).order_by(Account.created_at))
    ]


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
    end_ssh_sessions(row.id, "signed_out")
    db.delete(row)
    # The links of the account go with it; the way back to the version before the list and its fingerprint must see
    # that in the same commit, or the next start takes the change for the older version's (a backup for nothing).
    oidc_store.mirror_legacy(db)
    db.commit()


@router.post("/accounts/{account_id}/sign-out", status_code=204, summary="Operator: end every session of an account")
def sign_out_account(account_id: int, operator: OperatorAccount, db: DbSession) -> None:
    row = db.get(Account, account_id)
    if row is None:
        raise fehler("not_found", "Account not found.", 404)
    end_all_sessions(db, row.id)
    vault.lock(row.id)
    end_ssh_sessions(row.id, "signed_out")
    logger.warning("All sessions of account=%s ended by operator=%s", row.name, operator.name)


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
    if row.role == "operator":
        # An operator does not run out.
        row.expires_at = None
    db.commit()
    return account_view(db, row)


@router.put("/accounts/{account_id}/expiry", summary="Operator: when a member account ends, or no end")
def set_expiry(account_id: int, payload: ExpiryIn, operator: OperatorAccount, db: DbSession) -> dict[str, Any]:
    row = db.get(Account, account_id)
    if row is None:
        raise fehler("not_found", "Account not found.", 404)
    if row.role == "operator":
        raise fehler("operator_cannot_expire", "An operator account does not expire.", 422)
    if payload.expires_at is not None and payload.expires_at.tzinfo is None:
        raise fehler("invalid_input", "Give the end with a time zone.", 422)
    row.expires_at = payload.expires_at
    db.commit()
    if row.expired:
        # Ended now: every browser of it is out, and its terminals close at once rather than within the watch.
        end_all_sessions(db, row.id)
        end_ssh_sessions(row.id, "signed_out")
    logger.info(
        "Account expiry set name=%s expires_at=%s by=%s",
        row.name,
        row.expires_at.isoformat() if row.expires_at else "-",
        operator.name,
    )
    return account_view(db, row)


@router.get("/accounts/invites", summary="Open invitations")
def list_invites(operator: OperatorAccount, db: DbSession) -> list[dict[str, Any]]:
    return [
        {
            "id": row.id,
            "name": row.name,
            "role": row.role,
            "expires_at": row.expires_at.isoformat(),
            "account_expires_at": row.account_expires_at.isoformat() if row.account_expires_at else None,
        }
        for row in accounts.list_invites(db)
    ]


@router.post("/accounts/invites", status_code=201, summary="Invite an account; the link is shown once")
def create_invite(payload: InviteIn, request: Request, operator: OperatorAccount, db: DbSession) -> dict[str, Any]:
    try:
        if payload.account_expires_at is not None and payload.account_expires_at.tzinfo is None:
            raise AccountError("invalid_input", "Give the end with a time zone.", 422)
        invite, token = accounts.create_invite(db, operator, payload.name, payload.role, payload.account_expires_at)
    except AccountError as error:
        raise _raise(error) from error
    base = settings_service.public_url(db) or request_base(request).rstrip("/")
    return {
        "id": invite.id,
        "name": invite.name,
        "role": invite.role,
        "expires_at": invite.expires_at.isoformat(),
        "account_expires_at": invite.account_expires_at.isoformat() if invite.account_expires_at else None,
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
    return sign_in_response(db, request, response, account, vault_key=password_vault_key(account, payload.password))

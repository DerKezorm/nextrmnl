"""Accounts: the first one is the operator, the others come by invitation or through OIDC."""

from __future__ import annotations

import logging
import re
import secrets
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import MEMBER, OPERATOR, ROLES, SIGN_IN_OIDC, SIGN_IN_PASSWORD, Account, Invite, utcnow
from ..security import LOCK_MINUTES, MAX_FAILURES, hash_password, hash_token, verify_password
from . import vault

logger = logging.getLogger("nextrmnl.auth")

NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
INVITE_DAYS = 7


class AccountError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def count(db: Session) -> int:
    return int(db.scalar(select(func.count()).select_from(Account)) or 0)


def by_name(db: Session, name: str) -> Account | None:
    return db.scalar(select(Account).where(Account.name == name.strip().lower()))


def check_name(db: Session, name: str) -> str:
    cleaned = name.strip().lower()
    if not NAME_PATTERN.match(cleaned):
        raise AccountError("invalid_name", "Use 2 to 64 letters, digits, dots, dashes or underscores.", 422)
    if by_name(db, cleaned) is not None:
        raise AccountError("name_taken", "This name is already taken.", 409)
    return cleaned


def create_with_password(db: Session, name: str, password: str, role: str = MEMBER) -> Account:
    if role not in ROLES:
        raise ValueError("unknown role")
    account = Account(
        name=check_name(db, name), role=role, sign_in=SIGN_IN_PASSWORD, password_hash=hash_password(password)
    )
    db.add(account)
    db.commit()
    vault.set_up(db, account, password)
    logger.info("Account created name=%s role=%s sign_in=password", account.name, role)
    return account


def create_operator(db: Session, name: str, password: str) -> Account:
    if count(db) > 0:
        raise AccountError("already_set_up", "nextrmnl is already set up.", 409)
    return create_with_password(db, name, password, OPERATOR)


def create_oidc(db: Session, name: str, subject: str, email: str) -> Account:
    base = re.sub(r"[^a-z0-9._-]", "-", name.strip().lower()) or "user"
    candidate = base[:60]
    suffix = 1
    while by_name(db, candidate) is not None:
        suffix += 1
        candidate = f"{base[:58]}-{suffix}"
    account = Account(name=candidate, role=MEMBER, sign_in=SIGN_IN_OIDC, oidc_subject=subject, email=email)
    db.add(account)
    db.commit()
    logger.info("Account created name=%s role=%s sign_in=oidc", account.name, MEMBER)
    return account


def authenticate(db: Session, name: str, password: str) -> Account:
    """Checks name and password; counts failures and locks the account after too many."""
    account = by_name(db, name)
    now = utcnow()
    if account is None or account.sign_in != SIGN_IN_PASSWORD:
        # Same answer as for a wrong password: a name must not be guessable.
        logger.warning("Sign-in failed for unknown account %r", name.strip().lower()[:64])
        raise AccountError("wrong_credentials", "Name or password is wrong.", 401)
    if account.locked_until is not None and account.locked_until > now:
        wait = int((account.locked_until - now).total_seconds()) + 1
        logger.warning("Sign-in refused, account locked name=%s wait=%ss", account.name, wait)
        raise AccountError("account_locked", "Too many failed sign-ins. Try again later.", 429)
    if not verify_password(password, account.password_hash):
        account.failed_logins += 1
        if account.failed_logins >= MAX_FAILURES:
            account.locked_until = now + timedelta(minutes=LOCK_MINUTES)
            account.failed_logins = 0
            logger.warning(
                "Account locked after %s failures name=%s minutes=%s", MAX_FAILURES, account.name, LOCK_MINUTES
            )
        else:
            logger.warning(
                "Sign-in failed name=%s (%s of %s before lockout)", account.name, account.failed_logins, MAX_FAILURES
            )
        db.commit()
        raise AccountError("wrong_credentials", "Name or password is wrong.", 401)
    account.failed_logins = 0
    account.locked_until = None
    account.last_seen_at = now
    db.commit()
    return account


def change_password(db: Session, account: Account, current: str, new: str) -> None:
    if not verify_password(current, account.password_hash):
        raise AccountError("wrong_password", "The current password is wrong.", 401)
    account.password_hash = hash_password(new)
    db.commit()
    if account.vault_ready:
        vault.rewrap(db, account, current, new)
    logger.info("Password changed name=%s", account.name)


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------


def create_invite(db: Session, by: Account, name: str, role: str) -> tuple[Invite, str]:
    if role not in ROLES:
        raise AccountError("invalid_role", "Unknown role.", 422)
    token = secrets.token_urlsafe(24)
    invite = Invite(
        token_hash=hash_token(token),
        name=name.strip().lower()[:64],
        role=role,
        created_by=by.id,
        expires_at=utcnow() + timedelta(days=INVITE_DAYS),
    )
    db.add(invite)
    db.commit()
    logger.info("Invite created by=%s role=%s", by.name, role)
    return invite, token


def find_invite(db: Session, token: str) -> Invite | None:
    invite = db.scalar(select(Invite).where(Invite.token_hash == hash_token(token)))
    if invite is None or invite.expires_at <= utcnow():
        return None
    return invite


def accept_invite(db: Session, token: str, name: str, password: str) -> Account:
    invite = find_invite(db, token)
    if invite is None:
        raise AccountError("invite_invalid", "This invitation is not valid any more.", 404)
    account = create_with_password(db, name, password, invite.role)
    db.delete(invite)
    db.commit()
    logger.info("Invite accepted name=%s", account.name)
    return account


def list_invites(db: Session) -> list[Invite]:
    return list(db.scalars(select(Invite).where(Invite.expires_at > utcnow()).order_by(Invite.created_at)))


def delete_invite(db: Session, invite_id: int) -> bool:
    invite = db.get(Invite, invite_id)
    if invite is None:
        return False
    db.delete(invite)
    db.commit()
    return True

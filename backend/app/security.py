"""Password hashing, browser sessions and the brake against guessing."""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import Account, AuthSession, utcnow

SESSION_COOKIE = "nextrmnl_session"
#: The header every changing request must carry. A foreign page cannot set it without a preflight the browser
#: refuses; without it any page opened in the home network could act on the signed-in account.
CSRF_HEADER = "x-requested-by"
CSRF_VALUE = "nextrmnl"
MIN_PASSWORD = 12
#: After this many failures in a row an account waits a quarter of an hour. Not configurable on purpose.
MAX_FAILURES = 10
LOCK_MINUTES = 15


def _hasher() -> PasswordHasher:
    settings = get_settings()
    return PasswordHasher(
        time_cost=settings.argon2_time, memory_cost=settings.argon2_memory_kib, parallelism=settings.argon2_parallelism
    )


def hash_password(password: str) -> str:
    return _hasher().hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        return _hasher().verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def start_session(db: Session, account: Account, ip: str, user_agent: str) -> str:
    """Creates a browser session and returns the token for the cookie."""
    token = new_token()
    db.add(
        AuthSession(
            token_hash=hash_token(token),
            account_id=account.id,
            expires_at=utcnow() + timedelta(days=get_settings().session_days),
            ip=ip[:64],
            user_agent=user_agent[:255],
        )
    )
    account.last_seen_at = utcnow()
    db.commit()
    return token


def session_account(db: Session, token: str | None) -> Account | None:
    if not token:
        return None
    session = db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(token)))
    if session is None:
        return None
    now = utcnow()
    if session.expires_at <= now:
        db.delete(session)
        db.commit()
        return None
    account = db.get(Account, session.account_id)
    if account is None:
        return None
    # Only every few minutes: the frontend asks often.
    if (now - session.last_seen_at).total_seconds() > 300:
        session.last_seen_at = now
        account.last_seen_at = now
        db.commit()
    return account


def end_session(db: Session, token: str | None) -> None:
    if token:
        db.execute(delete(AuthSession).where(AuthSession.token_hash == hash_token(token)))
        db.commit()


def end_all_sessions(db: Session, account_id: int, except_token: str | None = None) -> None:
    statement = delete(AuthSession).where(AuthSession.account_id == account_id)
    if except_token:
        statement = statement.where(AuthSession.token_hash != hash_token(except_token))
    db.execute(statement)
    db.commit()


def purge_sessions(db: Session) -> int:
    result = db.execute(delete(AuthSession).where(AuthSession.expires_at <= utcnow()))
    db.commit()
    return result.rowcount or 0


class Brake:
    """Waiting time after wrong passwords, per sender. In memory only; the per-account lock is in the database."""

    FREE = 5
    MAX_WAIT = LOCK_MINUTES * 60

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._fails: dict[str, tuple[int, float]] = {}

    def wait_seconds(self, key: str) -> int:
        with self._lock:
            count, last = self._fails.get(key, (0, 0.0))
        if count < self.FREE:
            return 0
        wait = min(self.MAX_WAIT, 2 ** (count - self.FREE) * 5)
        remaining = last + wait - time.monotonic()
        return max(0, int(remaining + 0.999))

    def failed(self, key: str) -> None:
        with self._lock:
            count, _ = self._fails.get(key, (0, 0.0))
            self._fails[key] = (count + 1, time.monotonic())

    def succeeded(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)


brake = Brake()

"""The vault: one per account, open only in a browser session that gave the account's password.

An open vault is nothing but its unwrapped key in this process's memory, held for one browser session (by the hash
of its token, never the token) with an idle timer. Another browser of the same account, or a session that came
through a sign-in provider, does not share it: each session opens the vault with the password itself. Every access
extends the timer; after ``vault_lock_minutes`` without use the key is forgotten and the vault is locked for that
session. Signing out forgets it for that session; a new password, a deleted or signed-out account forgets it for
every other session. A restart of the process locks every vault.

Nothing in here logs a key, a password or a passphrase. Names and counts only.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

import asyncssh
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import crypto
from ..db import SessionLocal
from ..models import KEY_TYPES, Account, AuthSession, ConnectionShare, VaultKey, VaultPassword, active_share, utcnow
from ..security import hash_token

logger = logging.getLogger("nextrmnl.vault")


class VaultLocked(Exception):
    pass


class VaultNotSetUp(Exception):
    pass


@dataclass
class Opened:
    account_id: int
    key: bytes
    until: datetime
    idle: timedelta


#: Open vaults by the hash of their browser session's token.
_open: dict[str, Opened] = {}
_lock = threading.Lock()


def _slot(token: str | None) -> str | None:
    return hash_token(token) if token else None


def _get(account_id: int, token: str | None, now: datetime) -> Opened | None:
    """The open vault of this browser session, if it belongs to the account and has not run out. A run-out one is
    forgotten. The caller holds ``_lock``."""
    slot = _slot(token)
    if slot is None:
        return None
    opened = _open.get(slot)
    if opened is None or opened.account_id != account_id:
        return None
    if opened.until <= now:
        del _open[slot]
        logger.info("Vault locked after idle time account_id=%s", account_id)
        return None
    return opened


def is_open(account_id: int, token: str | None) -> bool:
    """Whether the vault is open in the browser session of this token."""
    with _lock:
        return _get(account_id, token, utcnow()) is not None


def key_for(account_id: int, token: str | None) -> bytes:
    """The vault key of the browser session of this token, or ``VaultLocked``. Extends the idle timer."""
    with _lock:
        now = utcnow()
        opened = _get(account_id, token, now)
        if opened is None:
            raise VaultLocked()
        opened.until = now + opened.idle
        return opened.key


def unlock(account: Account, token: str | None, password: str, idle_minutes: int) -> None:
    """Opens the vault in the browser session of this token with the account's password; ``crypto.WrongPassword``
    if it is not the one."""
    open_with_key(account, token, unwrap_key(account, password), idle_minutes)


def unwrap_key(account: Account, password: str) -> bytes:
    """The vault key for a password, without opening the vault; ``crypto.WrongPassword`` if it is not the one."""
    if not account.vault_ready:
        raise VaultNotSetUp()
    return crypto.unwrap(password, account.vault_salt or b"", account.vault_wrapped or b"", account.vault_kdf or "")


def open_with_key(account: Account, token: str | None, key: bytes, idle_minutes: int) -> None:
    """Opens the vault in the browser session of this token with a key unwrapped earlier, while the password was at
    hand (the first step of a sign-in with a second factor, or just before the session was started)."""
    slot = _slot(token)
    if slot is None:
        raise VaultLocked()
    idle = timedelta(minutes=max(1, idle_minutes))
    with _lock:
        _open[slot] = Opened(account_id=account.id, key=key, until=utcnow() + idle, idle=idle)
    # Unwrapping takes a while; the session may have ended meanwhile (signed out in another tab, by the operator),
    # and its end found nothing to lock yet. Checked after the key is in, on a fresh connection: an end that
    # commits later locks the slot itself, one that committed earlier is seen here.
    with SessionLocal() as check:
        alive = check.scalar(select(AuthSession.id).where(AuthSession.token_hash == slot)) is not None
    if not alive:
        lock_sessions([slot])
        return
    logger.info("Vault unlocked account=%s", account.name)


def _forget(match: Callable[[str, Opened], bool]) -> list[Opened]:
    with _lock:
        slots = [slot for slot, opened in _open.items() if match(slot, opened)]
        return [_open.pop(slot) for slot in slots]


def lock(account_id: int) -> None:
    """Locks the vault in every browser session of the account."""
    gone = _forget(lambda _slot, opened: opened.account_id == account_id)
    if gone:
        logger.info("Vault locked account_id=%s sessions=%s", account_id, len(gone))


def lock_others(account_id: int, token: str | None) -> None:
    """Locks the vault in every browser session of the account but the one of this token."""
    keep = _slot(token)
    gone = _forget(lambda slot, opened: opened.account_id == account_id and slot != keep)
    if gone:
        logger.info("Vault locked account_id=%s sessions=%s", account_id, len(gone))


def lock_session(token: str | None) -> None:
    """Locks the vault in the browser session of this token only."""
    slot = _slot(token)
    if slot is not None:
        lock_sessions([slot])


def lock_sessions(token_hashes: Iterable[str]) -> None:
    """Locks the vault in the browser sessions of these token hashes (sessions that ended, or one that asked)."""
    ended = set(token_hashes)
    for opened in _forget(lambda slot, _opened: slot in ended):
        logger.info("Vault locked account_id=%s", opened.account_id)


def lock_all() -> None:
    with _lock:
        _open.clear()


def sweep() -> int:
    """Forget keys whose idle time has passed."""
    now = utcnow()
    expired = _forget(lambda _slot, opened: opened.until <= now)
    for opened in expired:
        logger.info("Vault locked after idle time account_id=%s", opened.account_id)
    return len(expired)


def set_up(db: Session, account: Account, password: str) -> None:
    """Creates the vault for an account. For password accounts at sign-up, for OIDC accounts on first use."""
    salt, wrapped, _key = crypto.new_vault(password)
    account.vault_salt = salt
    account.vault_wrapped = wrapped
    account.vault_kdf = crypto.kdf_params_now()
    db.commit()
    logger.info("Vault created account=%s", account.name)


def rewrap(db: Session, account: Account, old_password: str, new_password: str) -> None:
    key = unwrap_key(account, old_password)
    account.vault_salt, account.vault_wrapped = crypto.wrap(key, new_password)
    account.vault_kdf = crypto.kdf_params_now()
    db.commit()
    logger.info("Vault rewrapped account=%s", account.name)


def reset(db: Session, account: Account, new_password: str) -> None:
    """Forgotten password: a fresh, empty vault. Every entry is lost, on purpose; nobody could read it anyway."""
    db.execute(VaultKey.__table__.delete().where(VaultKey.account_id == account.id))
    db.execute(VaultPassword.__table__.delete().where(VaultPassword.account_id == account.id))
    lock(account.id)
    set_up(db, account, new_password)
    logger.warning("Vault reset, entries discarded account=%s", account.name)


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedKey:
    key_type: str
    bits: int
    fingerprint: str
    public_key: str
    has_passphrase: bool


def _describe(key: asyncssh.SSHKey) -> tuple[str, int]:
    algorithm = key.get_algorithm()
    if algorithm == "ssh-ed25519":
        return "ed25519", 256
    if algorithm.startswith("ecdsa"):
        return "ecdsa", int(algorithm.rsplit("p", 1)[-1] or 0)
    if algorithm == "ssh-rsa":
        return "rsa", key.get_key_size() if hasattr(key, "get_key_size") else 0
    return algorithm, 0


def parse_private_key(text: str, passphrase: str | None = None) -> ParsedKey | None:
    """Reads a private key in OpenSSH or PEM format. Returns None if it is protected and no passphrase is given."""
    try:
        key = asyncssh.import_private_key(text, passphrase or None)
    except (asyncssh.KeyImportError, asyncssh.KeyEncryptionError) as error:
        if not passphrase and "assphrase" in str(error):
            return None
        raise
    key_type, bits = _describe(key)
    return ParsedKey(
        key_type=key_type,
        bits=bits,
        fingerprint=key.get_fingerprint(),
        public_key=key.export_public_key("openssh").decode("ascii").strip(),
        has_passphrase=bool(passphrase),
    )


def generate_key(kind: str, passphrase: str | None = None, comment: str = "") -> tuple[str, ParsedKey]:
    """Creates a new key pair. Returns the private key text (encrypted with the passphrase if given)."""
    if kind not in KEY_TYPES:
        raise ValueError("unknown key type")
    if kind == "ed25519":
        key = asyncssh.generate_private_key("ssh-ed25519", comment=comment)
    elif kind == "rsa":
        key = asyncssh.generate_private_key("ssh-rsa", key_size=4096, comment=comment)
    else:
        key = asyncssh.generate_private_key("ecdsa-sha2-nistp256", comment=comment)
    text = key.export_private_key("openssh", passphrase or None).decode("ascii")
    key_type, bits = _describe(key)
    return text, ParsedKey(
        key_type=key_type,
        bits=bits,
        fingerprint=key.get_fingerprint(),
        public_key=key.export_public_key("openssh").decode("ascii").strip(),
        has_passphrase=bool(passphrase),
    )


def list_keys(db: Session, account_id: int) -> list[VaultKey]:
    return list(db.scalars(select(VaultKey).where(VaultKey.account_id == account_id).order_by(VaultKey.name)))


def add_key(
    db: Session, account: Account, token: str | None, name: str, private_key_text: str, parsed: ParsedKey
) -> VaultKey:
    key = key_for(account.id, token)
    row = VaultKey(
        account_id=account.id,
        name=name,
        key_type=parsed.key_type,
        bits=parsed.bits,
        fingerprint=parsed.fingerprint,
        public_key=parsed.public_key,
        private_key_enc=crypto.encrypt_entry(key, private_key_text.encode("utf-8")),
        has_passphrase=parsed.has_passphrase,
    )
    db.add(row)
    db.commit()
    logger.info("Key added account=%s name=%s type=%s", account.name, name, parsed.key_type)
    return row


def private_key_text(db: Session, account_id: int, token: str | None, key_id: int) -> tuple[VaultKey, str]:
    row = db.get(VaultKey, key_id)
    if row is None or row.account_id != account_id:
        raise LookupError("key not found")
    return row, crypto.decrypt_entry(key_for(account_id, token), row.private_key_enc).decode("utf-8")


def delete_key(db: Session, account: Account, key_id: int) -> bool:
    row = db.get(VaultKey, key_id)
    if row is None or row.account_id != account.id:
        return False
    db.delete(row)
    db.commit()
    logger.info("Key deleted account=%s name=%s", account.name, row.name)
    return True


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------


def list_passwords(db: Session, account_id: int) -> list[VaultPassword]:
    return list(db.scalars(select(VaultPassword).where(VaultPassword.account_id == account_id)))


def share_of(db: Session, account_id: int, connection_id: int) -> ConnectionShare | None:
    """The member's share, if it gives access right now; a run-out share is as good as none."""
    return active_share(db, connection_id, account_id)


def _password_row(db: Session, account_id: int, connection_id: int) -> VaultPassword | None:
    return db.scalar(
        select(VaultPassword).where(
            VaultPassword.account_id == account_id, VaultPassword.connection_id == connection_id
        )
    )


def set_password(
    db: Session,
    account: Account,
    token: str | None,
    connection_id: int,
    password: str,
    *,
    host: str = "",
    port: int = 0,
    user: str = "",
) -> VaultPassword:
    """Stores the password for the connection as it points now: host, port and user are recorded with it."""
    key = key_for(account.id, token)
    row = _password_row(db, account.id, connection_id)
    if row is None:
        row = VaultPassword(account_id=account.id, connection_id=connection_id, password_enc=b"")
        db.add(row)
    row.password_enc = crypto.encrypt_entry(key, password.encode("utf-8"))
    row.changed_at = utcnow()
    row.host, row.port, row.user = host.strip().lower(), port, user.strip()
    db.commit()
    logger.info("Password stored account=%s connection_id=%s", account.name, connection_id)
    return row


def get_password(
    db: Session,
    account_id: int,
    token: str | None,
    connection_id: int,
    *,
    host: str | None = None,
    port: int = 0,
    user: str = "",
) -> str | None:
    """The stored password, but only for the target it was stored for. Somebody else's shared connection can be
    pointed at another machine at any time; the member's password must not follow it there."""
    row = _password_row(db, account_id, connection_id)
    if row is None:
        return None
    if host is not None and row.host and (row.host, row.port, row.user) != (host.strip().lower(), port, user.strip()):
        logger.warning(
            "Stored password not used, the connection points elsewhere now account_id=%s connection_id=%s",
            account_id,
            connection_id,
        )
        return None
    return crypto.decrypt_entry(key_for(account_id, token), row.password_enc).decode("utf-8")


def delete_password(db: Session, account: Account, connection_id: int) -> bool:
    row = _password_row(db, account.id, connection_id)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    logger.info("Password removed account=%s connection_id=%s", account.name, connection_id)
    return True

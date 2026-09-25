"""The vault: one per account, open only while the account's password has been given.

An open vault is nothing but its unwrapped key in this process's memory, with an idle timer. Every access
extends the timer; after ``vault_lock_minutes`` without use the key is forgotten and the vault is locked.
A restart of the process locks every vault.

Nothing in here logs a key, a password or a passphrase. Names and counts only.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

import asyncssh
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import crypto
from ..models import KEY_TYPES, Account, ConnectionShare, VaultKey, VaultPassword, utcnow

logger = logging.getLogger("nextrmnl.vault")


class VaultLocked(Exception):
    pass


class VaultNotSetUp(Exception):
    pass


@dataclass
class Opened:
    key: bytes
    until: datetime
    idle: timedelta


_open: dict[int, Opened] = {}
_lock = threading.Lock()


def is_open(account_id: int) -> bool:
    with _lock:
        opened = _open.get(account_id)
        if opened is None:
            return False
        if opened.until <= utcnow():
            del _open[account_id]
            logger.info("Vault locked after idle time account_id=%s", account_id)
            return False
        return True


def key_for(account_id: int) -> bytes:
    """The vault key, or ``VaultLocked``. Extends the idle timer."""
    with _lock:
        opened = _open.get(account_id)
        now = utcnow()
        if opened is None or opened.until <= now:
            _open.pop(account_id, None)
            raise VaultLocked()
        opened.until = now + opened.idle
        return opened.key


def unlock(account: Account, password: str, idle_minutes: int) -> None:
    """Opens the vault with the account's password; ``crypto.WrongPassword`` if it is not the one."""
    key = unwrap_key(account, password)
    idle = timedelta(minutes=max(1, idle_minutes))
    with _lock:
        _open[account.id] = Opened(key=key, until=utcnow() + idle, idle=idle)
    logger.info("Vault unlocked account=%s", account.name)


def unwrap_key(account: Account, password: str) -> bytes:
    """The vault key for a password, without opening the vault; ``crypto.WrongPassword`` if it is not the one."""
    if not account.vault_ready:
        raise VaultNotSetUp()
    return crypto.unwrap(password, account.vault_salt or b"", account.vault_wrapped or b"", account.vault_kdf or "")


def open_with_key(account: Account, key: bytes, idle_minutes: int) -> None:
    """Opens the vault with a key unwrapped earlier: the second step of a sign-in with a second factor."""
    idle = timedelta(minutes=max(1, idle_minutes))
    with _lock:
        _open[account.id] = Opened(key=key, until=utcnow() + idle, idle=idle)
    logger.info("Vault unlocked account=%s", account.name)


def lock(account_id: int) -> None:
    with _lock:
        removed = _open.pop(account_id, None) is not None
    if removed:
        logger.info("Vault locked account_id=%s", account_id)


def lock_all() -> None:
    with _lock:
        _open.clear()


def sweep() -> int:
    """Forget keys whose idle time has passed."""
    now = utcnow()
    with _lock:
        expired = [account_id for account_id, opened in _open.items() if opened.until <= now]
        for account_id in expired:
            del _open[account_id]
    for account_id in expired:
        logger.info("Vault locked after idle time account_id=%s", account_id)
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


def add_key(db: Session, account: Account, name: str, private_key_text: str, parsed: ParsedKey) -> VaultKey:
    key = key_for(account.id)
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


def private_key_text(db: Session, account_id: int, key_id: int) -> tuple[VaultKey, str]:
    row = db.get(VaultKey, key_id)
    if row is None or row.account_id != account_id:
        raise LookupError("key not found")
    return row, crypto.decrypt_entry(key_for(account_id), row.private_key_enc).decode("utf-8")


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
    return db.get(ConnectionShare, {"connection_id": connection_id, "account_id": account_id})


def _password_row(db: Session, account_id: int, connection_id: int) -> VaultPassword | None:
    return db.scalar(
        select(VaultPassword).where(
            VaultPassword.account_id == account_id, VaultPassword.connection_id == connection_id
        )
    )


def set_password(
    db: Session, account: Account, connection_id: int, password: str, *, host: str = "", port: int = 0, user: str = ""
) -> VaultPassword:
    """Stores the password for the connection as it points now: host, port and user are recorded with it."""
    key = key_for(account.id)
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
    db: Session, account_id: int, connection_id: int, *, host: str | None = None, port: int = 0, user: str = ""
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
    return crypto.decrypt_entry(key_for(account_id), row.password_enc).decode("utf-8")


def delete_password(db: Session, account: Account, connection_id: int) -> bool:
    row = _password_row(db, account.id, connection_id)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    logger.info("Password removed account=%s connection_id=%s", account.name, connection_id)
    return True

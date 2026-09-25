"""The own vault as a file: export it, look into a file, import one.

A backup of the server carries every vault, but sealed with keys nobody but the accounts can unwrap. Whoever
wants their keys and stored passwords in their own hands, or on a second nextrmnl, exports them as a
``.nextrmnl-vault`` file. The file is a small JSON document: a header in the clear (format, account, server,
when) and one AES-256-GCM ciphertext over the entries, with a key derived from the vault password by Argon2id and
a fresh salt per file.

Two rules keep it safe:

* **The vault password is asked again, even though the vault is open.** An open vault serves the session; it does
  not entitle whoever sits at the screen to carry the vault away. ``export`` proves the password against the
  wrapped vault key before it reads a single entry.
* **The Argon2 parameters are written into the file and read back from it**, not taken from this installation:
  the file must open on another nextrmnl with other costs, and on this one after the costs changed. A file
  demanding absurd costs is refused before any memory is spent.

On import everything is re-encrypted with the importing account's own vault key; the file's password is only
ever used to open the file.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from argon2.low_level import Type, hash_secret_raw
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import delete
from sqlalchemy.orm import Session

from .. import crypto
from ..config import get_settings
from ..models import Account, Connection, VaultKey, VaultPassword
from . import vault

logger = logging.getLogger("nextrmnl.vault")

FORMAT = "nextrmnl-vault"
VERSION = 1
SUFFIX = ".nextrmnl-vault"
AAD = b"nextrmnl-vault-file-v1"
NONCE_BYTES = 12
MERGE = "merge"
REPLACE = "replace"
MODES = (MERGE, REPLACE)
#: A vault file with a thousand keys is a few megabytes; anything beyond this is not one.
MAX_FILE = 16 * 1024 * 1024
#: Upper bounds for the Argon2 costs a file may ask for; more would be a way to exhaust the server. A file
#: written by nextrmnl carries this installation's costs (64 MiB, three passes by default); these bounds leave
#: room for a stronger installation without letting one upload pin a gigabyte.
MAX_TIME_COST = 10
MAX_MEMORY_KIB = 256 * 1024
MAX_PARALLELISM = 4
#: How many key derivations from uploaded files may run at once; each one costs the memory named in the file.
_KDF_SLOTS = threading.BoundedSemaphore(2)


class VaultFileError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(slots=True)
class Header:
    account: str
    server: str
    created: str
    keys: int
    passwords: int


@dataclass(slots=True)
class Payload:
    keys: list[dict[str, Any]]
    passwords: list[dict[str, Any]]


@dataclass(slots=True)
class ImportResult:
    added_keys: int = 0
    skipped_keys: int = 0
    added_passwords: int = 0
    skipped_passwords: int = 0


def _kdf_now(salt: bytes) -> dict[str, Any]:
    settings = get_settings()
    return {
        "algorithm": "argon2id",
        "time_cost": settings.argon2_time,
        "memory_kib": settings.argon2_memory_kib,
        "parallelism": settings.argon2_parallelism,
        "salt": base64.b64encode(salt).decode("ascii"),
    }


def _derive(password: str, salt: bytes, kdf: dict[str, Any]) -> bytes:
    """The same derivation as ``crypto.derive``, with the costs the file names instead of this installation's."""
    with _KDF_SLOTS:
        return hash_secret_raw(
            password.encode("utf-8"),
            salt,
            time_cost=int(kdf["time_cost"]),
            memory_cost=int(kdf["memory_kib"]),
            parallelism=int(kdf["parallelism"]),
            hash_len=crypto.KEY_BYTES,
            type=Type.ID,
        )


def _invalid(reason: str) -> VaultFileError:
    return VaultFileError("file_invalid", f"This is not a nextrmnl vault file: {reason}.", 422)


def _wrong_password() -> VaultFileError:
    return VaultFileError("wrong_password", "The password is wrong.", 401)


def _locked() -> VaultFileError:
    return VaultFileError("vault_locked", "The vault is locked. Enter your password to open it.", 423)


# --- Export ---------------------------------------------------------------------------------------------------- #


def prove_password(account: Account, password: str) -> None:
    """The vault password, checked against the wrapped vault key. Nothing is unlocked by it."""
    if not account.vault_ready:
        raise VaultFileError("vault_not_set_up", "This account has no vault yet.", 409)
    try:
        crypto.unwrap(password, account.vault_salt or b"", account.vault_wrapped or b"")
    except crypto.WrongPassword as error:
        raise _wrong_password() from error


def collect(db: Session, account: Account) -> Payload:
    """Every entry of the open vault in the clear, for sealing into a file. Never leaves this module unsealed."""
    try:
        key = vault.key_for(account.id)
    except vault.VaultLocked as error:
        raise _locked() from error
    keys = [
        {
            "name": row.name,
            "key_type": row.key_type,
            "bits": row.bits,
            "fingerprint": row.fingerprint,
            "public_key": row.public_key,
            "private_key": crypto.decrypt_entry(key, row.private_key_enc).decode("utf-8"),
            "has_passphrase": row.has_passphrase,
            "created_at": row.created_at.isoformat(),
        }
        for row in vault.list_keys(db, account.id)
    ]
    passwords = []
    for row in vault.list_passwords(db, account.id):
        connection = db.get(Connection, row.connection_id)
        if connection is None:
            continue
        if connection.owner_id != account.id and vault.share_of(db, account.id, connection.id) is None:
            # A share that was withdrawn: the password stays sealed, the connection's details are not the
            # account's to take along.
            continue
        passwords.append(
            {
                "connection": connection.name,
                "host": connection.host,
                "port": connection.port,
                "user": connection.user,
                "password": crypto.decrypt_entry(key, row.password_enc).decode("utf-8"),
            }
        )
    return Payload(keys=keys, passwords=passwords)


def seal(payload: Payload, password: str, account: str, server: str) -> bytes:
    salt = os.urandom(crypto.SALT_BYTES)
    kdf = _kdf_now(salt)
    nonce = os.urandom(NONCE_BYTES)
    plain = json.dumps({"keys": payload.keys, "passwords": payload.passwords}, ensure_ascii=False).encode("utf-8")
    ciphertext = AESGCM(_derive(password, salt, kdf)).encrypt(nonce, plain, AAD)
    document = {
        "format": FORMAT,
        "version": VERSION,
        "account": account,
        "server": server,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "kdf": kdf,
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }
    return json.dumps(document, indent=2).encode("utf-8")


def export(db: Session, account: Account, password: str, server: str) -> bytes:
    """The whole vault as a file. The vault must be open, and the password must be the vault password."""
    if not vault.is_open(account.id):
        raise _locked()
    prove_password(account, password)
    payload = collect(db, account)
    logger.info(
        "Vault exported account=%s key_count=%s password_count=%s",
        account.name,
        len(payload.keys),
        len(payload.passwords),
    )
    return seal(payload, password, account.name, server)


# --- Reading a file -------------------------------------------------------------------------------------------- #


def _b64(value: Any) -> bytes:
    if not isinstance(value, str):
        raise _invalid("a field is not text")
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise _invalid("a field is not base64") from error


def _kdf_of(document: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
    kdf = document.get("kdf")
    if not isinstance(kdf, dict) or kdf.get("algorithm") != "argon2id":
        raise _invalid("unknown key derivation")
    try:
        costs = {name: int(kdf[name]) for name in ("time_cost", "memory_kib", "parallelism")}
    except (KeyError, TypeError, ValueError) as error:
        raise _invalid("the key derivation lacks its costs") from error
    if not (
        1 <= costs["time_cost"] <= MAX_TIME_COST
        and 8 <= costs["memory_kib"] <= MAX_MEMORY_KIB
        and 1 <= costs["parallelism"] <= MAX_PARALLELISM
    ):
        raise _invalid("the key derivation costs are out of bounds")
    salt = _b64(kdf.get("salt"))
    if len(salt) < 8:
        raise _invalid("the salt is too short")
    return {**costs, "algorithm": "argon2id"}, salt


def read(raw: bytes, password: str) -> tuple[Header, Payload]:
    """Open a file with its password. ``file_invalid`` for anything that is not one, ``wrong_password`` when the
    ciphertext does not open."""
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise _invalid("not a JSON document") from error
    if not isinstance(document, dict) or document.get("format") != FORMAT:
        raise _invalid("wrong format")
    if document.get("version") != VERSION:
        raise _invalid("unknown version")
    kdf, salt = _kdf_of(document)
    nonce = _b64(document.get("nonce"))
    ciphertext = _b64(document.get("ciphertext"))
    if len(nonce) != NONCE_BYTES or len(ciphertext) < 16:
        raise _invalid("nonce or ciphertext have the wrong size")
    try:
        plain = AESGCM(_derive(password, salt, kdf)).decrypt(nonce, ciphertext, AAD)
    except InvalidTag as error:
        raise _wrong_password() from error
    try:
        content = json.loads(plain.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise _invalid("the content is not readable") from error
    keys = content.get("keys") if isinstance(content, dict) else None
    passwords = content.get("passwords") if isinstance(content, dict) else None
    if not isinstance(keys, list) or not isinstance(passwords, list):
        raise _invalid("the content lacks keys or passwords")
    if not all(isinstance(item, dict) for item in keys + passwords):
        raise _invalid("an entry is malformed")
    header = Header(
        account=str(document.get("account") or ""),
        server=str(document.get("server") or ""),
        created=str(document.get("created") or ""),
        keys=len(keys),
        passwords=len(passwords),
    )
    return header, Payload(keys=keys, passwords=passwords)


# --- Import ---------------------------------------------------------------------------------------------------- #


def _parsed(entry: dict[str, Any], text: str) -> vault.ParsedKey | None:
    """The public part of a key from the file, checked against the key text where that is possible."""
    if not text.lstrip().startswith("-----BEGIN") or "PRIVATE KEY-----" not in text:
        return None
    if not entry.get("has_passphrase"):
        try:
            return vault.parse_private_key(text)
        except Exception:  # noqa: BLE001 - whatever asyncssh dislikes about it, the key is skipped
            return None
    # Protected by its passphrase: nextrmnl cannot look inside; the file's description of it is all there is.
    try:
        return vault.ParsedKey(
            key_type=str(entry.get("key_type") or ""),
            bits=int(entry.get("bits") or 0),
            fingerprint=str(entry.get("fingerprint") or ""),
            public_key=str(entry.get("public_key") or ""),
            has_passphrase=True,
        )
    except (TypeError, ValueError):
        return None


def import_(db: Session, account: Account, payload: Payload, mode: str = MERGE) -> ImportResult:
    """Take the entries of a file into the own vault, re-encrypted with the own vault key.

    ``merge``: keys whose name exists are skipped; a password goes to the connection visible to the account with
    the same host, port and user, unless one is stored there already. ``replace``: the own keys and passwords go
    first.
    """
    from ..routers.connections import visible

    if mode not in MODES:
        raise VaultFileError("invalid_mode", "Choose merge or replace.", 422)
    try:
        vault.key_for(account.id)
    except vault.VaultLocked as error:
        raise _locked() from error
    result = ImportResult()
    if mode == REPLACE:
        db.execute(delete(VaultPassword).where(VaultPassword.account_id == account.id))
        db.execute(delete(VaultKey).where(VaultKey.account_id == account.id))
        db.commit()
        logger.warning("Vault entries replaced by an import account=%s", account.name)
    names = {row.name for row in vault.list_keys(db, account.id)}
    for entry in payload.keys:
        name = str(entry.get("name") or "").strip()[:64]
        text = entry.get("private_key")
        if not name or name in names or not isinstance(text, str):
            result.skipped_keys += 1
            continue
        parsed = _parsed(entry, text)
        if parsed is None:
            logger.warning("Vault import skipped an unreadable key account=%s name=%s", account.name, name)
            result.skipped_keys += 1
            continue
        try:
            vault.add_key(db, account, name, text, parsed)
        except vault.VaultLocked as error:
            raise _locked() from error
        names.add(name)
        result.added_keys += 1
    targets = {(row.host.lower(), row.port, row.user): row.id for row in visible(db, account)}
    stored = {row.connection_id for row in vault.list_passwords(db, account.id)}
    for entry in payload.passwords:
        secret_text = entry.get("password")
        try:
            target = (str(entry.get("host") or "").lower(), int(entry.get("port") or 0), str(entry.get("user") or ""))
        except (TypeError, ValueError):
            target = ("", 0, "")
        connection_id = targets.get(target)
        if connection_id is None or connection_id in stored or not isinstance(secret_text, str) or not secret_text:
            result.skipped_passwords += 1
            continue
        try:
            vault.set_password(db, account, connection_id, secret_text, host=target[0], port=target[1], user=target[2])
        except vault.VaultLocked as error:
            raise _locked() from error
        stored.add(connection_id)
        result.added_passwords += 1
    logger.info(
        "Vault imported account=%s mode=%s added_keys=%s skipped_keys=%s added_passwords=%s skipped_passwords=%s",
        account.name,
        mode,
        result.added_keys,
        result.skipped_keys,
        result.added_passwords,
        result.skipped_passwords,
    )
    return result

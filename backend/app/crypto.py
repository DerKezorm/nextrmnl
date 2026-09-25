"""The cryptography behind the vault and the server-side secrets.

Vault
-----
Every account has a random 32-byte **vault key**. It is stored *wrapped*: encrypted with a key derived from the
account's password by Argon2id (``derive``). Entries in the vault (private keys, passwords) are encrypted with
the vault key, AES-256-GCM with a fresh nonce each. Consequences:

* Changing the password only rewraps the vault key; the entries stay as they are.
* Nobody without the password gets in. Not the operator, not a backup, not a database dump.
* A wrong password does not decrypt to garbage; GCM's tag rejects it (``WrongPassword``).

Server secrets
--------------
The OIDC client secret and TOTP seeds must be readable by the server without any password. They are encrypted
with a key derived from ``secret.key``; that is why the key goes into backups.
"""

from __future__ import annotations

import hashlib
import os

from argon2.low_level import Type, hash_secret_raw
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import get_settings

KEY_BYTES = 32
SALT_BYTES = 16
NONCE_BYTES = 12
AAD_WRAP = b"nextrmnl-vault-key-v1"
AAD_ENTRY = b"nextrmnl-vault-entry-v1"
AAD_SERVER = b"nextrmnl-server-secret-v1"


class WrongPassword(Exception):
    pass


def derive(password: str, salt: bytes) -> bytes:
    settings = get_settings()
    return hash_secret_raw(
        password.encode("utf-8"),
        salt,
        time_cost=settings.argon2_time,
        memory_cost=settings.argon2_memory_kib,
        parallelism=settings.argon2_parallelism,
        hash_len=KEY_BYTES,
        type=Type.ID,
    )


def _seal(key: bytes, plaintext: bytes, aad: bytes) -> bytes:
    nonce = os.urandom(NONCE_BYTES)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def _open(key: bytes, sealed: bytes, aad: bytes) -> bytes:
    if len(sealed) < NONCE_BYTES + 16:
        raise InvalidTag()
    return AESGCM(key).decrypt(sealed[:NONCE_BYTES], sealed[NONCE_BYTES:], aad)


def new_vault(password: str) -> tuple[bytes, bytes, bytes]:
    """Returns (salt, wrapped vault key, vault key)."""
    vault_key = os.urandom(KEY_BYTES)
    salt, wrapped = wrap(vault_key, password)
    return salt, wrapped, vault_key


def wrap(vault_key: bytes, password: str) -> tuple[bytes, bytes]:
    salt = os.urandom(SALT_BYTES)
    return salt, _seal(derive(password, salt), vault_key, AAD_WRAP)


def unwrap(password: str, salt: bytes, wrapped: bytes) -> bytes:
    try:
        return _open(derive(password, salt), wrapped, AAD_WRAP)
    except InvalidTag as error:
        raise WrongPassword() from error


def encrypt_entry(vault_key: bytes, plaintext: bytes) -> bytes:
    return _seal(vault_key, plaintext, AAD_ENTRY)


def decrypt_entry(vault_key: bytes, sealed: bytes) -> bytes:
    return _open(vault_key, sealed, AAD_ENTRY)


def _server_key() -> bytes:
    secret = get_settings().resolved_secret_key().encode("utf-8")
    return hashlib.sha256(b"nextrmnl-secrets:" + secret).digest()


def encrypt_secret(text: str) -> str:
    """For values the server must read on its own, stored as hex."""
    if not text:
        return ""
    return _seal(_server_key(), text.encode("utf-8"), AAD_SERVER).hex()


def decrypt_secret(stored: str) -> str:
    if not stored:
        return ""
    try:
        return _open(_server_key(), bytes.fromhex(stored), AAD_SERVER).decode("utf-8")
    except (InvalidTag, ValueError):
        # A different secret.key than the one that encrypted it: the value is lost, not the app.
        return ""

"""The own vault: open and close it, keys and stored passwords."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import crypto
from ..deps import CurrentAccount, DbSession
from ..meldungen import fehler
from ..models import KEY_TYPES, SIGN_IN_OIDC, Connection, VaultKey, VaultPassword
from ..security import MIN_PASSWORD
from ..services import settings_service, vault

router = APIRouter(prefix="/api/vault", tags=["vault"])


class PasswordIn(BaseModel):
    password: str = Field(max_length=200)


class GenerateIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    key_type: str = Field(default="ed25519")
    passphrase: str = Field(default="", max_length=200)


class ImportIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    private_key: str = Field(min_length=1, max_length=20000)
    passphrase: str = Field(default="", max_length=200)


class StoredPasswordIn(BaseModel):
    password: str = Field(min_length=1, max_length=500)


def locked() -> Exception:
    return fehler("vault_locked", "The vault is locked. Enter your password to open it.", 423)


def key_view(row: VaultKey, used_by: int = 0) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "key_type": row.key_type,
        "bits": row.bits,
        "fingerprint": row.fingerprint,
        "public_key": row.public_key,
        "has_passphrase": row.has_passphrase,
        "created_at": row.created_at.isoformat(),
        "used_by": used_by,
    }


def password_view(row: VaultPassword) -> dict[str, Any]:
    return {"connection_id": row.connection_id, "changed_at": row.changed_at.isoformat()}


@router.get("", summary="State of the own vault")
def state(account: CurrentAccount) -> dict[str, Any]:
    return {"state": "unset" if not account.vault_ready else ("open" if vault.is_open(account.id) else "locked")}


@router.post("/unlock", summary="Open the vault with the account's password")
def unlock(payload: PasswordIn, account: CurrentAccount, db: DbSession) -> dict[str, Any]:
    if not account.vault_ready:
        raise fehler("vault_not_set_up", "This account has no vault yet.", 409)
    try:
        vault.unlock(account, payload.password, int(settings_service.get(db, "vault_lock_minutes")))
    except crypto.WrongPassword as error:
        raise fehler("wrong_password", "The password is wrong.", 401) from error
    return {"state": "open"}


@router.post("/lock", summary="Lock the vault now")
def lock(account: CurrentAccount) -> dict[str, Any]:
    vault.lock(account.id)
    return {"state": "locked"}


@router.post("/setup", summary="OIDC accounts: choose the vault password")
def set_up(payload: PasswordIn, account: CurrentAccount, db: DbSession) -> dict[str, Any]:
    if account.vault_ready:
        raise fehler("vault_exists", "This account already has a vault.", 409)
    if account.sign_in != SIGN_IN_OIDC:
        raise fehler("password_account", "Password accounts get their vault with the password.", 409)
    if len(payload.password) < MIN_PASSWORD:
        raise fehler("password_too_short", f"Use at least {MIN_PASSWORD} characters.", 422, minimum=MIN_PASSWORD)
    vault.set_up(db, account, payload.password)
    vault.unlock(account, payload.password, int(settings_service.get(db, "vault_lock_minutes")))
    return {"state": "open"}


class VaultPasswordChangeIn(BaseModel):
    current: str = Field(max_length=200)
    new: str = Field(max_length=200)


@router.put("/password", status_code=204, summary="OIDC accounts: change the vault password")
def change_vault_password(payload: VaultPasswordChangeIn, account: CurrentAccount, db: DbSession) -> None:
    if account.sign_in != SIGN_IN_OIDC:
        raise fehler("password_account", "Change the account password instead; it is the vault password.", 409)
    if len(payload.new) < MIN_PASSWORD:
        raise fehler("password_too_short", f"Use at least {MIN_PASSWORD} characters.", 422, minimum=MIN_PASSWORD)
    try:
        vault.rewrap(db, account, payload.current, payload.new)
    except crypto.WrongPassword as error:
        raise fehler("wrong_password", "The password is wrong.", 401) from error


@router.post("/reset", summary="Forgot the vault password: start over with an empty vault")
def reset(payload: PasswordIn, account: CurrentAccount, db: DbSession) -> dict[str, Any]:
    """Only for OIDC accounts; password accounts change their password, which rewraps the vault."""
    if account.sign_in != SIGN_IN_OIDC:
        raise fehler("password_account", "Password accounts reset the vault by changing the password.", 409)
    if len(payload.password) < MIN_PASSWORD:
        raise fehler("password_too_short", f"Use at least {MIN_PASSWORD} characters.", 422, minimum=MIN_PASSWORD)
    vault.reset(db, account, payload.password)
    vault.unlock(account, payload.password, int(settings_service.get(db, "vault_lock_minutes")))
    return {"state": "open"}


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------


def _usage(db: DbSession, account_id: int) -> dict[int, int]:
    counts: dict[int, int] = {}
    for key_id in db.scalars(
        select(Connection.key_id).where(Connection.owner_id == account_id, Connection.key_id.is_not(None))
    ):
        counts[int(key_id)] = counts.get(int(key_id), 0) + 1
    return counts


@router.get("/keys", summary="The own keys (public parts only)")
def list_keys(account: CurrentAccount, db: DbSession) -> list[dict[str, Any]]:
    usage = _usage(db, account.id)
    return [key_view(row, usage.get(row.id, 0)) for row in vault.list_keys(db, account.id)]


def _name_free(db: DbSession, account_id: int, name: str) -> None:
    if db.scalar(select(VaultKey).where(VaultKey.account_id == account_id, VaultKey.name == name)) is not None:
        raise fehler("key_name_taken", "A key with this name exists already.", 409)


@router.post("/keys/generate", status_code=201, summary="Generate a key pair in the vault")
def generate(payload: GenerateIn, account: CurrentAccount, db: DbSession) -> dict[str, Any]:
    if payload.key_type not in KEY_TYPES:
        raise fehler("invalid_key_type", "Unknown key type.", 422)
    name = payload.name.strip()
    _name_free(db, account.id, name)
    if not vault.is_open(account.id):
        raise locked()
    text, parsed = vault.generate_key(payload.key_type, payload.passphrase or None, comment=f"{name}@nextrmnl")
    try:
        row = vault.add_key(db, account, name, text, parsed)
    except vault.VaultLocked as error:
        raise locked() from error
    return key_view(row)


@router.post("/keys/import", status_code=201, summary="Store an existing private key in the vault")
def import_key(payload: ImportIn, account: CurrentAccount, db: DbSession) -> dict[str, Any]:
    name = payload.name.strip()
    _name_free(db, account.id, name)
    if not vault.is_open(account.id):
        raise locked()
    text = payload.private_key.strip() + "\n"
    try:
        parsed = vault.parse_private_key(text, payload.passphrase or None)
    except Exception as error:
        raise fehler("invalid_key", "This is not a private key nextrmnl can read.", 422) from error
    if parsed is None:
        # Protected and no passphrase given. nextrmnl needs it once to read the public part and the fingerprint;
        # the key is stored as it is, protected, and the passphrase is asked for on every connection.
        raise fehler("passphrase_needed", "This key is protected. Enter its passphrase so nextrmnl can read it.", 422)
    try:
        row = vault.add_key(db, account, name, text, parsed)
    except vault.VaultLocked as error:
        raise locked() from error
    return key_view(row)


@router.delete("/keys/{key_id}", status_code=204, summary="Delete a key")
def delete_key(key_id: int, account: CurrentAccount, db: DbSession) -> None:
    if not vault.delete_key(db, account, key_id):
        raise fehler("not_found", "Key not found.", 404)


# ---------------------------------------------------------------------------
# Stored passwords
# ---------------------------------------------------------------------------


@router.get("/passwords", summary="Which connections have a stored password (never the password itself)")
def list_passwords(account: CurrentAccount, db: DbSession) -> list[dict[str, Any]]:
    return [password_view(row) for row in vault.list_passwords(db, account.id)]


@router.put("/passwords/{connection_id}", summary="Store the password for a connection")
def store_password(
    connection_id: int, payload: StoredPasswordIn, account: CurrentAccount, db: DbSession
) -> dict[str, Any]:
    if db.get(Connection, connection_id) is None:
        raise fehler("not_found", "Connection not found.", 404)
    try:
        row = vault.set_password(db, account, connection_id, payload.password)
    except vault.VaultLocked as error:
        raise locked() from error
    return password_view(row)


@router.delete("/passwords/{connection_id}", status_code=204, summary="Forget a stored password")
def delete_password(connection_id: int, account: CurrentAccount, db: DbSession) -> None:
    if not vault.delete_password(db, account, connection_id):
        raise fehler("not_found", "No stored password for this connection.", 404)

"""The own vault as a file: export, check, import. The vault must be open, and the export asks for the vault
password again."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Request, Response, UploadFile
from pydantic import BaseModel, Field

from ..deps import CurrentAccount, DbSession, reauth_failed, reauth_guard, reauth_succeeded
from ..meldungen import fehler
from ..services import settings_service, vault, vault_file

router = APIRouter(prefix="/api/vault/file", tags=["vault"])


class ExportIn(BaseModel):
    password: str = Field(max_length=200)


def _failed(exc: vault_file.VaultFileError) -> Exception:
    return fehler(exc.code, exc.message, exc.status)


async def _read_upload(upload: UploadFile) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(64 * 1024):
        total += len(chunk)
        if total > vault_file.MAX_FILE:
            raise fehler("file_too_large", "This is larger than any vault file.", 413)
        chunks.append(chunk)
    return b"".join(chunks)


def _server(db: DbSession, request: Request) -> str:
    return settings_service.public_url(db) or request.headers.get("host", "")


@router.post("/export", summary="Download the own vault as an encrypted file; asks for the vault password again")
def export_vault(payload: ExportIn, request: Request, account: CurrentAccount, db: DbSession) -> Response:
    reauth_guard(request, account)
    try:
        data = vault_file.export(db, account, payload.password, _server(db, request))
    except vault_file.VaultFileError as exc:
        if exc.code == "wrong_password":
            reauth_failed(request, db, account)
        raise _failed(exc) from exc
    reauth_succeeded(request, db, account)
    name = f"tresor-{account.name}-{datetime.now(UTC).strftime('%Y-%m-%d')}{vault_file.SUFFIX}"
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.post("/check", summary="Look into a vault file without importing it")
async def check_vault_file(
    file: Annotated[UploadFile, File()], password: Annotated[str, Form()], account: CurrentAccount
) -> dict[str, Any]:
    raw = await _read_upload(file)
    try:
        header, _payload = vault_file.read(raw, password)
    except vault_file.VaultFileError as exc:
        raise _failed(exc) from exc
    return {
        "account": header.account,
        "server": header.server,
        "created": header.created,
        "keys": header.keys,
        "passwords": header.passwords,
    }


@router.post("/import", summary="Take the entries of a vault file into the own vault")
async def import_vault_file(
    file: Annotated[UploadFile, File()],
    password: Annotated[str, Form()],
    account: CurrentAccount,
    db: DbSession,
    mode: Annotated[str, Form()] = vault_file.MERGE,
) -> dict[str, Any]:
    if mode not in vault_file.MODES:
        raise fehler("invalid_mode", "Choose merge or replace.", 422)
    if not vault.is_open(account.id):
        # Said before the file is opened: the service checks again when it writes.
        raise fehler("vault_locked", "The vault is locked. Enter your password to open it.", 423)
    raw = await _read_upload(file)
    try:
        _header, payload = vault_file.read(raw, password)
        result = vault_file.import_(db, account, payload, mode)
    except vault_file.VaultFileError as exc:
        raise _failed(exc) from exc
    return {
        "added_keys": result.added_keys,
        "skipped_keys": result.skipped_keys,
        "added_passwords": result.added_passwords,
        "skipped_passwords": result.skipped_passwords,
    }

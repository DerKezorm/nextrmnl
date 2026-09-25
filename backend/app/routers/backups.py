"""Backups: list, make, download as an encrypted archive, delete, check and restore an archive. Operator only.

A restore happens at the next start: the archive is checked and laid out, the answer goes out, and nextrmnl ends
itself; the container starts it again and the start swaps the files before anything opens the database.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from ..deps import DbSession, OperatorAccount
from ..meldungen import fehler
from ..services import backups

logger = logging.getLogger("nextrmnl.backups")

router = APIRouter(prefix="/api/backups", tags=["backups"])


class BackupIn(BaseModel):
    note: str = Field(default="", max_length=200)


class ArchiveIn(BaseModel):
    # The archive holds secret.key; the minimum length is checked in the service so the code is the same everywhere.
    password: str = Field(max_length=200)


def _entry(entry: backups.Entry) -> dict[str, Any]:
    return {
        "name": entry.name,
        "size": entry.size,
        "created": entry.created,
        # The interface knows three words: what the schedule made is "auto".
        "kind": "auto" if entry.kind == backups.SCHEDULED else entry.kind,
        "note": entry.note,
        "version": entry.version,
        "compatible": entry.compatible,
        "reason": entry.reason,
    }


def _brief(brief: backups.Brief) -> dict[str, Any]:
    return {
        "version": brief.version,
        "created": brief.created,
        "kind": "auto" if brief.kind == backups.SCHEDULED else brief.kind,
        "note": brief.note,
        "accounts": brief.accounts,
        "connections": brief.connections,
        "key_in_archive": brief.key_in_archive,
        "key_from_env": brief.key_from_env,
        "compatible": brief.compatible,
        "reason": brief.reason,
    }


def _failed(exc: backups.BackupError) -> Exception:
    return fehler(exc.code, exc.message, exc.status)


@asynccontextmanager
async def _saved(upload: UploadFile) -> AsyncIterator[Path]:
    """The upload in a temporary file next to the backups, in pieces: an archive is never held whole in memory."""
    target = backups.temporary(".upload-")
    written = 0
    try:
        with target.open("wb") as sink:
            while chunk := await upload.read(1024 * 1024):
                written += len(chunk)
                if written > backups.MAX_UPLOAD:
                    raise fehler("archive_too_large", "The archive is larger than 2 GB.", 413)
                sink.write(chunk)
        yield target
    finally:
        target.unlink(missing_ok=True)


@router.get("", summary="The backups, newest first")
def list_backups(operator: OperatorAccount, db: DbSession) -> dict[str, Any]:
    return {
        "entries": [_entry(entry) for entry in backups.entries()],
        "folder": str(backups.folder()),
        "schedule": backups.schedule(db),
        "keep": backups.keep(db),
    }


@router.post("", status_code=201, summary="Make a backup now")
async def create_backup(payload: BackupIn, operator: OperatorAccount) -> dict[str, Any]:
    try:
        # In a worker thread: the copy grows with the database.
        path = await asyncio.to_thread(backups.create, kind=backups.MANUAL, note=payload.note)
    except backups.BackupError as exc:
        raise _failed(exc) from exc
    except Exception as exc:
        logger.exception("Manual backup failed")
        raise fehler("backup_failed", "The backup could not be made.", 500) from exc
    for entry in backups.entries():
        if entry.name == path.name:
            return _entry(entry)
    raise fehler("backup_failed", "The backup could not be made.", 500)


@router.delete("/{name}", status_code=204, summary="Delete a backup")
def delete_backup(name: str, operator: OperatorAccount) -> Response:
    try:
        path = backups.path_of(name)
    except backups.BackupError as exc:
        raise _failed(exc) from exc
    backups.remove(path)
    logger.info("Backup deleted name=%s", name)
    return Response(status_code=204)


@router.post("/{name}/archive", summary="Download a backup as an AES-256 ZIP; the password travels in the body")
async def download_backup(name: str, payload: ArchiveIn, operator: OperatorAccount) -> FileResponse:
    try:
        path = await asyncio.to_thread(backups.archive, name, payload.password)
    except backups.BackupError as exc:
        raise _failed(exc) from exc
    # The temporary archive goes once it is sent.
    return FileResponse(
        path,
        media_type="application/zip",
        filename=f"{name.removesuffix('.db')}.zip",
        background=BackgroundTask(path.unlink, missing_ok=True),
    )


@router.post("/check", summary="Look into an uploaded archive without restoring it")
async def check_archive(
    file: Annotated[UploadFile, File()], password: Annotated[str, Form()], operator: OperatorAccount
) -> dict[str, Any]:
    async with _saved(file) as path:
        try:
            brief = await asyncio.to_thread(backups.check, path, password)
        except backups.BackupError as exc:
            raise _failed(exc) from exc
    return _brief(brief)


@router.post("/restore", status_code=202, summary="Restore an uploaded archive at the next start")
async def restore_archive(
    file: Annotated[UploadFile, File()], password: Annotated[str, Form()], operator: OperatorAccount
) -> dict[str, Any]:
    async with _saved(file) as path:
        try:
            brief = await asyncio.to_thread(backups.stage_restore, path, password)
        except backups.BackupError as exc:
            raise _failed(exc) from exc
    logger.warning(
        "Restore requested by=%s version=%s created=%s at=%s",
        operator.name,
        brief.version,
        brief.created,
        datetime.now(UTC).isoformat(timespec="seconds"),
    )
    backups.restart_soon()
    return {"restarting": True, **_brief(brief)}

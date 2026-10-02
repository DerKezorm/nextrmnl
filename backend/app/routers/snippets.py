"""Snippets: commands an account keeps at hand. Each account sees and changes only its own, the operator included."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from ..deps import CurrentAccount, DbSession
from ..meldungen import fehler
from ..models import Account, Snippet, utcnow

router = APIRouter(prefix="/api/snippets", tags=["snippets"])

#: A command is something to type, not a script to upload; and the list is a list, not a store.
MAX_COMMAND = 4000
MAX_SNIPPETS = 500


class SnippetIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    command: str = Field(min_length=1, max_length=MAX_COMMAND)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("empty")
        return value

    @field_validator("command")
    @classmethod
    def _command(cls, value: str) -> str:
        # Control characters other than tab and line breaks would act as keystrokes in the shell (Ctrl+C,
        # escape sequences); a snippet is text.
        if any(ord(char) < 0x20 and char not in "\t\r\n" or ord(char) == 0x7F for char in value):
            raise ValueError("control characters")
        if not value.strip():
            raise ValueError("empty")
        return value


def _view(row: Snippet) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "command": row.command,
        "created_at": row.created_at.isoformat(),
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
    }


def _own(db: DbSession, account: Account, snippet_id: int) -> Snippet:
    row = db.get(Snippet, snippet_id)
    if row is None or row.owner_id != account.id:
        raise fehler("not_found", "Snippet not found.", 404)
    return row


@router.get("", summary="The own snippets, by name")
def listing(account: CurrentAccount, db: DbSession) -> list[dict[str, Any]]:
    rows = db.scalars(select(Snippet).where(Snippet.owner_id == account.id).order_by(func.lower(Snippet.name)))
    return [_view(row) for row in rows]


@router.post("", status_code=201, summary="Keep a command")
def create(payload: SnippetIn, account: CurrentAccount, db: DbSession) -> dict[str, Any]:
    count = db.scalar(select(func.count()).select_from(Snippet).where(Snippet.owner_id == account.id)) or 0
    if count >= MAX_SNIPPETS:
        raise fehler("too_many_snippets", f"At most {MAX_SNIPPETS} snippets per account.", 422)
    row = Snippet(owner_id=account.id, name=payload.name, command=payload.command)
    db.add(row)
    db.commit()
    return _view(row)


@router.put("/{snippet_id}", summary="Change a snippet")
def update(snippet_id: int, payload: SnippetIn, account: CurrentAccount, db: DbSession) -> dict[str, Any]:
    row = _own(db, account, snippet_id)
    row.name, row.command = payload.name, payload.command
    db.commit()
    return _view(row)


@router.post("/{snippet_id}/used", status_code=204, summary="Note that a snippet was used, for the order of the list")
def used(snippet_id: int, account: CurrentAccount, db: DbSession) -> None:
    row = _own(db, account, snippet_id)
    row.last_used_at = utcnow()
    db.commit()


@router.delete("/{snippet_id}", status_code=204, summary="Delete a snippet")
def delete(snippet_id: int, account: CurrentAccount, db: DbSession) -> None:
    db.delete(_own(db, account, snippet_id))
    db.commit()

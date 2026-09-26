"""The operator's read-only API keys: list, create, delete."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..deps import DbSession, OperatorAccount
from ..meldungen import fehler
from ..models import Account, ApiKey
from ..services import api_keys

router = APIRouter(prefix="/api/api-keys", tags=["api-keys"])


class KeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=api_keys.MAX_NAME)


def _view(row: ApiKey, names: dict[int, str]) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "prefix": row.prefix,
        "created_by": names.get(row.account_id, "?"),
        "created_at": row.created_at.isoformat(),
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
    }


def _names(db: DbSession) -> dict[int, str]:
    return {account_id: name for account_id, name in db.execute(select(Account.id, Account.name))}


@router.get("", summary="The API keys, newest first")
def listing(operator: OperatorAccount, db: DbSession) -> dict[str, Any]:
    names = _names(db)
    return {"allowed": api_keys.allowed(db), "keys": [_view(row, names) for row in api_keys.listing(db)]}


@router.post("", status_code=201, summary="Create a key; the key itself is in this answer and never again")
def create(payload: KeyIn, operator: OperatorAccount, db: DbSession) -> dict[str, Any]:
    try:
        row, plaintext = api_keys.create(db, operator, payload.name)
    except api_keys.KeyError_ as error:
        raise fehler(error.code, str(error), 422) from error
    return {**_view(row, _names(db)), "key": plaintext}


@router.delete("/{key_id}", status_code=204, summary="Delete a key; every dashboard using it stops at once")
def delete(key_id: int, operator: OperatorAccount, db: DbSession) -> None:
    if not api_keys.delete(db, key_id, operator):
        raise fehler("not_found", "Key not found.", 404)

"""Version, license, update state."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from .. import __version__
from ..deps import CurrentAccount, DbSession, OperatorAccount
from ..services import settings_service, updates

router = APIRouter(prefix="/api/about", tags=["about"])


def _view(status: updates.UpdateStatus, enabled: bool) -> dict[str, Any]:
    return {
        "version": __version__,
        "license": "AGPL-3.0",
        "repo_url": updates.REPO_URL,
        "release_url": status.release_url,
        "update_check": enabled,
        "update_checked": status.checked_at is not None,
        "checked_at": status.checked_at.isoformat() if status.checked_at else None,
        "latest_version": status.latest,
        "update_available": status.update_available,
    }


@router.get("", summary="About nextrmnl")
async def about(account: CurrentAccount, db: DbSession) -> dict[str, Any]:
    enabled = bool(settings_service.get(db, "update_check"))
    return _view(await updates.status(enabled=enabled), enabled)


@router.post("/check", summary="Check GitHub for a newer release now")
async def check(operator: OperatorAccount, db: DbSession) -> dict[str, Any]:
    enabled = bool(settings_service.get(db, "update_check"))
    return _view(await updates.status(enabled=enabled, force=True), enabled)

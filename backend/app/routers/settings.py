"""Operator settings: allowed targets, locking, retention, updates, backups, sign-in rules."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..deps import DbSession, OperatorAccount
from ..meldungen import fehler
from ..services import settings_service, targets, updates
from ..services.settings_service import TARGETS_MODES

router = APIRouter(prefix="/api/settings", tags=["settings"])

SCHEDULES = ("off", "daily", "weekly", "monthly")


class SettingsIn(BaseModel):
    targets_mode: str | None = None
    targets_list: list[str] | None = None
    vault_lock_minutes: int | None = Field(default=None, ge=1, le=1440)
    history_days: int | None = Field(default=None, ge=1, le=3650)
    update_check: bool | None = None
    backup_schedule: str | None = None
    backup_keep: int | None = Field(default=None, ge=2, le=50)
    password_login: bool | None = None
    two_factor_required: bool | None = None
    oidc_auto_create: bool | None = None
    public_url: str | None = Field(default=None, max_length=500)


@router.get("", summary="The operator settings")
def read(operator: OperatorAccount, db: DbSession) -> dict[str, Any]:
    return settings_service.public(db)


@router.put("", summary="Change operator settings")
def write(payload: SettingsIn, operator: OperatorAccount, db: DbSession) -> dict[str, Any]:
    values = payload.model_dump(exclude_none=True)
    if "targets_mode" in values and values["targets_mode"] not in TARGETS_MODES:
        raise fehler("invalid_targets_mode", "Unknown mode for allowed targets.", 422)
    if "targets_list" in values:
        entries = targets.parse_list(values["targets_list"])
        bad = targets.invalid_entries(entries)
        if bad:
            raise fehler("invalid_targets", "These entries are neither networks nor names.", 422, entries=bad)
        values["targets_list"] = entries
    if "backup_schedule" in values and values["backup_schedule"] not in SCHEDULES:
        raise fehler("invalid_schedule", "Unknown backup schedule.", 422)
    if "public_url" in values:
        try:
            values["public_url"] = settings_service.normalize_public_url(values["public_url"])
        except ValueError as error:
            raise fehler(
                "public_url_invalid", "Use http:// or https:// followed by a host name, without a path.", 422
            ) from error
    if values.get("update_check") is False:
        updates.forget()
    settings_service.save(db, values)
    return settings_service.public(db)

"""Operator settings: allowed targets, locking, retention, updates, backups, sign-in rules."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .. import crypto
from ..deps import DbSession, OperatorAccount
from ..meldungen import fehler
from ..services import notify, settings_service, targets, updates
from ..services.settings_service import TARGETS_MODES

router = APIRouter(prefix="/api/settings", tags=["settings"])

SCHEDULES = ("off", "daily", "weekly", "monthly")


class SettingsIn(BaseModel):
    targets_mode: str | None = None
    targets_list: list[str] | None = None
    vault_lock_minutes: int | None = Field(default=None, ge=1, le=1440)
    detach_minutes: int | None = Field(default=None, ge=0, le=120)
    history_days: int | None = Field(default=None, ge=1, le=3650)
    update_check: bool | None = None
    api_keys_allowed: bool | None = None
    backup_schedule: str | None = None
    backup_keep: int | None = Field(default=None, ge=2, le=50)
    password_login: bool | None = None
    two_factor_required: bool | None = None
    oidc_auto_create: bool | None = None
    public_url: str | None = Field(default=None, max_length=500)
    notify_enabled: bool | None = None
    notify_kind: str | None = None
    notify_url: str | None = Field(default=None, max_length=500)
    #: Write only. An empty string removes the stored token; leaving the field out keeps it.
    notify_token: str | None = Field(default=None, max_length=500)
    notify_events: list[str] | None = None


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
    if "notify_kind" in values and values["notify_kind"] not in notify.KINDS:
        raise fehler("invalid_notify_kind", "Choose Gotify, ntfy or a webhook.", 422)
    if "notify_url" in values:
        values["notify_url"] = values["notify_url"].strip()
        if values["notify_url"] and not notify.valid_url(values["notify_url"]):
            raise fehler("notify_url_invalid", "Use http:// or https:// and a host, without a user name.", 422)
    if "notify_events" in values:
        if not set(values["notify_events"]) <= set(notify.CATEGORIES):
            raise fehler("invalid_notify_events", "Unknown notification group.", 422)
        values["notify_events"] = [c for c in notify.CATEGORIES if c in values["notify_events"]]
    if "notify_token" in values:
        token = values.pop("notify_token").strip()
        values["notify_token_enc"] = crypto.encrypt_secret(token) if token else ""
    if values.get("notify_enabled"):
        url = values.get("notify_url", settings_service.get(db, "notify_url"))
        if not url:
            raise fehler("notify_url_missing", "Enter the address of the inbox first.", 422)
    if values.get("update_check") is False:
        updates.forget()
    settings_service.save(db, values)
    return settings_service.public(db)


@router.post("/notify/test", summary="Send a test message to the configured inbox, right away")
def notify_test(operator: OperatorAccount, db: DbSession) -> dict[str, Any]:
    try:
        status = notify.test()
    except notify.NotifyError as error:
        raise fehler(error.code, str(error), 502 if error.code != "notify_off" else 409) from error
    return {"status": status}

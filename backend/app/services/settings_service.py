"""Operator settings in the database, with defaults. Secrets go through ``crypto.encrypt_secret`` first."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Setting

TARGETS_PRIVATE = "private"
TARGETS_LIST = "list"
TARGETS_ALL = "all"
TARGETS_MODES = (TARGETS_PRIVATE, TARGETS_LIST, TARGETS_ALL)

DEFAULTS: dict[str, Any] = {
    #: Where connections may go. Private networks only, unless the operator widens it.
    "targets_mode": TARGETS_PRIVATE,
    "targets_list": [],
    #: An open vault locks itself after this many minutes without use.
    "vault_lock_minutes": 30,
    "history_days": 90,
    #: The only call that leaves the house; off until the operator turns it on.
    "update_check": False,
    #: Read-only API keys for dashboards: another way out, closed until the operator opens it.
    "api_keys_allowed": False,
    "backup_schedule": "weekly",
    "backup_keep": 5,
    "password_login": True,
    "two_factor_required": False,
    "log_mode": "normal",
    "log_mode_until": None,
    #: OIDC: issuer, client_id and the encrypted client secret; empty means not set up.
    "oidc_issuer": "",
    "oidc_client_id": "",
    "oidc_client_secret_enc": "",
    "oidc_provider_name": "",
    #: Whether an unknown identity from the provider gets a member account. Off: only accounts that exist
    #: (invited, or linked by their owner) may come in through OIDC. The operator decides here, not only there.
    "oidc_auto_create": False,
    #: The address people use to reach nextrmnl: invitation links, the OIDC redirect, the WebSocket origin
    #: check. Empty means ``NEXTRMNL_PUBLIC_URL`` from the environment, and without that the request's own.
    "public_url": "",
}

#: What the frontend may read and the operator may change through PUT /api/settings.
PUBLIC_KEYS = (
    "public_url",
    "targets_mode",
    "targets_list",
    "vault_lock_minutes",
    "history_days",
    "update_check",
    "api_keys_allowed",
    "backup_schedule",
    "backup_keep",
    "password_login",
    "two_factor_required",
    "oidc_auto_create",
)


def get(db: Session, key: str) -> Any:
    row = db.get(Setting, key)
    return DEFAULTS.get(key) if row is None else row.value


def get_all(db: Session) -> dict[str, Any]:
    values = dict(DEFAULTS)
    for row in db.scalars(select(Setting)):
        values[row.key] = row.value
    return values


def public(db: Session) -> dict[str, Any]:
    values = get_all(db)
    return {key: values[key] for key in PUBLIC_KEYS}


def save(db: Session, values: dict[str, Any]) -> None:
    for key, value in values.items():
        row = db.get(Setting, key)
        if row is None:
            db.add(Setting(key=key, value=value))
        else:
            row.value = value
    db.commit()


def normalize_public_url(value: str) -> str:
    """``scheme://host[:port]`` and nothing else, or empty. Raises ``ValueError`` for anything that is not an
    address people could type into a browser: no other schemes, no credentials, no path, no query."""
    text = value.strip()
    if not text:
        return ""
    parts = urlsplit(text)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise ValueError("scheme or host")
    if parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/"):
        raise ValueError("path, query or credentials")
    try:
        parts.port  # noqa: B018 - raises ValueError when the port is not a number
    except ValueError as error:
        raise ValueError("port") from error
    return f"{parts.scheme.lower()}://{parts.netloc}"


def public_url(db: Session) -> str:
    """The effective public address without a trailing slash: the setting when set, otherwise the environment,
    otherwise empty (callers then take the address of the request)."""
    stored = str(get(db, "public_url") or "").strip().rstrip("/")
    if stored:
        return stored
    return get_settings().public_url.strip().rstrip("/")

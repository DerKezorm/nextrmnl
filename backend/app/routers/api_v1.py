"""What a dashboard may read with an API key: status, live sessions, history, reachability. Nothing else.

⚠️ Only the Authorization header, never the cookie. These addresses are built
for other programs; a signed-in browser does not get in here, so no foreign
page can read them through someone's session. And a key opens none of the
other addresses.

⚠️ No sender addresses in any answer. A dashboard is often seen by guests;
who connected from which IP stays in nextrmnl's own sessions page.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select

from .. import __version__
from ..deps import DbSession, client_ip
from ..meldungen import fehler
from ..models import END_FAILED, Account, Connection, SessionRecord
from ..services import api_keys, settings_service, ssh, updates
from . import connections

logger = logging.getLogger("nextrmnl.api")

router = APIRouter(prefix="/api/v1", tags=["api-v1"])

#: How long a reachability answer is reused. A dashboard refreshing every few
#: seconds must not make nextrmnl knock at every host every few seconds.
REACH_SECONDS = 60
_reach_cache: dict[int, tuple[float, dict[int, dict[str, Any]]]] = {}


def key_account(request: Request, db: DbSession) -> Account:
    """The operator behind a valid key, asked on every request so the switch and a deletion act at once."""
    kind, _, value = request.headers.get("authorization", "").partition(" ")
    if kind.lower() != "bearer" or not value.strip():
        raise fehler("api_key_missing", "This address needs an API key in the Authorization header.", 401)
    if not api_keys.allowed(db):
        raise fehler("api_keys_off", "API keys are switched off in this installation.", 403)
    found = api_keys.find(db, value.strip())
    if found is None:
        logger.info("API request refused: unknown or withdrawn key from=%s", client_ip(request))
        raise fehler("api_key_invalid", "This API key is not valid.", 401)
    return found[1]


KeyAccount = Annotated[Account, Depends(key_account)]


def _today_start() -> datetime:
    return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


@router.get("/status", summary="Version, update and how busy nextrmnl is")
async def status(account: KeyAccount, db: DbSession) -> dict[str, Any]:
    update = await updates.status(enabled=bool(settings_service.get(db, "update_check")))
    since = _today_start()
    today = select(func.count()).select_from(SessionRecord).where(SessionRecord.started_at >= since)
    return {
        "version": __version__,
        "update_available": update.update_available,
        "latest_version": update.latest,
        "sessions_running": len(ssh.running(account)),
        "connections": len(connections.visible(db, account)),
        "sessions_today": db.scalar(today) or 0,
        "failed_today": db.scalar(today.where(SessionRecord.end == END_FAILED)) or 0,
    }


@router.get("/sessions", summary="Live sessions, oldest first")
def sessions(account: KeyAccount) -> list[dict[str, Any]]:
    return [
        {
            "account": session.account_name,
            "name": session.target.name,
            "target": session.target.label,
            "started_at": session.started_at.isoformat(),
            "state": "open" if session.opened_at else "connecting",
        }
        for session in ssh.running(account)
    ]


@router.get("/history", summary="Past sessions, newest first")
def history(account: KeyAccount, db: DbSession, limit: int = Query(default=20, ge=1, le=100)) -> list[dict[str, Any]]:
    names = {account_id: name for account_id, name in db.execute(select(Account.id, Account.name))}
    return [
        {
            "account": names.get(record.account_id, "?"),
            "name": record.name,
            "target": record.target,
            "started_at": record.started_at.isoformat(),
            "ended_at": record.ended_at.isoformat() if record.ended_at else None,
            "end": record.end,
            "detail": record.detail,
        }
        for record in ssh.history(db, account, limit)
    ]


@router.get("/connections", summary="Each connection and whether its port answers, reused for a minute")
async def connection_list(account: KeyAccount, db: DbSession) -> list[dict[str, Any]]:
    now = time.monotonic()
    cached = _reach_cache.get(account.id)
    if cached is None or now - cached[0] >= REACH_SECONDS:
        cached = (now, await connections.reach_of(db, account))
        _reach_cache[account.id] = cached
    reach = cached[1]
    rows: list[Connection] = connections.visible(db, account)
    return [
        {
            "name": row.name,
            "group": row.group,
            "target": f"{row.host}:{row.port}",
            "reach": reach.get(row.id, {}).get("reach", "unknown"),
            "latency_ms": reach.get(row.id, {}).get("latency_ms"),
        }
        for row in rows
    ]

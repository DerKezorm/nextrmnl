"""Read the log and set its level; operator only."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field

from ..deps import OperatorAccount
from ..meldungen import fehler
from ..services import logs

router = APIRouter(prefix="/api/logs", tags=["logs"])
DOWNLOAD_LIMIT = 20 * 1024 * 1024


class LogEntry(BaseModel):
    time: str
    level: str
    logger: str
    message: str
    request_id: str | None = None
    user: str | None = None


class LogMode(BaseModel):
    mode: str
    until: str | None = None
    fixed_by_env: bool = False
    modes: list[str] = Field(default_factory=lambda: list(logs.MODES))
    durations: list[int] = Field(default_factory=lambda: list(logs.ALLOWED_MINUTES))


class LogModeChange(BaseModel):
    mode: Literal["quiet", "normal", "detailed", "trace"]
    minutes: int = 0


@router.get("", response_model=list[LogEntry], summary="The newest lines")
def read_logs(
    operator: OperatorAccount,
    level: Annotated[Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=120)] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 300,
) -> list[logs.LogLine]:
    return logs.read(limit=limit, level=level, search=search)


@router.get("/level", response_model=LogMode, summary="Which level applies")
def read_level(operator: OperatorAccount) -> LogMode:
    state = logs.state()
    return LogMode(mode=state.mode, until=state.until, fixed_by_env=state.fixed_by_env)


@router.put("/level", response_model=LogMode, summary="Change the level, effective at once")
def set_level(operator: OperatorAccount, change: LogModeChange) -> LogMode:
    if logs.env_mode():
        raise fehler("log_level_from_environment", "The log level is fixed by NEXTRMNL_LOG_LEVEL.", 409)
    if change.minutes not in logs.ALLOWED_MINUTES:
        raise fehler("duration_not_allowed", "This duration is not offered.", 422)
    state = logs.set_mode(change.mode, change.minutes)
    return LogMode(mode=state.mode, until=state.until, fixed_by_env=state.fixed_by_env)


@router.get("/download", include_in_schema=False)
def download_logs(operator: OperatorAccount) -> Response:
    parts: list[str] = []
    total = 0
    files = [*reversed(logs.rotated_files()), logs.log_file()]
    included: list[str] = []
    for path in files:
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        if total + len(content) > DOWNLOAD_LIMIT and parts:
            continue
        parts.append(f"===== {path.name} =====\n{content}")
        included.append(path.name)
        total += len(content)
    missing = [p.name for p in files if p.is_file() and p.name not in included]
    if missing:
        parts.insert(0, f"===== left out (too large): {', '.join(missing)} =====\n")
    name = f"nextrmnl-log-{datetime.now(UTC).strftime('%Y-%m-%d')}.txt"
    return Response(
        content="\n".join(parts),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.delete("", status_code=204, summary="Clear the log")
def clear_logs(operator: OperatorAccount) -> None:
    logs.clear()

"""Logging.

nextrmnl writes its log to ``data/logs/nextrmnl.log``. The file rolls over at a size limit, three older files
are kept, and files older than 14 days are deleted.

Messages are English on purpose: log lines end up in bug reports and web searches. ``tests/test_log_language.py``
keeps that rule.

⚠️ What never goes into the log, at any level: terminal content, keystrokes, passwords, passphrases, private
keys, tokens, session identifiers, file contents. Start, end, account, target and result of a session do.
``tests/test_log_secrets.py`` scans the code for the obvious mistakes.

Four levels, switchable at runtime
----------------------------------
``quiet``     only warnings and errors
``normal``    what happened: sessions, sign-ins, backups, plus warnings and errors
``detailed``  plus the way there: every step of a connection, SFTP operations with path and size
``trace``     plus the raw data of the SSH library (negotiation, channels). Only on request.

``detailed`` and ``trace`` switch themselves off: the file is a ring buffer, and a forgotten trace level would
overwrite exactly the lines one wanted to keep. ``NEXTRMNL_LOG_LEVEL`` overrides the stored level, the emergency
exit for "the app does not even start".
"""

from __future__ import annotations

import logging
import re
import sys
import time
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ..config import get_settings

BACKUP_COUNT = 3
RETENTION_DAYS = 14
MAX_BYTES_NORMAL = 5 * 1024 * 1024
MAX_BYTES_DEEP = 25 * 1024 * 1024

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s [%(ctx)s] | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

#: Libraries that would otherwise log every request.
NOISY_LOGGERS = ("httpx", "httpcore", "watchfiles", "multipart", "sqlalchemy.engine", "uvicorn.access", "asyncssh")

MODES: dict[str, dict[str, int]] = {
    "quiet": {"app": logging.WARNING, "root": logging.WARNING, "libs": logging.WARNING},
    "normal": {"app": logging.INFO, "root": logging.INFO, "libs": logging.WARNING},
    "detailed": {"app": logging.DEBUG, "root": logging.INFO, "libs": logging.INFO},
    "trace": {"app": logging.DEBUG, "root": logging.DEBUG, "libs": logging.DEBUG},
}
DEEP_MODES = ("detailed", "trace")
DEFAULT_MODE = "normal"
ALLOWED_MINUTES = (30, 120, 480, 0)
LEVEL_ORDER = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

LINE_PATTERN = re.compile(
    r"^(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+"
    r"(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+"
    r"(?P<logger>\S+)\s"
    r"(?:\[(?P<ctx>[^\]]*)\]\s)?"
    r"\|\s(?P<message>.*)$"
)

# The request context is a *mutable* dict in the ContextVar: FastAPI runs sync dependencies in a thread pool
# with a copy of the context, and a value set there would be gone afterwards. The dict is shared.
_context: ContextVar[dict[str, str | None] | None] = ContextVar("nextrmnl_log_context", default=None)


def bind_request(request_id: str) -> object:
    return _context.set({"rid": request_id, "actor": None})


def unbind_request(token: object) -> None:
    _context.reset(token)  # type: ignore[arg-type]


def set_actor(name: str | None) -> None:
    data = _context.get()
    if data is not None:
        data["actor"] = name


def current_request_id() -> str | None:
    data = _context.get()
    return data.get("rid") if data else None


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        data = _context.get() or {}
        parts = [value for value in (data.get("rid"),) if value]
        actor = data.get("actor")
        if actor:
            parts.append(f"u:{actor}")
        record.ctx = " ".join(parts) if parts else "-"
        return True


class _NoDuplicateAsgiTraceback(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.getMessage() != "Exception in ASGI application"


@dataclass(frozen=True)
class LogLine:
    time: str
    level: str
    logger: str
    message: str
    request_id: str | None = None
    user: str | None = None


@dataclass(frozen=True)
class ModeState:
    mode: str
    until: str | None
    fixed_by_env: bool


def log_dir() -> Path:
    directory = get_settings().data_dir / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def log_file() -> Path:
    return log_dir() / "nextrmnl.log"


def rotated_files() -> list[Path]:
    return sorted(p for p in log_dir().glob("nextrmnl.log.*") if p.is_file())


_handler: RotatingFileHandler | None = None
_console: logging.StreamHandler | None = None
_mode = DEFAULT_MODE
#: Reads and writes the stored mode; set by ``main`` once the database exists.
_store_read: Callable[[], tuple[str, datetime | None]] | None = None
_store_write: Callable[[str, datetime | None], None] | None = None


def env_mode() -> str | None:
    value = (get_settings().log_level or "").strip().lower()
    if value in MODES:
        return value
    return {"warning": "quiet", "warn": "quiet", "info": "normal", "debug": "detailed"}.get(value)


def setup() -> None:
    global _handler, _console
    root = logging.getLogger()
    uvicorn_error = logging.getLogger("uvicorn.error")
    for logger_ in (root, uvicorn_error):
        for existing in list(logger_.handlers):
            if getattr(existing, "_nextrmnl", False):
                logger_.removeHandler(existing)

    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)
    handler = RotatingFileHandler(log_file(), maxBytes=MAX_BYTES_NORMAL, backupCount=BACKUP_COUNT, encoding="utf-8")
    handler.setFormatter(formatter)
    handler.addFilter(_ContextFilter())
    handler._nextrmnl = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    _handler = handler

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    console.addFilter(_ContextFilter())
    console._nextrmnl = True  # type: ignore[attr-defined]
    root.addHandler(console)
    _console = console

    # uvicorn's own logger does not propagate; without this no crash of the server reaches the file.
    uvicorn_error.addHandler(handler)
    if not any(isinstance(f, _NoDuplicateAsgiTraceback) for f in uvicorn_error.filters):
        uvicorn_error.addFilter(_NoDuplicateAsgiTraceback())

    apply_mode(env_mode() or DEFAULT_MODE)
    purge_old()


def apply_mode(mode: str) -> None:
    global _mode
    levels = MODES.get(mode)
    if levels is None:
        mode, levels = DEFAULT_MODE, MODES[DEFAULT_MODE]
    _mode = mode
    logging.getLogger().setLevel(levels["root"])
    logging.getLogger("nextrmnl").setLevel(levels["app"])
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(levels["libs"])
    if _handler is not None:
        _handler.maxBytes = MAX_BYTES_DEEP if mode in DEEP_MODES else MAX_BYTES_NORMAL
    if _console is not None:
        # Never finer than INFO on the console: the details belong in the file.
        _console.setLevel(max(levels["app"], logging.INFO))


def current_mode() -> str:
    return _mode


def attach_store(
    read: Callable[[], tuple[str, datetime | None]], write: Callable[[str, datetime | None], None]
) -> None:
    global _store_read, _store_write
    _store_read, _store_write = read, write


def _stored() -> tuple[str, datetime | None]:
    if _store_read is None:
        return DEFAULT_MODE, None
    mode, until = _store_read()
    return (mode if mode in MODES else DEFAULT_MODE), until


def _store(mode: str, until: datetime | None) -> None:
    if _store_write is not None:
        _store_write(mode, until)


def _now() -> datetime:
    return datetime.now(UTC)


def apply_stored_mode() -> None:
    """Take over the stored level, after ``init_db``."""
    forced = env_mode()
    if forced:
        apply_mode(forced)
        logging.getLogger("nextrmnl").info("Logging started mode=%s source=NEXTRMNL_LOG_LEVEL", forced)
        return
    mode, until = _stored()
    if mode in DEEP_MODES and until is not None and until <= _now():
        _store(DEFAULT_MODE, None)
        mode, until = DEFAULT_MODE, None
    apply_mode(mode)
    until_text = until.isoformat(timespec="seconds") if until else "-"
    logging.getLogger("nextrmnl").info("Logging started mode=%s until=%s file=%s", mode, until_text, log_file())


def set_mode(mode: str, minutes: int = 0) -> ModeState:
    if mode not in MODES:
        raise ValueError(f"unknown log mode: {mode}")
    until = _now() + timedelta(minutes=minutes) if mode in DEEP_MODES and minutes else None
    _store(mode, until)
    before = _mode
    apply_mode(mode)
    logging.getLogger("nextrmnl").info(
        "Log mode changed from=%s to=%s until=%s", before, mode, until.isoformat(timespec="seconds") if until else "-"
    )
    return state()


def state() -> ModeState:
    forced = env_mode()
    if forced:
        return ModeState(mode=forced, until=None, fixed_by_env=True)
    mode, until = _stored()
    if _expire(mode, until):
        until = None
    return ModeState(mode=_mode, until=until.isoformat(timespec="seconds") if until else None, fixed_by_env=False)


def enforce_expiry() -> bool:
    if env_mode():
        return False
    mode, until = _stored()
    return _expire(mode, until)


def _expire(mode: str, until: datetime | None) -> bool:
    if mode not in DEEP_MODES or until is None or until > _now():
        return False
    _store(DEFAULT_MODE, None)
    apply_mode(DEFAULT_MODE)
    logging.getLogger("nextrmnl").info("Log mode %s expired, back to %s", mode, DEFAULT_MODE)
    return True


def purge_old(now: float | None = None) -> int:
    """Delete rotated files older than the retention."""
    limit = (now or time.time()) - RETENTION_DAYS * 86400
    removed = 0
    for path in rotated_files():
        try:
            if path.stat().st_mtime < limit:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def parse_line(line: str) -> LogLine | None:
    match = LINE_PATTERN.match(line.rstrip("\n"))
    if match is None:
        return None
    request_id = user = None
    for part in (match["ctx"] or "").split():
        if part.startswith("u:"):
            user = part[2:]
        elif part != "-":
            request_id = part
    return LogLine(
        time=match["time"],
        level=match["level"],
        logger=match["logger"],
        message=match["message"],
        request_id=request_id,
        user=user,
    )


def read(limit: int = 200, level: str | None = None, search: str | None = None) -> list[LogLine]:
    """The newest lines first. ``level`` means this level and higher."""
    path = log_file()
    if not path.is_file():
        return []
    minimum = LEVEL_ORDER.index(level) if level in LEVEL_ORDER else 0
    needle = (search or "").strip().lower()
    result: list[LogLine] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        lines = handle.readlines()
    # Continuation lines (stack traces) belong to the line before them.
    merged: list[str] = []
    for raw in lines:
        if LINE_PATTERN.match(raw) or not merged:
            merged.append(raw)
        else:
            merged[-1] += raw
    for raw in reversed(merged):
        parsed = parse_line(raw.split("\n", 1)[0])
        if parsed is None:
            continue
        if raw.count("\n") > 1:
            parsed = LogLine(
                parsed.time, parsed.level, parsed.logger, raw.split("|", 1)[1].strip(), parsed.request_id, parsed.user
            )
        if LEVEL_ORDER.index(parsed.level) < minimum:
            continue
        haystack = f"{parsed.message} {parsed.request_id or ''} {parsed.user or ''}".lower()
        if needle and needle not in haystack:
            continue
        result.append(parsed)
        if len(result) >= limit:
            break
    return result


def clear() -> None:
    for path in rotated_files():
        try:
            path.unlink()
        except OSError:
            pass
    if _handler is not None:
        _handler.acquire()
        try:
            if _handler.stream:
                _handler.stream.close()
            log_file().write_text("", encoding="utf-8")
            _handler.stream = _handler._open()
        finally:
            _handler.release()
    elif log_file().is_file():
        log_file().write_text("", encoding="utf-8")
    logging.getLogger("nextrmnl").info("Log cleared by operator")


async def run_forever(stop) -> None:
    """Watchdog: takes a deep level back after its time, purges old files once a day."""
    import asyncio

    last_purge = time.time()
    while not stop.is_set():
        try:
            enforce_expiry()
            if time.time() - last_purge > 86400:
                purge_old()
                last_purge = time.time()
        except Exception:
            logging.getLogger("nextrmnl").exception("Log watchdog failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=60)
        except TimeoutError:
            continue

"""Entry point: the FastAPI app, routers, background tasks, the built frontend."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import get_settings
from .db import SessionLocal, init_db
from .meldungen import meldung
from .middleware import RequestContextMiddleware, unhandled_error
from .routers import about, auth, connections, health
from .routers import logs as logs_router
from .routers import settings as settings_router
from .routers import totp as totp_router
from .routers import vault as vault_router
from .security import purge_sessions
from .services import logs, settings_service, totp, vault

logger = logging.getLogger("nextrmnl")

ROUTERS = [health, auth, totp_router, vault_router, connections, settings_router, about, logs_router]

try:  # Optional parts, built separately. The app starts without them.
    from .routers import sessions as sessions_router

    ROUTERS.append(sessions_router)
except ImportError:  # pragma: no cover
    sessions_router = None
try:
    from .routers import backups as backups_router

    ROUTERS.append(backups_router)
except ImportError:  # pragma: no cover
    backups_router = None
try:
    from .routers import vault_file as vault_file_router

    ROUTERS.append(vault_file_router)
except ImportError:  # pragma: no cover
    vault_file_router = None
try:
    from .routers import oidc as oidc_router

    ROUTERS.append(oidc_router)
except ImportError:  # pragma: no cover
    oidc_router = None


def _read_log_mode() -> tuple[str, datetime | None]:
    with SessionLocal() as db:
        mode = settings_service.get(db, "log_mode")
        raw = settings_service.get(db, "log_mode_until")
    until = datetime.fromisoformat(raw).astimezone(UTC) if raw else None
    return str(mode or logs.DEFAULT_MODE), until


def _write_log_mode(mode: str, until: datetime | None) -> None:
    with SessionLocal() as db:
        settings_service.save(db, {"log_mode": mode, "log_mode_until": until.isoformat() if until else None})


async def _housekeeping(stop: asyncio.Event) -> None:
    """Every half minute: lock idle vaults; once an hour: purge expired browser sessions and old history."""
    last_purge = 0.0
    while not stop.is_set():
        try:
            vault.sweep()
            totp.sweep()
            now = asyncio.get_running_loop().time()
            if now - last_purge > 3600:
                with SessionLocal() as db:
                    purge_sessions(db)
                    if sessions_router is not None:
                        from .services import ssh

                        ssh.purge_history(db)
                last_purge = now
        except Exception:
            logger.exception("Housekeeping failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=30)
        except TimeoutError:
            continue


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    logs.setup()
    restored = False
    if backups_router is not None:
        from .services import backups

        try:
            restored = backups.apply_pending()
        except Exception:
            logger.exception("Applying the pending restore failed")
    init_db()
    # The server secret is created now, not on first use: a backup taken before the first OIDC setup would
    # otherwise carry KEY-MISSING.txt and a later restore would encrypt with a key that did not exist yet.
    get_settings().resolved_secret_key()
    logs.attach_store(_read_log_mode, _write_log_mode)
    logs.apply_stored_mode()
    if restored:
        with SessionLocal() as db:
            from sqlalchemy import delete

            from .models import AuthSession

            db.execute(delete(AuthSession))
            db.commit()
        logger.info("Backup restored at start, all browser sessions ended")
    stop = asyncio.Event()
    tasks: list[asyncio.Task[None]] = []
    if not get_settings().disable_background:
        tasks.append(asyncio.create_task(_housekeeping(stop)))
        tasks.append(asyncio.create_task(logs.run_forever(stop)))
        if backups_router is not None:
            from .services import backups

            tasks.append(asyncio.create_task(backups.run_forever(stop)))
    logger.info("nextrmnl %s started", __version__)
    try:
        yield
    finally:
        stop.set()
        if sessions_router is not None:
            from .services import ssh

            await ssh.close_all("server shutting down")
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        vault.lock_all()


app = FastAPI(
    title="nextrmnl",
    version=__version__,
    lifespan=lifespan,
    docs_url="/api/docs" if get_settings().api_docs else None,
    openapi_url="/api/openapi.json" if get_settings().api_docs else None,
    redoc_url=None,
)
app.add_middleware(RequestContextMiddleware)


@app.exception_handler(RequestValidationError)
async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    fields = [".".join(str(part) for part in error.get("loc", ()) if part != "body") for error in exc.errors()]
    return JSONResponse(
        status_code=422, content={"detail": meldung("invalid_input", "The input is not valid.", fields=fields)}
    )


app.add_exception_handler(Exception, unhandled_error)

for module in ROUTERS:
    app.include_router(module.router)


def _mount_frontend(target: FastAPI, dist: Path) -> None:
    index = dist / "index.html"
    if not index.exists():
        return
    if (dist / "assets").is_dir():
        target.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
    root = dist.resolve()
    start_page = index.resolve()

    @target.get("/{path:path}", include_in_schema=False, response_model=None)
    def spa(path: str) -> FileResponse | JSONResponse:
        if path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": meldung("not_found", "Not found.")})
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and root in candidate.parents and candidate != start_page:
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


_mount_frontend(app, get_settings().frontend_dist)

"""Backups: made by hand, on a schedule and before a schema change; downloaded as an encrypted archive; restored
at the next start.

Built after nexcrate's backup service, with the lessons of Nexview's:

* **A copy next to the database is no backup yet.** Both lie on the same volume. ``archive`` makes a downloadable
  AES-256 ZIP (7-Zip and WinRAR open it without nextrmnl); only then is it one.
* **The copy is made with SQLite's backup API**, never by copying the file: the running database is in WAL mode,
  and a file copy misses everything that still sits in the ``-wal`` side file. The copy is written in rollback
  journal mode, so it is one self-contained file.
* **The key goes into the archive.** Server-side secrets (the OIDC client secret, TOTP seeds) are encrypted with
  ``secret.key``; a database without it comes back with those unreadable. Therefore the archive has a password of
  at least twelve characters.
* **The vault entries inside a backup stay encrypted as they are.** Private keys and stored passwords are sealed
  with each account's vault key, which only the account's password unwraps. A backup, an archive or a database
  dump gives none of them away, not to the operator, not to whoever finds the file. That is the point.
* **The manifest lies next to the copy** (``<name>.json``): version, schema fingerprint, kind, note, counts.
* **Only automatic copies are pruned** (``scheduled`` and ``update``); one made by hand stays until deleted.

A restore happens at the next start, not in the running process: SSH sessions and open vaults live in memory,
and a database swapped under them would meet all of it. ``stage_restore`` checks the archive, makes an ``update``
copy of the current state (the way back), lays the files out in ``backups/restore-pending/`` and the process ends;
Docker starts it again, and ``apply_pending`` swaps the files before anything opens the database. A pending folder
without its manifest is a half-written one and is thrown away, never applied.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import signal
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path

import pyzipper

from .. import __version__
from ..config import get_settings
from . import settings_service

logger = logging.getLogger("nextrmnl.backups")

FOLDER_NAME = "backups"
DATABASE = "nextrmnl.db"
KEY = "secret.key"
#: The manifest inside the archive and in the pending folder.
MANIFEST = "nextrmnl-backup.json"
#: Where a checked archive waits for the next start.
PENDING = "restore-pending"
#: What goes into the archive instead of the key when it comes from the environment.
NO_KEY_FILE = "KEY-MISSING.txt"
NO_KEY = (
    "This installation has no secret.key: the key comes from the environment variable NEXTRMNL_SECRET_KEY.\n\n"
    "When restoring, the same value must be set again; otherwise the server-side secrets (OIDC client secret,\n"
    "second-factor seeds) cannot be decrypted. Vault entries are not affected: they open with the account's\n"
    "password only.\n"
)

MANUAL = "manual"
SCHEDULED = "scheduled"
#: Before a schema change and before a restore.
UPDATE = "update"
KINDS = (MANUAL, SCHEDULED, UPDATE)
AUTOMATIC_KINDS = (SCHEDULED, UPDATE)
KEEP_DEFAULT = 5
SCHEDULES = ("off", "daily", "weekly", "monthly")
#: Days between two scheduled copies.
INTERVALS = {"daily": 1, "weekly": 7, "monthly": 30}
#: Local hours a scheduled copy starts in.
NIGHT = range(3, 6)
#: A copy overdue by this many days more starts at any hour: a server that is off at night still gets one.
CATCH_UP_DAYS = 1
#: The job looks once an hour; the first look is an hour after the start.
INTERVAL_SECONDS = 3600
#: Since when the job waits for the first scheduled copy; a setting, so a restart does not reset the wait.
SETTING_WAITING = "backup_waiting_since"

PASSWORD_MIN = 12
#: Upload limit for an archive to restore.
MAX_UPLOAD = 2 * 1024**3
TEMPORARY_PREFIXES = (".archive-", ".upload-")
#: A temporary file older than this was left behind by a stopped process.
TEMPORARY_MAX_AGE = 6 * 3600
_CHUNK = 1024 * 1024
#: ``nextrmnl-YYYY-MM-DD-HHMMSS.db``, with a counter when two copies fall into the same second.
NAME = re.compile(r"^nextrmnl-\d{4}-\d{2}-\d{2}-\d{6}(-\d+)?\.db$")
SQLITE_HEADER = b"SQLite format 3\x00"

#: The schedule and staging a restore share this lock.
_lock = threading.Lock()


class BackupError(Exception):
    """Something is wrong with a backup or an archive. Carries a code for the interface."""

    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(slots=True)
class Manifest:
    version: str
    schema: str
    kind: str
    note: str
    created: str
    accounts: int = 0
    connections: int = 0

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str | bytes) -> Manifest:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise TypeError("manifest is not an object")
        allowed = {item.name for item in fields(cls)}
        manifest = cls(**{key: value for key, value in data.items() if key in allowed})
        texts = ("version", "schema", "kind", "note", "created")
        if not all(isinstance(getattr(manifest, name), str) for name in texts):
            raise TypeError("manifest fields have the wrong type")
        manifest.accounts = int(manifest.accounts)
        manifest.connections = int(manifest.connections)
        return manifest


@dataclass(slots=True)
class Entry:
    name: str
    size: int
    created: str
    kind: str
    note: str
    version: str
    compatible: bool
    reason: str


@dataclass(slots=True)
class Opened:
    manifest: Manifest
    key_in_archive: bool


@dataclass(slots=True)
class Brief:
    """What an archive holds, told without laying anything out."""

    version: str
    created: str
    kind: str
    note: str
    accounts: int
    connections: int
    key_in_archive: bool
    key_from_env: bool
    compatible: bool
    reason: str


# --- Places ---------------------------------------------------------------------------------------------------- #


def folder() -> Path:
    return get_settings().data_dir / FOLDER_NAME


def pending_folder() -> Path:
    return folder() / PENDING


def _manifest_path(copy: Path) -> Path:
    return copy.with_suffix(".json")


def path_of(name: str) -> Path:
    """The copy of this name in the backup folder; refuses anything that is not a plain backup name."""
    if not NAME.match(name):
        raise BackupError("backup_not_found", "There is no such backup.", 404)
    path = folder() / name
    if not path.is_file():
        raise BackupError("backup_not_found", "There is no such backup.", 404)
    return path


# --- Settings -------------------------------------------------------------------------------------------------- #


def schedule(db: object) -> str:
    value = settings_service.get(db, "backup_schedule")  # type: ignore[arg-type]
    return value if value in SCHEDULES else "off"


def keep(db: object) -> int:
    value = settings_service.get(db, "backup_keep")  # type: ignore[arg-type]
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return KEEP_DEFAULT


def _keep_setting() -> int:
    try:
        from ..db import SessionLocal

        with SessionLocal() as db:
            return keep(db)
    except Exception:  # noqa: BLE001 - before the first start there is no settings table
        return KEEP_DEFAULT


# --- Making a copy --------------------------------------------------------------------------------------------- #


def fingerprint(connection: sqlite3.Connection) -> str:
    """A fingerprint over every table and column: what the file looks like, not only who wrote it."""
    parts: list[str] = []
    for (table,) in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall():
        # The name comes from sqlite_master itself, not from outside.
        columns = sorted(row[1] for row in connection.execute(f'PRAGMA table_info("{table}")'))
        parts.append(f"{table}({','.join(columns)})")
    return "sha256:" + hashlib.sha256(";".join(parts).encode("utf-8")).hexdigest()[:32]


def _count(connection: sqlite3.Connection, table: str) -> int:
    present = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    if present is None:
        return 0
    # Only the two fixed names above reach this line.
    return int(connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0])  # noqa: S608


def create(*, kind: str = MANUAL, note: str = "", source: Path | None = None, base: Path | None = None) -> Path:
    """Write a consistent copy through SQLite's backup API and lay the manifest next to it. Automatic copies
    prune the older automatic ones."""
    if kind not in KINDS:
        raise ValueError(kind)
    source_path = source or get_settings().database_path
    if not source_path.is_file():
        raise BackupError("backup_failed", "There is no database to back up yet.", 500)
    target_folder = base or folder()
    target_folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d-%H%M%S")
    target = target_folder / f"nextrmnl-{stamp}.db"
    counter = 2
    while target.exists() or _manifest_path(target).exists():
        target = target_folder / f"nextrmnl-{stamp}-{counter}.db"
        counter += 1
    started = time.monotonic()
    origin = sqlite3.connect(source_path)
    try:
        copy = sqlite3.connect(target)
        try:
            origin.backup(copy)
            # One self-contained file: the copy must not depend on side files of its own.
            copy.execute("PRAGMA journal_mode=DELETE")
            schema = fingerprint(copy)
            accounts = _count(copy, "accounts")
            connections = _count(copy, "connections")
        finally:
            copy.close()
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    finally:
        origin.close()
    manifest = Manifest(
        version=__version__,
        schema=schema,
        kind=kind,
        note=note.strip()[:200],
        created=datetime.now(UTC).isoformat(timespec="seconds"),
        accounts=accounts,
        connections=connections,
    )
    _manifest_path(target).write_text(manifest.to_json(), encoding="utf-8")
    logger.info(
        "Backup created name=%s kind=%s size=%s duration_ms=%d",
        target.name,
        kind,
        target.stat().st_size,
        (time.monotonic() - started) * 1000,
    )
    if kind in AUTOMATIC_KINDS:
        prune(_keep_setting(), base=target_folder)
    return target


def remove(copy: Path) -> None:
    copy.unlink(missing_ok=True)
    _manifest_path(copy).unlink(missing_ok=True)


# --- The list -------------------------------------------------------------------------------------------------- #


def _numbers(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version)) or (0,)


def compatible(version: str) -> tuple[bool, str]:
    """Only a copy of this version or an older one: columns are only ever added, never taken away."""
    if not version:
        return False, "unknown_version"
    if _numbers(version) > _numbers(__version__):
        return False, "backup_newer"
    return True, "ok"


def _manifest_of(copy: Path) -> Manifest:
    """The manifest, or what can be told without one: a file somebody put there by hand, of unknown version."""
    path = _manifest_path(copy)
    if path.is_file():
        try:
            return Manifest.from_json(path.read_text(encoding="utf-8"))
        except (ValueError, TypeError):
            logger.warning("The manifest of %s is unreadable", copy.name)
    created = datetime.fromtimestamp(copy.stat().st_mtime, UTC).isoformat(timespec="seconds")
    return Manifest(version="", schema="", kind=MANUAL, note="", created=created)


def entries(base: Path | None = None) -> list[Entry]:
    """Every copy, newest first."""
    base = base or folder()
    if not base.is_dir():
        return []
    found: list[Entry] = []
    for copy in base.iterdir():
        if not copy.is_file() or not NAME.match(copy.name):
            continue
        manifest = _manifest_of(copy)
        ok, reason = compatible(manifest.version)
        found.append(
            Entry(
                name=copy.name,
                size=copy.stat().st_size,
                created=manifest.created,
                kind=manifest.kind if manifest.kind in KINDS else MANUAL,
                note=manifest.note,
                version=manifest.version,
                compatible=ok,
                reason=reason,
            )
        )
    return sorted(found, key=lambda entry: (entry.created, entry.name), reverse=True)


def prune(keep_count: int = KEEP_DEFAULT, base: Path | None = None) -> int:
    """Only the newest ``keep_count`` automatic copies stay; copies by hand are never pruned."""
    base = base or folder()
    automatic = [entry for entry in entries(base) if entry.kind in AUTOMATIC_KINDS]
    removed = 0
    for entry in automatic[max(1, keep_count) :]:
        try:
            remove(base / entry.name)
            removed += 1
        except OSError:
            logger.warning("Could not remove the old backup %s", entry.name)
    return removed


# --- The schedule ---------------------------------------------------------------------------------------------- #


def _last_scheduled() -> Entry | None:
    return next((entry for entry in entries() if entry.kind == SCHEDULED), None)


def due(every: str, *, now: datetime | None = None, waiting_since: datetime | None = None) -> bool:
    """Whether a scheduled copy is due. The time since the last one comes from the manifests, not from the
    database, which a restore would set back. Without any scheduled copy the first comes the first night, or a day
    after the job began to wait for one at any hour."""
    if every not in INTERVALS:
        return False
    moment = now or datetime.now().astimezone()
    last = _last_scheduled()
    if last is None:
        waited = (moment - waiting_since).total_seconds() / 86400 if waiting_since is not None else 0
        return moment.hour in NIGHT or waited >= CATCH_UP_DAYS
    try:
        previous = datetime.fromisoformat(last.created)
    except ValueError:
        return True
    if previous.tzinfo is None:
        previous = previous.replace(tzinfo=UTC)
    days = (moment - previous).total_seconds() / 86400
    interval = INTERVALS[every] - 0.25
    return days >= interval and (moment.hour in NIGHT or days >= interval + CATCH_UP_DAYS)


def _waiting_since(db: object) -> datetime:
    stored = settings_service.get(db, SETTING_WAITING)  # type: ignore[arg-type]
    try:
        return datetime.fromisoformat(str(stored or ""))
    except ValueError:
        moment = datetime.now(UTC)
        settings_service.save(db, {SETTING_WAITING: moment.isoformat(timespec="seconds")})  # type: ignore[arg-type]
        return moment


def run_job() -> None:
    """Once an hour: sweep what a stopped process left behind, and make a scheduled copy when one is due."""
    from ..db import SessionLocal

    with _lock:
        sweep_temporary()
        with SessionLocal() as db:
            every = schedule(db)
            waiting_since = _waiting_since(db) if every in INTERVALS and _last_scheduled() is None else None
        if due(every, waiting_since=waiting_since):
            logger.info("Scheduled backup due schedule=%s", every)
            create(kind=SCHEDULED)


async def run_forever(stop: asyncio.Event) -> None:
    """The hourly look, first an hour after the start: a start is busy enough, and a restart loop must not make a
    copy each time."""
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=INTERVAL_SECONDS)
            return
        except TimeoutError:
            pass
        try:
            await asyncio.to_thread(run_job)
        except Exception:
            logger.exception("Backup job failed")


# --- The archive ----------------------------------------------------------------------------------------------- #
#
# Written to and read from files in the backup folder, never held whole in memory. Temporary files carry a leading
# dot (``entries`` ignores them); ``sweep_temporary`` removes what a stopped process left behind.


def temporary(prefix: str) -> Path:
    base = folder()
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{prefix}{os.getpid()}-{time.time_ns()}.zip"


def sweep_temporary(now: float | None = None) -> int:
    base = folder()
    if not base.is_dir():
        return 0
    moment = now if now is not None else time.time()
    removed = 0
    for item in base.iterdir():
        stale = item.is_file() and item.name.startswith(TEMPORARY_PREFIXES)
        if stale and moment - item.stat().st_mtime > TEMPORARY_MAX_AGE:
            item.unlink(missing_ok=True)
            removed += 1
    return removed


def archive(name: str, password: str) -> Path:
    """The copy as an AES-256 ZIP with the key and the manifest. Returns a temporary file; the caller removes it
    after sending."""
    if len(password) < PASSWORD_MIN:
        raise BackupError("password_too_short", f"Use at least {PASSWORD_MIN} characters.", 422)
    copy = path_of(name)
    manifest = _manifest_of(copy)
    settings = get_settings()
    target = temporary(".archive-")
    try:
        with pyzipper.AESZipFile(target, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as zipped:
            zipped.setpassword(password.encode("utf-8"))
            zipped.write(copy, DATABASE)
            if settings.key_from_environment():
                zipped.writestr(NO_KEY_FILE, NO_KEY)
            else:
                zipped.writestr(KEY, settings.resolved_secret_key())
            zipped.writestr(MANIFEST, manifest.to_json())
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    logger.info("Backup archived name=%s", copy.name)
    return target


def open_archive(path: Path, password: str) -> Opened:
    """What the archive holds, checked with the password, without laying anything out."""
    try:
        zipped = pyzipper.AESZipFile(path)
    except Exception as exc:
        raise BackupError("archive_invalid", "This is not a backup archive.", 422) from exc
    zipped.setpassword(password.encode("utf-8"))
    try:
        names = set(zipped.namelist())
        if MANIFEST not in names or DATABASE not in names:
            raise BackupError("archive_invalid", "The archive holds no nextrmnl backup.", 422)
        # Reading a member is what checks the password; the manifest is small.
        raw_manifest = zipped.read(MANIFEST)
        with zipped.open(DATABASE) as member:
            header = member.read(len(SQLITE_HEADER))
    except BackupError:
        raise
    except RuntimeError as exc:
        raise BackupError("wrong_password", "The password does not open this archive.", 401) from exc
    except Exception as exc:
        raise BackupError("archive_invalid", "This is not a backup archive.", 422) from exc
    finally:
        zipped.close()
    if header != SQLITE_HEADER:
        raise BackupError("archive_invalid", "The file in the archive is no database.", 422)
    try:
        manifest = Manifest.from_json(raw_manifest)
    except (ValueError, TypeError) as exc:
        raise BackupError("archive_invalid", "The archive lacks a readable manifest.", 422) from exc
    return Opened(manifest, KEY in names)


def _brief(opened: Opened) -> Brief:
    ok, reason = compatible(opened.manifest.version)
    return Brief(
        version=opened.manifest.version,
        created=opened.manifest.created,
        kind=opened.manifest.kind,
        note=opened.manifest.note,
        accounts=opened.manifest.accounts,
        connections=opened.manifest.connections,
        key_in_archive=opened.key_in_archive,
        key_from_env=get_settings().key_from_environment(),
        compatible=ok,
        reason=reason,
    )


def check(path: Path, password: str) -> Brief:
    """Only look: what is it, and may it be restored?"""
    return _brief(open_archive(path, password))


def _refused(brief: Brief) -> BackupError:
    if brief.reason == "backup_newer":
        return BackupError(
            "backup_newer", f"This backup comes from version {brief.version} and is newer than {__version__}."
        )
    return BackupError("unknown_version", "This backup does not say which version it comes from.")


def stage_restore(path: Path, password: str) -> Brief:
    """Check the archive, keep a copy of now, and lay the archive out for the next start. Raises ``BackupError``
    before anything is laid out."""
    opened = open_archive(path, password)
    brief = _brief(opened)
    if not brief.compatible:
        raise _refused(brief)
    with _lock:
        # The way back when the restored backup turns out to be the wrong one.
        if get_settings().database_path.is_file():
            create(kind=UPDATE, note="before restore")
        pending = pending_folder()
        shutil.rmtree(pending, ignore_errors=True)
        pending.mkdir(parents=True)
        try:
            with pyzipper.AESZipFile(path) as zipped:
                zipped.setpassword(password.encode("utf-8"))
                for member in (DATABASE, KEY) if opened.key_in_archive else (DATABASE,):
                    with zipped.open(member) as source, (pending / member).open("wb") as sink:
                        shutil.copyfileobj(source, sink, _CHUNK)
            if (pending / KEY).is_file():
                try:
                    os.chmod(pending / KEY, 0o600)
                except OSError:
                    pass
            # Last: the manifest says the rest is complete. A start finding a folder without it throws it away.
            (pending / MANIFEST).write_text(opened.manifest.to_json(), encoding="utf-8")
        except BaseException:
            shutil.rmtree(pending, ignore_errors=True)
            raise
    logger.info("Backup staged for the next start version=%s created=%s", brief.version, brief.created)
    return brief


def restart_soon(delay: float = 1.5) -> None:
    """End the process shortly, after the answer went out; Docker starts it again (``restart: unless-stopped``).

    On POSIX a SIGTERM lets uvicorn shut down in order; should that not end the process, ``os._exit`` does. On
    Windows there is no signal to send, so the process ends directly with a code that says why.
    """

    def stop() -> None:
        time.sleep(delay)
        logger.info("nextrmnl stops to restore a backup")
        if os.name == "nt":
            os._exit(3)
        try:
            os.kill(os.getpid(), signal.SIGTERM)
        except OSError:
            os._exit(0)
        time.sleep(10)
        os._exit(0)

    threading.Thread(target=stop, name="restart-for-restore", daemon=True).start()


def apply_pending() -> bool:
    """At the start, before anything opens the database: swap in a staged backup. Returns whether one was."""
    pending = pending_folder()
    if not pending.is_dir():
        return False
    if not (pending / MANIFEST).is_file() or not (pending / DATABASE).is_file():
        logger.warning("An incomplete restore was found and thrown away")
        shutil.rmtree(pending, ignore_errors=True)
        return False
    try:
        manifest = Manifest.from_json((pending / MANIFEST).read_text(encoding="utf-8"))
    except (ValueError, TypeError):
        logger.warning("A restore with an unreadable manifest was found and thrown away")
        shutil.rmtree(pending, ignore_errors=True)
        return False
    settings = get_settings()
    target = settings.database_path
    target.parent.mkdir(parents=True, exist_ok=True)
    # Left behind, SQLite would read the old database's WAL into the restored one.
    for suffix in ("-wal", "-shm", "-journal"):
        target.with_name(target.name + suffix).unlink(missing_ok=True)
    shutil.copyfile(pending / DATABASE, target)
    key = pending / KEY
    if key.is_file():
        if settings.key_from_environment():
            logger.warning(
                "The restored backup holds a secret.key, but NEXTRMNL_SECRET_KEY is set: the variable wins. "
                "Server-side secrets stay unreadable unless it holds the same value."
            )
        else:
            shutil.copyfile(key, settings.data_dir / KEY)
            try:
                os.chmod(settings.data_dir / KEY, 0o600)
            except OSError:
                pass
            # Whatever this process remembered of the key was the old one.
            settings._remembered_key = None
    shutil.rmtree(pending, ignore_errors=True)
    logger.info(
        "Backup restored version=%s created=%s accounts=%s connections=%s",
        manifest.version,
        manifest.created,
        manifest.accounts,
        manifest.connections,
    )
    return True

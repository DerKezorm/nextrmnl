"""Looks up whether there is a newer nextrmnl release.

At most once a day the public GitHub API is asked for the latest release. Nothing is sent but the request itself.
The check is off by default; the operator turns it on under Settings, Security. Whatever fails here must not
break the About page: then it simply says nothing.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

from .. import __version__

logger = logging.getLogger("nextrmnl.updates")

REPO = "DerKezorm/nextrmnl"
REPO_URL = f"https://github.com/{REPO}"
RELEASES_URL = f"{REPO_URL}/releases"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
CHECK_INTERVAL = timedelta(hours=24)
TIMEOUT = httpx.Timeout(6.0, connect=4.0)
_VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")


@dataclass(frozen=True)
class UpdateStatus:
    current: str
    latest: str | None = None
    update_available: bool = False
    checked_at: datetime | None = None
    release_url: str = RELEASES_URL


_cached: UpdateStatus | None = None
_lock = asyncio.Lock()


def parse_version(text: str) -> tuple[int, int, int] | None:
    match = _VERSION_PATTERN.match(text.strip())
    return None if match is None else (int(match[1]), int(match[2]), int(match[3]))


def is_newer(latest: str, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    return a is not None and b is not None and a > b


async def _fetch() -> str | None:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        response = await client.get(
            API_URL, headers={"Accept": "application/vnd.github+json", "User-Agent": f"nextrmnl/{__version__}"}
        )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return ((response.json() or {}).get("tag_name") or "").strip() or None


async def status(*, enabled: bool, force: bool = False) -> UpdateStatus:
    global _cached
    if not enabled:
        return UpdateStatus(current=__version__)
    now = datetime.now(UTC)
    fresh = _cached is not None and _cached.checked_at is not None and now - _cached.checked_at < CHECK_INTERVAL
    if fresh and not force:
        return _cached  # type: ignore[return-value]
    async with _lock:
        try:
            latest = await _fetch()
        except (httpx.HTTPError, ValueError) as error:
            logger.warning("Update check failed: %s", error)
            return _cached or UpdateStatus(current=__version__)
        _cached = UpdateStatus(
            current=__version__,
            latest=latest,
            update_available=bool(latest) and is_newer(latest or "", __version__),
            checked_at=now,
            release_url=RELEASES_URL,
        )
        logger.info("Update check done current=%s latest=%s", __version__, latest or "-")
        return _cached


def forget() -> None:
    global _cached
    _cached = None

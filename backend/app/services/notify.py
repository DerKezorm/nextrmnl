"""Notifications to the operator's inbox: nexsift, Gotify, ntfy or any webhook.

Another way out of the house, so it is closed until the operator opens it, like the update check and the API
keys. What goes out is a title and a sentence: who, what, where. Never terminal content, passwords, keys, tokens
or file names; the same rules as for the log.

Four groups, each can be switched off: ``security`` (an account locked, a changed host key, a second factor reset,
a new API key), ``signin`` (every sign-in), ``sessions`` (every opened shell), ``operations`` (a failed backup, a
new version). Sending happens on two worker threads with short timeouts and without following redirects; a slow
or dead inbox never holds up a sign-in or a terminal, and when too much piles up, the rest is dropped and counted.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from .. import __version__, crypto
from ..db import SessionLocal
from . import settings_service

logger = logging.getLogger("nextrmnl.notify")

SECURITY = "security"
SIGNIN = "signin"
SESSIONS = "sessions"
OPERATIONS = "operations"
CATEGORIES = (SECURITY, SIGNIN, SESSIONS, OPERATIONS)

GOTIFY = "gotify"
NTFY = "ntfy"
WEBHOOK = "webhook"
KINDS = (GOTIFY, NTFY, WEBHOOK)

#: Gotify's scale, which nexsift reads too: 8 and up is urgent, 5 is normal, 2 is low.
PRIORITY = {SECURITY: 8, SIGNIN: 4, SESSIONS: 2, OPERATIONS: 5}
TIMEOUT = httpx.Timeout(5.0, connect=3.0)
MAX_PENDING = 50
MAX_TEXT = 500

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="notify")
_pending = 0
_dropped = 0
_lock = threading.Lock()


@dataclass(frozen=True)
class Target:
    kind: str
    url: str
    token: str


class NotifyError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def valid_url(url: str) -> bool:
    parts = urlsplit(url.strip())
    return parts.scheme in ("http", "https") and bool(parts.hostname) and not parts.username and not parts.password


def _target() -> tuple[Target | None, list[str]]:
    with SessionLocal() as db:
        values = settings_service.get_all(db)
    if not values.get("notify_enabled"):
        return None, []
    url = str(values.get("notify_url") or "").strip()
    kind = str(values.get("notify_kind") or GOTIFY)
    if kind not in KINDS or not valid_url(url):
        return None, []
    stored = str(values.get("notify_token_enc") or "")
    try:
        token = crypto.decrypt_secret(stored) if stored else ""
    except Exception:  # noqa: BLE001
        logger.warning("Notification token unreadable; the server secret has changed")
        return None, []
    events = values.get("notify_events")
    return Target(kind, url, token), list(events) if isinstance(events, list) else list(CATEGORIES)


def _clip(text: str) -> str:
    return " ".join(text.split())[:MAX_TEXT]


def request_for(target: Target, category: str, title: str, message: str) -> tuple[str, dict[str, str], Any, Any]:
    """URL, headers and body for one message, per kind. Returns (url, headers, json, content)."""
    title, message = _clip(title), _clip(message)
    priority = PRIORITY.get(category, 5)
    headers = {"User-Agent": f"nextrmnl/{__version__}"}
    if target.kind == GOTIFY:
        # Gotify and nexsift's Gotify door: the application token as a header, never in the address.
        if target.token:
            headers["X-Gotify-Key"] = target.token
        body = {"title": title, "message": message, "priority": priority}
        return target.url.rstrip("/") + "/message", headers, body, None
    if target.kind == NTFY:
        # ntfy: the topic is part of the address; the token is optional (access control on the server).
        if target.token:
            headers["Authorization"] = f"Bearer {target.token}"
        headers["Title"] = title.encode("ascii", "replace").decode("ascii")
        headers["Priority"] = {8: "5", 5: "3", 4: "3", 2: "2"}.get(priority, "3")
        headers["Tags"] = f"nextrmnl,{category}"
        return target.url, headers, None, message.encode("utf-8")
    if target.token:
        headers["Authorization"] = f"Bearer {target.token}"
    body = {"source": "nextrmnl", "category": category, "title": title, "message": message, "priority": priority}
    return target.url, headers, body, None


_client: httpx.Client | None = None


def _http() -> httpx.Client:
    """One client for all messages: building one costs most of a second (TLS context), sending one costs little.
    httpx clients are safe to share between the worker threads. The environment counts (``SSL_CERT_FILE`` for a
    home CA, a proxy if the operator set one)."""
    global _client
    with _lock:
        if _client is None:
            _client = httpx.Client(timeout=TIMEOUT, follow_redirects=False)
        return _client


def deliver(target: Target, category: str, title: str, message: str) -> int:
    url, headers, body, content = request_for(target, category, title, message)
    response = _http().post(url, headers=headers, json=body, content=content)
    if response.status_code >= 300:
        raise NotifyError("notify_refused", f"The inbox answered {response.status_code}.")
    return response.status_code


def _send(target: Target, category: str, title: str, message: str) -> None:
    global _pending
    try:
        status = deliver(target, category, title, message)
        logger.debug("Notification sent kind=%s category=%s status=%s", target.kind, category, status)
    except NotifyError as error:
        logger.warning("Notification refused kind=%s category=%s: %s", target.kind, category, error)
    except httpx.HTTPError as error:
        # The type only: an httpx message may carry the address, and with ntfy the address is the topic.
        logger.warning("Notification failed kind=%s category=%s: %s", target.kind, category, type(error).__name__)
    finally:
        with _lock:
            _pending -= 1


def emit(category: str, title: str, message: str) -> None:
    """Queues a message if notifications are on and the group is chosen. Never raises, never waits."""
    global _pending, _dropped
    try:
        target, events = _target()
    except Exception:
        logger.exception("Notification settings unreadable")
        return
    if target is None or category not in events:
        return
    with _lock:
        if _pending >= MAX_PENDING:
            _dropped += 1
            if _dropped in (1, 10, 100) or _dropped % 1000 == 0:
                logger.warning("Notification inbox does not keep up; %s messages dropped so far", _dropped)
            return
        _pending += 1
    _executor.submit(_send, target, category, title, message)


def wait_idle(timeout: float = 5.0) -> bool:
    """Waits until every queued message is through; for shutting down and for the tests."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with _lock:
            if _pending <= 0:
                return True
        time.sleep(0.02)
    return False


def test(category: str = SECURITY) -> int:
    """One message right away, for the button in the settings; raises with a code the frontend translates."""
    target, _events = _target()
    if target is None:
        raise NotifyError("notify_off", "Notifications are off or not set up.")
    try:
        return deliver(target, category, "nextrmnl", "Test message: notifications reach this inbox.")
    except httpx.HTTPError as error:
        raise NotifyError("notify_unreachable", f"The inbox did not answer ({type(error).__name__}).") from error

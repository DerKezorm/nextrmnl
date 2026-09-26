"""The second factor: time-based one-time codes (RFC 6238) plus recovery codes.

* The seed is stored encrypted with the server secret (``crypto.encrypt_secret``), the recovery codes as
  SHA-256 hashes. Seed and codes are shown once, at enrolment, and never logged.
* Enrolment takes two steps. ``begin_enrolment`` draws a seed and keeps it in memory for ten minutes;
  confirming needs a code from the app (proves the app holds the seed) and the account's password (proves the
  person at the keyboard is the owner). Only then is the seed stored.
* A sign-in with a second factor takes two steps as well. The password step opens nothing: it parks the
  unwrapped vault key in memory under a random pending token and hands the browser that token in a cookie. The
  code step turns the parked key into the open vault and the browser session. Five wrong codes end the pending
  sign-in, and the brake for the sender applies as with passwords.
* A code counts once. The time step of the last accepted code is stored, and a code at or before that step is
  refused: something read over a shoulder cannot be replayed within its thirty seconds.

Implemented here rather than with a library: RFC 6238 is thirty lines, and the tests hold the RFC's own vectors.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import secrets
import struct
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import quote

import segno
from sqlalchemy.orm import Session

from .. import crypto
from ..models import SIGN_IN_PASSWORD, Account
from . import settings_service

ISSUER = "nextrmnl"
STEP_SECONDS = 30
DIGITS = 6
#: Steps before and after the current one that are still accepted: clocks drift, people are slow.
WINDOW = 1
SEED_BYTES = 20
ENROLMENT_SECONDS = 600
PENDING_SECONDS = 300
#: Wrong codes before a pending sign-in is thrown away and the password has to be given again.
MAX_ATTEMPTS = 5
RECOVERY_CODES = 8
#: No 0, o, 1, l, i: the codes get typed from paper.
RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
RECOVERY_LENGTH = 10


# --- Codes ----------------------------------------------------------------------------------------------------- #


def generate_seed() -> str:
    """A fresh seed, base32 without padding, the way authenticator apps take it."""
    return base64.b32encode(secrets.token_bytes(SEED_BYTES)).decode("ascii").rstrip("=")


class SeedUnreadable(Exception):
    """The stored seed cannot be opened: the server secret is not the one that sealed it."""


def _seed_bytes(seed: str) -> bytes:
    cleaned = seed.strip().replace(" ", "").upper()
    if not cleaned:
        # An empty seed would make every code computable by anyone; it must never verify anything.
        raise SeedUnreadable()
    return base64.b32decode(cleaned + "=" * (-len(cleaned) % 8))


def code_at(seed: str, moment: float, step: int = STEP_SECONDS, digits: int = DIGITS) -> str:
    """The code for a point in time, RFC 6238 with HMAC-SHA1."""
    counter = int(moment) // step
    digest = hmac.new(_seed_bytes(seed), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(number % (10**digits)).zfill(digits)


def normalize_code(code: str) -> str:
    return code.strip().replace(" ", "").replace("-", "")


def verify_code(seed: str, code: str, *, after_step: int = 0, now: float | None = None) -> int | None:
    """The time step the code belongs to, or None. Steps at or before ``after_step`` are refused (replay)."""
    typed = normalize_code(code)
    if len(typed) != DIGITS or not typed.isdigit():
        return None
    moment = time.time() if now is None else now
    current = int(moment) // STEP_SECONDS
    matched: int | None = None
    # Every candidate is compared, in constant time each, so that the timing does not tell which one matched.
    for candidate in range(current - WINDOW, current + WINDOW + 1):
        expected = code_at(seed, candidate * STEP_SECONDS)
        if hmac.compare_digest(expected, typed) and candidate > after_step:
            matched = candidate
    return matched


def provisioning_uri(seed: str, account_name: str) -> str:
    label = quote(f"{ISSUER}:{account_name}", safe=":")
    return f"otpauth://totp/{label}?secret={seed}&issuer={ISSUER}&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}"


def qr_svg(uri: str) -> str:
    """The QR code as SVG, light modules transparent so it sits on any background.

    ⚠️ With the SVG namespace. The frontend shows it as a ``data:`` image, and
    a browser draws an SVG in an ``<img>`` only when it names its namespace.
    ``svg_inline`` leaves it out (it is meant for markup inside HTML), and the
    dialog showed a broken image where the code belonged.
    """
    out = io.BytesIO()
    code = segno.make(uri, error="m")
    code.save(out, kind="svg", scale=4, dark="#e5e7eb", light=None, border=2, xmldecl=False, svgns=True)
    return out.getvalue().decode("utf-8")


# --- Recovery codes -------------------------------------------------------------------------------------------- #


def generate_recovery_codes(count: int = RECOVERY_CODES) -> list[str]:
    codes = []
    for _ in range(count):
        raw = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(RECOVERY_LENGTH))
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes


def hash_recovery(code: str) -> str:
    cleaned = code.strip().replace("-", "").replace(" ", "").lower()
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()


def recovery_hashes(codes: list[str]) -> str:
    return json.dumps([hash_recovery(code) for code in codes])


def load_recovery(stored: str) -> list[str]:
    try:
        data = json.loads(stored or "[]")
    except ValueError:
        return []
    return [entry for entry in data if isinstance(entry, str)] if isinstance(data, list) else []


def use_recovery(stored: str, code: str) -> str | None:
    """The stored list without the used code, or None when the code is not in it."""
    hashes = load_recovery(stored)
    wanted = hash_recovery(code)
    remaining = [entry for entry in hashes if not hmac.compare_digest(entry, wanted)]
    if len(remaining) == len(hashes):
        return None
    return json.dumps(remaining)


def setup_required(db: Session, account: Account) -> bool:
    """The operator requires a second factor, and this password account has none yet. Such an account reaches
    its own account page and nothing else until it enrols."""
    return (
        bool(settings_service.get(db, "two_factor_required"))
        and account.sign_in == SIGN_IN_PASSWORD
        and not account.totp_secret_enc
    )


# --- Seeds at rest --------------------------------------------------------------------------------------------- #


def seal_seed(seed: str) -> str:
    return crypto.encrypt_secret(seed)


def open_seed(stored: str) -> str:
    """The seed, or ``SeedUnreadable`` when the server secret changed: then the second factor fails closed,
    the way the OIDC secret does, instead of accepting codes derived from nothing."""
    seed = crypto.decrypt_secret(stored)
    if not seed:
        raise SeedUnreadable()
    return seed


# --- What waits in memory -------------------------------------------------------------------------------------- #


@dataclass
class PendingSignIn:
    account_id: int
    vault_key: bytes | None
    expires: float
    attempts: int = 0


@dataclass
class _Enrolment:
    seed: str
    expires: float


@dataclass
class _Store:
    enrolments: dict[int, _Enrolment] = field(default_factory=dict)
    pending: dict[str, PendingSignIn] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)


_store = _Store()


def begin_enrolment(account_id: int) -> str:
    seed = generate_seed()
    with _store.lock:
        _store.enrolments[account_id] = _Enrolment(seed=seed, expires=time.monotonic() + ENROLMENT_SECONDS)
    return seed


def pending_seed(account_id: int) -> str | None:
    with _store.lock:
        entry = _store.enrolments.get(account_id)
        if entry is None:
            return None
        if entry.expires <= time.monotonic():
            del _store.enrolments[account_id]
            return None
        return entry.seed


def drop_enrolment(account_id: int) -> None:
    with _store.lock:
        _store.enrolments.pop(account_id, None)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def start_pending(account_id: int, vault_key: bytes | None) -> str:
    """Parks the password step and returns the token for the cookie."""
    token = secrets.token_urlsafe(32)
    with _store.lock:
        # One pending sign-in per account: a second password step replaces the first.
        for key, entry in list(_store.pending.items()):
            if entry.account_id == account_id:
                del _store.pending[key]
        _store.pending[_hash(token)] = PendingSignIn(
            account_id=account_id, vault_key=vault_key, expires=time.monotonic() + PENDING_SECONDS
        )
    return token


def get_pending(token: str | None) -> PendingSignIn | None:
    if not token:
        return None
    with _store.lock:
        entry = _store.pending.get(_hash(token))
        if entry is None:
            return None
        if entry.expires <= time.monotonic():
            del _store.pending[_hash(token)]
            return None
        return entry


def fail_pending(token: str) -> bool:
    """Counts a wrong code. Returns whether the pending sign-in still stands."""
    with _store.lock:
        entry = _store.pending.get(_hash(token))
        if entry is None:
            return False
        entry.attempts += 1
        if entry.attempts >= MAX_ATTEMPTS:
            del _store.pending[_hash(token)]
            return False
        return True


def finish_pending(token: str) -> PendingSignIn | None:
    """Takes the pending sign-in out of the store; the caller turns it into a session."""
    with _store.lock:
        return _store.pending.pop(_hash(token), None)


def forget_account(account_id: int) -> None:
    """Nothing waits for an account whose second factor was just reset or that was deleted."""
    with _store.lock:
        _store.enrolments.pop(account_id, None)
        for key, entry in list(_store.pending.items()):
            if entry.account_id == account_id:
                del _store.pending[key]


def sweep() -> int:
    now = time.monotonic()
    with _store.lock:
        stale_enrolments = [key for key, entry in _store.enrolments.items() if entry.expires <= now]
        stale_pending = [key for key, entry in _store.pending.items() if entry.expires <= now]
        for key in stale_enrolments:
            del _store.enrolments[key]
        for key in stale_pending:
            del _store.pending[key]
    return len(stale_enrolments) + len(stale_pending)


def clear_for_tests() -> None:
    with _store.lock:
        _store.enrolments.clear()
        _store.pending.clear()

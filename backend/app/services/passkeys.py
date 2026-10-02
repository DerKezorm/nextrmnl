"""Passkeys and security keys as the second factor (WebAuthn), checked by ``py_webauthn``.

The relying party is nextrmnl's own address: the public address from the settings when set, otherwise the one the
browser used. Browsers offer WebAuthn only on HTTPS and on localhost; on ``http://192.0.2.10`` it is not there,
and the API says so instead of failing halfway.

Challenges live in memory for five minutes: one per account for adding a key, one per parked password step for
signing in. Nothing about a challenge reaches the log; credential ids appear only as their first characters.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from ..models import Account, Passkey, utcnow
from . import settings_service

logger = logging.getLogger("nextrmnl.auth")

CHALLENGE_SECONDS = 300
MAX_PASSKEYS = 10
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


class PasskeyError(Exception):
    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


@dataclass(frozen=True)
class Party:
    rp_id: str
    origin: str


@dataclass
class _Challenge:
    value: bytes
    expires: float


_lock = threading.Lock()
_registrations: dict[int, _Challenge] = {}
_sign_ins: dict[str, _Challenge] = {}


def party(db: Session, request: Request) -> Party:
    """Who the browser talks to. Raises when the browser would not offer WebAuthn there."""
    base = settings_service.public_url(db)
    if base:
        parts = urlsplit(base)
        scheme, host, netloc = parts.scheme, parts.hostname or "", parts.netloc
    else:
        scheme = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip() or "http"
        netloc = request.headers.get("host") or request.url.netloc
        host = urlsplit(f"{scheme}://{netloc}").hostname or ""
    if not host:
        raise PasskeyError("passkeys_need_https", "Passkeys need nextrmnl on HTTPS.", 409)
    if scheme != "https" and host not in LOCAL_HOSTS:
        raise PasskeyError("passkeys_need_https", "Passkeys need nextrmnl on HTTPS (or localhost).", 409)
    return Party(rp_id=host, origin=f"{scheme}://{netloc}")


def listing(db: Session, account: Account) -> list[Passkey]:
    return list(db.scalars(select(Passkey).where(Passkey.account_id == account.id).order_by(Passkey.created_at)))


def count(db: Session, account_id: int) -> int:
    return len(list(db.scalars(select(Passkey.id).where(Passkey.account_id == account_id))))


def _user_id(account: Account) -> bytes:
    # Stable per account and meaningless outside this installation; the name is shown, not used as the id.
    return f"nextrmnl:{account.id}".encode()


def _descriptors(keys: list[Passkey]) -> list[PublicKeyCredentialDescriptor]:
    return [PublicKeyCredentialDescriptor(id=key.credential_id) for key in keys]


def _take(store: dict[Any, _Challenge], key: Any) -> bytes | None:
    with _lock:
        entry = store.pop(key, None)
    if entry is None or entry.expires <= time.monotonic():
        return None
    return entry.value


def begin_registration(db: Session, request: Request, account: Account) -> str:
    where = party(db, request)
    keys = listing(db, account)
    if len(keys) >= MAX_PASSKEYS:
        raise PasskeyError("too_many_passkeys", f"At most {MAX_PASSKEYS} passkeys per account.")
    challenge = secrets.token_bytes(32)
    options = generate_registration_options(
        rp_id=where.rp_id,
        rp_name="nextrmnl",
        user_id=_user_id(account),
        user_name=account.name,
        challenge=challenge,
        timeout=CHALLENGE_SECONDS * 1000,
        exclude_credentials=_descriptors(keys),
        # A second factor, not a sign-in of its own: the key need not live on the device as a discoverable one.
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.DISCOURAGED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
    )
    with _lock:
        _registrations[account.id] = _Challenge(challenge, time.monotonic() + CHALLENGE_SECONDS)
    return options_to_json(options)


def finish_registration(
    db: Session, request: Request, account: Account, credential: dict[str, Any], name: str
) -> Passkey:
    where = party(db, request)
    challenge = _take(_registrations, account.id)
    if challenge is None:
        raise PasskeyError("passkey_expired", "That took too long. Start again.", 410)
    try:
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=where.rp_id,
            expected_origin=where.origin,
        )
    except WebAuthnException as error:
        logger.warning("Passkey registration refused account=%s: %s", account.name, type(error).__name__)
        raise PasskeyError("passkey_invalid", "The browser's answer did not check out.") from error
    if db.scalar(select(Passkey).where(Passkey.credential_id == verified.credential_id)) is not None:
        raise PasskeyError("passkey_exists", "This key is already registered.", 409)
    transports = (
        credential.get("response", {}).get("transports") if isinstance(credential.get("response"), dict) else []
    )
    row = Passkey(
        account_id=account.id,
        name=name.strip()[:64] or "Passkey",
        credential_id=verified.credential_id,
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        transports=[str(t)[:32] for t in transports][:8] if isinstance(transports, list) else [],
    )
    db.add(row)
    db.commit()
    logger.info("Passkey added account=%s name=%s", account.name, row.name)
    return row


def begin_sign_in(db: Session, request: Request, account: Account, pending_token: str) -> str:
    where = party(db, request)
    keys = listing(db, account)
    if not keys:
        raise PasskeyError("passkey_none", "This account has no passkey.", 409)
    challenge = secrets.token_bytes(32)
    options = generate_authentication_options(
        rp_id=where.rp_id,
        challenge=challenge,
        timeout=CHALLENGE_SECONDS * 1000,
        allow_credentials=_descriptors(keys),
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    with _lock:
        _sign_ins[pending_token] = _Challenge(challenge, time.monotonic() + CHALLENGE_SECONDS)
    return options_to_json(options)


def finish_sign_in(
    db: Session, request: Request, account: Account, pending_token: str, credential: dict[str, Any]
) -> bool:
    """True when the key belongs to the account and its signature checks out. Each challenge counts once."""
    where = party(db, request)
    challenge = _take(_sign_ins, pending_token)
    if challenge is None:
        return False
    raw = credential.get("rawId") or credential.get("id")
    try:
        credential_id = base64url_to_bytes(str(raw))
    except Exception:  # noqa: BLE001
        return False
    key = db.scalar(select(Passkey).where(Passkey.account_id == account.id, Passkey.credential_id == credential_id))
    if key is None:
        logger.warning("Passkey sign-in with an unknown key account=%s", account.name)
        return False
    try:
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=where.rp_id,
            expected_origin=where.origin,
            credential_public_key=key.public_key,
            credential_current_sign_count=key.sign_count,
        )
    except WebAuthnException as error:
        logger.warning("Passkey sign-in refused account=%s key=%s: %s", account.name, key.name, type(error).__name__)
        return False
    key.sign_count = verified.new_sign_count
    key.last_used_at = utcnow()
    db.commit()
    return True


def remove(db: Session, account: Account, passkey_id: int) -> bool:
    row = db.get(Passkey, passkey_id)
    if row is None or row.account_id != account.id:
        return False
    db.delete(row)
    db.commit()
    logger.info("Passkey removed account=%s name=%s", account.name, row.name)
    return True


def remove_all(db: Session, account_id: int) -> int:
    rows = list(db.scalars(select(Passkey).where(Passkey.account_id == account_id)))
    for row in rows:
        db.delete(row)
    db.commit()
    forget(account_id)
    return len(rows)


def forget(account_id: int) -> None:
    with _lock:
        _registrations.pop(account_id, None)


def forget_sign_in(pending_token: str) -> None:
    with _lock:
        _sign_ins.pop(pending_token, None)


def view(row: Passkey) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "created_at": row.created_at.isoformat(),
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
        "credential": bytes_to_base64url(row.credential_id)[:8],
    }

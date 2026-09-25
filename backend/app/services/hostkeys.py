"""Trusted host keys: one per host and port, shared by every account of the installation.

This is nextrmnl's known_hosts. A key is stored only after a person looked at its fingerprint and accepted it;
nothing here trusts a key on its own. The comparison is done on the public key blob, not on the fingerprint:
the blob is the key, the fingerprint is a picture of it for humans.

The host is stored lowercased so that ``NAS.example.com`` and ``nas.example.com`` are the same machine.
"""

from __future__ import annotations

import logging

import asyncssh
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import HostKey, utcnow

logger = logging.getLogger("nextrmnl.hostkeys")

KNOWN = "known"
NEW = "new"
CHANGED = "changed"


def normalize_host(host: str) -> str:
    return host.strip().lower().rstrip(".")


def public_blob(key: asyncssh.SSHKey) -> str:
    """The base64 blob of the public key, the second field of a known_hosts line."""
    return key.export_public_key("openssh").decode("ascii").split()[1]


def stored(db: Session, host: str, port: int) -> HostKey | None:
    return db.scalar(select(HostKey).where(HostKey.host == normalize_host(host), HostKey.port == port))


def compare(db: Session, host: str, port: int, key: asyncssh.SSHKey) -> tuple[str, HostKey | None]:
    """``known`` when the stored key is this key, ``new`` when nothing is stored, ``changed`` otherwise."""
    row = stored(db, host, port)
    if row is None:
        return NEW, None
    if row.key_type == key.get_algorithm() and row.public_key == public_blob(key):
        return KNOWN, row
    return CHANGED, row


def trust(db: Session, host: str, port: int, key: asyncssh.SSHKey, by_account_name: str) -> HostKey:
    """Stores the key, replacing a changed one. Only called after a person accepted the fingerprint."""
    row = stored(db, host, port)
    replaced = row is not None
    if row is None:
        row = HostKey(host=normalize_host(host), port=port, key_type="", public_key="", fingerprint="")
        db.add(row)
    row.key_type = key.get_algorithm()
    row.public_key = public_blob(key)
    row.fingerprint = key.get_fingerprint()
    row.trusted_at = utcnow()
    row.trusted_by = by_account_name[:64]
    db.commit()
    logger.info(
        "Host key %s for %s:%s type=%s fingerprint=%s by=%s",
        "replaced" if replaced else "trusted",
        row.host,
        port,
        row.key_type,
        row.fingerprint,
        by_account_name,
    )
    return row


def forget(db: Session, host: str, port: int) -> bool:
    row = stored(db, host, port)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    logger.info("Host key forgotten for %s:%s", normalize_host(host), port)
    return True


def stored_key(row: HostKey) -> asyncssh.SSHKey | None:
    """The stored public key as an object, or None if the row cannot be read."""
    try:
        return asyncssh.import_public_key(f"{row.key_type} {row.public_key}")
    except (asyncssh.KeyImportError, ValueError):
        return None

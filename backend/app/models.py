"""The data model: accounts, sessions, vaults, connections, host keys, session history, settings.

What is secret is stored encrypted and never in the clear:

* ``Account.vault_wrapped`` is the vault key, wrapped with a key derived from the account's password.
  The server keeps the unwrapped key only in memory while the vault is open (``services/vault.py``).
* ``VaultKey.private_key_enc`` and ``VaultPassword.password_enc`` are encrypted with that vault key.
  Without the account's password nobody reads them, the operator included.
* Server-side secrets (OIDC client secret, TOTP seeds) are encrypted with ``secret.key``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(UTC)


class UtcDateTime(TypeDecorator[datetime]):
    """SQLite forgets the time zone. Stored as UTC, read back as UTC with the zone attached."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        return None if value is None else value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    pass


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=True)


OPERATOR = "operator"
MEMBER = "member"
ROLES = (OPERATOR, MEMBER)

SIGN_IN_PASSWORD = "password"
SIGN_IN_OIDC = "oidc"


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    role: Mapped[str] = mapped_column(String(16), default=MEMBER)
    sign_in: Mapped[str] = mapped_column(String(16), default=SIGN_IN_PASSWORD)
    #: Argon2id. Empty for accounts that only sign in via OIDC.
    password_hash: Mapped[str] = mapped_column(Text, default="")
    email: Mapped[str] = mapped_column(String(255), default="")
    oidc_subject: Mapped[str] = mapped_column(String(255), default="")
    #: TOTP seed, encrypted with the server secret. Empty: no second factor.
    totp_secret_enc: Mapped[str] = mapped_column(Text, default="")
    #: Salt and wrapped vault key. Both empty until the vault is set up (OIDC accounts choose a vault password).
    vault_salt: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    vault_wrapped: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    #: Terminal and clipboard preferences, free JSON for the frontend.
    prefs: Mapped[Any] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    @property
    def vault_ready(self) -> bool:
        return self.vault_wrapped is not None and self.vault_salt is not None


class AuthSession(Base):
    """A browser session. Only the hash of the token is stored; the token itself lives in the cookie."""

    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    last_seen_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(255), default="")


class Invite(Base):
    __tablename__ = "invites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    #: Suggested name, may be changed when accepting.
    name: Mapped[str] = mapped_column(String(64), default="")
    role: Mapped[str] = mapped_column(String(16), default=MEMBER)
    created_by: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)


KEY_TYPES = ("ed25519", "rsa", "ecdsa")


class VaultKey(Base):
    __tablename__ = "vault_keys"
    __table_args__ = (UniqueConstraint("account_id", "name", name="uq_vault_key_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    key_type: Mapped[str] = mapped_column(String(16))
    #: Bits for RSA, curve size for ECDSA, 256 for Ed25519.
    bits: Mapped[int] = mapped_column(Integer, default=0)
    fingerprint: Mapped[str] = mapped_column(String(80))
    public_key: Mapped[str] = mapped_column(Text)
    #: The private key in OpenSSH format, encrypted with the vault key.
    private_key_enc: Mapped[bytes] = mapped_column(LargeBinary)
    #: The key file itself carries a passphrase; nextrmnl asks for it on every connection.
    has_passphrase: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class VaultPassword(Base):
    __tablename__ = "vault_passwords"
    __table_args__ = (UniqueConstraint("account_id", "connection_id", name="uq_vault_password"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey("connections.id", ondelete="CASCADE"), index=True)
    password_enc: Mapped[bytes] = mapped_column(LargeBinary)
    changed_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


AUTH_KEY = "key"
AUTH_PASSWORD = "password"
AUTH_ASK = "ask"
AUTH_METHODS = (AUTH_KEY, AUTH_PASSWORD, AUTH_ASK)


class Connection(Base):
    __tablename__ = "connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    group: Mapped[str] = mapped_column(String(64), default="")
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer, default=22)
    user: Mapped[str] = mapped_column(String(64), default="root")
    auth: Mapped[str] = mapped_column(String(16), default=AUTH_KEY)
    #: The owner's key. Whoever the connection is shared with signs in with their own vault.
    key_id: Mapped[int | None] = mapped_column(ForeignKey("vault_keys.id", ondelete="SET NULL"), nullable=True)
    jump_id: Mapped[int | None] = mapped_column(ForeignKey("connections.id", ondelete="SET NULL"), nullable=True)
    keepalive: Mapped[bool] = mapped_column(Boolean, default=True)
    start_command: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


class ConnectionShare(Base):
    """Name, address and settings are shared; access never is.

    The member signs in with their own user name and their own key or password: ``user`` empty means the
    owner's user name, ``auth`` is the member's method, ``key_id`` a key from the member's own vault.
    """

    __tablename__ = "connection_shares"

    connection_id: Mapped[int] = mapped_column(ForeignKey("connections.id", ondelete="CASCADE"), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True)
    user: Mapped[str] = mapped_column(String(64), default="")
    auth: Mapped[str] = mapped_column(String(16), default=AUTH_ASK)
    key_id: Mapped[int | None] = mapped_column(ForeignKey("vault_keys.id", ondelete="SET NULL"), nullable=True)


class HostKey(Base):
    """The host keys nextrmnl has been told to trust, like a known_hosts file for the whole installation."""

    __tablename__ = "host_keys"
    __table_args__ = (UniqueConstraint("host", "port", name="uq_host_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer, default=22)
    key_type: Mapped[str] = mapped_column(String(32))
    #: Base64 of the public key blob, as in known_hosts.
    public_key: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(80))
    trusted_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    trusted_by: Mapped[str] = mapped_column(String(64), default="")


END_RUNNING = "running"
END_NORMAL = "normal"
END_FAILED = "failed"
END_HOSTKEY = "hostkey"
END_CUT = "cut"


class SessionRecord(Base):
    """Who connected where and when. Never what was typed."""

    __tablename__ = "session_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    connection_id: Mapped[int | None] = mapped_column(
        ForeignKey("connections.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: Name of the connection at the time, kept even if it is deleted later.
    name: Mapped[str] = mapped_column(String(64), default="")
    target: Mapped[str] = mapped_column(String(320), default="")
    from_ip: Mapped[str] = mapped_column(String(64), default="")
    started_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    end: Mapped[str] = mapped_column(String(16), default=END_RUNNING)
    #: Short English reason for a failure. Never contains secrets.
    detail: Mapped[str] = mapped_column(String(255), default="")

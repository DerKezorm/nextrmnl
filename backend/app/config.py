"""Settings from the environment, prefix ``NEXTRMNL_``.

What the operator changes at runtime (allowed targets, backups, log level, sign-in rules) lives in the
database, see ``services/settings_service.py``. Only what must be known before the first start is here.
"""

from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import PrivateAttr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEXTRMNL_",
        env_file=(PROJECT_DIR / ".env", BACKEND_DIR / ".env"),
        extra="ignore",
    )

    data_dir: Path = PROJECT_DIR / "data"
    #: Signs nothing itself; it protects the server-side secrets (OIDC client secret, TOTP seeds).
    #: Vaults do not depend on it: they open with the account's password only.
    secret_key: str = ""
    #: A browser session ends after this many days, whatever happens.
    session_days: int = 14
    #: Argon2id for passwords and for the vault key. The tests lower the cost.
    argon2_time: int = 3
    argon2_memory_kib: int = 65536
    argon2_parallelism: int = 2
    cookie_secure: str = "auto"
    disable_background: bool = False
    frontend_dist: Path = PROJECT_DIR / "frontend" / "dist"
    #: Overrides the stored log level; the emergency exit when the app does not even start.
    log_level: str = ""
    #: Where the app is reached from outside, for OIDC redirects. Empty: taken from the request.
    public_url: str = ""
    #: Addresses or networks of reverse proxies whose ``X-Forwarded-For`` may be believed, comma separated
    #: (``172.18.0.0/16, 10.0.0.5``). Empty: the header is ignored and the peer address counts, so that a sender
    #: cannot dodge the sign-in brake by making up addresses.
    trusted_proxies: str = ""
    #: Serves /api/docs and /api/openapi.json. Off by default: the route list of a security product is not for
    #: whoever finds the address.
    api_docs: bool = False
    default_timezone: str = os.environ.get("TZ", "") or "UTC"

    _remembered_key: str | None = PrivateAttr(default=None)

    @field_validator("data_dir", "frontend_dist")
    @classmethod
    def _relative_to_project(cls, value: Path) -> Path:
        # A relative path means the project, not whatever directory the process was started from.
        return value if value.is_absolute() else PROJECT_DIR / value

    @property
    def database_path(self) -> Path:
        return self.data_dir / "nextrmnl.db"

    def key_from_environment(self) -> bool:
        return bool(self.secret_key)

    def resolved_secret_key(self) -> str:
        if self.secret_key:
            return self.secret_key
        if self._remembered_key:
            return self._remembered_key
        self.data_dir.mkdir(parents=True, exist_ok=True)
        key_file = self.data_dir / "secret.key"
        if key_file.exists():
            self._remembered_key = key_file.read_text(encoding="utf-8").strip()
        else:
            self._remembered_key = secrets.token_urlsafe(48)
            key_file.write_text(self._remembered_key, encoding="utf-8")
        try:
            os.chmod(key_file, 0o600)
        except OSError:
            pass
        return self._remembered_key


@lru_cache
def get_settings() -> Settings:
    return Settings()

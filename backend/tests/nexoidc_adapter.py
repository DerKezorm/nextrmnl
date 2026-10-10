"""nexoidc contract tests: nextrmnl's adapter (copied once from ``bauplaene/oidc/tests-fuer-apps/adapter_template.py``,
from then on nextrmnl's own file).

The contract tests in ``tests/nexoidc/`` are the same in every nex app and never edited here. They reach nextrmnl
only through this class: its real routes, its database, its accounts, invitations and second factor. The test run's
own data directory and the cleaning between tests come from ``tests/conftest.py``. Passwords and seeds are made at
run time; none is written into this file.

Rights in the words of the contract: nextrmnl knows two roles, so an invitation with the rights ``editor`` makes an
operator, and ``member`` a member.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import timedelta
from typing import Any

import httpx
from fastapi.testclient import TestClient
from oidc_contract import StandardAdapter
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import MEMBER, OPERATOR, SIGN_IN_OIDC, SIGN_IN_PASSWORD, Account, Invite, utcnow
from app.routers.auth import PENDING_COOKIE
from app.security import Brake, brake, hash_password, hash_token
from app.services import accounts, oidc_store, settings_service, totp

OPERATOR_NAME = "operator"
UI = {"X-Requested-By": "nextrmnl"}
#: The contract's words for rights, and nextrmnl's roles.
RIGHTS = {"member": MEMBER, "editor": OPERATOR}


class Adapter(StandardAdapter):
    package = "app.vendor.nexoidc"
    api = "/api"
    single_account = False
    only_authentik = False
    #: nextrmnl is no app of the Verbund (Bauplan 06): its list is never coupled to nexsuite.
    supports_coupling = False
    has_second_factor = True
    legacy_model = "single"
    #: 0.4.2 asked no code of its own after the provider.
    trusted_before = True
    old_callback_paths: tuple[str, ...] = ()
    operator_name = OPERATOR_NAME

    def start(self) -> None:
        # tests/conftest.py emptied the database before this test.
        oidc_store.configure()
        self._operator: TestClient | None = None
        self._seeds: dict[str, str] = {}
        self.make_account(OPERATOR_NAME, role=OPERATOR)

    def stop(self) -> None:
        pass

    def browser(self) -> TestClient:
        # As the interface does: every changing request carries the CSRF header (refused without it).
        return TestClient(app, base_url="http://testserver", follow_redirects=False, headers=UI)

    def operator(self) -> TestClient:
        if self._operator is None:
            self._operator = self.browser()
            self.sign_in_with_password(self._operator, OPERATOR_NAME)
        return self._operator

    def make_account(
        self, name: str, *, password: bool = True, second_factor: bool = False, role: str = MEMBER
    ) -> int:
        with SessionLocal() as db:
            row = Account(
                name=name,
                role=role,
                sign_in=SIGN_IN_PASSWORD if password else SIGN_IN_OIDC,
                password_hash=hash_password(self.password_for(name)) if password else "",
            )
            if second_factor:
                seed = totp.generate_seed()
                self._seeds[name] = seed
                row.totp_secret_enc = totp.seal_seed(seed)
            db.add(row)
            db.commit()
            return row.id

    def sign_in_with_password(self, client: Any, name: str) -> None:
        answer = client.post("/api/auth/login", json={"name": name, "password": self.password_for(name)})
        assert answer.status_code == 200, answer.text
        if answer.json().get("second_factor"):
            # A code of the step after the last one taken, so that no two sign-ins in one step collide.
            code = totp.code_at(self._seeds[name], time.time() + 30 * self._codes_used(name))
            done = client.post("/api/auth/login/totp", json={"code": code})
            assert done.status_code == 200, done.text

    def _codes_used(self, name: str) -> int:
        with SessionLocal() as db:
            row = accounts.by_name(db, name)
            assert row is not None
            return 1 if row.totp_last_step else 0

    def who(self, client: Any) -> str | None:
        answer = client.get("/api/auth/me")
        return answer.json()["name"] if answer.status_code == 200 else None

    def second_factor_asked(self, client: Any, response: httpx.Response) -> bool:
        waiting = client.cookies.get(PENDING_COOKIE)
        return (
            response.headers.get("location", "") == "/login?step=code"
            and waiting is not None
            and totp.get_pending(waiting) is not None
            and client.get("/api/auth/login/pending").status_code == 200
        )

    def make_invite(self, rights: str) -> str:
        with SessionLocal() as db:
            operator = accounts.by_name(db, OPERATOR_NAME)
            assert operator is not None
            token = secrets.token_urlsafe(24)
            db.add(
                Invite(
                    token_hash=hash_token(token),
                    role=RIGHTS[rights],
                    created_by=operator.id,
                    expires_at=utcnow() + timedelta(days=7),
                )
            )
            db.commit()
        return token

    def rights_of(self, name: str) -> str | None:
        with SessionLocal() as db:
            row = accounts.by_name(db, name)
            if row is None:
                return None
            return "editor" if row.role == OPERATOR else "member"

    def account_names(self) -> list[str]:
        with SessionLocal() as db:
            return sorted(db.scalars(select(Account.name)))

    def store(self) -> AbstractContextManager[Any]:
        @contextmanager
        def opened() -> Iterator[oidc_store.SqlStore]:
            with SessionLocal() as db:
                yield oidc_store.SqlStore(db)

        return opened()

    def set_public_url(self, url: str) -> None:
        with SessionLocal() as db:
            settings_service.save(db, {"public_url": url})

    def read_public_url(self) -> str:
        # From the database, as a restart would find it.
        with SessionLocal() as db:
            return settings_service.public_url(db)

    def save_public_url_route(self, url: str, *, confirm: bool = False) -> httpx.Response:
        # nextrmnl saves the address on the settings page (Settings, Sign-in, card 1), a route of its own: it asks
        # ``authentik.public_url_guard`` first and answers 409 ``public_url_change``, saving nothing.
        return self.operator().put("/api/settings", json={"public_url": url, "public_url_confirm": confirm})

    def legacy_setup(
        self, *, issuer: str, client_id: str, client_secret: str, label: str, subjects: dict[str, str]
    ) -> None:
        # As nextrmnl 0.4.2 kept its one provider: in the settings, the secret sealed without a context.
        from app import crypto

        with SessionLocal() as db:
            settings_service.save(
                db,
                {
                    "oidc_issuer": issuer,
                    "oidc_client_id": client_id,
                    "oidc_client_secret_enc": crypto.encrypt_secret(client_secret),
                    "oidc_provider_name": label,
                },
            )
            for name, subject in subjects.items():
                row = accounts.by_name(db, name)
                assert row is not None
                row.oidc_subject = subject
            db.commit()

    def run_migration(self) -> None:
        # The whole start-up: the migration once, then what the older version changed, the mirror and the fingerprint.
        oidc_store.migrate_settings(backup=False)

    def legacy_read(self) -> dict[str, Any]:
        # What nextrmnl 0.4.2 would find: the old settings (the secret opened) and the subject of each account.
        with SessionLocal() as db:
            legacy, subjects = oidc_store.read_legacy(db)
            names = {row.id: row.name for row in db.scalars(select(Account))}
        return {
            "issuer": legacy.issuer,
            "client_id": legacy.client_id,
            "client_secret": legacy.client_secret,
            "label": legacy.label,
            "auto_create": legacy.auto_create,
            "subjects": {names[account_id]: subject for account_id, subject in subjects},
        }

    def delete_account(self, name: str) -> None:
        # The operator's own route, as the account list calls it.
        with SessionLocal() as db:
            row = accounts.by_name(db, name)
            assert row is not None
            account_id = row.id
        answer = self.operator().delete(f"/api/accounts/{account_id}")
        assert answer.status_code == 204, answer.text

    def trip_throttle(self, client: Any) -> None:
        # As many failed runs at the provider from this sender as the brake lets pass (every TestClient is the
        # sender "testclient"): the next start, return and linking wait.
        for _ in range(Brake.FREE + 1):
            brake.failed("oidc:testclient")

    def operator_unlink(self, client: Any, account_id: int, provider_id: int, password: str) -> httpx.Response:
        path = self.path("operator_unlink", account=account_id, provider=provider_id)
        return client.request("DELETE", path, json={"password": password})

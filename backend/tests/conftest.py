"""Every test run gets its own empty data directory and cheap Argon2 parameters."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator

_DATA = tempfile.mkdtemp(prefix="nextrmnl-tests-")
os.environ["NEXTRMNL_DATA_DIR"] = _DATA
os.environ["NEXTRMNL_DISABLE_BACKGROUND"] = "1"
os.environ["NEXTRMNL_ARGON2_TIME"] = "1"
os.environ["NEXTRMNL_ARGON2_MEMORY_KIB"] = "1024"
os.environ["NEXTRMNL_ARGON2_PARALLELISM"] = "1"
os.environ["NEXTRMNL_FRONTEND_DIST"] = os.path.join(_DATA, "no-frontend")
os.environ["NEXTRMNL_COOKIE_SECURE"] = "off"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import delete  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Account,
    AuthSession,
    Connection,
    ConnectionShare,
    HostKey,
    Invite,
    SessionRecord,
    Setting,
    VaultKey,
    VaultPassword,
)
from app.security import brake  # noqa: E402
from app.services import totp, vault  # noqa: E402

UI = {"X-Requested-By": "nextrmnl"}
PASSWORD = "correct-horse-battery"


@pytest.fixture(autouse=True)
def clean_db() -> Iterator[None]:
    init_db()
    with SessionLocal() as db:
        for model in (SessionRecord, VaultPassword, ConnectionShare, Connection, VaultKey, HostKey, Invite, AuthSession, Account, Setting):
            db.execute(delete(model))
        db.commit()
    vault.lock_all()
    totp.clear_for_tests()
    # The brake is per sender and in memory; every test starts unbraked.
    brake._fails.clear()
    yield
    vault.lock_all()


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app, base_url="http://testserver") as test_client:
        yield test_client


@pytest.fixture
def operator(client: TestClient) -> dict:
    """A signed-in operator with an open vault."""
    response = client.post("/api/setup", json={"name": "admin", "password": PASSWORD}, headers=UI)
    assert response.status_code == 200, response.text
    return response.json()


def sign_in(client: TestClient, name: str, password: str = PASSWORD) -> dict:
    response = client.post("/api/auth/login", json={"name": name, "password": password}, headers=UI)
    assert response.status_code == 200, response.text
    return response.json()


def invite_member(client: TestClient, name: str = "alex") -> TestClient:
    """The operator invites; a second client accepts and is signed in as the member."""
    created = client.post("/api/accounts/invites", json={"name": name, "role": "member"}, headers=UI)
    assert created.status_code == 201, created.text
    token = created.json()["link"].rsplit("/", 1)[-1]
    member = TestClient(app, base_url="http://testserver")
    accepted = member.post(f"/api/invites/{token}", json={"name": name, "password": PASSWORD}, headers=UI)
    assert accepted.status_code == 200, accepted.text
    return member

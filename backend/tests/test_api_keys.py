"""Read-only API keys: who may make them, what they open, and what they never open."""

from __future__ import annotations

import logging

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import Account, ApiKey, SessionRecord
from app.routers import api_v1

from .conftest import UI, invite_member


def _allow(client: TestClient, on: bool = True) -> None:
    assert client.put("/api/settings", json={"api_keys_allowed": on}, headers=UI).status_code == 200


def _key(client: TestClient, name: str = "nexdeck") -> str:
    created = client.post("/api/api-keys", json={"name": name}, headers=UI)
    assert created.status_code == 201, created.text
    return created.json()["key"]


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def test_the_switch_starts_closed(client: TestClient, operator: dict) -> None:
    assert client.get("/api/settings").json()["api_keys_allowed"] is False
    key = _key(client)
    outsider = TestClient(app, base_url="http://testserver")
    refused = outsider.get("/api/v1/status", headers=_bearer(key))
    assert refused.status_code == 403 and refused.json()["detail"]["code"] == "api_keys_off"
    _allow(client)
    assert outsider.get("/api/v1/status", headers=_bearer(key)).status_code == 200
    _allow(client, False)
    assert outsider.get("/api/v1/status", headers=_bearer(key)).status_code == 403, "closing acts at once"
    assert client.get("/api/api-keys").json()["keys"], "and deletes no key"


def test_only_the_operator_makes_keys_and_the_key_is_shown_once(client: TestClient, operator: dict) -> None:
    member = invite_member(client)
    assert member.post("/api/api-keys", json={"name": "mine"}, headers=UI).status_code == 403
    assert member.get("/api/api-keys").status_code == 403
    key = _key(client)
    assert key.startswith("nxt_") and len(key) > 40
    listed = client.get("/api/api-keys").json()["keys"]
    assert listed[0]["name"] == "nexdeck" and "key" not in listed[0] and key.startswith(listed[0]["prefix"])
    with SessionLocal() as db:
        stored = db.scalar(select(ApiKey))
        assert key not in (stored.token_hash, stored.prefix) and len(stored.token_hash) == 64, "only the hash is kept"


def test_a_key_opens_v1_and_nothing_else(client: TestClient, operator: dict) -> None:
    _allow(client)
    key = _key(client)
    outsider = TestClient(app, base_url="http://testserver")
    for path in ("/api/v1/status", "/api/v1/sessions", "/api/v1/history", "/api/v1/connections"):
        assert outsider.get(path, headers=_bearer(key)).status_code == 200, path
    for path in ("/api/connections", "/api/sessions/running", "/api/settings", "/api/api-keys", "/api/vault/keys"):
        assert outsider.get(path, headers=_bearer(key)).status_code in (401, 403), path
    # The signed-in browser's cookie does not open v1.
    assert client.get("/api/v1/status").status_code == 401


def test_a_deleted_key_and_a_key_of_a_former_operator_stop(client: TestClient, operator: dict) -> None:
    _allow(client)
    key = _key(client)
    outsider = TestClient(app, base_url="http://testserver")
    key_id = client.get("/api/api-keys").json()["keys"][0]["id"]
    assert client.delete(f"/api/api-keys/{key_id}", headers=UI).status_code == 204
    assert outsider.get("/api/v1/status", headers=_bearer(key)).status_code == 401
    second = _key(client, "wall")
    with SessionLocal() as db:
        db.scalar(select(Account).where(Account.name == "admin")).role = "member"
        db.commit()
    assert outsider.get("/api/v1/status", headers=_bearer(second)).status_code == 401, "no operator, no key"
    assert outsider.get("/api/v1/status", headers=_bearer("nxt_made-up")).status_code == 401
    assert outsider.get("/api/v1/status", headers=_bearer("")).status_code == 401


def test_the_answers_carry_no_sender_address(client: TestClient, operator: dict) -> None:
    _allow(client)
    key = _key(client)
    with SessionLocal() as db:
        admin = db.scalar(select(Account).where(Account.name == "admin"))
        db.add(SessionRecord(account_id=admin.id, name="nas", target="root@nas.example.com:22", from_ip="192.0.2.7", end="failed", detail="Host key changed"))
        db.commit()
    outsider = TestClient(app, base_url="http://testserver")
    history = outsider.get("/api/v1/history", headers=_bearer(key)).json()
    assert history[0]["name"] == "nas" and history[0]["end"] == "failed" and history[0]["account"] == "admin"
    assert "from_ip" not in history[0] and "192.0.2.7" not in str(history)
    status = outsider.get("/api/v1/status", headers=_bearer(key)).json()
    assert status["sessions_today"] == 1 and status["failed_today"] == 1 and status["sessions_running"] == 0
    assert status["version"] and "update_available" in status


def test_reachability_is_reused_for_a_minute(client: TestClient, operator: dict, monkeypatch) -> None:
    _allow(client)
    key = _key(client)
    made = client.post("/api/connections", json={"name": "nas", "host": "10.0.0.5", "port": 22, "user": "root", "auth": "password"}, headers=UI)
    assert made.status_code == 201, made.text
    probes: list[str] = []

    async def probe(host: str, port: int) -> tuple[str, int | None]:
        probes.append(host)
        return "up", 3

    from app.routers import connections

    monkeypatch.setattr(connections, "_probe", probe)
    api_v1._reach_cache.clear()
    outsider = TestClient(app, base_url="http://testserver")
    first = outsider.get("/api/v1/connections", headers=_bearer(key)).json()
    outsider.get("/api/v1/connections", headers=_bearer(key))
    assert first == [{"name": "nas", "group": "", "target": "10.0.0.5:22", "reach": "up", "latency_ms": 3}]
    assert probes == ["10.0.0.5"], "the second dashboard refresh knocks at no host"


def test_no_key_reaches_the_log(client: TestClient, operator: dict, caplog) -> None:
    _allow(client)
    with caplog.at_level(logging.DEBUG):
        key = _key(client)
        TestClient(app, base_url="http://testserver").get("/api/v1/status", headers=_bearer(key))
        TestClient(app, base_url="http://testserver").get("/api/v1/status", headers=_bearer(key + "x"))
    assert key not in caplog.text and key[4:] not in caplog.text
    assert "API key created name=nexdeck" in caplog.text

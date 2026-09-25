import pytest
from fastapi.testclient import TestClient

from app.services import targets
from tests.conftest import UI, invite_member


def _key(client: TestClient, name: str = "homelab") -> int:
    return client.post("/api/vault/keys/generate", json={"name": name}, headers=UI).json()["id"]


def test_create_update_delete(client: TestClient, operator: dict) -> None:
    key_id = _key(client)
    created = client.post(
        "/api/connections",
        json={"name": "web-01", "group": "Homelab", "host": "192.0.2.21", "user": "admin", "auth": "key", "key_id": key_id},
        headers=UI,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["host_key"]["state"] == "new" and body["shared_by"] is None
    cid = body["id"]
    updated = client.put(
        f"/api/connections/{cid}",
        json={"name": "web-01", "host": "192.0.2.21", "port": 2222, "user": "admin", "auth": "ask"},
        headers=UI,
    )
    assert updated.status_code == 200 and updated.json()["port"] == 2222 and updated.json()["key_id"] is None
    assert client.delete(f"/api/connections/{cid}", headers=UI).status_code == 204
    assert client.get("/api/connections").json() == []


def test_key_auth_needs_own_key(client: TestClient, operator: dict) -> None:
    response = client.post("/api/connections", json={"name": "x", "host": "192.0.2.1", "auth": "key"}, headers=UI)
    assert response.status_code == 422 and response.json()["detail"]["code"] == "key_required"
    member = invite_member(client, "alex")
    key_id = _key(client)
    foreign = member.post("/api/connections", json={"name": "x", "host": "192.0.2.1", "auth": "key", "key_id": key_id}, headers=UI)
    assert foreign.status_code == 422 and foreign.json()["detail"]["code"] == "key_not_found"


def test_sharing_shows_but_does_not_allow_changes(client: TestClient, operator: dict) -> None:
    member = invite_member(client, "alex")
    alex_id = member.get("/api/auth/me").json()["id"]
    cid = client.post("/api/connections", json={"name": "app", "host": "10.0.0.12", "auth": "ask"}, headers=UI).json()["id"]
    assert member.get("/api/connections").json() == []
    shared = client.put(f"/api/connections/{cid}/share", json={"account_ids": [alex_id]}, headers=UI)
    assert shared.status_code == 200 and shared.json()["shared_with"] == [alex_id]
    seen = member.get("/api/connections").json()
    assert len(seen) == 1 and seen[0]["shared_by"] == "admin" and seen[0]["shared_with"] == []
    assert member.put(f"/api/connections/{cid}", json={"name": "hacked", "host": "10.0.0.12", "auth": "ask"}, headers=UI).status_code == 404
    assert member.delete(f"/api/connections/{cid}", headers=UI).status_code == 404
    # Unsharing takes it away again.
    client.put(f"/api/connections/{cid}/share", json={"account_ids": []}, headers=UI)
    assert member.get("/api/connections").json() == []


def test_jump_cannot_be_self_or_invisible(client: TestClient, operator: dict) -> None:
    member = invite_member(client, "alex")
    hidden = client.post("/api/connections", json={"name": "bastion", "host": "192.0.2.5", "auth": "ask"}, headers=UI).json()["id"]
    refused = member.post("/api/connections", json={"name": "x", "host": "10.0.0.1", "auth": "ask", "jump_id": hidden}, headers=UI)
    assert refused.status_code == 404
    own = client.post("/api/connections", json={"name": "y", "host": "10.0.0.2", "auth": "ask"}, headers=UI).json()["id"]
    loop = client.put(f"/api/connections/{own}", json={"name": "y", "host": "10.0.0.2", "auth": "ask", "jump_id": own}, headers=UI)
    assert loop.status_code == 422 and loop.json()["detail"]["code"] == "jump_self"


def test_private_ranges() -> None:
    for address in ("10.1.2.3", "172.16.0.1", "172.31.255.255", "192.168.1.1", "100.64.0.1", "127.0.0.1", "fd00::1", "::1"):
        assert targets.is_private(address), address
    for address in ("8.8.8.8", "172.32.0.1", "203.0.113.1", "2001:db8::1", "example.com"):
        assert not targets.is_private(address), address


def test_target_list_entries() -> None:
    assert targets.invalid_entries(["192.0.2.0/24", "*.example.com", "nas", "203.0.113.7"]) == []
    assert targets.invalid_entries(["192.0.2.0/99", "not a name", "http://x"]) == ["192.0.2.0/99", "not a name", "http://x"]


@pytest.mark.asyncio
async def test_target_check_modes() -> None:
    assert (await targets.check("192.168.1.10", 22, "private", [])).allowed
    assert not (await targets.check("203.0.113.9", 22, "private", [])).allowed
    assert (await targets.check("203.0.113.9", 22, "list", ["203.0.113.0/24"])).allowed
    assert (await targets.check("host.example.com", 22, "list", ["*.example.com"])).allowed
    assert not (await targets.check("host.example.org", 22, "list", ["*.example.com"])).allowed
    assert (await targets.check("203.0.113.9", 22, "all", [])).allowed
    assert not (await targets.check("", 22, "all", [])).allowed


def test_operator_settings_validate_targets(client: TestClient, operator: dict) -> None:
    ok = client.put("/api/settings", json={"targets_mode": "list", "targets_list": ["192.0.2.0/24", " *.example.com "]}, headers=UI)
    assert ok.status_code == 200 and ok.json()["targets_list"] == ["192.0.2.0/24", "*.example.com"]
    bad = client.put("/api/settings", json={"targets_list": ["not a network"]}, headers=UI)
    assert bad.status_code == 422 and bad.json()["detail"]["entries"] == ["not a network"]
    assert client.put("/api/settings", json={"targets_mode": "everything"}, headers=UI).status_code == 422
    assert client.get("/api/settings").json()["update_check"] is False

"""Snippets: each account keeps its own, nobody else reads or changes them, and they stay text."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.routers import snippets
from tests.conftest import UI, invite_member


def add(client: TestClient, name: str, command: str) -> dict:
    response = client.post("/api/snippets", json={"name": name, "command": command}, headers=UI)
    assert response.status_code == 201, response.text
    return response.json()


def test_keep_change_use_and_delete(client: TestClient, operator: dict) -> None:
    assert client.get("/api/snippets").json() == []
    prune = add(client, "  Prune docker  ", "docker system prune -f")
    logs = add(client, "follow logs", "journalctl -fu nginx\n")
    assert prune["name"] == "Prune docker" and prune["last_used_at"] is None
    # By name, regardless of case.
    assert [s["name"] for s in client.get("/api/snippets").json()] == ["follow logs", "Prune docker"]

    changed = client.put(f"/api/snippets/{logs['id']}", json={"name": "logs", "command": "tail -f x"}, headers=UI)
    assert changed.status_code == 200 and changed.json()["command"] == "tail -f x"
    assert client.post(f"/api/snippets/{prune['id']}/used", headers=UI).status_code == 204
    assert next(s for s in client.get("/api/snippets").json() if s["id"] == prune["id"])["last_used_at"]

    assert client.delete(f"/api/snippets/{prune['id']}", headers=UI).status_code == 204
    assert [s["id"] for s in client.get("/api/snippets").json()] == [logs["id"]]
    assert client.delete(f"/api/snippets/{prune['id']}", headers=UI).status_code == 404


def test_each_account_sees_only_its_own(client: TestClient, operator: dict) -> None:
    mine = add(client, "mine", "uptime")
    member = invite_member(client, "alex")
    assert member.get("/api/snippets").json() == []
    theirs = member.post("/api/snippets", json={"name": "theirs", "command": "df -h"}, headers=UI).json()
    # Neither way round, the operator included: 404 as if it did not exist.
    for path in (f"/api/snippets/{mine['id']}",):
        assert member.put(path, json={"name": "x", "command": "y"}, headers=UI).status_code == 404
        assert member.delete(path, headers=UI).status_code == 404
        assert member.post(path + "/used", headers=UI).status_code == 404
    assert client.put(f"/api/snippets/{theirs['id']}", json={"name": "x", "command": "y"}, headers=UI).status_code == 404
    assert client.delete(f"/api/snippets/{theirs['id']}", headers=UI).status_code == 404
    assert [s["name"] for s in client.get("/api/snippets").json()] == ["mine"]
    assert [s["name"] for s in member.get("/api/snippets").json()] == ["theirs"]


def test_snippets_are_text_and_bounded(client: TestClient, operator: dict) -> None:
    for bad in (
        {"name": "", "command": "ls"},
        {"name": "   ", "command": "ls"},
        {"name": "x" * 65, "command": "ls"},
        {"name": "empty", "command": ""},
        {"name": "blank", "command": "  \n "},
        {"name": "long", "command": "a" * (snippets.MAX_COMMAND + 1)},
        # Keystrokes in disguise: Ctrl+C, an escape sequence, DEL.
        {"name": "ctrl-c", "command": "ls\x03"},
        {"name": "escape", "command": "\x1b[201~rm -rf /"},
        {"name": "del", "command": "ls\x7f"},
    ):
        response = client.post("/api/snippets", json=bad, headers=UI)
        assert response.status_code == 422, bad["name"]
    assert add(client, "tabs and lines", "for i in 1 2; do\n\techo $i\r\ndone")["command"].count("\n") == 2
    assert client.get("/api/snippets").json()[0]["name"] == "tabs and lines"


def test_the_list_has_a_ceiling(client: TestClient, operator: dict, monkeypatch) -> None:
    monkeypatch.setattr(snippets, "MAX_SNIPPETS", 2)
    add(client, "one", "a")
    add(client, "two", "b")
    third = client.post("/api/snippets", json={"name": "three", "command": "c"}, headers=UI)
    assert third.status_code == 422 and third.json()["detail"]["code"] == "too_many_snippets"


def test_snippets_need_a_sign_in_and_the_header(client: TestClient, operator: dict) -> None:
    assert client.post("/api/snippets", json={"name": "x", "command": "y"}).status_code == 403
    client.post("/api/auth/logout", headers=UI)
    assert client.get("/api/snippets").status_code == 401

"""The log routes: reading with filters, the level with its expiry, download, clear, operator only."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.services import logs, settings_service
from tests.conftest import UI, invite_member

probe = logging.getLogger("nextrmnl.probe")


@pytest.fixture(autouse=True)
def normal_mode_afterwards() -> Iterator[None]:
    """The level is process-wide; whatever a test sets, the next one starts at normal."""
    yield
    logs.apply_mode(logs.DEFAULT_MODE)


def test_members_get_403(client: TestClient, operator: dict) -> None:
    member = invite_member(client)
    assert member.get("/api/logs").status_code == 403
    assert member.get("/api/logs/level").status_code == 403
    assert member.put("/api/logs/level", json={"mode": "quiet"}, headers=UI).status_code == 403
    assert member.get("/api/logs/download").status_code == 403
    assert member.delete("/api/logs", headers=UI).status_code == 403
    assert client.get("/api/logs").status_code == 200


def test_the_level_filter_includes_higher_levels(client: TestClient, operator: dict) -> None:
    probe.info("probe-levels info line")
    probe.warning("probe-levels warning line")
    probe.error("probe-levels error line")
    lines = client.get("/api/logs", params={"level": "WARNING", "search": "probe-levels"}).json()
    assert [line["level"] for line in lines] == ["ERROR", "WARNING"], "newest first, INFO left out"
    assert all(line["logger"] == "nextrmnl.probe" for line in lines)
    everything = client.get("/api/logs", params={"search": "probe-levels"}).json()
    assert [line["level"] for line in everything] == ["ERROR", "WARNING", "INFO"]
    assert client.get("/api/logs", params={"level": "LOUD"}).status_code == 422


def test_the_search_by_request_id_finds_the_lines_of_one_request(client: TestClient, operator: dict) -> None:
    created = client.post(
        "/api/connections", json={"name": "switch", "host": "192.0.2.2", "user": "admin", "auth": "password"}, headers=UI
    )
    assert created.status_code == 201, created.text
    request_id = created.headers["x-request-id"]
    lines = client.get("/api/logs", params={"search": request_id}).json()
    assert lines, "the request logged something"
    assert all(line["request_id"] == request_id for line in lines)
    assert all(line["user"] == "admin" for line in lines)
    assert any(line["message"].startswith("Connection created") for line in lines)
    # Another request has another id.
    other = client.post(
        "/api/connections", json={"name": "router", "host": "192.0.2.3", "user": "admin", "auth": "password"}, headers=UI
    )
    assert other.headers["x-request-id"] != request_id
    assert all(line["request_id"] == request_id for line in client.get("/api/logs", params={"search": request_id}).json())


def test_a_deep_level_with_a_duration_expires(client: TestClient, operator: dict) -> None:
    level = client.get("/api/logs/level").json()
    assert (level["mode"], level["until"], level["fixed_by_env"]) == ("normal", None, False)
    assert level["modes"] == ["quiet", "normal", "detailed", "trace"] and 30 in level["durations"]

    changed = client.put("/api/logs/level", json={"mode": "detailed", "minutes": 30}, headers=UI)
    assert changed.status_code == 200, changed.text
    assert changed.json()["mode"] == "detailed"
    until = datetime.fromisoformat(changed.json()["until"])
    assert timedelta(minutes=29) < until - datetime.now(UTC) < timedelta(minutes=31)
    assert logs.current_mode() == "detailed"
    assert client.put("/api/logs/level", json={"mode": "trace", "minutes": 7}, headers=UI).status_code == 422

    # Time passes: the stored end lies in the past.
    with SessionLocal() as db:
        settings_service.save(db, {"log_mode_until": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()})
    state = logs.state()
    assert (state.mode, state.until) == ("normal", None)
    assert logs.current_mode() == "normal"
    assert client.get("/api/logs/level").json()["mode"] == "normal"
    with SessionLocal() as db:
        assert settings_service.get(db, "log_mode") == "normal"
        assert settings_service.get(db, "log_mode_until") is None


def test_a_level_without_a_duration_stays(client: TestClient, operator: dict) -> None:
    changed = client.put("/api/logs/level", json={"mode": "quiet"}, headers=UI)
    assert changed.status_code == 200 and changed.json()["until"] is None
    assert logs.enforce_expiry() is False
    assert logs.state().mode == "quiet"


def test_the_download_includes_rotated_files(client: TestClient, operator: dict) -> None:
    probe.warning("probe-download current line")
    rotated = logs.log_dir() / "nextrmnl.log.1"
    rotated.write_text("2026-09-01 03:00:00 INFO     nextrmnl.probe [-] | probe-download older line\n", encoding="utf-8")
    try:
        response = client.get("/api/logs/download")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        assert 'filename="nextrmnl-log-' in response.headers["content-disposition"]
        text = response.text
        assert "===== nextrmnl.log.1 =====" in text and "probe-download older line" in text
        assert "===== nextrmnl.log =====" in text and "probe-download current line" in text
        assert text.index("nextrmnl.log.1 =====") < text.index("===== nextrmnl.log ====="), "oldest first"
    finally:
        rotated.unlink(missing_ok=True)


def test_clear_empties_the_file_and_removes_rotated_ones(client: TestClient, operator: dict) -> None:
    probe.warning("probe-clear marker line")
    rotated = logs.log_dir() / "nextrmnl.log.2"
    rotated.write_text("older\n", encoding="utf-8")
    assert client.get("/api/logs", params={"search": "probe-clear"}).json()
    assert client.delete("/api/logs", headers=UI).status_code == 204
    assert not rotated.exists()
    text = logs.log_file().read_text(encoding="utf-8")
    assert "probe-clear marker line" not in text
    assert client.get("/api/logs", params={"search": "probe-clear"}).json() == []
    assert any(line["message"] == "Log cleared by operator" for line in client.get("/api/logs").json())
    # The file keeps taking lines after the clear.
    probe.warning("probe-clear after line")
    assert "probe-clear after line" in logs.log_file().read_text(encoding="utf-8")

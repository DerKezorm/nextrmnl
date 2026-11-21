"""The mark, the icons and the manifest come with ``no-cache``: a kept old icon put the old mark on a freshly
installed web app (seen with a sister app after its new icon)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import ALWAYS_ASK, _mount_frontend

PUBLIC = Path(__file__).resolve().parents[2] / "frontend" / "public"


def test_every_file_at_the_top_of_public_is_asked_for_again() -> None:
    files = {p.name for p in PUBLIC.iterdir() if p.is_file()}
    assert files and files <= ALWAYS_ASK, sorted(files - ALWAYS_ASK)


def test_the_icons_come_with_no_cache_also_with_their_version(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html>", encoding="utf-8")
    for name in ALWAYS_ASK:
        (dist / name).write_bytes(b"made up")
    (dist / "robots.txt").write_text("other", encoding="utf-8")
    target = FastAPI()
    _mount_frontend(target, dist)
    client = TestClient(target)

    for name in sorted(ALWAYS_ASK):
        answer = client.get(f"/{name}")
        assert answer.status_code == 200, name
        assert answer.headers["cache-control"] == "no-cache", name
    versioned = client.get("/logo.svg?v=2")
    assert (versioned.status_code, versioned.headers["cache-control"]) == (200, "no-cache")
    assert "cache-control" not in client.get("/robots.txt").headers

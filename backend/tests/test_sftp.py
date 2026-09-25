"""SFTP through a live session: list, mkdir, upload (streamed), download, rename, delete, and the error codes."""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from tests.conftest import UI, invite_member
from tests.helpers_sshd import (
    Sshd,
    connect_ws,
    open_session,
    receive_json,
    running_sshd,
)
from tests.test_ssh import make_connection, store_password


@pytest.fixture(name="sshd")
def _sshd(tmp_path: Path) -> Iterator[Sshd]:
    with running_sshd(tmp_path / "sftp-root") as server:
        yield server


def open_shell(client: TestClient, sshd: Sshd) -> tuple[WebSocketTestSession, str]:
    cid = make_connection(client, sshd, "password")
    store_password(client, cid)
    ws = connect_ws(client, cid)
    ws.__enter__()
    opened = open_session(ws)
    assert opened["state"] == "open", opened
    return ws, opened["session_id"]


def test_sftp_round_trip(client: TestClient, operator: dict, sshd: Sshd) -> None:
    (sshd.root / "hello.txt").write_bytes(b"hi there\n")
    (sshd.root / "docs").mkdir()
    ws, sid = open_shell(client, sshd)
    try:
        base = f"/api/sessions/{sid}/files"
        listing = client.get(base, params={"path": "~"}).json()
        assert listing["path"] == "/" and listing["home"] == "/"
        assert [(e["name"], e["type"]) for e in listing["entries"]] == [("docs", "dir"), ("hello.txt", "file")]
        hello = listing["entries"][1]
        assert hello["size"] == 9 and isinstance(hello["mtime"], int) and isinstance(hello["mode"], int)
        # No path at all means the home directory.
        assert client.get(base).json()["path"] == "/"

        made = client.post(f"{base}/mkdir", json={"path": "~/sub"}, headers=UI)
        assert made.status_code == 201 and made.json() == {"path": "/sub"}
        assert (sshd.root / "sub").is_dir()

        # Large enough to cross several multipart chunks, with a pattern that shows any shift or gap.
        payload = bytes(range(256)) * 1200 + b"tail"
        uploaded = client.post(
            f"{base}/upload",
            params={"path": "/sub"},
            data={"note": "ignored field before the file"},
            files={"file": ("data.bin", io.BytesIO(payload), "application/octet-stream")},
            headers=UI,
        )
        assert uploaded.status_code == 201, uploaded.text
        assert uploaded.json() == {"path": "/sub/data.bin", "name": "data.bin", "size": len(payload)}
        assert (sshd.root / "sub" / "data.bin").read_bytes() == payload

        downloaded = client.get(f"{base}/download", params={"path": "/sub/data.bin"})
        assert downloaded.status_code == 200
        assert downloaded.content == payload
        assert downloaded.headers["content-type"].startswith("application/octet-stream")
        assert 'filename="data.bin"' in downloaded.headers["content-disposition"]
        assert downloaded.headers["content-length"] == str(len(payload))

        info = client.get(f"{base}/stat", params={"path": "/sub/data.bin"}).json()
        assert info["type"] == "file" and info["size"] == len(payload) and info["name"] == "data.bin"

        renamed = client.post(f"{base}/rename", json={"path": "/sub/data.bin", "to": "moved.bin"}, headers=UI)
        assert renamed.status_code == 200 and renamed.json() == {"path": "/sub/moved.bin"}
        assert (sshd.root / "sub" / "moved.bin").exists() and not (sshd.root / "sub" / "data.bin").exists()
        moved = client.post(f"{base}/rename", json={"path": "/sub/moved.bin", "to": "/docs/final.bin"}, headers=UI)
        assert moved.json() == {"path": "/docs/final.bin"}

        assert client.delete(base, params={"path": "/docs/final.bin"}, headers=UI).status_code == 204
        assert not (sshd.root / "docs" / "final.bin").exists()
        assert client.delete(base, params={"path": "/sub"}, headers=UI).status_code == 204
        assert not (sshd.root / "sub").exists()
        names = [e["name"] for e in client.get(base, params={"path": "/"}).json()["entries"]]
        assert names == ["docs", "hello.txt"]
        # The shell still works next to the SFTP channel.
        ws.send_bytes(b"still here\n")
        from tests.helpers_sshd import receive_until

        receive_until(ws, b"echo: still here")
    finally:
        ws.send_bytes(b"exit\n")
        receive_json(ws)
        ws.__exit__(None, None, None)


def test_sftp_error_codes(client: TestClient, operator: dict, sshd: Sshd) -> None:
    (sshd.root / "hello.txt").write_bytes(b"hi\n")
    (sshd.root / "full").mkdir()
    (sshd.root / "full" / "x").write_bytes(b"x")
    ws, sid = open_shell(client, sshd)
    try:
        base = f"/api/sessions/{sid}/files"
        missing = client.get(base, params={"path": "/nope"})
        assert missing.status_code == 404 and missing.json()["detail"]["code"] == "not_found"
        assert client.get(f"{base}/download", params={"path": "/nope"}).status_code == 404
        as_dir = client.get(f"{base}/download", params={"path": "/full"})
        assert as_dir.status_code == 400 and as_dir.json()["detail"]["code"] == "sftp_failed"
        exists = client.post(f"{base}/mkdir", json={"path": "/hello.txt"}, headers=UI)
        assert exists.status_code == 400 and exists.json()["detail"]["code"] == "sftp_failed"
        not_empty = client.delete(base, params={"path": "/full"}, headers=UI)
        assert not_empty.status_code in (400, 403) and not_empty.json()["detail"]["code"] in ("sftp_failed", "permission_denied")
        assert (sshd.root / "full" / "x").exists()
        # With contents only when the browser says so explicitly; then the whole tree goes.
        gone = client.delete(base, params={"path": "/full", "recursive": "true"}, headers=UI)
        assert gone.status_code == 204
        assert not (sshd.root / "full").exists()
        no_name = client.post(f"{base}/upload", params={"path": "/"}, data={"other": "x"}, headers=UI)
        assert no_name.status_code == 422
        not_multipart = client.post(f"{base}/upload", params={"path": "/"}, content=b"raw", headers=UI)
        assert not_multipart.status_code == 422
        # Changing requests need the CSRF header like everywhere else.
        assert client.post(f"{base}/mkdir", json={"path": "/x"}).status_code == 403
        assert client.get("/api/sessions/unknown/files").status_code == 404
        assert client.get("/api/sessions/unknown/files").json()["detail"]["code"] == "session_not_found"
        # Another account's session is not found, whoever asks.
        member = invite_member(client, "alex")
        assert member.get(base).status_code == 404
        assert member.get(f"{base}/download", params={"path": "/hello.txt"}).status_code == 404
    finally:
        ws.send_bytes(b"exit\n")
        receive_json(ws)
        ws.__exit__(None, None, None)


def test_sftp_needs_an_open_session(client: TestClient, operator: dict, sshd: Sshd) -> None:
    cid = make_connection(client, sshd, "ask")
    with connect_ws(client, cid) as ws:
        need = open_session(ws)
        assert need["type"] == "need"
        # The session exists but is still waiting for the password: no files yet.
        sid = client.get("/api/sessions/running").json()[0]["id"]
        waiting = client.get(f"/api/sessions/{sid}/files")
        assert waiting.status_code == 409 and waiting.json()["detail"]["code"] == "session_not_open"
        ws.send_json({"type": "password", "value": "wrong", "store": False})
        receive_json(ws)
        ws.send_json({"type": "password", "value": "wrong", "store": False})
        receive_json(ws)
        ws.send_json({"type": "password", "value": "wrong", "store": False})
        assert receive_json(ws)["detail"] == "auth_failed"

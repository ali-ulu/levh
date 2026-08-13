"""Uploading a file for a path-based connector.

A browser never exposes the absolute path of a picked file, so the dashboard
could not fill ics_path / mbox_path / transcript_path from a file input. The
upload endpoint takes the bytes and hands back the path to import from.
"""

from __future__ import annotations

import base64
import os
import tempfile

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDER_MODE", "hash")
    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "levh.db"))
    monkeypatch.delenv("LEVH_TOKEN", raising=False)
    from server import api

    # The remote-access boundary is loopback-only without a token, and
    # TestClient otherwise presents itself as a non-local client.
    with TestClient(api.app, client=("127.0.0.1", 51234)) as c:
        yield c


def _upload(client, filename: str, payload: bytes):
    return client.post(
        "/api/connectors/upload",
        json={"filename": filename, "content_b64": base64.b64encode(payload).decode()},
    )


def test_upload_returns_a_readable_path(client, tmp_path):
    body = b"BEGIN:VCALENDAR\nEND:VCALENDAR\n"
    res = _upload(client, "meetings.ics", body)
    assert res.status_code == 200, res.text

    data = res.json()
    assert data["filename"] == "meetings.ics"
    assert data["bytes"] == len(body)
    # The path must be absolute — the connector resolves it server-side.
    assert os.path.isabs(data["path"])
    with open(data["path"], "rb") as fh:
        assert fh.read() == body
    # ...and it must land beside the database, not in the CWD.
    assert os.path.dirname(data["path"]) == str(tmp_path / "uploads")


def test_repeat_uploads_do_not_clobber_each_other(client):
    first = _upload(client, "notes.vtt", b"one").json()
    second = _upload(client, "notes.vtt", b"two").json()

    assert first["path"] != second["path"]
    with open(first["path"], "rb") as fh:
        assert fh.read() == b"one"
    with open(second["path"], "rb") as fh:
        assert fh.read() == b"two"


@pytest.mark.parametrize(
    "filename",
    [
        "../../etc/passwd",
        "..\\..\\windows\\system32\\evil.dll",
        "/etc/passwd",
        "sub/dir/notes.ics",
    ],
)
def test_traversal_attempts_cannot_escape_the_upload_dir(client, tmp_path, filename):
    res = _upload(client, filename, b"x")
    assert res.status_code == 200, res.text
    written = res.json()["path"]
    assert os.path.dirname(written) == str(tmp_path / "uploads")
    assert os.sep not in res.json()["filename"]


def test_dotfile_names_are_defanged(client):
    res = _upload(client, "...", b"x")
    assert res.status_code == 400


@pytest.mark.parametrize("filename", ["", "   ", ".", ".."])
def test_empty_filenames_are_rejected(client, filename):
    res = _upload(client, filename, b"x")
    assert res.status_code == 400


def test_empty_file_is_rejected(client):
    res = _upload(client, "empty.ics", b"")
    assert res.status_code == 400
    assert "empty" in res.json()["detail"]


def test_invalid_base64_is_rejected(client):
    res = client.post(
        "/api/connectors/upload",
        json={"filename": "x.ics", "content_b64": "not base64!!"},
    )
    assert res.status_code == 400


def test_oversized_upload_is_rejected(client, monkeypatch):
    from server.routes import connectors

    monkeypatch.setattr(connectors, "MAX_UPLOAD_BYTES", 16)
    res = _upload(client, "big.mbox", b"x" * 17)
    assert res.status_code == 413


def test_upload_requires_the_token_when_one_is_set(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDER_MODE", "hash")
    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "levh.db"))
    monkeypatch.setenv("LEVH_TOKEN", "secret")
    import importlib

    from server import api

    reloaded = importlib.reload(api)
    try:
        with TestClient(reloaded.app, client=("127.0.0.1", 51234)) as c:
            payload = {
                "filename": "x.ics",
                "content_b64": base64.b64encode(b"data").decode(),
            }
            assert c.post("/api/connectors/upload", json=payload).status_code == 401
            ok = c.post(
                "/api/connectors/upload",
                json=payload,
                headers={"X-LEVH-Token": "secret"},
            )
            assert ok.status_code == 200
    finally:
        monkeypatch.delenv("LEVH_TOKEN", raising=False)
        importlib.reload(api)


def test_uploaded_calendar_file_imports_end_to_end(client):
    ics = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\n"
        "UID:1\r\nDTSTART:20260101T100000Z\r\nDTEND:20260101T110000Z\r\n"
        "SUMMARY:Roadmap review\r\nATTENDEE:mailto:a@example.com\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n"
    ).encode()
    path = _upload(client, "cal.ics", ics).json()["path"]

    res = client.post(
        "/api/connectors/import",
        json={"connector": "calendar", "config": {"ics_path": path}},
    )
    assert res.status_code == 200, res.text
    assert res.json()["stored"] >= 1

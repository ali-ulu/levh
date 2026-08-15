"""Uploading arbitrary files and turning them into memories.

Same base64-in-JSON shape as /api/restore and /api/connectors/upload. Only
stdlib-backed formats (plain text, zip) are exercised here — PDF/Word/Excel
extraction is optional and guarded by ImportError in server/core/file_import.
"""

from __future__ import annotations

import base64
import io
import zipfile

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDER_MODE", "hash")
    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "levh.db"))
    monkeypatch.delenv("LEVH_TOKEN", raising=False)
    from server import api

    with TestClient(api.app, client=("127.0.0.1", 51234)) as c:
        yield c


def _upload(client, filename: str, payload: bytes, **extra):
    body = {"filename": filename, "content_b64": base64.b64encode(payload).decode(), **extra}
    return client.post("/api/import/file", json=body)


def test_plain_text_file_becomes_a_memory(client):
    res = _upload(client, "notes.txt", b"Remember to ship the file import feature.")
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["memories_created"] == 1
    assert data["chars_extracted"] > 0
    assert data["warnings"] == []


def test_long_text_is_split_into_multiple_chunks(client):
    paragraphs = [f"Paragraph {i} about the project, with some unique detail each time. " * 50 for i in range(10)]
    text = "\n\n".join(paragraphs)
    res = _upload(client, "long.md", text.encode())
    assert res.status_code == 200, res.text
    assert res.json()["memories_created"] > 1


def test_zip_archive_expands_into_one_memory_per_entry(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.txt", "The quarterly roadmap review happens every Monday at 9am.")
        zf.writestr("b.md", "Deploying the new file import feature requires a database migration.")
    res = _upload(client, "bundle.zip", buf.getvalue())
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["parts"] == 2
    assert data["memories_created"] == 2


def test_unsupported_binary_still_records_a_memory_with_a_warning(client):
    res = _upload(client, "photo.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["parts"] == 0
    assert data["memories_created"] == 1
    assert data["warnings"]


def test_empty_file_is_rejected(client):
    res = _upload(client, "empty.txt", b"")
    assert res.status_code == 400
    assert "empty" in res.json()["detail"]


def test_invalid_base64_is_rejected(client):
    res = client.post(
        "/api/import/file",
        json={"filename": "x.txt", "content_b64": "not base64!!"},
    )
    assert res.status_code == 400


def test_project_and_tags_flow_through_to_the_stored_memory(client):
    res = _upload(client, "notes.txt", b"Project-scoped note for the import feature.", project="levh")
    assert res.status_code == 200, res.text

    memories = client.get("/api/memories", params={"project": "levh"}).json()
    assert any("import:file" in m.get("tags", []) for m in memories)

"""Files attached to a memory as evidence — reference + derived text, not blob.

The memory stays text (decay/H(x,psi) keep working unmodified); the file
lives on disk and is referenced by path + sha256. Covers: attach, list,
verify (missing/changed → conflict candidate, #57's open decision), delete,
the browser upload helper, and backup/restore carrying attachments through.
"""

from __future__ import annotations

import base64
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDER_MODE", "hash")
    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "levh.db"))
    monkeypatch.delenv("LEVH_TOKEN", raising=False)
    from server import api
    from server.core import engine_provider

    # api._engine is a module global cached across tests; changing
    # SQLITE_DB_PATH alone doesn't make a already-constructed engine look at
    # the new path, so each test gets its own by resetting the cache first.
    api._engine = None
    api._initialized = False
    engine_provider.set_engine(None)

    with TestClient(api.app, client=("127.0.0.1", 51234)) as c:
        yield c


import uuid


def _store(client, content: str | None = None) -> str:
    if content is None:
        content = f"Notes about the {uuid.uuid4().hex[:12]} project milestone"
    # force=True: these tests are about attachments, not the admission gate's
    # dedupe heuristic, which the crude hash embedder trips on for
    # short/similar test strings regardless of how they're varied.
    res = client.post("/api/memories", json={"content": content, "force": True})
    assert res.status_code == 200, res.text
    return res.json()["id"]


def test_attach_a_local_file_to_a_memory(client, tmp_path):
    memory_id = _store(client)
    path = tmp_path / "design.txt"
    path.write_text("some design content")

    res = client.post(f"/api/memories/{memory_id}/attachments", json={"path": str(path)})
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["memory_id"] == memory_id
    assert data["status"] == "ok"
    assert data["size"] == path.stat().st_size
    assert len(data["sha256"]) == 64


def test_attach_with_derived_text_records_who_produced_it(client, tmp_path):
    memory_id = _store(client)
    path = tmp_path / "shot.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)

    res = client.post(
        f"/api/memories/{memory_id}/attachments",
        json={"path": str(path), "derived_text": "a screenshot of the login bug", "derived_by": "manual"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["derived_text"] == "a screenshot of the login bug"
    assert res.json()["derived_by"] == "manual"


def test_derived_by_defaults_to_none_without_derived_text(client, tmp_path):
    memory_id = _store(client)
    path = tmp_path / "a.txt"
    path.write_text("x")

    res = client.post(f"/api/memories/{memory_id}/attachments", json={"path": str(path)})
    assert res.json()["derived_by"] == "none"


def test_attaching_to_an_unknown_memory_fails(client, tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("x")
    res = client.post("/api/memories/does-not-exist/attachments", json={"path": str(path)})
    assert res.status_code == 400


def test_attaching_a_missing_path_fails(client, tmp_path):
    memory_id = _store(client)
    res = client.post(
        f"/api/memories/{memory_id}/attachments", json={"path": str(tmp_path / "nope.txt")}
    )
    assert res.status_code == 400


def test_list_attachments_for_a_memory(client, tmp_path):
    memory_id = _store(client)
    for name in ("a.txt", "b.txt"):
        p = tmp_path / name
        p.write_text(name)
        client.post(f"/api/memories/{memory_id}/attachments", json={"path": str(p)})

    res = client.get(f"/api/memories/{memory_id}/attachments")
    assert res.status_code == 200
    assert len(res.json()["attachments"]) == 2


def test_memories_list_and_recall_embed_attachments(client, tmp_path):
    memory_id = _store(client, "The roadmap review deck is attached here")
    path = tmp_path / "deck.pdf"
    path.write_bytes(b"%PDF-1.4 fake")
    client.post(f"/api/memories/{memory_id}/attachments", json={"path": str(path)})

    listed = client.get("/api/memories").json()
    row = next(m for m in listed if m["id"] == memory_id)
    assert len(row["attachments"]) == 1

    recalled = client.post(
        "/api/memories/recall", json={"query": "roadmap review deck", "top_k": 5}
    ).json()
    row = next(m for m in recalled["memories"] if m["id"] == memory_id)
    assert len(row["attachments"]) == 1

    single = client.get(f"/api/memories/{memory_id}").json()
    assert len(single["attachments"]) == 1


def test_verify_an_untouched_file_stays_ok(client, tmp_path):
    memory_id = _store(client)
    path = tmp_path / "a.txt"
    path.write_text("stable content")
    attachment_id = client.post(
        f"/api/memories/{memory_id}/attachments", json={"path": str(path)}
    ).json()["id"]

    res = client.post(f"/api/attachments/{attachment_id}/verify")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
    assert client.get("/api/conflicts", params={"status": "open"}).json()["conflicts"] == []


def test_verify_a_changed_file_raises_a_conflict_candidate(client, tmp_path):
    """#57's open decision: (c) a moved/changed attachment raises a conflict
    candidate for human review rather than silently altering the memory or
    just downgrading trust."""
    memory_id = _store(client)
    original_content = client.get(f"/api/memories/{memory_id}").json()["content"]
    path = tmp_path / "a.txt"
    path.write_text("original content")
    attachment_id = client.post(
        f"/api/memories/{memory_id}/attachments", json={"path": str(path)}
    ).json()["id"]

    path.write_text("edited content")
    res = client.post(f"/api/attachments/{attachment_id}/verify")
    assert res.status_code == 200
    assert res.json()["status"] == "changed"

    conflicts = client.get("/api/conflicts", params={"status": "open"}).json()["conflicts"]
    assert len(conflicts) == 1
    assert conflicts[0]["memory_id_a"] == memory_id
    assert conflicts[0]["memory_id_b"] == memory_id
    assert conflicts[0]["signal_type"] == "attachment_changed"

    # The memory itself is untouched — a candidate, never a verdict.
    memory = client.get(f"/api/memories/{memory_id}").json()
    assert memory["content"] == original_content


def test_restoring_the_file_resolves_its_conflict_candidate(client, tmp_path):
    memory_id = _store(client)
    path = tmp_path / "a.txt"
    path.write_text("original content")
    attachment_id = client.post(
        f"/api/memories/{memory_id}/attachments", json={"path": str(path)}
    ).json()["id"]

    path.write_text("edited content")
    client.post(f"/api/attachments/{attachment_id}/verify")
    assert len(client.get("/api/conflicts", params={"status": "open"}).json()["conflicts"]) == 1

    path.write_text("original content")
    res = client.post(f"/api/attachments/{attachment_id}/verify")
    assert res.json()["status"] == "ok"

    assert client.get("/api/conflicts", params={"status": "open"}).json()["conflicts"] == []
    resolved = client.get("/api/conflicts", params={"status": "resolved"}).json()["conflicts"]
    assert len(resolved) == 1
    assert resolved[0]["id"] == f"attachment:{attachment_id}"


def test_verify_a_deleted_file_raises_a_missing_conflict_candidate(client, tmp_path):
    memory_id = _store(client)
    path = tmp_path / "a.txt"
    path.write_text("content")
    attachment_id = client.post(
        f"/api/memories/{memory_id}/attachments", json={"path": str(path)}
    ).json()["id"]

    os.remove(path)
    res = client.post(f"/api/attachments/{attachment_id}/verify")
    assert res.json()["status"] == "missing"

    conflicts = client.get("/api/conflicts", params={"status": "open"}).json()["conflicts"]
    assert conflicts[0]["signal_type"] == "attachment_missing"


def test_verify_all_reports_counts_by_status(client, tmp_path):
    memory_id = _store(client)
    ok_path = tmp_path / "ok.txt"
    ok_path.write_text("fine")
    missing_path = tmp_path / "gone.txt"
    missing_path.write_text("temp")
    client.post(f"/api/memories/{memory_id}/attachments", json={"path": str(ok_path)})
    client.post(f"/api/memories/{memory_id}/attachments", json={"path": str(missing_path)})
    os.remove(missing_path)

    res = client.post("/api/attachments/verify-all")
    assert res.status_code == 200
    assert res.json() == {"ok": 1, "missing": 1, "changed": 0}


def test_verifying_an_unknown_attachment_404s(client):
    res = client.post("/api/attachments/does-not-exist/verify")
    assert res.status_code == 404


def test_delete_attachment(client, tmp_path):
    memory_id = _store(client)
    path = tmp_path / "a.txt"
    path.write_text("x")
    attachment_id = client.post(
        f"/api/memories/{memory_id}/attachments", json={"path": str(path)}
    ).json()["id"]

    res = client.delete(f"/api/attachments/{attachment_id}")
    assert res.status_code == 200
    assert res.json()["deleted"] is True
    assert client.get(f"/api/memories/{memory_id}/attachments").json()["attachments"] == []
    # The file on disk is left alone — only the record is removed.
    assert path.exists()


def test_deleting_an_unknown_attachment_404s(client):
    res = client.delete("/api/attachments/does-not-exist")
    assert res.status_code == 404


def test_deleting_a_memory_cascades_its_attachments(client, tmp_path):
    memory_id = _store(client)
    path = tmp_path / "a.txt"
    path.write_text("x")
    attachment_id = client.post(
        f"/api/memories/{memory_id}/attachments", json={"path": str(path)}
    ).json()["id"]

    client.delete(f"/api/memories/{memory_id}")
    res = client.post(f"/api/attachments/{attachment_id}/verify")
    assert res.status_code == 404


def test_upload_then_attach_the_returned_path(client):
    """The browser can't hand over an absolute path from a file picker, so it
    uploads bytes first and attaches the path handed back — same shape as
    /api/connectors/upload."""
    memory_id = _store(client)
    payload = b"uploaded bytes"
    res = client.post(
        "/api/attachments/upload",
        json={"filename": "notes.txt", "content_b64": base64.b64encode(payload).decode()},
    )
    assert res.status_code == 200, res.text
    uploaded = res.json()
    assert os.path.isabs(uploaded["path"])
    assert "notes" not in os.path.basename(uploaded["path"])

    attach = client.post(
        f"/api/memories/{memory_id}/attachments", json={"path": uploaded["path"]}
    )
    assert attach.status_code == 200, attach.text
    assert attach.json()["size"] == len(payload)


def test_upload_keeps_a_recognized_suffix(client):
    res = client.post(
        "/api/attachments/upload",
        json={"filename": "photo.PNG", "content_b64": base64.b64encode(b"x").decode()},
    )
    assert res.status_code == 200, res.text
    assert res.json()["path"].endswith(".png")


def test_upload_drops_an_unrecognized_suffix(client):
    """The on-disk suffix comes from a fixed allowlist (SAFE_UPLOAD_SUFFIXES),
    not a transform of the request — an unrecognized extension is dropped
    rather than carried through, same as /api/connectors/upload."""
    res = client.post(
        "/api/attachments/upload",
        json={"filename": "payload.sh", "content_b64": base64.b64encode(b"x").decode()},
    )
    assert res.status_code == 200, res.text
    assert not os.path.basename(res.json()["path"]).endswith(".sh")
    assert res.json()["filename"] == "payload.sh"


def test_upload_rejects_empty_file(client):
    res = client.post(
        "/api/attachments/upload", json={"filename": "x.txt", "content_b64": ""}
    )
    assert res.status_code == 400


def test_backup_restore_round_trips_attachments(client, tmp_path):
    memory_id = _store(client)
    path = tmp_path / "evidence.txt"
    path.write_text("proof")
    client.post(f"/api/memories/{memory_id}/attachments", json={"path": str(path)})

    backup = client.post("/api/backup", json={})
    assert backup.status_code == 200
    content_b64 = base64.b64encode(backup.content).decode()

    restore = client.post(
        "/api/restore", json={"content_b64": content_b64, "replace": True}
    )
    assert restore.status_code == 200, restore.text
    assert restore.json()["attachments"] == 1

    attachments = client.get(f"/api/memories/{memory_id}/attachments").json()["attachments"]
    assert len(attachments) == 1
    assert attachments[0]["path"] == str(path)

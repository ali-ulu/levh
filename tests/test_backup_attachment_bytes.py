"""A portable backup must carry the attachment files LEVH itself owns.

Attachment rows used to travel as a path, a hash and a size and nothing else.
Restoring on another machine wrote the same absolute path back, so the record
looked restored while the file it named was not there — and for a file
uploaded through LEVH, no other copy of it exists.

The tests below restore into a *different* database directory, because that is
the only arrangement in which the old behaviour is wrong: restoring into the
same directory happened to work, which is why the gap survived.
"""

from __future__ import annotations

import base64
import hashlib
import os

import pytest
import pytest_asyncio

from server.core.memory_engine import MemoryEngine

CONTENT = b"evidence bytes that only exist inside LEVH's own store"
NOTE = "Atlas incident review, with the console screenshot attached"


@pytest_asyncio.fixture
async def instance(tmp_path_factory, monkeypatch):
    """An engine whose attachment store lives in its own directory."""

    async def _make(name: str):
        directory = tmp_path_factory.mktemp(name)
        db_path = directory / "stackmemory.db"
        eng = MemoryEngine(db_path=str(db_path), embedder_mode="hash", short_term_max=20)
        await eng.initialize()
        return eng, directory

    return _make


def _point_store_at(monkeypatch, directory):
    """`attachments_dir()` reads the process-wide runtime config, so which
    instance owns the store is decided by patching it — the same way two
    machines would differ."""
    import server.core.attachment_store as store

    target = directory / "attachments"
    target.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(store, "attachments_dir", lambda: target)
    return target


async def _managed_attachment(eng, store_dir, content: bytes = CONTENT):
    """An upload, the way /api/attachments/upload makes one: LEVH writes the
    file into its own store under a name it invented."""
    memory = await eng.store(content=NOTE, memory_type="episodic")
    path = store_dir / "abc123.txt"
    path.write_bytes(content)
    await eng.attach_file(memory.id, str(path))
    return memory, path


@pytest.mark.asyncio
async def test_a_managed_attachment_travels_with_its_bytes(instance, monkeypatch):
    eng, directory = await instance("source")
    store_dir = _point_store_at(monkeypatch, directory)
    await _managed_attachment(eng, store_dir)

    snapshot = await eng.backup(app_version="test")

    assert snapshot["counts"]["attachments_carried"] == 1
    assert snapshot["counts"]["attachments_by_reference"] == 0
    row = snapshot["attachments"][0]
    assert row["carried"] is True
    assert base64.b64decode(row["content_b64"]) == CONTENT
    await eng.shutdown()


@pytest.mark.asyncio
async def test_restoring_on_a_clean_instance_produces_a_readable_file(instance, monkeypatch):
    source, source_dir = await instance("source")
    source_store = _point_store_at(monkeypatch, source_dir)
    memory, original_path = await _managed_attachment(source, source_store)
    snapshot = await source.backup(app_version="test")
    await source.shutdown()

    target, target_dir = await instance("target")
    target_store = _point_store_at(monkeypatch, target_dir)
    # The machine that made the backup is gone, and so is its attachments dir.
    original_path.unlink()

    result = await target.restore(snapshot, replace=True)

    assert result["attachment_files_written"] == 1
    assert result["attachment_files_failed"] == 0

    restored = (await target.list_memory_attachments(memory.id))[0]
    # The old absolute path belonged to another database directory. Keeping it
    # is what made the record look restored while the file was not there.
    assert restored["path"] != str(original_path)
    assert os.path.dirname(restored["path"]) == str(target_store)
    with open(restored["path"], "rb") as handle:
        assert handle.read() == CONTENT

    # And the file passes the product's own integrity check, unprompted.
    verified = await target.verify_attachment(restored["id"])
    assert verified["status"] == "ok"
    await target.shutdown()


@pytest.mark.asyncio
async def test_a_referenced_file_stays_a_reference(instance, monkeypatch):
    eng, directory = await instance("source")
    _point_store_at(monkeypatch, directory)

    memory = await eng.store(content=NOTE, memory_type="episodic")
    elsewhere = directory / "the-users-own-folder"
    elsewhere.mkdir()
    users_file = elsewhere / "report.txt"
    users_file.write_bytes(b"a document the user already owns")
    await eng.attach_file(memory.id, str(users_file))

    snapshot = await eng.backup(app_version="test")

    # Copying somebody's documents into a backup of their memories would take
    # more than was offered. The reference travels; the bytes do not.
    row = snapshot["attachments"][0]
    assert row["carried"] is False
    assert row["carry_skipped"] == "referenced"
    assert "content_b64" not in row
    assert snapshot["counts"]["attachments_by_reference"] == 1
    await eng.shutdown()


@pytest.mark.asyncio
async def test_a_file_over_the_ceiling_is_reported_not_dropped(instance, monkeypatch):
    eng, directory = await instance("source")
    store_dir = _point_store_at(monkeypatch, directory)
    await _managed_attachment(eng, store_dir)

    snapshot = await eng.backup(app_version="test", max_attachment_bytes=4)

    row = snapshot["attachments"][0]
    assert row["carried"] is False
    assert row["carry_skipped"] == "too_large"
    # The row still travels: the restored instance knows the attachment exists
    # and reports it missing, rather than forgetting it ever did.
    assert row["id"] and row["sha256"]
    await eng.shutdown()


@pytest.mark.asyncio
async def test_a_file_that_vanished_before_the_backup_is_recorded_as_unreadable(
    instance, monkeypatch
):
    eng, directory = await instance("source")
    store_dir = _point_store_at(monkeypatch, directory)
    _, path = await _managed_attachment(eng, store_dir)
    path.unlink()

    snapshot = await eng.backup(app_version="test")

    assert snapshot["attachments"][0]["carry_skipped"] == "unreadable"
    await eng.shutdown()


@pytest.mark.asyncio
async def test_carried_bytes_that_do_not_match_the_hash_are_refused(instance, monkeypatch):
    source, source_dir = await instance("source")
    source_store = _point_store_at(monkeypatch, source_dir)
    memory, _ = await _managed_attachment(source, source_store)
    snapshot = await source.backup(app_version="test")
    await source.shutdown()

    # A snapshot is untrusted input. Writing bytes that disagree with the hash
    # the row asserts would manufacture a "changed" attachment out of a backup
    # the user believed was intact.
    snapshot["attachments"][0]["content_b64"] = base64.b64encode(b"tampered").decode()

    target, target_dir = await instance("target")
    target_store = _point_store_at(monkeypatch, target_dir)
    result = await target.restore(snapshot, replace=True)

    assert result["attachment_files_written"] == 0
    assert result["attachment_files_failed"] == 1
    # Nothing reached this instance's store, so no file here can be mistaken
    # for a verified attachment.
    assert list(target_store.iterdir()) == []
    # The row still lands, pointing where it did — a verify pass reports it,
    # which is the whole difference from a silent loss.
    restored = (await target.list_memory_attachments(memory.id))[0]
    assert os.path.dirname(restored["path"]) != str(target_store)
    await target.shutdown()


@pytest.mark.asyncio
async def test_an_old_snapshot_without_carried_bytes_still_restores(instance, monkeypatch):
    source, source_dir = await instance("source")
    source_store = _point_store_at(monkeypatch, source_dir)
    memory, original_path = await _managed_attachment(source, source_store)
    snapshot = await source.backup(app_version="test")
    await source.shutdown()

    # What a backup taken before this change looks like.
    for row in snapshot["attachments"]:
        row.pop("content_b64", None)
        row.pop("carried", None)
        row.pop("carry_skipped", None)

    target, target_dir = await instance("target")
    _point_store_at(monkeypatch, target_dir)
    result = await target.restore(snapshot, replace=True)

    assert result["attachments"] == 1
    assert result["attachment_files_written"] == 0
    assert result["attachments_by_reference"] == 1
    # Unchanged behaviour for an unchanged input: the path comes back as it was.
    assert (await target.list_memory_attachments(memory.id))[0]["path"] == str(original_path)
    await target.shutdown()


@pytest.mark.asyncio
async def test_the_hash_is_the_one_recorded_at_attach_time(instance, monkeypatch):
    source, source_dir = await instance("source")
    source_store = _point_store_at(monkeypatch, source_dir)
    memory, _ = await _managed_attachment(source, source_store)
    snapshot = await source.backup(app_version="test")
    await source.shutdown()

    target, target_dir = await instance("target")
    _point_store_at(monkeypatch, target_dir)
    await target.restore(snapshot, replace=True)

    restored = (await target.list_memory_attachments(memory.id))[0]
    # Rebasing the hash to whatever was written would defeat the check it
    # exists for: it stays the hash of what was actually attached.
    assert restored["sha256"] == hashlib.sha256(CONTENT).hexdigest()
    await target.shutdown()

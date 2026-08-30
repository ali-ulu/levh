"""Tests for encrypted backup & restore (Faz 0 security): crypto round-trip,
backup snapshot serialization, engine.backup/restore (merge + replace),
/api/backup + /api/restore, and the create_backup/restore_backup MCP tools.
Offline & deterministic — EMBEDDER_MODE=hash."""

import base64
import os
import sys
import tempfile

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["EMBEDDER_MODE"] = "hash"

from server.core import backup as backup_mod
from server.core import crypto
from server.core.memory_engine import MemoryEngine


# ── crypto ───────────────────────────────────────────────────────────


def test_crypto_available():
    assert crypto.is_available()
    crypto.ensure_available()  # must not raise


def test_crypto_round_trip():
    data = "sensitive work memory 🔐".encode("utf-8")
    blob = crypto.encrypt(data, "correct horse battery staple")
    assert crypto.is_encrypted(blob)
    assert crypto.decrypt(blob, "correct horse battery staple") == data


def test_crypto_wrong_passphrase_rejected():
    blob = crypto.encrypt(b"secret", "right")
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(blob, "wrong")


def test_crypto_tamper_detected():
    blob = bytearray(crypto.encrypt(b"secret payload here", "pw"))
    blob[-1] ^= 0x01  # flip a bit in the ciphertext/HMAC
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(bytes(blob), "pw")


def test_crypto_empty_passphrase_rejected():
    with pytest.raises(ValueError):
        crypto.encrypt(b"x", "")


def test_crypto_unique_salt_per_encryption():
    a = crypto.encrypt(b"same", "pw")
    b = crypto.encrypt(b"same", "pw")
    assert a != b  # random salt -> different ciphertext each time


# ── backup blob serialization ────────────────────────────────────────


def _snap():
    return backup_mod.make_snapshot(
        memories=[{"id": "m1", "content": "hi"}],
        sessions=[{"id": "s1", "name": "work"}],
        app_version="2.12.0",
        created_at="2026-07-09T00:00:00+00:00",
    )


def test_backup_blob_plaintext_round_trip():
    snap = _snap()
    blob = backup_mod.make_backup_blob(snap)
    assert not crypto.is_encrypted(blob)
    out = backup_mod.read_backup_blob(blob)
    assert out["counts"] == {
        "memories": 1,
        "sessions": 1,
        "attachments": 0,
        # The envelope now states how many attachment files it actually
        # carries. The gap between these two numbers is precisely what a
        # restore on another machine cannot reproduce on its own.
        "attachments_carried": 0,
        "attachments_by_reference": 0,
    }
    assert out["format"] == backup_mod.BACKUP_FORMAT


def test_backup_blob_encrypted_round_trip():
    snap = _snap()
    blob = backup_mod.make_backup_blob(snap, passphrase="pw")
    assert crypto.is_encrypted(blob)
    out = backup_mod.read_backup_blob(blob, passphrase="pw")
    assert out["counts"]["memories"] == 1


def test_backup_blob_encrypted_requires_passphrase():
    blob = backup_mod.make_backup_blob(_snap(), passphrase="pw")
    with pytest.raises(backup_mod.BackupError):
        backup_mod.read_backup_blob(blob)  # no passphrase given


def test_backup_blob_rejects_foreign_json():
    with pytest.raises(backup_mod.BackupError):
        backup_mod.read_backup_blob(b'{"format": "something-else"}')


def test_backup_blob_rejects_garbage():
    with pytest.raises(backup_mod.BackupError):
        backup_mod.read_backup_blob(b"not json at all")


# ── engine backup / restore ──────────────────────────────────────────


@pytest_asyncio.fixture
async def engine():
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    eng = MemoryEngine(db_path=db_path, embedder_mode="hash", short_term_max=10)
    await eng.initialize()
    yield eng
    await eng.shutdown()
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest_asyncio.fixture
async def engine2():
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    eng = MemoryEngine(db_path=db_path, embedder_mode="hash", short_term_max=10)
    await eng.initialize()
    yield eng
    await eng.shutdown()
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.mark.asyncio
async def test_engine_backup_snapshot_shape(engine):
    s = await engine.create_session("work")
    await engine.store("pinned rule", memory_type="episodic", pinned=True, session_id=s.id)
    await engine.store("another memory", memory_type="episodic")

    snap = await engine.backup(app_version="2.12.0")
    assert snap["format"] == backup_mod.BACKUP_FORMAT
    assert snap["app_version"] == "2.12.0"
    assert snap["counts"]["memories"] == 2
    assert snap["counts"]["sessions"] == 1
    assert any(m["pinned"] for m in snap["memories"])


@pytest.mark.asyncio
async def test_engine_restore_merge_into_fresh(engine, engine2):
    await engine.create_session("work")
    await engine.store("decision to keep", memory_type="episodic", importance=0.9, pinned=True)
    snap = await engine.backup()

    result = await engine2.restore(snap)
    assert result["memories"] == 1
    assert result["sessions"] == 1

    stats = await engine2.get_stats()
    assert stats.total_memories == 1
    assert stats.pinned_count == 1
    assert len(await engine2.list_sessions()) == 1


@pytest.mark.asyncio
async def test_engine_restore_replace_wipes_existing(engine, engine2):
    # engine2 starts with its own data that should be gone after replace-restore
    await engine2.store("stale local memory", memory_type="episodic")
    await engine.store("the only survivor", memory_type="episodic")
    snap = await engine.backup()

    await engine2.restore(snap, replace=True)
    mems = await engine2.export_memories()
    contents = {m["content"] for m in mems}
    assert contents == {"the only survivor"}


@pytest.mark.asyncio
async def test_engine_restore_rejects_non_backup(engine):
    with pytest.raises(ValueError):
        await engine.restore({"not": "a backup"})


@pytest.mark.asyncio
async def test_engine_restore_preserves_decay_state(engine, engine2):
    m = await engine.store("high-importance fact", memory_type="episodic", importance=0.95)
    snap = await engine.backup()
    await engine2.restore(snap)

    restored = await engine2.episodic.get(m.id)
    assert restored is not None
    assert restored.importance == 0.95


# ── API ──────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def api_client():
    from httpx import ASGITransport, AsyncClient

    import server.api as api_mod

    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    if api_mod._engine is not None:
        await api_mod._engine.shutdown()
    api_mod._engine = MemoryEngine(db_path=db_path, embedder_mode="hash", short_term_max=50)
    await api_mod._engine.initialize()
    api_mod._initialized = True
    transport = ASGITransport(app=api_mod.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await api_mod._engine.shutdown()
    api_mod._engine = None
    api_mod._initialized = False
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.mark.asyncio
async def test_api_backup_then_restore_plaintext(api_client):
    await api_client.post(
        "/api/memories",
        json={"content": "backup me please", "memory_type": "episodic"},
    )
    r = await api_client.post("/api/backup", json={})
    assert r.status_code == 200
    assert r.headers["X-Backup-Encrypted"] == "0"
    assert r.headers["X-Backup-Memories"] == "1"
    blob = r.content

    # wipe and restore via base64 body
    content_b64 = base64.b64encode(blob).decode("ascii")
    r2 = await api_client.post(
        "/api/restore", json={"content_b64": content_b64, "replace": True}
    )
    assert r2.status_code == 200
    assert r2.json()["memories"] == 1


@pytest.mark.asyncio
async def test_api_backup_encrypted_and_restore(api_client):
    await api_client.post(
        "/api/memories",
        json={"content": "secret memory", "memory_type": "episodic"},
    )
    r = await api_client.post("/api/backup", json={"passphrase": "s3cret"})
    assert r.status_code == 200
    assert r.headers["X-Backup-Encrypted"] == "1"
    blob = r.content
    assert crypto.is_encrypted(blob)

    content_b64 = base64.b64encode(blob).decode("ascii")

    # wrong passphrase -> 400
    bad = await api_client.post(
        "/api/restore", json={"content_b64": content_b64, "passphrase": "nope"}
    )
    assert bad.status_code == 400

    # right passphrase -> ok
    good = await api_client.post(
        "/api/restore",
        json={"content_b64": content_b64, "passphrase": "s3cret", "replace": True},
    )
    assert good.status_code == 200
    assert good.json()["memories"] == 1


@pytest.mark.asyncio
async def test_api_restore_bad_base64(api_client):
    r = await api_client.post("/api/restore", json={"content_b64": "!!!not base64!!!"})
    assert r.status_code == 400


# ── MCP tools ────────────────────────────────────────────────────────


def _tool_text(result) -> str:
    if isinstance(result, tuple):
        _blocks, meta = result
        if isinstance(meta, dict) and "result" in meta:
            return meta["result"]
        return "\n".join(getattr(b, "text", str(b)) for b in _blocks)
    if isinstance(result, dict) and "result" in result:
        return result["result"]
    return str(result)


@pytest.mark.asyncio
async def test_mcp_backup_restore_file_round_trip(engine, engine2):
    from mcp.server.fastmcp import FastMCP

    from server.tools.backup import register as reg_backup

    await engine.store("mcp backup memory", memory_type="episodic", pinned=True)

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "backup.smbackup")

        mcp1 = FastMCP("t1")
        reg_backup(mcp1, engine)
        out = _tool_text(await mcp1.call_tool("create_backup", {"path": path, "passphrase": "pw"}))
        assert "Backup written" in out
        assert "encrypted" in out
        assert os.path.exists(path)

        mcp2 = FastMCP("t2")
        reg_backup(mcp2, engine2)
        res = _tool_text(
            await mcp2.call_tool("restore_backup", {"path": path, "passphrase": "pw"})
        )
        assert "Restored" in res
        stats = await engine2.get_stats()
        assert stats.total_memories == 1
        assert stats.pinned_count == 1


@pytest.mark.asyncio
async def test_mcp_restore_missing_file(engine):
    from mcp.server.fastmcp import FastMCP

    from server.tools.backup import register as reg_backup

    mcp = FastMCP("t")
    reg_backup(mcp, engine)
    res = _tool_text(await mcp.call_tool("restore_backup", {"path": "/nonexistent/x.json"}))
    assert "No backup file" in res


@pytest.mark.asyncio
async def test_mcp_restore_wrong_passphrase(engine, engine2):
    from mcp.server.fastmcp import FastMCP

    from server.tools.backup import register as reg_backup

    await engine.store("x", memory_type="episodic")
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "b.smbackup")
        mcp = FastMCP("t")
        reg_backup(mcp, engine)
        await mcp.call_tool("create_backup", {"path": path, "passphrase": "right"})

        mcp2 = FastMCP("t2")
        reg_backup(mcp2, engine2)
        res = _tool_text(
            await mcp2.call_tool("restore_backup", {"path": path, "passphrase": "wrong"})
        )
        assert "failed" in res.lower()

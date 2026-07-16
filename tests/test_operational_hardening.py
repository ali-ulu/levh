from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from server.core.database import CURRENT_SCHEMA_VERSION, Database
from server.core.embedder import Embedder
from server.core.memory_engine import MemoryEngine
from server.core.rate_limit import SlidingWindowRateLimiter


def _memory(memory_id: str, content: str) -> dict:
    return {
        "id": memory_id,
        "content": content,
        "memory_type": "episodic",
        "embedding": [0.1, 0.2],
        "importance": 0.5,
        "frequency": 1,
        "tags": [],
        "session_id": None,
        "project": "test",
        "source": "manual",
        "pinned": False,
        "metadata": {},
        "hscore": None,
        "created_at": "2026-01-01T00:00:00+00:00",
        "accessed_at": "2026-01-01T00:00:00+00:00",
        "decay_factor": 1.0,
        "stability_hours": 168.0,
        "recall_count": 0,
    }


def test_auto_embedder_is_local_first_even_with_openai_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-secret")

    def _fake_local(self: Embedder) -> None:
        self.mode = "hash"
        self.dimension = 384
        self.fallback_reason = "test fallback"

    monkeypatch.setattr(Embedder, "_init_local", _fake_local)
    embedder = Embedder("auto")

    assert embedder.requested_mode == "auto"
    assert embedder.resolved_mode == "local"
    assert embedder.mode == "hash"
    assert embedder.identity()["provider"] != "openai"


def test_explicit_openai_mode_still_selects_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "explicit-secret")
    embedder = Embedder("openai")
    assert embedder.resolved_mode == "openai"
    assert embedder.identity()["provider"] == "openai"


@pytest.mark.asyncio
async def test_sqlite_runtime_wal_busy_timeout_schema_and_fts(tmp_path):
    db = Database(str(tmp_path / "runtime.db"))
    await db.connect()
    try:
        status = await db.runtime_status()
        assert str(status["journal_mode"]).lower() == "wal"
        assert int(status["busy_timeout_ms"]) >= 5_000
        assert int(status["foreign_keys"]) == 1
        assert status["schema_version"] == CURRENT_SCHEMA_VERSION
        assert status["fts5_available"] is True
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_fts_search_tracks_insert_update_and_delete(tmp_path):
    db = Database(str(tmp_path / "fts.db"))
    await db.connect()
    try:
        if not db.fts5_available:
            pytest.skip("SQLite build has no FTS5")
        await db.insert_memory(_memory("m1", "Atlas uses PostgreSQL in production"))
        await db.insert_memory(_memory("m2", "Beacon uses SQLite for local tests"))

        rows = await db.search_memories(content_like="Postgres", limit=10)
        assert [row["id"] for row in rows] == ["m1"]

        await db.update_memory("m1", {"content": "Atlas uses CockroachDB in production"})
        assert await db.search_memories(content_like="Postgres", limit=10) == []
        rows = await db.search_memories(content_like="Cockroach", limit=10)
        assert [row["id"] for row in rows] == ["m1"]

        await db.delete_memory("m1")
        assert await db.search_memories(content_like="Cockroach", limit=10) == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_future_schema_version_fails_closed(tmp_path):
    path = tmp_path / "future.db"
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version = 999")
    db = Database(str(path))
    try:
        with pytest.raises(RuntimeError, match="newer than supported"):
            await db.connect()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_pre_versioned_database_is_backfilled_into_fts(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                memory_type TEXT NOT NULL DEFAULT 'short_term',
                embedding TEXT,
                importance REAL DEFAULT 0.5,
                frequency INTEGER DEFAULT 1,
                tags TEXT,
                session_id TEXT,
                metadata TEXT,
                hscore REAL,
                created_at TEXT NOT NULL,
                accessed_at TEXT NOT NULL,
                decay_factor REAL DEFAULT 1.0
            )
            """
        )
        conn.execute(
            """
            INSERT INTO memories
            (id, content, memory_type, created_at, accessed_at)
            VALUES ('legacy', 'Legacy Atlas PostgreSQL decision', 'episodic',
                    '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
            """
        )

    db = Database(str(path))
    await db.connect()
    try:
        assert db.schema_version == CURRENT_SCHEMA_VERSION
        rows = await db.search_memories(content_like="Postgres")
        assert [row["id"] for row in rows] == ["legacy"]
    finally:
        await db.close()


def test_sliding_window_limiter_is_deterministic():
    limiter = SlidingWindowRateLimiter(limit=2, window_seconds=10)
    assert limiter.allow("client", now=0) == (True, 0)
    assert limiter.allow("client", now=1) == (True, 0)
    allowed, retry_after = limiter.allow("client", now=2)
    assert allowed is False
    assert retry_after >= 8
    assert limiter.allow("other", now=2) == (True, 0)
    assert limiter.allow("client", now=11) == (True, 0)


@pytest.mark.asyncio
async def test_token_gate_rate_limits_bad_auth_attempts(monkeypatch):
    import server.api as api_mod

    old_token = api_mod._API_TOKEN
    old_auth = api_mod._auth_limiter
    old_api = api_mod._api_limiter
    api_mod._API_TOKEN = "correct-token"
    api_mod._auth_limiter = SlidingWindowRateLimiter(2, 60)
    api_mod._api_limiter = SlidingWindowRateLimiter(100, 60)
    try:
        transport = ASGITransport(app=api_mod.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.get("/api/stats", headers={"X-LEVH-Token": "bad"})
            second = await client.get("/api/stats", headers={"X-LEVH-Token": "bad"})
            third = await client.get("/api/stats", headers={"X-LEVH-Token": "bad"})
        assert [first.status_code, second.status_code, third.status_code] == [401, 401, 429]
        assert int(third.headers["Retry-After"]) >= 1
    finally:
        api_mod._API_TOKEN = old_token
        api_mod._auth_limiter = old_auth
        api_mod._api_limiter = old_api


@pytest.mark.asyncio
async def test_replace_restore_creates_recoverable_safety_backup(tmp_path):
    target_path = tmp_path / "target.db"
    source_path = tmp_path / "source.db"
    target = MemoryEngine(db_path=str(target_path), embedder_mode="hash")
    source = MemoryEngine(db_path=str(source_path), embedder_mode="hash")
    await target.initialize()
    await source.initialize()
    try:
        old = await target.store("old local state", memory_type="episodic")
        await source.store("new restored state", memory_type="episodic")
        snapshot = await source.backup()

        result = await target.restore(snapshot, replace=True)
        backup_path = result["safety_backup_path"]
        assert backup_path
        assert Path(backup_path).exists()
        if os.name != "nt":
            assert Path(backup_path).stat().st_mode & 0o777 == 0o600

        with sqlite3.connect(backup_path) as conn:
            row = conn.execute("SELECT content FROM memories WHERE id = ?", (old.id,)).fetchone()
        assert row == ("old local state",)
    finally:
        await target.shutdown()
        await source.shutdown()


@pytest.mark.asyncio
async def test_replace_restore_fails_closed_when_safety_backup_fails(tmp_path, monkeypatch):
    target = MemoryEngine(db_path=str(tmp_path / "target-fail.db"), embedder_mode="hash")
    source = MemoryEngine(db_path=str(tmp_path / "source-fail.db"), embedder_mode="hash")
    await target.initialize()
    await source.initialize()
    try:
        old = await target.store("old state survives", memory_type="episodic")
        await source.store("replacement state", memory_type="episodic")
        snapshot = await source.backup()

        async def _fail_backup(*_args, **_kwargs):
            raise OSError("backup volume unavailable")

        monkeypatch.setattr(target.db, "create_safety_backup", _fail_backup)
        with pytest.raises(OSError, match="backup volume unavailable"):
            await target.restore(snapshot, replace=True)
        assert await target.get_memory(old.id) is not None
        assert (await target.get_stats()).total_memories == 1
    finally:
        await target.shutdown()
        await source.shutdown()


def test_docker_runs_non_root_with_healthcheck_and_loopback_compose():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    assert "USER stackmemory" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "/api/health" in dockerfile
    assert '127.0.0.1:8000:8000' in compose


def test_network_connectors_define_client_level_timeouts():
    github = Path("server/connectors/github.py").read_text(encoding="utf-8")
    notion = Path("server/connectors/notion.py").read_text(encoding="utf-8")
    assert "AsyncClient(\n            timeout=httpx.Timeout" in github
    assert "AsyncClient(\n            timeout=httpx.Timeout" in notion


def test_doctor_reports_local_route_and_sqlite_runtime(tmp_path, monkeypatch, capsys):
    path = tmp_path / "doctor.db"

    async def _create() -> None:
        db = Database(str(path))
        await db.connect()
        await db.close()

    import asyncio

    asyncio.run(_create())
    monkeypatch.setenv("SQLITE_DB_PATH", str(path))
    monkeypatch.setenv("EMBEDDER_MODE", "hash")

    from server.cli import cmd_doctor

    assert cmd_doctor(argparse.Namespace()) == 0
    output = capsys.readouterr().out
    assert "route=local/offline" in output
    assert "SQLite runtime" in output
    assert "journal=wal" in output
    assert f"schema={CURRENT_SCHEMA_VERSION}/{CURRENT_SCHEMA_VERSION}" in output

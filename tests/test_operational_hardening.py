from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from server.auth import constant_time_token_matches, shared_auth_limiter
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

    assert constant_time_token_matches(None, "correct-token") is False
    assert constant_time_token_matches("", "correct-token") is False
    assert constant_time_token_matches("yanlış", "correct-token") is False
    assert constant_time_token_matches("doğru-token", "doğru-token") is True

    # The token and the limiters live in server.routes.deps, which is the one
    # place the middleware reads them from.
    from server.routes import deps

    old_token = os.environ.get("LEVH_TOKEN")
    old_auth = deps.auth_limiter
    old_api = deps.api_limiter
    os.environ["LEVH_TOKEN"] = "correct-token"
    deps.auth_limiter = SlidingWindowRateLimiter(10, 60)
    deps.api_limiter = SlidingWindowRateLimiter(100, 60)
    try:
        transport = ASGITransport(app=api_mod.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            unicode_response = await client.get(
                "/api/stats",
                headers=[(b"x-levh-token", "yanlış".encode("utf-8"))],
            )
            deps.auth_limiter = SlidingWindowRateLimiter(2, 60)
            first = await client.get("/api/stats", headers={"X-LEVH-Token": "bad"})
            second = await client.get("/api/stats", headers={"X-LEVH-Token": "bad"})
            third = await client.get("/api/stats", headers={"X-LEVH-Token": "bad"})
        assert unicode_response.status_code == 401
        assert [first.status_code, second.status_code, third.status_code] == [401, 401, 429]
        assert int(third.headers["Retry-After"]) >= 1
    finally:
        if old_token is None:
            os.environ.pop("LEVH_TOKEN", None)
        else:
            os.environ["LEVH_TOKEN"] = old_token
        deps.auth_limiter = old_auth
        deps.api_limiter = old_api
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


@pytest.mark.asyncio
async def test_docs_surface_requires_the_token_when_it_is_set(monkeypatch):
    """The root-level docs routes are part of the gate, not a way around it (#144).

    Withheld, not 401: a browser cannot attach ``X-LEVH-Token`` while loading
    ``/docs``, so a 401 would leave the operator with a docs URL that can never
    be opened. The surface is withheld entirely and ``LEVH_ENABLE_API_DOCS`` is
    the way back (#159); the browser-driven flow is covered by
    ``tests/test_api_docs_gate.py``.
    """
    from server.api import app

    monkeypatch.setenv("LEVH_TOKEN", "correct-token")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for path in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
            for headers in ({}, {"X-LEVH-Token": "wrong"}):
                response = await client.get(path, headers=headers)
                assert response.status_code == 404, f"{path} leaked without a token"


@pytest.mark.asyncio
async def test_docs_surface_opt_in_restores_it_behind_the_token(monkeypatch):
    """``LEVH_ENABLE_API_DOCS=true`` restores the surface the operator asked for."""
    from server.api import app

    monkeypatch.setenv("LEVH_TOKEN", "correct-token")
    monkeypatch.setenv("LEVH_ENABLE_API_DOCS", "true")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert (await client.get(path)).status_code == 200, path
        # The opt-in widens the docs surface only; /api keeps its gate.
        assert (await client.get("/api/stats")).status_code == 401


@pytest.mark.asyncio
async def test_docs_surface_stays_open_when_no_token_is_configured(monkeypatch):
    """Zero-config local use keeps its docs; the gate only exists with a token."""
    from server.api import app

    monkeypatch.delenv("LEVH_TOKEN", raising=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/docs")).status_code == 200
        assert (await client.get("/openapi.json")).status_code == 200


def test_docker_runs_non_root_with_healthcheck_and_loopback_compose():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    assert "USER stackmemory" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "/api/health" in dockerfile
    assert '127.0.0.1:8000:8000' in compose
    assert "LEVH_ALLOW_REMOTE_WITHOUT_TOKEN" not in dockerfile
    assert "LEVH_ALLOW_REMOTE_WITHOUT_TOKEN=true" in compose
    assert "Safe ONLY because" in compose


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


@pytest.mark.asyncio
async def test_health_reports_standing_remote_access_state(monkeypatch):
    """The open state survives the startup warning (issue #151)."""
    from server.api import app
    from server.auth import ALLOW_REMOTE_WITHOUT_TOKEN_ENV

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        monkeypatch.delenv(ALLOW_REMOTE_WITHOUT_TOKEN_ENV, raising=False)
        monkeypatch.delenv("STACKMEMORY_ALLOW_REMOTE_WITHOUT_TOKEN", raising=False)
        monkeypatch.delenv("LEVH_TOKEN", raising=False)
        closed = (await client.get("/api/health")).json()
        assert closed["auth_required"] is False
        assert closed["unauthenticated_remote_access"] is False

        monkeypatch.setenv(ALLOW_REMOTE_WITHOUT_TOKEN_ENV, "true")
        opened = (await client.get("/api/health")).json()
        assert opened["unauthenticated_remote_access"] is True

        # Adding a token closes the boundary even with the override still set.
        monkeypatch.setenv("LEVH_TOKEN", "configured")
        gated = (await client.get("/api/health")).json()
        assert gated["auth_required"] is True
        assert gated["unauthenticated_remote_access"] is False


def test_doctor_fails_on_tokenless_override_with_non_loopback_bind(tmp_path, monkeypatch, capsys):
    """The compose override plus a widened bind must not pass silently."""
    from server.auth import ALLOW_REMOTE_WITHOUT_TOKEN_ENV
    from server.cli import cmd_doctor

    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "doctor-remote.db"))
    monkeypatch.setenv("EMBEDDER_MODE", "hash")
    monkeypatch.delenv("LEVH_TOKEN", raising=False)
    monkeypatch.setenv(ALLOW_REMOTE_WITHOUT_TOKEN_ENV, "true")

    # The override alone, on the default loopback bind, stays a warning.
    assert cmd_doctor(argparse.Namespace()) == 0
    warning = capsys.readouterr().out
    assert "Remote access" in warning
    assert "WARN" in warning

    monkeypatch.setenv("API_HOST", "0.0.0.0")
    assert cmd_doctor(argparse.Namespace()) == 1
    failure = capsys.readouterr().out
    assert "Remote access" in failure
    assert "FAIL" in failure
    assert "Verdict: FAIL" in failure

    # A token satisfies the gate without touching the bind.
    monkeypatch.setenv("LEVH_TOKEN", "configured")
    assert cmd_doctor(argparse.Namespace()) == 0
    assert "FAIL" not in capsys.readouterr().out


def test_api_module_builds_no_second_rate_limiter():
    """server.api used to keep its own limiter/settings block, a leftover from
    before they moved to server.routes.deps. Nothing read it, and a stray use
    of it would have enforced limits that disagreed with the ones the
    middleware actually consults (issue #129)."""
    import server.api as api_mod
    from server.routes import deps

    for symbol in (
        "_AUTH_RATE_LIMIT",
        "_API_RATE_LIMIT",
        "_RATE_LIMIT_WINDOW",
        "_auth_limiter",
        "_api_limiter",
    ):
        assert not hasattr(api_mod, symbol), f"server.api still defines {symbol}"

    # One source of truth, and it is the one the middleware reads.
    assert deps.auth_limiter is shared_auth_limiter
    assert isinstance(deps.api_limiter, SlidingWindowRateLimiter)

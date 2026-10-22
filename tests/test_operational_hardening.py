from __future__ import annotations

import argparse
import os
import sqlite3
import threading
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


# ── Deferred local-embedder load (issue #525) ────────────────────────
#
# Constructing a local embedder imports torch and builds all-MiniLM-L6-v2,
# ~2.5 minutes on a cold machine. That used to happen inline on the request
# path (the ``engine.embedder`` property), freezing the whole event loop for
# the first store/recall after a restart with no readiness signal. These tests
# pin the two properties that fix it: construction must not load the model,
# and the load must run off the loop and be reported while it runs.


def test_deferred_construction_does_not_import_torch(monkeypatch):
    """Building the instance must be cheap even when a local model is present."""
    monkeypatch.setattr(
        "server.core.embedder.local_embedder_available", lambda: True
    )
    loaded: list[str] = []
    monkeypatch.setattr(
        Embedder, "_init_local", lambda self: loaded.append("loaded")
    )

    embedder = Embedder("auto", defer_local_load=True)

    assert loaded == []
    assert embedder.mode == "local"
    assert embedder.warm_state == "cold"
    assert embedder.needs_warm() is True
    assert embedder.is_semantic is True


def test_deferred_construction_without_local_falls_back_immediately(monkeypatch):
    """A missing sentence-transformers is a hash fallback, known up front."""
    monkeypatch.setattr(
        "server.core.embedder.local_embedder_available", lambda: False
    )

    embedder = Embedder("auto", defer_local_load=True)

    assert embedder.mode == "hash"
    assert embedder.warm_state == "unavailable"
    assert embedder.needs_warm() is False
    assert embedder.fallback is True
    assert "levh[local]" in embedder.fallback_reason


@pytest.mark.asyncio
async def test_warm_runs_the_load_off_the_event_loop(monkeypatch):
    """``warm()`` must not run the blocking load on the calling thread."""
    monkeypatch.setattr(
        "server.core.embedder.local_embedder_available", lambda: True
    )
    threads: list[str] = []

    def _fake_local(self: Embedder) -> None:
        threads.append(threading.current_thread().name)
        self._model = object()
        self.dimension = 384

    monkeypatch.setattr(Embedder, "_init_local", _fake_local)
    embedder = Embedder("local", defer_local_load=True)

    loop_thread = threading.current_thread().name
    await embedder.warm()

    assert embedder.warm_state == "ready"
    assert embedder.needs_warm() is False
    assert threads and threads[0] != loop_thread


@pytest.mark.asyncio
async def test_warm_is_idempotent_and_never_loads_twice(monkeypatch):
    monkeypatch.setattr(
        "server.core.embedder.local_embedder_available", lambda: True
    )
    calls: list[int] = []

    def _fake_local(self: Embedder) -> None:
        calls.append(1)
        self._model = object()

    monkeypatch.setattr(Embedder, "_init_local", _fake_local)
    embedder = Embedder("local", defer_local_load=True)

    await embedder.warm()
    await embedder.warm()

    assert calls == [1]


@pytest.mark.asyncio
async def test_warm_records_a_failed_load_and_degrades_to_hash(monkeypatch, caplog):
    """A failed deferred load must degrade loudly, not leave a dead embedder."""
    monkeypatch.setattr(
        "server.core.embedder.local_embedder_available", lambda: True
    )

    def _explode(self: Embedder) -> None:
        raise RuntimeError("no torch for you")

    monkeypatch.setattr(Embedder, "_init_local", _explode)
    embedder = Embedder("local", defer_local_load=True)

    with caplog.at_level("WARNING", logger="levh.embedder"):
        await embedder.warm()

    assert embedder.mode == "hash"
    assert embedder.warm_state == "unavailable"
    assert embedder.fallback_reason
    assert embedder.fallback is True
    assert any("resolved=hash" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_embed_warms_a_deferred_model_before_using_it(monkeypatch):
    """A request that beats startup warmup still works, via ``embed``."""
    monkeypatch.setattr(
        "server.core.embedder.local_embedder_available", lambda: True
    )

    class _Model:
        def encode(self, text, convert_to_numpy=True):
            import numpy as np

            return np.ones(8)

    def _fake_local(self: Embedder) -> None:
        self._model = _Model()
        self.dimension = 8

    monkeypatch.setattr(Embedder, "_init_local", _fake_local)
    embedder = Embedder("local", defer_local_load=True)

    vector = await embedder.embed("hello")

    assert embedder.warm_state == "ready"
    assert len(vector) == 8


@pytest.mark.asyncio
async def test_engine_warm_embedder_is_a_noop_for_hash(monkeypatch, tmp_path):
    """The default (hash) install must not pay a thread hop at startup."""
    monkeypatch.setenv("EMBEDDER_MODE", "hash")
    engine = MemoryEngine(
        db_path=str(tmp_path / "levh.db"), embedder_mode="hash", short_term_max=10
    )

    await engine.warm_embedder()

    assert engine._embedder is not None
    assert engine._embedder.warm_state == "ready"


@pytest.mark.asyncio
async def test_engine_embedder_property_does_not_block_on_local_load(monkeypatch, tmp_path):
    """Reading ``engine.embedder`` must not import torch inline (#525)."""
    monkeypatch.setattr(
        "server.core.embedder.local_embedder_available", lambda: True
    )
    loaded: list[str] = []
    monkeypatch.setattr(Embedder, "_init_local", lambda self: loaded.append("x"))
    engine = MemoryEngine(
        db_path=str(tmp_path / "levh.db"), embedder_mode="local", short_term_max=10
    )

    embedder = engine.embedder

    assert loaded == []
    assert embedder.warm_state == "cold"


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
async def test_safety_backup_cannot_interleave_with_a_derived_rebuild(tmp_path):
    """The pre-restore safety backup must wait for a rebuild holding write_lock.

    Regression guard for a hard hang. ``Connection.backup`` is queued on the
    shared aiosqlite connection, but its worker-thread call retries in 250 ms
    sleeps until SQLite reports it done, so it can outlive the statements queued
    around it. A derived-state rebuild issues its reindex statements while
    holding ``write_lock``; a backup queued in that window sat ahead of the
    rebuild's own queued commit, so neither the backup nor the rebuild's commit
    could run and ``/api/restore`` never answered — no error, no timeout, just a
    wedged request. Asserting the backup blocks while the lock is held pins the
    mutual exclusion that makes the interleave impossible.
    """
    import asyncio

    engine = MemoryEngine(db_path=str(tmp_path / "lock.db"), embedder_mode="hash")
    await engine.initialize()
    try:
        await engine.store("state to protect", memory_type="episodic")

        # Hold the lock the rebuild holds, then ask for a backup. The backup
        # must not complete: that is exactly the window that used to hang.
        await engine.db.write_lock.acquire()
        backup = asyncio.create_task(engine.db.create_safety_backup())
        done, _pending = await asyncio.wait({backup}, timeout=0.5)
        assert not done, "safety backup ran while a rebuild held write_lock"
        assert not backup.done()

        engine.db.write_lock.release()
        path = await asyncio.wait_for(backup, timeout=10)
        assert path and Path(path).exists()
    finally:
        await engine.shutdown()


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
async def test_doctor_inspects_sqlite_from_a_running_event_loop(tmp_path, monkeypatch, capsys):
    """The SQLite check survives being called from inside an event loop (#271).

    ``cmd_doctor`` is synchronous, but pytest's async tests (and the quarantine
    suite) call it while a loop is running. It used to use ``asyncio.run``
    directly, which raises there; the broad ``except`` swallowed the error and
    downgraded the check to a WARN, so the WAL/busy_timeout/schema contract was
    never actually verified in that context.
    """
    path = tmp_path / "doctor-loop.db"
    db = Database(str(path))
    await db.connect()
    await db.close()

    monkeypatch.setenv("SQLITE_DB_PATH", str(path))
    monkeypatch.setenv("EMBEDDER_MODE", "hash")

    from server.cli import cmd_doctor

    assert cmd_doctor(argparse.Namespace()) == 0
    output = capsys.readouterr().out
    assert "SQLite runtime" in output
    assert "journal=wal" in output
    assert "could not inspect" not in output
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
    from server.commands.doctor import NO_LIVE_PROBE_ENV

    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "doctor-remote.db"))
    monkeypatch.setenv("EMBEDDER_MODE", "hash")
    monkeypatch.delenv("LEVH_TOKEN", raising=False)
    monkeypatch.setenv(ALLOW_REMOTE_WITHOUT_TOKEN_ENV, "true")
    # The live probe answers whoever listens on the candidate ports — on a
    # dev machine an unrelated server on 8000 — which darkened the FAIL
    # assertion below. These tests assert the argv/config path, so the probe
    # stays off (issue #389).
    monkeypatch.setenv(NO_LIVE_PROBE_ENV, "1")

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


def test_doctor_reports_pip_debris_and_offers_the_repair(tmp_path, monkeypatch, capsys):
    """Issue #524: an interrupted update leaves pip wedged in this interpreter.

    The documented recovery step is itself a pip command, so doctor has to
    report the remnants with a way out that does not need pip.
    """
    from server.cli import cmd_doctor
    from server.commands.doctor import NO_LIVE_PROBE_ENV
    from server.core import pip_debris

    site = tmp_path / "site-packages"
    site.mkdir()
    (site / "~evh").mkdir()
    (site / "~umpy.libs").mkdir()
    monkeypatch.setattr(pip_debris, "target_site_packages", lambda: site)
    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "doctor-debris.db"))
    monkeypatch.setenv("EMBEDDER_MODE", "hash")
    monkeypatch.setenv(NO_LIVE_PROBE_ENV, "1")
    monkeypatch.setenv("LEVH_SUPERVISOR_LOG_DIR", str(tmp_path / "debris-logs"))

    assert cmd_doctor(argparse.Namespace()) == 1
    output = capsys.readouterr().out
    assert "Pip debris" in output
    assert "~evh" in output and "~umpy.libs" in output
    assert "--fix-debris" in output


def test_doctor_fix_debris_moves_remnants_aside_reversibly(tmp_path, monkeypatch, capsys):
    from server.cli import cmd_doctor
    from server.commands.doctor import NO_LIVE_PROBE_ENV
    from server.core import pip_debris

    site = tmp_path / "site-packages"
    site.mkdir()
    (site / "~evh").mkdir()
    (site / "~evh" / "payload.txt").write_text("only copy", encoding="utf-8")
    monkeypatch.setattr(pip_debris, "target_site_packages", lambda: site)
    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "doctor-fix-debris.db"))
    monkeypatch.setenv("EMBEDDER_MODE", "hash")
    monkeypatch.setenv(NO_LIVE_PROBE_ENV, "1")
    monkeypatch.setenv("LEVH_SUPERVISOR_LOG_DIR", str(tmp_path / "fix-logs"))

    assert cmd_doctor(argparse.Namespace(fix_debris=True)) == 0
    output = capsys.readouterr().out
    assert "Pip debris" in output
    assert "moved 1 remnant(s) aside" in output
    # Nothing deleted: the remnant, and what it held, survive in the backup.
    assert not (site / "~evh").exists()
    backups = [p for p in tmp_path.iterdir() if p.name.startswith(pip_debris.BACKUP_DIR_PREFIX)]
    assert len(backups) == 1
    assert (backups[0] / "~evh" / "payload.txt").read_text(encoding="utf-8") == "only copy"


def test_doctor_fix_debris_is_clean_when_there_is_nothing_to_move(tmp_path, monkeypatch, capsys):
    from server.cli import cmd_doctor
    from server.commands.doctor import NO_LIVE_PROBE_ENV
    from server.core import pip_debris

    site = tmp_path / "site-packages"
    site.mkdir()
    monkeypatch.setattr(pip_debris, "target_site_packages", lambda: site)
    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "doctor-clean-debris.db"))
    monkeypatch.setenv("EMBEDDER_MODE", "hash")
    monkeypatch.setenv(NO_LIVE_PROBE_ENV, "1")
    monkeypatch.setenv("LEVH_SUPERVISOR_LOG_DIR", str(tmp_path / "clean-logs"))

    assert cmd_doctor(argparse.Namespace(fix_debris=True)) == 0
    assert "Pip debris" in capsys.readouterr().out
def test_doctor_warns_when_auto_update_is_stuck_on_another_branch(tmp_path, monkeypatch, capsys):
    """Issue #522: the skip that used to live only in the supervisor's log.

    A checkout on a feature branch skipped every cycle for days while doctor,
    ``/api/health`` and the dashboard all reported healthy. Doctor now names the
    skip and the command that resumes updates.
    """
    from server.cli import cmd_doctor
    from server.commands.doctor import NO_LIVE_PROBE_ENV
    from server.core import update_state

    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "doctor-update.db"))
    monkeypatch.setenv("EMBEDDER_MODE", "hash")
    monkeypatch.setenv(NO_LIVE_PROBE_ENV, "1")
    log_dir = tmp_path / "suplogs"
    monkeypatch.setenv("LEVH_SUPERVISOR_LOG_DIR", str(log_dir))
    log_dir.mkdir()
    update_state.write_update_state(
        {"status": "skipped", "reason": "checkout is on 'fix/x', auto-update only follows 'main'"},
        path=log_dir / "update-state.json",
        interval_hours=6.0,
    )

    assert cmd_doctor(argparse.Namespace()) == 1
    output = capsys.readouterr().out
    assert "Auto-update" in output
    assert "WARN" in output
    assert "fix/x" in output
    assert "levh update --branch fix/x" in output


def test_doctor_passes_auto_update_when_nothing_is_stuck(tmp_path, monkeypatch, capsys):
    from server.cli import cmd_doctor
    from server.commands.doctor import NO_LIVE_PROBE_ENV
    from server.core import update_state

    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "doctor-update-ok.db"))
    monkeypatch.setenv("EMBEDDER_MODE", "hash")
    monkeypatch.setenv(NO_LIVE_PROBE_ENV, "1")
    log_dir = tmp_path / "suplogs-ok"
    monkeypatch.setenv("LEVH_SUPERVISOR_LOG_DIR", str(log_dir))
    log_dir.mkdir()
    update_state.write_update_state(
        {"status": "up_to_date"}, path=log_dir / "update-state.json", interval_hours=6.0
    )

    assert cmd_doctor(argparse.Namespace()) == 0
    output = capsys.readouterr().out
    assert "Auto-update" in output
    assert "Verdict: OK" in output


def test_doctor_does_not_warn_when_no_supervisor_ever_ran(tmp_path, monkeypatch, capsys):
    """A wheel install has no supervisor state; that is normal, not a problem."""
    from server.cli import cmd_doctor
    from server.commands.doctor import NO_LIVE_PROBE_ENV

    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "doctor-no-sup.db"))
    monkeypatch.setenv("EMBEDDER_MODE", "hash")
    monkeypatch.setenv(NO_LIVE_PROBE_ENV, "1")
    monkeypatch.setenv("LEVH_SUPERVISOR_LOG_DIR", str(tmp_path / "empty-logs"))

    assert cmd_doctor(argparse.Namespace()) == 0
    assert "Verdict: OK" in capsys.readouterr().out


def test_doctor_warns_when_env_overrides_a_conflicting_config_value(tmp_path, monkeypatch, capsys):
    """Issue #523: the precedence footgun that hid for a multi-hour forensic pass.

    ``config.json`` says ``embedder_mode: auto`` while a process env var says
    ``hash``. Doctor must name the disagreement, not just the effective value.
    """
    from server.cli import cmd_doctor
    from server.commands.doctor import NO_LIVE_PROBE_ENV

    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "doctor-precedence.db"))
    monkeypatch.setenv(NO_LIVE_PROBE_ENV, "1")
    monkeypatch.setenv("LEVH_SUPERVISOR_LOG_DIR", str(tmp_path / "prec-logs"))
    monkeypatch.chdir(tmp_path)
    cfg_dir = tmp_path / ".stackmemory"
    cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text('{"embedder_mode": "auto"}', encoding="utf-8")
    monkeypatch.setenv("EMBEDDER_MODE", "hash")

    assert cmd_doctor(argparse.Namespace()) == 1
    output = capsys.readouterr().out
    assert "Config precedence" in output
    assert "WARN" in output
    assert "EMBEDDER_MODE=hash" in output
    assert "embedder_mode=auto" in output
    # The embedder line names the layer that actually supplied the mode.
    assert "requested=hash (from environment variable EMBEDDER_MODE)" in output


def test_doctor_reports_no_precedence_conflict_when_layers_agree(tmp_path, monkeypatch, capsys):
    from server.cli import cmd_doctor
    from server.commands.doctor import NO_LIVE_PROBE_ENV

    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "doctor-precedence-ok.db"))
    monkeypatch.setenv(NO_LIVE_PROBE_ENV, "1")
    monkeypatch.setenv("LEVH_SUPERVISOR_LOG_DIR", str(tmp_path / "prec-ok-logs"))
    monkeypatch.chdir(tmp_path)
    cfg_dir = tmp_path / ".stackmemory"
    cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text('{"embedder_mode": "hash"}', encoding="utf-8")
    monkeypatch.setenv("EMBEDDER_MODE", "hash")

    assert cmd_doctor(argparse.Namespace()) == 0
    output = capsys.readouterr().out
    assert "Config precedence" in output
    assert "no env/config conflicts" in output


def test_doctor_reports_default_embedder_source_without_env_or_config(tmp_path, monkeypatch, capsys):
    from server.cli import cmd_doctor
    from server.commands.doctor import NO_LIVE_PROBE_ENV

    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "doctor-default-source.db"))
    monkeypatch.setenv(NO_LIVE_PROBE_ENV, "1")
    monkeypatch.setenv("LEVH_SUPERVISOR_LOG_DIR", str(tmp_path / "def-logs"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("EMBEDDER_MODE", raising=False)
    monkeypatch.delenv("LEVH_EMBEDDER_MODE", raising=False)
    monkeypatch.delenv("STACKMEMORY_EMBEDDER_MODE", raising=False)

    assert cmd_doctor(argparse.Namespace()) == 0
    assert "requested=auto (from default)" in capsys.readouterr().out


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


# ── Stdio embedder footprint (issue #526) ────────────────────────────


def test_looks_like_mcp_stdio_matches_every_launch_shape():
    """The matcher reads a whole command line, because ``ps`` and PowerShell
    hand back one string and re-splitting it would mangle a quoted Windows
    interpreter path (the ``paths.py`` bug). Each documented way to start the
    transport must count; an unrelated ``mcp`` word must not."""
    from server.core.process_inventory import looks_like_mcp_stdio

    positives = [
        "/usr/local/bin/python -m server.mcp_stdio",
        "/usr/bin/python3 -m server.mcp_stdio --debug",
        "levh mcp stdio",
        "/opt/levh/bin/python -m server.cli mcp stdio",
        r"C:\Python311\python.exe -m server.mcp_stdio",
        "/usr/bin/python /home/me/levh/server/mcp_stdio.py",
    ]
    negatives = [
        "python -m server.api",
        "levh serve --port 8000",
        "grep -r mcp stdio",
        "/usr/bin/python -m server.cli doctor",
        "ps -Ao command=",
    ]
    for command in positives:
        assert looks_like_mcp_stdio(command), command
    for command in negatives:
        assert not looks_like_mcp_stdio(command), command


def test_count_mcp_stdio_processes_uses_the_platform_listing(monkeypatch):
    """The count is derived from the listing, and a listing we cannot read is
    ``None`` — distinct from ``0``, so a failed look is never reported as
    "no stdio servers"."""
    from server.core import process_inventory

    listing = [
        "/usr/local/bin/python -m server.mcp_stdio",
        "levh serve --port 8000",
        "/usr/bin/python3 -m server.mcp_stdio --profile minimal",
        "node /usr/lib/code-server/out/server-main.js",
    ]
    monkeypatch.setattr(process_inventory, "_command_lines", lambda: listing)
    assert process_inventory.count_mcp_stdio_processes() == 2

    monkeypatch.setattr(process_inventory, "_command_lines", lambda: None)
    assert process_inventory.count_mcp_stdio_processes() is None


def test_linux_listing_reads_proc_cmdline(tmp_path):
    """``/proc/<pid>/cmdline`` is NUL-separated; joining on spaces rebuilds the
    command without the shell quoting ``ps`` would add. A non-pid directory and
    an empty cmdline (a kernel thread) are both skipped, not counted."""
    from server.core import process_inventory

    proc = tmp_path / "proc"
    (proc / "1234").mkdir(parents=True)
    (proc / "1234" / "cmdline").write_bytes(b"python\0-m\0server.mcp_stdio\0")
    (proc / "not-a-pid").mkdir()
    (proc / "5678").mkdir()
    (proc / "5678" / "cmdline").write_bytes(b"")

    assert process_inventory._linux_command_lines(str(proc)) == ["python -m server.mcp_stdio"]


def test_linux_listing_returns_none_when_proc_is_absent(tmp_path):
    """A missing ``/proc`` (a locked-down container) is ``None``, not an empty
    list: doctor must not report "no stdio servers" when it could not look."""
    from server.core import process_inventory

    assert process_inventory._linux_command_lines(str(tmp_path / "nope")) is None


def test_doctor_warns_when_many_stdio_servers_hold_a_local_model(tmp_path, monkeypatch, capsys):
    """Issue #526: each ``mcp stdio`` process loads its own local model.

    The warning has to come from the OS, not from ``agent_sessions``: a process
    that has not embedded yet has no session row and is the next one to pay.
    """
    from server.cli import cmd_doctor
    from server.commands.doctor import NO_LIVE_PROBE_ENV
    from server.core import process_inventory

    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "doctor-stdio.db"))
    monkeypatch.setenv("EMBEDDER_MODE", "local")
    monkeypatch.setenv(NO_LIVE_PROBE_ENV, "1")
    monkeypatch.setenv("LEVH_SUPERVISOR_LOG_DIR", str(tmp_path / "stdio-logs"))
    # A local model resolves to ``local`` only when sentence-transformers is
    # importable; force the provider so the check is exercised regardless.
    monkeypatch.setattr(process_inventory, "count_mcp_stdio_processes", lambda: 5)
    from server.core.embedder import Embedder

    monkeypatch.setattr(
        Embedder, "identity", lambda self: {"provider": "local", "model": "all-MiniLM-L6-v2"}
    )

    assert cmd_doctor(argparse.Namespace()) == 1
    output = capsys.readouterr().out
    assert "Stdio embedder" in output
    assert "5 `mcp stdio` process(es)" in output
    assert "#526" in output


def test_doctor_passes_stdio_check_under_a_model_free_mode(tmp_path, monkeypatch, capsys):
    """Hash and remote providers hold no per-process model, so the count is
    informational: doctor must not fail a machine that is configured safely."""
    from server.cli import cmd_doctor
    from server.commands.doctor import NO_LIVE_PROBE_ENV
    from server.core import process_inventory

    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "doctor-stdio-hash.db"))
    monkeypatch.setenv("EMBEDDER_MODE", "hash")
    monkeypatch.setenv(NO_LIVE_PROBE_ENV, "1")
    monkeypatch.setenv("LEVH_SUPERVISOR_LOG_DIR", str(tmp_path / "stdio-hash-logs"))
    monkeypatch.setattr(process_inventory, "count_mcp_stdio_processes", lambda: 9)

    assert cmd_doctor(argparse.Namespace()) == 0
    output = capsys.readouterr().out
    assert "Stdio embedder" in output
    assert "holds no per-process model" in output


def test_doctor_passes_stdio_check_when_local_but_no_sessions_run(tmp_path, monkeypatch, capsys):
    """A local store with no stdio server running holds no per-process model, so
    the check passes and must not claim a cost that is not being paid."""
    from server.cli import cmd_doctor
    from server.commands.doctor import NO_LIVE_PROBE_ENV
    from server.core import process_inventory

    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "doctor-stdio-zero.db"))
    monkeypatch.setenv("EMBEDDER_MODE", "local")
    monkeypatch.setenv(NO_LIVE_PROBE_ENV, "1")
    monkeypatch.setenv("LEVH_SUPERVISOR_LOG_DIR", str(tmp_path / "stdio-zero-logs"))
    monkeypatch.setattr(process_inventory, "count_mcp_stdio_processes", lambda: 0)
    from server.core.embedder import Embedder

    monkeypatch.setattr(
        Embedder, "identity", lambda self: {"provider": "local", "model": "all-MiniLM-L6-v2"}
    )

    assert cmd_doctor(argparse.Namespace()) == 0
    output = capsys.readouterr().out
    assert "Stdio embedder" in output
    assert "no `mcp stdio` processes running" in output


def test_doctor_stdio_threshold_override_is_read_and_clamped(tmp_path, monkeypatch):
    """The threshold is a knob, and a malformed value falls back to the default
    rather than failing the check."""
    from server.commands.doctor import _stdio_warn_threshold
    from server.core.process_inventory import (
        DEFAULT_STDIO_WARN_THRESHOLD,
        STDIO_WARN_THRESHOLD_ENV,
    )

    monkeypatch.delenv(STDIO_WARN_THRESHOLD_ENV, raising=False)
    assert _stdio_warn_threshold() == DEFAULT_STDIO_WARN_THRESHOLD
    monkeypatch.setenv(STDIO_WARN_THRESHOLD_ENV, "7")
    assert _stdio_warn_threshold() == 7
    monkeypatch.setenv(STDIO_WARN_THRESHOLD_ENV, "not-a-number")
    assert _stdio_warn_threshold() == DEFAULT_STDIO_WARN_THRESHOLD
    monkeypatch.setenv(STDIO_WARN_THRESHOLD_ENV, "0")
    assert _stdio_warn_threshold() == DEFAULT_STDIO_WARN_THRESHOLD

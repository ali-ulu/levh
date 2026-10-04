"""The tenancy boundary: every storage access is scoped to one workspace (#302).

Phase 1 of ``docs/internal/SHARED-MEMORY-DESIGN.md``. A single-user install has
one implicit workspace (``default``) and one implicit principal (``local``), so
these tests pin two things at once: the single-user round trip is byte-for-byte
unchanged, and a request bound to another workspace cannot read, count, update
or delete a row that belongs to a different one.

Offline and deterministic — hash embedder, no model, no network.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

os.environ["EMBEDDER_MODE"] = "hash"

from server.core.database import CURRENT_SCHEMA_VERSION, Database
from server.core.memory_engine import MemoryEngine
from server.core.tenancy import (
    DEFAULT_WORKSPACE_ID,
    AuthorizationError,
    Principal,
    bind_principal,
    reset_principal,
)

OTHER = "team-42"


class _Workspace:
    """Bind a principal for the duration of a ``with`` block."""

    def __init__(
        self,
        workspace_id: str,
        *,
        role: str = "admin",
        principal_id: str = "local",
    ) -> None:
        self.workspace_id = workspace_id
        self.role = role
        self.principal_id = principal_id
        self._token = None

    def __enter__(self):
        self._token = bind_principal(
            Principal(
                id=self.principal_id,
                workspace_id=self.workspace_id,
                role=self.role,
            )
        )
        return self

    def __exit__(self, *exc):
        reset_principal(self._token)
        return False


def workspace(
    workspace_id: str,
    *,
    role: str = "admin",
    principal_id: str = "local",
) -> _Workspace:
    return _Workspace(workspace_id, role=role, principal_id=principal_id)


@pytest_asyncio.fixture
async def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = Database(path)
    await store.connect()
    yield store
    await store.close()
    os.unlink(path)


@pytest_asyncio.fixture
async def engine():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    eng = MemoryEngine(db_path=path, embedder_mode="hash", short_term_max=50)
    await eng.initialize()
    yield eng
    await eng.shutdown()
    if os.path.exists(path):
        os.unlink(path)


def _row(memory_id: str, content: str) -> dict:
    from server.core.types import Memory

    return Memory(
        id=memory_id,
        content=content,
        memory_type="episodic",
        created_at="2026-01-01T00:00:00+00:00",
        accessed_at="2026-01-01T00:00:00+00:00",
    ).model_dump()


# ── The single-user case is unchanged ───────────────────────────────


def test_a_new_store_is_version_nine(db):
    assert db.schema_version == CURRENT_SCHEMA_VERSION == 9


def test_the_model_defaults_to_the_one_implicit_workspace():
    from server.core.types import Memory

    assert Memory(content="x").workspace_id == DEFAULT_WORKSPACE_ID


@pytest.mark.asyncio
async def test_default_store_and_read_round_trips(db):
    await db.insert_memory(_row("m1", "the deploy branch is prod"))
    assert await db.get_memory("m1") is not None
    assert await db.count_memories() == 1
    assert await db.content_exists("the deploy branch is prod")


# ── Phase 2 role enforcement ────────────────────────────────────────


@pytest.mark.asyncio
async def test_viewer_can_read_but_cannot_mutate_memory(db):
    await db.insert_memory(_row("m1", "shared team memory"))

    with workspace(DEFAULT_WORKSPACE_ID, role="viewer", principal_id="reader"):
        assert await db.get_memory("m1") is not None
        with pytest.raises(AuthorizationError):
            await db.insert_memory(_row("m2", "viewer must not write"))
        with pytest.raises(AuthorizationError):
            await db.update_memory("m1", {"importance": 0.9})
        with pytest.raises(AuthorizationError):
            await db.delete_memory("m1")
        with pytest.raises(AuthorizationError):
            await db.get_all_memories(across_workspaces=True)


@pytest.mark.asyncio
async def test_editor_can_mutate_but_cannot_run_whole_store_backup(db, tmp_path):
    await db.insert_memory(_row("m1", "editor-visible memory"))

    with workspace(DEFAULT_WORKSPACE_ID, role="editor", principal_id="writer"):
        assert await db.update_memory("m1", {"importance": 0.9}) is True
        with pytest.raises(AuthorizationError):
            await db.create_safety_backup(str(tmp_path / "backup.db"))


@pytest.mark.asyncio
async def test_full_export_and_portable_backup_require_admin(engine):
    mem = await engine.store("admin-only export payload")

    with workspace(DEFAULT_WORKSPACE_ID, role="viewer", principal_id="reader"):
        assert await engine.get_memory(mem.id) is not None
        with pytest.raises(AuthorizationError):
            await engine.export_memories()

    with workspace(DEFAULT_WORKSPACE_ID, role="editor", principal_id="writer"):
        with pytest.raises(AuthorizationError):
            await engine.backup()

    with workspace(DEFAULT_WORKSPACE_ID, role="admin", principal_id="owner"):
        exported = await engine.export_memories()
        assert any(row["id"] == mem.id for row in exported)
        snapshot = await engine.backup()
        assert snapshot["counts"]["memories"] >= 1


@pytest.mark.asyncio
async def test_authorization_error_maps_to_non_leaky_http_403():
    from server.api import app

    handler = app.exception_handlers[AuthorizationError]
    response = await handler(None, AuthorizationError("principal secret must not leak"))
    assert response.status_code == 403
    assert response.body == b'{"detail":"forbidden"}'


@pytest.mark.asyncio
async def test_unknown_role_fails_closed(db):
    with workspace(DEFAULT_WORKSPACE_ID, role="owner", principal_id="mystery"):
        with pytest.raises(AuthorizationError, match="unknown workspace role"):
            await db.get_memory("missing")


# ── The boundary actually separates workspaces ──────────────────────


@pytest.mark.asyncio
async def test_a_row_is_invisible_outside_its_workspace(db):
    with workspace(OTHER):
        await db.insert_memory(_row("m1", "only the team may read this"))

    assert await db.get_memory("m1") is None
    assert await db.count_memories() == 0
    assert await db.get_all_memories() == []
    assert await db.search_memories() == []
    assert not await db.content_exists("only the team may read this")

    with workspace(OTHER):
        assert await db.get_memory("m1") is not None
        assert await db.count_memories() == 1


@pytest.mark.asyncio
async def test_full_text_candidates_respect_the_boundary(db):
    with workspace(OTHER):
        await db.insert_memory(_row("m1", "zephyr is the deployment codename"))

    assert await db.search_memory_ids_fts("zephyr") == []
    with workspace(OTHER):
        assert await db.search_memory_ids_fts("zephyr") == ["m1"]


@pytest.mark.asyncio
async def test_update_and_delete_do_not_reach_across_the_boundary(db):
    with workspace(OTHER):
        await db.insert_memory(_row("m1", "team only"))

    assert await db.update_memory("m1", {"importance": 0.9}) is False
    assert await db.delete_memory("m1") is False
    assert await db.delete_memory_cascade("m1") is False

    with workspace(OTHER):
        row = await db.get_memory("m1")
        assert row is not None
        assert row["importance"] == 0.5


@pytest.mark.asyncio
async def test_aggregates_are_per_workspace(db):
    await db.insert_memory(_row("a", "default workspace memory"))
    with workspace(OTHER):
        await db.insert_memory(_row("b", "team memory"))

    assert await db.memory_aggregates() == {
        "count": 1,
        "avg_importance": 0.5,
        "avg_hscore": 0.0,
    }
    assert len(await db.list_tags()) == 0


# ── The in-process mirror is filtered at recall ─────────────────────


@pytest.mark.asyncio
async def test_recall_does_not_leak_across_workspaces(engine):
    with workspace(OTHER):
        await engine.store("the zephyr rollout is scheduled for friday")
    await engine.store("the office coffee machine is on the third floor")

    results = await engine.recall("when is the zephyr rollout", top_k=5)
    contents = [m.content for m in results.memories]
    assert "the zephyr rollout is scheduled for friday" not in contents

    with workspace(OTHER):
        results = await engine.recall("when is the zephyr rollout", top_k=5)
        contents = [m.content for m in results.memories]
        assert "the zephyr rollout is scheduled for friday" in contents


@pytest.mark.asyncio
async def test_viewer_recall_is_read_only_instead_of_failing(engine):
    mem = await engine.store("the release train leaves on friday")

    with workspace(DEFAULT_WORKSPACE_ID, role="viewer", principal_id="reader"):
        result = await engine.recall("when does the release train leave", top_k=3)
        assert any(item.id == mem.id for item in result.memories)
        stored = await engine.db.get_memory(mem.id)
        assert stored is not None
        assert stored["recall_count"] == 0


# ── A pre-tenancy store is migrated, never dropped ──────────────────


@pytest.mark.asyncio
async def test_a_pre_tenancy_store_gains_the_column_and_keeps_its_rows(tmp_path):
    path = str(tmp_path / "legacy.db")
    # Build a v3 store by hand: the memories table without workspace_id, one
    # row in it, and the schema version the old build left behind.
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE memories (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            memory_type TEXT NOT NULL DEFAULT 'short_term',
            embedding TEXT,
            importance REAL NOT NULL DEFAULT 0.5,
            frequency INTEGER NOT NULL DEFAULT 1,
            tags TEXT NOT NULL DEFAULT '[]',
            session_id TEXT,
            project TEXT,
            source TEXT,
            pinned INTEGER DEFAULT 0,
            metadata TEXT NOT NULL DEFAULT '{}',
            hscore REAL,
            created_at TEXT NOT NULL,
            accessed_at TEXT NOT NULL,
            decay_factor REAL DEFAULT 1.0,
            stability_hours REAL DEFAULT 168.0,
            recall_count INTEGER DEFAULT 0,
            valid_from TEXT,
            valid_to TEXT,
            superseded_by TEXT
        );
        INSERT INTO memories (id, content, memory_type, importance, frequency,
                              tags, metadata, created_at, accessed_at)
        VALUES ('legacy-1', 'written before tenancy existed', 'episodic', 0.5, 1,
                '[]', '{}', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00');
        PRAGMA user_version = 3;
        """
    )
    conn.commit()
    conn.close()

    store = Database(path)
    await store.connect()
    try:
        assert store.schema_version == CURRENT_SCHEMA_VERSION
        row = await store.get_memory("legacy-1")
        assert row is not None, "the migration dropped a pre-existing memory"
        assert row["workspace_id"] == DEFAULT_WORKSPACE_ID
        assert row["valid_from"] == "2026-01-01T00:00:00+00:00"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_the_workspace_backfill_is_idempotent(tmp_path):
    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE memories (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            memory_type TEXT NOT NULL DEFAULT 'short_term',
            embedding TEXT,
            importance REAL NOT NULL DEFAULT 0.5,
            frequency INTEGER NOT NULL DEFAULT 1,
            tags TEXT NOT NULL DEFAULT '[]',
            session_id TEXT,
            project TEXT,
            source TEXT,
            pinned INTEGER DEFAULT 0,
            metadata TEXT NOT NULL DEFAULT '{}',
            hscore REAL,
            created_at TEXT NOT NULL,
            accessed_at TEXT NOT NULL,
            decay_factor REAL DEFAULT 1.0,
            stability_hours REAL DEFAULT 168.0,
            recall_count INTEGER DEFAULT 0,
            valid_from TEXT,
            valid_to TEXT,
            superseded_by TEXT
        );
        INSERT INTO memories (id, content, created_at, accessed_at)
        VALUES ('legacy-1', 'a memory', '2026-01-01T00:00:00+00:00',
                '2026-01-01T00:00:00+00:00');
        PRAGMA user_version = 3;
        """
    )
    conn.commit()
    conn.close()

    for _ in range(2):
        store = Database(path)
        await store.connect()
        await store.close()

    conn = sqlite3.connect(path)
    try:
        value = conn.execute(
            "SELECT workspace_id FROM memories WHERE id = 'legacy-1'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert value == DEFAULT_WORKSPACE_ID


def test_workspace_is_not_caller_supplied(tmp_path):
    """The store stamps the boundary; a payload cannot choose another one."""
    path = Path(tempfile.mkdtemp()) / "store.db"

    async def _run() -> None:
        db = Database(str(path))
        await db.connect()
        try:
            with workspace(OTHER):
                payload = _row("m1", "team memory")
                payload["workspace_id"] = DEFAULT_WORKSPACE_ID
                await db.insert_memory(payload)
            # The context's workspace wins over the payload's field.
            assert await db.get_memory("m1") is None
            with workspace(OTHER):
                assert await db.get_memory("m1") is not None
        finally:
            await db.close()

    import asyncio

    asyncio.run(_run())


# ── Maintenance passes span workspaces and still write the right row ─


@pytest.mark.asyncio
async def test_reembed_updates_a_memory_in_another_workspace(engine):
    """The scan is store-wide, so the write must target each row's workspace."""
    with workspace(OTHER):
        mem = await engine.store("the build cache lives in /var/tmp")
        # Force staleness so the maintenance pass selects it.
        stale = mem.model_dump()
        stale["metadata"] = {"embedding_provenance": {"provider": "gone"}}
        await engine.db.update_memory(mem.id, stale)

    summary = await engine.reembed_memories()
    assert summary["stale"] == 1

    with workspace(OTHER):
        row = await engine.episodic.get(mem.id)
    assert row is not None
    assert row.metadata["embedding_provenance"] != {"provider": "gone"}


@pytest.mark.asyncio
async def test_demo_purge_removes_a_memory_in_another_workspace(engine):
    from server.core.onboarding import remove_demo_data

    with workspace(OTHER):
        mem = await engine.store("seeded demo memory", metadata={"demo": True})

    result = await remove_demo_data(engine)
    assert result["removed"] == 1

    with workspace(OTHER):
        assert await engine.episodic.get(mem.id) is None


@pytest.mark.asyncio
async def test_replace_restore_refuses_once_two_workspaces_exist(engine):
    """A whole-store replace cannot be scoped, so it fails loudly (#302)."""
    await engine.store("default workspace memory")
    with workspace(OTHER):
        await engine.store("team workspace memory")

    snapshot = await engine.backup()
    with pytest.raises(ValueError, match="more than one workspace"):
        await engine.restore(snapshot, replace=True)

    # Fail-closed: nothing was deleted.
    assert await engine.episodic.count() == 1
    with workspace(OTHER):
        assert await engine.episodic.count() == 1


@pytest.mark.asyncio
async def test_delete_does_not_clear_a_peer_workspaces_supersession_pointer(db):
    """Supersession is a within-workspace relation (#302).

    A row in another workspace that names this id — an import that preserved
    the pointer, say — must keep it when the id is deleted here.
    """
    await db.insert_memory(_row("m1", "the default workspace fact"))
    with workspace(OTHER):
        peer = _row("peer", "a team fact that mentions m1")
        peer["superseded_by"] = "m1"
        peer["metadata"] = {"superseded_by": "m1"}
        await db.insert_memory(peer)

    assert await db.delete_memory_cascade("m1") is True

    with workspace(OTHER):
        row = await db.get_memory("peer")
    assert row["superseded_by"] == "m1"
    assert row["metadata"]["superseded_by"] == "m1"

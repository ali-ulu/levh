r"""The store itself refuses a memory row it cannot read back.

Two real incidents on one machine: an outside agent wrote directly into
``%LOCALAPPDATA%\stackmemory.db`` and left ``id`` NULL, and another left
``memory_type='long_term'``. SQLite does not imply NOT NULL from PRIMARY KEY on
a non-INTEGER key, so both writes succeeded; the rows were then unreachable,
and the read path had to quarantine them (PR #267) and report the loss (PR
#270). This is the guard at the point of writing: the same INSERT now fails and
says which column the model needs.

The rules live in one table (``server.core.db.schema._MEMORY_ROW_RULES``) and
the triggers are generated from it, so a new rule is one entry — not a second
piece of SQL to keep in sync.
"""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
from pathlib import Path

import pytest

from server.core.database import Database
from server.core.db.schema import _MEMORY_ROW_RULES

_INSERT = (
    "INSERT INTO memories (id, content, memory_type, importance, frequency, "
    "created_at, accessed_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
)

_OK = ("mem-1", "a memory that must be stored", "episodic", 0.5, 1, "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00")

#: (label, rule the message must name, row) — one case per rule in the table.
REJECTED_ROWS = [
    ("id missing", "memories.id is required", (None,) + _OK[1:]),
    ("id blank", "memories.id is required", ("   ",) + _OK[1:]),
    ("content empty", "memories.content is required", ("mem-1", "", "episodic", 0.5, 1) + _OK[5:]),
    ("enum outside the model", "memory_type must be short_term or episodic", ("mem-1", "text", "long_term") + _OK[3:]),
    ("importance above the bound", "importance must be between", ("mem-1", "text", "episodic", 5.0) + _OK[4:]),
    ("frequency below the bound", "frequency counts recalls", ("mem-1", "text", "episodic", 0.5, 0) + _OK[5:]),
    ("created_at missing", "created_at and memories.accessed_at are required", ("mem-1", "text", "episodic", 0.5, 1, None, "2026-01-01T00:00:00+00:00")),
]


def _new_store() -> str:
    """A store levh created, which is where the guards get installed."""
    path = str(Path(tempfile.mkdtemp()) / "store.db")

    async def _create() -> None:
        db = Database(path)
        await db.connect()
        await db.close()

    asyncio.run(_create())
    return path


def _insert(path: str, row: tuple) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(_INSERT, row)


@pytest.mark.parametrize(("label", "expected", "row"), REJECTED_ROWS)
def test_the_store_refuses_a_row_the_model_cannot_read(label, expected, row):
    path = _new_store()
    with pytest.raises(sqlite3.IntegrityError) as caught:
        _insert(path, row)
    assert expected in str(caught.value), label


@pytest.mark.parametrize(("label", "expected", "row"), REJECTED_ROWS)
def test_a_refused_write_leaves_the_store_untouched(label, expected, row):
    """The guard rejects the statement, not the transaction's other rows."""
    path = _new_store()
    _insert(path, _OK)
    with pytest.raises(sqlite3.IntegrityError):
        _insert(path, row)
    _insert(path, ("mem-2", "another memory") + _OK[2:])
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 2


def test_updates_are_guarded_too():
    """A later UPDATE cannot reintroduce what an INSERT may not write."""
    path = _new_store()
    _insert(path, _OK)
    with sqlite3.connect(path) as conn:
        for sql in (
            "UPDATE memories SET id = NULL",
            "UPDATE memories SET memory_type = 'long_term'",
            "UPDATE memories SET importance = 2.0",
            "UPDATE memories SET frequency = 0",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(sql)
        # The row is still exactly what it was.
        assert conn.execute("SELECT id, memory_type, importance, frequency FROM memories").fetchone() == (
            "mem-1",
            "episodic",
            _OK[3],
            1,
        )


def test_the_guards_cover_every_rule_in_the_table():
    """No rule may exist without the two triggers it generates."""
    path = _new_store()
    with sqlite3.connect(path) as conn:
        installed = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                "AND name LIKE 'memories_integrity%'"
            )
        }
    expected = {
        f"memories_integrity_{rule}_{suffix}"
        for rule, _columns, _condition, _message in _MEMORY_ROW_RULES
        for suffix in ("ai", "au")
    }
    assert installed == expected


def test_a_store_written_before_the_guards_gains_them_on_connect():
    """Protection reaches databases that already exist, not just new ones."""
    path = _new_store()
    with sqlite3.connect(path) as conn:
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' "
            "AND name LIKE 'memories_integrity%'"
        ):
            conn.execute("DROP TRIGGER " + row[0])
    _insert(path, _OK)  # the same write the guards refuse, before they exist

    async def _reopen() -> int:
        db = Database(path)
        await db.connect()
        try:
            return db.schema_version
        finally:
            await db.close()

    assert asyncio.run(_reopen()) >= 1
    with pytest.raises(sqlite3.IntegrityError):
        _insert(path, ("mem-2", "x", "long_term") + _OK[3:])


def test_the_write_path_levh_itself_uses_is_unaffected():
    """The guards must not be a tax on legitimate writes or updates."""
    from server.core.memory_engine import MemoryEngine

    path = str(Path(tempfile.mkdtemp()) / "store.db")

    async def _round_trip():
        engine = MemoryEngine(db_path=path, embedder_mode="hash")
        try:
            await engine.initialize()
            stored = await engine.store("a memory levh writes itself")
            fetched = await engine.get_memory(stored.id)
            stored.importance = 0.9
            assert await engine.episodic.update(stored) is True
            return fetched
        finally:
            await engine.shutdown()

    fetched = asyncio.run(_round_trip())
    assert fetched is not None
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1

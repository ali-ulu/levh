"""A store row the model rejects must not take startup down (regression).

An external writer put ``memory_type="long_term"`` straight into the SQLite
file. Loading it raised out of ``MemoryEngine.initialize()``, so the API never
came up and every other memory became unreachable. Invalid rows are now
quarantined: skipped, and named in a warning.
"""

from __future__ import annotations

import logging
import sqlite3

import pytest

from server.core.memory_engine import MemoryEngine

_INSERT_SQL = (
    "INSERT INTO memories (id, content, memory_type, importance, frequency, tags, "
    "pinned, metadata, created_at, accessed_at) VALUES (?, ?, ?, ?, ?, ?, 0, '{}', "
    "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
)

_GOOD_CONTENT = "a memory that must stay reachable"


def _drop_integrity_triggers(db_path):
    """Make the store look like one written before the integrity guards.

    The guards (server/core/db/schema.py) refuse these rows on the way in, so a
    test that needs a *pre-existing* bad row has to take them off first. That
    is not a workaround: damage written before the guards existed, or by a tool
    that dropped them, is exactly the case the read path still has to survive.
    """
    with sqlite3.connect(db_path) as conn:
        names = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                "AND name LIKE 'memories_integrity%'"
            )
        ]
        for name in names:
            conn.execute("DROP TRIGGER " + name)


def _insert_invalid_row(db_path, row_id, **overrides):
    """Write a row the way an external tool would: straight through SQLite."""
    _drop_integrity_triggers(db_path)
    values = {
        "content": "written by an external tool",
        "memory_type": "episodic",
        "importance": 0.5,
        "frequency": 1,
        "tags": "[]",
    }
    values.update(overrides)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            _INSERT_SQL,
            (
                row_id,
                values["content"],
                values["memory_type"],
                values["importance"],
                values["frequency"],
                values["tags"],
            ),
        )


async def _store_with_one_good_memory(db_path):
    """Create the store the way the app does, then close it again."""
    engine = MemoryEngine(db_path=str(db_path), embedder_mode="hash")
    await engine.initialize()
    await engine.store(_GOOD_CONTENT)
    await engine.shutdown()


@pytest.mark.parametrize(
    ("label", "overrides"),
    [
        ("memory_type outside the enum", {"memory_type": "long_term"}),
        ("importance above the model bound", {"importance": 5.0}),
        ("frequency below the model bound", {"frequency": 0}),
    ],
)
@pytest.mark.asyncio
async def test_invalid_row_is_quarantined_and_startup_survives(
    tmp_path, caplog, label, overrides
):
    db_path = tmp_path / "quarantine.db"
    await _store_with_one_good_memory(db_path)
    _insert_invalid_row(db_path, "bad-row", **overrides)

    engine = MemoryEngine(db_path=str(db_path), embedder_mode="hash")
    try:
        with caplog.at_level(logging.WARNING, logger="server.core.episodic"):
            await engine.initialize()  # used to raise ValidationError
            listed = await engine.episodic.get_all()
            searched = await engine.episodic.search(limit=50)
            fetched = await engine.episodic.get("bad-row")
    finally:
        await engine.shutdown()

    assert [memory.content for memory in listed] == [_GOOD_CONTENT], label
    assert [memory.content for memory in searched] == [_GOOD_CONTENT], label
    assert fetched is None, label

    warnings = [
        record.getMessage()
        for record in caplog.records
        if "quarantined invalid memory row" in record.getMessage()
    ]
    assert warnings, label
    assert "bad-row" in warnings[0], label

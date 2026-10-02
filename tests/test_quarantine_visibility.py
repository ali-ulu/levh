"""Quarantined rows must be observable, not just skipped in a log.

PR #267 made an invalid store row non-fatal: it is skipped with a warning.
That skip was otherwise invisible, so this pass surfaces it in the two places
operators already look: the Prometheus registry (``/api/metrics``) and
``levh doctor``.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3

import pytest

from server.core import metrics
from server.core.memory_engine import MemoryEngine

_INSERT_SQL = (
    "INSERT INTO memories (id, content, memory_type, importance, frequency, tags, "
    "pinned, metadata, created_at, accessed_at) VALUES (?, ?, ?, ?, ?, ?, 0, '{}', "
    "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
)


@pytest.fixture(autouse=True)
def _clean_registry():
    metrics.reset()
    yield
    metrics.reset()


async def _store_with_one_good_memory(db_path):
    engine = MemoryEngine(db_path=str(db_path), embedder_mode="hash")
    try:
        await engine.initialize()
        await engine.store("a memory that must stay reachable")
    finally:
        await engine.shutdown()


def _insert_invalid_row(db_path, row_id):
    _drop_integrity_triggers(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            _INSERT_SQL,
            (row_id, "written by an external tool", "episodic", 5.0, 1, "[]"),
        )


def _count_quarantined(db_path):
    """The store-side check doctor uses: rows Memory(**dict) rejects."""
    from server.commands.doctor import _quarantined_rowids

    return len(_quarantined_rowids(str(db_path)))


async def _read_path_quarantined_ids(db_path, caplog):
    """The rows the real read path actually skips, by id."""
    engine = MemoryEngine(db_path=str(db_path), embedder_mode="hash")
    try:
        with caplog.at_level(logging.WARNING, logger="server.core.episodic"):
            await engine.initialize()
            rows = await engine.episodic.get_all()
    finally:
        await engine.shutdown()
    ids = set()
    for record in caplog.records:
        message = record.getMessage()
        if message.startswith("quarantined invalid memory row"):
            ids.add(message.split("id=", 1)[1].split(":", 1)[0])
    return ids, rows


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


def _insert_raw_row(db_path, row_id, **columns):
    """Insert a row shaped the way a corrupting writer might leave it."""
    values = {
        "id": row_id,
        "content": "written by an external tool",
        "memory_type": "episodic",
        "importance": 0.5,
        "frequency": 1,
        "tags": "[]",
        "metadata": "{}",
        "created_at": "2026-01-01T00:00:00+00:00",
        "accessed_at": "2026-01-01T00:00:00+00:00",
    }
    values.update(columns)
    _drop_integrity_triggers(db_path)
    columns_sql = ", ".join(values)
    placeholders = ", ".join("?" for _ in values)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"INSERT INTO memories ({columns_sql}) VALUES ({placeholders})",
            tuple(values.values()),
        )


# -- /api/metrics surface -------------------------------------------------


@pytest.mark.asyncio
async def test_quarantine_increments_the_prometheus_counter(tmp_path):
    db_path = tmp_path / "store.db"
    await _store_with_one_good_memory(db_path)
    _insert_invalid_row(db_path, "bad-1")

    engine = MemoryEngine(db_path=str(db_path), embedder_mode="hash")
    try:
        await engine.initialize()
        listed = await engine.episodic.get_all()
    finally:
        await engine.shutdown()

    # initialize() scans the store and quarantines the row once; get_all()
    # hits it again. The counter counts quarantine events, not distinct rows.
    exposition = metrics.render()
    assert "levh_memory_rows_quarantined_total 2" in exposition, exposition
    assert listed and listed[0].content == "a memory that must stay reachable"


@pytest.mark.asyncio
async def test_metrics_endpoint_serves_the_quarantine_counter(tmp_path):
    from fastapi.testclient import TestClient

    db_path = tmp_path / "store.db"
    await _store_with_one_good_memory(db_path)
    _insert_invalid_row(db_path, "bad-1")

    engine = MemoryEngine(db_path=str(db_path), embedder_mode="hash")
    try:
        await engine.initialize()
        await engine.episodic.get_all()
    finally:
        await engine.shutdown()

    from server.api import app

    client = TestClient(app, client=("127.0.0.1", 51234))
    response = client.get("/api/metrics")
    assert response.status_code == 200
    assert "levh_memory_rows_quarantined_total 2" in response.text
    assert "# HELP levh_memory_rows_quarantined_total" in response.text


# -- levh doctor surface --------------------------------------------------


def _doctor_output(db_path, args=None):
    import os

    from server.cli import cmd_doctor

    os.environ["SQLITE_DB_PATH"] = str(db_path)
    os.environ["EMBEDDER_MODE"] = "hash"
    import io as _io
    import contextlib

    buf = _io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cmd_doctor(args or argparse.Namespace())
    return code, buf.getvalue()


@pytest.mark.asyncio
async def test_doctor_reports_quarantined_rows_as_failure(tmp_path):
    db_path = tmp_path / "store.db"
    await _store_with_one_good_memory(db_path)
    _insert_invalid_row(db_path, "bad-1")

    code, out = _doctor_output(db_path)
    assert "Quarantined rows" in out
    assert "WARN" in out
    assert "1 row(s) this build rejects" in out
    assert code == 1  # data loss must not pass silently


@pytest.mark.asyncio
async def test_doctor_stays_green_on_a_clean_store(tmp_path):
    db_path = tmp_path / "store.db"
    await _store_with_one_good_memory(db_path)

    code, out = _doctor_output(db_path)
    assert "Quarantined rows" not in out
    assert "Memory store" in out
    assert code == 0


@pytest.mark.asyncio
async def test_doctor_count_matches_the_read_path(tmp_path, caplog):
    """The count must equal the rows the read path really skips.

    Checking raw columns instead of decoded ones counted every NULL
    ``metadata``/``tags`` row as broken: on the author's store 2 unreachable
    rows reported as 18. Shapes that mean "empty", not "invalid", must not be
    counted.
    """
    db_path = tmp_path / "store.db"
    await _store_with_one_good_memory(db_path)

    # Stored as NULL: sparse but valid — the query path decodes these to
    # ``{}`` / ``[]`` before the model sees them.
    _insert_raw_row(db_path, "sparse-1", tags=None, metadata=None, embedding=None)
    # Genuinely rejected: an enum no build accepts, and a missing id.
    _insert_raw_row(db_path, "bad-enum", memory_type="long_term")
    _insert_raw_row(db_path, None)

    doctor_count = _count_quarantined(db_path)
    read_ids, listed = await _read_path_quarantined_ids(db_path, caplog)

    assert doctor_count == len(read_ids), (
        f"doctor counts {doctor_count} rows; the read path skips {len(read_ids)}: {read_ids}"
    )
    assert doctor_count == 2
    assert "sparse-1" not in read_ids
    assert "sparse-1" in {memory.id for memory in listed}

# -- the row is addressable, and repairable ---------------------------------
#
# Counting quarantined rows made the loss visible but left it a dead end: the
# row that most needs naming is the one with no id to name it by, and every
# deletion path in the product takes an id. What follows makes a row whose only
# defect is a missing id get that id back, so the memory becomes recallable
# again instead of merely reported.


@pytest.mark.asyncio
async def test_the_query_layer_cannot_write_an_unreachable_row(tmp_path):
    """`insert_memory` binds a dict, so the row itself has to refuse it.

    The suspicion behind issue #324 was that this binding was the loophole that
    left a NULL-id row on a real machine. It is not: ``memories_integrity_id_ai``
    binds every writer, including this one, and the assertion below is the
    evidence rather than the assumption.
    """
    db_path = tmp_path / "store.db"
    await _store_with_one_good_memory(db_path)

    engine = MemoryEngine(db_path=str(db_path), embedder_mode="hash")
    try:
        await engine.initialize()
        with pytest.raises(sqlite3.IntegrityError, match="memories.id is required"):
            await engine.episodic.db.insert_memory(
                {
                    "id": None,
                    "content": "written without an id",
                    "memory_type": "episodic",
                    "embedding": None,
                    "importance": 0.5,
                    "frequency": 1,
                    "tags": [],
                    "session_id": None,
                    "project": None,
                    "source": None,
                    "pinned": 0,
                    "metadata": {},
                    "hscore": None,
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "accessed_at": "2026-01-01T00:00:00+00:00",
                    "decay_factor": 1.0,
                    "stability_hours": 168.0,
                    "recall_count": 0,
                }
            )
        listed = await engine.episodic.get_all()
    finally:
        await engine.shutdown()

    assert len(listed) == 1, "the refused row must not have been written"


@pytest.mark.asyncio
async def test_doctor_names_the_quarantined_rows_by_rowid(tmp_path):
    """A count says something is wrong; a rowid says where to look in the file."""
    db_path = tmp_path / "store.db"
    await _store_with_one_good_memory(db_path)
    _insert_invalid_row(db_path, "bad-1")

    _code, out = _doctor_output(db_path)
    assert "(rowid 2)" in out, out


@pytest.mark.asyncio
async def test_doctor_offers_the_repair_it_can_perform(tmp_path):
    """The warning has to carry its own way out.

    A row that only lacks an id is the one quarantine an operator can clear with
    one command, and a fix nobody can discover from the message is a fix that
    will not happen while the memory stays unreachable.
    """
    db_path = tmp_path / "store.db"
    await _store_with_one_good_memory(db_path)
    _insert_raw_row(db_path, None)

    _code, out = _doctor_output(db_path)
    assert "1 without an id" in out, out
    assert "levh doctor --fix-ids" in out, out


@pytest.mark.asyncio
async def test_fix_ids_makes_an_idless_memory_recallable(tmp_path):
    """The repair writes an id and nothing else, and the memory comes back."""
    db_path = tmp_path / "store.db"
    await _store_with_one_good_memory(db_path)
    _insert_raw_row(db_path, None)

    code, out = _doctor_output(db_path, argparse.Namespace(fix_ids=True))
    assert "Repaired ids" in out, out
    assert code == 0, out

    with sqlite3.connect(db_path) as conn:
        recovered = conn.execute(
            "SELECT id FROM memories WHERE content = 'written by an external tool'"
        ).fetchone()
        orphaned = conn.execute(
            "SELECT COUNT(*) FROM memories_fts WHERE memory_id IS NULL"
        ).fetchone()[0]
        indexed = conn.execute(
            "SELECT COUNT(*) FROM memories_fts WHERE memory_id = ?", (recovered[0],)
        ).fetchone()[0]

    assert recovered[0] and len(recovered[0]) == 32, "a generated id, not a placeholder"
    assert orphaned == 0, "the FTS entry keyed on the NULL must not outlive the repair"
    assert indexed == 1, "the recovered memory has to be findable by text as well"

    engine = MemoryEngine(db_path=str(db_path), embedder_mode="hash")
    try:
        await engine.initialize()
        listed = await engine.episodic.get_all()
    finally:
        await engine.shutdown()
    assert len(listed) == 2, "the repaired row is read back as a memory"


@pytest.mark.asyncio
async def test_fix_ids_leaves_a_row_it_cannot_repair_alone(tmp_path):
    """A row rejected for its type still has an id; inventing another one would
    change nothing, and rewriting what the user never asked about is not a
    repair, so doctor reports it and keeps its hands off."""
    db_path = tmp_path / "store.db"
    await _store_with_one_good_memory(db_path)
    _insert_raw_row(db_path, "bad-enum", memory_type="long_term")

    code, out = _doctor_output(db_path, argparse.Namespace(fix_ids=True))
    assert "no row was missing an id" in out, out
    assert "Quarantined rows" in out, out
    assert code == 1

    with sqlite3.connect(db_path) as conn:
        stored = conn.execute(
            "SELECT memory_type FROM memories WHERE id = 'bad-enum'"
        ).fetchone()
    assert stored[0] == "long_term", "untouched"


@pytest.mark.asyncio
async def test_fix_ids_on_a_healthy_store_writes_nothing(tmp_path):
    """The flag runs on every store an operator trusts, so the common case has
    to be a no-op rather than an update that rewrites ids it did not need to."""
    from server.commands.doctor import _recover_idless_rows

    db_path = tmp_path / "store.db"
    await _store_with_one_good_memory(db_path)

    with sqlite3.connect(db_path) as conn:
        before = conn.execute("SELECT id, rowid FROM memories ORDER BY rowid").fetchall()

    assert _recover_idless_rows(str(db_path)) == []

    with sqlite3.connect(db_path) as conn:
        after = conn.execute("SELECT id, rowid FROM memories ORDER BY rowid").fetchall()
    assert after == before


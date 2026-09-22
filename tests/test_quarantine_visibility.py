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
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            _INSERT_SQL,
            (row_id, "written by an external tool", "episodic", 5.0, 1, "[]"),
        )


def _count_quarantined(db_path):
    """The store-side check doctor uses: rows Memory(**dict) rejects."""
    from server.commands.doctor import _count_quarantined_rows

    return _count_quarantined_rows(str(db_path))


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


def _doctor_output(db_path):
    import os

    from server.cli import cmd_doctor

    os.environ["SQLITE_DB_PATH"] = str(db_path)
    os.environ["EMBEDDER_MODE"] = "hash"
    import io as _io
    import contextlib

    buf = _io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cmd_doctor(argparse.Namespace())
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

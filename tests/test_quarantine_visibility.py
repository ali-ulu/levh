"""Quarantined rows must be observable, not just skipped in a log.

PR #267 made an invalid store row non-fatal: it is skipped with a warning.
That skip was otherwise invisible, so this pass surfaces it in the two places
operators already look: the Prometheus registry (``/api/metrics``) and
``levh doctor``.
"""

from __future__ import annotations

import argparse
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

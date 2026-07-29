"""Tests for the full audit export (memories + entity graph + trust +
conflicts) in JSON, SQLite, and PDF form. Offline & deterministic —
EMBEDDER_MODE=hash."""

import os
import sys
import tempfile

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["EMBEDDER_MODE"] = "hash"

from server.core.full_export import build_full_export, export_full_sqlite, render_full_export_pdf
from server.core.memory_engine import MemoryEngine


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


@pytest.mark.asyncio
async def test_build_full_export_counts(engine):
    await engine.store("Met with attendees", memory_type="episodic", metadata={"attendees": ["a@x.com"]})
    await engine.store("plain memory", memory_type="episodic")
    await engine.recompute_trust_scores()

    export = await build_full_export(engine)

    assert export["format"] == "levh-full-export"
    assert export["counts"]["memories"] == 2
    assert export["counts"]["trust_scores"] == 2
    assert len(export["memories"]) == 2
    assert len(export["trust"]) == 2
    assert "entity_stats" in export


@pytest.mark.asyncio
async def test_export_full_sqlite_is_valid_db(engine):
    await engine.store("hello", memory_type="episodic")
    blob = await export_full_sqlite(engine)
    assert blob[:16] == b"SQLite format 3\x00"


@pytest.mark.asyncio
async def test_render_full_export_pdf_starts_with_pdf_header(engine):
    await engine.store("hello", memory_type="episodic")
    export = await build_full_export(engine)
    blob = render_full_export_pdf(export)
    assert blob[:5] == b"%PDF-"

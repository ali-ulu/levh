"""The onboarding status endpoint must not read the corpus to count the demo.

It used to take the total as an aggregate and then, for one more number, load
every memory — ``SELECT *``, embeddings included, each row deserialised into a
``Memory`` — and count in Python. The cost of showing a demo badge grew with
the corpus it was reporting on.

The regression test that matters here is the last one: it fails if
``onboarding_status`` ever goes back to materialising memories, whatever the
counts happen to say.
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest
import pytest_asyncio

from server.core.memory_engine import MemoryEngine
from server.core.onboarding import onboarding_status

REAL = "Atlas production database uses PostgreSQL with daily backups"


@pytest_asyncio.fixture
async def engine(tmp_path, monkeypatch):
    # Keep the receipt out of the developer's real home directory.
    monkeypatch.setenv("LEVH_ONBOARDING_RECEIPT_PATH", str(tmp_path / "receipt.json"))
    eng = MemoryEngine(
        db_path=str(tmp_path / "stackmemory.db"), embedder_mode="hash", short_term_max=50
    )
    await eng.initialize()
    yield eng
    await eng.shutdown()


async def _store(eng, content: str, metadata: dict | None = None):
    return await eng.store(content=content, memory_type="episodic", metadata=metadata)


@pytest.mark.asyncio
async def test_no_memories_counts_zero(engine):
    status = await onboarding_status(engine)
    assert status["memory_count"] == 0
    assert status["demo_memory_count"] == 0


@pytest.mark.asyncio
async def test_demo_and_real_memories_are_told_apart(engine):
    await _store(engine, REAL)
    await _store(engine, "Demo: a seeded example", {"demo": True})
    await _store(engine, "Demo: another seeded example", {"demo": True})

    status = await onboarding_status(engine)

    assert status["memory_count"] == 3
    assert status["demo_memory_count"] == 2
    assert status["demo_seeded"] is True


@pytest.mark.asyncio
async def test_the_aggregate_reproduces_python_truthiness(engine):
    # The count replaced `bool(metadata.get("demo"))`, so it has to agree with
    # Python and not with SQLite. The string "false" is the case that separates
    # a faithful translation from the obvious one.
    falsy = [None, {}, {"demo": False}, {"demo": 0}, {"demo": ""}, {"demo": []}, {"demo": {}}]
    truthy = [{"demo": True}, {"demo": 1}, {"demo": "yes"}, {"demo": "false"}, {"demo": [0]}]

    for index, metadata in enumerate(falsy):
        await _store(engine, f"falsy case {index}", metadata)
    for index, metadata in enumerate(truthy):
        await _store(engine, f"truthy case {index}", metadata)

    counted = await engine.db.count_demo_memories()

    # What the old Python-side expression would have produced, computed the
    # same way, so the two are compared rather than asserted separately.
    expected = sum(1 for m in falsy + truthy if bool((m or {}).get("demo")))
    assert expected == len(truthy)
    assert counted == expected


@pytest.mark.asyncio
async def test_unparseable_metadata_is_skipped_not_fatal(engine):
    await _store(engine, REAL, {"demo": True})
    # A row written before a schema change, or by hand. Counting must not throw.
    await engine.db.conn.execute(
        "UPDATE memories SET metadata = 'not json' WHERE content = ?", (REAL,)
    )
    await engine.db.conn.commit()

    assert await engine.db.count_demo_memories() == 0


@pytest.mark.asyncio
async def test_the_fallback_agrees_with_the_aggregate(engine):
    # The JSON1-free path exists for SQLite builds without the extension, and
    # an unused fallback that disagrees is worse than none.
    await _store(engine, REAL)
    await _store(engine, "Demo one", {"demo": True})
    await _store(engine, "Demo two", {"demo": "yes"})
    await _store(engine, "Not demo", {"demo": 0})

    assert await engine.db._count_demo_memories_without_json1() == (
        await engine.db.count_demo_memories()
    )


@pytest.mark.asyncio
async def test_a_sqlite_without_json1_still_answers(engine, monkeypatch):
    import sqlite3

    await _store(engine, "Demo one", {"demo": True})
    original = engine.db.conn.execute

    async def _no_json1(sql, *args, **kwargs):
        if "json_extract" in sql:
            raise sqlite3.OperationalError("no such function: json_extract")
        return await original(sql, *args, **kwargs)

    monkeypatch.setattr(engine.db.conn, "execute", _no_json1)

    assert await engine.db.count_demo_memories() == 1


@pytest.mark.asyncio
async def test_status_never_materialises_the_corpus(engine, monkeypatch):
    """The regression guard.

    Counts alone would not catch a reintroduction: loading every memory and
    counting in Python gives the same number. What must not happen is the load.
    """
    await _store(engine, REAL)
    await _store(engine, "Demo: a seeded example", {"demo": True})

    async def _forbidden(*args, **kwargs):
        raise AssertionError(
            "onboarding_status must not read whole memories to produce a count"
        )

    monkeypatch.setattr(engine.episodic, "get_all", _forbidden)
    monkeypatch.setattr(engine.db, "get_all_memories", _forbidden)

    status = await onboarding_status(engine)

    assert status["memory_count"] == 2
    assert status["demo_memory_count"] == 1


@pytest.mark.asyncio
async def test_the_rest_of_the_status_payload_is_unchanged(engine):
    await _store(engine, REAL)

    status = await onboarding_status(engine)

    # The fields the dashboard and the CLI read must survive the change.
    for field in ("first_run", "memory_count", "demo_seeded", "demo_memory_count",
                  "ready", "recommended_next_step", "checks"):
        assert field in status, field
    assert status["first_run"] is False
    assert status["demo_seeded"] is False
    assert any(check["id"] == "memory" for check in status["checks"])

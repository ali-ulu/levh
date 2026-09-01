"""Delta-based auto-checkpoint summarization.

The background auto-checkpoint must fold in only memories newer than the last
checkpoint, produce a summary that actually reflects the new content, and skip
the write entirely when nothing new arrived — so consecutive summaries are never
the same timestamped boilerplate. All runs offline with the hash embedder.
"""

from __future__ import annotations

import os
import sys
import tempfile

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["EMBEDDER_MODE"] = "hash"
os.environ.pop("OPENAI_API_KEY", None)  # force the offline extractive path

from server.commands.auto_checkpoint import create_delta_checkpoint
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


async def _store(engine, content: str, importance: float = 0.6) -> None:
    await engine.store(content=content, importance=importance, memory_type="episodic")


@pytest.mark.asyncio
async def test_first_checkpoint_captures_all_memories_and_writes(engine):
    await _store(engine, "Decided to use SQLite for zero infra")
    await _store(engine, "Blocker: auth token expiry is not handled yet")

    last, count, created = await create_delta_checkpoint(
        engine, agent="test", project="sm"
    )

    assert created is True
    assert count == 2
    assert last is not None

    checkpoints = await engine.agent_tracker.list_checkpoints(project="sm", limit=1)
    assert checkpoints
    assert checkpoints[0]["title"].startswith("Auto summary")


@pytest.mark.asyncio
async def test_no_new_memories_skips_the_checkpoint(engine):
    await _store(engine, "Only one memory")

    last, _count, created = await create_delta_checkpoint(
        engine, agent="test", project="sm"
    )
    assert created is True

    # Nothing new since `last` -> must skip, never write a duplicate summary.
    _last2, count2, created2 = await create_delta_checkpoint(
        engine, agent="test", project="sm", last_created=last
    )
    assert created2 is False
    assert count2 == 0

    checkpoints = await engine.agent_tracker.list_checkpoints(project="sm", limit=2)
    assert len(checkpoints) == 1  # only the first checkpoint was written


@pytest.mark.asyncio
async def test_summary_reflects_only_the_new_delta(engine):
    await _store(engine, "The API gateway now sits behind a read replica")
    last, _, _ = await create_delta_checkpoint(engine, agent="test", project="sm")

    await _store(engine, "Switched the scheduler to cron instead of systemd timers")
    _last2, count2, created2 = await create_delta_checkpoint(
        engine, agent="test", project="sm", last_created=last
    )
    assert created2 is True
    assert count2 == 1

    cp = (await engine.agent_tracker.list_checkpoints(project="sm", limit=1))[0]
    # The summary mentions the NEW memory's content, not the stale first one.
    assert "cron" in cp["summary"] or "scheduler" in cp["summary"]
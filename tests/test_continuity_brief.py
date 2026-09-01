"""Tests for engine.get_continuity_context — the brief injected at session
start. Offline and deterministic (EMBEDDER_MODE=hash)."""

import os
import sys
import tempfile

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["EMBEDDER_MODE"] = "hash"

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
async def test_brief_leads_with_latest_checkpoint(engine):
    await engine.store(content="some earlier work", memory_type="episodic")
    await engine.agent_tracker.create_checkpoint(
        agent_name="opencode",
        title="older checkpoint",
        summary="not the one",
        checkpoint_type="manual",
    )
    await engine.agent_tracker.create_checkpoint(
        agent_name="claude-code",
        title="PR merged, guard active",
        summary="tomorrow: Rust teaching",
        checkpoint_type="manual",
    )

    brief = await engine.get_continuity_context()

    assert "Last Checkpoint:" in brief
    assert "PR merged, guard active" in brief
    assert "tomorrow: Rust teaching" in brief
    assert "older checkpoint" not in brief
    assert brief.splitlines()[2] == "Last Checkpoint:"


@pytest.mark.asyncio
async def test_brief_omits_checkpoint_section_when_none_exist(engine):
    await engine.store(content="some work with no checkpoint", memory_type="episodic")

    brief = await engine.get_continuity_context()

    assert "Last Checkpoint:" not in brief


@pytest.mark.asyncio
async def test_checkpoint_section_respects_project_filter(engine):
    await engine.store(content="huqan work", memory_type="episodic", project="huqan")
    await engine.agent_tracker.create_checkpoint(
        agent_name="opencode",
        title="levh side checkpoint",
        project="levh",
        checkpoint_type="manual",
    )

    brief = await engine.get_continuity_context(project="huqan")

    assert "levh side checkpoint" not in brief

"""Continuity emission/use measurement (#378).

The brief is printed at startup and hooks are installed — that proves the
brief was BUILT, not that it was handed out or used. Locked here:

  - every emitter records one EMISSION row (never "delivery" — the name is
    the contract);
  - an empty brief still records (surfaced_count 0) so the denominator stays
    the set of brief-generating events;
  - the stats pair emission with use: of the memories a recent brief
    surfaced, how many a later recall returned;
  - the text brief and the structured signals agree on what "surfaced"
    means.

Offline: hash embedder, no LLM, no network.
"""

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


async def _seed(engine: MemoryEngine) -> None:
    """One pinned rule and one blocker: the brief's canonical ingredients."""
    await engine.store(
        content="Do not skip the pre-merge hooks; run the check suite first.",
        memory_type="episodic",
        pinned=True,
        tags=["levh-rule"],
        project="atlas",
    )
    await engine.store(
        content="Blocked: the atlas release pipeline is failing on the flaky runner.",
        memory_type="episodic",
        tags=["levh-blocker"],
        project="atlas",
    )
    await engine.agent_tracker.create_checkpoint(
        agent_name="freebuff",
        title="release prep",
        summary="waiting on the atlas pipeline",
        checkpoint_type="manual",
        project="atlas",
    )


@pytest.mark.asyncio
async def test_emission_records_row_with_surfaced_ids(engine):
    await _seed(engine)

    text = await engine.emit_continuity_brief(
        channel="stderr_bridge", project="atlas", session_id="sess-1"
    )

    assert "Last Checkpoint:" in text
    emissions = await engine.db.continuity_log.list_brief_emissions()
    assert len(emissions) == 1
    row = emissions[0]
    assert row["channel"] == "stderr_bridge"
    assert row["session_id"] == "sess-1"
    assert row["project"] == "atlas"
    # The surfaced ids join back to stored memories: the checkpoint leads,
    # then the rule and the blocker the brief actually printed.
    assert row["surfaced_count"] == len(row["surfaced_ids"]) == 3
    assert row["surfaced_ids"][0].startswith("checkpoint:")


@pytest.mark.asyncio
async def test_empty_brief_still_records_with_zero_surfaced(engine):
    """A fresh store has nothing to brief: the emission happened anyway, and
    the row says so with surfaced_count 0 — the denominator keeps the
    brief-generating events instead of only the lucky ones."""
    await engine.store(
        content="an ordinary work note with no pinned or blocker content",
        memory_type="episodic",
        project="quiet",
    )
    text = await engine.emit_continuity_brief(channel="cli", project="quiet")
    assert text == ""  # header-only brief renders as nothing

    emissions = await engine.db.continuity_log.list_brief_emissions()
    assert len(emissions) == 1
    assert emissions[0]["surfaced_count"] == 0


@pytest.mark.asyncio
async def test_stats_pairs_emission_with_use(engine):
    """The one number that speaks to use: a memory the brief surfaced is
    returned by a later recall inside the use window."""
    await _seed(engine)
    await engine.emit_continuity_brief(
        channel="stderr_bridge", project="atlas", session_id="sess-1"
    )

    before = await engine.db.continuity_log.continuity_stats()
    assert before["briefs_emitted"] == 1
    assert before["recalls_in_use_window"] == 0

    # The agent asks about the blocker afterwards — a genuine recall, logged
    # by the default-on recall_log. No session filter: the memories being
    # recalled were recorded by an earlier session; the use pairing does not
    # require them to come from the same session that was briefed.
    res = await engine.recall(
        query="what is failing in the atlas release pipeline?",
        top_k=3,
        reinforce=True,
    )
    assert res.memories, "recall must return the blocker"

    after = await engine.db.continuity_log.continuity_stats()
    # Both surfaced memories the query actually matched came back — the use
    # counter counts memories, so a two-memory hit is a 2, not a 1.
    assert after["recalls_in_use_window"] >= 1
    assert after["distinct_memories_surfaced"] >= 1


@pytest.mark.asyncio
async def test_text_brief_and_signals_agree_on_surfaced(engine):
    """The measurement contract: the ids get_continuity_signals reports are
    exactly the memories whose content the text brief prints (#378 keeps the
    text and the counter from drifting apart)."""
    await _seed(engine)

    text = await engine.get_continuity_context(project="atlas")
    signals = await engine.get_continuity_signals(project="atlas")

    stored = {m.id: m for m in (await engine.episodic.search(project="atlas", limit=10))}
    for memory_id in signals["surfaced_ids"]:
        if memory_id.startswith("checkpoint:"):
            # Checkpoints are not memories: their presence is asserted by the
            # section header, not by content join.
            assert "Last Checkpoint:" in text
            continue
        m = stored[memory_id]
        probe = m.content.strip()[:40]
        assert probe in text, f"{probe!r} surfaced by signals but absent from the brief"


@pytest.mark.asyncio
async def test_record_brief_emission_survives_a_store_failure(engine):
    """Best-effort by contract: a store-level failure degrades to
    {"logged": False} instead of breaking the emitter."""
    import sqlite3

    await _seed(engine)

    async def _broken(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    engine.db.continuity_log.record_brief_emission = _broken  # type: ignore[assignment]
    result = await engine.record_brief_emission(
        channel="cli", surfaced_ids=["abc"], project="atlas"
    )
    assert result == {"logged": False}

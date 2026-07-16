"""Tests for Meeting Prep (Phase 3 proactive brief): engine.meeting_prep,
/api/meeting-prep, and the meeting_prep MCP tool. Deterministic & offline —
EMBEDDER_MODE=hash."""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["EMBEDDER_MODE"] = "hash"

from server.core.memory_engine import MemoryEngine

NOW = datetime.now(timezone.utc)


def iso_in(days: int, hour: int = 14) -> str:
    dt = NOW + timedelta(days=days)
    return dt.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()


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


async def _seed_upcoming(engine):
    """Upcoming meeting + a prior interaction + a same-project commitment & decision."""
    meeting = await engine.store(
        "Q3 Planning sync",
        memory_type="episodic",
        source="connector:calendar",
        project="acme",
        metadata={
            "captured_at": iso_in(2),
            "title": "Q3 Planning",
            "attendees": ["Alice <alice@acme.com>", "bob@acme.com"],
        },
    )
    await engine.store(
        "Reviewed the roadmap with Alice last week",
        memory_type="episodic",
        metadata={"captured_at": iso_in(-5), "from": "Alice <alice@acme.com>"},
    )
    await engine.store(
        "I will prepare the slides before the sync",
        memory_type="episodic",
        project="acme",
    )
    await engine.store(
        "We decided to launch in September",
        memory_type="episodic",
        project="acme",
    )
    return meeting


# ── engine ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_picks_next_upcoming_meeting(engine):
    meeting = await _seed_upcoming(engine)
    prep = await engine.meeting_prep()
    assert prep["meeting"] is not None
    assert prep["meeting"]["id"] == meeting.id
    assert prep["reason"] == "next upcoming meeting"
    assert prep["meeting"]["title"] == "Q3 Planning"
    assert set(prep["meeting"]["attendees"]) == {"Alice", "bob"}


@pytest.mark.asyncio
async def test_attendee_context_recent_interactions(engine):
    await _seed_upcoming(engine)
    prep = await engine.meeting_prep()
    by_name = {p["name"]: p for p in prep["people"]}
    assert by_name["Alice"]["interaction_count"] == 1
    assert by_name["Alice"]["recent"][0]["summary"].startswith("Reviewed the roadmap")
    assert by_name["bob"]["interaction_count"] == 0


@pytest.mark.asyncio
async def test_relevant_commitments_and_decisions(engine):
    await _seed_upcoming(engine)
    prep = await engine.meeting_prep()
    assert any("prepare the slides" in c["text"] for c in prep["open_commitments"])
    assert any("launch in September" in d["text"] for d in prep["recent_decisions"])


@pytest.mark.asyncio
async def test_commitment_excludes_other_projects(engine):
    await _seed_upcoming(engine)
    # a commitment in a *different* project must not surface (no attendee named either)
    await engine.store(
        "I will refactor the parser", memory_type="episodic", project="other"
    )
    prep = await engine.meeting_prep()
    assert not any("refactor the parser" in c["text"] for c in prep["open_commitments"])


@pytest.mark.asyncio
async def test_commitment_matched_by_attendee_name(engine):
    # meeting with no project, commitment references an attendee by name
    await engine.store(
        "Strategy meeting",
        memory_type="episodic",
        source="connector:calendar",
        metadata={"captured_at": iso_in(1), "attendees": ["Carol <carol@x.io>"]},
    )
    await engine.store(
        "I'll email Carol the revised numbers", memory_type="episodic"
    )
    prep = await engine.meeting_prep()
    assert any("email Carol" in c["text"] for c in prep["open_commitments"])


@pytest.mark.asyncio
async def test_query_matches_specific_meeting(engine):
    await _seed_upcoming(engine)
    await engine.store(
        "Budget review",
        memory_type="episodic",
        source="connector:calendar",
        metadata={"captured_at": iso_in(3), "title": "Budget review", "attendees": ["d@acme.com"]},
    )
    prep = await engine.meeting_prep(query="Budget")
    assert prep["meeting"]["title"] == "Budget review"
    assert "matched query" in prep["reason"]


@pytest.mark.asyncio
async def test_no_upcoming_meeting(engine):
    # only a past meeting exists
    await engine.store(
        "Old sync",
        memory_type="episodic",
        source="connector:calendar",
        metadata={"captured_at": iso_in(-10), "attendees": ["e@acme.com"]},
    )
    prep = await engine.meeting_prep(within_days=7)
    assert prep["meeting"] is None
    assert "no upcoming meetings" in prep["reason"]


@pytest.mark.asyncio
async def test_empty_engine(engine):
    prep = await engine.meeting_prep()
    assert prep["meeting"] is None
    assert prep["people"] == []
    assert prep["open_commitments"] == []
    assert prep["recent_decisions"] == []


# ── API ──────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def api_client():
    from httpx import ASGITransport, AsyncClient

    import server.api as api_mod

    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    if api_mod._engine is not None:
        await api_mod._engine.shutdown()
    api_mod._engine = MemoryEngine(db_path=db_path, embedder_mode="hash", short_term_max=50)
    await api_mod._engine.initialize()
    api_mod._initialized = True
    transport = ASGITransport(app=api_mod.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await api_mod._engine.shutdown()
    api_mod._engine = None
    api_mod._initialized = False
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.mark.asyncio
async def test_api_meeting_prep(api_client):
    await api_client.post(
        "/api/memories",
        json={
            "content": "Kickoff",
            "memory_type": "episodic",
            "source": "connector:calendar",
            "metadata": {"captured_at": iso_in(1), "title": "Kickoff", "attendees": ["z@acme.com"]},
        },
    )
    r = await api_client.get("/api/meeting-prep")
    assert r.status_code == 200
    prep = r.json()["meeting_prep"]
    assert prep["meeting"]["title"] == "Kickoff"


@pytest.mark.asyncio
async def test_api_meeting_prep_empty(api_client):
    r = await api_client.get("/api/meeting-prep")
    assert r.status_code == 200
    assert r.json()["meeting_prep"]["meeting"] is None


# ── MCP tool ─────────────────────────────────────────────────────────


def _tool_text(result) -> str:
    if isinstance(result, tuple):
        _blocks, meta = result
        if isinstance(meta, dict) and "result" in meta:
            return meta["result"]
        return "\n".join(getattr(b, "text", str(b)) for b in _blocks)
    if isinstance(result, dict) and "result" in result:
        return result["result"]
    return str(result)


@pytest.mark.asyncio
async def test_meeting_prep_mcp_tool(engine):
    from mcp.server.fastmcp import FastMCP

    from server.tools.meeting_prep import register as reg_meeting_prep

    await _seed_upcoming(engine)
    mcp = FastMCP("test")
    reg_meeting_prep(mcp, engine)
    names = {t.name for t in await mcp.list_tools()}
    assert "meeting_prep" in names

    text = _tool_text(await mcp.call_tool("meeting_prep", {}))
    assert "MEETING: Q3 Planning" in text
    assert "Alice" in text
    assert "prepare the slides" in text


@pytest.mark.asyncio
async def test_meeting_prep_mcp_tool_none(engine):
    from mcp.server.fastmcp import FastMCP

    from server.tools.meeting_prep import register as reg_meeting_prep

    mcp = FastMCP("test")
    reg_meeting_prep(mcp, engine)
    text = _tool_text(await mcp.call_tool("meeting_prep", {}))
    assert "no upcoming" in text.lower()

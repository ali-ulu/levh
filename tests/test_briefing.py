"""Tests for the Daily Briefing feature (Phase 3 roadmap item): engine.briefing,
/api/briefing, and the briefing MCP tool. Mirrors tests/test_timeline.py. Offline
and fully deterministic (no LLM call) — EMBEDDER_MODE=hash."""

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
TODAY = NOW.date().isoformat()
OLD_ACCESSED = "2020-01-01T00:00:00+00:00"


def iso_days_ago(n: int, hour: int = 9, minute: int = 0) -> str:
    dt = NOW - timedelta(days=n)
    return dt.replace(hour=hour, minute=minute, second=0, microsecond=0).isoformat()


# ── engine ─────────────────────────────────────────────────────────


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


# ── Today section ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_briefing_today_includes_events_today_with_parsed_time(engine):
    today_mem = await engine.store(
        content="Team standup: discuss Q3 roadmap",
        memory_type="episodic",
        metadata={"captured_at": f"{TODAY}T09:30:00+00:00"},
    )
    other_day = (NOW - timedelta(days=3)).date().isoformat()
    await engine.store(
        content="Old planning meeting",
        memory_type="episodic",
        metadata={"captured_at": f"{other_day}T14:00:00+00:00"},
    )

    result = await engine.briefing(days=7)
    today_ids = {item["id"] for item in result["today"]}
    assert today_mem.id in today_ids
    assert len(result["today"]) == 1  # the other-day memory is excluded

    item = next(i for i in result["today"] if i["id"] == today_mem.id)
    assert item["time"] == "09:30"
    assert item["summary"].startswith("Team standup")
    assert result["counts"]["today"] == 1


@pytest.mark.asyncio
async def test_briefing_today_sorted_by_time_ascending_empty_last(engine):
    late = await engine.store(
        content="Afternoon review",
        memory_type="episodic",
        metadata={"captured_at": f"{TODAY}T15:00:00+00:00"},
    )
    early = await engine.store(
        content="Morning sync",
        memory_type="episodic",
        metadata={"captured_at": f"{TODAY}T08:00:00+00:00"},
    )
    # created_at defaults to "now" (today) with no captured_at -> empty time,
    # must sort last regardless of insertion order.
    no_time = await engine.store(content="Quick note, no scheduled time", memory_type="episodic")

    result = await engine.briefing(days=7)
    ids_in_order = [i["id"] for i in result["today"]]
    assert ids_in_order == [early.id, late.id, no_time.id]
    assert result["today"][-1]["time"] == ""


@pytest.mark.asyncio
async def test_briefing_today_empty_when_nothing_today(engine):
    other_day = (NOW - timedelta(days=5)).date().isoformat()
    await engine.store(
        content="Not today",
        memory_type="episodic",
        metadata={"captured_at": f"{other_day}T09:00:00+00:00"},
    )
    result = await engine.briefing(days=7)
    assert result["today"] == []
    assert result["counts"]["today"] == 0


# ── Commitments section ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_briefing_commitments_detects_markers_and_extracts_sentence(engine):
    commit_en = await engine.store(
        content="Standup update. I'll send the report to the team tomorrow. Let's regroup next week.",
        memory_type="episodic",
    )
    commit_tr = await engine.store(
        content="Proje toplantısı yapıldı. Yarın raporu göndereceğim ve süreci takip edeceğim.",
        memory_type="episodic",
    )
    plain = await engine.store(
        content="This is a plain status note with no open action items.",
        memory_type="episodic",
    )

    result = await engine.briefing(days=7)
    ids = {c["id"] for c in result["commitments"]}
    assert commit_en.id in ids
    assert commit_tr.id in ids
    assert plain.id not in ids

    en_item = next(c for c in result["commitments"] if c["id"] == commit_en.id)
    assert en_item["text"] == "I'll send the report to the team tomorrow"
    assert en_item["source"] == commit_en.source
    assert en_item["date"] == TODAY

    tr_item = next(c for c in result["commitments"] if c["id"] == commit_tr.id)
    assert "göndereceğim" in tr_item["text"] or "takip" in tr_item["text"]

    assert result["counts"]["commitments"] == len(result["commitments"])


@pytest.mark.asyncio
async def test_briefing_commitments_dedupe_identical_text(engine):
    content = "I'll follow up on this next week."
    m1 = await engine.store(content=content, memory_type="episodic")
    m2 = await engine.store(content=content, memory_type="episodic")

    result = await engine.briefing(days=7)
    # exactly one of the two duplicate memories should survive de-dup
    ids = {c["id"] for c in result["commitments"]} & {m1.id, m2.id}
    assert len(ids) == 1
    texts = [c["text"] for c in result["commitments"] if c["id"] in ids]
    assert texts == [content]


@pytest.mark.asyncio
async def test_briefing_commitments_respects_days_window(engine):
    stale = await engine.store(
        content="I'll circle back on this old thread.",
        memory_type="episodic",
        metadata={"captured_at": iso_days_ago(10)},
    )
    fresh = await engine.store(
        content="I need to finalize the proposal today.",
        memory_type="episodic",
    )

    result = await engine.briefing(days=7)
    ids = {c["id"] for c in result["commitments"]}
    assert fresh.id in ids
    assert stale.id not in ids


@pytest.mark.asyncio
async def test_briefing_commitments_most_recent_first(engine):
    older = await engine.store(
        content="I'll review the older doc.",
        memory_type="episodic",
        metadata={"captured_at": iso_days_ago(2)},
    )
    newer = await engine.store(
        content="I'll review the newer doc.",
        memory_type="episodic",
        metadata={"captured_at": iso_days_ago(0)},
    )

    result = await engine.briefing(days=7)
    ordered_ids = [c["id"] for c in result["commitments"] if c["id"] in {older.id, newer.id}]
    assert ordered_ids == [newer.id, older.id]


@pytest.mark.asyncio
async def test_briefing_commitments_project_filter(engine):
    await engine.store(
        content="I'll ship the alpha feature.", memory_type="episodic", project="alpha"
    )
    await engine.store(
        content="I'll ship the beta feature.", memory_type="episodic", project="beta"
    )

    result = await engine.briefing(days=7, project="alpha")
    texts = [c["text"] for c in result["commitments"]]
    assert any("alpha" in t for t in texts)
    assert not any("beta" in t for t in texts)


# ── Fading section ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_briefing_fading_returns_low_retention_items(engine):
    faded = await engine.store(content="Long forgotten trivia", memory_type="episodic")
    await engine.db.update_memory(faded.id, {"accessed_at": OLD_ACCESSED})

    fresh = await engine.store(content="Fresh important thing", memory_type="episodic")

    result = await engine.briefing(days=7)
    fading_ids = {f["id"] for f in result["fading"]}
    assert faded.id in fading_ids
    assert fresh.id not in fading_ids

    item = next(f for f in result["fading"] if f["id"] == faded.id)
    assert "retention" in item
    assert 0.0 <= item["retention"] < 0.5
    assert item["summary"].startswith("Long forgotten")
    assert result["counts"]["fading"] == len(result["fading"])
    assert len(result["fading"]) <= 5


# ── Counts / generated_at ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_briefing_counts_consistent_and_recent_total(engine):
    await engine.store(content="I'll do a thing.", memory_type="episodic")
    await engine.store(content="Just an FYI note with no marker at all.", memory_type="episodic")

    result = await engine.briefing(days=7)
    counts = result["counts"]
    assert counts["today"] == len(result["today"])
    assert counts["commitments"] == len(result["commitments"])
    assert counts["fading"] == len(result["fading"])
    # recent_total counts *all* recent memories, not just commitments.
    assert counts["recent_total"] >= 2
    assert counts["recent_total"] >= counts["commitments"]
    assert "generated_at" in result and result["generated_at"]


@pytest.mark.asyncio
async def test_briefing_empty(engine):
    result = await engine.briefing(days=7)
    assert result["today"] == []
    assert result["commitments"] == []
    assert result["fading"] == []
    assert result["counts"] == {
        "today": 0,
        "commitments": 0,
        "fading": 0,
        "recent_total": 0,
    }


# ── API ────────────────────────────────────────────────────────────


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
async def test_api_briefing(api_client):
    await api_client.post(
        "/api/memories",
        json={"content": "I'll send the follow-up email today.", "memory_type": "episodic"},
    )
    r = await api_client.get("/api/briefing")
    assert r.status_code == 200
    body = r.json()
    assert "briefing" in body
    briefing = body["briefing"]
    for key in ("generated_at", "today", "commitments", "fading", "counts"):
        assert key in briefing
    assert any("send the follow-up email" in c["text"] for c in briefing["commitments"])


@pytest.mark.asyncio
async def test_api_briefing_empty(api_client):
    r = await api_client.get("/api/briefing")
    assert r.status_code == 200
    briefing = r.json()["briefing"]
    assert briefing["today"] == []
    assert briefing["commitments"] == []
    assert briefing["fading"] == []


# ── MCP tools ──────────────────────────────────────────────────────


def _tool_text(result) -> str:
    """FastMCP's call_tool returns (content_blocks, {"result": ...}) for a
    plain-string-returning tool; extract the string either way."""
    if isinstance(result, tuple):
        _blocks, meta = result
        if isinstance(meta, dict) and "result" in meta:
            return meta["result"]
        return "\n".join(getattr(b, "text", str(b)) for b in _blocks)
    if isinstance(result, dict) and "result" in result:
        return result["result"]
    return str(result)


@pytest.mark.asyncio
async def test_briefing_mcp_tool_registered():
    from mcp.server.fastmcp import FastMCP

    from server.tools.register import register_all_tools

    mcp = FastMCP("test")
    register_all_tools(mcp, MemoryEngine(db_path=":memory:", embedder_mode="hash"))
    names = {t.name for t in await mcp.list_tools()}
    assert "briefing" in names


@pytest.mark.asyncio
async def test_briefing_mcp_tool_formats_digest(engine):
    from mcp.server.fastmcp import FastMCP

    from server.tools.briefing import register as reg_briefing

    await engine.store(
        content="Kickoff call",
        memory_type="episodic",
        metadata={"captured_at": f"{TODAY}T10:00:00+00:00"},
    )
    await engine.store(content="I'll draft the agenda.", memory_type="episodic")
    faded = await engine.store(content="Ancient trivia", memory_type="episodic")
    await engine.db.update_memory(faded.id, {"accessed_at": OLD_ACCESSED})

    mcp = FastMCP("test")
    reg_briefing(mcp, engine)
    tools = await mcp.list_tools()
    assert any(t.name == "briefing" for t in tools)

    result = await mcp.call_tool("briefing", {"days": 7})
    text = _tool_text(result)

    assert "TODAY" in text
    assert "OPEN COMMITMENTS" in text
    assert "MIGHT BE FORGETTING" in text
    assert "Kickoff call" in text
    assert "draft the agenda" in text


@pytest.mark.asyncio
async def test_briefing_mcp_tool_nothing_pressing_message(engine):
    from mcp.server.fastmcp import FastMCP

    from server.tools.briefing import register as reg_briefing

    mcp = FastMCP("test")
    reg_briefing(mcp, engine)

    result = await mcp.call_tool("briefing", {"days": 7})
    text = _tool_text(result)

    assert "Nothing pressing" in text

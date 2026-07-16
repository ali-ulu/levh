"""Tests for the Timeline feature (Phase 2 roadmap item): engine.timeline,
/api/timeline, and the timeline MCP tool. Mirrors tests/test_people.py. Offline."""

import os
import sys
import tempfile
from datetime import datetime, timezone

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["EMBEDDER_MODE"] = "hash"

from server.core.memory_engine import MemoryEngine

OLD_DATE = "2020-01-01T00:00:00+00:00"


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


@pytest.mark.asyncio
async def test_engine_timeline_groups_by_day_and_window(engine):
    today = datetime.now(timezone.utc).date().isoformat()

    # Recent memory keeps its default created_at (now) → inside the window.
    recent = await engine.store(content="Standup notes: shipped X", memory_type="episodic")

    # Old memory is pushed outside the window via a created_at override.
    old = await engine.store(content="Ancient note", memory_type="episodic")
    await engine.db.update_memory(old.id, {"created_at": OLD_DATE})

    groups = await engine.timeline(days=30)
    dates = {g["date"] for g in groups}
    assert today in dates
    assert "2020-01-01" not in dates

    today_group = next(g for g in groups if g["date"] == today)
    assert today_group["count"] == 1
    assert today_group["items"][0]["id"] == recent.id
    assert today_group["items"][0]["source"] is None
    assert today_group["items"][0]["memory_type"] == "episodic"

    # Sorted most-recent day first.
    assert [g["date"] for g in groups] == sorted(dates, reverse=True)


@pytest.mark.asyncio
async def test_engine_timeline_uses_captured_at_over_created_at(engine):
    today = datetime.now(timezone.utc).date().isoformat()
    # captured_at is recent even though created_at gets pushed into the past —
    # the memory should still land in today's group instead of falling out of
    # the window entirely.
    mem = await engine.store(
        content="Calendar event: Q3 kickoff",
        memory_type="episodic",
        metadata={"captured_at": f"{today}T09:00:00+00:00"},
    )
    await engine.db.update_memory(mem.id, {"created_at": OLD_DATE})

    groups = await engine.timeline(days=30)
    today_group = next((g for g in groups if g["date"] == today), None)
    assert today_group is not None
    assert any(item["id"] == mem.id for item in today_group["items"])
    # And it must not also show up under the old date.
    assert not any(g["date"] == "2020-01-01" for g in groups)


@pytest.mark.asyncio
async def test_engine_timeline_project_filter(engine):
    await engine.store(content="Alpha work update", memory_type="episodic", project="alpha")
    await engine.store(content="Beta work update", memory_type="episodic", project="beta")

    groups = await engine.timeline(days=30, project="alpha")
    summaries = [item["summary"] for g in groups for item in g["items"]]
    assert any("Alpha" in s for s in summaries)
    assert not any("Beta" in s for s in summaries)


@pytest.mark.asyncio
async def test_engine_timeline_respects_days_window(engine):
    mem = await engine.store(content="Borderline note", memory_type="episodic")
    await engine.db.update_memory(mem.id, {"created_at": OLD_DATE})

    # Old memory (2020) is outside a 30-day window from "now".
    assert await engine.timeline(days=30) == []

    # A generously large window picks it back up.
    groups = await engine.timeline(days=365 * 20)
    assert any(g["date"] == "2020-01-01" for g in groups)


@pytest.mark.asyncio
async def test_engine_timeline_empty(engine):
    assert await engine.timeline(days=30) == []


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
async def test_api_timeline(api_client):
    await api_client.post("/api/memories", json={
        "content": "Meeting notes", "memory_type": "episodic",
    })
    r = await api_client.get("/api/timeline")
    assert r.status_code == 200
    body = r.json()
    assert "timeline" in body
    assert isinstance(body["timeline"], list)
    assert body["timeline"][0]["count"] >= 1


@pytest.mark.asyncio
async def test_api_timeline_empty(api_client):
    r = await api_client.get("/api/timeline")
    assert r.status_code == 200
    assert r.json()["timeline"] == []


# ── MCP tools ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_timeline_mcp_tool_registered():
    from mcp.server.fastmcp import FastMCP

    from server.tools.register import register_all_tools

    mcp = FastMCP("test")
    register_all_tools(mcp, MemoryEngine(db_path=":memory:", embedder_mode="hash"))
    names = {t.name for t in await mcp.list_tools()}
    assert "timeline" in names

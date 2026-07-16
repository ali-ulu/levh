"""Tests for Spaced-Repetition Review (Phase 5): engine.review_queue /
apply_review, /api/memories/review[/{id}], and the list_review_memories /
review_memory MCP tools. Offline & deterministic — EMBEDDER_MODE=hash.

A memory is made to "fade" by pushing its accessed_at far into the past so its
predicted retention collapses below the review threshold."""

import os
import sys
import tempfile

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["EMBEDDER_MODE"] = "hash"

from server.core.memory_engine import MemoryEngine

OLD = "2020-01-01T00:00:00+00:00"


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


async def _faded(engine, content="old memory", **kw):
    m = await engine.store(content, memory_type="episodic", **kw)
    await engine.db.update_memory(m.id, {"accessed_at": OLD})
    return m


# ── review_queue ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_faded_in_queue_fresh_not(engine):
    faded = await _faded(engine, "fading thing")
    fresh = await engine.store("fresh thing", memory_type="episodic")
    q = await engine.review_queue(threshold=0.5)
    ids = {i["id"] for i in q}
    assert faded.id in ids
    assert fresh.id not in ids


@pytest.mark.asyncio
async def test_pinned_faded_not_in_queue(engine):
    pinned = await _faded(engine, "pinned faded", pinned=True)
    q = await engine.review_queue(threshold=0.5)
    assert all(i["id"] != pinned.id for i in q)


@pytest.mark.asyncio
async def test_queue_item_has_reason_and_context(engine):
    await _faded(engine, "context memory")
    q = await engine.review_queue(threshold=0.5)
    assert q
    item = q[0]
    assert item["reason"]
    for key in ("retention", "stability_hours", "last_accessed", "recall_count", "importance"):
        assert key in item


# ── apply_review actions ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_keep_increments_review_count(engine):
    m = await _faded(engine)
    r = await engine.apply_review(m.id, "keep")
    assert r["ok"] is True
    got = await engine.episodic.get(m.id)
    assert got.metadata["review"]["review_count"] == 1
    assert got.metadata["review"]["last_action"] == "keep"


@pytest.mark.asyncio
async def test_reinforce_increases_stability(engine):
    m = await _faded(engine)
    before = (await engine.episodic.get(m.id)).stability_hours
    await engine.apply_review(m.id, "reinforce")
    after = (await engine.episodic.get(m.id)).stability_hours
    assert after > before


@pytest.mark.asyncio
async def test_weaken_decreases_stability(engine):
    m = await _faded(engine)
    before = (await engine.episodic.get(m.id)).stability_hours
    await engine.apply_review(m.id, "weaken")
    after = (await engine.episodic.get(m.id)).stability_hours
    assert after < before


@pytest.mark.asyncio
async def test_forget_removes(engine):
    m = await _faded(engine)
    r = await engine.apply_review(m.id, "forget")
    assert r["removed"] is True
    assert await engine.episodic.get(m.id) is None


@pytest.mark.asyncio
async def test_pin_pins(engine):
    m = await _faded(engine)
    r = await engine.apply_review(m.id, "pin")
    assert r["pinned"] is True
    assert (await engine.episodic.get(m.id)).pinned is True


@pytest.mark.asyncio
async def test_snooze_sets_due_and_drops_from_queue(engine):
    m = await _faded(engine)
    r = await engine.apply_review(m.id, "snooze", snooze_days=30)
    assert r["review_due_at"] is not None
    q = await engine.review_queue(threshold=0.5)
    assert all(i["id"] != m.id for i in q)


@pytest.mark.asyncio
async def test_invalid_action_raises(engine):
    m = await _faded(engine)
    with pytest.raises(ValueError):
        await engine.apply_review(m.id, "bogus")


@pytest.mark.asyncio
async def test_review_missing_memory(engine):
    r = await engine.apply_review("nonexistent-id", "keep")
    assert r["ok"] is False


@pytest.mark.asyncio
async def test_review_history_recorded(engine):
    m = await _faded(engine)
    await engine.apply_review(m.id, "keep", reason="still relevant")
    await engine.apply_review(m.id, "weaken", reason="outdated")
    got = await engine.episodic.get(m.id)
    hist = got.metadata["review_history"]
    assert len(hist) == 2
    assert hist[0]["action"] == "keep" and hist[0]["reason"] == "still relevant"
    assert hist[1]["action"] == "weaken"


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
        yield client, api_mod._engine
    await api_mod._engine.shutdown()
    api_mod._engine = None
    api_mod._initialized = False
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.mark.asyncio
async def test_api_review_queue_and_action(api_client):
    client, engine = api_client
    m = await _faded(engine, "api faded memory")

    r = await client.get("/api/memories/review")
    assert r.status_code == 200
    review = r.json()["review"]
    assert any(i["id"] == m.id for i in review)

    # apply a keep action
    r2 = await client.post(f"/api/memories/{m.id}/review", json={"action": "keep"})
    assert r2.status_code == 200
    assert r2.json()["ok"] is True


@pytest.mark.asyncio
async def test_api_review_invalid_action_422(api_client):
    client, engine = api_client
    m = await _faded(engine)
    r = await client.post(f"/api/memories/{m.id}/review", json={"action": "nope"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_api_review_missing_404(api_client):
    client, _ = api_client
    r = await client.post("/api/memories/does-not-exist/review", json={"action": "keep"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_api_review_route_not_shadowed(api_client):
    # GET /api/memories/review must not be captured by /api/memories/{memory_id}
    client, _ = api_client
    r = await client.get("/api/memories/review")
    assert r.status_code == 200
    assert "review" in r.json()


# ── MCP tools ────────────────────────────────────────────────────────


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
async def test_review_mcp_tools(engine):
    from mcp.server.fastmcp import FastMCP

    from server.tools.review import register as reg_review

    m = await _faded(engine, "mcp faded memory")
    mcp = FastMCP("test")
    reg_review(mcp, engine)
    names = {t.name for t in await mcp.list_tools()}
    assert {"list_review_memories", "review_memory"} <= names

    listed = _tool_text(await mcp.call_tool("list_review_memories", {"threshold": 0.5}))
    assert "due for review" in listed

    applied = _tool_text(
        await mcp.call_tool("review_memory", {"memory_id": m.id, "action": "keep"})
    )
    assert "keep" in applied.lower()


@pytest.mark.asyncio
async def test_review_mcp_empty(engine):
    from mcp.server.fastmcp import FastMCP

    from server.tools.review import register as reg_review

    mcp = FastMCP("test")
    reg_review(mcp, engine)
    listed = _tool_text(await mcp.call_tool("list_review_memories", {}))
    assert "No memories due for review" in listed

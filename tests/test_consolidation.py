"""Tests for Memory Consolidation (Phase 5, sleep-like compression):
engine.consolidate_memories, /api/memories/consolidate-similar, and the
consolidate_memories MCP tool. Offline & deterministic — EMBEDDER_MODE=hash.

The hash embedder is deterministic: identical content → identical embedding
(cosine 1.0), so identical-text memories form a cluster reliably."""

import os
import sys
import tempfile

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["EMBEDDER_MODE"] = "hash"

from server.core.memory_engine import MemoryEngine

DEPLOY = "Deploy process: run make deploy, then verify the staging environment"
LUNCH = "Random note about what I had for lunch today"


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


async def _seed_cluster(engine, n=3):
    ids = []
    for _ in range(n):
        m = await engine.store(DEPLOY, memory_type="episodic", importance=0.4)
        ids.append(m.id)
    return ids


# ── dry run ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dry_run_previews_without_changing(engine):
    await _seed_cluster(engine, 3)
    await engine.store(LUNCH, memory_type="episodic")

    before = (await engine.get_stats()).total_memories
    result = await engine.consolidate_memories(min_age_days=0, dry_run=True)

    assert result["dry_run"] is True
    assert result["clusters_found"] == 1
    assert result["clusters"][0]["size"] == 3
    assert result["consolidated"] == 0
    assert result["archived"] == 0
    # nothing changed
    assert (await engine.get_stats()).total_memories == before


# ── apply ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_apply_consolidates_and_archives(engine):
    ids = await _seed_cluster(engine, 3)
    await engine.store(LUNCH, memory_type="episodic")

    result = await engine.consolidate_memories(min_age_days=0, dry_run=False)
    assert result["consolidated"] == 1
    assert result["archived"] == 3

    # originals are gone from active recall
    for mid in ids:
        assert await engine.episodic.get(mid) is None

    # a consolidated memory exists, tagged and carrying the originals
    consolidated = await engine.list_memories(source="consolidation")
    assert len(consolidated) == 1
    c = consolidated[0]
    assert "consolidated" in c.tags
    archived = c.metadata.get("consolidated_from")
    assert isinstance(archived, list) and len(archived) == 3
    assert all("id" in a and "content_sha256" in a for a in archived)
    assert all("content" not in a for a in archived)


@pytest.mark.asyncio
async def test_pinned_never_consolidated(engine):
    await _seed_cluster(engine, 2)
    pinned = await engine.store(DEPLOY, memory_type="episodic", pinned=True)

    result = await engine.consolidate_memories(min_age_days=0, dry_run=False)
    # the pinned memory must survive untouched
    assert await engine.episodic.get(pinned.id) is not None
    # only the 2 unpinned were archived
    assert result["archived"] == 2


@pytest.mark.asyncio
async def test_recent_memories_excluded_by_age(engine):
    await _seed_cluster(engine, 3)
    # default min_age_days=7 — freshly-created memories are too new
    result = await engine.consolidate_memories(dry_run=True)
    assert result["clusters_found"] == 0


@pytest.mark.asyncio
async def test_singletons_not_consolidated(engine):
    await engine.store(DEPLOY, memory_type="episodic")
    await engine.store(LUNCH, memory_type="episodic")
    result = await engine.consolidate_memories(min_age_days=0, dry_run=True)
    assert result["clusters_found"] == 0


@pytest.mark.asyncio
async def test_already_consolidated_not_recompressed(engine):
    await _seed_cluster(engine, 3)
    await engine.consolidate_memories(min_age_days=0, dry_run=False)
    # a second pass should find nothing new (the summary is tagged 'consolidated')
    second = await engine.consolidate_memories(min_age_days=0, dry_run=True)
    assert second["clusters_found"] == 0


@pytest.mark.asyncio
async def test_project_filter(engine):
    for _ in range(2):
        await engine.store(DEPLOY, memory_type="episodic", project="alpha")
    for _ in range(2):
        await engine.store(DEPLOY, memory_type="episodic", project="beta")
    result = await engine.consolidate_memories(min_age_days=0, project="alpha", dry_run=True)
    assert result["clusters_found"] == 1
    assert result["clusters"][0]["project"] == "alpha"


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
async def test_api_consolidate_dry_then_apply(api_client):
    for index in range(3):
        await api_client.post(
            "/api/memories",
            json={
                "content": DEPLOY,
                "memory_type": "episodic",
                "force": index > 0,
            },
        )
    dry = await api_client.post(
        "/api/memories/consolidate-similar", json={"min_age_days": 0, "dry_run": True}
    )
    assert dry.status_code == 200
    assert dry.json()["clusters_found"] == 1

    applied = await api_client.post(
        "/api/memories/consolidate-similar", json={"min_age_days": 0, "dry_run": False}
    )
    assert applied.status_code == 200
    body = applied.json()
    assert body["consolidated"] == 1
    assert body["archived"] == 3


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
async def test_consolidate_mcp_tool(engine):
    from mcp.server.fastmcp import FastMCP

    from server.tools.consolidate_memories import register as reg

    await _seed_cluster(engine, 3)
    mcp = FastMCP("test")
    reg(mcp, engine)
    names = {t.name for t in await mcp.list_tools()}
    assert "consolidate_similar" in names

    preview = _tool_text(await mcp.call_tool("consolidate_similar", {"min_age_days": 0, "dry_run": True}))
    assert "Would consolidate 1 cluster" in preview

    applied = _tool_text(await mcp.call_tool("consolidate_similar", {"min_age_days": 0, "dry_run": False}))
    assert "Consolidated 1 cluster" in applied


@pytest.mark.asyncio
async def test_consolidate_mcp_tool_nothing(engine):
    from mcp.server.fastmcp import FastMCP

    from server.tools.consolidate_memories import register as reg

    mcp = FastMCP("test")
    reg(mcp, engine)
    text = _tool_text(await mcp.call_tool("consolidate_similar", {"min_age_days": 0}))
    assert "No consolidatable clusters" in text

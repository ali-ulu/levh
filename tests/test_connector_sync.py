"""Tests for Connector Framework v2: MemoryEngine.ingest_items /
list_sync_state, the /api/connectors/sync + /api/connectors/sync-state REST
routes, and the sync_connector / connector_sync_status MCP tools. Offline &
deterministic — EMBEDDER_MODE=hash.

The hash embedder is deterministic: identical content -> identical embedding
(cosine 1.0), so an exact-duplicate item is reliably caught by the admission
gate's dedupe check."""

import os
import sys
import tempfile

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["EMBEDDER_MODE"] = "hash"

from server.core.memory_engine import MemoryEngine

UNIQUE_1 = "Deploy process: run make deploy, then verify the staging environment carefully"
UNIQUE_2 = "Random note about what I had for lunch today"
SECRET_ITEM = "token=ghp_abcdefghijklmnopqrstuvwxyz012345 was rotated for the release bot yesterday"


def _synthetic_items() -> list[dict]:
    return [
        {"content": UNIQUE_1},
        {"content": UNIQUE_2},
        {"content": UNIQUE_1},  # exact duplicate of the first item
        {"content": "   "},  # empty/whitespace-only -> silently skipped
        {"content": SECRET_ITEM},
    ]


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


# ── engine.ingest_items ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ingest_items_gated_breakdown(engine):
    result = await engine.ingest_items(_synthetic_items(), connector="test_source")

    assert result["connector"] == "test_source"
    assert result["fetched"] == 5
    # unique1, unique2, secret item -> stored (redacted counts as stored)
    assert result["stored"] == 3
    assert result["duplicates"] == 1
    assert result["redacted"] == 1
    assert result["held"] == 0
    assert result["errors"] == 0
    assert result["source_key"] == "test_source:"
    assert result["last_synced_at"]

    memories = await engine.list_memories(limit=100)
    secret_memories = [m for m in memories if "release bot" in m.content]
    assert len(secret_memories) == 1
    assert "[REDACTED]" in secret_memories[0].content
    assert "ghp_abcdefghijklmnopqrstuvwxyz012345" not in secret_memories[0].content


@pytest.mark.asyncio
async def test_ingest_items_rerun_is_idempotent(engine):
    await engine.ingest_items(_synthetic_items(), connector="test_source")
    stats_before = await engine.get_stats()

    result = await engine.ingest_items(_synthetic_items(), connector="test_source")

    # every item is now a duplicate of something already stored
    assert result["stored"] == 0
    assert result["duplicates"] >= 3

    stats_after = await engine.get_stats()
    assert stats_after.total_memories == stats_before.total_memories


@pytest.mark.asyncio
async def test_ingest_items_no_gate_stores_everything(engine):
    result = await engine.ingest_items(
        _synthetic_items(), connector="test_source", use_gate=False
    )

    # empty/whitespace item is still skipped, but the duplicate is stored
    assert result["fetched"] == 5
    assert result["stored"] == 4
    assert result["duplicates"] == 0
    assert result["redacted"] == 0
    assert result["errors"] == 0

    memories = await engine.list_memories(limit=100)
    unredacted = [m for m in memories if "ghp_abcdefghijklmnopqrstuvwxyz012345" in m.content]
    assert len(unredacted) == 1  # secret was NOT stripped without the gate


# ── engine.list_sync_state ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_sync_state_records_and_accumulates(engine):
    r1 = await engine.ingest_items(
        _synthetic_items(), connector="test_source", project="proj-a"
    )
    rows = await engine.list_sync_state()
    assert len(rows) == 1
    row = rows[0]
    assert row["connector"] == "test_source"
    assert row["project"] == "proj-a"
    assert row["runs"] == 1
    assert row["total_stored"] == r1["stored"]

    r2 = await engine.ingest_items(
        [{"content": "Carol scheduled the design review for next Wednesday afternoon in room 4B"}],
        connector="test_source",
        project="proj-a",
    )
    rows = await engine.list_sync_state()
    assert len(rows) == 1
    row = rows[0]
    assert row["runs"] == 2
    assert row["total_stored"] == r1["stored"] + r2["stored"]


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
async def test_api_connector_sync_local_files(api_client):
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "note1.md"), "w") as f:
            f.write("# Project Notes\n\nThe release is scheduled for next Tuesday.")
        with open(os.path.join(tmpdir, "note2.md"), "w") as f:
            f.write("# Follow ups\n\nPing the infra team about the migration status.")

        resp = await api_client.post(
            "/api/connectors/sync",
            json={"connector": "local_files", "config": {"directory": tmpdir}},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["connector"] == "local_files"
        assert body["stored"] >= 1
        assert body["fetched"] >= 1

    resp = await api_client.get("/api/connectors/sync-state")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["sync_state"]) >= 1
    assert body["sync_state"][0]["connector"] == "local_files"


@pytest.mark.asyncio
async def test_api_connector_sync_unknown_connector_404(api_client):
    resp = await api_client.post(
        "/api/connectors/sync", json={"connector": "does_not_exist"}
    )
    assert resp.status_code == 404


# ── MCP tools ─────────────────────────────────────────────────────────


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
async def test_connector_sync_mcp_tools_registered(engine):
    from mcp.server.fastmcp import FastMCP

    from server.tools.connector_sync import register as reg

    mcp = FastMCP("test")
    reg(mcp, engine)
    names = {t.name for t in await mcp.list_tools()}
    assert "sync_connector" in names
    assert "connector_sync_status" in names


@pytest.mark.asyncio
async def test_connector_sync_status_empty(engine):
    from mcp.server.fastmcp import FastMCP

    from server.tools.connector_sync import register as reg

    mcp = FastMCP("test")
    reg(mcp, engine)
    text = _tool_text(await mcp.call_tool("connector_sync_status", {}))
    assert "No connector syncs" in text


@pytest.mark.asyncio
async def test_connector_sync_status_lists_after_ingest(engine):
    from mcp.server.fastmcp import FastMCP

    from server.tools.connector_sync import register as reg

    await engine.ingest_items(_synthetic_items(), connector="test_source", project="proj-a")

    mcp = FastMCP("test")
    reg(mcp, engine)
    text = _tool_text(await mcp.call_tool("connector_sync_status", {}))
    assert "test_source" in text
    assert "proj-a" in text

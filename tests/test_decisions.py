"""Tests for the Decisions entity layer (Phase 2 roadmap item): engine.list_decisions,
/api/decisions, and the list_decisions MCP tool. Deterministic decision-statement
detection (no LLM). Mirrors tests/test_briefing.py. EMBEDDER_MODE=hash."""

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


def iso_days_ago(n: int, hour: int = 9) -> str:
    dt = NOW - timedelta(days=n)
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


# ── detection ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_detects_english_and_turkish_decisions(engine):
    en = await engine.store(
        content="Long meeting notes. We decided to migrate to Postgres. Next steps follow.",
        memory_type="episodic",
    )
    tr = await engine.store(
        content="Ekip toplantısı. Yeni mimari üzerinde anlaştık ve React kullanmaya karar verdik.",
        memory_type="episodic",
    )
    plain = await engine.store(
        content="Just a routine status update with nothing resolved.",
        memory_type="episodic",
    )

    decisions = await engine.list_decisions(days=90)
    ids = {d["id"] for d in decisions}
    assert en.id in ids
    assert tr.id in ids
    assert plain.id not in ids

    en_item = next(d for d in decisions if d["id"] == en.id)
    assert en_item["text"] == "We decided to migrate to Postgres"
    assert en_item["date"] == TODAY


@pytest.mark.asyncio
async def test_going_with_and_agreed_variants(engine):
    a = await engine.store(content="We're going with vendor B for hosting.", memory_type="episodic")
    b = await engine.store(content="Everyone agreed to ship on Friday.", memory_type="episodic")
    decisions = await engine.list_decisions(days=90)
    ids = {d["id"] for d in decisions}
    assert a.id in ids
    assert b.id in ids


@pytest.mark.asyncio
async def test_decisions_dedupe_identical_text(engine):
    content = "We decided to freeze the scope."
    m1 = await engine.store(content=content, memory_type="episodic")
    m2 = await engine.store(content=content, memory_type="episodic")
    decisions = await engine.list_decisions(days=90)
    ids = {d["id"] for d in decisions} & {m1.id, m2.id}
    assert len(ids) == 1


@pytest.mark.asyncio
async def test_decisions_respects_days_window(engine):
    stale = await engine.store(
        content="We decided to sunset the old API.",
        memory_type="episodic",
        metadata={"captured_at": iso_days_ago(120)},
    )
    fresh = await engine.store(
        content="We decided to adopt the new API.",
        memory_type="episodic",
        metadata={"captured_at": iso_days_ago(1)},
    )
    decisions = await engine.list_decisions(days=90)
    ids = {d["id"] for d in decisions}
    assert fresh.id in ids
    assert stale.id not in ids


@pytest.mark.asyncio
async def test_decisions_most_recent_first(engine):
    older = await engine.store(
        content="We chose the older option.",
        memory_type="episodic",
        metadata={"captured_at": iso_days_ago(5)},
    )
    newer = await engine.store(
        content="We chose the newer option.",
        memory_type="episodic",
        metadata={"captured_at": iso_days_ago(0)},
    )
    decisions = await engine.list_decisions(days=90)
    ordered = [d["id"] for d in decisions if d["id"] in {older.id, newer.id}]
    assert ordered == [newer.id, older.id]


@pytest.mark.asyncio
async def test_decisions_project_filter(engine):
    await engine.store(
        content="We decided to use Rust for alpha.", memory_type="episodic", project="alpha"
    )
    await engine.store(
        content="We decided to use Go for beta.", memory_type="episodic", project="beta"
    )
    decisions = await engine.list_decisions(days=90, project="alpha")
    texts = [d["text"] for d in decisions]
    assert any("Rust" in t for t in texts)
    assert not any("Go for beta" in t for t in texts)


@pytest.mark.asyncio
async def test_decisions_empty(engine):
    await engine.store(content="No resolution here, just chatter.", memory_type="episodic")
    assert await engine.list_decisions(days=90) == []


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
async def test_api_decisions(api_client):
    await api_client.post(
        "/api/memories",
        json={"content": "We agreed to launch next month.", "memory_type": "episodic"},
    )
    r = await api_client.get("/api/decisions")
    assert r.status_code == 200
    decisions = r.json()["decisions"]
    assert any("launch next month" in d["text"] for d in decisions)


@pytest.mark.asyncio
async def test_api_decisions_empty_shape(api_client):
    r = await api_client.get("/api/decisions")
    assert r.status_code == 200
    assert r.json()["decisions"] == []


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
async def test_decisions_mcp_tool(engine):
    from mcp.server.fastmcp import FastMCP

    from server.tools.decisions import register as reg_decisions

    await engine.store(content="We decided to hire two engineers.", memory_type="episodic")
    mcp = FastMCP("test")
    reg_decisions(mcp, engine)
    names = {t.name for t in await mcp.list_tools()}
    assert "list_decisions" in names

    text = _tool_text(await mcp.call_tool("list_decisions", {"days": 90}))
    assert "decisions" in text
    assert "hire two engineers" in text


@pytest.mark.asyncio
async def test_decisions_mcp_tool_empty_message(engine):
    from mcp.server.fastmcp import FastMCP

    from server.tools.decisions import register as reg_decisions

    mcp = FastMCP("test")
    reg_decisions(mcp, engine)
    text = _tool_text(await mcp.call_tool("list_decisions", {"days": 90}))
    assert "No decisions detected" in text

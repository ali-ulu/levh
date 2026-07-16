"""Tests for the Ask-your-life feature: engine.ask, answerer fallback,
/api/ask endpoint, and the ask_memory MCP tool. All run offline (hash
embedder, no OPENAI_API_KEY) exercising the deterministic path."""

import os
import sys
import tempfile

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["EMBEDDER_MODE"] = "hash"
os.environ.pop("OPENAI_API_KEY", None)  # force the offline extractive path

from server.core.answerer import answer_question
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


# ── answerer (unit) ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_answerer_empty_sources():
    out = await answer_question("anything", [], mode="auto")
    assert "don't have any memories" in out.lower()


@pytest.mark.asyncio
async def test_answerer_extractive_lists_sources():
    sources = [
        {"n": 1, "content": "We chose SQLite for zero infra", "created_at": "2026-01-02T00:00:00", "project": "sm"},
        {"n": 2, "content": "Auth uses the OS keychain", "created_at": "2026-01-03T00:00:00", "project": "sm"},
    ]
    out = await answer_question("why sqlite?", sources, mode="extractive")
    assert "[1]" in out and "[2]" in out
    assert "SQLite" in out


# ── engine.ask ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ask_returns_answer_and_sources(engine):
    await engine.store(content="We chose SQLite because it needs zero infrastructure",
                       importance=0.8, memory_type="episodic")
    await engine.store(content="The frontend package manager is pnpm, not npm",
                       importance=0.6, memory_type="episodic")

    result = await engine.ask("why did we choose SQLite?", top_k=3)
    assert result["question"] == "why did we choose SQLite?"
    assert isinstance(result["answer"], str) and result["answer"]
    assert len(result["sources"]) >= 1
    # Every source carries a citation index and no embedding
    for s in result["sources"]:
        assert "n" in s and "id" in s and "content" in s
        assert "embedding" not in s


@pytest.mark.asyncio
async def test_ask_is_read_only(engine):
    """Asking must not reinforce memories (no frequency/recall_count bump)."""
    mem = await engine.store(content="Read only probe fact", memory_type="episodic")
    before = await engine.get_memory(mem.id)
    await engine.ask("Read only probe fact", top_k=1)
    after = await engine.get_memory(mem.id)
    assert after.frequency == before.frequency
    assert after.recall_count == before.recall_count
    assert after.stability_hours == before.stability_hours


@pytest.mark.asyncio
async def test_ask_project_filter(engine):
    await engine.store(content="Alpha project secret", project="alpha", memory_type="episodic")
    await engine.store(content="Beta project secret", project="beta", memory_type="episodic")
    result = await engine.ask("secret", top_k=5, project="alpha")
    assert all(s["project"] == "alpha" for s in result["sources"])


@pytest.mark.asyncio
async def test_ask_no_memories(engine):
    result = await engine.ask("what is the meaning of life?", top_k=5)
    assert result["sources"] == []
    assert isinstance(result["answer"], str)


# ── REST endpoint ──────────────────────────────────────────────────


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
async def test_api_ask(api_client):
    await api_client.post("/api/memories", json={
        "content": "Deploy runs from the prod branch via GitHub Actions",
        "importance": 0.7, "memory_type": "episodic"})
    r = await api_client.post("/api/ask", json={"question": "how does deploy work?", "top_k": 3})
    assert r.status_code == 200
    body = r.json()
    assert "answer" in body and "sources" in body
    assert body["question"] == "how does deploy work?"


@pytest.mark.asyncio
async def test_api_ask_empty(api_client):
    r = await api_client.post("/api/ask", json={"question": "nothing stored"})
    assert r.status_code == 200
    assert r.json()["sources"] == []


# ── MCP tool registration ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_ask_memory_tool_registered():
    from mcp.server.fastmcp import FastMCP

    from server.tools.register import register_all_tools

    mcp = FastMCP("test")
    register_all_tools(mcp, MemoryEngine(db_path=":memory:", embedder_mode="hash"))
    names = {t.name for t in await mcp.list_tools()}
    assert "ask_memory" in names

"""Tests for the persistent Entity Knowledge Graph (Faz 2): pure extraction,
engine reindex_entities/list_entities_graph/get_entity/entity_graph_stats,
/api/entities endpoints, and MCP tools. Offline."""

import os
import sys
import tempfile

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["EMBEDDER_MODE"] = "hash"

from server.core.entities import extract_entities
from server.core.memory_engine import MemoryEngine


# ── extract_entities (unit, duck-typed memories) ────────────────────


class _Mem:
    def __init__(self, id, content="", metadata=None, source="connector:calendar", created_at="2026-01-01"):
        self.id = id
        self.content = content
        self.metadata = metadata or {}
        self.source = source
        self.created_at = created_at


def test_extract_entities_event_person_org():
    mem = _Mem(
        "m1",
        content="Kickoff for Q3",
        metadata={
            "title": "Q3 Planning",
            "captured_at": "2026-01-01T10:00:00+00:00",
            "attendees": ["Alice <alice@acme.com>", "bob@acme.com"],
        },
        source="connector:calendar",
    )
    entities = extract_entities(mem)
    types = {(e["type"], e["name"]) for e in entities}

    persons = [e for e in entities if e["type"] == "person"]
    assert any("alice" in e["key"] for e in persons)

    orgs = [e for e in entities if e["type"] == "organization"]
    assert any(e["name"] == "Acme" for e in orgs)

    events = [e for e in entities if e["type"] == "event"]
    assert any(e["name"] == "Q3 Planning" for e in events)
    assert ("event", "Q3 Planning") in types


def test_extract_entities_task():
    mem = _Mem("m2", content="I will send the report", source="connector:notes")
    entities = extract_entities(mem)
    tasks = [e for e in entities if e["type"] == "task"]
    assert len(tasks) == 1
    assert "send the report" in tasks[0]["name"]


def test_extract_entities_document():
    mem = _Mem(
        "m3",
        content="Spec body",
        metadata={"title": "Design Spec"},
        source="connector:notion",
    )
    entities = extract_entities(mem)
    docs = [e for e in entities if e["type"] == "document"]
    assert len(docs) == 1
    assert docs[0]["name"] == "Design Spec"


# ── engine ───────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def engine():
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    eng = MemoryEngine(db_path=db_path, embedder_mode="hash", short_term_max=50)
    await eng.initialize()
    yield eng
    await eng.shutdown()
    if os.path.exists(db_path):
        os.unlink(db_path)


async def _seed(engine):
    await engine.store(
        content="Kickoff for Q3",
        memory_type="episodic",
        source="connector:calendar",
        metadata={
            "title": "Q3 Planning",
            "captured_at": "2026-01-01T10:00:00+00:00",
            "attendees": ["Alice <alice@acme.com>", "bob@acme.com"],
        },
    )
    await engine.store(
        content="I will send the report",
        memory_type="episodic",
        source="connector:notes",
    )
    await engine.store(
        content="Spec body",
        memory_type="episodic",
        source="connector:notion",
        metadata={"title": "Design Spec"},
    )


@pytest.mark.asyncio
async def test_reindex_entities(engine):
    await _seed(engine)
    result = await engine.reindex_entities()

    assert result["memories"] == 3
    assert result["links"] > 0
    by_type = result["by_type"]
    for t in ("person", "organization", "event", "document", "task"):
        assert by_type.get(t, 0) >= 1


@pytest.mark.asyncio
async def test_list_entities_graph_filters_by_type(engine):
    await _seed(engine)
    await engine.reindex_entities()

    people = await engine.list_entities_graph(entity_type="person")
    assert len(people) >= 1
    assert all(p["type"] == "person" for p in people)
    assert all("mentions" in p for p in people)


@pytest.mark.asyncio
async def test_get_entity_alice_has_memories_and_related(engine):
    await _seed(engine)
    await engine.reindex_entities()

    result = await engine.get_entity("Alice")
    assert result is not None
    assert result["entity"]["type"] == "person"
    assert len(result["memories"]) > 0
    related_types = {r["type"] for r in result["related"]}
    assert "organization" in related_types or "event" in related_types


@pytest.mark.asyncio
async def test_get_entity_not_found(engine):
    assert await engine.get_entity("does-not-exist") is None


@pytest.mark.asyncio
async def test_reindex_is_idempotent(engine):
    await _seed(engine)
    first = await engine.reindex_entities()
    second = await engine.reindex_entities()

    assert first["entities"] == second["entities"]
    assert first["links"] == second["links"]
    assert first["by_type"] == second["by_type"]


@pytest.mark.asyncio
async def test_entity_graph_stats(engine):
    await _seed(engine)
    await engine.reindex_entities()
    stats = await engine.entity_graph_stats()
    assert stats["by_type"].get("person", 0) >= 1


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
async def test_api_entities_flow(api_client):
    await api_client.post("/api/memories", json={
        "content": "Kickoff for Q3", "memory_type": "episodic",
        "source": "connector:calendar",
        "metadata": {
            "title": "Q3 Planning",
            "captured_at": "2026-01-01T10:00:00+00:00",
            "attendees": ["Alice <alice@acme.com>", "bob@acme.com"],
        },
    })

    r = await api_client.post("/api/entities/reindex")
    assert r.status_code == 200
    body = r.json()
    assert body["entities"] > 0

    r = await api_client.get("/api/entities/stats")
    assert r.status_code == 200
    assert "by_type" in r.json()

    r = await api_client.get("/api/entities?type=person")
    assert r.status_code == 200
    people = r.json()["entities"]
    assert len(people) > 0
    entity_id = people[0]["id"]

    r = await api_client.get(f"/api/entities/{entity_id}")
    assert r.status_code == 200
    assert r.json()["entity"]["id"] == entity_id

    r = await api_client.get("/api/entities/does-not-exist-at-all")
    assert r.status_code == 404

    # ensure /api/entities/stats isn't shadowed by the {entity_id:path} route
    r = await api_client.get("/api/entities/stats")
    assert r.status_code == 200
    assert "by_type" in r.json()


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
async def test_entities_mcp_tools_registered():
    from mcp.server.fastmcp import FastMCP

    from server.tools.register import register_all_tools

    mcp = FastMCP("test")
    register_all_tools(mcp, MemoryEngine(db_path=":memory:", embedder_mode="hash"))
    names = {t.name for t in await mcp.list_tools()}
    assert "reindex_entities" in names
    assert "list_entities" in names
    assert "about_entity" in names


@pytest.mark.asyncio
async def test_entities_mcp_tools_flow(engine):
    from mcp.server.fastmcp import FastMCP

    from server.tools.entities import register as reg_entities

    await _seed(engine)

    mcp = FastMCP("test")
    reg_entities(mcp, engine)

    result = await mcp.call_tool("reindex_entities", {})
    text = _tool_text(result)
    assert "Indexed 3 memories" in text

    result = await mcp.call_tool("list_entities", {"entity_type": "person", "limit": 20})
    text = _tool_text(result)
    assert "person" in text.lower()

    result = await mcp.call_tool("about_entity", {"query": "Alice"})
    text = _tool_text(result)
    assert "Alice" in text

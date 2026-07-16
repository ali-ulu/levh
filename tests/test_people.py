"""Tests for the People layer (Phase 2 entity graph): parsing, aggregation,
engine list_people/get_person, /api/people endpoints, and MCP tools. Offline."""

import os
import sys
import tempfile

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["EMBEDDER_MODE"] = "hash"

from server.core.memory_engine import MemoryEngine
from server.core.people import aggregate_people, extract_people, find_person_key, parse_person


# ── parse_person (unit) ────────────────────────────────────────────


def test_parse_person_forms():
    assert parse_person("Dana Acme <dana@acme.com>") == ("Dana Acme", "dana@acme.com")
    assert parse_person("dana@acme.com") == ("dana", "dana@acme.com")
    assert parse_person("Bob Jones") == ("Bob Jones", "")
    assert parse_person("  ") is None
    # angle form with no name uses the local part
    assert parse_person("<x@y.com>") == ("x", "x@y.com")


def test_extract_people_from_metadata():
    md = {
        "organizer": "Alice <alice@x.com>",
        "attendees": ["Bob <bob@y.com>", "Carol"],
        "from": "Dana <dana@z.com>",
        "to": ["you@me.com"],
        "speakers": ["Eve"],
        "unrelated": "ignore",
    }
    people = extract_people(md)
    names = {p[0] for p in people}
    assert {"Alice", "Bob", "Carol", "Dana", "Eve"}.issubset(names)


# ── aggregation (unit, duck-typed memories) ────────────────────────


class _Mem:
    def __init__(self, id, metadata, source="connector:calendar", created_at="2026-01-01"):
        self.id = id
        self.metadata = metadata
        self.source = source
        self.created_at = created_at


def test_aggregate_counts_and_identity():
    mems = [
        _Mem("m1", {"attendees": ["Dana Acme <dana@acme.com>"]}, created_at="2026-01-02"),
        # Same person, different name form + source → should merge on email
        _Mem("m2", {"from": "Dana <dana@acme.com>"}, source="connector:email", created_at="2026-01-05"),
        _Mem("m3", {"attendees": ["Bob <bob@x.com>"]}),
    ]
    people = aggregate_people(mems)
    dana = next(p for p in people if p["email"] == "dana@acme.com")
    assert dana["memory_count"] == 2
    assert dana["name"] == "Dana Acme"  # longest display name kept
    assert set(dana["sources"]) == {"connector:calendar", "connector:email"}
    assert dana["last_seen"] == "2026-01-05"
    # sorted by count desc → Dana first
    assert people[0]["email"] == "dana@acme.com"


def test_person_counted_once_per_memory():
    mems = [_Mem("m1", {"from": "Dana <dana@acme.com>", "to": ["dana@acme.com"]})]
    people = aggregate_people(mems)
    assert people[0]["memory_count"] == 1


def test_find_person_key():
    people = aggregate_people([_Mem("m1", {"attendees": ["Dana Acme <dana@acme.com>"]})])
    assert find_person_key(people, "dana@acme.com") == "dana@acme.com"
    assert find_person_key(people, "dana") == "dana@acme.com"
    assert find_person_key(people, "nobody") is None


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
async def test_engine_list_and_get_person(engine):
    await engine.store(
        content="Meeting: Q3 sync", memory_type="episodic",
        metadata={"attendees": ["Dana Acme <dana@acme.com>", "Bob <bob@x.com>"]},
    )
    await engine.store(
        content="Email: pricing", memory_type="episodic",
        metadata={"from": "Dana <dana@acme.com>", "to": ["you@me.com"]},
    )

    people = await engine.list_people()
    assert any(p["email"] == "dana@acme.com" and p["memory_count"] == 2 for p in people)
    # summary view drops memory_ids
    assert all("memory_ids" not in p for p in people)

    detail = await engine.get_person("dana")
    assert detail is not None
    assert detail["person"]["email"] == "dana@acme.com"
    assert len(detail["memories"]) == 2
    assert all("embedding" not in m for m in detail["memories"])

    assert await engine.get_person("ghost") is None


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
async def test_api_people(api_client):
    await api_client.post("/api/memories", json={
        "content": "Meeting with Dana", "memory_type": "episodic",
        "metadata": {"attendees": ["Dana Acme <dana@acme.com>"]},
    })
    r = await api_client.get("/api/people")
    assert r.status_code == 200
    people = r.json()["people"]
    assert any(p["email"] == "dana@acme.com" for p in people)

    r = await api_client.get("/api/people/dana@acme.com")
    assert r.status_code == 200
    assert r.json()["person"]["email"] == "dana@acme.com"
    assert len(r.json()["memories"]) == 1

    r = await api_client.get("/api/people/ghost")
    assert r.status_code == 404


# ── MCP tools ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_people_mcp_tools_registered():
    from mcp.server.fastmcp import FastMCP

    from server.tools.register import register_all_tools

    mcp = FastMCP("test")
    register_all_tools(mcp, MemoryEngine(db_path=":memory:", embedder_mode="hash"))
    names = {t.name for t in await mcp.list_tools()}
    assert "list_people" in names
    assert "about_person" in names

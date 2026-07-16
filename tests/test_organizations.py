"""Tests for the Organizations entity layer (Phase 2 roadmap item): the pure
domain_to_org / aggregate_organizations helpers, engine.list_organizations /
engine.get_organization, /api/organizations[/{key}], and the
list_organizations / about_organization MCP tools. Mirrors tests/test_people.py.
Offline and fully deterministic — EMBEDDER_MODE=hash."""

import os
import sys
import tempfile

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["EMBEDDER_MODE"] = "hash"

from server.core.memory_engine import MemoryEngine
from server.core.organizations import (
    FREE_EMAIL_DOMAINS,
    aggregate_organizations,
    domain_to_org,
    find_org_key,
)


class _FakeMem:
    """Duck-typed stand-in for a Memory for the pure-function tests."""

    def __init__(self, id, metadata, source=None, created_at=""):
        self.id = id
        self.metadata = metadata
        self.source = source
        self.created_at = created_at


# ── domain_to_org ────────────────────────────────────────────────────


def test_domain_to_org_simple():
    assert domain_to_org("acme.com") == "Acme"


def test_domain_to_org_multi_label_and_country_suffix():
    assert domain_to_org("mail.acme.co.uk") == "Acme"
    assert domain_to_org("bbc.co.uk") == "Bbc"


def test_domain_to_org_strips_www():
    assert domain_to_org("www.globex.io") == "Globex"


def test_domain_to_org_empty_returns_input():
    assert domain_to_org("") == ""


# ── aggregate_organizations ──────────────────────────────────────────


def test_aggregate_groups_by_domain():
    mems = [
        _FakeMem("m1", {"from": "Alice <alice@acme.com>", "to": ["bob@acme.com"]},
                 source="email", created_at="2026-01-02T00:00:00+00:00"),
        _FakeMem("m2", {"organizer": "Carol <carol@acme.com>"},
                 source="calendar", created_at="2026-01-03T00:00:00+00:00"),
    ]
    orgs = aggregate_organizations(mems)
    assert len(orgs) == 1
    acme = orgs[0]
    assert acme["domain"] == "acme.com"
    assert acme["name"] == "Acme"
    assert acme["memory_count"] == 2
    assert acme["person_count"] == 3  # alice, bob, carol
    assert acme["last_seen"] == "2026-01-03T00:00:00+00:00"
    assert set(acme["sources"]) == {"email", "calendar"}


def test_aggregate_excludes_free_email_providers():
    mems = [
        _FakeMem("m1", {"from": "Someone <someone@gmail.com>"}),
        _FakeMem("m2", {"from": "Work <person@acme.com>"}),
    ]
    orgs = aggregate_organizations(mems)
    domains = {o["domain"] for o in orgs}
    assert "gmail.com" not in domains
    assert "acme.com" in domains
    assert "gmail.com" in FREE_EMAIL_DOMAINS


def test_aggregate_memory_counts_once_per_org():
    # Two people from the same domain in one memory -> memory counted once.
    mems = [
        _FakeMem("m1", {"attendees": ["a@acme.com", "b@acme.com", "c@acme.com"]}),
    ]
    orgs = aggregate_organizations(mems)
    assert orgs[0]["memory_count"] == 1
    assert orgs[0]["person_count"] == 3


def test_aggregate_sorted_most_frequent_first():
    mems = [
        _FakeMem("m1", {"from": "x@big.com"}),
        _FakeMem("m2", {"from": "y@big.com"}),
        _FakeMem("m3", {"from": "z@small.com"}),
    ]
    orgs = aggregate_organizations(mems)
    assert [o["domain"] for o in orgs] == ["big.com", "small.com"]


def test_aggregate_longest_name_wins_per_person():
    mems = [
        _FakeMem("m1", {"from": "al@acme.com"}),
        _FakeMem("m2", {"from": "Alice Anderson <al@acme.com>"}),
    ]
    orgs = aggregate_organizations(mems)
    # same email across two memories -> counted as one person, longest name kept
    assert orgs[0]["person_count"] == 1
    assert "Alice Anderson" in orgs[0]["people"]


def test_find_org_key():
    orgs = aggregate_organizations([
        _FakeMem("m1", {"from": "a@acme.com"}),
        _FakeMem("m2", {"from": "b@globex.io"}),
    ])
    assert find_org_key(orgs, "acme.com") == "acme.com"
    assert find_org_key(orgs, "Globex") == "globex.io"
    assert find_org_key(orgs, "nonexistent") is None
    assert find_org_key(orgs, "") is None


# ── engine ───────────────────────────────────────────────────────────


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
async def test_engine_list_organizations(engine):
    await engine.store(
        content="Contract review call",
        memory_type="episodic",
        source="calendar",
        metadata={"attendees": ["dave@acme.com", "erin@acme.com"]},
    )
    await engine.store(
        content="Personal reminder",
        memory_type="episodic",
        metadata={"from": "me@gmail.com"},
    )
    orgs = await engine.list_organizations()
    domains = {o["domain"] for o in orgs}
    assert "acme.com" in domains
    assert "gmail.com" not in domains
    acme = next(o for o in orgs if o["domain"] == "acme.com")
    assert "memory_ids" not in acme  # internal field dropped in summary view


@pytest.mark.asyncio
async def test_engine_get_organization_found_and_not_found(engine):
    m = await engine.store(
        content="Kickoff with the Acme team",
        memory_type="episodic",
        source="email",
        metadata={"from": "Frank <frank@acme.com>", "to": ["me@myco.com"]},
    )
    result = await engine.get_organization("acme.com")
    assert result is not None
    assert result["organization"]["domain"] == "acme.com"
    ids = {mem["id"] for mem in result["memories"]}
    assert m.id in ids

    assert await engine.get_organization("doesnotexist.com") is None


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
async def test_api_organizations_list_and_detail(api_client):
    await api_client.post(
        "/api/memories",
        json={
            "content": "Quarterly sync",
            "memory_type": "episodic",
            "source": "calendar",
            "metadata": {"attendees": ["grace@acme.com", "heidi@acme.com"]},
        },
    )
    r = await api_client.get("/api/organizations")
    assert r.status_code == 200
    orgs = r.json()["organizations"]
    assert any(o["domain"] == "acme.com" for o in orgs)

    r2 = await api_client.get("/api/organizations/acme.com")
    assert r2.status_code == 200
    assert r2.json()["organization"]["domain"] == "acme.com"


@pytest.mark.asyncio
async def test_api_organization_404(api_client):
    r = await api_client.get("/api/organizations/nope.com")
    assert r.status_code == 404


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
async def test_organizations_mcp_tools_registered_and_format(engine):
    from mcp.server.fastmcp import FastMCP

    from server.tools.organizations import register as reg_orgs

    await engine.store(
        content="Renewal discussion",
        memory_type="episodic",
        source="email",
        metadata={"from": "Ivan <ivan@acme.com>"},
    )
    mcp = FastMCP("test")
    reg_orgs(mcp, engine)
    names = {t.name for t in await mcp.list_tools()}
    assert {"list_organizations", "about_organization"} <= names

    listed = _tool_text(await mcp.call_tool("list_organizations", {"limit": 10}))
    assert "Acme" in listed
    assert "acme.com" in listed

    about = _tool_text(await mcp.call_tool("about_organization", {"query": "acme.com"}))
    assert "Acme" in about
    assert "Renewal discussion" in about


@pytest.mark.asyncio
async def test_about_organization_not_found_message(engine):
    from mcp.server.fastmcp import FastMCP

    from server.tools.organizations import register as reg_orgs

    mcp = FastMCP("test")
    reg_orgs(mcp, engine)
    about = _tool_text(await mcp.call_tool("about_organization", {"query": "ghost.com"}))
    assert "No organization matching" in about

"""Tests for the Provenance / Trust Score: pure helpers in server/core/trust.py,
engine recompute_trust_scores/get_trust/list_low_trust, /api/memories trust
endpoints, and MCP tools. Deterministic, offline (EMBEDDER_MODE=hash), no LLM,
no network. Trust is a reliability signal — NOT truth — and must never change
H-score / recall ranking."""

import os
import sys
import tempfile

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["EMBEDDER_MODE"] = "hash"

from server.core import trust
from server.core.memory_engine import MemoryEngine


# ── 1. source_score differs by source type (pure helper) ─────────────


def test_source_score_differs_by_source_type():
    manual = trust.source_score("dashboard")
    email = trust.source_score("connector:email")
    unknown = trust.source_score(None)

    assert manual > email > unknown


def test_source_score_pinned_floors_at_manual():
    low = trust.source_score("connector:email", pinned=False)
    pinned = trust.source_score("connector:email", pinned=True)
    assert pinned >= trust.source_score("dashboard")
    assert pinned > low


# ── engine fixture ─────────────────────────────────────────────────


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


# ── 2 & 3 & 9. corroboration via the entity graph ────────────────────


@pytest.mark.asyncio
async def test_independent_source_types_increase_corroboration(engine):
    # A lonely single-source memory about a person no one else references.
    lonely = await engine.store(
        content="Solo note about Zoe with no other corroborating source",
        memory_type="episodic",
        source="connector:notes",
        metadata={"attendees": ["zoe@gmail.com"]},
    )

    # Three memories about Alice from three DISTINCT source types.
    email_mem = await engine.store(
        content="Email thread discussing the Q3 roadmap with Alice",
        memory_type="episodic",
        source="connector:email",
        metadata={"from": "alice@gmail.com"},
    )
    await engine.store(
        content="Calendar invite for the roadmap sync meeting",
        memory_type="episodic",
        source="connector:calendar",
        metadata={"attendees": ["alice@gmail.com"]},
    )
    await engine.store(
        content="Transcript excerpt from the roadmap sync call",
        memory_type="episodic",
        source="connector:transcript",
        metadata={"speakers": ["alice@gmail.com"]},
    )

    await engine.recompute_trust_scores()

    lonely_bd = await engine.get_trust(lonely.id)
    email_bd = await engine.get_trust(email_mem.id)

    assert email_bd["components"]["corroboration_score"] > lonely_bd["components"]["corroboration_score"]
    assert len(email_bd["evidence"]["distinct_source_types"]) >= 3


@pytest.mark.asyncio
async def test_duplicate_same_source_does_not_inflate_corroboration(engine):
    # Two memories, same person, SAME source type -> only 1 distinct type.
    same_a = await engine.store(
        content="First email mentioning Bob's project status",
        memory_type="episodic",
        source="connector:email",
        metadata={"from": "bob@gmail.com"},
    )
    await engine.store(
        content="Second email mentioning Bob's project status again",
        memory_type="episodic",
        source="connector:email",
        metadata={"from": "bob@gmail.com"},
    )

    # Two-distinct-type case for comparison.
    diff_a = await engine.store(
        content="Email discussing Carol's onboarding",
        memory_type="episodic",
        source="connector:email",
        metadata={"from": "carol@gmail.com"},
    )
    await engine.store(
        content="Calendar invite for Carol's onboarding session",
        memory_type="episodic",
        source="connector:calendar",
        metadata={"attendees": ["carol@gmail.com"]},
    )

    await engine.recompute_trust_scores()

    same_bd = await engine.get_trust(same_a.id)
    diff_bd = await engine.get_trust(diff_a.id)

    assert len(same_bd["evidence"]["distinct_source_types"]) == 1
    assert same_bd["components"]["corroboration_score"] <= diff_bd["components"]["corroboration_score"]


# ── 4. pinned / reinforced memory gets a review boost ─────────────────


@pytest.mark.asyncio
async def test_pinned_memory_has_higher_review_score(engine):
    plain = await engine.store(
        content="Plain unpinned memory",
        memory_type="episodic",
        source="connector:notes",
    )
    pinned = await engine.store(
        content="Pinned memory that a human deliberately kept",
        memory_type="episodic",
        source="connector:notes",
        pinned=True,
    )

    await engine.recompute_trust_scores()

    plain_bd = await engine.get_trust(plain.id)
    pinned_bd = await engine.get_trust(pinned.id)

    assert pinned_bd["components"]["review_score"] > plain_bd["components"]["review_score"]


# ── 5. weakened / negative feedback lowers review score ───────────────


@pytest.mark.asyncio
async def test_negative_feedback_lowers_review_score(engine):
    mem = await engine.store(
        content="Memory that will later receive negative feedback",
        memory_type="episodic",
        source="connector:notes",
    )
    baseline_bd = await engine.get_trust(mem.id)
    baseline_review = baseline_bd["components"]["review_score"]

    await engine.memory_feedback(mem.id, helpful=False)
    await engine.recompute_trust_scores()

    weakened_bd = await engine.get_trust(mem.id)
    assert weakened_bd["components"]["review_score"] < baseline_review


# ── 6. redacted memory gets a risk penalty ────────────────────────────


@pytest.mark.asyncio
async def test_redacted_memory_gets_risk_penalty(engine):
    clean = await engine.store(
        content="Clean memory with no risk flags",
        memory_type="episodic",
        source="connector:notes",
    )
    redacted = await engine.store(
        content="Memory that had a secret redacted from it",
        memory_type="episodic",
        source="connector:notes",
        metadata={"redaction_history": [{"secrets": ["x"], "at": "2026-01-01T00:00:00+00:00"}]},
    )

    await engine.recompute_trust_scores()

    clean_bd = await engine.get_trust(clean.id)
    redacted_bd = await engine.get_trust(redacted.id)

    assert redacted_bd["components"]["risk_penalty"] > 0
    assert redacted_bd["components"]["risk_penalty"] > clean_bd["components"]["risk_penalty"]


# ── 7 & 11. confidence clamped [0,1]; explainable breakdown ───────────


@pytest.mark.asyncio
async def test_confidence_clamped_and_breakdown_is_explainable(engine):
    await engine.store(content="A", memory_type="episodic", source="connector:email")
    await engine.store(content="B", memory_type="episodic", source=None)
    await engine.store(
        content="C",
        memory_type="episodic",
        source="connector:notes",
        pinned=True,
        metadata={"redaction_history": [{"secrets": ["x"], "at": "now"}]},
    )

    result = await engine.recompute_trust_scores()
    assert result["scored"] == 3
    assert isinstance(result["by_label"], dict)

    for label, count in result["by_label"].items():
        assert count >= 1

    memories = await engine.episodic.search(limit=1000)
    for m in memories:
        bd = await engine.get_trust(m.id)
        assert 0.0 <= bd["confidence"] <= 1.0
        assert isinstance(bd["explanation"], list)
        assert len(bd["explanation"]) > 0
        assert set(bd["components"].keys()) == {
            "source_score",
            "corroboration_score",
            "review_score",
            "recency_score",
            "risk_penalty",
        }


@pytest.mark.asyncio
async def test_get_trust_none_for_missing_memory(engine):
    assert await engine.get_trust("does-not-exist") is None


# ── 8. trust scoring does NOT change H-score / recall ranking ────────


@pytest.mark.asyncio
async def test_trust_recompute_does_not_change_recall_ranking(engine):
    await engine.store(
        content="Roadmap planning notes for the widget team",
        memory_type="episodic",
        source="connector:notes",
        importance=0.8,
    )
    await engine.store(
        content="Widget team roadmap update from last week",
        memory_type="episodic",
        source="connector:email",
        importance=0.3,
    )
    await engine.store(
        content="Unrelated grocery list",
        memory_type="episodic",
        source="connector:notes",
    )

    before = await engine.recall("widget team roadmap", top_k=10, reinforce=False)
    before_order = [m.id for m in before.memories]

    await engine.recompute_trust_scores()

    after = await engine.recall("widget team roadmap", top_k=10, reinforce=False)
    after_order = [m.id for m in after.memories]

    assert before_order == after_order
    assert before.scores == after.scores


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
async def test_api_trust_flow(api_client):
    r = await api_client.post(
        "/api/memories",
        json={
            "content": "Email about the roadmap with Dana",
            "memory_type": "episodic",
            "source": "connector:email",
            "metadata": {"from": "dana@acme.com"},
        },
    )
    assert r.status_code == 200
    memory_id = r.json()["id"]

    # static sub-paths must not be shadowed by /api/memories/{memory_id}
    r = await api_client.post("/api/memories/trust/recompute")
    assert r.status_code == 200
    body = r.json()
    assert "scored" in body
    assert body["scored"] >= 1

    r = await api_client.get(f"/api/memories/{memory_id}/trust")
    assert r.status_code == 200
    breakdown = r.json()
    assert breakdown["memory_id"] == memory_id
    assert "confidence" in breakdown
    assert "components" in breakdown

    r = await api_client.get("/api/memories/bogus-id-does-not-exist/trust")
    assert r.status_code == 404

    r = await api_client.get("/api/memories/low-trust")
    assert r.status_code == 200
    assert "low_trust" in r.json()

    # re-confirm the static routes still resolve (not shadowed)
    r = await api_client.get("/api/memories/low-trust?threshold=0.9&limit=5")
    assert r.status_code == 200
    r = await api_client.post("/api/memories/trust/recompute")
    assert r.status_code == 200


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
async def test_trust_mcp_tools_registered():
    from mcp.server.fastmcp import FastMCP

    from server.tools.register import register_all_tools

    mcp = FastMCP("test")
    register_all_tools(mcp, MemoryEngine(db_path=":memory:", embedder_mode="hash"))
    names = {t.name for t in await mcp.list_tools()}
    assert "memory_trust" in names
    assert "recompute_trust_scores" in names
    assert "list_low_trust_memories" in names


@pytest.mark.asyncio
async def test_trust_mcp_tools_flow(engine):
    from mcp.server.fastmcp import FastMCP

    from server.tools.trust import register as reg_trust

    mem = await engine.store(
        content="Memory for MCP trust tool exercise",
        memory_type="episodic",
        source="connector:email",
        metadata={"from": "erin@acme.com"},
    )

    mcp = FastMCP("test")
    reg_trust(mcp, engine)

    result = await mcp.call_tool("recompute_trust_scores", {})
    text = _tool_text(result)
    assert "Scored" in text

    result = await mcp.call_tool("memory_trust", {"memory_id": mem.id})
    text = _tool_text(result)
    assert "confidence" in text.lower()

    result = await mcp.call_tool("memory_trust", {"memory_id": "does-not-exist"})
    text = _tool_text(result)
    assert "No memory" in text

    result = await mcp.call_tool(
        "list_low_trust_memories", {"threshold": 1.0, "limit": 20}
    )
    text = _tool_text(result)
    assert "low-trust" in text.lower() or mem.id[:8] in text

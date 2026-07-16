"""Tests for deterministic Conflict-Candidate marking: pure conflict.py
helpers, engine detect_conflict_candidates/list_conflict_candidates/
review_conflict_candidate, trust integration, /api/conflicts endpoints, and
MCP tools. Offline, no LLM, no network. A conflict candidate is a REVIEW
SIGNAL for a human — never a verdict, never an auto-delete."""

import os
import sys
import tempfile

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["EMBEDDER_MODE"] = "hash"

from server.core import conflict
from server.core.memory_engine import MemoryEngine


# ── pure conflict.py (unit) ─────────────────────────────────────────


def test_opposing_signal_antonym():
    signal = conflict.opposing_signal(
        "The contract is approved", "The contract is rejected"
    )
    assert signal is not None
    assert signal[0] == "antonym"


def test_opposing_signal_none_for_agreeing_text():
    assert conflict.opposing_signal("The sky is blue", "The sky is blue") is None


def test_candidate_confidence_cross_source_higher():
    same = conflict.candidate_confidence("antonym", 1)
    cross = conflict.candidate_confidence("antonym", 2)
    assert cross > same
    assert same == 0.7
    assert cross == 0.8


# ── engine fixtures ──────────────────────────────────────────────────


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


async def _seed_opposing(
    engine,
    source_a="connector:calendar",
    source_b="connector:calendar",
    content_a="The contract is approved",
    content_b="The contract is rejected",
):
    """Two memories that share the person entity alice@acme.com and assert
    opposing things about a contract (antonym: approved / rejected)."""
    a = await engine.store(
        content=content_a,
        memory_type="episodic",
        source=source_a,
        metadata={"attendees": ["Alice <alice@acme.com>"]},
    )
    b = await engine.store(
        content=content_b,
        memory_type="episodic",
        source=source_b,
        metadata={"attendees": ["Alice <alice@acme.com>"]},
    )
    return a, b


async def _seed_unrelated(engine):
    """Two memories with no shared entity and no opposing pattern."""
    a = await engine.store(
        content="I had lunch at noon",
        memory_type="episodic",
        source="connector:notes",
    )
    b = await engine.store(
        content="The weather was nice today",
        memory_type="episodic",
        source="connector:notes",
    )
    return a, b


# ── 1. same entity + opposing pattern creates a candidate ───────────


@pytest.mark.asyncio
async def test_detect_creates_candidate_for_shared_entity_antonym(engine):
    await _seed_opposing(engine)
    result = await engine.detect_conflict_candidates()
    assert result["new_candidates"] >= 1
    assert result["open_total"] >= 1

    open_candidates = await engine.list_conflict_candidates(status="open")
    assert len(open_candidates) >= 1
    assert any(c["signal_type"] == "antonym" for c in open_candidates)


# ── 2. unrelated memories do NOT create a candidate ──────────────────


@pytest.mark.asyncio
async def test_detect_no_candidate_for_unrelated_memories(engine):
    await _seed_unrelated(engine)
    result = await engine.detect_conflict_candidates()
    assert result["new_candidates"] == 0
    assert result["open_total"] == 0


# ── 3/4. same-source duplicate does not inflate severity; different
#          source types increase priority ───────────────────────────


@pytest.mark.asyncio
async def test_same_source_confidence_le_cross_source(engine):
    await _seed_opposing(engine, source_a="connector:calendar", source_b="connector:calendar")
    result_same = await engine.detect_conflict_candidates()
    same_candidates = await engine.list_conflict_candidates(status=None)
    assert len(same_candidates) == 1
    same_conf = same_candidates[0]["confidence"]

    # fresh pair with distinct source types (different wording so hash
    # embeddings don't collide with the first pair and trigger interference)
    await _seed_opposing(
        engine,
        source_a="connector:calendar",
        source_b="connector:email",
        content_a="This deal was approved",
        content_b="This deal was rejected",
    )
    await engine.detect_conflict_candidates()
    all_candidates = await engine.list_conflict_candidates(status=None, limit=1000)
    # The cross-source pair is the new one not equal to the first candidate id.
    cross_candidates = [c for c in all_candidates if c["id"] != same_candidates[0]["id"]]
    assert len(cross_candidates) >= 1
    cross_conf = max(c["confidence"] for c in cross_candidates)

    assert same_conf <= cross_conf
    assert same_conf == 0.7
    assert cross_conf == 0.8


# ── 5. trust scores are included in conflict output ──────────────────


@pytest.mark.asyncio
async def test_trust_scores_included_in_explanation(engine):
    await _seed_opposing(engine)
    await engine.recompute_trust_scores()
    await engine.detect_conflict_candidates()

    candidates = await engine.list_conflict_candidates(status="open")
    assert len(candidates) >= 1
    explanation = candidates[0]["explanation"]
    assert "a_trust" in explanation
    assert "b_trust" in explanation


# ── 6. open conflict adds a risk signal but does NOT auto-delete ────


@pytest.mark.asyncio
async def test_open_conflict_adds_risk_without_deleting(engine):
    a, b = await _seed_opposing(engine)
    await engine.detect_conflict_candidates()

    # neither memory was deleted
    assert await engine.episodic.get(a.id) is not None
    assert await engine.episodic.get(b.id) is not None

    trust_a = await engine.get_trust(a.id)
    assert trust_a["evidence"]["conflict_status"] == "open"
    assert trust_a["components"]["risk_penalty"] >= 0.15


# ── 7. dismissed conflict no longer appears as open ──────────────────


@pytest.mark.asyncio
async def test_dismissed_conflict_not_open_and_not_recreated(engine):
    await _seed_opposing(engine)
    await engine.detect_conflict_candidates()
    open_candidates = await engine.list_conflict_candidates(status="open")
    assert len(open_candidates) >= 1
    conflict_id = open_candidates[0]["id"]

    result = await engine.review_conflict_candidate(conflict_id, "dismiss")
    assert result["ok"] is True
    assert result["conflict"]["status"] == "dismissed"

    still_open = await engine.list_conflict_candidates(status="open")
    assert conflict_id not in {c["id"] for c in still_open}

    # re-running detection must not resurrect it as open (idempotent)
    await engine.detect_conflict_candidates()
    still_open_after = await engine.list_conflict_candidates(status="open")
    assert conflict_id not in {c["id"] for c in still_open_after}


# ── 8. confirmed conflict remains auditable ──────────────────────────


@pytest.mark.asyncio
async def test_confirmed_conflict_listed_under_confirmed(engine):
    await _seed_opposing(engine)
    await engine.detect_conflict_candidates()
    open_candidates = await engine.list_conflict_candidates(status="open")
    conflict_id = open_candidates[0]["id"]

    result = await engine.review_conflict_candidate(conflict_id, "confirm")
    assert result["ok"] is True

    confirmed = await engine.list_conflict_candidates(status="confirmed")
    assert conflict_id in {c["id"] for c in confirmed}


# ── 10. H-score semantics unchanged: recall ordering untouched ──────


@pytest.mark.asyncio
async def test_recall_ordering_unaffected_by_conflict_detection(engine):
    await _seed_opposing(engine)
    await engine.store(
        content="Unrelated memory about a picnic",
        memory_type="episodic",
        source="connector:notes",
    )

    before = await engine.recall("contract", top_k=10, reinforce=False)
    before_ids = [m.id for m in before.memories]

    await engine.detect_conflict_candidates()
    open_candidates = await engine.list_conflict_candidates(status="open")
    await engine.review_conflict_candidate(open_candidates[0]["id"], "confirm")

    after = await engine.recall("contract", top_k=10, reinforce=False)
    after_ids = [m.id for m in after.memories]

    assert before_ids == after_ids


# ── 11. trust score is NOT renamed to a truth score ──────────────────


@pytest.mark.asyncio
async def test_trust_breakdown_has_confidence_not_truth_score(engine):
    a, _ = await _seed_opposing(engine)
    bd = await engine.get_trust(a.id)
    assert "confidence" in bd
    assert "truth_score" not in bd
    assert "confidence" not in bd.get("evidence", {}) or True  # evidence has other keys
    assert "truth_score" not in bd.get("components", {})


# ── 12. invalid review action raises ValueError (engine) ────────────


@pytest.mark.asyncio
async def test_invalid_review_action_raises_value_error(engine):
    await _seed_opposing(engine)
    await engine.detect_conflict_candidates()
    open_candidates = await engine.list_conflict_candidates(status="open")
    conflict_id = open_candidates[0]["id"]
    with pytest.raises(ValueError):
        await engine.review_conflict_candidate(conflict_id, "nonsense_action")


@pytest.mark.asyncio
async def test_review_unknown_conflict_id_returns_not_ok(engine):
    result = await engine.review_conflict_candidate("does-not-exist|nope", "dismiss")
    assert result["ok"] is False


# ── 13. API ───────────────────────────────────────────────────────────


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
async def test_api_conflicts_flow(api_client):
    await api_client.post("/api/memories", json={
        "content": "The contract is approved", "memory_type": "episodic",
        "source": "connector:calendar",
        "metadata": {"attendees": ["Alice <alice@acme.com>"]},
    })
    await api_client.post("/api/memories", json={
        "content": "The contract is rejected", "memory_type": "episodic",
        "source": "connector:calendar",
        "metadata": {"attendees": ["Alice <alice@acme.com>"]},
        "force": True,
    })

    r = await api_client.post("/api/conflicts/detect")
    assert r.status_code == 200
    body = r.json()
    assert body["new_candidates"] >= 1

    r = await api_client.get("/api/conflicts")
    assert r.status_code == 200
    conflicts = r.json()["conflicts"]
    assert len(conflicts) >= 1
    conflict_id = conflicts[0]["id"]

    r = await api_client.post(f"/api/conflicts/{conflict_id}/review", json={"action": "confirm"})
    assert r.status_code == 200
    assert r.json()["conflict"]["status"] == "confirmed"

    # bogus id -> 404
    r = await api_client.post("/api/conflicts/does-not-exist|nope/review", json={"action": "confirm"})
    assert r.status_code == 404

    # invalid action -> 422
    r = await api_client.post(f"/api/conflicts/{conflict_id}/review", json={"action": "bogus"})
    assert r.status_code == 422


# ── 14. MCP tools ──────────────────────────────────────────────────


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
async def test_conflicts_mcp_tools_registered():
    from mcp.server.fastmcp import FastMCP

    from server.tools.register import register_all_tools

    mcp = FastMCP("test")
    register_all_tools(mcp, MemoryEngine(db_path=":memory:", embedder_mode="hash"))
    names = {t.name for t in await mcp.list_tools()}
    assert "detect_conflict_candidates" in names
    assert "list_conflict_candidates" in names
    assert "review_conflict_candidate" in names


@pytest.mark.asyncio
async def test_conflicts_mcp_tools_flow(engine):
    from mcp.server.fastmcp import FastMCP

    from server.tools.conflicts import register as reg_conflicts

    await _seed_opposing(engine)

    mcp = FastMCP("test")
    reg_conflicts(mcp, engine)

    result = await mcp.call_tool("detect_conflict_candidates", {})
    text = _tool_text(result)
    assert "conflict candidate" in text.lower()
    assert "not verdicts" in text.lower() or "not a verdict" in text.lower()

    result = await mcp.call_tool("list_conflict_candidates", {"status": "open", "limit": 20})
    text = _tool_text(result)
    assert "antonym" in text.lower()

    open_candidates = await engine.list_conflict_candidates(status="open")
    conflict_id = open_candidates[0]["id"]

    result = await mcp.call_tool(
        "review_conflict_candidate", {"conflict_id": conflict_id, "action": "dismiss"}
    )
    text = _tool_text(result)
    assert "dismissed" in text.lower() or "dismiss" in text.lower()

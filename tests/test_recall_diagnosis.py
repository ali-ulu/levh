"""Why an empty recall returned nothing.

An empty result has several indistinguishable causes — no memories at all, a
filter that excludes every match, or a query whose wording appears nowhere —
and they call for different fixes (write something, widen the filter, rephrase
the query). The diagnosis turns the store's own counts into a plain reason, so
"recall is broken" becomes a specific answer.

Offline and deterministic: ``EMBEDDER_MODE=hash``, no model, no network.
"""

from __future__ import annotations

import os
import tempfile

import pytest
import pytest_asyncio

os.environ["EMBEDDER_MODE"] = "hash"

from server.core.memory_engine import MemoryEngine

FACT = "The production deploy branch is prod, not main"


@pytest_asyncio.fixture
async def engine():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    eng = MemoryEngine(db_path=path, embedder_mode="hash", short_term_max=50)
    await eng.initialize()
    yield eng
    await eng.shutdown()
    if os.path.exists(path):
        os.unlink(path)


@pytest.mark.asyncio
async def test_a_successful_recall_carries_no_diagnosis(engine):
    await engine.store(content=FACT, memory_type="episodic")
    result = await engine.recall("deploy branch", top_k=5, reinforce=False)
    assert result.memories
    assert result.diagnosis is None


@pytest.mark.asyncio
async def test_empty_store_is_named_without_a_filter(engine):
    result = await engine.recall("anything", top_k=5, reinforce=False)
    assert result.memories == []
    assert result.diagnosis is not None
    assert result.diagnosis.stored_total == 0
    assert result.diagnosis.empty_store is True
    assert "no memories are stored yet" in result.diagnosis.reasons


@pytest.mark.asyncio
async def test_a_filter_that_hides_every_match_is_named(engine):
    await engine.store(
        content=FACT, memory_type="episodic", project="alpha", importance=0.3
    )
    result = await engine.recall(
        "deploy branch", top_k=5, reinforce=False, project="beta"
    )
    assert result.memories == []
    diagnosis = result.diagnosis
    assert diagnosis is not None
    assert diagnosis.stored_total == 1
    assert diagnosis.in_scope_total == 0
    assert diagnosis.excluded_by_project == 1
    assert any("project='beta'" in r for r in diagnosis.reasons)


@pytest.mark.asyncio
async def test_min_importance_that_hides_every_match_is_named(engine):
    await engine.store(content=FACT, memory_type="episodic", importance=0.2)
    result = await engine.recall(
        "deploy branch", top_k=5, reinforce=False, min_importance=0.9
    )
    assert result.memories == []
    assert result.diagnosis is not None
    assert result.diagnosis.excluded_by_importance == 1
    assert any("min_importance=0.9" in r for r in result.diagnosis.reasons)


@pytest.mark.asyncio
async def test_a_query_with_no_shared_wording_is_named(engine):
    await engine.store(content=FACT, memory_type="episodic", project="alpha")
    result = await engine.recall(
        "zzzqqq", top_k=5, reinforce=False, project="beta"
    )
    assert result.memories == []
    assert result.diagnosis is not None
    assert result.diagnosis.stored_total == 1
    assert any("shares a word with the query" in r for r in result.diagnosis.reasons)


@pytest.mark.asyncio
async def test_an_all_stopword_query_is_named(engine):
    await engine.store(content=FACT, memory_type="episodic", project="alpha")
    result = await engine.recall(
        "the of is a an", top_k=5, reinforce=False, project="beta"
    )
    assert result.memories == []
    assert result.diagnosis is not None
    assert result.diagnosis.query_terms == []
    assert any("no content words" in r for r in result.diagnosis.reasons)


@pytest.mark.asyncio
async def test_the_wording_mismatch_reason_wins_over_the_filter_reason(engine):
    """If the query matches nothing anywhere, a filter is not what emptied the
    recall — saying so would send the user to the wrong fix."""
    await engine.store(content=FACT, memory_type="episodic", project="alpha")
    result = await engine.recall(
        "zzzqqq", top_k=5, reinforce=False, project="beta"
    )
    assert result.diagnosis is not None
    reasons = " ".join(result.diagnosis.reasons)
    assert "shares a word with the query" in reasons
    assert "excludes them all" not in reasons


@pytest.mark.asyncio
async def test_the_diagnosis_counts_the_whole_store_not_the_filtered_slice(engine):
    await engine.store(content=FACT, memory_type="episodic", project="alpha")
    await engine.store(
        content="Vacation requests go through the HR portal",
        memory_type="episodic",
        project="alpha",
    )
    result = await engine.recall("deploy branch", top_k=5, reinforce=False, project="beta")
    assert result.diagnosis is not None
    # stored_total is the whole table, not the scoped slice.
    assert result.diagnosis.stored_total == 2
    assert result.diagnosis.in_scope_total == 0
    assert result.diagnosis.excluded_by_project == 2


@pytest.mark.asyncio
async def test_the_rest_route_surfaces_the_diagnosis(engine):
    """The dashboard and SDKs read the JSON payload, not the dataclass — the
    diagnosis has to survive the round trip."""
    import server.api as api_mod
    from server.core import engine_provider
    from httpx import AsyncClient, ASGITransport
    from server.api import app

    api_mod._engine = engine
    engine_provider.set_engine(engine)
    api_mod._initialized = True

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/memories/recall", json={"query": "anything", "top_k": 5}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["memories"] == []
    assert body["diagnosis"] is not None
    assert body["diagnosis"]["stored_total"] == 0
    assert body["diagnosis"]["reasons"]

    api_mod._engine = None
    engine_provider.set_engine(None)


@pytest.mark.asyncio
async def test_the_mcp_tool_reports_why_the_recall_was_empty(engine):
    """The agent-facing surface is text, not JSON: an empty recall must carry
    the reason, not just "nothing found"."""
    from mcp.server.fastmcp import FastMCP
    from server.tools.register import register_all_tools

    mcp = FastMCP("test")
    register_all_tools(mcp, engine, profile="full")

    result = await mcp.call_tool("recall_memory", {"query": "anything", "top_k": 5})
    blocks = result[0] if isinstance(result, tuple) else result
    text = "\n".join(getattr(b, "text", "") for b in blocks)
    assert "no memories are stored yet" in text

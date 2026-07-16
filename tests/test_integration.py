"""StackMemory Integration Tests — End-to-end testing of MemoryEngine + API.

Covers:
  1. Memory lifecycle (store -> recall -> update -> forget)
  2. H(x,psi) scoring correctness
  3. Session management
  4. Consolidation flow
  5. Export / Import round-trip
  6. REST API endpoints via TestClient
  7. MCP tool registration
  8. Concurrent operations
  9. Edge cases and error handling
  10. P0 stability: session isolation, stats accuracy, frequency persistence,
     consolidation move semantics, decay direction, API 404s
"""

import asyncio
import os
import sys
import tempfile

import pytest
import pytest_asyncio

# Ensure project root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Force hash embedder globally for all tests — no torch, no OpenAI
os.environ["EMBEDDER_MODE"] = "hash"

from server.core.memory_engine import MemoryEngine
from server.core.types import MemoryType, SessionStatus


# -- Cleanup helper for API tests (Fix 10) --

async def reset_api_engine():
    """Reset global API engine to prevent test hang."""
    import server.api as api_mod
    if api_mod._engine is not None:
        await api_mod._engine.shutdown()
    api_mod._engine = None
    api_mod._initialized = False


# -- Fixtures --


@pytest_asyncio.fixture
async def engine():
    """Create an engine with a temporary SQLite DB and hash embedder."""
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    eng = MemoryEngine(db_path=db_path, embedder_mode="hash", short_term_max=10)
    await eng.initialize()
    yield eng
    await eng.shutdown()
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest_asyncio.fixture
async def populated_engine(engine):
    """Engine pre-loaded with test memories."""
    await engine.store(content="Python FastAPI web framework", importance=0.9, tags=["python", "web"], memory_type="episodic")
    await engine.store(content="JWT authentication token handling", importance=0.8, tags=["auth", "jwt"], memory_type="episodic")
    await engine.store(content="PostgreSQL database schema design", importance=0.7, tags=["database", "sql"], memory_type="episodic")
    await engine.store(content="React component state management", importance=0.6, tags=["frontend", "react"], memory_type="episodic")
    await engine.store(content="Docker container deployment", importance=0.5, tags=["devops", "docker"], memory_type="episodic")
    return engine


# -- 1. Memory Lifecycle --


@pytest.mark.asyncio
async def test_store_and_retrieve(engine):
    """Store a memory and verify it can be retrieved by ID."""
    mem = await engine.store(content="Test memory content", importance=0.8)
    assert mem.id is not None
    assert mem.content == "Test memory content"
    assert mem.importance == 0.8
    assert len(mem.embedding) > 0

    retrieved = await engine.get_memory(mem.id)
    assert retrieved is not None
    assert retrieved.id == mem.id
    assert retrieved.content == mem.content


@pytest.mark.asyncio
async def test_store_default_values(engine):
    """Verify default values when storing a memory."""
    mem = await engine.store(content="Defaults test")
    assert mem.importance == 0.5
    assert mem.tags == []
    assert mem.memory_type == MemoryType.SHORT_TERM
    assert mem.session_id is None
    assert mem.metadata["embedding_provenance"]["provider"] == "hash"
    assert set(mem.metadata) == {"embedding_provenance"}
    assert mem.frequency >= 0
    assert mem.hscore is None


@pytest.mark.asyncio
async def test_store_with_all_params(engine):
    """Store with all parameters set."""
    mem = await engine.store(
        content="Full params memory",
        importance=0.95,
        tags=["test", "integration"],
        session_id="session-123",
        memory_type="episodic",
        metadata={"source": "test"},
    )
    assert mem.importance == 0.95
    assert mem.tags == ["test", "integration"]
    assert mem.session_id == "session-123"
    assert mem.memory_type == MemoryType.EPISODIC
    assert mem.metadata["source"] == "test"


@pytest.mark.asyncio
async def test_update_memory(engine):
    """Update content, importance, and tags of an existing memory."""
    mem = await engine.store(content="Original content", importance=0.5, tags=["old"])
    updated = await engine.update_memory(
        mem.id, content="Updated content", importance=0.9, tags=["new", "updated"]
    )
    assert updated is not None
    assert updated.content == "Updated content"
    assert updated.importance == 0.9
    assert "new" in updated.tags
    assert "updated" in updated.tags
    assert len(updated.embedding) > 0


@pytest.mark.asyncio
async def test_update_nonexistent_memory(engine):
    """Updating a nonexistent memory returns None."""
    result = await engine.update_memory("nonexistent-id", content="new")
    assert result is None


@pytest.mark.asyncio
async def test_forget_memory(engine):
    """Forgetting a memory removes it from all layers."""
    mem = await engine.store(content="To be forgotten")
    assert await engine.get_memory(mem.id) is not None
    success = await engine.forget(mem.id)
    assert success is True
    assert await engine.get_memory(mem.id) is None


@pytest.mark.asyncio
async def test_forget_nonexistent(engine):
    """Forgetting nonexistent memory returns False."""
    success = await engine.forget("nonexistent-id")
    assert success is False


# -- 2. H(x,psi) Scoring --


@pytest.mark.asyncio
async def test_recall_ranking(populated_engine):
    """Recall returns memories ranked by H(x,psi) score (lower = better)."""
    result = await populated_engine.recall(query="JWT auth token", top_k=5)
    assert len(result.memories) > 0
    assert len(result.scores) == len(result.memories)
    contents = [m.content for m in result.memories]
    jwt_found = any("JWT" in c for c in contents)
    assert jwt_found, "JWT memory should be in recall results for 'JWT auth token'"
    for i in range(len(result.scores) - 1):
        assert result.scores[i] <= result.scores[i + 1]


@pytest.mark.asyncio
async def test_recall_with_filters(populated_engine):
    """Recall respects session_id and min_importance filters."""
    await populated_engine.store(
        content="Session-specific memory",
        importance=0.9,
        session_id="test-session",
        memory_type="episodic",
    )
    result = await populated_engine.recall(
        query="session",
        session_id="test-session",
        min_importance=0.8,
    )
    for mem in result.memories:
        assert mem.session_id == "test-session"
        assert mem.importance >= 0.8


@pytest.mark.asyncio
async def test_recall_empty_query(engine):
    """Recall with empty query should still work."""
    await engine.store(content="Some content", importance=0.5, memory_type="episodic")
    result = await engine.recall(query="", top_k=5)
    assert isinstance(result.memories, list)


@pytest.mark.asyncio
async def test_score_breakdown(populated_engine):
    """Score breakdown returns all H(x,psi) components and sums correctly."""
    memories = await populated_engine.list_memories(limit=1)
    if not memories:
        pytest.skip("No memories for breakdown")
    bd = await populated_engine.score_breakdown(memories[0].id, "JWT auth")
    assert bd is not None
    assert bd.total_hscore > 0
    assert bd.alpha_component > 0
    assert bd.beta_component >= 0
    assert bd.gamma_component > 0
    assert bd.delta_component >= 0
    total = bd.alpha_component + bd.beta_component + bd.gamma_component + bd.delta_component
    assert abs(total - bd.total_hscore) < 0.01, f"breakdown sum {total} != total_hscore {bd.total_hscore}"


@pytest.mark.asyncio
async def test_fresh_memory_scores_lower_than_stale():
    """Fresh memory should score lower (better) than stale memory (Fix 4)."""
    from server.core.hscore import HScoreCalculator
    calc = HScoreCalculator()
    # Fresh: decay_factor=1.0 -> (1-1.0)=0 -> beta penalty=0
    fresh_score = calc.compute(similarity=0.9, decay_factor=1.0, importance=0.8, frequency=5)
    # Stale: decay_factor=0.0 -> (1-0.0)=1.0 -> beta penalty=0.2
    stale_score = calc.compute(similarity=0.9, decay_factor=0.0, importance=0.8, frequency=5)
    assert fresh_score < stale_score, f"fresh={fresh_score} should be < stale={stale_score}"


@pytest.mark.asyncio
async def test_compute_batch_matches_scalar():
    """compute_batch should match individual compute calls (Fix 4)."""
    from server.core.hscore import HScoreCalculator
    calc = HScoreCalculator()
    sims = [0.9, 0.5, 0.1]
    decays = [1.0, 0.5, 0.0]
    imps = [0.8, 0.6, 0.4]
    freqs = [10, 5, 1]
    batch = calc.compute_batch(sims, decays, imps, freqs)
    for i in range(3):
        single = calc.compute(similarity=sims[i], decay_factor=decays[i],
                              importance=imps[i], frequency=freqs[i])
        assert abs(batch[i] - single) < 1e-6, f"batch[{i}]={batch[i]} != single={single}"


# -- 3. Session Management --


@pytest.mark.asyncio
async def test_create_and_list_sessions(engine):
    """Create sessions and list them."""
    s1 = await engine.create_session(name="Test Session 1")
    s2 = await engine.create_session(name="Test Session 2")
    assert s1.id is not None
    assert s2.id is not None
    assert s1.name == "Test Session 1"

    sessions = await engine.list_sessions()
    assert len(sessions) >= 2
    assert any(s.name == "Test Session 1" for s in sessions)


@pytest.mark.asyncio
async def test_end_session(engine):
    """End a session changes its status."""
    session = await engine.create_session(name="Endable Session")
    assert session.status == SessionStatus.ACTIVE

    ended = await engine.end_session(session.id)
    assert ended is not None
    assert ended.status == SessionStatus.ENDED
    assert ended.ended_at is not None


@pytest.mark.asyncio
async def test_end_nonexistent_session(engine):
    """Ending a nonexistent session returns None."""
    result = await engine.end_session("nonexistent-session")
    assert result is None


# -- 4. Consolidation --


@pytest.mark.asyncio
async def test_consolidate_moves_and_clears(engine):
    """Consolidation moves short-term to episodic AND removes from short-term (Fix 7)."""
    await engine.store(content="ST memory 1", importance=0.7, memory_type="short_term")
    await engine.store(content="ST memory 2", importance=0.6, memory_type="short_term")
    assert len(engine.short_term) == 2

    # First consolidate should move both
    count = await engine.consolidate()
    assert count == 2, f"Expected 2, got {count}"
    assert len(engine.short_term) == 0, "Short-term should be empty after consolidate"

    # Second consolidate should return 0
    count2 = await engine.consolidate()
    assert count2 == 0, f"Second consolidate should return 0, got {count2}"


@pytest.mark.asyncio
async def test_clear_short_term(engine):
    """Clear short-term memory."""
    await engine.store(content="Temporary", memory_type="short_term")
    assert len(engine.short_term) > 0
    cleared = engine.clear_short_term()
    assert cleared > 0
    assert len(engine.short_term) == 0


# -- 5. Export / Import --


@pytest.mark.asyncio
async def test_export_import_roundtrip(engine):
    """Export memories, clear, import back, verify integrity."""
    m1 = await engine.store(content="Export test 1", importance=0.7, tags=["export"], memory_type="episodic")
    m2 = await engine.store(content="Export test 2", importance=0.8, tags=["export"], memory_type="episodic")

    exported = await engine.export_memories()
    assert len(exported) >= 2

    await engine.forget(m1.id)
    await engine.forget(m2.id)
    assert await engine.get_memory(m1.id) is None

    count = await engine.import_memories(exported)
    assert count >= 2

    restored = await engine.list_memories()
    contents = [m.content for m in restored]
    assert "Export test 1" in contents
    assert "Export test 2" in contents


@pytest.mark.asyncio
async def test_export_filtered_by_session(engine):
    """Export with session_id filter."""
    session = await engine.create_session(name="Export Session")
    await engine.store(content="Session memory", session_id=session.id, memory_type="episodic")
    await engine.store(content="No session memory", memory_type="episodic")

    exported = await engine.export_memories(session_id=session.id)
    contents = [e["content"] for e in exported]
    assert "Session memory" in contents
    for e in exported:
        if e.get("session_id"):
            assert e["session_id"] == session.id


# -- 6. REST API Endpoints (via TestClient) --


@pytest_asyncio.fixture
async def api_engine():
    """Pre-initialize the global API engine on an isolated temporary DB."""
    import server.api as api_mod
    from server.core import engine_provider

    if api_mod._engine is not None:
        await api_mod._engine.shutdown()
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    api_mod._engine = MemoryEngine(
        db_path=db_path,
        embedder_mode="hash",
        short_term_max=50,
    )
    engine_provider.set_engine(api_mod._engine)
    await api_mod._engine.initialize()
    api_mod._initialized = True
    yield
    await reset_api_engine()
    engine_provider.set_engine(None)
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.mark.asyncio
async def test_api_health(api_engine):
    """GET /api/health returns ok."""
    from httpx import AsyncClient, ASGITransport
    from server.api import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_api_full_crud(api_engine):
    """Full CRUD cycle via REST API."""
    from httpx import AsyncClient, ASGITransport
    from server.api import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # CREATE
        resp = await client.post("/api/memories", json={
            "content": "API test memory",
            "importance": 0.8,
            "tags": ["api", "test"],
            "memory_type": "episodic",
        })
        assert resp.status_code == 200
        mem = resp.json()
        mem_id = mem["id"]
        assert mem["content"] == "API test memory"

        # READ (list)
        resp = await client.get("/api/memories")
        assert resp.status_code == 200
        memories = resp.json()
        assert len(memories) > 0

        # READ (single)
        resp = await client.get(f"/api/memories/{mem_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == mem_id

        # UPDATE
        resp = await client.put(f"/api/memories/{mem_id}", json={
            "content": "Updated API memory",
            "importance": 0.95,
        })
        assert resp.status_code == 200
        assert resp.json()["content"] == "Updated API memory"

        # RECALL
        resp = await client.post("/api/memories/recall", json={
            "query": "API test",
            "top_k": 5,
        })
        assert resp.status_code == 200
        recall_data = resp.json()
        assert "memories" in recall_data
        assert "scores" in recall_data

        # DELETE
        resp = await client.delete(f"/api/memories/{mem_id}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True


@pytest.mark.asyncio
async def test_api_404_memory_not_found(api_engine):
    """Nonexistent memory returns 404 (Fix 9)."""
    from httpx import AsyncClient, ASGITransport
    from server.api import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/memories/nonexistent-id")
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_api_404_session_not_found(api_engine):
    """Nonexistent session returns 404 (Fix 9)."""
    from httpx import AsyncClient, ASGITransport
    from server.api import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/sessions/nonexistent-id")
        assert resp.status_code == 404

        resp = await client.patch("/api/sessions/nonexistent-id/end")
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_api_sessions_crud(api_engine):
    """Session CRUD via REST API."""
    from httpx import AsyncClient, ASGITransport
    from server.api import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/sessions", json={"name": "API Test Session"})
        assert resp.status_code == 200
        session = resp.json()
        session_id = session["id"]
        assert session["name"] == "API Test Session"
        assert session["status"] == "active"

        resp = await client.get("/api/sessions")
        assert resp.status_code == 200

        resp = await client.patch(f"/api/sessions/{session_id}/end")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ended"


@pytest.mark.asyncio
async def test_api_stats(api_engine):
    """GET /api/stats returns valid statistics."""
    from httpx import AsyncClient, ASGITransport
    from server.api import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/stats")
        assert resp.status_code == 200
        stats = resp.json()
        assert "total_memories" in stats
        assert "short_term_count" in stats
        assert "episodic_count" in stats
        assert "avg_hscore" in stats
        assert "sessions_count" in stats


@pytest.mark.asyncio
async def test_api_export_import(api_engine):
    """Export and import via REST API."""
    from httpx import AsyncClient, ASGITransport
    from server.api import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/api/memories", json={"content": "Export API test", "memory_type": "episodic"})

        resp = await client.post("/api/memories/export")
        assert resp.status_code == 200
        export_data = resp.json()
        assert export_data["count"] > 0

        resp = await client.post("/api/memories/import", json={"data": export_data["data"]})
        assert resp.status_code == 200
        assert resp.json()["imported"] + resp.json()["duplicates"] > 0


# -- 7. MCP Tool Registration --


@pytest.mark.asyncio
async def test_mcp_tools_registered():
    """All 15 MCP tools are registered with correct names and schemas."""
    from server.tools.register import register_all_tools
    from server.core.memory_engine import MemoryEngine

    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        pytest.skip("mcp not installed")

    mcp = FastMCP("test-stackmemory")
    engine = MemoryEngine(db_path=":memory:", embedder_mode="hash")
    register_all_tools(mcp, engine)

    tools = await mcp.list_tools()
    tool_names = {t.name for t in tools}

    expected_tools = [
        "store_memory", "recall_memory", "forget_memory", "search_memory",
        "update_memory", "list_memories", "get_memory_stats", "consolidate_memories",
        "clear_short_term", "set_importance", "get_context",
        "create_session", "end_session", "export_memories", "import_memories",
    ]
    for name in expected_tools:
        assert name in tool_names, f"Missing MCP tool: {name}"

    for tool in tools:
        assert tool.description is not None and len(tool.description) > 10


# -- 8. Concurrent Operations --


@pytest.mark.asyncio
async def test_concurrent_store(engine):
    """Multiple concurrent store operations should not corrupt data."""
    tasks = [
        engine.store(content=f"Concurrent memory {i}", importance=0.5)
        for i in range(20)
    ]
    results = await asyncio.gather(*tasks)
    assert len(results) == 20
    ids = {r.id for r in results}
    assert len(ids) == 20


@pytest.mark.asyncio
async def test_concurrent_recall(engine):
    """Multiple concurrent recall operations should not crash."""
    for i in range(10):
        await engine.store(content=f"Recall test {i}", importance=0.5, memory_type="episodic")

    tasks = [engine.recall(query=f"query {i}") for i in range(10)]
    results = await asyncio.gather(*tasks)
    assert len(results) == 10


# -- 9. Edge Cases --


@pytest.mark.asyncio
async def test_recall_with_no_memories(engine):
    """Recall from empty store returns empty result."""
    result = await engine.recall(query="anything", top_k=5)
    assert len(result.memories) == 0
    assert len(result.scores) == 0


@pytest.mark.asyncio
async def test_list_memories_with_filters(engine):
    """List with type, tag, and importance filters."""
    await engine.store(content="Important Python", importance=0.9, tags=["python", "important"], memory_type="episodic")
    await engine.store(content="Unimportant JS", importance=0.2, tags=["js"], memory_type="episodic")

    python_memories = await engine.list_memories(tag="python")
    assert all("python" in m.tags for m in python_memories)

    important = await engine.list_memories(min_importance=0.8)
    assert all(m.importance >= 0.8 for m in important)

    episodic = await engine.list_memories(memory_type="episodic")
    assert all(m.memory_type == MemoryType.EPISODIC for m in episodic)


@pytest.mark.asyncio
async def test_set_importance(engine):
    """Set importance clamps to [0, 1]."""
    mem = await engine.store(content="Importance test", importance=0.5)
    success = await engine.set_importance(mem.id, 1.5)
    assert success is True
    updated = await engine.get_memory(mem.id)
    assert updated.importance == 1.0

    success = await engine.set_importance(mem.id, -0.5)
    assert success is True
    updated = await engine.get_memory(mem.id)
    assert updated.importance == 0.0


@pytest.mark.asyncio
async def test_get_context(engine):
    """Context window returns recent short-term memories."""
    await engine.store(content="Context line 1", memory_type="short_term")
    await engine.store(content="Context line 2", memory_type="short_term")
    await engine.store(content="Context line 3", memory_type="short_term")

    ctx = await engine.get_context(max_tokens=500)
    assert "Context line" in ctx


@pytest.mark.asyncio
async def test_memory_frequency_tracking(engine):
    """Frequency increments on touch and consolidation."""
    mem = await engine.store(content="Frequency test", memory_type="short_term")
    freq_at_store = mem.frequency
    assert freq_at_store >= 0

    await engine.update_memory(mem.id, content="Updated frequency test")
    updated = await engine.get_memory(mem.id)
    assert updated.frequency >= 1

    await engine.consolidate()
    after = await engine.get_memory(mem.id)
    assert after.frequency >= updated.frequency


@pytest.mark.asyncio
async def test_list_with_offset(engine):
    """Pagination with offset works correctly."""
    for i in range(5):
        await engine.store(content=f"Page item {i}", memory_type="episodic")

    page1 = await engine.list_memories(limit=2, offset=0)
    page2 = await engine.list_memories(limit=2, offset=2)
    ids1 = {m.id for m in page1}
    ids2 = {m.id for m in page2}
    assert len(ids1.intersection(ids2)) == 0


@pytest.mark.asyncio
async def test_stats_accuracy(populated_engine):
    """Stats reflect actual memory counts (Fix 6)."""
    stats = await populated_engine.get_stats()
    assert stats.total_memories == stats.episodic_count, \
        f"total={stats.total_memories} should equal episodic={stats.episodic_count}"
    assert stats.episodic_count >= 5
    assert 0 <= stats.avg_hscore <= 1
    assert 0 <= stats.avg_importance <= 1


@pytest.mark.asyncio
async def test_store_special_characters(engine):
    """Memory with special/unicode characters stores and recalls correctly."""
    content = "Turkish: çışöğü, Emoji test: Memory, Math: alpha squared"
    mem = await engine.store(content=content, importance=0.7, memory_type="episodic")
    retrieved = await engine.get_memory(mem.id)
    assert retrieved.content == content

    result = await engine.recall(query="Turkish çışöğü", top_k=5)
    contents = [m.content for m in result.memories]
    assert any("Turkish" in c for c in contents)


# -- 10. P0 Stability Tests --


@pytest.mark.asyncio
async def test_strict_session_isolation(engine):
    """Session-specific recall returns ONLY that session's memories (Fix 5)."""
    m1 = await engine.store(content="Session 1 memory", session_id="s1", memory_type="episodic")
    m2 = await engine.store(content="Session 2 memory", session_id="s2", memory_type="episodic")
    m3 = await engine.store(content="No session memory", session_id=None, memory_type="episodic")

    result = await engine.recall(query="memory", session_id="s1")
    for mem in result.memories:
        assert mem.session_id == "s1", \
            f"Expected session_id='s1', got '{mem.session_id}' — strict isolation violation"


@pytest.mark.asyncio
async def test_stats_no_double_counting(engine):
    """total_memories should equal episodic_count, not st + ep (Fix 6)."""
    await engine.store(content="Memory A", memory_type="episodic")
    await engine.store(content="Memory B", memory_type="episodic")

    stats = await engine.get_stats()
    assert stats.total_memories == 2, \
        f"Expected 2, got {stats.total_memories} (episodic={stats.episodic_count}, st={stats.short_term_count})"


@pytest.mark.asyncio
async def test_consolidate_removes_from_short_term(engine):
    """After consolidate, short_term deque should be empty (Fix 7)."""
    await engine.store(content="Short-term 1", memory_type="short_term")
    await engine.store(content="Short-term 2", memory_type="short_term")
    assert len(engine.short_term) == 2

    count = await engine.consolidate()
    assert count == 2
    assert len(engine.short_term) == 0, "Short-term should be empty after consolidation"

    count2 = await engine.consolidate()
    assert count2 == 0, "Second consolidate should return 0"


@pytest.mark.asyncio
async def test_frequency_persists_on_recall(engine):
    """Recall should increment frequency and persist to SQLite (Fix 8)."""
    mem = await engine.store(content="Recall frequency test", importance=0.7, memory_type="episodic")
    before = await engine.get_memory(mem.id)
    freq_before = before.frequency

    # Recall should increment frequency
    await engine.recall(query="Recall frequency test")
    after = await engine.get_memory(mem.id)
    assert after.frequency > freq_before, \
        f"Frequency should have increased: before={freq_before}, after={after.frequency}"


@pytest.mark.asyncio
async def test_context_strict_session_isolation(engine):
    """get_context should also enforce strict session isolation (Fix 5)."""
    await engine.store(content="Context S1", session_id="s1", memory_type="short_term")
    await engine.store(content="Context S2", session_id="s2", memory_type="short_term")
    await engine.store(content="Context None", session_id=None, memory_type="short_term")

    ctx = await engine.get_context(session_id="s1")
    # Should only contain s1 memories, not s2 or None
    assert "Context S1" in ctx
    assert "Context S2" not in ctx
    assert "Context None" not in ctx

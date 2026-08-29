"""Deleting a session through the API, and what happens to its memories.

There was no DELETE, so removing a session meant opening ``stackmemory.db``
and running SQL by hand — a write path that goes around the product's own API
and leaves nothing anyone can read back. These tests pin the route and, more
importantly, pin that tidying up a session cannot quietly take memories with
it.
"""

from __future__ import annotations

import os
import tempfile

import pytest
import pytest_asyncio

from server.core.memory_engine import MemoryEngine

ONE = "Atlas production database uses PostgreSQL with daily backups"
TWO = "The billing service retries failed webhooks five times before parking them"


@pytest_asyncio.fixture
async def engine():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    eng = MemoryEngine(db_path=path, embedder_mode="hash", short_term_max=20)
    await eng.initialize()
    yield eng
    await eng.shutdown()
    if os.path.exists(path):
        os.unlink(path)


@pytest_asyncio.fixture
async def api_client():
    from httpx import ASGITransport, AsyncClient
    import server.api as api_mod

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    if api_mod._engine is not None:
        await api_mod._engine.shutdown()
    api_mod._engine = MemoryEngine(db_path=path, embedder_mode="hash", short_term_max=20)
    await api_mod._engine.initialize()
    api_mod._initialized = True
    transport = ASGITransport(app=api_mod.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, api_mod._engine
    await api_mod._engine.shutdown()
    api_mod._engine = None
    api_mod._initialized = False
    if os.path.exists(path):
        os.unlink(path)


async def _session_with(eng, *contents):
    session = await eng.create_session(name="probe")
    for content in contents:
        await eng.store(content=content, session_id=session.id, memory_type="episodic")
    return session


@pytest.mark.asyncio
async def test_an_empty_session_is_deleted(engine):
    session = await engine.create_session(name="mistake")

    result = await engine.delete_session(session.id)

    assert result["ok"] is True
    assert await engine.get_session(session.id) is None


@pytest.mark.asyncio
async def test_a_session_with_memories_is_refused_by_default(engine):
    session = await _session_with(engine, ONE, TWO)

    result = await engine.delete_session(session.id)

    # The default must not guess. Tidying up a session is not a decision to
    # delete two memories, and the count is what a caller needs to decide.
    assert result["ok"] is False
    assert result["error"] == "session_not_empty"
    assert result["memory_count"] == 2
    assert await engine.get_session(session.id) is not None
    assert await engine.episodic.count() == 2


@pytest.mark.asyncio
async def test_detach_keeps_the_memories_and_they_stay_recallable(engine):
    session = await _session_with(engine, ONE, TWO)

    result = await engine.delete_session(session.id, memories="detach")

    assert result["memories_detached"] == 2
    assert result["memories_deleted"] == 0
    assert await engine.get_session(session.id) is None
    assert await engine.episodic.count() == 2

    # Detaching must not evict them from the caches recall scores from —
    # that would be the same data loss by a slower route.
    recalled = await engine.recall(ONE, top_k=5)
    assert any(m.content == ONE for m in recalled.memories)

    # And nothing may still point at a session id that no longer resolves.
    for memory in await engine.episodic.search(limit=10):
        assert memory.session_id is None


@pytest.mark.asyncio
async def test_delete_removes_the_memories_through_the_normal_cascade(engine):
    session = await _session_with(engine, ONE, TWO)

    result = await engine.delete_session(session.id, memories="delete")

    assert result["memories_deleted"] == 2
    assert await engine.get_session(session.id) is None
    assert await engine.episodic.count() == 0
    assert (await engine.recall(ONE, top_k=5)).memories == []


@pytest.mark.asyncio
async def test_an_unknown_session_and_an_unknown_policy_are_told_apart(engine):
    session = await engine.create_session(name="probe")

    assert (await engine.delete_session("nope"))["error"] == "not_found"
    assert (await engine.delete_session(session.id, memories="purge"))["error"] == (
        "invalid_memories_policy"
    )
    # A rejected policy must change nothing, including for a valid session.
    assert await engine.get_session(session.id) is not None


@pytest.mark.asyncio
async def test_the_route_answers_404_409_400_and_200(api_client):
    client, eng = api_client

    assert (await client.delete("/api/sessions/nope")).status_code == 404

    session = await _session_with(eng, ONE)
    refused = await client.delete(f"/api/sessions/{session.id}")
    assert refused.status_code == 409
    assert refused.json()["detail"]["memory_count"] == 1

    bad = await client.delete(f"/api/sessions/{session.id}?memories=purge")
    assert bad.status_code == 400

    ok = await client.delete(f"/api/sessions/{session.id}?memories=detach")
    assert ok.status_code == 200, ok.text
    assert ok.json()["memories_detached"] == 1
    assert (await client.get(f"/api/sessions/{session.id}")).status_code == 404


@pytest.mark.asyncio
async def test_deleting_a_session_leaves_other_sessions_alone(engine):
    keep = await _session_with(engine, ONE)
    drop = await _session_with(engine, TWO)

    await engine.delete_session(drop.id, memories="delete")

    assert await engine.get_session(keep.id) is not None
    remaining = await engine.episodic.search(limit=10)
    assert [m.content for m in remaining] == [ONE]
    assert remaining[0].session_id == keep.id

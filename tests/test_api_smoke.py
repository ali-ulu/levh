"""API endpoint smoke test — end-to-end pass over the REST surface.

Runs under pytest (previously this file was a standalone __main__ script, so
`pytest -q` collected nothing from it and the smoke coverage never ran in CI).
Uses the hash embedder + a temporary DB so it needs no torch / OpenAI / network.
"""

import os
import sys
import tempfile

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Force hash embedder — no torch, no internet, no OpenAI
os.environ["EMBEDDER_MODE"] = "hash"

from server.core.memory_engine import MemoryEngine


@pytest_asyncio.fixture
async def api_client():
    """Point the global API engine at a throwaway DB, yield an ASGI client."""
    import server.api as api_mod

    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)

    if api_mod._engine is not None:
        await api_mod._engine.shutdown()
    api_mod._engine = MemoryEngine(
        db_path=db_path, embedder_mode="hash", short_term_max=50
    )
    await api_mod._engine.initialize()
    api_mod._initialized = True

    from server.api import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    if api_mod._engine is not None:
        await api_mod._engine.shutdown()
    api_mod._engine = None
    api_mod._initialized = False
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.mark.asyncio
async def test_api_smoke_full_surface(api_client):
    c = api_client

    # 1. Health
    r = await c.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"

    # 2. Store
    r = await c.post(
        "/api/memories",
        json={
            "content": "API smoke test",
            "importance": 0.8,
            "tags": ["smoke"],
            "memory_type": "episodic",
        },
    )
    assert r.status_code == 200
    mem_id = r.json()["id"]

    # 3. List
    r = await c.get("/api/memories")
    assert r.status_code == 200 and len(r.json()) > 0

    # 4. Get single
    r = await c.get(f"/api/memories/{mem_id}")
    assert r.status_code == 200 and r.json()["id"] == mem_id

    # 5. Update
    r = await c.put(
        f"/api/memories/{mem_id}",
        json={"content": "Updated smoke", "importance": 0.95},
    )
    assert r.status_code == 200 and r.json()["content"] == "Updated smoke"

    # 6. Recall
    r = await c.post("/api/memories/recall", json={"query": "smoke test", "top_k": 3})
    assert r.status_code == 200 and "memories" in r.json()

    # 7. Stats
    r = await c.get("/api/stats")
    assert r.status_code == 200 and "total_memories" in r.json()

    # 8. Sessions
    r = await c.post("/api/sessions", json={"name": "Smoke Session"})
    assert r.status_code == 200
    sid = r.json()["id"]

    r = await c.get("/api/sessions")
    assert r.status_code == 200

    r = await c.patch(f"/api/sessions/{sid}/end")
    assert r.status_code == 200 and r.json()["status"] == "ended"

    # 9. Export
    r = await c.post("/api/memories/export")
    assert r.status_code == 200 and r.json()["count"] > 0
    export_data = r.json()["data"]

    # 10. Import
    r = await c.post("/api/memories/import", json={"data": export_data[:1]})
    assert r.status_code == 200
    assert r.json()["imported"] + r.json()["duplicates"] > 0

    # 11. Consolidate
    r = await c.post("/api/memories/consolidate")
    assert r.status_code == 200

    # 12. Delete
    r = await c.delete(f"/api/memories/{mem_id}")
    assert r.status_code == 200 and r.json()["deleted"] is True

    # 13. 404 on missing memory
    r = await c.get("/api/memories/nonexistent-id")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_api_store_rejects_invalid_memory_type(api_client):
    """Invalid memory_type is a client error (422), not a 500."""
    r = await api_client.post(
        "/api/memories",
        json={"content": "bad type", "memory_type": "not_a_real_type"},
    )
    assert r.status_code == 422

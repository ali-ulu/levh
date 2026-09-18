"""Readiness probe contract (issue #145).

``/api/health`` only proves the process is up. Docker HEALTHCHECK pointed at
it, so a container with a locked database or a broken embedder stayed
"healthy" forever. ``/api/readyz`` exercises the two dependencies a memory
request actually needs - the SQLite connection and the embedder - and these
tests pin that contract.
"""

from __future__ import annotations

import os
import sys
import tempfile

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("EMBEDDER_MODE", "hash")

from server.core.memory_engine import MemoryEngine


@pytest_asyncio.fixture
async def ready_client():
    """ASGI client over an app wired to a throwaway engine."""
    import server.api as api_mod

    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    if api_mod._engine is not None:
        await api_mod._engine.shutdown()
    api_mod._engine = MemoryEngine(db_path=db_path, embedder_mode="hash")
    await api_mod._engine.initialize()

    from server.api import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, api_mod._engine

    await api_mod._engine.shutdown()
    api_mod._engine = None
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.mark.asyncio
async def test_readyz_reports_ready_with_healthy_dependencies(ready_client):
    client, engine = ready_client
    r = await client.get("/api/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["checks"]["db"] == "ok"
    assert body["checks"]["embedder"].startswith("ok")


@pytest.mark.asyncio
async def test_readyz_reports_not_ready_when_db_is_closed(ready_client):
    client, engine = ready_client
    # Close the underlying connection out from under the engine - the state a
    # locked/half-closed store leaves the process in.
    await engine.db.close()
    r = await client.get("/api/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["db"].startswith("error")
    # The embedder check is independent and still reports.
    assert body["checks"]["embedder"].startswith("ok")


@pytest.mark.asyncio
async def test_readyz_reports_not_ready_when_embedder_cannot_load(ready_client, monkeypatch):
    """A broken embedder (e.g. local mode without torch) must fail readiness."""
    client, engine = ready_client

    class Broken:
        @property
        def mode(self):
            raise RuntimeError("model load failed")

        dimension = 384

    orig_property = type(engine).embedder
    # Replace the lazy property with a raising descriptor on the instance path.
    monkeypatch.setattr(
        type(engine), "embedder",
        property(lambda self: (_ for _ in ()).throw(RuntimeError("model load failed"))),
    )
    r = await client.get("/api/readyz")
    body = r.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["embedder"].startswith("error")
    # DB is unaffected.
    assert body["checks"]["db"] == "ok"


@pytest.mark.asyncio
async def test_readyz_is_exempt_from_the_token_gate(ready_client, monkeypatch):
    """A HEALTHCHECK holds no token - the probe must answer 200 anyway."""
    client, _engine = ready_client
    monkeypatch.setenv("LEVH_TOKEN", "secret-token-value")
    r = await client.get("/api/readyz")
    assert r.status_code == 200
    # Same request without the token on a guarded path still fails closed.
    r2 = await client.get("/api/stats")
    assert r2.status_code in (401, 403)


@pytest.mark.asyncio
async def test_readyz_never_returns_memory_content(ready_client):
    """The unauthenticated surface must stay content-free."""
    client, engine = ready_client
    await engine.store(content="secret-canary-value-xyz", project="p")
    r = await client.get("/api/readyz")
    assert "secret-canary-value-xyz" not in r.text

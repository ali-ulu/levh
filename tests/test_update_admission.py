"""Content updates go through the admission gate (P0-3).

Storing a secret was redacted; updating a memory to contain the same secret was
not. The gate has to apply on whichever path the caller reaches — engine, REST
PUT, or the MCP update tool — or the security boundary depends on the endpoint.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from server.core.memory_engine import MemoryEngine

SECRET = "AKIAIOSFODNN7EXAMPLE"


@pytest_asyncio.fixture
async def engine(tmp_path):
    eng = MemoryEngine(db_path=str(tmp_path / "update.db"), embedder_mode="hash")
    await eng.initialize()
    try:
        yield eng
    finally:
        await eng.shutdown()


@pytest.mark.asyncio
async def test_updated_content_is_redacted_before_persistence(engine):
    memory = await engine.store("A harmless note about the build", memory_type="episodic")

    updated = await engine.update_memory(memory.id, content=f"aws key {SECRET} rotated")

    assert updated is not None
    assert SECRET not in updated.content
    assert "[REDACTED]" in updated.content
    # Persisted copy, not just the returned object.
    reloaded = await engine.get_memory(memory.id)
    assert SECRET not in reloaded.content
    admission = reloaded.metadata["admission"]
    assert admission["redacted"] is True
    assert admission["on_update"] is True
    assert "aws_access_key" in admission["secrets"]


@pytest.mark.asyncio
async def test_the_raw_secret_never_reaches_the_embedder(engine, monkeypatch):
    """Deleting the text later cannot un-embed it, so the redaction has to
    happen before the embedding call, not after."""
    memory = await engine.store("Another harmless note", memory_type="episodic")
    embedded: list[str] = []
    original = engine.embedder.embed

    async def spy(text: str):
        embedded.append(text)
        return await original(text)

    monkeypatch.setattr(engine.embedder, "embed", spy)
    await engine.update_memory(memory.id, content=f"token {SECRET} here")

    assert embedded, "update did not embed anything"
    assert all(SECRET not in text for text in embedded)


@pytest.mark.asyncio
async def test_metadata_only_update_does_not_run_the_gate(engine):
    """No new content means nothing to judge — and an existing admission
    record must survive untouched."""
    memory = await engine.admit_memory("A note worth keeping around", importance=0.4)
    before = (await engine.get_memory(memory["memory"]["id"]))
    original_admission = dict(before.metadata["admission"])

    updated = await engine.update_memory(
        before.id, importance=0.9, tags=["x"], pinned=True
    )

    assert updated.importance == 0.9
    assert updated.pinned is True
    assert updated.content == before.content
    assert updated.metadata["admission"] == original_admission
    assert "on_update" not in updated.metadata["admission"]


@pytest.mark.asyncio
async def test_a_memory_is_excluded_from_its_own_duplicate_probe(engine):
    """Without the exclusion, every content update would score ~1.0 similarity
    against itself and be judged a duplicate."""
    memory = await engine.store(
        "The deploy pipeline runs on Tuesdays", memory_type="episodic"
    )

    updated = await engine.update_memory(
        memory.id, content="The deploy pipeline runs on Tuesdays and Fridays"
    )

    assert updated is not None
    admission = updated.metadata["admission"]
    assert admission["action"] != "reject"
    assert "duplicate_exact" not in admission["reason_codes"]


@pytest.mark.asyncio
async def test_too_short_content_is_refused(engine):
    memory = await engine.store("A perfectly fine memory", memory_type="episodic")
    assert await engine.update_memory(memory.id, content="x", min_length=5) is None
    # The stored content is untouched by the refused update.
    assert (await engine.get_memory(memory.id)).content == "A perfectly fine memory"


@pytest.mark.asyncio
async def test_gate_bypass_is_recorded_as_an_override(engine):
    memory = await engine.store("Some note", memory_type="episodic")

    updated = await engine.update_memory(
        memory.id, content=f"raw {SECRET} kept deliberately", use_gate=False
    )

    assert SECRET in updated.content, "an explicit override must actually bypass"
    assert updated.metadata["admission"]["forced"] is True
    assert updated.metadata["admission"]["action"] == "bypassed"


@pytest.mark.asyncio
async def test_rest_put_redacts(tmp_path, monkeypatch):
    """The REST path was one of the two ways to smuggle a raw secret in."""
    import server.api as api_mod

    eng = MemoryEngine(db_path=str(tmp_path / "rest.db"), embedder_mode="hash")
    await eng.initialize()
    monkeypatch.setattr(api_mod, "_engine", eng)
    try:
        memory = await eng.store("REST note", memory_type="episodic")
        transport = ASGITransport(app=api_mod.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                f"/api/memories/{memory.id}", json={"content": f"key {SECRET} here"}
            )
        assert resp.status_code == 200
        assert SECRET not in resp.text
        assert SECRET not in (await eng.get_memory(memory.id)).content
    finally:
        await eng.shutdown()
        api_mod._engine = None


@pytest.mark.asyncio
async def test_mcp_update_tool_redacts(engine):
    """And the MCP tool was the other."""
    from server.tools.update import register

    captured = {}

    class _FakeMCP:
        def tool(self, *_args, **_kwargs):
            def decorator(fn):
                captured[fn.__name__] = fn
                return fn

            return decorator

    register(_FakeMCP(), engine)
    memory = await engine.store("MCP note", memory_type="episodic")

    await captured["update_memory"](memory_id=memory.id, content=f"key {SECRET} here")

    assert SECRET not in (await engine.get_memory(memory.id)).content

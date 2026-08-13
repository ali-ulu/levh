"""Tests for Hard-Delete + Redaction Audit: engine.audit_deletion /
purge_memory / audit_secrets / redact_memory / redact_all_secrets,
their /api/memories/... REST endpoints, and the audit_secrets /
redact_secrets / purge_memory MCP tools. Offline & deterministic —
EMBEDDER_MODE=hash."""

import os
import sys
import tempfile

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["EMBEDDER_MODE"] = "hash"

from server.core.memory_engine import MemoryEngine

SECRET_CONTENT = "config note: password=hunter2 for the staging db"
CLEAN_CONTENT = "Random note about what I had for lunch today"
# A standalone-pattern secret (github token) rather than a labelled
TOKEN_CONTENT = "leaked github token ghp_1234567890abcdef1234567890abcdef1234 in the log"
# key=value assignment shape — redaction must be idempotent here too: the
# assignment regex skips an already-redacted value so a second pass is a no-op.
ASSIGN_CONTENT = "service config with password=hunter2 in the file"


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


# ── audit_deletion ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_audit_deletion_present_then_absent(engine):
    m = await engine.store(CLEAN_CONTENT, memory_type="episodic")

    audit = await engine.audit_deletion(m.id)
    assert audit["memory_id"] == m.id
    assert audit["residue"]["episodic"] is True
    assert audit["fully_absent"] is False

    await engine.forget(m.id)

    audit2 = await engine.audit_deletion(m.id)
    assert audit2["fully_absent"] is True
    assert audit2["residue"]["short_term"] is False
    assert audit2["residue"]["vector_store"] is False
    assert audit2["residue"]["episodic"] is False


# ── purge_memory ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_purge_memory_existing(engine):
    m = await engine.store(CLEAN_CONTENT, memory_type="episodic")

    result = await engine.purge_memory(m.id)
    assert result["existed"] is True
    assert result["purged"] is True
    assert all(v is False for v in result["residue"].values())
    assert await engine.episodic.get(m.id) is None


@pytest.mark.asyncio
async def test_purge_memory_nonexistent(engine):
    result = await engine.purge_memory("does-not-exist")
    assert result["existed"] is False


# ── audit_secrets ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_audit_secrets_flags_only_secret_memory(engine):
    secret_mem = await engine.store(SECRET_CONTENT, memory_type="episodic")
    await engine.store(CLEAN_CONTENT, memory_type="episodic")

    audit = await engine.audit_secrets()
    assert audit["flagged"] == 1
    assert audit["items"][0]["id"] == secret_mem.id


# ── redact_memory ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_redact_memory_idempotent(engine):
    m = await engine.store(TOKEN_CONTENT, memory_type="episodic")

    result = await engine.redact_memory(m.id)
    assert result["ok"] is True
    assert result["redacted"] is True
    assert result["secrets"]

    refreshed = await engine.episodic.get(m.id)
    assert "[REDACTED]" in refreshed.content
    history = refreshed.metadata.get("redaction_history")
    assert isinstance(history, list) and len(history) > 0

    # calling again is a no-op — no more secrets left to strip
    second = await engine.redact_memory(m.id)
    assert second["ok"] is True
    assert second["redacted"] is False


@pytest.mark.asyncio
async def test_redact_memory_idempotent_assignment(engine):
    # password=X assignment shape — the harder idempotency case
    m = await engine.store(ASSIGN_CONTENT, memory_type="episodic")

    first = await engine.redact_memory(m.id)
    assert first["redacted"] is True
    refreshed = await engine.episodic.get(m.id)
    assert "[REDACTED]" in refreshed.content
    assert "hunter2" not in refreshed.content

    # second pass must NOT re-flag the already-redacted value
    second = await engine.redact_memory(m.id)
    assert second["redacted"] is False
    # exactly one redaction recorded in history
    again = await engine.episodic.get(m.id)
    assert len(again.metadata.get("redaction_history", [])) == 1


@pytest.mark.asyncio
async def test_redact_memory_not_found(engine):
    result = await engine.redact_memory("does-not-exist")
    assert result["ok"] is False
    assert result["error"] == "not found"


# ── redact_all_secrets ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_redact_all_secrets_dry_run_then_apply(engine):
    m1 = await engine.store(SECRET_CONTENT, memory_type="episodic")
    m2 = await engine.store("api_key=sk-abc123xyz for the payments service", memory_type="episodic")

    dry = await engine.redact_all_secrets(dry_run=True)
    assert dry["dry_run"] is True
    assert dry["flagged"] == 2
    assert dry["redacted"] == 0

    # content unchanged after dry run
    unchanged1 = await engine.episodic.get(m1.id)
    unchanged2 = await engine.episodic.get(m2.id)
    assert unchanged1.content == SECRET_CONTENT
    assert "sk-abc123xyz" in unchanged2.content

    applied = await engine.redact_all_secrets(dry_run=False)
    assert applied["dry_run"] is False
    assert applied["redacted"] == 2

    redacted1 = await engine.episodic.get(m1.id)
    redacted2 = await engine.episodic.get(m2.id)
    assert "hunter2" not in redacted1.content
    assert "sk-abc123xyz" not in redacted2.content


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
async def test_api_audit_secrets_not_shadowed_by_memory_id_route(api_client):
    resp = await api_client.get("/api/memories/audit-secrets")
    assert resp.status_code == 200
    body = resp.json()
    assert "audit" in body
    assert "flagged" in body["audit"]


@pytest.mark.asyncio
async def test_api_full_redaction_and_purge_flow(api_client):
    # Seed a legacy/raw secret directly so the audit/remediation flow has
    # something to find. The public POST path is admission-gated and would
    # redact this before persistence.
    import server.api as api_mod

    raw = await api_mod._engine.store(SECRET_CONTENT, memory_type="episodic")
    memory_id = raw.id

    audit_resp = await api_client.get("/api/memories/audit-secrets")
    assert audit_resp.status_code == 200
    assert audit_resp.json()["audit"]["flagged"] == 1

    redact_all_resp = await api_client.post(
        "/api/memories/redact-all", json={"dry_run": False}
    )
    assert redact_all_resp.status_code == 200
    assert redact_all_resp.json()["redacted"] == 1

    # single-memory redact on the now-clean memory is a no-op, still 200 & ok
    redact_resp = await api_client.post(f"/api/memories/{memory_id}/redact")
    assert redact_resp.status_code == 200
    assert redact_resp.json()["ok"] is True

    purge_resp = await api_client.post(f"/api/memories/{memory_id}/purge")
    assert purge_resp.status_code == 200
    assert purge_resp.json()["purged"] is True


@pytest.mark.asyncio
async def test_api_redact_missing_memory_404(api_client):
    resp = await api_client.post("/api/memories/does-not-exist/redact")
    assert resp.status_code == 404


# ── MCP tools ──────────────────────────────────────────────────────


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
async def test_privacy_mcp_tools_registered(engine):
    from mcp.server.fastmcp import FastMCP

    from server.tools.privacy import register as reg

    mcp = FastMCP("test")
    reg(mcp, engine)
    names = {t.name for t in await mcp.list_tools()}
    assert {"audit_secrets", "redact_secrets", "purge_memory"} <= names


@pytest.mark.asyncio
async def test_audit_secrets_mcp_tool(engine):
    from mcp.server.fastmcp import FastMCP

    from server.tools.privacy import register as reg

    await engine.store(SECRET_CONTENT, memory_type="episodic")
    mcp = FastMCP("test")
    reg(mcp, engine)

    text = _tool_text(await mcp.call_tool("audit_secrets", {}))
    assert "flagged" in text.lower() or "credential" in text.lower() or "password" in text.lower()


@pytest.mark.asyncio
async def test_redact_secrets_mcp_tool_apply(engine):
    from mcp.server.fastmcp import FastMCP

    from server.tools.privacy import register as reg

    await engine.store(SECRET_CONTENT, memory_type="episodic")
    mcp = FastMCP("test")
    reg(mcp, engine)

    text = _tool_text(await mcp.call_tool("redact_secrets", {"apply": True}))
    assert "redacted" in text.lower()


@pytest.mark.asyncio
async def test_purge_memory_mcp_tool(engine):
    from mcp.server.fastmcp import FastMCP

    from server.tools.privacy import register as reg

    m = await engine.store(CLEAN_CONTENT, memory_type="episodic")
    mcp = FastMCP("test")
    reg(mcp, engine)

    text = _tool_text(await mcp.call_tool("purge_memory", {"memory_id": m.id}))
    assert m.id[:8] in text
    assert "purge" in text.lower()


@pytest.mark.asyncio
async def test_the_audit_names_the_field_after_what_it_holds(engine):
    """`secret_types` holds detector labels, and the name has to say so.

    Called "secrets" it read as though the audit hands back credentials —
    which it never does — and a static analyzer flagged every line that
    printed it as clear-text logging of a secret.
    """
    await engine.store("aws_access_key = AKIAIOSFODNN7EXAMPLE", memory_type="episodic")

    report = await engine.audit_secrets()
    item = report["items"][0]

    assert "secrets" not in item
    assert item["secret_types"], "the detector labels are what the audit reports"
    assert all(isinstance(label, str) for label in item["secret_types"])
    # The value itself never leaves the audit, under any key.
    assert "AKIAIOSFODNN7EXAMPLE" not in repr(item)

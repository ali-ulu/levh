"""Tests for the Memory Admission Gate: server/core/admission.py (pure
decision core), MemoryEngine.evaluate_admission / admit_memory, the
/api/memories/evaluate-admission and /api/memories/admit REST routes, and the
evaluate_admission / admit_memory MCP tools. Offline & deterministic —
EMBEDDER_MODE=hash.

The hash embedder is deterministic: identical content -> identical embedding
(cosine 1.0), so re-storing identical text is reliably a near-exact duplicate."""

import os
import sys
import tempfile

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["EMBEDDER_MODE"] = "hash"

from server.core import admission
from server.core.memory_engine import MemoryEngine

GENUINE = "Deploy process: run make deploy, then verify the staging environment carefully"


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


# ── pure evaluate() ─────────────────────────────────────────────────


def test_evaluate_empty_is_reject():
    result = admission.evaluate("")
    assert result["action"] == "reject"


def test_evaluate_too_short_is_reject():
    result = admission.evaluate("hi", min_length=3)
    assert result["action"] == "reject"


def test_evaluate_genuine_low_similarity_is_admit():
    result = admission.evaluate(GENUINE, max_similarity=0.1)
    assert result["action"] == "admit"


def test_evaluate_mid_similarity_is_review():
    result = admission.evaluate(GENUINE, max_similarity=0.93)
    assert result["action"] == "review"


def test_evaluate_high_similarity_is_reject():
    result = admission.evaluate(GENUINE, max_similarity=0.99)
    assert result["action"] == "reject"


# ── pure redact_secrets() ───────────────────────────────────────────


def test_redact_password_assignment():
    redacted, types = admission.redact_secrets("password=hunter2")
    assert "hunter2" not in redacted
    assert "[REDACTED]" in redacted
    assert "credential_assignment" in types


def test_redact_aws_key():
    redacted, types = admission.redact_secrets("here is my key AKIAABCDEFGHIJKLMNOP for prod")
    assert "AKIAABCDEFGHIJKLMNOP" not in redacted
    assert "[REDACTED]" in redacted
    assert "aws_access_key" in types


def test_redact_leaves_normal_email_intact():
    content = "reach out to alice@acme.com about the roadmap"
    redacted, types = admission.redact_secrets(content)
    assert "alice@acme.com" in redacted
    assert types == []


# ── engine.evaluate_admission ───────────────────────────────────────


@pytest.mark.asyncio
async def test_engine_evaluate_admission_duplicate_is_reject(engine):
    await engine.store(GENUINE, memory_type="episodic")
    decision = await engine.evaluate_admission(GENUINE)
    assert decision["action"] == "reject"


@pytest.mark.asyncio
async def test_engine_evaluate_admission_new_content_is_admit(engine):
    decision = await engine.evaluate_admission("a completely different note about lunch plans")
    assert decision["action"] == "admit"


# ── engine.admit_memory ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admit_memory_admits_and_stores(engine):
    result = await engine.admit_memory(GENUINE)
    assert result["stored"] is True
    assert result["decision"]["action"] == "admit"
    assert result["memory"] is not None
    assert result["memory"]["metadata"]["admission"]["action"] == "admit"


@pytest.mark.asyncio
async def test_admit_memory_duplicate_not_stored(engine):
    await engine.admit_memory(GENUINE)
    result = await engine.admit_memory(GENUINE)
    assert result["stored"] is False
    assert result["decision"]["action"] == "reject"
    assert result["memory"] is None


@pytest.mark.asyncio
async def test_admit_memory_force_stores_duplicate(engine):
    await engine.admit_memory(GENUINE)
    result = await engine.admit_memory(GENUINE, force=True)
    assert result["stored"] is True
    assert result["decision"]["action"] == "reject"
    assert result["memory"]["metadata"]["admission"]["forced"] is True


@pytest.mark.asyncio
async def test_admit_memory_redacts_secrets(engine):
    result = await engine.admit_memory("password=hunter2 for the staging db")
    assert result["stored"] is True
    assert result["decision"]["action"] == "redact"
    stored_content = result["memory"]["content"]
    assert "[REDACTED]" in stored_content
    assert "hunter2" not in stored_content
    assert result["memory"]["metadata"]["admission"]["action"] == "redact"


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
async def test_api_evaluate_admission(api_client):
    resp = await api_client.post(
        "/api/memories/evaluate-admission", json={"content": GENUINE}
    )
    assert resp.status_code == 200
    decision = resp.json()["decision"]
    assert decision["action"] in ("reject", "review", "redact", "admit")


@pytest.mark.asyncio
async def test_api_admit_stores(api_client):
    resp = await api_client.post("/api/memories/admit", json={"content": GENUINE})
    assert resp.status_code == 200
    body = resp.json()
    assert body["stored"] is True
    assert body["decision"]["action"] == "admit"


@pytest.mark.asyncio
async def test_api_admit_duplicate_not_stored(api_client):
    await api_client.post("/api/memories/admit", json={"content": GENUINE})
    resp = await api_client.post("/api/memories/admit", json={"content": GENUINE})
    assert resp.status_code == 200
    body = resp.json()
    assert body["stored"] is False
    assert body["decision"]["action"] == "reject"


# ── MCP tools ─────────────────────────────────────────────────────────


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
async def test_admission_mcp_tools_registered(engine):
    from mcp.server.fastmcp import FastMCP

    from server.tools.admission import register as reg

    mcp = FastMCP("test")
    reg(mcp, engine)
    names = {t.name for t in await mcp.list_tools()}
    assert "evaluate_admission" in names
    assert "admit_memory" in names


@pytest.mark.asyncio
async def test_evaluate_admission_mcp_tool_on_secret(engine):
    from mcp.server.fastmcp import FastMCP

    from server.tools.admission import register as reg

    mcp = FastMCP("test")
    reg(mcp, engine)
    text = _tool_text(
        await mcp.call_tool("evaluate_admission", {"content": "password=hunter2 in prod"})
    )
    assert "REDACT" in text.upper() or "credential" in text.lower()


@pytest.mark.asyncio
async def test_admit_memory_mcp_tool_confirms(engine):
    from mcp.server.fastmcp import FastMCP

    from server.tools.admission import register as reg

    mcp = FastMCP("test")
    reg(mcp, engine)
    text = _tool_text(await mcp.call_tool("admit_memory", {"content": GENUINE}))
    assert "stored" in text.lower()

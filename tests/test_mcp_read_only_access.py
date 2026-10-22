"""Server-side enforcement for the read-only MCP profile (#530).

The profile is not just a discovery filter: the dispatcher re-checks every
call, so a mutating tool is refused even when a client invokes a hidden name.
These tests exercise that guard directly, without a subprocess; the protocol
black-box tests live in ``tests/test_mcp_blackbox.py``.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine
from server.tools.access import ToolAccessDenied, authorize_tool_call
from server.tools.profiles import MUTATING_TOOLS, READ_ONLY_TOOLS
from server.tools.register import register_all_tools


@pytest_asyncio.fixture
async def engine(tmp_path):
    eng = MemoryEngine(
        db_path=str(tmp_path / "read-only.db"),
        embedder_mode="hash",
        short_term_max=50,
    )
    await eng.initialize()
    try:
        yield eng
    finally:
        await eng.shutdown()


def test_authorize_allows_every_read_tool():
    for tool in READ_ONLY_TOOLS:
        authorize_tool_call("read_only", tool)


@pytest.mark.parametrize("tool", sorted(MUTATING_TOOLS))
def test_authorize_denies_every_mutating_tool(tool):
    with pytest.raises(ToolAccessDenied):
        authorize_tool_call("read_only", tool)


def test_authorize_denies_admin_reads_and_unknown_names():
    for tool in ("export_memories", "audit_secrets", "evaluate_admission", "not_a_tool"):
        with pytest.raises(ToolAccessDenied):
            authorize_tool_call("read_only", tool)


def test_non_read_only_profiles_are_not_restricted():
    for profile in ("minimal", "work", "admin", "full"):
        authorize_tool_call(profile, "store_memory")


@pytest.mark.asyncio
async def test_dispatch_guard_denies_a_hidden_mutating_tool(engine):
    """Discovery is not the boundary: add a write tool, call it by name, and
    prove the guard refuses before the tool body runs."""
    mcp = FastMCP("read-only-test")
    register_all_tools(mcp, engine, profile="read_only")

    @mcp.tool(name="store_memory")
    async def hidden_store(content: str = "") -> str:  # pragma: no cover - must not run
        return "write happened"

    before = (await engine.get_stats()).total_memories
    with pytest.raises(ToolAccessDenied):
        await mcp.call_tool("store_memory", {"content": "hidden write"})
    after = (await engine.get_stats()).total_memories
    assert after == before


@pytest.mark.asyncio
async def test_dispatch_guard_allows_read_tools(engine):
    mcp = FastMCP("read-only-test")
    register_all_tools(mcp, engine, profile="read_only")
    assert await mcp.call_tool("get_memory_stats", {}) is not None


@pytest.mark.asyncio
async def test_read_only_recall_does_not_reinforce(engine, monkeypatch):
    seen: dict = {}
    original = engine.recall

    async def spy(*args, **kwargs):
        seen.update(kwargs)
        return await original(*args, **kwargs)

    monkeypatch.setattr(engine, "recall", spy)
    mcp = FastMCP("read-only-test")
    register_all_tools(mcp, engine, profile="read_only")
    await mcp.call_tool("recall_memory", {"query": "nothing"})
    assert seen["reinforce"] is False


@pytest.mark.asyncio
async def test_read_only_continuity_brief_is_not_recorded(engine, monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("emission write must not run")

    async def text(**kwargs):
        return "read-only brief"

    monkeypatch.setattr(engine, "emit_continuity_brief", forbidden)
    monkeypatch.setattr(engine, "get_continuity_context", text)
    mcp = FastMCP("read-only-test")
    register_all_tools(mcp, engine, profile="read_only")
    result = await mcp.call_tool("get_continuity_brief", {})
    assert "read-only brief" in str(result)

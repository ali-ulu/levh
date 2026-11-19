"""import_from_app MCP tool — parameter contract (issue #110).

The tool must honor the documented `importance` default for items that do
not carry their own importance, and must not accept a `batch_size`
parameter it silently ignored.
"""

from __future__ import annotations

import json
import os
import sys

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["EMBEDDER_MODE"] = "hash"

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine
from server.tools import connectors as connectors_tool


@pytest_asyncio.fixture
async def wired(tmp_path):
    """Engine + a registered MCP tool surface pointing at a temp DB."""
    engine = MemoryEngine(db_path=str(tmp_path / "conn.db"), embedder_mode="hash")
    await engine.initialize()
    try:
        mcp = FastMCP("test")
        connectors_tool.register(mcp, engine)

        # The tool is registered under the same name as the MCP export.
        await mcp.call_tool("import_from_app", {"connector": "local_files"})
        # call_tool requires a real connection; instead grab the closure by
        # re-registering and reading the underlying function directly.
        yield engine, mcp
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_importance_default_applied(tmp_path, monkeypatch):
    """Items without their own importance get the tool's importance value."""
    root = tmp_path / "notes"
    root.mkdir()
    (root / "a.txt").write_text("alpha note about quarterly planning", encoding="utf-8")
    (root / "b.txt").write_text("beta note about roadmap reviews", encoding="utf-8")

    engine = MemoryEngine(db_path=str(tmp_path / "imp.db"), embedder_mode="hash")
    await engine.initialize()
    try:
        mcp = FastMCP("test")
        connectors_tool.register(mcp, engine)

        # Reach the registered tool function through the FastMCP tool manager.
        tool = mcp._tool_manager._tools["import_from_app"]
        result = await tool.fn(
            connector="local_files",
            config=json.dumps({"directory": str(root)}),
            importance=0.9,
        )
        assert "Stored: 2" in result

        memories = await engine.list_memories(limit=10)
        assert len(memories) == 2
        assert all(m.importance == pytest.approx(0.9) for m in memories)
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_item_importance_beats_tool_default(tmp_path):
    """A connector item that carries its own importance keeps it."""
    root = tmp_path / "notes"
    root.mkdir()
    (root / "a.txt").write_text(
        "gamma note with embedded importance",
        encoding="utf-8",
    )

    engine = MemoryEngine(db_path=str(tmp_path / "imp2.db"), embedder_mode="hash")
    await engine.initialize()
    try:
        mcp = FastMCP("test")
        connectors_tool.register(mcp, engine)
        tool = mcp._tool_manager._tools["import_from_app"]

        # LocalFilesConnector items carry no importance of their own; fake the
        # per-item importance by patching fetch on the instance we get.
        from server.connectors import get_connector

        conn = get_connector("local_files")
        original_fetch = conn.fetch

        async def fetch_with_importance():
            items = await original_fetch()
            return [{**item, "importance": 0.2} for item in items]

        conn.fetch = fetch_with_importance  # type: ignore[method-assign]

        import server.connectors as connectors_pkg

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(connectors_pkg, "get_connector", lambda name: conn)
        try:
            result = await tool.fn(
                connector="local_files",
                config=json.dumps({"directory": str(root)}),
                importance=0.9,
            )
            assert "Stored: 1" in result
        finally:
            monkeypatch.undo()

        memories = await engine.list_memories(limit=10)
        assert len(memories) == 1
        assert memories[0].importance == pytest.approx(0.2)
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_batch_size_no_longer_accepted(tmp_path):
    """The documented-but-ignored batch_size parameter is gone (#110)."""
    import inspect

    from server.tools.connectors import register as _  # noqa: F401

    engine = MemoryEngine(db_path=str(tmp_path / "sig.db"), embedder_mode="hash")
    await engine.initialize()
    try:
        mcp = FastMCP("test")
        connectors_tool.register(mcp, engine)
        tool = mcp._tool_manager._tools["import_from_app"]
        params = inspect.signature(tool.fn).parameters
        assert "batch_size" not in params
        assert "importance" in params
    finally:
        await engine.shutdown()

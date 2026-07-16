"""MCP Server (stdio mode) — Entry point for Claude Desktop integration.

Usage:
    python -m server.mcp_stdio

Claude Desktop config (claude_desktop_config.json):
{
    "mcpServers": {
        "stackmemory": {
            "command": "python",
            "args": ["-m", "server.mcp_stdio"],
            "cwd": "/path/to/stackmemory",
            "env": {
                "SQLITE_DB_PATH": "./stackmemory.db",
                "EMBEDDER_MODE": "local"
            }
        }
    }
}
"""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.core import engine_provider
from server.tools.register import register_all_tools

# ── Lifecycle: open/close the DB around the server's run ────────────


@asynccontextmanager
async def _lifespan(_server: FastMCP) -> AsyncIterator[None]:
    engine = engine_provider.get_engine()
    await engine.initialize()
    try:
        yield
    finally:
        await engine.shutdown()


mcp = FastMCP("StackMemory", lifespan=_lifespan)

# Register tools at import time so they are advertised on initialize. The tool
# *surface* is controlled by STACKMEMORY_MCP_PROFILE (minimal / work / admin /
# full). Unset defaults to "full" here so existing installs keep every tool;
# generated configs set it explicitly to "work" to keep the advertised surface
# small (better tool-selection accuracy). The engine is created here but its DB
# connection is opened by the lifespan above; the embedder stays lazy until the
# first tool call.
_MCP_PROFILE = os.getenv("STACKMEMORY_MCP_PROFILE", "full")
_registered = register_all_tools(mcp, engine_provider.get_engine(), profile=_MCP_PROFILE)
# Announce on stderr (stdout is the stdio protocol channel — never write there).
print(
    f"[stackmemory] MCP profile '{_MCP_PROFILE}' → {len(_registered)} tools advertised",
    file=sys.stderr,
)


# ── Run ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")

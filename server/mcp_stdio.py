"""MCP Server (stdio mode) — Entry point for Claude Desktop integration.

Usage:
    python -m server.mcp_stdio

Claude Desktop config (claude_desktop_config.json):
{
    "mcpServers": {
        "levh": {
            "command": "levh",
            "args": ["mcp", "stdio"],
            "cwd": "/path/to/levh",
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
from server.core.env import get_env
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


mcp = FastMCP("LEVH", lifespan=_lifespan)

# Register tools at import time so they are advertised on initialize. The tool
# *surface* is controlled by LEVH_MCP_PROFILE (minimal / work / admin /
# full). Unset defaults to "full" here so existing installs keep every tool;
# generated configs set it explicitly to "work" to keep the advertised surface
# small (better tool-selection accuracy). The engine is created here but its DB
# connection is opened by the lifespan above; the embedder stays lazy until the
# first tool call.
_MCP_PROFILE = get_env("LEVH_MCP_PROFILE", "full")
_registered = register_all_tools(mcp, engine_provider.get_engine(), profile=_MCP_PROFILE)
# Announce on stderr (stdout is the stdio protocol channel — never write there).
print(
    f"[levh] MCP profile '{_MCP_PROFILE}' → {len(_registered)} tools advertised",
    file=sys.stderr,
)


# ── Run ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")

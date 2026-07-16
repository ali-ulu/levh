"""MCP Server (SSE mode) — For web-based AI clients.

Serves MCP protocol over Server-Sent Events, mountable under FastAPI.
Compatible with VS Code extensions, web dashboards, and remote clients.

Uses the SHARED engine from server.core.engine_provider, so memories
stored via MCP SSE are instantly visible to the REST API / dashboard
(and vice versa) within the same process.

Usage (standalone):
    uvicorn server.mcp_sse:app --host 0.0.0.0 --port 8001

Usage (mounted in FastAPI at /api/mcp, stream at /api/mcp/sse):
    See server/api.py
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


mcp_sse = FastMCP("LEVH", lifespan=_lifespan)

# Register tools at import time so they are advertised on initialize. Tool
# surface is controlled by LEVH_MCP_PROFILE (minimal / work / admin /
# full); unset defaults to "full" for backward compatibility.
_MCP_PROFILE = get_env("LEVH_MCP_PROFILE", "full")
register_all_tools(mcp_sse, engine_provider.get_engine(), profile=_MCP_PROFILE)

# ── ASGI app (returned by .sse_app()) ────────────────────────────────

app = mcp_sse.sse_app()

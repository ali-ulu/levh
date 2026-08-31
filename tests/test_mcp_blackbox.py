from __future__ import annotations

import asyncio
from importlib.metadata import version as package_version
import os
from pathlib import Path
import socket
import subprocess
import sys

import httpx
import pytest

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
LEVH_VERSION = package_version("levh")


def _env(db_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "SQLITE_DB_PATH": str(db_path),
            "EMBEDDER_MODE": "hash",
            "LEVH_MCP_PROFILE": "minimal",
            "PYTHONPATH": str(ROOT),
        }
    )
    return env


def _tool_text(result) -> str:
    return "\n".join(getattr(item, "text", "") for item in result.content)


@pytest.mark.asyncio
async def test_mcp_stdio_protocol_blackbox(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "server.mcp_stdio"],
        env=_env(tmp_path / "stdio.db"),
        cwd=str(ROOT),
    )
    with open(os.devnull, "w", encoding="utf-8") as errlog:
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                init_result = await session.initialize()
                # FastMCP has no `version` constructor parameter and never
                # forwards one to the underlying lowlevel Server, which then
                # falls back to the `mcp` SDK's own package version. Left
                # unset, every client sees the library version instead of
                # LEVH's (regression: server/mcp_stdio.py used to report
                # e.g. "1.28.1" here instead of the real release).
                assert init_result.serverInfo.version == LEVH_VERSION
                tools = await session.list_tools()
                assert [tool.name for tool in tools.tools] == [
                    "store_memory",
                    "recall_memory",
                    "search_memory",
                    "get_memory_stats",
                    "get_context",
                    "get_continuity_brief",
                ]
                stored = await session.call_tool(
                    "store_memory",
                    {"content": "Stdio protocol black-box memory", "memory_type": "episodic"},
                )
                assert stored.isError is False
                # `(str, Enum)` still prints "MemoryType.EPISODIC" via
                # Enum.__str__ unless overridden (server/core/types.py), and
                # that used to leak straight into this tool-output text.
                assert "MemoryType." not in _tool_text(stored)
                assert "Type: episodic" in _tool_text(stored)
                recalled = await session.call_tool(
                    "recall_memory", {"query": "Stdio protocol", "top_k": 3}
                )
                assert recalled.isError is False
                assert "Stdio protocol black-box memory" in _tool_text(recalled)
                assert "MemoryType." not in _tool_text(recalled)
                assert "Type: episodic" in _tool_text(recalled)


@pytest.mark.asyncio
async def test_mcp_sse_protocol_blackbox(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "uvicorn",
        "server.mcp_sse:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        cwd=str(ROOT),
        env=_env(tmp_path / "sse.db"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            for _ in range(50):
                try:
                    response = await client.get(f"http://127.0.0.1:{port}/")
                    if response.status_code in (200, 404):
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.1)
            else:
                raise AssertionError("SSE MCP server did not become ready")

        async with sse_client(
            f"http://127.0.0.1:{port}/sse", timeout=5, sse_read_timeout=20
        ) as (read, write):
            async with ClientSession(read, write) as session:
                init_result = await session.initialize()
                # Same fallback as the stdio transport (see the parallel
                # assertion above) — a separate FastMCP instance, so it
                # needed, and got, the same fix independently.
                assert init_result.serverInfo.version == LEVH_VERSION
                tools = await session.list_tools()
                assert len(tools.tools) == 6
                stored = await session.call_tool(
                    "store_memory",
                    {"content": "SSE protocol black-box memory", "memory_type": "episodic"},
                )
                assert stored.isError is False
                assert "MemoryType." not in _tool_text(stored)
                recalled = await session.call_tool(
                    "recall_memory", {"query": "SSE protocol", "top_k": 3}
                )
                assert recalled.isError is False
                assert "SSE protocol black-box memory" in _tool_text(recalled)
                assert "MemoryType." not in _tool_text(recalled)
                assert "Type: episodic" in _tool_text(recalled)
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()

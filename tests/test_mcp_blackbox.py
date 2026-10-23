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

from server.core.continuity_instructions import MCP_CONTINUITY_INSTRUCTIONS
from server.tools.profiles import READ_ONLY_TOOLS

ROOT = Path(__file__).resolve().parents[1]
LEVH_VERSION = package_version("levh")


def _env(db_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "SQLITE_DB_PATH": str(db_path),
            # Both spellings: LEVH_EMBEDDER_MODE outranks the plain name, and a
            # developer who exported `local` made these subprocess tests load
            # torch before the first assertion instead of using the hash
            # fallback the suite promises to run under.
            "EMBEDDER_MODE": "hash",
            "LEVH_EMBEDDER_MODE": "hash",
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
                assert init_result.instructions == MCP_CONTINUITY_INSTRUCTIONS
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
                # explain adds the "why" line; without it the same call stays
                # compact.
                assert "why:" not in _tool_text(recalled)
                explained = await session.call_tool(
                    "recall_memory",
                    {"query": "Stdio protocol", "top_k": 3, "explain": True},
                )
                assert explained.isError is False
                text = _tool_text(explained)
                assert "why:" in text and "via lexical" in text
                assert "candidate from" in text


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
                assert init_result.instructions == MCP_CONTINUITY_INSTRUCTIONS
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


@pytest.mark.asyncio
async def test_mcp_stdio_read_only_profile_denies_writes(tmp_path):
    """The protocol surface is the allowlist, and a write tool invoked by name
    is an error — not just omitted from discovery."""
    env = _env(tmp_path / "read-only-stdio.db")
    env["LEVH_MCP_PROFILE"] = "read_only"
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "server.mcp_stdio"],
        env=env,
        cwd=str(ROOT),
    )
    with open(os.devnull, "w", encoding="utf-8") as errlog:
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert {tool.name for tool in tools.tools} == set(READ_ONLY_TOOLS)

                denied = await session.call_tool(
                    "store_memory",
                    {"content": "read-only must not land", "memory_type": "episodic"},
                )
                assert denied.isError is True

                stats = await session.call_tool("get_memory_stats", {})
                assert stats.isError is False
                assert "Total memories:     0" in _tool_text(stats)

                recalled = await session.call_tool(
                    "recall_memory", {"query": "anything", "top_k": 3}
                )
                assert recalled.isError is False
                assert "No matching memories found" in _tool_text(recalled)


@pytest.mark.asyncio
async def test_mcp_sse_read_only_auth_and_tool_denial(tmp_path):
    """No token / wrong token / valid token, with the read-only profile active,
    plus the protocol-level proof that a valid token still cannot write."""
    token = "read-only-sse-token"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    env = _env(tmp_path / "read-only-sse.db")
    env["LEVH_MCP_PROFILE"] = "read_only"
    env["LEVH_TOKEN"] = token
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
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        base = f"http://127.0.0.1:{port}"
        async with httpx.AsyncClient(timeout=1.0) as client:
            for _ in range(50):
                try:
                    response = await client.get(
                        f"{base}/", headers={"X-LEVH-Token": token}
                    )
                    if response.status_code in (200, 404):
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.1)
            else:
                raise AssertionError("SSE MCP server did not become ready")

            assert (await client.get(f"{base}/sse")).status_code == 401
            assert (
                await client.get(
                    f"{base}/sse", headers={"X-LEVH-Token": "wrong-token"}
                )
            ).status_code == 401

        async with sse_client(
            f"{base}/sse",
            headers={"X-LEVH-Token": token},
            timeout=5,
            sse_read_timeout=20,
        ) as (read, write):
            async with ClientSession(read, write) as session:
                init_result = await session.initialize()
                assert init_result.serverInfo.version == LEVH_VERSION
                tools = await session.list_tools()
                assert {tool.name for tool in tools.tools} == set(READ_ONLY_TOOLS)

                denied = await session.call_tool(
                    "store_memory",
                    {"content": "token must not buy a write", "memory_type": "episodic"},
                )
                assert denied.isError is True
                stats = await session.call_tool("get_memory_stats", {})
                assert stats.isError is False
                assert "Total memories:     0" in _tool_text(stats)
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()


def test_mcp_stdio_loads_the_embedder_before_reading_stdin_on_windows(tmp_path):
    """On Windows a DLL load deadlocks once the stdio reader blocks on stdin,
    so the model must be warmed before ``mcp.run`` starts that reader."""
    probe = (
        "import runpy, sys\n"
        "import server.mcp_stdio\n"
        "from mcp.server.fastmcp import FastMCP\n"
        "from server.core.embedder import Embedder\n"
        "calls = []\n"
        "async def _warm(self):\n"
        "    calls.append('warm')\n"
        "Embedder.warm = _warm\n"
        "FastMCP.run = lambda self, transport='stdio': calls.append('run')\n"
        "sys.platform = 'win32'\n"
        "runpy.run_module('server.mcp_stdio', run_name='__main__')\n"
        "print(calls)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        env=_env(tmp_path / "warm-order.db"),
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "['warm', 'run']"


def test_warm_embedder_before_stdio_runs_only_on_windows(monkeypatch):
    import server.mcp_stdio as stdio

    calls: list[str] = []

    class _Embedder:
        async def warm(self) -> None:
            calls.append("warm")

    class _Engine:
        embedder = _Embedder()

    monkeypatch.setattr(stdio.engine_provider, "get_engine", lambda: _Engine())
    monkeypatch.setattr(stdio.sys, "platform", "linux")
    stdio._warm_embedder_before_stdio()
    assert calls == []

    monkeypatch.setattr(stdio.sys, "platform", "win32")
    stdio._warm_embedder_before_stdio()
    assert calls == ["warm"]

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

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.commands.auto_checkpoint import start_background_auto_checkpoint
from server.core import engine_provider
from server.core.agent_heartbeat import (
    detect_agent_from_env,
    detect_project_from_git,
    get_agent_session,
    smart_auto_connect,
    stop_heartbeat_background,
)
from server.core.env import get_env
from server.core.onboarding import levh_version
from server.tools.register import register_all_tools

# ── Lifecycle: open/close the DB around the server's run ────────────


def _env_bool(name: str, default: bool) -> bool:
    value = get_env(name, "1" if default else "0").strip().lower()
    if value == "":
        return default
    return value not in ("0", "false", "no")


def _env_int(name: str, default: int) -> int:
    try:
        return int(get_env(name, str(default)))
    except ValueError:
        return default


@asynccontextmanager
async def _lifespan(_server: FastMCP) -> AsyncIterator[None]:
    engine = engine_provider.get_engine()
    await engine.initialize()
    _checkpoint_task: asyncio.Task | None = None
    try:
        # Auto-connect presence: detect the agent/project from the environment
        # and git remote, create a tracking session, and start the heartbeat
        # loop. Opt-out via LEVH_AUTO_CONNECT=0.
        if _env_bool("LEVH_AUTO_CONNECT", True):
            try:
                await smart_auto_connect(engine)
            except Exception:
                pass  # Presence tracking must never block server startup.

        # Background auto-checkpoint: every LEVH_AUTO_CHECKPOINT_INTERVAL
        # seconds fold everything new since the last checkpoint into a
        # meaningful (non-repeating) summary. Off by default to keep the
        # surface inert until the user opts in.
        if _env_bool("LEVH_AUTO_CHECKPOINT", False):
            try:
                _project = detect_project_from_git()
            except Exception:
                _project = None
            _interval = _env_int("LEVH_AUTO_CHECKPOINT_INTERVAL", 600)
            _checkpoint_task = start_background_auto_checkpoint(
                engine=engine,
                agent=detect_agent_from_env(),
                session_id=get_agent_session(),
                project=_project,
                interval=_interval,
            )

        # Universal auto-brief: MCP has no push channel, so we bridge the
        # continuity brief onto stderr for every client that attaches to this
        # server. Agents that surface the server's stderr (Claude Code, Codex,
        # most terminal-side MCP clients) see the brief without calling any
        # tool. Opt-out via LEVH_AUTO_BRIEF=0.
        if _env_bool("LEVH_AUTO_BRIEF", True):
            try:
                _brief = await engine.get_continuity_context(
                    project=detect_project_from_git() or None,
                    limit=5,
                )
                if _brief and _brief.strip():
                    print(f"[levh] Continuity brief for this session:\n{_brief}", file=sys.stderr)
            except Exception:
                pass  # A missing or empty brief must not fail startup.

        yield
    finally:
        if _checkpoint_task is not None:
            _checkpoint_task.cancel()
        stop_heartbeat_background()
        await engine.shutdown()


mcp = FastMCP("LEVH", lifespan=_lifespan)

# FastMCP's constructor has no `version` parameter and never passes one to the
# underlying lowlevel Server, which then falls back to the `mcp` SDK's own
# package version for `initialize`'s serverInfo.version. Set it explicitly so
# version-aware clients see LEVH's real version, not the mcp library's.
mcp._mcp_server.version = levh_version()

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

# Auto-inject: Print continuity brief hint so agents know to call it.
# This appears in the agent's stderr log, reminding it to load context.
if "get_continuity_brief" in _registered:
    print(
        "[levh] 💡 Tip: Call 'get_continuity_brief' at session start to load "
        "context from previous work.",
        file=sys.stderr,
    )


# ── Run ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")

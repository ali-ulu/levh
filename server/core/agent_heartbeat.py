"""Automatic Agent Heartbeat — sends heartbeat on every MCP tool call.

This module wraps MCP tool calls to automatically send heartbeats,
so agents don't need to manually call agent_heartbeat.
"""

from __future__ import annotations

import asyncio
import os
import time
from functools import wraps
from typing import Any, Callable

from .env import get_env


# Global state for auto-heartbeat
_heartbeat_task: asyncio.Task | None = None
_agent_session_id: str | None = None
_last_heartbeat: float = 0.0
_heartbeat_interval: float = 60.0  # seconds

# Feature flag: set LEVH_AUTO_HEARTBEAT=0 to disable
_AUTO_HEARTBEAT_ENABLED = get_env("LEVH_AUTO_HEARTBEAT", "1").strip() not in ("0", "false", "no")


def set_agent_session(session_id: str) -> None:
    """Set the current agent session ID for auto-heartbeat."""
    global _agent_session_id, _last_heartbeat
    _agent_session_id = session_id
    _last_heartbeat = time.time()


def get_agent_session() -> str | None:
    """Get the current agent session ID."""
    return _agent_session_id


def auto_heartbeat_enabled() -> bool:
    """Check if auto-heartbeat is enabled."""
    return _AUTO_HEARTBEAT_ENABLED


async def _heartbeat_loop() -> None:
    """Background task that sends heartbeats periodically."""
    global _last_heartbeat
    while True:
        try:
            await asyncio.sleep(_heartbeat_interval)
            if _agent_session_id and _AUTO_HEARTBEAT_ENABLED:
                from . import engine_provider
                engine = engine_provider.get_engine()
                if engine.agent_tracker:
                    await engine.agent_tracker.heartbeat(_agent_session_id)
                    _last_heartbeat = time.time()
        except asyncio.CancelledError:
            break
        except Exception:
            # Heartbeat failures must never crash the server
            continue


def start_heartbeat_background() -> None:
    """Start the background heartbeat task."""
    global _heartbeat_task
    if _heartbeat_task is None or _heartbeat_task.done():
        try:
            loop = asyncio.get_running_loop()
            _heartbeat_task = loop.create_task(_heartbeat_loop())
        except RuntimeError:
            pass  # No event loop running


def stop_heartbeat_background() -> None:
    """Stop the background heartbeat task."""
    global _heartbeat_task
    if _heartbeat_task and not _heartbeat_task.done():
        _heartbeat_task.cancel()
        _heartbeat_task = None


def with_auto_heartbeat(func: Callable) -> Callable:
    """Decorator that sends a heartbeat before/after a tool call."""
    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        # Send heartbeat on call (throttled to once per interval)
        if _agent_session_id and _AUTO_HEARTBEAT_ENABLED:
            now = time.time()
            if now - _last_heartbeat > _heartbeat_interval:
                try:
                    from . import engine_provider
                    engine = engine_provider.get_engine()
                    if engine.agent_tracker:
                        await engine.agent_tracker.heartbeat(_agent_session_id)
                except Exception:
                    pass  # Best-effort
        return await func(*args, **kwargs)
    return wrapper


# ── Smart Auto-Connect ───────────────────────────────────────────────

def detect_project_from_git() -> str | None:
    """Auto-detect project name from git remote URL."""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            # Extract repo name from URL
            # https://github.com/user/repo.git -> repo
            # git@github.com:user/repo.git -> repo
            if "/" in url:
                name = url.rstrip("/").split("/")[-1]
                if name.endswith(".git"):
                    name = name[:-4]
                return name
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def detect_agent_from_env() -> str:
    """Auto-detect agent name from environment, or a best-effort label.

    Falls back to ``auto-connect`` (not ``unknown``): when a client connects
    via smart auto-connect without exposing an identity we cannot know who
    it is, so labelling it "auto-connect" is honest where "unknown" only
    implied a problem. Real tools are covered by the env vars below.
    """
    # Check common env vars
    for var in ("LEVH_AGENT", "AGENT_NAME", "CLAUDE_AGENT", "CURSOR_AGENT"):
        val = os.environ.get(var, "").strip()
        if val:
            return val.lower()

    # Check if running inside MCP
    if os.environ.get("MCP_SERVER", ""):
        return "mcp-client"

    return "auto-connect"


async def smart_auto_connect(engine: Any) -> str | None:
    """Smart auto-connect: detect project and agent, create session automatically.

    Returns the agent_session_id if connected, None otherwise.
    """
    if not engine.agent_tracker:
        return None

    agent_name = detect_agent_from_env()
    project = detect_project_from_git()

    result = await engine.agent_tracker.agent_connect(
        agent_name=agent_name,
        project=project,
        metadata={"auto_connect": True, "smart": True},
    )

    session_id = result.get("agent_session_id")
    if session_id:
        set_agent_session(session_id)
        start_heartbeat_background()

    return session_id

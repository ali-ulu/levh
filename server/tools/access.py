"""Server-side authorization for MCP tool calls.

The size profiles in :mod:`server.tools.profiles` only narrow tool discovery.
The ``read_only`` profile is different: it is a permission boundary, and this
module is where that boundary is actually enforced.

Two layers, on purpose:

* ``register_all_tools`` only *registers* tools in the active profile, so the
  ordinary MCP client sees a read-only surface.
* ``install_tool_access_guard`` wraps the FastMCP dispatcher for every profile
  and re-checks each ``tools/call`` before it runs. An omitted advertisement is
  therefore not the security boundary — a client that invokes a hidden write
  tool by name is refused at the call site even if the tool somehow reached
  the manager.

The active profile is also bound for the duration of the call. Read tools that
have internal side effects (recall reinforcement, continuity emission) use
``read_only_active`` to skip the write and stay genuinely read-only.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

from server.tools.profiles import (
    DEFAULT_PROFILE,
    READ_ONLY_PROFILE,
    READ_ONLY_TOOLS,
    resolve_profile,
)


class ToolAccessDenied(PermissionError):
    """A tool call was refused by the active MCP profile."""


#: The profile of the server that is currently executing. ``None`` means no
#: server has bound one (a programmatic caller or a unit test); that is treated
#: as "not read-only", matching the historical full-access behaviour.
_active_profile: ContextVar[str | None] = ContextVar(
    "levh_mcp_active_profile", default=None
)


def bind_active_profile(name: str) -> Token[str | None]:
    """Bind the normalized profile for the current task; returns the token."""
    return _active_profile.set(resolve_profile(name))


def reset_active_profile(token: Token[str | None]) -> None:
    _active_profile.reset(token)


def active_profile() -> str:
    """The profile bound to this task, or the default when none is bound."""
    return _active_profile.get() or DEFAULT_PROFILE


def read_only_active() -> bool:
    """Whether the running MCP server is enforcing the read-only profile."""
    return _active_profile.get() == READ_ONLY_PROFILE


def authorize_tool_call(profile: str, tool_name: str) -> None:
    """Refuse a tool the active profile does not permit.

    ``read_only`` is an allowlist, so this fails closed: a name that is not in
    ``READ_ONLY_TOOLS`` is refused whether it is a write tool, an admin read,
    or a typo. The message names the profile, not the tool's tier, because the
    caller's remedy is always the same — use a write-capable profile.
    """
    if resolve_profile(profile) != READ_ONLY_PROFILE:
        return
    if tool_name in READ_ONLY_TOOLS:
        return
    raise ToolAccessDenied(
        f"MCP profile '{READ_ONLY_PROFILE}' is read-only; tool "
        f"'{tool_name}' is not permitted."
    )


def install_tool_access_guard(mcp: Any, profile: str) -> None:
    """Enforce *profile* on every ``call_tool`` through *mcp*.

    FastMCP's low-level server captures ``FastMCP.call_tool`` at construction
    time, and that method delegates to ``mcp._tool_manager.call_tool``. Wrapping
    the manager is therefore the one seam that sees a direct ``tools/call``
    without touching any of the 44 tool modules or their signatures.
    """
    manager = getattr(mcp, "_tool_manager", None)
    if manager is None:  # pragma: no cover - FastMCP always has one
        return

    original = manager.call_tool

    async def guarded_call(
        name: str,
        arguments: dict[str, Any],
        context: Any = None,
        convert_result: bool = False,
    ) -> Any:
        authorize_tool_call(profile, name)
        token = bind_active_profile(profile)
        try:
            return await original(
                name, arguments, context=context, convert_result=convert_result
            )
        finally:
            reset_active_profile(token)

    manager.call_tool = guarded_call

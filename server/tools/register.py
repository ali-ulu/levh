"""MCP Tools Registry — declarative table + profile-filtered registration.

Registration is data, not procedure (issue #96): every tool module declares
itself in ``TOOL_MODULES`` by import path; the loop below imports and calls
it. Adding a tool is one table line — no edits to registration code. The
modules keep the uniform ``register(mcp, engine)`` entry point and the
``@mcp.tool()`` decoration style; the profile proxy in this module decides
which of them actually get advertised.
"""

from __future__ import annotations

import importlib

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine
from server.tools.access import install_tool_access_guard
from server.tools.profiles import resolve_profile, tools_for_profile

# Every tool module, by import path. One line per tool module; the order is
# presentation-only (it is the order tools appear in unfiltered listings).
TOOL_MODULES: tuple[str, ...] = (
    "server.tools.store",
    "server.tools.recall",
    "server.tools.forget",
    "server.tools.search",
    "server.tools.update",
    "server.tools.list_memories",
    "server.tools.stats",
    "server.tools.consolidate",
    "server.tools.clear_short_term",
    "server.tools.set_importance",
    "server.tools.get_context",
    "server.tools.session",
    "server.tools.export_import",
    "server.tools.connectors",
    "server.tools.pin",
    "server.tools.attach_file",
    "server.tools.projects",
    "server.tools.context_file",
    "server.tools.dedupe",
    "server.tools.reinforce",
    "server.tools.feedback",
    "server.tools.related",
    "server.tools.summarize",
    "server.tools.ask",
    "server.tools.people",
    "server.tools.timeline",
    "server.tools.briefing",
    "server.tools.organizations",
    "server.tools.decisions",
    "server.tools.backup",
    "server.tools.meeting_prep",
    "server.tools.consolidate_memories",
    "server.tools.review",
    "server.tools.admission",
    "server.tools.connector_sync",
    "server.tools.privacy",
    "server.tools.entities",
    "server.tools.trust",
    "server.tools.conflicts",
    "server.tools.continuity",
    "server.tools.record_mistake",
    "server.tools.agent_tracking",
)


class _ProfileFilter:
    """Transparent proxy around a FastMCP instance that only lets tools in the
    active profile's allow-set actually register.

    The per-tool ``register`` modules all decorate with ``@mcp.tool()``; wrapping
    the ``tool`` decorator here means we filter by tool name without touching any
    of those 39 modules. Everything else delegates to the real instance.
    """

    def __init__(self, mcp: FastMCP, allowed: set[str]) -> None:
        self._mcp = mcp
        self._allowed = allowed
        self.registered: list[str] = []

    def tool(self, *args, **kwargs):
        real_decorator = self._mcp.tool(*args, **kwargs)

        def decorator(fn):
            name = kwargs.get("name") or getattr(fn, "__name__", None)
            if name in self._allowed:
                self.registered.append(name)
                return real_decorator(fn)
            # Not in this profile: return the function unregistered so the
            # module keeps working, but the tool is never advertised.
            return fn

        return decorator

    def __getattr__(self, item):
        return getattr(self._mcp, item)


def register_all_tools(
    mcp: FastMCP, engine: MemoryEngine, profile: str | None = None
) -> list[str]:
    """Register MCP tools on the given FastMCP instance.

    ``profile`` selects which subset of tools to advertise — see
    :mod:`server.tools.profiles`. ``None`` (a bare programmatic call) means
    **full**: expose every tool, backward-compatible with pre-profile callers.
    Profile-limiting is always an explicit opt-in.

    The profile is also installed as a call-time guard, so ``read_only`` is a
    real authorization boundary rather than an advertisement filter. A transport
    that needs the profile during startup (stdio's auto-connect/brief) binds it
    with :func:`server.tools.access.bind_active_profile`. Returns the sorted
    list of tool names actually registered.
    """
    resolved = "full" if profile is None else resolve_profile(profile)
    install_tool_access_guard(mcp, resolved)
    if resolved == "full":
        # Fast path: no filtering, register every decorated tool directly.
        _register(mcp, engine)
        return sorted(tools_for_profile("full"))

    proxy = _ProfileFilter(mcp, tools_for_profile(resolved))
    _register(proxy, engine)
    return sorted(proxy.registered)


def _register(mcp: FastMCP, engine: MemoryEngine) -> None:
    """Import every module in ``TOOL_MODULES`` and let it register its tools."""
    for module_path in TOOL_MODULES:
        module = importlib.import_module(module_path)
        module.register(mcp, engine)

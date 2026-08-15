"""MCP Tools Registry — Registers all memory and connector tools on the FastMCP server."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine
from server.tools.profiles import resolve_profile, tools_for_profile


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
    Profile-limiting is always an explicit opt-in. Returns the sorted list of
    tool names actually registered.
    """
    resolved = "full" if profile is None else resolve_profile(profile)
    if resolved == "full":
        # Fast path: no filtering, register every decorated tool directly.
        _register(mcp, engine)
        return sorted(tools_for_profile("full"))

    proxy = _ProfileFilter(mcp, tools_for_profile(resolved))
    _register(proxy, engine)
    return sorted(proxy.registered)


def _register(mcp: FastMCP, engine: MemoryEngine) -> None:
    """Register every MCP tool on the given FastMCP instance."""
    from .store import register as reg_store
    from .recall import register as reg_recall
    from .forget import register as reg_forget
    from .search import register as reg_search
    from .update import register as reg_update
    from .list_memories import register as reg_list
    from .stats import register as reg_stats
    from .consolidate import register as reg_consolidate
    from .clear_short_term import register as reg_clear
    from .set_importance import register as reg_importance
    from .get_context import register as reg_context
    from .session import register as reg_session
    from .export_import import register as reg_export
    from .connectors import register as reg_connectors
    from .pin import register as reg_pin
    from .attach_file import register as reg_attach_file
    from .projects import register as reg_projects
    from .context_file import register as reg_context_file
    from .dedupe import register as reg_dedupe
    from .reinforce import register as reg_reinforce
    from .feedback import register as reg_feedback
    from .related import register as reg_related
    from .summarize import register as reg_summarize
    from .ask import register as reg_ask
    from .people import register as reg_people
    from .timeline import register as reg_timeline
    from .briefing import register as reg_briefing
    from .organizations import register as reg_organizations
    from .decisions import register as reg_decisions
    from .backup import register as reg_backup
    from .meeting_prep import register as reg_meeting_prep
    from .consolidate_memories import register as reg_consolidate_memories
    from .review import register as reg_review
    from .admission import register as reg_admission
    from .connector_sync import register as reg_connector_sync
    from .privacy import register as reg_privacy
    from .entities import register as reg_entities
    from .trust import register as reg_trust
    from .conflicts import register as reg_conflicts
    from .continuity import register as reg_continuity
    from .record_mistake import register as reg_record_mistake

    reg_store(mcp, engine)
    reg_recall(mcp, engine)
    reg_forget(mcp, engine)
    reg_search(mcp, engine)
    reg_update(mcp, engine)
    reg_list(mcp, engine)
    reg_stats(mcp, engine)
    reg_consolidate(mcp, engine)
    reg_clear(mcp, engine)
    reg_importance(mcp, engine)
    reg_context(mcp, engine)
    reg_session(mcp, engine)
    reg_export(mcp, engine)
    reg_connectors(mcp, engine)
    reg_pin(mcp, engine)
    reg_attach_file(mcp, engine)
    reg_projects(mcp, engine)
    reg_context_file(mcp, engine)
    reg_dedupe(mcp, engine)
    reg_reinforce(mcp, engine)
    reg_feedback(mcp, engine)
    reg_related(mcp, engine)
    reg_summarize(mcp, engine)
    reg_ask(mcp, engine)
    reg_people(mcp, engine)
    reg_timeline(mcp, engine)
    reg_briefing(mcp, engine)
    reg_organizations(mcp, engine)
    reg_decisions(mcp, engine)
    reg_backup(mcp, engine)
    reg_meeting_prep(mcp, engine)
    reg_consolidate_memories(mcp, engine)
    reg_review(mcp, engine)
    reg_admission(mcp, engine)
    reg_connector_sync(mcp, engine)
    reg_privacy(mcp, engine)
    reg_entities(mcp, engine)
    reg_trust(mcp, engine)
    reg_conflicts(mcp, engine)
    reg_continuity(mcp, engine)
    reg_record_mistake(mcp, engine)

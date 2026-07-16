"""Tools 21 & 22: list_projects / list_sources — Workspace and client overview."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def list_projects() -> str:
        """List all projects/workspaces that have memories, with counts.

        Projects namespace memories per codebase — pass project="name" to
        store_memory / recall_memory to keep workspaces isolated.
        """
        projects = await engine.list_projects()
        if not projects:
            return "No projects yet. Store memories with a project name to create one."
        lines = [f"{len(projects)} projects:\n"]
        for p in projects:
            lines.append(
                f"- {p['name']}: {p['memory_count']} memories "
                f"(last used {p['last_used'][:10] if p['last_used'] else 'unknown'})"
            )
        return "\n".join(lines)

    @mcp.tool()
    async def list_sources() -> str:
        """List which AI clients/tools have stored memories, with counts.

        Sources track where each memory came from (claude-code, cursor,
        claude-desktop, dashboard, connectors...).
        """
        sources = await engine.list_sources()
        if not sources:
            return "No sources recorded yet. Pass source=\"client-name\" to store_memory."
        lines = [f"{len(sources)} sources:\n"]
        for s in sources:
            lines.append(f"- {s['name']}: {s['memory_count']} memories")
        return "\n".join(lines)

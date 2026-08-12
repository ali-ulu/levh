"""MCP Resource: Continuity Brief — levh://session/{project}/continuity"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.resource("levh://session/{project}/continuity")
    async def continuity_brief(project: str) -> str:
        """Get a continuity brief for resuming work in a project.

        Returns a synthesized context brief showing recent sessions, active files,
        decisions, blockers, and suggested next actions.
        """
        return await engine.get_continuity_context(project=project)

    # Task variant uses a path segment, not a query string: FastMCP compiles
    # resource templates into regexes without escaping "?", so a query-string
    # template registers but never matches a real URI.
    @mcp.resource("levh://session/{project}/continuity/{task}")
    async def continuity_brief_with_task(project: str, task: str) -> str:
        """Get a continuity brief for a specific task in a project."""
        return await engine.get_continuity_context(project=project, task=task)

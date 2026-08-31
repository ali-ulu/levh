"""MCP Tool + Resource: Continuity Brief

Tool: get_continuity_brief — callable by any agent at session start.
Resource: levh://session/{project}/continuity — readable URI.

Auto-inject: The tool description explicitly instructs agents to call this
at the start of every new session to load context from previous work.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    # ── MCP Tool: get_continuity_brief ──────────────────────────────
    @mcp.tool()
    async def get_continuity_brief(
        project: str = "",
        task: str = "",
        limit: int = 5,
    ) -> str:
        """Get a continuity brief for resuming work. CALL THIS AT SESSION START.

        This tool returns a synthesized context brief showing:
        - Rules learned from mistakes (never repeat these)
        - Pinned memories (always remember)
        - Recent sessions and what was worked on
        - Recent changes from commits
        - Decisions made
        - Blockers, errors, and TODOs
        - Suggested next actions

        IMPORTANT: Call this tool at the start of every new session to
        understand what was done previously and continue seamlessly.

        Args:
            project: Project name to filter by (optional, auto-detects from git)
            task: Specific task context (optional)
            limit: Number of recent sessions to include (default: 5)
        """
        return await engine.get_continuity_context(
            project=project or None,
            task=task or None,
            limit=limit,
        )

    # ── MCP Resource: levh://session/{project}/continuity ───────────
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

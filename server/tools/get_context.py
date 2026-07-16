"""Tool 11: get_context — Get the current context window."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def get_context(
        session_id: str = "",
        project: str = "",
        max_tokens: int = 4000,
    ) -> str:
        """Get the current context window — recent short-term memories plus
        pinned and important episodic memories, formatted for LLM consumption.

        Args:
            session_id: Filter by session. Empty = all.
            project: Filter by project/workspace. Empty = all.
            max_tokens: Approximate token budget for the context (default 4000).
        """
        context = await engine.get_context(
            session_id=session_id or None,
            project=project or None,
            max_tokens=max_tokens,
        )
        if not context:
            return "Context window is empty. No recent memories."
        return (
            f"Current Context Window ({len(context)} chars)\n"
            f"{'=' * 40}\n{context}"
        )

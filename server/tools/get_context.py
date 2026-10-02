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
        query: str = "",
    ) -> str:
        """Get the current context window within an approximate token budget.

        Pass `query` to make the window topic-focused: memories are ranked by the
        same relevance score `recall_memory` uses, and the budget is filled in
        score order, so a memory that is neither recent nor pinned can still
        reach the window when it is relevant. Without `query` the window is the
        layered default — recent short-term, then pinned, then important
        episodic. Pinned memories are included either way.

        Args:
            session_id: Filter by session. Empty = all.
            project: Filter by project/workspace. Empty = all.
            max_tokens: Approximate token budget for the context (default 4000).
            query: Optional topic to focus the window on. Empty = layered default.
        """
        context = await engine.get_context(
            session_id=session_id or None,
            project=project or None,
            max_tokens=max_tokens,
            query=query or None,
        )
        if not context:
            return "Context window is empty. No recent memories."
        return (
            f"Current Context Window ({len(context)} chars)\n"
            f"{'=' * 40}\n{context}"
        )

"""Tool 9: clear_short_term — Clear the in-memory short-term deque."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def clear_short_term() -> str:
        """Clear the in-memory short-term context window.

        Episodic memories in SQLite are NOT affected — only the live deque is cleared.
        Use consolidate_memories first if you want to save short-term memories.
        """
        count = engine.clear_short_term()
        return f"Short-term memory cleared. Removed {count} entries from the live deque."

"""Tool 8: consolidate_memories — Move short-term to episodic layer."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def consolidate_memories(session_id: str = "") -> str:
        """Consolidate short-term memories into episodic long-term storage.

        Promotes all short-term memories to episodic type and increments their frequency counter.

        Args:
            session_id: Only consolidate memories from this session. Empty = all.
        """
        count = await engine.consolidate(session_id=session_id or None)
        return (
            f"Consolidation complete.\n"
            f"  Memories promoted: {count}\n"
            f"  From: short_term → episodic"
        )

"""Tool 7: get_memory_stats — Memory system statistics."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def get_memory_stats() -> str:
        """Get statistics about the memory system: counts, averages, active sessions."""
        stats = await engine.get_stats()
        return (
            f"LEVH Statistics\n"
            f"====================\n"
            f"Total memories:     {stats.total_memories}\n"
            f"  Short-term:       {stats.short_term_count}\n"
            f"  Episodic:         {stats.episodic_count}\n"
            f"  Pinned:           {stats.pinned_count}\n"
            f"Projects:           {stats.projects_count}\n"
            f"Average H-score:    {stats.avg_hscore:.4f}\n"
            f"Average importance: {stats.avg_importance:.4f}\n"
            f"Active sessions:    {stats.sessions_count}"
        )

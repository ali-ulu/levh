"""Tool: related_memories — find memories related to a given one."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def related_memories(memory_id: str, top_k: int = 5) -> str:
        """Find memories most related to a given memory (nearest neighbours by
        semantic similarity) — a lightweight knowledge-graph 'see also'.

        Args:
            memory_id: The anchor memory ID.
            top_k: Number of related memories to return (1-20). Default 5.
        """
        related = await engine.get_related(memory_id, top_k=min(max(top_k, 1), 20))
        if not related:
            return "No related memories found (unknown ID or no neighbours)."

        lines = [f"Related to {memory_id}:\n"]
        for i, (mem, sim) in enumerate(related, 1):
            snippet = mem.content[:120] + ("..." if len(mem.content) > 120 else "")
            lines.append(f"{i}. [sim {sim:.3f}] {snippet}\n   ID: {mem.id}")
        return "\n\n".join(lines)

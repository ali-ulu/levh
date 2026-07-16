"""Tool 25: reinforce_memory — Manually strengthen a memory (spaced repetition)."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def reinforce_memory(memory_id: str) -> str:
        """Manually strengthen a memory, the same way recalling it does.

        Every memory has its own decay clock and half-life ("stability").
        Recalling a memory already reinforces it automatically; use this tool
        when you want to explicitly say "remember this more" without a full
        recall — e.g. right after storing something you know is important,
        or when a user corrects/confirms a fact. Each reinforcement resets
        the decay clock and grows the memory's stability (like spaced
        repetition), weighted by its importance.

        Args:
            memory_id: ID of the memory to reinforce.
        """
        memory = await engine.reinforce_memory(memory_id)
        if not memory:
            return f"Memory '{memory_id}' not found."
        return (
            f"Memory reinforced.\n"
            f"  ID: {memory.id}\n"
            f"  New stability: {memory.stability_hours:.1f}h "
            f"(~{memory.stability_hours / 24:.1f} days until 50% decay)\n"
            f"  Reinforced {memory.recall_count} time(s) total"
        )

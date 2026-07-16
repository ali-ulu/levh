"""Tools 19 & 20: pin_memory / unpin_memory — Protect memories from decay."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def pin_memory(memory_id: str) -> str:
        """Pin a memory so it never decays over time.

        Pinned memories are exempt from the H(x,ψ) time-decay penalty, sort
        first in listings, and are always included in generated context files
        (CLAUDE.md / .cursorrules). Use for rules, conventions, and decisions
        that must never be forgotten.

        Args:
            memory_id: ID of the memory to pin.
        """
        memory = await engine.set_pinned(memory_id, True)
        if not memory:
            return f"Memory '{memory_id}' not found."
        return (
            f"Memory pinned. It will never decay.\n"
            f"  ID: {memory.id}\n"
            f"  Content: {memory.content[:100]}"
        )

    @mcp.tool()
    async def unpin_memory(memory_id: str) -> str:
        """Unpin a memory, restoring normal time-decay behaviour.

        Args:
            memory_id: ID of the memory to unpin.
        """
        memory = await engine.set_pinned(memory_id, False)
        if not memory:
            return f"Memory '{memory_id}' not found."
        return f"Memory unpinned. Normal decay applies again.\n  ID: {memory.id}"

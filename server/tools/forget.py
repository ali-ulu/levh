"""Tool 3: forget_memory — Delete a memory from all layers."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def forget_memory(memory_id: str) -> str:
        """Permanently delete a memory from all layers (short-term, episodic, vector store).

        Args:
            memory_id: The unique ID of the memory to delete.
        """
        memory = await engine.get_memory(memory_id)
        if not memory:
            return f"Memory '{memory_id}' not found."

        content_preview = memory.content[:80]
        success = await engine.forget(memory_id)
        if success:
            return (
                f"Memory forgotten.\n"
                f"  ID: {memory_id}\n"
                f"  Was: \"{content_preview}...\""
            )
        return f"Failed to delete memory '{memory_id}'."

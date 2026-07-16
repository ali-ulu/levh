"""Tool 10: set_importance — Update importance of a specific memory."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def set_importance(memory_id: str, importance: float) -> str:
        """Set the importance level of a specific memory.

        Args:
            memory_id: ID of the memory.
            importance: New importance value (0=low, 1=critical).
        """
        clamped = max(0.0, min(1.0, importance))
        success = await engine.set_importance(memory_id, clamped)
        if success:
            return f"Importance updated to {clamped:.2f} for memory '{memory_id}'."
        return f"Memory '{memory_id}' not found."

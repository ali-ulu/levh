"""Tool 5: update_memory — Update content, importance, or tags of a memory."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def update_memory(
        memory_id: str,
        content: str = "",
        importance: float = -1.0,
        tags: str = "",
    ) -> str:
        """Update an existing memory's content, importance, and/or tags.

        Leave a parameter empty to keep the existing value.
        Provide importance=-1 (default) to keep current importance.

        Args:
            memory_id: ID of the memory to update.
            content: New content (leave empty to keep existing).
            importance: New importance 0-1, or -1 to keep existing.
            tags: New comma-separated tags (leave empty to keep existing).
        """
        memory = await engine.get_memory(memory_id)
        if not memory:
            return f"Memory '{memory_id}' not found."

        updated = await engine.update_memory(
            memory_id=memory_id,
            content=content if content else None,
            importance=importance if importance >= 0 else None,
            tags=[t.strip() for t in tags.split(",") if t.strip()] if tags else None,
        )

        if updated:
            return (
                f"Memory updated.\n"
                f"  ID: {updated.id}\n"
                f"  Content: {updated.content[:100]}...\n"
                f"  Importance: {updated.importance}\n"
                f"  Tags: {', '.join(updated.tags) or 'none'}"
            )
        return "Update failed."

"""Tool 6: list_memories — List memories with optional filters."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def list_memories(
        memory_type: str = "",
        session_id: str = "",
        project: str = "",
        source: str = "",
        tag: str = "",
        pinned_only: bool = False,
        limit: int = 20,
    ) -> str:
        """List all memories with optional filtering.

        Args:
            memory_type: Filter by type: "short_term" or "episodic". Empty = all.
            session_id: Filter by session ID. Empty = all.
            project: Filter by project/workspace name. Empty = all.
            source: Filter by originating AI client (e.g. "claude-code"). Empty = all.
            tag: Filter by tag. Empty = all.
            pinned_only: Only show pinned memories.
            limit: Max results (1-100). Default 20.
        """
        memories = await engine.list_memories(
            memory_type=memory_type or None,
            session_id=session_id or None,
            project=project or None,
            source=source or None,
            tag=tag or None,
            pinned=True if pinned_only else None,
            limit=min(max(limit, 1), 100),
        )

        if not memories:
            return "No memories found."

        lines = [f"Total: {len(memories)} memories\n"]
        for i, mem in enumerate(memories, 1):
            snippet = mem.content[:80] + ("..." if len(mem.content) > 80 else "")
            pin = " 📌" if mem.pinned else ""
            lines.append(
                f"{i}. [{mem.memory_type}]{pin} {snippet}\n"
                f"   ID: {mem.id} | Imp: {mem.importance} | Freq: {mem.frequency}"
                f"{' | Project: ' + mem.project if mem.project else ''}"
                f"{' | Source: ' + mem.source if mem.source else ''}"
            )
        return "\n".join(lines)

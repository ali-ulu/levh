"""Tool 2: recall_memory — Recall memories by query using H(x,ψ) scoring."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def recall_memory(
        query: str,
        top_k: int = 10,
        session_id: str = "",
        project: str = "",
        min_importance: float = 0.0,
    ) -> str:
        """Recall relevant memories ranked by H(x,ψ) relevance score.

        Args:
            query: Natural language query to search for.
            top_k: Number of results (1-50). Default 10.
            session_id: Optional session filter.
            project: Optional project/workspace filter.
            min_importance: Minimum importance threshold (0-1).
        """
        result = await engine.recall(
            query=query,
            top_k=min(max(top_k, 1), 50),
            session_id=session_id or None,
            project=project or None,
            min_importance=min_importance,
        )

        if not result.memories:
            return "No matching memories found."

        lines = [f"Found {len(result.memories)} memories:\n"]
        for i, (mem, score) in enumerate(zip(result.memories, result.scores), 1):
            snippet = mem.content[:120] + ("..." if len(mem.content) > 120 else "")
            pin = " 📌" if mem.pinned else ""
            lines.append(
                f"{i}. [{score:.3f}]{pin} {snippet}\n"
                f"   ID: {mem.id} | Type: {mem.memory_type} | Imp: {mem.importance}"
                f"{' | Project: ' + mem.project if mem.project else ''}\n"
                f"   Tags: {', '.join(mem.tags) or 'none'}"
            )
        return "\n\n".join(lines)

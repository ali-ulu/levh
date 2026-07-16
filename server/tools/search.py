"""Tool 4: search_memory — Semantic search with detailed scores."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def search_memory(
        query: str,
        top_k: int = 5,
    ) -> str:
        """Semantic search across all memories with H(x,ψ) score breakdown.

        Returns fewer results than recall but with detailed scoring info.

        Args:
            query: Search query.
            top_k: Number of results (1-20). Default 5.
        """
        result = await engine.recall(query=query, top_k=min(max(top_k, 1), 20))

        if not result.memories:
            return "No results found."

        lines = [f"Top {len(result.memories)} results for: \"{query}\"\n"]
        lines.append("Lower H-score = more relevant (0=perfect, 1=irrelevant)\n")

        for i, (mem, score) in enumerate(zip(result.memories, result.scores), 1):
            snippet = mem.content[:150] + ("..." if len(mem.content) > 150 else "")
            lines.append(
                f"--- Result {i} (H-score: {score:.4f}) ---\n"
                f"Content: {snippet}\n"
                f"ID: {mem.id}\n"
                f"Importance: {mem.importance} | Freq: {mem.frequency} | Type: {mem.memory_type}\n"
                f"Tags: {', '.join(mem.tags) or 'none'}"
            )
        return "\n\n".join(lines)

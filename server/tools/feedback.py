"""Tools 26 & 27: memory_feedback / list_fading_memories — Outcome learning."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def memory_feedback(memory_id: str, helpful: bool) -> str:
        """Report whether a recalled memory was actually helpful.

        This is how the memory system learns from outcomes (SM-2 style):
          - helpful=true  → the memory was correct/useful: it gets reinforced
            (decay clock resets, stability grows).
          - helpful=false → the memory was wrong or outdated: its stability is
            cut so it fades out quickly instead of resurfacing. Use this when
            a user corrects you or a remembered fact turns out to be stale.

        Args:
            memory_id: ID of the memory the feedback applies to.
            helpful: Whether the memory was accurate and useful.
        """
        memory = await engine.memory_feedback(memory_id, helpful)
        if not memory:
            return f"Memory '{memory_id}' not found."
        if helpful:
            return (
                f"Positive feedback recorded — memory reinforced.\n"
                f"  ID: {memory.id}\n"
                f"  New stability: {memory.stability_hours:.1f}h"
            )
        return (
            f"Negative feedback recorded — memory weakened, it will fade fast.\n"
            f"  ID: {memory.id}\n"
            f"  New stability: {memory.stability_hours:.1f}h\n"
            f"  Tip: if it's plain wrong, forget_memory removes it immediately;\n"
            f"  if it changed, update_memory with the corrected fact."
        )

    @mcp.tool()
    async def list_fading_memories(
        threshold: float = 0.35,
        project: str = "",
        limit: int = 10,
    ) -> str:
        """List memories that are about to be forgotten (low predicted retention).

        Useful as a periodic review: reinforce what still matters, update what
        changed, and let the rest fade — exactly how human memory curates itself.

        Args:
            threshold: Retention cutoff (0-1). Memories below this are "fading".
            project: Only check this project. Empty = all.
            limit: Max results. Default 10.
        """
        fading = await engine.list_fading(
            threshold=max(0.01, min(0.99, threshold)),
            project=project or None,
            limit=min(max(limit, 1), 50),
        )
        if not fading:
            return "Nothing is fading — all memories are healthy (or pinned)."
        lines = [f"{len(fading)} fading memories (most faded first):\n"]
        for memory, retention in fading:
            snippet = memory.content[:80] + ("..." if len(memory.content) > 80 else "")
            lines.append(
                f"- [{retention * 100:.0f}% retention] {snippet}\n"
                f"  ID: {memory.id}"
                f"{' | Project: ' + memory.project if memory.project else ''}"
            )
        return "\n".join(lines)

"""Tool 24: dedupe_memories — Find and remove near-duplicate memories."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def dedupe_memories(
        similarity_threshold: float = 0.95,
        project: str = "",
        apply: bool = False,
    ) -> str:
        """Find near-duplicate memories by embedding similarity, optionally deleting them.

        When duplicates are removed, the pinned / most important / newest memory
        of each group is kept. Pinned memories are never auto-deleted.

        Args:
            similarity_threshold: Cosine similarity above which two memories
                count as duplicates (0.8-1.0). Default 0.95.
            project: Only dedupe within this project. Empty = all.
            apply: False (default) = dry run, just report. True = delete duplicates.
        """
        threshold = max(0.8, min(1.0, similarity_threshold))

        if not apply:
            groups = await engine.find_duplicates(
                similarity_threshold=threshold, project=project or None
            )
            if not groups:
                return "No duplicates found."
            lines = [
                f"Found {len(groups)} duplicate groups "
                f"({sum(len(g) - 1 for g in groups)} removable). "
                f"Run again with apply=True to delete.\n"
            ]
            for i, group in enumerate(groups, 1):
                lines.append(f"Group {i}:")
                for m in group:
                    lines.append(f"  - [{m.id[:8]}] {m.content[:70]}")
            return "\n".join(lines)

        removed = await engine.dedupe(
            similarity_threshold=threshold, project=project or None
        )
        return f"Deduplication complete. Removed {removed} duplicate memories."

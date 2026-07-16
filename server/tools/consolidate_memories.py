"""Tool: consolidate_memories — sleep-like compression of related older
memories into a single durable "gist" memory, archiving the originals inside
it. Distinct from dedupe (which only removes near-identical duplicates)."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def consolidate_similar(
        dry_run: bool = True,
        similarity_threshold: float = 0.82,
        min_age_days: int = 7,
        project: str = "",
    ) -> str:
        """Consolidate clusters of related older memories into one summary each
        — like how sleep compresses many episodes into a durable gist. The raw
        originals are preserved inside the consolidated memory's metadata, so
        nothing is lost. Pinned and recent memories are never touched.

        Args:
            dry_run: If true (default), only preview the clusters that would be
                consolidated. Set false to actually apply it.
            similarity_threshold: How related memories must be to cluster
                (0-1). Default 0.82 — lower than dedupe's, to catch related
                (not just duplicate) memories.
            min_age_days: Only consolidate memories older than this. Default 7.
            project: Optional project filter.
        """
        result = await engine.consolidate_memories(
            similarity_threshold=similarity_threshold,
            min_age_days=min_age_days,
            project=project or None,
            dry_run=dry_run,
        )
        clusters = result["clusters"]
        if not clusters:
            return "No consolidatable clusters found (need ≥2 related, unpinned, aged memories)."

        if result["dry_run"]:
            lines = [f"Would consolidate {len(clusters)} cluster(s) — preview:\n"]
            for i, c in enumerate(clusters, 1):
                proj = f" [{c['project']}]" if c.get("project") else ""
                lines.append(f"{i}. {c['size']} memories{proj} →")
                lines.append(f"   {c['summary'][:160]}")
            lines.append("\nRun again with dry_run=false to apply.")
            return "\n".join(lines)

        return (
            f"Consolidated {result['consolidated']} cluster(s), archiving "
            f"{result['archived']} original memories into durable summaries."
        )

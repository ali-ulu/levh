"""Tool: timeline — episodic memories grouped by day ("what happened this/last week")."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def timeline(days: int = 7, project: str = "") -> str:
        """See what happened recently, grouped by day — a chronological digest
        of your captured memories.

        Uses ``captured_at`` (when a calendar event or email actually
        happened) over the memory's capture time, so imported history lands
        on the right day.

        Args:
            days: How many days back to look. Default 7 (max 365).
            project: Optional project filter.
        """
        groups = await engine.timeline(days=min(max(days, 1), 365), project=project or None)
        if not groups:
            return f"No memories in the last {days} days."

        total = sum(g["count"] for g in groups)
        lines = [f"Last {days} days: {total} memories across {len(groups)} active days\n"]
        for g in groups:
            lines.append(f"- {g['date']}: {g['count']} memories")
            for item in g["items"][:3]:
                lines.append(f"    {item['summary']}")
        return "\n".join(lines)

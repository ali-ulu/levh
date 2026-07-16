"""Tool: briefing — deterministic "Daily Briefing" digest (what's on today,
open commitments, and what you're about to forget). No LLM call — fully
offline and reproducible."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def briefing(days: int = 7, project: str = "") -> str:
        """Get your Daily Briefing: what's happening today, open commitments
        you recently made, and memories that are fading and may need review.

        Fully deterministic — no LLM call — so it's always available offline
        and gives the same answer for the same data.

        Args:
            days: Lookback window (in days) for commitments/recent-count.
                Default 7 (max 90).
            project: Optional project filter.
        """
        data = await engine.briefing(days=min(max(days, 1), 90), project=project or None)
        counts = data["counts"]

        if not data["today"] and not data["commitments"] and not data["fading"]:
            return (
                f"Nothing pressing — {counts['recent_total']} memories captured "
                f"in the last {days} days."
            )

        lines = [f"Daily briefing (last {days}d)\n"]

        if data["today"]:
            lines.append(f"TODAY ({counts['today']})")
            for item in data["today"]:
                prefix = f"{item['time']}  " if item["time"] else ""
                lines.append(f"- {prefix}{item['summary']}")
            lines.append("")

        if data["commitments"]:
            lines.append(f"OPEN COMMITMENTS ({counts['commitments']})")
            for c in data["commitments"]:
                source = c["source"] or "unknown"
                lines.append(f"- {c['text']}  ({source} · {c['date']})")
            lines.append("")

        if data["fading"]:
            lines.append(f"MIGHT BE FORGETTING ({counts['fading']})")
            for f in data["fading"]:
                lines.append(f"- {f['summary']}  (retention {f['retention']})")
            lines.append("")

        return "\n".join(lines).rstrip()

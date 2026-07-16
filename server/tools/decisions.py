"""Tool: list_decisions — deterministic detection of decision statements in
recent episodic memory content ("we decided...", "agreed to...", "karar
verdik...", ...). No LLM call — fully offline and reproducible, mirrors
briefing()'s commitment-detection logic with a decision-tuned marker regex."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def list_decisions(days: int = 90, project: str = "") -> str:
        """List decisions detected in your recent memories: what was
        decided, and when/where.

        Fully deterministic — no LLM call — detects phrases like "we
        decided", "agreed to", "we chose", "karar verdik", etc. in memory
        content.

        Args:
            days: Lookback window (in days). Default 90 (max 365).
            project: Optional project filter.
        """
        decisions = await engine.list_decisions(
            days=min(max(days, 1), 365), project=project or None
        )
        if not decisions:
            return f"No decisions detected in the last {days} days."
        lines = [f"{len(decisions)} decisions (last {days}d):\n"]
        for d in decisions:
            source = d["source"] or "unknown"
            proj = f" [{d['project']}]" if d.get("project") else ""
            lines.append(f"- {d['text']}  ({source} · {d['date']}){proj}")
        return "\n".join(lines)

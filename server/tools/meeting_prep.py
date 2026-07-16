"""Tool: meeting_prep — the proactive "before you walk in" brief for your
next meeting: who's attending, what you last discussed with each of them, and
the open commitments and decisions that matter. Deterministic, offline."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def meeting_prep(query: str = "", within_days: int = 14) -> str:
        """Prepare for a meeting: pull up the next upcoming meeting (or one
        matching your query), who's attending, what you last discussed with
        each person, and any relevant open commitments and decisions.

        Args:
            query: Optional text to target a specific meeting instead of the
                next upcoming one.
            within_days: How far ahead to look. Default 14 (max 90).
        """
        prep = await engine.meeting_prep(query=query or "", within_days=min(max(within_days, 1), 90))
        meeting = prep.get("meeting")
        if meeting is None:
            return prep.get("reason", "No upcoming meeting found.")

        lines = [f"MEETING: {meeting['title']}"]
        if meeting.get("when"):
            lines.append(f"  When: {meeting['when']}")
        if meeting.get("project"):
            lines.append(f"  Project: {meeting['project']}")
        if meeting.get("attendees"):
            lines.append(f"  Attendees: {', '.join(meeting['attendees'])}")
        lines.append(f"  ({prep['reason']})")

        people = prep.get("people") or []
        if people:
            lines.append("\nWHO YOU'RE MEETING")
            for p in people:
                head = p["name"]
                if p.get("last_seen"):
                    head += f" — last seen {p['last_seen']}, {p['interaction_count']} prior interactions"
                lines.append(f"- {head}")
                for r in p.get("recent", []):
                    lines.append(f"    · {r['summary']}")

        commits = prep.get("open_commitments") or []
        if commits:
            lines.append("\nRELEVANT OPEN COMMITMENTS")
            for c in commits:
                lines.append(f"- {c['text']}  ({c['date']})")

        decisions = prep.get("recent_decisions") or []
        if decisions:
            lines.append("\nRECENT DECISIONS")
            for d in decisions:
                lines.append(f"- {d['text']}  ({d['date']})")

        return "\n".join(lines)

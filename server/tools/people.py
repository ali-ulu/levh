"""Tools: list_people / about_person — the person graph over captured memory."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def list_people(limit: int = 25) -> str:
        """List the people who appear across your memories, most-frequent first.

        People are extracted from captured metadata — calendar attendees, email
        senders/recipients, and meeting-transcript speakers — so this reflects
        who you actually interact with, with no manual tagging.

        Args:
            limit: Max people to return. Default 25.
        """
        people = await engine.list_people(limit=min(max(limit, 1), 200))
        if not people:
            return (
                "No people found yet. Import a calendar, email, or meeting "
                "transcript (connectors) and people are extracted automatically."
            )
        lines = [f"{len(people)} people:\n"]
        for p in people:
            email = f" <{p['email']}>" if p.get("email") else ""
            last = (p.get("last_seen") or "")[:10]
            srcs = ", ".join(s.replace("connector:", "") for s in p.get("sources", []))
            lines.append(
                f"- {p['name']}{email} — {p['memory_count']} memories"
                + (f", last {last}" if last else "")
                + (f" ({srcs})" if srcs else "")
            )
        return "\n".join(lines)

    @mcp.tool()
    async def about_person(name: str) -> str:
        """Everything you know about one person: their profile and the memories
        that mention them (meetings, emails, notes).

        Resolves ``name`` by email or best name match. For a synthesized answer
        to a specific question about them, use ask_memory instead.

        Args:
            name: A person's name or email (e.g. "Dana" or "dana@acme.com").
        """
        result = await engine.get_person(name)
        if result is None:
            return f"No person matching '{name}' found in your memories."
        p = result["person"]
        mems = result["memories"]
        email = f" <{p['email']}>" if p.get("email") else ""
        lines = [
            f"{p['name']}{email}",
            f"  {p['memory_count']} memories"
            + (f", last seen {(p.get('last_seen') or '')[:10]}" if p.get("last_seen") else ""),
            "",
        ]
        for m in mems[:15]:
            snippet = m["content"].split("\n", 1)[0][:90]
            when = (m.get("created_at") or "")[:10]
            lines.append(f"- [{when}] {snippet}")
        if len(mems) > 15:
            lines.append(f"  … and {len(mems) - 15} more")
        return "\n".join(lines)

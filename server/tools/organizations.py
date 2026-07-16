"""Tools: list_organizations / about_organization — the organization graph
(people grouped by email domain) over captured memory."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def list_organizations(limit: int = 20) -> str:
        """List the organizations that appear across your memories, derived
        from people's email domains, most-frequent first.

        Organizations are grouped by the email domain of people extracted
        from captured metadata — calendar attendees, email senders/
        recipients, meeting-transcript speakers. Free/personal email
        providers (gmail.com, outlook.com, ...) are excluded since they
        aren't organizations.

        Args:
            limit: Max organizations to return. Default 20.
        """
        orgs = await engine.list_organizations(limit=min(max(limit, 1), 200))
        if not orgs:
            return (
                "No organizations found yet. Import a calendar, email, or "
                "meeting transcript (connectors) and organizations are "
                "derived automatically from people's email domains."
            )
        lines = [f"{len(orgs)} organizations:\n"]
        for o in orgs:
            last = (o.get("last_seen") or "")[:10]
            srcs = ", ".join(s.replace("connector:", "") for s in o.get("sources", []))
            lines.append(
                f"- {o['name']} ({o['domain']}) — {o['memory_count']} memories, "
                f"{o['person_count']} people"
                + (f", last {last}" if last else "")
                + (f" ({srcs})" if srcs else "")
            )
        return "\n".join(lines)

    @mcp.tool()
    async def about_organization(query: str) -> str:
        """Everything you know about one organization: its profile, the
        people from it, and the memories that mention them.

        Resolves ``query`` by domain or best name match. For a synthesized
        answer to a specific question about it, use ask_memory instead.

        Args:
            query: An organization's domain or name (e.g. "acme.com" or "Acme").
        """
        result = await engine.get_organization(query)
        if result is None:
            return f"No organization matching '{query}' found in your memories."
        o = result["organization"]
        mems = result["memories"]
        lines = [
            f"{o['name']} ({o['domain']})",
            f"  {o['memory_count']} memories, {o['person_count']} people"
            + (f", last seen {(o.get('last_seen') or '')[:10]}" if o.get("last_seen") else ""),
        ]
        if o.get("people"):
            people_line = ", ".join(o["people"][:10])
            if len(o["people"]) > 10:
                people_line += " …"
            lines.append(f"  People: {people_line}")
        lines.append("")
        for m in mems[:15]:
            snippet = m["content"].split("\n", 1)[0][:90]
            when = (m.get("created_at") or "")[:10]
            lines.append(f"- [{when}] {snippet}")
        if len(mems) > 15:
            lines.append(f"  … and {len(mems) - 15} more")
        return "\n".join(lines)

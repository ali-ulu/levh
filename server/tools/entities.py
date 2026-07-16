"""Tools: reindex_entities / list_entities / about_entity — the persistent
entity knowledge graph (Faz 2) over captured memory."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def reindex_entities() -> str:
        """Rebuild the persistent entity knowledge graph from every stored
        memory. Extracts typed entities (person / organization / event /
        document / task) and their memory links, so graph queries run as a
        join instead of a full re-scan. Idempotent — run this whenever you
        want the graph to reflect newly captured memories."""
        result = await engine.reindex_entities()
        by_type = result.get("by_type", {})
        breakdown = ", ".join(f"{k}: {v}" for k, v in by_type.items()) if by_type else "none"
        return (
            f"Indexed {result['memories']} memories → {result['entities']} entities, "
            f"{result['links']} links ({breakdown})"
        )

    @mcp.tool()
    async def list_entities(entity_type: str = "", limit: int = 20) -> str:
        """List entities in the persistent knowledge graph, most-mentioned
        first. Run reindex_entities first if the graph hasn't been built yet.

        Args:
            entity_type: Optional filter — one of person, organization, event,
                document, task. Empty for all types.
            limit: Max entities to return. Default 20.
        """
        entities = await engine.list_entities_graph(
            entity_type=entity_type or None, limit=min(max(limit, 1), 2000)
        )
        if not entities:
            return "No entities. Run reindex_entities first."
        lines = []
        for e in entities:
            lines.append(f"[{e['type']}] {e['name']} — {e['mentions']} mentions")
        return "\n".join(lines)

    @mcp.tool()
    async def about_entity(query: str) -> str:
        """Everything the graph knows about one entity: its profile, the
        entities it co-occurs with, and the memories that mention it.

        Args:
            query: An entity id (e.g. "person:alice@acme.com") or free-text
                name/query (resolved by best match).
        """
        result = await engine.get_entity(query)
        if result is None:
            return f"No entity matching '{query}'."
        e = result["entity"]
        related = result["related"]
        memories = result["memories"]
        lines = [
            f"[{e['type']}] {e['name']}",
            f"  {e.get('mentions', 0)} mentions",
            "",
        ]
        if related:
            lines.append("Related entities:")
            for r in related[:8]:
                lines.append(f"  - [{r['type']}] {r['name']} (shared: {r['shared']})")
            lines.append("")
        if memories:
            lines.append("Memories:")
            for m in memories[:8]:
                snippet = (m.get("content") or "").split("\n", 1)[0][:90]
                when = (m.get("created_at") or "")[:10]
                lines.append(f"  - [{when}] {snippet}")
            if len(memories) > 8:
                lines.append(f"  … and {len(memories) - 8} more")
        return "\n".join(lines)

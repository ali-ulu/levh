"""The entity knowledge graph.

A slice of :class:`server.core.database.Database`, split out to keep each
file readable. Bodies are unchanged from the single-file version.
"""

from __future__ import annotations

from typing import Optional




class EntityQueries:
    """The entity knowledge graph."""

    async def clear_entity_graph(self) -> None:
        await self.conn.execute("DELETE FROM memory_entities")
        await self.conn.execute("DELETE FROM entities")
        await self.conn.commit()

    async def upsert_entity(
        self, entity_id: str, etype: str, ekey: str, name: str, now: str
    ) -> None:
        """Insert an entity or keep the most descriptive (longest) name."""
        await self.conn.execute(
            """
            INSERT INTO entities (id, type, ekey, name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = CASE WHEN length(excluded.name) > length(entities.name)
                            THEN excluded.name ELSE entities.name END,
                updated_at = excluded.updated_at
            """,
            (entity_id, etype, ekey, name, now, now),
        )

    async def link_memory_entity(
        self, memory_id: str, entity_id: str, role: str | None
    ) -> None:
        await self.conn.execute(
            "INSERT OR IGNORE INTO memory_entities (memory_id, entity_id, role) VALUES (?, ?, ?)",
            (memory_id, entity_id, role),
        )

    async def list_entities(
        self, etype: Optional[str] = None, limit: int = 200
    ) -> list[dict]:
        """Entities with their memory mention counts, most-mentioned first."""
        base = """
            SELECT e.id, e.type, e.ekey, e.name, e.updated_at,
                   COUNT(me.memory_id) AS mentions
            FROM entities e
            LEFT JOIN memory_entities me ON me.entity_id = e.id
        """
        params: tuple = ()
        if etype:
            base += " WHERE e.type = ?"
            params = (etype,)
        base += " GROUP BY e.id ORDER BY mentions DESC, e.name ASC LIMIT ?"
        params = params + (limit,)
        cursor = await self.conn.execute(base, params)
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(r) for r in rows]

    async def get_entity_row(self, entity_id: str) -> Optional[dict]:
        cursor = await self.conn.execute(
            """
            SELECT e.id, e.type, e.ekey, e.name, e.updated_at,
                   COUNT(me.memory_id) AS mentions
            FROM entities e
            LEFT JOIN memory_entities me ON me.entity_id = e.id
            WHERE e.id = ?
            GROUP BY e.id
            """,
            (entity_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return dict(row) if row else None

    async def find_entity(self, query: str, etype: Optional[str] = None) -> Optional[str]:
        """Resolve free text to an entity id: exact id, then name/key substring."""
        q = query.strip().lower()
        if not q:
            return None
        exact = await self.get_entity_row(q if ":" in q else "")
        if exact:
            return exact["id"]
        sql = "SELECT id FROM entities WHERE (lower(name) LIKE ? OR ekey LIKE ?)"
        params: tuple = (f"%{q}%", f"%{q}%")
        if etype:
            sql += " AND type = ?"
            params = params + (etype,)
        sql += " LIMIT 1"
        cursor = await self.conn.execute(sql, params)
        row = await cursor.fetchone()
        await cursor.close()
        return row[0] if row else None

    async def entity_memory_ids(self, entity_id: str, limit: int = 100) -> list[str]:
        cursor = await self.conn.execute(
            "SELECT memory_id FROM memory_entities WHERE entity_id = ? LIMIT ?",
            (entity_id, limit),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [r[0] for r in rows]

    async def entity_neighbors(self, entity_id: str, limit: int = 20) -> list[dict]:
        """Entities that co-occur with ``entity_id`` in the same memories,
        ranked by how many memories they share."""
        cursor = await self.conn.execute(
            """
            SELECT e.id, e.type, e.name, COUNT(*) AS shared
            FROM memory_entities a
            JOIN memory_entities b ON a.memory_id = b.memory_id AND b.entity_id != a.entity_id
            JOIN entities e ON e.id = b.entity_id
            WHERE a.entity_id = ?
            GROUP BY b.entity_id
            ORDER BY shared DESC, e.name ASC
            LIMIT ?
            """,
            (entity_id, limit),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(r) for r in rows]

    async def entity_type_counts(self) -> dict:
        cursor = await self.conn.execute(
            "SELECT type, COUNT(*) FROM entities GROUP BY type"
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return {r[0]: r[1] for r in rows}

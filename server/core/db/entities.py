"""The entity knowledge graph.

A slice of :class:`server.core.database.Database`, split out to keep each
file readable. Bodies are unchanged from the single-file version.
"""

from __future__ import annotations

from typing import Optional




class EntityQueries:
    """The entity knowledge graph."""

    def __init__(self, db) -> None:
        self._db = db

    async def clear_entity_graph(self) -> None:
        await self._db.conn.execute("DELETE FROM memory_entities")
        await self._db.conn.execute("DELETE FROM entities")
        await self._db.commit()

    async def upsert_entity(
        self, entity_id: str, etype: str, ekey: str, name: str, now: str
    ) -> None:
        """Insert an entity or keep the most descriptive (longest) name."""
        await self._db.conn.execute(
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
        await self._db.conn.execute(
            "INSERT OR IGNORE INTO memory_entities (memory_id, entity_id, role) VALUES (?, ?, ?)",
            (memory_id, entity_id, role),
        )

    async def upsert_entities_many(
        self, rows: list[tuple[str, str, str, str, str, str]]
    ) -> None:
        """Batch form of ``upsert_entity`` — one executemany round-trip.

        The rebuild walks every memory, so a fresh graph holds tens of
        thousands of rows; issuing them one await at a time made the first
        graph read after startup crawl. Same ON CONFLICT semantics as the
        single-row insert.
        """
        if not rows:
            return
        await self._db.conn.executemany(
            """
            INSERT INTO entities (id, type, ekey, name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = CASE WHEN length(excluded.name) > length(entities.name)
                            THEN excluded.name ELSE entities.name END,
                updated_at = excluded.updated_at
            """,
            rows,
        )

    async def link_memory_entities_many(
        self, rows: list[tuple[str, str, str | None]]
    ) -> None:
        """Batch form of ``link_memory_entity`` (INSERT OR IGNORE)."""
        if not rows:
            return
        await self._db.conn.executemany(
            "INSERT OR IGNORE INTO memory_entities (memory_id, entity_id, role) VALUES (?, ?, ?)",
            rows,
        )

    async def list_entities(
        self,
        etype: Optional[str] = None,
        limit: int = 200,
        offset: int = 0,
        query: Optional[str] = None,
    ) -> list[dict]:
        """Entities with their memory mention counts, most-mentioned first.

        ``query`` narrows to a case-insensitive substring of the name or the
        entity key — the same match ``find_entity`` resolves, but returning
        every hit instead of the single best one, so the graph page can offer a
        list while the user types (#569).
        """
        base = """
            SELECT e.id, e.type, e.ekey, e.name, e.updated_at,
                   COUNT(me.memory_id) AS mentions
            FROM entities e
            LEFT JOIN memory_entities me ON me.entity_id = e.id
        """
        clauses: list[str] = []
        params: list = []
        if etype:
            clauses.append("e.type = ?")
            params.append(etype)
        if query:
            clauses.append("(lower(e.name) LIKE ? OR lower(e.ekey) LIKE ?)")
            needle = f"%{query.strip().lower()}%"
            params.extend((needle, needle))
        if clauses:
            base += " WHERE " + " AND ".join(clauses)
        base += " GROUP BY e.id ORDER BY mentions DESC, e.name ASC LIMIT ? OFFSET ?"
        params.extend((limit, offset))
        cursor = await self._db.conn.execute(base, tuple(params))
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(r) for r in rows]

    async def get_entity_row(self, entity_id: str) -> Optional[dict]:
        cursor = await self._db.conn.execute(
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
        exact = await self._db.get_entity_row(q if ":" in q else "")
        if exact:
            return exact["id"]
        sql = "SELECT id FROM entities WHERE (lower(name) LIKE ? OR ekey LIKE ?)"
        params: tuple = (f"%{q}%", f"%{q}%")
        if etype:
            sql += " AND type = ?"
            params = params + (etype,)
        sql += " LIMIT 1"
        cursor = await self._db.conn.execute(sql, params)
        row = await cursor.fetchone()
        await cursor.close()
        return row[0] if row else None

    async def entity_memory_ids(self, entity_id: str, limit: int = 100) -> list[str]:
        cursor = await self._db.conn.execute(
            "SELECT memory_id FROM memory_entities WHERE entity_id = ? LIMIT ?",
            (entity_id, limit),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [r[0] for r in rows]

    async def entity_neighbors(self, entity_id: str, limit: int = 20) -> list[dict]:
        """Entities that co-occur with ``entity_id`` in the same memories,
        ranked by how many memories they share."""
        cursor = await self._db.conn.execute(
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
        cursor = await self._db.conn.execute(
            "SELECT type, COUNT(*) FROM entities GROUP BY type"
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return {r[0]: r[1] for r in rows}

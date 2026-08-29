"""Candidates the admission gate held for a human — queries.

A slice of :class:`server.core.database.Database`, split out to keep each
file readable.

The gate's ``review`` verdict means "redundant but not identical, so a person
decides". Until this table existed the verdict had nowhere to put the
candidate: the caller was told it was not stored, and the content was gone.
"""

from __future__ import annotations


class HeldMemoryQueries:
    """CRUD for the ``held_memories`` table."""

    async def insert_held_memory(self, row: dict) -> None:
        await self.conn.execute(
            """
            INSERT INTO held_memories
                (id, content, importance, tags_json, session_id, project,
                 source, memory_type, pinned, metadata_json, reasons_json,
                 max_similarity, status, created_at, decided_at,
                 admitted_memory_id)
            VALUES
                (:id, :content, :importance, :tags_json, :session_id, :project,
                 :source, :memory_type, :pinned, :metadata_json, :reasons_json,
                 :max_similarity, :status, :created_at, :decided_at,
                 :admitted_memory_id)
            """,
            row,
        )
        await self.conn.commit()

    async def get_held_memory(self, held_id: str) -> dict | None:
        cursor = await self.conn.execute(
            "SELECT * FROM held_memories WHERE id = ?", (held_id,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        return dict(row) if row else None

    async def list_held_memories(
        self, status: str = "held", project: str | None = None, limit: int = 50
    ) -> list[dict]:
        """Newest first. ``status=""`` lists every decision state, which is what
        an audit of "what did we do with these" needs."""
        clauses: list[str] = []
        params: list = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if project:
            clauses.append("project = ?")
            params.append(project)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(int(limit), 200)))
        cursor = await self.conn.execute(
            f"SELECT * FROM held_memories {where} ORDER BY created_at DESC LIMIT ?",
            params,
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(r) for r in rows]

    async def count_held_memories(self, status: str = "held") -> int:
        cursor = await self.conn.execute(
            "SELECT COUNT(*) AS n FROM held_memories WHERE status = ?", (status,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        return int(row["n"]) if row else 0

    async def mark_held_memory_decided(
        self, held_id: str, status: str, decided_at: str, admitted_memory_id: str | None = None
    ) -> bool:
        """Records a decision, once. The ``status = 'held'`` guard makes this
        idempotent under a double-click or two agents racing: the second call
        changes no row and returns False, so one candidate cannot be admitted
        twice into two separate memories."""
        cursor = await self.conn.execute(
            """
            UPDATE held_memories
               SET status = ?, decided_at = ?, admitted_memory_id = ?
             WHERE id = ? AND status = 'held'
            """,
            (status, decided_at, admitted_memory_id, held_id),
        )
        changed = cursor.rowcount
        await cursor.close()
        await self.conn.commit()
        return bool(changed)

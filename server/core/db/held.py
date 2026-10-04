"""Candidates the admission gate held for a human — queries.

A slice of :class:`server.core.database.Database`, split out to keep each
file readable.

The gate's ``review`` verdict means "redundant but not identical, so a person
decides". Until this table existed the verdict had nowhere to put the
candidate: the caller was told it was not stored, and the content was gone.
"""

from __future__ import annotations

from server.core.tenancy import authorize, current_workspace_id


class HeldMemoryQueries:
    """CRUD for the ``held_memories`` table."""

    def __init__(self, db) -> None:
        self._db = db

    async def insert_held_memory(self, row: dict) -> None:
        actor = authorize("admit", current_workspace_id())
        await self._db.conn.execute(
            """
            INSERT INTO held_memories
                (id, content, importance, tags_json, session_id, project,
                 workspace_id, source, memory_type, pinned, metadata_json,
                 reasons_json, max_similarity, status, created_at, decided_at,
                 admitted_memory_id)
            VALUES
                (:id, :content, :importance, :tags_json, :session_id, :project,
                 :workspace_id, :source, :memory_type, :pinned, :metadata_json,
                 :reasons_json, :max_similarity, :status, :created_at, :decided_at,
                 :admitted_memory_id)
            """,
            {**row, "workspace_id": actor.workspace_id},
        )
        await self._db.commit()

    async def get_held_memory(self, held_id: str) -> dict | None:
        authorize("read", current_workspace_id())
        cursor = await self._db.conn.execute(
            "SELECT * FROM held_memories WHERE id = ? AND workspace_id = ?",
            (held_id, current_workspace_id()),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return dict(row) if row else None

    async def list_held_memories(
        self, status: str = "held", project: str | None = None, limit: int = 50
    ) -> list[dict]:
        """Newest first. ``status=""`` lists every decision state, which is what
        an audit of "what did we do with these" needs."""
        authorize("read", current_workspace_id())
        clauses: list[str] = ["workspace_id = ?"]
        params: list = [current_workspace_id()]
        if status:
            clauses.append("status = ?")
            params.append(status)
        if project:
            clauses.append("project = ?")
            params.append(project)
        where = f"WHERE {' AND '.join(clauses)}"
        params.append(max(1, min(int(limit), 200)))
        cursor = await self._db.conn.execute(
            f"SELECT * FROM held_memories {where} ORDER BY created_at DESC LIMIT ?",  # nosec B608 - `where` is built from literal clauses, values bound
            params,
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(r) for r in rows]

    async def count_held_memories(self, status: str = "held") -> int:
        authorize("read", current_workspace_id())
        cursor = await self._db.conn.execute(
            "SELECT COUNT(*) AS n FROM held_memories WHERE status = ? AND workspace_id = ?",
            (status, current_workspace_id()),
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
        authorize("admit", current_workspace_id())
        cursor = await self._db.conn.execute(
            """
            UPDATE held_memories
               SET status = ?, decided_at = ?, admitted_memory_id = ?
             WHERE id = ? AND workspace_id = ? AND status = 'held'
            """,
            (status, decided_at, admitted_memory_id, held_id, current_workspace_id()),
        )
        changed = cursor.rowcount
        await cursor.close()
        await self._db.commit()
        return bool(changed)

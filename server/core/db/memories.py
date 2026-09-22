"""Memory rows: insert, search, update, delete and the residue audit.

A slice of :class:`server.core.database.Database`, split out to keep each
file readable. Bodies are unchanged from the single-file version.
"""

from __future__ import annotations

import json
from typing import Optional

import aiosqlite



def row_to_memory_dict(row) -> dict:
    """Decode one stored row into the mapping :class:`Memory` validates.

    Storage shape is not model shape: ``embedding``/``tags``/``metadata`` are
    JSON text in SQLite (and NULL when never set), and ``pinned`` is an
    integer. Every reader that wants to check a row against the model must
    start here, or it will judge rows on how they are *stored* instead of what
    they mean: a NULL ``metadata`` reads as "no metadata" (``{}``), not as an
    invalid row.

    Lives at module level so surfaces outside the query layer — ``levh
    doctor`` counting quarantined rows — share the one conversion instead of
    re-deriving it (and drifting from it).
    """
    d = dict(row)
    for field in ("embedding", "tags", "metadata"):
        raw = d.get(field)
        if raw:
            d[field] = json.loads(raw)
        else:
            d[field] = [] if field == "tags" else ({} if field == "metadata" else None)
    d["pinned"] = bool(d.get("pinned"))
    return d


class MemoryQueries:
    """Memory rows: insert, search, update, delete and the residue audit."""

    def __init__(self, db) -> None:
        self._db = db

    async def insert_memory(self, memory: dict) -> None:
        await self._db.conn.execute(
            """
            INSERT OR REPLACE INTO memories
                (id, content, memory_type, embedding, importance, frequency,
                 tags, session_id, project, source, pinned, metadata, hscore,
                 created_at, accessed_at, decay_factor, stability_hours, recall_count)
            VALUES
                (:id, :content, :memory_type, :embedding, :importance, :frequency,
                 :tags, :session_id, :project, :source, :pinned, :metadata, :hscore,
                 :created_at, :accessed_at, :decay_factor, :stability_hours, :recall_count)
            """,
            {
                **memory,
                "embedding": json.dumps(memory.get("embedding")) if memory.get("embedding") else None,
                "tags": json.dumps(memory.get("tags", [])),
                "metadata": json.dumps(memory.get("metadata", {})),
                "pinned": 1 if memory.get("pinned") else 0,
            },
        )
        await self._db.commit()

    async def get_memory(self, memory_id: str) -> Optional[dict]:
        cursor = await self._db.conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,))
        row = await cursor.fetchone()
        await cursor.close()
        return self._row_to_memory(row) if row else None

    async def get_all_memories(self, limit: int = 10000) -> list[dict]:
        cursor = await self._db.conn.execute(
            "SELECT * FROM memories ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [self._row_to_memory(r) for r in rows]

    async def search_memories(
        self,
        memory_type: Optional[str] = None,
        session_id: Optional[str] = None,
        project: Optional[str] = None,
        source: Optional[str] = None,
        tag: Optional[str] = None,
        pinned: Optional[bool] = None,
        min_importance: Optional[float] = None,
        content_like: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        fts_query = self._db._fts_query(content_like or "") if content_like else ""
        use_fts = bool(content_like and fts_query and self._db.fts5_available)
        query = (
            "SELECT memories.* FROM memories "
            "JOIN memories_fts ON memories_fts.memory_id = memories.id WHERE 1=1"
            if use_fts
            else "SELECT memories.* FROM memories WHERE 1=1"
        )
        params: list = []

        if memory_type:
            query += " AND memories.memory_type = ?"
            params.append(memory_type)
        if session_id:
            query += " AND memories.session_id = ?"
            params.append(session_id)
        if project:
            query += " AND memories.project = ?"
            params.append(project)
        if source:
            query += " AND memories.source = ?"
            params.append(source)
        if pinned is not None:
            query += " AND memories.pinned = ?"
            params.append(1 if pinned else 0)
        if min_importance is not None:
            query += " AND memories.importance >= ?"
            params.append(min_importance)
        if tag:
            query += " AND memories.tags LIKE ?"
            params.append(f'%"{tag}"%')
        if content_like:
            if use_fts:
                query += " AND memories_fts MATCH ?"
                params.append(fts_query)
            else:
                query += " AND memories.content LIKE ?"
                params.append(f"%{content_like}%")

        if use_fts:
            query += (
                " ORDER BY memories.pinned DESC, bm25(memories_fts), "
                "memories.created_at DESC LIMIT ? OFFSET ?"
            )
        else:
            query += (
                " ORDER BY memories.pinned DESC, memories.created_at DESC LIMIT ? OFFSET ?"
            )
        params.extend([limit, offset])

        cursor = await self._db.conn.execute(query, params)
        rows = await cursor.fetchall()
        await cursor.close()
        return [self._row_to_memory(r) for r in rows]

    async def update_memory(self, memory_id: str, updates: dict) -> bool:
        sets = []
        params = []
        for key, val in updates.items():
            if key in ("embedding", "tags", "metadata"):
                val = json.dumps(val) if val is not None else None
            elif key == "pinned":
                val = 1 if val else 0
            sets.append(f"{key} = ?")
            params.append(val)
        if not sets:
            return False
        params.append(memory_id)
        cursor = await self._db.conn.execute(
            f"UPDATE memories SET {', '.join(sets)} WHERE id = ?", params  # nosec B608 - column names come from fixed callers, values bound
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def delete_memory(self, memory_id: str) -> bool:
        cursor = await self._db.conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        await self._db.commit()
        return cursor.rowcount > 0

    async def delete_memory_cascade(self, memory_id: str) -> bool:
        """Delete a memory and every derived row that references it.

        The operation is one SQLite transaction so a failed hard-delete cannot
        report success while entity, trust or conflict residues survive.
        Orphan entity rows are pruned after their final link disappears.
        """
        await self._db.conn.execute("BEGIN IMMEDIATE")
        try:
            await self._db.conn.execute(
                "DELETE FROM memory_conflict_candidates "
                "WHERE memory_id_a = ? OR memory_id_b = ?",
                (memory_id, memory_id),
            )
            await self._db.conn.execute(
                "DELETE FROM memory_trust_scores WHERE memory_id = ?",
                (memory_id,),
            )
            await self._db.conn.execute(
                "DELETE FROM memory_entities WHERE memory_id = ?",
                (memory_id,),
            )
            cursor = await self._db.conn.execute(
                "DELETE FROM memories WHERE id = ?", (memory_id,)
            )
            await self._db.conn.execute(
                "DELETE FROM entities WHERE id NOT IN "
                "(SELECT DISTINCT entity_id FROM memory_entities)"
            )
            await self._db.commit()
            return cursor.rowcount > 0
        except Exception:
            await self._db.conn.rollback()
            raise

    async def memory_residue(self, memory_id: str) -> dict:
        """Return row counts for all persistent layers referencing a memory."""
        checks = {
            "episodic": ("SELECT COUNT(*) FROM memories WHERE id = ?", (memory_id,)),
            "entity_links": (
                "SELECT COUNT(*) FROM memory_entities WHERE memory_id = ?",
                (memory_id,),
            ),
            "trust_score": (
                "SELECT COUNT(*) FROM memory_trust_scores WHERE memory_id = ?",
                (memory_id,),
            ),
            "conflict_candidates": (
                "SELECT COUNT(*) FROM memory_conflict_candidates "
                "WHERE memory_id_a = ? OR memory_id_b = ?",
                (memory_id, memory_id),
            ),
        }
        out = {}
        for key, (sql, params) in checks.items():
            cursor = await self._db.conn.execute(sql, params)
            row = await cursor.fetchone()
            await cursor.close()
            out[key] = int(row[0] if row else 0)
        return out

    @staticmethod
    def _row_to_memory(row: aiosqlite.Row) -> dict:
        """Decode a stored row; see :func:`row_to_memory_dict` (single owner)."""
        return row_to_memory_dict(row)

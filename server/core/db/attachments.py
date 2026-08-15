"""Files attached to a memory as evidence — queries.

A slice of :class:`server.core.database.Database`, split out to keep each
file readable.
"""

from __future__ import annotations

from datetime import datetime, timezone


class AttachmentQueries:
    """CRUD + verification bookkeeping for the ``attachments`` table."""

    async def insert_attachment(self, row: dict) -> None:
        await self.conn.execute(
            """
            INSERT INTO attachments
                (id, memory_id, path, sha256, mime, size, derived_text,
                 derived_by, status, created_at, verified_at)
            VALUES
                (:id, :memory_id, :path, :sha256, :mime, :size, :derived_text,
                 :derived_by, :status, :created_at, :verified_at)
            """,
            row,
        )
        await self.conn.commit()

    async def get_attachment(self, attachment_id: str) -> dict | None:
        cursor = await self.conn.execute(
            "SELECT * FROM attachments WHERE id = ?", (attachment_id,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        return dict(row) if row else None

    async def list_attachments(self, memory_id: str) -> list[dict]:
        cursor = await self.conn.execute(
            "SELECT * FROM attachments WHERE memory_id = ? ORDER BY created_at",
            (memory_id,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(r) for r in rows]

    async def list_attachments_for_memories(self, memory_ids: list[str]) -> dict[str, list[dict]]:
        """Batch fetch, keyed by memory_id — for enriching a list of memories
        without one query per row."""
        ids = [str(m) for m in memory_ids if m]
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        cursor = await self.conn.execute(
            f"SELECT * FROM attachments WHERE memory_id IN ({placeholders}) ORDER BY created_at",
            ids,
        )
        rows = await cursor.fetchall()
        await cursor.close()
        by_memory: dict[str, list[dict]] = {}
        for r in rows:
            d = dict(r)
            by_memory.setdefault(d["memory_id"], []).append(d)
        return by_memory

    async def all_attachments(self, limit: int = 100000) -> list[dict]:
        cursor = await self.conn.execute(
            "SELECT * FROM attachments ORDER BY created_at LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(r) for r in rows]

    async def update_attachment_status(
        self, attachment_id: str, status: str, sha256: str | None = None
    ) -> bool:
        verified_at = datetime.now(timezone.utc).isoformat()
        if sha256 is not None:
            cursor = await self.conn.execute(
                "UPDATE attachments SET status = ?, sha256 = ?, verified_at = ? WHERE id = ?",
                (status, sha256, verified_at, attachment_id),
            )
        else:
            cursor = await self.conn.execute(
                "UPDATE attachments SET status = ?, verified_at = ? WHERE id = ?",
                (status, verified_at, attachment_id),
            )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def delete_attachment(self, attachment_id: str) -> bool:
        cursor = await self.conn.execute(
            "DELETE FROM attachments WHERE id = ?", (attachment_id,)
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def delete_attachments_for_memory(self, memory_id: str) -> int:
        cursor = await self.conn.execute(
            "DELETE FROM attachments WHERE memory_id = ?", (memory_id,)
        )
        await self.conn.commit()
        return cursor.rowcount

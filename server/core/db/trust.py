"""Trust scores and conflict candidates.

A slice of :class:`server.core.database.Database`, split out to keep each
file readable. Bodies are unchanged from the single-file version.
"""

from __future__ import annotations

from typing import Optional




class TrustQueries:
    """Trust scores and conflict candidates."""

    async def upsert_trust(self, row: dict) -> None:
        await self.conn.execute(
            """
            INSERT INTO memory_trust_scores
                (memory_id, confidence, source_score, corroboration_score,
                 review_score, recency_score, risk_penalty, label,
                 computed_at, breakdown_json)
            VALUES
                (:memory_id, :confidence, :source_score, :corroboration_score,
                 :review_score, :recency_score, :risk_penalty, :label,
                 :computed_at, :breakdown_json)
            ON CONFLICT(memory_id) DO UPDATE SET
                confidence=excluded.confidence,
                source_score=excluded.source_score,
                corroboration_score=excluded.corroboration_score,
                review_score=excluded.review_score,
                recency_score=excluded.recency_score,
                risk_penalty=excluded.risk_penalty,
                label=excluded.label,
                computed_at=excluded.computed_at,
                breakdown_json=excluded.breakdown_json
            """,
            row,
        )

    async def get_trust(self, memory_id: str) -> Optional[dict]:
        cursor = await self.conn.execute(
            "SELECT * FROM memory_trust_scores WHERE memory_id = ?", (memory_id,)
        )
        r = await cursor.fetchone()
        await cursor.close()
        return dict(r) if r else None

    async def list_low_trust(self, threshold: float = 0.4, limit: int = 50) -> list[dict]:
        cursor = await self.conn.execute(
            """
            SELECT * FROM memory_trust_scores
            WHERE confidence < ?
            ORDER BY confidence ASC
            LIMIT ?
            """,
            (threshold, limit),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(r) for r in rows]

    async def list_all_trust(self, limit: int = 1_000_000) -> list[dict]:
        cursor = await self.conn.execute(
            "SELECT * FROM memory_trust_scores ORDER BY confidence ASC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(r) for r in rows]

    async def clear_trust(self) -> None:
        await self.conn.execute("DELETE FROM memory_trust_scores")
        await self.conn.commit()

    async def get_conflict(self, conflict_id: str) -> Optional[dict]:
        cursor = await self.conn.execute(
            "SELECT * FROM memory_conflict_candidates WHERE id = ?", (conflict_id,)
        )
        r = await cursor.fetchone()
        await cursor.close()
        return dict(r) if r else None

    async def insert_conflict_if_absent(self, row: dict) -> bool:
        """Insert a new candidate only if the pair isn't already recorded (so a
        re-run never resets a dismissed/confirmed candidate to open). Returns
        True if a new row was inserted."""
        existing = await self.get_conflict(row["id"])
        if existing is not None:
            return False
        await self.conn.execute(
            """
            INSERT INTO memory_conflict_candidates
                (id, memory_id_a, memory_id_b, shared_entities_json, signal_type,
                 confidence, status, explanation_json, created_at, reviewed_at)
            VALUES
                (:id, :memory_id_a, :memory_id_b, :shared_entities_json, :signal_type,
                 :confidence, :status, :explanation_json, :created_at, NULL)
            """,
            row,
        )
        return True

    async def list_conflicts(self, status: Optional[str] = None, limit: int = 100) -> list[dict]:
        if status:
            cursor = await self.conn.execute(
                "SELECT * FROM memory_conflict_candidates WHERE status = ? "
                "ORDER BY confidence DESC, created_at DESC LIMIT ?",
                (status, limit),
            )
        else:
            cursor = await self.conn.execute(
                "SELECT * FROM memory_conflict_candidates "
                "ORDER BY confidence DESC, created_at DESC LIMIT ?",
                (limit,),
            )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(r) for r in rows]

    async def update_conflict_status(
        self, conflict_id: str, status: str, reviewed_at: str
    ) -> bool:
        cursor = await self.conn.execute(
            "UPDATE memory_conflict_candidates SET status = ?, reviewed_at = ? WHERE id = ?",
            (status, reviewed_at, conflict_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def delete_conflict(self, conflict_id: str) -> bool:
        cursor = await self.conn.execute(
            "DELETE FROM memory_conflict_candidates WHERE id = ?", (conflict_id,)
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def delete_conflicts_for_memory_ids(self, memory_ids: list[str]) -> int:
        """Remove derived conflict rows that reference deleted memories."""
        ids = [str(mid) for mid in memory_ids if mid]
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        cursor = await self.conn.execute(
            f"DELETE FROM memory_conflict_candidates "
            f"WHERE memory_id_a IN ({placeholders}) OR memory_id_b IN ({placeholders})",
            [*ids, *ids],
        )
        await self.conn.commit()
        return cursor.rowcount

    async def conflicts_for_memory(self, memory_id: str) -> list[dict]:
        cursor = await self.conn.execute(
            "SELECT * FROM memory_conflict_candidates "
            "WHERE memory_id_a = ? OR memory_id_b = ?",
            (memory_id, memory_id),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(r) for r in rows]

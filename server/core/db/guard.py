"""Mistake-guard violation rows.

A slice of :class:`server.core.database.Database`, split out to keep each
file readable. Bodies are unchanged from the single-file version.
"""

from __future__ import annotations

from typing import Optional




class GuardQueries:
    """Mistake-guard violation rows."""

    async def insert_violation(self, violation: dict) -> None:
        await self.conn.execute(
            """
            INSERT INTO violations
                (id, rule_id, task, wrong_action, root_cause, tool_name,
                 severity, source, occurred_at, resolved, resolution)
            VALUES
                (:id, :rule_id, :task, :wrong_action, :root_cause, :tool_name,
                 :severity, :source, :occurred_at, :resolved, :resolution)
            """,
            violation,
        )
        await self.conn.commit()

    async def list_violations(
        self,
        since: Optional[str] = None,
        severity: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        query = "SELECT * FROM violations WHERE 1 = 1"
        params: list = []
        if since:
            query += " AND occurred_at >= ?"
            params.append(since)
        if severity:
            query += " AND severity = ?"
            params.append(severity)
        query += " ORDER BY occurred_at DESC LIMIT ?"
        params.append(limit)

        cursor = await self.conn.execute(query, params)
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(r) for r in rows]

    async def count_violations(self, since: Optional[str] = None) -> int:
        if since:
            cursor = await self.conn.execute(
                "SELECT COUNT(*) FROM violations WHERE occurred_at >= ?", (since,)
            )
        else:
            cursor = await self.conn.execute("SELECT COUNT(*) FROM violations")
        row = await cursor.fetchone()
        await cursor.close()
        return int(row[0]) if row else 0

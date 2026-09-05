"""Findings inbox — queries.

A slice of :class:`server.core.database.Database`, split out to keep each file
readable.

The one behaviour worth knowing before reading the SQL: recording is an
*upsert keyed on the fingerprint*. Seeing the same problem again must never
create a second row, because the reporter is a loop and a loop repeats. A
repeat bumps ``occurrences`` and ``last_seen_at`` — and reopens the finding if
it had been marked resolved, since a resolved problem that recurs is news.
"""

from __future__ import annotations

from datetime import datetime, timezone

_VALID_STATUS = {"open", "ack", "resolved", "ignored"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class FindingQueries:
    """CRUD for the ``findings`` table."""

    async def record_finding(self, row: dict) -> dict:
        """Insert a finding, or fold a repeat into the existing row.

        Returns the stored row plus ``repeat``/``reopened`` flags so the caller
        can tell a genuinely new problem from the hundredth sighting of an old
        one without a second query.
        """
        now = _now()
        existing = await self.get_finding(row["id"])
        if existing is None:
            await self.conn.execute(
                """
                INSERT INTO findings
                    (id, title, detail, category, severity, source, status,
                     occurrences, first_seen_at, last_seen_at, decided_at,
                     note, external_ref)
                VALUES
                    (:id, :title, :detail, :category, :severity, :source, 'open',
                     1, :now, :now, NULL, NULL, NULL)
                """,
                {**row, "now": now},
            )
            await self.conn.commit()
            stored = await self.get_finding(row["id"])
            return {**stored, "repeat": False, "reopened": False}

        # A resolved problem that happens again is not resolved.
        reopened = existing["status"] == "resolved"
        await self.conn.execute(
            """
            UPDATE findings
               SET occurrences  = occurrences + 1,
                   last_seen_at = :now,
                   detail       = :detail,
                   severity     = :severity,
                   status       = CASE WHEN status = 'resolved' THEN 'open' ELSE status END
             WHERE id = :id
            """,
            {"id": row["id"], "detail": row["detail"],
             "severity": row["severity"], "now": now},
        )
        await self.conn.commit()
        stored = await self.get_finding(row["id"])
        return {**stored, "repeat": True, "reopened": reopened}

    async def get_finding(self, finding_id: str) -> dict | None:
        cursor = await self.conn.execute(
            "SELECT * FROM findings WHERE id = ?", (finding_id,)
        )
        found = await cursor.fetchone()
        await cursor.close()
        return dict(found) if found else None

    async def list_findings(
        self, status: str = "open", category: str | None = None, limit: int = 100
    ) -> list[dict]:
        """Most recently seen first. ``status=""`` lists every state, which is
        what an audit of "what did we decide about these" needs."""
        clauses: list[str] = []
        params: list = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if category:
            clauses.append("category = ?")
            params.append(category)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(int(limit), 1000)))
        cursor = await self.conn.execute(
            f"SELECT * FROM findings {where} ORDER BY last_seen_at DESC LIMIT ?",
            params,
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(r) for r in rows]

    async def count_findings_by_status(self) -> dict[str, int]:
        cursor = await self.conn.execute(
            "SELECT status, COUNT(*) AS n FROM findings GROUP BY status"
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return {r["status"]: r["n"] for r in rows}

    async def decide_finding(
        self, finding_id: str, status: str, note: str | None = None
    ) -> dict | None:
        """Record the human's verdict. Returns the updated row, or None if the
        finding does not exist. Raises ValueError on an unknown status."""
        if status not in _VALID_STATUS:
            raise ValueError(
                f"unknown status {status!r}; expected one of {sorted(_VALID_STATUS)}"
            )
        if await self.get_finding(finding_id) is None:
            return None
        await self.conn.execute(
            """
            UPDATE findings
               SET status = :status,
                   note = COALESCE(:note, note),
                   decided_at = :now
             WHERE id = :id
            """,
            {"id": finding_id, "status": status, "note": note, "now": _now()},
        )
        await self.conn.commit()
        return await self.get_finding(finding_id)

    async def delete_finding(self, finding_id: str) -> bool:
        cursor = await self.conn.execute(
            "DELETE FROM findings WHERE id = ?", (finding_id,)
        )
        await self.conn.commit()
        return cursor.rowcount > 0

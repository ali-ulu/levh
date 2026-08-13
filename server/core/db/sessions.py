"""Session rows and connector sync bookkeeping.

A slice of :class:`server.core.database.Database`, split out to keep each
file readable. Bodies are unchanged from the single-file version.
"""

from __future__ import annotations

import json
from typing import Optional

import aiosqlite



class SessionQueries:
    """Session rows and connector sync bookkeeping."""

    async def clear_all_sessions(self) -> int:
        """Delete every session. Used by a replace-mode restore."""
        cursor = await self.conn.execute("DELETE FROM sessions")
        await self.conn.commit()
        return cursor.rowcount

    async def insert_session(self, session: dict) -> None:
        await self.conn.execute(
            """
            INSERT OR REPLACE INTO sessions
                (id, name, status, metadata, memory_count, created_at, ended_at)
            VALUES
                (:id, :name, :status, :metadata, :memory_count, :created_at, :ended_at)
            """,
            {**session, "metadata": json.dumps(session.get("metadata", {}))},
        )
        await self.conn.commit()

    async def get_session(self, session_id: str) -> Optional[dict]:
        cursor = await self.conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
        row = await cursor.fetchone()
        await cursor.close()
        return self._row_to_session(row) if row else None

    async def get_all_sessions(self, limit: int = 100) -> list[dict]:
        cursor = await self.conn.execute(
            "SELECT * FROM sessions ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [self._row_to_session(r) for r in rows]

    async def count_session_memories(self, session_id: str) -> int:
        cursor = await self.conn.execute(
            "SELECT COUNT(*) FROM memories WHERE session_id = ?", (session_id,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        return row[0]

    async def update_session(self, session_id: str, updates: dict) -> bool:
        sets = []
        params = []
        for key, val in updates.items():
            if key == "metadata":
                val = json.dumps(val) if val is not None else None
            sets.append(f"{key} = ?")
            params.append(val)
        if not sets:
            return False
        params.append(session_id)
        cursor = await self.conn.execute(
            f"UPDATE sessions SET {', '.join(sets)} WHERE id = ?", params
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    @staticmethod
    def _row_to_session(row: aiosqlite.Row) -> dict:
        d = dict(row)
        raw = d.get("metadata")
        d["metadata"] = json.loads(raw) if raw else {}
        return d

    async def get_sync_state(self, source_key: str) -> Optional[dict]:
        cursor = await self.conn.execute(
            "SELECT * FROM connector_sync WHERE source_key = ?", (source_key,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        return dict(row) if row else None

    async def list_sync_states(self) -> list[dict]:
        cursor = await self.conn.execute(
            "SELECT * FROM connector_sync ORDER BY last_synced_at DESC"
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(r) for r in rows]

    async def record_sync(
        self,
        source_key: str,
        connector: str,
        project: Optional[str],
        last_synced_at: str,
        fetched: int,
        stored: int,
    ) -> None:
        """Upsert a connector's sync bookkeeping, accumulating totals/runs."""
        await self.conn.execute(
            """
            INSERT INTO connector_sync
                (source_key, connector, project, last_synced_at,
                 last_fetched, last_stored, total_stored, runs)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(source_key) DO UPDATE SET
                connector      = excluded.connector,
                project        = excluded.project,
                last_synced_at = excluded.last_synced_at,
                last_fetched   = excluded.last_fetched,
                last_stored    = excluded.last_stored,
                total_stored   = connector_sync.total_stored + excluded.last_stored,
                runs           = connector_sync.runs + 1
            """,
            (source_key, connector, project, last_synced_at, fetched, stored, stored),
        )
        await self.conn.commit()

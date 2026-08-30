"""Counts and groupings over the memory table.

A slice of :class:`server.core.database.Database`, split out to keep each
file readable. Bodies are unchanged from the single-file version.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Optional




class AggregateQueries:
    """Counts and groupings over the memory table."""

    async def count_memories(self, memory_type: Optional[str] = None) -> int:
        if memory_type:
            cursor = await self.conn.execute("SELECT COUNT(*) FROM memories WHERE memory_type = ?", (memory_type,))
        else:
            cursor = await self.conn.execute("SELECT COUNT(*) FROM memories")
        row = await cursor.fetchone()
        await cursor.close()
        return row[0]

    async def count_demo_memories(self) -> int:
        """How many memories the demo seeded, as an aggregate.

        The onboarding status endpoint needs one number. It used to get it by
        loading every memory — ``SELECT *``, embeddings included, each row
        deserialised into a ``Memory`` — and counting in Python, so the cost of
        showing a demo badge grew with the corpus it was reporting on.

        ``demo`` is a key inside the JSON ``metadata`` column and the Python
        side tested it with ``bool(...)``, so this has to reproduce *Python*
        truthiness rather than SQLite's. Absent, JSON null, ``false``, ``0``,
        ``""``, ``[]`` and ``{}`` are falsy; everything else counts — including
        the string ``"false"``, which is truthy in Python and would be a real
        divergence if this were written the obvious way.
        """
        try:
            cursor = await self.conn.execute(
                """
                SELECT COUNT(*) FROM memories
                 WHERE metadata IS NOT NULL
                   AND json_valid(metadata)
                   AND json_extract(metadata, '$.demo') IS NOT NULL
                   AND json_extract(metadata, '$.demo') NOT IN (0, '', '[]', '{}')
                """
            )
        except sqlite3.OperationalError:
            # SQLite built without the JSON1 extension. Nothing else in the
            # codebase relies on it yet, so the count falls back rather than
            # making onboarding fail on an unusual build.
            return await self._count_demo_memories_without_json1()
        row = await cursor.fetchone()
        await cursor.close()
        return row[0]

    async def _count_demo_memories_without_json1(self) -> int:
        """The same count where ``json_extract`` is unavailable.

        Still bounded where it actually mattered: only the ``metadata`` column
        is read, so the embeddings are never pulled off disk and no ``Memory``
        objects are built.
        """
        cursor = await self.conn.execute(
            "SELECT metadata FROM memories WHERE metadata IS NOT NULL"
        )
        rows = await cursor.fetchall()
        await cursor.close()
        total = 0
        for row in rows:
            try:
                parsed = json.loads(row[0])
            except (TypeError, ValueError):
                continue
            if isinstance(parsed, dict) and bool(parsed.get("demo")):
                total += 1
        return total

    async def count_pinned(self) -> int:
        cursor = await self.conn.execute("SELECT COUNT(*) FROM memories WHERE pinned = 1")
        row = await cursor.fetchone()
        await cursor.close()
        return row[0]

    async def embedding_dimension_counts(self) -> dict[int, int]:
        """Count stored vectors by dimension for doctor/migration warnings."""
        cursor = await self.conn.execute(
            "SELECT embedding FROM memories WHERE embedding IS NOT NULL"
        )
        rows = await cursor.fetchall()
        await cursor.close()
        counts: dict[int, int] = {}
        for row in rows:
            try:
                dimension = len(json.loads(row[0]))
            except Exception:
                dimension = -1
            counts[dimension] = counts.get(dimension, 0) + 1
        return counts

    async def memory_aggregates(self) -> dict:
        """Aggregate stats over all persisted memories in one query."""
        cursor = await self.conn.execute(
            "SELECT COUNT(*), AVG(importance), AVG(hscore) FROM memories"
        )
        row = await cursor.fetchone()
        await cursor.close()
        return {
            "count": row[0] or 0,
            "avg_importance": row[1] or 0.0,
            "avg_hscore": row[2] or 0.0,
        }

    async def list_projects(self) -> list[dict]:
        """Distinct projects with memory counts, most recent first."""
        cursor = await self.conn.execute(
            """
            SELECT project, COUNT(*) as count, MAX(created_at) as last_used
            FROM memories
            WHERE project IS NOT NULL AND project != ''
            GROUP BY project
            ORDER BY last_used DESC
            """
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [
            {"name": r[0], "memory_count": r[1], "last_used": r[2]} for r in rows
        ]

    async def list_sources(self) -> list[dict]:
        """Distinct sources (AI clients/tools) with memory counts."""
        cursor = await self.conn.execute(
            """
            SELECT source, COUNT(*) as count, MAX(created_at) as last_used
            FROM memories
            WHERE source IS NOT NULL AND source != ''
            GROUP BY source
            ORDER BY count DESC
            """
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [
            {"name": r[0], "memory_count": r[1], "last_used": r[2]} for r in rows
        ]

    async def list_tags(self) -> list[dict]:
        """All tags with usage counts (tags are stored as JSON arrays)."""
        cursor = await self.conn.execute(
            "SELECT tags FROM memories WHERE tags IS NOT NULL AND tags != '[]'"
        )
        rows = await cursor.fetchall()
        await cursor.close()
        counts: dict[str, int] = {}
        for (raw,) in rows:
            try:
                for tag in json.loads(raw):
                    counts[tag] = counts.get(tag, 0) + 1
            except (json.JSONDecodeError, TypeError):
                continue
        return [
            {"name": name, "count": count}
            for name, count in sorted(counts.items(), key=lambda kv: -kv[1])
        ]

    async def count_sessions(self, status: Optional[str] = None) -> int:
        if status:
            cursor = await self.conn.execute("SELECT COUNT(*) FROM sessions WHERE status = ?", (status,))
        else:
            cursor = await self.conn.execute("SELECT COUNT(*) FROM sessions")
        row = await cursor.fetchone()
        await cursor.close()
        return row[0]

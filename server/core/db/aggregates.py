"""Counts and groupings over the memory table.

A slice of :class:`server.core.database.Database`, split out to keep each
file readable. Bodies are unchanged from the single-file version.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Optional

from server.core.tenancy import current_workspace_id


class AggregateQueries:
    """Counts and groupings over the memory table."""

    def __init__(self, db) -> None:
        self._db = db

    @staticmethod
    def _workspace() -> str:
        """The workspace these aggregates are scoped to (#302)."""
        return current_workspace_id()

    @staticmethod
    def dimension_counts_from_rows(rows) -> dict[int, int]:
        """Count stored vectors by dimension for doctor/migration warnings.

        ``rows`` is an iterable of one-element sequences holding raw JSON
        embedding strings (the shape of the doctor query). Unparseable values
        count under the sentinel dimension ``-1`` so corruption stays visible
        instead of being dropped.
        """
        counts: dict[int, int] = {}
        for (raw_embedding,) in rows:
            try:
                dimension = len(json.loads(raw_embedding))
            except Exception:  # noqa: BLE001 - an unparsable embedding counts as unknown dimension
                dimension = -1
            counts[dimension] = counts.get(dimension, 0) + 1
        return counts

    async def count_memories(self, memory_type: Optional[str] = None) -> int:
        if memory_type:
            cursor = await self._db.conn.execute(
                "SELECT COUNT(*) FROM memories WHERE memory_type = ? "
                "AND COALESCE(workspace_id, 'default') = ?",
                (memory_type, self._workspace()),
            )
        else:
            cursor = await self._db.conn.execute(
                "SELECT COUNT(*) FROM memories WHERE COALESCE(workspace_id, 'default') = ?",
                (self._workspace(),),
            )
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
            cursor = await self._db.conn.execute(
                """
                SELECT COUNT(*) FROM memories
                 WHERE COALESCE(workspace_id, 'default') = ?
                   AND metadata IS NOT NULL
                   AND json_valid(metadata)
                   AND json_extract(metadata, '$.demo') IS NOT NULL
                   AND json_extract(metadata, '$.demo') NOT IN (0, '', '[]', '{}')
                """,
                (self._workspace(),),
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
        cursor = await self._db.conn.execute(
            "SELECT metadata FROM memories WHERE metadata IS NOT NULL "
            "AND COALESCE(workspace_id, 'default') = ?",
            (self._workspace(),),
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
        cursor = await self._db.conn.execute(
            "SELECT COUNT(*) FROM memories WHERE pinned = 1 "
            "AND COALESCE(workspace_id, 'default') = ?",
            (self._workspace(),),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return row[0]

    async def memory_aggregates(self) -> dict:
        """Aggregate stats over all persisted memories in one query."""
        cursor = await self._db.conn.execute(
            "SELECT COUNT(*), AVG(importance), AVG(hscore) FROM memories "
            "WHERE COALESCE(workspace_id, 'default') = ?",
            (self._workspace(),),
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
        cursor = await self._db.conn.execute(
            """
            SELECT project, COUNT(*) as count, MAX(created_at) as last_used
            FROM memories
            WHERE project IS NOT NULL AND project != ''
              AND COALESCE(workspace_id, 'default') = ?
            GROUP BY project
            ORDER BY last_used DESC
            """,
            (self._workspace(),),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [
            {"name": r[0], "memory_count": r[1], "last_used": r[2]} for r in rows
        ]

    async def list_sources(self) -> list[dict]:
        """Distinct sources (AI clients/tools) with memory counts."""
        cursor = await self._db.conn.execute(
            """
            SELECT source, COUNT(*) as count, MAX(created_at) as last_used
            FROM memories
            WHERE source IS NOT NULL AND source != ''
              AND COALESCE(workspace_id, 'default') = ?
            GROUP BY source
            ORDER BY count DESC
            """,
            (self._workspace(),),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [
            {"name": r[0], "memory_count": r[1], "last_used": r[2]} for r in rows
        ]

    async def list_tags(self) -> list[dict]:
        """All tags with usage counts (tags are stored as JSON arrays)."""
        cursor = await self._db.conn.execute(
            "SELECT tags FROM memories WHERE tags IS NOT NULL AND tags != '[]' "
            "AND COALESCE(workspace_id, 'default') = ?",
            (self._workspace(),),
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

    async def count_memories_matching_terms(self, terms: list[str]) -> int:
        """How many stored memories share at least one content term with
        ``terms``, ignoring every filter. Diagnostic only: tells an empty
        recall apart from a recall whose filter excluded a match.

        Matching uses SQLite's LIKE over each term rather than FTS, because the
        query here is a term list the caller already stemmed and the point is a
        cheap count, not a ranked hit set.
        """
        if not terms:
            return 0
        clauses = " OR ".join("lower(content) LIKE ?" for _ in terms)
        params = [f"%{term.lower()}%" for term in terms]
        cursor = await self._db.conn.execute(
            f"SELECT COUNT(*) FROM memories WHERE ({clauses}) "  # nosec B608 - clause count is the term count, values bound
            "AND COALESCE(workspace_id, 'default') = ?",
            (*params, self._workspace()),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return row[0]

    async def count_recall_mismatches(
        self,
        project: Optional[str] = None,
        session_id: Optional[str] = None,
        min_importance: float = 0.0,
    ) -> dict[str, int]:
        """Count stored memories in the recall's scope and how each filter
        narrows the pool.

        Returns ``in_scope_total`` (everything the filters would admit) and a
        per-filter exclusion count. The exclusion counts are per-filter, so a
        memory excluded by two filters is counted twice — the numbers are there
        to name the filter that emptied the recall, not to partition the table.
        """
        cursor = await self._db.conn.execute(
            "SELECT session_id, project, importance FROM memories "
            "WHERE COALESCE(workspace_id, 'default') = ?",
            (self._workspace(),),
        )
        rows = await cursor.fetchall()
        await cursor.close()

        in_scope = 0
        by_project = by_session = by_importance = 0
        for sess, proj, importance in rows:
            if project and proj != project:
                by_project += 1
                continue
            if session_id and sess != session_id:
                by_session += 1
                continue
            if min_importance and (importance or 0.0) < min_importance:
                by_importance += 1
                continue
            in_scope += 1
        return {
            "in_scope_total": in_scope,
            "excluded_by_project": by_project,
            "excluded_by_session": by_session,
            "excluded_by_importance": by_importance,
        }

    async def count_sessions(self, status: Optional[str] = None) -> int:
        if status:
            cursor = await self._db.conn.execute("SELECT COUNT(*) FROM sessions WHERE status = ?", (status,))
        else:
            cursor = await self._db.conn.execute("SELECT COUNT(*) FROM sessions")
        row = await cursor.fetchone()
        await cursor.close()
        return row[0]

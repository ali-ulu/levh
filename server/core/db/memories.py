"""Memory rows: insert, search, update, delete and the residue audit.

A slice of :class:`server.core.database.Database`, split out to keep each
file readable. Bodies are unchanged from the single-file version.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Optional

import aiosqlite

from server.core.lexical import terms as lexical_terms
from server.core.tenancy import DEFAULT_WORKSPACE_ID, current_workspace_id

logger = logging.getLogger(__name__)


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
    # A pre-tenancy row can still be NULL here if it has not been through the
    # backfill; the read path treats NULL as the default workspace (#302), so
    # the model must see the same value the queries match on — otherwise a row
    # the COALESCE guard deliberately keeps visible would be quarantined.
    if not d.get("workspace_id"):
        d["workspace_id"] = DEFAULT_WORKSPACE_ID
    return d


class MemoryQueries:
    """Memory rows: insert, search, update, delete and the residue audit."""

    def __init__(self, db) -> None:
        self._db = db

    @staticmethod
    def _workspace() -> str:
        """The workspace this access is scoped to (#302).

        Read at call time from the per-request context, not captured at
        construction: one engine serves every request in the process, so a
        workspace frozen on the query group would be the first caller's
        workspace forever.
        """
        return current_workspace_id()

    async def insert_memory(self, memory: dict) -> None:
        # The tenancy boundary is set by the store, never by the caller's dict
        # (#302): a caller cannot write a row into a workspace other than the
        # one its request is scoped to. The context default (``default``) keeps
        # single-user writes byte-identical to before this column existed.
        row = {**memory, "workspace_id": self._workspace()}
        await self._db.conn.execute(
            """
            INSERT OR REPLACE INTO memories
                (id, content, memory_type, embedding, importance, frequency,
                 tags, session_id, project, workspace_id, source, pinned, metadata, hscore,
                 created_at, accessed_at, decay_factor, stability_hours, recall_count,
                 valid_from, valid_to, superseded_by)
            VALUES
                (:id, :content, :memory_type, :embedding, :importance, :frequency,
                 :tags, :session_id, :project, :workspace_id, :source, :pinned, :metadata, :hscore,
                 :created_at, :accessed_at, :decay_factor, :stability_hours, :recall_count,
                 :valid_from, :valid_to, :superseded_by)
            """,
            {
                **row,
                "embedding": json.dumps(memory.get("embedding")) if memory.get("embedding") else None,
                "tags": json.dumps(memory.get("tags", [])),
                "metadata": json.dumps(memory.get("metadata", {})),
                "pinned": 1 if memory.get("pinned") else 0,
                # A fresh write is current as of its own creation unless the
                # caller states otherwise — the world-time clock starts here.
                "valid_from": memory.get("valid_from") or memory.get("created_at"),
                "valid_to": memory.get("valid_to"),
                "superseded_by": memory.get("superseded_by"),
            },
        )
        await self._db.commit()

    async def get_memory(self, memory_id: str) -> Optional[dict]:
        cursor = await self._db.conn.execute(
            "SELECT * FROM memories WHERE id = ? AND COALESCE(workspace_id, 'default') = ?",
            (memory_id, self._workspace()),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return self._row_to_memory(row) if row else None

    async def get_all_memories(
        self, limit: int = 10000, across_workspaces: bool = False
    ) -> list[dict]:
        """Rows ordered by recency.

        Scoped to the current workspace by default. ``across_workspaces`` is
        the explicit opt-in the cache-mirroring and maintenance callers need
        (#302): the process-local vector store and short-term deque mirror the
        *whole* store, and workspace separation for reads from that mirror is
        enforced in recall's candidate predicate — not by loading a partial
        mirror. A caller that lists memories for a user must use
        ``search_memories``, which is always scoped.
        """
        if across_workspaces:
            cursor = await self._db.conn.execute(
                "SELECT * FROM memories ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        else:
            cursor = await self._db.conn.execute(
                "SELECT * FROM memories WHERE COALESCE(workspace_id, 'default') = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (self._workspace(), limit),
            )
        rows = await cursor.fetchall()
        await cursor.close()
        return [self._row_to_memory(r) for r in rows]

    async def memories_after_rowid(
        self, rowid: int, limit: int = 10000
    ) -> tuple[list[dict], int]:
        """Rows inserted after ``rowid``, oldest first, with the new watermark.

        The auto-checkpoint delta cursor (#379). It deliberately orders on
        ``rowid`` — the store's own insertion sequence — rather than on
        ``created_at``: ``created_at`` is a wall-clock string whose resolution
        is the operating system's (15.625 ms on Windows, where ``datetime.now``
        is built on ``GetSystemTimeAsFileTime``). Two writes inside one tick
        carry the *same* string, so ``created_at > cutoff`` silently drops the
        newer one and ``created_at DESC`` cannot say which came last. Insertion
        order is exact on every platform and needs no clock at all.

        Returns ``(rows, watermark)`` where ``watermark`` is the ``rowid`` of
        the last row returned (unchanged when the page is empty), so a caller
        that pages with ``limit`` never skips a row. Not workspace-scoped: the
        delta summarizes the whole mirror, like ``across_workspaces`` above.
        """
        cursor = await self._db.conn.execute(
            "SELECT rowid AS _rowid, * FROM memories "
            "WHERE rowid > ? ORDER BY rowid ASC LIMIT ?",
            (rowid, limit),
        )
        raw = await cursor.fetchall()
        await cursor.close()
        rows: list[dict] = []
        watermark = rowid
        for record in raw:
            stored = dict(record)
            watermark = int(stored.pop("_rowid"))
            rows.append(self._row_to_memory(stored))
        return rows, watermark

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
        include_global: bool = False,
        as_of: Optional[str] = None,
        include_superseded: bool = False,
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

        # Tenancy boundary (#302) first, so no other filter can widen past it.
        # ``COALESCE`` reads a pre-backfill NULL as the default workspace rather
        # than letting the row vanish from every read.
        query += " AND COALESCE(memories.workspace_id, 'default') = ?"
        params.append(self._workspace())

        if memory_type:
            query += " AND memories.memory_type = ?"
            params.append(memory_type)
        if session_id:
            query += " AND memories.session_id = ?"
            params.append(session_id)
        if project:
            # ``include_global`` folds in the rows recorded without a project.
            # The widening has to happen *in* the query, not after it: the
            # caller applies LIMIT here, so filtering a truncated page in
            # Python would let unrelated pinned rows crowd out the global rule
            # that actually applies. Off by default — every other caller wants
            # exact project matching, and the guard is the one caller that does
            # not.
            if include_global:
                query += " AND (memories.project = ? OR memories.project IS NULL)"
            else:
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

        # Bi-temporal filter (#335). A retired fact stays in the store and
        # stays auditable, but it is not *current*, so it must not surface in
        # an ordinary read. The filter runs in SQL, before LIMIT, for the same
        # reason ``include_global`` does: a retired row that is filtered in
        # Python still consumed a slot the page would have given a live one.
        #
        # ``as_of`` asks a different question — "what did the store believe on
        # date D" — so it selects the rows whose validity interval *contains*
        # D, retired or not. A row with a NULL ``valid_from`` (never
        # backfilled) is treated as valid from the beginning of time so it is
        # never silently hidden from a point-in-time read.
        if as_of:
            query += (
                " AND (memories.valid_from IS NULL OR memories.valid_from <= ?)"
                " AND (memories.valid_to IS NULL OR memories.valid_to > ?)"
            )
            params.extend([as_of, as_of])
        elif not include_superseded:
            query += " AND memories.valid_to IS NULL"

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


    async def search_memory_ids_fts(self, query: str, limit: int = 50) -> list[str]:
        """Memory ids whose content matches ``query``, best (bm25) first.

        The hybrid-retrieval candidate source: FTS indexes content, so it
        reaches a row the vector store cannot — one without an embedding. It
        matches inflected forms the vector store never had a vector for.

        Terms are OR-ed, not AND-ed as the stored-content search does: a
        candidate fetch wants *any* term to pull a row, then ranks by bm25 and
        re-ranks by H(x,ψ). An AND query would demand every query word appear,
        so "which branch do we deploy from" would find nothing. Stopwords are
        dropped first so the OR is not swamped by function words. Empty when
        FTS5 is unavailable, so callers fall back cleanly.
        """
        fts_query = self._fts_or_query(query)
        if not fts_query or not self._db.fts5_available:
            return []
        # The FTS table carries no workspace column, so the boundary is joined
        # back to ``memories`` here (#302): a candidate fetch must not reach
        # across workspaces any more than a content read may.
        cursor = await self._db.conn.execute(
            "SELECT memories_fts.memory_id FROM memories_fts "
            "JOIN memories ON memories.id = memories_fts.memory_id "
            "WHERE memories_fts MATCH ? AND COALESCE(memories.workspace_id, 'default') = ? "
            "ORDER BY bm25(memories_fts) LIMIT ?",
            (fts_query, self._workspace(), limit),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [row[0] for row in rows]

    @staticmethod
    def _fts_or_query(text: str) -> str:
        """Prefix-OR FTS5 query over the content words of ``text``.

        ``""`` when there is nothing to search.
        """
        if not text:
            return ""
        return " OR ".join(f"{term}*" for term in sorted(lexical_terms(text))[:20])

    async def get_memories_by_ids(self, memory_ids: list[str]) -> list[dict]:
        """Fetch several rows in one query, preserving the caller's order.

        ``row_to_memory_dict`` is applied per row (never raw) so a caller sees
        model-shaped values, exactly like ``get_memory``.
        """
        if not memory_ids:
            return []
        placeholders = ",".join("?" for _ in memory_ids)
        cursor = await self._db.conn.execute(
            f"SELECT * FROM memories WHERE id IN ({placeholders}) "  # nosec B608 - placeholders are `?`, values bound
            "AND COALESCE(workspace_id, 'default') = ?",
            (*memory_ids, self._workspace()),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        by_id = {self._row_to_memory(r)["id"]: r for r in rows}
        return [self._row_to_memory(by_id[mid]) for mid in memory_ids if mid in by_id]


    async def content_exists(self, content: str, project: Optional[str] = None) -> bool:
        """Whether a memory with byte-for-byte identical ``content`` exists.

        The exact-duplicate check the admission gate uses when it has no
        trustworthy similarity signal (the hash embedder; see
        ``Embedder.is_semantic``). Filtered by ``project`` so a literal re-store
        in one workspace does not shadow the same text in another.
        """
        query = "SELECT 1 FROM memories WHERE content = ? AND COALESCE(workspace_id, 'default') = ?"
        params: list = [content, self._workspace()]
        if project is not None:
            query += " AND project = ?"
            params.append(project)
        query += " LIMIT 1"
        cursor = await self._db.conn.execute(query, params)
        row = await cursor.fetchone()
        await cursor.close()
        return row is not None

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
        # ``id`` alone identifies a row across workspaces, so the boundary has
        # to be in the WHERE as well: an update addressed by an id from another
        # workspace must not touch it (#302).
        params.append(memory_id)
        params.append(self._workspace())
        cursor = await self._db.conn.execute(
            f"UPDATE memories SET {', '.join(sets)} "  # nosec B608 - column names come from fixed callers, values bound
            "WHERE id = ? AND COALESCE(workspace_id, 'default') = ?",
            params,
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def retire_if_current(
        self, memory_id: str, valid_to: str, superseded_by: str
    ) -> bool:
        """Close a memory's validity window, but only while it is still open.

        A compare-and-set in one statement (#335). The write path runs it as a
        separate step from the weakening update because the candidate set is
        not filtered on validity: two concurrent stores can both select the
        same predecessor, and a plain UPDATE would let the loser overwrite the
        winner's ``valid_to``/``superseded_by`` — erasing the first retirement
        edge and letting a later delete reopen a window that was already
        closed. ``valid_to IS NULL`` in the WHERE makes the first retirement
        win atomically; ``rowcount`` tells the caller whether it was the one
        that closed the window, so only that caller updates its cached copy.
        """
        cursor = await self._db.conn.execute(
            "UPDATE memories SET valid_to = ?, superseded_by = ? "
            "WHERE id = ? AND valid_to IS NULL "
            "AND COALESCE(workspace_id, 'default') = ?",
            (valid_to, superseded_by, memory_id, self._workspace()),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def delete_memory(self, memory_id: str) -> bool:
        cursor = await self._db.conn.execute(
            "DELETE FROM memories WHERE id = ? AND COALESCE(workspace_id, 'default') = ?",
            (memory_id, self._workspace()),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def delete_memory_cascade(self, memory_id: str) -> bool:
        """Delete a memory and every derived row that references it.

        The operation is one SQLite transaction so a failed hard-delete cannot
        report success while entity, trust or conflict residues survive.
        Orphan entity rows are pruned after their final link disappears.
        """
        workspace = self._workspace()
        # Refuse before touching anything when the id is not in this workspace
        # (#302): the derived-row deletes below are keyed by memory id alone, so
        # without this guard a cross-workspace id would strip a peer's trust,
        # conflict and entity links before the memory DELETE reported no match.
        cursor = await self._db.conn.execute(
            "SELECT 1 FROM memories WHERE id = ? AND COALESCE(workspace_id, 'default') = ?",
            (memory_id, workspace),
        )
        if await cursor.fetchone() is None:
            await cursor.close()
            return False
        await cursor.close()
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
                "DELETE FROM memories WHERE id = ? AND COALESCE(workspace_id, 'default') = ?",
                (memory_id, workspace),
            )
            # Clear the supersession pointer on any memory this one replaced,
            # so a deleted replacement cannot leave its predecessor demoted in
            # recall forever. Custom-registered JSON functions make this safe
            # where json_extract is unavailable; the fallback is a no-op scan.
            # Scoped to this workspace (#302): supersession is a within-workspace
            # relation, so a cross-workspace row that happens to name this id
            # (an import that preserved the pointer) must not be touched.
            try:
                await self._db.conn.execute(
                    "UPDATE memories SET metadata = json_remove(metadata, '$.superseded_by', '$.superseded_at') "
                    "WHERE json_extract(metadata, '$.superseded_by') = ? "
                    "AND COALESCE(workspace_id, 'default') = ?",
                    (memory_id, workspace),
                )
            except sqlite3.OperationalError:
                logger.warning(
                    "supersession pointer cleanup skipped while deleting %s: "
                    "JSON functions unavailable",
                    memory_id,
                )
            # The bi-temporal columns mirror the metadata pointer (#335), so
            # deleting a replacement has to reopen its predecessor's validity
            # window — otherwise the fact would stay retired in every ordinary
            # read even though nothing replaced it now. This is a plain column
            # UPDATE, not JSON, so it stays outside the fallback above: it must
            # run even where the JSON functions are unavailable.
            await self._db.conn.execute(
                "UPDATE memories SET superseded_by = NULL, valid_to = NULL "
                "WHERE superseded_by = ? AND COALESCE(workspace_id, 'default') = ?",
                (memory_id, workspace),
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

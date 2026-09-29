"""Recall log — the ranked record of what a recall actually returned.

A slice of :class:`server.core.database.Database`, split out to keep each file
readable.

WHY THIS EXISTS. The benchmark in ``server/core/benchmark.py`` measures recall
quality on a corpus its author wrote, under an embedder CI can install. That
answers "did a change regress ranking on the fixtures"; it cannot answer "did
this store, holding *my* memories, answer *my* questions well". The number a
user actually wants — of the memories I was handed, how many were worth having
— needs the queries they really asked and the ids they really got back, in
order.

WHAT IT STORES. One row per recall: the query (redacted and truncated by the
caller before it gets here — this module stays pure SQL, like ``findings``,
whose ``detail`` column is likewise "already scrubbed" by whoever wrote it),
the ranked result ids as a JSON array, the filters in force, and whether the
recall reinforced. Position matters: precision computed from an unordered set
cannot separate gold-at-rank-1 from gold-at-rank-10.

WHAT IT DOES NOT STORE. No memory content, no scores, no embedding. Ids join
back to ``memories`` when someone wants the text, so a memory that is later
forgotten or redacted is not preserved here by accident.

RETENTION. The log holds user-typed queries, so it cannot grow forever.
``record_recall`` prunes rows older than ``prune_max_days`` on a time throttle
rather than on every insert — a DELETE per recall would make a read path write
twice, and a prune nobody schedules is a prune that never runs.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

#: How long the caller's prune signal stays valid, in seconds. One DELETE per
#: ten minutes of traffic is invisible next to the recall it follows; one per
#: recall would not be.
PRUNE_INTERVAL_SECONDS = 600


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RecallLogQueries:
    """Rows for the ``recall_log`` table."""

    def __init__(self, db) -> None:
        self._db = db
        self._last_prune_at: float | None = None

    async def record_recall(self, row: dict, prune_max_days: int | None = None) -> dict:
        """Append one ranked recall and, on a time throttle, apply retention.

        ``row`` is expected to be redacted by the caller. ``prune_max_days``
        enables retention for this call; ``None`` (or ``<= 0``) disables it,
        which is what the caller does when the feature is off or unbounded.

        Returns ``{"logged": bool, "pruned": int}`` so the caller can report
        the outcome without a second query. A repeat of a previous query is
        still appended — "we asked this twice and got a different list" is
        exactly the signal worth keeping.
        """
        cursor = await self._db.conn.execute(
            """
            INSERT INTO recall_log
                (query, query_sha256, result_ids, result_count, top_k,
                 project, session_id, reinforced, logged_at)
            VALUES
                (:query, :query_sha256, :result_ids, :result_count, :top_k,
                 :project, :session_id, :reinforced, :logged_at)
            """,
            {
                **row,
                "result_ids": (
                    row["result_ids"]
                    if isinstance(row["result_ids"], str)
                    else json.dumps(row["result_ids"])
                ),
                "reinforced": 1 if row.get("reinforced") else 0,
                "logged_at": row.get("logged_at") or _now(),
            },
        )
        await self._db.commit()
        return {"logged": cursor.rowcount > 0, "pruned": await self._maybe_prune(prune_max_days)}

    async def _maybe_prune(self, max_days: int | None) -> int:
        """Retention, throttled to one attempt per PRUNE_INTERVAL_SECONDS."""
        if not max_days or max_days <= 0:
            return 0
        now = datetime.now(timezone.utc).timestamp()
        if self._last_prune_at is not None and (now - self._last_prune_at) < PRUNE_INTERVAL_SECONDS:
            return 0
        self._last_prune_at = now
        return await self.prune_recall_log(max_days)

    async def prune_recall_log(self, max_days: int) -> int:
        """Delete rows older than ``max_days``. Returns rows removed.

        Public because retention must also be runnable on demand — an operator
        cleaning up now should not wait for the throttle, and a test should not
        have to fake a clock.
        """
        if max_days <= 0:
            return 0
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max_days)).isoformat()
        cursor = await self._db.conn.execute(
            "DELETE FROM recall_log WHERE logged_at < ?", (cutoff,)
        )
        await self._db.commit()
        return cursor.rowcount

    async def list_recall_log(self, limit: int = 100, since: str | None = None) -> list[dict]:
        """Most recent recalls first. ``result_ids`` is decoded to a list so a
        caller reading the log never has to know it is JSON on disk."""
        query = "SELECT * FROM recall_log"
        params: list = []
        if since:
            query += " WHERE logged_at >= ?"
            params.append(since)
        query += " ORDER BY logged_at DESC, id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 1000)))

        cursor = await self._db.conn.execute(query, params)
        rows = await cursor.fetchall()
        await cursor.close()
        out: list[dict] = []
        for r in rows:
            item = dict(r)
            try:
                item["result_ids"] = json.loads(item.get("result_ids") or "[]")
            except (TypeError, ValueError):
                # A row written by something other than this code path stays
                # readable rather than taking the whole listing down.
                item["result_ids"] = []
            item["reinforced"] = bool(item.get("reinforced"))
            out.append(item)
        return out

    async def recall_log_stats(self) -> dict:
        """Counts an operator needs before trusting any number derived from
        this table: how much there is, how varied it is, and how old."""
        cursor = await self._db.conn.execute(
            """
            SELECT COUNT(*) AS total,
                   COUNT(DISTINCT query_sha256) AS distinct_queries,
                   MIN(logged_at) AS oldest,
                   MAX(logged_at) AS newest
            FROM recall_log
            """
        )
        row = await cursor.fetchone()
        await cursor.close()
        found = dict(row) if row else {}
        return {
            "total": int(found.get("total") or 0),
            "distinct_queries": int(found.get("distinct_queries") or 0),
            "oldest": found.get("oldest"),
            "newest": found.get("newest"),
        }

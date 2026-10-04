"""Continuity emissions — one row per brief actually handed out.

A slice of :class:`server.core.database.Database`, split out to keep each file
readable.

WHY THIS EXISTS. The continuity story claimed "sessions start already
briefed", backed by construction: the brief is printed at startup and hooks
are installed. That says the brief was *built*, not that it was handed out,
and says nothing about whether the agent used it (issue #378). The
``continuity_log`` table supplies the measurement's denominator: briefs
EMITTED, named after the producer-side event on purpose so the number cannot
be read as a delivery guarantee.

WHAT IT STORES. One row per emission: the channel that emitted it
(``stderr_bridge``, ``mcp_tool``, ``session_hook``, ``cli``), the memory ids
the brief surfaced *in brief presentation order*, and the emission time. The
companion ``get_continuity_signals`` computes the same surface list, so a
later recall's ids can be compared against what a specific brief offered.

WHAT IT DOES NOT STORE. No brief text and no memory content — ids join back
to ``memories`` when someone wants the words, so a memory that is later
forgotten or redacted is not preserved here by accident.

RETENTION. The log is small (one row per session start at most) and carries
no free text, so it is not pruned; per-window counts are computed on read.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

#: Channels that hand out a brief. Emitter code is expected to name one of
#: these; an unknown channel is stored as-is so a new emitter is never
#: silently dropped from the count it exists to make.
CHANNELS = ("stderr_bridge", "mcp_tool", "session_hook", "cli")

#: How far back "recent emissions" looks for the use-rate window. A brief and
#: the work it briefed belong to one session; 24h bounds that pairing without
#: pretending to define a session boundary.
USE_WINDOW_HOURS = 24


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ContinuityLogQueries:
    """Rows for the ``continuity_log`` table."""

    def __init__(self, db) -> None:
        self._db = db

    async def record_brief_emission(
        self,
        channel: str,
        surfaced_ids: list[str],
        project: str | None = None,
        session_id: str | None = None,
        emitted_at: str | None = None,
    ) -> dict:
        """Append one emission row.

        An emission with nothing surfaced is still an emission — the hook ran
        and said nothing — but ``surfaced_ids`` may be empty only when the
        brief itself was empty; a non-empty brief always carries at least one
        id. Returns ``{"logged": bool}`` so the caller can report the outcome
        without a second query.
        """
        cursor = await self._db.conn.execute(
            """
            INSERT INTO continuity_log
                (channel, surfaced_ids, surfaced_count, project, session_id, emitted_at)
            VALUES
                (:channel, :surfaced_ids, :surfaced_count, :project, :session_id, :emitted_at)
            """,
            {
                "channel": channel,
                "surfaced_ids": json.dumps(list(surfaced_ids)),
                "surfaced_count": len(surfaced_ids),
                "project": project,
                "session_id": session_id,
                "emitted_at": emitted_at or _now(),
            },
        )
        await self._db.commit()
        return {"logged": cursor.rowcount > 0}

    async def list_brief_emissions(
        self, limit: int = 50, since: str | None = None
    ) -> list[dict]:
        """Most recent emissions first. ``surfaced_ids`` is decoded to a list
        so a caller reading the log never has to know it is JSON on disk."""
        query = "SELECT * FROM continuity_log"
        params: list = []
        if since:
            query += " WHERE emitted_at >= ?"
            params.append(since)
        query += " ORDER BY emitted_at DESC, id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 1000)))

        cursor = await self._db.conn.execute(query, params)
        rows = await cursor.fetchall()
        await cursor.close()
        out: list[dict] = []
        for r in rows:
            item = dict(r)
            try:
                item["surfaced_ids"] = json.loads(item.get("surfaced_ids") or "[]")
            except (TypeError, ValueError):
                # A row written by something other than this code path stays
                # readable rather than taking the whole listing down.
                item["surfaced_ids"] = []
            out.append(item)
        return out

    async def continuity_stats(self) -> dict:
        """The emission/use pair, computed over the whole log.

        ``briefs_emitted`` counts rows. ``briefs_with_content`` counts rows
        that actually surfaced at least one memory — an empty-brief emission
        is an honest row but not a content delivery, so the two counts stay
        separate rather than one number pretending to be both.

        ``memories_surfaced`` and ``distinct_memories_surfaced`` measure how
        much the brief channel puts in front of the agent. ``recalls_in_use_window``
        is the use signal: of the memories some recent brief surfaced, how many
        were subsequently recalled (per ``recall_log``), within
        ``USE_WINDOW_HOURS`` of now. It is the only number here that speaks to
        *use* rather than emission, and it needs both logs to exist — on a
        store where recall logging is off it is honestly 0.
        """
        # Decoded in Python rather than aggregated in SQL: the counts span
        # rows and JSON elements, and the log is bounded (one row per session
        # start), so a thousand-row read is the whole dataset.
        emissions = await self.list_brief_emissions(limit=1000)
        total = len(emissions)
        with_content = sum(1 for e in emissions if e["surfaced_count"] > 0)
        surfaced = sum(e["surfaced_count"] for e in emissions)
        distinct: set[str] = set()
        for e in emissions:
            distinct.update(e["surfaced_ids"])

        oldest = min((e["emitted_at"] for e in emissions), default=None)
        newest = max((e["emitted_at"] for e in emissions), default=None)

        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=USE_WINDOW_HOURS)
        ).isoformat()

        recall_hits = 0
        if total:
            # A surfaced memory is "used" when a recall returned it in the use
            # window. recall_log keeps ranked ids per query, so a single SQL
            # fan-out over its ``result_ids`` answers the overlap; the set
            # difference on surfaced ids is applied in Python to keep the SQL
            # to one statement.
            cursor = await self._db.conn.execute(
                """
                SELECT DISTINCT value AS memory_id
                FROM recall_log, json_each(recall_log.result_ids)
                WHERE logged_at >= ?
                """,
                (cutoff,),
            )
            rows = await cursor.fetchall()
            await cursor.close()
            recalled = {row["memory_id"] for row in rows}
            surfaced_recent = {
                mid
                for e in emissions
                if e["emitted_at"] >= cutoff
                for mid in e["surfaced_ids"]
            }
            recall_hits = len(recalled & surfaced_recent)

        return {
            "briefs_emitted": total,
            "briefs_with_content": with_content,
            "memories_surfaced": surfaced,
            "distinct_memories_surfaced": len(distinct),
            "recalls_in_use_window": recall_hits,
            "use_window_hours": USE_WINDOW_HOURS,
            "oldest": oldest,
            "newest": newest,
        }

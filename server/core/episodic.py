"""Episodic Memory — SQLite-backed long-term memory storage."""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

from pydantic import ValidationError

from . import metrics
from .database import Database
from .types import Memory

logger = logging.getLogger(__name__)


def _why_invalid(exc: ValidationError) -> str:
    """One-line rendering of what the model rejected."""
    return "; ".join(
        "{}: {}".format(".".join(str(bit) for bit in err["loc"]), err["msg"])
        for err in exc.errors()
    )


def _row_to_memory(row: Mapping[str, Any]) -> Memory | None:
    """Build a :class:`Memory`, quarantining a row the model cannot accept.

    A store can hold values this build rejects: an older release wrote a
    different enum, or - the case seen in practice - an external tool wrote
    ``memory_type="long_term"`` straight into the SQLite file. Loading such a
    row raised straight out of ``MemoryEngine.initialize()``, so one bad row
    took the whole API down and every other memory became unreachable.

    Quarantined rows are skipped and named in a warning: the store stays
    readable, and the bad row stays visible instead of vanishing silently.
    """
    try:
        return Memory(**row)
    except ValidationError as exc:
        logger.warning(
            "quarantined invalid memory row id=%s: %s",
            row.get("id"),
            _why_invalid(exc),
        )
        metrics.inc("levh_memory_rows_quarantined_total")
        return None


class EpisodicMemory:
    """Long-term memory layer persisted in SQLite via the Database layer."""

    def __init__(self, db: Database):
        self.db = db

    async def store(self, memory: Memory) -> Memory:
        await self.db.insert_memory(memory.model_dump())
        return memory

    async def get(self, memory_id: str) -> Optional[Memory]:
        row = await self.db.get_memory(memory_id)
        return _row_to_memory(row) if row else None

    async def get_all(self, limit: int = 10000, across_workspaces: bool = False) -> list[Memory]:
        rows = await self.db.get_all_memories(limit, across_workspaces=across_workspaces)
        return [m for m in (_row_to_memory(r) for r in rows) if m]

    async def search(
        self,
        memory_type: str | None = None,
        session_id: str | None = None,
        project: str | None = None,
        source: str | None = None,
        tag: str | None = None,
        pinned: bool | None = None,
        min_importance: float | None = None,
        content_like: str | None = None,
        include_global: bool = False,
        as_of: str | None = None,
        include_superseded: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Memory]:
        rows = await self.db.search_memories(
            memory_type=memory_type,
            session_id=session_id,
            project=project,
            source=source,
            tag=tag,
            pinned=pinned,
            min_importance=min_importance,
            content_like=content_like,
            include_global=include_global,
            as_of=as_of,
            include_superseded=include_superseded,
            limit=limit,
            offset=offset,
        )
        return [m for m in (_row_to_memory(r) for r in rows) if m]

    async def search_fts_ids(self, query: str, limit: int = 50) -> list[str]:
        """Ids matching ``query`` by full-text (FTS5) search, best first.

        The candidate source the vector store cannot provide for a row that
        has no embedding: FTS indexes content, not vectors, so it still finds
        it. Empty when FTS5 is unavailable (LIKE-fallback runtimes), and recall
        degrades to the vector-only behaviour it had before.
        """
        return await self.db.search_memory_ids_fts(query, limit=limit)

    async def get_many(self, memory_ids: list[str]) -> list[Memory]:
        """Resolve ids to memories, skipping ids that no longer exist.

        Reads straight from SQLite (the source of truth), so a candidate found
        by FTS but not yet in the process-local vector store is still returned,
        and a peer's concurrent delete simply drops out. One query, not one per
        id: this runs on the recall hot path.
        """
        if not memory_ids:
            return []
        rows = await self.db.get_memories_by_ids(memory_ids)
        return [m for m in (_row_to_memory(r) for r in rows) if m]

    async def update(self, memory: Memory) -> bool:
        updates = memory.model_dump(exclude={"id"})
        # Serialize list/dict fields that DB layer expects
        return await self.db.update_memory(memory.id, updates)

    async def delete(self, memory_id: str) -> bool:
        return await self.db.delete_memory(memory_id)

    async def count(self, memory_type: str | None = None) -> int:
        return await self.db.count_memories(memory_type)

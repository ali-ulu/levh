"""Entity index service for the persistent knowledge graph.

This module owns entity extraction persistence and entity-index helpers used by
trust and conflict scoring. It deliberately depends on storage interfaces, not
on MemoryEngine.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Callable

from . import trust
from .database import Database
from .entities import extract_entities
from .episodic import EpisodicMemory
from .types import Memory

EventEmitter = Callable[[str, dict], None]


class EntityIndexService:
    """Build and query the persistent entity graph."""

    def __init__(
        self,
        db: Database,
        episodic: EpisodicMemory,
        emit: EventEmitter,
    ) -> None:
        self.db = db
        self.episodic = episodic
        self._emit = emit

    async def reindex_entities(self) -> dict:
        """Rebuild the persistent entity graph from every stored memory.

        Extract typed entities and their memory links so graph queries can run
        as joins instead of full memory scans. Idempotent: clears and rebuilds.
        """
        now = datetime.now(timezone.utc).isoformat()
        # Content and metadata only: the corpus-wide scan must not drag every
        # embedding blob through JSON decoding (see search_memories).
        memories = await self.episodic.search(limit=1_000_000, fetch_embeddings=False)

        # Extracting entities from every memory is pure CPU (text scanning
        # over the whole corpus). On the event loop it starved every concurrent
        # request for tens of seconds while the first graph read rebuilt the
        # index, so all the NER work is planned in a worker thread first; only
        # the DB writes below stay on the loop.
        def _extract_entities() -> list[tuple[str, list[dict]]]:
            return [(memory.id, extract_entities(memory)) for memory in memories]

        per_memory = await asyncio.to_thread(_extract_entities)

        # The same rebuild issued one DB round-trip per row before, which made
        # the first graph read after startup crawl even with the NER off the
        # loop. Two executemany batches instead keep the write volume constant
        # and the read fast.
        await self.db.clear_entity_graph()
        entity_rows: list[tuple[str, str, str, str, str, str]] = []
        link_rows: list[tuple[str, str, str | None]] = []
        seen_entities: set[str] = set()
        for memory_id, found in per_memory:
            for entity in found:
                entity_id = f"{entity['type']}:{entity['key']}"
                entity_rows.append(
                    (entity_id, entity["type"], entity["key"], entity["name"], now, now)
                )
                link_rows.append((memory_id, entity_id, entity.get("role")))
                seen_entities.add(entity_id)
        await self.db.upsert_entities_many(entity_rows)
        await self.db.link_memory_entities_many(link_rows)
        links = len(link_rows)
        await self.db.commit()

        counts = await self.db.entity_type_counts()
        self._emit("entities_reindexed", {"entities": len(seen_entities), "links": links})
        return {
            "memories": len(memories),
            "entities": len(seen_entities),
            "links": links,
            "by_type": counts,
        }

    async def list_entities_graph(
        self,
        entity_type: str | None = None,
        limit: int = 200,
        offset: int = 0,
        query: str | None = None,
    ) -> list[dict]:
        """List persisted entities, optionally filtered by type and name query."""
        return await self.db.list_entities(
            etype=entity_type,
            limit=min(max(limit, 1), 2000),
            offset=max(offset, 0),
            query=query or None,
        )

    async def get_entity(self, query: str, entity_type: str | None = None) -> dict | None:
        """Resolve a query to an entity profile with memories and neighbors."""
        entity_id = await self.db.find_entity(query, etype=entity_type)
        if not entity_id:
            return None
        row = await self.db.get_entity_row(entity_id)
        memory_ids = await self.db.entity_memory_ids(entity_id, limit=100)
        memories = []
        for memory_id in memory_ids:
            memory = await self.episodic.get(memory_id)
            if memory:
                memories.append(memory.model_dump(exclude={"embedding"}))
        memories.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        related = await self.db.entity_neighbors(entity_id, limit=20)
        return {"entity": row, "memories": memories, "related": related}

    async def entity_graph_stats(self) -> dict:
        """Counts of persisted entities by type."""
        return {"by_type": await self.db.entity_type_counts()}

    @staticmethod
    def entity_ids_for(memory: Memory) -> list[str]:
        return [f"{entity['type']}:{entity['key']}" for entity in extract_entities(memory)]

    @classmethod
    def build_entity_index(cls, memories: list[Memory]) -> dict[str, list[tuple[str, str]]]:
        """Return entity_id -> list of (memory_id, source_type)."""
        index: dict[str, list[tuple[str, str]]] = {}
        for memory in memories:
            source_type = trust.source_type(memory.source)
            for entity_id in cls.entity_ids_for(memory):
                index.setdefault(entity_id, []).append((memory.id, source_type))
        return index

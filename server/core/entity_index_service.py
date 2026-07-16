"""Entity index service for the persistent knowledge graph.

This module owns entity extraction persistence and entity-index helpers used by
trust and conflict scoring. It deliberately depends on storage interfaces, not
on MemoryEngine.
"""

from __future__ import annotations

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
        memories = await self.episodic.search(limit=1_000_000)

        await self.db.clear_entity_graph()
        entity_ids: set[str] = set()
        links = 0
        for memory in memories:
            for entity in extract_entities(memory):
                entity_id = f"{entity['type']}:{entity['key']}"
                await self.db.upsert_entity(
                    entity_id,
                    entity["type"],
                    entity["key"],
                    entity["name"],
                    now,
                )
                await self.db.link_memory_entity(memory.id, entity_id, entity.get("role"))
                entity_ids.add(entity_id)
                links += 1
        await self.db.commit()

        counts = await self.db.entity_type_counts()
        self._emit("entities_reindexed", {"entities": len(entity_ids), "links": links})
        return {
            "memories": len(memories),
            "entities": len(entity_ids),
            "links": links,
            "by_type": counts,
        }

    async def list_entities_graph(
        self, entity_type: str | None = None, limit: int = 200
    ) -> list[dict]:
        """List persisted entities, optionally filtered by type."""
        return await self.db.list_entities(
            etype=entity_type,
            limit=min(max(limit, 1), 2000),
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

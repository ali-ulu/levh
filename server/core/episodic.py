"""Episodic Memory — SQLite-backed long-term memory storage."""

from __future__ import annotations

from typing import Optional

from .database import Database
from .types import Memory, MemoryType


class EpisodicMemory:
    """Long-term memory layer persisted in SQLite via the Database layer."""

    def __init__(self, db: Database):
        self.db = db

    async def store(self, memory: Memory) -> Memory:
        await self.db.insert_memory(memory.model_dump())
        return memory

    async def get(self, memory_id: str) -> Optional[Memory]:
        row = await self.db.get_memory(memory_id)
        return Memory(**row) if row else None

    async def get_all(self, limit: int = 10000) -> list[Memory]:
        rows = await self.db.get_all_memories(limit)
        return [Memory(**r) for r in rows]

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
            limit=limit,
            offset=offset,
        )
        return [Memory(**r) for r in rows]

    async def update(self, memory: Memory) -> bool:
        updates = memory.model_dump(exclude={"id"})
        # Serialize list/dict fields that DB layer expects
        return await self.db.update_memory(memory.id, updates)

    async def delete(self, memory_id: str) -> bool:
        return await self.db.delete_memory(memory_id)

    async def count(self, memory_type: str | None = None) -> int:
        return await self.db.count_memories(memory_type)

"""Changing a stored memory's importance, pinning and short-term state.

Part of :class:`server.core.memory_engine.MemoryEngine`, split out to keep
each file readable. Mixins rather than separate services: the methods use the
engine's own state throughout, and moving the bodies unchanged is what makes
the split verifiable.
"""

from __future__ import annotations


from ..types import (
    Memory,
    MemoryType,
)


class MemoryAttributesMixin:
    """Changing a stored memory's importance, pinning and short-term state."""

    async def set_importance(self, memory_id: str, importance: float) -> bool:
        memory = await self.episodic.get(memory_id)
        if not memory:
            return False
        memory.importance = max(0.0, min(1.0, importance))
        memory.touch()
        await self.episodic.update(memory)
        self._refresh_memory_caches(memory)
        self._mark_derived_dirty()
        self._emit("updated", self._memory_event_payload(memory))
        return True

    async def set_pinned(self, memory_id: str, pinned: bool) -> Memory | None:
        """Pin/unpin a memory. Pinned memories never decay and sort first."""
        memory = await self.episodic.get(memory_id)
        if not memory:
            return None
        memory.pinned = pinned
        memory.touch()
        await self.episodic.update(memory)
        self._refresh_memory_caches(memory)
        self._mark_derived_dirty()
        self._emit("updated", self._memory_event_payload(memory))
        return memory

    async def consolidate(self, session_id: str | None = None) -> int:
        """Move short-term memories to episodic layer."""
        st_memories = list(self.short_term.get_all())  # copy to avoid mutating during iteration
        count = 0
        for m in st_memories:
            if session_id and m.session_id != session_id:
                continue
            m.memory_type = MemoryType.EPISODIC
            await self.episodic.update(m)
            self.short_term.remove(m.id)
            count += 1
        if count:
            self._emit("consolidated", {"count": count, "session_id": session_id})
        return count

    def clear_short_term(self) -> int:
        """Clear the in-memory short-term deque."""
        return self.short_term.clear()

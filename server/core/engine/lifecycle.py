"""Engine lifecycle and event fan-out.

Part of :class:`server.core.memory_engine.MemoryEngine`, split out to keep
each file readable. Mixins rather than separate services: the methods use the
engine's own state throughout, and moving the bodies unchanged is what makes
the split verifiable.
"""

from __future__ import annotations


from .helpers import EventListener
from ..types import (
    Memory,
    MemoryStats,
    MemoryType,
)


class MemoryLifecycleMixin:
    """Engine lifecycle and event fan-out."""

    async def initialize(self) -> None:
        """Connect to DB, load existing memories into vector store.

        Idempotent — safe to call more than once (lifespan + lazy getter).
        """
        if self._initialized:
            return
        # Guard against concurrent first-time initialization: two requests can
        # both pass the check above before either finishes, so serialize and
        # re-check inside the lock.
        async with self._init_lock:
            if self._initialized:
                return
            await self.db.connect()

            # Load all existing memories into the in-memory vector store
            all_memories = await self.episodic.get_all()
            for m in all_memories:
                if m.embedding:
                    self.vector_store.add(m)
                if m.memory_type == MemoryType.SHORT_TERM:
                    self.short_term.add(m)
            # Baseline for _sync_with_external_writes: this load IS current
            # as of this data_version, so the first recall() must not treat
            # it as stale and reload redundantly.
            self._known_data_version = await self.db.data_version()
            # Materialized graph/trust/conflict rows may come from an older
            # version or interrupted process. Reconcile lazily on first read.
            self._derived_dirty = True
            # Initialize agent tracker tables
            if self.agent_tracker:
                await self.agent_tracker.initialize()
            self._initialized = True

    async def _sync_with_external_writes(self) -> None:
        """Refresh the in-memory caches if a peer has written since our last
        sync — the fix for cross-process/cross-instance cache coherence.

        vector_store and short_term are process-local: two MemoryEngine
        instances sharing one SQLite file (two OS processes, or two engines
        in the same process, e.g. tests) do not otherwise learn about each
        other's create/update/delete until a restart reloads from SQLite,
        which is the source of truth. episodic reads (get_memory,
        list_memories) already go straight to SQLite on every call and were
        never affected; only the vector_store-backed candidate search in
        recall() was silently working from a stale snapshot.

        A single `PRAGMA data_version` query (see Database.data_version) is
        cheap enough to run before every recall(). On a miss — the common
        case, no peer wrote — this is that one query. On a hit, a full reload
        from episodic is O(corpus size); acceptable for a local, single-user
        tool with no distributed cache to invalidate incrementally, and only
        paid when something outside this connection actually changed.
        """
        async with self._sync_lock:
            version = await self.db.data_version()
            if self._known_data_version is not None and version == self._known_data_version:
                return
            all_memories = await self.episodic.get_all()
            self.vector_store.clear()
            self.short_term.clear()
            for m in all_memories:
                if m.embedding:
                    self.vector_store.add(m)
                if m.memory_type == MemoryType.SHORT_TERM:
                    self.short_term.add(m)
            self._known_data_version = version

    async def shutdown(self) -> None:
        await self.db.close()
        if self._embedder is not None:
            await self._embedder.aclose()
        self._initialized = False

    def subscribe(self, listener: EventListener) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)

    def unsubscribe(self, listener: EventListener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def _emit(self, event: str, payload: dict) -> None:
        for listener in list(self._listeners):
            try:
                listener(event, payload)
            except Exception:
                # A broken listener must never break memory operations.
                continue

    def _mark_derived_dirty(self) -> None:
        self._derived_dirty = True

    async def _ensure_derived_state(self) -> None:
        """Reconcile graph, conflicts and trust before a derived-state read.

        Mutations mark these materialized views dirty.  The first graph/trust/
        conflict read performs one deterministic rebuild, avoiding stale rows
        without forcing every high-volume ingest item to run an O(n²) conflict
        scan.
        """
        if not self._derived_dirty or self._refreshing_derived:
            return
        self._refreshing_derived = True
        try:
            await self.reindex_entities()
            await self.detect_conflict_candidates()
            await self.recompute_trust_scores()
            self._derived_dirty = False
        finally:
            self._refreshing_derived = False

    @staticmethod
    def _memory_event_payload(memory: Memory) -> dict:
        data = memory.model_dump(exclude={"embedding"})
        return data

    async def get_stats(self) -> MemoryStats:
        st_count = len(self.short_term)
        ep_count = await self.episodic.count()
        ses_count = await self.db.count_sessions(status="active")
        pinned_count = await self.db.count_pinned()
        projects = await self.db.list_projects()

        aggregates = await self.db.memory_aggregates()

        return MemoryStats(
            total_memories=ep_count,
            short_term_count=st_count,
            episodic_count=await self.db.count_memories("episodic"),
            avg_hscore=round(aggregates["avg_hscore"], 4),
            avg_importance=round(aggregates["avg_importance"], 4),
            sessions_count=ses_count,
            pinned_count=pinned_count,
            projects_count=len(projects),
        )

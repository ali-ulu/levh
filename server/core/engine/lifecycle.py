"""Engine lifecycle and event fan-out.

Part of :class:`server.core.memory_engine.MemoryEngine`, split out to keep
each file readable. Mixins rather than separate services: the methods use the
engine's own state throughout, and moving the bodies unchanged is what makes
the split verifiable.
"""

from __future__ import annotations

import asyncio

from .helpers import EventListener, logger
from ..types import (
    Memory,
    MemoryStats,
    MemoryType,
)

# A persistently failing rebuild step used to loop hot: the swallowed error
# left ``_derived_dirty`` set, the unconditional self-reschedule in the
# ``finally`` fired immediately, and ~100k retries/second burned CPU with no
# signal (issue #136). Rebuild failures now retry with capped backoff and
# surface via logging; a fresh write (``_mark_derived_dirty``) restarts the
# cycle at once because it may have removed the cause.
_DERIVED_RETRY_BACKOFF_SECONDS = 0.5
_DERIVED_RETRY_BACKOFF_CAP_SECONDS = 30.0
_DERIVED_RETRY_LOG_EVERY = 5


def _has_running_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


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
        """Schedule a derived-state rebuild without blocking the caller (issue #102).

        Mutations mark graph/trust/conflict views dirty.  A read used to run
        the whole rebuild inline — reindex_entities -> detect_conflict_candidates
        (O(n²) pairwise) -> recompute_trust_scores (full corpus) — so the first
        read after any write paid for all of it.  Now the read returns on the
        current (stale-ok) rows immediately and the rebuild runs as a
        background task, coalesced by ``_refreshing_derived`` so concurrent
        dirty reads schedule exactly one rebuild.  A write landing during a
        rebuild re-dirties the flag and the running pass schedules its own
        successor, so the views converge without the read ever waiting.

        Callers that need *fresh* rows (a verdict a human will act on right
        now) await :meth:`recompute_derived_state` instead.
        """
        if not self._derived_dirty:
            return
        if self._refreshing_derived:
            return
        self._refreshing_derived = True
        try:
            self._derived_task = asyncio.get_running_loop().create_task(
                self._rebuild_derived()
            )
        except RuntimeError:
            # No running loop (rare sync context): fall back to inline rebuild
            # rather than silently leaving the views stale forever.
            self._refreshing_derived = False
            await self._rebuild_derived()

    async def _rebuild_derived(self) -> None:
        """Run one deterministic derived-state rebuild; the sole writer of
        ``_derived_dirty = False`` for its own pass."""
        try:
            await self.reindex_entities()
            await self.detect_conflict_candidates()
            await self.recompute_trust_scores()
            self._derived_dirty = False
            self._derived_retry_count = 0
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a failed rebuild must not kill the task
            self._derived_retry_count += 1
            count = self._derived_retry_count
            if count == 1 or count % _DERIVED_RETRY_LOG_EVERY == 0:
                logger.warning(
                    "derived-state rebuild failed (attempt %d, retrying with "
                    "backoff)",
                    count,
                    exc_info=True,
                )
            delay = min(
                _DERIVED_RETRY_BACKOFF_SECONDS * (2 ** (count - 1)),
                _DERIVED_RETRY_BACKOFF_CAP_SECONDS,
            )
            await asyncio.sleep(delay)
        finally:
            self._refreshing_derived = False
            # Writes that landed mid-rebuild re-dirtied the flag; schedule the
            # successor so the views still converge. The flag is still set
            # after a failure too, so the backoff'd retry above is what keeps
            # this from spinning hot. Re-entry only when a loop is running:
            # without one, _ensure_derived_state would fall back to an inline
            # rebuild whose finally re-enters here — unbounded recursion.
            if self._derived_dirty and _has_running_loop():
                await self._ensure_derived_state()

    async def recompute_derived_state(self) -> None:
        """Rebuild derived state NOW and return when it is fresh.

        The opt-in freshness boundary (issue #102): MCP/HTTP paths and flows
        whose next line acts on the verdict — restore, review decisions —
        await this; ordinary reads go stale-ok through
        :meth:`_ensure_derived_state`.
        """
        self._derived_dirty = True
        await self._rebuild_derived_inline()

    async def _rebuild_derived_inline(self) -> None:
        """Inline rebuild for freshness-required callers. Serialized against a
        running background pass by awaiting its task first."""
        task = self._derived_task
        if task is not None and not task.done():
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 — the inline pass below decides freshness
                pass
        await self._rebuild_derived()

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

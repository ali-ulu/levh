"""Engine lifecycle and event fan-out.

Part of :class:`server.core.memory_engine.MemoryEngine`, split out to keep
each file readable. Mixins rather than separate services: the methods use the
engine's own state throughout, and moving the bodies unchanged is what makes
the split verifiable.
"""

from __future__ import annotations

import asyncio

from .helpers import EventListener, logger
from .. import metrics
from ..types import (
    Memory,
    MemoryStats,
    MemoryType,
)

# A persistently failing rebuild step used to loop hot: the swallowed error
# left ``_derived_dirty`` set, the unconditional self-reschedule in the
# ``finally`` fired immediately, and ~100k retries/second burned CPU with no
# signal (issue #136). Rebuild failures now retry with capped backoff and
# surface via logging; a fresh write (``_mark_derived_dirty``) clears the
# retry debt and interrupts the pending backoff sleep, because it may have
# removed the cause. The interrupted sleep still owes a debounce before the
# retry: an instant, unbounded retry per write reproduces the same hot loop
# on the write path (issue #166).
_DERIVED_RETRY_BACKOFF_SECONDS = 0.5
_DERIVED_RETRY_BACKOFF_CAP_SECONDS = 30.0
_DERIVED_RETRY_LOG_EVERY = 5
# Long enough that a burst of writes coalesces into a single retry, short
# enough that a write removing the cause is still followed by a fast rebuild.
_DERIVED_RETRY_DEBOUNCE_SECONDS = 0.1
# Cap on the passes one freshness read may run: a write landing during a pass
# makes another necessary. Past the cap the read fails loud rather than
# answering stale, and a write storm cannot hold the read open forever.
_DERIVED_INLINE_MAX_PASSES = 3


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

            # Load all existing memories into the in-memory vector store. The
            # mirror spans every workspace on purpose (#302): separation for
            # reads is enforced in recall's candidate predicate, and a partial
            # mirror would make a memory stored by one workspace unrecallable
            # from another request in this process.
            all_memories = await self.episodic.get_all(across_workspaces=True)
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
            all_memories = await self.episodic.get_all(across_workspaces=True)
            self.vector_store.clear()
            self.short_term.clear()
            for m in all_memories:
                if m.embedding:
                    self.vector_store.add(m)
                if m.memory_type == MemoryType.SHORT_TERM:
                    self.short_term.add(m)
            self._known_data_version = version

    async def shutdown(self) -> None:
        # Stop a pending background rebuild first: it holds the DB open and
        # a Windows teardown cannot unlink the file while it runs (seen as
        # PermissionError on tmp store cleanup after a failed rebuild).
        task = self._derived_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 - shutdown must complete, but log the failure
                logger.exception("background derived rebuild failed during shutdown")
            self._derived_task = None
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
            except Exception:  # noqa: BLE001 - a broken listener must never break memory operations
                # A broken listener must never break memory operations.
                continue

    def _mark_derived_dirty(self) -> None:
        """Flag derived views stale and restart the retry schedule.

        The write may have removed whatever was breaking the rebuild (a
        corrupt row deleted, a fix deployed), so the retry debt is cleared
        and any pending backoff sleep is interrupted — the next attempt runs
        immediately (issue #139: the counter previously only reset on
        success, so recovery latency grew to the cap even after the cause
        was gone).

        The reset also keeps a write storm from driving the retry rate.
        This path spawns at most one pass per failed attempt (the flag is
        raised for the whole pass), that pass owes its debounce before
        retrying, and its failure does not re-arm this path — writes and
        rebuild attempts stay decoupled (issue #166).
        """
        self._derived_dirty = True
        # Counts writes, not rebuilds: a pass compares this against its own
        # start to tell whether it covered everything it ran on top of.
        self._derived_write_count += 1
        self._derived_retry_wake.set()
        # Debt is read before the reset: only a pass that actually failed
        # needs this write to restart it (the failed pass no longer
        # self-schedules, issues #136/#139). Spawning on an ordinary first
        # write would put a rebuild on the loop that overlaps the caller's
        # own transaction — every write path shares one SQLite connection, so
        # a background pass issuing statements mid-`BEGIN IMMEDIATE` turned
        # into "cannot start a transaction within a transaction".
        retry_debt = self._derived_retry_count
        self._derived_retry_count = 0
        if (
            retry_debt
            and not self._refreshing_derived
            and _has_running_loop()
        ):
            self._refreshing_derived = True
            self._derived_task = asyncio.get_running_loop().create_task(
                self._rebuild_derived()
            )

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

    async def _rebuild_derived(self, *, retry: bool = True) -> None:
        """Run one deterministic derived-state rebuild; the sole writer of
        ``_derived_dirty = False`` for its own pass.

        Raises on failure — as a background task the exception is stored on
        the task; awaited inline it propagates to the freshness caller
        (issue #139). With ``retry=True`` (background path) a failure sleeps
        the capped backoff, interruptible by a fresh write, before raising;
        inline callers pass ``retry=False`` so a failing freshness read
        fails fast instead of blocking on someone else's retry schedule.

        An interrupted sleep still owes a debounce before the retry: without
        it a write storm — every write waking the sleep — buys one full
        rebuild per write and no backoff at all (issue #166).
        """
        # Writes that land while the steps below run are NOT covered by this
        # pass: the rows come from a snapshot taken before them. The flag stays
        # raised for the whole pass (so no read mistakes a half-rebuilt graph
        # for a fresh one) and the end only clears it when nothing was written
        # since — otherwise it survives and the successor in ``finally``
        # rebuilds on top, instead of the write being erased by an
        # unconditional ``_derived_dirty = False``.
        writes_at_start = self._derived_write_count
        try:
            # Held for the whole pass: every write path shares one SQLite
            # connection, and a pass leaves an implicit transaction open
            # between its DELETE and its commit, so a foreground
            # ``BEGIN IMMEDIATE`` (delete/restore) landing in that window
            # raises "cannot start a transaction within a transaction". The
            # lock serializes the two instead of letting them interleave.
            # The backoff sleep below stays OUTSIDE it: a failing pass must
            # not hold writers off for the backoff duration.
            async with self.db.write_lock:
                await self.reindex_entities()
                await self.detect_conflict_candidates()
                await self.recompute_trust_scores()
                self._derived_dirty = self._derived_write_count != writes_at_start
                self._derived_retry_count = 0
                # Stamp the pass: a later freshness read compares this against the
                # live store version to tell "nothing changed" from "a peer wrote".
                self._derived_data_version = await self.db.data_version()
            metrics.inc("levh_derived_rebuild_total", outcome="success")
        except asyncio.CancelledError:
            raise
        except Exception:
            # The flag is left raised for the same reason it is raised
            # throughout a pass: these views are stale until one completes.
            metrics.inc("levh_derived_rebuild_total", outcome="failure")
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
            if retry:
                wake = self._derived_retry_wake
                # Cleared before the sleep, then set again by any write
                # arriving during it: the write is what interrupts the
                # backoff. Left set from the write that caused this attempt,
                # the sleep below returns instantly and every write buys a
                # full rebuild (issue #166).
                wake.clear()
                try:
                    await asyncio.wait_for(wake.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
                else:
                    # A fresh write landed during the backoff. Honour #139's
                    # fast recovery without letting writes set the retry
                    # rate: retry after one debounce, not once per write.
                    await asyncio.sleep(
                        min(_DERIVED_RETRY_DEBOUNCE_SECONDS, delay)
                    )
            raise
        finally:
            self._refreshing_derived = False
            # Writes that landed mid-rebuild re-dirtied the flag; schedule the
            # successor so the views still converge. The successor path is
            # only taken when the rebuild SUCCEEDED — a failed background
            # pass must not self-reschedule (issue #136: that was the hot
            # loop; issue #139: the backoff sleep belongs to the background
            # retry cycle, not to inline freshness callers). The next retry
            # is scheduled by the next write (via _mark_derived_dirty) or by
            # the next stale-ok read.
            # A freshness caller rebuilds on top of this pass itself
            # (``recompute_derived_state`` holds ``_derived_inline_waiter`` for
            # its whole sequence), so spawning the successor here would pay for
            # the same rebuild twice — and, worse, land on the loop while that
            # caller's own transaction is open: every write path shares one
            # SQLite connection, so a background pass issuing statements
            # mid-transaction raises "cannot start a transaction within a
            # transaction" (same hazard as in _mark_derived_dirty).
            if (
                self._derived_dirty
                and not self._derived_retry_count
                and not self._derived_inline_waiter
                and _has_running_loop()
            ):
                await self._ensure_derived_state()

    async def recompute_derived_state(self) -> None:
        """Rebuild derived state NOW and return when it is fresh.

        The opt-in freshness boundary (issue #102): MCP/HTTP paths and flows
        whose next line acts on the verdict — restore, review decisions —
        await this; ordinary reads go stale-ok through
        :meth:`_ensure_derived_state`.

        Raises when the rebuild fails (issue #139): "return when it is
        fresh" must not silently hand back stale rows. Callers here act on
        the verdict — a loud failure beats a quiet wrong answer.

        A state that is already fresh returns untouched. The flag used to be
        raised unconditionally, so every freshness-required read paid a full
        corpus rebuild (entity reindex -> conflict candidates -> trust) even
        when the last pass had already covered every write: on a large store
        the graph, trust and conflict pages answered in ~25s each, every time,
        and a read that overlapped the startup pass waited it out and then
        rebuilt the same thing again. "Fresh" is what this contract asks for,
        and a clean flag plus an unchanged store version IS fresh.
        """
        if not self._derived_dirty and await self._derived_store_unchanged():
            return
        self._derived_dirty = True
        # Held for the whole inline sequence, so no pass in it spawns a
        # successor: this caller rebuilds on top of the running pass itself,
        # and a successor landing on the loop while the caller's own
        # transaction is open hits the shared connection with "cannot start a
        # transaction within a transaction" (see _mark_derived_dirty).
        self._derived_inline_waiter = True
        try:
            for _ in range(_DERIVED_INLINE_MAX_PASSES):
                await self._rebuild_derived_inline()
                if not self._derived_dirty:
                    return
        finally:
            self._derived_inline_waiter = False
        raise RuntimeError(
            "derived-state rebuild failed; derived views are stale "
            "(see levh.memory_engine warnings)"
        )

    async def _derived_store_unchanged(self) -> bool:
        """True when no other connection committed since the last successful
        rebuild.

        ``PRAGMA data_version`` is per-connection: this process's own writes
        do not move it (``_derived_dirty`` covers those), a peer process's
        commit does. The in-process flag alone cannot see the peer case, so
        the stamp is what keeps this early return honest rather than merely
        fast.
        """
        if self._derived_data_version is None:
            return False
        return await self.db.data_version() == self._derived_data_version

    async def _rebuild_derived_inline(self) -> None:
        """Inline rebuild for freshness-required callers. Serialized against a
        running background pass by awaiting its task first."""
        task = self._derived_task
        if task is not None and not task.done():
            try:
                await task
            except asyncio.CancelledError:
                pass
            # A failed background pass re-raised through the task; the inline
            # pass below retries once synchronously and its failure surfaces
            # through recompute_derived_state's staleness check (issue #139).
            except Exception:  # noqa: BLE001 - best-effort refresh; staleness caught on next read
                logger.exception("background derived refresh failed; inline retry follows")
            else:
                # The awaited pass succeeded. It cleared the flag only if no
                # write landed while it ran, and a clean flag means this
                # caller's answer is already fresh — rebuilding again would be
                # a second full pass for nothing, which is what a read
                # overlapping the startup pass used to pay.
                if not self._derived_dirty:
                    return
        await self._rebuild_derived(retry=False)

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

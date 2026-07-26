"""Memory Engine — Central orchestrator for the 3-layer memory system.

Coordinates ShortTermMemory (deque), EpisodicMemory (SQLite),
VectorStore (NumPy), and H(x,ψ) scoring.

Emits events ("stored", "updated", "deleted", "recalled", "consolidated",
"session_created", "session_ended") to registered listeners so transports
(WebSocket live feed) can stream activity in real time.
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone
from typing import Callable, Optional

from .database import Database
from .embedder import Embedder
from .entity_index_service import EntityIndexService
from .trust_service import TrustService
from .conflict_service import ConflictService
from .episodic import EpisodicMemory
from .hscore import HScoreCalculator, HScoreWeights, _env_float
from .short_term import ShortTermMemory
from .types import (
    Memory,
    MemoryStats,
    MemoryType,
    RecallResult,
    ScoreBreakdown,
    Session,
    SessionStatus,
)
from .vector_store import VectorStore

EventListener = Callable[[str, dict], None]


def _event_date(m: Memory) -> str:
    """Event date for a memory: ``metadata.captured_at`` (when a calendar
    event or email actually happened) over ``created_at`` (when it was
    captured into LEVH), truncated to ``YYYY-MM-DD``. Shared by
    ``timeline()``, ``briefing()``, and ``list_decisions()`` so "when did
    this happen" is answered identically everywhere."""
    captured_at = (m.metadata or {}).get("captured_at")
    day_source = captured_at if captured_at else m.created_at
    return (day_source or "")[:10]


def _event_when(m: Memory) -> str:
    """Full event timestamp for a memory (ISO): ``metadata.captured_at`` when
    present, else ``created_at``. Used by ``meeting_prep`` to order events on
    the clock, not just the day."""
    return ((m.metadata or {}).get("captured_at") or m.created_at or "")


# Commitment / open-action-item markers (English + Turkish). Shared by the
# meeting-prep relevance filter; ``briefing()`` keeps its own copy so its
# tested behaviour is insulated from changes here.
_COMMITMENT_PATTERN = re.compile(
    r"\bI['’]?ll\b|\bI will\b|\bwe['’]ll\b|\bwe will\b|\bgoing to\b|"
    r"\bneed to\b|\bTODO\b|\baction item\b|\bfollow[- ]?up\b|"
    r"yapacağ|göndereceğ|halledeceğ|takip ed",
    re.IGNORECASE,
)


def _first_marker_sentence(content: str, pattern: "re.Pattern", limit: int = 160) -> str | None:
    """Return the first sentence in ``content`` that matches ``pattern``
    (splitting on newlines then ``". "``), trimmed to ``limit`` chars, or
    None if nothing matches."""
    if not content or not pattern.search(content):
        return None
    segments: list[str] = []
    for line in content.split("\n"):
        segments.extend(line.split(". "))
    sentence = next((s for s in segments if pattern.search(s)), content)
    return sentence.strip()[:limit] or None


class MemoryEngine:
    """Singleton-like engine that coordinates all memory layers."""

    def __init__(
        self,
        db_path: str | None = None,
        embedder_mode: str | None = None,
        short_term_max: int | None = None,
    ):
        if db_path is None or embedder_mode is None or short_term_max is None:
            from .runtime_config import resolve_runtime_config

            runtime = resolve_runtime_config()
            db_path = db_path or runtime.database_path
            embedder_mode = embedder_mode or runtime.embedder_mode
            short_term_max = short_term_max if short_term_max is not None else runtime.short_term_max

        self.db = Database(db_path)
        self.short_term = ShortTermMemory(max_size=short_term_max)
        self.episodic = EpisodicMemory(self.db)
        self.vector_store = VectorStore()
        self.scorer = HScoreCalculator()
        self._initialized = False
        self._init_lock = asyncio.Lock()
        self._listeners: list[EventListener] = []
        self._derived_dirty = False
        self._refreshing_derived = False
        self.entity_index = EntityIndexService(self.db, self.episodic, self._emit)
        self.trust_service = TrustService(
            self.db,
            self.episodic,
            self.entity_index,
            self._emit,
        )
        self.conflict_service = ConflictService(
            self.db,
            self.episodic,
            self.entity_index,
            self._emit,
            self.memory_feedback,
            self._mark_derived_dirty,
        )

        # Retroactive interference: a new memory that is near-identical to an
        # older one weakens the older one (it is being superseded). The default
        # threshold (0.97) only fires on true near-duplicates in every embedder
        # mode; with a semantic embedder, 0.88-0.92 also catches contradictions.
        self.interference_threshold = _env_float("INTERFERENCE_THRESHOLD", 0.97)
        self.interference_factor = _env_float("INTERFERENCE_FACTOR", 0.6)

        # Auto-capture: on end_session, distill the session's memories into one
        # durable summary memory (LLM if available, extractive fallback else).
        self.auto_summarize = os.getenv(
            "AUTO_SUMMARIZE_SESSIONS", ""
        ).strip().lower() in ("1", "true", "yes", "on")

        # Lazy-init embedder (downloads model on first use)
        self._embedder: Embedder | None = None
        self._embedder_mode = embedder_mode

        # Cross-process/cross-instance cache coherence (see
        # _sync_with_external_writes). None until initialize() sets a
        # baseline; a synced engine's own writes never change its own view of
        # data_version, so this only ever detects a peer's writes. The lock
        # serializes concurrent refreshes on this engine (e.g. several
        # recall() calls arriving together right after a peer's write) so
        # they don't all pay for a redundant full reload.
        self._known_data_version: int | None = None
        self._sync_lock = asyncio.Lock()

    @property
    def embedder(self) -> Embedder:
        if self._embedder is None:
            self._embedder = Embedder(mode=self._embedder_mode)
            self.vector_store.dimension = self._embedder.dimension
        return self._embedder

    # ── Events ─────────────────────────────────────────────────────

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

    # ── Lifecycle ─────────────────────────────────────────────────

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

    # ── Memory Operations ─────────────────────────────────────────

    async def store(
        self,
        content: str,
        importance: float = 0.5,
        tags: list[str] | None = None,
        session_id: str | None = None,
        memory_type: str = "short_term",
        metadata: dict | None = None,
        project: str | None = None,
        source: str | None = None,
        pinned: bool = False,
    ) -> Memory:
        """Store a new memory. Persisted to SQLite + vector store always;
        added to the short-term deque only when it is a short-term memory."""
        try:
            mtype = MemoryType(memory_type)
        except ValueError as exc:
            valid = ", ".join(t.value for t in MemoryType)
            raise ValueError(
                f"invalid memory_type '{memory_type}'; expected one of: {valid}"
            ) from exc

        embedding = await self.embedder.embed(content)
        enriched_metadata = dict(metadata or {})
        enriched_metadata["embedding_provenance"] = self.embedder.identity()

        mem = Memory(
            content=content,
            embedding=embedding,
            importance=max(0.0, min(1.0, importance)),
            tags=tags or [],
            session_id=session_id,
            project=project,
            source=source,
            pinned=pinned,
            memory_type=mtype,
            metadata=enriched_metadata,
        )

        if mem.memory_type == MemoryType.SHORT_TERM:
            self.short_term.add(mem)
        self.vector_store.add(mem)
        await self.episodic.store(mem)

        await self._apply_interference(mem)

        if session_id:
            await self._refresh_session_count(session_id)

        self._mark_derived_dirty()
        self._emit("stored", self._memory_event_payload(mem))
        return mem

    async def _apply_interference(self, new_memory: Memory) -> list[str]:
        """Retroactive interference: storing something near-identical to an
        older memory weakens the older one — the new information supersedes it.
        Pinned memories are immune, and interference stays within the same
        project so unrelated workspaces never affect each other."""
        if not new_memory.embedding or self.interference_threshold >= 1.0:
            return []

        def _candidate(m: Memory) -> bool:
            return (
                m.id != new_memory.id
                and not m.pinned
                and m.project == new_memory.project
            )

        similar = self.vector_store.search(
            new_memory.embedding, top_k=5, predicate=_candidate
        )
        interfered: list[str] = []
        for old, similarity in similar:
            if similarity < self.interference_threshold:
                continue
            old.stability_hours = self.scorer.weaken(
                old.stability_hours, self.interference_factor
            )
            await self.db.update_memory(old.id, {"stability_hours": old.stability_hours})
            interfered.append(old.id)

        if interfered:
            self._emit(
                "interference",
                {
                    "new_id": new_memory.id,
                    "weakened_ids": interfered,
                    "content": new_memory.content[:90],
                },
            )
        return interfered

    async def recall(
        self,
        query: str,
        top_k: int = 10,
        session_id: str | None = None,
        project: str | None = None,
        min_importance: float = 0.0,
        reinforce: bool = True,
    ) -> RecallResult:
        """Recall memories ranked by H(x,ψ) score.

        Filters are applied BEFORE ranking so a filtered recall still returns
        up to top_k results. Only the memories actually returned get their
        access frequency / timestamp updated.

        Set ``reinforce=False`` for read-only recalls (e.g. dashboard search
        previews) so browsing the UI does not artificially strengthen memories
        or inflate their access frequency — only genuine AI recall should
        reinforce.
        """
        await self._sync_with_external_writes()
        query_embedding = await self.embedder.embed(query)

        def _predicate(memory: Memory) -> bool:
            if min_importance and memory.importance < min_importance:
                return False
            if session_id and memory.session_id != session_id:
                return False
            if project and memory.project != project:
                return False
            return True

        # Fetch extra candidates so H(x,ψ) re-ranking has room beyond raw similarity.
        candidates = self.vector_store.search(
            query_embedding, top_k=top_k * 3, predicate=_predicate
        )

        scored: list[tuple[Memory, float]] = []
        for memory, similarity in candidates:
            # Pinned memories are exempt from time decay. Everyone else decays
            # from their LAST ACCESS at their OWN stability (half-life), not a
            # global one — a memory that's been recalled often forgets slower.
            decay = (
                1.0
                if memory.pinned
                else self.scorer.compute_decay(memory.accessed_at, half_life_hours=memory.stability_hours)
            )
            hscore = self.scorer.compute(
                similarity=similarity,
                decay_factor=decay,
                importance=memory.importance,
                frequency=memory.frequency,
            )
            memory.hscore = hscore
            scored.append((memory, hscore))

        # Sort by score (lower = better relevance)
        scored.sort(key=lambda x: x[1])
        top = scored[:top_k]

        # Reinforce only the memories actually returned: recalling a memory
        # resets its decay clock AND makes it more durable (spaced repetition /
        # the testing effect) — untouched candidates are left completely alone.
        # A read-only recall (reinforce=False) skips this entirely.
        if reinforce:
            for memory, hscore in top:
                memory.stability_hours = self.scorer.reinforce(memory.stability_hours, memory.importance)
                memory.recall_count += 1
                memory.touch()
                memory.frequency += 1
                await self.db.update_memory(
                    memory.id,
                    {
                        "accessed_at": memory.accessed_at,
                        "frequency": memory.frequency,
                        "hscore": hscore,
                        "stability_hours": memory.stability_hours,
                        "recall_count": memory.recall_count,
                    },
                )

        self._emit(
            "recalled",
            {"query": query, "count": len(top), "ids": [m.id for m, _ in top]},
        )

        return RecallResult(
            memories=[m for m, _ in top],
            scores=[s for _, s in top],
        )

    async def ask(
        self,
        question: str,
        top_k: int = 6,
        session_id: str | None = None,
        project: str | None = None,
        min_importance: float = 0.0,
    ) -> dict:
        """Ask your memory a question and get a synthesized, cited answer.

        Recalls the most relevant memories (read-only — asking does not
        reinforce), then synthesizes a direct answer that cites them by number.
        Uses an LLM when OPENAI_API_KEY is set, otherwise returns the ranked
        evidence deterministically (fully offline). Returns a dict with the
        answer text and the source memories it was grounded in.
        """
        from .answerer import answer_question

        result = await self.recall(
            query=question,
            top_k=top_k,
            session_id=session_id,
            project=project,
            min_importance=min_importance,
            reinforce=False,  # asking is read-only; browsing must not reinforce
        )

        sources = [
            {
                "n": i,
                "id": m.id,
                "content": m.content,
                "created_at": m.created_at,
                "project": m.project,
                "score": round(score, 4),
            }
            for i, (m, score) in enumerate(zip(result.memories, result.scores), 1)
        ]

        client = self._embedder._http if self._embedder is not None else None
        answer = await answer_question(question, sources, mode="auto", client=client)

        self._emit("asked", {"question": question, "source_count": len(sources)})
        return {"question": question, "answer": answer, "sources": sources}

    async def forget(self, memory_id: str) -> bool:
        """Remove a memory and every derived reference atomically."""
        existing = await self.episodic.get(memory_id)
        self.short_term.remove(memory_id)
        self.vector_store.remove(memory_id)
        deleted = await self.db.delete_memory_cascade(memory_id)
        if deleted:
            if existing and existing.session_id:
                await self._refresh_session_count(existing.session_id)
            self._mark_derived_dirty()
            self._emit("deleted", {"id": memory_id})
        return deleted

    async def update_memory(
        self,
        memory_id: str,
        content: str | None = None,
        importance: float | None = None,
        tags: list[str] | None = None,
        project: str | None = None,
        pinned: bool | None = None,
        use_gate: bool = True,
        min_length: int = 3,
    ) -> Memory | None:
        """Update an existing memory.

        A content change goes through the same admission gate as a new memory
        *before* it is embedded or persisted. Writing a secret via ``PUT`` used
        to bypass the redaction that ``store``/``admit`` apply, so the same
        secret was redacted or kept depending only on which endpoint the caller
        reached — and the raw value also went into the embedding, where deleting
        the text later would not remove it.

        The gate does two separable jobs, and an update needs them differently:

          - **Content safety** (secret redaction, minimum length) applies
            exactly as it does on create. Enforced here.
          - **Corpus hygiene** (duplicate detection) exists to stop the store
            growing near-identical copies. An update does not grow the corpus,
            and editing a memory towards an existing one is a legitimate thing
            to do, so the duplicate verdict is recorded in metadata for review
            rather than blocking the write.

        The memory being updated is excluded from its own duplicate probe.
        ``use_gate=False`` is the audited administrative override, mirroring
        ``admit_memory(force=True)``; the decision is recorded either way.

        Metadata-only updates (``content is None``) never run the gate — there
        is no new content to judge.
        """
        memory = await self.episodic.get(memory_id)
        if not memory:
            return None

        if content is not None:
            admitted_content = content
            decision: dict | None = None
            if use_gate:
                decision = await self.evaluate_admission(
                    content,
                    project=project if project is not None else memory.project,
                    min_length=min_length,
                    exclude_id=memory_id,
                )
                # "reject" here can only mean too-short content; duplicates are
                # recorded, not blocking (see the docstring).
                if "too_short" in decision.get("reason_codes", []):
                    return None
                if decision["redacted"]:
                    admitted_content = decision["redacted_content"]

            memory.content = admitted_content
            memory.embedding = await self.embedder.embed(admitted_content)
            memory.metadata = dict(memory.metadata or {})
            memory.metadata["embedding_provenance"] = self.embedder.identity()
            memory.metadata["admission"] = {
                "action": decision["action"] if decision else "bypassed",
                "reasons": decision["reasons"] if decision else ["gate bypassed on update"],
                "reason_codes": decision["reason_codes"] if decision else ["gate_bypassed"],
                "redacted": bool(decision["redacted"]) if decision else False,
                "secrets": decision["secrets"] if decision else [],
                "max_similarity": decision["max_similarity"] if decision else 0.0,
                "forced": not use_gate,
                "on_update": True,
            }
        if importance is not None:
            memory.importance = max(0.0, min(1.0, importance))
        if tags is not None:
            memory.tags = tags
        if project is not None:
            memory.project = project
        if pinned is not None:
            memory.pinned = pinned

        memory.touch()
        await self.episodic.update(memory)

        # Funnel through the shared cache refresh like every other mutator, so
        # the in-memory layers cannot drift from what was just persisted.
        self._refresh_memory_caches(memory)

        self._mark_derived_dirty()
        self._emit("updated", self._memory_event_payload(memory))
        return memory

    def _refresh_memory_caches(self, memory: Memory) -> None:
        """Keep the in-memory layers in sync with a memory that was just
        mutated + persisted.

        recall() scores candidates from the vector store's *cached* Memory
        objects, so any mutation path that only writes to SQLite/episodic would
        leave ranking working off stale importance / pinned / stability values
        until the next process restart. Every mutator must funnel through here.
        """
        self.vector_store.add(memory)  # keyed by id → replaces the stale copy
        st = self.short_term.find(memory.id)
        if st is not None and st is not memory:
            st.content = memory.content
            st.importance = memory.importance
            st.tags = memory.tags
            st.project = memory.project
            st.pinned = memory.pinned
            st.stability_hours = memory.stability_hours

    async def get_memory(self, memory_id: str) -> Memory | None:
        """Get a single memory by ID."""
        return await self.episodic.get(memory_id)

    async def list_memories(
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
        return await self.episodic.search(
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

    # ── Consolidation ─────────────────────────────────────────────

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

    # ── Importance / Pinning ──────────────────────────────────────

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

    # ── Reinforcement (spaced-repetition-style memory strengthening) ──

    async def reinforce_memory(self, memory_id: str) -> Memory | None:
        """Manually strengthen a memory without a full recall — the explicit
        version of what happens automatically when a memory is recalled.
        Resets its decay clock and grows its stability (half-life)."""
        memory = await self.episodic.get(memory_id)
        if not memory:
            return None
        memory.stability_hours = self.scorer.reinforce(memory.stability_hours, memory.importance)
        memory.recall_count += 1
        memory.touch()
        await self.episodic.update(memory)
        self._refresh_memory_caches(memory)
        self._mark_derived_dirty()
        self._emit("updated", self._memory_event_payload(memory))
        return memory

    async def memory_feedback(self, memory_id: str, helpful: bool) -> Memory | None:
        """Learn from recall outcomes, SM-2 style.

        helpful=True  → the memory was correct/useful: reinforce it (resets
                        the decay clock, grows stability).
        helpful=False → the memory was wrong or stale: weaken its stability
                        WITHOUT resetting the clock, so it fades out quickly
                        unless someone rescues it (edit, pin, or reinforce).
        """
        memory = await self.episodic.get(memory_id)
        if not memory:
            return None
        if helpful:
            memory.stability_hours = self.scorer.reinforce(
                memory.stability_hours, memory.importance
            )
            memory.recall_count += 1
            memory.touch()
        else:
            memory.stability_hours = self.scorer.weaken(memory.stability_hours)
        await self.episodic.update(memory)
        self._refresh_memory_caches(memory)
        self._mark_derived_dirty()
        self._emit("updated", self._memory_event_payload(memory))
        return memory

    async def list_fading(
        self,
        threshold: float = 0.35,
        project: str | None = None,
        limit: int = 20,
    ) -> list[tuple[Memory, float]]:
        """Memories whose predicted retention has dropped below `threshold` —
        the 'about to be forgotten' review queue. Pinned memories never fade.
        Returns (memory, retention) pairs, most-faded first."""
        memories = await self.episodic.search(project=project, limit=2000)
        fading: list[tuple[Memory, float]] = []
        for m in memories:
            if m.pinned:
                continue
            retention = self.scorer.compute_decay(
                m.accessed_at, half_life_hours=m.stability_hours
            )
            if retention < threshold:
                fading.append((m, retention))
        fading.sort(key=lambda pair: pair[1])
        return fading[:limit]

    # ── Spaced-repetition review (Faz 5: close the lifecycle loop) ─

    async def review_queue(
        self, threshold: float = 0.5, project: str | None = None, limit: int = 20
    ) -> list[dict]:
        """Memories due for a spaced-repetition review: fading (retention below
        ``threshold``), not pinned, and not currently snoozed. Each item carries
        the decay context a human needs to decide keep / reinforce / weaken /
        pin / forget / snooze. No LLM.

        Turns the passive fading queue into an active review flow — the last
        step of the lifecycle: store → recall → decay → **review** →
        reinforce/weaken/forget.
        """
        from datetime import datetime, timezone

        now_iso = datetime.now(timezone.utc).isoformat()
        # Pull a wider fading set, then drop snoozed ones and cap to `limit`.
        fading = await self.list_fading(
            threshold=threshold, project=project, limit=max(limit * 3, limit)
        )

        items: list[dict] = []
        for m, retention in fading:
            review = (m.metadata or {}).get("review") or {}
            due_at = review.get("review_due_at")
            if due_at and due_at > now_iso:
                continue  # snoozed — not due yet
            items.append(
                {
                    "id": m.id,
                    "content": m.content.split("\n", 1)[0][:160],
                    "project": m.project,
                    "source": m.source,
                    "importance": m.importance,
                    "hscore": m.hscore,
                    "retention": round(retention, 4),
                    "stability_hours": m.stability_hours,
                    "last_accessed": m.accessed_at,
                    "recall_count": m.recall_count,
                    "review_count": int(review.get("review_count", 0)),
                    "reason": (
                        f"retention {round(retention, 2)} is below {threshold} "
                        f"(stability {round(m.stability_hours, 1)}h, "
                        f"{m.recall_count} recalls)"
                    ),
                }
            )
            if len(items) >= limit:
                break
        return items

    async def apply_review(
        self,
        memory_id: str,
        action: str,
        snooze_days: int = 7,
        reason: str = "",
    ) -> dict:
        """Apply a review decision to a memory and record it auditable-y in
        ``metadata.review`` + ``metadata.review_history``. Actions:

          - ``keep``      — reset the decay clock (mild reinforcement), no
                            stability change; the memory stays active.
          - ``reinforce`` — strong stability increase (full reinforcement).
          - ``weaken``    — decrease stability so it fades faster.
          - ``pin``       — pin it (never decays, never auto-forgotten).
          - ``forget``    — remove via the existing forget path.
          - ``snooze``    — push the next review out by ``snooze_days`` without
                            changing decay.

        Pinned memories are never auto-forgotten; ``forget`` here is an explicit
        human decision, so it is honoured. Returns a small result dict.
        """
        from datetime import datetime, timedelta, timezone

        valid = {"keep", "reinforce", "weaken", "forget", "pin", "snooze"}
        if action not in valid:
            raise ValueError(
                f"invalid review action '{action}'; expected one of {sorted(valid)}"
            )

        memory = await self.episodic.get(memory_id)
        if not memory:
            return {"ok": False, "error": "not found", "memory_id": memory_id}

        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        if action == "forget":
            removed = await self.forget(memory_id)
            return {
                "ok": removed,
                "action": "forget",
                "memory_id": memory_id,
                "removed": removed,
            }

        # Decay-affecting actions delegate to the existing tested paths, then we
        # re-fetch so the review metadata is written on top of their changes.
        if action == "reinforce":
            await self.reinforce_memory(memory_id)
            memory = await self.episodic.get(memory_id) or memory
        elif action == "weaken":
            await self.memory_feedback(memory_id, helpful=False)
            memory = await self.episodic.get(memory_id) or memory
        elif action == "pin":
            memory.pinned = True
        elif action == "keep":
            memory.touch()  # reset decay clock only — mild, no stability change

        md = dict(memory.metadata or {})
        review = dict(md.get("review") or {})
        review["last_reviewed_at"] = now_iso
        review["review_count"] = int(review.get("review_count", 0)) + 1
        review["last_action"] = action
        if action == "snooze":
            review["review_due_at"] = (
                now + timedelta(days=max(snooze_days, 1))
            ).isoformat()
        else:
            review.pop("review_due_at", None)  # no longer snoozed
        md["review"] = review

        history = list(md.get("review_history") or [])
        history.append({"action": action, "reason": reason, "at": now_iso})
        md["review_history"] = history[-50:]  # keep the last 50 decisions
        memory.metadata = md

        await self.episodic.update(memory)
        self._refresh_memory_caches(memory)
        self._mark_derived_dirty()
        self._emit("reviewed", {"memory_id": memory_id, "action": action})
        return {
            "ok": True,
            "action": action,
            "memory_id": memory_id,
            "review_count": review["review_count"],
            "review_due_at": review.get("review_due_at"),
            "pinned": memory.pinned,
        }

    async def get_forgetting_curve(self, memory_id: str, days: int = 30) -> dict | None:
        """Predicted retention curve for a memory over the next `days`,
        given its current stability — visualizes the Ebbinghaus-style
        forgetting curve this memory is following right now."""
        memory = await self.episodic.get(memory_id)
        if not memory:
            return None
        current_decay = (
            1.0
            if memory.pinned
            else self.scorer.compute_decay(memory.accessed_at, half_life_hours=memory.stability_hours)
        )
        return {
            "memory_id": memory_id,
            "pinned": memory.pinned,
            "stability_hours": memory.stability_hours,
            "recall_count": memory.recall_count,
            "current_retention": current_decay,
            "curve": self.scorer.retention_curve(memory.stability_hours, days=days),
        }

    # ── Related memories (graph-lite) ─────────────────────────────

    async def get_related(
        self, memory_id: str, top_k: int = 5, project_scoped: bool = True
    ) -> list[tuple[Memory, float]]:
        """Nearest-neighbour memories to a given one by embedding similarity —
        a lightweight "related memories" / knowledge-graph edge computed live
        from the vector store (no extra schema, always current).

        Args:
            memory_id: The anchor memory.
            top_k: Max related memories to return.
            project_scoped: When True, only relate within the same project so
                unrelated workspaces don't bleed into each other.
        """
        anchor = self.vector_store.get(memory_id) or await self.episodic.get(memory_id)
        if not anchor or not anchor.embedding:
            return []

        def _candidate(m: Memory) -> bool:
            if m.id == memory_id:
                return False
            if project_scoped and m.project != anchor.project:
                return False
            return True

        # top_k+1 in case the anchor itself is returned then filtered.
        neighbours = self.vector_store.search(
            anchor.embedding, top_k=top_k + 1, predicate=_candidate
        )
        return neighbours[:top_k]

    # ── Context Window ────────────────────────────────────────────

    async def get_context(
        self,
        session_id: str | None = None,
        project: str | None = None,
        max_tokens: int = 4000,
    ) -> str:
        """Build a context window: recent short-term + pinned + important episodic.

        max_tokens is approximate (1 token ≈ 4 chars).
        """
        max_chars = max_tokens * 4
        parts: list[str] = []
        seen: set[str] = set()

        def _matches(m: Memory) -> bool:
            if session_id and m.session_id != session_id:
                return False
            if project and m.project != project:
                return False
            return True

        # 1. Short-term first (most recent live context)
        for m in self.short_term.get_recent(10):
            if _matches(m) and m.id not in seen:
                parts.append(m.content)
                seen.add(m.id)

        # 2. Pinned memories (always-on context)
        pinned = await self.episodic.search(
            project=project, session_id=session_id, pinned=True, limit=20
        )
        for m in pinned:
            if m.id not in seen:
                parts.append(m.content)
                seen.add(m.id)

        # 3. Important episodic memories fill the remaining budget
        important = await self.episodic.search(
            memory_type="episodic",
            project=project,
            session_id=session_id,
            min_importance=0.7,
            limit=20,
        )
        for m in important:
            if m.id not in seen:
                parts.append(m.content)
                seen.add(m.id)

        if not parts:
            return ""
        return "\n".join(parts)[:max_chars]

    # ── Context File Generation (CLAUDE.md / .cursorrules) ───────

    async def generate_context_file(
        self,
        project: str | None = None,
        style: str = "claude",
        max_memories: int = 60,
    ) -> str:
        """Compile memories into a persistent context file for AI clients.

        Args:
            project: Only include this project's memories (None = all).
            style: "claude" (CLAUDE.md) or "cursor" (.cursorrules).
            max_memories: Cap on included memories.
        """
        pinned = await self.episodic.search(project=project, pinned=True, limit=max_memories)
        important = await self.episodic.search(
            project=project, min_importance=0.7, limit=max_memories
        )
        recent = await self.episodic.search(project=project, limit=15)

        seen: set[str] = set()

        def _dedup(memories: list[Memory]) -> list[Memory]:
            out = []
            for m in memories:
                if m.id not in seen:
                    out.append(m)
                    seen.add(m.id)
            return out

        pinned = _dedup(pinned)
        important = [m for m in _dedup(important) if not m.pinned]
        recent = _dedup(recent)

        title = f"Project Memory — {project}" if project else "Project Memory"
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        lines: list[str] = []
        if style == "cursor":
            lines.append(f"# {title}")
            lines.append(f"# Generated by LEVH on {now}. Do not edit by hand.")
        else:
            lines.append(f"# {title}")
            lines.append("")
            lines.append(f"> Generated by LEVH on {now}. Do not edit by hand — ")
            lines.append("> update memories in LEVH and regenerate.")
        lines.append("")

        def _bullet(m: Memory) -> str:
            tags = f" _[{', '.join(m.tags)}]_" if m.tags else ""
            return f"- {m.content.strip()}{tags}"

        if pinned:
            lines.append("## Always Remember (pinned)")
            lines.extend(_bullet(m) for m in pinned)
            lines.append("")
        if important:
            lines.append("## Key Decisions & Facts")
            lines.extend(_bullet(m) for m in important)
            lines.append("")
        if recent:
            lines.append("## Recent Context")
            lines.extend(_bullet(m) for m in recent[:10])
            lines.append("")

        if not (pinned or important or recent):
            lines.append("_No memories stored yet._")

        return "\n".join(lines).rstrip() + "\n"

    # ── Sessions ───────────────────────────────────────────────────

    async def _refresh_session_count(self, session_id: str) -> None:
        count = await self.db.count_session_memories(session_id)
        await self.db.update_session(session_id, {"memory_count": count})

    async def create_session(
        self, name: str = "Untitled Session", metadata: dict | None = None
    ) -> Session:
        session = Session(name=name, metadata=metadata or {})
        await self.db.insert_session(session.model_dump())
        self._emit("session_created", session.model_dump())
        return session

    async def summarize_session(
        self, session_id: str, max_memories: int = 50, store: bool = True
    ) -> Memory | None:
        """Distill a session's memories into one consolidated summary memory.

        Uses an LLM when OPENAI_API_KEY is set, otherwise a deterministic
        extractive fallback — so it works fully offline. Returns the created
        summary Memory (or None when the session has no memories). When
        ``store`` is False the summary text is returned on a transient Memory
        without persisting.
        """
        from .summarizer import summarize_texts

        memories = await self.episodic.search(session_id=session_id, limit=max_memories)
        texts = [m.content for m in memories if m.content]
        if not texts:
            return None

        client = self._embedder._http if self._embedder is not None else None
        summary_text = await summarize_texts(texts, mode="auto", client=client)
        if not summary_text.strip():
            return None

        header = f"Session summary ({len(texts)} memories):\n{summary_text}"
        if not store:
            return Memory(content=header, session_id=session_id, memory_type=MemoryType.EPISODIC)

        # Inherit the session's dominant project so the summary is namespaced.
        project = next((m.project for m in memories if m.project), None)
        summary = await self.store(
            content=header,
            importance=0.75,
            tags=["session-summary"],
            session_id=session_id,
            project=project,
            source="auto-summary",
            memory_type="episodic",
        )
        self._emit(
            "session_summarized",
            {"session_id": session_id, "summary_id": summary.id, "from_count": len(texts)},
        )
        return summary

    async def end_session(self, session_id: str) -> Session | None:
        """End a session: consolidate its short-term memories, optionally
        summarize them, then mark ended."""
        row = await self.db.get_session(session_id)
        if not row:
            return None

        consolidated = await self.consolidate(session_id=session_id)

        if self.auto_summarize:
            try:
                await self.summarize_session(session_id)
            except Exception:
                # Summarization is best-effort — never block session end on it.
                pass

        session = Session(**row)
        session.status = SessionStatus.ENDED
        session.ended_at = datetime.now(timezone.utc).isoformat()
        session.memory_count = await self.db.count_session_memories(session_id)
        await self.db.update_session(session_id, session.model_dump(exclude={"id"}))
        self._emit(
            "session_ended",
            {**session.model_dump(), "consolidated": consolidated},
        )
        return session

    async def list_sessions(self, limit: int = 50) -> list[Session]:
        rows = await self.db.get_all_sessions(limit)
        return [Session(**r) for r in rows]

    async def get_session(self, session_id: str) -> Session | None:
        row = await self.db.get_session(session_id)
        return Session(**row) if row else None

    # ── Projects / Sources / Tags ─────────────────────────────────

    async def list_projects(self) -> list[dict]:
        return await self.db.list_projects()

    async def list_sources(self) -> list[dict]:
        return await self.db.list_sources()

    async def list_tags(self) -> list[dict]:
        return await self.db.list_tags()

    # ── People (entity graph over captured metadata) ──────────────

    async def list_people(self, limit: int = 200) -> list[dict]:
        """Distinct people mentioned across memories (calendar attendees,
        email senders/recipients, transcript speakers), most-frequent first.
        Each entry drops the internal ``memory_ids`` list for the summary view."""
        from .people import aggregate_people

        memories = await self.episodic.search(limit=10000)
        people = aggregate_people(memories)
        return [{k: v for k, v in p.items() if k != "memory_ids"} for p in people[:limit]]

    async def get_person(self, query: str) -> dict | None:
        """Resolve a name/email to a person and return their profile plus the
        memories that mention them (most recent first)."""
        from .people import aggregate_people, find_person_key

        memories = await self.episodic.search(limit=10000)
        people = aggregate_people(memories)
        key = find_person_key(people, query)
        if key is None:
            return None
        person = next(p for p in people if p["key"] == key)
        ids = set(person["memory_ids"])
        by_id = {m.id: m for m in memories}
        person_memories = [by_id[i] for i in ids if i in by_id]
        person_memories.sort(key=lambda m: m.created_at or "", reverse=True)
        profile = {k: v for k, v in person.items() if k != "memory_ids"}
        return {
            "person": profile,
            "memories": [m.model_dump(exclude={"embedding"}) for m in person_memories],
        }

    # ── Organizations (people graph grouped by email domain) ───────

    async def list_organizations(self, limit: int = 200) -> list[dict]:
        """Distinct organizations across all memories, grouped by the email
        domain of the people mentioned (calendar attendees, email
        senders/recipients, transcript speakers), most-frequent first. Each
        entry drops the internal ``memory_ids`` list and caps ``people`` to
        50 names so large organizations don't blow up the summary view."""
        from .organizations import aggregate_organizations

        memories = await self.episodic.search(limit=10000)
        orgs = aggregate_organizations(memories)
        result = []
        for o in orgs[:limit]:
            entry = {k: v for k, v in o.items() if k != "memory_ids"}
            entry["people"] = entry["people"][:50]
            result.append(entry)
        return result

    async def get_organization(self, query: str) -> dict | None:
        """Resolve a domain/name to an organization and return its profile
        plus the memories that reference someone from it (most recent
        first)."""
        from .organizations import aggregate_organizations, find_org_key

        memories = await self.episodic.search(limit=10000)
        orgs = aggregate_organizations(memories)
        key = find_org_key(orgs, query)
        if key is None:
            return None
        org = next(o for o in orgs if o["key"] == key)
        ids = set(org["memory_ids"])
        by_id = {m.id: m for m in memories}
        org_memories = [by_id[i] for i in ids if i in by_id]
        org_memories.sort(key=lambda m: m.created_at or "", reverse=True)
        profile = {k: v for k, v in org.items() if k != "memory_ids"}
        profile["people"] = profile["people"][:50]
        return {
            "organization": profile,
            "memories": [m.model_dump(exclude={"embedding"}) for m in org_memories],
        }

    async def timeline(self, days: int = 30, project: str | None = None) -> list[dict]:
        """Group episodic memories by day so a user can see "what happened
        this/last week". Uses ``metadata.captured_at`` (when a calendar event
        or email actually happened) over ``created_at`` (when it was
        captured into LEVH) if present. Returns day-groups sorted
        most-recent-first."""
        from datetime import datetime, timedelta, timezone

        memories = await self.episodic.search(project=project, limit=10000)

        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()

        days_map: dict[str, list] = {}
        for mem in memories:
            day = _event_date(mem)
            if not day or day < cutoff:
                continue
            days_map.setdefault(day, []).append(mem)

        groups = []
        for day, mems in days_map.items():
            mems.sort(key=lambda m: m.created_at or "", reverse=True)
            items = []
            for m in mems[:20]:
                mtype = m.memory_type.value if hasattr(m.memory_type, "value") else str(m.memory_type)
                items.append(
                    {
                        "id": m.id,
                        "summary": m.content.split("\n", 1)[0][:100],
                        "source": m.source,
                        "memory_type": mtype,
                    }
                )
            groups.append({"date": day, "count": len(mems), "items": items})

        groups.sort(key=lambda g: g["date"], reverse=True)
        return groups

    async def briefing(self, project: str | None = None, days: int = 7) -> dict:
        """Deterministic "Daily Briefing": what's on today, what you recently
        committed to, and what you're about to forget. No LLM call — every
        section is computed from stored metadata/content so results are
        reproducible offline.

        Uses ``metadata.captured_at`` (when a calendar event or email
        actually happened) over ``created_at`` — same convention as
        ``timeline()`` — for both the "today" and "commitments" windows.

        Args:
            project: Optional project filter.
            days: Lookback window (in days) for the commitments/recent-count
                sections. Default 7.
        """
        from datetime import datetime, timedelta, timezone
        import re

        days = min(max(days, 1), 90)
        now = datetime.now(timezone.utc)
        today = now.date().isoformat()
        cutoff = (now - timedelta(days=days)).date().isoformat()

        memories = await self.episodic.search(project=project, limit=10000)

        # ── Today: memories whose event date is today ──────────────
        today_items = []
        for m in memories:
            if _event_date(m) != today:
                continue
            captured_at = (m.metadata or {}).get("captured_at") or ""
            time_part = captured_at[11:16] if "T" in captured_at else ""
            today_items.append(
                {
                    "id": m.id,
                    "summary": m.content.split("\n", 1)[0][:120],
                    "source": m.source,
                    "time": time_part,
                }
            )
        today_items.sort(key=lambda it: (it["time"] == "", it["time"]))

        # ── Commitments: open action items in recent memories ───────
        marker_pattern = re.compile(
            r"\bI['’]?ll\b|\bI will\b|\bwe['’]ll\b|\bwe will\b|\bgoing to\b|"
            r"\bneed to\b|\bTODO\b|\baction item\b|\bfollow[- ]?up\b|"
            r"yapacağ|göndereceğ|halledeceğ|takip ed",
            re.IGNORECASE,
        )

        recent = [m for m in memories if _event_date(m) and _event_date(m) >= cutoff]
        recent.sort(key=lambda m: _event_date(m), reverse=True)

        commitments: list[dict] = []
        seen_text: set[str] = set()
        for m in recent:
            content = m.content or ""
            if not marker_pattern.search(content):
                continue
            segments: list[str] = []
            for line in content.split("\n"):
                segments.extend(line.split(". "))
            sentence = next((s for s in segments if marker_pattern.search(s)), content)
            text = sentence.strip()[:160]
            if not text or text in seen_text:
                continue
            seen_text.add(text)
            commitments.append(
                {
                    "id": m.id,
                    "text": text,
                    "source": m.source,
                    "date": _event_date(m),
                    "project": m.project,
                }
            )
            if len(commitments) >= 30:
                break

        # ── Fading: reuse the existing "about to be forgotten" logic ─
        fading_pairs = await self.list_fading(threshold=0.5, project=project, limit=5)
        fading = [
            {
                "id": m.id,
                "summary": m.content.split("\n", 1)[0][:120],
                "retention": round(retention, 4),
            }
            for m, retention in fading_pairs
        ]

        self._emit("briefed", {"project": project, "recent_total": len(recent)})
        return {
            "generated_at": now.isoformat(),
            "today": today_items,
            "commitments": commitments,
            "fading": fading,
            "counts": {
                "today": len(today_items),
                "commitments": len(commitments),
                "fading": len(fading),
                "recent_total": len(recent),
            },
        }

    async def meeting_prep(
        self, query: str = "", within_days: int = 14, max_people: int = 8
    ) -> dict:
        """Prepare for a meeting — the proactive "before you walk in" brief.

        Picks the next upcoming meeting (an event with attendees, or a
        calendar/transcript memory dated in the future) — or, if ``query`` is
        given, the best-matching meeting — then assembles, deterministically
        and offline:

          - the meeting itself (title, time, attendees, project);
          - for each attendee, what you last discussed with them (recent
            memories mentioning them, newest first);
          - open commitments relevant to the meeting (same project, or
            mentioning an attendee by name);
          - recent decisions relevant to the meeting's project.

        Args:
            query: Optional text to match a specific meeting instead of the
                next upcoming one.
            within_days: How far ahead to look for the next meeting. Default 14.
            max_people: Cap on attendees to build context for. Default 8.
        """
        from datetime import datetime, timedelta, timezone

        from .people import extract_people

        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        horizon_iso = (now + timedelta(days=max(within_days, 1))).isoformat()

        memories = await self.episodic.search(limit=10000)

        def _is_meeting(m: Memory) -> bool:
            md = m.metadata or {}
            if isinstance(md.get("attendees"), list) and md.get("attendees"):
                return True
            src = m.source or ""
            return "calendar" in src or "transcript" in src

        meetings = [m for m in memories if _is_meeting(m)]

        chosen: Memory | None = None
        if query:
            q = query.strip().lower()
            matches = [
                m
                for m in meetings
                if q in (m.content or "").lower()
                or q in str((m.metadata or {}).get("title", "")).lower()
            ]
            upcoming = sorted(
                [m for m in matches if _event_when(m) >= now_iso], key=_event_when
            )
            if upcoming:
                chosen, reason = upcoming[0], "matched query (upcoming)"
            elif matches:
                chosen = sorted(matches, key=_event_when, reverse=True)[0]
                reason = "matched query (most recent)"
            else:
                reason = f"no meeting matching '{query}'"
        else:
            upcoming = sorted(
                [m for m in meetings if now_iso <= _event_when(m) <= horizon_iso],
                key=_event_when,
            )
            if upcoming:
                chosen, reason = upcoming[0], "next upcoming meeting"
            else:
                reason = f"no upcoming meetings in the next {within_days} days"

        if chosen is None:
            self._emit("meeting_prepped", {"found": False})
            return {
                "generated_at": now_iso,
                "meeting": None,
                "reason": reason,
                "people": [],
                "open_commitments": [],
                "recent_decisions": [],
            }

        md = chosen.metadata or {}
        # Deduplicate attendees by identity key (email, else lowercased name).
        seen: set[str] = set()
        attendees: list[tuple[str, str, str]] = []
        for name, email in extract_people(md):
            key = email or name.lower()
            if key in seen:
                continue
            seen.add(key)
            attendees.append((key, name, email))

        when_raw = _event_when(chosen)
        meeting = {
            "id": chosen.id,
            "title": (str(md.get("title") or "").strip() or chosen.content.split("\n", 1)[0])[:140],
            "when": when_raw[:16].replace("T", " ") if when_raw else "",
            "project": chosen.project,
            "source": chosen.source,
            "attendees": [name for _, name, _ in attendees],
        }

        # Per-attendee: the memories (other than this meeting) that mention them.
        people_ctx = []
        attendee_names_lower = [name.lower() for _, name, _ in attendees]
        for key, name, email in attendees[:max_people]:
            hits = []
            for m in memories:
                if m.id == chosen.id:
                    continue
                found = extract_people(m.metadata or {})
                if any((e or n.lower()) == key for n, e in found):
                    hits.append(m)
            hits.sort(key=lambda m: m.created_at or "", reverse=True)
            recent = [
                {
                    "id": m.id,
                    "summary": m.content.split("\n", 1)[0][:120],
                    "date": _event_date(m),
                }
                for m in hits[:3]
            ]
            people_ctx.append(
                {
                    "name": name,
                    "email": email or None,
                    "last_seen": (hits[0].created_at or "")[:10] if hits else "",
                    "interaction_count": len(hits),
                    "recent": recent,
                }
            )

        # Open commitments relevant to this meeting: same project if the
        # meeting has one, else those naming an attendee. Excludes the meeting
        # memory itself and de-dups by extracted sentence.
        open_commitments = []
        seen_text: set[str] = set()
        for m in memories:
            if m.id == chosen.id:
                continue
            sentence = _first_marker_sentence(m.content or "", _COMMITMENT_PATTERN)
            if not sentence:
                continue
            relevant = False
            if chosen.project and m.project == chosen.project:
                relevant = True
            elif attendee_names_lower and any(
                n and n in (m.content or "").lower() for n in attendee_names_lower
            ):
                relevant = True
            if not relevant or sentence in seen_text:
                continue
            seen_text.add(sentence)
            open_commitments.append(
                {
                    "id": m.id,
                    "text": sentence,
                    "date": _event_date(m),
                    "project": m.project,
                }
            )
            if len(open_commitments) >= 15:
                break
        open_commitments.sort(key=lambda c: c["date"], reverse=True)

        # Recent decisions relevant to the meeting's project (or global).
        recent_decisions = await self.list_decisions(
            project=chosen.project, days=90, limit=10
        )

        self._emit("meeting_prepped", {"found": True, "meeting_id": chosen.id})
        return {
            "generated_at": now_iso,
            "meeting": meeting,
            "reason": reason,
            "people": people_ctx,
            "open_commitments": open_commitments,
            "recent_decisions": recent_decisions,
        }

    async def list_decisions(
        self,
        project: str | None = None,
        days: int = 90,
        limit: int = 50,
    ) -> list[dict]:
        """Detect decision statements in episodic memory content within the
        last ``days`` days — "what did we decide, and when/where". Mirrors
        ``briefing()``'s commitment-detection logic exactly (same event-date
        convention, cutoff window, and sentence-extraction approach) but with
        a marker regex tuned for decisions ("we decided", "agreed to",
        "karar verdik", ...) instead of open action items.

        Args:
            project: Optional project filter.
            days: Lookback window (in days). Default 90 (max 365).
            limit: Max decisions to return. Default 50.
        """
        import re
        from datetime import datetime, timedelta, timezone

        days = min(max(days, 1), 365)
        limit = min(max(limit, 1), 200)
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(days=days)).date().isoformat()

        memories = await self.episodic.search(project=project, limit=10000)

        marker_pattern = re.compile(
            r"\bwe decided\b|\bdecided to\b|\bdecision\b|\bwe agreed\b|\bagreed to\b|"
            r"\bwe['’]?re going with\b|\bgoing with\b|\bwe chose\b|\bchose to\b|"
            r"\bwe will use\b|\bkarar ver|kararlaştır|\bkarar:|üzerinde anlaş|seçtik",
            re.IGNORECASE,
        )

        recent = [m for m in memories if _event_date(m) and _event_date(m) >= cutoff]
        recent.sort(key=lambda m: _event_date(m), reverse=True)

        decisions: list[dict] = []
        seen_text: set[str] = set()
        for m in recent:
            content = m.content or ""
            if not marker_pattern.search(content):
                continue
            segments: list[str] = []
            for line in content.split("\n"):
                segments.extend(line.split(". "))
            sentence = next((s for s in segments if marker_pattern.search(s)), content)
            text = sentence.strip()[:180]
            if not text or text in seen_text:
                continue
            seen_text.add(text)
            decisions.append(
                {
                    "id": m.id,
                    "text": text,
                    "source": m.source,
                    "date": _event_date(m),
                    "project": m.project,
                }
            )
            if len(decisions) >= limit:
                break

        return decisions

    # ── Statistics ─────────────────────────────────────────────────

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

    # ── Score Breakdown (for visualization) ───────────────────────

    async def score_breakdown(
        self, memory_id: str, query: str
    ) -> ScoreBreakdown | None:
        """Get individual H(x,ψ) components for a memory vs query."""
        memory = await self.episodic.get(memory_id)
        if not memory or not memory.embedding:
            return None

        query_embedding = await self.embedder.embed(query)
        import numpy as np

        vec_mem = np.array(memory.embedding, dtype=np.float32)
        vec_q = np.array(query_embedding, dtype=np.float32)
        cosine = float(
            np.dot(vec_mem, vec_q)
            / (np.linalg.norm(vec_mem) * np.linalg.norm(vec_q) + 1e-8)
        )

        decay = (
            1.0
            if memory.pinned
            else self.scorer.compute_decay(memory.accessed_at, half_life_hours=memory.stability_hours)
        )
        bd = self.scorer.breakdown(
            similarity=cosine,
            decay_factor=decay,
            importance=memory.importance,
            frequency=memory.frequency,
        )

        total = self.scorer.compute(
            similarity=cosine,
            decay_factor=decay,
            importance=memory.importance,
            frequency=memory.frequency,
        )

        return ScoreBreakdown(
            memory_id=memory_id,
            content_snippet=memory.content[:100],
            total_hscore=total,
            alpha_component=bd["alpha_component"],
            beta_component=bd["beta_component"],
            gamma_component=bd["gamma_component"],
            delta_component=bd["delta_component"],
        )

    # ── Export / Import ────────────────────────────────────────────

    async def export_memories(self, session_id: str | None = None) -> list[dict]:
        filters = {}
        if session_id:
            filters["session_id"] = session_id
        memories = await self.episodic.search(**filters, limit=10000)
        return [m.model_dump() for m in memories]

    async def import_memories(self, data: list[dict]) -> int:
        """Restore trusted, already-normalized memory records exactly.

        This low-level path preserves IDs, timestamps and lifecycle state and is
        therefore reserved for backup/restore and internal compatibility.  User
        facing JSON imports must use :meth:`import_memories_gated` so content is
        evaluated by the admission policy before persistence.

        SQLite is written *before* in-memory caches.  A failed DB write can no
        longer leave a ghost memory that is recallable until process restart.
        """
        count = 0
        skipped = 0
        for item in data:
            try:
                mem = Memory(**item)
                await self.episodic.store(mem)
                if mem.memory_type == MemoryType.SHORT_TERM:
                    self.short_term.add(mem)
                if mem.embedding:
                    self.vector_store.add(mem)
                count += 1
            except Exception:
                # Malformed record — skip it but keep a count so a partially
                # bad import file is visible instead of silently swallowed.
                skipped += 1
                continue
        if count or skipped:
            if count:
                self._mark_derived_dirty()
            self._emit("imported", {"count": count, "skipped": skipped, "gated": False})
        return count

    async def import_memories_gated(self, data: list[dict]) -> dict:
        """Import user-supplied JSON through the deterministic admission gate.

        The record's portable identity and lifecycle fields are preserved, but
        untrusted embeddings are discarded and recomputed from the admitted
        (possibly redacted) content using the active embedder.  Reject/review
        decisions are not persisted.  Each item is isolated and the returned
        breakdown makes partial imports explicit.
        """
        imported = redacted = duplicates = held = errors = 0

        for item in data:
            try:
                mem = Memory(**item)
                decision = await self.evaluate_admission(
                    mem.content, project=mem.project
                )
                action = decision["action"]
                if action in ("reject", "review"):
                    if action == "review":
                        held += 1
                    else:
                        duplicates += 1
                    continue

                content = (
                    decision["redacted_content"]
                    if decision["redacted"]
                    else mem.content
                )
                metadata = dict(mem.metadata or {})
                metadata["admission"] = {
                    "action": action,
                    "reasons": decision["reasons"],
                    "reason_codes": decision["reason_codes"],
                    "redacted": decision["redacted"],
                    "secrets": decision["secrets"],
                    "max_similarity": decision["max_similarity"],
                    "forced": False,
                }
                metadata["imported_via"] = "json"

                # Never trust an imported vector.  It may be stale, poisoned or
                # from a different embedding dimension/model.
                embedding = await self.embedder.embed(content)
                metadata["embedding_provenance"] = self.embedder.identity()
                mem = mem.model_copy(
                    update={
                        "content": content,
                        "embedding": embedding,
                        "metadata": metadata,
                    }
                )

                # Persist first, then expose through process-local caches.
                await self.episodic.store(mem)
                if mem.memory_type == MemoryType.SHORT_TERM:
                    self.short_term.add(mem)
                self.vector_store.add(mem)
                await self._apply_interference(mem)
                if mem.session_id:
                    await self._refresh_session_count(mem.session_id)

                imported += 1
                if decision["redacted"]:
                    redacted += 1
            except Exception:
                errors += 1
                continue

        result = {
            "imported": imported,
            "redacted": redacted,
            "duplicates": duplicates,
            "held": held,
            "errors": errors,
            "gated": True,
        }
        if any((imported, redacted, duplicates, held, errors)):
            if imported:
                self._mark_derived_dirty()
            self._emit("imported", result)
        return result

    # ── Backup / Restore (Faz 0 security) ─────────────────────────

    async def backup(self, app_version: str = "") -> dict:
        """Build a full portable snapshot: every memory (with its complete
        decay state) plus every session. The returned dict is the plain
        snapshot; encrypting it into a file blob is the caller's job (see
        ``server.core.backup.make_backup_blob``)."""
        from .backup import make_snapshot

        memories = await self.episodic.search(limit=1_000_000)
        mem_dicts = [m.model_dump() for m in memories]
        sessions = await self.db.get_all_sessions(limit=1_000_000)
        created_at = datetime.now(timezone.utc).isoformat()
        return make_snapshot(mem_dicts, sessions, app_version, created_at)

    async def restore(self, snapshot: dict, replace: bool = False) -> dict:
        """Atomically restore a fully validated backup snapshot.

        Validation is completed for every memory and session before a replace
        can delete current data.  The SQLite merge/replace is one transaction;
        caches and all derived graph/trust/conflict state are rebuilt only after
        commit.  Malformed snapshots fail closed with the existing store intact.
        """
        from .backup import BACKUP_FORMAT
        from .types import Session

        if not isinstance(snapshot, dict) or snapshot.get("format") != BACKUP_FORMAT:
            raise ValueError("not a LEVH backup snapshot")

        raw_memories = snapshot.get("memories")
        raw_sessions = snapshot.get("sessions")
        if not isinstance(raw_memories, list) or not isinstance(raw_sessions, list):
            raise ValueError("backup snapshot memories/sessions must be arrays")

        memories: list[Memory] = []
        sessions: list[Session] = []
        try:
            for index, item in enumerate(raw_memories):
                if not isinstance(item, dict):
                    raise ValueError(f"memory[{index}] is not an object")
                memories.append(Memory(**item))
            for index, item in enumerate(raw_sessions):
                if not isinstance(item, dict):
                    raise ValueError(f"session[{index}] is not an object")
                sessions.append(Session(**item))
        except Exception as exc:
            raise ValueError(f"invalid backup snapshot record: {exc}") from exc

        memory_ids = [m.id for m in memories]
        session_ids = [s.id for s in sessions]
        if len(memory_ids) != len(set(memory_ids)):
            raise ValueError("backup snapshot contains duplicate memory ids")
        if len(session_ids) != len(set(session_ids)):
            raise ValueError("backup snapshot contains duplicate session ids")

        safety_backup_path: str | None = None
        if replace:
            existing_items = await self.db.count_memories() + await self.db.count_sessions()
            if existing_items:
                # Fail closed: a destructive replace is not allowed to proceed
                # unless the current SQLite state has first been copied with
                # SQLite's online-backup API. In-memory test stores have no
                # durable location and intentionally return None.
                safety_backup_path = await self.db.create_safety_backup()

        await self.db.restore_snapshot_transaction(
            [m.model_dump(mode="json") for m in memories],
            [s.model_dump(mode="json") for s in sessions],
            replace=replace,
        )

        # Rebuild process-local state from committed SQLite, never from the
        # untrusted snapshot objects.
        self.short_term.clear()
        self.vector_store.clear()
        restored_all = await self.episodic.get_all(limit=1_000_000)
        for memory in restored_all:
            if memory.memory_type == MemoryType.SHORT_TERM:
                self.short_term.add(memory)
            if memory.embedding:
                self.vector_store.add(memory)

        self._derived_dirty = True
        await self._ensure_derived_state()

        self._emit(
            "restored",
            {"memories": len(memories), "sessions": len(sessions), "replace": replace},
        )
        return {
            "memories": len(memories),
            "sessions": len(sessions),
            "replace": replace,
            "safety_backup_path": safety_backup_path,
        }

    # ── Admission Gate (quality: decide before storing) ───────────

    async def evaluate_admission(
        self,
        content: str,
        project: str | None = None,
        min_length: int = 3,
        exclude_id: str | None = None,
    ) -> dict:
        """Judge a candidate memory WITHOUT storing it: admit / review / redact
        / reject. Computes the duplicate signal (max cosine similarity to any
        existing memory) from the vector store, then applies the deterministic
        admission rules. No LLM.

        The duplicate probe embeds the *redacted* text — i.e. what would
        actually be stored — so that re-ingesting the same secret-bearing item
        (common when a connector re-syncs) is correctly seen as a duplicate
        instead of accumulating near-identical copies."""
        from .admission import evaluate, redact_secrets

        probe_text, _ = redact_secrets(content or "")
        probe_text = (probe_text or "").strip()
        max_sim = 0.0
        if probe_text:
            embedding = await self.embedder.embed(probe_text)

            def _pred(m: Memory) -> bool:
                # A memory being updated is its own nearest neighbour, so
                # without this it would always look like a duplicate of itself.
                if exclude_id is not None and m.id == exclude_id:
                    return False
                return project is None or m.project == project

            neighbours = self.vector_store.search(embedding, top_k=1, predicate=_pred)
            if neighbours:
                max_sim = float(neighbours[0][1])
        return evaluate(content, max_similarity=max_sim, min_length=min_length)

    async def admit_memory(
        self,
        content: str,
        importance: float = 0.5,
        tags: list[str] | None = None,
        session_id: str | None = None,
        project: str | None = None,
        source: str | None = None,
        pinned: bool = False,
        memory_type: str = "short_term",
        metadata: dict | None = None,
        force: bool = False,
        min_length: int = 3,
    ) -> dict:
        """Run the admission gate, then act on its verdict:

          - ``reject`` / ``review`` → NOT stored (unless ``force=True``);
          - ``redact``              → stored with secrets stripped;
          - ``admit``               → stored as-is.

        The verdict is recorded in the stored memory's ``metadata.admission``.
        Returns ``{"stored": bool, "decision": <gate result>, "memory": <dict|None>}``.
        """
        decision = await self.evaluate_admission(
            content, project=project, min_length=min_length
        )
        action = decision["action"]

        if action in ("reject", "review") and not force:
            return {"stored": False, "decision": decision, "memory": None}

        store_content = (
            decision["redacted_content"] if decision["redacted"] else content
        )
        md = dict(metadata or {})
        md["admission"] = {
            "action": action,
            "reasons": decision["reasons"],
            "reason_codes": decision["reason_codes"],
            "redacted": decision["redacted"],
            "secrets": decision["secrets"],
            "max_similarity": decision["max_similarity"],
            "forced": bool(force and action in ("reject", "review")),
        }
        mem = await self.store(
            content=store_content,
            importance=importance,
            tags=tags,
            session_id=session_id,
            memory_type=memory_type,
            metadata=md,
            project=project,
            source=source,
            pinned=pinned,
        )
        return {"stored": True, "decision": decision, "memory": mem.model_dump(exclude={"embedding"})}

    # ── Connector ingest v2 (gate-integrated, incremental) ────────

    async def ingest_items(
        self,
        items: list[dict],
        connector: str,
        project: str | None = None,
        use_gate: bool = True,
    ) -> dict:
        """Store a batch of connector-fetched items, routed through the
        admission gate (dedupe + secret redaction) when ``use_gate`` is True.

        This is Connector-framework-v2 ingest: every item is isolated (one bad
        item can't fail the batch), duplicates are dropped instead of piling
        up, secrets are stripped, and the run is recorded in ``connector_sync``
        so re-syncing is incremental and reportable ("N new since last sync").

        Returns a breakdown:
            fetched, stored, redacted, duplicates, held, errors, source_key,
            last_synced_at.
        """
        from datetime import datetime, timezone

        source = f"connector:{connector}"
        stored = redacted = duplicates = held = errors = 0

        for item in items:
            content = (item or {}).get("content", "")
            if not content or not str(content).strip():
                continue
            tags = item.get("tags", []) or []
            metadata = dict(item.get("metadata", {}) or {})
            metadata["imported_via"] = connector
            importance = float(item.get("importance", 0.5))
            try:
                if use_gate:
                    result = await self.admit_memory(
                        content=content,
                        importance=importance,
                        tags=tags,
                        project=project,
                        source=source,
                        memory_type="episodic",
                        metadata=metadata,
                    )
                    action = result["decision"]["action"]
                    if not result["stored"]:
                        # reject (duplicate) vs review (held for a human)
                        if action == "review":
                            held += 1
                        else:
                            duplicates += 1
                        continue
                    stored += 1
                    if action == "redact":
                        redacted += 1
                else:
                    await self.store(
                        content=content,
                        importance=importance,
                        tags=tags,
                        project=project,
                        source=source,
                        memory_type="episodic",
                        metadata=metadata,
                    )
                    stored += 1
            except Exception:
                # Error isolation — a single malformed item never fails the run.
                errors += 1
                continue

        now_iso = datetime.now(timezone.utc).isoformat()
        source_key = f"{connector}:{project or ''}"
        await self.db.record_sync(
            source_key=source_key,
            connector=connector,
            project=project,
            last_synced_at=now_iso,
            fetched=len(items),
            stored=stored,
        )
        self._emit(
            "connector_synced",
            {"connector": connector, "stored": stored, "duplicates": duplicates},
        )
        return {
            "connector": connector,
            "fetched": len(items),
            "stored": stored,
            "redacted": redacted,
            "duplicates": duplicates,
            "held": held,
            "errors": errors,
            "source_key": source_key,
            "last_synced_at": now_iso,
        }

    async def list_sync_state(self) -> list[dict]:
        """All connector sync bookkeeping rows, most-recent first."""
        return await self.db.list_sync_states()

    # ── Hard-delete audit & redaction (trust) ─────────────────────

    async def audit_deletion(self, memory_id: str) -> dict:
        """Prove absence across runtime, primary and derived layers."""
        persistent = await self.db.memory_residue(memory_id)
        residue = {
            "short_term": self.short_term.find(memory_id) is not None,
            "vector_store": self.vector_store.get(memory_id) is not None,
            "episodic": persistent["episodic"] > 0,
            "entity_links": persistent["entity_links"] > 0,
            "trust_score": persistent["trust_score"] > 0,
            "conflict_candidates": persistent["conflict_candidates"] > 0,
        }
        return {
            "memory_id": memory_id,
            "residue": residue,
            "fully_absent": not any(residue.values()),
        }

    async def purge_memory(self, memory_id: str) -> dict:
        """Hard-delete a memory and verify nothing survives. ``forget`` already
        removes from every layer; this wraps it with a post-condition audit so
        a "really delete" is provable, not assumed. Pinned memories are deleted
        too — an explicit purge is a deliberate human action."""
        existed = (await self.episodic.get(memory_id)) is not None
        await self.forget(memory_id)
        audit = await self.audit_deletion(memory_id)
        return {
            "memory_id": memory_id,
            "existed": existed,
            "purged": audit["fully_absent"],
            "residue": audit["residue"],
        }

    async def audit_secrets(self, limit: int = 10000) -> dict:
        """Scan stored memories for secrets that slipped in before the
        admission gate existed (or via paths that bypass it). Read-only —
        reports which memories contain credentials so they can be redacted."""
        from .admission import redact_secrets

        memories = await self.episodic.search(limit=limit)
        flagged = []
        for m in memories:
            redacted_content, secrets = redact_secrets(m.content or "")
            if secrets:
                flagged.append(
                    {
                        "id": m.id,
                        "secrets": secrets,
                        # Never echo the credential that the audit detected.
                        "preview": (redacted_content or "").split("\n", 1)[0][:100],
                        "project": m.project,
                        "source": m.source,
                    }
                )
        return {"scanned": len(memories), "flagged": len(flagged), "items": flagged}

    async def redact_memory(self, memory_id: str) -> dict:
        """Strip secrets from an already-stored memory in place: rewrite the
        content, re-embed, refresh every layer, and record the event in
        ``metadata.redaction_history``. Idempotent — a memory with no secrets
        is left untouched."""
        from datetime import datetime, timezone

        from .admission import redact_secrets

        memory = await self.episodic.get(memory_id)
        if not memory:
            return {"ok": False, "error": "not found", "memory_id": memory_id}

        new_content, secrets = redact_secrets(memory.content or "")
        if not secrets:
            return {"ok": True, "redacted": False, "secrets": [], "memory_id": memory_id}

        memory.content = new_content
        memory.embedding = await self.embedder.embed(new_content)
        md = dict(memory.metadata or {})
        md["embedding_provenance"] = self.embedder.identity()
        history = list(md.get("redaction_history") or [])
        history.append(
            {"secrets": secrets, "at": datetime.now(timezone.utc).isoformat()}
        )
        md["redaction_history"] = history[-50:]
        memory.metadata = md

        await self.episodic.update(memory)
        self._refresh_memory_caches(memory)
        self.vector_store.add(memory)
        self._mark_derived_dirty()
        self._emit("updated", self._memory_event_payload(memory))
        return {
            "ok": True,
            "redacted": True,
            "secrets": secrets,
            "memory_id": memory_id,
        }

    async def redact_all_secrets(self, dry_run: bool = True, limit: int = 10000) -> dict:
        """Bulk redaction of secrets across stored memories. ``dry_run=True``
        (default) only reports; set False to rewrite every flagged memory."""
        audit = await self.audit_secrets(limit=limit)
        redacted = 0
        if not dry_run:
            for item in audit["items"]:
                result = await self.redact_memory(item["id"])
                if result.get("redacted"):
                    redacted += 1
        return {
            "dry_run": dry_run,
            "scanned": audit["scanned"],
            "flagged": audit["flagged"],
            "redacted": redacted,
            "items": audit["items"],
        }

    # ── Onboarding: demo seed (2.23B) ─────────────────────────────

    async def seed_demo(self, force: bool = False) -> dict:
        """Populate an empty store with a deterministic demo corpus so a first
        run shows a live dashboard instead of empty states.

        Every memory flows through the real ``store`` path, is backdated from
        its ``age_days`` (so the forgetting curve, review queue, and briefing
        show a genuine time spread), and — where marked — has its durability
        nudged to model strong vs fading memories. After ingest the full
        derived pipeline runs (entity reindex → trust recompute → conflict
        detection), so people, organizations, the trust breakdown, and one real
        conflict candidate are all present immediately.

        Refuses to run on a non-empty store unless ``force=True`` — it never
        touches memories the user already has.
        """
        from datetime import datetime, timedelta, timezone

        from .demo_data import demo_memories

        existing = await self.episodic.count()
        if existing and not force:
            return {
                "seeded": 0,
                "skipped": True,
                "reason": "store not empty",
                "existing": existing,
            }

        now = datetime.now(timezone.utc)
        seeded_ids: list[str] = []
        for item in demo_memories():
            age_days = int(item.get("age_days", 0))
            created = now - timedelta(days=age_days)
            created_iso = created.isoformat()

            metadata = dict(item.get("metadata") or {})
            # Anchor event-like memories in time so meeting-prep / timeline order
            # them by when they happened, matching how real connectors capture.
            metadata.setdefault("captured_at", created_iso)
            metadata.setdefault("demo", True)

            mem = await self.store(
                content=item["content"],
                importance=float(item.get("importance", 0.5)),
                tags=list(item.get("tags") or []),
                memory_type="episodic",
                metadata=metadata,
                project=item.get("project"),
                source=item.get("source"),
                pinned=bool(item.get("pinned", False)),
            )

            # Backdate the record. A reinforced memory keeps a recent
            # accessed_at (it's been recalled lately, so it stays strong);
            # everything else was last touched when it was created, so it decays.
            reinforce = item.get("reinforce") or {}
            updates: dict = {
                "created_at": created_iso,
                "frequency": int(reinforce.get("frequency", mem.frequency)),
                "recall_count": int(reinforce.get("recall_count", mem.recall_count)),
                "stability_hours": float(
                    reinforce.get("stability_hours", mem.stability_hours)
                ),
            }
            if reinforce.get("recall_count"):
                # Strong memory: recalled within the last day.
                updates["accessed_at"] = (now - timedelta(hours=6)).isoformat()
            else:
                updates["accessed_at"] = created_iso

            # Keep the in-memory object (shared by the vector store) in sync so
            # scoring in this same process sees the backdated values.
            mem.created_at = updates["created_at"]
            mem.accessed_at = updates["accessed_at"]
            mem.frequency = updates["frequency"]
            mem.recall_count = updates["recall_count"]
            mem.stability_hours = updates["stability_hours"]
            await self.db.update_memory(mem.id, updates)
            seeded_ids.append(mem.id)

        # Build everything the dashboard reads off derived tables.
        entities = await self.reindex_entities()
        trust = await self.recompute_trust_scores()
        conflicts = await self.detect_conflict_candidates()

        self._emit("demo_seeded", {"seeded": len(seeded_ids)})
        return {
            "seeded": len(seeded_ids),
            "skipped": False,
            "entities": entities.get("entities", 0),
            "entity_links": entities.get("links", 0),
            "trust_scored": trust.get("scored", 0),
            "conflict_candidates": conflicts.get("new_candidates", 0),
        }

    async def onboarding_status(self) -> dict:
        """Return deterministic first-run readiness computed from real state."""
        from .onboarding import onboarding_status

        return await onboarding_status(self)

    async def remove_demo_data(self) -> dict:
        """Remove only memories explicitly marked as demo data."""
        from .onboarding import remove_demo_data

        return await remove_demo_data(self)

    # ── Entity knowledge graph (Faz 2) ────────────────────────────

    async def reindex_entities(self) -> dict:
        """Rebuild the persistent entity graph from every stored memory:
        extract typed entities (person / organization / event / document /
        task) and their memory links, so graph queries ("which memories
        mention X", "which entities co-occur with X") run as a join instead of
        a full re-scan. Idempotent — clears and rebuilds."""
        return await self.entity_index.reindex_entities()

    async def list_entities_graph(
        self, entity_type: str | None = None, limit: int = 200
    ) -> list[dict]:
        """List persisted entities (optionally filtered by type), most-mentioned
        first. Distinct from ``list_people`` — this reads the persistent graph."""
        await self._ensure_derived_state()
        return await self.entity_index.list_entities_graph(entity_type=entity_type, limit=limit)

    async def get_entity(self, query: str, entity_type: str | None = None) -> dict | None:
        """Resolve a query to a persisted entity and return its profile: the
        memories that mention it (newest first) and the entities it co-occurs
        with (its graph neighbours)."""
        await self._ensure_derived_state()
        return await self.entity_index.get_entity(query, entity_type=entity_type)

    async def entity_graph_stats(self) -> dict:
        """Counts of persisted entities by type."""
        await self._ensure_derived_state()
        return await self.entity_index.entity_graph_stats()

    # ── Provenance / trust score (deterministic, NOT truth) ───────

    @staticmethod
    def _build_entity_index(memories: list[Memory]) -> dict[str, list[tuple[str, str]]]:
        """entity_id → list of (memory_id, source_type) across all memories."""
        return EntityIndexService.build_entity_index(memories)


    async def recompute_trust_scores(self) -> dict:
        """Compute and persist the provenance/trust score for every memory.

        Corroboration is drawn from the entity graph and remains independent of
        the H-score used for recall ranking.
        """
        return await self.trust_service.recompute_trust_scores()

    async def get_trust(self, memory_id: str) -> dict | None:
        """Return the stored trust breakdown, computing it on demand if absent."""
        await self._ensure_derived_state()
        return await self.trust_service.get_trust(memory_id)

    async def list_low_trust(self, threshold: float = 0.4, limit: int = 50) -> list[dict]:
        """Return stored memories below the requested trust threshold."""
        await self._ensure_derived_state()
        return await self.trust_service.list_low_trust(
            threshold=threshold,
            limit=limit,
        )

    # ── Conflict candidates (deterministic review signal) ─────────

    async def detect_conflict_candidates(self) -> dict:
        """Detect deterministic conflict candidates for human review."""
        return await self.conflict_service.detect_conflict_candidates()

    async def list_conflict_candidates(
        self, status: str | None = "open", limit: int = 100
    ) -> list[dict]:
        await self._ensure_derived_state()
        return await self.conflict_service.list_conflict_candidates(
            status=status,
            limit=limit,
        )

    async def review_conflict_candidate(self, conflict_id: str, action: str) -> dict:
        """Apply a human review decision to a conflict candidate."""
        return await self.conflict_service.review_conflict_candidate(
            conflict_id,
            action,
        )

    # ── Deduplication ─────────────────────────────────────────────

    async def find_duplicates(
        self, similarity_threshold: float = 0.95, project: str | None = None
    ) -> list[list[Memory]]:
        """Find groups of near-duplicate memories by embedding similarity."""
        import numpy as np

        memories = await self.episodic.search(project=project, limit=10000)
        with_emb = [m for m in memories if m.embedding]
        groups: list[list[Memory]] = []
        used: set[str] = set()

        by_dim: dict[int, list[Memory]] = {}
        for m in with_emb:
            by_dim.setdefault(len(m.embedding), []).append(m)

        for dim_memories in by_dim.values():
            if len(dim_memories) < 2:
                continue
            matrix = np.stack(
                [np.array(m.embedding, dtype=np.float32) for m in dim_memories]
            )
            norms = matrix / (np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-8)
            sim = norms @ norms.T
            n = len(dim_memories)
            for i in range(n):
                if dim_memories[i].id in used:
                    continue
                group = [dim_memories[i]]
                for j in range(i + 1, n):
                    if dim_memories[j].id in used:
                        continue
                    if float(sim[i, j]) >= similarity_threshold:
                        group.append(dim_memories[j])
                        used.add(dim_memories[j].id)
                if len(group) > 1:
                    used.add(dim_memories[i].id)
                    groups.append(group)
        return groups

    async def dedupe(
        self, similarity_threshold: float = 0.95, project: str | None = None
    ) -> int:
        """Delete near-duplicates, keeping the most important / most recent of each group."""
        groups = await self.find_duplicates(similarity_threshold, project)
        removed = 0
        for group in groups:
            # Keep pinned first, then highest importance, then newest.
            group.sort(
                key=lambda m: (m.pinned, m.importance, m.created_at), reverse=True
            )
            for duplicate in group[1:]:
                if duplicate.pinned:
                    continue  # never auto-delete pinned memories
                if await self.forget(duplicate.id):
                    removed += 1
        return removed

    # ── Consolidation (Faz 5: sleep-like memory compression) ──────

    async def consolidate_memories(
        self,
        similarity_threshold: float = 0.82,
        min_age_days: int = 7,
        min_cluster_size: int = 2,
        project: str | None = None,
        dry_run: bool = True,
    ) -> dict:
        """Compress clusters of *related* older memories into a single
        consolidated memory — modelled on how human memory consolidates during
        sleep: many similar episodes collapse into one durable gist, the raw
        episodes fade.

        Unlike ``dedupe`` (which only removes near-identical duplicates at a
        high threshold and keeps one verbatim), this uses a lower similarity
        threshold to group *related* memories and replaces the whole cluster
        with an LLM/extractive summary. The raw originals are preserved inside
        the consolidated memory's ``metadata.consolidated_from`` so nothing is
        lost — they can be recovered — but they stop cluttering active recall.

        Safeguards: pinned memories are never consolidated, and only memories
        older than ``min_age_days`` are eligible so recent working memory is
        left intact. ``dry_run=True`` (default) previews clusters without
        changing anything.

        Returns a dict with the clusters found and, when applied, how many
        consolidated memories were created and how many originals archived.
        """
        from datetime import datetime, timedelta, timezone

        from .summarizer import summarize_texts

        now = datetime.now(timezone.utc)
        age_cutoff = (now - timedelta(days=max(min_age_days, 0))).isoformat()
        min_cluster_size = max(min_cluster_size, 2)

        groups = await self.find_duplicates(similarity_threshold, project)
        client = self._embedder._http if self._embedder is not None else None

        clusters: list[dict] = []
        consolidated_count = 0
        removed_count = 0

        for group in groups:
            # Eligible members: not pinned, older than the age cutoff, and not
            # already a consolidation output (avoid recompressing summaries).
            members = [
                m
                for m in group
                if not m.pinned
                and (m.created_at or "") <= age_cutoff
                and "consolidated" not in (m.tags or [])
            ]
            if len(members) < min_cluster_size:
                continue

            members.sort(key=lambda m: m.created_at or "")
            texts = [m.content for m in members if m.content]
            if not texts:
                continue
            summary_text = (await summarize_texts(texts, mode="auto", client=client)).strip()
            if not summary_text:
                continue

            cluster_project = next((m.project for m in members if m.project), None)
            cluster = {
                "size": len(members),
                "project": cluster_project,
                "summary": summary_text[:500],
                "member_ids": [m.id for m in members],
                "sample": [m.content.split("\n", 1)[0][:80] for m in members[:3]],
            }

            if not dry_run:
                importance = max((m.importance for m in members), default=0.5)
                consolidated = await self.store(
                    content=(
                        f"Consolidated memory ({len(members)} related memories):\n"
                        f"{summary_text}"
                    ),
                    importance=min(max(importance, 0.5), 1.0),
                    tags=["consolidated"],
                    project=cluster_project,
                    source="consolidation",
                    memory_type="episodic",
                    metadata={
                        # Lineage proves which records were compressed without
                        # retaining a second undeletable copy of their content.
                        "consolidated_from": [
                            {
                                "id": m.id,
                                "created_at": m.created_at,
                                "content_sha256": __import__("hashlib").sha256(
                                    (m.content or "").encode("utf-8")
                                ).hexdigest(),
                            }
                            for m in members
                        ],
                        # A summary derived exclusively from demo records remains
                        # demo-tagged so the safe onboarding cleanup can remove it.
                        "demo": bool(members) and all(
                            bool((m.metadata or {}).get("demo")) for m in members
                        ),
                    },
                )
                cluster["consolidated_id"] = consolidated.id
                consolidated_count += 1
                for m in members:
                    if await self.forget(m.id):
                        removed_count += 1

            clusters.append(cluster)

        if not dry_run and (consolidated_count or removed_count):
            self._emit(
                "consolidated_memories",
                {"consolidated": consolidated_count, "archived": removed_count},
            )

        return {
            "dry_run": dry_run,
            "clusters_found": len(clusters),
            "consolidated": consolidated_count,
            "archived": removed_count,
            "clusters": clusters,
        }

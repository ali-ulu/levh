"""Storing, updating and removing memories.

Part of :class:`server.core.memory_engine.MemoryEngine`, split out to keep
each file readable. Mixins rather than separate services: the methods use the
engine's own state throughout, and moving the bodies unchanged is what makes
the split verifiable.
"""

from __future__ import annotations

import sqlite3

from .helpers import logger
from ..types import (
    Memory,
    MemoryType,
)


class MemoryWriteMixin:
    """Storing, updating and removing memories."""

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

        await self.episodic.store(mem)
        if mem.memory_type == MemoryType.SHORT_TERM:
            self.short_term.add(mem)
        self.vector_store.add(mem)

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
            weakened = self.scorer.weaken(old.stability_hours, self.interference_factor)
            # This write is best-effort, like the embedder/summarizer fallbacks
            # (see docs/ARCHITECTURE.md invariant #5): the new memory this call
            # exists to interfere around is already committed by the time we
            # get here (store() calls episodic.store(mem) first), so a transient
            # SQLite contention here must not fail a store the caller already
            # succeeded at. Worst case, one older memory keeps its prior
            # stability a little longer than ideal — not a correctness issue.
            try:
                await self.db.update_memory(old.id, {"stability_hours": weakened})
            except sqlite3.OperationalError:
                logger.warning(
                    "interference weaken skipped for %s: transient SQLite error",
                    old.id,
                )
                continue
            old.stability_hours = weakened
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

    async def forget(self, memory_id: str) -> bool:
        """Remove a memory and every derived reference atomically."""
        existing = await self.episodic.get(memory_id)
        deleted = await self.db.delete_memory_cascade(memory_id)
        if deleted:
            self.short_term.remove(memory_id)
            self.vector_store.remove(memory_id)
            if existing and existing.session_id:
                await self._refresh_session_count(existing.session_id)
            self._mark_derived_dirty()
            self._emit("deleted", {"id": memory_id})
        return deleted

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

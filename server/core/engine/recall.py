"""Reading memory back: recall, search, related items and the context window.

Part of :class:`server.core.memory_engine.MemoryEngine`, split out to keep
each file readable. Mixins rather than separate services: the methods use the
engine's own state throughout, and moving the bodies unchanged is what makes
the split verifiable.
"""

from __future__ import annotations


from ..types import (
    Memory,
    RecallResult,
)


class MemoryRecallMixin:
    """Reading memory back: recall, search, related items and the context window."""

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
        from ..answerer import answer_question

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

"""Reading memory back: recall, search, related items and the context window.

Part of :class:`server.core.memory_engine.MemoryEngine`, split out to keep
each file readable. Mixins rather than separate services: the methods use the
engine's own state throughout, and moving the bodies unchanged is what makes
the split verifiable.
"""

from __future__ import annotations

import time

from .. import metrics
from ..lexical import expand_terms
from ..lexical import similarity_expanded as lexical_similarity
from ..lexical import terms as lexical_terms
from ..hscore import SUPERSEDED_PENALTY
from ..synonyms import SynonymTable
from ..types import (
    Memory,
    RecallResult,
    ScoreBreakdown,
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
        explain: bool = False,
    ) -> RecallResult:
        """Time the ranked recall and record its latency (issue #145).

        A thin wrapper so the metric covers every failure path too — the
        histogram is what a latency alert watches, and it must not go quiet
        exactly when recalls start raising.
        """
        started = time.perf_counter()
        try:
            return await self._recall(
                query,
                top_k=top_k,
                session_id=session_id,
                project=project,
                min_importance=min_importance,
                reinforce=reinforce,
                explain=explain,
            )
        finally:
            metrics.observe(
                "levh_recall_latency_seconds", time.perf_counter() - started
            )

    async def _recall(
        self,
        query: str,
        top_k: int = 10,
        session_id: str | None = None,
        project: str | None = None,
        min_importance: float = 0.0,
        reinforce: bool = True,
        explain: bool = False,
    ) -> RecallResult:
        """Recall memories ranked by H(x,ψ) score.

        Filters are applied BEFORE ranking so a filtered recall still returns
        up to top_k results. Only the memories actually returned get their
        access frequency / timestamp updated.

        Set ``reinforce=False`` for read-only recalls (e.g. dashboard search
        previews) so browsing the UI does not artificially strengthen memories
        or inflate their access frequency — only genuine AI recall should
        reinforce.

        Set ``explain=True`` to also return a per-result :class:`ScoreBreakdown`
        naming which signal drove the ranking, where each candidate came from,
        and the four penalty components that sum to the score. The extra work is
        nil — the components are computed on this path already — but it is
        opt-in so the common recall stays a compact payload.
        """
        await self._sync_with_external_writes()
        query_embedding = await self.embedder.embed(query)
        # The hash embedder's cosine is positional, not semantic (see
        # Embedder.is_semantic). Ranking on it hides memories whose every
        # content word matches the query, so model-free mode ranks on
        # word-overlap instead — the cosine is meaningless there and a max()
        # of the two would let its noise win. With a real embedder the cosine
        # is already the better ranking; behaviour is unchanged for
        # local/openai/ollama.
        #
        # Word overlap alone misses synonymy, though: "how do users log in"
        # shares no surface word with a memory about JWT authentication. The
        # synonym table turns a query term into its equivalents, which then
        # take part in both candidate retrieval and the score.
        synonym_table = SynonymTable.load()
        expansions = synonym_table.expand(query)
        query_terms = (
            expand_terms(query, expansions) if not self.embedder.is_semantic else set()
        )
        lexical_terms_set = query_terms
        similarity_source = "cosine" if self.embedder.is_semantic else "lexical"

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

        # In model-free mode the query terms also pull candidates the positional
        # cosine ranked poorly, so a strong keyword match can still surface.
        by_id: dict[str, Memory] = {memory.id: memory for memory, _ in candidates}
        keyword_ids: set[str] = set()
        entity_ids: set[str] = set()
        synonym_ids: list[str] = []
        if lexical_terms_set:
            for memory in self.vector_store.memories():
                if memory.id in by_id or not _predicate(memory):
                    continue
                if lexical_terms_set & lexical_terms(memory.content):
                    by_id[memory.id] = memory
                    keyword_ids.add(memory.id)

            # The keyword scan above only sees the vector store's rows; the FTS
            # query below reaches the rest, and is widened with the synonyms so
            # a translated term finds the stored wording.
            synonym_query = " ".join(
                [query, *sorted({e for eqs in expansions.values() for e in eqs})]
            )
            synonym_ids = await self.episodic.search_fts_ids(
                synonym_query, limit=top_k * 3
            )

        # Full-text candidates: FTS indexes content, so it reaches rows the
        # vector store cannot — most importantly an embedding-less row (a peer
        # imported it, or a mode switch left it without a vector). Those rows
        # are invisible to every in-memory candidate path and so were silently
        # unrecallable; the DB is the source of truth and FTS reads it.
        fts_ids = await self.episodic.search_fts_ids(query, limit=top_k * 3)
        fts_ids = list(dict.fromkeys(fts_ids + synonym_ids))
        for memory in await self.episodic.get_many(
            [mid for mid in fts_ids if mid not in by_id]
        ):
            if _predicate(memory):
                by_id[memory.id] = memory
                keyword_ids.add(memory.id)

        # Entity bridge: a query term that is the name of something in the graph
        # ("Zephyr") reaches every memory connected to that entity. The graph
        # already stores this; it was only ever exposed as its own endpoint, so
        # recall could not use it as a candidate source at all. A memory already
        # pulled in by the vector store still counts as an entity hit here, so
        # the label reports the stronger signal rather than the incidental one.
        for memory in await self._entity_linked_memories(query_terms, lexical_terms(query)):
            if not _predicate(memory):
                continue
            if memory.id not in by_id:
                by_id[memory.id] = memory
            entity_ids.add(memory.id)

        cosine_by_id = {memory.id: similarity for memory, similarity in candidates}

        scored: list[tuple[Memory, float]] = []
        breakdowns_by_id: dict[str, ScoreBreakdown] = {}
        for memory_id, memory in by_id.items():
            # Pinned memories are exempt from time decay. Everyone else decays
            # from their LAST ACCESS at their OWN stability (half-life), not a
            # global one — a memory that's been recalled often forgets slower.
            decay = (
                1.0
                if memory.pinned
                else self.scorer.compute_decay(memory.accessed_at, half_life_hours=memory.stability_hours)
            )
            cosine = cosine_by_id.get(memory_id, 0.0)
            if lexical_terms_set:
                similarity = lexical_similarity(query, memory.content, expansions)
                sim_source = similarity_source
            elif memory_id in keyword_ids or memory_id in entity_ids:
                # Semantic mode, but this candidate was reached by FTS or the
                # entity graph only — it has no vector the query could compare
                # against, so a cosine of 0 would bury a genuine term match.
                # Fall back to coverage and say so, rather than reporting a
                # cosine that was never measured.
                similarity = lexical_similarity(query, memory.content, expansions)
                sim_source = "lexical"
            else:
                similarity = cosine
                sim_source = similarity_source
            hscore = self.scorer.compute(
                similarity=similarity,
                decay_factor=decay,
                importance=memory.importance,
                frequency=memory.frequency,
            )
            # A superseded memory is one that a newer, near-identical memory
            # replaced (the write path marks it). Weakening its stability
            # lowers the score it *will* have, not the one it has now: the
            # replaced fact still ties with its replacement and can outrank it
            # on importance or access frequency. The explicit penalty is what
            # actually demotes it, so the current fact wins the ranking.
            superseded = bool(getattr(memory, "metadata", {}).get("superseded_by"))
            superseded_penalty = SUPERSEDED_PENALTY if superseded else 0.0
            hscore = min(1.0, hscore + superseded_penalty)
            memory.hscore = hscore
            scored.append((memory, hscore))
            if explain:
                bd = self.scorer.breakdown(
                    similarity=similarity,
                    decay_factor=decay,
                    importance=memory.importance,
                    frequency=memory.frequency,
                )
                breakdowns_by_id[memory_id] = ScoreBreakdown(
                    memory_id=memory_id,
                    content_snippet=memory.content[:100],
                    total_hscore=hscore,
                    alpha_component=bd["alpha_component"],
                    beta_component=bd["beta_component"],
                    gamma_component=bd["gamma_component"],
                    delta_component=bd["delta_component"],
                    superseded_penalty=superseded_penalty,
                    similarity_source=sim_source,
                    similarity=round(float(similarity), 6),
                    cosine=round(float(cosine), 6),
                    candidate_source=(
                        "entity"
                        if memory_id in entity_ids
                        else ("keyword" if memory_id in keyword_ids else "vector")
                    ),
                    decay_factor=decay,
                    importance=memory.importance,
                    frequency=memory.frequency,
                )

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
            breakdowns=(
                [breakdowns_by_id[m.id] for m, _ in top] if explain else []
            ),
        )

    # Matches to try when bridging a query to the entity graph, and the cap on
    # how many memories the bridge may contribute. Small on purpose: an entity
    # hit is a strong signal but a common entity ("github", "todo") would
    # otherwise flood recall with everything it touches.
    _ENTITY_MATCH_LIMIT = 5
    _ENTITY_MEMORY_LIMIT = 30

    async def _entity_linked_memories(
        self, expanded_terms: set[str], raw_terms: set[str]
    ) -> list[Memory]:
        """Memories reached through the entity graph by the query's own words.

        A query term that names an entity ("Zephyr") pulls the memories that
        mention it. Resolution is the graph's own (case-insensitive substring
        on the entity name/key), deliberately permissive; the match cap and the
        memory cap below bound how much a common entity can contribute.
        """
        terms = expanded_terms or raw_terms
        if not terms:
            return []
        memory_ids: list[str] = []
        seen_entities: set[str] = set()
        for term in sorted(terms):
            entity_id = await self.db.find_entity(term)
            if not entity_id or entity_id in seen_entities:
                continue
            seen_entities.add(entity_id)
            if len(seen_entities) > self._ENTITY_MATCH_LIMIT:
                break
            memory_ids.extend(
                await self.db.entity_memory_ids(entity_id, limit=self._ENTITY_MEMORY_LIMIT)
            )
        if not memory_ids:
            return []
        return await self.episodic.get_many(list(dict.fromkeys(memory_ids)))

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
